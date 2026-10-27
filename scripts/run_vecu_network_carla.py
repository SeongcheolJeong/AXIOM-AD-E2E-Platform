"""Normal SDK execution of the saved native-network CARLA acceptance cases."""
import argparse,json,time,uuid
from pathlib import Path
import httpx
from axiom_sdk import AxiomClient
p=argparse.ArgumentParser();p.add_argument('--api',default='http://127.0.0.1:8230');p.add_argument('--project',default='project_9f48a965d4194be0b18859e334e007bb');p.add_argument('--output',type=Path,required=True);p.add_argument('--fault',default='udp_repeat');p.add_argument('--author-only',action='store_true');a=p.parse_args()
a.output.mkdir(parents=True,exist_ok=True);client=AxiomClient(a.api,timeout=120)
def ref(x):return {k:x[k] for k in ('id','revision','sha256')}
if (a.output/'records.json').exists():records=json.loads((a.output/'records.json').read_text())
else:
    hil=httpx.get(a.api+'/api/v1/hil-sim/tests',params={'project_id':a.project}).json()['items']
    source=next(t for t in hil if t.get('scene_profile',{}).get('id')=='lead_deceleration')
    network=client.save_vecu_network_record('networks',dict(project_id=a.project,name='Four ECU · SIL Kit / public BSW / Trampoline'),idempotency_key=str(uuid.uuid4()))
    tests={}
    for fault in ['udp_delay','udp_drop','udp_repeat','can_command_drop','restbus_drop','ecu_reset','os_event_drop']:
        tests[fault]=client.save_vecu_network_record('tests',dict(project_id=a.project,name='Native network CARLA · '+fault+' · 3–7 s',network_ref=ref(network),mode='carla_closed_loop',hil_test_ref=ref(source),seed=42,faults=[dict(kind=fault,start_ms=3000,end_ms=7000)]),idempotency_key=str(uuid.uuid4()))
    records=dict(network=network,tests=tests);(a.output/'records.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
if not a.author_only:
    test=records['tests'][a.fault];preview=client.preview_vecu_network_test(test)
    (a.output/(a.fault+'-preview.json')).write_text(json.dumps(preview,ensure_ascii=False))
    job=client.execute_vecu_network_test(test,idempotency_key=str(uuid.uuid4()));identity=job['id'];print(identity,flush=True)
    (a.output/(a.fault+'-submitted.json')).write_text(json.dumps(job,ensure_ascii=False))
    while True:
        result=client.vecu_network_execution(identity,project_id=a.project)
        if result['status'] not in ('pending','queued','running'):break
        time.sleep(3)
    (a.output/(a.fault+'-result.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    if result['status']!='completed':raise RuntimeError(json.dumps(result,ensure_ascii=False)[-5000:])
    (a.output/(a.fault+'-export.json')).write_bytes(client.export_vecu_network_execution(identity,project_id=a.project))
    print(json.dumps({'id':identity,'status':result['status'],'url':f'http://127.0.0.1:5330/#hil-sim?project={a.project}&mode=vecu&tab=network&test={test["id"]}&revision={test["revision"]}&execution={identity}&step=100'},ensure_ascii=False),flush=True)
