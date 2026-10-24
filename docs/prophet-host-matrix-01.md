# First Prophet host matrix: incomplete, no adaptive improvement established

Audit date: 2026-10-20. Raw directory: `results/prophet-host-matrix-01/`.
Read-only revalidation, hashes, controller summary, source/binary rollback copy,
and local test logs: `results/prophet-matrix01-audit-20261020/`.
Run `python3 results/prophet-matrix01-audit-20261020/analyze.py` to reproduce
the partial-data audit. It checks that the source result directory is unchanged.

## Why the matrix stopped

`heavy-rusty-1` recorded 26 presented trials and four feedback timeouts, IDs
27–30. Both benchmark and scheduler returned zero. All 32 background workers
reported, sched_ext returned to disabled, and the rejection count stayed zero.
Rusty's log reports normal userspace unregister, not a scheduler crash.

All 120 work stages completed with at least 20 ms of thread CPU work.
The four failed trials completed their work chains in approximately
100.0, 87.4, 86.9 and 104.0 ms, then failed to receive presentation feedback
within the existing one-second deadline after commit. Failure to receive that
callback does not prove a frame was never displayed or determine display latency.
The run correctly stopped at its integrity gate.

The existing logs cannot distinguish compositor delay, client scheduling delay,
display state, or surface occlusion/suspension. No visibility history was recorded.
Do not attribute these failures to Rusty's policy, or dismiss them as environmental,
without additional evidence. The printed `completed` meant processes exited;
the subsequent validation had not passed. The runner now says `processes finished`,
then prints `validated` only after passing its gate.

## What the successful portion establishes

All nine idle preflights passed, including the real external-map benchmark
attachment for each Prophet arm. Ten heavy blocks passed, containing 300 trials.
The failed eleventh block adds 26 presented trials and four timeouts.
Do not pool the extra LAVD/Rusty blocks against single blocks for other arms.

The first complete heavy round had 30 trials per arm and 32 background workers:

| Arm | Presentation P95 (ms) | Background iterations/s (billions) |
| --- | ---: | ---: |
| Linux | 285.162 | 5.486 |
| LAVD | 129.718 | 5.226 |
| Rusty | 183.920 | 5.142 |
| Pulse propagation off | 142.078 | 5.085 |
| Prophet observe, 20 ms | 129.068 | 5.101 |
| Prophet static, 10 ms | 126.978 | 5.129 |
| Prophet static, 5 ms | 141.774 | 5.154 |
| Prophet adaptive | 147.474 | 5.216 |
| Prophet 20 ms without telemetry | 146.353 | 5.189 |

These are exploratory empirical percentiles, with very few observations in the
tail, from one round. They do not establish a ranking, precision, or a causal
advantage. The measured origin is synthetic client dispatch, not hardware input.
In particular, the adaptive P95 is 14.3% worse than observe in this block.
Static 10 ms is only 1.6% lower, below the preregistered 5% candidate screen.
Telemetry overhead cannot be isolated reliably from one observe/unobserved pair.

Adaptive mode really used all three profiles: 4,228 / 6,235 / 18,010 enqueue
observations for 20 / 10 / 5 ms in the sampled counter deltas.
The controller log reports 51.2 ms total process CPU time, including startup
and shutdown; this is not an isolated polling-overhead measurement.
Successful switching is functional evidence, not evidence that switching helps.
Policy thresholds and profiles have not been retuned after viewing these results.

The failed Rusty block's percentiles would describe only its 26 successful
trials, excluding a 13.3% feedback failure rate. They must not be reported as
overall latency tails. Summaries now explicitly label this conditional population.

## Changes and validation

The next benchmark emits `surface_state` for xdg-toplevel configure notifications,
and `feedback_timeout_diagnostic` with elapsed wait, last received state, negotiated
protocol version, and buffer occupancy. Suspension is `null` when unsupported or
not yet observed. xdg-shell version 6 defines suspension as not ordinarily being
repainted, with occlusion and screen lock as examples; see the installed primary
protocol `/usr/share/wayland-protocols/stable/xdg-shell/xdg-shell.xml`.
These are received compositor hints, not proof of visibility or exact event timing.
Diagnostics never skip trials, change workload, or extend the timeout.

Validation now saves per-block PASS/FAIL reports even for structural errors, and
includes timeout counts and trial IDs in the exception. Raw files remain intact.
No scheduler implementation, slice profile, comparator list, or acceptance gate
was changed. The measured benchmark binary is retained under the audit's `before/`.

Local verification: separate Prophet benchmark build; ASan/UBSan external-backend
and callback lifecycle tests; five equal-work worker chains; 14 Python tests.
Tests cover suspension/timeout lifecycle and rejection despite zero process exits.
New compositor diagnostics have not yet been exercised against the real compositor.
Current namespace has zero effective capabilities and NoNewPrivs=1; a fresh
privileged matrix cannot be run here.

## Next host run

Keep the benchmark surface visible on the active workspace and the display awake.
This is a measurement condition, not an explanation of the previous failure.
Run the same nine-arm matrix into a new directory:

```sh
sudo env WAYLAND_DISPLAY="$WAYLAND_DISPLAY" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
  python3 scripts/prophet_matrix.py --output results/prophet-host-matrix-02
```

Do not resume the old matrix with a changed executable, remove Rusty, extend
timeouts, or repeatedly rerun until success. A repeat failure should trigger
analysis of the retained diagnostics before another experiment.

The no-reinvention decision remains: userspace tuning/adaptive slices already
exist in Rusty, and predictive placement mechanisms exist in CAKE. This feedback
baseline is not the research contribution. Prediction/MPC remains gated on a
useful leading signal and matched evidence that simpler control is insufficient.
See `prophet-architecture.md` for the local source audit and prior-art references.
