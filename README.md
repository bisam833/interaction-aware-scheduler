# PulseSched

PulseSched is an Operating Systems research project evaluating a custom Linux
`sched_ext` policy. An explicit interaction starts a bounded latency-sensitive
epoch; controlled display-presentation feedback is intended to end that epoch.
The primary comparison is against a configurable fixed-TTL policy.

This repository is at the environment-audit stage. No PulseSched scheduler,
benchmark, or result data exists yet. See [STATUS.md](STATUS.md) and
[`docs/environment.md`](docs/environment.md) for the current evidence.

## Safety

The scheduler will be runtime-loaded and must have a tested detach/recovery
path. No scheduler is loaded by the current audit. Do not run a future loader
or benchmark as root unless the documented experiment procedure explicitly
requires it and a recovery path is available.

## Scope

The project is a real Linux scheduling experiment, not a simulator, a nice/
cgroup-only prototype, a generic dashboard, or an ML scheduler. Claims remain
unverified until supported by collected raw measurements.


Measured results and remaining regressions are in
[docs/host-matrix-v2-analysis.md](docs/host-matrix-v2-analysis.md): 1,440 host trials
completed, and both propagation modes passed the verifier. Heavy-load P95 was
286 ms for Linux, 139 ms for Pulse propagation-off, and 149 ms for propagation-on;
inheritance remains experimental because it worsened the presentation tail.
Repair history and validation commands are in
[docs/latency-repair-audit.md](docs/latency-repair-audit.md).
