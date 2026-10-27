"""New native SPICE repeat at vehicle4.35s, sealed prior FPGA repeats, fresh CARLA A/B."""
import json,time
from pathlib import Path
from axiom_sdk import AxiomClient
p=Path('docs/validation/hil-power-recurrence-20261018');p.mkdir(parents=True,exist_ok=True)
prior=Path('docs/validation/hil-model-recurrence-20261018/overflow-200us')
c=AxiomClient('http://127.0.0.1:8240',timeout=180)
def save(n,v):(p/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
source=json.loads((prior/'source.json').read_text());project=source['project_id'];save('source.json',source)
old=json.loads((prior/'result.json').read_text());save('comparison-run.json',dict(id=old['id'],manifest_hash=old['manifest_hash']))
fpga=c.hil_model_injection(json.loads((prior/'fpga-pair.json').read_text())['id'],project_id=project)
fpga_jobs=json.loads((prior/'fpga-model-jobs.json').read_text());save('fpga-pair.json',fpga);save('fpga-model-jobs.json',fpga_jobs)
power=c.save_hil_model_injection(dict(project_id=project,name='4.35초 전원6V 재발 · 300ms 강하 · native1.75s',seed=42,
    settings=dict(boundary='power',start_ms=3,end_ms=303,stop_ms=1750,step_us=20,voltage_v=6,
        repeats=[dict(start_ms=1350,end_ms=1650)])),idempotency_key='power-recurrence-20261018-pair-v1')
save('power-pair.json',power)
jobs=c.execute_hil_model_injection(power,idempotency_key='power-recurrence-20261018-native-v1');save('power-model-jobs.json',jobs)
for side,job in jobs.items():
 previous=None;deadline=time.monotonic()+1800
 while time.monotonic()<deadline:
  r=c.experiment_execution(job['id'])
  if r['status']!=previous:print('native power',side,job['id'],r['status'],flush=True);previous=r['status']
  if r['status'] not in ('queued','running','cancel_requested'):break
  time.sleep(2)
 else:raise TimeoutError('Native power deadline')
 save('power-'+side+'-result.json',r)
 assert r['status']=='completed' and r['summary']['engine']=='spice',r.get('error')
b=c.save_hil_model_set(source,[dict(pair=power,runs=jobs),dict(pair=fpga,runs=fpga_jobs)],
    name='4.35초 전원 재발 · 3.8초 FPGA 재발 유지 · 보호 교차',idempotency_key='power-recurrence-20261018-binding-v1')
save('binding.json',b);save('preview.json',c.preview_hil_binding(b))
j=c.execute_hil_binding(b,idempotency_key='power-recurrence-20261018-carla-v1');save('submitted.json',j);print('CARLA submitted',j['id'],flush=True)
