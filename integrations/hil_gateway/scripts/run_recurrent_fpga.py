"""Public SDK: freshly execute recurrent RTL stalls and replay with sealed long power sag."""
import json,time
from pathlib import Path
from axiom_sdk import AxiomClient

p=Path('docs/validation/hil-model-recurrence-20261018/overflow-200us');p.mkdir(parents=True,exist_ok=True)
prior=Path('docs/validation/hil-model-recovery-order-20261018')
c=AxiomClient('http://127.0.0.1:8240',timeout=180)
def save(name,v):(p/name).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
source=json.loads((prior/'source.json').read_text());project=source['project_id'];save('source.json',source)
old=json.loads((prior/'result.json').read_text());save('comparison-run.json',dict(id=old['id'],manifest_hash=old['manifest_hash']))
power=c.hil_model_injection(json.loads((prior/'power-pair.json').read_text())['id'],project_id=project)
power_jobs=json.loads((prior/'power-model-jobs.json').read_text());save('power-pair.json',power);save('power-model-jobs.json',power_jobs)
fpga=c.save_hil_model_injection(dict(project_id=project,name='FPGA 재발 · 3.8초 · 4×1080p30',seed=42,
    settings=dict(boundary='fpga',channel=0,at_us=5000,duration_us=100,repeats=[dict(at_us=800000,duration_us=200)])),
    idempotency_key='recurrence-20261018-fpga-v2')
save('fpga-pair.json',fpga)
jobs=c.execute_hil_model_injection(fpga,idempotency_key='recurrence-20261018-native-v2');save('fpga-model-jobs.json',jobs)
for side,job in jobs.items():
    previous=None;deadline=time.monotonic()+1800
    while time.monotonic()<deadline:
        run=c.experiment_execution(job['id'])
        if run['status']!=previous:print('native FPGA',side,job['id'],run['status'],flush=True);previous=run['status']
        if run['status'] not in ('queued','running','cancel_requested'):break
        time.sleep(2)
    else:raise TimeoutError('Native FPGA deadline')
    save('fpga-'+side+'-result.json',run)
    assert run['status']=='completed' and run['summary']['verdict']=='passed',run.get('error')
b=c.save_hil_model_set(source,[dict(pair=power,runs=power_jobs),dict(pair=fpga,runs=jobs)],
    name='3.8초 FPGA 정체 재발 · 전원 보호 해제 교차 · 500ms 유지',idempotency_key='recurrence-20261018-binding-v2')
save('binding.json',b);save('preview.json',c.preview_hil_binding(b))
job=c.execute_hil_binding(b,idempotency_key='recurrence-20261018-carla-v2');save('submitted.json',job)
print('CARLA submitted',job['id'],flush=True)
