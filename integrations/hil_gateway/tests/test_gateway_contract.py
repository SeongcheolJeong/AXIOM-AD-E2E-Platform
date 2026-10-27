import copy
import pytest
from axiom_hil_gateway.socketcan import encode_frame,decode_frame
from axiom_vecu_network import wire
from axiom_carla.hil_gateway import Audit
from axiom_hil_gateway.evidence import derive

@pytest.mark.parametrize('control',[{'throttle':0.,'brake':1.,'steer':0.},{'throttle':.2314,'brake':0.,'steer':-.5}])
def test_can_roundtrip_preserves_existing_sil_codec(control):
    data=wire.command_bytes(control,255,7)
    cid,decoded=decode_frame(encode_frame(256,data))
    assert cid==256 and decoded==data
    assert wire.decode_command(decoded)==wire.decode_command(data)

@pytest.mark.parametrize('cid,data',[(2048,[0]*8),(256,[0]*7),(True,[0]*8)])
def test_unsupported_can_before_io(cid,data):
    with pytest.raises(ValueError):encode_frame(cid,data)

def test_no_detection_fabricated_from_braking():
    rows={'faulted':[{'step':60,'reply':{'bridge':{'events':[
        {'kind':'fault_start','fault_index':0,'time_ms':3000,'monotonic_ns':1},
        {'kind':'brake_output_observed','time_ms':3300,'monotonic_ns':2,'control':{'brake':1}}
    ]}}}]}
    before=copy.deepcopy(rows);report=derive(rows,{'binding':{'injections':[{'kind':'udp_delay','start_ms':3000,'end_ms':7000}],'source_ref':{'id':'t'}}})
    assert report['cases'][0]['stages']['detection'] is None
    assert report['cases'][0]['latency_ms']['detection'] is None
    assert report['cases'][0]['stages']['brake_output']['time_ms']==3300
    assert before==rows

def test_selected_control_requires_exact_received_brake_bytes():
    audit=Audit({'gateway_runtime':{'device':{'target':'vcan','codec_id':'axiom-example/1'}}})
    control={'throttle':0.,'brake':1.,'steer':0.};data=bytes(wire.command_bytes(control,0,0)).hex()
    reply={'sequence':0,'control':control,'state':'unobserved','bridge':{'schema_version':'hil-gateway-observation/1','target':'vcan',
        'diagnostic_observed':False,'brake_feedback':{'control':control,'data_hex':data}}}
    audit.check({'sequence':0},reply)
    wrong=copy.deepcopy(reply);wrong['control']['brake']=0
    with pytest.raises(ValueError):audit.check({'sequence':0},wrong)
    reply['state']='degraded'
    with pytest.raises(ValueError,match='fabricated'):audit.check({'sequence':0},reply)

def test_can_drop_attributes_watchdog_to_brake_not_an_adas_brake_command():
    events=[dict(kind='fault_start',fault_index=0,time_ms=3000,monotonic_ns=1),
        dict(kind='detection',reason='command_watchdog',ecu='brake',evidence_source='reference_process',time_ms=3100,monotonic_ns=2),
        dict(kind='brake_output_observed',control={'brake':1},time_ms=3101,monotonic_ns=3),
        dict(kind='can_tx',ecu='adas',control={'brake':1},time_ms=4000,monotonic_ns=4)]
    rows={'faulted':[{'step':62,'reply':{'bridge':{'events':events}}}]}
    report=derive(rows,{'binding':{'injections':[{'kind':'can_command_drop'}],'source_ref':{'id':'t'}}})
    case=report['cases'][0]
    assert case['stages']['detection']['ecu']=='brake'
    assert case['stages']['can_command'] is None
    assert case['latency_ms']['brake_output']==101


def test_physical_diagnostic_without_clock_mapping_has_no_detection_latency():
    events=[dict(kind='fault_start',fault_index=0,time_ms=3000),
        dict(kind='detection',reason='object_age',ecu='adas',evidence_source='physical_ecu_report',time_ms=None,gateway_received_time_ms=3300),
        dict(kind='can_command_observed',control={'brake':1},time_ms=3305),
        dict(kind='brake_output_observed',control={'brake':1},time_ms=3310)]
    report=derive({'faulted':[{'step':66,'reply':{'bridge':{'events':events}}}]},
                  {'binding':{'injections':[{'kind':'udp_delay'}],'source_ref':{'id':'t'}}})
    case=report['cases'][0]
    assert case['detection_status']=='observed'
    assert case['latency_ms']['detection'] is None
    assert case['latency_ms']['brake_output']==310


def test_optional_carla_source_identity_resolves_original_dependencies():
    from axiom_hil_gateway import carla_native
    from axiom_carla import hil_native
    new=carla_native.source_hashes();original=hil_native.source_hashes()
    assert new['vecu_remote_sha256']==original['vecu_remote_sha256']
    assert new['hil_dut_sha256']==original['hil_dut_sha256']
    assert new['hil_native_sha256']!=original['hil_native_sha256']


def test_absent_ecu_diagnostic_uses_host_geometry_only_for_carla_bookkeeping():
    from axiom_hil_gateway.carla_native import HilBridge
    class Injector:
        def step(self,sequence,objects):return {'sequence':sequence}, {'delivery_present':True}
    reply={'op':'step','sequence':0,'control':{'throttle':0.,'brake':1.,'steer':0.},'plan':{'diagnostic_source':'unobserved'},
        'bridge':{'diagnostic_observed':False},'object_sequence':-1,'object_age_s':None,'state':'unobserved'}
    class Dut:
        def exchange(self,request):return copy.deepcopy(reply),1
    bridge=HilBridge.__new__(HilBridge);bridge.gateway=True;bridge.network=True;bridge.ego_id='ego';bridge.injector=Injector();bridge.dut=Dut();bridge.trace={'rows':[]}
    bridge.observation_route=[{'x':0,'y':0,'z':0},{'x':20,'y':0,'z':0}]
    control,plan=bridge.step(0,{'ego':{'x':3,'y':0,'z':0}},10)
    assert plan['progress_m']==3 and plan['evidence_source']=='host_route_observation'
    assert control==reply['control']
    assert bridge.trace['rows'][0]['reply']==reply
