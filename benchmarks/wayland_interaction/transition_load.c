/* Deterministic offered load on CLOCK_MONOTONIC. No scheduler permissions.
 * Reports buffered per-second/per-phase work, CPU and progress observations.
 * Counts are charged at batch start; final batches may straddle boundaries.
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <inttypes.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

#define MAX_PHASES 64
#define MAX_BINS 2048
#define SECOND UINT64_C(1000000000)
struct phase { uint64_t duration; unsigned batch, periodic; };
struct bin { uint64_t iterations, cpu, gap, first, last; unsigned phase; };
static volatile sig_atomic_t stopped;
static void stop(int sig) { (void)sig; stopped = 1; }
static uint64_t ns(clockid_t clock)
{
	struct timespec t;
	if (clock_gettime(clock, &t)) { perror("clock_gettime"); exit(2); }
	return (uint64_t)t.tv_sec * SECOND + t.tv_nsec;
}
static void until(uint64_t when)
{
	struct timespec t = {.tv_sec=when / SECOND, .tv_nsec=when % SECOND};
	int rc;
	while (!stopped && (rc = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &t, NULL))) {
		if (rc != EINTR) { errno = rc; perror("clock_nanosleep"); exit(2); }
	}
}
static unsigned number(const char *s, unsigned max)
{
	char *end; errno = 0;
	unsigned long v = strtoul(s, &end, 10);
	if (errno || !*s || *end || v > max) exit(2);
	return (unsigned)v;
}
int main(int argc, char **argv)
{
	if (argc != 5) return 2;
	unsigned index = number(argv[2], 4095), periodic = number(argv[3], 1);
	unsigned fd = number(argv[4], 1048576);
	FILE *plan = fopen(argv[1], "r");
	if (!plan) return 2;
	struct phase phases[MAX_PHASES]; unsigned count = 0;
	uint64_t duration = 0;
	while (count < MAX_PHASES &&
	       fscanf(plan, "%" SCNu64 " %u %u", &phases[count].duration,
		      &phases[count].batch, &phases[count].periodic) == 3) {
		struct phase *p = &phases[count++];
		if (!p->duration || p->duration % SECOND || p->duration > 120*SECOND ||
		    p->batch > 4096 || p->periodic > 4096) return 2;
		duration += p->duration;
	}
	if (!feof(plan) || !count || duration / SECOND > MAX_BINS) return 2;
	fclose(plan);
	struct bin bins[MAX_BINS] = {};
	signal(SIGTERM, stop); signal(SIGINT, stop);
	printf("{\"type\":\"load_ready\",\"index\":%u,\"periodic\":%u,\"pid\":%d}\n",
	       index, periodic, getpid()); fflush(stdout);
	uint64_t epoch;
	if (read((int)fd, &epoch, sizeof(epoch)) != sizeof(epoch) || !epoch) return 2;
	close((int)fd);
	uint64_t offset = 0;
	volatile uint64_t x = 1;
	for (unsigned phase = 0; phase < count && !stopped; phase++) {
		uint64_t start = epoch + offset, end = start + phases[phase].duration;
		unsigned active = index < (periodic ? phases[phase].periodic : phases[phase].batch);
		until(start);
		uint64_t observed_start = ns(CLOCK_MONOTONIC), last = start;
		uint64_t last_period = UINT64_MAX, windows_with_progress = 0;
		for (uint64_t b = offset/SECOND; b < (offset+phases[phase].duration)/SECOND; b++)
			bins[b].phase = phase;
		while (!stopped && active) {
			uint64_t now = ns(CLOCK_MONOTONIC);
			if (now >= end) break;
			/* Periodic mix: 20 ms eligible, 80 ms asleep. Reset gap across
			 * intentional sleeps; no claim that these are runnable gaps. */
			if (periodic) {
				uint64_t within = (now-start) % 100000000ULL;
				if (within >= 20000000ULL) {
					uint64_t wake = now + 100000000ULL - within;
					if (wake > end) wake = end;
					until(wake); last = wake; continue;
				}
			}
			uint64_t b = (now-epoch)/SECOND, cpu = ns(CLOCK_PROCESS_CPUTIME_ID);
			if (periodic && (now-start)/100000000ULL != last_period) {
				last_period = (now-start)/100000000ULL;
				windows_with_progress++;
			}
			for (unsigned i = 0; i < 1000000; i++) x = x*1664525ULL + 1013904223ULL;
			uint64_t finished = ns(CLOCK_MONOTONIC);
			struct bin *v = &bins[b];
			v->iterations += 1000000;
			v->cpu += ns(CLOCK_PROCESS_CPUTIME_ID) - cpu;
			uint64_t gap = finished - last;
			if (gap > v->gap) v->gap = gap;
			if (!v->first) v->first = now;
			v->last = finished;
			last = finished;
		}
		if (!active) until(end);
		uint64_t observed_end = ns(CLOCK_MONOTONIC);
		/* Include unfinished end-of-phase progress gap for continuously busy workers. */
		if (active && !periodic && end > last) {
			struct bin *v = &bins[(offset + phases[phase].duration)/SECOND - 1];
			if (end-last > v->gap) v->gap = end-last;
		}
		printf("{\"type\":\"load_phase\",\"phase\":%u,\"active\":%s,"
		       "\"planned_start_ns\":%" PRIu64 ",\"planned_end_ns\":%" PRIu64 ","
		       "\"observed_start_ns\":%" PRIu64 ",\"observed_end_ns\":%" PRIu64 ","
		       "\"offered_periodic_windows\":%" PRIu64 ",\"periodic_windows_with_progress\":%" PRIu64 "}\n",
		       phase, active ? "true" : "false", start, end, observed_start, observed_end,
		       active && periodic ? (uint64_t)(phases[phase].duration/100000000ULL) : 0,
		       windows_with_progress);
		offset += phases[phase].duration;
	}
	uint64_t total_iterations = 0, max_gap = 0;
	for (uint64_t b = 0; b < duration/SECOND; b++) {
		struct bin *v = &bins[b];
		total_iterations += v->iterations;
		if (v->gap > max_gap) max_gap = v->gap;
		printf("{\"type\":\"load_bin\",\"index\":%u,\"periodic\":%u,\"phase\":%u,"
		       "\"start_ns\":%" PRIu64 ",\"end_ns\":%" PRIu64 ","
		       "\"iterations\":%" PRIu64 ",\"cpu_ns\":%" PRIu64 ","
		       "\"max_progress_gap_ns\":%" PRIu64 ",\"first_ns\":%" PRIu64 ",\"last_ns\":%" PRIu64 "}\n",
		       index, periodic, v->phase, epoch+b*SECOND, epoch+(b+1)*SECOND,
		       v->iterations, v->cpu, v->gap, v->first, v->last);
	}
	uint64_t finish = ns(CLOCK_MONOTONIC);
	printf("{\"type\":\"background\",\"iterations\":%" PRIu64 ",\"wall_ns\":%" PRIu64 ","
	       "\"cpu_ns\":%" PRIu64 ",\"max_batch_gap_ns\":%" PRIu64 "}\n",
	       total_iterations, finish > epoch ? finish-epoch : 0,
	       ns(CLOCK_PROCESS_CPUTIME_ID), max_gap);
	printf("{\"type\":\"load_exit\",\"complete\":%s,\"process_cpu_ns\":%" PRIu64 "}\n",
	       !stopped && offset == duration ? "true" : "false", ns(CLOCK_PROCESS_CPUTIME_ID));
	return stopped ? 1 : 0;
}
