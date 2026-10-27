"""Authored trace fixtures: interpretation and missing-link checks, not simulator runs."""
import copy
import pytest
from ade2e.vecu_network_causality import derive_causality
from ade2e.vecu_network_schemas import NetworkSave


def authored_case(kind='udp_delay'):
    fault=dict(kind=kind,start_ms=3000,end_ms=7000,delay_ms=400,reset_restart_ms=200,target_ecu='adas')
    network=NetworkSave(name='Fixture').model_dump(mode='json')
    snapshot=dict(test=dict(faults=[fault],mode='carla_closed_loop',seed=42,network_ref={'id':'fixture','revision':1,'sha256':'a'*64},hil_test_ref={'id':'scene','revision':1,'sha256':'b'*64}),network=network)
    rows=[]
    for i in range(80):
        t=i*50
        rows.append(dict(request={'time_s':t/1000,'ego':{'speed_mps':2.}},reply=dict(control=dict(brake=0.,throttle=.2,steer=0.),network=dict(time_ms=t,events=[],controller_steps=[],brake={})),next_ego=dict(speed_mps=2.,control=dict(brake=0.,throttle=.2,steer=0.))))
    def add(t,event):
        event=dict(time_ms=t,**event);rows[(t+49)//50]['reply']['network']['events'].append(event)
    add(3000,dict(kind='fault_start',fault=fault))
    detect,send,apply=(3250,3250,3300) if kind=='udp_delay' else (3091,3100,3150)
    add(2991,dict(kind='can_rx',ecu='adas',can_id=513,data=[0]*8))
    if kind=='restbus_drop':add(detect,dict(kind='heartbeat_timeout',ecu='adas'))
    else:
        rows[65]['reply']['network']['controller_steps']=[dict(request=dict(time_s=3.25,object_packet=dict(sequence=59,time_s=2.95)),reply=dict(state='degraded',object_age_s=.3))]
    data=[0,0,16,39,0,0,send//50,0]
    add(send,dict(kind='control',ecu='adas',state='degraded' if kind=='udp_delay' else 'tracking',heartbeat_override=kind=='restbus_drop'))
    add(send,dict(kind='can_tx',ecu='adas',can_id=256,data=data,dropped=False))
    add(send+1,dict(kind='can_rx',ecu='brake',can_id=256,data=data,transport_time_ns=send*1000000))
    row=rows[apply//50];row['reply']['network']['brake']=dict(last_command_ms=send+1,command_hex=bytes(data).hex())
    row['reply']['control']=dict(brake=1.,throttle=0.,steer=0.)
    row['next_ego']['control']=dict(row['reply']['control'])
    row['next_ego']['speed_mps']=2.01  # A positive brake readback is not yet a speed decrease.
    rows[apply//50+1]['next_ego']['speed_mps']=1.9
    rows[apply//50+2]['next_ego']['speed_mps']=0.
    return {'sides':{'faulted':rows}},snapshot


@pytest.mark.parametrize('kind,expected',[('udp_delay',[3000,3250,3250,3251,3300,3350,3400]),('restbus_drop',[3000,3091,3100,3101,3150,3200,3250])])
def test_detector_can_and_vehicle_clocks_are_distinct(kind,expected):
    trace,snapshot=authored_case(kind);case=derive_causality(trace,snapshot)['cases'][0]
    assert case['status']=='observed'
    assert [case['stages'][k]['time_ms'] for k in ('injection','detection','can_tx','can_rx','application','vehicle','slowdown')]==expected
    assert case['latency_ms']['detection_to_can_tx']==(0 if kind=='udp_delay' else 9)
    if kind=='restbus_drop':assert case['stages']['detection']['evidence']['observed_silence_ms']==100
    else:assert case['stages']['detection']['evidence']['object_age_ms']==300


def test_initial_protective_braking_and_global_degraded_do_not_establish_detection():
    trace,snapshot=authored_case()
    for row in trace['sides']['faulted']:
        row['reply']['state']='degraded';row['reply']['network']['controller_steps']=[]
    case=derive_causality(trace,snapshot)['cases'][0]
    assert case['stages']['detection'] is None and case['stages']['can_tx'] is None


@pytest.mark.parametrize('mutation',['payload','transport_timestamp','receiver','command_id','missing_receive'])
def test_can_receive_must_identify_the_same_transmitted_command(mutation):
    trace,snapshot=authored_case();events=trace['sides']['faulted'][66]['reply']['network']['events']
    rx=next(e for e in events if e['kind']=='can_rx')
    if mutation=='payload':rx['data']=rx['data'][:];rx['data'][6]+=1
    elif mutation=='transport_timestamp':rx['transport_time_ns']+=1000000
    elif mutation=='receiver':rx['ecu']='adas'
    elif mutation=='command_id':rx['can_id']=257
    else:events.remove(rx)
    stages=derive_causality(trace,snapshot)['cases'][0]['stages']
    assert stages['can_tx'] is not None
    assert all(stages[k] is None for k in ('can_rx','application','vehicle'))


def test_application_and_vehicle_require_recorded_command_and_control_readback():
    trace,snapshot=authored_case();row=trace['sides']['faulted'][66]
    row['reply']['network']['brake']['last_command_ms']=3201
    assert derive_causality(trace,snapshot)['cases'][0]['stages']['application'] is None
    row['reply']['network']['brake']['last_command_ms']=3251
    row['next_ego']['control']['brake']=0.
    case=derive_causality(trace,snapshot)['cases'][0]
    assert case['stages']['application'] is not None and case['stages']['vehicle'] is None


def test_missing_injection_event_and_unsupported_reset_are_explicit():
    trace,snapshot=authored_case();trace['sides']['faulted'][60]['reply']['network']['events']=[]
    case=derive_causality(trace,snapshot)['cases'][0]
    assert case['status']=='not_observed' and all(p is None for p in case['stages'].values())
    snapshot['test']['faults'][0]['kind']='ecu_reset'
    assert derive_causality(trace,snapshot)['cases'][0]['status']=='unsupported'


def test_open_loop_has_no_invented_vehicle_observations_and_inputs_are_unchanged():
    trace,snapshot=authored_case();snapshot['test']['mode']='fixed_replay';before=copy.deepcopy((trace,snapshot))
    result=derive_causality(trace,snapshot)
    assert all(result['cases'][0]['stages'][key] is None for key in ('vehicle','slowdown','stop'))
    assert (trace,snapshot)==before
