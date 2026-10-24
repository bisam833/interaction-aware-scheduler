#!/usr/bin/env fish
# Manual-input comparison; both modes run four equal CPU-work stages.
# Use audit_latency.py for rotating blocks, LAVD, throughput and lifecycle gates.
# v2 filenames deliberately keep these runs separate from unequal-work history.

set root (realpath (dirname (status filename))/..)
set bench "$root/benchmarks/wayland_interaction/build/pulsesched-wayland-bench"
set raw "$root/results/raw"
set report "$root/results/phase6-linux-vs-pulsesched.html"
set trials 20
if test (count $argv) -ge 1; and test "$argv[1]" = report
    set report_inputs $argv[2..-1]
    if test (count $report_inputs) -eq 0
        set report_inputs $raw/phase6-v2-compare-*.jsonl
    end
    if test (count $report_inputs) -eq 0; or not test -e $report_inputs[1]
        echo "no comparison JSONL files found" >&2
        exit 3
    end
    python3 "$root/scripts/visualize_linux_vs_pulsesched.py" $report_inputs > $report
    echo "visual_report=$report"
    exit $status
end
if test (count $argv) -ge 1
    set trials $argv[1]
end
if not string match -qr '^[1-9][0-9]*$' -- $trials
    echo "usage: run_linux_vs_pulsesched.fish [trials]" >&2
    exit 2
end
if not test -x $bench
    echo "benchmark not built: $bench" >&2
    exit 3
end
if test -z "$WAYLAND_DISPLAY"; or test -z "$XDG_RUNTIME_DIR"
    echo "WAYLAND_DISPLAY and XDG_RUNTIME_DIR must be set" >&2
    exit 3
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

for condition in idle half heavy
    set workers 0
    if test $condition = half
        set workers (math (nproc) / 2)
    else if test $condition = heavy
        set workers (math (nproc) - 1)
    end
    for i in (seq 1 $workers)
        yes > /dev/null &
        set bg_pids $bg_pids $last_pid
    end

    for implementation in linux pulsesched
        set run_id (date +%Y%m%d-%H%M%S)
        set output "$raw/phase6-v2-compare-$condition-$implementation-$trials-$run_id.jsonl"
        echo "START condition=$condition implementation=$implementation workers=$workers"
        if test "$implementation" = linux
            $bench 20 $trials 1000 0 dep-normal > $output
            or exit $status
        else
            printf 'PRE state='; cat /sys/kernel/sched_ext/state
            printf 'PRE nr_rejected='; cat /sys/kernel/sched_ext/nr_rejected
            sudo env PULSESCHED_MAX_PROPAGATION_DEPTH=2 \
                WAYLAND_DISPLAY=$WAYLAND_DISPLAY XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR \
                $bench 20 $trials 1000 250 dep-on > $output
            set rc $status
            printf 'POST state='; cat /sys/kernel/sched_ext/state
            printf 'POST nr_rejected='; cat /sys/kernel/sched_ext/nr_rejected
            if test $rc -ne 0
                exit $rc
            end
        end
        echo "raw_output=$output"
    end

    for pid in $bg_pids
        kill $pid 2>/dev/null
    end
    for pid in $bg_pids
        wait $pid 2>/dev/null
    end
    set bg_pids
end

set report "$root/results/phase6-linux-vs-pulsesched.html"
python3 "$root/scripts/visualize_linux_vs_pulsesched.py" \
    $raw/phase6-v2-compare-*-linux-$trials-*.jsonl \
    $raw/phase6-v2-compare-*-pulsesched-$trials-*.jsonl > $report
echo "visual_report=$report"
