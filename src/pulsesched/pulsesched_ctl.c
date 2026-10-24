/* SPDX-License-Identifier: GPL-2.0 */
/* Minimal Phase 2 userspace control/test program. */
#include <errno.h>
#include <inttypes.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
#include <bpf/bpf.h>
#include <scx/common.h>
#include "pulsesched.bpf.skel.h"

/* glibc's sched.h on this host omits the newer kernel UAPI constant. */
#ifndef SCHED_EXT
#define SCHED_EXT 7
#endif

static uint64_t mono_ns(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (uint64_t)ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}

static void busy_ms(uint64_t ms)
{
	uint64_t end = mono_ns() + ms * 1000000ULL;
	volatile uint64_t x = 0;
	while (mono_ns() < end)
		x = x * 1664525ULL + 1013904223ULL;
	(void)x;
}

static int set_ext(void)
{
	struct sched_param param = { .sched_priority = 0 };
	if (sched_setscheduler(0, SCHED_EXT, &param) < 0) {
		perror("sched_setscheduler(SCHED_EXT)");
		return -1;
	}
	return 0;
}

#include "pulse_policy.h"

static int update_state(int fd, uint32_t pid, struct pulse_task_state *state)
{
	return bpf_map_update_elem(fd, &pid, state, BPF_ANY);
}

int main(int argc, char **argv)
{
	int stale_test = argc > 1 && strcmp(argv[1], "--stale") == 0;
	uint64_t ttl_ms = argc > 1 ? strtoull(argv[1], NULL, 10) : 100;
	uint64_t work_ms = argc > 2 ? strtoull(argv[2], NULL, 10) : 20;
	uint64_t epochs = argc > 3 ? strtoull(argv[3], NULL, 10) : 1;
	struct pulsesched *skel;
	struct bpf_link *link;
	struct pulse_task_state state = {0};
	uint32_t pid = (uint32_t)getpid();
	uint64_t before, after;

	if (!stale_test && (!ttl_ms || ttl_ms > 10000 || !work_ms || !epochs || epochs > 100)) {
		fprintf(stderr, "invalid ttl/work/epochs; ttl 1..10000 ms, work > 0, epochs 1..100\n");
		return 2;
	}
	libbpf_set_strict_mode(LIBBPF_STRICT_ALL);
	skel = SCX_OPS_OPEN(pulsesched_ops, pulsesched);
	SCX_OPS_LOAD(skel, pulsesched_ops, pulsesched, uei);
	link = SCX_OPS_ATTACH(skel, pulsesched_ops, pulsesched);
	if (set_ext() < 0)
		return 3;

	state.epoch_id = 0;
	if (update_state(bpf_map__fd(skel->maps.task_state), pid, &state) < 0) {
		perror("register task");
		return 4;
	}
	printf("registered pid=%u policy=SCHED_EXT\n", pid);
	if (stale_test) {
		uint64_t now = mono_ns();
		state.epoch_id = 99;
		state.start_ns = now - 1000000ULL;
		state.end_ns = 0;
		state.deadline_ns = now - 1;
		state.active = 1;
		state.termination_reason = 0;
		if (update_state(bpf_map__fd(skel->maps.task_state), pid, &state) < 0) {
			perror("stale registration");
			return 5;
		}
		printf("stale_record deadline_ns=%" PRIu64 "\n", (uint64_t)state.deadline_ns);
		busy_ms(50);
		if (bpf_map_lookup_elem(bpf_map__fd(skel->maps.task_state), &pid, &state) < 0)
			perror("read stale record");
		printf("stale_result active=%u reason=%u end_ns=%" PRIu64 "\n",
		       state.active, state.termination_reason, (uint64_t)state.end_ns);
		goto metrics;
	}
	printf("normal_work_start_ns=%" PRIu64 "\n", mono_ns());
	busy_ms(work_ms);

	for (uint64_t epoch = 1; epoch <= epochs; epoch++) {
		state.epoch_id = epoch;
		state.direct = 1;
		state.root_pid = pid;
		state.start_ns = mono_ns();
		state.end_ns = 0;
		state.deadline_ns = state.start_ns + ttl_ms * 1000000ULL;
		state.boosted_runtime_ns = 0;
		state.last_run_start_ns = 0;
		state.normal_enqueues = 0;
		state.urgent_enqueues = 0;
		state.urgent_dispatches = 0;
		state.active = 1;
		state.termination_reason = 0;
		before = state.start_ns;
		if (update_state(bpf_map__fd(skel->maps.task_state), pid, &state) < 0) {
			perror("start epoch");
			return 5;
		}
		printf("epoch=%" PRIu64 " start_ns=%" PRIu64 " ttl_ms=%" PRIu64 "\n",
		       epoch, before, ttl_ms);
		busy_ms(work_ms);

		for (int i = 0; i < 110; i++) {
			if (bpf_map_lookup_elem(bpf_map__fd(skel->maps.task_state), &pid, &state) < 0)
				break;
			if (!state.active)
				break;
			usleep(10000);
		}
		after = mono_ns();
		if (bpf_map_lookup_elem(bpf_map__fd(skel->maps.task_state), &pid, &state) < 0)
			perror("read epoch");
		printf("epoch=%" PRIu64 " end_ns=%" PRIu64 " active=%u reason=%u"
		       " boosted_runtime_ns=%" PRIu64 " urgent_enqueues=%" PRIu64 "\n",
		       epoch, (uint64_t)state.end_ns, state.active, state.termination_reason,
		       (uint64_t)state.boosted_runtime_ns, (uint64_t)state.urgent_enqueues);
		printf("control_read_ns=%" PRIu64 "\n", after);
	}

	metrics:
	int nr_cpus = libbpf_num_possible_cpus();
	uint64_t values[nr_cpus];
	uint32_t metric;
	for (metric = 0; metric < 12; metric++) {
		memset(values, 0, sizeof(values));
		if (bpf_map_lookup_elem(bpf_map__fd(skel->maps.metrics), &metric, values) == 0) {
			uint64_t total = 0;
			for (int cpu = 0; cpu < nr_cpus; cpu++)
				total += values[cpu];
			printf("metric[%u]=%" PRIu64 "\n", metric, total);
		}
	}

	bpf_link__destroy(link);
	__u64 ecode = UEI_REPORT(skel, uei);
	pulsesched__destroy(skel);
	return UEI_ECODE_USER(ecode) ? 1 : 0;
}
