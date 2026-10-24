import copy
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prophet_transitions as experiment
from analyze_prophet_transitions import recovery, quantiles, bootstrap_mean, analyze_block, compare


class ProtocolTests(unittest.TestCase):
    def fixture(self, directory):
        d = Path(directory)
        plan = experiment.plans(1, 1)['preflight']
        epoch, stem = 100_000_000_000, 'preflight-linux-0'
        meta = dict(plan=plan, epoch_ns=epoch, before={'state': 'disabled', 'rejected': 0},
                    after={'state': 'disabled', 'rejected': 0}, requested_trials=6, workers=3,
                    worker_returncodes=[0, 0, 0], returncode=0, scheduler_returncode=0,
                    condition='preflight', implementation='linux', block=0, work_ms_per_stage=20,
                    all_workload_children_usage={})
        records = [dict(type='transition_epoch', epoch_ns=epoch, observed_ns=epoch-100),
                   dict(type='workload', version=2, work_clock='thread-cpu', stages=4,
                        work_ms_per_stage=20, requested_trials=6, input_origin='synthetic-client-dispatch',
                        partial=False, trace_delay=False)]
        for i in range(6):
            due = epoch+250_000_000+i*500_000_000
            records += [dict(type='planned_input', interaction_id=i+1, due_ns=due, dispatch_ns=due+5_000_000),
                        dict(type='trial', interaction_id=i+1, status='presented', input_ns=due+5_000_000,
                             work_start_ns=due+5_000_000, commit_ns=due+85_000_000, presentation_ns=due+100_000_000,
                             presentation_clock_id=1, work_to_commit_ns=80_000_000, commit_to_presentation_ns=15_000_000)]
            records += [dict(type='work_stage', interaction_id=i+1, worker=j, cpu_ns=20_000_000,
                             handoff_ns=1000) for j in range(4)]
        raw = d/(stem+'.jsonl')
        raw.write_text('\n'.join(map(json.dumps, records))+'\n')
        path = d/(stem+'.meta.json')
        path.write_text(json.dumps(meta))
        for role, index in ((0, 0), (0, 1), (1, 0)):
            rows = [dict(type='load_ready', periodic=role, index=index)]
            for i in range(3):
                start = epoch+i*experiment.NS
                rows += [dict(type='load_phase', phase=i, active=i == 1,
                              planned_start_ns=start, planned_end_ns=start+experiment.NS,
                              observed_start_ns=start+1000, observed_end_ns=start+experiment.NS+1000,
                              offered_periodic_windows=10 if role and i == 1 else 0,
                              periodic_windows_with_progress=10 if role and i == 1 else 0),
                         dict(type='load_bin', phase=i, periodic=role, index=index,
                              start_ns=start, end_ns=start+experiment.NS, iterations=1_000_000 if i == 1 else 0,
                              cpu_ns=1_000_000 if i == 1 else 0, max_progress_gap_ns=1_000_000 if i == 1 else 0)]
            rows += [dict(type='background', iterations=1_000_000, wall_ns=3*experiment.NS,
                          cpu_ns=1_000_000, max_batch_gap_ns=1_000_000), dict(type='load_exit', complete=True)]
            (d/f'{stem}-worker-{role}-{index}.jsonl').write_text('\n'.join(map(json.dumps, rows))+'\n')
        return path, raw, records

    def test_full_integrity_and_phase_analysis(self):
        with tempfile.TemporaryDirectory() as d:
            path, raw, records = self.fixture(d)
            self.assertEqual(experiment.validate_transition(path)['status'], 'PASS')
            report = analyze_block(path, dict(recovery_threshold_ms=200, recovery_consecutive_trials=3,
                                             transition_window_ns=2*experiment.NS))
            self.assertEqual(len(report['phases']), 3)
            self.assertEqual(report['phases'][1]['windows']['whole']['latency']['planned_to_presentation_ms']['p95'], 100)
            self.assertEqual(report['phases'][1]['windows']['whole']['iterations_per_second'], 3_000_000)
            self.assertFalse(report['phases'][1]['recovery']['recovered'])
            blocks = []
            for mode in experiment.MODES:
                block = copy.deepcopy(report)
                block['implementation'] = mode
                blocks.append(block)
            comparisons = compare(blocks, {'analysis': {'bootstrap_seed': 3}})
            self.assertEqual(len(comparisons), (3*3+1)*8)
            self.assertTrue(all(c['p95_delta_ms']['mean'] == 0 for c in comparisons if c['p95_delta_ms']))
            records[2]['due_ns'] += 1
            raw.write_text('\n'.join(map(json.dumps, records))+'\n')
            with self.assertRaisesRegex(RuntimeError, 'arrival schedule'):
                experiment.validate_transition(path)

    def test_work_in_inactive_phase_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path, _, _ = self.fixture(d)
            worker = next(Path(d).glob('*-worker-*.jsonl'))
            records = experiment.read_records(worker)
            first_bin = next(r for r in records if r['type'] == 'load_bin')
            first_bin['iterations'] = 1
            worker.write_text('\n'.join(map(json.dumps, records))+'\n')
            with self.assertRaisesRegex(RuntimeError, 'inactive phase'):
                experiment.validate_transition(path)

    def test_surface_suspension_is_not_a_successful_latency_run(self):
        with tempfile.TemporaryDirectory() as d:
            path, raw, records = self.fixture(d)
            records.append(dict(type='surface_state', suspended=True))
            raw.write_text('\n'.join(map(json.dumps, records))+'\n')
            with self.assertRaisesRegex(RuntimeError, 'surface suspension'):
                experiment.validate_transition(path)

    def test_seeded_plans_and_balanced_order(self):
        a = experiment.plans(16, 123)
        self.assertEqual(a, experiment.plans(16, 123))
        self.assertNotEqual(a['bursts'], experiment.plans(16, 124)['bursts'])
        phases = a['steps']['phases']
        self.assertEqual([p['batch_workers'] for p in phases], [0, 8, 32, 8, 0])
        schedule = experiment.order(8, ['steps'], 123)
        self.assertEqual(schedule, experiment.order(8, ['steps'], 123))
        for mode in experiment.MODES:
            positions = [i % len(experiment.MODES) for i, r in enumerate(schedule) if r['mode'] == mode]
            self.assertEqual(sorted(positions), list(range(len(experiment.MODES))))
        for plan in a.values():
            self.assertEqual(plan['duration_ns'], sum(p['duration_ns'] for p in plan['phases']))
            self.assertLess(plan['first_input_ns']+(plan['trials']-1)*plan['interval_ms']*1_000_000,
                            plan['duration_ns'])

    def test_recovery_is_censored_and_uses_completed_streak(self):
        rows = [{'due_ns': i*500_000_000, 'presentation_ns': i*500_000_000+100_000_000,
                 'planned_to_presentation_ms': 100} for i in range(5)]
        r = recovery(rows, 0, 3_000_000_000, 200, 3)
        self.assertTrue(r['recovered'])
        self.assertEqual(r['elapsed_ms'], 1100)
        rows[1]['planned_to_presentation_ms'] = 250
        self.assertEqual(recovery(rows, 0, 3_000_000_000, 200, 3)['elapsed_ms'], 2100)
        self.assertFalse(recovery(rows, 0, 2_000_000_000, 200, 3)['recovered'])
        self.assertIsNone(recovery([], 0, 1_000_000_000, 200, 3)['elapsed_ms'])

    def test_tail_support_and_block_uncertainty(self):
        self.assertIsNone(quantiles([])['p99'])
        self.assertEqual(quantiles([10, 20, 30, 40])['expected_upper_tail_observations']['p99'], .04)
        b = bootstrap_mean([-2, 1, 3, -1], 3)
        self.assertEqual(b['n_blocks'], 4)
        self.assertEqual(b, bootstrap_mean([-2, 1, 3, -1], 3))
        self.assertEqual(b['paired_block_differences'], [-2, 1, 3, -1])

    def test_readiness_rejects_exit_before_start(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'raw'
            path.write_text('{"type":"load_ready"}')  # incomplete line not an acknowledgement
            self.assertFalse(experiment.ready(path, 'load_ready'))
            path.write_text('{"type":"load_ready"}\n')
            self.assertTrue(experiment.ready(path, 'load_ready'))
            with patch.object(experiment, 'ready', return_value=False):
                process = subprocess.Popen([sys.executable, '-c', 'pass'])
                process.wait()
                with self.assertRaisesRegex(RuntimeError, 'exited'):
                    experiment.wait_ready([(path, 'load_ready')], [process], .1)


class NativeLoadTests(unittest.TestCase):
    def test_runner_failure_reaps_owned_workers_before_epoch(self):
        with tempfile.TemporaryDirectory() as d, \
             patch.object(experiment, 'BENCH', Path('/bin/false')), \
             patch.object(experiment.audit, 'snapshot', return_value={'state': 'disabled', 'rejected': 0}), \
             patch.object(experiment.audit, 'assert_fallback'):
            with self.assertRaisesRegex(RuntimeError, 'exited'):
                experiment.run_block(Path(d), 'preflight', 'linux', 0, experiment.plans(1, 1)['preflight'])
            meta = json.loads((Path(d)/'preflight-linux-0.meta.json').read_text())
            self.assertNotIn('epoch_ns', meta)
            self.assertEqual(meta['returncode'], 1)
            self.assertEqual(len(meta['worker_returncodes']), 3)
            self.assertTrue(all(rc is not None for rc in meta['worker_returncodes']))

    def test_real_worker_barrier_phases_bins_and_cancellation(self):
        self.assertTrue(experiment.LOAD.exists(), 'build transitions before running native tests')
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            plan = experiment.plans(1, 1)['preflight']
            experiment.write_plan(d/'plan', plan)
            read_fd, write_fd = os.pipe()
            with (d/'raw').open('w') as raw:
                p = subprocess.Popen([str(experiment.LOAD), str(d/'plan'), '0', '0', str(read_fd)],
                                     pass_fds=(read_fd,), stdout=raw)
                os.close(read_fd)
                try:
                    experiment.wait_ready([(d/'raw', 'load_ready')], [p], 2)
                    epoch = time.monotonic_ns()+100_000_000
                    os.write(write_fd, struct.pack('=Q', epoch))
                    os.close(write_fd); write_fd = None
                    self.assertEqual(p.wait(timeout=6), 0)
                finally:
                    if write_fd is not None:
                        os.close(write_fd)
                    experiment.audit.stop_child(p)
            rs = experiment.read_records(d/'raw')
            phases = [r for r in rs if r['type'] == 'load_phase']
            bins = [r for r in rs if r['type'] == 'load_bin']
            self.assertEqual(len(phases), 3)
            self.assertEqual([r['active'] for r in phases], [False, True, False])
            self.assertEqual([r['iterations'] > 0 for r in bins], [False, True, False])
            self.assertTrue(all(r['observed_start_ns'] >= r['planned_start_ns'] for r in phases))
            self.assertTrue(rs[-1]['complete'])
            # An owned worker waiting at a barrier must leave on pipe EOF.
            rfd, wfd = os.pipe()
            try:
                p = subprocess.Popen([str(experiment.LOAD), str(d/'plan'), '0', '0', str(rfd)],
                                     pass_fds=(rfd,), stdout=subprocess.DEVNULL)
            finally:
                os.close(rfd); os.close(wfd)
            self.assertNotEqual(p.wait(timeout=2), 0)


if __name__ == '__main__':
    unittest.main()
