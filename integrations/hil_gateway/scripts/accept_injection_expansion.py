"""Explicit native acceptance via the same versioned SDK as the product. Resumes saved jobs."""
import argparse,hashlib,json,time
from pathlib import Path
from axiom_sdk import AxiomClient
p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--api',default='http://127.0.0.1:8240');p.add_argument('--output',type=Path,required=True)
p.add_argument('--run-key',default='r1');p.add_argument('--phase',choices=['reference','models'],required=True)
p.add_argument('--case',choices=['reset','os-event','restbus-reset'])
a=p.parse_args()
if not a.execute:raise SystemExit('Pass --execute to submit native runs')
a.output.mkdir(parents=True,exist_ok=True);c=AxiomClient(a.api,timeout=180)
def save(folder,name,value):
    folder.mkdir(parents=True,exist_ok=True);(folder/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def collect(folder,job,project='default',bridge=False):
    previous=None;deadline=time.monotonic()+3600
    while time.monotonic()<deadline:
        run=c.hil_bridge_execution(job['id'],project_id=project) if bridge else c.experiment_execution(job['id'])
        if run['status']!=previous:print(json.dumps(dict(execution=job['id'],status=run['status'])),flush=True);previous=run['status']
        if run['status'] not in ('pending','queued','running','cancel_requested'):break
        time.sleep(2)
    else:raise TimeoutError('Native run deadline; inspect original submitted job before retry')
    save(folder,'result.json',run)
    if bridge:
        export=c.export_hil_bridge_execution(job['id'],project_id=project)
        assert hashlib.sha256(export).hexdigest()==run['manifest_hash'];(folder/'export.json').write_bytes(export)
    else:save(folder,'export.json',c.export_engineering_lab_execution(job['id']))
    wanted={'vecu-trace.json','hil-causality.json','hil-bridge-provenance.json','hil-native-diagnostics.json','engineering-trace.json','adas-trace.json','adas-summary.json','engineering-summary.json','trace.json','frames.json','summary.json','provenance.json','test.json','consumer.json'}
    for item in run['artifacts']:
        if item['name'] in wanted or item['name'].endswith('stderr.log'):
            path=folder/item['name'];c.download_experiment_artifact(run['id'],item['id'],path)
            assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
    print(json.dumps(dict(execution=job['id'],verdict=(run.get('summary') or {}).get('verdict'),error=run.get('error')),ensure_ascii=False),flush=True)
    if run['status']!='completed':raise RuntimeError('Native run failed; original job/artifacts retained')
    return run
if a.phase=='reference':
    project='project_9f48a965d4194be0b18859e334e007bb'
    original=c.vecu_network_record('tests','vecunet_ca9cae2294094e88aecee1f4a4408f2b',revision=1,project_id=project)
    cases={
        'reset':[dict(kind='ecu_reset',start_ms=3000,end_ms=3200)],
        'os-event':[dict(kind='os_event_drop',start_ms=3000,end_ms=7000)],
        'restbus-reset':[dict(kind='restbus_drop',start_ms=3000,end_ms=7000),dict(kind='ecu_reset',start_ms=6900,end_ms=7100)],
    }
    for name,faults in cases.items():
        if a.case and name!=a.case:continue
        folder=a.output/name
        fields={k:original[k] for k in ('project_id','network_ref','mode','hil_test_ref','seed')}
        test=c.save_vecu_network_record('tests',fields|dict(name='HIL 참조 내부 주입 · '+name,faults=faults),idempotency_key='injection-expansion-source-'+name)
        binding=c.save_hil_binding(dict(project_id=project,name='HIL 참조 내부 주입 · '+name,source_ref={k:test[k] for k in ('id','revision','sha256')}),idempotency_key='injection-expansion-binding-'+name)
        save(folder,'binding.json',binding)
        submitted=folder/'submitted.json'
        if submitted.exists():job=json.loads(submitted.read_text())
        else:
            save(folder,'preview.json',c.preview_hil_binding(binding));job=c.execute_hil_binding(binding,idempotency_key='injection-expansion-run-'+name+'-'+a.run_key);save(folder,'submitted.json',job)
        collect(folder,job,project,True)
else:
    for item in c.hil_model_injection_catalog()['items']:
        name=item['boundary'];folder=a.output/name
        pair=c.save_hil_model_injection(dict(name='경계 주입 · '+item['title'],settings=item['settings']),idempotency_key='native-injection-pair-'+name)
        save(folder,'pair.json',pair)
        submitted=folder/'submitted.json'
        if submitted.exists():jobs=json.loads(submitted.read_text())
        else:jobs=c.execute_hil_model_injection(pair,idempotency_key='native-injection-run-'+name);save(folder,'submitted.json',jobs)
        for side,job in jobs.items():collect(folder/side,job)
