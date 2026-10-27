"""Independently compare native model artifacts, ECU reports, CAN bytes and vehicle output."""
import argparse,hashlib,json,struct
from pathlib import Path
from axiom_sdk import AxiomClient
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--api',default='http://127.0.0.1:8240');a=p.parse_args()
c=AxiomClient(a.api,timeout=180)
def artifact(run,name,folder):
    item=next(v for v in run['artifacts'] if v['name']==name);path=folder/name;folder.mkdir(exist_ok=True)
    if not path.exists():c.download_experiment_artifact(run['id'],item['id'],path)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
    return json.loads(path.read_text())
report=dict(schema_version='hil-model-loop-checks/1',physical_validation=False,hard_realtime=False,browser_workflow_verified=False,items={})
for boundary in ('camera_raw_csi','fpga','power','analog'):
    folder=a.root/boundary;run=json.loads((folder/'result.json').read_text());binding=json.loads((folder/'binding.json').read_text())
    assert run['status']=='completed' and run['summary']['verdict']=='passed', (boundary,run['summary']['validity_errors'])
    trace=artifact(run,'vecu-trace.json',folder);causes=artifact(run,'hil-causality.json',folder);link=run['summary']['model_link']
    assert link==binding['model_link'] and link['time_scale']==1 and not link['physical_hardware_connected']
    assert len(causes['cases'])==1
    events={};model_bad_steps=[]
    for side in ('baseline','faulted'):
        rows=trace['sides'][side];assert len(rows)==360
        source=link['runs'][side];native=c.experiment_execution(source['execution_id'])
        assert native['manifest_hash']==source['manifest_sha256']
        data=artifact(native,source['artifact_name'],folder/('native-'+side))
        assert next(v for v in native['artifacts'] if v['name']==source['artifact_name'])['sha256']==source['artifact_sha256']
        events[side]=[e for r in rows for e in r['reply']['bridge']['events']]
        sent=[e for e in events[side] if e['kind']=='model_measurement_tx'];received=[e for e in events[side] if e['kind']=='model_measurement_rx']
        assert len(sent)==len(received)==360
        assert [e['sample'] for e in sent]==[e['sample'] for e in received]
        for r in rows:
            reply=r['reply'];t,b,s,_,_=struct.unpack('<HHhBB',bytes.fromhex(reply['bridge']['brake_feedback']['data_hex']))
            assert reply['control']==dict(throttle=t/10000,brake=b/10000,steer=s/32767)
            m=reply['bridge']['model_monitor'];sample=m['sample'];assert sample['sequence']==r['step'] and not m['missing']
            assert sample==sent[r['step']]['sample']
            if side=='baseline':assert not m['protect']
            model_t=r['step']*50-link['start_ms']
            if model_t>=0:
                if boundary in ('camera_raw_csi','fpga'):
                    frames=[f for f in data['frames'] if f['channel']==sample['channel'] and model_t-50<f['terminal_ns']/1e6<=model_t]
                    assert sample['abort_count']==sum(f['status']=='aborted' for f in frames)
                    assert sample['commit_count']==sum(f['status']=='committed' for f in frames)
                    prior=sorted([f for f in data['frames'] if f['channel']==sample['channel'] and f['terminal_ns']/1e6<=model_t],key=lambda f:f['terminal_ns'])
                    assert sample['last_status']==(prior[-1]['status'] if prior else 'pending')
                    assert sample['last_frame']==(prior[-1]['frame'] if prior else None)
                    assert sample['loss_reasons']==sorted({f['loss_reason'] for f in frames if f['status']=='aborted'})
                    bad=sample['abort_count']>0 or sample['last_status']=='aborted'
                else:
                    values=data['samples'];chunk=[v['v(out)'] for v in values if model_t-50<v['time']*1000<=model_t]
                    prior=[v['v(out)'] for v in values if v['time']*1000<=model_t-50]
                    if prior:chunk.append(prior[-1])
                    if not chunk:chunk=[values[0]['v(out)']]
                    assert sample['min_v']==min(chunk) and sample['max_v']==max(chunk)
                    arrived=[v['v(out)'] for v in values if v['time']*1000<=model_t]
                    assert sample['last_v']==(arrived[-1] if arrived else values[0]['v(out)'])
                    bad=min(chunk)<8 if boundary=='power' else min(chunk)<2 or max(chunk)>3
                if side=='faulted' and bad:
                    model_bad_steps.append(r['step']);assert m['protect'] and reply['control']['brake']==1
            if m['protect']:assert reply['control']['brake']==1
        assert run['summary']['carla_result']['sides'][side]['cleanup_status']=='confirmed'
    assert model_bad_steps and not any(e['kind']=='detection' and e.get('reason')=='model_'+boundary for e in events['baseline'])
    case=causes['cases'][0];stages=case['stages'];d=stages['detection'];recovery=case['model_recovery']
    assert d['ecu']=='adas' and d['evidence_source']=='reference_process' and d['reason']=='model_'+boundary
    assert d['source']['execution_id']==link['runs']['faulted']['execution_id']
    assert d['measurement']['basis']!='measurement_missing'
    assert all(stages[k] is not None for k in ('injection','detection','can_command','brake_output','vehicle'))
    assert stages['injection']['time_ms']<=d['time_ms']<=stages['can_command']['time_ms']<=stages['brake_output']['time_ms']<=stages['vehicle']['time_ms']
    assert recovery and recovery['measurement']['fresh_samples']>=link['policy']['recovery_samples']
    assert recovery['time_ms']-recovery['measurement']['last_bad_ms']>=link['policy']['min_hold_ms']
    faulted=trace['sides']['faulted'];release=next(r for r in faulted if r['step']>model_bad_steps[-1] and r['reply']['control']['brake']==0)
    assert release['reply']['bridge']['observed_time_ms']>=recovery['time_ms']
    step=release['step'];before=trace['sides']['baseline'][step]['next_ego']['speed_mps'];after=faulted[step]['next_ego']['speed_mps']
    assert after<before-.1, (boundary,before,after)
    moving=next(r for r in faulted if r['step']>=step and r['next_ego']['speed_mps']>.1)
    assert moving['step']-step<=20 and moving['reply']['control']['brake']==0
    report['items'][boundary]=dict(execution=run['id'],manifest_sha256=run['manifest_hash'],model_sources=link['runs'],
        verdict=run['summary']['verdict'],detected_ms=d['time_ms'],detection_basis=d['measurement']['basis'],latency_ms=case['latency_ms'],
        measured_fault_steps=model_bad_steps,recovery_observed_ms=recovery['time_ms'],brake_release_input_ms=step*50,
        release_speed_mps=dict(normal=before,faulted=after),normal_no_model_protection=True,
        first_motion_after_release=dict(input_step=moving['step'],input_time_ms=moving['step']*50,next_observed_speed_mps=moving['next_ego']['speed_mps'],criterion='next vehicle observation > 0.1m/s; not the ECU clock'),
        all_720_outputs_match_brake_bytes=True,all_720_measurements_match_native_sources=True,source_time_scale=1)
(a.root/'checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False,indent=2))
