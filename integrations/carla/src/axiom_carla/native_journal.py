"""Native durable boundaries. No engine imports or recovery world mutations.

Returned asynchronous calls are recorded as returns, never acknowledgements.
Only the active v3 sink adds I/O; v1/v2 retain their original behavior.
"""
from __future__ import annotations

import hashlib
import math

from . import durability

SETTINGS = ('synchronous_mode', 'no_rendering_mode', 'fixed_delta_seconds', 'substepping',
    'max_substep_delta_time', 'max_substeps', 'max_culling_distance', 'deterministic_ragdolls',
    'tile_stream_distance', 'actor_active_distance', 'spectator_as_ego')
WEATHER = ('cloudiness', 'precipitation', 'precipitation_deposits', 'wind_intensity',
    'sun_azimuth_angle', 'sun_altitude_angle', 'fog_density', 'fog_distance', 'fog_falloff',
    'wetness', 'scattering_intensity', 'mie_scattering_scale', 'rayleigh_scattering_scale', 'dust_storm')


def fields(value, names):
    result, invalid = {}, {}
    for name in names:
        try:
            item = getattr(value, name)
            if item is None or type(item) in (str, bool, int):
                result[name] = item
            elif type(item) is float and math.isfinite(item):
                result[name] = item
            else:
                result[name] = None
                invalid[name] = repr(item)[:120]
        except Exception as error:
            result[name] = None
            invalid[name] = type(error).__name__ + ': ' + str(error)[:120]
    return {'values': result, 'invalid_fields': invalid}


def vector(value):
    return fields(value, ('x', 'y', 'z'))


def pose(value):
    return {'position_m': vector(value.location),
        'rotation_deg': fields(value.rotation, ('pitch', 'yaw', 'roll'))}


def begin(world, settings, weather, *, mode, scenario, version):
    if not durability.enabled():
        return
    episode = world.id
    if type(episode) is not int or episode < 0:
        raise ValueError('Durable execution requires a nonnegative integer CARLA World.id')
    original = {'world_id': str(episode), 'mode': mode, 'server_version': version,
        'scenario': scenario.model_dump(mode='json'), 'expected_intervals': round(scenario.duration_s/.05),
        'expected_observations': round(scenario.duration_s/.05)+1,
        'original_settings': fields(settings, SETTINGS), 'original_weather': fields(weather, WEATHER),
        'original_actors': [{'actor_id': actor.id, 'type_id': actor.type_id,
            'role_name': dict(actor.attributes).get('role_name')} for actor in world.get_actors()]}
    durability.event('world_original', original)


def mutation(operation, call, data=None, *, acknowledgement=False):
    """Persist intent before calling; failures keep the outcome unknown."""
    if not durability.enabled():
        return call()
    value = {'operation': operation, **(data or {})}
    durability.event('mutation_intent', value)
    result = call()
    durability.event('mutation_returned', {**value,
        'acknowledgement': acknowledgement and result is True,
        'return_value': result if type(result) in (bool, int, float, str) or result is None else None,
        'interpretation': 'observed API return; asynchronous return is not a server acknowledgement'})
    return result


def spawn(world, blueprint, transform, role, *, parent=None, optional=False, owned=None):
    if not durability.enabled():
        actor = (world.try_spawn_actor(blueprint, transform) if optional else
            world.spawn_actor(blueprint, transform, **({'attach_to': parent} if parent else {})))
        if actor is not None and owned is not None:
            owned.append(actor)
        return actor
    unique_role = durability.role_name(role)
    blueprint.set_attribute('role_name', unique_role)
    data = {'role': role, 'role_name': unique_role, 'blueprint_id': getattr(blueprint, 'id', getattr(blueprint, 'name', None)),
        'parent_actor_id': parent.id if parent else None, 'transform': pose(transform), 'world_id': str(world.id)}
    durability.event('spawn_intent', data)
    actor = (world.try_spawn_actor(blueprint, transform) if optional else
        world.spawn_actor(blueprint, transform, **({'attach_to': parent} if parent else {})))
    # Keep cleanup ownership even if committing the successful spawn response
    # fails. A process death before this event still leaves its nonce intent.
    if actor is not None and owned is not None:
        owned.append(actor)
    durability.event('spawn_returned', {**data, 'actor_id': actor.id if actor is not None else None,
        'spawned': actor is not None})
    return actor


def snapshot(world_snapshot, actors, phase):
    if not durability.enabled():
        return
    row = {'phase': phase, 'world_frame': world_snapshot.frame,
        'clock': fields(world_snapshot.timestamp, ('elapsed_seconds', 'delta_seconds')), 'actors': {}}
    for role, actor in actors.items():
        observed = world_snapshot.find(actor.id)
        if observed is None:
            row['actors'][role] = {'actor_id': actor.id, 'status': 'missing'}
            continue
        item = {'actor_id': actor.id, 'status': 'observed'}
        for key, getter in (('transform', 'get_transform'), ('velocity_mps', 'get_velocity'),
                ('acceleration_mps2', 'get_acceleration'), ('angular_velocity_deg_s', 'get_angular_velocity')):
            try:
                value = getattr(observed, getter)()
                item[key] = pose(value) if key == 'transform' else vector(value)
            except Exception as error:
                item[key] = {'status': 'unavailable', 'error': str(error)[:300]}
        row['actors'][role] = item
    durability.event('physics_snapshot', row)


def tick(world, *, phase, **kwargs):
    return mutation('world.tick', lambda: world.tick(**kwargs), {'phase': phase})


class StartupWorld:
    """Journal tick intents without changing the existing startup policy."""
    def __init__(self, world):
        self.world = world

    def __getattr__(self, name):
        return getattr(self.world, name)

    def tick(self, **kwargs):
        return tick(self.world, phase='startup', **kwargs)


def startup_sample(sample):
    durability.event('startup_sample', {'policy_version': 'carla-startup-stability/1', 'sample': sample})


def raw_source(kind, content, data):
    if not durability.enabled():
        return
    if len(content) > 2*1024*1024:
        raise ValueError('Native raw source exceeds the 2 MiB durable blob limit')
    durability.blob(kind, content, {**data, 'size_bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()})


def cleanup(operation, call, data=None, *, acknowledgement=False):
    """A failing journal must not prevent best-effort owned cleanup.

    Any journal failure is still raised after the actual cleanup attempt, so the
    execution cannot report clean terminal success without durable evidence.
    """
    if not durability.enabled():
        return call()
    failure = None
    value = {'operation': operation, **(data or {})}
    try:
        durability.event('cleanup_intent', value)
    except Exception as error:
        failure = error
    try:
        result = call()
    except Exception as error:
        try:
            durability.event('cleanup_error', {**value, 'error': type(error).__name__ + ': ' + str(error)[:500]})
        except Exception:
            pass
        raise
    try:
        durability.event('cleanup_returned', {**value, 'return_value': result if type(result) in (bool, int, str) or result is None else None,
            'acknowledgement': acknowledgement and result is True,
            'world_verified': False})
    except Exception as error:
        failure = failure or error
    if failure is not None:
        raise failure
    return result


class LegacyEvidence:
    """Durable-only diagnostic prefix; no invented trajectory metrics or ACKs."""
    def __init__(self, scenario, capture):
        self.scenario, self.capture = scenario, capture
        self.commands, self.observations = [], []
        self.collisions = CollisionJournal()
        self.manifest, self.captured = None, []

    def control(self, sequence, role, actor_id, target, brake, operation):
        if not durability.enabled():
            return operation()
        row = {'sequence': sequence, 'role': role, 'actor_id': actor_id,
            'target_speed_mps': target, 'force_brake': brake, 'status': 'send_outcome_unknown',
            'acknowledged': False, 'transport': 'actor.apply_control_async'}
        self.commands.append(row)
        result = mutation('legacy_control', operation, row.copy())
        row['status'] = 'api_returned_unacknowledged'
        return result

    def artifacts(self, payloads, frames, events, phase):
        from ade2e.schemas import ArtifactPayload
        from .recording import _json, capture_recording
        result = [payload for payload in payloads if payload.name not in {'carla-legacy-prefix.json', 'carla-sensors.mcap', 'carla-capture-manifest.json'}]
        data = {'schema_version': 'carla-legacy-prefix/1', 'status': 'partial', 'phase': phase,
            'scenario_id': self.scenario.id, 'scenario_revision': self.scenario.revision, 'seed': self.scenario.seed,
            'expected_intervals': round(self.scenario.duration_s/.05),
            'expected_observations': round(self.scenario.duration_s/.05)+1,
            'capture_enabled': self.capture, 'capture_plan_indices': [0, round(self.scenario.duration_s/.05)//2, round(self.scenario.duration_s/.05)] if self.capture else [],
            'commands': self.commands, 'observations': self.observations,
            'trajectory': [frame.model_dump(mode='json') for frame in frames],
            'events': [event.model_dump(mode='json') for event in events],
            'limitations': ['Ground-truth controller; no perception evaluation.',
                'Controls are asynchronous API returns, not server acknowledgements.',
                'Legacy trajectory uses actor getters; raw coherent snapshots are separate journal events.',
                'Partial evidence has no completed run metrics or cleanup verification.']}
        result.append(ArtifactPayload(name='carla-legacy-prefix.json', kind='execution_diagnostic', media_type='application/json', content=_json(data)))
        if self.manifest is not None:
            manifest = {**self.manifest, 'status': 'partial', 'recovery_phase': phase}
            if self.captured:
                result.append(ArtifactPayload(name='carla-sensors.mcap', kind='sensor_recording', media_type='application/mcap',
                    content=capture_recording(self.captured, metadata=manifest)))
            result.append(ArtifactPayload(name='carla-capture-manifest.json', kind='sensor_manifest', media_type='application/json', content=_json(manifest)))
        return result

    def checkpoint(self, payloads, frames, events, phase):
        if durability.enabled():
            self.collisions.flush()
            durability.checkpoint(self.artifacts(payloads, frames, events, phase), phase)


class CollisionJournal:
    """Callbacks enqueue only; the owner thread commits bounded observed events."""
    def __init__(self):
        import threading
        self.enabled = durability.enabled()
        self.lock, self.pending, self.overflow = threading.Lock(), [], False

    def receive(self, event):
        if not self.enabled:
            return
        with self.lock:
            if len(self.pending) >= 1000:
                self.overflow = True
                return
            self.pending.append({'world_frame': getattr(event, 'frame', None),
                'timestamp_s': event.timestamp, 'other_actor_id': event.other_actor.id,
                'stream_complete': False})

    def flush(self):
        if not self.enabled:
            return
        with self.lock:
            if self.overflow:
                raise ValueError('Collision journal callback queue overflow')
            pending = list(self.pending)
        for item in pending:
            durability.event('collision_observed', item)
        with self.lock:
            del self.pending[:len(pending)]


def interrupted_startup(report):
    import copy
    frozen = copy.deepcopy(report)
    frozen.update(status='failed', reason_code='interrupted_checkpoint',
        reason='Interrupted projection at durable startup checkpoint; no completed rest-gate claim')
    return frozen
