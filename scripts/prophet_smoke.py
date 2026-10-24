#!/usr/bin/env python3
"""Fail-closed privileged Phase-1 gate. No performance or novelty claims.

Uses only owned children; refuses to replace an active scheduler. Every variant
must pass before later performance work. Raw files live in a fresh directory.
"""
import argparse
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import time

from audit_latency import snapshot, wait_enabled, assert_fallback, stop_child

ROOT = Path(__file__).resolve().parents[1]
PROPHET = ROOT / 'src/scx_prophet/build/scx_prophet'
LOAD = ROOT / 'benchmarks/wayland_interaction/build/pulse-load'


class Epoch(C.Structure):
    _fields_ = [(k, C.c_uint64) for k in (
        'epoch_id', 'start_ns', 'end_ns', 'deadline_ns', 'boosted_runtime_ns',
        'last_run_start_ns', 'normal_enqueues', 'urgent_enqueues', 'urgent_dispatches')]
    _fields_ += [(k, C.c_uint32) for k in (
        'active', 'termination_reason', 'inherited', 'depth', 'parent_pid', 'direct', 'root_pid')]


class Maps:
    def __init__(self, attached):
        self.lib = C.CDLL('libbpf.so.1', use_errno=True)
        self.lib.bpf_map_get_fd_by_id.argtypes = [C.c_uint32]
        self.lib.bpf_map_get_fd_by_id.restype = C.c_int
        self.lib.bpf_map_lookup_elem.argtypes = [C.c_int, C.c_void_p, C.c_void_p]
        self.lib.bpf_map_update_elem.argtypes = [C.c_int, C.c_void_p, C.c_void_p, C.c_uint64]
        self.fds = []
        try:
            for name in ('task_map_id', 'telemetry_map_id', 'metrics_map_id'):
                fd = self.lib.bpf_map_get_fd_by_id(attached[name])
                if fd < 0:
                    raise OSError(C.get_errno(), 'map_get_fd_by_id')
                self.fds.append(fd)
        except BaseException:
            self.close()
            raise
        self.ncpu = attached['possible_cpus']

    def close(self):
        for fd in self.fds:
            os.close(fd)
        self.fds = []

    def renew(self, pid, generation):
        now = time.monotonic_ns()
        epoch = Epoch(epoch_id=generation, start_ns=now,
                      deadline_ns=now + 1_000_000_000, active=1, direct=1, root_pid=pid)
        key = C.c_uint32(pid)
        if self.lib.bpf_map_update_elem(self.fds[0], C.byref(key), C.byref(epoch), 0):
            raise OSError(C.get_errno(), 'epoch renewal')

    def counters(self):
        values = (C.c_uint64 * (73 * self.ncpu))()
        key = C.c_uint32(0)
        if self.lib.bpf_map_lookup_elem(self.fds[1], C.byref(key), values):
            raise OSError(C.get_errno(), 'telemetry lookup')
        return [sum(values[cpu * 73 + j] for cpu in range(self.ncpu)) for j in range(73)]

    def urgent_runs(self):
        values = (C.c_uint64 * self.ncpu)()
        key = C.c_uint32(1)  # PULSE_METRIC_URGENT_DISPATCH
        if self.lib.bpf_map_lookup_elem(self.fds[2], C.byref(key), values):
            raise OSError(C.get_errno(), 'metrics lookup')
        return sum(values)


def variants():
    return [
        ('observe', [], None),
        ('static-1', ['--mode', 'static', '--profile', '1'], None),
        ('static-2', ['--mode', 'static', '--profile', '2'], None),
        ('adaptive', ['--mode', 'adaptive'], None),
        ('no-telemetry', ['--mode', 'static', '--no-telemetry'], None),
        ('propagation', ['--propagation'], None),
        ('freeze', ['--mode', 'static', '--profile', '2', '--freeze-after-ms', '1000'], 'freeze'),
        ('sigstop', ['--mode', 'static', '--profile', '2'], 'stop'),
        ('sigkill', [], 'kill'),
    ]


def read_attached(path):
    for line in path.read_text().splitlines():
        if line.startswith('{'):
            record = json.loads(line)
            if record.get('type') == 'attached':
                return record
    raise RuntimeError('attachment log missing map IDs')


def validate_counters(name, before, after):
    if name == 'no-telemetry':
        if any(after):
            raise RuntimeError('telemetry-disable path still counts events')
        return
    if sum(after[:32]) == 0 or sum(after[32:64]) == 0 or after[70] == 0:
        raise RuntimeError('enqueue/runnable/runtime accounting did not execute')
    if name in ('static-1', 'static-2') and after[64 + int(name[-1])] == 0:
        raise RuntimeError('requested profile did not reach enqueue')
    if name in ('freeze', 'sigstop'):
        if after[67] <= before[67] or after[64] <= before[64]:
            raise RuntimeError('lease expiry did not select baseline while attached')
        if after[66] != before[66]:
            raise RuntimeError('expired controller continued to select 5ms slices')


def run_variant(out, name, flags, fault, cpu):
    before = snapshot()
    if before['state'] != 'disabled':
        raise RuntimeError('another scheduler is active')
    workers, handles = [], []
    scheduler = maps = None
    stopped = False
    record = {'name': name, 'flags': flags, 'cpu': cpu, 'before': before}
    try:
        raw = (out / f'{name}.jsonl').open('x')
        err = (out / f'{name}.stderr').open('x')
        handles += [raw, err]
        scheduler = subprocess.Popen([str(PROPHET), *flags], stdout=raw, stderr=err)
        wait_enabled(scheduler)
        # Kernel enabled and userspace attached line are not simultaneous.
        deadline = time.monotonic() + 2
        while True:
            try:
                attached = read_attached(out / f'{name}.jsonl')
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.02)
        maps = Maps(attached)
        for i in range(2):
            f = (out / f'{name}-worker-{i}.jsonl').open('x')
            handles.append(f)
            workers.append(subprocess.Popen(['taskset', '-c', str(cpu), str(LOAD)], stdout=f))
        start = time.monotonic()
        generation = 0
        lease_before = None
        while time.monotonic() - start < 5:
            elapsed = time.monotonic() - start
            if scheduler.poll() is not None:
                raise RuntimeError('scheduler exited during gate')
            if any(w.poll() is not None for w in workers):
                raise RuntimeError('worker exited during gate')
            generation += 1
            maps.renew(workers[0].pid, generation)  # sustained direct urgency
            if fault == 'stop' and elapsed >= 1 and not stopped:
                scheduler.send_signal(signal.SIGSTOP)
                stopped = True
            # By 3.5s, freeze and SIGSTOP leases have expired for >1 second.
            if fault in ('stop', 'freeze') and elapsed >= 3.5 and lease_before is None:
                lease_before = maps.counters()
            time.sleep(.1)
        counts = maps.counters()
        validate_counters(name, lease_before or [0] * 73, counts)
        if maps.urgent_runs() == 0:
            raise RuntimeError('persistent direct epoch never ran urgently')
        record.update(counters=counts, lease_before=lease_before, urgent_runs=maps.urgent_runs())
        if fault == 'kill':
            scheduler.kill()
            scheduler.wait(timeout=10)
        if stopped:
            scheduler.send_signal(signal.SIGCONT)
            stopped = False
    finally:
        for worker in workers:
            stop_child(worker)
        if scheduler:
            if stopped:
                scheduler.send_signal(signal.SIGCONT)
            record['scheduler_returncode'] = stop_child(scheduler)
        if maps:
            maps.close()
        for f in handles:
            f.close()
        record['after'] = assert_fallback(before)
        (out / f'{name}.meta.json').write_text(json.dumps(record, indent=2) + '\n')
    expected_rc = -signal.SIGKILL if fault == 'kill' else 0
    if record['scheduler_returncode'] != expected_rc:
        raise RuntimeError('scheduler exited abnormally')
    reports = [json.loads((out / f'{name}-worker-{i}.jsonl').read_text()) for i in range(2)]
    if any(w.returncode or r['iterations'] <= 0 for w, r in zip(workers, reports)):
        raise RuntimeError('worker made no progress or failed')
    fraction = reports[0]['cpu_ns'] / sum(r['cpu_ns'] for r in reports)
    if not .2 < fraction < .8:
        raise RuntimeError(f'urgent/normal CPU share outside smoke bounds: {fraction}')
    print(f'{name}: passed attach, executed accounting, progress, selected fault, detach', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = {'kernel': platform.release(), 'before': snapshot(),
                'cpus': sorted(os.sched_getaffinity(0)), 'epoch_abi_bytes': C.sizeof(Epoch),
                'sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in (PROPHET, LOAD)}}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    try:
        if os.geteuid() != 0:
            raise RuntimeError('privileged host required; no scheduler was loaded')
        for name, flags, fault in variants():
            run_variant(args.output, name, flags, fault, manifest['cpus'][-1])
    except Exception as exc:
        (args.output / 'status.json').write_text(json.dumps({'status': 'FAILED_OR_BLOCKED', 'error': str(exc)}) + '\n')
        raise
    (args.output / 'status.json').write_text(json.dumps({'status': 'PASS', 'scope': 'phase1 smoke only'}) + '\n')


if __name__ == '__main__':
    main()
