#!/usr/bin/env fish
# Run as the desktop user; Python handles bounded sudo and opens the browser.
set root (realpath (dirname (status filename))/..)
if not command -q python3
    echo 'Python 3 is required.' >&2
    exit 2
end
cd "$root"; or exit 2
python3 "$root/scripts/demo_review.py" $argv
