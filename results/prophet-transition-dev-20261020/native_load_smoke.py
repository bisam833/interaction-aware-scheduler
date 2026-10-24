"""Functional test only: bounded native load on one owned CPU; no sched_ext/GUI."""
from pathlib import Path
import hashlib,json,os,struct,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import prophet_transitions as t
out=Path(__file__).resolve().parent/(sys.argv[1] if len(sys.argv)>1 else 'native-load-smoke')
out.mkdir(exist_ok=False)
assert t.audit.snapshot()['state']=='disabled'
cpu=max(os.sched_getaffinity(0)); reports=[]
cases={'steps':[(1,0,0),(1,1,0),(1,2,0),(1,1,0),(1,0,0)],
       'irregular':[(1,0,0),(2,2,0),(1,0,0),(1,1,0),(2,0,0)],
       'mixed':[(1,0,0),(1,1,0),(1,2,1),(1,1,1),(1,0,0)]}
for name,phases in cases.items():
 plan=out/(name+'.txt');plan.write_text(''.join(f'{seconds*t.NS} {batch} {periodic}\n' for seconds,batch,periodic in phases))
 ps=[];files=[];fds=[];waiting=[]
 try:
  for role,column in [(0,1),(1,2)]:
   for i in range(max(p[column] for p in phases)):
    r,w=os.pipe();fds.append(w);path=out/f'{name}-{role}-{i}.jsonl'
    stream=path.open('x');files.append(stream)
    try:p=subprocess.Popen([str(t.LOAD),str(plan),str(i),str(role),str(r)],pass_fds=(r,),stdout=stream)
    finally:os.close(r)
    ps.append(p);os.sched_setaffinity(p.pid,{cpu});waiting.append((path,'load_ready'))
  t.wait_ready(waiting,ps,3)
  epoch=time.monotonic_ns()+200_000_000
  while fds:
   fd=fds.pop()
   try:os.write(fd,struct.pack('=Q',epoch))
   finally:os.close(fd)
  for p in ps:assert p.wait(timeout=12)==0
 finally:
  for fd in fds:os.close(fd)
  for p in ps:t.audit.stop_child(p)
  for stream in files:stream.close()
 rows=[r for path,_ in waiting for r in t.read_records(path)]
 exits=[r for r in rows if r['type']=='load_exit'];assert all(r['complete'] for r in exits)
 for path,_ in waiting:
  records=t.read_records(path);identity=records[0];bs=[r for r in records if r['type']=='load_bin']
  assert len(bs)==sum(p[0] for p in phases)
  for i,phase in enumerate(phases):
   active=identity['index']<phase[2 if identity['periodic'] else 1]
   n=sum(r['iterations'] for r in bs if r['phase']==i)
   assert (n>0)==active
 phase_rows=[r for r in rows if r['type']=='load_phase']
 reports.append(dict(scenario=name,epoch_ns=epoch,workers=len(ps),cpu=cpu,
  max_activation_lag_ms=max((r['observed_start_ns']-r['planned_start_ns'])/1e6 for r in phase_rows if r['active']),
  max_continuous_worker_batch_gap_ms=max(r['max_progress_gap_ns']/1e6 for r in rows if r['type']=='load_bin' and not r['periodic']),
  status='PASS'))
 print(name,'PASS',flush=True)
assert t.audit.snapshot()['state']=='disabled'
(out/'summary.json').write_text(json.dumps({'purpose':'generator validation only; no Wayland/scheduler comparison','generator_sha256':hashlib.sha256(t.LOAD.read_bytes()).hexdigest(),'runs':reports},indent=2)+'\n')
