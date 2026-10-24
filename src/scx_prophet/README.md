# scx_prophet experiment

An isolated PulseSched-derived feedback baseline. **No prediction or MPC.**
Default `observe` runs baseline 20ms slices with telemetry. The first host smoke
gate passed all nine variants; adaptive mode stayed at profile 0. See the
[host results and next comparison](../../docs/prophet-host-smoke-01.md).

```bash
make -C src/scx_prophet -j2 all check
python3 -m unittest discover -s tests -p 'test_*.py'
```

On the privileged Linux host, with sched_ext disabled, rerun the gate into a **new**
directory. It will abort on the first failure and retain the logs:

```bash
sudo python3 scripts/prophet_smoke.py --output results/prophet-host-smoke-02
```

The gate uses the existing `benchmarks/wayland_interaction/build/pulse-load`.
It does not require Wayland. It briefly attaches a full-system scheduler and
tests pinned urgent/normal workers plus controller freeze/stop/death recovery.
Keep the host's standard sched_ext recovery route available. It never replaces
another active scheduler or signals processes it did not create.

After acceptance, standalone commands for controlled experiments:

```bash
sudo src/scx_prophet/build/scx_prophet --duration 10 > prophet-observe.jsonl
sudo src/scx_prophet/build/scx_prophet --mode static --profile 1 --duration 10 > prophet-static.jsonl
sudo src/scx_prophet/build/scx_prophet --mode adaptive --duration 10 > prophet-adaptive.jsonl
python3 scripts/summarize_prophet.py prophet-observe.jsonl prophet-static.jsonl prophet-adaptive.jsonl
```

Profiles: 0=20ms, 1=10ms, 2=5ms. `--propagation` is opt-in. `--poll-ms` accepts
20–200ms (default 100); `--no-telemetry` requires static mode. `--freeze-after-ms`
is fault injection, not a performance option. Output records raw counter deltas,
requested and observed profiles, controller work CPU and whole-process CPU.
Unfinished intervals and asynchronous reads limit those measurements.

Stop with SIGTERM/Ctrl-C; no maps or links are pinned. On a controller stall,
new enqueues use baseline parameters after the 1.5s lease expires. The original
PulseSched source, build, launchers and results remain untouched for rollback.

The new `prophet-wayland-bench` build adds an external-map backend to the existing
Wayland source. Build and native checks pass; actual Wayland integration is still
gated on the host. The original benchmark binary remains preserved.

```bash
make -C benchmarks/wayland_interaction -j2 build/prophet-wayland-bench prophet-check
sudo env WAYLAND_DISPLAY="$WAYLAND_DISPLAY" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
  python3 scripts/prophet_matrix.py --output results/prophet-host-matrix-01
```

This verifies the matching smoke result, runs all-arm integration preflights,
then compares nine arms using the same four-stage CPU-work benchmark. It includes
Rusty as the existing adaptive comparator. It stops on failure and retains data.
The precise mechanism, prior art, measurement limits and remaining gates are in
[the architecture decision](../../docs/prophet-architecture.md).
