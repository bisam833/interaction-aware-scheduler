# Matrix-02 audit artifacts

Source data: `../prophet-host-matrix-02/`; unchanged by this audit.
Human interpretation: `../../docs/prophet-host-matrix-02.md`.

- `analysis.json`: independent integrity checks, individual blocks, pooled
  descriptive summaries, matched comparisons, and controller window reconstruction.
- `raw-sha256.json`: hashes of every raw-run file.
- `validation.json`: executed checks and rollback archive hash.
- `python-tests.log`: 18 passing tests, including pairing and reconstruction.
- `replay.c`, `replay-block-{0,1,2,3}.txt`, `replay-results.json`: 68 decision
  windows replayed through the actual `src/scx_prophet/control.h`, without BPF.
- `verified-implementation-snapshot.tar.gz`, `snapshot-sha256.json`: measured
  workspace executables plus current relevant source/generated headers and analysis
  code. External comparator binaries remain identified by the raw manifest hashes;
  system libraries and the full scx dependency checkout are not bundled.

Reproduce from the repository root into a new output directory:

```sh
python3 scripts/analyze_prophet_matrix.py results/prophet-host-matrix-02 \
  --output results/prophet-matrix02-analysis-recheck
cc -std=gnu11 -Wall -Wextra -Werror -fsanitize=address,undefined -I. \
  results/prophet-matrix02-analysis-20261020/replay.c -o /tmp/prophet-policy-replay
env ASAN_OPTIONS=detect_leaks=0 /tmp/prophet-policy-replay \
  < results/prophet-matrix02-analysis-20261020/replay-block-0.txt
```

Repeat the last command for blocks 1–3. Each has 17 decisions. Replay inputs use
the logged start time, elapsed window, total/slow samples, and recorded output.
All polls after initial baselining were valid and unfrozen; no missing samples or
later invalid-window resets were elided. This reproduces the policy's response,
not the kernel execution or causal benefit of its actions.
