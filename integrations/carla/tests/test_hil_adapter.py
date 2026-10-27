"""Adapter evidence/dispatch contract checks; fake native transport only."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from ade2e.adapters import AdapterCancelled
from ade2e.experiment_runtime import ExperimentExecution, ExperimentOutputFile
from axiom_carla import city, hil, hil_native


def scenario():
    points=[{'x':float(x),'y':0.,'z':0.,'heading_rad':0.} for x in range(0,41,2)]
    def actor(identity,role,route,speed,mode):
        return {'id':identity,'role':role,'kind':'vehicle','pose':route[0],'route':route,'speed_mps':speed,
            'controller':{'kind':mode,'end_behavior':'stop','stop_time_s':4 if role!='ego' else None}}
    return {'duration_s':18,'timestep_s':.05,'environment':{'lighting':'day','weather':'dry'},
        'actors':[actor('ego','ego',points,3,'traffic'),actor('lead','other',points[6:],2,'route_follow')],
        'sensors':[{'id':key,'actor_id':'ego','width':640,'height':360,'period_s':.1} for key in ('front','chase')],
        'signals':[]}


def snapshot():
    return {'schema_version':'carla-software-dut-experiment/1','hil_source_hashes':hil_native.source_hashes(),
        'hil_adapter_sha256':city.file_sha(hil.__file__),'city_adapter_version':city.VERSION,
        'scenario_source':{'scenario_id':'scenario','scenario_revision':1},
        'city_snapshot':{'scenario':scenario(),'map':{'native_name':'Carla/Maps/Town10HD_Opt'},
            'assets':{key:{'blueprint_id':'vehicle.toyota.prius'} for key in ('ego','lead')}},
        'fault':{'kind':'delay','start_s':3,'duration_s':4,'delay_ms':300}}


def test_profile_rejects_reactive_tuning_or_unrelated_scene():
    hil.validate_profile(scenario())
    for mutate in (lambda s:s['actors'][0]['controller'].update(kind='reactive'),
                   lambda s:s.update(duration_s=30),lambda s:s['sensors'][0].update(width=960),
                   lambda s:s['actors'][1]['pose'].update(y=5)):
        changed=copy.deepcopy(scenario());mutate(changed)
        with pytest.raises(ValueError):hil.validate_profile(changed)


def test_explicit_transport_and_adjacent_city_fallback(monkeypatch):
    monkeypatch.setenv('AXIOM_CARLA_CITY_SCRIPT','/native/city_native.py')
    monkeypatch.delenv('AXIOM_CARLA_HIL_SCRIPT',raising=False)
    assert hil.HilCityTransport().script=='/native/hil_native.py'
    monkeypatch.setenv('AXIOM_CARLA_HIL_SCRIPT','/isolated/hil_native.py')
    assert hil.HilCityTransport().script=='/isolated/hil_native.py'


def fake_transport(calls, *, fail_faulted=False):
    def execute(frozen,work,cancel):
        side=frozen['hil']['side'];calls.append(copy.deepcopy(frozen))
        work.mkdir(parents=True)
        (work/'dispatch.json').write_text(side)
        if fail_faulted and side=='faulted':raise RuntimeError('Native DUT terminated')
        trace={'frames':[{'dut_state':'tracking'}]}
        (work/'hil-trace.json').write_text(json.dumps(trace));(work/'city-scene.json').write_text('{}')
        (work/'map.xodr').write_text('x');(work/'city-evaluation.json').write_text('{}')
        return ExperimentExecution(summary={'schema_version':'carla-city-result/1','scene_artifact':'city-scene.json',
            'map_artifact':'map.xodr','evaluation_artifact':'city-evaluation.json'},
            artifacts=[ExperimentOutputFile(work/name,name,'application/json') for name in
                       ('city-scene.json','hil-trace.json','map.xodr','city-evaluation.json')])
    return SimpleNamespace(execute=execute)


@pytest.mark.parametrize('fault',[{'kind':'delay','start_s':3,'duration_s':4,'delay_ms':300},
                                  {'kind':'delay','start_s':1.5,'duration_s':2,'delay_ms':50},
                                  {'kind':'drop','start_s':17,'duration_s':.5,'delay_ms':0}])
def test_two_sides_share_frozen_scene_and_preserve_prefixed_artifacts(tmp_path,monkeypatch,fault):
    handler=hil.HilExperimentHandler();calls=[];handler.transport=fake_transport(calls)
    monkeypatch.setattr(hil,'validate_trace',lambda trace,*_:trace)
    frozen=snapshot();frozen['fault']=copy.deepcopy(fault)
    result=handler.execute(frozen,tmp_path,lambda:False)
    assert [r['hil']['side'] for r in calls]==['baseline','faulted']
    assert calls[0]['scenario']==calls[1]['scenario']==frozen['city_snapshot']['scenario']
    assert calls[0]['hil']['fault']==calls[1]['hil']['fault']==result.summary['fault']==frozen['fault']==fault
    assert result.fidelity=='actual_carla_software_dut'
    assert {a.name for a in result.artifacts}>={'baseline-city-scene.json','faulted-city-scene.json',
        'baseline-hil-trace.json','faulted-hil-trace.json','hil-summary.json'}
    for side in ('baseline','faulted'):
        summary=result.summary['sides'][side]
        assert summary['scene_artifact']==side+'-city-scene.json'
        assert summary['hil_trace_sha256']==city.file_sha(tmp_path/side/'hil-trace.json')


def test_cancel_before_dispatch_and_fault_failure_preserve_completed_side(tmp_path,monkeypatch):
    handler=hil.HilExperimentHandler();calls=[];handler.transport=fake_transport(calls,fail_faulted=True)
    monkeypatch.setattr(hil,'validate_trace',lambda trace,*_:trace)
    with pytest.raises(AdapterCancelled):handler.execute(snapshot(),tmp_path,lambda:True)
    assert calls==[]
    with pytest.raises(RuntimeError,match='DUT terminated'):handler.execute(snapshot(),tmp_path,lambda:False)
    assert (tmp_path/'baseline'/'city-scene.json').is_file()
    assert not (tmp_path/'hil-summary.json').exists()


def test_source_changed_after_preview_refuses_dispatch(tmp_path):
    handler=hil.HilExperimentHandler();frozen=snapshot();frozen['hil_source_hashes']['hil_dut_sha256']='0'*64
    with pytest.raises(ValueError,match='changed after preview'):handler.execute(frozen,tmp_path,lambda:False)


def test_native_linux_steering_roundoff_allows_recomputation_but_not_changed_command():
    # Actual baseline job_3d9e2577b24d4590a767f9ec369de6d5, sequence44.
    # Linux DUT and macOS replay differ by4.34e-19 in atan2-based steering.
    native={'sequence':44,'control':{'steer':-0.0017343854239569162,'throttle':0.2,'brake':0.},
            'state':'tracking','object_age_s':0.}
    replay=copy.deepcopy(native);replay['control']['steer']=-0.0017343854239569158
    assert native!=replay and hil.same_dut_response(native,replay)
    corrupted=copy.deepcopy(native);corrupted['control']['steer']+=1e-6
    assert not hil.same_dut_response(corrupted,replay)
    corrupted=copy.deepcopy(native);corrupted['sequence']=44.0
    assert not hil.same_dut_response(corrupted,replay)
    corrupted=copy.deepcopy(native);corrupted['control']['steer']=float('nan')
    assert not hil.same_dut_response(corrupted,replay)
    corrupted=copy.deepcopy(native);corrupted['state']='degraded'
    assert not hil.same_dut_response(corrupted,replay)
