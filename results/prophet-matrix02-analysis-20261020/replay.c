/* Offline replay of the actual policy header; no BPF or scheduler attachment. */
#include <stdio.h>
#include "src/scx_prophet/control.h"
int main(void)
{
    unsigned long long start, now, elapsed, n, slow;
    int expected, count = 0;
    if (scanf("%llu", &start) != 1) return 2;
    struct prophet_control ctl = {.last_switch_ns = start};
    while (scanf("%llu %llu %llu %llu %d", &now, &elapsed, &n, &slow, &expected) == 5) {
        if (slow > n) return 2;
        struct prophet_stats delta = {};
        delta.c[P_ENQUEUE] = n - slow;
        delta.c[P_ENQUEUE + 23] = slow;
        int actual = prophet_decide(&ctl, &delta, elapsed, now);
        if (actual != expected) {
            fprintf(stderr, "window %d: expected %d, got %d\n", count, expected, actual);
            return 1;
        }
        count++;
    }
    if (!feof(stdin)) return 2;
    printf("PASS: %d recorded decisions match actual prophet_decide\n", count);
    return 0;
}
