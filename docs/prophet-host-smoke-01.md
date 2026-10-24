# Prophet host acceptance and benchmark integration

The user ran `scripts/prophet_smoke.py` on the host on 2026-10-18. All nine
variants passed. The recorded scheduler and load-worker SHA256 hashes match the
current binaries and the Phase-1 build. Kernel: `7.2.10-1-cachyos`; 16 logical
CPUs, with both test workers pinned to CPU 15. Original raw evidence remains in
`results/prophet-host-smoke-01/`; an independent extraction is in
`results/prophet-integration-20261018/host-smoke-validation.json`.

## What the run establishes

- Actual BPF loading and attachment succeeded with telemetry on/off and
  propagation off/on, so the exposed constant-pruned branches passed the host
  verifier. Propagation's causal chain was not exercised by these workers.
- Static profile 1 produced 12,533 10ms insertions; profile 2 produced 10,922 5ms
  insertions. Startup fallback insertions were also recorded as expected.
- During the final measurement segment after the frozen controller's lease
  expired, 2,528 new insertions used fallback/profile 0 and none used profile 2.
  SIGSTOP produced 2,379 such fallback insertions and no profile-2 insertions.
  The scheduler remained attached during these tests.
- Each variant recorded urgent execution and both pinned workers made progress.
  The urgent worker's CPU share ranged from 50.49% to 51.02%. Worst worker batch
  gaps ranged from 24.01ms to 65.03ms. These are five-second smoke observations,
  not general fairness bounds or matched performance comparisons.
- Ordinary termination returned zero; the intentional SIGKILL returned -9.
  Every variant ended with sched_ext disabled and `nr_rejected=0` unchanged.

**Adaptive selection was not exercised:** all 14,358 adaptive-mode insertions
used profile 0. Requested profiles were only -1 (unsupported/initial evidence)
and 0. This run validates loading and the exercised accounting/liveness paths,
not adaptive benefit or predictive accuracy. It must not be described as a
successful dynamic-policy ablation. Hotplug, other affinity/nice combinations,
concurrent epochs and long stress runs remain untested.

## Next implementation, using the existing benchmark

The existing benchmark now has an explicitly compiled external backend. Its
four CPU-work stages, epoch start/closure callbacks, Wayland surface and
presentation measurement remain the same. The new `prophet-wayland-bench` output
shares that source; the original `pulsesched-wayland-bench` executable was not
rebuilt or overwritten. The scheduler binary/BPF object are also unchanged.

The runner owns the Prophet process and obtains its task/metric map descriptors
from the attachment record. It passes only those descriptors and a pidfd to the
benchmark. The benchmark validates map schemas and the pidfd, registers its
participants through the existing epoch ABI, and never loads/detaches Prophet.
It polls the pidfd to detect owner death. The runner separately monitors the
scheduler, retains its telemetry, and owns cleanup. This adapter supports full
switching with propagation off/depth 2; incompatible flags are rejected.

`scripts/prophet_matrix.py` calls the existing `audit_latency.run_one` lifecycle
through optional external-backend arguments. The original runner's default
behavior remains available. All comparison arms use the new common benchmark
binary, including Pulse-off's unchanged embedded BPF policy. Do not pool these
new measurements with historical data solely because labels match.

The matrix includes Linux, LAVD, **Rusty**, repaired Pulse-off, Prophet observe,
static 10ms, static 5ms, adaptive feedback, and static 20ms with telemetry off.
Rusty's existing tuner is a particularly relevant prior-art comparator; no new
predictor, learning framework or scheduler framework was introduced.

The default is four rotating blocks of 30 trials under heavy contention
(32 background workers here), after separate three-trial idle preflights for
every arm and the existing Pulse correctness preflight. There are 1,080 measured
trials plus 27 integration-preflight trials. The manifest freezes hashes,
versions, exact order, workload and exploratory screening margins before loading.
The runner requires both established schedulers instead of silently omitting one.
On failure it preserves the block and stops; it never replaces an active scheduler.

## Validation and limits of the new integration

Executed: new benchmark build; ASan/UBSan checks of the actual external backend
with only map/scheduling syscalls mocked; real pidfd death and descriptor-cleanup
checks; actual worker chains; original feedback callbacks compiled into a
separate temporary build; 12 Python regression tests. All 48 existing matrix
blocks also pass the new duplicate/stage/CPU-work integrity checks.

**The external-map/Wayland path has not yet run on the host.** Native mocks do
not establish that connection. The supplied matrix therefore performs a real
integration preflight before any heavy measurement. The sandbox still has no
BPF capabilities; the successful host logs do not grant privileges here.

The screen asks whether an adaptive candidate improves paired presentation P95
by at least 5% with at most 3% background-throughput loss and no progress gap
above `max(100ms, 2x paired observe)`. These are preregistered engineering screens
for further replication, not statistical significance or a real-time guarantee.
Compare adaptive to **both** observe and the best static profile; Rusty/LAVD remain
the established comparators. Four blocks are exploratory and rotation does not
fully balance nine positions. P99 has sparse support; no P99.9 claim is planned.

The controller still makes decisions from completed enqueue-delay samples. Its
profile trace, support failures, switch timing and total process CPU must be
reported. Static and adaptive use identical BPF telemetry but currently refresh
parameters at different rates (per poll versus supported decision window);
do not call their total userspace overhead exactly identical. Telemetry-off
still runs the loader/controller, isolating only the observer portion.

Use a fresh output directory from the logged-in Wayland host session:

```bash
sudo env WAYLAND_DISPLAY="$WAYLAND_DISPLAY" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
  python3 scripts/prophet_matrix.py --output results/prophet-host-matrix-01
```

For only the integration gate, add `--preflight-only` and choose another output
directory. After a completed matrix, the existing summarizer remains usable:

```bash
python3 scripts/summarize_latency_audit.py results/prophet-host-matrix-01 \
  > results/prophet-host-matrix-01-summary.jsonl
```

Transient workloads and predictive control remain subsequent gates. First
determine whether this established feedback mechanism beats its simpler static
alternatives without worsening real interaction latency or throughput.
