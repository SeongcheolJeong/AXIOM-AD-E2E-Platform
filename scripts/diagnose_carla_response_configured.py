#!/usr/bin/env python3
"""Post-spawn configuration CARLA 0.4.0 diagnostic, never a platform run or verdict.

Run with the existing optional CARLA environment and an immutable
SimulationExecutionRequest JSON. Exactly one selected intervention is executed;
there is no retry, map reload, foreign actor cleanup, or platform database access.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import sys
import time
import traceback

DT = 0.05
INTERVALS = 80
TRIALS = ('configure_after_spawn',)
TELEMETRY_FIELDS = ('speed', 'steer', 'throttle', 'brake', 'engine_rpm', 'gear', 'drag')
WHEEL_FIELDS = ('tire_friction', 'lat_slip', 'long_slip', 'omega', 'tire_load',
    'normalized_tire_load', 'torque', 'long_force', 'lat_force',
    'normalized_long_force', 'normalized_lat_force')
PHYSICS_FIELDS = ('mass', 'drag_coefficient', 'max_rpm', 'moi',
    'damping_rate_full_throttle', 'damping_rate_zero_throttle_clutch_engaged',
    'damping_rate_zero_throttle_clutch_disengaged', 'use_gear_autobox',
    'gear_switch_time', 'clutch_strength', 'final_ratio', 'use_sweep_wheel_collision')
PHYSICS_WHEEL_FIELDS = ('tire_friction', 'damping_rate', 'max_steer_angle', 'radius',
    'max_brake_torque', 'max_handbrake_torque', 'lat_stiff_max_load', 'lat_stiff_value', 'long_stiff_value')
SETTINGS_FIELDS = ('synchronous_mode', 'fixed_delta_seconds', 'substepping',
    'max_substep_delta_time', 'max_substeps', 'no_rendering_mode', 'deterministic_ragdolls')
WEATHER_FIELDS = ('cloudiness', 'precipitation', 'precipitation_deposits', 'wind_intensity',
    'sun_azimuth_angle', 'sun_altitude_angle', 'fog_density', 'fog_distance', 'fog_falloff',
    'wetness', 'scattering_intensity', 'mie_scattering_scale', 'rayleigh_scattering_scale', 'dust_storm')
CONTROL_FIELDS = ('throttle', 'brake', 'steer', 'reverse', 'hand_brake', 'manual_gear_shift', 'gear')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def field(value, path, issues):
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if math.isfinite(value):
            return value
        issues.append({'field': path, 'status': 'nonfinite', 'raw': repr(value)})
        return None
    issues.append({'field': path, 'status': 'unsupported_type', 'raw': repr(value)[:120]})
    return None


def fields(value, names, issues, prefix=''):
    result = {}
    for name in names:
        path = prefix + name
        try:
            result[name] = field(getattr(value, name), path, issues)
        except Exception as error:
            result[name] = None
            issues.append({'field': path, 'status': 'unavailable', 'error': str(error)[:240]})
    return result


def vector(value, issues, prefix):
    return fields(value, ('x', 'y', 'z'), issues, prefix)


def structure(value, names):
    issues = []
    return {'values': fields(value, names, issues), 'invalid_fields': issues}


def physics(value):
    """Explicit exported physics fields; configured scale is not effective tire mu."""
    issues = []
    result = fields(value, PHYSICS_FIELDS, issues)
    for name, names in (('center_of_mass', ('x', 'y', 'z')),):
        try:
            result[name] = fields(getattr(value, name), names, issues, name + '.')
        except Exception as error:
            result[name] = None
            issues.append({'field': name, 'status': 'unavailable', 'error': str(error)[:240]})
    for name, names in (('wheels', PHYSICS_WHEEL_FIELDS), ('torque_curve', ('x', 'y')),
                        ('steering_curve', ('x', 'y')), ('forward_gears', ('ratio', 'down_ratio', 'up_ratio'))):
        try:
            values = list(getattr(value, name))
            if len(values) > 64:
                raise ValueError('Physics sequence exceeds 64 entries')
            result[name] = []
            for index, item in enumerate(values):
                prefix = f'{name}.{index}.'
                entry = fields(item, names, issues, prefix)
                if name == 'wheels':
                    try:
                        entry['position'] = vector(item.position, issues, prefix + 'position.')
                    except Exception as error:
                        entry['position'] = None
                        issues.append({'field': prefix + 'position', 'status': 'unavailable', 'error': str(error)[:240]})
                result[name].append(entry)
        except Exception as error:
            result[name] = None
            issues.append({'field': name, 'status': 'unavailable', 'error': str(error)[:240]})
    return {'values': result, 'invalid_fields': issues}


def differences(requested, actual, path=''):
    """Exact values, including float32 roundoff, remain visible; never reapply."""
    if isinstance(requested, dict) and isinstance(actual, dict):
        return [entry for key in sorted(set(requested) | set(actual))
            for entry in differences(requested.get(key), actual.get(key), f'{path}.{key}'.lstrip('.'))]
    if isinstance(requested, list) and isinstance(actual, list) and len(requested) == len(actual):
        return [entry for i, (left, right) in enumerate(zip(requested, actual))
            for entry in differences(left, right, f'{path}.{i}')]
    return [] if requested == actual else [{'field': path, 'requested': requested, 'actual': actual}]


def actor_snapshot(snapshot, actor):
    issues = []
    result = {'actor_id': actor.id}
    item = snapshot.find(actor.id)
    if item is None:
        return {**result, 'status': 'unavailable', 'error': 'Actor absent from coherent snapshot'}
    try:
        pose = item.get_transform()
        result['position_m'] = vector(pose.location, issues, 'position_m.')
        result['rotation_deg'] = fields(pose.rotation, ('pitch', 'yaw', 'roll'), issues, 'rotation_deg.')
        result['forward_vector'] = vector(pose.get_forward_vector(), issues, 'forward_vector.')
        for key, method in (('velocity_mps', item.get_velocity), ('acceleration_mps2', item.get_acceleration),
                            ('angular_velocity_deg_s', item.get_angular_velocity)):
            result[key] = vector(method(), issues, key + '.')
    except Exception as error:
        issues.append({'field': 'actor_snapshot', 'status': 'unavailable', 'error': str(error)[:300]})
    result.update(status='invalid_fields' if issues else 'available', invalid_fields=issues)
    return result


def telemetry(actor):
    issues = []
    try:
        value = actor.get_telemetry_data()
        result = fields(value, TELEMETRY_FIELDS, issues)
        wheels = list(value.wheels)
        if len(wheels) > 16:
            raise ValueError('Telemetry has more than 16 wheels')
        result['wheels'] = [fields(wheel, WHEEL_FIELDS, issues, f'wheels.{index}.') for index, wheel in enumerate(wheels)]
        return {'status': 'invalid_fields' if issues else 'available', 'values': result, 'invalid_fields': issues}
    except Exception as error:
        return {'status': 'unavailable', 'error': str(error)[:400], 'invalid_fields': issues}


def lead_control(snapshot, lead, road_map, target_speed, force_brake):
    """Same 0.4 lead-script equation, but reads one snapshot and returns a request."""
    actor = snapshot.find(lead.id)
    pose, velocity = actor.get_transform(), actor.get_velocity()
    speed = math.hypot(velocity.x, velocity.y, velocity.z)
    waypoint = road_map.get_waypoint(pose.location)
    choices = waypoint.next(max(4, speed * .6)) if waypoint else []
    if len(choices) != 1:
        raise ValueError('Lead map route is absent or ambiguous')
    aim = choices[0].transform.location
    desired = math.degrees(math.atan2(aim.y-pose.location.y, aim.x-pose.location.x))
    yaw_error = (desired-pose.rotation.yaw+180) % 360-180
    error = target_speed-speed
    return {'throttle': 0 if force_brake else max(0, min(.7, error*.25)),
        'brake': 1 if force_brake else max(0, min(.8, -error*.2)),
        'steer': max(-.5, min(.5, yaw_error/45)), 'reverse': False, 'hand_brake': False}


def checkpoint(directory, report):
    raw = canonical(report)
    if len(raw) > 12 * 1024 * 1024:
        raise ValueError('Diagnostic report exceeds 12 MiB bound')
    temporary = directory / 'report.json.pending'
    with temporary.open('wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(directory / 'report.json')


def diagnose(request_path, directory, *, trial='configure_after_spawn', wall_timeout=120., rpc_timeout=10.):
    # Optional imports are deliberately inside execution. No database/HTTP modules.
    from ade2e.simulation_context import SimulationExecutionRequest, simulation_input_hash, validate_simulation_context
    from ade2e.stack_runtime import StackRegistry, StackSession
    from ade2e.stack_schemas import StackPacket, StackLidar
    from axiom_carla.runner import CarlaRunner, _copy_transform, _sensor_frame, _transform_evidence
    from axiom_carla.stack_bridge import connect_preflight, _configured_lidar, _ego_source, _consumed_xyz, carla_control
    from axiom_carla.recording import live_source_messages, live_stack_recording, simulation_timestamp_ns
    from axiom_carla.startup import StartupFailure, StartupPolicy, wait_for_stability, ANGULAR_VELOCITY_BASIS
    from axiom_carla.version import ADAPTER_VERSION

    if trial not in TRIALS or not 10 <= wall_timeout <= 300 or not .1 <= rpc_timeout <= 10:
        raise ValueError('Invalid trial or bounded wall/RPC timeout')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    report = {'schema_version': 'carla-response-diagnostic/2', 'configuration_order': 'spawn_both_then_apply_both',
        'parent_script_sha256': '694ecc50041c4dd34cfc934788c42c3de6877be011f97139d6a365e8f32af134', 'status': 'running', 'phase': 'input_validation',
        'scope': 'Standalone controlled diagnostic; not a platform run, validation verdict, tire calibration, or measured real-world result.',
        'trial': trial, 'script_sha256': sha(Path(__file__).read_bytes()), 'adapter_version': ADAPTER_VERSION,
        'wall_timeout_s': wall_timeout, 'rpc_timeout_s': rpc_timeout, 'dt_s': DT, 'expected_commands': INTERVALS,
        'expected_measurement_observations': INTERVALS+1, 'startup': None, 'extra_rest': [], 'boundaries': [],
        'observations': [], 'commands': [], 'physics': {}, 'cleanup': [], 'files': [],
        'limitations': ['No automatic retry or additional trial. The only intervention is applying both physics configurations after both vehicle spawns.',
            'Both ego and lead use synchronous batch ACK; original 0.4 lead control used async dispatch.',
            'Diagnostic attaches only LiDAR, with no collision sensor; original platform mode also attached a collision observer. No collision outcome is claimed.',
            'Target-velocity and physics setter returns are dispatch returns, not actuator acknowledgments.',
            'Telemetry has no frame ID. Frame-bracketed RPC readout under sole-tick ownership is separate from coherent ActorSnapshot data.',
            'Debug-derived telemetry may be uninitialized in headless builds. NaN/Inf and unavailable fields remain explicit.',
            'Timeouts bound tick/RPC requests; an unresponsive native call or OS cannot be forcibly interrupted by this Python process. Cleanup has its own RPC budget.',
            'Shared kernel input preserves the 0.4 norm-of-3D ego speed; raw vectors permit independent longitudinal/vertical diagnosis.',
            'No post-injection samples or control intervals are trimmed. A failed trial retains only the actually observed prefix.'],
        'telemetry_basis': {'source_version': '0.9.16', 'url': 'https://github.com/carla-simulator/carla/blob/0.9.16/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Vehicle/CarlaWheeledVehicle.cpp',
            'speed': 'signed vehicle-forward m/s', 'engine_rpm': 'RPM', 'drag': 'N; UE Debug field',
            'wheel_tire_friction': 'effective PhysX query value, distinct from configured physics tire_friction scale',
            'lat_slip': 'degrees', 'long_slip': 'dimensionless', 'omega': 'direct PhysX wheel rotation speed; rad/s expected, CARLA header has no unit annotation',
            'tire_load': 'N; UE Debug field', 'torque': 'Nm; UE Debug field', 'long_force_and_lat_force': 'N; UE Debug fields',
            'normalized_load_and_forces': 'dimensionless; force division has no zero-load guard'},
        'angular_velocity_basis': ANGULAR_VELOCITY_BASIS}
    checkpoint(directory, report)
    world = client = None
    actors, sensors, messages = [], [], []
    original_settings = original_weather = None
    raw_stream = xyz_stream = None
    raw_digest, xyz_digest = hashlib.sha256(), hashlib.sha256()
    raw_size = xyz_size = 0
    ticks_after_spawn = 0
    cleanup_deadline = None

    def budget():
        remaining = wall_timeout-(time.monotonic()-started)
        if remaining <= 0:
            raise TimeoutError('Diagnostic wall-clock deadline exceeded')
        if client is not None:
            client.set_timeout(min(rpc_timeout, remaining))
        return remaining

    def advance(previous):
        nonlocal ticks_after_spawn
        frame = world.tick(seconds=min(rpc_timeout, budget()))
        ticks_after_spawn += 1
        budget()
        snapshot = world.get_snapshot()
        at = float(snapshot.timestamp.elapsed_seconds)
        before = float(previous.timestamp.elapsed_seconds)
        delta = float(snapshot.timestamp.delta_seconds)
        if frame != snapshot.frame or snapshot.frame != previous.frame+1:
            raise ValueError('Tick/snapshot frame is not exactly previous + 1')
        if not all(math.isfinite(x) for x in (at, before, delta)) or abs(at-before-DT) > 1e-6 or abs(delta-DT) > 1e-6:
            raise ValueError('Snapshot simulation clock is not the pinned 20 Hz interval')
        return snapshot

    def bracket(snapshot, *, include_physics=False):
        budget()
        before = world.get_snapshot().frame
        data = {'frame_before': before, 'expected_frame': snapshot.frame, 'actors': {}}
        for role, actor in zip(('ego', 'lead'), actors):
            budget()
            result = {'control': {'status': 'unavailable'}, 'telemetry': {'status': 'not_sampled_before_first_tick'}}
            data['actors'][role] = result
            try:
                result['control'] = {'status': 'available', **structure(actor.get_control(), CONTROL_FIELDS)}
            except Exception as error:
                result['control'] = {'status': 'unavailable', 'error': str(error)[:400]}
            if ticks_after_spawn > 0:
                budget()
                result['telemetry'] = telemetry(actor)
            if include_physics:
                budget()
                try:
                    result['physics'] = {'status': 'available', **physics(actor.get_physics_control())}
                except Exception as error:
                    result['physics'] = {'status': 'unavailable', 'error': str(error)[:400]}
        budget()
        data['frame_after'] = world.get_snapshot().frame
        data['frame_bracket_stable'] = before == snapshot.frame == data['frame_after']
        return data

    def observe_state(snapshot, phase, index=None, *, include_physics=False):
        row = {'phase': phase, 'index': index, 'world_frame': snapshot.frame,
            'time_s': float(snapshot.timestamp.elapsed_seconds), 'delta_s': float(snapshot.timestamp.delta_seconds),
            'actors': {role: actor_snapshot(snapshot, actor) for role, actor in zip(('ego', 'lead'), actors)}}
        # Retain coherent vectors before optional RPCs can fail.
        report['observations'].append(row)
        row['rpc_readings'] = bracket(snapshot, include_physics=include_physics)
        if not row['rpc_readings']['frame_bracket_stable']:
            raise ValueError('World advanced during post-tick RPC readback; sole tick ownership lost')
        return row

    try:
        with Path(request_path).open('rb') as request_stream:
            request_raw = request_stream.read(1024*1024+1)
        if len(request_raw) > 1024*1024:
            raise ValueError('Request exceeds 1 MiB bound')
        report['request_sha256'] = sha(request_raw)
        (directory/'request.json').write_bytes(request_raw)
        request = SimulationExecutionRequest.model_validate_json(request_raw)
        if ADAPTER_VERSION != '0.4.0':
            raise ValueError(f'Diagnostic is pinned to immutable adapter 0.4.0; installed {ADAPTER_VERSION}')
        report['request'] = request.model_dump(mode='json')
        report['canonical_request_sha256'] = sha(canonical(report['request']))
        report['context_hash'] = request.context.context_hash
        report['input_hash'] = request.input_hash
        scenario, context = request.scenario, request.context
        p = scenario.parameters
        if request.input_hash != simulation_input_hash(scenario, 'carla', '0.4.0', context):
            raise ValueError('Execution request input hash mismatch')
        if (scenario.duration_s != 4 or scenario.seed != 42 or p.ego_speed_mps != 2 or
                p.target_speed_mps != 2 or p.friction != .8 or scenario.category != 'lane_follow'):
            raise ValueError('Trial requires lane_follow, 4 s, seed 42, ego/lead 2 m/s, declared friction 0.8')
        registry = StackRegistry(load_plugins=False)
        validate_simulation_context(scenario, context, stack_registry=registry)
        session = StackSession(context.stack_snapshot.definition, registry)
        report['phase'] = 'preflight'
        budget()
        runner = CarlaRunner()
        carla, client, world, version, road_map, transforms, context = connect_preflight(runner, scenario, context)
        budget()
        report.update(server_version=version, client_version=client.get_client_version(), map=road_map.name,
            map_opendrive_sha256=sha(road_map.to_opendrive().encode()), host=os.getenv('ADE2E_CARLA_HOST', '127.0.0.1'),
            port=int(os.getenv('ADE2E_CARLA_PORT', '2000')))
        original_settings, original_weather = world.get_settings(), world.get_weather()
        report['original_settings'] = structure(original_settings, SETTINGS_FIELDS)
        report['original_weather'] = structure(original_weather, WEATHER_FIELDS)
        report['original_actor_ids'] = [actor.id for actor in world.get_actors()]
        checkpoint(directory, report)
        report['phase'] = 'world_configuration'
        settings = world.get_settings()
        settings.synchronous_mode, settings.fixed_delta_seconds = True, DT
        settings.substepping, settings.max_substep_delta_time, settings.max_substeps = True, .01, 10
        budget(); world.apply_settings(settings)
        budget(); world.set_weather(carla.WeatherParameters(cloudiness=80 if scenario.weather != 'clear' else 10,
            precipitation=70 if scenario.weather == 'rain' else 0, wetness=80 if scenario.weather == 'rain' else 0,
            fog_density=60 if scenario.weather == 'fog' else 0,
            sun_altitude_angle={'day': 65, 'dusk': 5, 'night': -30}[scenario.time_of_day]))
        report['configured_settings'] = structure(world.get_settings(), SETTINGS_FIELDS)
        report['configured_weather'] = structure(world.get_weather(), WEATHER_FIELDS)
        library = world.get_blueprint_library()
        blueprint = library.find('vehicle.tesla.model3')
        report['spawn_transforms'] = {}
        for role, source in zip(('ego', 'lead'), transforms):
            report['phase'] = 'spawn_' + role
            blueprint.set_attribute('role_name', 'axiom_response_diagnostic_' + role)
            pose = _copy_transform(carla, source, z_offset=.3)
            report['spawn_transforms'][role] = {'source': _transform_evidence(source), 'requested': _transform_evidence(pose)}
            budget(); actor = world.try_spawn_actor(blueprint, pose)
            if actor is None:
                raise RuntimeError('Spawn rejected for ' + role)
            actors.append(actor)
        # Deliberate independent intervention: both actors exist before either
        # requested physics configuration is applied. No hidden reapply/retry.
        for role, actor in zip(('ego', 'lead'), actors):
            report['phase'] = 'configure_after_spawn_' + role
            budget(); value = actor.get_physics_control()
            stock = physics(value)
            if trial == 'configure_after_spawn':
                wheels = value.wheels
                for wheel in wheels:
                    wheel.tire_friction = .8
                value.wheels = wheels
            requested = physics(value)
            entry = {'actor_id': actor.id, 'stock': stock, 'requested': requested,
                'operation': 'none_stock_untouched' if trial == 'native_physics' else 'apply_physics_control',
                'dispatch_status': 'not_sent'}
            report['physics'][role] = entry
            if trial != 'native_physics':
                entry['dispatch_status'] = 'outcome_unknown'
                budget(); actor.apply_physics_control(value)
                entry['dispatch_status'] = 'dispatch_returned_not_acknowledged'
            else:
                entry['dispatch_status'] = 'not_applicable'
        ego, lead = actors
        report['phase'] = 'rest_stability_gate'
        try:
            report['startup'] = wait_for_stability(world, {'ego': ego, 'lead': lead}, dt=DT,
                policy=StartupPolicy(max_wall_time_s=min(60., budget()), max_tick_rpc_time_s=rpc_timeout))
        except StartupFailure as error:
            report['startup'] = error.report
            raise
        ticks_after_spawn = report['startup']['completed_ticks']
        snapshot = world.get_snapshot()
        rest = observe_state(snapshot, 'startup_gate_passed', include_physics=True)
        for role in ('ego', 'lead'):
            actual = rest['rpc_readings']['actors'][role]['physics']
            report['physics'][role]['post_startup_readback'] = actual
            if actual['status'] != 'available':
                raise RuntimeError('Essential post-startup physics readback unavailable: ' + role)
            report['physics'][role]['exact_differences'] = differences(report['physics'][role]['requested']['values'], actual['values'])
            report['physics'][role]['comparison_policy'] = 'Exact exported-field comparison, including float32 roundoff; mismatch is recorded, never automatically reapplied.'
        if trial == 'rest40':
            for _ in range(max(0, 40-ticks_after_spawn)):
                snapshot = advance(snapshot)
                row = observe_state(snapshot, 'extra_rest_until_total_40_ticks')
                report['extra_rest'].append({'world_frame': snapshot.frame, 'observation_index': len(report['observations'])-1})
        report['phase'] = 'initial_target_velocity'
        boundary = {'type': 'initial_target_velocity', 'world_frame_before': snapshot.frame,
            'time_s': snapshot.timestamp.elapsed_seconds, 'requests': {}}
        report['boundaries'].append(boundary)
        for role, actor in zip(('ego', 'lead'), actors):
            forward = snapshot.find(actor.id).get_transform().get_forward_vector()
            target = carla.Vector3D(forward.x*2, forward.y*2, forward.z*2)
            entry = {'actor_id': actor.id, 'requested_native_velocity_mps': structure(target, ('x', 'y', 'z')),
                'dispatch_status': 'outcome_unknown'}
            boundary['requests'][role] = entry
            budget(); actor.set_target_velocity(target)
            entry['dispatch_status'] = 'dispatch_returned_not_acknowledged'
        boundary['world_frame_after_dispatch'] = world.get_snapshot().frame
        if boundary['world_frame_after_dispatch'] != snapshot.frame:
            raise ValueError('World advanced during initial target velocity dispatch')
        checkpoint(directory, report)
        report['phase'] = 'sensor_setup'
        lidar_queue, callback_errors = queue.Queue(maxsize=4), []
        def receive(cloud):
            try:
                lidar_queue.put_nowait(cloud)
            except queue.Full:
                if not callback_errors:
                    callback_errors.append('LiDAR queue overflow')
        readback = {}
        lidar_bp = _configured_lidar(library, context.sensor_profile, carla, readback)
        report['lidar_profile_readback'] = readback
        budget(); lidar = world.spawn_actor(lidar_bp, carla.Transform(carla.Location(z=2.2)), attach_to=ego)
        sensors.append(lidar)
        lidar.listen(receive)
        raw_stream = (directory/'raw-lidar-xyzi.bin').open('xb')
        xyz_stream = (directory/'consumed-ego-xyz.bin').open('xb')

        def observe_sensor(snapshot, index):
            nonlocal raw_size, xyz_size
            row = observe_state(snapshot, 'measurement', index)
            if callback_errors:
                raise RuntimeError(callback_errors[0])
            cloud = _sensor_frame(lidar_queue, snapshot.frame, timeout=min(rpc_timeout, budget()))
            if simulation_timestamp_ns(cloud.timestamp) != simulation_timestamp_ns(snapshot.timestamp.elapsed_seconds):
                raise ValueError('LiDAR timestamp does not match coherent snapshot')
            raw = bytes(cloud.raw_data)
            if len(raw) > 20_000*16:
                raise ValueError('Raw LiDAR exceeds 20,000 point bound')
            row['raw_lidar'] = {'world_frame': cloud.frame, 'timestamp_s': cloud.timestamp,
                'sha256': sha(raw), 'size_bytes': len(raw), 'offset_bytes': raw_size, 'file': 'raw-lidar-xyzi.bin'}
            raw_stream.write(raw); raw_stream.flush()
            raw_digest.update(raw); raw_size += len(raw)
            source, ego_state = _ego_source(snapshot, ego.id, road_map)
            source_messages = live_source_messages(snapshot.frame, cloud.timestamp, raw, source)
            if sum(len(m.content) for m in messages)+sum(len(m.content) for m in source_messages) > 18*1024*1024:
                raise ValueError('MCAP source budget would be exceeded before next command')
            row['source_refs'] = [message.reference(len(messages)+i) for i, message in enumerate(source_messages)]
            messages.extend(source_messages)
            packet = StackPacket(sequence=index, at_ns=source['timestamp_ns'], ego=ego_state)
            points = None
            try:
                points = _consumed_xyz(raw, context.sensor_profile.mount)
                packet.lidar = StackLidar(timestamp_ns=source['timestamp_ns'], capture_time_ns=source['timestamp_ns'],
                    frame_id='ego', point_count=len(points)//12, points_sha256=sha(points), size_bytes=len(points),
                    source_message_sha256=sha(source_messages[0].content), complete_frame=True)
                row['consumed_points'] = {'file': 'consumed-ego-xyz.bin', 'offset_bytes': xyz_size,
                    'size_bytes': len(points), 'sha256': sha(points), 'point_count': len(points)//12}
                xyz_stream.write(points); xyz_stream.flush()
                xyz_digest.update(points); xyz_size += len(points)
            except ValueError as error:
                packet.unavailable_reasons.append('lidar_unavailable: ' + str(error)[:400])
            row['packet'] = packet.model_dump(mode='json')
            return packet, points

        report['phase'] = 'initial_measurement'
        snapshot = advance(snapshot)
        report['measurement_start_frame'] = snapshot.frame
        report['measurement_start_time_s'] = snapshot.timestamp.elapsed_seconds
        packet, points = observe_sensor(snapshot, 0)
        for index in range(INTERVALS):
            report['phase'] = 'command_' + str(index)
            budget()
            output = session.step(packet, points)
            requested = {'ego': carla_control(output.control),
                'lead': lead_control(snapshot, lead, road_map, p.target_speed_mps, False)}
            command = {'sequence': index, 'source_world_frame': snapshot.frame,
                'source_time_s': snapshot.timestamp.elapsed_seconds, 'output': output.model_dump(mode='json'),
                'requested': requested, 'rpc_responses': [], 'application_status': 'not_sent',
                'next_world_frame': None, 'next_time_s': None}
            report['commands'].append(command)
            command['application_status'] = 'send_outcome_unknown'
            checkpoint(directory, report)
            budget()
            replies = client.apply_batch_sync([carla.command.ApplyVehicleControl(actor.id, carla.VehicleControl(**requested[role]))
                for role, actor in zip(('ego', 'lead'), actors)], False)
            if not isinstance(replies, (list, tuple)) or len(replies) != 2:
                raise ValueError('Control RPC did not return exactly two results')
            rejected = False
            for role, actor, reply in zip(('ego', 'lead'), actors, replies):
                error_flag = reply.has_error()
                if type(error_flag) is not bool:
                    raise ValueError('Invalid control RPC result error flag')
                entry = {'role': role, 'actor_id': None, 'error': None}
                command['rpc_responses'].append(entry)
                if error_flag:
                    entry['error'] = str(reply.error)[:400]
                    rejected = True
                else:
                    entry['actor_id'] = reply.actor_id
                    if type(reply.actor_id) is not int or reply.actor_id != actor.id:
                        raise ValueError('Control ACK actor identity mismatch')
            if rejected:
                command['application_status'] = 'send_rejected'
                raise RuntimeError('At least one requested control was rejected; no automatic retry')
            command['application_status'] = 'acknowledged_without_next_observation'
            checkpoint(directory, report)
            snapshot = advance(snapshot)
            command.update(next_world_frame=snapshot.frame, next_time_s=snapshot.timestamp.elapsed_seconds,
                application_status='next_frame_observed')
            checkpoint(directory, report)
            packet, points = observe_sensor(snapshot, index+1)
            command['readback_observation_index'] = len(report['observations'])-1
        report.update(status='completed', phase='completed', measured_duration_s=round(snapshot.timestamp.elapsed_seconds-report['measurement_start_time_s'], 9))
    except BaseException as error:
        report.update(status='failed', error={'type': type(error).__name__, 'message': str(error)[:2000],
            'traceback': traceback.format_exc()[-6000:]})
    finally:
        report['execution_elapsed_wall_s'] = time.monotonic()-started
        cleanup_deadline = time.monotonic()+30
        def cleanup(label, action):
            try:
                if client is not None:
                    client.set_timeout(min(rpc_timeout, max(.1, cleanup_deadline-time.monotonic())))
                result = action()
                report['cleanup'].append({'operation': label, 'status': 'completed', 'result': result})
            except BaseException as error:
                report['cleanup'].append({'operation': label, 'status': 'failed', 'error': str(error)[:500]})
        for sensor in reversed(sensors):
            cleanup(f'stop_sensor_{sensor.id}', sensor.stop)
            def destroy_sensor(sensor=sensor):
                if sensor.destroy() is not True:
                    raise RuntimeError('Sensor destroy not acknowledged')
            cleanup(f'destroy_sensor_{sensor.id}', destroy_sensor)
        for actor in reversed(actors):
            def destroy_actor(actor=actor):
                if actor.destroy() is not True:
                    raise RuntimeError('Vehicle destroy not acknowledged')
            cleanup(f'destroy_actor_{actor.id}', destroy_actor)
        if world is not None and original_weather is not None:
            cleanup('restore_weather', lambda: world.set_weather(original_weather))
        if world is not None and original_settings is not None:
            cleanup('restore_settings', lambda: world.apply_settings(original_settings))
            def final_state():
                settings = structure(world.get_settings(), SETTINGS_FIELDS)
                weather = structure(world.get_weather(), WEATHER_FIELDS)
                owned = {a.id for a in [*actors, *sensors]}
                report['cleanup_actor_checks'] = []
                for attempt in range(4):
                    remaining = [a.id for a in world.get_actors()]
                    check = {'attempt': attempt, 'world_frame': world.get_snapshot().frame,
                        'actor_ids': remaining, 'own_actor_ids_remaining': sorted(set(remaining) & owned)}
                    report['cleanup_actor_checks'].append(check)
                    if not check['own_actor_ids_remaining'] or attempt == 3:
                        break
                    if original_settings.synchronous_mode:
                        break
                    # Read-only wait for the restored async world; never tick it.
                    remaining_wall = cleanup_deadline-time.monotonic()
                    if remaining_wall <= 0:
                        break
                    try:
                        observed = world.wait_for_tick(seconds=min(2., rpc_timeout, remaining_wall))
                        check['waited_frame'] = observed.frame if observed is not None else None
                    except Exception as error:
                        check['wait_error'] = str(error)[:300]
                report['final_state'] = {'settings': settings, 'weather': weather, **report['cleanup_actor_checks'][-1]}
                if report['final_state']['own_actor_ids_remaining']:
                    raise RuntimeError('Owned actor IDs remain after bounded asynchronous readback waits')
                if settings != report['original_settings'] or weather != report['original_weather']:
                    raise RuntimeError('Original settings or weather readback differs after restoration')
            cleanup('verify_original_state', final_state)
        for stream in (raw_stream, xyz_stream):
            if stream is not None:
                cleanup('close_' + Path(stream.name).name, stream.close)
        for name, size, digest in (('raw-lidar-xyzi.bin', raw_size, raw_digest), ('consumed-ego-xyz.bin', xyz_size, xyz_digest)):
            if (directory/name).exists():
                report['files'].append({'name': name, 'size_bytes': size, 'sha256': digest.hexdigest()})
        try:
            raw = live_stack_recording(messages, metadata={'diagnostic': True, 'trial': trial,
                'request_sha256': report.get('request_sha256'), 'context_hash': report.get('context_hash'),
                'status': report['status'], 'scope': 'Standalone diagnostic original source prefix; not a platform run'})
            (directory/'sources.mcap').write_bytes(raw)
            report['files'].append({'name': 'sources.mcap', 'size_bytes': len(raw), 'sha256': sha(raw)})
        except BaseException as error:
            report['source_export_error'] = str(error)[:500]
            report['status'] = 'failed'
        if any(item['status'] == 'failed' for item in report['cleanup']):
            report['status'] = 'failed'
        report['total_elapsed_wall_s'] = time.monotonic()-started
        report['actual_commands'] = len(report['commands'])
        report['acknowledged_commands'] = sum(row['application_status'] in ('acknowledged_without_next_observation', 'next_frame_observed') for row in report['commands'])
        report['actual_measurement_observations'] = sum(row['phase'] == 'measurement' for row in report['observations'])
        checkpoint(directory, report)
        (directory/'report.sha256').write_text(sha((directory/'report.json').read_bytes())+'  report.json\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True, help='Immutable 0.4.0 SimulationExecutionRequest JSON')
    parser.add_argument('--output-dir', type=Path, required=True, help='New directory; existing paths are refused')
    parser.add_argument('--trial', choices=TRIALS, default='configure_after_spawn', help='Exactly one trial, no automatic retry')
    parser.add_argument('--wall-timeout', type=float, default=120)
    parser.add_argument('--rpc-timeout', type=float, default=10)
    args = parser.parse_args(argv)
    report = diagnose(args.request, args.output_dir, trial=args.trial, wall_timeout=args.wall_timeout, rpc_timeout=args.rpc_timeout)
    print(json.dumps({'status': report['status'], 'trial': args.trial, 'report': str((args.output_dir/'report.json').resolve()),
        'actual_commands': report['actual_commands'], 'measurement_observations': report['actual_measurement_observations']}, allow_nan=False))
    return 0 if report['status'] == 'completed' else 1


if __name__ == '__main__':
    sys.exit(main())
