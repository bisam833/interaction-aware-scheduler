#!/usr/bin/env python3
"""Turn Phase 6 JSONL observations into a compact Markdown table."""
import json
import math
import re
import sys
from pathlib import Path


def percentile(values, p):
    values = sorted(values)
    if not values:
        return None
    x = (len(values) - 1) * p
    lo, hi = math.floor(x), math.ceil(x)
    return values[lo] + (values[hi] - values[lo]) * (x - lo)


def ms(value):
    return "-" if value is None else f"{value / 1_000_000:.3f}"


def summarize(path):
    records = [json.loads(line) for line in Path(path).read_text().splitlines()]
    trials = [r for r in records if r.get("type") == "trial"]
    presented = [r for r in trials if r.get("status") == "presented"]
    epochs = [r for r in records if r.get("type") == "scheduler_epoch"]
    members = [r for r in records if r.get("type") == "epoch_member"]
    metrics = [r for r in records if r.get("type") == "propagation_metrics"]
    latest = metrics[-1] if metrics else {}
    name = Path(path).name
    match = re.search(r"(?:phase6|demo-phase6)-(idle|half|heavy)-(dep-off|dep-on)-", name)
    condition = match.group(1) if match else "?"
    mode = match.group(2) if match else "?"
    term = {1: 0, 2: 0, 3: 0, 4: 0}
    for epoch in epochs:
        term[epoch.get("termination_reason", 0)] = term.get(epoch.get("termination_reason", 0), 0) + 1
    depths = [m.get("depth", 0) for m in members]
    inherited_by_interaction = {}
    for member in members:
        if member.get("inherited"):
            interaction_id = member.get("interaction_id")
            inherited_by_interaction[interaction_id] = inherited_by_interaction.get(interaction_id, 0) + member.get("boosted_runtime_ns", 0)
    inherited_runtime = list(inherited_by_interaction.values())
    ignored = sum(r.get("type") in {"input-ignored", "late-feedback", "protocol-error", "raw-parse-error"} for r in records)
    return {
        "condition": condition,
        "mode": mode,
        "trials": len(trials),
        "presented": len(presented),
        "ignored": ignored,
        "interaction": percentile([r["presentation_ns"] - r["input_ns"] for r in presented], .5),
        "interaction_p95": percentile([r["presentation_ns"] - r["input_ns"] for r in presented], .95),
        "commit": percentile([r["commit_to_presentation_ns"] for r in presented], .5),
        "commit_p95": percentile([r["commit_to_presentation_ns"] for r in presented], .95),
        "feedback": term[1],
        "timeout": term[2],
        "depth": max(depths, default=0),
        "success": latest.get("successful_inherited_boosts", 0),
        "inherited": percentile(inherited_runtime, .5),
        "rejected": latest.get("depth_rejected", 0),
    }


paths = [Path(arg) for arg in sys.argv[1:]]
if not paths:
    print("usage: make_phase6_observation_table.py RAW.jsonl [...]", file=sys.stderr)
    raise SystemExit(2)

rows = [summarize(path) for path in paths]
print("| condition | propagation | trials/presented | interaction→presentation median/P95 (ms) | commit→presentation median/P95 (ms) | feedback | timeout | ignored | max depth | inherited boosts | inherited runtime median (ms) | depth rejects |")
print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
for row in rows:
    print(f"| {row['condition']} | {row['mode']} | {row['trials']}/{row['presented']} | {ms(row['interaction'])}/{ms(row['interaction_p95'])} | {ms(row['commit'])}/{ms(row['commit_p95'])} | {row['feedback']} | {row['timeout']} | {row['ignored']} | {row['depth']} | {row['success']} | {ms(row['inherited'])} | {row['rejected']} |")
