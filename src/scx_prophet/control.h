/* SPDX-License-Identifier: GPL-2.0 */
#ifndef PROPHET_CONTROL_H
#define PROPHET_CONTROL_H
#include <linux/types.h>
#include "prophet.h"

struct prophet_control {
	__u32 profile, high, low, switches;
	__u64 last_switch_ns, era_ns, blocked_until_ns;
};

/* Conservative feedback baseline, not a predictor. Returns -1 on bad support.
 * Percentages are decision thresholds, not statistical confidence levels. */
static inline int prophet_decide(struct prophet_control *s,
		const struct prophet_stats *d, __u64 elapsed, __u64 now)
{
	__u64 n = 0, slow = 0;
	for (int i = 0; i < PROPHET_BINS; i++) {
		n += d->c[P_ENQUEUE + i];
		if (i >= 23) slow += d->c[P_ENQUEUE + i]; /* >= 8.388608 ms */
	}
	if (elapsed < PROPHET_WINDOW_NS || elapsed > PROPHET_MAX_WINDOW_NS ||
	    n < PROPHET_MIN_SAMPLES || n > 1000000000ULL ||
	    now < s->blocked_until_ns) {
		s->profile = s->high = s->low = 0;
		return -1;
	}
	if (!s->era_ns || now - s->era_ns >= 30000000000ULL) {
		s->era_ns = now;
		s->switches = 0;
	}
	if (slow * 100 >= n * 10) {
		if (s->high < 2) s->high++;
		s->low = 0;
	} else if (slow * 100 <= n * 2) {
		if (s->low < 3) s->low++;
		s->high = 0;
	} else {
		s->high = s->low = 0;
	}
	__u32 next = s->profile;
	if (s->high >= 2 && next < 2) next++;
	if (s->low >= 3 && next > 0) next--;
	if (next != s->profile && now - s->last_switch_ns >= PROPHET_DWELL_NS) {
		if (s->switches >= 6) {
			s->blocked_until_ns = now + 30000000000ULL;
			s->profile = s->high = s->low = 0;
			return -1;
		}
		s->switches++;
		s->profile = next;
		s->last_switch_ns = now;
		s->high = s->low = 0;
	}
	return (int)s->profile;
}
#endif
