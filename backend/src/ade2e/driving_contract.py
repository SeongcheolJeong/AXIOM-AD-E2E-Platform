"""Causal recorded driving input contract. No learning framework imports.

This module intentionally supports Python 3.8 for isolated native runners.
Validation is an input check, never a model/road-safety verdict.
"""
import hashlib
import json
import math
from pathlib import PurePosixPath

PROFILE = 'vad-tiny-native/1'
CAMERAS = ('CAM_FRONT', 'CAM_FRONT_RIGHT', 'CAM_FRONT_LEFT', 'CAM_BACK', 'CAM_BACK_LEFT', 'CAM_BACK_RIGHT')
SOURCE_COMMIT = '1688c4b1c3a9e2e7873ca9700ff8058170c0e3c8'


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def content_hash(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def fields(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError('Missing/unknown driving contract fields: ' + ', '.join(expected))


def number(value):
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError('Driving values must be finite numeric values')


def integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('Invalid driving integer')


def text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError('Invalid driving text')


def sha(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('Invalid SHA-256')


def vector(value, length):
    if not isinstance(value, list) or len(value) != length:
        raise ValueError('Invalid driving vector dimensions')
    for element in value:
        number(element)


def matrix(value, length):
    if not isinstance(value, list) or len(value) != length:
        raise ValueError('Invalid driving matrix dimensions')
    for row in value:
        vector(row, length)


def relative_path(value):
    text(value)
    p = PurePosixPath(value)
    if p.is_absolute() or '..' in p.parts or '\\' in value or ':' in value:
        raise ValueError('Source files require bounded relative paths')


def validate_input(value):
    fields(value, ('schema_version', 'profile', 'dataset', 'camera_order', 'frames', 'route_command', 'state_policy'))
    if value['schema_version'] != 'driving-input/1' or value['profile'] != PROFILE:
        raise ValueError('Unsupported driving input profile')
    if value['camera_order'] != list(CAMERAS) or value['state_policy'] != 'reset_then_chronological_prefix':
        raise ValueError('Unsupported camera order/state policy')
    if value['route_command'] not in ('all_modes', 'right', 'left', 'straight'):
        raise ValueError('Use an explicitly declared route command')
    dataset = value['dataset']
    fields(dataset, ('name', 'version', 'scene_token', 'scene_name', 'metadata_sha256', 'use_scope', 'rights_note'))
    if dataset['name'] != 'nuScenes' or dataset['version'] != 'v1.0-mini' or dataset['use_scope'] != 'research_isolated':
        raise ValueError('This connector supports isolated nuScenes mini research only')
    for key in ('scene_token', 'scene_name', 'rights_note'):
        text(dataset[key])
    sha(dataset['metadata_sha256'])
    frames = value['frames']
    if not isinstance(frames, list) or not 3 <= len(frames) <= 16:
        raise ValueError('Require 3–16 real chronological frames; no repeated-frame padding')
    previous, samples, files = None, set(), set()
    for frame in frames:
        fields(frame, ('sample_token', 'previous_sample_token', 'timestamp_us', 'observed_until_us', 'cameras', 'can_bus', 'can_bus_source', 'lidar_frame'))
        text(frame['sample_token'])
        if not isinstance(frame['previous_sample_token'], str):
            raise ValueError('Invalid previous sample identity')
        if previous:
            text(frame['previous_sample_token'])
        integer(frame['timestamp_us'], 1); integer(frame['observed_until_us'], 1)
        if frame['sample_token'] in samples:
            raise ValueError('Repeated source sample')
        samples.add(frame['sample_token'])
        if previous and (frame['previous_sample_token'] != previous['sample_token'] or frame['timestamp_us'] <= previous['timestamp_us'] or frame['observed_until_us'] <= previous['observed_until_us']):
            raise ValueError('Discontinuous or unordered driving sequence')
        if frame['timestamp_us'] > frame['observed_until_us']:
            raise ValueError('Anchor is later than observation cutoff')
        if not isinstance(frame['cameras'], list) or len(frame['cameras']) != 6:
            raise ValueError('All six real camera inputs are required')
        for name, camera in zip(CAMERAS, frame['cameras']):
            fields(camera, ('name', 'sample_data_token', 'path', 'sha256', 'timestamp_us', 'width', 'height', 'lidar2img'))
            if camera['name'] != name:
                raise ValueError('Camera order differs from checkpoint contract')
            text(camera['sample_data_token']); relative_path(camera['path']); sha(camera['sha256'])
            integer(camera['timestamp_us'], 1); integer(camera['width'], 1); integer(camera['height'], 1)
            if camera['timestamp_us'] > frame['observed_until_us'] or abs(camera['timestamp_us']-frame['timestamp_us']) > 100000:
                raise ValueError('Camera observation is future or exceeds 100ms alignment limit')
            if camera['sample_data_token'] in files:
                raise ValueError('Repeated camera observation')
            files.add(camera['sample_data_token'])
            matrix(camera['lidar2img'], 4)
        vector(frame['can_bus'], 18)
        source = frame['can_bus_source']
        fields(source, ('path', 'sha256', 'timestamp_us', 'policy'))
        relative_path(source['path']); sha(source['sha256']); integer(source['timestamp_us'], 1)
        if source['policy'] != 'latest_at_or_before_anchor' or not 0 <= frame['timestamp_us']-source['timestamp_us'] <= 100000:
            raise ValueError('CAN input must be observed, causal and at most 100ms old; no zero fallback')
        reference = frame['lidar_frame']
        fields(reference, ('sample_data_token', 'calibration_token', 'ego_pose_token', 'ego2global', 'lidar2ego'))
        for key in ('sample_data_token', 'calibration_token', 'ego_pose_token'):
            text(reference[key])
        for key in ('ego2global', 'lidar2ego'):
            matrix(reference[key], 4)
            if reference[key][3] != [0, 0, 0, 1]:
                raise ValueError('Invalid homogeneous reference transform')
        previous = frame
    return value


def preflight(value):
    validate_input(value)
    return {'schema_version': 'driving-preflight/1', 'input_hash': content_hash(value),
        'structural_status': 'ready', 'native_execution_status': 'not_checked',
        'file_identity_status': 'requires_native_file_check', 'frames': len(value['frames']),
        'camera_observations': 6*len(value['frames']), 'profile': PROFILE,
        'capabilities': ['recorded_multicamera', 'temporal_bev', 'three_trajectory_modes'],
        'unavailable': ['closed_loop', 'safety_verdict', 'commercial_data_rights'],
        'limitations': ['Input checks do not execute or qualify the model.',
            'No missing camera/CAN fallback, no future-label model input.',
            'The selected Tiny configuration does not use its optional ego-history/LCF encoders.',
            'Native coordinate frame is current nuScenes LiDAR XY in meters, not global/map coordinates.']}


def validate_result(value):
    fields(value, ('schema_version', 'input', 'input_hash', 'model', 'execution', 'outputs', 'limitations'))
    if value['schema_version'] != 'driving-result/1':
        raise ValueError('Unsupported driving result')
    validate_input(value['input'])
    if content_hash(value['input']) != value['input_hash']:
        raise ValueError('Driving result input hash mismatch')
    model = value['model']
    fields(model, ('source_commit', 'source_files_hash', 'adapter_source_hash', 'checkpoint_sha256', 'config_sha256', 'resolved_config_hash', 'adapter_version', 'profile', 'weights_rights'))
    if model['source_commit'] != SOURCE_COMMIT or model['profile'] != PROFILE or model['adapter_version'] not in ('axiom-vad-native/1', 'axiom-vad-native/2'):
        raise ValueError('Unsupported native source/model contract')
    for key in ('source_files_hash', 'adapter_source_hash', 'checkpoint_sha256', 'config_sha256', 'resolved_config_hash'):
        sha(model[key])
    text(model['weights_rights'])
    execution = value['execution']
    fields(execution, ('status', 'engine', 'device', 'library_versions', 'seed', 'elapsed_ms', 'peak_cuda_bytes', 'ground_truth_in_model_input'))
    if execution['status'] != 'completed' or execution['engine'] != 'native_torch' or execution['ground_truth_in_model_input'] is not False:
        raise ValueError('Only completed native prediction receipts without GT input are supported')
    text(execution['device']); integer(execution['seed']); number(execution['elapsed_ms']); integer(execution['peak_cuda_bytes'])
    if execution['elapsed_ms'] < 0 or not isinstance(execution['library_versions'], dict) or not execution['library_versions']:
        raise ValueError('Invalid execution provenance')
    for key, version in execution['library_versions'].items():
        text(key); text(version)
    outputs = value['outputs']
    if not isinstance(outputs, list) or len(outputs) != len(value['input']['frames']):
        raise ValueError('Preserve every input frame in the output denominator')
    for frame, output in zip(value['input']['frames'], outputs):
        fields(output, ('sample_token', 'timestamp_us', 'coordinate_frame', 'step_seconds', 'modes'))
        if output['sample_token'] != frame['sample_token'] or output['timestamp_us'] != frame['timestamp_us'] or output['coordinate_frame'] != 'current_lidar_xy_m' or output['step_seconds'] != 0.5:
            raise ValueError('Driving output identity/coordinate mismatch')
        fields(output['modes'], ('right', 'left', 'straight'))
        for trajectory in output['modes'].values():
            if not isinstance(trajectory, list) or len(trajectory) != 6:
                raise ValueError('Require six predicted points per mode')
            for point in trajectory:
                vector(point, 2)
    if not isinstance(value['limitations'], list) or not value['limitations']:
        raise ValueError('Driving results require explicit fidelity limitations')
    for limitation in value['limitations']:
        text(limitation)
    return value
