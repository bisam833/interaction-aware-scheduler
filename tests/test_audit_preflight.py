import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import audit_latency as audit


class PreflightTests(unittest.TestCase):
    def test_selected_verifier_configurations(self):
        self.assertEqual(audit.smoke_variants(), [('off', []), ('on', ['--propagation'])])
        self.assertEqual(audit.smoke_variants(True, True), [
            ('off', ['--trace-delay']),
            ('on', ['--trace-delay', '--propagation']),
            ('partial', ['--trace-delay', '--propagation', '--partial'])])

    def test_propagation_load_failure_aborts_gate_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(audit, 'snapshot', return_value={'state': 'disabled', 'rejected': 0}), \
             patch.object(audit.subprocess, 'Popen') as popen, \
             patch.object(audit, 'wait_enabled', side_effect=[None, RuntimeError('load failed')]), \
             patch.object(audit, 'stop_child', side_effect=[0, 1]) as stop, \
             patch.object(audit, 'assert_fallback') as fallback, \
             patch.object(audit.subprocess, 'run') as control:
            with self.assertRaisesRegex(RuntimeError, 'on preflight failed.*attach-on.log'):
                audit.smoke(Path(tmp))
            self.assertEqual(popen.call_count, 2)
            self.assertEqual(popen.call_args_list[1].args[0][-1], '--propagation')
            self.assertEqual(stop.call_count, 2)
            self.assertEqual(fallback.call_count, 2)
            control.assert_not_called()
            self.assertTrue((Path(tmp) / 'attach-on.log').exists())

    @patch('builtins.print')
    def test_both_modes_and_ttl_are_required_for_success(self, _print):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(audit, 'snapshot', return_value={'state': 'disabled', 'rejected': 0}), \
             patch.object(audit.subprocess, 'Popen') as popen, \
             patch.object(audit, 'wait_enabled'), \
             patch.object(audit, 'stop_child', return_value=0), \
             patch.object(audit, 'assert_fallback') as fallback, \
             patch.object(audit.subprocess, 'run', return_value=SimpleNamespace(
                 returncode=0, stdout='active=0 reason=2', stderr='')) as control:
            audit.smoke(Path(tmp))
            self.assertEqual(popen.call_count, 2)
            self.assertEqual(control.call_count, 2)
            self.assertEqual(fallback.call_count, 4)


if __name__ == '__main__':
    unittest.main()
