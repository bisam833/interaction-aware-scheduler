#!/usr/bin/env fish
# Master presentation command: run all six Linux-vs-PulseSched conditions,
# generate the HTML report, and open it in the desktop browser.

set root (realpath (dirname (status filename))/..)
set trials 20
set open_report 1

for arg in $argv
    if test "$arg" = --no-open
        set open_report 0
    else if string match -qr '^[1-9][0-9]*$' -- $arg
        set trials $arg
    else
        echo "usage: master_linux_vs_pulsesched.fish [TRIALS] [--no-open]" >&2
        exit 2
    end
end

fish "$root/scripts/run_linux_vs_pulsesched.fish" $trials
set rc $status
if test $rc -ne 0
    echo "comparison stopped before the complete report was generated" >&2
    echo "To graph completed JSONL files: fish scripts/run_linux_vs_pulsesched.fish report" >&2
    exit $rc
end

set report "$root/results/phase6-linux-vs-pulsesched.html"
if not test -s $report
    echo "report was not generated: $report" >&2
    exit 3
end

echo "comparison complete"
echo "report=$report"
if test $open_report -eq 1
    if type -q xdg-open
        xdg-open $report >/dev/null 2>&1 &
    else if type -q gio
        gio open $report >/dev/null 2>&1 &
    else
        echo "Open this file manually: $report" >&2
    end
end
