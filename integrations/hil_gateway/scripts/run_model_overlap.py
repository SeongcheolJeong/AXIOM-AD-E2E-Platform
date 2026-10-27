"""Public SDK workflow: native 4x1080p30 stall, then new power+FPGA CARLA A/B."""
import argparse
import json
import time
from pathlib import Path

from axiom_sdk import AxiomClient

root=Path('docs/validation/hil-model-overlap-20261018')
parser=argparse.ArgumentParser()
parser.add_argument('--execution-key',default='hil-overlap-20261018-carla-v2',help='Reuse for exact resume; a new key explicitly creates a new run')
args=parser.parse_args()
root.mkdir(parents=True,exist_ok=True)
prior=Path('docs/validation/hil-model-loop-20261017/r2')
c=AxiomClient('http://127.0.0.1:8240',timeout=180)
source=json.loads((prior/'source.json').read_text())
project=source['project_id']
def save(name,value):
    (root/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
save('source.json',source)
power=c.hil_model_injection(json.loads((prior/'power/pair.json').read_text())['id'],project_id=project)
power_jobs=json.loads((prior/'power/model-jobs.json').read_text())
save('power-pair.json',power);save('power-model-jobs.json',power_jobs)
pair=c.save_hil_model_injection(dict(project_id=project,name='복합 회복 검증 · CH0 300ms FPGA 정체',seed=42,
    settings=dict(boundary='fpga',channel=0,at_us=5000,duration_us=300000)),idempotency_key='hil-overlap-20261018-fpga-300ms-v1')
save('fpga-pair.json',pair)
jobs=c.execute_hil_model_injection(pair,idempotency_key='hil-overlap-20261018-native-v1')
save('fpga-model-jobs.json',jobs)
for side,job in jobs.items():
    previous=None;deadline=time.monotonic()+1800
    while time.monotonic()<deadline:
        run=c.experiment_execution(job['id'])
        if run['status']!=previous:print('native FPGA',side,job['id'],run['status'],flush=True);previous=run['status']
        if run['status'] not in ('queued','running','cancel_requested'):break
        time.sleep(2)
    else:raise TimeoutError('Native FPGA deadline')
    save('fpga-'+side+'-result.json',run)
    assert run['status']=='completed' and run['summary']['verdict']=='passed',run.get('error') or run.get('summary')
binding=c.save_hil_model_set(source,[dict(pair=power,runs=power_jobs),dict(pair=pair,runs=jobs)],
    name='전원 강하 + FPGA 300ms 정체 · 독립 회복 / 모든 보호 해제',idempotency_key='hil-overlap-20261018-binding-v1')
save('binding.json',binding);save('preview.json',c.preview_hil_binding(binding))
job=c.execute_hil_binding(binding,idempotency_key=args.execution_key)
save('submitted.json',job);print('CARLA submitted',job['id'],flush=True)
