"""Independent checks against sealed native captures (not generated expected results)."""
import argparse,hashlib,json,math,struct
from pathlib import Path
from axiom_sdk import AxiomClient
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--overlap-root',type=Path);p.add_argument('--api',default='http://127.0.0.1:8240');a=p.parse_args();c=AxiomClient(a.api,timeout=180)
def artifact(folder,run,name):
    item=next(x for x in run['artifacts'] if x['name']==name);path=folder/name
    if not path.exists():c.download_experiment_artifact(run['id'],item['id'],path)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
    return json.loads(path.read_text())
report={'schema_version':'hil-injection-expansion-checks/1','physical_validation':False,'browser_workflow':'blocked_by_saved_permission','reference':{},'models':{}}
for name in ('reset','os-event','restbus-reset'):
    folder=(a.overlap_root if name=='restbus-reset' and a.overlap_root else a.root/'reference-r2')/name;run=json.loads((folder/'result.json').read_text())
    assert run['status']=='completed' and run['summary']['verdict']=='passed'
    trace=artifact(folder,run,'vecu-trace.json');causes=artifact(folder,run,'hil-causality.json')
    events_by_side={}
    for side,rows in trace['sides'].items():
        assert len(rows)==360
        for r in rows:
            t,b,s,_,_=struct.unpack('<HHhBB',bytes.fromhex(r['reply']['bridge']['brake_feedback']['data_hex']))
            assert r['reply']['control']=={'throttle':t/10000,'brake':b/10000,'steer':s/32767}
        events_by_side[side]=[e for r in rows for e in r['reply']['bridge']['events']]
    assert not any(e['kind'] in ('reset_asserted','task_suppressed','fault_start') for e in events_by_side['baseline'])
    events=events_by_side['faulted'];start,end=(6900,7100) if name=='restbus-reset' else (3000,7000) if name=='os-event' else (3000,3200)
    suppressed=[e for e in events if e['kind']=='task_suppressed']
    assert len(suppressed)==(end-start)//50
    assert not any(e['kind']=='can_tx' and e.get('ecu')=='adas' and start+2<e['time_ms']<end for e in events)
    assert any(e['kind']=='object_rx' and start<e['time_ms']<end for e in events) if name=='os-event' else True
    for e in events:
        if e['kind']=='output' and e.get('reason') in ('watchdog','initial','recovery'):
            assert e['control']['brake']==1 and e['control']['throttle']==0
    if name=='restbus-reset':
        assert all(r['reply']['control']['brake']==1 for r in trace['sides']['faulted'] if 6900<=r['step']*50<7200)
    release=next(r for r in trace['sides']['faulted'] if r['step']*50>=end and r['reply']['control']['brake']==0)
    for case in causes['cases']:
        if case['fault']['kind'] in ('ecu_reset','os_event_drop'):
            assert case['reference_hook_application'] is not None
            assert case['stages']['can_command'] is None
            # In the overlap case Brake already protects due to Restbus; a second
            # watchdog detection is not required or invented while already degraded.
            if name!='restbus-reset':assert case['stages']['detection']['ecu']=='brake'
    report['reference'][name]=dict(execution=run['id'],verdict=run['summary']['verdict'],samples_per_side=360,suppressed_ticks=len(suppressed),
        release_input_ms=release['step']*50,causality=[{'kind':x['fault']['kind'],'latency_ms':x['latency_ms']} for x in causes['cases']],all_brake_outputs_match_received_bytes=True)
for name in ('camera_raw_csi','fpga','power','analog'):
    folder=a.root/'models'/name;pair=json.loads((folder/'pair.json').read_text());sides={}
    for side in ('baseline','faulted'):
        run=json.loads((folder/side/'result.json').read_text());assert run['status']=='completed'
        sides[side]=dict(run=run,trace=artifact(folder/side,run,'trace.json'))
    if name in ('camera_raw_csi','fpga'):
        frames={side:artifact(folder/side,v['run'],'frames.json') for side,v in sides.items()}
        for side,v in sides.items():
            s=v['run']['summary'];assert (s['width'],s['height'],s['channels'],s['clock_hz'])==(1920,1080,4,100000000)
            assert s['source_window_ns']==1000000000 and s['requested_frames']==120 and s['verdict']=='passed'
            assert s['committed_mismatch_count']==s['unexpected_mismatch_count']==s['protocol_error_count']==s['consumer_aborted_visible_frames']==0
            assert s['completed_frames']==(120 if side=='baseline' else 119)
            assert s['aborted_frames']==(0 if side=='baseline' else 1)
        normal={(f['channel'],f['frame']):f for f in frames['baseline']['frames']}
        aborted=[]
        for f in frames['faulted']['frames']:
            key=(f['channel'],f['frame'])
            assert f['input_sha256']==normal[key]['input_sha256']
            if f['status']=='committed':
                assert f['output_sha256']==normal[key]['output_sha256']==f['reference_sha256']
            else:aborted.append(f)
        assert len(aborted)==1 and (aborted[0]['channel'],aborted[0]['frame'])==(0,0)
        assert aborted[0]['loss_reason']==('frame_boundary' if name=='camera_raw_csi' else 'fifo_overflow')
        report['models'][name]={side:{k:v['run']['summary'][k] for k in ('completed_frames','aborted_frames','committed_mismatch_count','unexpected_mismatch_count','clock_hz','source_window_ns')}|{'execution':v['run']['id']} for side,v in sides.items()}
        report['models'][name]['isolation']={'unchanged_committed_frame_hashes':119,'aborted_channel':0,'aborted_frame':0,'loss_reason':aborted[0]['loss_reason'],'fifo_peak_pixels':aborted[0]['fifo_peak_pixels'],'discarded_mismatch_count':aborted[0]['mismatch_count']}
    else:
        a_samples=sides['baseline']['trace']['samples'];b_samples=sides['faulted']['trace']['samples']
        nominal=12 if name=='power' else 2.5;fault=6 if name=='power' else 3.5
        assert all(abs(r['v(source)']-nominal)<1e-8 for r in a_samples)
        mid=[r for r in b_samples if .004<r['time']<.006]
        assert mid and all(abs(r['v(source)']-fault)<1e-8 for r in mid)
        # Compare the RC response independently using its circuit time constant.
        ratio=120/122 if name=='power' else 1
        tau=(2*120/122)*100e-6 if name=='power' else .001
        r=min(b_samples,key=lambda r:abs(r['time']-.005))
        expected=ratio*(fault+(nominal-fault)*math.exp(-(r['time']-.0030005)/tau))
        assert abs(r['v(out)']-expected)<.003
        report['models'][name]=dict(executions={k:v['run']['id'] for k,v in sides.items()},source_nominal_v=nominal,source_fault_v=fault,
            measured_model_out_v=r['v(out)'],independent_rc_expected_v=expected,rc_check_error_v=abs(r['v(out)']-expected),scope='native_ngspice_reference_circuit_not_ECU')
(a.root/'checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False,indent=2))
