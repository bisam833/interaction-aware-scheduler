#!/usr/bin/env python3
"""Reuse the existing matched Wayland workload/runner with an external Prophet.
Raw results are exclusive-created. Preflight failures stop the entire matrix.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import time
from types import SimpleNamespace

import audit_latency as audit
from prophet_smoke import Maps, PROPHET, read_attached, variants, validate_counters
from summarize_latency_audit import summarize, read_records
from summarize_prophet import summarize as summarize_controller

ROOT = audit.ROOT
BENCH = ROOT / 'benchmarks/wayland_interaction/build/prophet-wayland-bench'
PROFILES = {
    'prophet-observe': [],
    'prophet-static-1': ['--mode', 'static', '--profile', '1'],
    'prophet-static-2': ['--mode', 'static', '--profile', '2'],
    'prophet-adaptive': ['--mode', 'adaptive'],
    'prophet-unobserved': ['--mode', 'static', '--no-telemetry'],
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_smoke(path):
    if json.loads((path / 'status.json').read_text()).get('status') != 'PASS':
        raise RuntimeError('host smoke gate did not pass')
    manifest = json.loads((path / 'manifest.json').read_text())
    if manifest['kernel'] != platform.release():
        raise RuntimeError('kernel changed since host smoke')
    for binary in (PROPHET, audit.LOAD):
        if manifest['sha256'].get(str(binary.relative_to(ROOT))) != digest(binary):
            raise RuntimeError(f'binary changed since host smoke: {binary}')
    for name, _, fault in variants():
        meta = json.loads((path / f'{name}.meta.json').read_text())
        if meta['after'] != {'state': 'disabled', 'rejected': meta['before']['rejected']}:
            raise RuntimeError(f'invalid detach result: {name}')
        if meta['scheduler_returncode'] != (-signal.SIGKILL if fault == 'kill' else 0):
            raise RuntimeError(f'invalid exit result: {name}')
        validate_counters(name, meta.get('lease_before') or [0] * 73, meta['counters'])
        workers = [json.loads((path / f'{name}-worker-{i}.jsonl').read_text()) for i in range(2)]
        if any(w['iterations'] <= 0 or w['cpu_ns'] <= 0 for w in workers):
            raise RuntimeError(f'no worker progress: {name}')
        fraction = workers[0]['cpu_ns'] / sum(w['cpu_ns'] for w in workers)
        if not .2 < fraction < .8 or meta['urgent_runs'] <= 0:
            raise RuntimeError(f'invalid urgency/progress result: {name}')
    return manifest


def external_factory(mode):
    def start(out, stem):
        raw_path = out / f'{stem}-controller.jsonl'
        raw = raw_path.open('x')
        err = (out / f'{stem}-controller.stderr').open('x')
        process = maps = None
        owner_fd = None

        def release():
            if maps is not None:
                maps.close()
            if owner_fd is not None:
                os.close(owner_fd)
            raw.close()
            err.close()

        try:
            process = subprocess.Popen([str(PROPHET), *PROFILES[mode]], stdout=raw, stderr=err)
            audit.wait_enabled(process)
            deadline = time.monotonic() + 2
            while True:
                try:
                    attached = read_attached(raw_path)
                    break
                except RuntimeError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise
                    time.sleep(.02)
            if attached['abi'] != 1 or attached['propagation']:
                raise RuntimeError('incompatible Prophet configuration')
            maps = Maps(attached)
            owner_fd = os.pidfd_open(process.pid)
            fds = (maps.fds[0], maps.fds[2], owner_fd)
            env = dict(zip(('PROPHET_TASK_FD', 'PROPHET_METRICS_FD', 'PROPHET_PID_FD'), map(str, fds)))
            return process, env, fds, release
        except BaseException:
            if process is not None:
                audit.stop_child(process)
            release()
            raise
    return start


def validate_block(meta_path):
    meta = json.loads(meta_path.read_text())
    output, _ = summarize(meta_path)
    if (meta.get('returncode') != 0 or meta.get('scheduler_returncode', 0) != 0 or
        output['error'] or output['missing'] or output['invalid_presentations'] or
        output['protocol_errors'] or output['statuses']['timeout'] or output['statuses']['discarded'] or
        output['background_workers_reported'] != output['background_workers_expected']):
        raise RuntimeError(f'failed/incomplete block retained: {meta_path}')
    raw = meta_path.with_name(meta_path.name.replace('.meta.json', '.jsonl'))
    records = read_records(raw)
    workload = [r for r in records if r.get('type') == 'workload']
    if len(workload) != 1 or any(workload[0].get(k) != v for k, v in {
        'version': 2, 'work_clock': 'thread-cpu', 'stages': 4,
        'work_ms_per_stage': meta['work_ms_per_stage'], 'requested_trials': meta['requested_trials'],
        'input_origin': 'synthetic-client-dispatch', 'partial': False, 'trace_delay': False}.items()):
        raise RuntimeError('workload conditions changed')
    trials = [r for r in records if r.get('type') == 'trial']
    if sorted(r['interaction_id'] for r in trials) != list(range(1, meta['requested_trials'] + 1)):
        raise RuntimeError('missing/duplicate/unexpected trial IDs')
    for trial in trials:
        if not trial['input_ns'] <= trial['work_start_ns'] <= trial['commit_ns'] <= trial['presentation_ns']:
            raise RuntimeError('invalid timestamp order')
        stages = [r for r in records if r.get('type') == 'work_stage' and r['interaction_id'] == trial['interaction_id']]
        if len(stages) != 4 or {r['worker'] for r in stages} != {0, 1, 2, 3}:
            raise RuntimeError('unequal work: missing/duplicate stages')
        if any(r['cpu_ns'] < meta['work_ms_per_stage'] * 1_000_000 for r in stages):
            raise RuntimeError('CPU-work condition was not met')
        if meta.get('external_scheduler') and (not trial['epoch_start_ns'] or
                trial['epoch_termination_ns'] < trial['epoch_start_ns']):
            raise RuntimeError('external epoch did not start/close')
    if meta.get('external_scheduler'):
        scheduler = [r for r in records if r.get('type') == 'scheduler']
        if len(scheduler) != 1 or scheduler[0]['mode'] != 'scx-prophet-external':
            raise RuntimeError('benchmark did not use external Prophet')
        controller_path = meta_path.with_name(meta_path.name.replace('.meta.json', '-controller.jsonl'))
        ctl = summarize_controller(controller_path)
        # The owner returncode above applies UEI_ECODE_USER; don't reinterpret
        # arbitrary packed UEI codes in Python.
        if not ctl['complete']:
            raise RuntimeError('incomplete controller log')
        controller_path.with_suffix('.summary.json').write_text(json.dumps(ctl, indent=2) + '\n')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--smoke-results', type=Path, default=ROOT / 'results/prophet-host-smoke-01')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--trials', type=int, default=30)
    parser.add_argument('--blocks', type=int, default=4)
    parser.add_argument('--conditions', nargs='+', choices=['idle', 'moderate', 'heavy'], default=['heavy'])
    parser.add_argument('--preflight-only', action='store_true')
    parser.add_argument('--plan', action='store_true', help='save exact protocol/hashes without attaching')
    args = parser.parse_args()
    if not 1 <= args.trials <= 10000 or not 1 <= args.blocks <= 100:
        parser.error('trials must be 1..10000 and blocks 1..100')
    smoke = check_smoke(args.smoke_results)
    if not BENCH.exists() or not shutil.which('scx_lavd') or not shutil.which('scx_rusty'):
        parser.error('build prophet-wayland-bench and install both LAVD and Rusty; no silent comparator removal')
    if audit.snapshot()['state'] != 'disabled':
        parser.error('another scheduler is active; refusing to replace it')
    if not args.plan and (os.geteuid() != 0 or not all(os.getenv(k) for k in ('WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR'))):
        parser.error('run on privileged host with WAYLAND_DISPLAY and XDG_RUNTIME_DIR preserved')
    args.output.mkdir(parents=True, exist_ok=False)
    cpus = sorted(os.sched_getaffinity(0))
    modes = ['linux', 'lavd', 'rusty', 'pulse-off', *PROFILES]
    workers = {'idle': 0, 'moderate': max(1, len(cpus)//2), 'heavy': 2*len(cpus)}
    paths = [BENCH, PROPHET, audit.LOAD, ROOT / 'src/pulsesched/build/pulsesched',
             Path(shutil.which('scx_lavd')), Path(shutil.which('scx_rusty')),
             Path(__file__), ROOT / 'scripts/audit_latency.py', ROOT / 'scripts/prophet_smoke.py',
             ROOT / 'benchmarks/wayland_interaction/benchmark.c',
             ROOT / 'benchmarks/wayland_interaction/external_scheduler.h']
    plan = {'kernel': platform.release(), 'cpus': cpus, 'modes': modes, 'conditions': args.conditions,
            'workers': workers, 'blocks': args.blocks, 'trials': args.trials,
            'work_ms_per_stage': 20, 'stages': 4, 'interval_ms': 200,
            'prophet_arguments': PROFILES, 'smoke_manifest': smoke,
            'sha256': {str(p): digest(p) for p in paths},
            'analysis': 'Exploratory matched steady-load comparison. No prediction. Preserve failures; compare paired block tails, throughput and progress. No P99.9 claims.',
            'candidate_screen': {'paired_p95_reduction_fraction': .05,
                                 'maximum_background_throughput_loss_fraction': .03,
                                 'progress_gap_limit': 'max(100ms, 2x paired observe gap)',
                                 'meaning': 'screen for further replication, not significance or proof'},
            'schedule': [{'block': b, 'condition': c, 'mode': m} for b in range(args.blocks)
                         for c in args.conditions for m in modes[b % len(modes):]+modes[:b % len(modes)]]}
    (args.output / 'manifest.json').write_text(json.dumps(plan, indent=2) + '\n')
    for name in ('lavd', 'rusty'):
        version = subprocess.run(['scx_' + name, '--version'], capture_output=True, text=True, timeout=10)
        if version.returncode:
            raise RuntimeError(f'cannot identify {name} version')
        (args.output / f'{name}-version.txt').write_text(version.stdout + version.stderr)
    if args.plan:
        print(f'Plan only; no scheduler attached: {args.output}')
        return
    options = SimpleNamespace(trials=args.trials, work_ms=20, interval_ms=200,
                              trace_delay=False, timeout=max(180, args.trials * 3))

    def run(out, condition, mode, block, opts):
        audit.run_one(out, condition, workers[condition], mode, block, opts,
                      external=external_factory(mode) if mode in PROFILES else None, bench_path=BENCH)
        result = validate_block(out / f'{condition}-{mode}-{block}.meta.json')
        with (out / 'block-validation.jsonl').open('a') as stream:
            stream.write(json.dumps(result) + '\n')

    try:
        preflight = args.output / 'preflight'
        preflight.mkdir()
        audit.smoke(preflight)
        short = SimpleNamespace(**vars(options)); short.trials = 3
        for mode in modes:
            run(preflight, 'idle', mode, 0, short)
        if not args.preflight_only:
            for item in plan['schedule']:
                run(args.output, item['condition'], item['mode'], item['block'], options)
    except BaseException as exc:
        (args.output / 'status.json').write_text(json.dumps({'status': 'FAILED_OR_INTERRUPTED', 'error': str(exc)}) + '\n')
        raise
    (args.output / 'status.json').write_text(json.dumps({'status': 'PASS', 'preflight_only': args.preflight_only}) + '\n')
    print(f'Completed; raw artifacts: {args.output}')


if __name__ == '__main__':
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    main()
