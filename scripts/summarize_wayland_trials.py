#!/usr/bin/env python3
"""Summarize one or more raw benchmark JSONL files without dropping failures."""
import argparse
import json
import math
import statistics
from pathlib import Path


def percentile(values, probability):
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def load(path):
    records = []
    with Path(path).open() as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as error:
                records.append({"type": "raw-parse-error", "line": line_number,
                                "detail": str(error)})
    trials = {r["interaction_id"]: r for r in records
              if r.get("type") == "trial" and "interaction_id" in r}
    interactions = {r["interaction_id"]: r for r in records
                    if r.get("type") == "interaction" and "interaction_id" in r}
    epochs = {r["interaction_id"]: r for r in records
              if r.get("type") == "scheduler_epoch" and "interaction_id" in r}
    return records, trials, interactions, epochs


def summarize(path):
    records, trials, interactions, epochs = load(path)
    statuses = {status: sum(r.get("status") == status for r in trials.values())
                for status in ("presented", "discarded", "timeout")}
    protocol_errors = sum(r.get("type") in ("protocol-error", "late-feedback",
                                               "input-ignored", "raw-parse-error")
                          for r in records)
    commit_latency = []
    interaction_latency = []
    epoch_duration = []
    for interaction_id, trial in trials.items():
        if trial.get("status") == "presented":
            commit_latency.append(trial["commit_to_presentation_ns"])
            interaction_latency.append(trial["presentation_ns"] - trial["input_ns"])
        epoch = epochs.get(interaction_id)
        interaction = interactions.get(interaction_id)
        if epoch and interaction and epoch.get("end_ns") and interaction.get("input_ns"):
            epoch_duration.append(epoch["end_ns"] - interaction["input_ns"])
    return {
        "file": str(path),
        "trial_records": len(trials),
        "interaction_records": len(interactions),
        "interactions_without_final_trial": len(set(interactions) - set(trials)),
        "statuses": statuses,
        "protocol_or_input_errors": protocol_errors,
        "commit_to_presentation_ns": {
            "median": percentile(commit_latency, 0.50),
            "p95": percentile(commit_latency, 0.95),
            "n": len(commit_latency),
        },
        "interaction_to_presentation_ns": {
            "median": percentile(interaction_latency, 0.50),
            "p95": percentile(interaction_latency, 0.95),
            "n": len(interaction_latency),
        },
        "boosted_epoch_duration_ns": {
            "median": percentile(epoch_duration, 0.50),
            "p95": percentile(epoch_duration, 0.95),
            "n": len(epoch_duration),
        },
        "epoch_termination_reasons": sorted(
            {r.get("termination_reason") for r in epochs.values()}
        ),
        "raw_record_types": dict(sorted(
            (record_type, sum(r.get("type") == record_type for r in records))
            for record_type in {r.get("type") for r in records}
        )),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.files:
        print(json.dumps(summarize(path), sort_keys=True))


if __name__ == "__main__":
    main()
