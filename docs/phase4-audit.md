# Phase 4 fixed-TTL baseline integration

The Wayland benchmark has an optional fixed-TTL PulseSched mode. The default
benchmark remains normal Linux scheduling. Presentation feedback remains
measurement-only and cannot terminate an epoch.

Invocation:

```text
pulsesched-wayland-bench [work_ms] [trials] [feedback_timeout_ms] [pulse_ttl_ms]
```

A nonzero fourth argument loads the project-owned partial scheduler, sets the
benchmark process to `SCHED_EXT`, registers its PID, and starts one TTL epoch
per real interaction. Each epoch is written to the task-state map before CPU
work begins. The presentation callbacks do not write that map or cancel an
epoch. `scheduler_epoch` JSONL records expose the later TTL termination and
boosted-runtime accounting.

The integration builds successfully. Idle and contention baseline records are
now collected.

## Idle fixed-TTL host run

Five trials were run with 20 ms CPU work and a 100 ms TTL. All five visual
updates were presented with `wp_presentation` flags 7. Each scheduler epoch
ended later with `active=0` and `termination_reason=2` (`TIMEOUT`), not at the
presentation callback. Reported scheduler-observed boosted runtimes were:

```text
20,451,307 ns  20,356,777 ns  20,376,035 ns
20,561,978 ns  20,395,011 ns
```

Presentation occurred approximately 16--42 ms after commit, while the TTL
epoch ended approximately 100 ms after interaction. This is direct evidence
that presentation feedback remained measurement-only in the fixed-TTL mode.
The scheduler returned to `disabled` and `nr_rejected` remained 0 after the
run.

## Heavy-contention fixed-TTL host run

Five additional trials were run with `nproc - 1` continuously busy `yes`
workers. This is a deliberately heavy condition: it saturated nearly all
available CPUs and made the laptop temporarily lag until the workers were
terminated. It is retained as heavy-contention evidence, not described as a
moderate load.

All five updates were presented with flags 7, and all five scheduler epochs
ended by timeout. Commit-to-presentation times were:

```text
66,589,836 ns  62,563,756 ns  83,647,940 ns
68,122,785 ns  39,105,679 ns
```

Boosted-runtime values were:

```text
16,700,307 ns  16,414,222 ns  16,052,180 ns
16,544,621 ns  16,960,689 ns
```

The scheduler epoch remained independent of presentation feedback. In trial 3,
the epoch ended at 31197113278447 ns and presentation arrived at
31197114132000 ns, approximately 0.85 ms later; this is expected fixed-TTL
behavior and must not be interpreted as feedback-controlled termination.

The raw run used the normal Wayland environment forwarding under `sudo` and
cleaned up all background workers afterward.

## Paired normal-Linux contention run

Five trials were run without PulseSched under a half-CPU background load. All
five updates were presented with flags 7. Commit-to-presentation times were:

```text
26,387,935 ns  17,355,029 ns  15,358,685 ns
45,033,724 ns  42,817,163 ns
```

This provides a normal-scheduling contention reference for the fixed-TTL
contention records. These small samples are baseline evidence, not a
statistical claim of scheduler superiority; larger repetitions and additional
TTL values remain appropriate for the eventual experiment.

## Required matched matrix

The repository now includes `scripts/run_wayland_condition.fish`, which uses
the same 20 ms work, 1000 ms feedback timeout, trial count, background-worker
lifetime, and cleanup path for normal and TTL modes. It writes each run to a
new file under `results/raw/`. `scripts/summarize_wayland_trials.py` reports
medians, interpolated P95 values, full interaction-to-presentation and
commit-to-presentation latency, epoch durations, termination reasons, and all
failure statuses without filtering records.

The minimum 20-trial six-condition matrix is not yet collected. Closed-loop
termination remains out of scope until it is complete.

## 20-trial matrix status

The normal idle, TTL idle, normal half-CPU, TTL half-CPU, and normal heavy
conditions each produced 20 presented trial records. Two heavy-TTL attempts
did not complete: one produced 3 trials and two `input-ignored` records; the
other produced 2 trials and five `input-ignored` records. The raw files remain
under `results/raw/` and are not treated as 20-trial data. The next heavy-TTL
run must provide the next real input only after the preceding
`scheduler_epoch` record, so pending feedback and fixed-TTL observation are
not flooded with queued input.

A third heavy-TTL attempt also stopped incomplete after 5 trials, with one
`input-ignored` event and one interaction lacking a final trial record. It is
likewise preserved and excluded from the 20-trial dataset.

## Completed 20-trial matrix

The final selected files contain 20/20 trial records, 20/20 interaction
records, zero unmatched interactions, and zero protocol/input errors in every
condition. Every presentation result was `presented` with flags 7. Every TTL
epoch had termination reason 2 (`TIMEOUT`). The summary script uses linear
interpolation for P95. Epoch duration is measured from the interaction input
timestamp to the scheduler epoch end record; it is not presented as a direct
CPU-time measurement.

| condition | commit→presentation median / P95 | interaction→presentation median / P95 | epoch duration median / P95 |
|---|---:|---:|---:|
| normal idle | 41.111 / 50.887 ms | 61.204 / 70.962 ms | N/A |
| TTL idle | 13.255 / 16.979 ms | 33.366 / 37.068 ms | 105.849 / 108.679 ms |
| normal half-CPU | 15.761 / 43.200 ms | 35.918 / 63.300 ms | N/A |
| TTL half-CPU | 15.349 / 18.169 ms | 35.486 / 38.285 ms | 106.337 / 109.659 ms |
| normal heavy | 28.538 / 33.165 ms | 48.679 / 53.525 ms | N/A |
| TTL heavy | 47.651 / 60.665 ms | 67.794 / 80.910 ms | 109.792 / 193.529 ms |

Values are descriptive results from this 20-trial run, not a claim that TTL is
superior. The raw files remain the source of truth, including the three
incomplete heavy-TTL attempts excluded from this table.
