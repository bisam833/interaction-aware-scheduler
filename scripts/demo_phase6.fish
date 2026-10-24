#!/usr/bin/env fish
# Short presentation runner. This does not change scheduler code or run the
# full matrix; it runs one idle OFF/ON pair and preserves both JSONL outputs.

set root (realpath (dirname (status filename))/..)
set bench "$root/benchmarks/wayland_interaction/build/pulsesched-wayland-bench"
set raw "$root/results/raw"
set contention idle
set trials 1

if test (count $argv) -ge 1
    set contention $argv[1]
end
if test (count $argv) -ge 2
    set trials $argv[2]
end
if test "$contention" != idle; and test "$contention" != half; and test "$contention" != heavy
    echo "usage: demo_phase6.fish [idle|half|heavy] [trials]" >&2
    exit 2
end
if not string match -qr '^[1-9][0-9]*$' -- $trials
    echo "trials must be positive" >&2
    exit 2
end
if not test -x $bench
    echo "benchmark not built: $bench" >&2
    echo "run: make -C benchmarks/wayland_interaction -j2" >&2
    exit 3
end
if test -z "$WAYLAND_DISPLAY"; or test -z "$XDG_RUNTIME_DIR"
    echo "WAYLAND_DISPLAY and XDG_RUNTIME_DIR must be set" >&2
    exit 3
end

set workers 0
if test "$contention" = half
    set workers (math (nproc) / 2)
else if test "$contention" = heavy
    set workers (math (nproc) - 1)
end

mkdir -p $raw
sudo -v; or exit 3
set bg_pids
function cleanup --on-event fish_exit
    for pid in $bg_pids
        if kill -0 $pid 2>/dev/null
            kill $pid 2>/dev/null
        end
    end
    for pid in $bg_pids
        wait $pid 2>/dev/null
    end
end

for i in (seq 1 $workers)
    yes > /dev/null &
    set bg_pids $bg_pids $last_pid
end

echo "Phase 6 demonstration: closed-loop, TTL 250 ms, $contention contention, $trials trial(s)"
echo "The expected live evidence is: root + inherited depth-1/2 tasks, then epoch termination."

set outputs
for mode in dep-off dep-on
    set run_id (date +%Y%m%d-%H%M%S)
    set output "$raw/demo-phase6-$contention-$mode-$run_id.jsonl"
    set outputs $outputs $output
    echo
    echo "--- propagation $mode ---"
    printf 'PRE state='; cat /sys/kernel/sched_ext/state
    printf 'PRE nr_rejected='; cat /sys/kernel/sched_ext/nr_rejected
    sudo env PULSESCHED_MAX_PROPAGATION_DEPTH=2 \
        WAYLAND_DISPLAY=$WAYLAND_DISPLAY XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR \
        $bench 20 $trials 1000 250 $mode | tee $output
    set rc $pipestatus[1]
    printf 'POST state='; cat /sys/kernel/sched_ext/state
    printf 'POST nr_rejected='; cat /sys/kernel/sched_ext/nr_rejected
    echo "raw_output=$output"
    if test $rc -ne 0
        exit $rc
    end
end

echo
echo "Observation table:"
python3 "$root/scripts/make_phase6_observation_table.py" $outputs
