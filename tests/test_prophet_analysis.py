import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from analyze_prophet_matrix import paired, controller_windows


class AnalysisTests(unittest.TestCase):
    screen = {'paired_p95_reduction_fraction': .05,
              'maximum_background_throughput_loss_fraction': .03}

    def row(self, block, p95, throughput=100):
        return {'condition': 'heavy', 'block': block,
                'latency': {'client_to_presentation_ms': {'p95': p95}},
                'background_iterations_per_second': throughput,
                'background_max_progress_gap_ms': 40}

    def test_pair_by_block_not_input_order(self):
        result = paired([self.row(1, 90), self.row(0, 190)],
                        [self.row(0, 200), self.row(1, 100)], self.screen)
        self.assertEqual([r['block'] for r in result], [0, 1])
        self.assertAlmostEqual(result[0]['p95_reduction_fraction'], .05)
        self.assertAlmostEqual(result[1]['p95_reduction_fraction'], .1)
        self.assertTrue(result[1]['meets_each_screen_threshold'])

    def test_missing_duplicate_or_wrong_condition_rejected(self):
        row = self.row(0, 100)
        for candidate in ([], [row, row], [{**row, 'condition': 'idle'}]):
            with self.assertRaises(ValueError):
                paired(candidate, [row], self.screen)

    def test_throughput_and_gap_fail_even_with_lower_latency(self):
        reference = [self.row(0, 100)]
        candidate = self.row(0, 80, 96)
        self.assertFalse(paired([candidate], reference, self.screen)[0]['meets_each_screen_threshold'])
        candidate['background_iterations_per_second'] = 100
        candidate['background_max_progress_gap_ms'] = 101
        self.assertFalse(paired([candidate], reference, self.screen)[0]['meets_each_screen_threshold'])
        reference[0]['background_iterations_per_second'] = 0
        self.assertIsNone(paired([candidate], reference, self.screen)[0]['background_throughput_loss_fraction'])

    def test_windows_reset_invalid_and_include_boundary_bin(self):
        record = {'type': 'sample', 'now_ns': 100, 'valid': False,
                  'requested': -1, 'generation': 0, 'counters': [0] * 73}
        records = [copy.deepcopy(record)]
        record.update(now_ns=100_000_100, valid=True, requested=0, generation=1)
        record['counters'][22] = 10
        record['counters'][23] = 2
        records.append(copy.deepcopy(record))
        record.update(now_ns=200_000_100, valid=False)
        records.append(copy.deepcopy(record))
        record.update(now_ns=700_000_100, valid=True, requested=1, generation=2)
        records.append(copy.deepcopy(record))
        windows, transitions = controller_windows(records)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]['samples'], 12)
        self.assertEqual(windows[0]['slow_samples'], 2)
        self.assertEqual(windows[0]['elapsed_ns'], 500_000_000)
        self.assertEqual(windows[0]['requested_after_window'], 1)
        self.assertEqual([t['requested'] for t in transitions], [-1, 0, 1])


if __name__ == '__main__':
    unittest.main()
