"""Collect only the already-submitted compound CARLA execution and verify sealed hashes."""
import argparse
import hashlib
import json
import time
from pathlib import Path
from axiom_sdk import AxiomClient

parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path('docs/validation/hil-model-overlap-20261018'));args=parser.parse_args()
p=args.root
c=AxiomClient('http://127.0.0.1:8240',timeout=180)
b=json.loads((p/'binding.json').read_text());job=json.loads((p/'submitted.json').read_text())
previous=None;deadline=time.monotonic()+1800
while time.monotonic()<deadline:
    run=c.hil_bridge_execution(job['id'],project_id=b['project_id'])
    if run['status']!=previous:print(run['id'],run['status'],flush=True);previous=run['status']
    if run['status'] not in ('queued','running','cancel_requested'):break
    time.sleep(2)
else:raise TimeoutError('CARLA deadline')
(p/'result.json').write_text(json.dumps(run,ensure_ascii=False,indent=2)+'\n')
export=c.export_hil_bridge_execution(run['id'],project_id=b['project_id'])
assert hashlib.sha256(export).hexdigest()==run['manifest_hash']
(p/'export.json').write_bytes(export)
for artifact in run['artifacts']:
    if artifact['name'] in ('vecu-trace.json','hil-causality.json','hil-bridge-provenance.json','hil-native-diagnostics.json') or artifact['name'].endswith('stderr.log'):
        path=p/artifact['name'];c.download_experiment_artifact(run['id'],artifact['id'],path)
        assert hashlib.sha256(path.read_bytes()).hexdigest()==artifact['sha256']
print('verdict',(run.get('summary') or {}).get('verdict'),run.get('error'),flush=True)
assert run['status']=='completed' and run['summary']['verdict']=='passed', 'Non-passing result retained without promotion'
