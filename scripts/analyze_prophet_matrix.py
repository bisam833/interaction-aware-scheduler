#!/usr/bin/env python3
"""Read-only complete-matrix audit. Preserve block pairing and conditional tails.

Writes a new output directory; does not resume, pool separate runs, or tune policy.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics

from prophet_matrix import validate_block
from summarize_latency_audit import distribution, read_records, summarize
from summarize_prophet import summarize as summarize_controller


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def paired(candidate, reference, screen):
    def index(rows):
        result = {(r['condition'], r['block']): r for r in rows}
        if len(result) != len(rows):
            raise ValueError('duplicate condition/block in pairing')
        return result
    candidates, references = index(candidate), index(reference)
    if candidates.keys() != references.keys():
        raise ValueError('unmatched condition/block in pairing')
    result = []
    for key in sorted(candidates):
        c, r = candidates[key], references[key]
        reduction = 1 - c['latency']['client_to_presentation_ms']['p95'] / r['latency']['client_to_presentation_ms']['p95']
        throughput_loss = (1 - c['background_iterations_per_second'] / r['background_iterations_per_second']
                           if r['background_iterations_per_second'] else None)
        limit = max(100, 2 * r['background_max_progress_gap_ms']) if r['background_max_progress_gap_ms'] is not None else None
        result.append({
            'condition': key[0], 'block': key[1], 'p95_reduction_fraction': reduction,
            'background_throughput_loss_fraction': throughput_loss,
            'candidate_progress_gap_ms': c['background_max_progress_gap_ms'],
            'progress_gap_limit_ms': limit,
            'meets_each_screen_threshold': (
                reduction >= screen['paired_p95_reduction_fraction'] and
                throughput_loss is not None and
                throughput_loss <= screen['maximum_background_throughput_loss_fraction'] and
                c['background_max_progress_gap_ms'] <= limit),
        })
    return result


def controller_windows(records):
    """Reconstruct ABI-1's 500 ms accumulation windows, not exact kernel state.

    The current poll's requested profile is the *result* of that decision.
    Mixed-profile windows are retained; no causal attribution is inferred.
    """
    start = window_start = previous_request = None
    bins = [0] * 32
    windows, transitions = [], []
    for r in records:
        if r['type'] != 'sample':
            continue
        now = r['now_ns']
        if start is None:
            start = window_start = now
        if r['requested'] != previous_request:
            transitions.append({'seconds': (now - start) / 1e9,
                                'requested': r['requested'], 'generation': r['generation']})
            previous_request = r['requested']
        if not r['valid']:
            window_start, bins = now, [0] * 32
            continue
        bins = [a + b for a, b in zip(bins, r['counters'][:32])]
        if now - window_start >= 500_000_000:
            n = sum(bins)
            windows.append({'seconds': (now - start) / 1e9,
                            'now_ns': now, 'elapsed_ns': now - window_start, 'samples': n,
                            'slow_samples': sum(bins[23:]),
                            'enqueue_wait_ge_8388608ns_fraction': sum(bins[23:]) / n if n else None,
                            'requested_after_window': r['requested']})
            window_start, bins = now, [0] * 32
    return windows, transitions


def analyze(directory):
    manifest = json.loads((directory / 'manifest.json').read_text())
    status = json.loads((directory / 'status.json').read_text())
    if status != {'status': 'PASS', 'preflight_only': False}:
        raise ValueError('complete passing matrix required; use partial audit for failed runs')
    checks = {p: Path(p).is_file() and digest(Path(p)) == sha
              for p, sha in manifest['sha256'].items()}
    if not all(checks.values()):
        raise ValueError(f'current files differ from measured manifest: {[p for p, ok in checks.items() if not ok]}')
    expected = {f"{r['condition']}-{r['mode']}-{r['block']}.meta.json" for r in manifest['schedule']}
    if len(expected) != len(manifest['schedule']) or expected != {p.name for p in directory.glob('*.meta.json')}:
        raise ValueError('missing, duplicate or unexpected measured blocks')
    preflight = directory / 'preflight'
    expected_preflight = {f'idle-{m}-0.meta.json' for m in manifest['modes']}
    if expected_preflight != {p.name for p in preflight.glob('*.meta.json')}:
        raise ValueError('missing or unexpected preflight blocks')
    blocks, preflights, controllers = [], [], []
    pooled = defaultdict(lambda: defaultdict(list))
    for path in sorted([*directory.glob('*.meta.json'), *preflight.glob('*.meta.json')]):
        meta = json.loads(path.read_text())
        result = validate_block(path)  # no writes, no trusting saved PASS alone
        saved = json.loads(path.with_name(path.name.replace('.meta.json', '.validation.json')).read_text())
        if saved['status'] != 'PASS':
            raise ValueError(f'saved verdict disagrees: {path}')
        if (meta['before']['state'] != 'disabled' or meta['after']['state'] != 'disabled' or
                meta['after']['rejected'] != meta['before']['rejected']):
            raise ValueError(f'detach/rejection check failed: {path}')
        if meta['kernel'] != manifest['kernel'] or meta['cpus'] != manifest['cpus']:
            raise ValueError(f'host/affinity changed: {path}')
        stem = path.name.removesuffix('.meta.json')
        workers = [r for p in path.parent.glob(stem + '-worker-*.jsonl') for r in read_records(p)]
        if any(r.get('type') != 'background' or min(r['iterations'], r['cpu_ns'], r['wall_ns']) <= 0 for r in workers):
            raise ValueError(f'background progress missing: {path}')
        rates = [r['cpu_ns'] / r['wall_ns'] for r in workers]
        result['background_cpu_rate_jain_index'] = (
            sum(rates)**2 / (len(rates) * sum(x*x for x in rates)) if rates else None)
        result['background_min_to_max_cpu_rate'] = min(rates) / max(rates) if rates else None
        records = read_records(path.with_name(stem + '.jsonl'))
        states = [r for r in records if r.get('type') == 'surface_state']
        result['surface_state'] = {'events': len(states),
                                   'suspended_events': sum(r['suspended'] is True for r in states),
                                   'unknown_events': sum(r['suspended'] is None for r in states)}
        if path.parent == preflight:
            preflights.append(result)
            continue
        if meta['workers'] != manifest['workers'][meta['condition']] or meta['requested_trials'] != manifest['trials']:
            raise ValueError(f'manifest workload mismatch: {path}')
        blocks.append(result)
        _, series = summarize(path)
        for key, values in series.items():
            pooled[(result['condition'], result['implementation'])][key].extend(values)
        if meta.get('external_scheduler'):
            ctl_path = path.with_name(stem + '-controller.jsonl')
            ctl = summarize_controller(ctl_path)
            raw = read_records(ctl_path)
            samples = [r for r in raw if r['type'] == 'sample']
            windows, transitions = controller_windows(raw)
            span = samples[-1]['now_ns'] - samples[0]['now_ns'] if len(samples) > 1 else 0
            ctl.update(condition=meta['condition'], implementation=meta['implementation'], block=meta['block'],
                       decision_windows=windows, requested_transitions=transitions,
                       measured_control_body_cpu_ms=sum(r['control_cpu_ns'] for r in samples)/1e6,
                       first_sample_ns=samples[0]['now_ns'] if samples else None,
                       sampled_span_ns=span,
                       process_cpu_ms=ctl['exit']['process_cpu_ns']/1e6,
                       process_cpu_per_sampled_span_percent=100*ctl['exit']['process_cpu_ns']/span if span else None,
                       overhead_caveat='Process CPU includes load/start/stop/JSON; denominator is first-to-last sample span. Control body excludes JSON. Neither measures BPF overhead.')
            controllers.append(ctl)
    aggregates, comparisons = [], []
    for (condition, mode), series in sorted(pooled.items()):
        rows = [r for r in blocks if (r['condition'], r['implementation']) == (condition, mode)]
        aggregates.append({
            'condition': condition, 'implementation': mode, 'blocks': len(rows),
            'pooled_descriptive_latency': {k: distribution(xs) for k, xs in series.items()},
            'mean_block_background_iterations_per_second': statistics.mean(r['background_iterations_per_second'] for r in rows),
            'maximum_progress_gap_ms': max(r['background_max_progress_gap_ms'] for r in rows if r['background_max_progress_gap_ms'] is not None) if any(r['background_max_progress_gap_ms'] is not None for r in rows) else None,
            'minimum_block_jain_cpu_rate_index': min(r['background_cpu_rate_jain_index'] for r in rows if r['background_cpu_rate_jain_index'] is not None) if any(r['background_cpu_rate_jain_index'] is not None for r in rows) else None,
        })
        for reference in ('prophet-observe', 'prophet-static-1', 'prophet-static-2', 'pulse-off', 'lavd', 'linux', 'rusty'):
            if mode == reference:
                continue
            refs = [r for r in blocks if r['condition'] == condition and r['implementation'] == reference]
            comparisons.append({'condition': condition, 'candidate': mode, 'reference': reference,
                                'pairs': paired(rows, refs, manifest['candidate_screen'])})
    return {'manifest_hash_checks': checks, 'preflight': preflights, 'blocks': blocks,
            'aggregates': aggregates, 'comparisons': comparisons, 'controllers': controllers,
            'screen': manifest['candidate_screen'],
            'limitations': [
                'Paired blocks are sequential runs, not concurrent randomized treatment.',
                'Four blocks do not support precise uncertainty estimates; no significance claim.',
                'Pooled P99 at 120 samples has approximately 1.2 observations above its rank; descriptive only.',
                'All wake histograms are system-wide completed waits, not per-interaction attribution.',
                'Background rates/gaps include startup and teardown and are not pure benchmark-interval measurements.',
                'Progress gaps are observed million-iteration batch completion gaps, not maximum runnable starvation.',
                'Jain index describes equal workers CPU-time rates during this run, not general fairness.',
                'No suspend notification does not prove continuous visibility.',
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.directory.resolve()
    output = args.output.resolve()
    if output == source or source in output.parents:
        parser.error('analysis must be outside the raw directory')
    before = {str(p.relative_to(source)): digest(p) for p in source.rglob('*') if p.is_file()}
    report = analyze(source)
    after = {str(p.relative_to(source)): digest(p) for p in source.rglob('*') if p.is_file()}
    if before != after:
        raise RuntimeError('raw directory changed during analysis')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'analysis.json').write_text(json.dumps(report, indent=2) + '\n')
    (output / 'raw-sha256.json').write_text(json.dumps(before, indent=2) + '\n')
    print(f"Validated {len(report['blocks'])} measured blocks and {len(report['preflight'])} preflights; {output}")


if __name__ == '__main__':
    main()
