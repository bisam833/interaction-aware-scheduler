/* Native state-machine/accounting checks. NOT verifier or SMP validation. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../src/scx_prophet/control.h"
#include "../src/pulsesched/pulse_policy.h"

static void config_test(void)
{
	struct prophet_config c = {.issued_ns=100, .generation=1, .abi=PROPHET_ABI, .profile=2};
	assert(!prophet_config_live(NULL, 100));
	assert(!prophet_config_live(&c, 99));
	assert(prophet_config_live(&c, 100));
	assert(prophet_config_live(&c, 100 + PROPHET_LEASE_NS - 1));
	assert(!prophet_config_live(&c, 100 + PROPHET_LEASE_NS));
	c.profile = 3; assert(!prophet_config_live(&c, 100));
	c.profile = 0; c.abi++; assert(!prophet_config_live(&c, 100));
	c.abi--; c.generation = 0; assert(!prophet_config_live(&c, 100));
	assert(prophet_slice(999) == PULSE_SLICE_NS);
}

static void histogram_test(void)
{
	assert(prophet_bucket(0) == 0);
	assert(prophet_bucket(1) == 0);
	for (unsigned i = 1; i < 63; i++) {
		unsigned b = i < 31 ? i : 31;
		assert(prophet_bucket(1ULL << i) == b);
		assert(prophet_bucket((1ULL << i) - 1) == (i <= 31 ? i-1 : 31));
	}
	assert(prophet_bucket(~0ULL) == 31);
}

static void control_test(void)
{
	struct prophet_control s = {};
	struct prophet_stats high = {}, low = {}, sparse = {};
	high.c[24] = 200; low.c[10] = 200; sparse.c[24] = 127;
	__u64 t = 10000000000ULL;
	assert(prophet_decide(&s, &sparse, PROPHET_WINDOW_NS, t) == -1);
	assert(prophet_decide(&s, &high, PROPHET_WINDOW_NS, t) == 0);
	t += PROPHET_WINDOW_NS;
	assert(prophet_decide(&s, &high, PROPHET_WINDOW_NS, t) == 1);
	for (int i=0; i<3; i++) {
		t += PROPHET_WINDOW_NS;
		assert(prophet_decide(&s, &high, PROPHET_WINDOW_NS, t) == 1);
	}
	t += PROPHET_WINDOW_NS;
	assert(prophet_decide(&s, &high, PROPHET_WINDOW_NS, t) == 2);
	for (int i=0; i<3; i++) {
		t += PROPHET_WINDOW_NS;
		assert(prophet_decide(&s, &low, PROPHET_WINDOW_NS, t) == 2);
	}
	t += PROPHET_WINDOW_NS;
	assert(prophet_decide(&s, &low, PROPHET_WINDOW_NS, t) == 1);
	t += PROPHET_WINDOW_NS;
	assert(prophet_decide(&s, &high, PROPHET_MAX_WINDOW_NS+1, t) == -1);
	assert(s.profile == 0 && s.high == 0);
	/* Rate-limit repeated excursions; uncertainty does not reset the budget. */
	s = (struct prophet_control){.era_ns=t, .switches=6, .high=1};
	assert(prophet_decide(&s, &high, PROPHET_WINDOW_NS, t) == -1);
	assert(s.blocked_until_ns == t + 30000000000ULL);
	assert(prophet_decide(&s, &low, PROPHET_WINDOW_NS, t+1) == -1);
	assert(prophet_decide(&s, &low, PROPHET_WINDOW_NS, s.blocked_until_ns) == 0);
	/* Alternating evidence must not chatter. */
	s = (struct prophet_control){};
	for (int i=0; i<1000; i++) {
		t += PROPHET_WINDOW_NS;
		assert(prophet_decide(&s, i%2 ? &low : &high, PROPHET_WINDOW_NS, t) == 0);
	}
}

static void fairness_test(unsigned weight0)
{
	__u64 base[2] = {}, key[2] = {pulse_queue_key(0, 1), 0};
	__u64 slice[2] = {PULSE_SLICE_NS, PULSE_SLICE_NS};
	__u64 service[2] = {}, last[2] = {}, maxgap[2] = {}, clock = 0, wall = 0;
	for (unsigned i=0; i<50000; i++) {
		int p = pulse_before(key[0], key[1]) ? 0 : 1;
		if (pulse_before(clock, base[p])) clock = base[p];
		/* Actual charged time is the OLD entry's slice, not the new profile. */
		service[p] += slice[p]; wall += slice[p];
		if (wall - last[p] > maxgap[p]) maxgap[p] = wall - last[p];
		last[p] = wall;
		base[p] += pulse_runtime_charge(slice[p], p ? 100 : weight0);
		base[p] = pulse_base_vtime(base[p], clock);
		key[p] = pulse_queue_key(base[p], p == 0);
		slice[p] = prophet_slice(i % 3); /* adversarial rapid updates */
	}
	__s64 err = (__s64)(service[0] * 100 - service[1] * weight0);
	assert(err > -8000000000LL && err < 8000000000LL);
	assert(maxgap[1] <= 120000000ULL);
	assert(pulse_base_vtime(100 * PULSE_SLICE_NS, 0) == 100 * PULSE_SLICE_NS);
}

int main(void)
{
	config_test(); histogram_test(); control_test(); fairness_test(100); fairness_test(200);
	puts("PASS: lease/ABI bounds, histogram edges, hysteresis/dwell/circuit breaker, queued-slice runtime accounting, model fairness");
}
