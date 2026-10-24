# Executed evidence and continuation state

Phase 0 inspected the current repaired working tree, benchmark, raw matrix,
local scx sources and primary documentation. Phase 1 produced an isolated
feedback prototype. **Phase 1 host acceptance is blocked; Phase 2 and Phase 3
have not run.** This directory contains no Prophet performance measurements.

`validation.json` records final source/binary hashes and check outcomes.

| Executed check | Outcome | Artifact |
|---|---|---|
| BPF compile, skeleton, userspace link | Pass | `final-build-check.log` |
| BTF object parsing | Pass; not verifier acceptance | `btf.txt` |
| Native ASan/UBSan policy/controller checks | Pass | `final-build-check.log` |
| Python regression tests | 8 pass | `python-tests.log` |
| CLI bounds/mode checks | 7 pass | `cli-checks.json` |
| Original policy/worker/feedback checks | Pass | `baseline-policy-tests.log`, `worker-tests.log`, `feedback-tests.log` |
| Preservation of original source and executable hashes | All unchanged | `baseline-preservation.json` |
| Actual BPF load attempt | EPERM at trivial libbpf probe, before Prophet verifier | `final-load-attempt.log` |
| Host gate invocation | Refused non-privileged environment | `host-gate-attempt/status.json` |
| Wayland connection attempt | Failed to connect; exit 3 | `wayland-attempt.stderr` |
| Existing heavy matrix recomputation | Reproduces reported tails | `baseline-recomputed.jsonl` |

The environment has `CapEff=0`, `NoNewPrivs=1`; sudo cannot acquire privileges.
After the denied load, sched_ext was still `disabled`, `nr_rejected=0`. That is
unchanged state, **not a tested detach or watchdog recovery**. Native fairness
checks are single-CPU policy models, not evidence of kernel/SMP behavior.
Upstream compatibility headers emitted warnings; the new project sources had
no build errors. ASan/UBSan ran with LeakSanitizer disabled, matching the existing
namespace restriction.

The original sources and binaries are archived in `baseline-before.tar.gz` with
`baseline-sha256.json`; pre-existing edits are recorded in `baseline-working.diff`
and `git-status-before.txt`. No original results were overwritten. No commits
or external scx modifications were made.

Continue on the privileged host from the workspace root:

```bash
make -C src/scx_prophet -j2 all check
sudo python3 scripts/prophet_smoke.py --output results/prophet-host-smoke-01
```

Use a new output directory. The gate retains failed artifacts and stops at the
first failure. Passing it is required before performance testing; it does not
establish global fairness or predictive benefit. It checks static profile use
and controller faults; adaptive profile changes depend on sufficient real
samples and need inspection of its trace. Do not substitute synthetic controller
unit-test inputs for runtime performance evidence.

The old Wayland benchmark owns an embedded Pulse loader, so an explicit
external-map backend is still needed for Prophet interaction experiments. Do
not launch that benchmark's Pulse mode alongside Prophet. The proposed matched
comparisons and prediction gate are in
[`docs/prophet-architecture.md`](../../docs/prophet-architecture.md).
