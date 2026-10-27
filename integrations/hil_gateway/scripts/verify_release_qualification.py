"""Independent full-wire, native-envelope and release-candidate acceptance."""
import argparse,hashlib,json,struct
from pathlib import Path
from axiom_sdk import AxiomClient
root=Path('docs/validation/hil-release-qualification-20261018');c=AxiomClient('http://127.0.0.1:8240',timeout=180)
parser=argparse.ArgumentParser();parser.add_argument('--case',type=int);args=parser.parse_args()
read=lambda p:json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
reports=[]
for onset in ([args.case] if args.case else [4350,4349,4351,4399]):
 p=root/str(onset);r=read(p/'result.json');b=read(p/'binding.json');trace=read(p/'vecu-trace.json')
 assert r['status']=='completed' and r['summary']['verdict']=='passed' and r['summary']['validity_errors']=={'baseline':[],'faulted':[]}
 assert hashlib.sha256((p/'export.json').read_bytes()).hexdigest()==r['manifest_hash']
 assert c.hil_binding(b['id'],revision=1,project_id=b['project_id'])==b
 assert b['model_link']['policy']['release_qualification']==dict(algorithm='fresh-window-v2',period_ms=50,fresh_windows=2,max_age_ms=10)
 links={m['boundary']:m for m in b['model_link']['members']};events={};counts=dict(wire_controls=0,native_envelopes=0,protected_can=0,protected_brake=0);candidates=[]
 for side,rows in trace['sides'].items():
  assert len(rows)==360;events[side]=[e for row in rows for e in row['reply']['bridge']['events']]
  tx=[e['sample'] for e in events[side] if e['kind']=='model_measurement_tx'];rx=[e['sample'] for e in events[side] if e['kind']=='model_measurement_rx']
  assert len(tx)==360 and tx==rx
  native={}
  for n,l in links.items():
   src=l['runs'][side];job=c.experiment_execution(src['execution_id']);assert job['manifest_hash']==src['manifest_sha256']
   a=next(a for a in job['artifacts'] if a['id']==src['artifact_id']);assert a['sha256']==src['artifact_sha256']
   dest=p/f'{n}-{side}-{src["artifact_name"]}';c.download_experiment_artifact(job['id'],a['id'],dest)
   assert hashlib.sha256(dest.read_bytes()).hexdigest()==a['sha256'];native[n]=read(dest)
   if n=='fpga':
    assert job['summary']['requested_frames']==120 and job['summary']['clock_hz']==100000000 and job['summary']['width']==1920 and job['summary']['height']==1080
    assert job['summary']['committed_mismatch_count']==job['summary']['protocol_error_count']==0
    assert all(len([f for f in native[n]['frames'] if f['channel']==ch and f['status']=='committed'])==30 for ch in (1,2,3))
  voltage=native['power']['samples'];cursor=0;held=voltage[0]['v(out)'];started=False
  for row in rows:
   step=row['step'];reply=row['reply'];m=reply['bridge']['model_monitor'];g=m['release_guard'];active=any(v['protect'] for v in m['members'].values())
   assert m['protect']==(active or g['pending']) and not m['missing'] and g['fresh'] and 0<=g['input_age_ms']<=10
   assert m['sample']==tx[step]
   wire=struct.unpack('<HHhBB',bytes.fromhex(reply['bridge']['brake_feedback']['data_hex']))
   assert reply['control']==dict(throttle=wire[0]/10000,brake=wire[1]/10000,steer=wire[2]/32767);counts['wire_controls']+=1
   if m['protect']:assert reply['control']['brake']==1
   if side=='baseline':assert not m['protect']
   if g['pending']:
    candidate=g['candidate'];assert candidate and candidate['required_sequence']==candidate['sequence']+2
    assert not active and reply['control']['brake']==1 and m['state']=='recovering'
    if not started:candidates.append(dict(side=side,input_ms=step*50,**candidate));started=True
   else:started=False
   for n,member in m['members'].items():
    packet=member['sample'];t=step*50-links[n]['start_ms']
    if t<0:continue
    if n=='power':
     values=[held]
     while cursor<len(voltage) and voltage[cursor]['time']*1000<=t:
      held=voltage[cursor]['v(out)'];values.append(held);cursor+=1
     assert packet['min_v']==min(values) and packet['max_v']==max(values)
    else:
     frames=[f for f in native[n]['frames'] if f['channel']==packet['channel']]
     interval=[f for f in frames if t-50<f['terminal_ns']/1e6<=t]
     arrived=sorted([f for f in frames if f['terminal_ns']/1e6<=t],key=lambda f:f['terminal_ns'])
     assert packet['abort_count']==sum(f['status']=='aborted' for f in interval)
     assert packet['commit_count']==sum(f['status']=='committed' for f in interval)
     assert packet['last_status']==(arrived[-1]['status'] if arrived else 'pending')
    counts['native_envelopes']+=1
  assert r['summary']['carla_result']['sides'][side]['cleanup_status']=='confirmed'
 e=events['faulted'];detections=[v for v in e if v['kind']=='detection' and v.get('reason','').startswith('model_')];groups=[v for v in e if v['kind']=='model_group_recovered']
 assert len(detections)==4 and len(groups)==1
 end=groups[0]['time_ms'];begin=min(v['time_ms'] for v in detections)
 assert groups[0]['measurement']['release_guard']['confirmed_sequence'] is not None
 assert candidates and groups[0]['measurement']['release_guard']['confirmed_sequence']>=candidates[-1]['required_sequence']
 gaps=[]
 for v in e:
  if not begin<=v['time_ms']<end:continue
  key='protected_can' if v['kind']=='can_tx' and v.get('ecu')=='adas' else 'protected_brake' if v['kind']=='brake_output_observed' else None
  if key:
   counts[key]+=1
   if struct.unpack('<HHhBB',bytes.fromhex(v['data_hex']))[1]!=10000:gaps.append(v)
 assert not gaps and counts['protected_can']>40 and counts['protected_brake']>200
 rows=trace['sides']['faulted'];release=next(v for v in rows if v['reply']['bridge']['observed_time_ms']>=end and v['reply']['control']['brake']==0)
 movement=next(v for v in rows if v['step']>=release['step'] and v['next_ego']['speed_mps']>.1)
 assert movement['step']-release['step']<=20
 report=dict(schema_version='hil-release-qualification-case/1',onset_ms=onset,execution=r['id'],manifest_sha256=r['manifest_hash'],counts=counts,no_transient_release=True,
  candidates=candidates,group_recovery_ms=end,release_input_ms=release['step']*50,motion_carla_ms=(movement['step']+1)*50,detections=[dict(reason=v['reason'],time_ms=v['time_ms']) for v in detections],
  guard=groups[0]['measurement']['release_guard'],browser_workflow_verified=False,physical_validation=False,hard_realtime=False)
 save(p/'checks.json',report);reports.append(report);print(json.dumps({k:v for k,v in report.items() if k not in ('detections','candidates','guard')},ensure_ascii=False),flush=True)
if not args.case:
 save(root/'campaign.json',dict(schema_version='hil-release-qualification-campaign/1',cases=reports,all_passed=True,
  totals={k:sum(r['counts'][k] for r in reports) for k in reports[0]['counts']},source_onsets_ms=[r['onset_ms'] for r in reports],physical_validation=False,browser_workflow_verified=False))
