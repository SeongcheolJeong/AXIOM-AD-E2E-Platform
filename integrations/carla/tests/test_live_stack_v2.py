"""Native kernel2 contract over an authored fake world, never CARLA acceptance.

Grid returns below are fixed fixtures. The fake longitudinal plant uses 4m/s²
at full brake, independently of the selected experimental controller mapping.
"""
import copy
import json
import struct

import httpx
import pytest
from fastapi.testclient import TestClient

from ade2e.adapters import AdapterExecutionError, AdapterInputError
from ade2e.execution_attempt import ExecutionAttemptRequest
from ade2e.simulation_context import (SimulationControlSelection, SimulationExecutionRequest,
    build_simulation_context, simulation_input_hash)
from ade2e.simulation_evidence import validate_simulation_artifacts
from ade2e.stack_runtime import StackRegistry, StackSession
from ade2e.stack_schemas import StackSnapshot, StackDetail, StackPacket, validate_stack_definition
from axiom_carla import wire, worker
from axiom_carla.recording import _json
from axiom_carla.remote import RemoteCarlaAdapter
from axiom_carla.runner import CarlaRunner
from axiom_carla.spool import Store, StoreUnavailable
from axiom_carla.version import ADAPTER_VERSION
from test_live_stack import live_request, install_live_world, artifacts, digest


def v2_request(scenario, *, ground=True, brake_model=True, profile='carla-lidar-ego-near/1'):
    original = live_request(scenario)
    registry = StackRegistry(load_plugins=False)
    selections = {}
    for meta in registry.list():
        if meta.version != '2':
            continue
        parameters = copy.deepcopy(meta.default_parameters)
        if meta.role == 'perception' and ground:
            parameters.update(ground_reference_z_m=0., ground_reference_source='Authored flat fake world, uncalibrated')
        if meta.role == 'policy':
            parameters.update(allow_no_obstacle_observed_motion=True)
        if meta.role == 'controller' and brake_model:
            parameters.update(assumed_full_brake_deceleration_mps2=4., brake_response_source='Declared authored fake-plant assumption, not CARLA calibration')
        selections[meta.role] = {'id': meta.id, 'version': meta.version, 'parameters': parameters}
    definition = validate_stack_definition({'schema_version': '2', 'kernel_version': 'stack-kernel/2', **selections})
    snapshot = StackSnapshot(id='test-stack-v2', revision=1, created_at='2026-09-13T00:00:00Z',
        name='Authored stateful native fixture', note='No simulator or measured vehicle evidence',
        definition=definition, kernel_version='stack-kernel/2', component_metadata=registry.validate_definition(definition))
    content = _json(snapshot.model_dump(mode='json'))
    stack = StackDetail(**snapshot.model_dump(mode='json'), manifest_hash=digest(content), manifest_size_bytes=len(content),
        download_url='/api/v1/stacks/test-stack-v2/export?revision=1')
    rig = original.context.rig_snapshot
    selection = SimulationControlSelection(stack_id=stack.id, stack_revision=1, rig_id=rig.id, rig_revision=1,
        lidar_sensor_key='roof', sensor_profile_id=profile)
    context = build_simulation_context(stack, rig, selection, original.scenario.seed)
    return SimulationExecutionRequest(execution_id='run_authored_native_v2', scenario=original.scenario, context=context,
        input_hash=simulation_input_hash(original.scenario, 'carla', ADAPTER_VERSION, context))


def sampled_grid(*, surface_x=None):
    points = [(2.5 + row + offset, y, -2.2, .5)
              for row in range(13) for offset in (.15, .3) for y in (-.8, 0., .8)]
    if surface_x is not None:
        points.append((surface_x, 0., -1.2, .5))
    return b''.join(struct.pack('<ffff', *point) for point in points)


@pytest.fixture
def native_v2(carla_scenario, monkeypatch):
    world = install_live_world(monkeypatch, raw=sampled_grid(), route_left=0.)
    request = v2_request(carla_scenario)
    result = CarlaRunner().execute_with_context(request)
    return request, result, world


def validate(request, result):
    validate_simulation_artifacts(request, result.artifact_payloads, completed=True,
        trajectory=result.trajectory, adapter_version=ADAPTER_VERSION)


def test_full_v2_native_chain_preserves_state_packet_and_complete_denominator(native_v2):
    request, result, world = native_v2
    validate(request, result)
    saved = artifacts(result)
    ticks = json.loads(saved['carla-stack-trace.json'])['ticks']
    assert len(ticks) == 80 and len(result.trajectory) == 81
    assert all(t['status'] == 'completed' and t['control_acknowledged'] for t in ticks)
    session = StackSession(request.context.stack_snapshot.definition)
    for index, tick in enumerate(ticks):
        output = tick['output']
        assert output['kernel_version'] == 'stack-kernel/2'
        assert output['observation']['coverage']['qualified_prefix_m'] == 12.5
        assert output['decision']['mode'] == 'pilot_cruise'
        assert output['state_before']['last_sequence'] == (index-1 if index else None)
        ref = tick['points']
        source = saved['carla-stack-points.bin'][ref['offset_bytes']:ref['offset_bytes']+ref['size_bytes']]
        repeated = session.step(StackPacket.model_validate(tick['packet']), source).model_dump(mode='json')
        assert {k:v for k,v in repeated.items() if k != 'elapsed_ms'} == {k:v for k,v in output.items() if k != 'elapsed_ms'}
    assert world.settings.synchronous_mode is False and all(actor.destroy_called for actor in world.vehicles)


@pytest.mark.parametrize('ground, brake_model, sampled', [(False, True, False), (True, False, True)])
def test_missing_sensor_or_controller_assumptions_stop_with_separate_observation_facts(carla_scenario, monkeypatch, ground, brake_model, sampled):
    install_live_world(monkeypatch, raw=sampled_grid(), route_left=0.)
    request = v2_request(carla_scenario, ground=ground, brake_model=brake_model)
    result = CarlaRunner().execute_with_context(request)
    validate(request, result)
    ticks = json.loads(artifacts(result)['carla-stack-trace.json'])['ticks']
    assert all(t['output']['observation']['input_valid'] == sampled for t in ticks)
    assert all(t['output']['decision']['mode'] == 'unknown_stop' for t in ticks)
    assert all(t['requested_carla_control']['brake'] == 1 and t['requested_carla_control']['throttle'] == 0 for t in ticks)


def test_controlled_stop_plan_is_latched_and_never_restarts_from_fixed_returns(carla_scenario, monkeypatch):
    install_live_world(monkeypatch, raw=sampled_grid(surface_x=7.75), route_left=0.)
    request = v2_request(carla_scenario)
    result = CarlaRunner().execute_with_context(request)
    validate(request, result)
    ticks = json.loads(artifacts(result)['carla-stack-trace.json'])['ticks']
    plans = [t['output']['decision']['stop_plan'] for t in ticks]
    assert ticks[0]['output']['decision']['mode'] == 'controlled_stop'
    assert all(plan == plans[0] for plan in plans)
    assert all(t['output']['state_after']['latched'] and t['requested_carla_control']['throttle'] == 0 for t in ticks)


@pytest.mark.parametrize('fault', ['sensor_frame', 'readback', 'missing_ack'])
def test_v2_native_failure_retains_only_stateful_prefix_and_no_resumed_tick(carla_scenario, monkeypatch, fault):
    world = install_live_world(monkeypatch, raw=sampled_grid(), route_left=0., fault=fault)
    request = v2_request(carla_scenario)
    with pytest.raises(AdapterExecutionError) as error:
        CarlaRunner().execute_with_context(request)
    validate_simulation_artifacts(request, error.value.artifact_payloads, completed=False, adapter_version=ADAPTER_VERSION)
    ticks = json.loads(artifacts(error.value)['carla-stack-trace.json'])['ticks']
    failed = next(i for i,t in enumerate(ticks) if t['status'] == 'failed')
    assert len(ticks) == 80 and all(t['status'] == 'not_attempted' for t in ticks[failed+1:])
    assert all(t['output'] is None for t in ticks[failed+1:])
    assert all(actor.destroy_called for actor in world.vehicles)


@pytest.mark.parametrize('damage', ['state_prefix', 'kernel', 'coverage_prefix', 'geometry', 'count', 'current_distance', 'lowered_distance'])
def test_forged_v2_inner_evidence_rejected_even_after_rehash(native_v2, damage):
    request, result, _ = native_v2
    result = result.model_copy(deep=True)
    trace = next(p for p in result.artifact_payloads if p.kind == 'live_stack_trace')
    content = json.loads(trace.content)
    output = content['ticks'][1]['output']
    if damage == 'state_prefix': output['state_before']['previous_speed_mps'] += .1
    elif damage == 'kernel': output['kernel_version'] = 'stack-kernel/1'
    elif damage == 'coverage_prefix': output['observation']['coverage']['qualified_prefix_m'] = 13.
    elif damage == 'geometry':
        output['observation']['coverage']['cells'][0]['point_min_xyz_m'][2] += .05
        output['observation']['coverage']['cells'][0]['point_max_xyz_m'][2] += .05
    elif damage == 'count': output['observation']['input_points'] += 1
    elif damage == 'lowered_distance': output['decision']['current_stopping_distance_m'] = 0.
    else: output['decision']['current_stopping_distance_m'] = 999.
    trace.content = _json(content)
    manifest = next(p for p in result.artifact_payloads if p.kind == 'live_stack_manifest')
    content = json.loads(manifest.content)
    content['artifacts'] = [{'name':p.name,'kind':p.kind,'size_bytes':len(p.content),'sha256':digest(p.content)}
                            for p in result.artifact_payloads if p.kind != 'live_stack_manifest']
    manifest.content = _json(content)
    with pytest.raises(ValueError):
        validate(request, result)


@pytest.mark.parametrize('old_version', ['0.5.0', '0.6.0'])
@pytest.mark.parametrize('profile', ['carla-lidar-ego/1', 'carla-lidar-ego-near/1'])
def test_v2_cannot_be_relabelled_as_historical_version_on_read_or_dispatch(tmp_path, monkeypatch, carla_scenario, old_version, profile):
    original = v2_request(carla_scenario, profile=profile)
    request = ExecutionAttemptRequest(execution_id=original.execution_id, adapter_id='carla', adapter_version=old_version,
        scenario=original.scenario, context=original.context,
        input_hash=simulation_input_hash(original.scenario, 'carla', old_version, original.context))
    monkeypatch.setenv('ADE2E_CARLA_EXECUTION_STORE', str(tmp_path/'store'))
    monkeypatch.delenv('ADE2E_CARLA_WORKER_TOKEN', raising=False)
    store = Store(tmp_path/'store')
    adapter = RemoteCarlaAdapter('http://worker.test', transport=httpx.MockTransport(lambda _:pytest.fail('Invalid kernel crossed network')))
    with pytest.raises(AdapterInputError): adapter.inspect_execution(request)
    with pytest.raises(AdapterInputError): adapter.execute_attempt(request, expected_store_id=store.id)
    with pytest.raises(ValueError): wire.encode_failure('Invalid kernel', request=request.simulation_request(), adapter_version=old_version)
    with TestClient(worker.app) as client:
        assert client.post('/v3/inspect', json=request.model_dump(mode='json')).status_code == 422
        assert client.post('/v3/simulate', params={'store_id':store.id}, json=request.model_dump(mode='json')).status_code == 422
    assert store.find(request.execution_id) is None
    # Internal callers cannot launder a rehashed unsupported historical context.
    with store.begin(request) as attempt:
        with pytest.raises(StoreUnavailable): attempt.verified_meta()
        with attempt.db() as db: assert db.execute('SELECT count(*) FROM archive').fetchone()[0] == 0
