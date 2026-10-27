"""Real software process and simulated fault tests; no CARLA engine claimed."""
import copy
import os
import signal
import time
from types import SimpleNamespace
import pytest
from axiom_carla import hil_dut, hil_native


def init():
    return {'op':'init','schema_version':'hil-dut-init/1','target_speed_mps':3.,
        'route':[{'x':0.,'y':0.,'z':0.},{'x':40.,'y':0.,'z':0.}],
        'physics':{'wheelbase_m':2.7,'max_steer_angle_rad':.6}}


def actor(identity,x,speed=2.):
    return {'id':identity,'x':x,'y':0.,'z':0.,'heading_rad':0.,'speed_mps':speed}


def message(index, packet=None):
    return {'op':'step','schema_version':'hil-dut-step/1','sequence':index,'time_s':index*.05,
            'ego':actor('ego',index*.1), 'object_packet':packet or
            {'sequence':index,'time_s':index*.05,'objects':[actor('lead',12+index*.1)]}}


def test_actual_separate_process_controls_and_closes(tmp_path):
    process=hil_native.DutProcess(init(),tmp_path/'dut.log')
    assert process.initial['pid']!=os.getpid()
    try:
        for i in range(8):
            reply,latency=process.exchange(message(i))
            assert reply['sequence']==i and reply['state']=='tracking'
            assert reply['control']['throttle']>0 and reply['control']['brake']==0
            assert latency>=0
    finally:process.close()
    assert process.process.returncode==0


@pytest.mark.parametrize('fault',[{'kind':'delay','start_s':3.,'duration_s':4.,'delay_ms':300},
                                  {'kind':'drop','start_s':3.5,'duration_s':1.,'delay_ms':0},
                                  {'kind':'delay','start_s':1.,'duration_s':4.,'delay_ms':1000},
                                  {'kind':'delay','start_s':5.5,'duration_s':2.,'delay_ms':300},
                                  {'kind':'drop','start_s':1.,'duration_s':4.,'delay_ms':0},
                                  {'kind':'drop','start_s':17.,'duration_s':.5,'delay_ms':0}])
def test_fault_only_objects_stale_brake_three_step_recovery(fault):
    injector=hil_native.ObjectFault(fault);dut=hil_dut.ReferenceDut(init());states=[];packets=[]
    start=round(fault['start_s']/.05)
    end=round((fault['start_s']+fault['duration_s'])/.05)
    for i in range(360):
        packet,event=injector.step(i,[actor('lead',12+i*.1)])
        request=message(i,packet);original_ego=copy.deepcopy(request['ego'])
        reply=dut.step(request)
        assert request['ego']==original_ego
        assert packet['sequence']<=i
        assert event['active'] is (start<=i<end)
        if not event['active']:
            assert packet['sequence']==i and event['new_packet_delivered'] is True
        elif fault['kind']=='drop':
            assert packet['sequence']==start-1 and event['new_packet_delivered'] is False
        if reply['state'] in ('degraded','recovering'):
            assert reply['control']['throttle']==0 and reply['control']['brake']==1
        states.append(reply['state']);packets.append(packet['sequence'])
    assert 'degraded' in states and 'recovering' in states and states[-1]=='tracking'
    assert packets==sorted(packets)
    assert states[:start]==['tracking']*start
    assert states[end:end+3]==['recovering','recovering','tracking']


def test_age_boundary_and_invalid_sequence_rejected():
    dut=hil_dut.ReferenceDut(init());frozen=message(0)['object_packet']
    for i in range(6):assert dut.step(message(i,frozen))['state']=='tracking'
    assert dut.step(message(6,frozen))['state']=='degraded'
    with pytest.raises(ValueError,match='sequence'):dut.step(message(8))


def test_process_death_is_error_without_controller_fallback(tmp_path):
    process=hil_native.DutProcess(init(),tmp_path/'dut.log')
    process.process.kill();process.process.wait(timeout=2)
    try:
        with pytest.raises((RuntimeError,BrokenPipeError)):process.exchange(message(0))
    finally:process.close(graceful=False)


def test_hung_process_has_bounded_timeout_and_cleanup(tmp_path):
    process=hil_native.DutProcess(init(),tmp_path/'dut.log',timeout_s=.1)
    os.kill(process.process.pid,signal.SIGSTOP)
    started=time.monotonic()
    try:
        with pytest.raises(TimeoutError):process.exchange(message(0))
        assert time.monotonic()-started<1
    finally:
        os.kill(process.process.pid,signal.SIGCONT)
        process.close(graceful=False)
    assert process.process.poll() is not None


@pytest.mark.parametrize('mutate',[lambda f:f.update(delay_ms=75),lambda f:f.update(kind='drop'),
    lambda f:f.update(duration_s=.2),lambda f:f.update(start_s=0),lambda f:f.update(delay_ms=float('nan'))])
def test_unsupported_fault_rejected(mutate):
    fault={'kind':'delay','start_s':3,'duration_s':4,'delay_ms':300};mutate(fault)
    with pytest.raises(ValueError):hil_native.validate_fault(fault)


@pytest.mark.parametrize('start,duration',[(1,.5),(1.5,4),(13.5,4),(16,1.5),(17,.5)])
@pytest.mark.parametrize('kind,delay',[('delay',50),('delay',1000),('drop',0)])
def test_fault_window_boundaries_preserve_exact_input(start,duration,kind,delay):
    fault={'kind':kind,'start_s':start,'duration_s':duration,'delay_ms':delay}
    result=hil_native.validate_fault(fault)
    assert result==fault and result is not fault


@pytest.mark.parametrize('start,duration',[(.5,1),(17.5,.5),(1.25,1),(1.0001,1),
    (1,.25),(1,4.5),(1,1.25),(1,1.0001),(17,1),(14,4)])
def test_off_grid_windows_and_missing_recovery_tail_rejected(start,duration):
    with pytest.raises(ValueError):
        hil_native.validate_fault({'kind':'drop','start_s':start,'duration_s':duration,'delay_ms':0})


@pytest.mark.parametrize('field',['start_s','duration_s','delay_ms'])
@pytest.mark.parametrize('value',[float('nan'),float('inf'),float('-inf'),True])
def test_fault_numeric_fields_reject_nonfinite_and_boolean(field,value):
    fault={'kind':'delay','start_s':1.5,'duration_s':1.,'delay_ms':300}
    fault[field]=value
    with pytest.raises(ValueError,match='Unsupported HIL fault'):
        hil_native.validate_fault(fault)


@pytest.mark.parametrize('fault',[{'kind':'delay','start_s':1.5,'duration_s':1.,'delay_ms':300},
                                  {'kind':'drop','start_s':17.,'duration_s':.5,'delay_ms':0}])
def test_shifted_fault_events_retain_input_clock_and_next_response_clock(tmp_path,fault):
    # Real child protocol over authored observations; no CARLA physics is claimed.
    bridge=hil_native.HilBridge({'source_hashes':hil_native.source_hashes(),'side':'faulted','fault':fault},
        {'id':'ego','route':init()['route'],'speed_mps':3.},init()['physics'],tmp_path)
    try:
        end=round((fault['start_s']+fault['duration_s'])/.05)
        for i in range(end+3):
            current={'ego':actor('ego',i*.1),'lead':actor('lead',12+i*.1)}
            command,_=bridge.step(i,current,100+i)
            bridge.observed({**actor('ego',(i+1)*.1),'control':command},101+i,command)
        events=[item for item in bridge.trace['events'] if item['type'].startswith('fault_')]
        assert [item['type'] for item in events]==['fault_started','fault_ended']
        assert [item['input_time_s'] for item in events]==pytest.approx([fault['start_s'],fault['start_s']+fault['duration_s']])
        assert [item['time_s'] for item in events]==pytest.approx([fault['start_s']+.05,fault['start_s']+fault['duration_s']+.05])
        assert [frame['dut_state'] for frame in bridge.trace['frames'][end:end+3]]==['recovering','recovering','tracking']
        assert bridge.trace['frames'][-1]['time_s']<=18
        assert bridge.trace['max_object_age_s']==.25 and bridge.trace['recovery_fresh_steps']==3
    finally:bridge.dut.close()


def test_hil_bridge_records_real_process_and_native_response(tmp_path):
    spec={'id':'ego','route':init()['route'],'speed_mps':3.}
    bridge=hil_native.HilBridge({'source_hashes':hil_native.source_hashes(),'side':'baseline',
        'fault':{'kind':'drop','start_s':3.5,'duration_s':1.,'delay_ms':0}},spec,init()['physics'],tmp_path)
    try:
        current={'ego':actor('ego',0),'lead':actor('lead',12)}
        command,plan=bridge.step(0,current,100)
        bridge.observed({**actor('ego',.1),'control':command},101,command)
        assert bridge.trace['frames'][0]['response_frame']==101
        assert bridge.trace['frames'][0]['time_s']==.05
        assert bridge.trace['frames'][0]['input_time_s']==0
        assert bridge.trace['rows'][0]['request']['ego']==current['ego']
        assert bridge.trace['dut_initialization']['pid']!=os.getpid()
    finally:bridge.dut.close()


def test_sync_batch_ack_before_tick_and_failed_readback_is_retained(tmp_path):
    bridge=hil_native.HilBridge({'source_hashes':hil_native.source_hashes(),'side':'baseline',
        'fault':{'kind':'drop','start_s':3.5,'duration_s':1.,'delay_ms':0}},
        {'id':'ego','route':init()['route'],'speed_mps':3.},init()['physics'],tmp_path)
    try:
        command,_=bridge.step(0,{'ego':actor('ego',0),'lead':actor('lead',12)},100)
        calls=[]
        def batch(commands,tick):
            calls.append((commands,tick))
            return [SimpleNamespace(actor_id=identity,error='') for identity,_ in commands]
        carla=SimpleNamespace(VehicleControl=lambda **value:value,
            command=SimpleNamespace(ApplyVehicleControl=lambda identity,value:(identity,value)))
        bridge.apply_controls(carla,SimpleNamespace(apply_batch_sync=batch),
            {'ego':SimpleNamespace(id=10),'lead':SimpleNamespace(id=11)},
            {'ego':command,'lead':{'throttle':.2,'brake':0.,'steer':0.}})
        assert calls[0][1] is False
        assert bridge.trace['rows'][0]['command_acknowledgements']==[
            {'actor_id':10,'error':None},{'actor_id':11,'error':None}]
        stale={'throttle':0.,'brake':1.,'steer':0.}
        with pytest.raises(ValueError,match='readback differs'):
            bridge.observed(actor('ego',.1),101,stale)
        assert bridge.trace['rows'][0]['applied_control']==stale
        assert bridge.trace['rows'][0]['response_frame']==101
        assert not bridge.trace['frames']
        with pytest.raises(RuntimeError,match='not acknowledged'):
            bridge.apply_controls(carla,SimpleNamespace(apply_batch_sync=lambda *_:[SimpleNamespace(actor_id=10,error='blocked')]),
                {'ego':SimpleNamespace(id=10)},{'ego':command})
    finally:bridge.dut.close()

@pytest.mark.parametrize('kind,count,missing',[('repeat',80,0),('drop',0,80),('delay',8,0)])
def test_packet_delivery_detection_actual_process(tmp_path,kind,count,missing):
    injector=hil_native.ObjectFault(dict(kind=kind,start_s=3,duration_s=4,delay_ms=400 if kind=='delay' else 0))
    process=hil_native.DutProcess(init(),tmp_path/(kind+'.log'))
    rows=[]
    try:
        for i in range(180):
            packet,event=injector.step(i,[actor('lead',12+i*.1)])
            request=message(i,packet);request['object_delivery_present']=event['delivery_present']
            response,_=process.exchange(request);rows.append(response)
            assert response['duplicate_packet_detected']==event['duplicate_packet_delivered']
            if kind=='repeat' and 60<=i<140:
                assert packet['sequence']==59 and packet['time_s']==pytest.approx(2.95)
                assert packet['objects']==[actor('lead',17.9)]
                assert response['fresh_recovery_steps']==0
        assert rows[-1]['duplicate_packet_count']==count
        assert rows[-1]['missing_packet_count']==missing
        assert rows[65]['state']=='degraded' and rows[65]['control']['brake']==1
        assert rows[140]['state']=='recovering' and rows[142]['state']=='tracking'
    finally:process.close()


def test_duplicate_sequence_cannot_change_contents():
    dut=hil_dut.ReferenceDut(init());dut.step(message(0))
    changed=message(1,{'sequence':0,'time_s':0.,'objects':[actor('lead',99)]})
    with pytest.raises(ValueError,match='changed packet contents'):dut.step(changed)


def test_fresh_duplicate_does_not_count_toward_recovery():
    dut=hil_dut.ReferenceDut(init())
    held={'sequence':0,'time_s':0.,'objects':[actor('lead',12)]}
    for i in range(8):dut.step(message(i,held))
    first={'sequence':8,'time_s':.4,'objects':[actor('lead',13)]}
    assert dut.step(message(8,first))['fresh_recovery_steps']==1
    duplicate=dut.step(message(9,first))
    assert duplicate['duplicate_packet_detected'] and duplicate['fresh_recovery_steps']==0
    assert duplicate['state']=='recovering' and duplicate['control']['brake']==1
    for i in (10,11):assert dut.step(message(i))['state']=='recovering'
    assert dut.step(message(12))['state']=='tracking'
