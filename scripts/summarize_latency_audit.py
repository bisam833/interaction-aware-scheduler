#!/usr/bin/env python3
"""Report each block, plus same-condition/mode aggregates; preserve all failures."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
from summarize_wayland_trials import percentile

LATENCY_POPULATION = ('Presentation latency uses valid presented trials only. Missing, timeout and '
                      'discarded feedback are excluded; these percentiles do not describe failed trials. '
                      'Handoff and CPU-work metrics include all recorded stages.')


def distribution(xs):
    return {'n': len(xs), **{k: percentile(xs, q) for k, q in [('p50', .5), ('p95', .95), ('p99', .99)]}}


def read_records(path):
    result = []
    for line in path.read_text().splitlines():
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            result.append({'type': 'parse-error'})
    return result


def summarize(meta_path):
    meta = json.loads(meta_path.read_text())
    stem = meta_path.name.removesuffix('.meta.json')
    raw = meta_path.with_name(stem + '.jsonl')
    records = read_records(raw) if raw.exists() else []
    trials = [r for r in records if r.get('type') == 'trial']
    presented = [r for r in trials if r.get('status') == 'presented' and
                 r.get('presentation_clock_id') == 1 and r['presentation_ns'] >= r['commit_ns']]
    timing = {r['interaction_id']: r for r in records if r.get('type') == 'feedback_timing'}
    series = {
        'client_to_presentation_ms': [(r['presentation_ns'] - r['input_ns']) / 1e6 for r in presented],
        'work_to_commit_ms': [r['work_to_commit_ns'] / 1e6 for r in presented],
        'commit_to_presentation_ms': [r['commit_to_presentation_ns'] / 1e6 for r in presented],
        'handoff_ms': [r['handoff_ns'] / 1e6 for r in records if r.get('type') == 'work_stage'],
        'work_cpu_ms': [r['cpu_ns'] / 1e6 for r in records if r.get('type') == 'work_stage'],
        'feedback_delivery_ms': [(timing[r['interaction_id']]['feedback_received_ns'] - r['presentation_ns']) / 1e6
                                 for r in presented if r['interaction_id'] in timing],
        # Refresh intervals elapsed after commit; NOT a count of compositor-dropped frames.
        'commit_wait_refresh_intervals': [r['commit_to_presentation_ns'] / timing[r['interaction_id']]['refresh_ns']
                                         for r in presented if timing.get(r['interaction_id'], {}).get('refresh_ns', 0)],
    }
    background = [r for p in meta_path.parent.glob(stem + '-worker-*.jsonl') for r in read_records(p)
                  if r.get('type') == 'background']
    output = {
        'file': str(raw), 'condition': meta['condition'], 'implementation': meta['implementation'],
        'block': meta['block'], 'returncode': meta.get('returncode'), 'error': meta.get('error'),
        'expected': meta['requested_trials'], 'trial_records': len(trials),
        'missing': max(0, meta['requested_trials'] - len(trials)),
        'statuses': {s: sum(r.get('status') == s for r in trials) for s in ('presented', 'discarded', 'timeout')},
        'invalid_presentations': sum(r.get('status') == 'presented' for r in trials) - len(presented),
        'protocol_errors': sum(r.get('type') in ('protocol-error', 'parse-error', 'late-feedback') for r in records),
        'ignored_inputs': sum(r.get('type') == 'input-ignored' for r in records),
        'background_workers_expected': meta['workers'], 'background_workers_reported': len(background),
        'background_iterations_per_second': sum(r['iterations'] * 1e9 / r['wall_ns'] for r in background),
        'background_max_progress_gap_ms': max((r['max_batch_gap_ns'] / 1e6 for r in background), default=None),
        'benchmark_usage': meta.get('benchmark_usage'),
        'latency_population': LATENCY_POPULATION,
        'valid_presented_fraction': len(presented) / meta['requested_trials'] if meta['requested_trials'] else None,
        'latency': {k: distribution(v) for k, v in series.items()},
    }
    return output, series


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    aggregates = defaultdict(lambda: defaultdict(list))
    failures = defaultdict(int)
    for path in sorted(args.directory.glob('*.meta.json')):
        output, series = summarize(path)
        print(json.dumps({'type': 'block', **output}))
        key = output['condition'], output['implementation']
        for metric, values in series.items():
            aggregates[key][metric].extend(values)
        failures[key] += (output['missing'] + output['statuses']['timeout'] +
                          output['statuses']['discarded'] + output['protocol_errors'] +
                          output['invalid_presentations'] + bool(output['error']))
    for (condition, implementation), series in sorted(aggregates.items()):
        print(json.dumps({'type': 'aggregate', 'condition': condition, 'implementation': implementation,
                          'failure_indicators': failures[(condition, implementation)],
                          'latency_population': LATENCY_POPULATION,
                          'latency': {k: distribution(v) for k, v in series.items()}}))


if __name__ == '__main__':
    main()
