"""Normal SDK binding/run/export workflow. Only --execute submits a new bench run."""
import argparse,json,time
from pathlib import Path
from axiom_sdk import AxiomClient
p=argparse.ArgumentParser()
p.add_argument('--api',default='http://127.0.0.1:8240');p.add_argument('--project',required=True)
p.add_argument('--test',required=True);p.add_argument('--revision',type=int,required=True)
p.add_argument('--output',type=Path,required=True);p.add_argument('--execute',action='store_true')
a=p.parse_args();client=AxiomClient(a.api,timeout=120)
a.output.mkdir(parents=True,exist_ok=True)
def save(name,value):
    (a.output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
test=client.vecu_network_record('tests',a.test,revision=a.revision,project_id=a.project)
source={k:test[k] for k in ('id','revision','sha256')}
binding=client.save_hil_binding(dict(project_id=a.project,name='vCAN connection · '+test['name'],source_ref=source),
                                idempotency_key='hil-accept-binding-'+test['sha256'])
save('binding.json',binding)
if a.execute:
    save('preview.json',client.preview_hil_binding(binding))
    # Resume the exact submitted run on repeated invocation; never silently replace evidence.
    submitted=a.output/'submitted.json'
    if submitted.exists():
        job=json.loads(submitted.read_text())
    else:
        job=client.execute_hil_binding(binding,idempotency_key='hil-accept-execute-'+binding['sha256']);save('submitted.json',job)
    while True:
        run=client.hil_bridge_execution(job['id'],project_id=a.project)
        if run['status'] not in ('pending','queued','running','cancel_requested'):break
        time.sleep(2)
    save('result.json',run)
    (a.output/'export.json').write_bytes(client.export_hil_bridge_execution(job['id'],project_id=a.project))
    if run['status']!='completed':raise SystemExit('Execution did not complete: '+str(run.get('error')))
    save('causality.json',client.hil_bridge_causality(job['id'],project_id=a.project))
    print(json.dumps(dict(execution=job['id'],verdict=run['summary']['verdict'],physical_validation=False)))
    if run['summary']['verdict']!='passed':raise SystemExit('Timing acceptance did not pass; do not lower the threshold to hide this result')
else:print(json.dumps(dict(binding=binding['id'],revision=binding['revision'],execution='not_requested')))
