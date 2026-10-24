#!/usr/bin/env python3
"""Summarize Phase 6 raw JSONL without discarding failed interactions."""
import json
import math
import sys
from pathlib import Path


def pct(values, p):
    values = sorted(values)
    if not values:
        return None
    pos = (len(values) - 1) * p
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return values[lo]
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def summarize(path):
    records = [json.loads(line) for line in Path(path).read_text().splitlines()]
    trials = {r["interaction_id"]: r for r in records if r.get("type") == "trial"}
    epochs = {r["interaction_id"]: r for r in records
              if r.get("type") == "scheduler_epoch"}
    members = {}
    for record in records:
        if record.get("type") == "epoch_member":
            members.setdefault(record["interaction_id"], []).append(record)
    propagation = [r for r in records if r.get("type") == "propagation_metrics"]
    latest = propagation[-1] if propagation else {}
    presented = [r for r in trials.values() if r.get("status") == "presented"]
    commit = [r["commit_to_presentation_ns"] for r in presented]
    interaction = [r["presentation_ns"] - r["input_ns"] for r in presented]
    root_runtime = [r["boosted_runtime_ns"] for r in epochs.values()]
    inherited_runtime = [sum(m.get("boosted_runtime_ns", 0) for m in members.get(i, [])
                             if m.get("inherited")) for i in epochs]
    depths = [m.get("depth", 0) for values in members.values() for m in values]
    errors = sum(r.get("type") in {"protocol-error", "late-feedback",
                                    "input-ignored", "raw-parse-error"}
                 for r in records)
    return {
        "file": str(path),
        "scheduler_mode": next((r.get("mode") for r in records
                                 if r.get("type") == "scheduler"), "normal"),
        "trials": len(trials),
        "interactions": sum(r.get("type") == "interaction" for r in records),
        "failed_or_ignored_records": errors,
        "statuses": {s: sum(r.get("status") == s for r in trials.values())
                     for s in ("presented", "discarded", "timeout")},
        "termination_reasons": {str(reason): sum(
            r.get("termination_reason") == reason for r in epochs.values())
            for reason in (1, 2)},
        "interaction_to_presentation_ns": {"median": pct(interaction, .5),
                                             "p95": pct(interaction, .95)},
        "commit_to_presentation_ns": {"median": pct(commit, .5),
                                       "p95": pct(commit, .95)},
        "root_boosted_runtime_ns": {"median": pct(root_runtime, .5),
                                     "p95": pct(root_runtime, .95)},
        "inherited_boosted_runtime_ns": {"median": pct(inherited_runtime, .5),
                                          "p95": pct(inherited_runtime, .95)},
        "propagation_totals": {k: latest.get(k, 0) for k in (
            "attempts", "successful_inherited_boosts", "depth_rejected",
            "inactive_rejected", "inherited_urgent_enqueues",
            "inherited_urgent_dispatches")},
        "maximum_depth_observed": max(depths, default=0),
    }


for name in sys.argv[1:]:
    print(json.dumps(summarize(name), sort_keys=True))
