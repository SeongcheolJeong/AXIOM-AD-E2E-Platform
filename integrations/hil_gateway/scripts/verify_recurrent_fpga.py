"""Independent native measurement, per-cause latch, wire-control and vehicle checks."""
import argparse
import hashlib
import json
import struct
from pathlib import Path
from axiom_sdk import AxiomClient

parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path('docs/validation/hil-model-recurrence-20261018/overflow-200us'));parser.add_argument('--first-recovery',choices=['power','fpga'],default='fpga');args=parser.parse_args()
p=args.root;first=args.first_recovery;last='fpga' if first=='power' else 'power'
c=AxiomClient('http://127.0.0.1:8240',timeout=180)
run=json.loads((p/'result.json').read_text());binding=json.loads((p/'binding.json').read_text())
trace=json.loads((p/'vecu-trace.json').read_text());causes=json.loads((p/'hil-causality.json').read_text())
assert run['status']=='completed' and run['summary']['verdict']=='passed'
assert run['summary']['validity_errors']==dict(baseline=[],faulted=[])
assert run['summary']['model_link']==binding['model_link']
assert binding['model_link']['combination']=='any_protect'
assert c.hil_binding(binding['id'],revision=binding['revision'],project_id=binding['project_id'])==binding
assert hashlib.sha256(c.export_hil_bridge_execution(run['id'],project_id=binding['project_id'])).hexdigest()==run['manifest_hash']
assert len(causes['cases'])==3
links={m['boundary']:m for m in binding['model_link']['members']}
assert set(links)=={'power','fpga'}
events={};partial=[];raw_recovery=[];brake_checks=0;measurement_checks=0
native_sources={}
for side in ('baseline','faulted'):
    rows=trace['sides'][side]
    assert len(rows)==360
    events[side]=[e for row in rows for e in row['reply']['bridge']['events']]
    sent=[e for e in events[side] if e['kind']=='model_measurement_tx']
    received=[e for e in events[side] if e['kind']=='model_measurement_rx']
    assert len(sent)==len(received)==360
    assert [e['sample'] for e in sent]==[e['sample'] for e in received]
    native={}
    for n,link in links.items():
        source=link['runs'][side];original=c.experiment_execution(source['execution_id'])
        assert original['manifest_hash']==source['manifest_sha256']
        artifact=next(a for a in original['artifacts'] if a['id']==source['artifact_id'])
        assert artifact['sha256']==source['artifact_sha256']
        path=p/f'{n}-{side}-{source["artifact_name"]}'
        c.download_experiment_artifact(original['id'],artifact['id'],path)
        assert hashlib.sha256(path.read_bytes()).hexdigest()==artifact['sha256']
        native[n]=json.loads(path.read_text())
        native_sources[n+'-'+side]=source
        if n=='fpga':
            s=original['summary']
            assert s['width']==1920 and s['height']==1080 and s['requested_frames']==120 and s['clock_hz']==100000000
            assert s['verdict']=='passed' and s['committed_mismatch_count']==s['protocol_error_count']==0
            for channel in (1,2,3):
                frames=[f for f in native[n]['frames'] if f['channel']==channel]
                assert len(frames)==30 and all(f['status']=='committed' for f in frames)
    for row in rows:
        reply=row['reply'];m=reply['bridge']['model_monitor'];step=row['step']
        assert not m['missing'] and m['sample']==sent[step]['sample']
        assert m['protect']==any(v['protect'] for v in m['members'].values())
        assert set(m['active_boundaries'])=={n for n,v in m['members'].items() if v['protect']}
        t,b,s,_,_=struct.unpack('<HHhBB',bytes.fromhex(reply['bridge']['brake_feedback']['data_hex']))
        assert reply['control']==dict(throttle=t/10000,brake=b/10000,steer=s/32767)
        brake_checks+=1
        if m['protect']:assert reply['control']['brake']==1
        if side=='baseline':assert not m['protect']
        for n,v in m['members'].items():
            sample=v['sample'];model_t=step*50-links[n]['start_ms']
            assert sample==m['sample']['samples'][n]
            if model_t>=0:
                if n=='power':
                    values=native[n]['samples']
                    chunk=[x['v(out)'] for x in values if model_t-50<x['time']*1000<=model_t]
                    prior=[x['v(out)'] for x in values if x['time']*1000<=model_t-50]
                    if prior:chunk.append(prior[-1])
                    if not chunk:chunk=[values[0]['v(out)']]
                    assert sample['min_v']==min(chunk) and sample['max_v']==max(chunk)
                else:
                    frames=[f for f in native[n]['frames'] if f['channel']==sample['channel']]
                    interval=[f for f in frames if model_t-50<f['terminal_ns']/1e6<=model_t]
                    arrived=sorted([f for f in frames if f['terminal_ns']/1e6<=model_t],key=lambda f:f['terminal_ns'])
                    assert sample['abort_count']==sum(f['status']=='aborted' for f in interval)
                    assert sample['commit_count']==sum(f['status']=='committed' for f in interval)
                    assert sample['last_status']==(arrived[-1]['status'] if arrived else 'pending')
                measurement_checks+=1
        if side=='faulted' and not m['members'][first]['protect'] and m['members'][last]['protect']:
            assert reply['control']['brake']==1 and row['next_ego']['speed_mps']<.1
            partial.append(step)
        if side=='faulted':
            power_sample=m['members']['power']['sample'];fpga_sample=m['members']['fpga']['sample']
            raw_partial=(power_sample['min_v']>=8 and (fpga_sample['abort_count'] or fpga_sample['last_status']=='aborted')) if first=='power' else (fpga_sample['last_status']=='committed' and not fpga_sample['abort_count'] and power_sample['min_v']<8)
            if step>=61 and raw_partial:
                assert reply['control']['brake']==1
                raw_recovery.append(step)
    assert run['summary']['carla_result']['sides'][side]['cleanup_status']=='confirmed'

diagnoses=[e for e in events['faulted'] if e['kind']=='detection' and e.get('reason','').startswith('model_')]
fpga_detections=[e for e in diagnoses if e['reason']=='model_fpga']
assert len(fpga_detections)==2 and len(diagnoses)==3
recovered=[e for e in events['faulted'] if e['kind']=='model_recovered']
f=[e for e in recovered if e['measurement']['boundary']=='fpga'];power=[e for e in recovered if e['measurement']['boundary']=='power']
assert len(f)==2 and len(power)==1
assert f[0]['time_ms']<3800<fpga_detections[1]['time_ms']<power[0]['time_ms']<f[1]['time_ms']
assert power[0]['aggregate_protect'] and power[0]['remaining_boundaries']==['fpga']
assert f[0]['aggregate_protect'] and f[0]['remaining_boundaries']==['power']
assert not f[1]['aggregate_protect']
for e in recovered:
    n=e['measurement']['boundary']
    assert e['source']==links[n]['runs']['faulted']
    assert e['measurement']['fresh_samples']>=links[n]['policy']['recovery_samples']
    assert e['time_ms']-e['measurement']['last_bad_ms']>=links[n]['policy']['min_hold_ms']
group=[e for e in events['faulted'] if e['kind']=='model_group_recovered']
assert len(group)==1 and group[0]['time_ms']>=f[1]['time_ms']
wire_checks={};begin=min(e['time_ms'] for e in diagnoses);end=group[0]['time_ms']
for e in events['faulted']:
    if begin<=e['time_ms']<end:
        if e['kind']=='can_tx' and e.get('ecu')=='adas':
            assert e['control']['brake']==1
            assert struct.unpack('<HHhBB',bytes.fromhex(e['data_hex']))[1]==10000
            wire_checks['adas_can']=wire_checks.get('adas_can',0)+1
        if e['kind']=='brake_output_observed':
            _,brake,_,_,_=struct.unpack('<HHhBB',bytes.fromhex(e['data_hex']))
            assert brake==10000
            wire_checks['brake_feedback']=wire_checks.get('brake_feedback',0)+1
assert wire_checks.get('adas_can',0)>20 and wire_checks.get('brake_feedback',0)>20
rows=trace['sides']['faulted']
protected=[r for r in rows if 3050<=r['step']*50<4350]
assert all(r['reply']['control']['brake']==1 for r in protected)
# The previous run restarted at input 4.0s. The recurrent case must still be stopped there.
critical=[r for r in rows if r['reply']['bridge']['observed_time_ms']>=power[0]['time_ms'] and r['step']*50<=4250]
assert len(critical)>=7
assert all(r['reply']['bridge']['model_monitor']['active_boundaries']==['fpga'] for r in critical)
assert all(r['reply']['control']['brake']==1 and r['next_ego']['speed_mps']<.1 for r in critical)
release=next(r for r in rows if r['reply']['bridge']['observed_time_ms']>=end and r['reply']['control']['brake']==0)
moving=next(r for r in rows if r['step']>=release['step'] and r['next_ego']['speed_mps']>.1)
assert moving['step']-release['step']<=20
native=json.loads((p/'fpga-faulted-frames.json').read_text())
aborted=[v for v in native['frames'] if v['channel']==0 and v['status']=='aborted']
assert len(aborted)==2 and 800<=aborted[1]['terminal_ns']/1e6<801
schedule=binding['injections'];repeat=next(i for i in schedule if i['kind']=='model_fpga' and i['start_ms']==3800)
assert repeat['end_ms']==3800.2
markers=[e for e in events['faulted'] if e['kind']=='fault_start']
assert len(markers)==3 and all(0<=e['lateness_ms']<=5 for e in markers)
assert next(e for e in markers if e['scheduled_ms']==3800)['fault']['kind']=='model_fpga'
previous=json.loads(Path('docs/validation/hil-model-recovery-order-20261018/checks.json').read_text())
report=dict(schema_version='hil-model-recurrence-checks/1',execution=run['id'],manifest_sha256=run['manifest_hash'],
    verdict=run['summary']['verdict'],native_sources=native_sources,native_aborts=aborted,
    all_wire_outputs_checked=brake_checks,post_start_measurements_checked=measurement_checks,
    uninterrupted_braking_wire_events_checked=wire_checks,protected_input_steps=[r['step'] for r in protected],
    partial_recovery_steps=partial,detections=diagnoses,member_recovery=recovered,group_recovery=group[0],
    brake_release_input_ms=release['step']*50,first_motion_input_ms=moving['step']*50,
    first_motion_carla_model_ms=(moving['step']+1)*50,first_motion_speed_mps=moving['next_ego']['speed_mps'],
    previous_execution=previous['execution'],previous_brake_release_input_ms=previous['brake_release_input_ms'],
    previous_first_motion_carla_model_ms=previous['first_motion_carla_model_ms'],
    no_transient_release_on_power_clear=True,browser_workflow_verified=False,physical_validation=False,hard_realtime=False)
(p/'checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k not in ('native_sources','detections','member_recovery','group_recovery','native_aborts','protected_input_steps')},ensure_ascii=False,indent=2))
