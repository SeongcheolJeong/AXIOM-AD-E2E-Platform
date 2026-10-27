"""Independent full-wire, native-envelope and release-candidate acceptance."""
import argparse,hashlib,json,struct
from pathlib import Path
from axiom_sdk import AxiomClient
root=Path('docs/validation/hil-pending-fpga-20261018');c=AxiomClient('http://127.0.0.1:8240',timeout=180)
parser=argparse.ArgumentParser();parser.add_argument('--case',type=int);args=parser.parse_args()
read=lambda p:json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
reports=[]
for onset in [5250]:
 p=root;r=read(p/'result.json');b=read(p/'binding.json');trace=read(p/'vecu-trace.json')
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
    assert job['summary']['requested_frames']==300 and job['summary']['source_window_ns']==2500000000 and job['summary']['clock_hz']==100000000 and job['summary']['width']==1920 and job['summary']['height']==1080
    assert job['summary']['committed_mismatch_count']==job['summary']['protocol_error_count']==0
    assert all(len([f for f in native[n]['frames'] if f['channel']==ch and f['status']=='committed'])==75 for ch in (1,2,3))
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
 assert len(detections)==5 and len(groups)==1
 late=next(v for v in detections if v['reason']=='model_fpga' and v['time_ms']>5250)
 rows=trace['sides']['faulted'];pending=rows[105]['reply']['bridge']['model_monitor'];cancelled=rows[106]['reply']['bridge']['model_monitor']
 assert pending['release_guard']['pending'] and not pending['active_boundaries']
 candidate=pending['release_guard']['candidate'];assert candidate and candidate['required_sequence']>=106
 assert cancelled['active_boundaries']==['fpga'] and cancelled['release_guard']['candidate'] is None
 assert cancelled['release_guard']['reason']=='active_fault' and cancelled['protect']
 assert rows[105]['reply']['control']['brake']==rows[106]['reply']['control']['brake']==1
 end=groups[0]['time_ms'];begin=min(v['time_ms'] for v in detections)
 assert end>late['time_ms']+500
 gaps=[]
 for v in e:
  if not begin<=v['time_ms']<end:continue
  key='protected_can' if v['kind']=='can_tx' and v.get('ecu')=='adas' else 'protected_brake' if v['kind']=='brake_output_observed' else None
  if key:
   counts[key]+=1
   if struct.unpack('<HHhBB',bytes.fromhex(v['data_hex']))[1]!=10000:gaps.append(v)
 assert not gaps
 last_candidate=candidates[-1]
 assert last_candidate['sequence']>candidate['required_sequence'] and last_candidate['time_ms']>=late['measurement']['last_bad_ms']+500
 assert groups[0]['measurement']['release_guard']['confirmed_sequence']>=last_candidate['required_sequence']
 native=json.loads((p/'fpga-faulted-frames.json').read_text());aborts=[f for f in native['frames'] if f['channel']==0 and f['status']=='aborted']
 assert len(aborts)==3 and [f['frame'] for f in aborts]==[0,24,67]
 assert 2250<=aborts[-1]['terminal_ns']/1e6<2250.2 and aborts[-1]['loss_reason']=='fifo_overflow'
 assert len([f for f in native['frames'] if f['status']=='committed'])==297
 release=next(v for v in rows if v['reply']['bridge']['observed_time_ms']>=end and v['reply']['control']['brake']==0)
 motion=next(v for v in rows if v['step']>=release['step'] and v['next_ego']['speed_mps']>.1)
 markers=[v for v in e if v['kind']=='fault_start'];assert len(markers)==5 and all(0<=v['lateness_ms']<=5 for v in markers)
 assert next(v for v in markers if v['scheduled_ms']==5250)['fault']['end_ms']==5250.2
 old=json.loads(Path('docs/validation/hil-release-qualification-20261018/4350/checks.json').read_text())
 report=dict(schema_version='hil-pending-fpga-checks/1',execution=r['id'],manifest_sha256=r['manifest_hash'],counts=counts,
  no_transient_release=True,cancelled_candidate=candidate,cancellation_observed_input_ms=5300,
  cancellation_readback_ms=rows[106]['reply']['bridge']['observed_time_ms'],cancellation_evidence='reference_process_state_transition',
  late_detection=late,native_aborts=aborts,candidates=candidates,final_candidate=last_candidate,group_recovery_ms=end,
  release_input_ms=release['step']*50,motion_carla_ms=(motion['step']+1)*50,
  previous_execution=old['execution'],previous_group_recovery_ms=old['group_recovery_ms'],previous_motion_carla_ms=old['motion_carla_ms'],
  browser_workflow_verified=False,physical_validation=False,hard_realtime=False)
 save(p/'checks.json',report);print(json.dumps({k:v for k,v in report.items() if k not in ('late_detection','native_aborts','candidates')},ensure_ascii=False,indent=2))
