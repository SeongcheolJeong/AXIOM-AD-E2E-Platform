"""Native API boundary regressions over an authored fake world, not CARLA trials."""
import copy
import json
from types import SimpleNamespace

import pytest

from ade2e.adapters import AdapterExecutionError
from ade2e.simulation_context import simulation_source_hash
from ade2e.simulation_evidence import validate_simulation_artifacts
from axiom_carla.runner import CarlaRunner
from axiom_carla.vehicle_response import _rpc
from axiom_carla.version import ADAPTER_VERSION
from test_live_stack import install_live_world, live_request
from test_runner_evidence import fake_world_runner


def saved(payloads, kind):
    return json.loads(next(p.content for p in payloads if p.kind == kind))


def validate(request, payloads, completed):
    validate_simulation_artifacts(request,payloads,completed=completed,adapter_version=ADAPTER_VERSION)


def mutate_payload(payloads, mutate):
    payloads=copy.deepcopy(payloads)
    response=next(p for p in payloads if p.kind=='vehicle_response')
    data=json.loads(response.content);mutate(data)
    response.content=json.dumps(data,separators=(',',':'),allow_nan=False).encode()
    manifest=next(p for p in payloads if p.kind=='live_stack_manifest')
    data=json.loads(manifest.content)
    data['artifacts']=[{'name':p.name,'kind':p.kind,'sha256':simulation_source_hash(p.content),'size_bytes':len(p.content)}
        for p in payloads if p.kind!='live_stack_manifest']
    manifest.content=json.dumps(data,separators=(',',':')).encode()
    return payloads


def test_raw_snapshot_preserved_when_optional_api_is_missing(monkeypatch,carla_scenario):
    install_live_world(monkeypatch);request=live_request(carla_scenario)
    result=CarlaRunner().execute_with_context(request)
    validate(request,result.artifact_payloads,True)
    data=saved(result.artifact_payloads,'vehicle_response')
    assert len(data['observations'])==81 and data['after_rest']['world_frame']+1==data['observations'][0]['world_frame']
    assert data['initial_velocity_requests'][0]['ack_status']=='unknown'
    assert all(row['actors']['ego']['telemetry']['status']=='unavailable' for row in data['observations'])
    assert all(c['friction_status']=='matched' for c in data['physics_configuration'].values())


@pytest.mark.parametrize('mutate',[
    lambda d:d.update(server_version='another-build'),
    lambda d:d['after_rest']['actors']['ego'].update(actor_id=999),
    lambda d:d['observations'][1]['actors']['ego'].update(reported_control={'status':'available','error':None,
        'before_world_frame':d['observations'][1]['world_frame'],'after_world_frame':d['observations'][1]['world_frame'],
        'data':{'throttle':1.,'brake':1.,'steer':1.,'reverse':False,'hand_brake':False,'manual_gear_shift':False,'gear':1}}),
])
def test_outer_manifest_actor_and_control_joins_reject_tampering(monkeypatch,carla_scenario,mutate):
    install_live_world(monkeypatch);request=live_request(carla_scenario)
    result=CarlaRunner().execute_with_context(request)
    with pytest.raises(ValueError):validate(request,mutate_payload(result.artifact_payloads,mutate),True)


def test_friction_mismatch_retains_failure_and_never_injects_velocity(monkeypatch,carla_scenario):
    world=install_live_world(monkeypatch);spawn=world.try_spawn_actor;injections=[]
    def wrong_physics(*args):
        actor=spawn(*args)
        if actor.id==1: actor.apply_physics_control=lambda _:None  # Server retains default3.5.
        original=actor.set_target_velocity
        def inject(value):
            injections.append(actor.id);original(value)
        actor.set_target_velocity=inject
        return actor
    world.try_spawn_actor=wrong_physics;request=live_request(carla_scenario)
    with pytest.raises(AdapterExecutionError,match='friction') as raised:CarlaRunner().execute_with_context(request)
    assert injections==[]
    payloads=raised.value.artifact_payloads;validate(request,payloads,False)
    data=saved(payloads,'vehicle_response')
    assert data['physics_configuration']['ego']['friction_status']=='mismatch'
    assert data['physics_configuration']['ego']['wheels'][0]['fields']['tire_friction']['value']==3.5
    assert data['physics_configuration']['lead']['friction_status']=='matched'
    assert data['after_rest'] and data['observations']==[] and data['initial_velocity_requests']==[]
    assert all(a.destroy_called for a in world.vehicles)


def test_all_vehicle_spawns_precede_physics_configuration(monkeypatch,carla_scenario):
    """Model the observed second same-blueprint spawn resetting prior tire data."""
    world=install_live_world(monkeypatch);spawn=world.try_spawn_actor;events=[]
    def shared_tire_spawn(*args):
        actor=spawn(*args);events.append(('spawn',actor.id))
        if actor.id==2:
            for wheel in world.vehicles[0].physics.wheels:wheel.tire_friction=3.5
        apply=actor.apply_physics_control
        def record_apply(value):
            events.append(('apply',actor.id));apply(value)
        actor.apply_physics_control=record_apply
        return actor
    world.try_spawn_actor=shared_tire_spawn;request=live_request(carla_scenario)
    result=CarlaRunner().execute_with_context(request)
    assert events[:4]==[('spawn',1),('spawn',2),('apply',1),('apply',2)]
    validate(request,result.artifact_payloads,True)
    data=saved(result.artifact_payloads,'vehicle_response')
    assert all(c['friction_status']=='matched' for c in data['physics_configuration'].values())


def test_legacy_configures_after_all_spawns_and_saves_readback(fake_world_runner,carla_scenario):
    world=fake_world_runner();spawn=world.try_spawn_actor;events=[]
    def reset_previous(*args):
        actor=spawn(*args);events.append(('spawn',actor.id))
        if actor.id==2:
            for wheel in world.vehicles[0].physics.wheels:wheel.tire_friction=3.5
        apply=actor.apply_physics_control
        def record(value):events.append(('apply',actor.id));apply(value)
        actor.apply_physics_control=record
        return actor
    world.try_spawn_actor=reset_previous
    result=CarlaRunner().execute(carla_scenario)
    assert events[:4]==[('spawn',1),('spawn',2),('apply',1),('apply',2)]
    startup=json.loads(next(p.content for p in result.artifact_payloads if p.name=='carla-startup.json'))
    assert all(c['friction_status']=='matched' for c in startup['context']['physics_configuration'].values())


def test_legacy_physics_rejection_keeps_startup_and_never_injects(fake_world_runner,carla_scenario):
    world=fake_world_runner();spawn=world.try_spawn_actor;injections=[]
    def rejected(*args):
        actor=spawn(*args)
        if actor.id==1:actor.apply_physics_control=lambda _:None
        actor.set_target_velocity=lambda _:injections.append(actor.id)
        return actor
    world.try_spawn_actor=rejected
    with pytest.raises(AdapterExecutionError,match='friction') as raised:CarlaRunner().execute(carla_scenario)
    assert injections==[]
    startup=json.loads(next(p.content for p in raised.value.artifact_payloads if p.name=='carla-startup.json'))
    assert startup['status']=='passed'  # Rest was stable; configuration gate is separate.
    assert startup['context']['physics_configuration']['ego']['friction_status']=='mismatch'
    assert all(a.destroy_called for a in world.vehicles)


def test_legacy_post_startup_snapshot_failure_keeps_completed_rest_trace(fake_world_runner,carla_scenario):
    world=fake_world_runner();read=world.get_snapshot;spawn=world.try_spawn_actor;frame8_reads=0;injections=[]
    def record_actor(*args):
        actor=spawn(*args)
        actor.set_target_velocity=lambda _:injections.append(actor.id)
        return actor
    def fail_after_successful_gate():
        nonlocal frame8_reads
        if world.frame==8:
            frame8_reads+=1
            if frame8_reads==2:raise RuntimeError('Injected first snapshot read after successful startup')
        return read()
    world.try_spawn_actor=record_actor;world.get_snapshot=fail_after_successful_gate
    with pytest.raises(AdapterExecutionError,match='first snapshot read') as raised:CarlaRunner().execute(carla_scenario)
    startup_payloads=[p for p in raised.value.artifact_payloads if p.name=='carla-startup.json']
    assert len(startup_payloads)==1 and frame8_reads==2 and injections==[]
    startup=json.loads(startup_payloads[0].content)
    assert startup['status']=='passed' and startup['completed_ticks']==8 and len(startup['trace'])==8
    assert startup['trace'][-1]['world_frame']==8
    assert 'physics_configuration' not in startup['context']  # The readback was never attempted.
    assert all(a.destroy_called for a in world.vehicles)
    assert world.settings.synchronous_mode is False and world.weather=='original-weather'


@pytest.mark.parametrize('unavailable', [False, True])
def test_telemetry_nonfinite_null_missing_and_rpc_failure_preserve_raw(monkeypatch,carla_scenario,unavailable):
    world=install_live_world(monkeypatch);spawn=world.try_spawn_actor
    def telemetry_actor(*args):
        actor=spawn(*args)
        def telemetry():
            if unavailable:raise RuntimeError('Telemetry RPC unavailable')
            return SimpleNamespace(speed=2.,engine_rpm=1000.,gear=0,wheels=[SimpleNamespace(
                omega=float('inf'),lat_slip=None,tire_friction=.8) for _ in range(4)])
        actor.get_telemetry_data=telemetry
        return actor
    world.try_spawn_actor=telemetry_actor;request=live_request(carla_scenario)
    result=CarlaRunner().execute_with_context(request);validate(request,result.artifact_payloads,True)
    data=saved(result.artifact_payloads,'vehicle_response');assert len(data['observations'])==81
    telemetry=data['observations'][0]['actors']['ego']['telemetry']
    if unavailable:
        assert telemetry['status']=='unavailable' and telemetry['wheels']==[]
    else:
        assert telemetry['status']=='available'
        fields=telemetry['wheels'][0]['fields']
        assert fields['omega']['status']=='nonfinite' and fields['omega']['value'] is None
        assert fields['lat_slip']['status']=='null' and fields['long_slip']['status']=='unavailable'
        assert fields['tire_friction']['value']==.8 and telemetry['scalars']['engine_rpm']['value']==1000.
        assert 'torque' not in fields and 'long_force' not in fields


def test_initial_velocity_request_survives_async_rpc_exception(monkeypatch,carla_scenario):
    world=install_live_world(monkeypatch);spawn=world.try_spawn_actor
    def failing_actor(*args):
        actor=spawn(*args)
        if actor.id==1:
            def fail(_):raise RuntimeError('Unknown target velocity send result')
            actor.set_target_velocity=fail
        return actor
    world.try_spawn_actor=failing_actor;request=live_request(carla_scenario)
    with pytest.raises(AdapterExecutionError) as raised:CarlaRunner().execute_with_context(request)
    validate(request,raised.value.artifact_payloads,False)
    data=saved(raised.value.artifact_payloads,'vehicle_response')
    assert len(data['initial_velocity_requests'])==1 and data['initial_velocity_requests'][0]['ack_status']=='unknown'
    assert data['observations']==[]


def test_raw_vector_failure_keeps_already_observed_physics_prefix(monkeypatch,carla_scenario):
    world=install_live_world(monkeypatch);snapshot=world.get_snapshot
    def invalid_snapshot():
        result=snapshot()
        if world.sensor_ticks==3:
            find=result.find
            def bad_find(identifier):
                actor=find(identifier)
                if identifier==1:actor.get_acceleration=lambda:SimpleNamespace(x=float('nan'),y=0,z=0)
                return actor
            result.find=bad_find
        return result
    world.get_snapshot=invalid_snapshot;request=live_request(carla_scenario)
    with pytest.raises(AdapterExecutionError) as raised:CarlaRunner().execute_with_context(request)
    validate(request,raised.value.artifact_payloads,False)
    response=saved(raised.value.artifact_payloads,'vehicle_response');physics=saved(raised.value.artifact_payloads,'physics_trace')
    assert len(response['observations'])==2 and len(physics['observations'])==3
    assert 'Nonfinite' in response['error']


def test_rpc_cross_frame_is_unavailable_not_a_coherent_claim():
    frames=iter([10,11]);world=SimpleNamespace(get_snapshot=lambda:SimpleNamespace(frame=next(frames)))
    bracket,data=_rpc(world,10,lambda:{'observation':1})
    assert bracket['status']=='unavailable' and bracket['before_world_frame']==10 and bracket['after_world_frame']==11
    assert data is None and 'crossed' in bracket['error']
