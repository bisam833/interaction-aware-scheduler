# Architecture (planned)

The planned pipeline is:

```text
controlled interaction -> epoch start -> PulseSched sched_ext policy
    -> latency-critical task(s) -> application work -> surface update
    -> presentation feedback -> epoch termination -> normal scheduling
```

The scheduler will have a normal path and a bounded temporary interaction path.
Per-task/epoch state is expected to include an epoch identifier, active flag,
start and expiry timestamps, boosted CPU budget, bounded wakeup propagation
depth, and termination reason. Exact structures remain an implementation
decision after the Phase 1 baseline.

Userspace/application-to-scheduler communication is a high-risk design point.
The interface must carry explicit interaction and feedback events with stable
identifiers and must not pretend to infer causal relationships from generic
system activity. A controlled benchmark application is the initial scope;
arbitrary desktop application support is future work.

Wakeup propagation and CPU-performance control are post-Phase-5 enhancements
and are not prerequisites for the core claim.

## Phase 2 implementation

The first project-owned scheduler lives under `src/pulsesched/`. It is partial:
only explicitly `SCHED_EXT` tasks are visible to it. An inactive or missing
per-PID record takes the normal DSQ; a valid active record takes the urgent DSQ
until its bounded deadline. BPF counters distinguish normal and urgent dispatch,
and the state map records epoch timestamps, timeout reason, and boosted runtime.

The boosted-runtime field is scheduler-observed runtime, not a perf-counter
measurement. `pulse_running()` opens an interval and `pulse_stopping()` closes
it. Because userspace can start an epoch while the selected task is already
running, `pulse_tick()` seeds an otherwise missing interval from the recorded
epoch start when that task is current. This boundary assumption must be
validated with longer host-side workloads.

