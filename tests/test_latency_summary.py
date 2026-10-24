import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from summarize_latency_audit import summarize


class SummaryTests(unittest.TestCase):
    def test_missing_timeout_and_invalid_clock_are_not_successes(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'idle-linux-0.meta.json'
            p.write_text(json.dumps({'condition': 'idle', 'implementation': 'linux',
                                     'block': 0, 'requested_trials': 4, 'workers': 0}))
            records = [
                {'type': 'trial', 'status': 'timeout'},
                {'type': 'trial', 'status': 'presented', 'presentation_clock_id': 0},
                {'type': 'trial', 'status': 'presented', 'presentation_clock_id': 1,
                 'interaction_id': 3, 'input_ns': 1, 'commit_ns': 2, 'presentation_ns': 3,
                 'work_to_commit_ns': 1, 'commit_to_presentation_ns': 1}]
            p.with_name('idle-linux-0.jsonl').write_text('\n'.join(map(json.dumps, records)))
            output, series = summarize(p)
            self.assertEqual(output['missing'], 1)
            self.assertEqual(output['statuses']['timeout'], 1)
            self.assertEqual(output['invalid_presentations'], 1)
            self.assertEqual(len(series['client_to_presentation_ms']), 1)


if __name__ == '__main__':
    unittest.main()
