"""Read-only, bounded native body/physics diagnostics for live stack execution.

The caller retains sole tick ownership. This module never ticks, commands a
vehicle, changes physics, imports CARLA or activates debug visualizations.
"""
from __future__ import annotations

import json
import math

from ade2e.schemas import ArtifactPayload
from ade2e.simulation_context import execution_correlation
from ade2e.vehicle_response import (MAX_BYTES, ROLES, SCALAR_FIELDS, WHEEL_FIELDS,
    TELEMETRY_FIELDS, TELEMETRY_SCALARS, VehicleResponse, ResponseObservation,
    float32)


class VehicleResponseError(RuntimeError):
    """Capture/gate failed; the evidence object still owns the collected prefix."""


def _error(error):
    return (type(error).__name__ + ': ' + str(error))[:300]


def _vector(value, keys=('x', 'y', 'z')):
    result = {key: float(getattr(value, key)) for key in keys}
    if not all(math.isfinite(v) for v in result.values()):
        raise VehicleResponseError('Nonfinite raw snapshot vector: ' + repr(result)[:200])
    return result


def _value(source, name, *, boolean=False):
    try:
        value = getattr(source, name)
    except Exception as error:
        return {'status': 'unavailable', 'value': None, 'reason': _error(error)}
    if value is None:
        return {'status': 'null', 'value': None, 'reason': 'API returned null'}
    if isinstance(value, bool):
        if boolean:
            return {'status': 'available', 'value': value, 'reason': None}
        return {'status': 'unavailable', 'value': None, 'reason': 'Unexpected boolean field'}
    if not isinstance(value, (int, float)):
        return {'status': 'unavailable', 'value': None, 'reason': 'Unsupported API value type'}
    if not math.isfinite(value):
        return {'status': 'nonfinite', 'value': None, 'reason': 'API returned ' + repr(value)}
    if boolean:
        return {'status': 'unavailable', 'value': None, 'reason': 'Expected a boolean API field'}
    return {'status': 'available', 'value': float(value), 'reason': None}


def _rpc(world, snapshot_frame, reader):
    record = {'before_world_frame': None, 'after_world_frame': None,
        'status': 'unavailable', 'error': 'Not attempted'}
    data = None
    try:
        record['before_world_frame'] = world.get_snapshot().frame
        data = reader()
        record['after_world_frame'] = world.get_snapshot().frame
        if record['before_world_frame'] != snapshot_frame or record['after_world_frame'] != snapshot_frame:
            raise VehicleResponseError('RPC bracket crossed the supplied WorldSnapshot frame')
        record.update(status='available', error=None)
    except Exception as error:
        record['error'] = _error(error)
        if record['after_world_frame'] is None:
            try:
                record['after_world_frame'] = world.get_snapshot().frame
            except Exception:
                pass
        data = None
    return record, data


def _raw(snapshot, actors):
    if set(actors) != set(ROLES):
        raise VehicleResponseError('Distinct ego and lead actors are required')
    result = {'world_frame': snapshot.frame, 'source_time_s': float(snapshot.timestamp.elapsed_seconds),
        'delta_seconds': float(snapshot.timestamp.delta_seconds), 'actors': {}}
    for role, actor in actors.items():
        item = snapshot.find(actor.id)
        if item is None:
            raise VehicleResponseError('Missing ' + role + ' in coherent WorldSnapshot')
        pose = item.get_transform()
        result['actors'][role] = {'actor_id': actor.id,
            'position_m': _vector(pose.location), 'rotation_deg': _vector(pose.rotation, ('pitch', 'yaw', 'roll')),
            'forward_vector': _vector(pose.get_forward_vector()),
            'velocity_mps': _vector(item.get_velocity()), 'acceleration_mps2': _vector(item.get_acceleration()),
            'angular_velocity_deg_s': _vector(item.get_angular_velocity())}
    return ResponseObservation.model_validate(result).model_dump(mode='json')


def _control(world, frame, actor):
    def read():
        value = actor.get_control()
        from ade2e.vehicle_response import ReportedControl
        return ReportedControl.model_validate({name: getattr(value, name) for name in ReportedControl.model_fields}).model_dump(mode='json')
    bracket, data = _rpc(world, frame, read)
    return {**bracket, 'data': data}


def _telemetry(world, frame, actor):
    def read():
        method = getattr(actor, 'get_telemetry_data', None)
        if not callable(method):
            raise VehicleResponseError('Installed vehicle API does not expose get_telemetry_data')
        value = method()
        wheels = list(value.wheels)
        if len(wheels) != 4:
            raise VehicleResponseError('Telemetry does not contain four wheel records')
        return {'wheels': [{'index': index, 'fields': {name: _value(wheel, name) for name in TELEMETRY_FIELDS}}
            for index, wheel in enumerate(wheels)], 'scalars': {name: _value(value, name) for name in TELEMETRY_SCALARS}}
    bracket, data = _rpc(world, frame, read)
    return {**bracket, 'source_api': 'vehicle.get_telemetry_data',
        'interpretation': 'raw_api_fields_units_unverified', **(data or {'wheels': [], 'scalars': {}})}


def _physics(world, frame, actor, requested):
    def read():
        value = actor.get_physics_control()
        wheels = list(value.wheels)
        if len(wheels) != 4:
            raise VehicleResponseError('Physics readback does not contain four wheels')
        return {'scalars': {name: _value(value, name, boolean=name in ('use_gear_autobox','use_sweep_wheel_collision'))
                for name in SCALAR_FIELDS},
            'wheels': [{'index': index, 'fields': {name: _value(wheel, name) for name in WHEEL_FIELDS}}
                for index, wheel in enumerate(wheels)]}
    bracket, data = _rpc(world, frame, read)
    target = float32(requested)
    state = 'unavailable'
    if data is not None:
        state = 'matched' if all(w['fields']['tire_friction']['status'] == 'available'
            and w['fields']['tire_friction']['value'] == target for w in data['wheels']) else 'mismatch'
    return {**bracket, 'actor_id': actor.id, 'requested_tire_friction': requested,
        'requested_tire_friction_float32': target, 'friction_status': state,
        **(data or {'scalars': {}, 'wheels': []})}


def read_physics_configuration(snapshot, world, actors, requested_friction):
    """Read both vehicles without commanding/ticking; preserve rejected readbacks."""
    if set(actors) != set(ROLES) or len({actor.id for actor in actors.values()}) != 2:
        raise VehicleResponseError('Physics configuration requires distinct ego and lead')
    return {role: _physics(world, snapshot.frame, actor, requested_friction) for role, actor in actors.items()}


def require_physics_configuration(configuration):
    """Call only after the caller has retained the full readback as evidence."""
    if set(configuration) != set(ROLES):
        raise VehicleResponseError('Post-startup physics configuration is incomplete')
    failures = [role + ':' + value['friction_status'] for role,value in configuration.items()
        if value['friction_status'] != 'matched']
    if failures:
        raise VehicleResponseError('Post-startup tire friction configuration rejected: ' + ', '.join(failures))


class VehicleResponseEvidence:
    def __init__(self, request, server_version, adapter_version):
        self.request = request
        self.data = {'schema_version': 'carla-vehicle-response/1', **execution_correlation(request),
            'worker_adapter_version': adapter_version, 'server_version': server_version, 'status': 'partial',
            'coordinate_convention': 'carla_left_handed_world',
            'angular_velocity_basis': 'official_carla_0.9.16_degrees_per_second_installed_build_separate',
            'expected_observations': round(request.scenario.duration_s/.05)+1,
            'after_rest': None, 'physics_configuration': {}, 'initial_velocity_requests': [], 'observations': [],
            'error': None, 'limitations': [
                'Raw body vectors come from one WorldSnapshot; configuration, reported control and telemetry are separately bracketed RPC reads.',
                'Angular velocity units follow official CARLA 0.9.16 source; the installed build is recorded separately and is not proof of identical source.',
                'Target velocity requests are saved before the asynchronous call; return is not a server acknowledgement and does not establish wheel/body convergence.',
                'Reported control is cached simulator state, not measured actuator force or torque.',
                'Telemetry omega, slips, friction, speed, engine_rpm and gear are unverified native API fields; unavailable and nonfinite values are not zero.',
                'Tire friction is a CARLA tire configuration scale, not a calibrated road coefficient or a maximum deceleration constraint.',
                'Existing metrics retain XYZ speed finite differences and first acceleration zero; these raw snapshot accelerations are separate observations.',
                'No debug telemetry visualization, tire force/torque interpretation, measured real-world validation or metric threshold adjustment is performed.']}

    def _auxiliary(self, row, world, actors):
        for role, actor in actors.items():
            row['actors'][role]['reported_control'] = _control(world, row['world_frame'], actor)
            row['actors'][role]['telemetry'] = _telemetry(world, row['world_frame'], actor)

    def capture_after_rest(self, snapshot, world, actors):
        if self.data['after_rest'] is not None or self.data['observations']:
            raise VehicleResponseError('Rest boundary may be captured only once')
        row = _raw(snapshot, actors)
        self.data['after_rest'] = row  # Persist raw values before any separate RPC.
        self.data['physics_configuration'] = read_physics_configuration(snapshot, world, actors,
            self.request.scenario.parameters.friction)
        self._auxiliary(row, world, actors)
        require_physics_configuration(self.data['physics_configuration'])

    def record_initial_velocity_request(self, role, velocity):
        rest = self.data['after_rest']
        if rest is None or set(self.data['physics_configuration']) != set(ROLES) or any(
                value['friction_status'] != 'matched' for value in self.data['physics_configuration'].values()):
            raise VehicleResponseError('Initial velocity requires verified post-startup physics')
        if role not in ROLES or any(value['role'] == role for value in self.data['initial_velocity_requests']) or self.data['observations']:
            raise VehicleResponseError('Initial velocity role/order is invalid')
        speed = self.request.scenario.parameters.ego_speed_mps if role == 'ego' else self.request.scenario.parameters.target_speed_mps
        self.data['initial_velocity_requests'].append({'role': role, 'actor_id': rest['actors'][role]['actor_id'],
            'after_world_frame': rest['world_frame'], 'velocity_mps': {key: float32(value) for key,value in _vector(velocity).items()},
            'requested_speed_mps': speed, 'ack_status': 'unknown', 'operation': 'actor.set_target_velocity'})

    def capture_observation(self, snapshot, world, actors):
        if self.data['after_rest'] is None or len(self.data['initial_velocity_requests']) != 2:
            raise VehicleResponseError('Measurement requires saved initial velocity requests')
        if len(self.data['observations']) >= self.data['expected_observations']:
            raise VehicleResponseError('Vehicle response observation budget exhausted')
        row = _raw(snapshot, actors)
        previous = self.data['observations'][-1] if self.data['observations'] else self.data['after_rest']
        if row['world_frame'] != previous['world_frame']+1 or abs(row['source_time_s']-previous['source_time_s']-.05) > 1e-6:
            raise VehicleResponseError('Vehicle response WorldSnapshot frame/time progression differs')
        self.data['observations'].append(row)  # Keep even when optional RPC reads fail.
        self._auxiliary(row, world, actors)

    def artifact(self, *, completed=False, error=None):
        data = {**self.data, 'status': 'completed' if completed else 'partial', 'error': str(error)[:1000] if error is not None else None}
        model = VehicleResponse.model_validate(data)
        content = json.dumps(model.model_dump(mode='json'), sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
        if len(content) > MAX_BYTES:
            raise VehicleResponseError('Vehicle response artifact exceeds its fixed byte budget')
        return ArtifactPayload(name='carla-vehicle-response.json', kind='vehicle_response', media_type='application/json', content=content)
