#!/usr/bin/env python3
"""Short live review demo. No scheduler changes; no historical data substitution."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

import audit_latency as audit
import prophet_matrix as matrix
from summarize_latency_audit import read_records, summarize
from demo_review_dashboard import render

ROOT = audit.ROOT
MODES = [('linux', 'Linux'), ('lavd', 'LAVD'), ('pulse-off', 'PulseSched'),
         ('prophet-static-2', 'Prophet · 5 ms'), ('prophet-adaptive', 'Prophet · adaptive')]
TRIALS = 32

def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')

def empty_report():
    return {'arms': [dict(mode=m, label=n, status='OMITTED', reason='Not run', valid=0,
                          expected=TRIALS) for m, n in MODES], 'cleanup': False}

def preflight():
    if audit.snapshot()['state'] != 'disabled':
        raise RuntimeError('Another sched_ext scheduler is active; refusing to replace it. Stop it yourself before the demo.')
    for key in ('WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR'):
        if not os.getenv(key):
            raise RuntimeError(f'{key} is missing; run from your Wayland desktop terminal.')
    display = Path(os.environ['WAYLAND_DISPLAY'])
    if not display.is_absolute():
        display = Path(os.environ['XDG_RUNTIME_DIR']) / display
    # Connect and close without creating a surface or dispatching any workload.
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(1)
        sock.connect(str(display))
    frozen = json.loads((ROOT / 'results/prophet-matrix02-analysis-20261020/snapshot-sha256.json').read_text())
    for binary in (matrix.BENCH, matrix.PROPHET, audit.LOAD):
        if not os.access(binary, os.X_OK):
            raise RuntimeError(f'Missing executable: {binary}. No automatic rebuild during review.')
        key = str(binary.relative_to(ROOT))
        if hashlib.sha256(binary.read_bytes()).hexdigest() != frozen[key]:
            raise RuntimeError(f'Frozen executable changed: {key}')
    matrix.check_smoke(ROOT / 'results/prophet-host-smoke-01')
    return audit.snapshot()

def arm_result(out, arm):
    meta_path = out / f'heavy-{arm["mode"]}-0.meta.json'
    if not meta_path.exists():
        raise RuntimeError('Benchmark metadata missing')
    summary, series = summarize(meta_path)
    arm.update(valid=summary['latency']['client_to_presentation_ms']['n'],
               counts=summary['statuses'], missing=summary['missing'],
               invalid=summary['invalid_presentations'], protocol_errors=summary['protocol_errors'])
    # Only a fully validated arm receives charts and throughput.
    matrix.validate_block(meta_path)
    records = read_records(meta_path.with_name(meta_path.name.replace('.meta.json', '.jsonl')))
    if summary['ignored_inputs'] or any(r.get('type') == 'surface_state' and r.get('suspended') for r in records):
        raise RuntimeError('Unexpected input or suspended benchmark surface')
    meta = json.loads(meta_path.read_text())
    if meta['after'] != meta['before'] or meta['after']['state'] != 'disabled':
        raise RuntimeError('Scheduler state/rejection count did not return to baseline')
    for p in out.glob(meta_path.name.replace('.meta.json', '-worker-*.jsonl')):
        rows = read_records(p)
        if len(rows) != 1 or rows[0].get('iterations', 0) <= 0 or rows[0].get('cpu_ns', 0) <= 0:
            raise RuntimeError('Background worker did not make valid progress')
    arm.update(latency=summary['latency']['client_to_presentation_ms'],
               stages={k: sum(series[k])/len(series[k]) for k in ('work_to_commit_ms', 'commit_to_presentation_ms')},
               throughput=summary['background_iterations_per_second']/1e9,
               duration_s=meta['elapsed_ns']/1e9)

def run_owned(out, mode, workers, options):
    """Do not interrupt the existing runner once owned-child cleanup starts."""
    original = audit.stop_child
    saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM)}
    def protected_stop(process):
        signal.setitimer(signal.ITIMER_REAL, 0)
        for signum in saved:
            signal.signal(signum, signal.SIG_IGN)
        return original(process)
    audit.stop_child = protected_stop
    try:
        audit.run_one(out, 'heavy', workers, mode, 0, options,
                      external=matrix.external_factory(mode) if mode in matrix.PROFILES else None,
                      bench_path=matrix.BENCH)
    finally:
        audit.stop_child = original
        for signum, handler in saved.items():
            signal.signal(signum, handler)
        # Rearm only before another block, never in the middle of cleanup.
        signal.setitimer(signal.ITIMER_REAL, 0)


def worker(out, deadline):
    os.umask(0o022)
    report = empty_report()
    started = time.monotonic()
    initial = None
    interrupted = False
    def cancel(signum, frame):
        # A single interruption unwinds through run_one's owned-child cleanup.
        signal.setitimer(signal.ITIMER_REAL, 0)
        for s in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM):
            signal.signal(s, signal.SIG_IGN)
        raise KeyboardInterrupt(f'Demo deadline/interruption (signal {signum})')
    for s in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM):
        signal.signal(s, cancel)
    signal.setitimer(signal.ITIMER_REAL, max(.01, deadline-time.monotonic()))
    try:
        initial = preflight()
        for key in list(os.environ):
            if key.startswith(('PULSESCHED_', 'PROPHET_')):
                del os.environ[key]
        workers = 2*len(os.sched_getaffinity(0))
        report.update(workers=workers, cpus=len(os.sched_getaffinity(0)), trials=TRIALS,
                      work='4 × 20 ms thread-CPU stages; 200 ms completion-relative interval',
                      warmup='1 second background warmup; all 32 trials retained', initial=initial)
        for index, arm in enumerate(report['arms']):
            remaining = deadline-time.monotonic()
            if remaining < 8:
                arm['reason'] = 'Omitted: remaining deadline budget too short'
                continue
            if arm['mode'] == 'lavd' and not shutil.which('scx_lavd'):
                arm.update(status='FAILED', reason='scx_lavd executable not found')
                continue
            print(f'[{index+1}/5] {arm["label"]} — {workers} workers, {TRIALS} trials', flush=True)
            arm.update(status='FAILED', reason='Interrupted or incomplete')
            options = SimpleNamespace(trials=TRIALS, work_ms=20, interval_ms=200,
                                      trace_delay=False, timeout=min(19, max(1, remaining-3)))
            try:
                signal.setitimer(signal.ITIMER_REAL, max(.01, deadline-time.monotonic()))
                run_owned(out, arm['mode'], workers, options)
                arm_result(out, arm)
                arm.update(status='PASS', reason='All trials and worker/lifecycle checks passed')
            except BaseException as exc:
                arm.update(status='FAILED', reason=str(exc) or type(exc).__name__)
                try:
                    arm_result(out, arm)
                except Exception as validation_error:
                    arm['validation_error'] = str(validation_error)
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    interrupted = True
                    break
            finally:
                save(out / 'live-report.json', report)
            if audit.snapshot() != initial:
                raise RuntimeError('State changed after cleanup; no further scheduler will be started')
    except BaseException as exc:
        report['error'] = str(exc) or type(exc).__name__
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        try:
            report['final'] = audit.snapshot()
            report['cleanup'] = initial is not None and report['final'] == initial
        except Exception as exc:
            report['cleanup_error'] = str(exc)
        report.update(measurement_s=time.monotonic()-started, interrupted=interrupted)
        save(out / 'live-report.json', report)
    return 0 if report['cleanup'] and all(a['status']=='PASS' for a in report['arms']) else 1

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--deadline', type=float, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if os.geteuid() != 0 or args.deadline is None:
            parser.error('Internal worker requires sudo and a deadline')
        return worker(args.worker, args.deadline)
    if os.geteuid() == 0:
        print('Run ./scripts/demo_review.fish as your desktop user; sudo is handled internally.', file=sys.stderr)
        return 2
    started = time.monotonic()
    out = ROOT / 'results' / (time.strftime('demo-review-%Y%m%d-%H%M%S') + f'-{os.getpid()}')
    out.mkdir(parents=True)
    report = empty_report()
    process = None
    def interrupt(signum, frame):
        # Keep the unprivileged presenter alive to render failure evidence after cleanup.
        if process and process.poll() is None:
            process.send_signal(signal.SIGINT)
    try:
        preflight()
        binaries = [matrix.BENCH, matrix.PROPHET, audit.LOAD]
        if shutil.which('scx_lavd'):
            binaries.append(Path(shutil.which('scx_lavd')))
        sources = [Path(__file__), ROOT/'scripts/demo_review_dashboard.py', ROOT/'scripts/demo_review.fish']
        save(out/'manifest.json', {'kernel': os.uname().release, 'cpus': sorted(os.sched_getaffinity(0)),
             'order': [m for m, _ in MODES], 'trials_per_arm': TRIALS, 'warmup_seconds': 1,
             'work_ms_per_stage': 20, 'stages': 4, 'interval_ms': 200, 'worker_deadline_s': 105,
             'total_budget_s': 120, 'cleanup_may_exceed_budget': True,
             'sha256': {str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p):
                        hashlib.sha256(p.read_bytes()).hexdigest() for p in binaries+sources}})
        if not shutil.which('sudo'):
            raise RuntimeError('sudo is not installed')
        print('PulseSched live review • about 60–90 seconds • deadline 120 seconds\nKeep the benchmark window visible; do not click/type into it.', flush=True)
        # Authentication is part of the total budget, not an unbounded prelude.
        subprocess.run(['sudo', '-v'], check=True, timeout=20)
        env_args = [f'{k}={os.environ[k]}' for k in ('WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR')]
        signal.signal(signal.SIGINT, interrupt)
        signal.signal(signal.SIGTERM, interrupt)
        process = subprocess.Popen(['sudo', '-n', 'env', *env_args, sys.executable,
                                    str(Path(__file__).resolve()), '--worker', str(out),
                                    '--deadline', str(started+105)])
        process.wait()  # worker deadline unwinds cleanup; never kill the cleanup owner
        if (out/'live-report.json').exists():
            report = json.loads((out/'live-report.json').read_text())
        else:
            report['error'] = f'Worker exited without report: {process.returncode}'
    except BaseException as exc:
        report['error'] = str(exc) or type(exc).__name__
        report['preflight_failed'] = True
    report.update(total_s=time.monotonic()-started, output=str(out))
    save(out/'report.json', report)
    html = out/'index.html'
    render(report, out, html)
    latest = ROOT/'results/demo-latest.html'
    temporary = latest.with_suffix('.tmp')
    shutil.copyfile(html, temporary)
    temporary.replace(latest)
    print(f'\nDashboard: {html}\nLatest: {latest}', flush=True)
    if report.get('error'):
        print(f'Demo issue: {report["error"]}', file=sys.stderr)
    if not report['cleanup'] and not report.get('preflight_failed'):
        print('Scheduler restoration not confirmed; inspect live-report.json. Browser opening withheld.', file=sys.stderr)
    elif shutil.which('xdg-open'):
        try:
            with (out/'browser.log').open('w') as log:
                subprocess.Popen(['xdg-open', str(html)], stdout=log, stderr=log, start_new_session=True)
        except OSError as exc:
            print(f'Browser could not open: {exc}. Open {html}', file=sys.stderr)
    return 0 if report['cleanup'] and all(a['status']=='PASS' for a in report['arms']) else 1

if __name__ == '__main__':
    raise SystemExit(main())
