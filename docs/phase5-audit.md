# Phase 5 closed-loop presentation termination

## Scope

Phase 5 adds only the userspace control transition from a matched Wayland
presentation callback to the selected PulseSched epoch. The scheduler still
uses `SCX_OPS_SWITCH_PARTIAL`, the same task registration and two DSQs, and a
100 ms maximum TTL. No dependency propagation, frequency control, or new
scheduling heuristic was added.

The benchmark uses the interaction ID as the frame ID and epoch ID. On a
`presented` callback it reads the task-state map, verifies the ID, writes
`active=0`, the callback-time end timestamp, and reason 1
(`PRESENTATION_FEEDBACK`), then reads back the final state. A discarded frame,
missing feedback, or an already-expired epoch remains a `TIMEOUT` fallback and
is recorded as such. Late feedback is emitted as `late-feedback` and is not
counted as successful termination.

Every trial now records epoch start/end, configured maximum TTL, termination
reason and name, boosted runtime, normal enqueues, and urgent enqueues in
addition to the existing input/work/commit/presentation timestamps.

## Correctness evidence

Raw files are preserved in `results/raw/`:

- `closed-correctness-20261014-010745.jsonl`: one presented trial, reason
  `PRESENTATION_FEEDBACK`; epoch duration 46.593 ms with a 100 ms maximum TTL.
- `closed-fallback-20261014-010801.jsonl`: one timeout trial, reason `TIMEOUT`,
  epoch duration 105.371 ms; late feedback was explicitly reported.

Both runs ended with `state=disabled` and `nr_rejected=0`.

## Matched matrix

Each valid matrix file contains 20 final trials with 20 ms CPU work and the
same background generator used for the Phase 4 matrix: 0, half, or
`nproc-1` `yes` workers. Raw Phase 4 files were not changed.

| condition | mode | interaction to presentation | commit to presentation | epoch duration | termination |
|---|---|---:|---:|---:|---|
| idle | normal | 61.204 / 70.962 | 41.111 / 50.887 | N/A | N/A |
| idle | TTL | 33.366 / 37.068 | 13.255 / 16.979 | 105.849 / 108.679 | 20 TIMEOUT |
| idle | closed | 63.761 / 67.400 | 43.661 / 47.292 | 64.493 / 67.950 | 20 PRESENTATION_FEEDBACK |
| half CPU | normal | 35.918 / 63.300 | 15.761 / 43.200 | N/A | N/A |
| half CPU | TTL | 35.486 / 38.285 | 15.349 / 18.169 | 106.337 / 109.659 | 20 TIMEOUT |
| half CPU | closed | 62.420 / 65.621 | 42.302 / 45.370 | 62.878 / 68.457 | 19 PRESENTATION_FEEDBACK, 1 TIMEOUT |
| heavy | normal | 48.679 / 53.525 | 28.538 / 33.165 | N/A | N/A |
| heavy | TTL | 67.794 / 80.910 | 47.651 / 60.665 | 109.792 / 193.529 | 20 TIMEOUT |
| heavy | closed | 90.239 / 137.096 | 69.725 / 83.207 | 96.178 / 199.984 | 11 PRESENTATION_FEEDBACK, 9 TIMEOUT |

The closed-loop result did not reduce the heavy-contention latency regression
in this run. Its median interaction-to-presentation latency was higher than
both normal Linux and fixed TTL. This is an observation, not evidence that the
mechanism is universally worse: the matrix is small, platform-specific, and
contains substantial feedback timeouts under heavy contention.

The fixed-TTL heavy run still shows the previously observed overshoot of a
nominal 100 ms TTL: median 109.792 ms and P95 193.529 ms. The closed-loop run
also shows timeout overshoot when presentation feedback arrives too late.

## Interleaved TTL/Closed audit

To separate mode sequencing from separate-process/session effects, an `ab`
mode was added to the benchmark. In one process with one scheduler attachment
and one half-CPU `yes` load, 40 interactions alternated TTL, Closed, TTL,
Closed. The resulting raw file is
`results/raw/ab-half-40-20261014-011952.jsonl`.

| pre-presentation measure | TTL median / P95 ms | Closed median / P95 ms |
|---|---:|---:|
| input to work | 0.000230 / 0.000325 | 0.000231 / 0.000342 |
| epoch map registration after input | 0.016792 / 0.021255 | 0.017203 / 0.026480 |
| work to commit | 20.113205 / 20.128426 | 20.113406 / 20.223688 |
| commit to feedback request | 0.000080 / 0.000169 | 0.000086 / 0.000254 |

These measurements support that epoch creation, map registration, CPU work,
feedback request, and commit occur through the same pre-presentation path. The
small timestamp differences are not zero, so “identical” means the same code
and state transition, not bit-for-bit equal runtime scheduling.

The interleaved presentation measurements were 60.980 ms median for TTL and
59.172 ms for Closed; commit-to-presentation was 40.869 ms versus 39.057 ms.
Thus this controlled run did not reproduce the earlier sequential Closed
regression. It also found a counter difference: TTL recorded 3 urgent enqueues
and Closed recorded 0. These counters are wake/enqueue events observed before
the eventual state readback, not proof that one mode has a different urgency
rule. The BPF `urgent_now()` branch is mode-independent before feedback; the
two modes differ only inside the presentation callback and in how long they
hold the process after that callback. That post-callback difference changes
the next trial's starting environment, even in an alternating sequence.

Conclusion: pre-presentation policy code is shared, but observed runtime
behavior is not proven exactly identical. The interleaved evidence rules out
the claim that closed-loop has a distinct earlier scheduling branch before
feedback, while leaving scheduler-event timing and cross-trial carry-over as
real contributors requiring larger experiments.

## Limitations

The benchmark proves compositor-reported presentation feedback for its own
surface, not physical glass-to-glass latency and not universal attribution for
arbitrary desktop applications. `normal_enqueues` and `urgent_enqueues` are
per-task enqueue counters; zero is a valid observation and is not converted
into a dispatch claim. The closed-loop path is userspace-controlled and uses
the TTL as a safety bound; it is not a kernel-resident presentation notifier.

An earlier heavy closed-loop attempt was incomplete because pending input was
fatal. The harness now emits `input-ignored` as a visible nonfatal notice, and
the incomplete raw files remain preserved rather than included in the matched
20-trial result.
