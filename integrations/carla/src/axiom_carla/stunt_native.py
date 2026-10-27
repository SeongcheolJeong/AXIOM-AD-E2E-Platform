"""Bounded standalone CARLA stunt runner. Never loads a world or teleports a run.

Read-only probe: --port P --owner owner.json --spool S --probe
Commission: same arguments --commission --output NEW_DIR
Run: same arguments --output NEW_DIR < frozen-stunt-input.json

Only this file and stunt_math.py must be staged beside the CARLA Python runtime.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import signal
import struct
import subprocess
import sys
import time

try:
    from . import stunt_math as mechanics
except ImportError:
    import stunt_math as mechanics

VERSION = 'carla-stunt/1.0.0'
CANCELLED = False
# CARLA 0.9.16 Server.cpp forwards torque raw to AddTorqueInDegrees, while
# add_force converts N to engine linear units. Dimensional guessing is unsafe:
# bundled build 1ae5356 produced 100x excess angular response with a 10000 factor.
# The 100 factor below was established by a known SI offset-impulse couple; every
# commissioning repeats that independent equivalence check on the current build.
TORQUE_API_PER_NM = 100. * 180. / math.pi
SETTINGS = ('synchronous_mode', 'fixed_delta_seconds', 'substepping', 'max_substep_delta_time', 'max_substeps')
SOURCES = ['https://github.com/carla-simulator/carla/blob/1ae5356/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Server/CarlaServer.cpp',
           'https://github.com/carla-simulator/carla/blob/1ae5356/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Actor/CarlaActor.cpp',
           'https://github.com/carla-simulator/carla/blob/1ae5356/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Server/ServerSynchronization.h']


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def hashes():
    return {'stunt_native_sha256': file_sha(__file__), 'stunt_math_sha256': file_sha(mechanics.__file__)}


def write_json(path, value):
    temporary = Path(str(path)+'.tmp')
    temporary.write_bytes(canonical(value))
    os.replace(temporary, path)


def vector(v):
    values = [float(v.x), float(v.y), float(v.z)]
    if not all(math.isfinite(x) for x in values):
        raise ValueError('Native observation contains nonfinite values')
    return values


def dynamic(world):
    return [a for a in world.get_actors() if a.type_id.startswith(('vehicle.', 'walker.', 'sensor.'))]


def setting_values(settings):
    return {name: getattr(settings, name) for name in SETTINGS}


def connect(args):
    owner = json.loads(Path(args.owner).read_bytes())
    if (owner.get('port') != args.port or args.port == 2000
            or owner.get('purpose') != 'AXIOM isolated stunt acceptance'):
        raise ValueError('An operator-owned isolated stunt endpoint is required')
    os.kill(owner['pid'], 0)
    import carla
    client = carla.Client('127.0.0.1', args.port)
    client.set_timeout(20.)
    if client.get_client_version() != client.get_server_version():
        raise ValueError('CARLA client/server versions differ')
    if not (client.get_server_version().startswith('0.9.16') or client.get_server_version() == '1ae5356'):
        raise ValueError('Only CARLA 0.9.16 or the commissioning-gated bundled 1ae5356 build is supported')
    return carla, client, owner


@contextmanager
def lease(spool, identity):
    root = Path(spool)
    root.mkdir(parents=True, exist_ok=True)
    with (root/'engine.lock').open('a+b') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Dedicated stunt world is already owned') from error
        quarantine = root/'quarantine.json'
        if quarantine.exists():
            raise RuntimeError('Prior native cleanup is unconfirmed; engine quarantined')
        marker = {'identity': identity, 'pid': os.getpid(), **hashes()}
        write_json(quarantine, marker)
        state = {'clean': True}
        try:
            yield state
        finally:
            if state['clean'] and json.loads(quarantine.read_bytes()) == marker:
                quarantine.unlink()


def commissioning_identity(client, owner):
    world = client.get_world()
    return {**hashes(), 'server_version': client.get_server_version(),
            'client_version': client.get_client_version(), 'server_pid': owner['pid'],
            'map': world.get_map().name, 'opendrive_sha256': sha(world.get_map().to_opendrive().encode())}


def probe(carla, client, owner, args):
    world = client.get_world()
    identity = commissioning_identity(client, owner)
    required = ('add_force', 'add_torque', 'add_impulse_at_location', 'get_angular_velocity', 'set_enable_gravity')
    capabilities = {name: callable(getattr(carla.Actor, name, None)) for name in required}
    foreign = [{'id': a.id, 'type_id': a.type_id} for a in dynamic(world)]
    record_path = Path(args.spool)/'commissioning.json'
    record = json.loads(record_path.read_bytes()) if record_path.exists() else None
    verified = bool(record and record.get('passed') is True and record.get('identity') == identity)
    available = all(capabilities.values()) and not foreign and not world.get_settings().synchronous_mode and not (Path(args.spool)/'quarantine.json').exists()
    return {'schema_version': 'carla-stunt-probe/1', **identity, 'adapter_version': VERSION,
            'available': available, 'physics_verified': verified,
            'reason': 'ready' if available and verified else 'commissioning_required' if available else 'native_world_unavailable',
            'foreign_dynamic_actors': foreign, 'capabilities': capabilities,
            'settings': setting_values(world.get_settings()),
            'commissioning_sha256': file_sha(record_path) if record else None,
            'ramp_available': False, 'physical_validation': 'not_established'}


def validate_config(config):
    bounds = {'mass_kg': (500, 4000), 'initial_speed_mps': (0, 15), 'max_thrust_n': (1e-9, 150000),
              'impulse_budget_ns': (0, 1000000), 'thrust_scale': (0, 1), 'target_height_m': (1, 15),
              'duration_s': (3, 20), 'suction_force_n': (0, 100000)}
    if config.get('schema_version') != 'stunt-spec/1' or config.get('preset') != 'kickflip':
        raise ValueError('Only stunt-spec/1 kickflip is executable; Ramp requires a verified collision asset')
    for key, (low, high) in bounds.items():
        value = config[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError('Out-of-bounds stunt input: '+key)
    if config['dt_s'] not in (.01, .005) or config['camera_fps'] != 20:
        raise ValueError('Unsupported physics/camera clock')
    if abs(config['duration_s']*config['camera_fps']-round(config['duration_s']*config['camera_fps'])) > 1e-8:
        raise ValueError('Duration must align to the 20fps camera period (0.05s)')
    return config


def tick_snapshot(world, timeout_s=10.):
    """One tick command, then read-only cache synchronization; never re-tick."""
    expected = world.tick()
    deadline = time.monotonic()+timeout_s
    while True:
        snapshot = world.get_snapshot()
        if snapshot.frame == expected:
            return snapshot
        if snapshot.frame > expected:
            raise RuntimeError(f'Foreign tick: expected frame {expected}, observed {snapshot.frame}')
        if time.monotonic() >= deadline:
            raise RuntimeError(f'Snapshot cache timeout: expected frame {expected}, observed {snapshot.frame}')
        time.sleep(.002)


def engine_dt(dt):
    # 1ae5356 uses a strict double target-time comparison but advances float32
    # DeltaSeconds. Exact float32 promotion avoids accidentally granting two ticks
    # for decimal 0.01 or 0.005. Nominal and actual intervals stay recorded.
    return struct.unpack('<f', struct.pack('<f', dt))[0]


def prepare_world(world, dt):
    dt = engine_dt(dt)
    settings = world.get_settings()
    settings.synchronous_mode = True
    settings.fixed_delta_seconds = dt
    settings.substepping = True
    settings.max_substep_delta_time = dt
    settings.max_substeps = 1
    world.apply_settings(settings)
    actual = setting_values(world.get_settings())
    if actual != setting_values(settings):
        raise RuntimeError('Native physics settings readback differs')
    return actual


def spawn_transform(carla, world, extra_height=0., roll=0.):
    # Sorted deterministic spawn choice; selected coordinates retained in evidence.
    candidates = [p for p in world.get_map().get_spawn_points()
                  if abs(p.rotation.pitch) < 1 and abs(p.rotation.roll) < 1]
    if not candidates:
        raise ValueError('Current map has no flat authored spawn point')
    candidates.sort(key=lambda p: (p.location.x, p.location.y, p.location.z))
    # First point is the least-x edge lane, giving a reproducible linear trial.
    point = candidates[0]
    return carla.Transform(carla.Location(x=point.location.x, y=point.location.y, z=point.location.z+extra_height),
                           carla.Rotation(pitch=point.rotation.pitch, yaw=point.rotation.yaw, roll=roll))


def spawn_vehicle(carla, world, config, owned, extra_height=0., roll=0.):
    bp = world.get_blueprint_library().find(config['vehicle_blueprint'])
    bp.set_attribute('role_name', 'axiom-stunt-owned')
    vehicle = world.try_spawn_actor(bp, spawn_transform(carla, world, extra_height, roll))
    if vehicle is None:
        raise RuntimeError('Deterministic trial spawn is obstructed')
    owned.append(vehicle)
    vehicle.set_simulate_physics(True)
    vehicle.set_enable_gravity(True)
    physics = vehicle.get_physics_control()
    physics.mass = config['mass_kg']
    vehicle.apply_physics_control(physics)
    tick_snapshot(world)  # Physics-control RPC is applied at the next engine frame.
    if abs(vehicle.get_physics_control().mass-config['mass_kg']) > .01:
        raise RuntimeError('Vehicle mass readback differs')
    return vehicle


def apply_wrench(carla, vehicle, force, torque):
    vehicle.add_force(carla.Vector3D(*force))
    vehicle.add_torque(carla.Vector3D(*(v*TORQUE_API_PER_NM for v in torque)))


def clean_world(world, original, owned, state):
    errors, warnings = [], []
    for actor in reversed(owned):
        try:
            if actor.type_id.startswith('sensor.'):
                actor.stop()
            # is_alive is snapshot-cached and can be false for a newly spawned
            # actor before the first tick. Always issue destroy for owned IDs.
            actor.destroy()
        except Exception as error:
            warnings.append(str(error))
    try:
        # DestroyActor and physics-control RPCs require an engine-frame barrier.
        if world.get_settings().synchronous_mode:
            tick_snapshot(world)
        else:
            world.wait_for_tick(10.)
        survivors = {a.id for a in world.get_actors()} & {a.id for a in owned}
        if survivors:
            errors.append('Owned actors remain: '+str(sorted(survivors)))
        world.apply_settings(original)
        if setting_values(world.get_settings()) != setting_values(original):
            errors.append('World settings restoration differs')
    except Exception as error:
        errors.append(str(error))
    state['clean'] = not errors
    return {'cleanup_status': 'confirmed' if not errors else 'unconfirmed',
            'cleanup_errors': errors, 'cleanup_warnings': warnings}


def commission(carla, client, owner, args, lease_state):
    world = client.get_world()
    if dynamic(world) or world.get_settings().synchronous_mode:
        lease_state['clean'] = True
        raise RuntimeError('Commissioning refuses foreign actors or an already synchronous world')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    original = world.get_settings()
    owned = []
    record = {'schema_version': 'carla-stunt-commissioning/1', 'identity': commissioning_identity(client, owner),
              'passed': False, 'source_review': SOURCES, 'torque_api_per_nm': TORQUE_API_PER_NM,
              'physical_validation': 'not_established', 'probes': [], 'error': None}
    config = {'vehicle_blueprint': 'vehicle.audi.tt', 'mass_kg': 1500.}
    lease_state['clean'] = False
    try:
        record['settings'] = prepare_world(world, .01)
        record['clock_checks'] = []
        for nominal_dt in (.01, .005):
            prepare_world(world, nominal_dt)
            tick_snapshot(world)
            time.sleep(.03)
            for _ in range(5):
                before = world.get_snapshot()
                after = tick_snapshot(world)
                time.sleep(.03)
                dwell = world.get_snapshot()
                record['clock_checks'].append({'nominal_dt_s': nominal_dt, 'engine_dt_s': engine_dt(nominal_dt),
                    'before_frame': before.frame, 'after_frame': after.frame, 'dwell_frame': dwell.frame,
                    'actual_delta_s': after.timestamp.elapsed_seconds-before.timestamp.elapsed_seconds})
        if any(r['after_frame'] != r['before_frame']+1 or r['dwell_frame'] != r['after_frame']
               or abs(r['actual_delta_s']-r['engine_dt_s']) > 1e-5 for r in record['clock_checks']):
            raise RuntimeError('Single-tick ownership commissioning failed')
        prepare_world(world, .01)
        for kind in ('freefall', 'force', 'offset_impulse_pair', 'torque'):
            vehicle = spawn_vehicle(carla, world, config, owned, extra_height=8.)
            tick_snapshot(world)
            before = world.get_snapshot().find(vehicle.id)
            v0, w0 = vector(before.get_velocity()), vector(before.get_angular_velocity())
            transform = before.get_transform()
            axis = vector(transform.get_forward_vector())
            force_n, torque_nm = 15000., 1000.
            count = 10
            if kind == 'offset_impulse_pair':
                matrix = transform.get_matrix()
                # 1000 Nm for .1 s -> opposing +/-66.6667 Ns at +/-0.75m.
                impulse = torque_nm*(count*.01)/1.5
                for y, z_sign in ((.75, 1.), (-.75, -1.)):
                    offset = mechanics.rotate(matrix, [0., y, 0.])
                    direction = mechanics.rotate(matrix, [0., 0., impulse*z_sign])
                    p = transform.location
                    location = carla.Location(x=p.x+offset[0], y=p.y+offset[1], z=p.z+offset[2])
                    vehicle.add_impulse_at_location(carla.Vector3D(*direction), location)
            for _ in range(count):
                if kind == 'force':
                    vehicle.add_force(carla.Vector3D(z=force_n))
                elif kind == 'torque':
                    vehicle.add_torque(carla.Vector3D(*(v*torque_nm*TORQUE_API_PER_NM for v in axis)))
                tick_snapshot(world)
            after = world.get_snapshot().find(vehicle.id)
            v1, w1 = vector(after.get_velocity()), vector(after.get_angular_velocity())
            accumulator = mechanics.RollAccumulator()
            accumulator.observe(mechanics.quaternion(transform.get_matrix()))
            observed_twist = accumulator.observe(mechanics.quaternion(after.get_transform().get_matrix()))
            row = {'before_quaternion_xyzw': mechanics.quaternion(transform.get_matrix()),
                   'after_quaternion_xyzw': mechanics.quaternion(after.get_transform().get_matrix()),
                   'observed_body_roll_twist_rad': observed_twist, 'kind': kind, 'duration_s': count*.01, 'before_velocity_mps': v0,
                   'after_velocity_mps': v1, 'delta_vz_mps': v1[2]-v0[2],
                   'delta_angular_rad_s': [math.radians(b-a) for a, b in zip(w0, w1)],
                   'body_roll_delta_rad_s': math.radians(mechanics.dot([b-a for a, b in zip(w0, w1)], axis))}
            record['probes'].append(row)
            if not vehicle.destroy():
                raise RuntimeError('Commissioning actor destruction was rejected')
            tick_snapshot(world)
            if vehicle.id in {a.id for a in world.get_actors()} or world.get_snapshot().find(vehicle.id) is not None:
                raise RuntimeError('Commissioning actor remains after destroy frame barrier')
        fall, force, pair, torque = record['probes']
        vehicle = spawn_vehicle(carla, world, config, owned, extra_height=2., roll=180.)
        contacts = queue.Queue()
        sensor = world.spawn_actor(world.get_blueprint_library().find('sensor.other.collision'), carla.Transform(), attach_to=vehicle)
        owned.append(sensor)
        sensor.listen(contacts.put_nowait)
        first_height = vehicle.get_location().z
        inverted_contacts, first_contact_step = [], None
        for step in range(250):
            tick_snapshot(world)
            while not contacts.empty():
                event = contacts.get_nowait()
                impulse = vector(event.normal_impulse)
                inverted_contacts.append({'world_frame': event.frame, 'other_type': event.other_actor.type_id,
                    'normal_impulse': impulse})
                if abs(impulse[2]) > .5*mechanics.norm(impulse):
                    first_contact_step = first_contact_step if first_contact_step is not None else step
            if first_contact_step is not None and step-first_contact_step >= 20:
                break
        inverted = {'kind': 'inverted_gravity_contact', 'contacts': inverted_contacts,
            'drop_m': first_height-vehicle.get_location().z, 'velocity_mps': vector(vehicle.get_velocity()),
            'rotation_deg': [vehicle.get_transform().rotation.roll, vehicle.get_transform().rotation.pitch, vehicle.get_transform().rotation.yaw]}
        record['probes'].append(inverted)
        sensor.stop()
        sensor.destroy()
        vehicle.destroy()
        tick_snapshot(world)
        record['checks'] = {
            'single_tick_ownership': all(r['after_frame'] == r['before_frame']+1 and r['dwell_frame'] == r['after_frame'] for r in record['clock_checks']),
            'both_engine_clock_intervals': all(abs(r['actual_delta_s']-r['engine_dt_s']) <= 1e-5 for r in record['clock_checks']),
            'gravity_response': abs(fall['delta_vz_mps']+.981) < .12,
            'force_response': abs((force['delta_vz_mps']-fall['delta_vz_mps'])-1.) < .12,
            'torque_direction': pair['body_roll_delta_rad_s'] > .001 and torque['body_roll_delta_rad_s'] > .001,
            'quaternion_angular_velocity_sign': pair['observed_body_roll_twist_rad'] > 0 and torque['observed_body_roll_twist_rad'] > 0,
            'inverted_collision_contact': first_contact_step is not None and inverted['drop_m'] > .3,
            'torque_against_offset_impulse': abs(torque['body_roll_delta_rad_s']-pair['body_roll_delta_rad_s'])
                <= max(.01, abs(pair['body_roll_delta_rad_s'])*.2)}
        record['passed'] = all(record['checks'].values())
    except Exception as error:
        record['error'] = type(error).__name__+': '+str(error)
    finally:
        record.update(clean_world(world, original, owned, lease_state))
        record['passed'] = record['passed'] and lease_state['clean']
        write_json(output/'commissioning.json', record)
        if record['passed']:
            write_json(Path(args.spool)/'commissioning.json', record)
    return record


def snapshot_state(snapshot, vehicle, origin_z, start_time):
    item = snapshot.find(vehicle.id)
    if item is None:
        raise RuntimeError('Owned vehicle missing from world snapshot')
    pose = item.get_transform()
    matrix = pose.get_matrix()
    return {'world_frame': snapshot.frame, 'time_s': float(snapshot.timestamp.elapsed_seconds)-start_time,
            'delta_seconds': float(snapshot.timestamp.delta_seconds),
            'position_m': vector(pose.location), 'rotation_deg': [float(pose.rotation.roll), float(pose.rotation.pitch), float(pose.rotation.yaw)],
            'quaternion_xyzw': mechanics.quaternion(matrix), 'matrix': matrix,
            'velocity_mps': vector(item.get_velocity()),
            'angular_velocity_rad_s': [math.radians(v) for v in vector(item.get_angular_velocity())],
            'height_m': float(pose.location.z)-origin_z}


def record_observation(state, applied_command, observed_roll):
    frame = {k: v for k, v in state.items() if k != 'matrix'}
    # Preserve the controller's actual float32 engine matrix. Reconstructing it
    # from a normalized quaternion projects it and changes small wrench terms.
    frame['rotation_matrix3x3'] = [list(row[:3]) for row in state['matrix'][:3]]
    frame.update({k: v for k, v in applied_command.items() if k not in ('brake', 'rotation_complete')})
    frame['observed_accumulated_roll_rad'] = observed_roll
    return frame


def ground_support(carla, world, state):
    """Four native scene queries outside the chassis, with explicit semantic hits."""
    hits = []
    if not callable(getattr(world, 'ground_projection', None)):
        return {'available': False, 'reason': 'ground_projection_unavailable'}
    for x, y in ((1.1, -1.4), (1.1, 1.4), (-1.1, -1.4), (-1.1, 1.4)):
        offset = mechanics.rotate(state['matrix'], [x, y, 0.])
        pos = state['position_m']
        point = world.ground_projection(carla.Location(x=pos[0]+offset[0], y=pos[1]+offset[1], z=pos[2]+.3), 4.)
        if point is None:
            return {'available': False, 'reason': 'ground_ray_miss', 'hits': hits}
        hit = {'position_m': vector(point.location), 'semantic_label': str(point.label)}
        hits.append(hit)
    heights = [hit['position_m'][2] for hit in hits]
    road = all('road' in h['semantic_label'].lower() or 'ground' in h['semantic_label'].lower() for h in hits)
    return {'available': road and max(heights)-min(heights) < .15, 'hits': hits,
            'body_ground_clearance_m': state['position_m'][2]-sum(heights)/4,
            'reason': 'native_road_ground_hits' if road else 'non_ground_geometry'}


def attach_cameras(carla, world, vehicle, origin, config, owned):
    result = []
    yaw = math.radians(origin.rotation.yaw)
    # Fixed world camera encompasses the launch, apex and nearby landing area.
    external = carla.Transform(carla.Location(x=origin.location.x-7*math.cos(yaw)-15*math.sin(yaw),
        y=origin.location.y-7*math.sin(yaw)+15*math.cos(yaw), z=origin.location.z+7),
        carla.Rotation(pitch=-13, yaw=origin.rotation.yaw-68))
    onboard = carla.Transform(carla.Location(x=1.25, z=1.3), carla.Rotation(pitch=4))
    for name, transform, attach in [('external', external, None), ('onboard', onboard, vehicle)]:
        bp = world.get_blueprint_library().find('sensor.camera.rgb')
        for key, value in {'image_size_x': '960', 'image_size_y': '540', 'fov': '95', 'sensor_tick': str(1/config['camera_fps'])}.items():
            bp.set_attribute(key, value)
        sensor = world.spawn_actor(bp, transform, attach_to=attach) if attach else world.spawn_actor(bp, transform)
        owned.append(sensor)
        samples = queue.Queue(maxsize=16)
        sensor.listen(samples.put_nowait)
        result.append((name, sensor, samples))
    return result


def encode_camera(out, name, count, fps):
    if count < 2:
        raise RuntimeError('Missing native '+name+' camera frames')
    command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-framerate', str(fps),
        '-i', str(out/name/'%06d.png'), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20',
        '-movflags', '+faststart', str(out/(name+'.mp4'))]
    result = subprocess.run(command, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError('Camera encoding failed: '+result.stderr.decode(errors='replace')[-500:])


def capture(payload, carla, client, owner, args, lease_state):
    design = payload['design']
    if sha(canonical({k: v for k, v in design.items() if k != 'sha256'})) != design['sha256']:
        lease_state['clean'] = True
        raise ValueError('Frozen stunt design hash differs')
    config = validate_config(design['config'])
    world = client.get_world()
    native = probe(carla, client, owner, args)
    pinned = payload.get('probe', {})
    for key in (*hashes(), 'server_version', 'client_version', 'server_pid', 'map', 'opendrive_sha256', 'commissioning_sha256'):
        if pinned.get(key) != native.get(key):
            lease_state['clean'] = True
            raise ValueError('Native identity changed since saved preview: '+key)
    # The current lease owns the quarantine marker, so inspect readiness components.
    if not native['physics_verified'] or native['foreign_dynamic_actors'] or not all(native['capabilities'].values()):
        lease_state['clean'] = True
        raise RuntimeError('Native physics commissioning/readiness is not verified')
    if world.get_settings().synchronous_mode or native['settings'] != pinned.get('settings'):
        lease_state['clean'] = True
        raise RuntimeError('Dedicated world clock ownership or initial settings changed')
    if world.get_map().name.split('/')[-1] != config['map_name']:
        lease_state['clean'] = True
        raise RuntimeError('Current map differs; this runner never reloads a world')
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    original = world.get_settings()
    owned, frames, events, camera_manifest = [], [], [], []
    cameras = []
    collision_queue = queue.Queue()
    record = {'schema_version': 'carla-stunt-trace/1', 'adapter_version': VERSION,
              'controller_version': mechanics.VERSION, 'provenance': payload,
              'design_ref': {k: design[k] for k in ('id', 'revision', 'sha256')},
              'seed': design['seed'], 'config': config,
              'native_identity': commissioning_identity(client, owner),
              'commissioning_sha256': native['commissioning_sha256'],
              'execution_fidelity': 'assumed_vehicle_rigid_body', 'physical_validation': 'not_established',
              'coordinate_basis': 'carla_world_m_with_proper_transform_rotation_matrix',
              'torque_api_per_nm': TORQUE_API_PER_NM,
              'clock_contract': {'nominal_dt_s': config['dt_s'], 'engine_dt_s': engine_dt(config['dt_s']),
                  'normalization': 'exact_float32_promoted_to_double_for_1ae5356_sync_window',
                  'impulse_accounting': 'nominal_dt_conservative_budget'}, 'frames': frames, 'events': events,
              'contact_interpretation': 'Vertical collision callback or native ground rays plus sustained zero-thrust physical support; no tire force telemetry.',
              'error': None}
    status, contact, previous, controller = 'completed', None, None, None
    last_camera_frame = {}
    camera_counts = {'external': 0, 'onboard': 0}
    start_wall = time.monotonic()
    lease_state['clean'] = False
    try:
        record['settings'] = prepare_world(world, config['dt_s'])
        vehicle = spawn_vehicle(carla, world, config, owned)
        vehicle.apply_control(carla.VehicleControl(brake=1.))
        for _ in range(round(1/config['dt_s'])):
            tick_snapshot(world)
        origin = vehicle.get_transform()
        record['spawn_transform'] = {'position_m': vector(origin.location), 'rotation_deg': [origin.rotation.roll, origin.rotation.pitch, origin.rotation.yaw]}
        rest_snapshot = world.get_snapshot()
        rest_state = snapshot_state(rest_snapshot, vehicle, origin.location.z, rest_snapshot.timestamp.elapsed_seconds)
        ground_baseline = ground_support(carla, world, rest_state)
        record['ground_support_baseline'] = ground_baseline
        record['vehicle_physics'] = {'mass_kg': vehicle.get_physics_control().mass,
            'bounding_box_extent_m': vector(vehicle.bounding_box.extent), 'blueprint': vehicle.type_id,
            'initialization': 'spawn_rest_then_one_target_velocity_before_recorded_execution'}
        sensor = world.spawn_actor(world.get_blueprint_library().find('sensor.other.collision'), carla.Transform(), attach_to=vehicle)
        owned.append(sensor)
        sensor.listen(collision_queue.put_nowait)
        cameras = attach_cameras(carla, world, vehicle, origin, config, owned)
        # Sensor startup can have a nonperiodic first interval. Initialize while
        # the car is still braked, before initial velocity and recorded motion.
        # Late GPU callbacks from these frames remain explicitly filtered below.
        discarded_pretrial = {name: 0 for name, _, _ in cameras}
        warmup_steps = round(.3/config['dt_s'])
        for _ in range(warmup_steps):
            tick_snapshot(world)
            for name, _, samples in cameras:
                while not samples.empty():
                    samples.get_nowait()
                    discarded_pretrial[name] += 1
        for name in camera_counts:
            (out/name).mkdir()
        vehicle.apply_control(carla.VehicleControl())
        forward = origin.get_forward_vector()
        vehicle.set_target_velocity(carla.Vector3D(x=forward.x*config['initial_speed_mps'], y=forward.y*config['initial_speed_mps'], z=0.))
        first = world.get_snapshot()
        snapshot = first
        start_time = first.timestamp.elapsed_seconds
        record['camera_initialization'] = {'warmup_nominal_s': .3, 'warmup_physics_frames': warmup_steps,
            'recording_initial_world_frame': first.frame, 'discarded_pretrial_callback_counts': discarded_pretrial}
        controller = mechanics.KickflipController(config)
        last_command = {'force_n': [0., 0., 0.], 'torque_nm': [0., 0., 0.], 'thruster_forces_n': [0.]*4,
                        'remaining_impulse_ns': config['impulse_budget_ns'], 'phase': 'initial', 'accumulated_roll_rad': 0.}
        support_start = None
        support_boundary = None
        record['clock_observations'] = []
        for step in range(round(config['duration_s']/config['dt_s'])+1):
            if CANCELLED or (out/'cancel.request').exists():
                status = 'cancelled'
                break
            if time.monotonic()-start_wall > 480:
                raise RuntimeError('Native stunt exceeded wall-clock bound')
            state = snapshot_state(snapshot, vehicle, origin.location.z, start_time)
            record['clock_observations'].append({'step': step, 'world_frame': snapshot.frame,
                'source_time_s': float(snapshot.timestamp.elapsed_seconds), 'delta_seconds': state['delta_seconds']})
            if step and abs(state['delta_seconds']-engine_dt(config['dt_s'])) > 1e-5:
                raise RuntimeError('Observed native timestep differs')
            if previous and state['world_frame'] != previous['world_frame']+1:
                raise RuntimeError(f'Foreign tick or missed world frame: step={step}, previous={previous["world_frame"]}, observed={state["world_frame"]}')
            if step % 20 == 0:
                foreign = {a.id for a in dynamic(world)}-{a.id for a in owned}
                if foreign:
                    raise RuntimeError('Foreign dynamic actor entered owned episode')
            while not collision_queue.empty():
                event = collision_queue.get_nowait()
                collision = {'type': 'native_collision', 'time_s': max(0., float(event.timestamp)-start_time),
                    'world_frame': event.frame, 'other_actor_id': event.other_actor.id,
                    'other_actor_type': event.other_actor.type_id, 'normal_impulse': vector(event.normal_impulse)}
                events.append(collision)
                if event.frame > state['world_frame']:
                    raise RuntimeError('Collision callback references an unobserved future frame')
                if (controller.airborne and state['height_m'] < .5 and event.frame > first.frame
                        and mechanics.norm(collision['normal_impulse']) > 0
                        and abs(collision['normal_impulse'][2]) >= .5*mechanics.norm(collision['normal_impulse'])):
                    impact_window = [r for r in frames if
                        event.frame-max(2, round(.15/config['dt_s'])) <= r['world_frame'] < event.frame]
                    if not impact_window:
                        raise RuntimeError('Collision callback has no retained pre-impact source frames')
                    # Bind impact speed to SOURCE frame, never callback receipt:
                    # rendering/sensor transport may deliver after the body rests.
                    impact_speed = max(abs(r['velocity_mps'][2]) for r in impact_window)
                    if support_boundary is not None:
                        impact_speed = max(impact_speed, support_boundary['pre_contact_vertical_speed_mps'])
                    if contact is None:
                        contact = {'time_s': (event.frame-first.frame)*config['dt_s'],
                            'world_frame': event.frame, 'source': 'native_collision_callback',
                            'pre_contact_vertical_speed_mps': impact_speed}
                    elif impact_speed > contact['pre_contact_vertical_speed_mps']:
                        contact['pre_contact_vertical_speed_mps'] = impact_speed
                        contact['additional_collision_world_frame'] = event.frame
            if previous and controller.airborne and contact is None and state['height_m'] < .5:
                predicted_dvz = (last_command['force_n'][2]/config['mass_kg']-mechanics.GRAVITY)*config['dt_s']
                observed_dvz = state['velocity_mps'][2]-previous['velocity_mps'][2]
                if support_boundary is None and previous['velocity_mps'][2] < -.2 and observed_dvz > predicted_dvz+.08:
                    support_boundary = {'time_s': state['time_s'], 'previous_world_frame': previous['world_frame'],
                        'world_frame': state['world_frame'], 'observed_delta_vz_mps': observed_dvz,
                        'commanded_freeflight_delta_vz_mps': predicted_dvz,
                        'pre_contact_vertical_speed_mps': max([abs(r['velocity_mps'][2]) for r in frames[-max(2, round(.15/config['dt_s'])):]] or [abs(previous['velocity_mps'][2])])}
            # Native ray hits must agree with the measured resting clearance.
            # Combine them with gravity-on, zero thrust and sustained support;
            # neither height nor low velocity alone establishes contact.
            if controller.airborne and contact is None and state['height_m'] < .2 and abs(state['velocity_mps'][2]) < .15 and mechanics.norm(last_command['force_n']) < .1:
                support = ground_support(carla, world, state)
                state['ground_support_evidence'] = support
                grounded = (support_boundary is not None and ground_baseline.get('available') and support.get('available') and
                    abs(support['body_ground_clearance_m']-ground_baseline['body_ground_clearance_m']) < .12)
                if grounded:
                    if support_start is None:
                        support_start = {'time_s': state['time_s'], 'source': 'native_ground_rays_and_zero_thrust_support',
                            'pre_contact_vertical_speed_mps': support_boundary['pre_contact_vertical_speed_mps'],
                            'impact_boundary': support_boundary,
                            'ground_evidence': support, 'baseline': ground_baseline}
                    if state['time_s']-support_start['time_s'] >= .12:
                        contact = support_start | {'confirmed_at_s': state['time_s']}
                else:
                    support_start = None
            else:
                support_start = None
            state['contact_confirmed'] = contact is not None
            if step < round(config['duration_s']/config['dt_s']):
                command = controller.step(state)
            else:
                controller.roll = controller.accumulator.observe(state['quaternion_xyzw'])
                command = last_command
            frame = record_observation(state, last_command, controller.roll)
            frames.append(frame)
            for name, _, samples in cameras:
                # RGB has a renderer pipeline delay. Drain existing frames each
                # step and check exact source-frame gaps in the final manifest.
                while not samples.empty():
                    image = samples.get_nowait()
                    if image.frame <= first.frame:
                        discarded_pretrial[name] += 1
                        continue
                    index = camera_counts[name]
                    path = out/name/f'{index:06d}.png'
                    image.save_to_disk(str(path))
                    camera_manifest.append({'camera': name, 'index': index, 'world_frame': image.frame,
                        'time_s': float(image.timestamp)-start_time, 'sha256': file_sha(path)})
                    camera_counts[name] += 1
                    last_camera_frame[name] = image.frame
            if step == round(config['duration_s']/config['dt_s']):
                break
            apply_wrench(carla, vehicle, command['force_n'], command['torque_nm'])
            vehicle.apply_control(carla.VehicleControl(brake=1. if command['brake'] else 0.))
            last_command, previous = command, state
            snapshot = tick_snapshot(world)
        # Let already-produced camera callbacks arrive without advancing physics.
        time.sleep(.15)
        for name, _, samples in cameras:
            while not samples.empty():
                image = samples.get_nowait()
                if image.frame <= first.frame:
                    discarded_pretrial[name] += 1
                    continue
                index = camera_counts[name]
                path = out/name/f'{index:06d}.png'
                image.save_to_disk(str(path))
                camera_manifest.append({'camera': name, 'index': index, 'world_frame': image.frame,
                    'time_s': float(image.timestamp)-start_time, 'sha256': file_sha(path)})
                camera_counts[name] += 1
        record['summary'] = mechanics.landing_summary(frames, contact=contact,
            rotation_complete=controller.rotation_complete)
        record['summary'].update(max_height_m=max((r['height_m'] for r in frames), default=0.),
            accumulated_roll_deg=math.degrees(controller.roll),
            impulse_used_ns=config['impulse_budget_ns']-(frames[-1]['remaining_impulse_ns'] if frames else config['impulse_budget_ns']))
    except Exception as error:
        status = 'failed'
        record['error'] = type(error).__name__+': '+str(error)
        record['summary'] = {'demo_landing_passed': False, 'reason': 'execution_error', 'physical_validation': 'not_established'}
    finally:
        record.update(clean_world(world, original, owned, lease_state))
        record['status'] = status
        write_json(out/'stunt-trace.json', record)
        write_json(out/'camera-manifest.json', {'schema_version': 'stunt-camera-manifest/1', 'fps': config['camera_fps'],
            'frames': camera_manifest, 'counts': camera_counts, 'physical_validation': 'not_established'})
    return finish_capture_output(out, config, camera_counts, camera_manifest, record, payload)


def finish_capture_output(out, config, camera_counts, camera_manifest, record, payload):
    """Preserve cleanup truth even when camera validation or encoding fails."""
    if record['status'] == 'completed':
        try:
            for name, count in camera_counts.items():
                rows = [r for r in camera_manifest if r['camera'] == name]
                interval = round(1/(config['camera_fps']*config['dt_s']))
                if any(b['world_frame']-a['world_frame'] != interval for a, b in zip(rows, rows[1:])):
                    raise RuntimeError('Native camera source frame gap; refusing complete result')
                if count < config['duration_s']*config['camera_fps']-2:
                    raise RuntimeError('Native camera capture incomplete')
                encode_camera(out, name, count, config['camera_fps'])
        except Exception as error:
            record['status'] = 'failed'
            record['error'] = type(error).__name__+': '+str(error)
            record['summary'] = {**record['summary'], 'demo_landing_passed': False,
                'reason': 'artifact_error', 'physical_validation': 'not_established'}
            write_json(out/'stunt-trace.json', record)
    artifacts = []
    for name, media in [('stunt-trace.json', 'application/json'), ('camera-manifest.json', 'application/json'),
                         ('external.mp4', 'video/mp4'), ('onboard.mp4', 'video/mp4')]:
        path = out/name
        if path.exists():
            artifacts.append({'name': name, 'sha256': file_sha(path), 'size_bytes': path.stat().st_size, 'media_type': media})
    receipt = {'schema_version': 'carla-stunt-native-receipt/1', 'status': record['status'],
        'external_execution_id': payload['external_execution_id'], 'cleanup_status': record['cleanup_status'],
        'artifacts': artifacts, 'summary': record['summary'], 'error': record['error']}
    write_json(out/'receipt.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--spool', required=True)
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--commission', action='store_true')
    parser.add_argument('--output')
    args = parser.parse_args()
    carla, client, owner = connect(args)
    if args.probe:
        print(json.dumps(probe(carla, client, owner, args), allow_nan=False))
        return
    if not args.output:
        raise ValueError('A new output directory is required')
    with lease(args.spool, Path(args.output).name) as state:
        if args.commission:
            result = commission(carla, client, owner, args, state)
        else:
            raw = sys.stdin.buffer.read(2*1024**2+1)
            if len(raw) > 2*1024**2:
                state['clean'] = True
                raise ValueError('Stunt payload exceeds 2 MiB')
            result = capture(json.loads(raw), carla, client, owner, args, state)
    print(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    def cancel_signal(*_):
        global CANCELLED
        CANCELLED = True
    signal.signal(signal.SIGINT, cancel_signal)
    signal.signal(signal.SIGTERM, cancel_signal)
    main()
