/* SPDX-License-Identifier: GPL-2.0 */
#ifndef PULSE_POLICY_H
#define PULSE_POLICY_H

/* Keep the existing 20 ms slice; isolate policy repairs from slice tuning. */
#define PULSE_SLICE_NS 20000000ULL
#define MAX_EPOCH_TTL_NS (10ULL * 1000000000ULL)

struct pulse_task_state {
	__u64 epoch_id, start_ns, end_ns, deadline_ns, boosted_runtime_ns;
	__u64 last_run_start_ns, normal_enqueues, urgent_enqueues, urgent_dispatches;
	__u32 active, termination_reason, inherited, depth, parent_pid, direct;
	__u32 root_pid;
};

static inline int pulse_before(__u64 a, __u64 b)
{
	return (__s64)(a - b) < 0;
}

static inline int pulse_epoch_live(const struct pulse_task_state *s, __u64 now)
{
	return s && s->active && s->epoch_id && s->start_ns <= now &&
		s->deadline_ns > s->start_ns &&
		s->deadline_ns - s->start_ns <= MAX_EPOCH_TTL_NS &&
		now < s->deadline_ns;
}

static inline int pulse_root_live(const struct pulse_task_state *child,
				 const struct pulse_task_state *root, __u64 now)
{
	return pulse_epoch_live(root, now) && root->direct &&
		root->epoch_id == child->epoch_id && root->start_ns == child->start_ns;
}

/* Clamp sleeper credit, but never erase runtime debt when an epoch restarts. */
static inline __u64 pulse_base_vtime(__u64 base, __u64 now)
{
	return pulse_before(base, now - PULSE_SLICE_NS) ? now - PULSE_SLICE_NS : base;
}

static inline __u64 pulse_queue_key(__u64 base, int urgent)
{
	return base - (urgent ? PULSE_SLICE_NS : 0);
}

static inline __u64 pulse_runtime_charge(__u64 runtime, __u32 weight)
{
	return runtime * 100 / (weight ? weight : 1);
}
#endif
