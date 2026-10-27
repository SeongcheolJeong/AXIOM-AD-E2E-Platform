"""Live project/API checks against retained model-loop acceptance; never submit jobs."""
import argparse
import copy
import hashlib
import json
import uuid
from pathlib import Path

from axiom_sdk import AxiomClient
from axiom_sdk.client import AxiomError

p = argparse.ArgumentParser()
p.add_argument('--root', type=Path, required=True)
p.add_argument('--api', default='http://127.0.0.1:8240')
a = p.parse_args()
c = AxiomClient(a.api)
b = json.loads((a.root/'power/binding.json').read_text())
project = b['project_id']
checks = []

def rejected(name, fn, status):
    try:
        fn()
    except AxiomError as exc:
        assert exc.status_code == status, (name, exc.status_code, exc.detail)
        checks.append(dict(check=name, status_code=exc.status_code, reason=exc.detail))
    else:
        raise AssertionError('Expected rejection: '+name)

body = {k: b[k] for k in ('project_id','name','source_kind','source_ref','profile_id','max_lateness_ms','additional_faults','model_coupling')}
def invalid_save(name, change, status=422):
    draft = copy.deepcopy(body)
    change(draft)
    rejected(name, lambda: c.save_hil_binding(draft, idempotency_key='model-negative-'+uuid.uuid4().hex), status)

def swap(d):
    v=d['model_coupling']
    v['baseline_execution'],v['faulted_execution']=v['faulted_execution'],v['baseline_execution']
invalid_save('swapped native normal/fault runs', swap)
invalid_save('physical target cannot use reference model replay', lambda d: d.update(profile_id='axiom-two-ecu-hardware'))
invalid_save('additional fault cannot silently contaminate model A/B', lambda d: d.update(additional_faults=[dict(kind='os_event_drop',start_ms=3000,end_ms=3100)]))
invalid_save('pair hash mismatch', lambda d: d['model_coupling']['pair_ref'].update(sha256='0'*64), 409)
rejected('other project cannot restore binding',lambda:c.hil_binding(b['id'],revision=1,project_id='default'),404)
original = c.vecu_network_record('tests','vecunet_ca9cae2294094e88aecee1f4a4408f2b',revision=1,project_id=project)
invalid_save('faulted source cannot silently contaminate model A/B',lambda d:d.update(source_ref={k:original[k] for k in ('id','revision','sha256')}))

catalog=c.hil_model_injections(project_id=project)['items']
for boundary in ('camera_raw_csi','fpga','power','analog'):
    folder=a.root/boundary
    saved=json.loads((folder/'binding.json').read_text())
    actual=c.hil_binding(saved['id'],revision=saved['revision'],project_id=project)
    assert actual==saved
    run=json.loads((folder/'result.json').read_text())
    assert hashlib.sha256(c.export_hil_bridge_execution(run['id'],project_id=project)).hexdigest()==run['manifest_hash']
    assert any(x['id']==saved['model_coupling']['pair_ref']['id'] for x in catalog)
    checks.append(dict(check=boundary+' exact binding restore, pair listing and sealed export',passed=True))

old=c.hil_binding('hilbind_67abc996fda1408ab4fcc21b0a0a7299',revision=1,project_id=project)
assert 'model_coupling' not in old
for identity in ('job_a00af83b35864eafb93fd45fa3e3db52','job_aa0406a441f44decb5b6a552a310f473'):
    run=c.experiment_execution(identity)
    assert run['status']=='completed' and run['manifest_hash']
    checks.append(dict(check='retained HIL/SIL readable',execution=identity,manifest_sha256=run['manifest_hash']))
output=dict(schema_version='hil-model-api-checks/1',checks=checks,browser_workflow_verified=False)
(a.root/'api-checks.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(output,ensure_ascii=False,indent=2))
