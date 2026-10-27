"""Public SDK readback/preview/export checks, independent of blocked browser workflow."""
from pathlib import Path
import hashlib,json
from axiom_sdk import AxiomClient
p=Path('docs/validation/hil-model-recurrence-20261018/overflow-200us');c=AxiomClient('http://127.0.0.1:8240',timeout=180)
read=lambda n:json.loads((p/n).read_text());b=read('binding.json');r=read('result.json');source=read('source.json');pair=read('fpga-pair.json');jobs=read('fpga-model-jobs.json')
assert c.hil_binding(b['id'],revision=1,project_id=b['project_id'])==b
preview=c.preview_hil_binding(b)
assert len(b['injections'])==3 and b['injections'][2]['start_ms']==3800 and b['injections'][2]['end_ms']==3800.2
assert c.hil_bridge_execution(r['id'],project_id=b['project_id'])==r
assert hashlib.sha256(c.export_hil_bridge_execution(r['id'],project_id=b['project_id'])).hexdigest()==r['manifest_hash']
causal=c.hil_bridge_causality(r['id'],project_id=b['project_id']);artifact=read('hil-causality.json')
assert {k:causal[k] for k in artifact}==artifact and all(v['detection_status']=='observed' for v in causal['cases'])
assert causal['execution_id']==r['id'] and causal['manifest_sha256']==r['manifest_hash']
assert causal['artifact_sha256']==hashlib.sha256((p/'hil-causality.json').read_bytes()).hexdigest()
single=c.save_hil_model_loop(source,pair,jobs,name='재발 FPGA 단독 연결 표식 인수 · 실행 안 함',idempotency_key='recurrent-fpga-single-preview-20261018-v2')
assert len(single['injections'])==2 and single['injections'][1]['start_ms']==3800
assert len(single['model_link']['injections'])==2
single_preview=c.preview_hil_binding(single)
(p/'single-recurrence-binding.json').write_text(json.dumps(single,ensure_ascii=False,indent=2)+'\n')
report=dict(schema_version='hil-recurrence-api-checks/1',binding_restored_exact=True,compound_injections=b['injections'],
 all_causality_detections_observed=True,execution_restored_exact=True,export_sha256=r['manifest_hash'],
 preview_hash=preview['preview_hash'],single_binding=single['id'],single_preview_hash=single_preview['preview_hash'],
 single_recurrence_markers_preserved=True,single_executed=False,browser_workflow_verified=False,physical_validation=False)
(p/'api-checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False,indent=2))
