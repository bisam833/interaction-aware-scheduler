# Architecture decisions

## D0001 — Audit before scheduler implementation

- Decision: begin with an environment audit and documentation only.
- Alternatives: immediately copy or write a full sched_ext scheduler.
- Evidence/reason: the workspace has no source, is not a valid Git repository,
  and sched_ext loading privileges are not available to this process.
- Consequences: implementation is delayed until the baseline load/recovery path
  is known; this avoids making an untestable kernel-dependent design.

## D0002 — Controlled Wayland client for initial attribution

- Decision: use a controlled benchmark surface as the first presentation
  feedback target; do not claim arbitrary application attribution.
- Alternatives: generic DRM vblank, compositor-wide frame events, or input
  monitoring without a causal frame identifier.
- Evidence/reason: the project requires an interaction-specific causal loop;
  generic vblank does not establish that relationship. The Wayland
  presentation-time protocol is installed, but support is not yet tested.
- Consequences: the benchmark must explicitly correlate input, work, surface
  update, and callback; generality is limited but the measurement claim is
  auditable.

## D0003 — Partial sched_ext is the development safety model

- Decision: investigate and prefer `SCX_OPS_SWITCH_PARTIAL` before any custom
  policy. Normal desktop tasks should remain on the fair scheduler during
  development if the kernel test confirms the documented behavior.
- Alternatives: switch the whole desktop to sched_ext, or use only a packaged
  scheduler without source inspection.
- Evidence/reason: the installed kernel's BTF/header data contains the flag and
  `SCHED_EXT=7`; the full behavior still requires a source baseline and a
  privileged test.
- Consequences: the Phase 1 test must include an explicitly opted-in task and
  must verify that ordinary tasks remain outside sched_ext.

## D0004 — No privileged execution from this session

- Decision: do not bypass `no-new-privs`, grant persistent capabilities, or run
  the shell as root.
- Evidence/reason: `sudo -n` fails before execution and the environment lacks
  required BPF/admin capabilities.
- Consequences: Phase 1A is blocked pending a narrowly scoped administrator
  execution path.

