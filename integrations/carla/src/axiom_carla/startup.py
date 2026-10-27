"""Bounded rest-state initialization gate over coherent CARLA world snapshots.

This is an experimental initialization policy, not tire calibration or a road
safety predicate. It never commands motion or discards measured-run samples.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import time


# The 0.9.16 ActorSnapshot API prose says rad/s, but the actual active-actor
# serialization calls GetPhysicsAngularVelocityInDegrees; neither EpisodeState
# nor the Python snapshot binding converts that value. Pin the source basis in
# each report rather than silently relying on the contradictory prose.
ANGULAR_VELOCITY_BASIS = {
    'unit': 'degrees_per_second', 'verified_source_version': '0.9.16',
    'server': 'https://github.com/carla-simulator/carla/blob/0.9.16/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Sensor/WorldObserver.cpp#L247-L254',
    'client': 'https://github.com/carla-simulator/carla/blob/0.9.16/LibCarla/source/carla/client/detail/EpisodeState.cpp#L24-L33',
    'python': 'https://github.com/carla-simulator/carla/blob/0.9.16/PythonAPI/carla/source/libcarla/Snapshot.cpp#L30-L35',
    'documentation_discrepancy': 'ActorSnapshot prose says rad/s; active-actor server serialization and both getters preserve degrees/s.',
}

@dataclass(frozen=True)
class StartupPolicy:
    version: str = 'carla-startup-stability/1'
    min_ticks: int = 8
    max_ticks: int = 100
    consecutive_stable_samples: int = 5
    max_linear_speed_mps: float = 0.1
    max_vertical_speed_mps: float = 0.05
    max_angular_speed_deg_s: float = 1.0
    max_position_step_m: float = 0.005
    max_rotation_step_deg: float = 0.05
    max_acceleration_mps2: float = 0.5
    max_wall_time_s: float = 60.0
    max_tick_rpc_time_s: float = 10.0
    clock_tolerance_s: float = 0.000001

    def __post_init__(self):
        if not (type(self.min_ticks) is int and type(self.max_ticks) is int
                and type(self.consecutive_stable_samples) is int
                and 8 <= self.min_ticks <= self.max_ticks <= 100
                and 2 <= self.consecutive_stable_samples <= self.max_ticks):
            raise ValueError('Invalid bounded startup tick policy')
        for key, value in asdict(self).items():
            if key not in {'version', 'min_ticks', 'max_ticks', 'consecutive_stable_samples'}:
                if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                    raise ValueError('Startup policy bounds must be finite and positive')
        if self.max_wall_time_s > 60 or self.max_tick_rpc_time_s > 10:
            raise ValueError('Startup wall/RPC timeout exceeds the supported bound')


class StartupFailure(RuntimeError):
    def __init__(self, message: str, report: dict):
        super().__init__(message)
        self.report = report


def _number(value, field, invalid):
    if type(value) not in (float, int) or not math.isfinite(value):
        invalid.append({'field': field, 'observed': repr(value)[:120]})
        return None
    return float(value)


def _vector(value, field, invalid, keys=('x', 'y', 'z')):
    return {key: _number(getattr(value, key), field + '.' + key, invalid) for key in keys}


def _norm(value):
    return math.hypot(*value.values())


def _actor_state(snapshot, actor_id, role, previous, policy):
    item = snapshot.find(actor_id)
    if item is None:
        return {'actor_id': actor_id, 'invalid_fields': [{'field': role, 'observed': 'actor absent from world snapshot'}], 'stable': False}
    invalid = []
    pose = item.get_transform()
    value = {'actor_id': actor_id, 'position_m': _vector(pose.location, role+'.position_m', invalid),
        'rotation_deg': _vector(pose.rotation, role+'.rotation_deg', invalid, ('pitch', 'yaw', 'roll')),
        'velocity_mps': _vector(item.get_velocity(), role+'.velocity_mps', invalid),
        'angular_velocity_deg_s': _vector(item.get_angular_velocity(), role+'.angular_velocity_deg_s', invalid),
        'acceleration_mps2': _vector(item.get_acceleration(), role+'.acceleration_mps2', invalid),
        'invalid_fields': invalid, 'stable': False}
    if invalid:
        return value
    displacement, rotation_step = None, None
    if previous is not None:
        displacement = math.dist(tuple(value['position_m'].values()), tuple(previous['position_m'].values()))
        rotation_step = max(abs((value['rotation_deg'][key]-previous['rotation_deg'][key]+180)%360-180) for key in ('pitch','yaw','roll'))
    value['derived'] = {'linear_speed_mps': _norm(value['velocity_mps']),
        'absolute_vertical_speed_mps': abs(value['velocity_mps']['z']), 'angular_speed_deg_s': _norm(value['angular_velocity_deg_s']),
        'acceleration_norm_mps2': _norm(value['acceleration_mps2']), 'position_step_m': displacement, 'rotation_step_deg': rotation_step}
    for key, number in value['derived'].items():
        if number is not None and not math.isfinite(number):
            value['derived'][key] = None
            invalid.append({'field': role+'.derived.'+key, 'observed': repr(number)})
    if invalid:
        return value
    value['criteria'] = {
        'linear_speed': value['derived']['linear_speed_mps'] <= policy.max_linear_speed_mps,
        'vertical_speed': value['derived']['absolute_vertical_speed_mps'] <= policy.max_vertical_speed_mps,
        'angular_speed': value['derived']['angular_speed_deg_s'] <= policy.max_angular_speed_deg_s,
        'acceleration': value['derived']['acceleration_norm_mps2'] <= policy.max_acceleration_mps2,
        'position_step': displacement is not None and displacement <= policy.max_position_step_m,
        'rotation_step': rotation_step is not None and rotation_step <= policy.max_rotation_step_deg}
    value['stable'] = all(value['criteria'].values())
    return value


def wait_for_stability(world, actors: dict, *, dt=0.05, policy: StartupPolicy | None = None, clock=time.monotonic, on_sample=None, on_progress=None):
    policy = policy or StartupPolicy()
    if set(actors) != {'ego', 'lead'} or len({actor.id for actor in actors.values()}) != 2:
        raise ValueError('Startup requires distinct ego and lead actor identities')
    if type(dt) not in (int, float) or not math.isfinite(dt) or dt <= 0 or dt*policy.max_ticks > 5.000001:
        raise ValueError('Startup simulated duration must be bounded to five seconds')
    started = clock()
    report = {'schema_version': '1', 'policy': asdict(policy), 'fixed_delta_seconds': dt,
        'angular_velocity_basis': dict(ANGULAR_VELOCITY_BASIS),
        'max_simulated_duration_s': dt*policy.max_ticks, 'status': 'running', 'reason_code': None, 'reason': None,
        'actor_ids': {role: actor.id for role, actor in actors.items()}, 'initial_world_frame': None,
        'initial_elapsed_seconds': None, 'trace': [], 'stable_streak': 0, 'completed_ticks': 0,
        'elapsed_simulated_seconds': 0.0, 'elapsed_wall_seconds': 0.0,
        'limitations': ['Experimental rest-initialization criterion; not a calibrated dynamics or road-safety guarantee.',
            'Success precedes imposed target velocity; it does not establish post-imposition speed/wheel convergence.',
            'Measurements use one coherent CARLA world snapshot per tick; units remain native CARLA coordinates.',
            'Angular units are based on the linked official 0.9.16 source. The recorded installed server build is separate evidence and is not proof of identical source or custom-physics behavior.',
            'Wall deadline is checked before/after snapshot calls and tick RPCs. A blocking RPC cannot be forcibly cancelled here; tick timeout is capped by remaining time and other RPCs retain the configured client timeout.',
            'All startup samples are initialization evidence outside the measured run interval; no measured interval samples are trimmed.']}
    emitted_samples = 0
    def emit_sample():
        nonlocal emitted_samples
        if (on_sample is not None or on_progress is not None) and len(report['trace']) > emitted_samples:
            # Commit the actual sample before another tick. A sink failure must
            # stop actuation; never retry it from the exception handler.
            emitted_samples = len(report['trace'])
            if on_sample is not None:
                on_sample(report['trace'][-1])
            if on_progress is not None:
                on_progress(report)
    def fail(code, message):
        emit_sample()
        report.update(status='failed', reason_code=code, reason=message, elapsed_wall_seconds=max(0.,clock()-started))
        raise StartupFailure(message, report)
    def check_deadline():
        remaining = policy.max_wall_time_s - (clock()-started)
        if remaining <= 0:
            fail('wall_timeout', 'Startup stability gate exceeded its wall-clock deadline')
        return remaining
    try:
        check_deadline()
        initial = world.get_snapshot()
        check_deadline()
        frame = initial.frame
        elapsed = initial.timestamp.elapsed_seconds
        if type(frame) is not int or frame < 0 or type(elapsed) not in (int,float) or not math.isfinite(elapsed) or elapsed < 0:
            fail('invalid_clock', 'Initial CARLA world frame/time is invalid')
        report['initial_world_frame'], report['initial_elapsed_seconds'] = frame, elapsed
        previous = None
        for index in range(1, policy.max_ticks+1):
            remaining = check_deadline()
            returned_frame = world.tick(seconds=min(policy.max_tick_rpc_time_s, remaining))
            check_deadline()
            snapshot = world.get_snapshot()
            check_deadline()
            invalid = []
            sample_time = _number(snapshot.timestamp.elapsed_seconds, 'elapsed_seconds', invalid)
            delta = _number(snapshot.timestamp.delta_seconds, 'delta_seconds', invalid)
            observed_frame = snapshot.frame
            sample = {'tick': index, 'world_frame': observed_frame if type(observed_frame) is int else None,
                'tick_returned_frame': returned_frame if type(returned_frame) is int else None,
                'elapsed_seconds': sample_time, 'delta_seconds': delta, 'invalid_fields': invalid,
                'actors': {}, 'stable': False, 'stable_streak': 0}
            report['trace'].append(sample)
            report['completed_ticks'] = index
            if (invalid or type(observed_frame) is not int or type(returned_frame) is not int
                    or observed_frame != returned_frame or observed_frame != frame+1
                    or abs(delta-dt) > policy.clock_tolerance_s or abs(sample_time-elapsed-dt) > policy.clock_tolerance_s):
                fail('invalid_clock', 'CARLA frame/time did not advance exactly one configured synchronous tick')
            report['elapsed_simulated_seconds'] = sample_time - report['initial_elapsed_seconds']
            for role, actor in actors.items():
                sample['actors'][role] = _actor_state(snapshot, actor.id, role, previous[role] if previous else None, policy)
            if any(value['invalid_fields'] for value in sample['actors'].values()):
                fail('invalid_actor_sample', 'Startup actor snapshot is missing or contains nonfinite values')
            sample['stable'] = all(value['stable'] for value in sample['actors'].values())
            report['stable_streak'] = report['stable_streak']+1 if sample['stable'] else 0
            sample['stable_streak'] = report['stable_streak']
            previous, frame, elapsed = sample['actors'], observed_frame, sample_time
            emit_sample()
            check_deadline()
            if index >= policy.min_ticks and report['stable_streak'] >= policy.consecutive_stable_samples:
                report.update(status='passed', reason_code='stable_rest_observed',
                    reason='Both vehicles met every declared rest criterion for the required consecutive snapshots',
                    elapsed_wall_seconds=max(0.,clock()-started))
                return report
        fail('stability_timeout', 'Both vehicles did not meet the declared rest criteria within the bounded preparation interval')
    except StartupFailure:
        raise
    except Exception as error:
        fail('snapshot_error', 'Startup snapshot/tick failed: '+type(error).__name__+': '+str(error)[:500])
