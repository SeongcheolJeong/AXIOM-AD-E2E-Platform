"""Inspect retained native results through the public SDK; never rerun or substitute evidence."""
from pathlib import Path
import argparse,hashlib,json,struct
from axiom_sdk import AxiomClient
p=argparse.ArgumentParser();p.add_argument('--api',default='http://127.0.0.1:8240');p.add_argument('--project',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
c=AxiomClient(a.api,timeout=120);a.output.mkdir(parents=True,exist_ok=True);report=[]
for entry in c.hil_bridge_executions(project_id=a.project)['items']:
 r=c.hil_bridge_execution(entry['id'],project_id=a.project)
 if r['status'] in ('queued','pending','running','cancel_requested'):continue
 folder=a.output/r['id'];folder.mkdir(exist_ok=True)
 export=c.export_hil_bridge_execution(r['id'],project_id=a.project)
 assert hashlib.sha256(export).hexdigest()==r['manifest_hash']
 (folder/'export.json').write_bytes(export)
 item=dict(id=r['id'],status=r['status'],name=entry['name'],manifest_sha256=r['manifest_hash'],error=r.get('error'),summary=r.get('summary'))
 if r['status']!='completed':report.append(item);continue
 for name in ('vecu-trace.json','hil-causality.json','hil-bridge-provenance.json'):
  artifact=next(v for v in r['artifacts'] if v['name']==name)
  path=folder/name
  if not path.exists():c.download_experiment_artifact(r['id'],artifact['id'],path)
  assert hashlib.sha256(path.read_bytes()).hexdigest()==artifact['sha256']
 trace=json.loads((folder/'vecu-trace.json').read_bytes());source=json.loads((folder/'hil-bridge-provenance.json').read_bytes());counts={};checks={}
 for side,rows in trace['sides'].items():
  assert len(rows)==source['source']['duration_ms']//50
  for index,row in enumerate(rows):
   assert row['step']==index
   reply=row['reply'];raw=bytes.fromhex(reply['bridge']['brake_feedback']['data_hex'])
   t,b,s,_,_=struct.unpack('<HHhBB',raw)
   assert reply['control']==dict(throttle=t/10000,brake=b/10000,steer=s/32767)
  events=[e for row in rows for e in row['reply']['bridge']['events']]
  counts[side]={kind:sum(e['kind']==kind for e in events) for kind in sorted({e['kind'] for e in events})}
  if side=='baseline':assert not any(e['kind']=='fault_start' for e in events)
  else:
   for index,fault in enumerate(source['binding']['injections']):
    started=next(e for e in events if e['kind']=='fault_start' and e['fault_index']==index)
    assert started['scheduled_ms']==fault['start_ms']
    assert any(e['kind']=='fault_end' and e['fault_index']==index for e in events)
    effect='object_queued' if fault['kind']=='udp_delay' else 'injection_repeat' if fault['kind']=='udp_repeat' else 'injection_drop'
    assert any(e['kind']==effect for e in events)
    checks[fault['kind']]=dict(injection_observed=True,recovery_boundary_observed=True)
 item.update(samples={k:len(v) for k,v in trace['sides'].items()},observed_event_counts=counts,checks=checks,
             causality=c.hil_bridge_causality(r['id'],project_id=a.project),all_brake_controls_match_received_CAN_bytes=True)
 report.append(item)
(a.output/'native-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps([dict(id=r['id'],name=r['name'],status=r['status'],verdict=(r.get('summary') or {}).get('verdict')) for r in report],ensure_ascii=False,indent=2))
