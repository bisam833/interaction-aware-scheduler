#!/usr/bin/env python3
"""Compare recorded sensors across static interventions; no counterfactual replay."""
import argparse
import json
from pathlib import Path
from summarize_latency_audit import read_records
from analyze_prophet_matrix import controller_windows


def analyze(directory):
    rows = []
    for path in sorted(directory.glob('heavy-prophet-*-controller.jsonl')):
        records = read_records(path)
        samples = [r for r in records if r['type'] == 'sample']
        first = samples[0]['now_ns']
        # Discard the same first two elapsed seconds, not outcome-selected samples.
        retained = [r for r in samples if r['valid'] and r['now_ns']-first >= 2_000_000_000]
        elapsed = sum(r['elapsed_ns'] for r in retained)
        counters = [sum(r['counters'][i] for r in retained) for i in range(73)]
        windows, transitions = controller_windows(records)
        occupancy = {str(p): 0 for p in (-1, 0, 1, 2)}
        for a, b in zip(samples, samples[1:]):
            occupancy[str(a['requested'])] += b['now_ns']-a['now_ns']
        span = samples[-1]['now_ns']-first
        n = sum(counters[:32])
        wake = sum(counters[32:64])
        rows.append(dict(file=str(path), mode=records[0]['mode'], initial_profile=records[0]['profile'],
                         telemetry=records[0]['telemetry'], block=int(path.name.split('-')[-2]),
                         sampled_seconds=elapsed/1e9,
                         enqueue_wait_slow_fraction=sum(counters[23:32])/n if n else None,
                         wake_wait_slow_fraction=sum(counters[55:64])/wake if wake else None,
                         enqueue_completions_per_second=n*1e9/elapsed,
                         wake_completions_per_second=wake*1e9/elapsed,
                         runnable_callbacks_per_second=counters[71]*1e9/elapsed,
                         charged_cpu_equivalents=counters[70]/elapsed,
                         requested_time_fractions={p: t/span for p, t in occupancy.items()},
                         windows=windows, transitions=transitions))
    return dict(rows=rows, limits=[
        'Static profiles are separate runs, not simultaneous counterfactual outcomes.',
        'Background worker count is fixed, but old client arrivals were completion-relative.',
        'Runnable/wake counts depend on service and blocking, not just offered demand.',
        'Requested wall time is not actual CPU residency or enqueue-count occupancy.',
        'Completed waits miss tasks still waiting; no queue occupancy identity.',
        'Correct controller replay proves code consistency, not policy benefit.',
        'No causal estimate of oscillation harm; no stabilization justified from this alone.'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    with args.output.open('x') as out:
        json.dump(analyze(args.directory), out, indent=2)
        out.write('\n')


if __name__ == '__main__':
    main()
