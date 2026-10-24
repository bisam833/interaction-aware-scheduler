import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prophet_matrix as matrix


class MatrixTests(unittest.TestCase):
    def fixture(self, directory):
        path = Path(directory) / 'idle-prophet-observe-0.meta.json'
        meta = {'condition': 'idle', 'implementation': 'prophet-observe', 'block': 0,
                'requested_trials': 1, 'work_ms_per_stage': 20, 'workers': 0,
                'returncode': 0, 'scheduler_returncode': 0, 'external_scheduler': True}
        records = [
            {'type': 'workload', 'version': 2, 'stages': 4, 'work_clock': 'thread-cpu',
             'work_ms_per_stage': 20, 'requested_trials': 1, 'input_origin': 'synthetic-client-dispatch',
             'partial': False, 'trace_delay': False},
            {'type': 'scheduler', 'mode': 'scx-prophet-external'},
            {'type': 'trial', 'interaction_id': 1, 'status': 'presented',
             'input_ns': 1000000, 'work_start_ns': 2000000, 'commit_ns': 83000000,
             'presentation_ns': 100000000, 'presentation_clock_id': 1,
             'epoch_start_ns': 1000000, 'epoch_termination_ns': 101000000,
             'work_to_commit_ns': 81000000, 'commit_to_presentation_ns': 17000000}]
        records += [{'type': 'work_stage', 'worker': i, 'interaction_id': 1,
                     'cpu_ns': 20000000, 'handoff_ns': 1000} for i in range(4)]
        path.write_text(json.dumps(meta))
        raw = path.with_name(path.name.replace('.meta.json', '.jsonl'))
        raw.write_text('\n'.join(map(json.dumps, records)))
        controller = path.with_name(path.name.replace('.meta.json', '-controller.jsonl'))
        controller.write_text(json.dumps({'type': 'attached', 'abi': 1})+'\n'+
                              json.dumps({'type': 'exit', 'uei_code': 0})+'\n')
        return path, raw, records

    def test_valid_block_and_no_duplicate_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            path, raw, records = self.fixture(directory)
            self.assertEqual(matrix.validate_block(path)['statuses']['presented'], 1)
            records[-1]['worker'] = 0
            raw.write_text('\n'.join(map(json.dumps, records)))
            with self.assertRaisesRegex(RuntimeError, 'unequal work'):
                matrix.validate_block(path)

    def test_no_silent_epoch_or_controller_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path, raw, records = self.fixture(directory)
            records[2]['epoch_start_ns'] = 0
            raw.write_text('\n'.join(map(json.dumps, records)))
            with self.assertRaisesRegex(RuntimeError, 'epoch'):
                matrix.validate_block(path)
            records[2]['epoch_start_ns'] = 1000000
            raw.write_text('\n'.join(map(json.dumps, records)))
            meta = json.loads(path.read_text()); meta['scheduler_returncode'] = 1
            path.write_text(json.dumps(meta))
            with self.assertRaisesRegex(RuntimeError, 'failed/incomplete'):
                matrix.validate_block(path)

    def test_timeout_fails_despite_clean_process_exits_and_saves_verdict(self):
        with tempfile.TemporaryDirectory() as directory:
            path, raw, records = self.fixture(directory)
            records[2]['status'] = 'timeout'
            records[2]['presentation_ns'] = 0
            raw.write_text('\n'.join(map(json.dumps, records)))
            before = raw.read_bytes()
            with self.assertRaisesRegex(RuntimeError, r'timeout=1 trial_ids=\[1\]'):
                matrix.record_validation(path)
            report = json.loads(path.with_name(path.name.replace('.meta.json', '.validation.json')).read_text())
            self.assertEqual(report['status'], 'FAIL')
            self.assertIn('timeout=1', report['error'])
            self.assertEqual(raw.read_bytes(), before)
            summary, _ = matrix.summarize(path)
            self.assertEqual(summary['valid_presented_fraction'], 0)
            self.assertEqual(summary['latency']['client_to_presentation_ms']['n'], 0)
            self.assertIn('excluded', summary['latency_population'])

    def test_structural_failure_also_saves_verdict(self):
        with tempfile.TemporaryDirectory() as directory:
            path, raw, records = self.fixture(directory)
            records[-1]['cpu_ns'] = 1
            raw.write_text('\n'.join(map(json.dumps, records)))
            with self.assertRaisesRegex(RuntimeError, 'CPU-work'):
                matrix.record_validation(path)
            report = json.loads(path.with_name(path.name.replace('.meta.json', '.validation.json')).read_text())
            self.assertEqual(report['status'], 'FAIL')

    def test_load_failure_stops_only_owned_scheduler(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(matrix.subprocess, 'Popen') as popen, \
             patch.object(matrix.audit, 'wait_enabled', side_effect=RuntimeError('load failed')), \
             patch.object(matrix.audit, 'stop_child') as stop, \
             patch.object(matrix, 'Maps') as maps:
            with self.assertRaisesRegex(RuntimeError, 'load failed'):
                matrix.external_factory('prophet-observe')(Path(directory), 'test')
            stop.assert_called_once_with(popen.return_value)
            maps.assert_not_called()
            self.assertTrue((Path(directory) / 'test-controller.stderr').exists())

    def test_external_descriptors_and_release(self):
        attached = {'abi': 1, 'propagation': False}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(matrix.subprocess, 'Popen') as popen, \
             patch.object(matrix.audit, 'wait_enabled'), \
             patch.object(matrix, 'read_attached', return_value=attached), \
             patch.object(matrix, 'Maps') as maps, \
             patch.object(matrix.os, 'pidfd_open', return_value=44), \
             patch.object(matrix.os, 'close') as close:
            maps.return_value.fds = [41, 42, 43]
            process, env, fds, release = matrix.external_factory('prophet-adaptive')(Path(directory), 'test')
            self.assertIs(process, popen.return_value)
            self.assertEqual(fds, (41, 43, 44))
            self.assertEqual(env, {'PROPHET_TASK_FD': '41', 'PROPHET_METRICS_FD': '43', 'PROPHET_PID_FD': '44'})
            release()
            maps.return_value.close.assert_called_once()
            close.assert_called_once_with(44)


if __name__ == '__main__':
    unittest.main()
