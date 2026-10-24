/* Fixed-work background throughput and progress-gap observer. */
#define _POSIX_C_SOURCE 200809L
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <time.h>

static volatile sig_atomic_t stop;
static void finish(int sig) { (void)sig; stop = 1; }
static uint64_t ns(clockid_t clock)
{
    struct timespec t;
    clock_gettime(clock, &t);
    return (uint64_t)t.tv_sec * 1000000000 + t.tv_nsec;
}
int main(void)
{
    signal(SIGTERM, finish);
    signal(SIGINT, finish);
    uint64_t start = ns(CLOCK_MONOTONIC), last = start, cpu = ns(CLOCK_PROCESS_CPUTIME_ID);
    uint64_t batches = 0, max_gap = 0;
    volatile uint64_t x = 1;
    while (!stop) {
        for (unsigned i = 0; i < 1000000; i++) x = x * 1664525ULL + 1013904223ULL;
        batches++;
        uint64_t now = ns(CLOCK_MONOTONIC), gap = now - last;
        if (gap > max_gap) max_gap = gap;
        last = now;
    }
    printf("{\"type\":\"background\",\"iterations\":%llu,\"wall_ns\":%llu,"
           "\"cpu_ns\":%llu,\"max_batch_gap_ns\":%llu}\n",
           (unsigned long long)(batches * 1000000),
           (unsigned long long)(ns(CLOCK_MONOTONIC) - start),
           (unsigned long long)(ns(CLOCK_PROCESS_CPUTIME_ID) - cpu),
           (unsigned long long)max_gap);
    return 0;
}
