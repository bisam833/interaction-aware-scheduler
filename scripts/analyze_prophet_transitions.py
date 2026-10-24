#!/usr/bin/env python3
"""Validate and summarize transition trials without hiding queueing or censoring."""
import argparse
from bisect import bisect_right
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import random
import statistics

from prophet_transitions import validate_transition, NS
from summarize_latency_audit import read_records
from summarize_wayland_trials import percentile
from summarize_prophet import histogram


def quantiles(values):
    n = len(values)
    return {'n': n, **{f'p{int(q*100)}': percentile(values, q) for q in (.5, .95, .99)},
            'expected_upper_tail_observations': {'p95': n*.05, 'p99': n*.01},
            'interpretation': 'empirical only; fewer than 20 tail observations is sparse, not precise'}


def recovery(trials, start, end, threshold_ms, consecutive):
    """Time until a completed run of k on-time outcomes; right-censor at phase end."""
    streak = 0
    for r in sorted(trials, key=lambda r: r['due_ns']):
        if not start <= r['due_ns'] < end:
            continue
        streak = streak+1 if r['planned_to_presentation_ms'] <= threshold_ms else 0
        if streak >= consecutive and r['presentation_ns'] <= end:
            return {'recovered': True, 'elapsed_ms': (r['presentation_ns']-start)/1e6,
                    'definition': 'k consecutive planned-input-to-presentation outcomes under fixed SLO'}
    return {'recovered': False, 'elapsed_ms': None, 'right_censored_after_ms': (end-start)/1e6,
            'definition': 'not observed before this phase ended; not zero'}


@lru_cache(maxsize=32)
def bootstrap_indices(n, seed):
    rng = random.Random(seed)
    return tuple(tuple(rng.choices(range(n), k=n)) for _ in range(4000))


def bootstrap_mean(differences, seed):
    if not differences:
        return None
    means = [sum(differences[i] for i in indexes)/len(differences)
             for indexes in bootstrap_indices(len(differences), seed)]
    return dict(n_blocks=len(differences), mean=statistics.mean(differences),
                minimum=min(differences), maximum=max(differences),
                paired_block_differences=differences,
                exploratory_bootstrap_95=[percentile(means, .025), percentile(means, .975)],
                warning='few-block resampling does not establish significance or tail accuracy')


def analyze_block(path, settings):
    verdict = validate_transition(path)
    meta = json.loads(path.read_text())
    plan, epoch = meta['plan'], meta['epoch_ns']
    stem = path.name.removesuffix('.meta.json')
    records = read_records(path.with_name(stem+'.jsonl'))
    scheduled = {r['interaction_id']: r for r in records if r.get('type') == 'planned_input'}
    trials = []
    for r in records:
        if r.get('type') != 'trial':
            continue
        due = scheduled[r['interaction_id']]['due_ns']
        trials.append({**r, 'due_ns': due,
                       'planned_to_presentation_ms': (r['presentation_ns']-due)/1e6,
                       'dispatch_lag_ms': (r['input_ns']-due)/1e6,
                       'client_to_presentation_ms': (r['presentation_ns']-r['input_ns'])/1e6,
                       'commit_to_presentation_ms': r['commit_to_presentation_ns']/1e6})
    bins, worker_phases = [], []
    for p in path.parent.glob(stem+'-worker-*.jsonl'):
        rows = read_records(p)
        bins.extend(r for r in rows if r.get('type') == 'load_bin')
        worker_phases.extend(r for r in rows if r.get('type') == 'load_phase')
    controller_path = path.with_name(stem+'-controller.jsonl')
    controller = read_records(controller_path) if controller_path.exists() else []
    samples = [r for r in controller if r['type'] == 'sample']
    sample_times = [r['now_ns'] for r in samples]
    result = dict(condition=meta['condition'], implementation=meta['implementation'], block=meta['block'],
                  verdict=verdict, phases=[], controller_exit=[r for r in controller if r['type'] == 'exit'],
                  workload_usage=meta['all_workload_children_usage'])
    duration = plan['duration_ns']
    result['overall'] = dict(
        latency={key: quantiles([r[key] for r in trials]) for key in
                 ('planned_to_presentation_ms', 'dispatch_lag_ms', 'client_to_presentation_ms',
                  'commit_to_presentation_ms')},
        iterations_per_second=sum(b['iterations'] for b in bins)*NS/duration,
        batch_iterations_per_second=sum(b['iterations'] for b in bins if not b['periodic'])*NS/duration,
        periodic_iterations_per_second=sum(b['iterations'] for b in bins if b['periodic'])*NS/duration,
        max_batch_progress_gap_ms=max((b['max_progress_gap_ns']/1e6 for b in bins if not b['periodic']), default=None),
        crossed_workload_end_outcomes=sum(r['presentation_ns'] >= epoch+duration for r in trials))
    for i, p in enumerate(plan['phases']):
        start, end = epoch+p['start_ns'], epoch+p['start_ns']+p['duration_ns']
        phase_trials = [r for r in trials if start <= r['due_ns'] < end]
        phase = dict(index=i, label=p['label'], **{k: p[k] for k in ('batch_workers', 'periodic_workers')},
                     recovery=recovery(phase_trials, start, end, settings['recovery_threshold_ms'],
                                       settings['recovery_consecutive_trials']),
                     crossed_phase_outcomes=sum(r['presentation_ns'] >= end for r in phase_trials),
                     missed_periodic_windows=sum(r['offered_periodic_windows']-r['periodic_windows_with_progress']
                                                 for r in worker_phases if r['phase'] == i),
                     maximum_worker_activation_lag_ms=max(
                         ((r['observed_start_ns']-r['planned_start_ns'])/1e6 for r in worker_phases
                          if r['phase'] == i and r['active']), default=0),
                     windows={})
        for label, lo, hi in [('whole', start, end),
                              ('transition', start, min(start+settings['transition_window_ns'], end)),
                              ('steady', min(start+settings['transition_window_ns'], end), end)]:
            rs = [r for r in phase_trials if lo <= r['due_ns'] < hi]
            bs = [b for b in bins if lo <= b['start_ns'] < hi]
            ss = [s for s in samples if lo <= s['now_ns']-s['elapsed_ns'] and s['now_ns'] <= hi]
            valid = [s for s in ss if s['valid']]
            duration = hi-lo
            occupancy = {str(x): 0 for x in (-1, 0, 1, 2)}
            arrivals = {str(x): 0 for x in (-1, 0, 1, 2)}
            for r in rs:
                index = bisect_right(sample_times, r['due_ns'])-1
                if index >= 0:
                    arrivals[str(samples[index]['requested'])] += 1
            for a, b in zip(samples, samples[1:]):
                overlap = max(0, min(hi, b['now_ns'])-max(lo, a['now_ns']))
                occupancy[str(a['requested'])] += overlap
            phase['windows'][label] = dict(
                latency={key: quantiles([r[key] for r in rs]) for key in
                         ('planned_to_presentation_ms', 'dispatch_lag_ms', 'client_to_presentation_ms',
                          'commit_to_presentation_ms')},
                iterations_per_second=sum(b['iterations'] for b in bs)*NS/duration if duration else None,
                batch_iterations_per_second=sum(b['iterations'] for b in bs if not b['periodic'])*NS/duration if duration else None,
                periodic_iterations_per_second=sum(b['iterations'] for b in bs if b['periodic'])*NS/duration if duration else None,
                workload_cpu_ns=sum(b['cpu_ns'] for b in bs),
                max_batch_progress_gap_ms=max((b['max_progress_gap_ns']/1e6 for b in bs if not b['periodic']), default=None),
                control_body_cpu_ns=sum(s['control_cpu_ns'] for s in ss),
                controller_polls_wholly_inside_window=len(ss),
                invalid_controller_polls=sum(not s['valid'] for s in ss),
                requested_profile_fraction={k: v/duration if duration else None for k, v in occupancy.items()} if samples else None,
                planned_arrivals_by_last_requested_profile=arrivals if samples else None,
                actual_profile_enqueue_counts=[sum(s['counters'][64+j] for s in valid) for j in range(3)] if samples else None,
                fallback_enqueues=sum(s['counters'][67] for s in valid) if samples else None,
                runnable_to_first_run=histogram([sum(s['counters'][32+j] for s in valid) for j in range(32)]) if samples else None)
        result['phases'].append(phase)
    return result


def compare(blocks, manifest):
    """Compare within block/phase; never treat individual trials as replicates."""
    comparisons = []
    keys = {(b['condition'], p['index']) for b in blocks for p in b['phases']}
    keys.update((b['condition'], 'overall') for b in blocks)
    for scenario, phase in sorted(keys, key=lambda x: (x[0], str(x[1]))):
        for window in (('whole',) if phase == 'overall' else ('whole', 'transition', 'steady')):
            pairs = [('prophet-adaptive', r) for r in
                     ('prophet-static-1', 'prophet-static-2', 'prophet-observe', 'pulse-off', 'lavd', 'rusty', 'linux')]
            pairs.append(('prophet-static-2', 'prophet-static-1'))
            for candidate_name, reference in pairs:
                def select(mode):
                    return {b['block']: (b['overall'] if phase == 'overall' else b['phases'][phase]['windows'][window]) for b in blocks
                            if b['condition'] == scenario and b['implementation'] == mode}
                candidate, refs = select(candidate_name), select(reference)
                if candidate.keys() != refs.keys():
                    raise ValueError('unmatched blocks; no survivor-only comparison')
                reductions, losses, latency_deltas, gaps = [], [], [], []
                role_losses = {'batch': [], 'periodic': []}
                for block in sorted(candidate):
                    c, r = candidate[block], refs[block]
                    cq = c['latency']['planned_to_presentation_ms']['p95']
                    rq = r['latency']['planned_to_presentation_ms']['p95']
                    if cq is not None and rq:
                        reductions.append(1-cq/rq)
                        latency_deltas.append(cq-rq)
                    if r['iterations_per_second']:
                        losses.append(1-c['iterations_per_second']/r['iterations_per_second'])
                    if c['max_batch_progress_gap_ms'] is not None and r['max_batch_progress_gap_ms'] is not None:
                        gaps.append(c['max_batch_progress_gap_ms']-r['max_batch_progress_gap_ms'])
                    for role in role_losses:
                        if r[role+'_iterations_per_second']:
                            role_losses[role].append(1-c[role+'_iterations_per_second']/r[role+'_iterations_per_second'])
                comparisons.append(dict(scenario=scenario, phase=phase, window=window,
                                        candidate=candidate_name, reference=reference,
                                        p95_delta_ms=bootstrap_mean(latency_deltas, manifest['analysis']['bootstrap_seed']),
                                        p95_reduction_fraction=bootstrap_mean(reductions, manifest['analysis']['bootstrap_seed']),
                                        throughput_loss_fraction=bootstrap_mean(losses, manifest['analysis']['bootstrap_seed']),
                                        batch_progress_gap_delta_ms=bootstrap_mean(gaps, manifest['analysis']['bootstrap_seed']),
                                        throughput_loss_by_role={role: bootstrap_mean(xs, manifest['analysis']['bootstrap_seed'])
                                                                 for role, xs in role_losses.items()}))
    return comparisons


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.directory.resolve() == args.output.resolve() or args.directory.resolve() in args.output.resolve().parents:
        p.error('output must be outside raw directory')
    manifest = json.loads((args.directory/'manifest.json').read_text())
    if json.loads((args.directory/'status.json').read_text()) != {'status': 'PASS', 'preflight_only': False}:
        p.error('complete passing run required; retain failed runs separately')
    expected = {f"{r['scenario']}-{r['mode']}-{r['block']}.meta.json" for r in manifest['schedule']}
    if len(expected) != len(manifest['schedule']) or expected != {f.name for f in args.directory.glob('*.meta.json')}:
        p.error('block inventory differs from plan')
    hashes = {name: Path(name).is_file() and hashlib.sha256(Path(name).read_bytes()).hexdigest() == value
              for name, value in manifest['sha256'].items()}
    if not all(hashes.values()):
        p.error('current files differ from measured manifest; restore the measured revision for analysis')
    if json.loads((args.directory/'scheduler-smoke/status.json').read_text())['status'] != 'PASS':
        p.error('scheduler smoke gate did not pass')
    preflight = args.directory/'preflight'
    expected_preflight = {f'preflight-{m}-0.meta.json' for m in manifest['modes']}
    if expected_preflight != {f.name for f in preflight.glob('*.meta.json')}:
        p.error('preflight inventory differs from plan')
    for name in sorted(expected_preflight):
        validate_transition(preflight/name)
    raw_hashes = {str(f.relative_to(args.directory)): hashlib.sha256(f.read_bytes()).hexdigest()
                  for f in args.directory.rglob('*') if f.is_file()}
    blocks = []
    for name in sorted(expected):
        path = args.directory/name
        meta = json.loads(path.read_text())
        if meta['plan'] != manifest['plans'][meta['condition']] or meta['cpus'] != manifest['cpus'] or meta['kernel'] != manifest['kernel']:
            p.error('per-block plan or host differs from manifest')
        saved = path.with_name(path.name.replace('.meta.json', '.validation.json'))
        if json.loads(saved.read_text())['status'] != 'PASS':
            p.error('saved block verdict is not PASS')
        blocks.append(analyze_block(path, manifest['analysis']))
    result = dict(manifest_hash_checks=hashes, blocks=blocks, comparisons=compare(blocks, manifest), limitations=[
        'No precise P99 when phase/window tail support is sparse; see per-cell counts.',
        'Recovery is a predeclared 200ms SLO streak, not convergence to an estimated equilibrium.',
        'Completed phase cohorts are assigned by planned input time, including late/cross-phase outcomes.',
        'Throughput is charged by worker batch start; boundary batch CPU/time may straddle phases.',
        'Progress gaps are batch completion gaps, not exact longest runnable starvation.',
        'Requested profile time and actual enqueue fractions are different quantities.',
        'Adaptive/static crossing is necessary opportunity evidence, not proof of useful timing.',
        'An occupancy-matched randomized open-loop intervention is still needed before attributing timing benefit.',
        'No controller process CPU attribution per phase; only measured body CPU and whole-process total.',
    ])
    after = {str(f.relative_to(args.directory)): hashlib.sha256(f.read_bytes()).hexdigest()
             for f in args.directory.rglob('*') if f.is_file()}
    if after != raw_hashes:
        raise RuntimeError('raw files changed during analysis')
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output/'analysis.json').write_text(json.dumps(result, indent=2)+'\n')
    (args.output/'raw-sha256.json').write_text(json.dumps(raw_hashes, indent=2)+'\n')
    print(f'Analyzed {len(blocks)} matched blocks: {args.output}')


if __name__ == '__main__':
    main()
