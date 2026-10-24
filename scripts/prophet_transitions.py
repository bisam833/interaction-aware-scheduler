#!/usr/bin/env python3
"""Frozen-controller transition experiment; absolute offered work, owned lifecycle."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import resource
import shutil
import signal
import struct
import subprocess
import sys
import time

import audit_latency as audit
import prophet_matrix as matrix
from summarize_latency_audit import read_records

ROOT = audit.ROOT
BENCH = ROOT / 'benchmarks/wayland_interaction/build/prophet-transition-bench'
LOAD = BENCH.with_name('transition-load')
MODES = ['linux', 'lavd', 'rusty', 'pulse-off', 'prophet-observe',
         'prophet-static-1', 'prophet-static-2', 'prophet-adaptive']
NS = 1_000_000_000


def plans(ncpu, seed):
    """Offered eligibility is independent of the scheduler and its predictions."""
    moderate, saturated = max(1, ncpu // 2), 2 * ncpu
    rng = random.Random(seed)

    def pack(rows):
        offset, phases = 0, []
        for label, seconds, batch, periodic in rows:
            phases.append(dict(label=label, start_ns=offset, duration_ns=seconds*NS,
                               batch_workers=batch, periodic_workers=periodic))
            offset += seconds*NS
        return dict(phases=phases, duration_ns=offset, interval_ms=500, first_input_ns=250_000_000,
                    trials=offset//500_000_000, work_ms=20, feedback_timeout_ms=1000)
    steps = [('idle', 12, 0, 0), ('moderate-up', 12, moderate, 0),
             ('saturated', 12, saturated, 0), ('moderate-down', 12, moderate, 0),
             ('idle-final', 12, 0, 0)]
    bursts = [('idle', 4, 0, 0)]
    for i in range(6):
        bursts.append((f'burst-{i}', rng.randint(3, 7), saturated if i % 2 == 0 else moderate, 0))
        bursts.append((f'gap-{i}', rng.randint(2, 4), 0, 0))
    mixed = [('idle', 6, 0, 0), ('batch', 12, moderate, 0),
             ('batch-plus-periodic', 12, saturated, moderate),
             ('moderate-plus-periodic', 12, moderate, moderate), ('idle-final', 6, 0, 0)]
    return {'steps': pack(steps), 'bursts': pack(bursts), 'mixed': pack(mixed),
            'preflight': pack([('idle', 1, 0, 0), ('busy', 1, 2, 1), ('idle-final', 1, 0, 0)])}


def order(blocks, scenarios, seed):
    rng = random.Random(seed)
    result = []
    # Randomly permuted Latin rows: each arm occurs once per block; over eight
    # blocks every arm occupies each position once. Four is a partial balance.
    base = MODES.copy()
    rng.shuffle(base)
    rotations = list(range(len(base)))
    rng.shuffle(rotations)
    for block in range(blocks):
        names = list(scenarios)
        rng.shuffle(names)
        shift = rotations[block % len(base)]
        modes = base[shift:] + base[:shift]
        for scenario in names:
            result += [dict(block=block, scenario=scenario, mode=m) for m in modes]
    return result


def write_plan(path, plan):
    path.write_text(''.join(f"{p['duration_ns']} {p['batch_workers']} {p['periodic_workers']}\n"
                            for p in plan['phases']))


def ready(path, kind):
    if not path.exists():
        return False
    # Writer may be between writes. Only complete lines can acknowledge readiness.
    for line in path.read_text().splitlines(keepends=True):
        if line.endswith('\n') and json.loads(line).get('type') == kind:
            return True
    return False


def wait_ready(items, processes, timeout=30):
    deadline = time.monotonic() + timeout
    while True:
        if any(p.poll() is not None for p in processes):
            raise RuntimeError('owned process exited before start barrier')
        if all(ready(path, kind) for path, kind in items):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('readiness timeout; no workload epoch issued')
        time.sleep(.02)


def validate_transition(meta_path):
    meta = json.loads(meta_path.read_text())
    matrix.validate_block(meta_path)  # existing strict work/feedback/owner gate
    plan, epoch = meta['plan'], meta['epoch_ns']
    if meta['after'] != {'state': 'disabled', 'rejected': meta['before']['rejected']}:
        raise RuntimeError('detach/rejection state changed')
    stem = meta_path.name.removesuffix('.meta.json')
    records = read_records(meta_path.with_name(stem + '.jsonl'))
    if any(r.get('type') == 'surface_state' and r.get('suspended') is True for r in records):
        raise RuntimeError('compositor reported surface suspension; visibility condition failed')
    planned = [r for r in records if r.get('type') == 'planned_input']
    starts = [r for r in records if r.get('type') == 'transition_epoch']
    if len(starts) != 1 or starts[0]['epoch_ns'] != epoch or starts[0]['observed_ns'] >= epoch:
        raise RuntimeError('client missed common start barrier')
    if [r['interaction_id'] for r in planned] != list(range(1, plan['trials'] + 1)):
        raise RuntimeError('missing/duplicate planned arrivals')
    trials = {r['interaction_id']: r for r in records if r.get('type') == 'trial'}
    for i, r in enumerate(planned):
        due = epoch + plan['first_input_ns'] + i*plan['interval_ms']*1_000_000
        if r['due_ns'] != due or r['dispatch_ns'] < due or r['dispatch_ns'] != trials[i+1]['input_ns']:
            raise RuntimeError('arrival schedule changed or dispatch timestamp invalid')
    paths = sorted(meta_path.parent.glob(stem + '-worker-*.jsonl'))
    expected_workers = {(role, i) for role, key in ((0, 'batch_workers'), (1, 'periodic_workers'))
                        for i in range(max(p[key] for p in plan['phases']))}
    workers = set()
    for path in paths:
        rs = read_records(path)
        begins = [r for r in rs if r.get('type') == 'load_ready']
        exits = [r for r in rs if r.get('type') == 'load_exit']
        phases = [r for r in rs if r.get('type') == 'load_phase']
        bins = [r for r in rs if r.get('type') == 'load_bin']
        if len(begins) != 1 or len(exits) != 1 or not exits[0]['complete']:
            raise RuntimeError(f'incomplete worker: {path}')
        identity = begins[0]['periodic'], begins[0]['index']
        if identity in workers:
            raise RuntimeError('duplicate worker identity')
        workers.add(identity)
        if len(phases) != len(plan['phases']) or len(bins) != plan['duration_ns']//NS:
            raise RuntimeError('missing worker phases/bins')
        for i, (observed, p) in enumerate(zip(phases, plan['phases'])):
            active = identity[1] < p['periodic_workers' if identity[0] else 'batch_workers']
            if (observed['phase'] != i or observed['active'] != active or
                observed['planned_start_ns'] != epoch+p['start_ns'] or
                observed['planned_end_ns'] != epoch+p['start_ns']+p['duration_ns'] or
                observed['observed_start_ns'] < observed['planned_start_ns'] or
                observed['observed_end_ns'] < observed['planned_end_ns']):
                raise RuntimeError('worker phase timing/eligibility changed')
            if active and not sum(b['iterations'] for b in bins if b['phase'] == i):
                raise RuntimeError('active worker made no phase progress (retained as failure)')
            if not active and any(b['iterations'] or b['cpu_ns'] for b in bins if b['phase'] == i):
                raise RuntimeError('inactive phase contains background work')
            offered = p['duration_ns']//100_000_000 if active and identity[0] else 0
            if (observed['offered_periodic_windows'] != offered or
                    not 0 <= observed['periodic_windows_with_progress'] <= offered):
                raise RuntimeError('periodic eligibility accounting mismatch')
        for i, b in enumerate(bins):
            p = next(j for j, p in enumerate(plan['phases'])
                     if p['start_ns'] <= i*NS < p['start_ns']+p['duration_ns'])
            if (b['start_ns'] != epoch+i*NS or b['end_ns'] != epoch+(i+1)*NS or
                b['phase'] != p or (b['periodic'], b['index']) != identity or
                min(b['iterations'], b['cpu_ns'], b['max_progress_gap_ns']) < 0):
                raise RuntimeError('worker bin mismatch')
    if (workers != expected_workers or len(meta['worker_returncodes']) != len(workers) or
            any(rc != 0 for rc in meta['worker_returncodes'])):
        raise RuntimeError('worker inventory/exit mismatch')
    return {'status': 'PASS', 'trials': len(trials), 'workers': len(workers),
            'maximum_input_dispatch_lag_ms': max((r['dispatch_ns']-r['due_ns'])/1e6 for r in planned)}


def run_block(out, scenario, mode, block, plan):
    stem = f'{scenario}-{mode}-{block}'
    before = audit.snapshot()
    if before['state'] != 'disabled':
        raise RuntimeError('another scheduler is active; refusing replacement')
    plan_path = out / f'{stem}.load-plan.txt'
    write_plan(plan_path, plan)
    children, workers, handles, pipes, waits = [], [], [], [], []
    scheduler = bench = release = None
    extra_env, pass_fds = {}, ()
    meta = dict(condition=scenario, implementation=mode, block=block, plan=plan,
                before=before, kernel=platform.release(), cpus=sorted(os.sched_getaffinity(0)),
                workers=0, requested_trials=plan['trials'], work_ms_per_stage=20,
                interval_ms=plan['interval_ms'], trace_delay=False,
                input_origin='synthetic-client-dispatch',
                primary_latency_origin='absolute planned input due time')
    started = time.monotonic_ns()

    def launch(command, raw_path, env=None, inherited=()):
        rfd, wfd = os.pipe()
        pipes.append(wfd)
        output = raw_path.open('x')
        error = raw_path.with_suffix('.stderr').open('x')
        handles.extend((output, error))
        try:
            p = subprocess.Popen(command(rfd), stdout=output, stderr=error, env=env(rfd) if env else None,
                                 pass_fds=(*inherited, rfd))
        finally:
            os.close(rfd)
        children.append(p)
        return p

    try:
        if mode in ('lavd', 'rusty'):
            log = (out / f'{stem}-{mode}.log').open('x')
            handles.append(log)
            scheduler = subprocess.Popen(['scx_' + mode], stdout=log, stderr=log)
            children.append(scheduler)
            audit.wait_enabled(scheduler)
        elif mode.startswith('prophet'):
            scheduler, extra_env, pass_fds, release = matrix.external_factory(mode)(out, stem)
            children.append(scheduler)
            meta['external_scheduler'] = True
        for role, key in ((0, 'batch_workers'), (1, 'periodic_workers')):
            for i in range(max(p[key] for p in plan['phases'])):
                path = out / f'{stem}-worker-{role}-{i}.jsonl'
                workers.append(launch(lambda fd, i=i, role=role: [str(LOAD), str(plan_path), str(i), str(role), str(fd)], path))
                waits.append((path, 'load_ready'))
        meta['workers'] = len(workers)
        env = {k: v for k, v in os.environ.items() if not k.startswith(('PULSESCHED_', 'PROPHET_'))}
        env.update(extra_env, PULSESCHED_AUTORUN_MS=str(plan['interval_ms']),
                   PULSESCHED_MAX_PROPAGATION_DEPTH='2', PROPHET_DURATION_NS=str(plan['duration_ns']))
        pulse = mode == 'pulse-off' or mode.startswith('prophet')
        command = [str(BENCH), '20', str(plan['trials']), '1000', '250' if pulse else '0',
                   'dep-off' if pulse else 'dep-normal']
        meta['command'] = command
        raw = out / f'{stem}.jsonl'
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        bench = launch(lambda fd: command, raw, lambda fd: {**env, 'PROPHET_START_FD': str(fd)}, pass_fds)
        waits.append((raw, 'transition_ready'))
        wait_ready(waits, children)
        epoch = time.monotonic_ns() + 2*NS
        meta['epoch_ns'] = epoch
        (out / f'{stem}-proc-before.txt').write_text(Path('/proc/stat').read_text())
        while pipes:
            fd = pipes.pop()
            try:
                os.write(fd, struct.pack('=Q', epoch))
            finally:
                os.close(fd)
        deadline = time.monotonic() + 2 + plan['duration_ns']/NS + 60
        while bench.poll() is None or any(p.poll() is None for p in workers):
            if scheduler and scheduler.poll() is not None:
                raise RuntimeError('scheduler exited during transition experiment')
            if bench.poll() not in (None, 0) or any(p.poll() not in (None, 0) for p in workers):
                raise RuntimeError('workload process failed')
            if time.monotonic() >= deadline:
                raise RuntimeError('workload watchdog expired; no arrivals discarded')
            time.sleep(.05)
        after_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        meta['all_workload_children_usage'] = {
            k: getattr(after_usage, k)-getattr(usage, k)
            for k in ('ru_utime', 'ru_stime', 'ru_nvcsw', 'ru_nivcsw')}
        (out / f'{stem}-proc-after.txt').write_text(Path('/proc/stat').read_text())
    except BaseException as exc:
        meta['error'] = str(exc)
        raise
    finally:
        for fd in pipes:
            os.close(fd)
        # Keep scheduler alive until workload processes have exited.
        if bench:
            meta['returncode'] = audit.stop_child(bench)
        meta['worker_returncodes'] = [audit.stop_child(p) for p in workers]
        if scheduler:
            meta['scheduler_returncode'] = audit.stop_child(scheduler)
        if release:
            release()
        for f in handles:
            f.close()
        meta['after'] = audit.snapshot()
        meta['elapsed_ns'] = time.monotonic_ns() - started
        (out / f'{stem}.meta.json').write_text(json.dumps(meta, indent=2)+'\n')
        audit.assert_fallback(before)
    report = {}
    try:
        report = validate_transition(out / f'{stem}.meta.json')
    except Exception as exc:
        report = {'status': 'FAIL', 'error': str(exc)}
        raise
    finally:
        (out / f'{stem}.validation.json').write_text(json.dumps(report, indent=2)+'\n')
    print(f'validated {stem}', flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--seed', type=int, default=20261020)
    ap.add_argument('--blocks', type=int, default=4)
    ap.add_argument('--scenarios', nargs='+', choices=['steps', 'bursts', 'mixed'], default=['steps', 'bursts', 'mixed'])
    ap.add_argument('--plan', action='store_true')
    ap.add_argument('--preflight-only', action='store_true')
    args = ap.parse_args()
    if not 1 <= args.blocks <= 32 or len(set(args.scenarios)) != len(args.scenarios):
        ap.error('blocks must be 1..32 and scenarios unique')
    if not BENCH.exists() or not LOAD.exists() or not all(shutil.which('scx_'+x) for x in ('lavd', 'rusty')):
        ap.error('build make -C benchmarks/wayland_interaction transitions; LAVD and Rusty required')
    if audit.snapshot()['state'] != 'disabled':
        ap.error('another scheduler is active')
    if not args.plan and (os.geteuid() != 0 or not all(os.getenv(k) for k in ('WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR'))):
        ap.error('requires privileged host and Wayland session environment')
    args.output.mkdir(parents=True, exist_ok=False)
    cpus = sorted(os.sched_getaffinity(0))
    scenarios = plans(len(cpus), args.seed)
    paths = [BENCH, LOAD, matrix.PROPHET, ROOT/'src/pulsesched/build/pulsesched',
             Path(shutil.which('scx_lavd')), Path(shutil.which('scx_rusty')),
             Path(__file__), ROOT/'scripts/prophet_matrix.py', ROOT/'scripts/audit_latency.py',
             ROOT/'scripts/analyze_prophet_transitions.py', ROOT/'benchmarks/wayland_interaction/benchmark.c',
             ROOT/'benchmarks/wayland_interaction/transition_load.c',
             ROOT/'benchmarks/wayland_interaction/external_scheduler.h',
             ROOT/'scripts/prophet_smoke.py', ROOT/'scripts/summarize_latency_audit.py',
             ROOT/'scripts/summarize_prophet.py', ROOT/'src/scx_prophet/control.h',
             ROOT/'src/scx_prophet/prophet.c', ROOT/'src/scx_prophet/prophet.bpf.c',
             ROOT/'src/scx_prophet/prophet.h']
    manifest = dict(version=1, seed=args.seed, cpus=cpus, kernel=platform.release(), modes=MODES,
                    plans={k: scenarios[k] for k in [*args.scenarios, 'preflight']},
                    schedule=order(args.blocks, args.scenarios, args.seed), blocks=args.blocks,
                    sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
                    analysis=dict(transition_window_ns=2*NS, recovery_threshold_ms=200,
                                  recovery_consecutive_trials=3, p95_candidate_reduction=.05,
                                  throughput_loss_limit=.03, bootstrap_seed=20261020,
                                  uncertainty_unit='paired complete blocks; descriptive with few blocks',
                                  timing_gate='adaptive must beat both static profiles; occupancy-matched intervention still required before claiming timing causality'),
                    periodic_eligibility=dict(period_ns=100_000_000, active_ns=20_000_000))
    (args.output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    for name in ('lavd', 'rusty'):
        version = subprocess.run(['scx_'+name, '--version'], capture_output=True, text=True, timeout=10)
        if version.returncode:
            raise RuntimeError(f'cannot identify {name}')
        (args.output/f'{name}-version.txt').write_text(version.stdout+version.stderr)
    if args.plan:
        seconds = sum(scenarios[x['scenario']]['duration_ns']/NS for x in manifest['schedule'])
        print(f'Plan only: {len(manifest["schedule"])} blocks, {seconds/60:.1f} workload minutes; no attachment')
        return
    try:
        # Fresh host verifier, lease and lifecycle gate, not an old PASS label.
        subprocess.run([sys.executable, str(ROOT/'scripts/prophet_smoke.py'),
                        '--output', str(args.output/'scheduler-smoke')], check=True)
        matrix.check_smoke(args.output/'scheduler-smoke')
        pre = args.output/'preflight'
        pre.mkdir()
        audit.smoke(pre)
        for mode in MODES:
            run_block(pre, 'preflight', mode, 0, scenarios['preflight'])
        if not args.preflight_only:
            for item in manifest['schedule']:
                run_block(args.output, item['scenario'], item['mode'], item['block'], scenarios[item['scenario']])
    except BaseException as exc:
        (args.output/'status.json').write_text(json.dumps({'status': 'FAIL', 'error': str(exc)})+'\n')
        raise
    (args.output/'status.json').write_text(json.dumps({'status': 'PASS', 'preflight_only': args.preflight_only})+'\n')


if __name__ == '__main__':
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    main()
