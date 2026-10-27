"""Adapter lifecycle contract fixtures, explicitly not native engine evidence."""
import copy
import sys
import types
from pathlib import Path
import pytest
from ade2e.adapters import AdapterCancelled
from ade2e.service import digest
from ade2e.vecu import example
from axiom_vecu_network import NetworkHandler
import axiom_vecu_network as module

@pytest.fixture
def handler(monkeypatch):
    installation={'schema_version':'fixture-install/1','engines':{'fixture':'not native execution'}}
    probe={'available':True,'manifest':installation,'manifest_sha256':digest(installation)}
    helper=types.ModuleType('axiom_vecu_network.remote');helper.call=lambda *args,**kwargs:copy.deepcopy(probe);helper.command=lambda *args:['fixture-not-started']
    monkeypatch.setitem(sys.modules,'axiom_vecu_network.remote',helper)
    monkeypatch.setattr(module,'remote',helper,raising=False)
    monkeypatch.setattr(module,'identity',lambda:{'fixture':'a'*64})
    return NetworkHandler(),probe


def test_readiness_validates_manifest_and_does_not_substitute_reference(handler):
    engine,probe=handler
    assert engine.readiness()['available']
    probe['manifest']['engines']['fixture']='tampered'
    result=engine.readiness();assert not result['available'] and 'identity failed' in result['reason']
    probe.clear();probe.update(available=False,reason='native engine unavailable')
    result=engine.readiness();assert not result['available'] and 'unavailable' in result['reason']
    with pytest.raises(ValueError):engine.readiness('unregistered')


def snapshot(engine):
    source=example();source['frames']=source['frames'][:20]
    test=dict(mode='fixed_replay',project_id='default',id='fixture-test',revision=1,sha256='b'*64,seed=42,
        network_ref={'id':'fixture-network','revision':1,'sha256':'c'*64},input_ref={'id':'fixture-input','revision':1,'sha256':'d'*64},hil_test_ref=None,
        faults=[dict(kind='udp_repeat',start_ms=200,end_ms=600)])
    runtimes={side:dict(kind='network_reference',dut_version='network-ecu/1',side=side) for side in ['baseline','faulted']}
    return dict(test=test,source=source,runtimes=runtimes,runtime=engine._probe(),adapter_sources=module.identity(),execution_fidelity='contract_fixture_only')


def install_fixture_process(monkeypatch):
    created=[];audited=[]
    class FakeProcess:
        def __init__(self,runtime,init,path,check,**kwargs):
            self.side=runtime['side'];self.closed=False;self.init=init;self.kwargs=kwargs;Path(path).write_text('fixture only\n');created.append(self)
        def exchange(self,request):
            reply=dict(schema_version='hil-dut-reply/1',op='step',sequence=request['sequence'],state='tracking',
                control=dict(throttle=.1,brake=0,steer=0),object_age_s=0,
                network=dict(stats={'udp_received':request['sequence']+1},events=[{'time_ms':request['sequence']*50,'layer':'ethernet','kind':'fixture'}]))
            return reply,.2
        def close(self):self.closed=True
    class FakeAudit:
        def __init__(self,init):self.init=init
        def check(self,request,reply):audited.append((request,reply))
    audit_module=types.ModuleType('axiom_carla.hil_network');audit_module.NetworkAudit=FakeAudit
    monkeypatch.setitem(sys.modules,'axiom_carla.hil_network',audit_module)
    monkeypatch.setattr(module.process_runtime,'Process',FakeProcess)
    return created,audited


def test_fixed_replay_forwards_unmodified_packets_and_freezes_each_side(handler,monkeypatch,tmp_path):
    engine,_=handler;created,audited=install_fixture_process(monkeypatch);frozen=snapshot(engine)
    result=engine.execute(frozen,tmp_path,lambda:False)
    assert len(created)==2 and all(child.closed for child in created) and len(audited)==40
    assert created[0].kwargs['preamble']==frozen['runtimes']['baseline']
    assert created[1].init['network_runtime']==frozen['runtimes']['faulted']
    assert all(request['object_packet']['sequence']==request['sequence'] for request,_ in audited)
    assert all(request['object_delivery_present'] is True for request,_ in audited)
    assert result.summary['sides']['baseline']['input_sha256']==result.summary['sides']['faulted']['input_sha256']
    assert result.summary['network']['sides']['faulted']['udp_received']==20
    assert result.summary['checks']['driving_safety']=='not_evaluated'
    assert {a.name for a in result.artifacts}>={'vecu-trace.json','network-trace.json','network-summary.json','vecu-provenance.json'}


def test_source_or_installation_drift_stops_before_process_launch(handler,monkeypatch,tmp_path):
    engine,probe=handler;created,_=install_fixture_process(monkeypatch);frozen=snapshot(engine)
    frozen['adapter_sources']={'wrong':'source'}
    with pytest.raises(ValueError,match='sources changed'):engine.execute(frozen,tmp_path,lambda:False)
    assert not created
    frozen=snapshot(engine);probe['manifest']['other']='new';probe['manifest_sha256']=digest(probe['manifest'])
    with pytest.raises(ValueError,match='installation changed'):engine.execute(frozen,tmp_path,lambda:False)
    assert not created


def test_cancel_after_launch_closes_child_and_publishes_no_success(handler,monkeypatch,tmp_path):
    engine,_=handler;created,_=install_fixture_process(monkeypatch);frozen=snapshot(engine);count=0
    def cancel():
        nonlocal count
        count+=1;return count>=5
    with pytest.raises(AdapterCancelled):engine.execute(frozen,tmp_path,cancel)
    assert len(created)==1 and created[0].closed
    assert not (tmp_path/'vecu-summary.json').exists()


def test_overlapping_injection_retains_both_faults_and_individual_boundaries():
    faults=[dict(kind='restbus_drop',start_ms=3000,end_ms=7000),dict(kind='ecu_reset',start_ms=5000,end_ms=5200)]
    test={'faults':faults}
    assert module._injection(test,'baseline',5000,{})['faults']==[]
    assert module._injection(test,'faulted',4999,{})['kinds']==['restbus_drop']
    overlap=module._injection(test,'faulted',5000,{})
    assert overlap['active'] and overlap['kinds']==['restbus_drop','ecu_reset']
    assert overlap['kind']=='restbus_drop + ecu_reset'
    overlap['faults'][0]['end_ms']=1
    assert faults[0]['end_ms']==7000
    assert module._injection(test,'faulted',5200,{})['kinds']==['restbus_drop']
    assert not module._injection(test,'faulted',7000,{})['active']
