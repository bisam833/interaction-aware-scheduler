# PulseSched / scx_prophet — frozen university review

**Release freeze: 20 October 2026.** Present this as a working Linux scheduler
with bounded interaction priority and an experimental adaptive slice controller.
Predictive MPC is **proposed and unimplemented**. No new experiments, algorithm
changes, or transition-matrix runs are part of this release.

## Problem statement

CPU contention can delay an interactive application even when total CPU
throughput is high. An interaction crosses several dependent worker stages
before a frame is presented; accelerating one handoff need not improve the
user-visible result. This project implements and measures interaction-aware
scheduling, then asks whether adjusting scheduling slices improves presentation
tail latency without excessive background-throughput loss.

## Frozen implementation and preservation

The last complete host-verified implementation is the **matrix-02 revision**:
repaired PulseSched with propagation off as the established baseline, and
`src/scx_prophet/build/scx_prophet` as the experimental feedback implementation.
Its nine-arm comparison completed 36 heavy blocks and nine idle preflights.
The Prophet executable SHA256 is:

```text
bcca58bcf5b24035b7c67e5ae02ed39f4e1b876d39dde646fbf85ea7d020571c
```

The measured source, executables, and per-file hashes were already preserved in
`results/prophet-matrix02-analysis-20261020/verified-implementation-snapshot.tar.gz`
and `snapshot-sha256.json`. A separate review copy and preservation manifest
are in `results/review-freeze-20261020/`. The manifest identifies matching live
scheduler files and original measured executables; it is an integrity record,
not a new runtime test.

Use the existing binaries for the review; do not rebuild or restore over the
working tree. Later transition tooling remains preserved but is not the
host-verified demonstration. Existing uncommitted work and raw results remain
in place. Older documents contain historical plans and superseded validation
status; this review uses the completed matrix-02 evidence.

## Actual implemented architecture

- **Kernel fast path:** sched_ext `struct_ops` callbacks written in eBPF, task
  storage for scheduling debt and timestamps, local idle-CPU dispatch, and a
  shared virtual-time dispatch queue (DSQ). Interaction epochs carry bounded
  priority; causal wakeup propagation is optional and off by default.
- **Userspace:** a libbpf loader owns the scheduler link and polls cumulative
  per-CPU telemetry. Modes are observe, static, and adaptive. The only adaptive
  parameter is the slice profile: 20, 10, or 5 ms.
- **Control interface:** one replaceable hash-map entry contains issuance time,
  generation, ABI, and profile. BPF validates it independently and falls back
  to 20 ms when absent, invalid, or at least 1.5 seconds old. Replacement does
  not constitute a simultaneous switch on all CPUs.
- **Telemetry:** per-CPU enqueue/runnable delay histograms and counters, without
  per-event streaming or inference in scheduling callbacks. Userspace polls
  every 100 ms by default; aggregation is not an atomic all-CPU snapshot.
- **Measurement:** the existing Wayland benchmark runs four equal 20 ms
  thread-CPU work stages and uses presentation feedback. The runner owns
  processes and scheduler lifetime; the external benchmark receives validated
  map descriptors and a pidfd for owner-death detection.

Source entry points: `src/scx_prophet/{prophet.bpf.c,prophet.c,control.h}`,
`src/pulsesched/pulse_policy.h`, and
`benchmarks/wayland_interaction/benchmark.c`.

## Scheduling algorithm, step by step

1. Track per-task virtual runtime debt and validate any active interaction epoch
   against its root, generation, and expiry. Optional wakeup inheritance is
   depth/expiry bounded; it does not grant permanent priority.
2. Select an idle CPU using the existing sched_ext helper when possible and
   insert locally. Otherwise enqueue into the shared virtual-time DSQ.
3. Bound sleeper advantage using
   `base = max(task_debt, shared_vtime - 20 ms)`, with wraparound-safe ordering.
   The shared queue key is `base - 20 ms` for a live interaction, otherwise
   `base`. This credit changes ordering, not accumulated runtime debt.
4. For each new insertion, read and validate the current configuration and
   choose a 20/10/5 ms slice. Existing queued entries are not reordered or
   rewritten by a profile update. A task-storage failure uses the baseline
   global-queue path.
5. Dispatch from the shared queue; record running timestamps and delay
   observations. On stopping, add `elapsed_running_ns * 100 / task_weight`
   to task debt. The elapsed interval can include interrupt time; it is not
   isolated task CPU time. Repeated interaction credit cannot erase this debt.
6. In userspace, accumulate supported 0.5–1 second windows with at least 128
   completed enqueue-wait samples. At least 10% waiting ≥8.388608 ms counts as
   high; at most 2% counts as low. Two high windows propose one shorter slice;
   three low windows propose one longer slice.
7. Enforce adjacent profile changes, a two-second minimum dwell, and a switch
   circuit breaker. Invalid or unsupported evidence prevents normal renewal;
   the BPF lease remains authoritative. No controller priority bypass exists.
8. If the controller stalls, the attached scheduler uses fallback parameters
   after lease expiry. If its owning process exits, its unpinned link closes
   and Linux scheduling resumes. These exercised failure paths passed host
   smoke checks; arbitrary kernel-fault recovery was not exhaustively tested.

## What is technically interesting

The project connects application interaction lifetimes to kernel scheduling
while keeping priority bounded and charging actual running intervals. It
separates a small BPF fast path from a fallible userspace control loop and
preserves useful behavior when that loop stops. Its measurement pipeline checks
equal CPU work, presentation completion, worker progress, and scheduler cleanup.

The important experimental findings include negative results: wakeup propagation
can speed handoffs yet worsen presentation, and a controller can replay every
decision correctly while choosing an unhelpful policy. Weighted virtual-time
queues, hybrid control, and adaptive slices have prior art, including Rusty and
LAVD; this work does not claim to invent them or establish predictive novelty.

## Verified results and limitations

Completed matrix-02: kernel `7.2.10-1-cachyos`, 16 allowed CPUs, 32 background
workers, four rotating blocks, 30 heavy trials per arm per block. All 1,080 heavy
trials and 27 idle-preflight trials presented successfully; recorded detach
checks returned sched_ext to disabled without increased rejection counts.

**Synthetic client-dispatch-to-presentation latency**, pooled descriptively
over 120 heavy trials per arm:

| Scheduler / mode | P50 ms | P95 ms | P99 ms | Background billion iterations/s |
| --- | ---: | ---: | ---: | ---: |
| Linux | 237.2 | 278.6 | 294.9 | 5.121 |
| LAVD | 123.1 | 138.5 | 166.0 | 4.868 |
| Rusty | 148.9 | 162.8 | 171.7 | 5.044 |
| Repaired Pulse, propagation off | 121.2 | 138.3 | 160.1 | 5.072 |
| Prophet observe, 20 ms | 123.8 | 164.2 | 242.5 | 5.056 |
| Prophet static, 10 ms | 125.3 | 141.9 | 169.9 | 5.045 |
| Prophet static, 5 ms | 128.1 | 136.0 | 141.4 | 5.025 |
| Prophet adaptive | 125.4 | 136.0 | 168.4 | 5.023 |
| Prophet 20 ms, telemetry off | 122.7 | 148.1 | 176.3 | 4.998 |

**Defensible conclusion:** adaptive improves on its observed 20 ms baseline in
all four paired blocks, but has no demonstrated advantage over static 5 ms,
repaired PulseSched, or LAVD. Against static 5 ms, adaptive's block P95 changes
were −2.89%, +14.57%, −1.50%, +0.56% (positive is worse). Equal pooled P95 hides
variability and adaptive's worse P99.

All 68 recorded controller decisions replay correctly, but all four runs
repeated 20→10→5→10→5 ms. Slice changes affect both wait duration and requeue
sample frequency, making self-induced feedback plausible; replay establishes
implementation consistency, not control quality or causal harm.

Adaptive controller process CPU was 51–54 ms per run, approximately
0.53–0.56% of one CPU over the sample interval, with startup/shutdown included
in the numerator. This does not isolate BPF overhead. All workers progressed;
the largest adaptive worker batch gap was 45.11 ms, a progress proxy rather
than a starvation bound.

Separately, the earlier matched Pulse matrix found propagation-off P95/P99
138.7/141.4 ms versus propagation-on 149.1/164.0 ms. Keep that evidence separate
from matrix-02: it explains the default-off decision, not another Prophet win.

Only about six observations support each pooled upper 5% and 1.2 the upper 1%;
no precise population P99, P99.9, or statistical significance is claimed.
The input origin is synthetic dispatch, not a physical device. Presentation
delay includes compositor/GPU/refresh behavior; compositor CPU delay is not
isolated. Results are specific to this host and workload.

Evidence: [complete host matrix](prophet-host-matrix-02.md),
[host smoke](prophet-host-smoke-01.md), and
[earlier matched Pulse analysis](host-matrix-v2-analysis.md).

## Why this is substantial OS engineering

This is an executing kernel/userspace system, not a policy simulation: it
requires verifier-safe BPF, scheduler callbacks, concurrent task accounting,
bounded task/epoch state, map ABI validation, libbpf link ownership, and
failure recovery. The project also implements a real multithreaded Wayland
measurement path, correct presentation-feedback lifetime handling, controlled
CPU work, and reproducible comparisons against established schedulers.
Preserved raw failures and matched baselines make the conclusions auditable.

## Shortest reliable review demonstration

**Use existing evidence first; no experiment or rebuild is needed.** From the
repository root, open this document's results table, then display the saved
controller summary and replay record:

```sh
cd /home/bisam/osproject
cat results/prophet-host-matrix-02/heavy-prophet-adaptive-0-controller.summary.json
cat results/prophet-matrix02-analysis-20261020/replay-results.json
```

Explain the recorded profile changes and why correct replay does not prove
better scheduling. The completed host matrix and smoke artifacts establish
the actual execution; reading them does not constitute a new test.

**Optional ten-second live attachment only if faculty requests execution:**
use the documented standalone command below on the same privileged Linux host,
with sched_ext initially disabled. It needs no Wayland or workload generator.
Do not run it if another scheduler is active.

```sh
cat /sys/kernel/sched_ext/state
sudo src/scx_prophet/build/scx_prophet --duration 10 > prophet-observe.jsonl
cat /sys/kernel/sched_ext/state
python3 scripts/summarize_prophet.py prophet-observe.jsonl
```

This command is already documented in `src/scx_prophet/README.md`; its
observe/attachment/termination paths were exercised by the host smoke.
Expected: initial and final state `disabled`, a temporary attachment and
telemetry, then automatic exit (Ctrl-C also terminates it). Use a fresh demo
directory or preserve an existing `prophet-observe.jsonl` before redirecting.
This demonstrates lifecycle and observation, not latency improvement or
adaptive switching under idle load. No live command was run for this freeze.

## Adaptive control versus predictive MPC

**Working:** measured-delay feedback selects bounded slice profiles, with
sample checks, hysteresis, dwell, lease fallback, and logged decisions.
It reacts to completed observations; it does not predict future demand.

**Unimplemented:** a validated workload forecast, state-space identification,
Kalman filtering, risk-sensitive MPC, an optimizer, or prediction-driven
scheduling. “Prophet” is a provisional project name, not evidence of prediction.

## Five likely faculty questions

1. **How is this different from changing process priority?**  
   A validated interaction epoch temporarily advances a virtual queue key;
   runtime debt continues accumulating and expiry removes the interaction
   advantage. It is integrated into full-system sched_ext dispatch.
2. **Can urgent work starve background tasks?**  
   Credit is bounded and running intervals are charged; tested workers all
   progressed. We have finite-run progress evidence, not a universal starvation
   theorem, cgroup fairness implementation, or hard real-time guarantee.
3. **What happens if userspace crashes or hangs?**  
   A stalled controller loses its 1.5-second configuration lease and BPF uses
   baseline parameters; owner death closes the link and returns to Linux.
   Freeze, stop, kill, and normal detach paths passed host smoke.
4. **Did adaptation beat the best static policy?**  
   No established advantage: both adaptive and static 5 ms pooled P95 were
   136.0 ms, with worse adaptive P99 and inconsistent paired-block differences.
   Adaptation is functional, but superiority is unproven.
5. **Where is the prediction or novelty?**  
   Prediction/MPC is not implemented. The defensible contribution for this
   project is the working interaction-aware OS integration, safe feedback
   prototype, and evidence showing when apparently useful mechanisms fail;
   established scheduling primitives are explicitly reused.

## Remaining issues — frozen, not scheduled work

Adaptive switching may reflect its own policy-dependent sensor, and its latency
advantage over fixed 5 ms remains unestablished.
Tail estimates and overhead attribution are limited by sample size and the
single-host measurement design.
Hotplug, broad affinity/nice/cgroup behavior, and prolonged stress are not
comprehensively validated.
The later transition tooling has local checks but no completed host comparison.
Predictive control and MPC remain unimplemented and outside this release.
