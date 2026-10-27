"""Actual shared kernel and MCAP bytes over a deterministic fake world, not CARLA physics."""
import base64
import copy
import hashlib
import io
import json
import math
import struct
from types import SimpleNamespace

import pytest
from mcap.reader import make_reader

from ade2e.adapters import AdapterExecutionError, AdapterInputError
from ade2e.sensor_schemas import RigCreate, RigSnapshot, RigDetail
from ade2e.simulation_context import (SimulationControlSelection, SimulationExecutionRequest,
    build_simulation_context, simulation_input_hash)
from ade2e.stack_runtime import StackRegistry, StackSession
from ade2e.stack_schemas import StackDefinition, StackSnapshot, StackDetail, StackPacket
from ade2e.simulation_evidence import validate_simulation_artifacts
from axiom_carla.recording import EGO_SCHEMA, _json
from axiom_carla.runner import CarlaRunner
from axiom_carla.stack_bridge import _rh_rotation, carla_control
from axiom_carla.version import ADAPTER_VERSION
from test_runner_evidence import FakeWorld, FakeVehicle, FakeCollisionSensor, transform


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def live_request(scenario, *, ground=True):
    scenario = scenario.model_copy(update={'parameters': scenario.parameters.model_copy(update={'ego_speed_mps': 2.0, 'target_speed_mps': 2.0})})
    registry = StackRegistry(load_plugins=False)
    selections = {}
    for component in registry.list():
        if component.version != '1':
            continue
        parameters = copy.deepcopy(component.default_parameters)
        if component.role == 'perception' and ground:
            parameters.update(ground_reference_z_m=0.0, ground_reference_source='Authored flat fake-world ground; no real calibration')
        if component.role == 'policy':
            parameters.update(cruise_speed_mps=3.0, allow_no_obstacle_observed_motion=True)
        selections[component.role] = {'id': component.id, 'version': component.version, 'parameters': parameters}
    definition = StackDefinition(**selections)
    snapshot = StackSnapshot(id='test-stack', revision=1, created_at='2026-09-13T00:00:00Z', name='Native boundary test',
        note='Authored deterministic fake-world evidence only', definition=definition, component_metadata=registry.validate_definition(definition))
    content = _json(snapshot.model_dump(mode='json'))
    stack = StackDetail(**snapshot.model_dump(mode='json'), manifest_hash=digest(content), manifest_size_bytes=len(content),
        download_url='/api/v1/stacks/test-stack/export?revision=1')
    definition = RigCreate(name='Fixed native recipe', reference_frame='ego', coordinate_convention='right_handed_z_up',
        note='Nominal declared mount only', sensors=[{'key': 'roof', 'name': 'Roof LiDAR', 'kind': 'lidar', 'frame_id': 'lidar_roof',
            'transform': {'translation_m': [0, 0, 2.2], 'rotation_xyzw': [0, 0, 0, 1]}}])
    snapshot = RigSnapshot(**definition.model_dump(mode='json'), id='test-rig', revision=1, created_at='2026-09-13T00:00:00Z',
        definition_hash=digest(_json(definition.model_dump(mode='json'))))
    content = _json(snapshot.model_dump(mode='json'))
    rig = RigDetail(**snapshot.model_dump(mode='json'), manifest_hash=digest(content), manifest_size_bytes=len(content),
        download_url='/api/v1/sensor-rigs/test-rig/export?revision=1')
    selection = SimulationControlSelection(stack_id=stack.id, stack_revision=1, rig_id=rig.id, rig_revision=1, lidar_sensor_key='roof')
    context = build_simulation_context(stack, rig, selection, scenario.seed)
    return SimulationExecutionRequest(execution_id='reserved-test-run', scenario=scenario, context=context,
        input_hash=simulation_input_hash(scenario, 'carla', ADAPTER_VERSION, context))


@pytest.mark.parametrize('profile_id, attributes', [
    ('carla-lidar-ego-dense/1', {'points_per_second': '128000'}),
    ('carla-lidar-ego-near/1', {'channels': '64', 'points_per_second': '256000', 'lower_fov': '-65.0'}),
])
def test_recipe_sets_detached_blueprint_and_preserves_exact_profile_evidence(monkeypatch, carla_scenario, profile_id, attributes):
    """Authored fake returns check plumbing, never actual sampling or sensing quality."""
    world = install_live_world(monkeypatch)
    baseline = live_request(carla_scenario)
    prior = baseline.context
    selection = SimulationControlSelection(stack_id=prior.stack_snapshot.id, stack_revision=prior.stack_snapshot.revision,
        rig_id=prior.rig_snapshot.id, rig_revision=prior.rig_snapshot.revision, lidar_sensor_key=prior.lidar_sensor_key,
        sensor_profile_id=profile_id)
    context = build_simulation_context(prior.stack_snapshot, prior.rig_snapshot, selection, baseline.scenario.seed)
    request = SimulationExecutionRequest(execution_id=baseline.execution_id, scenario=baseline.scenario, context=context,
        input_hash=simulation_input_hash(baseline.scenario, 'carla', ADAPTER_VERSION, context))
    result = CarlaRunner().execute_with_context(request)
    validate_simulation_artifacts(request, result.artifact_payloads, completed=True,
                                  trajectory=result.trajectory, adapter_version=ADAPTER_VERSION)
    values = {item.name: item.content for item in result.artifact_payloads}
    manifest = json.loads(values['carla-stack-manifest.json'])
    sources = json.loads(values['carla-stack-trace.json'])
    expected_attributes = prior.sensor_profile.attributes | attributes
    for key in ('points_per_second', 'channels'):
        assert manifest['actual_lidar_attributes'][key] == {'type': 'int', 'value': int(expected_attributes[key])}
    assert manifest['actual_lidar_attributes']['lower_fov'] == {'type': 'float32', 'value': float(expected_attributes['lower_fov'])}
    assert manifest['worker_adapter_version'] == ADAPTER_VERSION
    assert manifest['context_hash'] == sources['context_hash'] == context.context_hash
    assert sources['expected_intervals'] == 80
    profile = context.sensor_profile.model_dump(mode='json')
    assert profile['attributes'] == expected_attributes
    assert context.context_hash != prior.context_hash and request.input_hash != baseline.input_hash
    # Exact profile is retained in the source recording metadata, not inferred
    # from the fake callback point count or a nominal 6,400-return claim.
    recording = make_reader(io.BytesIO(values['carla-stack-sources.mcap']))
    metadata = list(recording.iter_metadata())
    assert any(profile['profile_hash'] in json.dumps(item.metadata) for item in metadata)
    assert all(actor.destroy_called for actor in world.vehicles)


def pose(x=0, y=0, z=0, pitch=0, yaw=0, roll=0):
    result = transform(x, y, z)
    result.rotation = SimpleNamespace(pitch=pitch, yaw=yaw, roll=roll)
    # Native CARLA GetMatrix convention, with pitch-positive-up and y-right.
    def matrix():
        p, y, r = (math.radians(getattr(result.rotation, name)) for name in ('pitch', 'yaw', 'roll'))
        cp, sp, cy, sy, cr, sr = math.cos(p), math.sin(p), math.cos(y), math.sin(y), math.cos(r), math.sin(r)
        return [[cp*cy, cy*sp*sr-sy*cr, -cy*sp*cr-sy*sr, result.location.x],
            [cp*sy, sy*sp*sr+cy*cr, -sy*sp*cr+cy*sr, result.location.y],
            [sp, -cp*sr, cp*cr, result.location.z], [0, 0, 0, 1]]
    result.get_matrix = matrix
    result.get_forward_vector = lambda: SimpleNamespace(**dict(zip(('x', 'y', 'z'), [row[0] for row in matrix()[:3]])))
    return result


def install_live_world(monkeypatch, *, fault=None, lead_x=45, route_left=.2, raw=None):
    class Blueprint:
        def __init__(self, name):
            self.name, self.values = name, {}
        def has_attribute(self, name):
            return not (fault == 'missing_attribute' and name == 'dropoff_general_rate')
        def set_attribute(self, name, value):
            self.values[name] = value
        def get_attribute(self, name):
            value = '99' if fault == 'blueprint_readback' and name == 'channels' else self.values[name]
            integer = name in {'channels', 'points_per_second', 'noise_seed'}
            return SimpleNamespace(type='wrong' if fault == 'attribute_type' else 'int' if integer else 'float', as_int=lambda: int(value),
                as_float=lambda: struct.unpack('<f', struct.pack('<f', float(value)))[0])

    class Vehicle(FakeVehicle):
        def __init__(self, pose):
            super().__init__(pose)
            self.control = SimpleNamespace(throttle=0, brake=0, steer=0, reverse=False, hand_brake=False)
            self.commands = []
            self.physics = SimpleNamespace(wheels=[SimpleNamespace(tire_friction=3.5) for _ in range(4)])
        def get_physics_control(self):
            return copy.deepcopy(self.physics)
        def apply_physics_control(self, value):
            self.physics = copy.deepcopy(value)
            for wheel in self.physics.wheels:
                wheel.tire_friction = struct.unpack('<f', struct.pack('<f', wheel.tire_friction))[0]
        def apply_control(self, control):
            if self.id == 1 and fault == 'send' and len(self.commands) == 2:
                raise RuntimeError('Injected unknown send outcome')
            self.control = control
            self.commands.append((world.frame, vars(control).copy()))
        def get_control(self):
            if fault == 'readback' and len(self.commands) == 2:
                raise RuntimeError('Injected readback failure')
            return self.control
        def destroy(self):
            acknowledged = super().destroy()
            return False if fault == 'cleanup' else acknowledged

    class World(FakeWorld):
        def __init__(self):
            super().__init__()
            self.lidar = FakeCollisionSensor()
            self.sensor_ticks = 0
            self.mutations = 0
            self.raw = raw if raw is not None else b''.join(struct.pack('<ffff', x, y, -2.2, .5)
                for x in (4, 6, 14, 17, 24, 29) for y in (-.3, .3))
            self.blueprints = {}
        def get_blueprint_library(self):
            def find(name):
                return self.blueprints.setdefault(name, Blueprint(name))
            return SimpleNamespace(find=find)
        def get_map(self):
            def waypoint(location):
                return SimpleNamespace(next=lambda distance: [SimpleNamespace(transform=pose(location.x+distance, location.y-route_left*distance))])
            return SimpleNamespace(name='/Game/Carla/Maps/Town01', get_waypoint=waypoint)
        def try_spawn_actor(self, blueprint, value):
            vehicle = Vehicle(value)
            vehicle.id = len(self.vehicles)+1
            self.vehicles.append(vehicle)
            return vehicle
        def spawn_actor(self, blueprint, *args, **kwargs):
            return self.lidar if blueprint.name == 'sensor.lidar.ray_cast' else self.collision
        def apply_settings(self, settings):
            self.mutations += 1
            super().apply_settings(settings)
        def tick(self, seconds=10):
            for vehicle in self.vehicles:
                control = vehicle.control
                vehicle.velocity.x = max(0, vehicle.velocity.x + (2*control.throttle-4*control.brake)*.05)
            frame = super().tick(seconds)
            if self.lidar.callback:
                self.sensor_ticks += 1
                bad = self.sensor_ticks == 3
                source_frame = frame+1 if fault == 'sensor_frame' and bad else frame
                timestamp = frame*.05 + (.001 if fault == 'sensor_clock' and bad else 0)
                data = b'bad' if fault == 'layout' and bad else self.raw
                self.lidar.callback(SimpleNamespace(frame=source_frame, timestamp=timestamp, raw_data=data))
                if fault == 'physics_frame' and bad:
                    self.frame += 1
            return frame
        def get_snapshot(self):
            snapshot = super().get_snapshot()
            # Native snapshots are immutable; copying prevents fake actor writes
            # from silently changing a previously observed pose.
            actors = {}
            for vehicle in self.vehicles:
                location, rotation = vehicle.pose.location, vehicle.pose.rotation
                observed = FakeVehicle(pose(**vars(location), **vars(rotation)))
                observed.velocity = copy.copy(vehicle.velocity)
                if fault == 'startup':
                    observed.get_acceleration = lambda: SimpleNamespace(x=2, y=0, z=0)
                actors[vehicle.id] = observed
            snapshot.find = lambda identifier: actors.get(identifier)
            return snapshot
    world = World()
    carla = SimpleNamespace(WeatherParameters=lambda **kwargs: SimpleNamespace(**kwargs),
        Location=lambda **kwargs: SimpleNamespace(**({'x': 0, 'y': 0, 'z': 0} | kwargs)),
        Rotation=lambda **kwargs: SimpleNamespace(**({'pitch': 0, 'yaw': 0, 'roll': 0} | kwargs)),
        Transform=lambda location=None, rotation=None: pose(**(vars(location) if location else {}), **(vars(rotation) if rotation else {})),
        Vector3D=lambda x, y, z: SimpleNamespace(x=x, y=y, z=z),
        VehicleControl=lambda **kwargs: SimpleNamespace(**kwargs))
    carla.ActorAttributeType = SimpleNamespace(Int='int', Float='float')
    carla.command = SimpleNamespace(ApplyVehicleControl=lambda actor_id, control: (actor_id, control))
    def batch(commands, do_tick):
        assert do_tick is False and len(commands) == 1
        actor_id, control = commands[0]
        if fault == 'missing_ack':
            return []
        if fault == 'rejected':
            return [SimpleNamespace(actor_id=0, error='Injected server rejection', has_error=lambda: True)]
        world.vehicles[actor_id-1].apply_control(control)
        return [SimpleNamespace(actor_id=actor_id if fault != 'ack_actor' else 999, error='', has_error=lambda: False)]
    client = SimpleNamespace(apply_batch_sync=batch)
    monkeypatch.setenv('ADE2E_CARLA_CAPTURE', '1')
    monkeypatch.setattr(CarlaRunner, '_connect', lambda _: (carla, client, world, 'fake-world-no-physics-validation'))
    monkeypatch.setattr(CarlaRunner, '_straight_spawn', lambda *_: (pose(), pose(x=lead_x)))
    def lead_only(_carla, _map, actor, target, brake):
        assert actor.id == 2, 'The GT controller must never be used for the stack-controlled ego'
    monkeypatch.setattr(CarlaRunner, '_control', staticmethod(lead_only))
    return world


def artifacts(result):
    return {item.name: item.content for item in result.artifact_payloads}


def test_native_sources_kernel_commands_and_next_physics_are_linked(monkeypatch, carla_scenario, tmp_path):
    world = install_live_world(monkeypatch)
    request = live_request(carla_scenario)
    result = CarlaRunner().execute_with_context(request)
    validate_simulation_artifacts(request, result.artifact_payloads, completed=True, trajectory=result.trajectory, adapter_version=ADAPTER_VERSION)
    saved = artifacts(result)
    trace = json.loads(saved['carla-stack-trace.json'])
    physics = json.loads(saved['carla-stack-physics.json'])
    assert len(trace['ticks']) == 80 and len(physics['observations']) == 81 == len(result.trajectory)
    assert trace['status'] == 'completed'
    reader = make_reader(io.BytesIO(saved['carla-stack-sources.mcap']))
    messages = list(reader.iter_messages(log_time_order=False))
    assert len(messages) == 162
    from ade2e.stack_replay_sources import EGO_SCHEMA as REPLAY_EGO_SCHEMA, decode_ego
    assert EGO_SCHEMA == REPLAY_EGO_SCHEMA
    replay = StackSession(request.context.stack_snapshot.definition)
    for index, tick in enumerate(trace['ticks']):
        assert tick['status'] == 'completed' and tick['application_status'] == 'next_frame_observed'
        assert tick['next_world_frame'] == tick['source_world_frame']+1
        assert tick['requested_carla_control']['steer'] < 0  # RH left -> native negative steer.
        assert tick['observed_control'] == tick['requested_carla_control']
        cloud_ref, ego_ref = tick['source_refs']['lidar'], tick['source_refs']['ego']
        for ref in (cloud_ref, ego_ref):
            _, channel, message = messages[ref['ordinal']]
            assert channel.id == ref['channel_id'] and digest(message.data) == ref['message_sha256']
            assert str(message.log_time) == ref['log_time_ns'] == ref['publish_time_ns']
        cloud = json.loads(messages[cloud_ref['ordinal']][2].data)
        native = base64.b64decode(cloud['data'])
        reverted = bytearray(native)
        for offset in range(7, len(reverted), 16):
            reverted[offset] ^= 0x80
        assert digest(reverted) == cloud_ref['raw_carla_xyzi_sha256']
        assert decode_ego(messages[ego_ref['ordinal']][2].data).frame_id == 'ego'
        ref = tick['points']
        points = saved[ref['artifact_name']][ref['offset_bytes']:ref['offset_bytes']+ref['size_bytes']]
        assert digest(points) == ref['sha256'] == tick['output']['input_points_sha256']
        assert list(struct.iter_unpack('<fff', points))[0] == pytest.approx((4, .3, 0), abs=1e-6)
        output = replay.step(StackPacket.model_validate(tick['packet']), points).model_dump(mode='json')
        assert {k: v for k, v in output.items() if k != 'elapsed_ms'} == {k: v for k, v in tick['output'].items() if k != 'elapsed_ms'}
    manifest = json.loads(saved['carla-stack-manifest.json'])
    for descriptor in manifest['artifacts']:
        assert digest(saved[descriptor['name']]) == descriptor['sha256']
    assert len(world.vehicles[0].commands) == 80
    assert world.settings.synchronous_mode is False and world.lidar.destroyed
    # UI agent/root may inspect this authored protocol fixture without live claims.
    (tmp_path/'trace.json').write_bytes(saved['carla-stack-trace.json'])


@pytest.mark.parametrize('fault,expected', [('send', 'send_outcome_unknown'), ('readback', 'next_frame_observed'),
    ('sensor_frame', 'next_frame_observed'), ('sensor_clock', 'next_frame_observed'), ('layout', 'next_frame_observed'),
    ('physics_frame', 'acknowledged_without_next_observation'), ('rejected', 'send_rejected'), ('ack_actor', 'send_outcome_unknown'),
    ('missing_ack', 'send_outcome_unknown')])
def test_failure_retains_prefix_and_precise_application_state(monkeypatch, carla_scenario, fault, expected):
    world = install_live_world(monkeypatch, fault=fault)
    request = live_request(carla_scenario)
    with pytest.raises(AdapterExecutionError) as caught:
        CarlaRunner().execute_with_context(request)
    validate_simulation_artifacts(request, caught.value.artifact_payloads, completed=False, adapter_version=ADAPTER_VERSION)
    saved = artifacts(caught.value)
    trace = json.loads(saved['carla-stack-trace.json'])
    failed = next(row for row in trace['ticks'] if row['status'] == 'failed')
    assert failed['application_status'] == expected
    assert len(trace['ticks']) == 80 and any(row['status'] == 'not_attempted' for row in trace['ticks'])
    assert trace['status'] == 'partial'
    physics = json.loads(saved['carla-stack-physics.json'])['observations']
    if expected == 'next_frame_observed':
        assert physics[-1]['world_frame'] == failed['next_world_frame']
    messages = list(make_reader(io.BytesIO(saved['carla-stack-sources.mcap'])).iter_messages())
    assert 2 <= len(messages) < 162 and len(messages) % 2 == 0
    if fault == 'layout':
        assert saved['carla-stack-invalid-source.bin'] == b'bad'
    assert world.settings.synchronous_mode is False and all(actor.destroy_called for actor in world.vehicles)


@pytest.mark.parametrize('raw', [b'', struct.pack('<ffff', 5, math.nan, 0, .5)])
def test_empty_and_nonfinite_sources_are_preserved_and_command_unknown_stop(monkeypatch, carla_scenario, raw):
    world = install_live_world(monkeypatch, raw=raw)
    request = live_request(carla_scenario)
    result = CarlaRunner().execute_with_context(request)
    validate_simulation_artifacts(request, result.artifact_payloads, completed=True, trajectory=result.trajectory, adapter_version=ADAPTER_VERSION)
    saved = artifacts(result)
    rows = json.loads(saved['carla-stack-trace.json'])['ticks']
    assert all(row['output']['decision']['mode'] == 'unknown_stop' for row in rows)
    assert all(command['brake'] == 1 and command['throttle'] == 0 for _, command in world.vehicles[0].commands)
    assert saved['carla-stack-raw-xyzi.bin'] == raw*81
    assert len(list(make_reader(io.BytesIO(saved['carla-stack-sources.mcap'])).iter_messages())) == 162


@pytest.mark.parametrize('fault', ['missing_attribute', 'blueprint_readback', 'attribute_type', 'disabled', 'input_hash', 'component_drift'])
def test_preflight_rejects_before_world_mutation(monkeypatch, carla_scenario, fault):
    world = install_live_world(monkeypatch, fault=fault)
    request = live_request(carla_scenario)
    if fault == 'disabled':
        monkeypatch.setenv('ADE2E_CARLA_CAPTURE', '0')
    elif fault == 'input_hash':
        request.input_hash = '0'*64
    elif fault == 'component_drift':
        from ade2e.stack_components import LidarCorridor
        original = LidarCorridor.metadata
        def changed(self):
            value = original(self)
            value.implementation['algorithm'] = 'changed-at-same-version'
            return value
        monkeypatch.setattr(LidarCorridor, 'metadata', changed)
    with pytest.raises(AdapterInputError):
        CarlaRunner().execute_with_context(request)
    assert world.mutations == 0 and not world.vehicles


def test_surrounding_gt_changes_never_change_stack_observation_or_commands(monkeypatch, carla_scenario):
    outputs = []
    for lead_x in (4, 150):
        install_live_world(monkeypatch, lead_x=lead_x)
        result = CarlaRunner().execute_with_context(live_request(carla_scenario))
        rows = json.loads(artifacts(result)['carla-stack-trace.json'])['ticks']
        outputs.append([{k: row[k] for k in ('packet', 'requested_carla_control')} for row in rows])
    assert outputs[0] == outputs[1]


def test_unknown_ground_remains_a_supported_stop_context(monkeypatch, carla_scenario):
    install_live_world(monkeypatch)
    result = CarlaRunner().execute_with_context(live_request(carla_scenario, ground=False))
    row = json.loads(artifacts(result)['carla-stack-trace.json'])['ticks'][0]
    assert 'ground_reference_unknown' in row['output']['observation']['reasons']
    assert row['requested_carla_control']['brake'] == 1


def test_pose_uses_reflected_matrix_and_control_maps_positive_left():
    yaw, pitch, roll = _rh_rotation(pose(pitch=2, yaw=30, roll=3))
    assert (yaw, pitch, roll) == pytest.approx(tuple(math.radians(v) for v in (-30, -2, 3)))
    from ade2e.stack_schemas import StackControl
    assert carla_control(StackControl(throttle=.2, brake=0, steer=.3))['steer'] == -.3


def test_budget_failure_is_before_ego_actuation_and_preserves_source(monkeypatch, carla_scenario):
    import axiom_carla.stack_bridge as bridge
    world = install_live_world(monkeypatch)
    original = bridge.LiveEvidence.reserve_before_dispatch
    def bounded(self):
        original(self)
        if len(self.messages) >= 4:
            raise ValueError('Injected source preservation budget exhausted')
    monkeypatch.setattr(bridge.LiveEvidence, 'reserve_before_dispatch', bounded)
    with pytest.raises(AdapterExecutionError) as caught:
        CarlaRunner().execute_with_context(live_request(carla_scenario))
    trace = json.loads(artifacts(caught.value)['carla-stack-trace.json'])
    assert len(world.vehicles[0].commands) == 1
    assert trace['ticks'][1]['application_status'] == 'not_sent'
    assert trace['ticks'][1]['source_refs']['ego'] is not None


@pytest.mark.parametrize('fault', ['startup', 'cleanup'])
def test_startup_and_cleanup_failures_keep_context_diagnostics_and_no_result(monkeypatch, carla_scenario, fault):
    world = install_live_world(monkeypatch, fault=fault)
    request = live_request(carla_scenario)
    with pytest.raises(AdapterExecutionError) as caught:
        CarlaRunner().execute_with_context(request)
    validate_simulation_artifacts(request, caught.value.artifact_payloads, completed=False, adapter_version=ADAPTER_VERSION)
    saved = artifacts(caught.value)
    startup = json.loads(saved['carla-startup.json'])
    manifest = json.loads(saved['carla-stack-manifest.json'])
    trace = json.loads(saved['carla-stack-trace.json'])
    assert startup['context']['context_hash'] == request.context.context_hash
    assert manifest['status'] == 'partial'
    if fault == 'startup':
        assert startup['status'] == 'failed'
        assert all(tick['status'] == 'not_attempted' for tick in trace['ticks'])
        assert not world.vehicles[0].commands
        assert list(make_reader(io.BytesIO(saved['carla-stack-sources.mcap'])).iter_messages()) == []
    else:
        assert len(world.vehicles[0].commands) == 80
        assert manifest['cleanup_errors'] and manifest['observed_frames'] == 81
    assert world.settings.synchronous_mode is False


def test_native_mcap_import_binding_and_existing_replay_reproduce_consumed_inputs(monkeypatch, carla_scenario, tmp_path):
    from fastapi.testclient import TestClient
    from ade2e.adapters import Registry
    from ade2e.database import Settings
    from ade2e.main import create_app
    from ade2e.stack_schemas import StackCreate
    from ade2e.worker import run_once

    install_live_world(monkeypatch)
    request = live_request(carla_scenario)
    saved = artifacts(CarlaRunner().execute_with_context(request))
    native = json.loads(saved['carla-stack-trace.json'])['ticks']
    app = create_app(Settings(data_dir=tmp_path, seed_examples=False), Registry(load_plugins=False))
    with TestClient(app) as client:
        snapshot = request.context.stack_snapshot
        body = {key: getattr(snapshot, key) for key in StackCreate.model_fields}
        response = client.post('/api/v1/stacks', json=StackCreate(**body).model_dump(mode='json'))
        assert response.status_code == 201, response.text
        stack = response.json()
        snapshot = request.context.rig_snapshot
        body = {key: getattr(snapshot, key) for key in RigCreate.model_fields}
        response = client.post('/api/v1/sensor-rigs', json=RigCreate(**body).model_dump(mode='json'))
        assert response.status_code == 201, response.text
        rig = response.json()
        response = client.post('/api/v1/recordings?name=NativeFakeSource&source_kind=synthetic',
            content=saved['carla-stack-sources.mcap'], headers={'Content-Type': 'application/octet-stream'})
        assert response.status_code == 201, response.text
        recording = response.json()
        response = client.get('/api/v1/stack-replay-sources/' + recording['id'])
        assert {channel['role'] for channel in response.json()['channels']} == {'lidar', 'ego'}
        response = client.post(f'/api/v1/recordings/{recording["id"]}/sensor-bindings', json={
            'expected_revision': 0, 'rig_id': rig['id'], 'rig_revision': 1, 'note': 'Pin nominal native recipe; no calibration claim',
            'channels': [{'channel_id': 1, 'sensor_key': 'roof', 'decoder_id': 'foxglove-json', 'decoder_version': '1',
                'max_age_ns': '100000000', 'log_time_offset_ns': '0'}]})
        assert response.status_code == 201, response.text
        response = client.post('/api/v1/stack-replays', headers={'Idempotency-Key': 'native-source-roundtrip'}, json={
            'label': 'Replay native fake-world original messages', 'stack_id': stack['id'], 'stack_revision': 1,
            'recording_id': recording['id'], 'binding_revision': 1, 'lidar_channel_id': 1, 'ego_channel_id': 2,
            'ego_ordinals': [tick['source_refs']['ego']['ordinal'] for tick in native]})
        assert response.status_code == 202, response.text
        replay = run_once(app.state.db, app.state.registry, 'native-roundtrip-worker', job_id=response.json()['id'],
            runtime_registry=app.state.runtime_registry, sensor_registry=app.state.sensor_registry,
            evaluator_registry=app.state.evaluator_registry, stack_registry=app.state.stack_registry)
        assert replay.status == 'completed'
        for expected, actual in zip(native, replay.ticks):
            assert actual.control_application == 'not_applied_open_loop'
            assert actual.packet == expected['packet']
            assert actual.points.sha256 == expected['points']['sha256']
            assert {key: value for key, value in actual.output.items() if key != 'elapsed_ms'} == {
                key: value for key, value in expected['output'].items() if key != 'elapsed_ms'}
