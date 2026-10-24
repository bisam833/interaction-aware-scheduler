# Research question and experiment scope

## Central question

Can a Linux scheduler use explicit user-interaction events to begin a temporary
latency-critical scheduling epoch and actual display-presentation feedback to
terminate that epoch, reducing unnecessary boosted CPU execution compared with
a fixed-duration TTL policy while preserving or improving interactive latency
under CPU contention?

## Planned configurations

1. Linux/default scheduling.
2. PulseSched with configurable fixed TTL.
3. PulseSched with presentation-feedback termination and a maximum safety TTL.

The TTL is a comparison baseline and fail-safe, not a hidden completion
mechanism for the closed-loop condition. Every epoch must record whether it
ended by `PRESENTATION_FEEDBACK`, `TIMEOUT`, `ERROR`, `CANCELLED`, or another
explicit reason.

## Controlled causal loop

The benchmark must record monotonic timestamps for explicit input, application
CPU work, surface/frame update, and presentation feedback. Generic vblank is not
accepted as proof that the interaction's frame was presented. If attribution
cannot be established, the result will be reported as incomplete rather than
silently relabeled.

## Metrics and conditions

Idle/light, moderate, and heavy CPU contention are required. Planned metrics
include interaction-to-presentation latency, percentiles, boosted CPU time,
feedback/timeout rates, background throughput, context switches, migrations,
and scheduler dispatch/wakeup latency where measurable. Raw output and
processed results must remain separate.

