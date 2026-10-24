# PulseSched latency repair audit — 2026-10-18

Completed host validation: see [host-matrix-v2-analysis.md](host-matrix-v2-analysis.md)
for the full 48-block result, verified enabled-mode loading, and remaining
propagation tail regression. The account below preserves the earlier audit state.

Host follow-up: the first matrix passed propagation-off checks but uncovered a
propagation-on verifier rejection. The compiler-load repair and strengthened
preflight are documented in [propagation-verifier-fix.md](propagation-verifier-fix.md),
including the three completed idle blocks. The initial sandbox audit below
predates those host results.

Status: implementation repaired; native/build checks passed; **no after-change
scheduler latency or verifier result is available**. Do not deploy this as a
performance-validated scheduler. Existing JSONL and earlier audit documents are
historical evidence, not evidence for this revision.

## Findings, ranked

1. **Mixed-class scheduling defeats the boost under contention.** At `d25067a`,
   `pulsesched.bpf.c` enabled `SCX_OPS_SWITCH_PARTIAL`; the benchmark moved its
   main/worker threads to SCHED_EXT, while the runner's `yes` load and compositor
   remained in the fair class. Fair has higher scheduling-class precedence:
   https://docs.kernel.org/scheduler/sched-ext.html. Neither urgent DSQ priority
   nor a longer epoch can overcome that precedence. This is a proven policy
   defect and the leading explanation for the stalls, **not a measured causal
   allocation of every millisecond**. The contention runs lack enqueue traces.
   The raw heavy trial 1 spends 1384.249 ms before commit and only 19.370 ms
   from commit to presentation. The root's 250 ms epoch expires before return;
   presentation feedback cannot rescue work already delayed before commit.

2. **The headline Linux comparison did four times as many stages on PulseSched.**
   The untracked comparison runner used `20 ... 0` for Linux (one stage), versus
   `20 ... 250 dep-on` for PulseSched (four sequential stages). The historical
   idle medians are 20.084 versus 80.225 ms for work-to-commit. Thus 39.6 versus
   95.1 ms is not an equal-work scheduler comparison. Contention P95s came from
   **three**, not twenty, trials. The `half-linux-20` file has no completed trials.
   Previous workers also ended work by wall clock: waiting off CPU reduced the
   amount of computation. New workload version 2 uses per-thread CPU time and
   cannot be pooled with those old runs.

3. **Idle placement was sacrificed to instrumentation.** `891ad22` changed the
   normal idle path from `SCX_DSQ_LOCAL` to a shared DSQ solely to make dispatch
   counters increment. The urgent path already did the same. This gives up the
   selected CPU's direct dispatch, adds shared-queue work and allows another
   runnable task to take its place. The defect is visible in Git; its latency
   contribution is unmeasured.

4. **Strict urgent-first FIFO is unsafe as a full-system policy.** Dispatch always
   drained urgent before normal, without runtime debt, weights, or a service
   bound. Sustained/repeated epochs could starve normal tasks; TTL alone does
   not bound aggregate priority. This is an architectural starvation mechanism,
   not proof that it caused these particular five-thread benchmark stalls.
   Copied tokens also lacked root validation, allowing an in-flight copy to
   survive a userspace invalidation walk. Propagation could replace another
   interaction and insert unregistered recipients; no exit hook reclaimed keys.

5. **Measurement/lifecycle defects compromised responsiveness and evidence.**
   Presentation callbacks polled/slept waiting for epoch state (and always slept
   another 1 ms), blocking the event loop after presentation. Observation-error
   paths freed `pending` without clearing it, and timeout retained callback
   contexts; feedback proxies were not destroyed. Shared-memory pixels were
   reused without waiting for buffer release. `tick()` seeded runtime from
   epoch start, potentially counting off-CPU time; enqueue/dispatch counters
   omitted the select_cpu path. These do not explain the current trial's
   pre-commit stalls but make correctness and subsequent input handling worse.

## Implemented repairs

- Default full switching brings background and interactive work into the same
  scheduling class. Partial switching is available only as an explicit benchmark
  ablation (`PULSESCHED_PARTIAL=1`). Standalone loader reports full switching.
- Restore idle CPU local insertion. Count enqueue/running events on both paths.
- Replace two strict-priority FIFOs with one weighted virtual-time DSQ using
  the scx_simple approach. Actual on-CPU intervals accrue debt in task storage,
  independent of userspace token replacement. Active interactions receive one
  slice of queue-order credit, never a reset of runtime debt. Sleeper credit is
  bounded to one slice; normal work competes in the same order. **20 ms slices
  are unchanged** to isolate policy from tuning. There is still no wakeup
  preemption; shorter slices/preemption require a measured follow-up.
- Inherited eligibility checks the live root ID AND start time. Only registered
  recipients can inherit; direct tokens/live interactions are protected; stale
  root-invalid copies can be replaced. Exit removes registration; task storage
  is automatically reclaimed. The depth limit remains 2.
- Share the token ABI header across BPF, controller and benchmark. Correct build
  dependencies force the embedded benchmark skeleton to rebuild with BPF edits.
- Snapshot epoch state without blocking the event loop; close on presentation,
  discard and feedback timeout, preserving TTL precedence. Destroy callback
  proxies and clear pending state before freeing; use two release-tracked shm
  buffers. Clean up partial worker creation and partial scheduler initialization.
- Add equal four-stage `dep-normal` for Linux/LAVD, versioned CPU-work metadata,
  optional synthetic client-side initiation, per-worker handoff/wall/CPU times,
  presentation-feedback delivery delay and refresh interval. Reject incompatible
  presentation clocks. Optional enqueue-to-running log2 histogram is off by
  default. The old manual comparison now runs the same chain for both modes and
  writes distinct v2 filenames.

## Historical measurements (not before/after validation)

Milliseconds; linear-interpolated empirical percentiles, recomputed directly
from preserved raw JSONL. Full P50/P95/P99 and source filenames are in
`results/audit-static-20261018/historical-summary.json`.

| Condition | trials per mode | Linux total P50 / P95 | old Pulse total P50 / P95 | old Pulse work P95 | old Pulse commit→present P95 |
|---|---:|---:|---:|---:|---:|
| Idle, 20-trial files | 20 | 39.643 / 63.154 | 95.116 / 125.050 | 80.300 | 44.832 |
| Half, 3-trial files | 3 | 58.313 / 69.698 | 130.312 / 1244.146 | 1224.312 | 46.815 |
| Heavy, 3-trial files | 3 | 37.766 / 39.165 | 1043.260 / 1367.583 | 1347.815 | 23.008 |

**After: unmeasured for every mode/condition.** P95s of separate stages do not
add algebraically. Old half/heavy meant ncpu/2 and ncpu-1 workers, respectively;
new heavy means 2*ncpu workers, explicitly recorded. Do not compare these labels
without checking worker count. The old clock IDs were CLOCK_MONOTONIC.

## Executed validation

- Clang BPF compilation, bpftool skeleton generation, loader/control/benchmark
  and background-load builds passed. BTF object parsing passed.
- Native policy tests with ASan/UBSan passed: persistent urgent versus normal
  CPU work, weight 2:1, debt persistence, wraparound order, TTL and root closure.
  These are single-CPU policy-model checks, not SMP or kernel verifier proofs.
- Actual worker implementation: five four-stage CPU-work chains and teardown
  passed without Wayland/BPF.
- Actual feedback callbacks with only proxy destruction mocked: presented,
  discarded, timeout, map-observation failure and release gating passed under
  ASan/UBSan. LeakSanitizer is disabled because this namespace runs under ptrace;
  address and undefined-behavior instrumentation remain enabled.
- Summary regression test keeps missing/timeout/invalid-clock trials visible.
  Python compilation, changed Fish syntax and Git whitespace checks passed.
- Actual BPF loading attempt: libbpf's trivial capability probe failed `EPERM`,
  **before PulseSched reached the verifier**. Effective caps are zero and
  `NoNewPrivs=1`. Wayland connection failed. Installed `scx_lavd` is available,
  but cannot be attached here. sched_ext remained `disabled`, nr_rejected=0;
  this is unchanged state after a denied load, not a successful fallback test.

## Exact host validation

Run from the logged-in Wayland session, with no other sched_ext scheduler active.
The runner refuses to replace an existing scheduler and only stops its children.
The performance run automatically repeats the smoke gate. Bash commands:

```bash
cd /home/bisam/osproject
make -C src/pulsesched -j2
make -C benchmarks/wayland_interaction -j2 all check
cc -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined \
  tests/pulse_policy_test.c -o /tmp/pulse-policy-test
ASAN_OPTIONS=detect_leaks=0 /tmp/pulse-policy-test
python3 -m unittest discover -s tests -p 'test_*.py'
sudo python3 scripts/audit_latency.py --smoke --output results/audit-host-smoke
sudo env WAYLAND_DISPLAY="$WAYLAND_DISPLAY" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
  python3 scripts/audit_latency.py --trials 30 --blocks 4 \
  --output results/audit-host-matrix
python3 scripts/summarize_latency_audit.py results/audit-host-matrix \
  > results/audit-host-matrix-summary.jsonl
```

Smoke checks actual verifier/load, attachment, SIGTERM detach, stale/TTL state
and disabled fallback with unchanged rejection counter. Do not proceed if it
fails. Kernel watchdog/error fallback under stress still requires live review.
The matrix rotates condition and scheduler blocks: Linux, LAVD default, Pulse
propagation-off and Pulse propagation-on. All run 4×20 CPU-ms. It keeps missing,
discarded, timeout and failed runs, records binary hashes, process CPU/context
switches, /proc/stat snapshots and every load worker's throughput/progress gap.
The 180 s per-block watchdog is a failure boundary, not a latency censoring rule.

Repeat the same matrix in a fresh output directory with `--partial-ablation`
to isolate scheduling-class precedence, and separately with `--trace-delay`
to quantify observer overhead. Compare per-block distributions, not only pooled
P95s; increase samples if P99 estimates remain unstable. The partial ablation
uses the repaired queue policy, so only the switching mode differs. A blocked
run is retained and aborts further measurement instead of silently continuing.

For exact runnable-to-running delay, capture short, separate trace runs with
`perf sched record -a -- sleep 10` while a matrix block is active, then
`perf sched timehist -i perf.data`. Filter to the root/worker TIDs emitted in
`dependency_chain`; inspect sched_waking/sched_switch and CPU placement. `perf`
is not installed in this namespace. Also test concurrent epochs, affinity,
CPU-heavy clients with repeated urgency, cancellation mid-chain and normal-task
progress before accepting a full-system scheduler. Test a CPU-bound urgent task
beside a normal task on one CPU and compare their runtime shares and maximum
runnable delay; native fairness tests cannot validate kernel integration.

## Remaining limits

- **No measured improvement yet.** Full-system attach expands the blast radius.
  Queue locks, approximate shared vtime updates, 20 ms non-preemptive slices,
  cgroup policy (not implemented), topology/NUMA and SMP fairness remain live
  validation requirements. Do not infer system stability from compilation.
- “Input” begins at the client callback, after input delivery/scheduling; synthetic
  runs begin inside the client. Neither measures physical-input-to-photon or
  initial compositor delivery delay. Human-input mode remains available; both
  workloads report the origin. Feedback timeout begins at commit, so pre-commit
  stalls remain fully present in total latency rather than becoming timeouts.
- Handoff time includes eventfd/syscall overhead and is not pure scheduling delay.
  The optional BPF histogram starts at enqueue/select_cpu and covers all tasks;
  it omits delays before those callbacks and mixes normal/interactive classes.
  Refresh-interval waits are not measured dropped-frame counts; this is a
  one-response-per-stimulus test, not a continuous animation benchmark.
- Process CPU and /proc/stat are overhead proxies, not isolated BPF cost. Background
  throughput/progress gaps cover each whole run including warmup/attach/teardown,
  so use targeted steady-state tracing before attributing small differences.
- Wake-context attribution remains heuristic. Registered-only inheritance avoids
  boosting unrelated unregistered tasks, but the benchmark is still an explicit
  cooperative prototype, not general desktop foreground classification. Compositor
  tasks receive fair service under full switching, not automatically an epoch.
- Root validation prevents stale eligibility after closure; queued keys are not
  retroactively rekeyed and may retain at most the bounded credit. Userspace and
  BPF still share best-effort epoch counters; map read/modify/write can lose an
  accounting update. In-flight runtime is absent from immediate snapshots, and
  inherited closure accounting is not a precise measure of service caused by a
  boost. Policy debt lives separately and does not depend on those counters.
- The pre-existing modified `scripts/run_phase6_correctness.fish` has a malformed
  TTL argument. It was preserved; use the new smoke/matrix commands above.
