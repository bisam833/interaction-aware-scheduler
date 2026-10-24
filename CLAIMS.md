# Claims ledger

## Claim: The host kernel contains sched_ext support.

- Evidence: `CONFIG_SCHED_CLASS_EXT=y` and `/sys/kernel/sched_ext` exists.
- Confidence: high for kernel support; operational usability is unverified.
- Known limitations: current process lacks capabilities needed for a full BPF
  probe or scheduler load.

## Claim: PulseSched implements interaction-triggered closed-loop scheduling.

- Evidence: Phase 5 raw correctness and matched runs show a userspace
  presentation callback verifying the interaction/epoch ID and setting the
  matching BPF task state inactive with reason `PRESENTATION_FEEDBACK`.
- Confidence: high for this controlled benchmark path.
- Known limitations: this is a partial sched_ext prototype for one opted-in
  benchmark process; it is not a general desktop attribution mechanism.

## Claim: Presentation feedback can be causally attributed to the benchmark's
## interaction frame.

- Evidence: the benchmark binds `wp_presentation` v2, observes the advertised
  `CLOCK_MONOTONIC` clock, and records independently matched interaction IDs,
  commits, and presentation callbacks in the Phase 3 and Phase 5 raw JSONL.
- Confidence: high for the controlled surface and compositor session tested.
- Known limitations: this does not prove physical glass-to-glass latency or
  attribution for arbitrary applications.

## Claim: Closed-loop termination reduces boosted CPU execution without harming
## interaction latency versus fixed TTL.

- Evidence: UNVERIFIED as a beneficial result. Closed-loop termination was
  observed and reduced epoch duration in many trials, but the matched heavy
  condition had higher latency than both normal Linux and fixed TTL.
- Confidence: high that the stated broad benefit is not supported by this
  dataset; the experiment remains small and platform-specific.
- Known limitations: no universal superiority claim is made, and timeout
  fallbacks remain visible in the raw records.

## Claim: An official minimal C sched_ext scheduler builds and runs on this host.

- Evidence: unchanged `scx_simple` from C-example commit
  `370b1cea95c6bb668d5b9261f069d433142b9986` built with exit code 0, attached
  as `simple`, and returned to `disabled` after normal termination.
- Confidence: high for this tested environment.
- Known limitations: the checkout bundles a v6.18-rc4 BTF header while the host
  kernel is 7.2.9-2-cachyos.

## Claim: Partial sched_ext accepts one explicitly opted-in SCHED_EXT process.

- Evidence: unchanged `scx_qmap -p` attached as `qmap`; exactly one `yes`
  process reported `SCHED_EXT`/policy 7; cleanup returned state to disabled with
  `nr_rejected=0`.
- Confidence: high for registration and selected-task opt-in.
- Known limitations: a complete census of ordinary task policies was not
  collected, so stronger isolation claims remain unverified.

