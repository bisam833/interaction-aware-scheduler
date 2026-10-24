# Phase 6: bounded wakeup-based urgency propagation

Status: implementation complete; runtime acceptance evidence pending in the
interactive GNOME/root session. The Codex execution namespace could not connect
to the compositor or load sched_ext as root, so no Phase 4/5 raw file was
changed and no Phase 6 result is represented as passed here.

## Implementation

`src/pulsesched/pulsesched.bpf.c` preserves `SCX_OPS_SWITCH_PARTIAL` and the
Phase 5 presentation-feedback and TTL termination reasons. It performs
propagation from `pulse_select_cpu(struct task_struct *p, s32 prev_cpu,
u64 wake_flags)`, because sched_ext documents `ops.select_cpu()` as the first
callback on the wakeup path. The callback uses
`bpf_get_current_task_btf()` as the candidate waker and `p` as the wakee, then
copies the active parent's epoch ID, start time, and deadline into the wakee's
`task_state` record. A child gets `inherited=1`, `direct=0`,
`depth=parent.depth+1`, and `parent_pid=waker->pid`. Expired, inactive, absent,
self, and over-depth parents are rejected. An already active same-epoch child
is not overwritten.

The default maximum depth is 2 and can be changed for the benchmark with
`PULSESCHED_MAX_PROPAGATION_DEPTH` (0..32). The root task is depth 0 and is
marked direct. The benchmark's `dep-off` mode uses the same four-task workload
with propagation disabled; `dep-on` enables the BPF propagation flag.

The BPF metrics are:

| Index | Meaning |
|---:|---|
| 6 | propagation attempts |
| 7 | successful inherited boosts |
| 8 | depth rejects |
| 9 | inactive/expired rejects |
| 10 | inherited urgent enqueues |
| 11 | inherited urgent dispatches |

`pulse_enqueue`, `pulse_running`, and `pulse_stopping` retain the existing
urgent-path accounting. Userspace `pulse_invalidate_epoch()` walks the task
state map on either presentation-feedback termination or TTL timeout, clears
every active member of that epoch, and writes the same termination reason and
end timestamp. Thus inherited state cannot survive epoch closure or be
resurrected by a later wakeup.

## Controlled workload

`benchmarks/wayland_interaction/benchmark.c` adds four separately scheduled
pthread workers. Each worker has its own `eventfd` wake and completion fd and is
set to `SCHED_EXT`. The fixed chain is:

```text
main -> worker 0 -> worker 1 -> worker 2 -> worker 3 -> main render/commit
```

Each worker performs the same configured CPU work before waking its child and
waiting for the child completion. The benchmark emits the root/worker TIDs and
the intended wake edges, plus `epoch_member` records containing inherited,
depth, parent PID, active state, termination reason, and boosted runtime.

## Build evidence

The following completed successfully:

```text
make -C src/pulsesched clean
make -C src/pulsesched -j2
make -C benchmarks/wayland_interaction -j2
fish -n scripts/run_wayland_condition.fish
git diff --check
```

The build emitted only the existing libbpf/scx portability and generated
skeleton warnings; there were no compile or syntax errors.

## Runtime gate

Not executed from this namespace. The attempted command was:

```text
sudo env WAYLAND_DISPLAY="$WAYLAND_DISPLAY" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
  ./benchmarks/wayland_interaction/build/pulsesched-wayland-bench \
  20 1 1000 100 dep-on
```

It could not run because sudo reported `no new privileges` and the non-root
attempt could not connect to the compositor. Consequently the required claims
about depth-1/depth-2 inheritance, inherited urgent dispatch, feedback/TTL
invalidation, `nr_rejected=0`, and the idle/half/heavy matched matrix remain
unverified. No Phase 6 raw JSONL was created.

The earlier `ops.runnable()` placement was removed. `runnable()` is a generic
ready-to-run lifecycle callback and is not guaranteed to identify the waker.
`select_cpu()` is safer for this workload because it is the documented first
wakeup callback, but its API still has no explicit waker parameter and is also
used for fork/exec paths. Therefore `bpf_get_current_task_btf()` remains only a
candidate wake-context attribution; this is bounded wakeup-based urgency
propagation, not perfect causal dependency tracking.

The benchmark now rejects a worker unless `sched_getscheduler(0)` returns
policy 7 (`SCHED_EXT`) after `sched_setscheduler()`. The `dependency_chain`
JSON record reports the root and all four worker TIDs and their observed
policies.

## Correctness commands

Run these from the logged-in GNOME session. Preserve each JSONL output under a
new `results/raw/phase6-*` name. The benchmark emits `dependency_chain`,
`epoch_member`, `scheduler_epoch`, and `propagation_metrics` records.

Depth 0 -> 1 -> 2 and depth-3 rejection:

```fish
cd ~/osproject
mkdir -p results/raw
set f results/raw/phase6-depth-(date +%Y%m%d-%H%M%S).jsonl
sudo env PULSESCHED_MAX_PROPAGATION_DEPTH=2 \
  WAYLAND_DISPLAY=$WAYLAND_DISPLAY XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR \
  ./benchmarks/wayland_interaction/build/pulsesched-wayland-bench \
  5 1 1000 100 dep-on | tee $f
```

Expected evidence: root depth 0, workers at depths 1 and 2, at least one
`depth_rejected` count for the next edge, inherited urgent enqueue/dispatch
counts, and all observed policies equal 7.

Presentation termination clearing inherited state:

```fish
set f results/raw/phase6-feedback-(date +%Y%m%d-%H%M%S).jsonl
sudo env WAYLAND_DISPLAY=$WAYLAND_DISPLAY XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR \
  ./benchmarks/wayland_interaction/build/pulsesched-wayland-bench \
  20 1 1000 100 dep-on | tee $f
```

Expected evidence: `termination_reason_name` is
`PRESENTATION_FEEDBACK`; every `epoch_member` for that interaction has
`active:0` and reason 1.

TTL fallback clearing inherited state:

```fish
set f results/raw/phase6-ttl-(date +%Y%m%d-%H%M%S).jsonl
sudo env WAYLAND_DISPLAY=$WAYLAND_DISPLAY XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR \
  ./benchmarks/wayland_interaction/build/pulsesched-wayland-bench \
  20 1 1 100 dep-on | tee $f
```

Expected evidence: reason `TIMEOUT` (2), all epoch members inactive, and any
late presentation feedback reported separately rather than treated as success.

For every run, record scheduler rejection state before and after:

```fish
printf 'PRE state='; cat /sys/kernel/sched_ext/state
printf 'PRE nr_rejected='; cat /sys/kernel/sched_ext/nr_rejected
# run exactly one command above
printf 'POST state='; cat /sys/kernel/sched_ext/state
printf 'POST nr_rejected='; cat /sys/kernel/sched_ext/nr_rejected
```

Acceptance requires clean post-run `disabled` state and unchanged
`nr_rejected=0`. Do not start the performance matrix until these records show
the required inheritance and cleanup evidence.

