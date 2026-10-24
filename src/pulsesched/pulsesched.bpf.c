/* SPDX-License-Identifier: GPL-2.0 */
/* Full-system weighted virtual time with bounded interaction credit. */
#include <scx/common.bpf.h>
#include "pulse_policy.h"

char _license[] SEC("license") = "GPL";

#define SHARED_DSQ 0

static __u64 vtime_now;

const volatile __u32 pulse_max_propagation_depth = 2;
const volatile bool pulse_propagation_enabled = false;

enum pulse_end_reason {
	PULSE_REASON_NONE = 0,
	PULSE_REASON_PRESENTATION_FEEDBACK = 1,
	PULSE_REASON_TIMEOUT = 2,
	PULSE_REASON_CANCELLED = 3,
	PULSE_REASON_ERROR = 4,
};

/* Scheduler-owned accounting is never replaced by a userspace epoch write. */
struct pulse_sched_state {
	__u64 vtime;
	__u64 run_start_ns;
	__u64 queued_ns;
};

struct {
	__uint(type, BPF_MAP_TYPE_TASK_STORAGE);
	__uint(map_flags, BPF_F_NO_PREALLOC);
	__type(key, int);
	__type(value, struct pulse_sched_state);
} sched_state SEC(".maps");

/* log2 nanosecond enqueue-to-running histogram; optional observer. */
const volatile bool pulse_trace_delay = false;
struct {
	__uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
	__uint(max_entries, 64);
	__type(key, __u32);
	__type(value, __u64);
} delay_hist SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, 1024);
	__type(key, __u32);
	__type(value, struct pulse_task_state);
} task_state SEC(".maps");

enum pulse_metric_index {
	PULSE_METRIC_NORMAL_DISPATCH = 0,
	PULSE_METRIC_URGENT_DISPATCH = 1,
	PULSE_METRIC_TIMEOUT = 2,
	PULSE_METRIC_STALE = 3,
	PULSE_METRIC_REGISTER = 4,
	PULSE_METRIC_INVALID = 5,
	PULSE_METRIC_PROPAGATION_ATTEMPT = 6,
	PULSE_METRIC_PROPAGATION_SUCCESS = 7,
	PULSE_METRIC_PROPAGATION_DEPTH_REJECT = 8,
	PULSE_METRIC_PROPAGATION_INACTIVE_REJECT = 9,
	PULSE_METRIC_INHERITED_URGENT_ENQUEUE = 10,
	PULSE_METRIC_INHERITED_URGENT_DISPATCH = 11,
	PULSE_METRIC_COUNT = 12,
};

struct {
	__uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
	__uint(max_entries, PULSE_METRIC_COUNT);
	__type(key, __u32);
	__type(value, __u64);
} metrics SEC(".maps");

static void metric_inc(__u32 index)
{
	__u64 *value = bpf_map_lookup_elem(&metrics, &index);
	if (value)
		(*value)++;
}

static struct pulse_task_state *state_for(struct task_struct *p)
{
	__u32 pid = p->pid;
	return bpf_map_lookup_elem(&task_state, &pid);
}

static bool expire_if_needed(struct task_struct *p, __u64 now)
{
	struct pulse_task_state *state = state_for(p);
	if (!state || !state->active)
		return false;

	if (state->deadline_ns == 0 ||
	    state->deadline_ns - state->start_ns > MAX_EPOCH_TTL_NS ||
	    now >= state->deadline_ns) {
		state->active = 0;
		state->end_ns = state->deadline_ns > state->start_ns &&
			state->deadline_ns <= now ? state->deadline_ns : now;
		state->termination_reason = PULSE_REASON_TIMEOUT;
		metric_inc(PULSE_METRIC_TIMEOUT);
		return true;
	}
	return false;
}

static bool urgent_now(struct task_struct *p, __u64 now)
{
	struct pulse_task_state *state = state_for(p);
	if (!state || expire_if_needed(p, now) || !pulse_epoch_live(state, now))
		return false;
	if (state->inherited) {
		__u32 root_pid = state->root_pid;
		struct pulse_task_state *root = bpf_map_lookup_elem(&task_state, &root_pid);
		/* A late copy cannot resurrect an epoch after root closure/replacement. */
		if (!pulse_root_live(state, root, now))
			return false;
	}
	return true;
}

static void pulse_try_propagate(struct task_struct *p)
{
	__u64 now = bpf_ktime_get_ns();
	struct task_struct *waker;
	struct pulse_task_state *parent;
	struct pulse_task_state *child;
	struct pulse_task_state inherited = {};
	__u32 child_pid = p->pid;

	if (!pulse_propagation_enabled)
		return;
	metric_inc(PULSE_METRIC_PROPAGATION_ATTEMPT);
	waker = (struct task_struct *)bpf_get_current_task_btf();
	if (!waker || waker == p) {
		metric_inc(PULSE_METRIC_PROPAGATION_INACTIVE_REJECT);
		return;
	}
	parent = state_for(waker);
	if (!parent || !urgent_now(waker, now)) {
		metric_inc(PULSE_METRIC_PROPAGATION_INACTIVE_REJECT);
		return;
	}
	if (parent->depth >= pulse_max_propagation_depth) {
		metric_inc(PULSE_METRIC_PROPAGATION_DEPTH_REJECT);
		return;
	}
	child = bpf_map_lookup_elem(&task_state, &child_pid);
	/* Propagate only to registered participants, never over a direct token or
	 * another live interaction. Completion wakeups must not rewrite the root. */
	if (!child || child->direct || urgent_now(p, now))
		return;
	inherited.epoch_id = parent->epoch_id;
	inherited.start_ns = parent->start_ns;
	inherited.deadline_ns = parent->deadline_ns;
	inherited.active = 1;
	inherited.inherited = 1;
	inherited.depth = parent->depth + 1;
	/* Materialize the task PID before selecting a root. A plain ternary
	 * between map and task fields lets LLVM select their addresses and emit
	 * one load for two verifier pointer types, which the kernel rejects.
	 * READ_ONCE keeps this task-field load distinct from the map-field load. */
	__u32 waker_pid = READ_ONCE(waker->pid);
	inherited.parent_pid = waker_pid;
	inherited.direct = 0;
	inherited.root_pid = parent->inherited ? parent->root_pid : waker_pid;
	if (!bpf_map_update_elem(&task_state, &child_pid, &inherited, BPF_EXIST))
		metric_inc(PULSE_METRIC_PROPAGATION_SUCCESS);
}

static struct pulse_sched_state *sched_for(struct task_struct *p)
{
	return bpf_task_storage_get(&sched_state, p, 0, 0);
}

static void record_enqueue(struct task_struct *p, bool urgent, __u64 now)
{
	struct pulse_task_state *state = state_for(p);
	struct pulse_sched_state *sched = sched_for(p);
	if (sched && pulse_trace_delay)
		sched->queued_ns = now;
	if (!state)
		return;
	if (urgent) {
		state->urgent_enqueues++;
		if (state->inherited)
			metric_inc(PULSE_METRIC_INHERITED_URGENT_ENQUEUE);
	} else {
		state->normal_enqueues++;
	}
}

s32 BPF_STRUCT_OPS(pulse_select_cpu, struct task_struct *p, s32 prev_cpu,
			   u64 wake_flags)
{
	bool is_idle = false;
	pulse_try_propagate(p);
	s32 cpu = scx_bpf_select_cpu_dfl(p, prev_cpu, wake_flags, &is_idle);
	if (is_idle) {
		__u64 now = bpf_ktime_get_ns();
		struct pulse_sched_state *sched = sched_for(p);
		if (sched)
			sched->vtime = pulse_base_vtime(sched->vtime, vtime_now);
		record_enqueue(p, urgent_now(p, now), now);
		/* The selected idle CPU should run this task, not search a shared queue. */
		scx_bpf_dsq_insert(p, SCX_DSQ_LOCAL, PULSE_SLICE_NS, 0);
	}
	return cpu;
}

void BPF_STRUCT_OPS(pulse_enqueue, struct task_struct *p, u64 enq_flags)
{
	__u64 now = bpf_ktime_get_ns();
	bool urgent = urgent_now(p, now);
	struct pulse_sched_state *sched = sched_for(p);
	if (!sched) {
		/* Allocation failure must abort attachment via init_task, not lose work. */
		scx_bpf_error("missing task accounting");
		scx_bpf_dsq_insert(p, SCX_DSQ_GLOBAL, PULSE_SLICE_NS, enq_flags);
		return;
	}
	sched->vtime = pulse_base_vtime(sched->vtime, vtime_now);
	record_enqueue(p, urgent, now);
	/* The credit changes queue order, never the runtime account itself. Thus
	 * even continuous/repeated epochs cannot starve normal runnable tasks. */
	scx_bpf_dsq_insert_vtime(p, SHARED_DSQ, PULSE_SLICE_NS,
				 pulse_queue_key(sched->vtime, urgent), enq_flags);
}

void BPF_STRUCT_OPS(pulse_dispatch, s32 cpu, struct task_struct *prev)
{
	scx_bpf_dsq_move_to_local(SHARED_DSQ);
}

void BPF_STRUCT_OPS(pulse_running, struct task_struct *p)
{
	__u64 now = bpf_ktime_get_ns();
	struct pulse_sched_state *sched = sched_for(p);
	struct pulse_task_state *state = state_for(p);
	if (sched) {
		/* Same approximate shared clock as scx_simple; debt is per-task. */
		if (pulse_before(vtime_now, sched->vtime))
			vtime_now = sched->vtime;
		sched->run_start_ns = now;
		if (pulse_trace_delay && sched->queued_ns) {
			__u64 delay = now - sched->queued_ns;
			__u32 bucket = 0;
			int i;
			bpf_for(i, 0, 63) {
				if (delay <= 1)
					break;
				delay >>= 1;
				bucket++;
			}
			__u64 *count = bpf_map_lookup_elem(&delay_hist, &bucket);
			if (count)
				(*count)++;
			sched->queued_ns = 0;
		}
	}
	if (urgent_now(p, now)) {
		metric_inc(PULSE_METRIC_URGENT_DISPATCH);
		if (state) {
			state->last_run_start_ns = now;
			state->urgent_dispatches++;
			if (state->inherited)
				metric_inc(PULSE_METRIC_INHERITED_URGENT_DISPATCH);
		}
	} else {
		metric_inc(PULSE_METRIC_NORMAL_DISPATCH);
	}
}

void BPF_STRUCT_OPS(pulse_stopping, struct task_struct *p, bool runnable)
{
	__u64 now = bpf_ktime_get_ns();
	struct pulse_sched_state *sched = sched_for(p);
	struct pulse_task_state *state = state_for(p);
	if (sched && sched->run_start_ns) {
		sched->vtime += pulse_runtime_charge(now - sched->run_start_ns, p->scx.weight);
		/* Count only this actual on-CPU interval intersected with the epoch. */
		if (state && state->epoch_id) {
			__u64 start = sched->run_start_ns > state->start_ns ?
				sched->run_start_ns : state->start_ns;
			__u64 end = now < state->deadline_ns ? now : state->deadline_ns;
			if (state->end_ns && state->end_ns < end)
				end = state->end_ns;
			if (end > start)
				state->boosted_runtime_ns += end - start;
			state->last_run_start_ns = 0;
		}
		sched->run_start_ns = 0;
	}
	expire_if_needed(p, now);
}

void BPF_STRUCT_OPS(pulse_tick, struct task_struct *p)
{
	expire_if_needed(p, bpf_ktime_get_ns());
}

s32 BPF_STRUCT_OPS(pulse_init_task, struct task_struct *p,
		   struct scx_init_task_args *args)
{
	return bpf_task_storage_get(&sched_state, p, 0,
		BPF_LOCAL_STORAGE_GET_F_CREATE) ? 0 : -ENOMEM;
}

void BPF_STRUCT_OPS(pulse_exit_task, struct task_struct *p,
		    struct scx_exit_task_args *args)
{
	__u32 pid = p->pid;
	bpf_map_delete_elem(&task_state, &pid);
}

UEI_DEFINE(uei);

void BPF_STRUCT_OPS(pulse_enable, struct task_struct *p)
{
	struct pulse_sched_state *sched = sched_for(p);
	if (sched)
		sched->vtime = vtime_now;
}

s32 BPF_STRUCT_OPS_SLEEPABLE(pulse_init)
{
	return scx_bpf_create_dsq(SHARED_DSQ, -1);
}

void BPF_STRUCT_OPS(pulse_exit, struct scx_exit_info *ei)
{
	UEI_RECORD(uei, ei);
}

SCX_OPS_DEFINE(pulsesched_ops,
		       .select_cpu = (void *)pulse_select_cpu,
		       .enqueue = (void *)pulse_enqueue,
		       .dispatch = (void *)pulse_dispatch,
		       .running = (void *)pulse_running,
		       .stopping = (void *)pulse_stopping,
		       .tick = (void *)pulse_tick,
		       .enable = (void *)pulse_enable,
		       .init = (void *)pulse_init,
		       .exit = (void *)pulse_exit,
		       .init_task = (void *)pulse_init_task,
		       .exit_task = (void *)pulse_exit_task,
		       .flags = 0,
		       .name = "pulsesched");
