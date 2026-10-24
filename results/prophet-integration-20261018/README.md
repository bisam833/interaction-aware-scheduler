# Host smoke audit and next benchmark preparation

The preserved user-run `results/prophet-host-smoke-01` passed all nine variants.
`host-smoke-validation.json` independently extracts profile use, pinned worker
shares, recovery counters, exit state and current binary-hash matches. Adaptive
mode used only profile 0; this is not adaptive-performance evidence.

The new external benchmark adapter is built and native-tested. `validation.json`
records its source/binary hashes and the exact evidence boundary. Tests:

- `build-check.log`: adapter build, ASan/UBSan backend checks with BPF syscalls
  mocked, real pidfd death/cleanup, and actual worker chains.
- `shared-benchmark-check.log`: modified common benchmark source compiled in
  `/tmp`, with original worker and feedback-callback tests passing.
- `python-tests.log`: 12 tests pass, including external runner cleanup and
  failed/incomplete/work-integrity rejection.
- All 48 preserved baseline blocks pass the new work-integrity checks.
- `preservation.json`: verified original binaries and host-accepted scheduler
  remain unchanged. Benchmark source/Makefile and runner gained optional hooks.

The pre-edit benchmark files are retained as `benchmark.c.before` and
`Makefile.before`. Original results were neither modified nor pooled. No
performance experiment ran in this namespace. `wayland-attempt.stderr` records
the new binary's failed connection attempt (exit 3).

`final-matrix-plan/manifest.json` is the current dry-run protocol and hashes.
`matrix-plan/` is an earlier plan-only snapshot, retained for provenance. Neither
directory contains measured trials. The real runner creates its own immutable
manifest in a fresh output directory, verifies the host smoke hashes, runs every
arm's integration preflight, and only then begins heavy contention.

The next host command and interpretation limits are in
[`docs/prophet-host-smoke-01.md`](../../docs/prophet-host-smoke-01.md).
