"""Public SDK native timing campaign; unchanged per-cause thresholds/holds, new release gate."""
import hashlib,json,time
from pathlib import Path
from axiom_sdk import AxiomClient
root=Path('docs/validation/hil-release-qualification-20261018');root.mkdir(parents=True,exist_ok=True)
prior=Path('docs/validation/hil-power-recurrence-20261018');c=AxiomClient('http://127.0.0.1:8240',timeout=180)
read=lambda p:json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
source=read(prior/'source.json');project=source['project_id'];fpga=read(prior/'fpga-pair.json');fpga_jobs=read(prior/'fpga-model-jobs.json')
old=read(prior/'result.json');save(root/'comparison-run.json',dict(id=old['id'],manifest_hash=old['manifest_hash']))
def wait(job,path,bridge=False):
 last=None;deadline=time.monotonic()+1800
 while time.monotonic()<deadline:
  r=c.hil_bridge_execution(job['id'],project_id=project) if bridge else c.experiment_execution(job['id'])
  if r['status']!=last:print(path.parent.name,path.name,job['id'],r['status'],flush=True);last=r['status']
  if r['status'] not in ('queued','running','cancel_requested'):break
  time.sleep(2)
 else:raise TimeoutError(job['id'])
 save(path,r)
 assert r['status']=='completed',r.get('error')
 return r
for start in (4350,4349,4351,4399):
 p=root/str(start);p.mkdir(exist_ok=True);prefix=f'release-gate-20261018-{start}-v1'
 save(p/'source.json',source);save(p/'fpga-pair.json',fpga);save(p/'fpga-model-jobs.json',fpga_jobs)
 if start==4350:
  power=read(prior/'power-pair.json');jobs=read(prior/'power-model-jobs.json')
 else:
  power=c.save_hil_model_injection(dict(project_id=project,name=f'해제 경계 {start}ms · 전원6V/300ms',seed=42,
   settings=dict(boundary='power',start_ms=3,end_ms=303,stop_ms=1800,step_us=20,voltage_v=6,
    repeats=[dict(start_ms=start-3000,end_ms=start-2700)])),idempotency_key=prefix+'-native-pair')
  jobs=c.execute_hil_model_injection(power,idempotency_key=prefix+'-native')
 save(p/'power-pair.json',power);save(p/'power-model-jobs.json',jobs)
 for side,j in jobs.items():wait(j,p/f'power-{side}-result.json')
 b=c.save_hil_model_set(source,[dict(pair=power,runs=jobs),dict(pair=fpga,runs=fpga_jobs)],name=f'해제 개선 · 전원재발 {start}ms · 연속 두 완료구간',idempotency_key=prefix+'-binding')
 assert b['model_link']['policy']['release_qualification']['fresh_windows']==2
 save(p/'binding.json',b);save(p/'preview.json',c.preview_hil_binding(b))
 j=c.execute_hil_binding(b,idempotency_key=prefix+'-carla');save(p/'submitted.json',j)
 r=wait(j,p/'result.json',True)
 export=c.export_hil_bridge_execution(r['id'],project_id=project);assert hashlib.sha256(export).hexdigest()==r['manifest_hash'];(p/'export.json').write_bytes(export)
 for a in r['artifacts']:
  if a['name'] in ('vecu-trace.json','hil-causality.json','hil-bridge-provenance.json','hil-native-diagnostics.json'):
   dest=p/a['name'];c.download_experiment_artifact(r['id'],a['id'],dest);assert hashlib.sha256(dest.read_bytes()).hexdigest()==a['sha256']
 assert r['summary']['verdict']=='passed',r['summary']['validity_errors']
 print('CASE COMPLETE',start,r['id'],flush=True)
