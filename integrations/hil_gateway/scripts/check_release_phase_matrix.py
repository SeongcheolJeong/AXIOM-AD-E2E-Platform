"""Sealed legacy code vs new monitor over the bounded release-boundary phase grid."""
import hashlib,importlib.util,json
from pathlib import Path
from axiom_hil_gateway.model_monitor import ModelMonitor
root=Path('docs/validation/hil-release-qualification-20261018');legacy=root/'legacy-model-monitor.py'
assert hashlib.sha256(legacy.read_bytes()).hexdigest()=='4f62394e6606a98b49b2b97233c2a3550b2efd26b4b473f1e59f05daa1a43445'
spec=importlib.util.spec_from_file_location('sealed_legacy_monitor',legacy);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
q=dict(algorithm='fresh-window-v2',period_ms=50,fresh_windows=2,max_age_ms=10)
def run(cls,onset,phase,qualified):
 p=dict(boundary='multiple',combination='any_protect',members={n:dict(boundary=n,min_hold_ms=500,recovery_samples=3,power_min_v=8,analog_min_v=2,analog_max_v=3) for n in ('power','fpga')})
 if qualified:p['release_qualification']=q
 m=cls(p);output=[]
 for seq in range(125):
  t=seq*50;bad=3050<=t<=3350 or onset<t<=onset+350
  sample=dict(sequence=seq,samples=dict(power=dict(sequence=seq,phase='native_replay',min_v=6 if bad else 12),fpga=dict(sequence=seq,phase='native_replay',abort_count=int(t in (3050,3850)),last_status='aborted' if t in (3050,3850) else 'committed')))
  now=t+phase+(0 if phase==0 or seq%2 else -.25);r=m.step(sample,seq,now)
  output.append(dict(sequence=seq,input_ms=t,protect=r['protect'],state=r['state'],release_guard=r.get('release_guard'),active=r['active_boundaries']))
 last_bad=max(i for i in range(125) if onset<i*50<=onset+350)
 gaps=[r['input_ms'] for r in output[61:last_bad+10] if not r['protect']]
 assert any(not r['protect'] for r in output[last_bad+12:])
 return output,gaps
cases=[]
for onset in range(4301,4401):
 for phase in (0,1,5,9,10):
  old,og=run(module.ModelMonitor,onset,phase,False);new,ng=run(ModelMonitor,onset,phase,True)
  assert not ng
  cases.append(dict(onset_ms=onset,ecu_phase_ms=phase,legacy_gap_input_ms=og,qualified_gap_input_ms=ng))
old,_=run(module.ModelMonitor,4350,5,False);new,_=run(ModelMonitor,4350,5,True)
value=dict(schema_version='hil-release-phase-matrix/1',evidence='authored monitor fixtures; not native CAN/CARLA',cases=cases,case_count=len(cases),legacy_cases_with_gap=sum(bool(r['legacy_gap_input_ms']) for r in cases),qualified_cases_with_gap=0,
 representative=dict(onset_ms=4350,ecu_phase_ms=5,legacy=old,qualified=new),qualification=q,
 scope='Onset4301..4400ms, phases0/1/5/9/10ms and alternating0.25ms jitter; not an arbitrary-future-fault or physical-safety guarantee',physical_validation=False)
(root/'phase-matrix.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in value.items() if k not in ('cases','representative')},ensure_ascii=False))
