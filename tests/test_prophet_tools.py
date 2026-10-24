import ctypes
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prophet_smoke import Epoch, validate_counters, variants
from summarize_prophet import histogram, summarize


class ProphetToolsTests(unittest.TestCase):
    def test_tail_support_and_saturation(self):
        bins = [0] * 32
        bins[31] = 1999
        self.assertFalse(histogram(bins)['quantiles']['p99']['supported'])
        bins[31] = 2000
        p = histogram(bins)['quantiles']['p99']
        self.assertTrue(p['supported'])
        self.assertEqual(p['lower_ns'], 1 << 31)
        self.assertIsNone(p['upper_exclusive_ns'])
        bins = [20000] + [0] * 31
        self.assertEqual(histogram(bins)['quantiles']['p999']['upper_exclusive_ns'], 2)

    def test_incomplete_and_invalid_remain_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'sample.jsonl'
            path.write_text(json.dumps({'type': 'sample', 'valid': False, 'counters': [1]*73})+'\n')
            result = summarize(path)
            self.assertFalse(result['complete'])
            self.assertEqual(result['invalid_polls_including_initial_baseline'], 1)
            self.assertEqual(result['enqueue_to_run']['samples'], 32)
            path.write_text('{broken')
            with self.assertRaises(json.JSONDecodeError):
                summarize(path)

    def test_gate_rejects_false_fallback_success(self):
        before = [0] * 73
        after = [1] * 73
        # Fallback activity alone isn't enough: expired profiles must stop.
        with self.assertRaises(RuntimeError):
            validate_counters('sigstop', before, after)
        after[66] = 0
        validate_counters('sigstop', before, after)
        with self.assertRaises(RuntimeError):
            validate_counters('freeze', after, after)
        with self.assertRaises(RuntimeError):
            validate_counters('static-1', before, before)
        with self.assertRaises(RuntimeError):
            validate_counters('no-telemetry', before, after)
        validate_counters('no-telemetry', before, before)

    def test_rodata_variants_and_epoch_abi(self):
        self.assertEqual(ctypes.sizeof(Epoch), 104)
        flags = [v[1] for v in variants()]
        self.assertIn(['--propagation'], flags)
        self.assertIn(['--mode', 'static', '--no-telemetry'], flags)
        self.assertTrue(any(v[2] == 'stop' for v in variants()))
        self.assertTrue(any(v[2] == 'kill' for v in variants()))


if __name__ == '__main__':
    unittest.main()
