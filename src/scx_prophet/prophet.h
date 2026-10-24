/* SPDX-License-Identifier: GPL-2.0 */
#ifndef PROPHET_H
#define PROPHET_H

/* Types supplied by vmlinux.h in BPF, linux/types.h in userspace. */
#define PROPHET_ABI 1
#define PROPHET_PROFILES 3
#define PROPHET_LEASE_NS 1500000000ULL
#define PROPHET_WINDOW_NS 500000000ULL
#define PROPHET_MAX_WINDOW_NS 1000000000ULL
#define PROPHET_DWELL_NS 2000000000ULL
#define PROPHET_MIN_SAMPLES 128
#define PROPHET_BINS 32
#define P_ENQUEUE 0
#define P_WAKE 32
#define P_PROFILE 64
#define P_FALLBACK 67
#define P_LOCAL 68
#define P_SHARED 69
#define P_RUNTIME 70
#define P_RUNNABLE 71
#define P_QUIESCENT 72
#define P_COUNTERS 73

struct prophet_config {
	__u64 issued_ns;
	__u64 generation;
	__u32 abi;
	__u32 profile;
};

struct prophet_stats { __u64 c[P_COUNTERS]; };

/* One immutable hash entry per generation. BPF never writes it. */
static inline int prophet_config_live(const struct prophet_config *c, __u64 now)
{
	return c && c->abi == PROPHET_ABI && c->generation &&
		c->profile < PROPHET_PROFILES && c->issued_ns <= now &&
		now - c->issued_ns < PROPHET_LEASE_NS;
}

static inline __u64 prophet_slice(__u32 profile)
{
	switch (profile) {
	case 1: return 10000000ULL;
	case 2: return 5000000ULL;
	default: return 20000000ULL;
	}
}

/* floor(log2(ns)), saturated; bin 0 includes 0 and 1 ns. No loops. */
static inline __u32 prophet_bucket(__u64 ns)
{
	__u32 b = 0;
	if (ns >= (1ULL << 31)) return 31;
	if (ns >= (1ULL << 16)) { ns >>= 16; b += 16; }
	if (ns >= (1ULL << 8)) { ns >>= 8; b += 8; }
	if (ns >= (1ULL << 4)) { ns >>= 4; b += 4; }
	if (ns >= (1ULL << 2)) { ns >>= 2; b += 2; }
	if (ns >= 2) b++;
	return b;
}
#endif
