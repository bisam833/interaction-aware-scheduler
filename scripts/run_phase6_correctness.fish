#!/usr/bin/env fish

if test (count $argv) -ne 1
    echo "usage: run_phase6_correctness.fish depth|feedback|ttl" >&2
    exit 2
end

set mode $argv[1]
set root (realpath (dirname (status filename))/..)
set bench "$root/benchmarks/wayland_interaction/build/pulsesched-wayland-bench"
set raw "$root/results/raw"
mkdir -p $raw

if not test -x $bench
    echo "benchmark not built: $bench" >&2
    exit 3
end
if test -z "$WAYLAND_DISPLAY"; or test -z "$XDG_RUNTIME_DIR"
    echo "WAYLAND_DISPLAY and XDG_RUNTIME_DIR must be set" >&2
    exit 3
end

switch $mode
case depth
    set label phase6-depth
    set args 5 1 1000 100 dep-on
case feedback
    set label phase6-feedback
    set args 20 1 1000 100 dep-on
case ttl
    set label phase6-ttl
    set args 20 1 1 100 dep-onxdxdg-open results/phase6-linux-vs-pulsesched.htmlg-open results/phase6-linux-vs-pulsesched.html
case '*'
    echo "mode must be depth, feedback, or ttl" >&2
    exit 2
end

set run_id (date +%Y%m%d-%H%M%S)
set output "$raw/$label-$run_id.jsonl"
printf 'PRE state='; cat /sys/kernel/sched_ext/state
printf 'PRE nr_rejected='; cat /sys/kernel/sched_ext/nr_rejected

sudo env PULSESCHED_MAX_PROPAGATION_DEPTH=2 \
    WAYLAND_DISPLAY=$WAYLAND_DISPLAY XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR \
    $bench $args | tee $output
set rc $pipestatus[1]

printf 'POST state='; cat /sys/kernel/sched_ext/state
printf 'POST nr_rejected='; cat /sys/kernel/sched_ext/nr_rejected
echo "raw_output=$output" >&2
exit $rc
