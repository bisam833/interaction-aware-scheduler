# Propagation verifier rejection — host follow-up, 2026-10-18

Completed host validation: see [host-matrix-v2-analysis.md](host-matrix-v2-analysis.md)
for the full 48-block result, verified enabled-mode loading, and remaining
propagation tail regression. The account below preserves the earlier audit state.

The initial host run passed the propagation-off smoke gate and completed three
30-trial idle blocks. It then failed to **load**, not to schedule, propagation-on.
The original results in `results/audit-host-matrix` remain untouched.

## Proven cause and repair

`idle-pulse-on-0.stderr` ends with `same insn cannot be used with different
pointers` in `pulse_select_cpu`. The source expression selected between
`parent->root_pid` (BPF map value) and `waker->pid` (task pointer). Clang selected
between their addresses and emitted a shared load at instruction 127. The
verifier encountered different pointer types at that instruction and rejected it.

The repair reads the task PID with `READ_ONCE`, then selects scalar values.
Rebuilt disassembly shows a distinct task load at instruction 116 and map load
at instruction 122, merging only the scalar PID before the store at 123. This
eliminates the observed mixed-pointer load without changing token semantics or
disabling propagation. Other compiler versions may assign different instruction
numbers; inspect the pointer types and separate loads, not those numbers.

The old smoke gate loaded only propagation-off. Constant propagation removed
this branch during verification, so that gate could not validate propagation-on.
The standalone loader now accepts `--propagation`, `--trace-delay`, and
`--partial`. The runner loads off/on plus selected ablation configurations before
starting any measurement and identifies the failing preflight log. A failure in
any configuration aborts the gate. Unit tests cover variant selection, abort and
cleanup on enabled-mode failure, and successful completion of all required steps.

## Available measurements

Client-dispatch to presentation, milliseconds. Equal four-stage CPU-work workload;
one idle block, n=30 each. These are preliminary matched observations, not a
contention result or evidence about the newly fixed propagation-on path.

| Mode | P50 | P95 | P99 |
|---|---:|---:|---:|
| Linux | 119.321 | 126.269 | 127.324 |
| LAVD | 120.538 | 125.340 | 127.156 |
| Pulse propagation-off | 119.921 | 125.109 | 125.362 |
| Pulse propagation-on | no trials: verifier rejection | — | — |

Summary including the failed block is saved in
`results/audit-verifier-fix-20261018/host-partial-summary.jsonl`.

## Validation and next execution

BPF compilation/skeleton generation, loader and benchmark rebuilds, actual
worker-chain tests, ASan/UBSan feedback lifecycle checks, four Python regression
tests, and whitespace checks passed. Disassembly and build logs are saved in
`results/audit-verifier-fix-20261018`. Enabled-mode loading in the sandbox still
fails at libbpf's trivial capability probe with EPERM, before verifier evaluation.
**Host verifier acceptance of this fix is pending.** Host acceptance of the prior
propagation-off binary and its attach/detach/TTL checks is established by the
preserved original logs; the on-mode failure left sched_ext disabled with no
rejection-counter increase (a verifier failure is not a rejected task).

The binaries have been rebuilt in the shared workspace. Use a fresh directory
so measurements from different executable hashes do not silently mix:

```bash
sudo env WAYLAND_DISPLAY="$WAYLAND_DISPLAY" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
  python3 scripts/audit_latency.py --trials 30 --blocks 4 \
  --output results/audit-host-matrix-v2
```

The strengthened preflight runs automatically first. To run only that check:

```bash
sudo python3 scripts/audit_latency.py --smoke --output results/audit-host-smoke-v2
```
