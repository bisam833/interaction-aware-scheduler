# Prophet matrix-02: functional pass; adaptation has not beaten static policy

2026-10-20. The complete host run passed independent revalidation: 36 heavy-load
blocks, 1,080 presented trials, nine idle preflights with 27 presented trials,
and no discarded feedback, timeouts, malformed trials, or unequal-work failures.
The recorded source/binary hashes match the current measured implementation.
Every block returned sched_ext to disabled without increasing its rejection count.
Raw matrix-01 failures remain preserved separately; this run does not explain them.

The useful finding is narrower than "adaptive scheduling works": shorter slices
improve tails relative to Prophet's observed 20 ms baseline in this experiment,
but feedback has not demonstrated an advantage over static short slices or
repaired PulseSched/LAVD. The feedback also repeatedly reverses its own actions.
Do not advance this controller to prediction/MPC on these results.

## Provenance and reproducibility

- Raw data: `results/prophet-host-matrix-02/`, unchanged by analysis.
- Audit: `results/prophet-matrix02-analysis-20261020/analysis.json`.
- Raw-file hashes: that directory's `raw-sha256.json`.
- Rollback snapshot: `verified-implementation-snapshot.tar.gz` and its per-file
  hashes in the same audit directory; measured scheduler executables remain unchanged.
- Reusable read-only analyzer: `scripts/analyze_prophet_matrix.py`.
- Actual-policy replay: audit directory `replay.c`, four input files, and
  `replay-results.json`. All 68 reconstructed decision windows reproduce the
  recorded requested profile through the existing `prophet_decide` function.

```sh
python3 scripts/analyze_prophet_matrix.py results/prophet-host-matrix-02 \
  --output results/prophet-matrix02-analysis-recheck
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The output directory must be new and outside the raw directory. The analyzer
checks the full planned block inventory, saved and recomputed verdicts, host and
affinity consistency, worker progress, fallback state, and current manifest
hashes. It rejects an incomplete run instead of silently analyzing survivors.
If measured files later change, use their preserved revision to rerun the audit.

This is kernel `7.2.10-1-cachyos`, 16 allowed CPUs, 32 identical background workers,
four 20 ms thread-CPU work stages per trial, 200 ms autorun spacing, and the
unchanged one-second feedback timeout. Nine arms rotate order over four blocks.
The input origin is synthetic client dispatch, not hardware input.

## Descriptive results

Each arm has 120 successful heavy trials. Percentiles below pool those trials
for description; the comparisons below retain block pairing.

| Arm | P50 ms | P95 ms | P99 ms | Mean block throughput, billion iterations/s | Longest observed worker batch gap, ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| Linux | 237.2 | 278.6 | 294.9 | 5.121 | 23.67 |
| LAVD | 123.1 | 138.5 | 166.0 | 4.868 | 30.63 |
| Rusty | 148.9 | 162.8 | 171.7 | 5.044 | 66.08 |
| Pulse propagation off | 121.2 | 138.3 | 160.1 | 5.072 | 54.61 |
| Prophet observe, 20 ms | 123.8 | 164.2 | 242.5 | 5.056 | 65.06 |
| Prophet static, 10 ms | 125.3 | 141.9 | 169.9 | 5.045 | 45.11 |
| Prophet static, 5 ms | 128.1 | 136.0 | 141.4 | 5.025 | 50.13 |
| Prophet adaptive | 125.4 | 136.0 | 168.4 | 5.023 | 45.11 |
| Prophet 20 ms without telemetry | 122.7 | 148.1 | 176.3 | 4.998 | 66.03 |

With 120 samples, roughly six observations support the upper 5% and only 1.2
the upper 1%. Per-block P95 uses just 30 trials. These are empirical summaries,
not precise population-tail estimates. No P99.9 or statistical significance claim
is supported. Four sequential paired blocks cannot remove time/order/frequency
confounding; adjacent Prophet arms occupy similar positions across rotations.

Adaptive versus observe P95 reductions by block are **27.09%, 6.53%, 13.24%,
11.60%**. Corresponding background throughput losses are **1.15%, 1.88%, -1.35%,
0.81%**; negative loss is a measured gain. Each pair meets the manifest's 5%
latency, at most 3% throughput loss, and progress-gap screen. This is a candidate
screen for replication, not proof of improvement.

Static 10 ms also meets those thresholds in all four observe comparisons.
Static 5 ms meets them in three; one has 3.16% throughput loss, exceeding the
3% limit. That miss is retained, not rounded into a pass.

Adaptive's paired P95 changes versus static 5 ms are **-2.89%, +14.57%, -1.50%,
+0.56%**, where positive means worse. None reaches 5% improvement. Adaptive
versus Pulse-off is **-13.31%, +17.48%, +1.59%, -0.69%**; versus LAVD it is
**+2.38%, +19.39%, -6.64%, -0.45%**. These do not establish a consistent win.
The nearly equal pooled adaptive/static-5 P95 hides the worse adaptive block.

Observe itself has worse tails than repaired Pulse-off. Its state/map lookup,
telemetry, separate owner process, timing and random variation are possible
contributors. Observe/unobserved comparisons are mixed and do not identify which
component causes this difference. "No telemetry" still runs the userspace loop,
publishes configuration, and writes sample JSON; it is not controller-free.

## Feedback failure mode

All four adaptive runs use the same requested sequence:

`fallback → 20 ms → 10 ms → 5 ms → 10 ms → 5 ms`.

Transitions to 10/5/10/5 ms occur approximately at 2.1–2.2, 4.4–4.6, 6.6–6.8,
and 8.8–9.0 seconds after the first controller sample. All noninitial telemetry
polls were valid. Native replay reproduces every decision, so this is the
implemented feedback rule operating as written, not evidence of corrupt updates.

The policy counts completed enqueue waits at least 8.388608 ms, including
requeues. Two high windows shorten slices; three low windows lengthen them,
subject to two-second dwell. Example from block 0:

| Window end, s | Long-wait fraction | Requested slice after decision |
| --- | ---: | ---: |
| 4.60 | 68.03% | 5 ms |
| 5.10 | 2.96% | 5 ms |
| 5.70 | 1.95% | 5 ms |
| 6.20 | 1.03% | 5 ms |
| 6.80 | 0.80% | 10 ms |
| 7.40 | 68.43% | 10 ms |
| 8.00 | 65.97% | 10 ms |
| 8.50 | 53.23% | 10 ms |
| 9.00 | 72.36% | 5 ms |

This is consistent with an action-dependent sensor causing a repeated reversal:
shortening slices changes both queue waits and requeue observation frequency,
then a low wait fraction is interpreted as permission to lengthen slices.
The background worker count stayed fixed. Interactive activity still varies,
and windows can straddle actions, so these traces do not identify a complete
causal response model or prove a sustained mathematical limit cycle.

Slew limits and dwell prevent rapid switching; they do not make this signal a
workload estimator. Four switches in these short runs do not exercise the
six-switch circuit breaker. Do not retune thresholds on the same data and call
that independent validation.

## Overhead, progress, presentation stage and visibility

Adaptive controller process CPU totals are 51.0–54.0 ms per run. Dividing by
first-to-last sample duration gives approximately 0.53–0.56% of one CPU.
This numerator includes load, startup, JSON output and shutdown outside that
sample interval; it is not a precise steady-state overhead fraction. Measured
control-body CPU is 1.30–1.68 ms per run, excluding JSON. These do not measure
fast-path BPF overhead or total system scheduling overhead.

All workers progress. Adaptive's minimum per-block Jain index of worker CPU-time
rates is 0.999970; its largest observed batch gap is 45.11 ms. Batch gaps include
one million iterations of computation and startup/teardown effects, not just
runqueue waiting. They are finite-run observations, not starvation bounds or
cgroup/nice/affinity fairness results.

Observe has pooled work-to-commit P95 82.4 ms and commit-to-presentation P95
83.9 ms; adaptive has 91.2 and 45.4 ms respectively. This suggests that faster
worker completion alone is not the presentation objective, consistent with the
earlier propagation regression. Quantiles do not add, and commit-to-presentation
includes refresh/compositor/display behavior. This is not compositor runnable
latency attribution. Per-task compositor control or benefit is not established.

There are 72 surface-state events in measured blocks and 18 in preflight,
all using xdg-shell version 7, with no suspension notifications. This confirms
the diagnostics execute on the real compositor. It does not prove continuous
visibility or explain matrix-01's timeouts.

## Decision and next research gate

Retain the verified experimental implementation and all static/observer arms.
Keep repaired PulseSched as the established project baseline; do not promote
adaptive mode on a favorable observe-only comparison.

The initial policy portfolio and userspace feedback are established techniques:
Rusty already supplies userspace tuning and adaptive slices, LAVD adapts latency
policy, and CAKE includes prediction from upcoming timer events. The local-source
audit and primary links are in `prophet-architecture.md`. This experiment has
not demonstrated a novel anticipatory mechanism.

Before predictive control, the next useful experiment is an explicitly recorded
workload phase change with fixed 20/10/5 ms controls and the frozen feedback
baseline. Separate workload inputs (arrival/runnable activity) from the
action-conditioned wait distribution; record actual phase start times and
controller response times. Keep simple static policies and Rusty's tuner in the
comparison. Fix the protocol and hold out transitions before fitting a model.
An externally announced phase could test an oracle upper bound, but it must be
labelled as privileged information and cannot count as a deployed predictor.

No predictive model, threshold retuning, or MPC implementation is justified by
this steady-load result. Workload-change recovery, burst behavior, sustained
controller stability and prediction value remain unmeasured.
