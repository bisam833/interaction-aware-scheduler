#!/usr/bin/env python3
"""Matched, rotating-block comparison. Run from the logged-in Wayland session.
Raw artifacts are exclusive-created; no old results are pooled or overwritten.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / 'benchmarks/wayland_interaction/build/pulsesched-wayland-bench'
LOAD = BENCH.with_name('pulse-load')
STATE = Path('/sys/kernel/sched_ext/state')
REJECTED = Path('/sys/kernel/sched_ext/nr_rejected')


def snapshot():
    return {'state': STATE.read_text().strip(), 'rejected': int(REJECTED.read_text())}


def stop_child(p):
    if p.poll() is None:
        p.send_signal(signal.SIGTERM)
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()  # only the process created by this runner
            p.wait()
    return p.returncode


def wait_enabled(p):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if p.poll() is not None:
            raise RuntimeError(f'scheduler exited before attach: {p.returncode}')
        if snapshot()['state'] == 'enabled':
            return
        time.sleep(.05)
    raise RuntimeError('scheduler attachment timed out')


def assert_fallback(before):
    deadline = time.monotonic() + 5
    while snapshot()['state'] != 'disabled' and time.monotonic() < deadline:
        time.sleep(.05)
    after = snapshot()
    if after['state'] != 'disabled' or after['rejected'] != before['rejected']:
        raise RuntimeError(f'fallback/rejection check failed: {before} -> {after}')
    return after


def smoke_variants(trace_delay=False, partial=False):
    common = ['--trace-delay'] if trace_delay else []
    variants = [('off', common), ('on', [*common, '--propagation'])]
    if partial:
        variants.append(('partial', [*common, '--propagation', '--partial']))
    return variants


def smoke(out, *, trace_delay=False, partial=False):
    before = snapshot()
    if before['state'] != 'disabled':
        raise RuntimeError('another sched_ext scheduler is active; leave it untouched')
    # rodata constants prune verifier paths: success with propagation disabled
    # says nothing about enabled propagation. Load every selected configuration.
    for name, flags in smoke_variants(trace_delay, partial):
        log_path = out / f'attach-{name}.log'
        with log_path.open('x') as log:
            p = subprocess.Popen([str(ROOT / 'src/pulsesched/build/pulsesched'), *flags],
                                 stdout=log, stderr=log)
            try:
                wait_enabled(p)
            except RuntimeError as error:
                raise RuntimeError(f'{name} preflight failed; see {log_path}: {error}') from error
            finally:
                rc = stop_child(p)
                assert_fallback(before)
            if rc:
                raise RuntimeError(f'{name} attach/detach failed ({rc}); see {log_path}')
    for name, args, expected in [('stale', ['--stale'], 'active=0 reason=2'),
                                 ('ttl', ['100', '20', '2'], 'active=0 reason=2')]:
        p = subprocess.run([str(ROOT / 'src/pulsesched/build/pulsesched_ctl'), *args],
                           capture_output=True, text=True, timeout=30)
        (out / f'{name}.log').write_text(p.stdout + p.stderr)
        if p.returncode or expected not in p.stdout:
            raise RuntimeError(f'{name} validation failed; see {name}.log')
        assert_fallback(before)
    print('Smoke passed: propagation off/on (and selected ablations), verifier/load, attach, '
          'TTL/stale, detach and fallback state.', flush=True)


def run_one(out, condition, workers, implementation, block, args, *, external=None, bench_path=None):
    stem = f'{condition}-{implementation}-{block}'
    before = snapshot()
    if before['state'] != 'disabled':
        raise RuntimeError('another scheduler is active; refusing to replace it')
    env = os.environ.copy()
    for key in ('PULSESCHED_PARTIAL', 'PULSESCHED_TRACE_DELAY', 'PULSESCHED_MAX_PROPAGATION_DEPTH'):
        env.pop(key, None)
    for key in ('PROPHET_TASK_FD', 'PROPHET_METRICS_FD', 'PROPHET_PID_FD'):
        env.pop(key, None)
    env['PULSESCHED_AUTORUN_MS'] = str(args.interval_ms)
    env['PULSESCHED_MAX_PROPAGATION_DEPTH'] = '2'
    if args.trace_delay:
        env['PULSESCHED_TRACE_DELAY'] = '1'
    if implementation == 'pulse-partial':
        env['PULSESCHED_PARTIAL'] = '1'
    pulse = implementation.startswith('pulse') or external is not None
    mode = ('dep-off' if implementation == 'pulse-off' or external else 'dep-on') if pulse else 'dep-normal'
    cmd = [str(bench_path or BENCH), str(args.work_ms), str(args.trials), '1000', '250' if pulse else '0', mode]
    meta = {'condition': condition, 'workers': workers, 'implementation': implementation,
            'block': block, 'command': cmd, 'kernel': platform.release(),
            'cpus': sorted(os.sched_getaffinity(0)), 'before': before,
            'trace_delay': args.trace_delay, 'workload_version': 2,
            'requested_trials': args.trials, 'work_ms_per_stage': args.work_ms,
            'input_origin': 'synthetic-client-dispatch', 'interval_ms': args.interval_ms}
    procs, handles = [], []
    scheduler = bench = None
    release_external = None
    pass_fds = ()
    start = time.monotonic_ns()
    try:
        # Background workload is the same executable and worker count in every mode.
        for i in range(workers):
            f = (out / f'{stem}-worker-{i}.jsonl').open('x')
            handles.append(f)
            procs.append(subprocess.Popen([str(LOAD)], stdout=f))
        if implementation in ('lavd', 'rusty'):
            log = (out / f'{stem}-{implementation}.log').open('x')
            handles.append(log)
            scheduler = subprocess.Popen(['scx_' + implementation], stdout=log, stderr=log)
            wait_enabled(scheduler)
        if external:
            scheduler, extra_env, pass_fds, release_external = external(out, stem)
            env.update(extra_env)
            meta['external_scheduler'] = True
        time.sleep(1)
        (out / f'{stem}-proc-before.txt').write_text(Path('/proc/stat').read_text())
        with (out / f'{stem}.jsonl').open('x') as raw, (out / f'{stem}.stderr').open('x') as err:
            usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
            bench = subprocess.Popen(cmd, stdout=raw, stderr=err, env=env, pass_fds=pass_fds)
            deadline = time.monotonic() + args.timeout
            while bench.poll() is None:
                if scheduler and scheduler.poll() is not None:
                    raise RuntimeError('external scheduler exited during measurement')
                if time.monotonic() >= deadline:
                    raise RuntimeError('benchmark watchdog expired; preserve incomplete trials')
                time.sleep(.1)
            meta['returncode'] = bench.returncode
            usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
            meta['benchmark_usage'] = {k: getattr(usage_after, k) - getattr(usage_before, k)
                                       for k in ('ru_utime', 'ru_stime', 'ru_nvcsw', 'ru_nivcsw')}
            meta['sched_schedstats'] = Path('/proc/sys/kernel/sched_schedstats').read_text().strip()
            if bench.returncode:
                raise RuntimeError(f'benchmark failed: {stem} rc={bench.returncode}')
        (out / f'{stem}-proc-after.txt').write_text(Path('/proc/stat').read_text())
    except BaseException as error:
        meta['error'] = str(error)
        raise
    finally:
        if bench:
            meta['returncode'] = stop_child(bench)
        if scheduler:
            meta['scheduler_returncode'] = stop_child(scheduler)
        if release_external:
            release_external()
        for p in procs:
            stop_child(p)
        for f in handles:
            f.close()
        meta['elapsed_ns'] = time.monotonic_ns() - start
        meta['after'] = snapshot()
        (out / f'{stem}.meta.json').write_text(json.dumps(meta, indent=2) + '\n')
        assert_fallback(before)
    print(f'processes finished {stem}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--smoke', action='store_true', help='only verifier/lifecycle checks, no GUI/load')
    parser.add_argument('--trials', type=int, default=30)
    parser.add_argument('--blocks', type=int, default=4)
    parser.add_argument('--work-ms', type=int, default=20, help='CPU milliseconds per worker; four workers')
    parser.add_argument('--interval-ms', type=int, default=200)
    parser.add_argument('--timeout', type=int, default=180, help='watchdog seconds per block; failures retained')
    parser.add_argument('--trace-delay', action='store_true', help='separate observer-on ablation')
    parser.add_argument('--partial-ablation', action='store_true', help='add known problematic mixed-class policy')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if min(args.trials, args.blocks, args.work_ms, args.interval_ms, args.timeout) <= 0:
        parser.error('numeric arguments must be positive')
    if os.geteuid() != 0:
        parser.error('run via sudo from the Wayland session; BPF needs host privileges')
    if not args.smoke and not all(os.environ.get(k) for k in ('WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR')):
        parser.error('WAYLAND_DISPLAY and XDG_RUNTIME_DIR are required')
    out = args.output or ROOT / 'results' / ('audit-' + time.strftime('%Y%m%d-%H%M%S'))
    out.mkdir(parents=True, exist_ok=False)
    manifest = {'kernel': platform.uname()._asdict(), 'cpus': sorted(os.sched_getaffinity(0)),
                'clock_ticks': os.sysconf('SC_CLK_TCK'),
                'binaries_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in (BENCH, LOAD, ROOT / 'src/pulsesched/build/pulsesched')},
                'arguments': {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    smoke(out, trace_delay=args.trace_delay, partial=args.partial_ablation)  # fail closed
    if args.smoke:
        return
    modes = ['linux', 'pulse-off', 'pulse-on']
    if shutil.which('scx_lavd'):
        modes.insert(1, 'lavd')
        v = subprocess.run(['scx_lavd', '--version'], capture_output=True, text=True)
        (out / 'lavd-version.txt').write_text(v.stdout + v.stderr)
    else:
        (out / 'lavd-unavailable.txt').write_text('scx_lavd executable not found\n')
    if args.partial_ablation:
        modes.append('pulse-partial')
    ncpu = len(os.sched_getaffinity(0))
    # Match old ncpu-1 separately: "heavy" must actually oversubscribe the machine.
    conditions = [('idle', 0), ('moderate', max(1, ncpu // 2)), ('heavy', 2 * ncpu)]
    for block in range(args.blocks):
        rotated_conditions = conditions[block % 3:] + conditions[:block % 3]
        rotated_modes = modes[block % len(modes):] + modes[:block % len(modes)]
        for condition, workers in rotated_conditions:
            for mode in rotated_modes:
                run_one(out, condition, workers, mode, block, args)
    print(f'Artifacts: {out}')


if __name__ == '__main__':
    # Raise through finally blocks so only owned processes are stopped.
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    main()
