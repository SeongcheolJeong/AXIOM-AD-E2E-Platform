"""Extend native power with a third sag at vehicle5.85s; keep the sealed FPGA source."""
import json,time
from pathlib import Path
from axiom_sdk import AxiomClient
p=Path('docs/validation/hil-pending-power-20261018');p.mkdir(parents=True,exist_ok=True)
prior=Path('docs/validation/hil-pending-fpga-20261018');read=lambda p:json.loads(p.read_text())
c=AxiomClient('http://127.0.0.1:8240',timeout=180)
def save(n,v):(p/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
source=read(prior/'source.json');project=source['project_id'];save('source.json',source)
old=read(prior/'result.json');save('comparison-run.json',dict(id=old['id'],manifest_hash=old['manifest_hash']))
fpga=c.hil_model_injection(read(prior/'fpga-pair.json')['id'],project_id=project)
fpga_jobs=read(prior/'fpga-model-jobs.json');save('fpga-pair.json',fpga);save('fpga-model-jobs.json',fpga_jobs)
power=c.save_hil_model_injection(dict(project_id=project,name='해제 검증 대기 중5.85초 전원 재발 · 6V 300ms · native4s',seed=42,
 settings=dict(boundary='power',start_ms=3,end_ms=303,stop_ms=4000,step_us=20,voltage_v=6,
 repeats=[dict(start_ms=1350,end_ms=1650),dict(start_ms=2850,end_ms=3150)])),idempotency_key='pending-power-20261018-pair-v1')
save('power-pair.json',power);jobs=c.execute_hil_model_injection(power,idempotency_key='pending-power-20261018-native-v1');save('power-model-jobs.json',jobs)
for side,j in jobs.items():
 last=None;deadline=time.monotonic()+1800
 while time.monotonic()<deadline:
  r=c.experiment_execution(j['id'])
  if r['status']!=last:print('native power',side,j['id'],r['status'],flush=True);last=r['status']
  if r['status'] not in ('queued','running','cancel_requested'):break
  time.sleep(2)
 else:raise TimeoutError('Native power deadline')
 save('power-'+side+'-result.json',r)
 assert r['status']=='completed' and r['summary']['engine']=='spice',r.get('error')
b=c.save_hil_model_set(source,[dict(pair=power,runs=jobs),dict(pair=fpga,runs=fpga_jobs)],
 name='해제 후보 재취소 ·5.85초 전원 재발 · FPGA5.25초 유지',idempotency_key='pending-power-20261018-binding-v1')
save('binding.json',b);save('preview.json',c.preview_hil_binding(b))
j=c.execute_hil_binding(b,idempotency_key='pending-power-20261018-carla-v1');save('submitted.json',j);print('CARLA submitted',j['id'],flush=True)
