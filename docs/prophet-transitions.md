# Prophet transition experiment, protocol v1

2026-10-20. **Controller frozen; no prediction, MPC, filter, or threshold tuning.**
This extends the matrix-02 investigation. Implementation and local functional
tests are available; the full privileged Wayland comparison has not run here.
Do not infer an adaptive performance win from the new test harness.

## Oscillation: evidence and counterfactual boundary

`scripts/analyze_prophet_oscillation.py` compares the matrix-02 controller traces,
omitting the same first two elapsed seconds in each run. Saved output:
`results/prophet-transition-dev-20261020/oscillation.json`.

Across the four matched heavy blocks:

| Frozen slice | Completed enqueue waits >=8.388608 ms | Completed enqueue observations/s | Wake-to-first-run waits >=8.388608 ms |
| --- | ---: | ---: | ---: |
| 20 ms, observe | 42.6–54.5% | 1,426–1,802 | 2.97–6.96% |
| 10 ms | 52.7–70.3% | 2,024–2,696 | 0.95–1.28% |
| 5 ms | 0.96–3.25% | 3,183–3,875 | 0.06–0.23% |

The controller's enqueue signal mixes wakeups with runnable requeues. Slice
selection changes both the wait distribution and its observation rate. The
threshold lies between the 5 and 10 ms slices. A long enqueue-wait fraction is
therefore not an action-independent estimate of offered workload. The wake
histogram responds differently, but its arrival/completion rate also depends
on service and blocking: it is not automatically an exogenous demand sensor.
Charged runtime crosses sample boundaries, and runnable-minus-quiescent counts
are not an instantaneous queue-length estimator.

The repeated reversals are consistent with a policy-induced response. Correct
replay of all 68 decisions establishes implementation consistency only.
Static runs supply evidence about intervention response, not the counterfactual
outcome of the same task at the same instant under another profile. Their client
arrival timing, CPU frequency, background interference and scheduling history
can differ. No causal estimate of oscillation harm has been established.

An additional correction to interpretation: adaptive requested 5 ms for only
**29.2–32.3% of the recorded first-to-last-sample wall interval** in matrix-02.
Enqueue shares are different because shorter slices produce more enqueue
observations. Neither requested wall time nor enqueue count is actual per-task
CPU residency. It is inaccurate to assume that adaptive spent most of that run
at 5 ms. Matching the static-5 pooled P95 still does not establish timing value.

The controller already has two high / three low windows, a two-second minimum
dwell and a switch circuit breaker. Adding those mechanisms again would not be
a justified fix. Threshold retuning, added dwell, or a latch stays gated on an
interventional test of harm and an untouched control arm.

## Why a separate benchmark build is necessary

The old benchmark sets its next input deadline relative to the end of the
preceding work. A slower scheduler therefore receives a different offered
timeline. That is unsuitable for attributing transition-window effects.

`build/prophet-transition-bench` shares the existing four-worker CPU chain,
epoch protocol, presentation callbacks and timeout checks. A compile-time hook:

1. Announces readiness after Wayland setup, scheduler attachment and worker setup.
2. Receives one common future CLOCK_MONOTONIC epoch through an inherited pipe.
3. Offers inputs at epoch +250 ms, then every 500 ms, identically in every arm.
4. Processes all arrivals in order; when busy, it retains delay rather than
   dropping arrivals or shifting the future schedule.
5. Logs planned due time separately from actual dispatch, commit and presentation.

Primary latency is **planned due time to presentation**. Dispatch-to-presentation
remains available as a secondary metric. Inputs are synthetic; these are not
hardware-input timestamps. Serial processing and presentation backpressure can
create a queue; that cost is included in primary latency. Completed outcomes are
assigned to the phase of their planned arrival even when they cross a boundary.
The embedded Pulse owner remains attached through the complete offered-load period.
Manual input during the experiment is an explicit failure, not an extra trial.
An explicit compositor suspension notification also fails the visibility
condition. Absence of such a notification still does not prove visibility.

The original steady-load benchmark executables, scheduler binaries, raw results
and controller policy are preserved. New outputs have distinct filenames.

## Offered load and execution order

`scripts/prophet_transitions.py` writes the exact plans, seed, order and hashes
before starting work. Every arm receives identical planned eligibility windows
and foreground work. Actual activation and service can be late under a scheduler;
those delays are recorded as outcomes, not claimed to be simultaneous execution.

At 16 allowed CPUs:

- **Steps:** five 12-second phases: idle → 8 batch workers → 32 batch workers →
  8 batch workers → idle. "Idle" means no background burners; the foreground
  client and ordinary host activity still exist.
- **Irregular bursts:** six seeded 3–7-second load episodes, alternating 32 and
  8 batch workers, separated by seeded 2–4-second zero-burner gaps.
  The default seed is 20261020; the realized plan is saved.
- **Mixed:** idle, moderate batch, saturated batch plus 8 periodic burners,
  moderate batch plus those periodic burners, then idle.
  Periodic burners are eligible for 20 wall-clock milliseconds per 100 ms.
  They are duty-gated CPU burners, **not fixed-CPU-demand periodic jobs**.
  Eligibility windows without a work-batch start are counted; a started batch
  may finish after its window. Throughput is reported
  separately for continuous and periodic roles so losing periodic service
  cannot hide behind increased batch throughput.

`transition-load` uses absolute sleeps and bounded in-memory one-second bins.
It records batch iterations, process CPU work, progress gaps, actual phase
entry/exit, and missed periodic eligibility windows. CPU work is charged at batch
start; a final one-million-iteration batch can straddle a boundary. Intentional
sleep is excluded when resetting the periodic progress-gap clock. The primary
gap comparison uses continuously eligible batch workers.

Arms: Linux, LAVD, Rusty, repaired Pulse propagation-off, Prophet observe (20 ms),
frozen 10 ms, frozen 5 ms, and unchanged adaptive. Rusty remains because it is an
existing adaptive tuner. Observe provides the meaningful frozen 20 ms comparison;
no redundant static-20 arm is added.

Order is a seeded permutation with permuted cyclic rotations. Every block
contains every arm; eight blocks balance positions completely, while the default
four are a partial balance. Scenario order is also seeded. Paired comparisons
use the same scenario, phase and block, never unrelated runs. Randomized order
reduces but does not eliminate temporal confounding.

The default full protocol has 96 measured blocks and approximately **85.3 minutes
of offered workload**, plus readiness, smoke tests, warmup and draining.

## Measurements fixed before the host run

`scripts/analyze_prophet_transitions.py` recomputes integrity checks and produces
per-block, per-phase and per-window results:

- Primary planned-input-to-presentation P50/P95/P99, dispatch queueing, actual
  client-to-presentation, and commit-to-presentation delays.
- First two seconds after each phase starts versus later samples, with counts
  and cross-phase outcomes retained.
- Recovery: time until the **third consecutive completed outcome at or below
  200 ms** since the phase start. Unobserved recovery is right-censored at phase
  end, never replaced by zero. This is an operational SLO streak, not estimated
  equilibrium convergence; the 500 ms input spacing limits its resolution.
- Total and role-specific background throughput, CPU work and longest observed
  batch-completion gap; actual worker activation lag and missed periodic windows.
- Measured controller-body CPU, invalid polls, fallback enqueues, whole-process
  exit CPU, and supported system-wide runnable-to-first-run histogram bounds.
- Requested-profile wall fractions, planned-arrival counts under each last
  requested profile, and actual profile enqueue counts, kept distinct.
- Each paired block difference and exploratory paired-block bootstrap intervals.
  Trials are not treated as independent experimental replicates.

Four blocks and short windows cannot precisely estimate tails. A two-second
window normally has four foreground trials; its P99 is descriptive and has
effectively no tail support. Every cell exposes its sample count and expected
upper-tail count. Bootstrap repetitions do not create independent evidence.
No significance claim or multiple-comparison winner selection is authorized.

Observed batch gaps include computation and bookkeeping, and are not exact
longest runnable starvation intervals. Accurate compositor/task runnable-delay
attribution remains unavailable without a separately validated tracing experiment.
Commit-to-presentation includes refresh/GPU/compositor effects. No automatic
compositor control, hard deadline, zero-overhead or fairness theorem is claimed.
Controller body CPU excludes JSON writes; whole-process CPU includes startup
and shutdown. Neither isolates fast-path BPF overhead.

## Timing attribution and decision gates

1. First test whether frozen 5/10/20 ms choices show a reproducible,
   workload-dependent tradeoff. The analyzer explicitly compares frozen 5
   against frozen 10 per phase as well as adaptive against both.
2. Adaptive must improve over the best fixed policy for the complete planned
   workload, with the existing exploratory screen of at least 5% P95 reduction,
   no more than 3% background throughput loss, and acceptable progress. Inspect
   role-specific loss and missed periodic service as well as the aggregate.
   Do not choose a favorable phase while ignoring the rest.
3. Exposure accounting tests the simple “mostly 5 ms” explanation. It does not
   supply a causal counterfactual: job history, timing and prior actions differ.
4. If a genuine opportunity survives, a **separate occupancy-matched open-loop
   intervention** is required before crediting adaptive timing. Freeze an action
   tape from a training run, then compare aligned versus phase-shifted/shuffled
   tapes with the same durations, switch count and telemetry on held-out
   transitions. A weighted mixture of static-run quantiles is not a substitute.
   This intervention is not yet implemented because the prerequisite profile
   tradeoff has not been measured.
5. Prediction is justified only by an actionable opportunity that fixed policies
   and simple feedback cannot adequately capture, plus an available leading
   signal on held-out transitions. A planner's future phase schedule is privileged
   oracle information; it must never be relabelled a deployed prediction.

If fixed 5 ms remains as good within the available uncertainty, report that and
retain the simpler policy. Matrix-02 currently establishes no adaptive advantage
over fixed 5 ms. There is no new performance result until this protocol runs.

## Execution, gates and preservation

```sh
make -C benchmarks/wayland_interaction transition-check
python3 -m unittest discover -s tests -p 'test_*.py' -v

# Host Wayland session; the output directory must be new.
sudo env WAYLAND_DISPLAY="$WAYLAND_DISPLAY" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
  python3 scripts/prophet_transitions.py --output results/prophet-transitions-01

python3 scripts/analyze_prophet_transitions.py results/prophet-transitions-01 \
  --output results/prophet-transitions-01-analysis
```

`--plan` writes the exact protocol without attachment. `--preflight-only` runs
the privileged gates and eight short transition preflights. Neither is a full
comparison and the analyzer rejects preflight-only status.

Every real invocation reruns Prophet's verifier/load, lease/freeze/stop/kill,
progress and detach smoke; then original Pulse propagation off/on and stale/TTL
checks; then all eight short Wayland preflights. Any failure stops the experiment
and preserves raw files. There is no automatic retry or comparator removal.
Only owned child processes are terminated. Barrier failure closes remaining
pipes and reaps workers before detaching the scheduler. A watchdog bounds
workload draining; its expiry is a retained failure, not permission to drop jobs.

Local artifacts are under `results/prophet-transition-dev-20261020/`:
source/binary rollback copies, existing-trace analysis, native/sanitizer tests,
saved seeded plan and Linux-only load-generator smoke data. The current namespace
has zero effective capabilities and NoNewPrivs=1. A Linux-only Wayland access
probe returned code 3, `wayland connect failed`. Thus no new privileged verifier
or live presentation result is claimed here. The unchanged scheduler executable
retains its prior host verification; the new integration still needs the host gate.
