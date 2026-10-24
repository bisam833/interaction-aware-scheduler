#!/usr/bin/env python3
"""Create a dependency-free HTML comparison report from Phase 6 JSONL."""
import html
import json
import math
import re
import sys
from pathlib import Path


def pct(values, p):
    values = sorted(values)
    if not values:
        return None
    x = (len(values) - 1) * p
    lo, hi = math.floor(x), math.ceil(x)
    return values[lo] + (values[hi] - values[lo]) * (x - lo)


def ms(value):
    return 0 if value is None else value / 1_000_000


def read(path):
    records = [json.loads(line) for line in Path(path).read_text().splitlines()]
    trials = [r for r in records if r.get("type") == "trial"]
    presented = [r for r in trials if r.get("status") == "presented"]
    epochs = [r for r in records if r.get("type") == "scheduler_epoch"]
    metrics = [r for r in records if r.get("type") == "propagation_metrics"]
    latest = metrics[-1] if metrics else {}
    match = re.search(r"phase6-compare-(idle|half|heavy)-(linux|pulsesched)-", path.name)
    condition, implementation = match.groups() if match else ("unknown", "unknown")
    reasons = {1: 0, 2: 0}
    for epoch in epochs:
        if epoch.get("termination_reason") in reasons:
            reasons[epoch["termination_reason"]] += 1
    return {
        "condition": condition,
        "implementation": implementation,
        "trials": len(trials),
        "presented": len(presented),
        "ignored": sum(r.get("type") in {"input-ignored", "late-feedback", "protocol-error"} for r in records),
        "interaction_median": ms(pct([r["presentation_ns"] - r["input_ns"] for r in presented], .5)),
        "interaction_p95": ms(pct([r["presentation_ns"] - r["input_ns"] for r in presented], .95)),
        "commit_median": ms(pct([r["commit_to_presentation_ns"] for r in presented], .5)),
        "commit_p95": ms(pct([r["commit_to_presentation_ns"] for r in presented], .95)),
        "feedback": reasons[1],
        "timeout": reasons[2],
        "success": latest.get("successful_inherited_boosts", 0),
        "depth": max((m.get("depth", 0) for m in records if m.get("type") == "epoch_member"), default=0),
        "path": str(path),
    }


def cell(value):
    return html.escape(str(value))


def bar_chart(rows, metric, title):
    width, height = 760, 250
    max_value = max((r[metric] for r in rows), default=1) or 1
    parts = [f'<h3>{html.escape(title)}</h3><svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">']
    colors = {"linux": "#64748b", "pulsesched": "#2563eb"}
    for index, condition in enumerate(("idle", "half", "heavy")):
        group = [r for r in rows if r["condition"] == condition]
        x = 80 + index * 225
        parts.append(f'<text x="{x + 55}" y="238" text-anchor="middle">{condition}</text>')
        for j, implementation in enumerate(("linux", "pulsesched")):
            row = next((r for r in group if r["implementation"] == implementation), None)
            if not row:
                continue
            value = row[metric]
            bar_h = 175 * value / max_value
            bx = x + j * 62
            by = 215 - bar_h
            parts.append(f'<rect x="{bx}" y="{by:.1f}" width="45" height="{bar_h:.1f}" fill="{colors[implementation]}"/>')
            parts.append(f'<text x="{bx + 22}" y="{by - 5:.1f}" text-anchor="middle" font-size="11">{value:.1f}</text>')
            parts.append(f'<text x="{bx + 22}" y="230" text-anchor="middle" font-size="10">{implementation}</text>')
    parts.append("</svg>")
    return "".join(parts)


paths = [Path(arg) for arg in sys.argv[1:]]
if not paths:
    raise SystemExit("usage: visualize_linux_vs_pulsesched.py RAW.jsonl [...]")
rows = [read(path) for path in paths]
rows.sort(key=lambda r: (r["condition"], r["implementation"]))

table = ["<table><thead><tr><th>Condition</th><th>Implementation</th><th>Trials/presented</th><th>Interaction median/P95 ms</th><th>Commit median/P95 ms</th><th>Feedback</th><th>Timeout</th><th>Ignored</th><th>Depth</th><th>Inherited boosts</th></tr></thead><tbody>"]
for r in rows:
    table.append("<tr>" + "".join(f"<td>{cell(v)}</td>" for v in (
        r["condition"], r["implementation"], f'{r["trials"]}/{r["presented"]}',
        f'{r["interaction_median"]:.3f}/{r["interaction_p95"]:.3f}',
        f'{r["commit_median"]:.3f}/{r["commit_p95"]:.3f}', r["feedback"],
        r["timeout"], r["ignored"], r["depth"], r["success"])) + "</tr>")
table.append("</tbody></table>")

print("<!doctype html><html><head><meta charset='utf-8'><title>Linux vs PulseSched</title>")
print("<style>body{font:15px system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 18px;color:#172033}table{border-collapse:collapse;width:100%;margin:18px 0}th,td{border:1px solid #d7dce5;padding:8px;text-align:right}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}th{background:#eef2f7}svg{width:100%;height:auto;background:#fafbfc;border:1px solid #e1e5eb}h1{margin-bottom:4px}.note{background:#fff8e1;padding:12px;border-left:4px solid #d97706}</style></head><body>")
print("<h1>Normal Linux vs PulseSched</h1><p>Generated from preserved JSONL; values are milliseconds unless stated.</p>")
print("<div class='note'><b>Comparison boundary:</b> the current benchmark's normal mode is the Linux baseline, while the PulseSched run uses the existing dependency-chain mode. This report is observational; it does not claim an identical kernel callback path for the baseline.</div>")
print("<h2>Observation table</h2>" + "".join(table))
print(bar_chart(rows, "interaction_median", "Interaction → presentation median (ms)"))
print(bar_chart(rows, "interaction_p95", "Interaction → presentation P95 (ms)"))
print(bar_chart(rows, "commit_median", "Commit → presentation median (ms)"))
print("<h2>Raw files</h2><ul>" + "".join(f"<li>{html.escape(r['path'])}</li>" for r in rows) + "</ul>")
print("</body></html>")
