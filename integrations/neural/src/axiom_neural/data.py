"""Bounded CARLA camera/point conversion into a known-pose Nerfstudio dataset.

World: CARLA LH XYZ -> RH X-forward/Y-left/Z-up. Cameras: OpenGL
X-right/Y-up/Z-back. Only translation is centered/scaled; no pose estimation.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import zipfile
import numpy as np
from PIL import Image

SCALE = 0.05
SWEEP_FRAME_COUNT = 61
WORLD = np.diag([1., -1., 1., 1.])
CAMERA = np.array([[0., 0., -1., 0.], [1., 0., 0., 0.], [0., 1., 0., 0.], [0., 0., 0., 1.]])

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

def matrix(value):
    result = np.asarray(value, dtype=np.float64)
    if result.shape != (4, 4) or not np.isfinite(result).all() or not np.allclose(result[3], [0, 0, 0, 1], atol=1e-7):
        raise ValueError('Camera/source transform must be a finite affine 4x4 matrix')
    if not np.allclose(result[:3, :3].T @ result[:3, :3], np.eye(3), atol=1e-4) or abs(np.linalg.det(result[:3, :3]) - 1) > 1e-4:
        raise ValueError('Native transform must be a rigid proper rotation')
    return result

def camera_pose(native, origin, scale=SCALE):
    result = WORLD @ matrix(native) @ CAMERA
    result[:3, 3] = (result[:3, 3] - np.asarray(origin)) * scale
    return result

def changed_pose(pose, lateral_m, height_m, scale=SCALE):
    result = matrix(pose).copy()
    result[:3, 3] += result[:3, 0] * float(lateral_m) * scale
    result[2, 3] += float(height_m) * scale
    return result


def camera_sweep_lateral_m(index, amplitude_m):
    """Metric camera translation for a six-second frozen-time left-to-right sweep."""
    if (type(index) is not int or not 0 <= index < SWEEP_FRAME_COUNT
            or type(amplitude_m) not in (int, float) or not math.isfinite(amplitude_m)
            or not 0 < amplitude_m <= 2):
        raise ValueError('Camera sweep requires frame 0..60 and amplitude in (0, 2] metres')
    if index == (SWEEP_FRAME_COUNT - 1) // 2:
        return 0.0
    return -float(amplitude_m) * math.cos(math.pi * index / (SWEEP_FRAME_COUNT - 1))

def inspect_manifest(value, camera='front'):
    if (value.get('schema_version') != 'carla-driving-multisensor-result/1' or value.get('status') != 'completed'
            or value.get('drop_count') != 0 or value.get('camera_is_policy_input') is not False):
        raise ValueError('Use a complete native CARLA driving capture with no dropped frames')
    count = value.get('frame_count')
    if type(count) is not int or not 10 <= count <= 300:
        raise ValueError('Neural demo supports 10–300 original camera frames')
    sensor = next((s for s in value['sensors'] if s['key'] == camera and s['kind'] == 'rgb'), None)
    if sensor is None:
        raise ValueError('Selected native RGB camera is missing')
    intrinsics = sensor['pinhole_intrinsics']
    if intrinsics['width'] != 640 or intrinsics['height'] != 360:
        raise ValueError('Neural demo v1 requires the original 640x360 RGB capture')
    rows = [r for r in value['records'] if r['key'] == camera]
    if len(rows) != count or [r['index'] for r in rows] != list(range(count)):
        raise ValueError('Camera frame identities must be complete and ordered')
    previous = None
    for row in rows:
        matrix(row['sensor_world_transform']['matrix'])
        if (row['width'], row['height']) != (640, 360) or row['raw_artifact']['size_bytes'] != 640 * 360 * 4:
            raise ValueError('Native pixel dimensions differ from camera intrinsics')
        now = float(row['timestamp_seconds'])
        if not np.isfinite(now) or previous is not None and not 0 < now - previous < .2:
            raise ValueError('Native camera clock is discontinuous')
        previous = now
    return sensor, rows

def read_member(archive, item, maximum):
    name = item['name']
    if not isinstance(name, str) or name.startswith('/') or '\\' in name or '..' in name.split('/'):
        raise ValueError('Unsafe source member path')
    info = archive.getinfo(name)
    if info.file_size != item['size_bytes'] or not 0 <= info.file_size <= maximum:
        raise ValueError('Source member size differs')
    raw = archive.read(info)
    if hashlib.sha256(raw).hexdigest() != item['sha256']:
        raise ValueError('Source frame hash differs')
    return raw

def build_dataset(manifest_path, archive_path, destination, source_execution_id, source_manifest_sha256):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    (destination / 'images').mkdir()
    manifest = json.loads(Path(manifest_path).read_bytes())
    sensor, records = inspect_manifest(manifest)
    native_centers = np.array([(WORLD @ matrix(r['sensor_world_transform']['matrix']))[:3, 3] for r in records])
    origin = native_centers.mean(axis=0)
    intrinsics = sensor['pinhole_intrinsics']
    transforms = {'camera_model': 'OPENCV', 'fl_x': intrinsics['fx'], 'fl_y': intrinsics['fy'],
                  'cx': intrinsics['cx'], 'cy': intrinsics['cy'], 'w': 640, 'h': 360,
                  'k1': 0., 'k2': 0., 'p1': 0., 'p2': 0., 'frames': []}
    evidence = {'schema_version': 'neural-dataset/1', 'source_execution_id': source_execution_id,
                'source_manifest_sha256': source_manifest_sha256, 'source_kind': 'simulation',
                'camera': 'front', 'rig': 'rig_a', 'width': 640, 'height': 360, 'fps': 10,
                'coordinate_convention': 'right_handed_z_up_opengl_camera', 'world_origin_m': origin.tolist(),
                'scale': SCALE, 'intrinsics': intrinsics, 'frames': []}
    lidar = {r['index']: r for r in manifest['records'] if r['key'] == 'lidar'}
    points, colors = [], []
    with zipfile.ZipFile(archive_path) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError('Duplicate source archive members')
        for row in records:
            raw = read_member(archive, row['raw_artifact'], 640 * 360 * 4)
            rgb = np.frombuffer(raw, np.uint8).reshape(360, 640, 4)[:, :, [2, 1, 0]].copy()
            name = f"images/{row['index']:06d}.png"
            Image.fromarray(rgb).save(destination / name)
            pose = camera_pose(row['sensor_world_transform']['matrix'], origin)
            transforms['frames'].append({'file_path': name, 'transform_matrix': pose.tolist()})
            evidence['frames'].append({'frame_index': row['index'], 'source_frame': row['frame'],
                'time_s': row['index'] / 10., 'source_timestamp_seconds': row['timestamp_seconds'],
                'source_raw_sha256': row['raw_artifact']['sha256'], 'file_path': name,
                'png_sha256': sha_file(destination / name), 'camera_to_world': pose.tolist()})
            if row['index'] not in lidar:
                continue
            scan = lidar[row['index']]
            if scan['frame'] != row['frame']:
                raise ValueError('Initialization point cloud is not aligned to the RGB frame')
            raw_points = read_member(archive, scan['raw_artifact'], 3200 * 16)
            xyz = np.frombuffer(raw_points, '<f4').reshape(-1, 4)[:, :3].astype(np.float64)
            if not np.isfinite(xyz).all():
                raise ValueError('Nonfinite original LiDAR coordinates')
            lidar_world = WORLD @ matrix(scan['sensor_world_transform']['matrix'])
            world = xyz @ lidar_world[:3, :3].T + lidar_world[:3, 3]
            local = ((world - origin) * SCALE - pose[:3, 3]) @ pose[:3, :3]
            depth = -local[:, 2]
            front = depth > .02
            u = np.round(intrinsics['fx'] * local[:, 0] / np.maximum(depth, .001) + intrinsics['cx']).astype(int)
            v = np.round(-intrinsics['fy'] * local[:, 1] / np.maximum(depth, .001) + intrinsics['cy']).astype(int)
            valid = front & (u >= 0) & (u < 640) & (v >= 0) & (v < 360)
            points.append((world[valid] - origin) * SCALE)
            colors.append(rgb[v[valid], u[valid]])
    if points and sum(len(p) for p in points) >= 100:
        xyz, rgb = np.concatenate(points), np.concatenate(colors)
        _, selected = np.unique(np.floor(xyz / (0.1 * SCALE)).astype(np.int64), axis=0, return_index=True)
        selected = np.sort(selected)
        if len(selected) > 100000:
            selected = selected[np.linspace(0, len(selected) - 1, 100000, dtype=int)]
        cloud = np.empty(len(selected), dtype=[('x', '<f4'), ('y', '<f4'), ('z', '<f4'), ('red', 'u1'), ('green', 'u1'), ('blue', 'u1')])
        for i, key in enumerate(('x', 'y', 'z')):
            cloud[key] = xyz[selected, i]
        for i, key in enumerate(('red', 'green', 'blue')):
            cloud[key] = rgb[selected, i]
        header = f'ply\nformat binary_little_endian 1.0\nelement vertex {len(cloud)}\nproperty float x\nproperty float y\nproperty float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n'
        (destination / 'points.ply').write_bytes(header.encode() + cloud.tobytes())
        transforms['ply_file_path'] = 'points.ply'
        evidence['initialization'] = {'source': 'original_native_lidar_projected_to_original_rgb', 'points': len(cloud), 'sha256': sha_file(destination / 'points.ply')}
    else:
        raise ValueError('Insufficient native LiDAR geometry for the fixed demonstration recipe')
    (destination / 'transforms.json').write_bytes(canonical(transforms))
    (destination / 'neural-dataset.json').write_bytes(canonical(evidence))
    packed = destination.with_suffix('.zip')
    with zipfile.ZipFile(packed, 'x', compression=zipfile.ZIP_STORED) as bundle:
        for path in sorted(destination.rglob('*')):
            if path.is_file():
                bundle.write(path, path.relative_to(destination).as_posix())
    return evidence, packed
