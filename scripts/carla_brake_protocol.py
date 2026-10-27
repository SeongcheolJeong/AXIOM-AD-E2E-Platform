"""Versioned, simulator-independent inputs for bounded CARLA plant diagnostics.

This protocol issues no engine operations. Its preparation controller is an
experimental condition, not a production driving policy or calibrated model.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
import statistics
import uuid

VERSION = 'carla-brake-protocol/2'
LEGACY_VERSION = 'carla-brake-protocol/1'
SETTLED_VERSION = 'carla-brake-protocol/3'
EXECUTABLE_VERSIONS = (VERSION, SETTLED_VERSION)
INTENT_VERSION = 'carla-brake-intent/1'
REPORT_VERSION = 'carla-brake-diagnostic/1'
DT = .05
STEPS = 80
BRAKE_LEVELS = (0., .025, .05, .1, .2, .35, .6, 1.)
POLICY = {
    'dt_s': DT, 'measurement_steps': STEPS, 'target_speed_mps': 2.,
    'speed_window_mps': [1.9, 2.1], 'pulse_start_step': 5, 'pulse_steps': 5,
    'rest_min_ticks': 8, 'rest_max_ticks': 100, 'rest_stable_samples': 5,
    'rest_speed_mps': .03, 'rest_acceleration_mps2': .1,
    'rest_control': {'throttle': 0., 'brake': 0., 'steer': 0., 'hand_brake': False,
                     'reverse': False, 'manual_gear_shift': False, 'gear': 1},
    'preparation_max_ticks': 300, 'preparation_stable_samples': 5,
    'preparation_throttle_gain': .4, 'preparation_max_throttle': .35,
    'preparation_brake': 0., 'automatic_forward_gear_request': 1,
    'main_wall_seconds': 600., 'cleanup_wall_seconds': 40.,
    'rpc_seconds': 5., 'callback_drain_seconds': 5.,
    'maximum_distance_from_spawn_m': 30., 'maximum_lateral_distance_m': 1.,
    'maximum_heading_error_deg': 5., 'maximum_speed_mps': 3.,
    'stop_absolute_speed_mps': .03, 'stop_consecutive_samples': 5,
    'collision_observer': True, 'lidar_sensor': False, 'lead_vehicle': False,
    'tire_friction': .8, 'vehicle_blueprint': 'vehicle.tesla.model3',
}
LEGACY_POLICY = deepcopy(POLICY)
POLICY.update({
    'maximum_rest_horizontal_speed_mps': .3,
    'maximum_rest_vertical_speed_mps': 4.5,
    'maximum_rest_vertical_displacement_m': 1.25,
    'maximum_rest_horizontal_displacement_m': .5,
})
SETTLED_POLICY = deepcopy(POLICY)
SETTLED_POLICY.pop('preparation_throttle_gain')
SETTLED_POLICY.update({
    'pulse_start_step': 0,
    'preparation_controller': 'bounded-speed-pi/1',
    'preparation_proportional_gain': .08,
    'preparation_integral_gain': .05,
    'preparation_throttle_slew_per_second': .1,
    'preparation_stable_samples': 20,
    'preparation_speed_window_mps': [1.95, 2.05],
    'preparation_max_heading_acceleration_mps2': .1,
    'preparation_max_interval_acceleration_mps2': .1,
    'preparation_max_vertical_speed_mps': .01,
    'preparation_max_angular_speed_deg_s': 1.,
    'preparation_max_throttle_span': .002,
    'preparation_max_raw_state_relative_span': .02,
    'raw_state_minimum_absolute_median': 1e-6,
    'entry_progress_m': 12.,
    'entry_max_overshoot_m': .11,
    'pair_max_position_distance_m': .015,
    'pair_max_velocity_distance_mps': .005,
    'pair_max_rotation_difference_deg': .05,
    'pair_max_cached_throttle_difference': .0005,
    'pair_max_heading_acceleration_difference_mps2': .02,
    'pair_max_angular_velocity_distance_deg_s': .05,
    'pair_max_raw_state_relative_difference': .02,
    'pair_require_equal_preparation_command_count': True,
})
POLICIES = {LEGACY_VERSION: LEGACY_POLICY, VERSION: POLICY, SETTLED_VERSION: SETTLED_POLICY}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value, label):
    require(type(value) in (int, float) and math.isfinite(value), label + ' must be finite numeric')
    return float(value)


def exact_keys(value, keys, label):
    require(type(value) is dict and set(value) == set(keys), label + ' fields differ')


def parse_intent(raw):
    require(type(raw) is bytes and len(raw) <= 128 * 1024, 'Intent byte budget exceeded')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    def invalid(token):
        raise ValueError('Nonfinite JSON token: ' + token)
    return validate_intent(json.loads(raw, object_pairs_hook=unique, parse_constant=invalid))


def validate_protocol(value):
    exact_keys(value, ('schema_version', 'initialization', 'measurement', 'brake_level',
                       'repeat_index', 'seed', 'policy'), 'Protocol')
    require(type(value['schema_version']) is str and value['schema_version'] in POLICIES,
            'Unsupported brake protocol version')
    require(value['initialization'] in ('velocity_injection', 'throttle'), 'Unsupported initialization')
    require(value['schema_version'] != SETTLED_VERSION or value['initialization'] == 'throttle',
            'Settled road-entry protocol requires physical throttle preparation')
    require(value['measurement'] in ('coast', 'pulse'), 'Unsupported measurement')
    brake = finite(value['brake_level'], 'brake_level')
    require(brake in BRAKE_LEVELS, 'Unsupported brake screening level')
    require(value['measurement'] != 'coast' or brake == 0., 'Coast must have brake zero')
    require(type(value['repeat_index']) is int and 1 <= value['repeat_index'] <= 100, 'Invalid repeat index')
    require(type(value['seed']) is int and 0 <= value['seed'] < 2**32, 'Invalid provenance seed')
    # Canonical comparison rejects boolean-for-number substitutions as well.
    expected = POLICIES[value['schema_version']]
    require(canonical(value['policy']) == canonical(expected), 'Versioned policy must be pinned exactly')
    return deepcopy(value)


def make_protocol(initialization, *, measurement='coast', brake_level=0., repeat_index=1, seed=42, version=VERSION):
    finite(brake_level, 'brake_level')
    require(type(version) is str and version in POLICIES, 'Unsupported brake protocol version')
    return validate_protocol({'schema_version': version, 'initialization': initialization,
        'measurement': measurement, 'brake_level': float(brake_level),
        'repeat_index': repeat_index, 'seed': seed,
        'policy': deepcopy(POLICIES[version])})


def control(*, throttle=0., brake=0., hand_brake=False, gear=1):
    return {'throttle': float(throttle), 'brake': float(brake), 'steer': 0.,
            'hand_brake': hand_brake, 'reverse': False, 'manual_gear_shift': False, 'gear': gear}


def measurement_sequence(protocol):
    protocol = validate_protocol(protocol)
    start = protocol['policy']['pulse_start_step']
    end = start + protocol['policy']['pulse_steps']
    return [{'step': i, 'time_s': i * DT, 'control': control(brake=(protocol['brake_level']
        if protocol['measurement'] == 'pulse' and start <= i < end else 0.))} for i in range(STEPS)]


def preparation_control(protocol, signed_speed_mps):
    protocol = validate_protocol(protocol)
    require(protocol['schema_version'] != SETTLED_VERSION, 'Settled preparation requires its explicit PI state')
    require(protocol['initialization'] == 'throttle', 'Throttle preparation not selected')
    speed = finite(signed_speed_mps, 'signed_speed_mps')
    return control(throttle=min(.35, max(0., .4 * (2. - speed))))


def speed_in_window(speed):
    return 1.9 <= finite(speed, 'signed_speed_mps') <= 2.1


def _settled_sample(row):
    """Read only the declared native fields; do not trust cached derived values."""
    frame = row['world_frame']
    require(type(frame) is int and frame > 0, 'Invalid preparation world frame')
    source_time = finite(row['source_time_s'], 'Preparation source time')
    ego = row['ego']
    yaw = finite(ego['rotation_deg']['yaw'], 'Preparation yaw')
    speed = signed_heading(ego['velocity_mps'], yaw)
    acceleration = signed_heading(ego['acceleration_mps2'], yaw)
    angular = ego['angular_velocity_deg_s']
    exact_keys(angular, ('x', 'y', 'z'), 'Preparation angular velocity')
    readback = row.get('reported_control') or {}
    require(readback.get('status') == 'available' and
            readback.get('before_world_frame') == frame == readback.get('after_world_frame'),
            'Preparation control source unavailable or incoherent')
    cached = readback['data']
    require(type(cached.get('gear')) is int, 'Preparation cached gear unavailable')
    throttle = finite(cached.get('throttle'), 'Preparation cached throttle')
    require(0. <= throttle <= .35, 'Preparation cached throttle outside declared bound')
    return {'frame': frame, 'time': source_time, 'speed': speed,
            'acceleration': acceleration, 'vertical_speed': finite(ego['velocity_mps']['z'], 'Vertical speed'),
            'angular_speed': math.hypot(*(finite(angular[k], k) for k in ('x', 'y', 'z'))),
            'gear': cached['gear'], 'throttle': throttle}


def settled_preparation_step(protocol, observation, state=None):
    """One explicit PI command from an observed source frame, with anti-windup.

    The first request uses one declared actuation horizon for slew limiting and
    integrates no elapsed time. Subsequent requests use the actual source delta.
    These are experimental controller constants, not an identified plant model.
    """
    protocol = validate_protocol(protocol)
    require(protocol['schema_version'] == SETTLED_VERSION, 'PI state requires protocol3')
    policy = protocol['policy']
    sample = _settled_sample(observation)
    before = {'step': 0, 'integral': 0., 'previous_throttle': 0.,
              'previous_source_time_s': None, 'previous_world_frame': None} if state is None else deepcopy(state)
    exact_keys(before, ('step', 'integral', 'previous_throttle', 'previous_source_time_s',
                        'previous_world_frame'), 'Preparation PI state')
    require(type(before['step']) is int and 0 <= before['step'] < policy['preparation_max_ticks'], 'Invalid PI step')
    old_i = finite(before['integral'], 'PI integral')
    old_u = finite(before['previous_throttle'], 'Previous PI throttle')
    require(0. <= old_i <= .35 and 0. <= old_u <= .35, 'PI state outside declared bounds')
    if before['step'] == 0:
        require(before == {'step': 0, 'integral': 0., 'previous_throttle': 0.,
                'previous_source_time_s': None, 'previous_world_frame': None}, 'Nonzero initial PI state')
        elapsed = None
    else:
        require(type(before['previous_world_frame']) is int and
                sample['frame'] == before['previous_world_frame'] + 1, 'PI source frame discontinuity')
        elapsed = sample['time'] - finite(before['previous_source_time_s'], 'Previous PI source time')
        require(abs(elapsed - DT) <= 1e-6, 'PI source delta differs from fixed tick')
    horizon = DT if elapsed is None else elapsed
    error = policy['target_speed_mps'] - sample['speed']
    proportional = policy['preparation_proportional_gain'] * error
    integrating = elapsed is not None and sample['gear'] > 0
    candidate = min(.35, max(0., old_i + policy['preparation_integral_gain'] * error * elapsed)) if integrating else old_i
    delta = policy['preparation_throttle_slew_per_second'] * horizon
    lower, upper = max(0., old_u - delta), min(policy['preparation_max_throttle'], old_u + delta)
    desired = proportional + candidate
    saturated_in_error_direction = (error > 0. and desired > upper) or (error < 0. and desired < lower)
    integral = old_i if saturated_in_error_direction else candidate
    throttle = min(upper, max(lower, proportional + integral))
    after = {'step': before['step'] + 1, 'integral': integral, 'previous_throttle': throttle,
             'previous_source_time_s': sample['time'], 'previous_world_frame': sample['frame']}
    trace = {'schema_version': 'carla-preparation-pi-step/1', 'source_world_frame': sample['frame'],
             'source_time_s': sample['time'], 'source_speed_mps': sample['speed'],
             'source_cached_gear': sample['gear'], 'observed_interval_s': elapsed,
             'slew_interval_s': horizon, 'error_mps': error, 'proportional': proportional,
             'candidate_integral': candidate, 'integrator_enabled': integrating,
             'saturated_in_error_direction': saturated_in_error_direction,
             'throttle_lower_bound': lower, 'throttle_upper_bound': upper,
             'state_before': before, 'state_after': after}
    return {'control': control(throttle=throttle), 'state': after, 'trace': trace}


def _raw_rotating_state(row):
    """Relative raw-state observations, not a unit or force interpretation."""
    envelope = row.get('telemetry') or {}
    frame = row['world_frame']
    require(envelope.get('status') == 'available' and
            envelope.get('before_world_frame') == frame == envelope.get('after_world_frame'),
            'Raw rotating state unavailable or incoherent')
    def value(field):
        require(type(field) is dict and field.get('status') == 'available' and field.get('reason') is None,
                'Raw rotating field unavailable')
        return finite(field.get('value'), 'Raw rotating state')
    rpm = value(envelope['scalars']['engine_rpm'])
    require(rpm > 0., 'Raw RPM is not positive')
    wheels = envelope['wheels']
    require(type(wheels) is list and len(wheels) == 4, 'Four raw wheel states required')
    for i, wheel in enumerate(wheels):
        require(type(wheel.get('index')) is int and wheel['index'] == i, 'Raw wheel order differs')
    return [rpm, *(value(wheel['fields']['omega']) for wheel in wheels)]


def settled_preparation_gate(protocol, observations, spawn):
    """First crossing of spawn-heading plane after 20 observed stable intervals.

    Missing optional telemetry is explicitly unassessable, not zero or stable.
    The entire source prefix is considered when detecting an earlier crossing.
    """
    protocol = validate_protocol(protocol)
    require(protocol['schema_version'] == SETTLED_VERSION, 'Road-entry gate requires protocol3')
    policy = protocol['policy']
    require(type(observations) is list and 0 < len(observations) <= 301, 'Invalid preparation observation prefix')
    samples = [_settled_sample(row) for row in observations]
    for left, right in zip(samples, samples[1:]):
        require(right['frame'] == left['frame'] + 1 and abs(right['time'] - left['time'] - DT) <= 1e-6,
                'Preparation gate source discontinuity')
    yaw = math.radians(finite(spawn['rotation_deg']['yaw'], 'Spawn heading'))
    origin = spawn['position_m']
    progress = [(finite(row['ego']['position_m']['x'], 'Position x') - finite(origin['x'], 'Spawn x')) * math.cos(yaw)
              + (finite(row['ego']['position_m']['y'], 'Position y') - finite(origin['y'], 'Spawn y')) * math.sin(yaw)
                for row in observations]
    first_crossing = next((i for i, distance in enumerate(progress) if distance >= policy['entry_progress_m']), None)
    crossed_now = first_crossing == len(samples) - 1 and first_crossing != 0
    reached = first_crossing is not None
    count = policy['preparation_stable_samples'] + 1
    window = samples[-count:]
    full = len(window) == count
    low, high = policy['preparation_speed_window_mps']
    intervals = [(b['speed'] - a['speed']) / (b['time'] - a['time']) for a, b in zip(window, window[1:])]
    gears = sorted({sample['gear'] for sample in window})
    throttle_span = max(s['throttle'] for s in window) - min(s['throttle'] for s in window)
    raw_state, raw_error = None, None
    try:
        raw_state = [_raw_rotating_state(row) for row in observations[-count:]]
        relative_spans = []
        for values in zip(*raw_state):
            median = statistics.median(values)
            require(abs(median) > policy['raw_state_minimum_absolute_median'] and
                    (min(values) > 0. or max(values) < 0.), 'Raw rotating state has zero denominator or changing sign')
            relative_spans.append((max(values) - min(values)) / abs(median))
    except (ValueError, KeyError, TypeError) as error:
        raw_error = str(error)
        relative_spans = None
    criteria = {'complete_20_intervals': full,
        'speed_window': full and all(low <= s['speed'] <= high for s in window),
        'interval_acceleration': full and all(abs(a) <= policy['preparation_max_interval_acceleration_mps2'] for a in intervals),
        'engine_heading_acceleration': full and all(abs(s['acceleration']) <= policy['preparation_max_heading_acceleration_mps2'] for s in window[1:]),
        'vertical_speed': full and all(abs(s['vertical_speed']) <= policy['preparation_max_vertical_speed_mps'] for s in window),
        'angular_speed': full and all(s['angular_speed'] <= policy['preparation_max_angular_speed_deg_s'] for s in window),
        'same_positive_cached_gear': full and len(gears) == 1 and gears[0] > 0,
        'cached_throttle_span': full and throttle_span <= policy['preparation_max_throttle_span'],
        'raw_rotating_state': full and relative_spans is not None and
            all(value <= policy['preparation_max_raw_state_relative_span'] for value in relative_spans)}
    stable = all(criteria.values())
    overshoot = progress[-1] - policy['entry_progress_m'] if reached else None
    admitted = crossed_now and stable and overshoot <= policy['entry_max_overshoot_m']
    return {'schema_version': 'carla-preparation-entry/1', 'world_frame': samples[-1]['frame'],
            'sample_count': len(samples), 'window_frames': [s['frame'] for s in window],
            'window_duration_s': window[-1]['time'] - window[0]['time'],
            'criteria': criteria, 'observed_admission_stable': stable,
            'raw_state_assessment': 'available' if raw_error is None else 'unassessable',
            'raw_state_error': raw_error, 'raw_state_relative_spans': relative_spans,
            'cached_throttle_span': throttle_span, 'cached_gears': gears,
            'interval_accelerations_mps2': intervals,
            'previous_progress_m': progress[-2] if len(progress) > 1 else None,
            'progress_m': progress[-1], 'plane_reached': reached,
            'first_crossing_index': first_crossing, 'crossed_plane_now': crossed_now,
            'overshoot_m': overshoot, 'admitted': admitted}


def signed_heading(vector, yaw_deg):
    exact_keys(vector, ('x', 'y', 'z'), 'Native vector')
    x, y, _ = (finite(vector[k], k) for k in ('x', 'y', 'z'))
    yaw = math.radians(finite(yaw_deg, 'yaw'))
    return x * math.cos(yaw) + y * math.sin(yaw)


def spatial_check(position, rotation, velocity, spawn, *, phase='measurement', protocol=None):
    """Preserve each failing observation; this is a bounded-run guard, not safety proof."""
    for item in (position, velocity):
        exact_keys(item, ('x', 'y', 'z'), 'Native vector')
        for key in item:
            finite(item[key], key)
    exact_keys(rotation, ('pitch', 'yaw', 'roll'), 'Native rotation')
    for key in rotation:
        finite(rotation[key], key)
    require(phase in ('inventory_refresh', 'rest', 'preparation', 'measurement'), 'Unknown diagnostic phase')
    selected = validate_protocol(protocol) if protocol is not None else None
    policy = selected['policy'] if selected is not None else POLICY
    rest_specific = ('maximum_rest_vertical_speed_mps' in policy and phase in ('rest', 'inventory_refresh'))
    origin = spawn['position_m']
    yaw = math.radians(spawn['rotation_deg']['yaw'])
    dx, dy = position['x'] - origin['x'], position['y'] - origin['y']
    distance = math.dist(tuple(position[k] for k in ('x', 'y', 'z')),
                         tuple(origin[k] for k in ('x', 'y', 'z')))
    lateral = -dx * math.sin(yaw) + dy * math.cos(yaw)
    heading_error = (rotation['yaw'] - spawn['rotation_deg']['yaw'] + 180.) % 360. - 180.
    speed = math.hypot(*(velocity[k] for k in ('x', 'y', 'z')))
    values = {'distance_from_spawn_m': distance, 'lateral_distance_m': lateral,
              'heading_error_deg': heading_error, 'speed_norm_mps': speed}
    failures = [name for name, failed in (
        ('distance', distance > 30.), ('lateral', abs(lateral) > 1.),
        ('heading', abs(heading_error) > 5.), ('speed', not rest_specific and speed > 3.)) if failed]
    if rest_specific:
        horizontal = math.hypot(velocity['x'], velocity['y'])
        vertical_displacement = position['z'] - origin['z']
        values.update(horizontal_speed_mps=horizontal, vertical_speed_mps=velocity['z'],
                      vertical_displacement_m=vertical_displacement, horizontal_displacement_m=math.hypot(dx, dy),
                      bounds_policy='rest_settling/2')
        failures.extend(name for name, failed in (
            ('rest_horizontal_speed', horizontal > policy['maximum_rest_horizontal_speed_mps']),
            ('rest_horizontal_displacement', math.hypot(dx, dy) > policy['maximum_rest_horizontal_displacement_m']),
            ('rest_vertical_speed', abs(velocity['z']) > policy['maximum_rest_vertical_speed_mps']),
            ('rest_vertical_displacement', abs(vertical_displacement) > policy['maximum_rest_vertical_displacement_m'])) if failed)
    return {'status': 'within_bounds' if not failures else 'failed', 'failures': failures, **values}


def validate_intent(value):
    exact_keys(value, ('schema_version', 'experiment_id', 'protocol', 'protocol_sha256',
        'sequence_sha256', 'expected_world', 'expected_store_id', 'engine_build',
        'adapter_version', 'spawn_transform', 'requested_weather', 'expected_physics', 'source_files',
        'source_evidence'), 'Intent')
    require(value['schema_version'] == INTENT_VERSION, 'Unsupported intent version')
    require(str(uuid.UUID(value['experiment_id'])) == value['experiment_id'], 'Noncanonical experiment UUID')
    require(str(uuid.UUID(value['expected_store_id'])) == value['expected_store_id'], 'Noncanonical store UUID')
    require(value['engine_build'] == '1ae5356' and value['adapter_version'] == '0.8.0', 'Unsupported native build')
    protocol = validate_protocol(value['protocol'])
    require(sha(canonical(protocol)) == value['protocol_sha256'], 'Protocol hash differs')
    require(sha(canonical(measurement_sequence(protocol))) == value['sequence_sha256'], 'Command sequence hash differs')
    world = value['expected_world']
    exact_keys(world, ('world_id', 'map', 'actors', 'settings', 'weather'), 'Expected world')
    require(type(world['world_id']) is str and world['world_id'].isdigit(), 'World ID must retain decimal precision')
    exact_keys(world['map'], ('name', 'opendrive_sha256', 'opendrive_size_bytes'), 'Map')
    require(world['map']['name'].split('/')[-1] == 'Town10HD_Opt', 'Unsupported map')
    require(type(world['map']['opendrive_sha256']) is str and re.fullmatch('[0-9a-f]{64}', world['map']['opendrive_sha256']), 'Invalid OpenDRIVE hash')
    require(type(world['map']['opendrive_size_bytes']) is int and 0 < world['map']['opendrive_size_bytes'] <= 20*1024*1024, 'Invalid map size')
    require(type(world['actors']) is list and 0 < len(world['actors']) <= 1000, 'Invalid original inventory')
    ids = []
    for actor in world['actors']:
        exact_keys(actor, ('actor_id', 'type_id', 'role_name'), 'Actor identity')
        require(type(actor['actor_id']) is int and actor['actor_id'] > 0, 'Invalid actor ID')
        require(type(actor['type_id']) is str and not actor['type_id'].startswith(('vehicle.', 'sensor.', 'walker.')), 'Existing moving/sensor actor forbids diagnostic')
        require(actor['role_name'] is None or type(actor['role_name']) is str, 'Invalid role name')
        ids.append(actor['actor_id'])
    require(ids == sorted(set(ids)), 'Inventory must be unique and sorted')
    for name, fields in (('settings', world['settings']), ('weather', world['weather']), ('requested_weather', value['requested_weather'])):
        exact_keys(fields, ('values', 'invalid_fields'), name)
        require(type(fields['values']) is dict and bool(fields['values']) and fields['invalid_fields'] == {}, 'Incomplete world fields')
        canonical(fields)
    require(world['settings']['values'].get('synchronous_mode') is False, 'Existing synchronous world owner')
    physics = value['expected_physics']
    exact_keys(physics, ('scalars', 'wheels'), 'Expected physics')
    require(type(physics['scalars']) is dict and physics['scalars'], 'Missing physics scalars')
    require(type(physics['wheels']) is list and len(physics['wheels']) == 4, 'Four wheel configurations required')
    for i, wheel in enumerate(physics['wheels']):
        exact_keys(wheel, ('index', 'fields'), 'Wheel configuration')
        require(type(wheel['index']) is int and wheel['index'] == i, 'Wheel order differs')
        require(type(wheel['fields']) is dict and wheel['fields'], 'Missing wheel fields')
    for fields in [physics['scalars'], *(w['fields'] for w in physics['wheels'])]:
        for field in fields.values():
            exact_keys(field, ('status', 'value', 'reason'), 'Physics value')
            require(field['status'] == 'available' and field['reason'] is None, 'Unavailable pinned physics')
            if type(field['value']) is not bool:
                finite(field['value'], 'Physics value')
    pose = value['spawn_transform']
    exact_keys(pose, ('position_m', 'rotation_deg'), 'Spawn')
    for key, names in (('position_m', ('x', 'y', 'z')), ('rotation_deg', ('pitch', 'yaw', 'roll'))):
        exact_keys(pose[key], names, key)
        for name in names:
            require(abs(finite(pose[key][name], name)) < 100000, 'Spawn coordinate out of bounds')
    require(type(value['source_files']) is dict and set(value['source_files']) == {
        'carla_brake_protocol.py', 'diagnose_carla_brake_response.py', 'diagnose_carla_self_hit.py'}, 'Exact diagnostic source set required')
    for digest in value['source_files'].values():
        require(type(digest) is str and re.fullmatch('[0-9a-f]{64}', digest), 'Invalid source hash')
    exact_keys(value['source_evidence'], ('path', 'sha256'), 'Source evidence')
    require(type(value['source_evidence']['path']) is str and 0 < len(value['source_evidence']['path']) <= 1000, 'Missing source evidence path')
    require(type(value['source_evidence']['sha256']) is str and re.fullmatch('[0-9a-f]{64}', value['source_evidence']['sha256']), 'Invalid source evidence hash')
    return deepcopy(value)
