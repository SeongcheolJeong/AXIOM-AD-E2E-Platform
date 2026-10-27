"""Actual host vECU replay and optional CARLA through the public SDK; no service starts."""
import argparse,json,hashlib,time
from pathlib import Path
from uuid import uuid4
from axiom_sdk import AxiomClient

def ref(value):return {k:value[k] for k in ('id','revision','sha256')}
def main():
 p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:8200');p.add_argument('--ui',default='http://127.0.0.1:5300');p.add_argument('--project',default='project_9f48a965d4194be0b18859e334e007bb');p.add_argument('--output',default='docs/validation/vecu-20261017.json');p.add_argument('--hil-test',default='hiltest_73456bf4c2fd44aabb7ec521295025bb');p.add_argument('--resume',action='store_true');a=p.parse_args()
 output=Path(a.output);folder=output.parent/(output.stem+'-artifacts');folder.mkdir(parents=True,exist_ok=True)
 if a.resume:d=json.loads(output.read_text())
 else:
  if output.exists():raise ValueError('Receipt exists; use --resume')
  d=dict(schema_version='vecu-validation/1',key=str(uuid4()),api=a.url,project=a.project,runs={})
 if d['api']!=a.url or d['project']!=a.project:raise ValueError('Resume scope differs')
 def save():output.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
 save()
 with AxiomClient(a.url,timeout=180) as c:
  if 'package' not in d:d['package']=c.save_vecu_record('packages',dict(project_id=a.project,name='ADAS reference vECU',runtime_id='reference-traffic-dut'),idempotency_key=d['key']+'-package');save()
  if 'source' not in d:d['source']=c.save_vecu_record('inputs',c.vecu_catalog(project_id=a.project)['input_example']|{'project_id':a.project},idempotency_key=d['key']+'-input');save()
  cases=[('normal',[]),('delay',[dict(kind='delay',start_step=80,end_step=120)]),('dropout',[dict(kind='dropout',start_step=80,end_step=120)]),('repeat',[dict(kind='repeat',start_step=80,end_step=120)]),('settings',[dict(kind='delay',start_step=80,end_step=120)])]
  for name,faults in cases:
   if name not in d['runs']:
    body=dict(project_id=a.project,name='vECU · '+name,package_ref=ref(d['package']),input_ref=ref(d['source']),faults=faults)
    if name=='settings':body.update(comparison='settings_ab',variant=dict(max_object_age_ms=100,recovery_fresh_steps=5))
    d['runs'][name]={'test':c.save_vecu_record('tests',body,idempotency_key=d['key']+'-'+name+'-save')};save()
  for n in (2,3):d['runs'].setdefault('delay-repeat-'+str(n),{'test':d['runs']['delay']['test']})
  if a.hil_test and 'carla' not in d['runs']:
   tests=c._request('GET','/hil-sim/tests',params={'project_id':a.project}).json()['items'];h=next(t for t in tests if t['id']==a.hil_test)
   d['runs']['carla']={'test':c.save_vecu_record('tests',dict(project_id=a.project,name='vECU · CARLA closed loop',mode='carla_closed_loop',package_ref=ref(d['package']),hil_test_ref=ref(h),seed=h['experiment']['seed']),idempotency_key=d['key']+'-carla-save')};save()
  for name,item in d['runs'].items():
   if 'job' not in item:
    try:item['job']=c.execute_vecu_test(item['test'],idempotency_key=d['key']+'-'+name)
    except Exception as e:item.update(preflight_error=str(e),status='not_executed');save();print(name,'BLOCKED',str(e),flush=True);continue
    save()
   print(name,item['job']['id'],flush=True)
   start=time.monotonic()
   while True:
    run=c.experiment_execution(item['job']['id'])
    if run['status'] in {'completed','failed','cancelled','interrupted'}:break
    if time.monotonic()-start>1200:raise TimeoutError('Run still active; --resume')
    time.sleep(2)
   item.update(status=run['status'],summary=run.get('summary'),error=run.get('error'));save()
   if run['status']!='completed':print(name,run['status'],run.get('error'),flush=True);continue
   dest=folder/name;dest.mkdir(exist_ok=True);verified=[]
   for art in run['artifacts']:
    path=dest/art['name'];path.parent.mkdir(parents=True,exist_ok=True)
    if not path.resolve().is_relative_to(dest.resolve()):raise ValueError('Unsafe artifact')
    if not path.exists():c.download_experiment_artifact(run['id'],art['id'],path)
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    if sha!=art['sha256']:raise ValueError('Artifact hash differs')
    verified.append(dict(name=art['name'],sha256=sha))
   export=dest/'execution-export.json';c.export_experiment_execution(run['id'],export)
   if hashlib.sha256(export.read_bytes()).hexdigest()!=run['manifest_hash']:raise ValueError('Export differs')
   item.update(verified_artifacts=verified,manifest_hash=run['manifest_hash'],url=f"{a.ui}/#hil-sim?project={a.project}&mode=vecu&test={item['test']['id']}&revision={item['test']['revision']}&execution={run['id']}&step=100");save()
   print(name,run['status'],run['summary']['sides'],flush=True)
  repeats=[d['runs'][k] for k in ('delay','delay-repeat-2','delay-repeat-3')]
  d['three_repeat_logical_hashes_equal']=all(r.get('status')=='completed' for r in repeats) and len({json.dumps({k:v['logical_sha256'] for k,v in r['summary']['sides'].items()},sort_keys=True) for r in repeats})==1
  d['all_runs_completed']=all(r.get('status')=='completed' for r in d['runs'].values());d['physical_hil']='not_configured';save()
if __name__=='__main__':main()
