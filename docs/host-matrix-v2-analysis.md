# PulseSched host matrix v2 — measured results, 2026-10-18

**The repaired scheduler substantially beats stock Linux under this heavy-load
workload, without a large throughput loss. Propagation itself is not an
end-to-end improvement in this matrix.** Keep propagation opt-in (the existing
loader default remains off); do not present these results as validation of the
whole interaction-propagation architecture.

## Integrity and scope

Source: `results/audit-host-matrix-v2`. Four rotating blocks × three conditions ×
four implementations × 30 trials = **48 blocks, 1,440 presented trials**. All
completed with return code zero: no missing/duplicate trial IDs, discarded or
timeout trials, protocol errors, invalid clocks, or missing background-worker
reports. Every trial has four unique work stages, each at least 20 CPU-ms.
Ordering input ≤ work ≤ commit ≤ presentation is consistent throughout.

The manifest hashes match the current benchmark, background worker and loader
binaries. Kernel: `7.2.10-1-cachyos`, 16 logical CPUs; LAVD: `1.1.3`, default
configuration. Idle/moderate/heavy use 0/8/32 background workers respectively.
Both Pulse propagation configurations passed verifier/load, attach/detach checks.
TTL and stale-token smoke tests passed. Every block returned to sched_ext
`disabled` with nr_rejected unchanged at zero. All sampled epoch members were
inactive after closure; every Pulse-on trial terminated via presentation feedback.
The on-mode recorded 60 inherited boosts per block (two eligible workers × 30),
with actual inherited running events. This validates the exercised path, not
arbitrary race-free cancellation, watchdog recovery or all system workloads.

The workload starts at **synthetic client dispatch**, executes four sequential
20 CPU-ms stages, then waits for a single surface presentation. It does not
include physical input delivery or continuously animated frame production.

## Matched client-to-presentation latency

Milliseconds; 120 trials per row; linearly interpolated empirical percentiles.
No failed trials were discarded to obtain this table (there were none).

| Load | Scheduler | P50 | P95 | P99 |
|---|---|---:|---:|---:|
| Idle | Linux | 119.320 | 125.952 | 126.724 |
| Idle | LAVD | 119.667 | 126.682 | 127.241 |
| Idle | Pulse-off | 119.371 | 126.017 | 126.587 |
| Idle | Pulse-on | 119.479 | 125.940 | 126.408 |
| Moderate | Linux | 117.391 | 122.440 | 122.489 |
| Moderate | LAVD | 121.761 | 122.115 | 124.287 |
| Moderate | Pulse-off | 120.978 | 121.833 | 124.178 |
| Moderate | Pulse-on | 121.574 | 122.107 | 124.092 |
| Heavy | Linux | 257.089 | 286.288 | 287.322 |
| Heavy | LAVD | 125.630 | 137.063 | 153.385 |
| Heavy | Pulse-off | 124.711 | 138.676 | 141.362 |
| Heavy | Pulse-on | 125.586 | 149.077 | 164.017 |

Heavy Pulse-on improves P95 by **47.9%** and P99 by **42.9%** versus Linux.
Pulse-off improves those by **51.6%** and **50.8%**. Idle is essentially unchanged.
Moderate-load medians are worse than Linux by about 3.6 ms (off) / 4.2 ms (on),
although P95s are similar; neither mode is a universal latency winner.

The earlier 1.2–1.4 second P95s are not reproduced. However, old files used
unequal work, wall-clock work limits, different worker counts and tiny samples.
**Do not claim an exact historical before/after speedup.** This is a matched
comparison of the repaired build against Linux and LAVD. No partial-switching
ablation was run, so the individual contribution of that repair is not isolated.

## Remaining propagation regression

Under heavy load, propagation-on versus off raises total P95 by **10.401 ms
(7.5%)** and P99 by **22.655 ms (16.0%)**. Three of four paired block P95s worsen:

| Block | Pulse-off P95 | Pulse-on P95 |
|---|---:|---:|
| 0 | 134.408 | 153.903 |
| 1 | 137.599 | 155.967 |
| 2 | 139.903 | 146.138 |
| 3 | 138.440 | 132.603 |

Stage evidence under heavy load:

| Metric | Linux P95 | LAVD P95 | Pulse-off P95 | Pulse-on P95 |
|---|---:|---:|---:|---:|
| Work to commit, ms | 243.946 | 86.600 | 85.190 | 82.967 |
| Commit to presentation, ms | 46.185 | 53.059 | 56.729 | 67.154 |
| Worker handoff, ms | 2.150 | 0.067 | 0.941 | 0.338 |
| Feedback delivery after presentation, ms | 0.939 | 2.780 | 12.812 | 12.282 |

Propagation improves handoffs and work completion, but these gains do not survive
through presentation. The unresolved tail is downstream of commit. Contention
with compositor/rendering work is a hypothesis; these records cannot distinguish
CPU scheduling, GPU/compositor buffering or refresh-phase effects. Delivery of
feedback is also delayed under Pulse; it is after presentation and therefore
cannot be added to the reported presentation latency. Its late arrival can keep
an epoch eligible longer. Separate-stage percentiles must not be summed.

There are only four blocks and 120 trials per mode/load: P99 is based on very few
observations. The repeated block evidence warrants investigation, not a claim
that an exact 16% penalty generalizes. No speculative slice/preemption change
was made in response to these results.

## Throughput, overhead proxies and progress

Heavy-load arithmetic mean of per-block aggregate background rates:

| Scheduler | Billion iterations/s | Versus Linux | Worst batch progress gap, ms |
|---|---:|---:|---:|
| Linux | 5.7662 | reference | 32.541 |
| LAVD | 5.6992 | -1.16% | 40.639 |
| Pulse-off | 5.7228 | -0.75% | 49.940 |
| Pulse-on | 5.7289 | -0.65% | 63.712 |

All workers made progress. Pulse's larger worst progress gaps are a remaining
fairness/tail concern; no indefinite starvation was observed in this run. These
rates and gaps include startup/warmup/teardown and are not pure steady-state
scheduler measurements. Small throughput differences should not be overclaimed.
Moderate mean throughput is about 1.11% lower for Pulse-off and 0.21% lower for
Pulse-on than Linux.

Mean benchmark CPU time per heavy block: Linux 2.412 s, LAVD 2.415 s, Pulse-off
2.458 s, Pulse-on 2.456 s. This includes userspace epoch/observer/loading work and
**is not isolated BPF overhead**. Exact scheduling delay was not captured:
trace_delay=false and sched_schedstats=0. Worker handoff times include syscalls.

## Decision and next validation

The full-system bounded-vtime scheduler is a viable improvement for this test.
The data does not establish that interaction inheritance is better than its
ablation, or that Pulse outperforms LAVD overall. Pulse-off still starts direct
root epochs; it is not a no-epoch baseline. Keep both experimental modes intact,
and retain propagation-off as the existing default.

The next useful work is targeted heavy-load tracing around surface commit and
presentation, with off/on order rotated and the same CPU-work configuration.
Capture compositor and benchmark runnable delays on all CPUs; correlate TIDs,
commit timestamps, actual presentation and feedback receipt. Repeat an
observer-on matrix in a new directory using the existing `--trace-delay` option
for an overhead check, but recognize its histogram aggregates all tasks and
cannot localize compositor delay. A shorter slice/preemption or earlier token
closure should be tested only as an explicit ablation, not silently substituted
into this validated baseline. Test concurrent interactions and repeated urgency
on a pinned CPU before declaring fairness established beyond this workload.

Machine-readable artifacts:

- `results/audit-host-matrix-v2-summary.jsonl`: each block and pooled percentiles,
  with failures and missing trials retained by the summarizer.
- `results/audit-host-matrix-v2-validation.json`: integrity checks, binary hashes,
  per-block P95s, aggregate rates, progress gaps and CPU-time summaries.

Recompute the statistical summary without changing any raw files:

```bash
python3 scripts/summarize_latency_audit.py results/audit-host-matrix-v2 \
  > results/audit-host-matrix-v2-summary.jsonl
```
