/* SPDX-License-Identifier: MIT */
/*
 * PulseSched controlled Wayland interaction benchmark.
 *
 * Each real pointer-button or keyboard press starts one userspace trial. The
 * trial performs CPU work, changes the shm-backed surface, requests exactly
 * one presentation feedback object for that commit, and emits one JSONL
 * result when that feedback is presented, discarded, or times out.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <poll.h>
#include <stdint.h>
#include <stdatomic.h>
#include <signal.h>
#include <sched.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/eventfd.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#include <wayland-client.h>
#include <wayland-client-protocol.h>
#include <bpf/bpf.h>
#include <scx/common.h>
#include "presentation-time-client-protocol.h"
#include "xdg-shell-client-protocol.h"
#include "pulsesched.bpf.skel.h"

#ifndef SCHED_EXT
#define SCHED_EXT 7
#endif

#define WORKER_STOP_COMMAND (UINT64_MAX - 1)

enum result_status { RESULT_PRESENTED, RESULT_DISCARDED, RESULT_TIMEOUT };

static volatile sig_atomic_t interrupted;
static void on_signal(int sig) { (void)sig; interrupted = 1; }

struct app;
struct frame_buffer {
	struct wl_buffer *buffer;
	void *pixels;
	int busy;
};
struct feedback_ctx {
	struct app *app;
	struct wp_presentation_feedback *feedback;
	uint64_t feedback_received_ns;
	uint32_t refresh_ns;
	uint64_t interaction_id;
	uint64_t input_ns;
	uint64_t work_start_ns;
	uint64_t commit_ns;
	uint64_t feedback_request_ns;
	uint64_t epoch_register_ns;
	uint64_t scheduler_epoch_start_ns;
	uint64_t scheduler_epoch_end_ns;
	uint64_t scheduler_boosted_runtime_ns;
	uint64_t scheduler_normal_enqueues;
	uint64_t scheduler_urgent_enqueues;
	uint32_t scheduler_termination_reason;
	int closed_loop;
	uint64_t presentation_ns;
	uint32_t flags;
	int completed;
	int timed_out;
};

#include "pulse_policy.h"

struct pulse_runtime {
	struct pulsesched *skel;
	struct bpf_link *link;
	int enabled;
	int external, task_fd, metrics_fd, owner_pidfd;
};

#ifdef PULSE_EXTERNAL_BACKEND
#include "external_scheduler.h"
#endif

struct app {
	struct wl_display *display;
	struct wl_registry *registry;
	struct wl_compositor *compositor;
	struct wl_shm *shm;
	struct wl_seat *seat;
	struct xdg_wm_base *wm_base;
	struct wp_presentation *presentation;
	struct wl_surface *surface;
	struct xdg_surface *xdg_surface;
	struct xdg_toplevel *toplevel;
	struct frame_buffer buffers[2];
	struct frame_buffer *render_buffer;
	int shm_fd;
	int configured;
	int running;
	atomic_int protocol_error;
	uint32_t presentation_version;
	uint32_t xdg_version;
	uint64_t surface_state_received_ns;
	int surface_activated, surface_suspended;
	uint32_t presentation_clock_id;
	int presentation_clock_seen;
	uint64_t work_ms;
	uint64_t feedback_timeout_ms;
	uint64_t pulse_ttl_ms;
	int closed_loop;
	int interleaved;
	int propagation_enabled;
	int dependency_mode;
	uint32_t max_propagation_depth;
	uint32_t worker_pid[4];
	int worker_policy[4];
	int worker_wake_fd[4];
	int worker_done_fd[4];
	pthread_t worker_thread[4];
	int worker_index[4];
	pthread_mutex_t worker_mutex;
	pthread_cond_t worker_cond;
	int worker_ready;
	int worker_created;
	int worker_sync_initialized;
	uint64_t worker_sent_ns[4], worker_start_ns[4], worker_end_ns[4];
	uint64_t worker_cpu_ns[4];
	uint64_t auto_interval_ms, next_auto_ns;
	uint64_t requested_trials;
	uint64_t completed_trials;
	uint64_t next_interaction_id;
	struct feedback_ctx *pending;
	struct pulse_runtime pulse;
};

static struct frame_buffer *free_buffer(struct app *app)
{
	for (int i = 0; i < 2; i++)
		if (!app->buffers[i].busy) return &app->buffers[i];
	return NULL;
}

static void start_trial(struct app *app);
static int pulse_observe_epoch(struct app *app, struct feedback_ctx *ctx);
static uint64_t clock_ns(clockid_t clock_id);
static void busy_ms(uint64_t ms);
static void emit_error(struct app *app, const char *kind, const char *detail);
static uint32_t current_tid(void);
static int pulse_update_state(struct app *app, struct pulse_task_state *state);
static int pulse_task_fd(struct app *app)
{
	return app->pulse.external ? app->pulse.task_fd : bpf_map__fd(app->pulse.skel->maps.task_state);
}

static int pulse_metrics_fd(struct app *app)
{
	return app->pulse.external ? app->pulse.metrics_fd : bpf_map__fd(app->pulse.skel->maps.metrics);
}

static int pulse_exited(struct app *app)
{
#ifdef PULSE_EXTERNAL_BACKEND
	if (app->pulse.external) return !external_alive(app->pulse.owner_pidfd);
#endif
	return UEI_EXITED(app->pulse.skel, uei);
}

static int pulse_update_state_pid(struct app *app, uint32_t pid,
					 struct pulse_task_state *state);

static uint64_t clock_ns(clockid_t clock_id)
{
	struct timespec ts;
	if (clock_gettime(clock_id, &ts) < 0)
		return 0;
	return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

static uint32_t current_tid(void)
{
	return (uint32_t)syscall(SYS_gettid);
}

static void *dependency_worker(void *arg)
{
	int index = *(int *)arg;
	struct app *app = (struct app *)((char *)arg -
				offsetof(struct app, worker_index) -
				(size_t)index * sizeof(app->worker_index[0]));
	struct pulse_task_state state = {0};
	uint64_t command;
	struct sched_param param = { .sched_priority = 0 };

	app->worker_pid[index] = (uint32_t)syscall(SYS_gettid);
	if (app->pulse.enabled) {
		if (sched_setscheduler(0, SCHED_EXT, &param) < 0)
			emit_error(app, "protocol-error", "worker-sched-ext-failed");
		else if (pulse_update_state_pid(app, app->worker_pid[index], &state) < 0)
			emit_error(app, "protocol-error", "worker-registration-failed");
	}
	app->worker_policy[index] = sched_getscheduler(0);
	if (app->pulse.enabled && app->worker_policy[index] != SCHED_EXT)
		emit_error(app, "protocol-error", "worker-policy-is-not-sched-ext");
	pthread_mutex_lock(&app->worker_mutex);
	app->worker_ready++;
	pthread_cond_broadcast(&app->worker_cond);
	pthread_mutex_unlock(&app->worker_mutex);
	while (read(app->worker_wake_fd[index], &command, sizeof(command)) == sizeof(command)) {
		if (command == WORKER_STOP_COMMAND)
			break;
		app->worker_start_ns[index] = clock_ns(CLOCK_MONOTONIC);
		uint64_t cpu_start = clock_ns(CLOCK_THREAD_CPUTIME_ID);
		busy_ms(app->work_ms);
		app->worker_cpu_ns[index] = clock_ns(CLOCK_THREAD_CPUTIME_ID) - cpu_start;
		app->worker_end_ns[index] = clock_ns(CLOCK_MONOTONIC);
		if (index + 1 < 4) {
			uint64_t child_done = 0;
			app->worker_sent_ns[index + 1] = clock_ns(CLOCK_MONOTONIC);
			if (write(app->worker_wake_fd[index + 1], &command,
				  sizeof(command)) != sizeof(command) ||
			    read(app->worker_done_fd[index + 1], &child_done,
				 sizeof(child_done)) != sizeof(child_done))
				break;
		}
		command = 1;
		if (write(app->worker_done_fd[index], &command, sizeof(command)) !=
		    sizeof(command))
			break;
	}
	return NULL;
}

static void busy_ms(uint64_t ms)
{
	/* CPU time makes work independent of time descheduled. Versioned in metadata. */
	uint64_t end = clock_ns(CLOCK_THREAD_CPUTIME_ID) + ms * 1000000ULL;
	volatile uint64_t value = 0;
	do {
		for (int i = 0; i < 1024; i++)
			value = value * 1664525ULL + 1013904223ULL;
	} while (clock_ns(CLOCK_THREAD_CPUTIME_ID) < end);
	(void)value;
}

static const char *status_name(enum result_status status)
{
	switch (status) {
	case RESULT_PRESENTED: return "presented";
	case RESULT_DISCARDED: return "discarded";
	case RESULT_TIMEOUT: return "timeout";
	}
	return "protocol-error";
}

static const char *termination_reason_name(uint32_t reason)
{
	switch (reason) {
	case 1: return "PRESENTATION_FEEDBACK";
	case 2: return "TIMEOUT";
	case 3: return "CANCELLED";
	case 4: return "ERROR";
	default: return "NONE";
	}
}

static void emit_result(struct feedback_ctx *ctx, enum result_status status)
{
	uint64_t presentation = ctx->presentation_ns;
	uint64_t input_to_work = ctx->work_start_ns - ctx->input_ns;
	uint64_t work_to_commit = ctx->commit_ns - ctx->work_start_ns;
	uint64_t commit_to_presentation = presentation >= ctx->commit_ns
		? presentation - ctx->commit_ns : 0;
	printf("{\"type\":\"trial\",\"interaction_id\":%llu,"
	       "\"status\":\"%s\",\"input_ns\":%llu,"
	       "\"work_start_ns\":%llu,\"commit_ns\":%llu,"
	       "\"presentation_ns\":%llu,\"input_to_work_ns\":%llu,"
	       "\"work_to_commit_ns\":%llu,\"commit_to_presentation_ns\":%llu,"
	       "\"presentation_clock_id\":%u,\"presentation_flags\":%u,"
	       "\"scheduler_mode\":\"%s\",\"feedback_request_ns\":%llu,"
	       "\"epoch_register_ns\":%llu,"
	       "\"epoch_start_ns\":%llu,\"epoch_termination_ns\":%llu,"
	       "\"termination_reason\":%u,\"termination_reason_name\":\"%s\","
	       "\"maximum_ttl_ms\":%llu,\"boosted_runtime_ns\":%llu,"
	       "\"normal_enqueues\":%llu,\"urgent_enqueues\":%llu}\n",
	       (unsigned long long)ctx->interaction_id, status_name(status),
	       (unsigned long long)ctx->input_ns,
	       (unsigned long long)ctx->work_start_ns,
	       (unsigned long long)ctx->commit_ns,
	       (unsigned long long)presentation,
	       (unsigned long long)input_to_work,
	       (unsigned long long)work_to_commit,
	       (unsigned long long)commit_to_presentation,
	       ctx->app->presentation_clock_id, ctx->flags,
	       ctx->closed_loop ? "closed" : (ctx->app->pulse.enabled ? "ttl" : "normal"),
	       (unsigned long long)ctx->feedback_request_ns,
	       (unsigned long long)ctx->epoch_register_ns,
	       (unsigned long long)ctx->scheduler_epoch_start_ns,
	       (unsigned long long)ctx->scheduler_epoch_end_ns,
	       ctx->scheduler_termination_reason,
	       termination_reason_name(ctx->scheduler_termination_reason),
	       (unsigned long long)ctx->app->pulse_ttl_ms,
	       (unsigned long long)ctx->scheduler_boosted_runtime_ns,
	       (unsigned long long)ctx->scheduler_normal_enqueues,
	       (unsigned long long)ctx->scheduler_urgent_enqueues);
	printf("{\"type\":\"feedback_timing\",\"interaction_id\":%llu,"
	       "\"feedback_received_ns\":%llu,\"refresh_ns\":%u}\n",
	       (unsigned long long)ctx->interaction_id,
	       (unsigned long long)ctx->feedback_received_ns, ctx->refresh_ns);
	if (ctx->app->dependency_mode) {
		for (int i = 0; i < 4; i++)
			printf("{\"type\":\"work_stage\",\"interaction_id\":%llu,"
			       "\"worker\":%d,\"handoff_ns\":%llu,\"wall_ns\":%llu,\"cpu_ns\":%llu}\n",
			       (unsigned long long)ctx->interaction_id, i,
			       (unsigned long long)(ctx->app->worker_start_ns[i] - ctx->app->worker_sent_ns[i]),
			       (unsigned long long)(ctx->app->worker_end_ns[i] - ctx->app->worker_start_ns[i]),
			       (unsigned long long)ctx->app->worker_cpu_ns[i]);
	}
	fflush(stdout);
}

static void emit_error(struct app *app, const char *kind, const char *detail)
{
	printf("{\"type\":\"%s\",\"detail\":\"%s\"}\n", kind, detail);
	fflush(stdout);
	app->protocol_error = 1;
}

static void emit_notice(struct app *app, const char *kind, const char *detail)
{
	(void)app;
	printf("{\"type\":\"%s\",\"detail\":\"%s\"}\n", kind, detail);
	fflush(stdout);
}

static int pulse_update_state_pid(struct app *app, uint32_t pid,
					 struct pulse_task_state *state)
{
	return bpf_map_update_elem(pulse_task_fd(app),
				   &pid, state, BPF_ANY);
}

static int pulse_update_state(struct app *app, struct pulse_task_state *state)
{
	return pulse_update_state_pid(app, current_tid(), state);
}

static int pulse_init(struct app *app)
{
	struct pulse_task_state state = {0};
	struct sched_param param = { .sched_priority = 0 };
	libbpf_set_strict_mode(LIBBPF_STRICT_ALL);
#ifdef PULSE_EXTERNAL_BACKEND
	if (getenv("PROPHET_TASK_FD") || getenv("PROPHET_METRICS_FD") || getenv("PROPHET_PID_FD")) {
		app->pulse.external = 1;
		app->pulse.task_fd = external_fd("PROPHET_TASK_FD");
		app->pulse.metrics_fd = external_fd("PROPHET_METRICS_FD");
		app->pulse.owner_pidfd = external_fd("PROPHET_PID_FD");
		/* This integration deliberately supports only the tested default: full
		 * switching, propagation off, depth 2. Refuse mislabeled ablations. */
		if (app->propagation_enabled || app->max_propagation_depth != 2 ||
		    getenv("PULSESCHED_PARTIAL") || getenv("PULSESCHED_TRACE_DELAY") ||
		    !external_map_valid(app->pulse.task_fd, "task_state", BPF_MAP_TYPE_HASH,
				 sizeof(struct pulse_task_state), 1024) ||
		    !external_map_valid(app->pulse.metrics_fd, "metrics", BPF_MAP_TYPE_PERCPU_ARRAY, 8, 12) ||
		    !external_pidfd_valid(app->pulse.owner_pidfd) ||
		    !external_alive(app->pulse.owner_pidfd)) {
			emit_error(app, "protocol-error", "invalid-prophet-external-backend");
			return -1;
		}
		goto register_root;
	}
#endif
	app->pulse.skel = SCX_OPS_OPEN(pulsesched_ops, pulsesched);
	app->pulse.skel->rodata->pulse_propagation_enabled = app->propagation_enabled;
	app->pulse.skel->rodata->pulse_max_propagation_depth =
		app->max_propagation_depth;
	app->pulse.skel->rodata->pulse_trace_delay = getenv("PULSESCHED_TRACE_DELAY") != NULL;
	/* Diagnostic ablation only; production/default is full-system scheduling. */
	if (getenv("PULSESCHED_PARTIAL"))
		app->pulse.skel->struct_ops.pulsesched_ops->flags |= SCX_OPS_SWITCH_PARTIAL;
	SCX_OPS_LOAD(app->pulse.skel, pulsesched_ops, pulsesched, uei);
	app->pulse.link = SCX_OPS_ATTACH(app->pulse.skel, pulsesched_ops, pulsesched);
#ifdef PULSE_EXTERNAL_BACKEND
register_root:
#endif
	if (sched_setscheduler(0, SCHED_EXT, &param) < 0) {
		perror("sched_setscheduler(SCHED_EXT)");
		return -1;
	}
	if (sched_getscheduler(0) != SCHED_EXT) {
		emit_error(app, "protocol-error", "root-policy-is-not-sched-ext");
		return -1;
	}
	if (pulse_update_state(app, &state) < 0)
		return -1;
	app->pulse.enabled = 1;
	printf("{\"type\":\"scheduler\",\"mode\":\"%s\","
	       "\"ttl_ms\":%llu,\"policy\":\"SCHED_EXT\","
	       "\"propagation_enabled\":%s,\"max_propagation_depth\":%u}\n",
	       app->pulse.external ? "scx-prophet-external" : app->interleaved ? "pulsesched-interleaved" :
	       (app->closed_loop ? "pulsesched-closed-loop" : "pulsesched-ttl"),
	       (unsigned long long)app->pulse_ttl_ms,
	       app->propagation_enabled ? "true" : "false",
	       app->max_propagation_depth);
	fflush(stdout);
	return 0;
}

static void pulse_cleanup(struct app *app)
{
	if (app->pulse.external) {
		if (app->pulse.task_fd >= 0) close(app->pulse.task_fd);
		if (app->pulse.metrics_fd >= 0) close(app->pulse.metrics_fd);
		if (app->pulse.owner_pidfd >= 0) close(app->pulse.owner_pidfd);
		app->pulse.task_fd = app->pulse.metrics_fd = app->pulse.owner_pidfd = -1;
		app->pulse.enabled = 0;
		return; /* External owner alone controls attachment lifetime. */
	}
	if (!app->pulse.skel)
		return;
	if (getenv("PULSESCHED_TRACE_DELAY") && app->pulse.enabled) {
		int ncpu = libbpf_num_possible_cpus();
		if (ncpu > 0) {
			uint64_t values[ncpu];
			for (uint32_t bucket = 0; bucket < 64; bucket++) {
				uint64_t total = 0;
				if (!bpf_map_lookup_elem(bpf_map__fd(app->pulse.skel->maps.delay_hist), &bucket, values)) {
					for (int cpu = 0; cpu < ncpu; cpu++) total += values[cpu];
					if (total) printf("{\"type\":\"enqueue_delay_hist\",\"log2_ns\":%u,\"count\":%llu}\n",
						bucket, (unsigned long long)total);
				}
			}
		}
	}
	bpf_link__destroy(app->pulse.link);
	__u64 ecode = UEI_REPORT(app->pulse.skel, uei);
	if (UEI_ECODE_USER(ecode))
		emit_error(app, "protocol-error", "pulsesched-exit");
	pulsesched__destroy(app->pulse.skel);
	app->pulse.enabled = 0;
	app->pulse.skel = NULL;
	app->pulse.link = NULL;
}

static int dependency_start(struct app *app)
{
	if (!app->dependency_mode)
		return 0;
	for (int i = 0; i < 4; i++) {
		app->worker_wake_fd[i] = -1;
		app->worker_done_fd[i] = -1;
	}
	pthread_mutex_init(&app->worker_mutex, NULL);
	pthread_cond_init(&app->worker_cond, NULL);
	app->worker_sync_initialized = 1;
	for (int i = 0; i < 4; i++) {
		app->worker_wake_fd[i] = eventfd(0, EFD_CLOEXEC);
		app->worker_done_fd[i] = eventfd(0, EFD_CLOEXEC);
		app->worker_index[i] = i;
		if (app->worker_wake_fd[i] < 0 || app->worker_done_fd[i] < 0)
			return -1;
	}
	for (int i = 0; i < 4; i++) {
		if (pthread_create(&app->worker_thread[i], NULL, dependency_worker,
				   &app->worker_index[i]) != 0)
			return -1;
		app->worker_created++;
	}
	pthread_mutex_lock(&app->worker_mutex);
	while (app->worker_ready < 4)
		pthread_cond_wait(&app->worker_cond, &app->worker_mutex);
	pthread_mutex_unlock(&app->worker_mutex);
	printf("{\"type\":\"dependency_chain\",\"root_pid\":%u,"
	       "\"root_policy\":%d,\"worker_pids\":[%u,%u,%u,%u],"
	       "\"worker_policies\":[%d,%d,%d,%d],"
	       "\"edges\":[[0,1],[1,2],[2,3]]}\n",
	       (unsigned)current_tid(), sched_getscheduler(0), app->worker_pid[0],
	       app->worker_pid[1], app->worker_pid[2], app->worker_pid[3],
	       app->worker_policy[0], app->worker_policy[1],
	       app->worker_policy[2], app->worker_policy[3]);
	fflush(stdout);
	return app->protocol_error ? -1 : 0;
}

static void dependency_stop(struct app *app)
{
	uint64_t stop = WORKER_STOP_COMMAND;
	if (!app->dependency_mode)
		return;
	for (int i = 0; i < app->worker_created; i++)
		(void)write(app->worker_wake_fd[i], &stop, sizeof(stop));
	for (int i = 0; i < app->worker_created; i++)
		pthread_join(app->worker_thread[i], NULL);
	for (int i = 0; i < 4; i++) {
		close(app->worker_wake_fd[i]);
		close(app->worker_done_fd[i]);
	}
	if (app->worker_sync_initialized) {
		pthread_cond_destroy(&app->worker_cond);
		pthread_mutex_destroy(&app->worker_mutex);
	}
	app->worker_created = 0;
	app->worker_ready = 0;
}

static int pulse_start_epoch(struct app *app, struct feedback_ctx *ctx)
{
	struct pulse_task_state state = {0};
	state.epoch_id = ctx->interaction_id;
	state.start_ns = ctx->input_ns;
	state.deadline_ns = ctx->input_ns + app->pulse_ttl_ms * 1000000ULL;
	state.active = 1;
	state.direct = 1;
	state.inherited = 0;
	state.depth = 0;
	state.parent_pid = 0;
	state.root_pid = current_tid();
	ctx->scheduler_epoch_start_ns = state.start_ns;
	if (pulse_update_state(app, &state) < 0)
		return -1;
	ctx->epoch_register_ns = clock_ns(CLOCK_MONOTONIC);
	return 0;
}

static uint64_t pulse_metric_total(struct app *app, uint32_t metric)
{
	int ncpu = libbpf_num_possible_cpus();
	uint64_t values[ncpu];
	uint64_t total = 0;
	memset(values, 0, sizeof(values));
	if (bpf_map_lookup_elem(pulse_metrics_fd(app),
				&metric, values) < 0)
		return 0;
	for (int cpu = 0; cpu < ncpu; cpu++)
		total += values[cpu];
	return total;
}

static void pulse_invalidate_epoch(struct app *app, uint64_t epoch_id,
				   uint32_t reason, uint64_t end_ns)
{
	int fd = pulse_task_fd(app);
	uint32_t key = 0, next;
	int have_key = bpf_map_get_next_key(fd, NULL, &key) == 0;
	while (have_key) {
		struct pulse_task_state state = {0};
		int next_result = bpf_map_get_next_key(fd, &key, &next);
		if (bpf_map_lookup_elem(fd, &key, &state) == 0 &&
		    state.epoch_id == epoch_id && state.active) {
			state.active = 0;
			state.end_ns = end_ns;
			state.termination_reason = reason;
			bpf_map_update_elem(fd, &key, &state, BPF_ANY);
		}
		if (next_result < 0)
			break;
		key = next;
	}
}

static void pulse_emit_epoch_members(struct app *app, uint64_t epoch_id)
{
	int fd = pulse_task_fd(app);
	uint32_t key = 0, next;
	int have_key = bpf_map_get_next_key(fd, NULL, &key) == 0;
	while (have_key) {
		struct pulse_task_state state = {0};
		int next_result = bpf_map_get_next_key(fd, &key, &next);
		if (bpf_map_lookup_elem(fd, &key, &state) == 0 &&
		    state.epoch_id == epoch_id) {
			printf("{\"type\":\"epoch_member\",\"interaction_id\":%llu,"
			       "\"pid\":%u,\"active\":%u,\"inherited\":%u,"
			       "\"depth\":%u,\"parent_pid\":%u,"
			       "\"boosted_runtime_ns\":%llu,\"termination_reason\":%u}\n",
			       (unsigned long long)epoch_id, key, state.active,
			       state.inherited, state.depth, state.parent_pid,
			       (unsigned long long)state.boosted_runtime_ns,
			       state.termination_reason);
		}
		if (next_result < 0)
			break;
		key = next;
	}
	fflush(stdout);
}

static int pulse_terminate(struct app *app, uint64_t epoch_id, uint32_t reason)
{
	if (!app->pulse.enabled)
		return 0;
	struct pulse_task_state state = {0};
	uint32_t pid = current_tid();
	int fd = pulse_task_fd(app);
	if (bpf_map_lookup_elem(fd, &pid, &state) < 0 || state.epoch_id != epoch_id)
		return -1;
	if (!state.active) {
		pulse_invalidate_epoch(app, epoch_id, state.termination_reason, state.end_ns);
		return 0;
	}
	state.active = 0;
	state.end_ns = clock_ns(CLOCK_MONOTONIC);
	state.termination_reason = reason;
	if (state.end_ns >= state.deadline_ns) {
		state.end_ns = state.deadline_ns;
		state.termination_reason = 2;
	}
	if (bpf_map_update_elem(fd, &pid, &state, BPF_ANY) < 0)
		return -1;
	pulse_invalidate_epoch(app, epoch_id, state.termination_reason, state.end_ns);
	return 1;
}

static int pulse_observe_epoch(struct app *app, struct feedback_ctx *ctx)
{
	if (!app->pulse.enabled)
		return 0;
	struct pulse_task_state state = {0};
	uint64_t epoch_id = ctx->interaction_id;
	uint32_t pid = current_tid();
	int fd = pulse_task_fd(app);
	if (bpf_map_lookup_elem(fd, &pid, &state) < 0 || state.epoch_id != epoch_id)
		return -1;
	uint64_t now = clock_ns(CLOCK_MONOTONIC);
	if (state.active && now >= state.deadline_ns) {
		state.active = 0;
		state.end_ns = state.deadline_ns;
		state.termination_reason = 2;
		if (bpf_map_update_elem(fd, &pid, &state, BPF_ANY) < 0)
			return -1;
	}
	if (!state.active)
		pulse_invalidate_epoch(app, epoch_id, state.termination_reason, state.end_ns);
	ctx->scheduler_epoch_end_ns = state.end_ns;
	ctx->scheduler_boosted_runtime_ns = state.boosted_runtime_ns;
	ctx->scheduler_normal_enqueues = state.normal_enqueues;
	ctx->scheduler_urgent_enqueues = state.urgent_enqueues;
	ctx->scheduler_termination_reason = state.termination_reason;
	printf("{\"type\":\"scheduler_epoch\",\"interaction_id\":%llu,"
	       "\"active\":%u,\"end_ns\":%llu,\"termination_reason\":%u,"
	       "\"termination_reason_name\":\"%s\",\"boosted_runtime_ns\":%llu,"
	       "\"normal_enqueues\":%llu,\"urgent_enqueues\":%llu}\n",
	       (unsigned long long)epoch_id, state.active,
	       (unsigned long long)state.end_ns, state.termination_reason,
	       termination_reason_name(state.termination_reason),
	       (unsigned long long)state.boosted_runtime_ns,
	       (unsigned long long)state.normal_enqueues,
	       (unsigned long long)state.urgent_enqueues);
	fflush(stdout);
	pulse_emit_epoch_members(app, epoch_id);
	printf("{\"type\":\"propagation_metrics\",\"interaction_id\":%llu,"
	       "\"attempts\":%llu,\"successful_inherited_boosts\":%llu,"
	       "\"depth_rejected\":%llu,\"inactive_rejected\":%llu,"
	       "\"inherited_urgent_enqueues\":%llu,"
	       "\"inherited_urgent_dispatches\":%llu}\n",
	       (unsigned long long)epoch_id,
	       (unsigned long long)pulse_metric_total(app, 6),
	       (unsigned long long)pulse_metric_total(app, 7),
	       (unsigned long long)pulse_metric_total(app, 8),
	       (unsigned long long)pulse_metric_total(app, 9),
	       (unsigned long long)pulse_metric_total(app, 10),
	       (unsigned long long)pulse_metric_total(app, 11));
	fflush(stdout);
	return 0;
}

static void feedback_sync_output(void *data, struct wp_presentation_feedback *feedback,
				 struct wl_output *output)
{
	(void)data; (void)feedback; (void)output;
}

static void feedback_presented(void *data, struct wp_presentation_feedback *feedback,
				       uint32_t sec_hi, uint32_t sec_lo, uint32_t nsec,
				       uint32_t refresh, uint32_t seq_hi, uint32_t seq_lo,
				       uint32_t flags)
{
	struct feedback_ctx *ctx = data;
	ctx->refresh_ns = refresh;
	(void)seq_hi; (void)seq_lo;
	ctx->feedback_received_ns = clock_ns(CLOCK_MONOTONIC);
	wp_presentation_feedback_destroy(feedback);
	ctx->feedback = NULL;
	ctx->app->pending = NULL;
	ctx->presentation_ns = (((uint64_t)sec_hi << 32) | sec_lo) * 1000000000ULL + nsec;
	ctx->flags = flags;
	if (ctx->completed) {
		emit_error(ctx->app, "late-feedback", "presented-after-timeout");
		free(ctx);
		return;
	}
	if (ctx->closed_loop && ctx->app->pulse.enabled &&
	    pulse_terminate(ctx->app, ctx->interaction_id, 1) < 0) {
		emit_error(ctx->app, "protocol-error", "epoch-id-mismatch-on-presentation");
		free(ctx);
		return;
	}
	ctx->completed = 1;
	if (pulse_observe_epoch(ctx->app, ctx) < 0) {
		emit_error(ctx->app, "protocol-error", "epoch-observation-failed");
		free(ctx);
		return;
	}
	emit_result(ctx, RESULT_PRESENTED);
	ctx->app->pending = NULL;
	ctx->app->completed_trials++;
	free(ctx);
}

static void feedback_discarded(void *data, struct wp_presentation_feedback *feedback)
{
	struct feedback_ctx *ctx = data;
	ctx->feedback_received_ns = clock_ns(CLOCK_MONOTONIC);
	wp_presentation_feedback_destroy(feedback);
	ctx->feedback = NULL;
	ctx->app->pending = NULL;
	if (pulse_terminate(ctx->app, ctx->interaction_id, 3) < 0)
		emit_error(ctx->app, "protocol-error", "discard-termination-failed");
	if (ctx->completed) {
		emit_error(ctx->app, "late-feedback", "discarded-after-timeout");
		free(ctx);
		return;
	}
	ctx->completed = 1;
	if (pulse_observe_epoch(ctx->app, ctx) < 0) {
		emit_error(ctx->app, "protocol-error", "epoch-observation-failed");
		free(ctx);
		return;
	}
	emit_result(ctx, RESULT_DISCARDED);
	ctx->app->pending = NULL;
	ctx->app->completed_trials++;
	free(ctx);
}

static const struct wp_presentation_feedback_listener feedback_listener = {
	.sync_output = feedback_sync_output,
	.presented = feedback_presented,
	.discarded = feedback_discarded,
};

static void wm_ping(void *data, struct xdg_wm_base *wm_base, uint32_t serial)
{
	(void)data;
	xdg_wm_base_pong(wm_base, serial);
}

static const struct xdg_wm_base_listener wm_listener = { .ping = wm_ping };

static void xdg_configure(void *data, struct xdg_surface *surface, uint32_t serial)
{
	struct app *app = data;
	(void)surface;
	xdg_surface_ack_configure(app->xdg_surface, serial);
	app->configured = 1;
}

static const struct xdg_surface_listener xdg_surface_listener = {
	.configure = xdg_configure,
};

static void toplevel_configure(void *data, struct xdg_toplevel *toplevel,
				       int32_t width, int32_t height, struct wl_array *states)
{
	struct app *app = data;
	uint32_t *state;
	(void)toplevel;
	app->surface_activated = app->surface_suspended = 0;
	wl_array_for_each(state, states) {
		app->surface_activated |= *state == XDG_TOPLEVEL_STATE_ACTIVATED;
		app->surface_suspended |= *state == XDG_TOPLEVEL_STATE_SUSPENDED;
	}
	app->surface_state_received_ns = clock_ns(CLOCK_MONOTONIC);
	printf("{\"type\":\"surface_state\",\"received_ns\":%llu,\"interaction_id\":%llu,"
	       "\"xdg_version\":%u,\"width\":%d,\"height\":%d,"
	       "\"activated\":%s,\"suspended\":%s}\n",
	       (unsigned long long)app->surface_state_received_ns,
	       (unsigned long long)(app->pending ? app->pending->interaction_id : 0),
	       app->xdg_version, width, height,
	       app->surface_activated ? "true" : "false",
	       app->xdg_version < 6 ? "null" : app->surface_suspended ? "true" : "false");
	fflush(stdout);
}

static void toplevel_close(void *data, struct xdg_toplevel *toplevel)
{
	struct app *app = data;
	(void)toplevel;
	emit_error(app, "window-closed", "compositor-closed-benchmark-surface");
	app->running = 0;
}

static void toplevel_configure_bounds(void *data, struct xdg_toplevel *toplevel,
					      int32_t width, int32_t height)
{
	(void)data; (void)toplevel; (void)width; (void)height;
}

static void toplevel_wm_capabilities(void *data, struct xdg_toplevel *toplevel,
					     struct wl_array *capabilities)
{
	(void)data; (void)toplevel; (void)capabilities;
}

static const struct xdg_toplevel_listener toplevel_listener = {
	.configure = toplevel_configure,
	.close = toplevel_close,
	.configure_bounds = toplevel_configure_bounds,
	.wm_capabilities = toplevel_wm_capabilities,
};

static void pointer_enter(void *data, struct wl_pointer *pointer, uint32_t serial,
				  struct wl_surface *surface, wl_fixed_t sx, wl_fixed_t sy)
{
	(void)data; (void)pointer; (void)serial; (void)surface; (void)sx; (void)sy;
}

static void pointer_leave(void *data, struct wl_pointer *pointer, uint32_t serial,
				  struct wl_surface *surface)
{
	(void)data; (void)pointer; (void)serial; (void)surface;
}

static void pointer_motion(void *data, struct wl_pointer *pointer, uint32_t time,
				   wl_fixed_t sx, wl_fixed_t sy)
{
	(void)data; (void)pointer; (void)time; (void)sx; (void)sy;
}

static void pointer_button(void *data, struct wl_pointer *pointer, uint32_t serial,
				   uint32_t time, uint32_t button, uint32_t state)
{
	struct app *app = data;
	(void)pointer; (void)serial; (void)time; (void)button;
#ifdef PULSE_TRANSITION
	if (state == WL_POINTER_BUTTON_STATE_PRESSED)
		emit_error(app, "protocol-error", "manual-input-during-planned-workload");
	return;
#endif
	if (state == WL_POINTER_BUTTON_STATE_PRESSED) {
		if (app->pending || !free_buffer(app))
			emit_notice(app, "input-ignored", "feedback-still-pending");
		else if (app->completed_trials < app->requested_trials)
			start_trial(app);
	}
}

static void pointer_axis(void *data, struct wl_pointer *pointer, uint32_t time,
				 uint32_t axis, wl_fixed_t value)
{
	(void)data; (void)pointer; (void)time; (void)axis; (void)value;
}

static void pointer_frame(void *data, struct wl_pointer *pointer)
{
	(void)data; (void)pointer;
}

static void pointer_axis_source(void *data, struct wl_pointer *pointer, uint32_t source)
{
	(void)data; (void)pointer; (void)source;
}

static void pointer_axis_stop(void *data, struct wl_pointer *pointer, uint32_t time,
				      uint32_t axis)
{
	(void)data; (void)pointer; (void)time; (void)axis;
}

static void pointer_axis_discrete(void *data, struct wl_pointer *pointer, uint32_t axis,
					 int32_t discrete)
{
	(void)data; (void)pointer; (void)axis; (void)discrete;
}

static const struct wl_pointer_listener pointer_listener = {
	.enter = pointer_enter, .leave = pointer_leave, .motion = pointer_motion,
	.button = pointer_button, .axis = pointer_axis, .frame = pointer_frame,
	.axis_source = pointer_axis_source, .axis_stop = pointer_axis_stop,
	.axis_discrete = pointer_axis_discrete,
};

static void keyboard_keymap(void *data, struct wl_keyboard *keyboard, uint32_t format,
				    int32_t fd, uint32_t size)
{
	(void)data; (void)keyboard; (void)format; (void)size;
	close(fd);
}

static void keyboard_enter(void *data, struct wl_keyboard *keyboard, uint32_t serial,
				   struct wl_surface *surface, struct wl_array *keys)
{
	(void)data; (void)keyboard; (void)serial; (void)surface; (void)keys;
}

static void keyboard_leave(void *data, struct wl_keyboard *keyboard, uint32_t serial,
				   struct wl_surface *surface)
{
	(void)data; (void)keyboard; (void)serial; (void)surface;
}

static void keyboard_key(void *data, struct wl_keyboard *keyboard, uint32_t serial,
				 uint32_t time, uint32_t key, uint32_t state)
{
	struct app *app = data;
	(void)keyboard; (void)serial; (void)time; (void)key;
#ifdef PULSE_TRANSITION
	if (state == WL_KEYBOARD_KEY_STATE_PRESSED)
		emit_error(app, "protocol-error", "manual-input-during-planned-workload");
	return;
#endif
	if (state == WL_KEYBOARD_KEY_STATE_PRESSED && !app->pending && free_buffer(app) &&
	    app->completed_trials < app->requested_trials)
		start_trial(app);
	else if (state == WL_KEYBOARD_KEY_STATE_PRESSED && (app->pending || !free_buffer(app)))
		emit_notice(app, "input-ignored", "feedback-still-pending");
}

static void keyboard_modifiers(void *data, struct wl_keyboard *keyboard, uint32_t serial,
				       uint32_t mods_depressed, uint32_t mods_latched,
				       uint32_t mods_locked, uint32_t group)
{
	(void)data; (void)keyboard; (void)serial; (void)mods_depressed;
	(void)mods_latched; (void)mods_locked; (void)group;
}

static void keyboard_repeat_info(void *data, struct wl_keyboard *keyboard,
					 int32_t rate, int32_t delay)
{
	(void)data; (void)keyboard; (void)rate; (void)delay;
}

static const struct wl_keyboard_listener keyboard_listener = {
	.keymap = keyboard_keymap, .enter = keyboard_enter, .leave = keyboard_leave,
	.key = keyboard_key, .modifiers = keyboard_modifiers,
	.repeat_info = keyboard_repeat_info,
};

static void seat_capabilities(void *data, struct wl_seat *seat, uint32_t capabilities)
{
	struct app *app = data;
	if (capabilities & WL_SEAT_CAPABILITY_POINTER) {
		struct wl_pointer *pointer = wl_seat_get_pointer(seat);
		wl_pointer_add_listener(pointer, &pointer_listener, app);
	}
	if (capabilities & WL_SEAT_CAPABILITY_KEYBOARD) {
		struct wl_keyboard *keyboard = wl_seat_get_keyboard(seat);
		wl_keyboard_add_listener(keyboard, &keyboard_listener, app);
	}
}

static void seat_name(void *data, struct wl_seat *seat, const char *name)
{
	(void)data; (void)seat; (void)name;
}

static const struct wl_seat_listener seat_listener = {
	.capabilities = seat_capabilities, .name = seat_name,
};

static void presentation_clock(void *data, struct wp_presentation *presentation,
				       uint32_t clock_id)
{
	struct app *app = data;
	(void)presentation;
	app->presentation_clock_id = clock_id;
	app->presentation_clock_seen = 1;
	printf("{\"type\":\"capability\",\"protocol\":\"wp_presentation\","
	       "\"version\":%u,\"clock_id\":%u,\"clock_name\":\"%s\"}\n",
	       app->presentation_version, clock_id,
	       clock_id == CLOCK_MONOTONIC ? "CLOCK_MONOTONIC" : "unknown");
	fflush(stdout);
}

static const struct wp_presentation_listener presentation_listener = {
	.clock_id = presentation_clock,
};

static void registry_global(void *data, struct wl_registry *registry, uint32_t name,
				    const char *interface, uint32_t version)
{
	struct app *app = data;
	if (!strcmp(interface, "wl_compositor"))
		app->compositor = wl_registry_bind(registry, name, &wl_compositor_interface,
						   version < 4 ? version : 4);
	else if (!strcmp(interface, "wl_shm"))
		app->shm = wl_registry_bind(registry, name, &wl_shm_interface, 1);
	else if (!strcmp(interface, "wl_seat")) {
		app->seat = wl_registry_bind(registry, name, &wl_seat_interface,
						 version < 7 ? version : 7);
		wl_seat_add_listener(app->seat, &seat_listener, app);
	} else if (!strcmp(interface, "xdg_wm_base")) {
		app->xdg_version = version < 7 ? version : 7;
		app->wm_base = wl_registry_bind(registry, name, &xdg_wm_base_interface,
						 app->xdg_version);
		xdg_wm_base_add_listener(app->wm_base, &wm_listener, app);
	} else if (!strcmp(interface, "wp_presentation")) {
		app->presentation_version = version < 2 ? version : 2;
		app->presentation = wl_registry_bind(registry, name,
						     &wp_presentation_interface,
						     app->presentation_version);
		wp_presentation_add_listener(app->presentation, &presentation_listener, app);
	}
}

static void registry_remove(void *data, struct wl_registry *registry, uint32_t name)
{
	(void)data; (void)registry; (void)name;
}

static const struct wl_registry_listener registry_listener = {
	.global = registry_global, .global_remove = registry_remove,
};

static void buffer_release(void *data, struct wl_buffer *buffer)
{
	(void)buffer;
	((struct frame_buffer *)data)->busy = 0;
}
static const struct wl_buffer_listener buffer_listener = { .release = buffer_release };

static int create_buffer(struct app *app)
{
	const int width = 640, height = 360;
	const size_t size = (size_t)width * height * 4;
	app->shm_fd = syscall(SYS_memfd_create, "pulsesched-wayland", MFD_CLOEXEC);
	if (app->shm_fd < 0 || ftruncate(app->shm_fd, (off_t)(2 * size)) < 0)
		return -1;
	void *pixels = mmap(NULL, 2 * size, PROT_READ | PROT_WRITE, MAP_SHARED, app->shm_fd, 0);
	if (pixels == MAP_FAILED)
		return -1;
	struct wl_shm_pool *pool = wl_shm_create_pool(app->shm, app->shm_fd, (int)(2 * size));
	for (int i = 0; i < 2; i++) {
		app->buffers[i].pixels = (char *)pixels + i * size;
		app->buffers[i].buffer = wl_shm_pool_create_buffer(pool, (int)(i * size),
			width, height, width * 4, WL_SHM_FORMAT_XRGB8888);
		if (!app->buffers[i].buffer) { wl_shm_pool_destroy(pool); return -1; }
		wl_buffer_add_listener(app->buffers[i].buffer, &buffer_listener, &app->buffers[i]);
	}
	wl_shm_pool_destroy(pool);
	return 0;
}

static void draw_frame(struct app *app, uint64_t id)
{
	uint32_t color = (uint32_t)(id * 2654435761U) & 0x00ffffffU;
	app->render_buffer = free_buffer(app);
	uint32_t *pixels = app->render_buffer->pixels;
	for (size_t i = 0; i < (size_t)640 * 360; i++)
		pixels[i] = 0xff000000U | color;
}

static void start_trial(struct app *app)
{
	struct feedback_ctx *ctx = calloc(1, sizeof(*ctx));
	if (!ctx) {
		emit_error(app, "protocol-error", "out-of-memory");
		return;
	}
	ctx->app = app;
	ctx->interaction_id = ++app->next_interaction_id;
	ctx->closed_loop = app->interleaved
		? ((ctx->interaction_id % 2) == 0) : app->closed_loop;
	ctx->input_ns = clock_ns(CLOCK_MONOTONIC);
#ifdef PULSE_TRANSITION
	printf("{\"type\":\"planned_input\",\"interaction_id\":%llu,"
	       "\"due_ns\":%llu,\"dispatch_ns\":%llu}\n",
	       (unsigned long long)ctx->interaction_id,
	       (unsigned long long)app->next_auto_ns, (unsigned long long)ctx->input_ns);
#endif
	ctx->work_start_ns = clock_ns(CLOCK_MONOTONIC);
	if (app->pulse.enabled && pulse_start_epoch(app, ctx) < 0) {
		emit_error(app, "protocol-error", "pulsesched-epoch-start-failed");
		free(ctx);
		return;
	}
	if (app->dependency_mode) {
		uint64_t command = 1;
		uint64_t done = 0;
		app->worker_sent_ns[0] = clock_ns(CLOCK_MONOTONIC);
		if (write(app->worker_wake_fd[0], &command, sizeof(command)) != sizeof(command) ||
		    read(app->worker_done_fd[0], &done, sizeof(done)) != sizeof(done)) {
			emit_error(app, "protocol-error", "dependency-worker-io-failed");
			free(ctx);
			return;
		}
	} else {
		busy_ms(app->work_ms);
	}
	draw_frame(app, ctx->interaction_id);
	ctx->commit_ns = clock_ns(CLOCK_MONOTONIC);
	ctx->feedback_request_ns = clock_ns(CLOCK_MONOTONIC);
	struct wp_presentation_feedback *feedback =
		wp_presentation_feedback(app->presentation, app->surface);
	if (!feedback) {
		free(ctx);
		emit_error(app, "protocol-error", "feedback-object-not-created");
		return;
	}
	ctx->feedback = feedback;
	wp_presentation_feedback_add_listener(feedback, &feedback_listener, ctx);
	app->render_buffer->busy = 1;
	wl_surface_attach(app->surface, app->render_buffer->buffer, 0, 0);
	wl_surface_damage_buffer(app->surface, 0, 0, 640, 360);
	wl_surface_commit(app->surface);
	if (wl_display_flush(app->display) < 0 && errno != EAGAIN)
		emit_error(app, "protocol-error", "commit-flush-failed");
	app->pending = ctx;
	printf("{\"type\":\"interaction\",\"interaction_id\":%llu,"
	       "\"scheduler_mode\":\"%s\",\"input_ns\":%llu,"
	       "\"work_start_ns\":%llu,\"commit_ns\":%llu,"
	       "\"feedback_request_ns\":%llu,\"epoch_register_ns\":%llu}\n",
	       (unsigned long long)ctx->interaction_id,
	       ctx->closed_loop ? "closed" : (app->pulse.enabled ? "ttl" : "normal"),
	       (unsigned long long)ctx->input_ns,
	       (unsigned long long)ctx->work_start_ns,
       (unsigned long long)ctx->commit_ns,
       (unsigned long long)ctx->feedback_request_ns,
       (unsigned long long)ctx->epoch_register_ns);
	fflush(stdout);
}

static void maybe_timeout(struct app *app)
{
	struct feedback_ctx *ctx = app->pending;
	uint64_t now = clock_ns(CLOCK_MONOTONIC);
	if (!ctx || now - ctx->commit_ns <
		    app->feedback_timeout_ms * 1000000ULL)
		return;
	/* Observed callback noncompletion is not proof of a missing display scanout.
	 * These state events are advisory; never skip trials or relax the gate. */
	printf("{\"type\":\"feedback_timeout_diagnostic\",\"interaction_id\":%llu,"
	       "\"observed_ns\":%llu,\"commit_ns\":%llu,\"wait_ns\":%llu,"
	       "\"limit_ms\":%llu,\"surface_state_received_ns\":%llu,"
	       "\"xdg_version\":%u,\"suspended\":%s,\"buffers_busy\":[%d,%d]}\n",
	       (unsigned long long)ctx->interaction_id, (unsigned long long)now,
	       (unsigned long long)ctx->commit_ns, (unsigned long long)(now - ctx->commit_ns),
	       (unsigned long long)app->feedback_timeout_ms,
	       (unsigned long long)app->surface_state_received_ns, app->xdg_version,
	       app->xdg_version < 6 || !app->surface_state_received_ns ? "null" :
	       app->surface_suspended ? "true" : "false",
	       app->buffers[0].busy, app->buffers[1].busy);
	wp_presentation_feedback_destroy(ctx->feedback);
	ctx->feedback = NULL;
	app->pending = NULL;
	if (pulse_terminate(app, ctx->interaction_id, 2) < 0)
		emit_error(app, "protocol-error", "timeout-termination-failed");
	ctx->timed_out = 1;
	ctx->completed = 1;
	if (pulse_observe_epoch(app, ctx) < 0) {
		emit_error(app, "protocol-error", "epoch-observation-failed");
		app->pending = NULL;
		free(ctx);
		return;
	}
	emit_result(ctx, RESULT_TIMEOUT);
	app->pending = NULL;
	app->completed_trials++;
	free(ctx);
}

static int parse_u64(const char *text, uint64_t *value)
{
	char *end = NULL;
	errno = 0;
	unsigned long long parsed = strtoull(text, &end, 10);
	if (errno || !end || *end || !parsed)
		return -1;
	*value = parsed;
	return 0;
}

static int parse_u64_allow_zero(const char *text, uint64_t *value)
{
	char *end = NULL;
	errno = 0;
	unsigned long long parsed = strtoull(text, &end, 10);
	if (errno || !end || *end)
		return -1;
	*value = parsed;
	return 0;
}

/* Exercise the real chain and teardown without Wayland or BPF privileges. */
static int worker_selftest(void)
{
	struct app app = {.dependency_mode=1, .work_ms=2};
	if (dependency_start(&app) < 0) {
		dependency_stop(&app);
		return 1;
	}
	for (int trial = 0; trial < 5; trial++) {
		uint64_t command = 1, done = 0;
		app.worker_sent_ns[0] = clock_ns(CLOCK_MONOTONIC);
		if (write(app.worker_wake_fd[0], &command, sizeof(command)) != sizeof(command) ||
		    read(app.worker_done_fd[0], &done, sizeof(done)) != sizeof(done)) return 1;
		for (int i = 0; i < 4; i++)
			if (app.worker_cpu_ns[i] < 2000000 ||
			    app.worker_end_ns[i] < app.worker_start_ns[i] ||
			    app.worker_start_ns[i] < app.worker_sent_ns[i]) return 1;
	}
	dependency_stop(&app);
	puts("worker selftest passed: five equal-work chains and clean teardown");
	return app.protocol_error ? 1 : 0;
}

int main(int argc, char **argv)
{
	if (argc == 2 && !strcmp(argv[1], "--selftest-workers"))
		return worker_selftest();
	struct app app = { .running = 1, .work_ms = 20, .feedback_timeout_ms = 1000,
				  .requested_trials = 1, .pulse_ttl_ms = 0,
				  .max_propagation_depth = 2 };
	if (argc > 1 && parse_u64(argv[1], &app.work_ms) < 0)
		return 2;
	if (argc > 2 && parse_u64(argv[2], &app.requested_trials) < 0)
		return 2;
	if (argc > 3 && parse_u64(argv[3], &app.feedback_timeout_ms) < 0)
		return 2;
	if (argc > 4 && parse_u64_allow_zero(argv[4], &app.pulse_ttl_ms) < 0)
		return 2;
	const char *depth_env = getenv("PULSESCHED_MAX_PROPAGATION_DEPTH");
	if (depth_env && *depth_env) {
		uint64_t depth = 0;
		if (parse_u64_allow_zero(depth_env, &depth) < 0 || depth > 32)
			return 2;
		app.max_propagation_depth = (uint32_t)depth;
	}
	if (argc > 5) {
		if (!strcmp(argv[5], "dep-normal"))
			app.dependency_mode = 1;
		else if (!strcmp(argv[5], "closed"))
			app.closed_loop = 1;
		else if (!strcmp(argv[5], "ab"))
			app.interleaved = 1;
		else if (!strcmp(argv[5], "dep-off")) {
			app.closed_loop = 1;
			app.dependency_mode = 1;
		} else if (!strcmp(argv[5], "dep-on")) {
			app.closed_loop = 1;
			app.dependency_mode = 1;
			app.propagation_enabled = 1;
		}
		else if (strcmp(argv[5], "ttl"))
			return 2;
	}
	if (app.pulse_ttl_ms > 10000 || app.work_ms > 10000 ||
	    app.feedback_timeout_ms > 60000 ||
	    ((app.closed_loop || app.interleaved) && !app.pulse_ttl_ms))
		return 2;
	const char *auto_ms = getenv("PULSESCHED_AUTORUN_MS");
	if (auto_ms && (parse_u64(auto_ms, &app.auto_interval_ms) < 0 || app.auto_interval_ms > 60000))
		return 2;
	signal(SIGINT, on_signal);
	signal(SIGTERM, on_signal);
	app.display = wl_display_connect(NULL);
	if (!app.display) {
		fprintf(stderr, "wayland connect failed\n");
		return 3;
	}
	app.registry = wl_display_get_registry(app.display);
	wl_registry_add_listener(app.registry, &registry_listener, &app);
	if (wl_display_roundtrip(app.display) < 0 || !app.compositor || !app.shm ||
	    !app.wm_base || !app.seat || !app.presentation) {
		emit_error(&app, "protocol-error", "required-global-missing");
		return 4;
	}
	if (wl_display_roundtrip(app.display) < 0 || !app.presentation_clock_seen || app.presentation_clock_id != CLOCK_MONOTONIC) {
		emit_error(&app, "protocol-error", "presentation-clock-missing-or-not-monotonic");
		return 4;
	}
	if (app.pulse_ttl_ms && pulse_init(&app) < 0) {
		emit_error(&app, "protocol-error", "pulsesched-init-failed");
		pulse_cleanup(&app);
		return 5;
	}
	if (dependency_start(&app) < 0) {
		emit_error(&app, "protocol-error", "dependency-worker-start-failed");
		dependency_stop(&app);
		pulse_cleanup(&app);
		return 5;
	}
	printf("{\"type\":\"workload\",\"version\":2,\"work_clock\":\"thread-cpu\","
	       "\"stages\":%d,\"work_ms_per_stage\":%llu,\"requested_trials\":%llu,"
	       "\"input_origin\":\"%s\",\"partial\":%s,\"trace_delay\":%s}\n",
	       app.dependency_mode ? 4 : 1, (unsigned long long)app.work_ms,
	       (unsigned long long)app.requested_trials,
	       app.auto_interval_ms ? "synthetic-client-dispatch" : "wayland-client-callback",
	       getenv("PULSESCHED_PARTIAL") ? "true" : "false",
	       getenv("PULSESCHED_TRACE_DELAY") ? "true" : "false");
	app.surface = wl_compositor_create_surface(app.compositor);
	app.xdg_surface = xdg_wm_base_get_xdg_surface(app.wm_base, app.surface);
	app.toplevel = xdg_surface_get_toplevel(app.xdg_surface);
	xdg_surface_add_listener(app.xdg_surface, &xdg_surface_listener, &app);
	xdg_toplevel_add_listener(app.toplevel, &toplevel_listener, &app);
	xdg_toplevel_set_title(app.toplevel, "PulseSched Phase 3 controlled benchmark");
	xdg_toplevel_set_app_id(app.toplevel, "pulsesched-wayland-bench");
	if (create_buffer(&app) < 0) {
		emit_error(&app, "protocol-error", "shared-memory-buffer-failed");
		return 5;
	}
	wl_surface_commit(app.surface);
	while (!interrupted && app.running && !app.configured && wl_display_dispatch(app.display) >= 0)
		;
	if (!app.configured)
		return 6;
	draw_frame(&app, 0);
	app.render_buffer->busy = 1;
	wl_surface_attach(app.surface, app.render_buffer->buffer, 0, 0);
	wl_surface_damage_buffer(app.surface, 0, 0, 640, 360);
	wl_surface_commit(app.surface);
#ifdef PULSE_TRANSITION
	/* Separate experimental build: all clients receive the same absolute epoch.
	 * No change to the original benchmark's input schedule. */
	const char *barrier_text = getenv("PROPHET_START_FD");
	const char *duration_text = getenv("PROPHET_DURATION_NS");
	uint64_t barrier_fd, epoch_ns = 0, transition_duration;
	if (!app.auto_interval_ms || !barrier_text ||
	    parse_u64(barrier_text, &barrier_fd) < 0 || barrier_fd > 1048576 ||
	    !duration_text || parse_u64(duration_text, &transition_duration) < 0 ||
	    transition_duration > 2048000000000ULL)
		return 9;
	printf("{\"type\":\"transition_ready\",\"pid\":%d}\n", getpid());
	fflush(stdout);
	if (read((int)barrier_fd, &epoch_ns, sizeof(epoch_ns)) != sizeof(epoch_ns) || !epoch_ns)
		return 9;
	close((int)barrier_fd);
	app.next_auto_ns = epoch_ns + 250000000ULL;
	printf("{\"type\":\"transition_epoch\",\"epoch_ns\":%llu,\"observed_ns\":%llu}\n",
	       (unsigned long long)epoch_ns, (unsigned long long)clock_ns(CLOCK_MONOTONIC));
	fflush(stdout);
#endif
	while (!interrupted && app.running && !app.protocol_error &&
	       app.completed_trials < app.requested_trials) {
		if (wl_display_dispatch_pending(app.display) < 0)
			break;
		maybe_timeout(&app);
		if (app.pulse.enabled && pulse_exited(&app)) {
			emit_error(&app, "protocol-error", "scheduler-exited-during-trial");
			break;
		}
		if (app.auto_interval_ms && !app.pending && free_buffer(&app) &&
		    app.completed_trials < app.requested_trials && clock_ns(CLOCK_MONOTONIC) >= app.next_auto_ns) {
			start_trial(&app);
#ifdef PULSE_TRANSITION
			app.next_auto_ns += app.auto_interval_ms * 1000000ULL;
#else
			app.next_auto_ns = clock_ns(CLOCK_MONOTONIC) + app.auto_interval_ms * 1000000ULL;
#endif
		}
		if (app.completed_trials >= app.requested_trials || app.protocol_error)
			break;
		if (wl_display_flush(app.display) < 0 && errno != EAGAIN) {
			emit_error(&app, "protocol-error", "wayland-display-flush-failed");
			break;
		}
		struct pollfd pfd = { .fd = wl_display_get_fd(app.display), .events = POLLIN };
		int timeout = app.auto_interval_ms ? 10 : 1000;
		if (app.pending) {
			uint64_t now = clock_ns(CLOCK_MONOTONIC);
			uint64_t deadline = app.pending->commit_ns +
				app.feedback_timeout_ms * 1000000ULL;
			uint64_t remaining_ms = deadline > now
				? (deadline - now + 999999ULL) / 1000000ULL : 0;
			timeout = remaining_ms < 20 ? (int)remaining_ms : 20;
		}
		int ret = poll(&pfd, 1, timeout);
		if (ret < 0 && errno == EINTR)
			continue;
		if (ret < 0)
			break;
		if (ret > 0 && wl_display_dispatch(app.display) < 0) {
			emit_error(&app, "protocol-error", "wayland-display-dispatch-failed");
			break;
		}
	}
#ifdef PULSE_TRANSITION
	/* Retain the embedded Pulse owner through the complete offered-load period. */
	if (!app.protocol_error && app.completed_trials == app.requested_trials) {
		uint64_t end = epoch_ns + transition_duration;
		struct timespec until = {.tv_sec=end/1000000000ULL, .tv_nsec=end%1000000000ULL};
		while (!interrupted && clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &until, NULL) == EINTR) {}
	}
#endif
	if (app.pending) {
		pulse_terminate(&app, app.pending->interaction_id, 3);
		wp_presentation_feedback_destroy(app.pending->feedback);
		free(app.pending);
		app.pending = NULL;
	}
	dependency_stop(&app);
	pulse_cleanup(&app);
	if (wl_display_get_error(app.display))
		emit_error(&app, "protocol-error", "wayland-display-error");
	return app.protocol_error ? 7 : (app.completed_trials == app.requested_trials ? 0 : 8);
}
