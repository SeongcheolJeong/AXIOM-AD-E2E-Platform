"""Read original nuScenes JSON without pickles, GT, map annotations or devkit imports."""
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

from ade2e.driving_contract import CAMERAS, PROFILE, content_hash, validate_input


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def bounded_file(root, relative):
    root = Path(root).resolve()
    value = (root/relative).resolve()
    try:
        value.relative_to(root)
    except ValueError:
        raise ValueError('Input path escapes selected data root')
    if not value.is_file():
        raise ValueError('Missing required input file: ' + str(relative))
    return value


def transform(record):
    q = np.asarray(record['rotation'], dtype=np.float64)
    if q.shape != (4,) or not np.isfinite(q).all() or not np.isclose(np.linalg.norm(q), 1, atol=1e-5):
        raise ValueError('Invalid wxyz unit quaternion')
    w,x,y,z = q
    rotation = np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
        [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
        [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])
    translation = np.asarray(record['translation'], dtype=np.float64)
    if translation.shape != (3,) or not np.isfinite(translation).all():
        raise ValueError('Invalid pose translation')
    matrix = np.eye(4); matrix[:3,:3] = rotation; matrix[:3,3] = translation
    return matrix


def export_input(root, scene_token, target_sample_token, *, frame_count=3, route_command='all_modes', rights_note):
    root = Path(root).resolve()
    if type(frame_count) is not int or not 3 <= frame_count <= 16:
        raise ValueError('Select 3–16 real frames')
    tables, table_hashes = {}, {}
    for name in ('scene','sample','sample_data','calibrated_sensor','ego_pose','sensor'):
        file = bounded_file(root, 'v1.0-mini/'+name+'.json')
        table_hashes[name] = file_hash(file)
        rows = json.loads(file.read_text())
        tables[name] = {row['token']: row for row in rows}
        if len(tables[name]) != len(rows):
            raise ValueError('Duplicate nuScenes metadata token')
    # Raw sample.json has no devkit-decorated 'data' map. Join original keys.
    channels = {}
    for data in tables['sample_data'].values():
        if not data['is_key_frame']:
            continue
        calibration = tables['calibrated_sensor'][data['calibrated_sensor_token']]
        sensor = tables['sensor'][calibration['sensor_token']]
        channel = sensor['channel']
        if channel not in (*CAMERAS, 'LIDAR_TOP'):
            continue
        sample_channels = channels.setdefault(data['sample_token'], {})
        if channel in sample_channels:
            raise ValueError('Duplicate original keyframe channel')
        sample_channels[channel] = data['token']
    scene = tables['scene'][scene_token]
    current = tables['sample'][target_sample_token]
    chosen = []
    for _ in range(frame_count):
        if current['scene_token'] != scene_token:
            raise ValueError('Sequence crosses scene boundary')
        chosen.append(current)
        if len(chosen) < frame_count:
            if not current['prev']:
                raise ValueError('Insufficient actual history; select a later sample')
            current = tables['sample'][current['prev']]
    can_relative = 'can_bus/'+scene['name']+'_pose.json'
    can_file = bounded_file(root, can_relative)
    can_hash = file_hash(can_file)
    poses = json.loads(can_file.read_text())
    if not poses or any(type(row['utime']) is not int for row in poses) or any(b['utime'] <= a['utime'] for a,b in zip(poses,poses[1:])):
        raise ValueError('CAN pose timestamps must be strictly ordered')
    frames = []
    for sample in reversed(chosen):
        lidar = tables['sample_data'][channels[sample['token']]['LIDAR_TOP']]
        pose = tables['ego_pose'][lidar['ego_pose_token']]
        calibration = tables['calibrated_sensor'][lidar['calibrated_sensor_token']]
        world_from_lidar = transform(pose) @ transform(calibration)
        cameras = []
        for name in CAMERAS:
            data = tables['sample_data'][channels[sample['token']][name]]
            if data['sample_token'] != sample['token'] or not data['is_key_frame']:
                raise ValueError('Camera does not belong to selected original keyframe')
            cp = tables['ego_pose'][data['ego_pose_token']]
            cc = tables['calibrated_sensor'][data['calibrated_sensor_token']]
            world_from_camera = transform(cp) @ transform(cc)
            intrinsic = np.asarray(cc['camera_intrinsic'], dtype=np.float64)
            if intrinsic.shape != (3,3) or not np.isfinite(intrinsic).all() or abs(np.linalg.det(intrinsic)) < 1e-9:
                raise ValueError('Missing/singular camera calibration')
            projection = np.eye(4); projection[:3,:3] = intrinsic
            projection = projection @ np.linalg.inv(world_from_camera) @ world_from_lidar
            file = bounded_file(root, data['filename'])
            with Image.open(file) as image:
                width,height = image.size
                if (width,height) != (data['width'],data['height']) or image.format not in ('JPEG','PNG'):
                    raise ValueError('Image bytes differ from original shape/format')
                image.verify()
            cameras.append({'name':name,'sample_data_token':data['token'],'path':data['filename'],
                'sha256':file_hash(file),'timestamp_us':data['timestamp'],'width':width,'height':height,'lidar2img':projection.tolist()})
        anchor = lidar['timestamp']
        observed = max([anchor]+[camera['timestamp_us'] for camera in cameras])
        causal = [row for row in poses if row['utime'] <= anchor]
        if not causal or anchor-causal[-1]['utime'] > 100000:
            raise ValueError('Missing causal CAN message within 100ms; no zero/future fallback')
        message = causal[-1]
        # Match the original native CAN layout while avoiding the upstream converter's fallback.
        bus = list(pose['translation'])+list(pose['rotation'])+list(message['accel'])+list(message['rotation_rate'])+list(message['vel'])
        rotation = transform(pose)[:3,:3]
        angle = math.degrees(math.atan2(rotation[1,0],rotation[0,0])) % 360
        bus.extend([math.radians(angle),angle])
        frames.append({'sample_token':sample['token'],'previous_sample_token':sample['prev'],
            'timestamp_us':anchor,'observed_until_us':observed,'cameras':cameras,'can_bus':bus,
            'can_bus_source':{'path':can_relative,'sha256':can_hash,'timestamp_us':message['utime'],'policy':'latest_at_or_before_anchor'},
            'lidar_frame':{'sample_data_token':lidar['token'],'calibration_token':calibration['token'],'ego_pose_token':pose['token'],
                'ego2global':transform(pose).tolist(),'lidar2ego':transform(calibration).tolist()}})
    value = {'schema_version':'driving-input/1','profile':PROFILE,
        'dataset':{'name':'nuScenes','version':'v1.0-mini','scene_token':scene_token,'scene_name':scene['name'],
            'metadata_sha256':content_hash(table_hashes),'use_scope':'research_isolated','rights_note':rights_note},
        'camera_order':list(CAMERAS),'frames':frames,'route_command':route_command,'state_policy':'reset_then_chronological_prefix'}
    validate_input(value)
    return value


def verify_files(root, value):
    validate_input(value)
    table_hashes = {name:file_hash(bounded_file(root,'v1.0-mini/'+name+'.json'))
        for name in ('scene','sample','sample_data','calibrated_sensor','ego_pose','sensor')}
    if content_hash(table_hashes) != value['dataset']['metadata_sha256']:
        raise ValueError('Original metadata changed after input selection')
    for frame in value['frames']:
        for reference in [*frame['cameras'],frame['can_bus_source']]:
            if file_hash(bounded_file(root,reference['path'])) != reference['sha256']:
                raise ValueError('Original file bytes changed after input selection')
    rebuilt = export_input(root,value['dataset']['scene_token'],value['frames'][-1]['sample_token'],
        frame_count=len(value['frames']),route_command=value['route_command'],rights_note=value['dataset']['rights_note'])
    if content_hash(rebuilt) != content_hash(value):
        raise ValueError('Original files/causal metadata differ from pinned input')
    return value
