# Project status

## Current milestone

**PHASE 5 — PARTIAL**: presentation feedback now explicitly terminates the
matching PulseSched epoch, with a TTL fallback and per-trial termination
reason. Correctness and matched 20-trial idle, half-load, and heavy-load raw
runs are preserved. The closed-loop run did not improve the heavy-contention
regression in this dataset. Further interpretation and any later phase remain
unapproved.

## What now works

- Host facts and tool versions have been collected in
  [`docs/environment.md`](docs/environment.md).
- The kernel exposes `/sys/kernel/sched_ext` and reports `CONFIG_SCHED_CLASS_EXT=y`.
- Wayland, GNOME Shell 51, Wayland protocols, libbpf, and LLVM tooling are
  installed.

## Verified

- Kernel: CachyOS 7.2.9-2-cachyos, x86_64.
- `CONFIG_SCHED_CLASS_EXT=y`, BPF syscall/JIT, and BTF are present.
- Current sched_ext state is `disabled`; `nr_rejected=0` at audit time.
- `scx-scheds` 1.1.3 and `scx-tools` 1.1.3-1.1 are installed.
- Current session is Wayland/GNOME 51 with `wayland-0`.
- The installed kernel build headers expose `SCX_OPS_SWITCH_PARTIAL` and
  `SCHED_EXT=7`.
- Phase 1A binary smoke test passed with `scx_rusty`: enabled state, visible ops,
  normal termination, disabled recovery, `enable_seq` increment, and
  `nr_rejected=0` were verified.
- Official C `scx_simple` built unchanged with exit code 0, attached as
  `simple`, and returned to `disabled` after normal termination.
- Official C `scx_qmap` built unchanged; `-p` attached as `qmap`, and one `yes`
  process was set to `SCHED_EXT` (policy 7) while the scheduler was enabled.
- Partial-mode cleanup returned sched_ext to `disabled` with `nr_rejected=0`.
- Project-owned Phase 2 BPF and userspace programs build with exit code 0.
- Phase 2 revision adds direct `NORMAL_DSQ` dispatch instrumentation and a
  `--stale` expired-record test mode.
- Project-owned `pulsesched` attached in partial mode and returned cleanly to
  `disabled` after Ctrl-C.
- The controller registered itself as `SCHED_EXT`; two epochs ended inactive
  with timeout reason 2 and nonzero boosted runtime.
- Urgent dispatch instrumentation was nonzero (`metric[1]=14`).
- Malformed TTL input was rejected without loading the scheduler.
- The revised long workload produced `metric[0]=1` and `metric[1]=9`, proving
  both normal and urgent dispatch paths.
- A 100 ms workload reported 100250095 ns of scheduler-observed boosted
  runtime, resolving the previous short-workload accounting undercount.
- An already-expired stale record safely became inactive with timeout reason 2.
- `nr_rejected` remained 0 before and after the long and stale tests.
- The Phase 3 benchmark builds against generated local presentation-time and
  xdg-shell client bindings.
- The first host run bound `wp_presentation` and reported version 2 with
  `CLOCK_MONOTONIC`, then exposed a missing newer xdg-toplevel listener;
  compatibility callbacks were added and the benchmark rebuilt successfully.
- The corrected normal-scheduling run produced three independently matched
  interaction/commit/presentation records with presentation flags 7.
- The benchmark now builds with the project-owned PulseSched skeleton and can
  start one fixed-TTL epoch per real interaction; privileged runtime validation
  is pending.
- Five idle fixed-TTL trials completed; all presented and all scheduler epochs
  ended later by timeout with approximately 20 ms boosted runtime.
- Five heavy-contention fixed-TTL trials completed using `nproc - 1` busy
  workers; all presented and all epochs ended by timeout. This condition caused
  temporary desktop lag and is labeled heavy, not moderate, contention.
- Five normal-Linux half-CPU-contention trials are collected for the paired
  reference.
- Fish condition runner and raw JSONL summarizer have been added for the
  required 20-trial six-condition matrix.
- `external/scx` is official `sched-ext/scx` commit
  `fead94e15184e957469b346b129f412641691bbe`.
- Phase 0 documentation was committed as `9b8abc2` in an explicit Git
  directory `.git-repo`, because the host-mounted `.git` directory is empty,
  read-only, and cannot be removed or replaced.

## Still unverified

- Independent enumeration proving that every ordinary desktop task remained
  outside sched_ext during partial mode.
- Full ordinary-task policy census during partial mode remains unperformed.
- Normal-vs-fixed-TTL baseline measurements under idle and contention are not
  not yet collected at the required 20-trial minimum.
- The corrected idle and contention TTL runs are collected. A matched
  normal-Linux heavy-contention run is not required for the current moderate
  paired reference.
- Heavy-TTL remains incomplete after three attempts; the latest has 5/20
  completed trials, one ignored input, and one unmatched interaction.
- A final heavy-TTL run completed 20/20 trials. The selected six-condition
  matrix now has 120 valid trial records; all TTL epochs ended by timeout and
  all presentation records remained measurement-only.
- Phase 5 correctness proved a presented epoch ended with
  `PRESENTATION_FEEDBACK` before the 100 ms maximum, while the forced missing
  feedback case ended with `TIMEOUT` and emitted `late-feedback`.
- Phase 5 closed-loop idle and half-load runs completed 20/20 trials; the
  heavy final run completed 20/20 trials with 11 presentation-feedback and 9
  timeout terminations. All three closed-loop runs unloaded cleanly with
  `nr_rejected=0`.
- The first interactive run aborted before surface presentation because of the
  listener bug; that output is not valid benchmark evidence.
- A compositor-discarded callback has not yet been separately induced; the
  missing-feedback timeout fallback was exercised.
- A causal input-to-surface-to-presentation association.
- Any PulseSched behavior, latency, CPU-time, or throughput measurement.

## Blockers

- Unprivileged probing reports missing `CAP_SYS_ADMIN`, `CAP_BPF`,
  `CAP_NET_ADMIN`, and `CAP_PERFMON`. A controlled privileged test or an
  administrator-provided loader path is required before Phase 1 can be marked
  working.
- The sandbox cannot perform the privileged test itself; the manually executed
  host tests above succeeded for the paths they covered.
- The host-mounted `.git` directory is `tmpfs` read-only and busy. A separate
  explicit Git directory was created as a workaround; normal `git status` from
  the project root still cannot discover it.
- The manual Wayland check verified `wp_presentation` version 2 with clock ID 1
  (`CLOCK_MONOTONIC`).
- The sandbox cannot run the project-owned scheduler because privileged BPF
  loading is unavailable. Further manual tests are required for the open items.

## Technical debt

- No build system, source attribution manifest, test harness, or raw-data
  schema exists yet.
- Exact hardware frequency policy and experiment isolation have not been
  specified.

## Next concrete step

Stop for human review of the Phase 5 evidence. Do not implement dependency
propagation or CPU-frequency control without approval.

## Risks to project goal

The remaining Phase 1 risk is that ordinary-task isolation during partial mode
was inferred from the accepted `-p` flag and selected-task policy, not from a
complete task-policy census. Causal input-to-frame attribution remains a later
benchmark risk.

