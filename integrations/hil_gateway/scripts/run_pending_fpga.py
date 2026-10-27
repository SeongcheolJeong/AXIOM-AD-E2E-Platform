"""Fresh full-resolution extended RTL; recurrent stall during the qualified release wait."""
import json,time
from pathlib import Path
from axiom_sdk import AxiomClient
p=Path('docs/validation/hil-pending-fpga-20261018');p.mkdir(parents=True,exist_ok=True)
prior=Path('docs/validation/hil-release-qualification-20261018/4350');read=lambda p:json.loads(p.read_text())
c=AxiomClient('http://127.0.0.1:8240',timeout=180)
def save(n,v):(p/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
source=read(prior/'source.json');project=source['project_id'];save('source.json',source)
old=read(prior/'result.json');save('comparison-run.json',dict(id=old['id'],manifest_hash=old['manifest_hash']))
power=read(prior/'power-pair.json');power_jobs=read(prior/'power-model-jobs.json');save('power-pair.json',power);save('power-model-jobs.json',power_jobs)
fpga=c.save_hil_model_injection(dict(project_id=project,name='해제 검증 대기 중5.25초 FPGA 재발 · 75프레임/채널',seed=42,
 settings=dict(boundary='fpga',frames=75,channel=0,at_us=5000,duration_us=100,
 repeats=[dict(at_us=800000,duration_us=200),dict(at_us=2250000,duration_us=200)])),idempotency_key='pending-fpga-20261018-pair-v1')
save('fpga-pair.json',fpga);jobs=c.execute_hil_model_injection(fpga,idempotency_key='pending-fpga-20261018-native-v1');save('fpga-model-jobs.json',jobs)
for side,j in jobs.items():
 last=None;deadline=time.monotonic()+1800
 while time.monotonic()<deadline:
  r=c.experiment_execution(j['id'])
  if r['status']!=last:print('native',side,j['id'],r['status'],flush=True);last=r['status']
  if r['status'] not in ('queued','running','cancel_requested'):break
  time.sleep(2)
 else:raise TimeoutError('Native full-resolution deadline')
 save('fpga-'+side+'-result.json',r)
 assert r['status']=='completed' and r['summary']['verdict']=='passed',r.get('error')
b=c.save_hil_model_set(source,[dict(pair=power,runs=power_jobs),dict(pair=fpga,runs=jobs)],
 name='해제 후보 취소 ·5.25초 FPGA 재주입 · 두 완료구간 확인',idempotency_key='pending-fpga-20261018-binding-v1')
save('binding.json',b);save('preview.json',c.preview_hil_binding(b))
j=c.execute_hil_binding(b,idempotency_key='pending-fpga-20261018-carla-v1');save('submitted.json',j);print('CARLA submitted',j['id'],flush=True)
