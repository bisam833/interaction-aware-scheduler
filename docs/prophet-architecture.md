# scx_prophet: Phase-0 decisions and Phase-1 implementation

2026-10-18. **Experimental feedback baseline, not a predictive scheduler yet.**
The first host smoke run now passes verifier/load, tested progress, controller
lease expiry and detach checks. Adaptive mode remained at profile 0; there is
no adaptive-improvement or prediction result. See
[the host follow-up](prophet-host-smoke-01.md) for evidence and the new benchmark
adapter. The subsequent host matrix passed all nine idle preflights and exercised
all adaptive profiles under heavy load, but stopped on four Rusty presentation
feedback timeouts. The first complete heavy round does not show adaptive benefit;
see [the partial matrix audit](prophet-host-matrix-01.md). Privileged execution
remains unavailable inside this namespace.

2026-10-20 update: [matrix-02](prophet-host-matrix-02.md) passed all 36 measured
blocks (1,080 heavy trials) and nine preflights. Adaptive improves on observe in
all four paired blocks but has no established advantage over static 5 ms,
repaired Pulse-off or LAVD. Repeated 10/5/10/5 ms reversals track an action-dependent
wait signal; 68 offline decisions replay exactly through the current controller.
Keep prediction/MPC gated. No scheduler thresholds were retuned after these data.

The next implementation is the [frozen-controller transition protocol](prophet-transitions.md):
absolute offered inputs, scheduled background eligibility, seeded arm order,
per-phase analysis and fresh host gates. It preserves the scheduler and existing
benchmark binaries. Local load/sanitizer tests pass; privileged transition and
Wayland results remain pending.

## Reality and reuse

The working tree already contained substantial repairs beyond `d25067a`; `.git`
is a protected directory and the actual metadata is `.git-repo`. During initial
Phase 1, no original source, binary, raw result, or pre-existing edit was changed.
The subsequent benchmark integration adds an optional external backend and
runner hook while preserving the baseline scheduler and original binaries.
`results/prophet-phase1-20261018/baseline-before.tar.gz`, its SHA256 manifest,
working diff and status preserve the starting implementation. Prophet is a
separate build in `src/scx_prophet`, derived from that repaired BPF source and
sharing `src/pulsesched/pulse_policy.h`. This duplication isolates an experiment;
do not refactor the verified baseline until Prophet passes host validation.

Reuse: default idle selection, local dispatch, one shared virtual-time DSQ,
weighted runtime debt in task storage, bounded sleeper/interaction credit,
the existing epoch ABI and root validation, libbpf, generated skeletons, scx
compatibility/UEI helpers, the existing fixed-work background worker and runner
cleanup checks. No replacement scheduler framework, inference runtime, solver,
custom BPF loader, or new presentation benchmark was built.

Recomputed the original 48-block matrix using the existing summarizer, retaining
failures. Heavy client-dispatch-to-presentation results (120 samples each):

| Implementation | P95 ms | P99 ms |
|---|---:|---:|
| Linux | 286.288 | 287.322 |
| LAVD | 137.063 | 153.385 |
| Repaired Pulse, propagation off | 138.676 | 141.362 |
| Repaired Pulse, propagation on | 149.077 | 164.017 |

These are **baseline evidence, not Prophet results**. Faster handoffs with
propagation did not offset the downstream presentation regression. The benchmark
starts at synthetic client dispatch, uses four equal CPU-work stages, and does
not measure physical input delivery. Handoff includes syscalls; commit-to-present
includes GPU/compositor/refresh effects. Compositor CPU delay is not isolated.
P99 here has very little tail support; P99.9 is not defensible. See the existing
[host analysis](host-matrix-v2-analysis.md) for scope and progress-gap evidence.

## Prior art and the novelty boundary

Inspected local scx commit `fead94e15184e957469b346b129f412641691bbe` and C examples
commit `370b1cea95c6bb668d5b9261f069d433142b9986` (these are source references,
not the version of every installed baseline executable).

| Established mechanism | Evidence inspected | Decision |
|---|---|---|
| Weighted virtual-time DSQ and bounded sleeper credit | `external/scx-c-examples/scheds/c/scx_simple.bpf.c` | Reuse Pulse's derivative; no novelty claim. |
| Hybrid userspace/BPF scheduler and adaptive slices | `external/scx/scheds/rust/scx_rusty/src/{main.rs,tuner.rs}`: 100ms tuner, 20ms/1ms under/overutilized defaults | Our slice portfolio is a simple comparison baseline. |
| Latency-aware deadlines, runtime history and adaptive slices | `external/scx/scheds/rust/scx_lavd/src/bpf/{main,lat_cri,sys_stat}.bpf.c` | Do not call LAVD universally reactive or unaware of dependencies. |
| Anticipating a near-future interruption | `external/scx/scheds/rust/scx_cake/src/bpf/cake.bpf.c`, `cake_cpu_tick_soon`: uses timer next event and probed wake-hop cost | Existing predictive placement directly defeats a blanket novelty claim about prediction. |

Current primary references: [LAVD](https://github.com/sched-ext/scx/tree/main/scheds/rust/scx_lavd),
[Rusty](https://github.com/sched-ext/scx/tree/main/scheds/rust/scx_rusty),
[CAKE](https://github.com/sched-ext/scx/tree/main/scheds/rust/scx_cake).
Beyond sched_ext, [Shenango (NSDI 2019)](https://www.usenix.org/conference/nsdi19/presentation/ousterhout)
already reallocates CPU cores at microsecond timescales, and
[Caladan (OSDI 2020)](https://www.usenix.org/conference/osdi20/presentation/fried)
uses carefully selected signals/actions to respond to interference. Their
datacenter runtimes are not drop-in desktop schedulers; feedback CPU allocation
and split control timescales nonetheless have substantial prior art. This is a
targeted review, not an exhaustive novelty search.

**Proposed contribution: none demonstrated.** A future hypothesis is that a
signal available *before* an interaction's downstream CPU demand can improve
placement/slicing before that demand arrives. It must beat static short slices,
ordinary feedback, and simple phase-informed epoch closure. Moving epoch closure
from presentation to commit alone is not predictive research. No evidence yet
shows existing schedulers cannot achieve the useful behavior.

## Smallest implemented controller

Profiles are a closed enum: 0 = 20ms, 1 = 10ms, 2 = 5ms. For every profile:

```
base = max_in_wraparound_order(task_runtime_debt, shared_vtime - 20ms)
queue_key = base - (live_interaction ? 20ms : 0)
debt += elapsed_running_interval * 100 / task_weight
```

Credit never resets debt; the 20ms credit and sleeper floor stay fixed when a
profile changes. There is no new preemption mechanism, arbitrary deadline offset,
controller priority exemption, CPU reservation, or partial switching. Parameters
only select the slice for a **new insertion**. Existing DSQ keys/slices are not
rewritten; no cross-profile queue ordering discontinuity is introduced by a new
offset. Previously queued and running work can retain its old slice. Running
interval accounting follows the baseline's ktime measurement, including interrupt
time within that interval; it is not an isolated task CPU-time measurement.

Modes: `observe` (default, profile 0), `static --profile N`, `adaptive`. Observe and
adaptive execute the same BPF telemetry. Static accepts `--no-telemetry` for the
observer-cost ablation. Propagation remains opt-in and is outside control.

Feedback polls at 100ms by default; 20–200ms is exposed for later observer-cost
experiments. Decisions accumulate >=500ms and <=1s of completed enqueue delays
with >=128 samples. A fraction >=10% at or beyond 8.388608ms counts as high wait;
<=2% counts as low wait. Two consecutive high windows propose one shorter slice;
three low windows propose one longer slice. There is at least 2s between normal
changes, only adjacent steps, and after six changes in a 30s era an additional
attempt triggers a 30s baseline-only circuit breaker. Insufficient support,
late polls, map read failures and counter regressions reject control evidence.
These thresholds were chosen before host outcomes; they are not optimality or
confidence claims. On recovery the controller restarts from profile 0.

This intentionally slow baseline is **not** designed to beat reactive schedulers
on a 10ms burst. Its purpose is to establish the cost and causal value of an
adaptive slice knob. It cannot detect an unprofitable end-to-end change from
scheduler telemetry alone, so adaptive mode is experimental and observe remains
the default. Profitability requires matched presentation/throughput data.

## Parameter consistency and failure behavior

One `BPF_MAP_TYPE_HASH` entry contains `{issued_ns, generation, abi, profile}`.
Userspace replaces it using `bpf_map_update_elem`; BPF has `BPF_F_RDONLY_PROG`
access and never mutates a published value. Hash replacement is atomic according
to the [kernel map documentation](https://docs.kernel.org/bpf/map_hash.html).
Each enqueue reads one entry; CPUs may legitimately use different generations
during a transition. This is not a simultaneous all-CPU switch. The generation
is diagnostic, not an authenticated replay defense against a privileged writer.

BPF independently rejects missing, future-dated, ABI-invalid, out-of-range or
>=1.5s-old entries and uses profile 0. The lease is fixed in BPF, not a userspace
supplied expiration. Healthy static/observe renew every poll; adaptive renews on
supported decision windows. Invalid reads revoke the entry; insufficient sample
support stops renewal. Already queued work keeps its bounded old slice. There
is no claim that all tasks run or all old work drains within the lease duration.

If the controller/logger stalls, callbacks continue with baseline parameters
after lease expiry. If its process dies, its unpinned struct_ops link closes and
the kernel should return to Linux. Kernel sched_ext error/stall handling remains
independent of the controller, as specified in the
[kernel scheduler documentation](https://docs.kernel.org/scheduler/sched-ext.html).
Both controller lease expiry and process-death detach have now passed the host
smoke test. They still cannot be executed inside this namespace; arbitrary
kernel error/stall recovery was not fault-injected.

## Telemetry and rejected assumptions

One additional per-CPU value holds 73 cumulative u64 counters (584 bytes per
possible CPU): 32-bin enqueue-to-running and runnable-to-first-running histograms,
effective profile/fallback counts, local/shared insertions, completed running
interval time, and runnable/quiescent events. Fixed cost buckets and atomic
increments; no per-event ring buffer or inference. Existing epoch/observer maps
remain for source/ABI continuity, so 584 bytes is additional, not total memory.
Task storage scales with the kernel's task population, not an unbounded event log.

Userspace makes one lookup per poll, subtracting each CPU's cumulative values
without resetting them. Individual aligned counters on this x86 build are useful;
the multi-field/multi-CPU copy is **not** a coherent point-in-time snapshot.
Sample totals come from bin sums. A failed read forces rebaselining before reuse.
No double-buffer handoff pretends to solve in-flight readers/writers.

- Short-window p99 is rejected: 128 observations do not support tail prediction.
  The feedback uses a coarse exceedance fraction, not an estimated p99. Even
  that fraction is correlated and completion-biased under overload.
- Buckets report bounds, not interpolated nanosecond percentiles. The final bin
  is unbounded above. The summarizer withholds quantiles unless both empirical
  tails have >=20 observations; this is a support rule, not a confidence bound.
- Runnable callback timestamps omit time before that callback, including part
  of wakeup/CPU selection; they are not exact `sched_waking` measurements.
  Enqueue histograms mix wakeups and runnable requeues. Never conflate the two.
- Unfinished waits do not appear until a task runs. Counter differences are not
  instantaneous queue lengths; running intervals are charged at stopping and
  can cross window boundaries. No utilization or queueing identity is assumed.
- No identifiable state-space model has been established. Congestion, placement,
  controller actions, IRQs, frequency and GPU/refresh behavior are confounded.
  Saturation invalidates casual stationary queueing estimates; a Gaussian
  residual model does not establish p99 calibration.
- Polling at 50–100Hz has no demonstrated benefit here. Measure observer-on/off
  throughput, presentation latency and total controller CPU before raising rate.
- Fairness has native policy-model checks plus a short pinned two-task host test. Affinity,
  migration, hotplug, SMP races in the approximate shared clock, cgroup fairness
  (not implemented), concurrent epochs and prolonged self-starvation remain
  host validation concerns. No real-time bound is claimed.

## Gates and next experiment

1. Pass `scripts/prophet_smoke.py` on the privileged host. It tests all exposed
   rodata branches, pinned persistent-urgent versus normal worker progress,
   accounting activity, static profile use, stopped/frozen controller leases,
   SIGTERM and SIGKILL detach, and unchanged rejection count. These are smoke
   bounds, not a general fairness proof. Extend to affinity changes, nice weights,
   hotplug and concurrent/inherited epochs before broad use. The propagation
   variant currently validates loading, not a causal inheritance chain.
2. The external-map backend is now implemented in a separate benchmark build.
   Its real all-arm Wayland preflight passed in matrix-01; the full comparison
   remains incomplete (see the partial matrix audit). The original embedded Pulse loader cannot attach alongside Prophet.
   Do not run the old matrix while Prophet is loaded or relabel dep-normal as
   interaction-on.
3. Use the existing rotating-block design, raw trials and background worker. New
   arms: original Linux/LAVD/Pulse-off, Prophet observe, static 10ms/5ms, adaptive,
   and static20 telemetry-off. Include Rusty's existing tuner as a direct prior-art
   comparator. Trace compositor runnable delays separately. Use steady heavy load,
   announced/unannounced bursts and phase changes; preserve failed trials.
4. Freeze the comparison and throughput/progress acceptance margins before data;
   evaluate paired block uncertainty, recovery delays and switching counts. The
   current four blocks/120 trials are not sufficient for precise tail claims.
5. Only if feedback helps and a leading signal exists, train a small interpretable
   forecast offline, evaluate chronologically held-out workload changes,
   calibration/coverage, persistence/feedback baselines and shadow-mode overhead.
   Only then permit bounded predictive actions. Compare prediction on/off with
   equivalent telemetry and userspace work. MPC requires an advantage over those
   mechanisms; no solver or online exploration is justified yet.
