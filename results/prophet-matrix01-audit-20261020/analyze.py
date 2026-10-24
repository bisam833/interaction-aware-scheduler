"""Read-only revalidation of the stopped matrix; write only this audit directory."""
import hashlib
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from prophet_matrix import validate_block
from summarize_latency_audit import summarize, read_records
from summarize_prophet import summarize as controller_summary
source = ROOT / 'results/prophet-host-matrix-01'
out = Path(__file__).resolve().parent
before = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in source.rglob('*') if p.is_file()}
blocks = []
for meta in sorted(source.rglob('*.meta.json')):
    if 'requested_trials' not in json.loads(meta.read_text()):
        continue
    result, _ = summarize(meta)
    try:
        validate_block(meta)
        result['audit_gate'] = 'PASS'
    except RuntimeError as exc:
        result.update(audit_gate='FAIL', audit_error=str(exc))
    result['preflight'] = 'preflight' in meta.parts
    raw = read_records(meta.with_name(meta.name.replace('.meta.json', '.jsonl')))
    stages = [r for r in raw if r.get('type') == 'work_stage']
    result['all_recorded_stages'] = len(stages)
    result['all_stage_cpu_min_ms'] = min((r['cpu_ns']/1e6 for r in stages), default=None)
    result['failed_trials'] = [r for r in raw if r.get('type') == 'trial' and r.get('status') != 'presented']
    blocks.append(result)
(out / 'blocks.json').write_text(json.dumps(blocks, indent=2) + '\n')
ctl = controller_summary(source / 'heavy-prophet-adaptive-0-controller.jsonl')
(out / 'adaptive-controller.json').write_text(json.dumps(ctl, indent=2) + '\n')
assert before == {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in source.rglob('*') if p.is_file()}
(out / 'raw-sha256.json').write_text(json.dumps(before, indent=2)+'\n')
for b in blocks:
    if not b['preflight']:
        d=b['latency']['client_to_presentation_ms']
        print(b['implementation'], b['block'], b['audit_gate'], b['statuses'],
              'P95',round(d['p95'],3), 'work_min_ms',b['all_stage_cpu_min_ms'])
