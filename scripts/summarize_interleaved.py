#!/usr/bin/env python3
"""Summarize one in-process TTL/closed alternating JSONL run by mode."""
import json
import math
import statistics
import sys


def percentile(values, probability):
    values = sorted(values)
    if not values:
        return None
    position = (len(values) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return values[lower]
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def ms(values):
    values = [value / 1e6 for value in values]
    return {"median": percentile(values, .5), "p95": percentile(values, .95),
            "n": len(values)}


if len(sys.argv) != 2:
    raise SystemExit("usage: summarize_interleaved.py FILE.jsonl")

groups = {"ttl": [], "closed": []}
for line in open(sys.argv[1]):
    record = json.loads(line)
    if record.get("type") == "trial" and record.get("scheduler_mode") in groups:
        groups[record["scheduler_mode"]].append(record)

for mode, trials in groups.items():
    presentation = [r for r in trials if r.get("status") == "presented"]
    print(json.dumps({
        "mode": mode,
        "trials": len(trials),
        "statuses": {s: sum(r.get("status") == s for r in trials)
                     for s in ("presented", "discarded", "timeout")},
        "termination_reasons": {
            name: sum(r.get("termination_reason_name") == name for r in trials)
            for name in ("PRESENTATION_FEEDBACK", "TIMEOUT")
        },
        "input_to_work_ms": ms([r["input_to_work_ns"] for r in trials]),
        "epoch_register_after_input_ms": ms([
            r["epoch_register_ns"] - r["input_ns"] for r in trials]),
        "work_to_commit_ms": ms([r["work_to_commit_ns"] for r in trials]),
        "commit_to_feedback_request_ms": ms([
            r["feedback_request_ns"] - r["commit_ns"] for r in trials]),
        "commit_to_presentation_ms": ms([
            r["commit_to_presentation_ns"] for r in presentation]),
        "interaction_to_presentation_ms": ms([
            r["presentation_ns"] - r["input_ns"] for r in presentation]),
        "normal_enqueues": sum(r.get("normal_enqueues", 0) for r in trials),
        "urgent_enqueues": sum(r.get("urgent_enqueues", 0) for r in trials),
    }, sort_keys=True))
