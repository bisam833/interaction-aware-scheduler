# Transition experiment development record

`validation.json` records completed local checks and the pending host gate.
No new scheduler performance comparison was executed here.

- `before/` and `before-sha256.json`: pre-edit rollback copies and hashes.
- `transition-implementation-snapshot.tar.gz` and `implementation-sha256.json`:
  tested implementation snapshot, including new isolated benchmark binaries.
- `protocol-final-plan/`: final seeded 96-block plan, about 85.3 workload minutes
  before startup and correctness gates. `protocol-plan/` is an earlier draft.
- `oscillation.json`: analysis of existing matrix-02 telemetry; observational
  policy dependence does not establish causal harm from oscillation.
- `native-load-smoke-v2/`: current generator's three Linux-only functional runs.
  `native-load-smoke/` preserves earlier development runs.
- `python-tests.log`: 27 passing tests.
- `transition-native-tests.log`, `baseline-native-tests.log`,
  `policy-check.log`: native and sanitizer checks.
- `local-wayland-attempt.*`: failed local display-access probe, exit 3.

Original benchmark and scheduler binaries remain byte-identical. All 1,517
files in the saved matrix-02 raw manifest retain their hashes.

Run the host experiment from the repository root in the active Wayland session:

```sh
sudo env WAYLAND_DISPLAY="$WAYLAND_DISPLAY" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
  python3 scripts/prophet_transitions.py --output results/prophet-transitions-01
python3 scripts/analyze_prophet_transitions.py results/prophet-transitions-01 \
  --output results/prophet-transitions-01-analysis
```

The runner requires a fresh output directory, reruns privileged correctness and
eight comparator preflights, stops on failure, and retains incomplete data.
See `docs/prophet-transitions.md` for measurement definitions and decision gates.
