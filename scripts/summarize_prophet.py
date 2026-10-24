#!/usr/bin/env python3
"""Report histogram bounds, support and observed profiles; never interpolate tails."""
import argparse
import json
import math
from pathlib import Path


def histogram(bins):
    n = sum(bins)
    quantiles = {}
    for label, q, minimum in [('p50', .5, 40), ('p95', .95, 400),
                               ('p99', .99, 2000), ('p999', .999, 20000)]:
        if n < minimum:
            quantiles[label] = {'supported': False, 'minimum_samples': minimum}
            continue
        rank, cumulative = math.ceil(q * n), 0
        for b, count in enumerate(bins):
            cumulative += count
            if cumulative >= rank:
                quantiles[label] = {'supported': True, 'lower_ns': 0 if b == 0 else 1 << b,
                                    'upper_exclusive_ns': None if b == 31 else 1 << (b + 1)}
                break
    return {'samples': n, 'bins': bins, 'quantiles': quantiles,
            'support_rule': 'at least 20 observations in each tail; not a confidence interval'}


def summarize(path):
    totals = [0] * 73
    samples = invalid = 0
    attached = exited = None
    for line in path.read_text().splitlines():
        record = json.loads(line)  # malformed/truncated logs fail, never silently disappear
        if record['type'] == 'attached':
            if attached is not None or record['abi'] != 1:
                raise ValueError('mixed sessions or unsupported ABI')
            attached = record
        elif record['type'] == 'exit':
            exited = record
        elif record['type'] == 'sample':
            counters = record['counters']
            if len(counters) != 73 or any(type(x) is not int or x < 0 for x in counters):
                raise ValueError('invalid counter array')
            totals = [a + b for a, b in zip(totals, counters)]
            samples += 1
            invalid += not record['valid']
    return {'file': str(path), 'attached': attached, 'exit': exited,
            'complete': attached is not None and exited is not None,
            'polls': samples, 'invalid_polls_including_initial_baseline': invalid,
            'enqueue_to_run': histogram(totals[:32]),
            'runnable_to_first_run': histogram(totals[32:64]),
            'profile_enqueues': totals[64:67], 'fallback_enqueues': totals[67],
            'local_enqueues': totals[68], 'shared_enqueues': totals[69],
            'completed_running_interval_ns': totals[70],
            'runnable_events': totals[71], 'quiescent_events': totals[72],
            'caveat': 'Completed intervals only; approximate asynchronous CPU reads; no per-task/compositor attribution.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logs', type=Path, nargs='+')
    args = parser.parse_args()
    for path in args.logs:
        print(json.dumps(summarize(path)))


if __name__ == '__main__':
    main()
