"""Offline, dependency-free projector dashboard. Accepts only this run's report."""
import html
import json
from pathlib import Path

COLORS = ['#7e9bb8', '#57c9ff', '#66e0be', '#b29aff', '#ffca78']

def esc(value):
    return html.escape(str(value), quote=True)

def svg(body, height, label):
    return f'<svg viewBox="0 0 1000 {height}" role="img" aria-label="{esc(label)}">{body}</svg>'

def text(x, y, value, size=22, fill='#dce8f5'):
    return f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}">{esc(value)}</text>'

def bar_chart(arms):
    scale = max([a['latency']['p95'] for a in arms if a['status']=='PASS'] or [1])
    body = ''
    for i,a in enumerate(arms):
        y=35+i*62
        body += text(0,y,a['label'])
        if a['status']!='PASS':
            body += text(295,y,a['status'],22,'#ff8d98')
            continue
        value=a['latency']['p95']; width=530*value/max(scale,1)
        body += f'<rect x="295" y="{y-24}" width="{width:.2f}" height="32" rx="6" fill="{COLORS[i]}"/>'
        body += text(305+width,y,f'{value:.1f} ms',24)
    return svg(body,320,'P95 client dispatch to presentation, milliseconds; lower is better')

def tails(arms):
    maximum=max([a['latency']['p99'] for a in arms if a['status']=='PASS'] or [1])
    body=''
    for i,a in enumerate(arms):
        x=35+i*195
        body+=text(x,250,['Linux','LAVD','Pulse','Static 5','Adaptive'][i],19)
        if a['status']!='PASS':
            body+=text(x,130,a['status'],15,'#ff8d98'); continue
        for j,k in enumerate(('p50','p95','p99')):
            value=a['latency'][k]; h=155*value/max(maximum,1)
            body+=f'<rect x="{x+j*52}" y="{210-h:.2f}" width="40" height="{h:.2f}" rx="4" fill="{COLORS[i]}" opacity="{.4+j*.3}"/>'
            body+=text(x+j*52,200-h,f'{value:.1f}',14)
            body+=text(x+j*52,230,k.upper(),13)
    return svg(body,275,'P50 P95 P99 empirical latency')

def stages(arms):
    keys=('work_to_commit_ms','commit_to_presentation_ms')
    maximum=max([sum(a['stages'].values()) for a in arms if a['status']=='PASS'] or [1])
    body=''
    for i,a in enumerate(arms):
        y=30+i*48; body+=text(0,y,a['label'],20)
        if a['status']!='PASS':
            body+=text(295,y,a['status'],18,'#ff8d98'); continue
        x=295
        for k,c in zip(keys,('#57c9ff','#b29aff')):
            value=a['stages'][k]; w=480*value/max(maximum,1)
            body+=f'<rect x="{x:.2f}" y="{y-20}" width="{w:.2f}" height="27" fill="{c}"/>'
            x+=w
        body+=text(x+10,y,' / '.join(f'{a["stages"][k]:.1f}' for k in keys)+' ms',17)
    return svg(body,255,'Arithmetic mean work to commit and commit to presentation')

def controller(out):
    path=out/'heavy-prophet-adaptive-0-controller.jsonl'
    samples=[]
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                row=json.loads(line)
                if row.get('type')=='sample': samples.append(row)
            except (ValueError,TypeError):
                pass  # validation marks malformed arms failed; preserve readable trace
    if not samples:
        return '<p>No adaptive telemetry recorded. No switching activity is inferred.</p>'
    start=samples[0]['now_ns']; duration=max((samples[-1]['now_ns']-start)/1e9,.1)
    ys={-1:185,0:40,1:90,2:140}
    body=''.join(text(0,y+6,label,17) for y,label in [(40,'20 ms'),(90,'10 ms'),(140,'5 ms'),(185,'No request')])
    points=[]; previous=None; switches=0
    for row in samples:
        x=130+830*(row['now_ns']-start)/1e9/duration
        profile=row.get('requested',-1); y=ys.get(profile,185)
        if previous is not None:
            points.append(f'{x:.2f},{previous}')
            if y!=previous: switches+=1
        points.append(f'{x:.2f},{y}')
        previous=y
    body+=f'<polyline points="{" ".join(points)}" fill="none" stroke="#ffca78" stroke-width="3"/>'
    body+=text(130,220,'0 s',16)+text(880,220,f'{duration:.1f} s',16)
    note='No requested-profile changes occurred.' if not switches else f'{switches} requested-state changes, including initial support acquisition if present.'
    return svg(body,240,'Actual requested slice profile over time')+f'<p>{note} Requested configuration is not an instantaneous all-CPU switch; unsupported evidence can trigger BPF fallback. Failed-arm telemetry, if present, is diagnostic only.</p>'

def render(report, out, target):
    arms=report['arms']; passed=[a for a in arms if a['status']=='PASS']
    rows=''
    for a in arms:
        counts=a.get('counts',{})
        value=f'{a["throughput"]:.3f}' if a['status']=='PASS' else '—'
        details=a.get('reason','')
        if a.get('validation_error'): details+='; '+a['validation_error']
        if 'counts' in a: details+=f'; invalid presentations: {a.get("invalid",0)}, protocol errors: {a.get("protocol_errors",0)}'
        counts_text=f'{counts.get("timeout",0)} / {counts.get("discarded",0)} / {a.get("missing",a["expected"])}'
        rows+=f'<tr><td>{esc(a["label"])}</td><td class="{a["status"].lower()}">{esc(a["status"])}</td><td>{a.get("valid",0)}/{a["expected"]}</td><td>{value}</td><td>{counts_text}</td></tr><tr class="detail"><td colspan="5">{esc(details)}</td></tr>'
    linux=next((a for a in passed if a['mode']=='linux'),None)
    adaptive=next((a for a in passed if a['mode']=='prophet-adaptive'),None)
    fixed=next((a for a in passed if a['mode']=='prophet-static-2'),None)
    interpretation='This is a short sequential demonstration, not a statistical ranking or proof of predictive benefit.'
    if linux and len(passed)>1:
        best=min(passed,key=lambda a:a['latency']['p95'])
        interpretation=f'Lowest observed P95: {best["label"]} ({best["latency"]["p95"]:.1f} ms). '+interpretation
    if adaptive and fixed:
        delta=adaptive['latency']['p95']-fixed['latency']['p95']
        interpretation+=f' Adaptive minus fixed 5 ms P95: {delta:+.1f} ms in this run; negative is lower.'
    warning=report.get('error','')
    if not report.get('cleanup'): warning+=' Scheduler restoration was not confirmed (or preflight stopped before attachment).'
    page='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PulseSched • Live OS Review</title><style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#08111f;color:#e8f1fb;font:20px/1.5 system-ui,sans-serif}main{max-width:1440px;margin:auto;padding:38px}header{border-bottom:1px solid #29405a;padding-bottom:24px}h1{font-size:48px;margin:8px 0;letter-spacing:-1px}h2{font-size:27px;margin:0 0 12px}.eyebrow{color:#66e0be;letter-spacing:3px;font-size:16px}p{color:#aebed2;margin:10px 0}.stats{display:flex;gap:14px;flex-wrap:wrap;margin:24px 0}.stat{flex:1;min-width:180px;background:#132237;border:1px solid #29405a;border-radius:12px;padding:16px}.stat strong{display:block;font-size:32px}.stat span{font-size:16px;color:#aebed2}.card{background:#101e30;border:1px solid #263b54;border-radius:16px;padding:24px;margin:22px 0}svg{width:100%;height:auto;font-family:system-ui,sans-serif}table{width:100%;border-collapse:collapse;font-size:18px}td,th{padding:10px;text-align:left;border-bottom:1px solid #263b54}th{color:#aebed2;font-size:16px}.detail td{font-size:14px;color:#aebed2;padding-top:0;overflow-wrap:anywhere}.pass{color:#66e0be}.failed,.omitted,.warning{color:#ff9ba5}.warning{border-left:4px solid;padding:15px;background:#2a1826}.note{font-size:16px}.cyan{color:#57c9ff}.purple{color:#b29aff}footer{font-size:15px;color:#8da2bd;overflow-wrap:anywhere}@media(max-width:700px){main{padding:16px}h1{font-size:34px}.card{padding:12px}table{font-size:14px}}</style></head><body><main>'''
    page+=f'<header><div class="eyebrow">OPERATING SYSTEMS / LIVE MEASUREMENT</div><h1>PulseSched <span style="color:#66e0be">×</span> scx_prophet</h1><p>Real Linux sched_ext scheduling · controlled CPU contention · Wayland presentation feedback</p></header>'
    page+=f'<div class="stats"><div class="stat"><strong>{len(passed)} / 5</strong><span>validated configurations</span></div><div class="stat"><strong>{report.get("workers","—")}</strong><span>background CPU workers</span></div><div class="stat"><strong>{sum(a.get("valid",0) for a in passed)}</strong><span>trials in validated arms</span></div><div class="stat"><strong>{report.get("total_s",0):.1f} s</strong><span>elapsed through cleanup, before rendering</span></div></div>'
    if warning: page+=f'<p class="warning">{esc(warning)}</p>'
    page+=f'<section class="card"><h2>P95 input-to-presentation latency</h2><p>Lower is better · milliseconds · input origin is synthetic client dispatch, not hardware input</p>{bar_chart(arms)}</section>'
    page+=f'<section class="card"><h2>Latency distribution</h2>{tails(arms)}<p class="note">32 trials per complete arm: P95/P99 have sparse tail support and are empirical descriptions, not confident population estimates. Failed configurations have no latency bars.</p></section>'
    page+=f'<section class="card"><h2>Where presentation time is spent</h2><p><span class="cyan">■ Work-to-commit mean</span> &nbsp; <span class="purple">■ Commit-to-presentation mean</span></p>{stages(arms)}<p class="note">Arithmetic means over the same presented trials; not summed percentiles. Work-to-commit starts at the recorded work-start timestamp. Commit-to-presentation includes compositor, GPU and display-refresh effects.</p></section>'
    page+=f'<section class="card"><h2>Adaptive slice requests · actual telemetry</h2>{controller(out)}<p class="note">Implemented: reactive bounded slice selection. Predictive MPC is not implemented.</p></section>'
    page+=f'<section class="card"><h2>Run integrity & throughput</h2><table><thead><tr><th>Configuration</th><th>Status</th><th>Presented*</th><th>G iterations/s</th><th>Timeout / discarded / missing</th></tr></thead><tbody>{rows}</tbody></table><p class="note">*Presented counts in failed arms are diagnostic and excluded from charts. Raw logs retain invalid timestamps, protocol errors and lifecycle failures. Throughput sums worker iteration rates over their full lifetimes, including warmup/startup/cleanup; it is not precisely aligned to the trial-only interval.</p></section>'
    page+=f'<section class="card"><h2>What this run demonstrates</h2><p>{esc(interpretation)}</p><p class="note">Same executable, four 20 ms thread-CPU stages, 200 ms completion-relative interval, one-second background warmup. Fixed sequential order can confound results with time/temperature. Do not compare these small-sample figures as if they were the historical research matrix. No historical results are substituted.</p></section>'
    page+=f'<footer>Scheduler restoration: {"confirmed" if report.get("cleanup") else "not confirmed / not run"} · Raw JSONL, diagnostics and report: {esc(out)}<br>Offline dashboard · no external assets, network, server, or simulated results</footer></main></body></html>'
    target.write_text(page)
