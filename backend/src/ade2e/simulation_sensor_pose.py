"""Pure coordinate/readback contract; no engine, database or network imports.

Readback tolerances qualify the declared rigid transform, not sensor calibration.
Original raw point bytes and their exact consumption arithmetic use no tolerance.
"""
from __future__ import annotations

import math
import struct

POLICY = 'carla-rigid-mount-readback/1'
FLOAT32_EPSILON = 2 ** -23
ROTATION_TOLERANCE = 2e-5
SPAWN_ROTATION_TOLERANCE = 2e-6


def f32(value):
    return struct.unpack('<f', struct.pack('<f', float(value)))[0]


def matrix(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(not isinstance(row, (list, tuple)) or len(row) != 4 for row in value)):
        raise ValueError('Mount matrix must be 4 by 4')
    if any(type(v) not in (int, float) or not math.isfinite(v) for row in value for v in row):
        raise ValueError('Mount matrix must be finite numeric data')
    result = [[float(v) for v in row] for row in value]
    if result[3] != [0, 0, 0, 1]:
        raise ValueError('Mount matrix must have a homogeneous last row')
    gram = max(abs(sum(result[k][i]*result[k][j] for k in range(3))-(i == j))
               for i in range(3) for j in range(3))
    a, b, c = result[:3]
    det = a[0]*(b[1]*c[2]-b[2]*c[1])-a[1]*(b[0]*c[2]-b[2]*c[0])+a[2]*(b[0]*c[1]-b[1]*c[0])
    if gram > ROTATION_TOLERANCE or abs(det-1) > 2*ROTATION_TOLERANCE:
        raise ValueError('Mount matrix must contain a proper rigid rotation')
    if max(abs(row[3]) for row in result[:3]) > 1_000_000:
        raise ValueError('Mount matrix position exceeds the numeric domain')
    return result


def multiply(a, b):
    return [[math.fsum(a[i][k]*b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def inverse_rigid(a):
    return [[a[j][i] for j in range(3)] + [-math.fsum(a[k][i]*a[k][3] for k in range(3))]
            for i in range(3)] + [[0.0, 0.0, 0.0, 1.0]]


def reflect(a):
    signs = (1, -1, 1, 1)
    return [[a[i][j]*signs[i]*signs[j] for j in range(4)] for i in range(4)]


def declared_matrix(mount):
    if hasattr(mount, 'model_dump'):
        mount = mount.model_dump(mode='json')
    x, y, z, w = mount['rotation_xyzw']
    dx, dy, dz = mount['translation_m']
    return matrix([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w), dx],
                   [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w), dy],
                   [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y), dz],
                   [0, 0, 0, 1]])


def residual(a, b):
    return (max(abs(a[i][3]-b[i][3]) for i in range(3)),
            max(abs(a[i][j]-b[i][j]) for i in range(3) for j in range(3)))


def native_rotation_matrix(angles, location):
    """CARLA native convention expressed as Rz(yaw) Ry(-pitch) Rx(-roll)."""
    p, y, r = (math.radians(angles[key]) for key in ('pitch', 'yaw', 'roll'))
    cp, sp, cy, sy, cr, sr = math.cos(p), math.sin(p), math.cos(y), math.sin(y), math.cos(r), math.sin(r)
    rz = [[cy,-sy,0,0],[sy,cy,0,0],[0,0,1,0],[0,0,0,1]]
    ry = [[cp,0,-sp,0],[0,1,0,0],[sp,0,cp,0],[0,0,0,1]]
    rx = [[1,0,0,0],[0,cr,sr,0],[0,-sr,cr,0],[0,0,0,1]]
    result = multiply(multiply(rz,ry),rx)
    for i in range(3):
        result[i][3] = location[i]
    return result


def translation_tolerance(*world_matrices):
    # 16 float32 epsilon units across the observed coordinate scale plus a
    # 20-micrometre floor. Frozen before native trials. Far-from-origin error
    # grows explicitly; this is not a hidden fixed calibration accuracy claim.
    scale = max(1.0, *(sum(abs(row[3]) for row in value[:3]) for value in world_matrices))
    return 2e-5 + 16 * FLOAT32_EPSILON * scale


def typed_attributes(attributes):
    result = {}
    for name, value in attributes.items():
        if type(name) is not str or type(value) is not str or len(value) > 128:
            raise ValueError('Sensor actor attributes must be bounded original strings')
        integer = name in {'channels', 'points_per_second', 'noise_seed'}
        numeric = int(value) if integer else f32(value)
        if not math.isfinite(numeric):
            raise ValueError('Sensor attribute is nonfinite')
        result[name] = {'type': 'int' if integer else 'float32', 'value': numeric}
    return result


def mount_row_analysis(row, mount):
    """Recompute qualification from original fields, without trusting residuals."""
    ego = matrix(row['ego_world_matrix'])
    sensor = matrix(row['sensor_world_matrix'])
    api = matrix(row['sensor_api_world_matrix'])
    relative = multiply(inverse_rigid(ego), sensor)
    rh = reflect(relative)
    requested = declared_matrix(mount)
    translation, rotation = residual(rh, requested)
    tol = translation_tolerance(ego, sensor)
    api_t, api_r = residual(api, sensor)
    measurement_t = measurement_r = None
    if row['measurement_world_matrix'] is not None:
        measurement_t, measurement_r = residual(matrix(row['measurement_world_matrix']), sensor)
    reasons = []
    if tol > .01:
        reasons.append('world_coordinate_precision_exceeds_one_centimetre_mount_budget')
    if (any(type(row[k]) is not int for k in ('world_frame','bracket_before_frame','bracket_after_frame')) or row['world_frame'] < 0
            or row['bracket_before_frame'] != row['world_frame'] or row['bracket_after_frame'] != row['world_frame']):
        reasons.append('world_frame_bracket_mismatch')
    if (type(row['sensor_actor_id']) is not int or row['sensor_actor_id'] <= 0
            or type(row['ego_actor_id']) is not int or row['ego_actor_id'] <= 0
            or row['sensor_actor_id'] == row['ego_actor_id']
            or type(row['parent_actor_id']) is not int or row['parent_actor_id'] != row['ego_actor_id']
            or row['sensor_type_id'] != 'sensor.lidar.ray_cast'):
        reasons.append('sensor_identity_or_parent_mismatch')
    if translation > tol or rotation > ROTATION_TOLERANCE:
        reasons.append('declared_mount_mismatch')
    if api_t > tol or api_r > ROTATION_TOLERANCE:
        reasons.append('sensor_api_snapshot_pose_mismatch')
    if measurement_t is not None:
        if (type(row['measurement_frame']) is not int or row['measurement_frame'] != row['world_frame']
                or type(row['measurement_time_s']) not in (int,float) or row['measurement_time_s'] != row['source_time_s']
                or measurement_t > tol or measurement_r > ROTATION_TOLERANCE):
            reasons.append('measurement_snapshot_pose_or_time_mismatch')
    elif row['measurement_frame'] is not None or row['measurement_time_s'] is not None:
        reasons.append('incomplete_measurement_pose')
    return {'relative_native_matrix': relative, 'relative_rh_matrix': rh,
            'translation_residual_m': translation, 'rotation_residual': rotation,
            'translation_tolerance_m': tol, 'rotation_tolerance': ROTATION_TOLERANCE,
            'api_translation_residual_m': api_t, 'api_rotation_residual': api_r,
            'measurement_translation_residual_m': measurement_t, 'measurement_rotation_residual': measurement_r,
            'qualified': not reasons, 'reasons': reasons}


ROW_FIELDS = {'world_frame', 'source_time_s', 'bracket_before_frame', 'bracket_after_frame',
              'sensor_actor_id', 'ego_actor_id', 'parent_actor_id', 'sensor_type_id',
              'ego_world_matrix', 'sensor_world_matrix', 'sensor_api_world_matrix',
              'measurement_world_matrix', 'measurement_frame', 'measurement_time_s', 'analysis'}


def validate_sensor_configuration_evidence(value, context, identity, *, completed, source_frames):
    """Check the new artifact, including every retained source frame's pose.

    Partial evidence can retain a rejected mount observation. Such an observation
    must never have reached the consumer source frames or an actuator command.
    """
    expected_keys = {'schema_version', *identity, 'status', 'failure_phase', 'error', 'sensor_frame',
                     'configuration_manifest_hash', 'rig_manifest_hash', 'sensor_profile_hash', 'policy',
                     'requested', 'spawn', 'detached_attributes', 'actor_attributes', 'startup_mount',
                     'observations', 'callback_drain'}
    if set(value) != expected_keys or value['schema_version'] != 'carla-sensor-configuration/1':
        raise ValueError('Configured sensor evidence schema mismatch')
    if any(value[k] != v for k, v in identity.items()):
        raise ValueError('Configured sensor evidence correlation mismatch')
    profile = context.sensor_profile
    for key, expected in {'status': 'completed' if completed else 'partial', 'sensor_frame': profile.sensor_frame,
            'configuration_manifest_hash': context.sensor_configuration_snapshot.manifest_hash,
            'rig_manifest_hash': context.rig_snapshot.manifest_hash, 'sensor_profile_hash': profile.profile_hash,
            'policy': POLICY, 'requested': {'attributes': profile.attributes, 'mount': profile.mount.model_dump(mode='json')}}.items():
        if value[key] != expected:
            raise ValueError('Configured sensor evidence differs from pinned ' + key)
    wanted = typed_attributes(profile.attributes)
    detached = value['detached_attributes']
    if not isinstance(detached, dict) or any(k not in wanted or v != wanted[k] for k, v in detached.items()):
        raise ValueError('Configured sensor detached readback differs')
    actor = value['actor_attributes']
    if actor is not None:
        if set(actor) != {'raw', 'typed', 'qualified'} or set(actor['raw']) != set(wanted):
            raise ValueError('Configured sensor actor attribute fields differ')
        actual = typed_attributes(actor['raw'])
        if actor['typed'] != actual or actor['qualified'] is not (actual == wanted):
            raise ValueError('Configured sensor actor readback qualification differs')
    spawn = value['spawn']
    if spawn is not None:
        if set(spawn) != {'policy', 'declared_rh_matrix', 'requested_native_matrix', 'native_spawn_matrix', 'native_location_m', 'native_rotation_deg'}:
            raise ValueError('Configured sensor spawn fields differ')
        requested = declared_matrix(profile.mount)
        if (spawn['policy'] != 'carla_rh_rig_to_lh_native_matrix/1'
                or spawn['declared_rh_matrix'] != requested or spawn['requested_native_matrix'] != reflect(requested)):
            raise ValueError('Configured sensor requested spawn differs')
        native = matrix(spawn['native_spawn_matrix'])
        dt, dr = residual(native, spawn['requested_native_matrix'])
        if dt > 1e-6 or dr > SPAWN_ROTATION_TOLERANCE:
            raise ValueError('Configured sensor native spawn conversion differs')
        if spawn['native_location_m'] != [native[i][3] for i in range(3)]:
            raise ValueError('Configured sensor native location differs')
        angles = spawn['native_rotation_deg']
        if set(angles) != {'pitch', 'yaw', 'roll'} or any(type(v) not in (int,float) or not math.isfinite(v) for v in angles.values()):
            raise ValueError('Configured sensor native rotation is invalid')
        if residual(native, native_rotation_matrix(angles, spawn['native_location_m']))[1] > SPAWN_ROTATION_TOLERANCE:
            raise ValueError('Configured sensor spawn angles and matrix differ')
    rows = value['observations']
    if not isinstance(rows, list) or len(rows) > 81:
        raise ValueError('Configured sensor observation denominator exceeds its bound')
    all_rows = ([value['startup_mount']] if value['startup_mount'] is not None else []) + rows
    for row in all_rows:
        if set(row) != ROW_FIELDS or type(row['source_time_s']) not in (int,float) or not math.isfinite(row['source_time_s']) or row['source_time_s'] < 0:
            raise ValueError('Configured sensor mount observation fields differ')
        if row['analysis'] != mount_row_analysis(row, profile.mount):
            raise ValueError('Configured sensor mount analysis differs from original matrices')
    startup = value['startup_mount']
    if startup is not None and startup['measurement_world_matrix'] is not None:
        raise ValueError('Startup must precede listener measurements')
    frames = [row['world_frame'] for row in rows]
    if len(frames) != len(set(frames)) or frames != sorted(frames):
        raise ValueError('Configured sensor mount frames are not an ordered unique prefix')
    retained = list(source_frames)
    if frames[:len(retained)] != retained or (completed and frames != retained):
        raise ValueError('Configured sensor mount observations differ from retained sources')
    accepted = rows[:len(retained)]
    if any(not row['analysis']['qualified'] or row['measurement_world_matrix'] is None for row in accepted):
        raise ValueError('Rejected or missing sensor pose reached retained sources')
    if startup is not None:
        if any((row['sensor_actor_id'],row['ego_actor_id']) != (startup['sensor_actor_id'],startup['ego_actor_id']) for row in rows):
            raise ValueError('Configured sensor identity changed during the run')
        if rows and startup['world_frame'] >= rows[0]['world_frame']:
            raise ValueError('Sensor startup frame must precede retained measurements')
    if retained or completed:
        if (spawn is None or detached != wanted or actor is None or not actor['qualified']
                or startup is None or not startup['analysis']['qualified']):
            raise ValueError('Configured sensor was consumed before complete physical readback')
    drain = value['callback_drain']
    if set(drain) != {'accepting','started','finished','active','rejected_after_stop','drained'}:
        raise ValueError('Configured sensor callback drain fields differ')
    if any(type(drain[k]) is not int or drain[k] < 0 for k in ('started','finished','active','rejected_after_stop')) or drain['active'] != drain['started']-drain['finished']:
        raise ValueError('Configured sensor callback counts differ')
    if type(drain['accepting']) is not bool or type(drain['drained']) is not bool:
        raise ValueError('Configured sensor callback states are invalid')
    if drain['drained'] and (drain['accepting'] or drain['active']):
        raise ValueError('Configured sensor callback drain claim is inconsistent')
    if completed and (value['failure_phase'] is not None or value['error'] is not None or not drain['drained']):
        raise ValueError('Completed configured sensor evidence lacks terminal readbacks')
    return value


def initial_sensor_configuration_evidence(request):
    """Pinned initial state; no detached, actor or measurement readback claimed.

    A durable reader may reconstruct this state only when no checkpoint exists
    and the original attempt records that native mutations never started.
    """
    from .simulation_context import execution_correlation
    context = request.context
    if context.schema_version != 'simulation-control-context/2':
        raise ValueError('Initial configured sensor evidence requires context/2')
    profile = context.sensor_profile
    return {
        'schema_version': 'carla-sensor-configuration/1', **execution_correlation(request),
        'status': 'partial', 'failure_phase': 'before_native_entry',
        'error': 'Native sensor observations are not_observed; no native lifecycle evidence was recorded.',
        'sensor_frame': profile.sensor_frame,
        'configuration_manifest_hash': context.sensor_configuration_snapshot.manifest_hash,
        'rig_manifest_hash': context.rig_snapshot.manifest_hash, 'sensor_profile_hash': profile.profile_hash,
        'policy': POLICY, 'requested': {'attributes': dict(profile.attributes),
                                     'mount': profile.mount.model_dump(mode='json')},
        'spawn': None, 'detached_attributes': {}, 'actor_attributes': None,
        'startup_mount': None, 'observations': [],
        'callback_drain': {'accepting': True, 'started': 0, 'finished': 0, 'active': 0,
                           'rejected_after_stop': 0, 'drained': False},
    }
