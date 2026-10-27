"""API/SDK exact readback, retained legacy evidence, safe legacy preview rejection."""
import hashlib,json
from pathlib import Path
from axiom_sdk import AxiomClient
p=Path('docs/validation/hil-release-qualification-20261018');c=AxiomClient('http://127.0.0.1:8240',timeout=180);items=[]
for n in (4350,4349,4351,4399):
 path=p/str(n);b=json.loads((path/'binding.json').read_text());r=json.loads((path/'result.json').read_text())
 assert c.hil_binding(b['id'],revision=1,project_id=b['project_id'])==b
 preview=c.preview_hil_binding(b)
 assert c.hil_bridge_execution(r['id'],project_id=b['project_id'])==r
 assert hashlib.sha256(c.export_hil_bridge_execution(r['id'],project_id=b['project_id'])).hexdigest()==r['manifest_hash']
 cause=c.hil_bridge_causality(r['id'],project_id=b['project_id']);original=json.loads((path/'hil-causality.json').read_text())
 assert {k:cause[k] for k in original}==original and len(cause['cases'])==4 and all(v['detection_status']=='observed' for v in cause['cases'])
 items.append(dict(onset_ms=n,execution=r['id'],preview_hash=preview['preview_hash'],manifest_sha256=r['manifest_hash'],guard=b['model_link']['policy']['release_qualification']))
old=Path('docs/validation/hil-power-recurrence-20261018');ob=json.loads((old/'binding.json').read_text());orr=json.loads((old/'result.json').read_text())
assert c.hil_binding(ob['id'],revision=1,project_id=ob['project_id'])==ob
assert c.hil_bridge_execution(orr['id'],project_id=ob['project_id'])==orr
assert hashlib.sha256(c.export_hil_bridge_execution(orr['id'],project_id=ob['project_id'])).hexdigest()==orr['manifest_hash']
try:c.preview_hil_binding(ob)
except Exception as exc:
 assert 'Native model evidence changed after binding save' in str(exc),str(exc)
 legacy=dict(new_preview_rejected=True,error=str(exc),retained_readback_unchanged=True)
else:raise AssertionError('Legacy immediate-release binding must be saved with the new contract before new execution')
report=dict(schema_version='hil-release-api-checks/1',cases=items,legacy=legacy,browser_workflow_verified=False,physical_validation=False)
(p/'api-checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False,indent=2))
