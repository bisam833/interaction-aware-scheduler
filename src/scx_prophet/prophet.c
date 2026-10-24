/* SPDX-License-Identifier: GPL-2.0 */
#include <errno.h>
#include <getopt.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <bpf/bpf.h>
#include <scx/common.h>
#include "control.h"
#include "prophet.bpf.skel.h"

static volatile sig_atomic_t stop;
static void on_signal(int sig) { (void)sig; stop = 1; }
static __u64 ns(clockid_t clock)
{
	struct timespec t;
	if (clock_gettime(clock, &t)) { perror("clock_gettime"); exit(1); }
	return (__u64)t.tv_sec * 1000000000ULL + t.tv_nsec;
}

static int number(const char *s, int min, int max)
{
	char *end;
	errno = 0;
	long n = strtol(s, &end, 10);
	if (errno || !*s || *end || n < min || n > max) {
		fprintf(stderr, "invalid integer: %s (range %d..%d)\n", s, min, max);
		exit(2);
	}
	return n;
}

static void usage(const char *name)
{
	printf("usage: %s [--mode observe|adaptive|static] [--profile 0|1|2]\n"
	       "  [--poll-ms 20..200] [--duration 1..86400] [--no-telemetry]\n"
	       "  [--propagation] [--freeze-after-ms N]\n"
	       "Default observe: profile 0 with identical telemetry; no prediction.\n"
	       "Profiles: 0=20ms, 1=10ms, 2=5ms. Full-system; no RT bypass.\n"
	       "freeze-after-ms is a watchdog test: stop all config writes.\n", name);
}

static int disabled(void)
{
	char buf[32];
	FILE *f = fopen("/sys/kernel/sched_ext/state", "r");
	if (!f) return 0;
	int ok = fgets(buf, sizeof(buf), f) && !strcmp(buf, "disabled\n");
	fclose(f);
	return ok;
}

static __u32 map_id(int fd)
{
	struct bpf_map_info info = {};
	__u32 len = sizeof(info);
	if (bpf_obj_get_info_by_fd(fd, &info, &len)) return 0;
	return info.id;
}

int main(int argc, char **argv)
{
	const char *mode = "observe";
	int profile = 0, profile_set = 0, poll_ms = 100, duration = 0, freeze_ms = 0;
	bool telemetry = true, propagation = false;
	static const struct option opts[] = {
		{"mode", 1, 0, 'm'}, {"profile", 1, 0, 'p'},
		{"poll-ms", 1, 0, 'i'}, {"duration", 1, 0, 'd'},
		{"freeze-after-ms", 1, 0, 'f'}, {"no-telemetry", 0, 0, 'n'},
		{"propagation", 0, 0, 'P'}, {"help", 0, 0, 'h'}, {0}
	};
	int opt;
	while ((opt = getopt_long(argc, argv, "", opts, NULL)) != -1) {
		switch (opt) {
		case 'm': mode = optarg; break;
		case 'p': profile = number(optarg, 0, 2); profile_set = 1; break;
		case 'i': poll_ms = number(optarg, 20, 200); break;
		case 'd': duration = number(optarg, 1, 86400); break;
		case 'f': freeze_ms = number(optarg, 1, 86400000); break;
		case 'n': telemetry = false; break;
		case 'P': propagation = true; break;
		case 'h': usage(argv[0]); return 0;
		default: usage(argv[0]); return 2;
		}
	}
	bool adaptive = !strcmp(mode, "adaptive"), fixed = !strcmp(mode, "static");
	if (optind != argc || (!adaptive && !fixed && strcmp(mode, "observe")) ||
	    (profile_set && !fixed) || (!telemetry && !fixed)) {
		usage(argv[0]); return 2;
	}
	if (!disabled()) {
		fprintf(stderr, "sched_ext must be disabled; refusing attachment\n"); return 1;
	}
	int ncpu = libbpf_num_possible_cpus();
	if (ncpu <= 0 || ncpu > 65536) return 1;
	struct prophet_stats *current = calloc(ncpu, sizeof(*current));
	struct prophet_stats *previous = calloc(ncpu, sizeof(*previous));
	if (!current || !previous) { free(current); free(previous); return 1; }
	setvbuf(stdout, NULL, _IOLBF, 0);
	signal(SIGINT, on_signal); signal(SIGTERM, on_signal);
	struct prophet *skel = SCX_OPS_OPEN(prophet_ops, prophet);
	skel->rodata->prophet_telemetry = telemetry;
	skel->rodata->pulse_propagation_enabled = propagation;
	SCX_OPS_LOAD(skel, prophet_ops, prophet, uei);
	struct bpf_link *link = SCX_OPS_ATTACH(skel, prophet_ops, prophet);
	__u32 key = 0;
	int cfd = bpf_map__fd(skel->maps.control), tfd = bpf_map__fd(skel->maps.telemetry);
	__u32 tid = map_id(tfd), taskid = map_id(bpf_map__fd(skel->maps.task_state));
	__u32 mid = map_id(bpf_map__fd(skel->maps.metrics));
	if (!tid || !taskid || !mid) { perror("map info"); stop = 1; }
	printf("{\"type\":\"attached\",\"mode\":\"%s\",\"profile\":%d,"
	       "\"telemetry\":%s,\"propagation\":%s,\"poll_ms\":%d,"
	       "\"possible_cpus\":%d,\"telemetry_map_id\":%u,\"task_map_id\":%u,"
	       "\"metrics_map_id\":%u,\"abi\":%d}\n",
	       mode, profile, telemetry ? "true" : "false", propagation ? "true" : "false",
	       poll_ms, ncpu, tid, taskid, mid, PROPHET_ABI);
	__u64 start = ns(CLOCK_MONOTONIC), last = start, window_start = start;
	__u64 generation = 0, next = start, last_publish = 0;
	struct prophet_control ctl = {.last_switch_ns = start};
	struct prophet_stats window = {};
	int rc = 0, requested = 0;
	bool have_previous = false;
	while (!stop && !UEI_EXITED(skel, uei)) {
		__u64 now = ns(CLOCK_MONOTONIC), cpu_start = ns(CLOCK_PROCESS_CPUTIME_ID);
		if (duration && now - start >= (__u64)duration * 1000000000ULL) break;
		bool frozen = freeze_ms && now - start >= (__u64)freeze_ms * 1000000ULL;
		bool valid = now - last <= PROPHET_WINDOW_NS;
		struct prophet_stats delta = {};
		if (telemetry) {
			if (bpf_map_lookup_elem(tfd, &key, current)) {
				perror("telemetry lookup"); valid = false; have_previous = false;
			} else {
				if (!have_previous) valid = false;
				for (int cpu = 0; cpu < ncpu; cpu++)
					for (int j = 0; j < P_COUNTERS; j++) {
						if (current[cpu].c[j] < previous[cpu].c[j]) valid = false;
						else delta.c[j] += current[cpu].c[j] - previous[cpu].c[j];
					}
				memcpy(previous, current, ncpu * sizeof(*current));
				have_previous = true;
			}
		}
		bool publish = false;
		if (last_publish && now - last_publish >= PROPHET_LEASE_NS) {
			ctl.profile = ctl.high = ctl.low = 0;
			requested = 0;
		}
		if (!valid) {
			memset(&window, 0, sizeof(window)); window_start = now;
			ctl.profile = ctl.high = ctl.low = 0;
			requested = -1;
			if (!frozen && bpf_map_delete_elem(cfd, &key) && errno != ENOENT) {
				perror("control revoke"); rc = 1; break;
			}
		} else {
			for (int j = 0; j < P_COUNTERS; j++) window.c[j] += delta.c[j];
			if (now - window_start >= PROPHET_WINDOW_NS) {
				if (adaptive) {
					requested = prophet_decide(&ctl, &window, now - window_start, now);
					publish = requested >= 0;
				}
				memset(&window, 0, sizeof(window)); window_start = now;
			}
			if (!adaptive) { requested = fixed ? profile : 0; publish = true; }
		}
		if (publish && !frozen) {
			struct prophet_config c = {.issued_ns = now, .generation = ++generation,
				.abi = PROPHET_ABI, .profile = requested};
			if (bpf_map_update_elem(cfd, &key, &c, BPF_ANY)) {
				perror("control publish"); rc = 1; break;
			}
			last_publish = now;
		}
		/* Raw per-poll deltas retained. Fields are NOT an atomic multi-CPU snapshot.
		 * CPU cost excludes this JSON formatting/write; total process CPU at exit
		 * includes it. A blocked logger cannot renew the lease. */
		printf("{\"type\":\"sample\",\"now_ns\":%llu,\"elapsed_ns\":%llu,"
		       "\"valid\":%s,\"requested\":%d,\"generation\":%llu,"
		       "\"frozen\":%s,\"control_cpu_ns\":%llu,\"counters\":[",
		       now, now - last, valid ? "true" : "false", requested, generation,
		       frozen ? "true" : "false", ns(CLOCK_PROCESS_CPUTIME_ID) - cpu_start);
		for (int j = 0; j < P_COUNTERS; j++) printf("%s%llu", j ? "," : "", delta.c[j]);
		puts("]}");
		last = now;
		next += (__u64)poll_ms * 1000000ULL;
		__u64 after = ns(CLOCK_MONOTONIC);
		if (next <= after) next = after + (__u64)poll_ms * 1000000ULL;
		struct timespec until = {.tv_sec = next / 1000000000ULL, .tv_nsec = next % 1000000000ULL};
		while (!stop && clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &until, NULL) == EINTR) {}
	}
	bpf_link__destroy(link);
	__u64 ecode = UEI_REPORT(skel, uei);
	printf("{\"type\":\"exit\",\"process_cpu_ns\":%llu,\"uei_code\":%llu}\n",
	       ns(CLOCK_PROCESS_CPUTIME_ID), ecode);
	prophet__destroy(skel);
	free(current); free(previous);
	return rc || UEI_ECODE_USER(ecode) ? 1 : 0;
}
