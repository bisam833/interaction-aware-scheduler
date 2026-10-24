# One-command live review demo

From the repository root in your **fish desktop terminal**, run:

```fish
./scripts/demo_review.fish
```

Run as your normal user; the command requests sudo internally. Keep the benchmark
window visible and do not interact with it during measurement. It refuses to
replace an already active sched_ext scheduler. Existing host-smoke provenance,
frozen executable hashes, and the Wayland connection are checked without rebuilding.

Five sequential arms: Linux, LAVD, Pulse propagation-off, Prophet static 5 ms,
and Prophet adaptive. Each requests 32 trials of four 20 ms thread-CPU stages,
a 200 ms completion-relative interval, twice the allowed CPU count in background
workers (32 on the verified 16-CPU host), and one second of background warmup.
All trials count; this is a short demonstration, not an independent research result.
Expected duration is about 60–90 seconds, depending on contention and presentation.
The measurement deadline is 105 seconds from launch, including sudo authentication
(which has a 20-second timeout); rendering/browser launch have the remaining budget.
Owned-process safety cleanup can exceed the 120-second cap if termination is slow.

The HTML opens as your desktop user **after cleanup**, with no server or internet.
Raw logs and reports are in `results/demo-review-<timestamp>-<pid>/`; the dashboard
is also copied to `results/demo-latest.html`. The terminal always prints its path.
If browser launching fails, open that path manually; diagnostics are in `browser.log`.
Failed and budget-omitted arms stay visible and have no latency bars. Ctrl+C requests
cleanup and a partial dashboard; repeated signals cannot interrupt owned-child cleanup.
No historical measurements are substituted. BPF/scheduler/controller sources and
frozen executables are unchanged.

Local validation: fish/Python syntax, nine focused checks including a real
owned-worker cleanup smoke with a failing client, and read-only recorded-data
parser verification. No live five-scheduler result is claimed from the restricted
namespace; host BPF privileges are required. Check artifacts:
`results/review-demo-tool-checks-20261021/`.
