"""Preserve original nuScenes rows and explicit causal, derived planar motion.

Source collection uses only stdlib and can be streamed over authenticated SSH.
MCAP writing imports the platform's exact versioned schemas locally. No future
annotation is resolved, and no missing velocity or measured ego shape is invented.
"""
import hashlib
import base64
import io
import json
import math
import re
import tarfile
import zlib
from pathlib import Path

VERSION = 'axiom-nuscenes-planning/1'
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_BUNDLE_BYTES = 1536 * 1024
MAX_EXPANDED_LINEAGE_BYTES = 16 * 1024 * 1024
COMPACT_LINEAGE_ENCODING = 'zlib-base64/1'
MAX_ARCHIVE_BYTES = 8 * 1024**3
MAX_FRAMES = 16
MAX_LOG_FRAMES = 64
LOG_SELECTION_SCHEMA = 'nuscenes-log-selection/1'
TOKEN = re.compile(r'^[a-f0-9]{32}$')
HASH = re.compile(r'^[a-f0-9]{64}$')
DIRECT_TABLES = ('scene', 'sample', 'sample_data', 'calibrated_sensor', 'ego_pose', 'sensor')
ARCHIVE_TABLES = ('sample_annotation', 'instance', 'category', 'attribute', 'visibility', 'log')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def compact_lineage(value):
    """Losslessly retain original rows within the existing MCAP text bound."""
    raw = canonical(value)
    if len(raw) > MAX_EXPANDED_LINEAGE_BYTES:
        raise ValueError('Expanded original lineage exceeds its 16 MiB bound')
    envelope = {'schema_version': 'axiom.compact-original-lineage/1',
        'encoding': COMPACT_LINEAGE_ENCODING, 'uncompressed_sha256': hashlib.sha256(raw).hexdigest(),
        'uncompressed_size_bytes': len(raw),
        'data_base64': base64.b64encode(zlib.compress(raw, level=9)).decode()}
    if len(canonical(envelope)) > 1024 * 1024:
        raise ValueError('Compact original lineage exceeds the existing MCAP text bound')
    return envelope


def expand_lineage(envelope):
    """Bounded recovery of the exact retained original lineage JSON."""
    expected = {'schema_version', 'encoding', 'uncompressed_sha256', 'uncompressed_size_bytes', 'data_base64'}
    if (not isinstance(envelope, dict) or set(envelope) != expected
            or envelope['schema_version'] != 'axiom.compact-original-lineage/1'
            or envelope['encoding'] != COMPACT_LINEAGE_ENCODING
            or type(envelope['uncompressed_size_bytes']) is not int
            or not 0 < envelope['uncompressed_size_bytes'] <= MAX_EXPANDED_LINEAGE_BYTES
            or len(canonical(envelope)) > 1024 * 1024):
        raise ValueError('Unsupported compact original lineage envelope')
    encoded = base64.b64decode(envelope['data_base64'], validate=True)
    decoder = zlib.decompressobj()
    raw = decoder.decompress(encoded, envelope['uncompressed_size_bytes'] + 1)
    if (len(raw) != envelope['uncompressed_size_bytes'] or not decoder.eof
            or decoder.unused_data or decoder.unconsumed_tail
            or hashlib.sha256(raw).hexdigest() != envelope['uncompressed_sha256']):
        raise ValueError('Compact original lineage bytes differ from their pinned source')
    value = parse_json(raw)
    if canonical(value) != raw:
        raise ValueError('Expanded original lineage must retain canonical JSON')
    return value


def _pairs(items):
    output = {}
    for key, value in items:
        if key in output:
            raise ValueError('Duplicate JSON key: ' + key)
        output[key] = value
    return output


def parse_json(raw):
    return json.loads(raw, object_pairs_hook=_pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON: ' + value)))


def file_hash(path, *, maximum=MAX_FILE_BYTES):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Original input must be a regular non-symlink file')
    sha, size = hashlib.sha256(), 0
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            size += len(chunk)
            if size > maximum:
                raise ValueError('Original file exceeds its byte bound')
            sha.update(chunk)
    return sha.hexdigest(), size


def _read(path, *, maximum=MAX_FILE_BYTES):
    sha, size = file_hash(path, maximum=maximum)
    raw = Path(path).read_bytes()
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError('Original file changed during source collection')
    return parse_json(raw), {'sha256': sha, 'size_bytes': size}


def _index(rows, name):
    if not isinstance(rows, list) or len(rows) > 100_000:
        raise ValueError('Original table is absent or exceeds its row bound: ' + name)
    output = {}
    for row in rows:
        if not isinstance(row, dict) or not TOKEN.fullmatch(str(row.get('token', ''))) or row['token'] in output:
            raise ValueError('Missing or duplicate original table token: ' + name)
        output[row['token']] = row
    return output


def _vector(value, size):
    if not isinstance(value, list) or len(value) != size or any(type(v) not in (float, int) or not math.isfinite(v) for v in value):
        raise ValueError('Invalid original finite vector')
    return [float(v) for v in value]


def _pose(rotation):
    w, x, y, z = _vector(rotation, 4)
    if abs(w*w+x*x+y*y+z*z-1) > 1e-5:
        raise ValueError('Original rotation must be a unit wxyz quaternion')
    return [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
            [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
            [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]]


def _yaw(rotation):
    matrix = _pose(rotation)
    return math.atan2(matrix[1][0], matrix[0][0])


def validate_geometry(value):
    expected = {'length_m', 'width_m', 'origin_offset_xy_m', 'provenance', 'original_measured_geometry_available'}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError('Explicit ego geometry declaration is required; no default dimensions')
    if value['provenance'] != 'authored_reference_footprint' or value['original_measured_geometry_available'] is not False:
        raise ValueError('This source has no measured ego footprint or verified center offset')
    for key, maximum in [('length_m', 30), ('width_m', 10)]:
        if type(value[key]) not in (int, float) or not math.isfinite(value[key]) or not 0 < value[key] <= maximum:
            raise ValueError('Explicit ego dimensions must be finite and positive')
    if _vector(value['origin_offset_xy_m'], 2) != [0., 0.]:
        raise ValueError('Only zero offset at the original ego-pose origin is supported in this reference slice')
    return dict(value)


def collect_source_context(data_root, archive, *, expected_archive_sha256):
    """Read and hash originals once for a bounded operator batch, without extraction."""
    root = Path(data_root).resolve()
    if not HASH.fullmatch(expected_archive_sha256):
        raise ValueError('Explicit original archive content digest is required')
    tables, identities = {}, {}
    for name in DIRECT_TABLES:
        path = root/'v1.0-mini'/(name+'.json')
        rows, receipt = _read(path)
        tables[name] = _index(rows, name)
        identities[name] = receipt | {'relative_path': str(path.relative_to(root)), 'rows': len(rows)}
    table_digest = digest({name: identities[name]['sha256'] for name in DIRECT_TABLES})
    archive_sha, archive_size = file_hash(archive, maximum=MAX_ARCHIVE_BYTES)
    if archive_sha != expected_archive_sha256:
        raise ValueError('Original mini archive differs from the acquired content hash')
    found = set()
    with tarfile.open(archive, 'r:gz') as stream:
        for member in stream:
            if member.name not in {'v1.0-mini/'+name+'.json' for name in ARCHIVE_TABLES}:
                continue
            name = Path(member.name).stem
            if name in found or not member.isfile() or not 0 < member.size <= MAX_FILE_BYTES:
                raise ValueError('Duplicate, nonregular or oversize original archive table')
            raw = stream.extractfile(member).read(MAX_FILE_BYTES+1)
            if len(raw) != member.size:
                raise ValueError('Original table member byte count differs')
            rows = parse_json(raw)
            # Visibility uses short integer-string IDs rather than UUID tokens.
            if name == 'visibility':
                if not isinstance(rows, list) or len({r['token'] for r in rows}) != len(rows):
                    raise ValueError('Invalid original visibility table')
                tables[name] = {row['token']: row for row in rows}
            else:
                tables[name] = _index(rows, name)
            identities[name] = {'archive_member': member.name, 'sha256': hashlib.sha256(raw).hexdigest(),
                                'size_bytes': len(raw), 'rows': len(rows)}
            found.add(name)
            if len(found) == len(ARCHIVE_TABLES):
                break
    if found != set(ARCHIVE_TABLES):
        raise ValueError('Required original annotation/taxonomy tables are missing from the retained archive')
    return {'root': str(root), 'archive_path': str(Path(archive).resolve()), 'tables': tables,
            'identities': identities, 'table_digest': table_digest, 'archive_sha': archive_sha,
            'archive_size': archive_size}


def collect_source_bundle(data_root, archive, input_bytes, *, expected_archive_sha256,
                          converter_source_sha256, ego_geometry, compact=False, source_context=None):
    """Read originals; source_context reuses this process's verified batch snapshot."""
    root = Path(data_root).resolve()
    geometry = validate_geometry(ego_geometry)
    if not HASH.fullmatch(expected_archive_sha256) or not HASH.fullmatch(converter_source_sha256):
        raise ValueError('Explicit original archive and converter source digests are required')
    if not isinstance(input_bytes, bytes) or len(input_bytes) > 256*1024:
        raise ValueError('Original selection bytes are absent or exceed the byte bound')
    manifest = parse_json(input_bytes)
    if (manifest.get('schema_version') not in ('driving-input/1', LOG_SELECTION_SCHEMA)
            or manifest.get('dataset', {}).get('use_scope') != 'research_isolated'
            or manifest['dataset'].get('version') != 'v1.0-mini' or not manifest['dataset'].get('rights_note')):
        raise ValueError('Select an explicitly approved isolated noncommercial mini input')
    selected = manifest.get('frames', [])
    frame_limit = MAX_LOG_FRAMES if manifest['schema_version'] == LOG_SELECTION_SCHEMA else MAX_FRAMES
    if not 2 <= len(selected) <= frame_limit:
        raise ValueError('Selected original frame count exceeds its input contract')
    context = source_context if source_context is not None else collect_source_context(
        root, archive, expected_archive_sha256=expected_archive_sha256)
    if (context['root'] != str(root) or context['archive_path'] != str(Path(archive).resolve())
            or context['archive_sha'] != expected_archive_sha256):
        raise ValueError('Batch source context differs from the selected original source')
    tables, identities, table_digest = context['tables'], context['identities'], context['table_digest']
    archive_sha, archive_size = context['archive_sha'], context['archive_size']
    if table_digest != manifest['dataset']['metadata_sha256']:
        raise ValueError('Original metadata differs from the approved source selection')
    scene = tables['scene'].get(manifest['dataset']['scene_token'])
    if not scene or scene['name'] != manifest['dataset']['scene_name']:
        raise ValueError('Selected original scene identity differs')
    can_path = root/'can_bus'/(scene['name']+'_pose.json')
    can_rows, can_receipt = _read(can_path)
    if (not can_rows or any(type(row.get('utime')) is not int for row in can_rows)
            or any(b['utime'] <= a['utime'] for a,b in zip(can_rows, can_rows[1:]))):
        raise ValueError('Original CAN chronology is incomplete or unordered')
    frames = []
    previous = None
    for chosen in selected:
        sample = tables['sample'].get(chosen['sample_token'])
        lidar = tables['sample_data'].get(chosen['lidar_frame']['sample_data_token'])
        if not sample or not lidar or sample['scene_token'] != scene['token'] or lidar['sample_token'] != sample['token'] or lidar['is_key_frame'] is not True:
            raise ValueError('Selected sample/LiDAR identity differs from the original scene')
        pose = tables['ego_pose'][lidar['ego_pose_token']]
        calibration = tables['calibrated_sensor'][lidar['calibrated_sensor_token']]
        sensor = tables['sensor'][calibration['sensor_token']]
        at = sample['timestamp']
        if (type(at) is not int or at != chosen['timestamp_us'] or at != lidar['timestamp'] or at != pose['timestamp']
                or sensor['channel'] != 'LIDAR_TOP' or pose['token'] != chosen['lidar_frame']['ego_pose_token']
                or calibration['token'] != chosen['lidar_frame']['calibration_token']):
            raise ValueError('Original keyframe timestamps, ego pose or calibration differ; no interpolation')
        if previous and (sample['prev'] != previous['token'] or at <= previous['timestamp'] or at-previous['timestamp'] > 1_000_000):
            raise ValueError('Selected original keyframes must be contiguous with at most one-second gaps')
        causal = [(index,row) for index,row in enumerate(can_rows) if row['utime'] <= at]
        if not causal or at-causal[-1][1]['utime'] > 100_000:
            raise ValueError('No original causal CAN velocity within 100 ms')
        can_index, can = causal[-1]
        if can_receipt['sha256'] != chosen['can_bus_source']['sha256'] or can['utime'] != chosen['can_bus_source']['timestamp_us']:
            raise ValueError('Causal CAN identity differs from the original driving input')
        originals = []
        for annotation in sorted((row for row in tables['sample_annotation'].values() if row['sample_token'] == sample['token']), key=lambda row: row['instance_token']):
            instance = tables['instance'][annotation['instance_token']]
            category = tables['category'][instance['category_token']]
            history = tables['sample_annotation'].get(annotation['prev']) if annotation['prev'] else None
            history_sample = tables['sample'].get(history['sample_token']) if history else None
            if annotation['prev'] and (not history or not history_sample or history['instance_token'] != instance['token'] or history_sample['scene_token'] != scene['token'] or history_sample['timestamp'] >= at):
                raise ValueError('Original previous annotation identity or chronology is invalid')
            originals.append({'annotation': annotation, 'annotation_record_sha256': digest(annotation),
                'instance': instance, 'category': category,
                'visibility': tables['visibility'].get(annotation['visibility_token']),
                'attributes': [tables['attribute'][token] for token in annotation['attribute_tokens']],
                'previous_annotation': history, 'previous_sample': history_sample,
                'previous_annotation_record_sha256': digest(history) if history else None})
        if len(originals) > 128 or not originals:
            raise ValueError('Original object denominator is empty or exceeds the supported 128-object frame bound')
        frames.append({'sample': sample, 'sample_data': lidar, 'ego_pose': pose, 'calibrated_sensor': calibration,
                       'sensor': sensor, 'can_pose': can, 'can_original_index': can_index, 'objects': originals})
        previous = sample
    bundle = {'schema_version': 'nuscenes-planning-source/1', 'converter_version': VERSION,
        'converter_source_sha256': converter_source_sha256, 'use_scope': 'isolated_noncommercial_research',
        'commercial_use': False, 'original_driving_input': manifest,
        'original_driving_input_sha256': hashlib.sha256(input_bytes).hexdigest(),
        'original_metadata_hash': table_digest, 'original_archive': {'sha256': archive_sha, 'size_bytes': archive_size},
        'original_tables': identities, 'original_scene': scene, 'original_log': tables['log'][scene['log_token']],
        'original_can_file': can_receipt | {'relative_path': str(can_path.relative_to(root)), 'rows': len(can_rows)},
        'ego_geometry': geometry, 'frames': frames}
    if manifest['schema_version'] == LOG_SELECTION_SCHEMA:
        original_samples = sorted((row for row in tables['sample'].values() if row['scene_token'] == scene['token']),
                                  key=lambda row: row['timestamp'])
        original_indices = {row['token']: index for index, row in enumerate(original_samples)}
        if scene.get('nbr_samples') != len(original_samples):
            raise ValueError('Original library scene denominator differs from its metadata')
        metadata = {'scene_token': scene['token'], 'scene_name': scene['name'], 'description': scene['description'],
            'original_frame_count': len(original_samples), 'sample_start_index': original_indices[frames[0]['sample']['token']],
            'sample_end_index': original_indices[frames[-1]['sample']['token']],
            'start_time_s': (frames[0]['sample']['timestamp'] - original_samples[0]['timestamp']) / 1e6,
            'end_time_s': (frames[-1]['sample']['timestamp'] - original_samples[0]['timestamp']) / 1e6,
            'original_scene_record_sha256': digest(scene)}
        if any(manifest.get('selection', {}).get(key) != value for key, value in metadata.items()):
            raise ValueError('Library selection metadata differs from original scene/sample rows')
        bundle['library_metadata'] = metadata
    if type(compact) is not bool:
        raise ValueError('Compact lineage selection must be explicit boolean')
    if compact:
        bundle['lineage_encoding'] = COMPACT_LINEAGE_ENCODING
    if len(canonical(bundle)) > (MAX_EXPANDED_LINEAGE_BYTES if compact else MAX_BUNDLE_BYTES):
        raise ValueError('Retained source lineage exceeds its 1.5 MiB bound')
    return bundle


def convert_bundle(bundle):
    if (bundle.get('schema_version') != 'nuscenes-planning-source/1' or bundle.get('converter_version') != VERSION
            or bundle.get('use_scope') != 'isolated_noncommercial_research' or bundle.get('commercial_use') is not False):
        raise ValueError('Unsupported source or research-use contract')
    geometry = validate_geometry(bundle['ego_geometry'])
    frames, lineage, previous = [], [], None
    frame_limit = MAX_LOG_FRAMES if bundle.get('original_driving_input', {}).get('schema_version') == LOG_SELECTION_SCHEMA else MAX_FRAMES
    if not 2 <= len(bundle['frames']) <= frame_limit:
        raise ValueError('Source frame count is unsupported')
    world = 'nuscenes_global_'+bundle['original_scene']['log_token']
    for index, source in enumerate(bundle['frames']):
        sample, pose, can = source['sample'], source['ego_pose'], source['can_pose']
        at = sample['timestamp']
        if (type(at) is not int or not 0 <= at*1000 < 2**64 or pose['timestamp'] != at
                or source['sample_data']['timestamp'] != at or can['utime'] > at or at-can['utime'] > 100_000
                or previous is not None and (at <= previous or at-previous > 1_000_000)):
            raise ValueError('Original source clocks are invalid, future or unsupported')
        previous = at
        xyz, velocity = _vector(pose['translation'], 3), _vector(can['vel'], 3)
        if velocity[0] < 0:
            raise ValueError('Reverse ego velocity is unsupported by the reference planning contract')
        ego = {'timestamp_ns': str(at*1000), 'frame_id': world, 'x_m': xyz[0], 'y_m': xyz[1],
            'yaw_rad': _yaw(pose['rotation']), 'speed_mps': math.hypot(*velocity[:2]),
            'length_m': float(geometry['length_m']), 'width_m': float(geometry['width_m']),
            'source': 'original ego-pose + causal CAN; explicitly authored reference footprint'}
        objects, motion = [], []
        for entry in source['objects']:
            original = entry['annotation']
            if original['sample_token'] != sample['token'] or entry['annotation_record_sha256'] != digest(original):
                raise ValueError('Original annotation record identity differs')
            dimensions, center = _vector(original['size'], 3), _vector(original['translation'], 3)
            if min(dimensions) <= 0:
                raise ValueError('Original annotation dimensions must be positive')
            obj = {'id': original['instance_token'], 'x_m': center[0], 'y_m': center[1],
                   'yaw_rad': _yaw(original['rotation']), 'length_m': dimensions[1], 'width_m': dimensions[0],
                   'vx_mps': None, 'vy_mps': None}
            history, history_sample = entry['previous_annotation'], entry['previous_sample']
            receipt = {'annotation_token': original['token'], 'instance_token': original['instance_token'],
                       'method': 'previous_only_world_position_difference/1', 'status': 'unavailable',
                       'reason': 'no_previous_original_annotation', 'source_record_sha256': digest(original),
                       'previous_record_sha256': None, 'interval_ns': None, 'uses_future_annotations': False}
            if history:
                if (not history_sample or history['token'] != original['prev'] or history['instance_token'] != original['instance_token']
                        or entry['previous_annotation_record_sha256'] != digest(history)):
                    raise ValueError('Original previous annotation identity differs')
                dt_us = at-history_sample['timestamp']
                if dt_us <= 0:
                    raise ValueError('Previous-only velocity would consume a future or duplicate annotation')
                receipt.update(previous_record_sha256=digest(history), interval_ns=str(dt_us*1000))
                if dt_us <= 1_500_000:
                    prior_center = _vector(history['translation'], 3)
                    obj.update(vx_mps=(center[0]-prior_center[0])/(dt_us/1e6),
                               vy_mps=(center[1]-prior_center[1])/(dt_us/1e6))
                    receipt.update(status='derived', reason='causal_annotation_motion_not_measured_velocity')
                else:
                    receipt['reason'] = 'previous_annotation_gap_exceeds_1_5s'
            objects.append(obj); motion.append(receipt)
        if len(objects) > 128 or len({obj['id'] for obj in objects}) != len(objects):
            raise ValueError('Original object denominator is oversized or duplicates an instance')
        tracks = {'timestamp_ns': str(at*1000), 'frame_id': world, 'coordinate_frame': 'world',
                  'velocity_semantics': 'absolute_ground_velocity', 'source': 'original nuScenes GT annotations; causal derived velocities with explicit unknowns', 'objects': objects}
        frames.append({'at_ns': str(at*1000), 'ego': ego, 'objects': tracks})
        lineage.append({'index': index, 'sample_token': sample['token'], 'original_timestamp_us': at,
            'ego_pose_record_sha256': digest(pose), 'can_pose_record_sha256': digest(can),
            'can_measurement_timestamp_us': can['utime'], 'can_age_ns': str((at-can['utime'])*1000),
            'object_count': len(objects), 'derived_velocity_count': sum(v['status'] == 'derived' for v in motion),
            'unknown_velocity_count': sum(v['status'] != 'derived' for v in motion), 'object_motion': motion})
    provenance = {'schema_version': 'axiom.nuscenes-planning-lineage/1', 'converter_version': VERSION,
        'source_bundle_sha256': digest(bundle), 'source_kind': 'measured',
        'input_semantics': 'original_recorded_gt_annotations_not_sensor_perception',
        'use_scope': 'isolated_noncommercial_research', 'commercial_use': False,
        'world_frame': world, 'source_bundle': bundle, 'converted_frames': lineage,
        'limitations': ['nuScenes data and derived results remain user-approved isolated noncommercial research.',
            'Original recorded annotation boxes are not a sensor perception model output or live observation.',
            'Object velocities are past/current annotation differences; unknown histories remain null.',
            'Original ego footprint dimensions and center offset are unavailable; an explicit authored reference footprint is used at zero offset.',
            'Planar world XY/yaw omits original Z/height/pitch/roll; original full records are retained.',
            'Original publish/transport timestamps are unavailable. MCAP log/publish clocks copy original sample capture time.',
            'Logged surrounding objects and retained cameras are nonreactive. Reference control feedback is not physical vehicle or driving-safety validation.']}
    encoding = bundle.get('lineage_encoding')
    if encoding not in (None, COMPACT_LINEAGE_ENCODING):
        raise ValueError('Unsupported original lineage encoding')
    if len(canonical(provenance)) > (MAX_EXPANDED_LINEAGE_BYTES if encoding else MAX_BUNDLE_BYTES):
        raise ValueError('Retained conversion lineage exceeds the accepted metadata bound')
    return frames, provenance


def mcap_bytes(frames, provenance):
    """Create exact supported message/schema contracts; no implicit role guessing."""
    from mcap.writer import Writer, CompressionType
    from ade2e.scene_sequence_schemas import PlanningEgo, ObjectTracks
    from ade2e.scene_sequences import EGO_SCHEMA, OBJECT_SCHEMA, EGO_SCHEMA_NAME, OBJECT_SCHEMA_NAME
    output = io.BytesIO()
    writer = Writer(output, compression=CompressionType.ZSTD, enable_crcs=True, enable_data_crcs=True)
    writer.start(profile='axiom-nuscenes-planning/1', library=VERSION)
    es = writer.register_schema(EGO_SCHEMA_NAME, 'jsonschema', canonical(EGO_SCHEMA))
    os = writer.register_schema(OBJECT_SCHEMA_NAME, 'jsonschema', canonical(OBJECT_SCHEMA))
    ec = writer.register_channel('/planning/ego', 'json', es)
    oc = writer.register_channel('/planning/original_annotations', 'json', os)
    full_lineage = canonical(compact_lineage(provenance) if provenance['source_bundle'].get('lineage_encoding') else provenance)
    if len(full_lineage) > MAX_BUNDLE_BYTES:
        raise ValueError('Original retained lineage exceeds the metadata bound')
    summary = {key: provenance[key] for key in ('schema_version', 'converter_version', 'source_bundle_sha256',
        'source_kind', 'input_semantics', 'use_scope', 'commercial_use', 'world_frame', 'limitations')}
    source = provenance['source_bundle']
    summary.update(original_archive=source['original_archive'], original_tables=source['original_tables'],
        original_driving_input_sha256=source['original_driving_input_sha256'],
        original_metadata_hash=source['original_metadata_hash'], original_can_file=source['original_can_file'],
        converter_source_sha256=source['converter_source_sha256'], ego_geometry=source['ego_geometry'],
        frames=[{key: frame[key] for key in ('sample_token', 'original_timestamp_us', 'can_age_ns',
            'object_count', 'derived_velocity_count', 'unknown_velocity_count')} for frame in provenance['converted_frames']],
        object_velocity_method='previous_only_world_position_difference/1', uses_future_annotations=False,
        original_lineage_metadata={'name': 'axiom.original_lineage', 'field': 'lineage_json',
                                  'sha256': hashlib.sha256(full_lineage).hexdigest()})
    if source.get('lineage_encoding'):
        summary['original_lineage_encoding'] = COMPACT_LINEAGE_ENCODING
        summary['original_lineage_expanded_sha256'] = digest(provenance)
    if source['original_driving_input'].get('schema_version') == LOG_SELECTION_SCHEMA:
        summary['library_metadata'] = source['library_metadata']
    if len(canonical(summary)) > 128*1024:
        raise ValueError('Recognized source provenance summary exceeds 128 KiB')
    writer.add_metadata('axiom.source_provenance', {
        'schema_version': 'axiom.source-provenance/1', 'use_scope': 'isolated_noncommercial_research',
        'commercial_use': 'false', 'source_dataset': 'nuScenes mini', 'converter_version': VERSION,
        'lineage_json': canonical(summary).decode()})
    writer.add_metadata('axiom.original_lineage', {'schema_version': 'axiom.original-lineage/1',
                                               'lineage_json': full_lineage.decode()})
    ordinals = []
    for index, frame in enumerate(frames):
        at = int(frame['at_ns'])
        tracks = ObjectTracks.model_validate(frame['objects']).model_dump(mode='json')
        ego = PlanningEgo.model_validate(frame['ego']).model_dump(mode='json')
        writer.add_message(oc, at, canonical(tracks), at, sequence=index)
        writer.add_message(ec, at, canonical(ego), at, sequence=index)
        ordinals.append(index*2+1)
    writer.finish()
    raw = output.getvalue()
    return raw, {'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw),
                 'ego_channel_id': ec, 'object_channel_id': oc, 'ego_ordinals': ordinals,
                 'world_frame': frames[0]['ego']['frame_id'], 'frame_count': len(frames)}
