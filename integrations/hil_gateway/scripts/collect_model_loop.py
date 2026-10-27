"""Collect existing native jobs via SDK. Never submit, replace or promote a failed run."""
import argparse,hashlib,json,time
from pathlib import Path
from axiom_sdk import AxiomClient
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--api',default='http://127.0.0.1:8240');a=p.parse_args()
c=AxiomClient(a.api,timeout=180);project=json.loads((a.root/'source.json').read_text())['project_id']
for boundary in ('camera_raw_csi','fpga','power','analog'):
    folder=a.root/boundary;job=json.loads((folder/'submitted.json').read_text());previous=None;deadline=time.monotonic()+1800
    while time.monotonic()<deadline:
        run=c.hil_bridge_execution(job['id'],project_id=project)
        if run['status']!=previous:print(boundary,run['id'],run['status'],flush=True);previous=run['status']
        if run['status'] not in ('queued','running','cancel_requested'):break
        time.sleep(2)
    else:raise TimeoutError('Native collection deadline')
    (folder/'result.json').write_text(json.dumps(run,ensure_ascii=False,indent=2)+'\n')
    raw=c.export_hil_bridge_execution(run['id'],project_id=project)
    assert hashlib.sha256(raw).hexdigest()==run['manifest_hash'];(folder/'export.json').write_bytes(raw)
    for item in run['artifacts']:
        if item['name'] in ('vecu-trace.json','hil-causality.json','hil-bridge-provenance.json','hil-native-diagnostics.json') or item['name'].endswith('stderr.log'):
            path=folder/item['name'];c.download_experiment_artifact(run['id'],item['id'],path)
            assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
    print(boundary,(run.get('summary') or {}).get('verdict'),run.get('error'),flush=True)
    if run['status']!='completed':raise RuntimeError('Failed native run retained')
