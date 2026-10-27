"""Bounded native lane/ego scene evidence. No CARLA import or renderer dependency.

Lane ribbons are derived displays of sampled engine lane widths, not engine
mesh, measured pavement, or physical sensor calibration. Original XODR and
response matrices remain separately hash-bound.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
import xml.etree.ElementTree as ET

SCHEMA = 'sensor-scene/1'
COORDINATES = 'left_handed_x_forward_y_right_z_up'
FIDELITY = 'sampled_native_driving_lane_geometry'
MAP_MAX_BYTES = 8 * 1024**2
SCENE_MAX_BYTES = 4 * 1024**2
MAX_SAMPLES = 12000
MAX_SEGMENTS = 2000
SPACING_M = 2.
MARGIN_M = 40.
GAP_S_M = 4.5
GAP_DISTANCE_M = 8.
LIMITATIONS = [
    'Lane centerlines and widths are sampled native driving-lane geometry; derived ribbons do not reproduce renderer pavement, markings or scenery meshes.',
    'Only the owned ego and cameras are retained; no reactive traffic or inferred surrounding actors.',
    'Native bounding box and nominal pinhole cameras are engine model readbacks, not measured vehicle dimensions or physical calibration.',
    'Planning input frame f and ego/camera response frame f+1 remain distinct; no interpolated response is original evidence.',
]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _finite(value, lo=-1e6, hi=1e6):
    return type(value) in (int, float) and math.isfinite(value) and lo <= value <= hi


def _integer(value, lo=0, hi=2147483647):
    return type(value) is int and lo <= value <= hi


def _hash(value):
    return type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None


def rigid_transform(value):
    if type(value) is not dict or set(value) != {'position_m', 'rotation_deg', 'matrix'}:
        raise ValueError('Scene native transform has an unsupported complete shape')
    position, rotation, matrix = (value[k] for k in ('position_m', 'rotation_deg', 'matrix'))
    if any(type(v) is not list or len(v) != 3 or any(not _finite(n) for n in v) for v in (position, rotation)):
        raise ValueError('Scene native transform position/rotation is nonfinite')
    if (type(matrix) is not list or len(matrix) != 4 or any(type(row) is not list or len(row) != 4 for row in matrix)
            or any(not _finite(n) for row in matrix for n in row) or matrix[3] != [0, 0, 0, 1]):
        raise ValueError('Scene native transform matrix is not homogeneous')
    gram = max(abs(sum(matrix[k][i] * matrix[k][j] for k in range(3)) - (i == j)) for i in range(3) for j in range(3))
    a, b, c = matrix[:3]
    determinant = a[0]*(b[1]*c[2]-b[2]*c[1])-a[1]*(b[0]*c[2]-b[2]*c[0])+a[2]*(b[0]*c[1]-b[1]*c[0])
    if gram > 2e-5 or abs(determinant - 1) > 4e-5 or any(abs(matrix[i][3] - position[i]) > 1e-8 for i in range(3)):
        raise ValueError('Scene native transform matrix is not a proper matching pose')
    # CARLA rotation convention: pitch/yaw/roll, native left-handed engine axes.
    pitch, yaw, roll = map(math.radians, rotation)
    cp, sp, cy, sy, cr, sr = math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw), math.cos(roll), math.sin(roll)
    expected = [[cp*cy, cy*sp*sr-sy*cr, -cy*sp*cr-sy*sr],
                [cp*sy, sy*sp*sr+cy*cr, -sy*sp*cr+cy*sr], [sp, -cp*sr, cp*cr]]
    if max(abs(matrix[i][j] - expected[i][j]) for i in range(3) for j in range(3)) > 2e-5:
        raise ValueError('Scene native rotation metadata differs from its original matrix')
    return value


def road_index(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAP_MAX_BYTES:
        raise ValueError('Original native OpenDRIVE exceeds its byte bound')
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('Original native OpenDRIVE may not declare external entities')
    try:
        root = ET.fromstring(raw.decode('utf-8', errors='strict'))
    except (UnicodeError, ET.ParseError) as error:
        raise ValueError('Original native map is not complete UTF-8 OpenDRIVE') from error
    if root.tag != 'OpenDRIVE':
        raise ValueError('Original native map root is not OpenDRIVE')
    roads = {}
    for road in root.findall('road'):
        try:
            identity, length = int(road.attrib['id']), float(road.attrib['length'])
        except (KeyError, ValueError) as error:
            raise ValueError('Original native road identity/length is invalid') from error
        if identity in roads or not _integer(identity) or not _finite(length, .001, 1e6):
            raise ValueError('Original native road identity/length exceeds its domain')
        lanes = {}
        for section_id, section in enumerate(road.findall('lanes/laneSection')):
            for lane in section.findall('left/lane') + section.findall('right/lane'):
                try:
                    lane_id = int(lane.attrib['id'])
                except (KeyError, ValueError) as error:
                    raise ValueError('Original native lane identity is invalid') from error
                lanes[(section_id, lane_id)] = lane.attrib.get('type')
        roads[identity] = {'length_m': length, 'lanes': lanes}
    if not roads or len(roads) > 20000:
        raise ValueError('Original native map road denominator is unsupported')
    return roads


def sampled_map(native_map, route, expected_sha256):
    """Read native waypoints without ticking/spawning or changing the world."""
    raw = native_map.to_opendrive().encode('utf-8')
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Native map bytes changed from the pinned OpenDRIVE')
    roads = road_index(raw)
    points = route['points']
    roi = {'min_x': min(p['x_m'] for p in points) - MARGIN_M, 'max_x': max(p['x_m'] for p in points) + MARGIN_M,
           'min_y': min(p['y_m'] for p in points) - MARGIN_M, 'max_y': max(p['y_m'] for p in points) + MARGIN_M}
    waypoints = native_map.generate_waypoints(SPACING_M)
    if not isinstance(waypoints, (list, tuple)) or len(waypoints) > 250000:
        raise ValueError('Native map waypoint denominator exceeds the bounded profile')
    groups, denominator = {}, 0
    for waypoint in waypoints:
        if str(waypoint.lane_type).split('.')[-1] != 'Driving':
            continue
        pose = waypoint.transform
        x, y, z = float(pose.location.x), float(pose.location.y), float(pose.location.z)
        if any(not _finite(value) for value in (x, y, z)):
            raise ValueError('Native waypoint pose is nonfinite; no silent sample omission')
        if not roi['min_x'] <= x <= roi['max_x'] or not roi['min_y'] <= y <= roi['max_y']:
            continue
        key = (waypoint.road_id, waypoint.section_id, waypoint.lane_id)
        if (not _integer(key[0]) or not _integer(key[1]) or not _integer(key[2], -1000, 1000) or key[2] == 0
                or key[0] not in roads or roads[key[0]]['lanes'].get(key[1:]) != 'driving'):
            raise ValueError('Native waypoint is not tied to an original driving lane')
        point = {'s_m': float(waypoint.s), 'x_m': x, 'y_m': y, 'z_m': z,
                 'yaw_rad': math.radians(float(pose.rotation.yaw)), 'lane_width_m': float(waypoint.lane_width),
                 'is_junction': bool(waypoint.is_junction)}
        _lane_point(point, roads[key[0]]['length_m'], roi)
        groups.setdefault(key, []).append(point)
        denominator += 1
        if denominator > MAX_SAMPLES:
            raise ValueError('Native scene ROI has too many lane samples; no silent truncation')
    segments = []
    for (road, section, lane), samples in sorted(groups.items()):
        parts = [[]]
        for point in sorted(samples, key=lambda p: (p['s_m'], p['x_m'], p['y_m'], p['z_m'])):
            previous = parts[-1][-1] if parts[-1] else None
            if previous and point['s_m'] == previous['s_m']:
                if point != previous:
                    raise ValueError('Native lane has conflicting samples at one longitudinal coordinate')
                continue
            if previous and (point['s_m'] - previous['s_m'] > GAP_S_M or _distance(point, previous) > GAP_DISTANCE_M):
                parts.append([])
            parts[-1].append(point)
        for index, part in enumerate(parts):
            segments.append({'id': f'road:{road}/section:{section}/lane:{lane}/segment:{index}', 'road_id': road,
                             'section_id': section, 'lane_id': lane, 'lane_type': 'Driving', 'points': part})
    result = {'name': native_map.name, 'source_artifact': 'map.xodr', 'sha256': expected_sha256,
              'sampling': {'method': 'carla.Map.generate_waypoints/1', 'spacing_m': SPACING_M, 'roi_margin_m': MARGIN_M,
                           'roi_xy_m': roi, 'gap_split_s_m': GAP_S_M, 'gap_split_distance_m': GAP_DISTANCE_M},
              'lane_segments': segments, 'sample_count': sum(len(s['points']) for s in segments), 'segment_count': len(segments)}
    validate_map(result, raw, route, expected_sha256)
    return result, raw


def _distance(a, b):
    return math.sqrt(sum((a[k] - b[k])**2 for k in ('x_m', 'y_m', 'z_m')))


def _lane_point(point, length, roi):
    if type(point) is not dict or set(point) != {'s_m', 'x_m', 'y_m', 'z_m', 'yaw_rad', 'lane_width_m', 'is_junction'}:
        raise ValueError('Native lane point has unsupported complete fields')
    if (any(not _finite(point[k]) for k in ('x_m', 'y_m', 'z_m')) or not _finite(point['s_m'], -.001, length + .1)
            or not _finite(point['yaw_rad'], -4*math.pi, 4*math.pi) or not _finite(point['lane_width_m'], .1, 30)
            or type(point['is_junction']) is not bool
            or not roi['min_x'] <= point['x_m'] <= roi['max_x'] or not roi['min_y'] <= point['y_m'] <= roi['max_y']):
        raise ValueError('Native lane point width/geometry exceeds the retained ROI')


def validate_map(value, raw, route, expected_sha256):
    roads = road_index(raw)
    if type(value) is not dict or set(value) != {'name', 'source_artifact', 'sha256', 'sampling', 'lane_segments', 'sample_count', 'segment_count'}:
        raise ValueError('Native scene map has unsupported complete fields')
    if (type(value['name']) is not str or not 1 <= len(value['name']) <= 200 or value['source_artifact'] != 'map.xodr'
            or value['sha256'] != expected_sha256 or hashlib.sha256(raw).hexdigest() != expected_sha256):
        raise ValueError('Native scene map identity differs from original OpenDRIVE')
    points = route['points']
    roi = {'min_x': min(p['x_m'] for p in points)-MARGIN_M, 'max_x': max(p['x_m'] for p in points)+MARGIN_M,
           'min_y': min(p['y_m'] for p in points)-MARGIN_M, 'max_y': max(p['y_m'] for p in points)+MARGIN_M}
    expected_sampling = {'method': 'carla.Map.generate_waypoints/1', 'spacing_m': SPACING_M, 'roi_margin_m': MARGIN_M,
                         'roi_xy_m': roi, 'gap_split_s_m': GAP_S_M, 'gap_split_distance_m': GAP_DISTANCE_M}
    segments = value['lane_segments']
    if value['sampling'] != expected_sampling or type(segments) is not list or not 1 <= len(segments) <= MAX_SEGMENTS:
        raise ValueError('Native map sampling/segment denominator is unsupported')
    seen, samples, lane_parts = set(), 0, {}
    for segment in segments:
        if type(segment) is not dict or set(segment) != {'id', 'road_id', 'section_id', 'lane_id', 'lane_type', 'points'}:
            raise ValueError('Native lane segment has unsupported fields')
        road, section, lane = (segment[k] for k in ('road_id', 'section_id', 'lane_id'))
        if not _integer(road) or not _integer(section) or not _integer(lane, -1000, 1000) or lane == 0:
            raise ValueError('Native lane segment identity is outside its integer domain')
        key = (road, section, lane)
        part_index = lane_parts.get(key, 0)
        expected_id = f'road:{road}/section:{section}/lane:{lane}/segment:{part_index}'
        if (not _integer(road) or not _integer(section) or not _integer(lane, -1000, 1000) or lane == 0
                or road not in roads or roads[road]['lanes'].get((section, lane)) != 'driving'
                or segment['lane_type'] != 'Driving' or segment['id'] != expected_id or expected_id in seen):
            raise ValueError('Native lane segment does not identify an original driving lane')
        seen.add(expected_id); lane_parts[key] = part_index + 1
        rows = segment['points']
        if type(rows) is not list or not 1 <= len(rows) <= MAX_SAMPLES:
            raise ValueError('Native lane segment sample denominator is unsupported')
        for index, point in enumerate(rows):
            _lane_point(point, roads[road]['length_m'], roi)
            if index and (point['s_m'] <= rows[index-1]['s_m'] or point['s_m'] - rows[index-1]['s_m'] > GAP_S_M
                          or _distance(point, rows[index-1]) > GAP_DISTANCE_M):
                raise ValueError('Native lane segment bridges a gap or repeats its clock coordinate')
        samples += len(rows)
    if samples > MAX_SAMPLES or value['sample_count'] != samples or value['segment_count'] != len(segments):
        raise ValueError('Native map sample/segment denominator differs from retained geometry')
    return value


def bounding_box(local_transform, extent):
    rigid_transform(local_transform)
    if type(extent) is not list or len(extent) != 3 or any(not _finite(v, .001, 30) for v in extent):
        raise ValueError('Native ego bounding box extent is unsupported; no default dimensions')
    return {'coordinate_frame': 'ego_local', 'source': 'carla.Actor.bounding_box', 'local_transform': deepcopy(local_transform),
            'extent_m': list(extent), 'dimensions_m': [2*v for v in extent]}


def _cameras(manifest):
    return [{key: deepcopy(sensor[key]) for key in ('key', 'actor_id', 'blueprint_id', 'mount_native', 'pinhole_intrinsics', 'declared_rh_mount_matrix')}
            for sensor in manifest['sensors']]


def _frames(manifest, trace):
    count = len(manifest['sensors'])
    return [{**{key: tick[key] for key in ('index', 'input_frame', 'frame', 'input_timestamp_seconds', 'timestamp_seconds', 'simulation_timestamp_ns')},
             'ego_world_transform': deepcopy(tick['ego']['world_transform']),
             'cameras': [{key: deepcopy(record[key]) for key in ('key', 'frame', 'timestamp_seconds', 'simulation_timestamp_ns',
                                                               'sensor_actor_id', 'sensor_parent_actor_id', 'sensor_world_transform')}
                         for record in manifest['records'][tick['index']*count:(tick['index']+1)*count]]}
            for tick in trace['ticks']]


def build_scene(manifest, trace, trace_raw, map_data, bbox):
    probe = manifest['probe']
    source = {key: manifest[key] for key in ('external_execution_id', 'configuration_hash', 'configuration_revision', 'seed', 'adapter_version')}
    source.update({key: probe[key] for key in ('native_script_sha256', 'native_support_script_sha256', 'native_scene_script_sha256', 'map_opendrive_sha256')})
    source.update(route_sha256=probe['route']['sha256'], trace_sha256=hashlib.sha256(trace_raw).hexdigest())
    return {'schema_version': SCHEMA, 'coordinate_convention': COORDINATES, 'fidelity': FIDELITY, 'source': source,
            'map': deepcopy(map_data), 'ego': {'actor_id': manifest['ego_actor_id'], 'blueprint_id': manifest['ego_blueprint'],
                                              'role': 'ego', 'native_bounding_box': deepcopy(bbox)},
            'cameras': _cameras(manifest), 'frames': _frames(manifest, trace), 'limitations': list(LIMITATIONS)}


def validate_scene(scene, map_raw, manifest, trace, trace_raw):
    if type(scene) is not dict or set(scene) != {'schema_version', 'coordinate_convention', 'fidelity', 'source', 'map', 'ego', 'cameras', 'frames', 'limitations'}:
        raise ValueError('Native sensor scene has unsupported complete fields')
    if scene['schema_version'] != SCHEMA or scene['coordinate_convention'] != COORDINATES or scene['fidelity'] != FIDELITY:
        raise ValueError('Native sensor scene version/coordinate/fidelity is unsupported')
    if len(canonical(scene)) > SCENE_MAX_BYTES:
        raise ValueError('Native sensor scene exceeds its byte bound')
    expected = build_scene(manifest, trace, trace_raw, scene['map'], scene['ego'].get('native_bounding_box', {}) if type(scene['ego']) is dict else {})
    source = scene['source']
    if (source != expected['source'] or any(not _hash(source[k]) for k in ('configuration_hash', 'native_script_sha256',
            'native_support_script_sha256', 'native_scene_script_sha256', 'map_opendrive_sha256', 'route_sha256', 'trace_sha256'))):
        raise ValueError('Native sensor scene source hashes/configuration differ from original execution')
    validate_map(scene['map'], map_raw, manifest['probe']['route'], manifest['probe']['map_opendrive_sha256'])
    if scene['map']['name'] != manifest['probe']['map']:
        raise ValueError('Native sensor scene map name differs from the pinned map')
    actor = scene['ego']
    if (type(actor) is not dict or set(actor) != {'actor_id', 'blueprint_id', 'role', 'native_bounding_box'}
            or actor['actor_id'] != manifest['ego_actor_id'] or actor['blueprint_id'] != manifest['ego_blueprint'] or actor['role'] != 'ego'):
        raise ValueError('Native scene ego does not identify the owned actor')
    bbox = actor['native_bounding_box']
    if type(bbox) is not dict or set(bbox) != {'coordinate_frame', 'source', 'local_transform', 'extent_m', 'dimensions_m'}:
        raise ValueError('Native ego bounding box has unsupported complete fields')
    if bbox != bounding_box(bbox['local_transform'], bbox['extent_m']) or bbox != manifest.get('ego_bounding_box'):
        raise ValueError('Native ego bounding box differs from original readback or dimensions')
    if scene['cameras'] != expected['cameras'] or scene['frames'] != expected['frames'] or scene['limitations'] != LIMITATIONS:
        raise ValueError('Native scene camera/ego response matrices do not exactly join the original trace/capture')
    for sensor in scene['cameras']:
        rigid_transform(sensor['mount_native'])
    for frame in scene['frames']:
        rigid_transform(frame['ego_world_transform'])
        for camera in frame['cameras']:
            rigid_transform(camera['sensor_world_transform'])
    return scene
