"""Bounded native vehicle observations; independent of simulator imports.

The raw body state is a coherent WorldSnapshot. Control, wheel telemetry and
configuration are separate RPC reads bracketed by world-frame observations.
These diagnostics do not redefine the existing trajectory-derived metrics.
"""
from __future__ import annotations

import json
import math
import struct
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .base_schemas import StrictModel
from .schemas import ArtifactPayload
from .simulation_context import SimulationExecutionRequest, validate_execution_correlation

SCHEMA_VERSION = 'carla-vehicle-response/1'
MAX_BYTES = 2 * 1024 * 1024
ROLES = ('ego', 'lead')
SCALAR_FIELDS = ('mass', 'drag_coefficient', 'max_rpm', 'moi', 'clutch_strength',
    'gear_switch_time', 'final_ratio', 'damping_rate_full_throttle',
    'damping_rate_zero_throttle_clutch_engaged', 'damping_rate_zero_throttle_clutch_disengaged',
    'use_gear_autobox', 'use_sweep_wheel_collision')
WHEEL_FIELDS = ('tire_friction', 'damping_rate', 'max_steer_angle', 'radius',
    'max_brake_torque', 'max_handbrake_torque', 'lat_stiff_max_load', 'lat_stiff_value', 'long_stiff_value')
TELEMETRY_FIELDS = ('omega', 'long_slip', 'lat_slip', 'tire_friction')
TELEMETRY_SCALARS = ('speed', 'engine_rpm', 'gear')


class Vector3(StrictModel):
    x: float = Field(allow_inf_nan=False, strict=True)
    y: float = Field(allow_inf_nan=False, strict=True)
    z: float = Field(allow_inf_nan=False, strict=True)


class Rotation(StrictModel):
    pitch: float = Field(allow_inf_nan=False, strict=True)
    yaw: float = Field(allow_inf_nan=False, strict=True)
    roll: float = Field(allow_inf_nan=False, strict=True)


class DiagnosticValue(StrictModel):
    status: Literal['available', 'unavailable', 'null', 'nonfinite']
    value: float | bool | None = None
    reason: str | None = Field(default=None, max_length=300)

    @field_validator('value', mode='before')
    @classmethod
    def raw_value_type(cls, value):
        if value is not None and type(value) not in (int, float, bool):
            raise ValueError('Diagnostic values cannot be coerced from strings or objects')
        return value

    @model_validator(mode='after')
    def value_state(self):
        if self.status == 'available':
            if self.value is None or (not isinstance(self.value, bool) and not math.isfinite(self.value)) or self.reason is not None:
                raise ValueError('Available diagnostic requires a finite value and no error')
        elif self.value is not None or not self.reason:
            raise ValueError('Unavailable diagnostic requires null value and an explicit reason')
        return self


class ReportedControl(StrictModel):
    throttle: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)
    brake: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)
    steer: float = Field(ge=-1, le=1, allow_inf_nan=False, strict=True)
    reverse: bool = Field(strict=True)
    hand_brake: bool = Field(strict=True)
    manual_gear_shift: bool = Field(strict=True)
    gear: int = Field(ge=-100, le=100, strict=True)


class RpcBracket(StrictModel):
    before_world_frame: int | None = Field(default=None, ge=0, lt=2**32, strict=True)
    after_world_frame: int | None = Field(default=None, ge=0, lt=2**32, strict=True)
    status: Literal['available', 'unavailable'] = 'unavailable'
    error: str | None = Field(default='Not attempted', max_length=300)


class ControlReadback(RpcBracket):
    data: ReportedControl | None = None

    @model_validator(mode='after')
    def status_data(self):
        if (self.status == 'available') != (self.data is not None) or (self.status == 'available') != (self.error is None):
            raise ValueError('Control read status differs from payload')
        return self


class WheelTelemetry(StrictModel):
    index: int = Field(ge=0, le=3, strict=True)
    fields: dict[str, DiagnosticValue]


class TelemetryReadback(RpcBracket):
    source_api: Literal['vehicle.get_telemetry_data'] = 'vehicle.get_telemetry_data'
    interpretation: Literal['raw_api_fields_units_unverified'] = 'raw_api_fields_units_unverified'
    wheels: list[WheelTelemetry] = Field(default_factory=list, max_length=4)
    scalars: dict[str, DiagnosticValue] = Field(default_factory=dict)

    @model_validator(mode='after')
    def telemetry_state(self):
        if self.status == 'available':
            if self.error is not None or [w.index for w in self.wheels] != list(range(4)) or set(self.scalars) != set(TELEMETRY_SCALARS):
                raise ValueError('Telemetry requires four ordered wheel records')
            if any(isinstance(v.value, bool) for v in self.scalars.values()):
                raise ValueError('Telemetry scalars must be numbers')
            for wheel in self.wheels:
                if set(wheel.fields) != set(TELEMETRY_FIELDS) or any(isinstance(v.value, bool) for v in wheel.fields.values()):
                    raise ValueError('Telemetry field contract differs')
        elif self.wheels or self.scalars or not self.error:
            raise ValueError('Unavailable telemetry cannot contain wheel observations')
        return self


class ResponseActor(StrictModel):
    actor_id: int = Field(ge=1, lt=2**32, strict=True)
    position_m: Vector3
    rotation_deg: Rotation
    forward_vector: Vector3
    velocity_mps: Vector3
    acceleration_mps2: Vector3
    angular_velocity_deg_s: Vector3
    reported_control: ControlReadback = Field(default_factory=ControlReadback)
    telemetry: TelemetryReadback = Field(default_factory=TelemetryReadback)


class ResponseObservation(StrictModel):
    world_frame: int = Field(ge=0, lt=2**32, strict=True)
    source_time_s: float = Field(ge=0, allow_inf_nan=False, strict=True)
    delta_seconds: float = Field(gt=0, le=1, allow_inf_nan=False, strict=True)
    actors: dict[Literal['ego', 'lead'], ResponseActor]

    @model_validator(mode='after')
    def actor_roles(self):
        if set(self.actors) != set(ROLES) or len({a.actor_id for a in self.actors.values()}) != 2:
            raise ValueError('Distinct ego and lead snapshots are required')
        return self


class WheelPhysics(StrictModel):
    index: int = Field(ge=0, le=3, strict=True)
    fields: dict[str, DiagnosticValue]


class PhysicsReadback(RpcBracket):
    actor_id: int = Field(ge=1, lt=2**32, strict=True)
    requested_tire_friction: float = Field(gt=0, allow_inf_nan=False, strict=True)
    requested_tire_friction_float32: float = Field(gt=0, allow_inf_nan=False, strict=True)
    friction_status: Literal['matched', 'mismatch', 'unavailable'] = 'unavailable'
    scalars: dict[str, DiagnosticValue] = Field(default_factory=dict)
    wheels: list[WheelPhysics] = Field(default_factory=list, max_length=4)


class InitialVelocityRequest(StrictModel):
    role: Literal['ego', 'lead']
    actor_id: int = Field(ge=1, lt=2**32, strict=True)
    after_world_frame: int = Field(ge=0, lt=2**32, strict=True)
    velocity_mps: Vector3
    requested_speed_mps: float = Field(ge=0, allow_inf_nan=False, strict=True)
    ack_status: Literal['unknown'] = 'unknown'
    operation: Literal['actor.set_target_velocity'] = 'actor.set_target_velocity'


class VehicleResponse(StrictModel):
    schema_version: Literal['carla-vehicle-response/1'] = SCHEMA_VERSION
    execution_id: str = Field(min_length=1, max_length=200)
    input_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    context_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    worker_adapter_version: str = Field(min_length=1, max_length=100)
    server_version: str = Field(min_length=1, max_length=200)
    status: Literal['completed', 'partial']
    coordinate_convention: Literal['carla_left_handed_world'] = 'carla_left_handed_world'
    angular_velocity_basis: Literal['official_carla_0.9.16_degrees_per_second_installed_build_separate'] = 'official_carla_0.9.16_degrees_per_second_installed_build_separate'
    expected_observations: int = Field(ge=2, le=81, strict=True)
    after_rest: ResponseObservation | None = None
    physics_configuration: dict[Literal['ego', 'lead'], PhysicsReadback] = Field(default_factory=dict)
    initial_velocity_requests: list[InitialVelocityRequest] = Field(default_factory=list, max_length=2)
    observations: list[ResponseObservation] = Field(default_factory=list, max_length=81)
    error: str | None = Field(default=None, max_length=1000)
    limitations: list[str] = Field(max_length=16)


def _require(condition, message):
    if not condition:
        raise ValueError('Vehicle response evidence: ' + message)


def float32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def _parse(content):
    def pairs(items):
        result = {}
        for k, v in items:
            _require(k not in result, 'duplicate JSON key')
            result[k] = v
        return result
    def invalid(value):
        raise ValueError('Nonfinite JSON: ' + value)
    _require(isinstance(content, bytes) and len(content) <= MAX_BYTES, 'JSON byte budget exceeded')
    return VehicleResponse.model_validate(json.loads(content, object_pairs_hook=pairs, parse_constant=invalid))


def validate_vehicle_response(request: SimulationExecutionRequest, payload: ArtifactPayload,
        physics: dict, startup: dict | None, *, completed: bool, adapter_version: str | None = None) -> VehicleResponse:
    """Verify this additional diagnostic against the already validated live chain.

    The caller enforces presence for its new adapter version. Old 0.4 payloads
    remain valid without this optional artifact. Failure may have no rest row.
    """
    _require(payload.name == 'carla-vehicle-response.json' and payload.kind == 'vehicle_response'
        and payload.media_type == 'application/json', 'artifact identity differs')
    report = _parse(payload.content)
    validate_execution_correlation(report.model_dump(mode='json'), request)
    _require(report.status == ('completed' if completed else 'partial'), 'terminal status differs')
    if adapter_version is not None:
        _require(report.worker_adapter_version == adapter_version, 'adapter version differs')
    expected = round(request.scenario.duration_s / .05) + 1
    _require(report.expected_observations == expected, 'observation denominator differs')
    rows = physics.get('observations', [])
    _require(len(report.observations) <= len(rows), 'response extends beyond physics evidence')
    if completed:
        _require(report.error is None and len(report.observations) == len(rows) == expected, 'completed raw observation coverage is incomplete')
    if report.after_rest is None:
        _require(not completed and not rows and not report.observations and not report.physics_configuration and not report.initial_velocity_requests,
            'missing rest boundary contradicts subsequent evidence')
        return report
    rest = report.after_rest
    _require(startup is not None and startup.get('status') == 'passed' and startup.get('trace'), 'rest boundary lacks successful startup')
    last = startup['trace'][-1]
    _require(rest.world_frame == last.get('world_frame') and rest.source_time_s == last.get('elapsed_seconds')
        and rest.delta_seconds == last.get('delta_seconds'), 'rest boundary differs from startup frame')
    for role, actor in rest.actors.items():
        original = last['actors'][role]
        _require(actor.actor_id == original['actor_id'], 'actor identity differs from startup')
        for field in ('position_m', 'rotation_deg', 'velocity_mps', 'acceleration_mps2', 'angular_velocity_deg_s'):
            _require(getattr(actor, field).model_dump() == original[field], 'rest raw state differs from startup: ' + field)
    target = request.scenario.parameters.friction
    wanted = float32(target)
    for role, config in report.physics_configuration.items():
        _require(config.actor_id == rest.actors[role].actor_id and config.requested_tire_friction == target
            and config.requested_tire_friction_float32 == wanted, 'physics requested identity/value differs')
        if config.status == 'available':
            _require(config.error is None and set(config.scalars) == set(SCALAR_FIELDS)
                and [w.index for w in config.wheels] == list(range(4)), 'physics readback shape differs')
            _require(all(set(w.fields) == set(WHEEL_FIELDS) for w in config.wheels), 'wheel physics fields differ')
            _require(all(not isinstance(v.value, bool) for w in config.wheels for v in w.fields.values()), 'wheel physics requires numeric fields')
            for name, value in config.scalars.items():
                if value.status == 'available':
                    _require(isinstance(value.value, bool) == (name in ('use_gear_autobox', 'use_sweep_wheel_collision')),
                        'physics scalar value type differs')
            matched = all(w.fields['tire_friction'].status == 'available'
                and not isinstance(w.fields['tire_friction'].value, bool)
                and w.fields['tire_friction'].value == wanted for w in config.wheels)
            _require(config.friction_status == ('matched' if matched else 'mismatch'), 'friction gate does not match actual readback')
        else:
            _require(config.friction_status == 'unavailable' and config.error is not None
                and not config.scalars and not config.wheels, 'unavailable physics mislabeled')
        if config.friction_status == 'matched':
            _require(config.before_world_frame == config.after_world_frame == rest.world_frame, 'physics read crossed the rest frame')
    gate_passed = set(report.physics_configuration) == set(ROLES) and all(c.friction_status == 'matched' for c in report.physics_configuration.values())
    if completed or report.initial_velocity_requests or report.observations:
        _require(gate_passed, 'motion evidence follows an unverified friction configuration')
    _require(len({v.role for v in report.initial_velocity_requests}) == len(report.initial_velocity_requests), 'duplicate initial velocity role')
    for value in report.initial_velocity_requests:
        actor = rest.actors[value.role]
        speed = request.scenario.parameters.ego_speed_mps if value.role == 'ego' else request.scenario.parameters.target_speed_mps
        _require(value.actor_id == actor.actor_id and value.after_world_frame == rest.world_frame and value.requested_speed_mps == speed,
            'initial velocity request differs from pinned scenario/rest actor')
        for axis in ('x', 'y', 'z'):
            # CARLA's native Vector3D stores requested components as float32.
            _require(getattr(value.velocity_mps, axis) == float32(getattr(actor.forward_vector, axis)*speed),
                'initial velocity vector differs from rest forward vector')
    if completed or report.observations:
        _require({v.role for v in report.initial_velocity_requests} == set(ROLES), 'initial velocity request coverage incomplete')
    for index, observation in enumerate([rest, *report.observations]):
        for role, actor in observation.actors.items():
            _require(actor.actor_id == rest.actors[role].actor_id, 'raw actor identity changed')
            norm = math.sqrt(sum(getattr(actor.forward_vector, key)**2 for key in ('x','y','z')))
            _require(abs(norm-1) <= 2e-6, 'forward vector is not unit length')
            p, y = math.radians(actor.rotation_deg.pitch), math.radians(actor.rotation_deg.yaw)
            expected_forward = (math.cos(p)*math.cos(y), math.cos(p)*math.sin(y), math.sin(p))
            _require(all(abs(getattr(actor.forward_vector, k)-v) <= 2e-6 for k,v in zip(('x','y','z'),expected_forward)), 'forward vector differs from pose')
            for rpc in (actor.reported_control, actor.telemetry):
                if rpc.status == 'available':
                    _require(rpc.before_world_frame == rpc.after_world_frame == observation.world_frame, 'RPC data claimed at a different world frame')
        if not index:
            continue
        row = rows[index-1]
        _require(observation.world_frame == rest.world_frame+index and observation.world_frame == row['world_frame']
            and observation.source_time_s == row['source_time_s'] and abs(observation.delta_seconds-.05) <= 1e-6
            and abs(observation.source_time_s-rest.source_time_s-index*.05) <= 1e-6,
            'raw observation time/frame differs from physics')
        for role, actor in observation.actors.items():
            stored = row['frame']['ego'] if role == 'ego' else row['frame']['actors'][0]
            speed = math.sqrt(sum(getattr(actor.velocity_mps, axis)**2 for axis in ('x','y','z')))
            _require(stored['speed_mps'] == speed, 'raw XYZ speed differs from published observer')
            _require(stored['x'] == actor.position_m.x and stored['y'] == -actor.position_m.y,
                'raw XY position differs from reflected observer')
            # Observer yaw is extracted from the native rotation matrix; permit
            # only float32 trig representation error, not a different orientation.
            _require(abs((stored['yaw'] + math.radians(actor.rotation_deg.yaw) + math.pi) % (2*math.pi)-math.pi) <= 2e-6,
                'raw yaw differs from reflected observer')
    return report
