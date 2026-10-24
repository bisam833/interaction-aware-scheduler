# Phase 1 preparation and blocked smoke-test record

## Pre-test state

Recorded before any scheduler load:

```text
/sys/kernel/sched_ext/state      disabled
/sys/kernel/sched_ext/enable_seq 0
/sys/kernel/sched_ext/nr_rejected 0
```

No `/sys/kernel/sched_ext/*/ops` file was present because no scheduler was
loaded.

## Candidate and compatibility

No smoke-test scheduler was selected or run. `scx_rusty` is explicitly excluded
as a PulseSched implementation base. A packaged scheduler could be used only as
a binary smoke test, but the preferred source baseline is kernel-matched
`scx_simple` or an officially compatible minimal C example. The installed
headers expose `SCX_OPS_SWITCH_PARTIAL`; the source needed to test it is not
installed.

## Recovery procedure prepared

For a future supervised test:

1. Capture the three state values above and any existing `*/ops` value.
2. Start exactly one selected scheduler in a dedicated process group.
3. Confirm `/sys/kernel/sched_ext/state` becomes `enabled` and read its `ops`.
4. Terminate it normally with `SIGINT` and wait for process exit.
5. Confirm state returns to `disabled`, `enable_seq` increments, and no error or
   stall is reported.
6. If normal termination fails, use the documented kernel sched_ext emergency
   SysRq disable (`SysRq-S`) or allow the kernel watchdog fallback; do not start
   another scheduler over the first one.

The emergency path must be confirmed with the administrator before execution.

## Why no test ran

The process has no BPF/admin capabilities, `no-new-privs` is set, and
`sudo -n -v` fails. This is an environment access blocker, not a scheduler
failure. No repeated random scheduler attempts were made.

## Partial mode

The kernel header/BTF data contains `SCX_OPS_SWITCH_PARTIAL` and UAPI
`SCHED_EXT=7`. The documented expected test is to set the scheduler ops flag,
launch a deliberately selected test process with `sched_setscheduler(...,
SCHED_EXT, ...)`, and verify its scheduling behavior while an ordinary control
process remains on the fair scheduler. This has not been executed.

