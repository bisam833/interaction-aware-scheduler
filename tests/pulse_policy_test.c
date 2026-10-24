/* Native policy tests: these do not substitute for the kernel verifier. */
#include <assert.h>
#include <linux/types.h>
#include <stdio.h>
#include "../src/pulsesched/pulse_policy.h"

static void fairness(unsigned weight0)
{
    __u64 base[2] = {0, 0}, clock = 0;
    unsigned runs[2] = {0, 0}, gap[2] = {0, 0}, maxgap[2] = {0, 0};
    /* Task 0 renews urgency on EVERY enqueue, task 1 is always normal. */
    for (unsigned i = 0; i < 20000; i++) {
        for (int j = 0; j < 2; j++) base[j] = pulse_base_vtime(base[j], clock);
        int pick = pulse_before(pulse_queue_key(base[0], 1), pulse_queue_key(base[1], 0)) ? 0 : 1;
        if (pulse_before(clock, base[pick])) clock = base[pick];
        base[pick] += pulse_runtime_charge(PULSE_SLICE_NS, pick ? 100 : weight0);
        runs[pick]++;
        for (int j = 0; j < 2; j++) {
            gap[j] = pick == j ? 0 : gap[j] + 1;
            if (gap[j] > maxgap[j]) maxgap[j] = gap[j];
        }
    }
    assert(maxgap[1] <= 4); /* normal CPU hog must make progress under persistent urgency */
    long error = (long)runs[0] * 100 - (long)runs[1] * weight0;
    assert(error >= -400 && error <= 400);
}

int main(void)
{
    fairness(100);
    fairness(200);
    __u64 debt = 1000 * PULSE_SLICE_NS;
    assert(pulse_base_vtime(debt, 0) == debt);
    assert(pulse_before(pulse_queue_key(debt, 1), pulse_queue_key(debt, 0)));
    assert(pulse_runtime_charge(12345, 100) == 12345);
    /* Wraparound queue ordering, including initial sleeper/boost credits. */
    assert(pulse_before(pulse_queue_key(0, 1), 0));
    assert(pulse_base_vtime(0, 10 * PULSE_SLICE_NS) == 9 * PULSE_SLICE_NS);
    struct pulse_task_state root = {.epoch_id=1, .start_ns=100, .deadline_ns=200,
                                    .active=1, .direct=1};
    struct pulse_task_state child = root;
    assert(pulse_root_live(&child, &root, 150));
    assert(!pulse_epoch_live(&root, 99));
    assert(!pulse_epoch_live(&root, 200));
    root.active = 0; /* Copy racing with closure still has active=1. */
    assert(!pulse_root_live(&child, &root, 150));
    root.active = 1;
    root.epoch_id++;
    assert(!pulse_root_live(&child, &root, 150));
    root.epoch_id--;
    root.start_ns++;
    assert(!pulse_root_live(&child, &root, 150));
    root.deadline_ns = root.start_ns + MAX_EPOCH_TTL_NS + 1;
    assert(!pulse_epoch_live(&root, 150));
    puts("policy tests passed: bounded urgency, weighted fairness, runtime debt, TTL, root closure");
}
