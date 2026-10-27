"""Public API/SDK immutable readback for the pending-release recurrence case."""
import hashlib,json
from pathlib import Path
from axiom_sdk import AxiomClient
p=Path('docs/validation/hil-pending-fpga-20261018');c=AxiomClient('http://127.0.0.1:8240',timeout=180);items=[]
for path in [Path('docs/validation/hil-release-qualification-20261018/4350'),p]:
 b=json.loads((path/'binding.json').read_text());r=json.loads((path/'result.json').read_text())
 assert c.hil_binding(b['id'],revision=1,project_id=b['project_id'])==b
 preview=c.preview_hil_binding(b)
 assert c.hil_bridge_execution(r['id'],project_id=b['project_id'])==r
 assert hashlib.sha256(c.export_hil_bridge_execution(r['id'],project_id=b['project_id'])).hexdigest()==r['manifest_hash']
 cause=c.hil_bridge_causality(r['id'],project_id=b['project_id']);original=json.loads((path/'hil-causality.json').read_text())
 assert {k:cause[k] for k in original}==original
 expected=5 if path==p else 4
 assert len(cause['cases'])==expected and all(v['detection_status']=='observed' for v in cause['cases'])
 items.append(dict(execution=r['id'],preview_hash=preview['preview_hash'],manifest_sha256=r['manifest_hash'],guard=b['model_link']['policy']['release_qualification']))
report=dict(schema_version='hil-pending-fpga-api-checks/1',cases=items,retained_readback_unchanged=True,browser_workflow_verified=False,physical_validation=False)
(p/'api-checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False,indent=2))
