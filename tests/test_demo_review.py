import copy
import json
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import demo_review as demo
from demo_review_dashboard import render, controller

class DemoTests(unittest.TestCase):
    def test_failed_and_omitted_visible_without_latency(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); report=demo.empty_report()
            report['arms'][0].update(status='FAILED',reason='<script>bad</script>')
            render(report,p,p/'index.html')
            text=(p/'index.html').read_text()
            for _,name in demo.MODES: self.assertIn(name,text)
            self.assertIn('FAILED',text); self.assertIn('OMITTED',text)
            self.assertIn('&lt;script&gt;bad&lt;/script&gt;',text)
            self.assertNotIn('<script>bad',text)
            self.assertNotIn('0.0 ms',text)
            self.assertNotIn('https://',text)

    def test_constant_controller_is_not_invented_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'heavy-prophet-adaptive-0-controller.jsonl').write_text('\n'.join(
                json.dumps(dict(type='sample',now_ns=t,requested=2)) for t in (100,1000000100)))
            self.assertIn('No requested-profile changes occurred.',controller(p))

    def test_missing_metadata_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError,'metadata missing'):
                demo.arm_result(Path(tmp),demo.empty_report()['arms'][0])

    def test_cleanup_signal_protection_restores_handlers(self):
        original=demo.audit.stop_child
        def fake_run(*args,**kwargs):
            demo.audit.stop_child(object())
            self.assertEqual(signal.getsignal(signal.SIGINT),signal.SIG_IGN)
            raise RuntimeError('owned cleanup completed')
        prior=signal.getsignal(signal.SIGINT)
        with patch.object(demo.audit,'run_one',side_effect=fake_run), patch.object(demo.audit,'stop_child',return_value=0) as stop:
            with self.assertRaisesRegex(RuntimeError,'owned cleanup'):
                demo.run_owned(Path('/unused'),'linux',32,object())
            stop.assert_called_once()
            self.assertIs(demo.audit.stop_child,stop)
        self.assertEqual(signal.getsignal(signal.SIGINT),prior)
        self.assertIs(demo.audit.stop_child,original)

    def test_worker_failure_is_retained_and_other_arms_continue(self):
        initial={'state':'disabled','rejected':0}
        def fake_run(out,mode,workers,options):
            if mode=='lavd': raise RuntimeError('attach failed')
        def fake_result(out,arm): arm['valid']=32
        with tempfile.TemporaryDirectory() as tmp, patch.object(demo,'preflight',return_value=initial), patch.object(demo,'run_owned',side_effect=fake_run), patch.object(demo,'arm_result',side_effect=fake_result), patch.object(demo.audit,'snapshot',return_value=initial), patch.object(demo.shutil,'which',return_value='/fake'):
            rc=demo.worker(Path(tmp),demo.time.monotonic()+30)
            report=json.loads((Path(tmp)/'live-report.json').read_text())
            self.assertEqual(rc,1); self.assertTrue(report['cleanup'])
            self.assertEqual(report['arms'][1]['status'],'FAILED')
            self.assertEqual(report['arms'][4]['status'],'PASS')

    def test_interruption_omits_remaining_arms(self):
        initial={'state':'disabled','rejected':0}
        with tempfile.TemporaryDirectory() as tmp, patch.object(demo,'preflight',return_value=initial), patch.object(demo,'run_owned',side_effect=KeyboardInterrupt('cancel')), patch.object(demo.audit,'snapshot',return_value=initial):
            demo.worker(Path(tmp),demo.time.monotonic()+30)
            report=json.loads((Path(tmp)/'live-report.json').read_text())
            self.assertTrue(report['interrupted']); self.assertTrue(report['cleanup'])
            self.assertEqual([a['status'] for a in report['arms']],['FAILED']+['OMITTED']*4)

    def test_real_owned_workers_clean_up_after_client_failure(self):
        # Native lifecycle smoke only: two owned workers, no scheduler attachment.
        initial={'state':'disabled','rejected':0}
        options=demo.SimpleNamespace(trials=1,work_ms=20,interval_ms=200,trace_delay=False,timeout=2)
        with tempfile.TemporaryDirectory() as tmp, patch.object(demo.matrix,'BENCH',Path('/bin/false')), patch.object(demo.audit,'snapshot',return_value=initial):
            out=Path(tmp)
            with self.assertRaisesRegex(RuntimeError,'benchmark failed'):
                demo.run_owned(out,'linux',2,options)
            meta=json.loads((out/'heavy-linux-0.meta.json').read_text())
            self.assertEqual(meta['returncode'],1)
            self.assertEqual(meta['after'],initial)
            for i in range(2):
                row=json.loads((out/f'heavy-linux-0-worker-{i}.jsonl').read_text())
                self.assertGreater(row['iterations'],0)
                self.assertGreater(row['cpu_ns'],0)

    def test_complete_dashboard_renders_recorded_fixture_in_temp_only(self):
        root=Path(__file__).resolve().parents[1]
        report=demo.empty_report()
        for arm in report['arms']:
            demo.arm_result(root/'results/prophet-host-matrix-02',arm)
            arm.update(status='PASS',expected=30,reason='Historical unit-test fixture only')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            render(report,p,p/'fixture.html')
            page=(p/'fixture.html').read_text()
            self.assertIn('Historical unit-test fixture only',page)
            for arm in report['arms']:
                self.assertIn(f'{arm["latency"]["p95"]:.1f} ms',page)
            self.assertEqual(page.count('<svg '),3)
            self.assertIn('Arithmetic means over the same presented trials',page)

    def test_actual_recorded_block_parses_without_using_it_as_live(self):
        # Read-only regression fixture; never copied into a demo result directory.
        root=Path(__file__).resolve().parents[1]
        arm=copy.deepcopy(demo.empty_report()['arms'][0])
        demo.arm_result(root/'results/prophet-host-matrix-02',arm)
        self.assertEqual(arm['valid'],30)
        self.assertAlmostEqual(arm['latency']['p95'],262.9596191,places=6)
        self.assertGreater(arm['throughput'],0)

if __name__=='__main__': unittest.main()
