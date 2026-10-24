#!/usr/bin/env fish
# Matched Phase 6 matrix: closed-loop propagation off/on at three load levels.
# Each condition uses 20 trials, 20 ms work, 1000 ms feedback wait, and 100 ms TTL.

set root (realpath (dirname (status filename))/..)
set runner "$root/scripts/run_wayland_condition.fish"
set half (math (nproc) / 2)
set heavy (math (nproc) - 1)

if test -z "$WAYLAND_DISPLAY"; or test -z "$XDG_RUNTIME_DIR"
    echo "WAYLAND_DISPLAY and XDG_RUNTIME_DIR must be set" >&2
    exit 3
end

sudo -v; or exit 3

for condition in idle half heavy
    set workers 0
    if test $condition = half
        set workers $half
    else if test $condition = heavy
        set workers $heavy
    end
    for mode in dep-off dep-on
        fish $runner "phase6-$condition-$mode-20" $mode $workers 20
        or exit $status
    end
end

echo "Matrix raw files are under $root/results/raw" >&2
