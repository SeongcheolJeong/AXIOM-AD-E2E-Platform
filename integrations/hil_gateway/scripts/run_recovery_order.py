"""Public SDK: native long power sag + sealed short FPGA stall, then fresh CARLA A/B."""
import json
import time
from pathlib import Path
from axiom_sdk import AxiomClient

p=Path('docs/validation/hil-model-recovery-order-20261018');p.mkdir(parents=True,exist_ok=True)
prior=Path('docs/validation/hil-model-overlap-20261018')
c=AxiomClient('http://127.0.0.1:8240',timeout=180)
source=json.loads((prior/'source.json').read_text());project=source['project_id']
def save(name,v):(p/name).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
save('source.json',source)
old=json.loads((prior/'result.json').read_text());save('comparison-run.json',dict(id=old['id'],manifest_hash=old['manifest_hash']))
power=c.save_hil_model_injection(dict(project_id=project,name='역순 회복 · 300ms 전원6V 강하',seed=42,
    settings=dict(boundary='power',start_ms=3,end_ms=303,stop_ms=400,voltage_v=6)),idempotency_key='recovery-order-20261018-power-v1')
save('power-pair.json',power)
jobs=c.execute_hil_model_injection(power,idempotency_key='recovery-order-20261018-native-v1');save('power-model-jobs.json',jobs)
for side,job in jobs.items():
    previous=None;deadline=time.monotonic()+1800
    while time.monotonic()<deadline:
        run=c.experiment_execution(job['id'])
        if run['status']!=previous:print('native power',side,job['id'],run['status'],flush=True);previous=run['status']
        if run['status'] not in ('queued','running','cancel_requested'):break
        time.sleep(2)
    else:raise TimeoutError('Native power deadline')
    save('power-'+side+'-result.json',run)
    assert run['status']=='completed' and run['summary']['engine']=='spice',run.get('error')
fp=Path('docs/validation/hil-model-loop-20261017/r2/fpga')
fpga=c.hil_model_injection(json.loads((fp/'pair.json').read_text())['id'],project_id=project)
fpga_jobs=json.loads((fp/'model-jobs.json').read_text());save('fpga-pair.json',fpga);save('fpga-model-jobs.json',fpga_jobs)
b=c.save_hil_model_set(source,[dict(pair=power,runs=jobs),dict(pair=fpga,runs=fpga_jobs)],
    name='역순 회복 · FPGA 먼저 / 전원 나중 · 동일 500ms 보호',idempotency_key='recovery-order-20261018-binding-v1')
save('binding.json',b);save('preview.json',c.preview_hil_binding(b))
job=c.execute_hil_binding(b,idempotency_key='recovery-order-20261018-carla-v1');save('submitted.json',job)
print('CARLA submitted',job['id'],flush=True)
