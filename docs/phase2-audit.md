# Phase 2 implementation and validation record

## Scope

Phase 2 contains only the interaction-epoch scheduling mechanism. It does not
contain presentation feedback, dependency propagation, CPU-frequency control,
or a GUI.

## Project-owned code

- `src/pulsesched/pulsesched.bpf.c`: BPF policy, partial ops flag, two DSQs,
  per-PID state, TTL expiration, runtime accounting, and metrics.
- `src/pulsesched/pulsesched.c`: minimal privileged scheduler loader/unloader.
- `src/pulsesched/pulsesched_ctl.c`: self-opt-in, registration, epoch trigger,
  busy CPU work, repeated epochs, and state/metric reporting.
- `src/pulsesched/Makefile`: project build.

The BPF and userspace structure is derived from the validated official C
`scx_simple` example. `external/scx-c-examples` was not modified.

## Scheduling decision logic

The scheduler registers `SCX_OPS_SWITCH_PARTIAL`, so only tasks whose Linux
policy is explicitly `SCHED_EXT` can enter this sched_ext instance.

- No state or inactive state: `NORMAL_DSQ`.
- Active state with a nonzero epoch and an unexpired bounded deadline:
  `URGENT_DSQ`.
- Missing state, zero deadline, excessive TTL, or expired deadline: normal path;
  an active expired record is marked inactive with timeout reason.
- `dispatch` drains the urgent DSQ before the normal DSQ.
- `pulse_stopping` accounts runtime even if `pulse_tick` already expired the
  epoch, avoiding lost boosted-runtime accounting.

The BPF-side maximum TTL is 10 seconds. Userspace rejects TTLs outside 1–10000
milliseconds. The scheduler has no permanent priority inheritance and no
wakeup propagation.

## Userspace communication

Userspace and BPF communicate through the generated skeleton's maps:

- `task_state`: hash keyed by PID, containing epoch ID, timestamps, deadline,
  active bit, termination reason, boosted runtime, and urgent counters.
- `metrics`: per-CPU array containing normal dispatch, urgent dispatch, timeout,
  stale, registration, and invalid counters.

The controller loads and attaches the partial scheduler, calls
`sched_setscheduler(..., SCHED_EXT, ...)` for itself, registers its PID, writes
an epoch record, performs busy CPU work, then reads back the state and metrics.

## Build result

Command:

```text
make clean
make
```

Final result: `PHASE2_BUILD_EXIT=0`.

The first build exposed one userspace API mismatch: this host's libc
`<sched.h>` does not define `SCHED_EXT`, although kernel UAPI and `chrt` support
policy 7. The project controller uses a guarded `#define SCHED_EXT 7` and
records the reason in source. This does not alter the kernel API.

The reused external compatibility headers emit signedness and unused-parameter
warnings. They are external warnings, not suppressed by changing the policy.

## Runtime validation recorded from the host

- `sudo ./build/pulsesched` attached successfully and printed
  `pulsesched attached partial=1`.
- While attached, `/sys/kernel/sched_ext/state` was `enabled` and
  `/sys/kernel/sched_ext/root/ops` was `pulsesched`.
- Ctrl-C caused `EXIT: unregistered from user space`; a subsequent check
  showed `state=disabled`.
- `sudo ./build/pulsesched_ctl 100 20 2` registered PID 44170 as
  `SCHED_EXT`, completed normal work, ran two epochs, and ended both with
  `active=0 reason=2` (`PULSE_REASON_TIMEOUT`). The reported boosted runtime
  was 192652 ns and 187302 ns.
- `metric[1]=14` proves the urgent dispatch branch executed. The zero
  `urgent_enqueues` count is not evidence that urgency was unused: the task
  remained runnable after the epoch began, so no urgent re-enqueue was
  required.
- The malformed argument test `./build/pulsesched_ctl 0 20` rejected the
  request before loading the scheduler.

Two items remain open. `metric[0]=0` means the current output does not
independently prove a normal DSQ dispatch, so normal-path instrumentation needs
to be made clearer. The stale-registration case has not yet been exercised.
The pasted post-run terminal output did not include `nr_rejected`, so that
counter is not claimed as verified for this Phase 2 run.

## Accounting investigation and validation revision

The original short-workload result (`boosted_runtime_ns` around 0.19 ms for a
20 ms busy loop) was not accepted as correct without analysis. The original
counter was accumulated only by `pulse_stopping()` from intervals opened by
`pulse_running()`. Since the controller writes the epoch map while it is
already running, an epoch can begin without another `running()` callback; the
busy interval was therefore absent from the counter until a later scheduling
boundary.

The revised `pulse_tick()` records the epoch start as the beginning of the
currently running task's interval when no interval is open, then
`pulse_stopping()` closes and accumulates it. This is an accounting correction,
not a claim of physical CPU-time measurement: it relies on the sched_ext tick
callback referring to the currently running task and can include a small
boundary interval around the userspace map update.

The normal idle selection path now inserts into `NORMAL_DSQ` instead of the
local DSQ, so successful normal dispatches are directly observable through
`metric[0]`. The controller also supports `--stale`, which writes an active
record whose deadline is already past and checks that BPF expiry deactivates it.

The revised source has built successfully. The longer workload, stale test,
normal/urgent counter test, and before/after `nr_rejected` capture still
require the host-side privileged rerun.

## Revised host validation

The host-side rerun completed the open checks:

- Before the long test: `state=disabled`, `nr_rejected=0`.
- `sudo ./build/pulsesched_ctl 200 100 1` registered PID 45159 as
  `SCHED_EXT` and ended the epoch with `reason=2` (`TIMEOUT`).
- The long workload reported `boosted_runtime_ns=100250095` ns for 100 ms of
  configured CPU work. This is consistent with scheduler-observed runtime and
  no longer resembles the earlier 0.19 ms undercount.
- Both scheduling paths were directly observed: `metric[0]=1` for
  `NORMAL_DSQ` dispatch and `metric[1]=9` for urgent dispatch.
- After the long test: `state=disabled`, `nr_rejected=0`.
- `sudo ./build/pulsesched_ctl --stale` installed an already-expired active
  record and observed `active=0 reason=2` with a populated end timestamp.
- Before and after the stale test, `nr_rejected=0`; the scheduler returned to
  `disabled`.

The stale behavior is validated by the state transition and timeout reason.
The separate `metric[3]` stale counter remains zero because this test exercises
deadline expiry rather than a missing-PID lookup event; it is not used as the
stale-test verdict.
