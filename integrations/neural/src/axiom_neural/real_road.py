"""Read only operator-registered, hash-pinned real-road source packages.

The browser supplies an opaque source ID, never a filesystem path. These checks
preserve source identity and research scope; they do not establish render quality.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import zipfile

HASH = re.compile(r'[0-9a-f]{64}\Z')
IDENTITY = re.compile(r'[A-Za-z0-9_-]{1,100}\Z')
NS = re.compile(r'(0|[1-9][0-9]{0,19})\Z')
JSON_LIMIT = 4 * 1024 ** 2
DATASET_LIMIT = 512 * 1024 ** 2


def _sha(path, maximum):
    if not path.is_file() or not 0 < path.stat().st_size <= maximum:
        raise ValueError('Registered Neural source file is missing or exceeds its byte limit')
    checksum, total = hashlib.sha256(), 0
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 ** 2), b''):
            total += len(chunk)
            if total > maximum:
                raise ValueError('Registered Neural source grew beyond its byte limit')
            checksum.update(chunk)
    return checksum.hexdigest()


def _json(raw):
    if len(raw) > JSON_LIMIT:
        raise ValueError('Neural source JSON exceeds its byte limit')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Neural source JSON contains duplicate keys')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON number')))
    if not isinstance(value, dict):
        raise ValueError('Neural source JSON must be an object')
    return value


def _hash(value):
    return isinstance(value, str) and HASH.fullmatch(value)


def _ns(value):
    if not isinstance(value, str) or not NS.fullmatch(value) or int(value) >= 2 ** 64:
        raise ValueError('Real-road source times must be exact uint64 decimal strings')
    return int(value)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _pose(value):
    if (not isinstance(value, list) or len(value) != 4
            or any(not isinstance(row, list) or len(row) != 4 or not all(_finite(x) for x in row) for row in value)
            or value[3] != [0, 0, 0, 1]):
        raise ValueError('Real-road camera poses must be finite homogeneous 4x4 matrices')


def _validate_manifest(source, source_id, dataset_sha256):
    if (source.get('schema_version') != 'neural-real-road-source/1' or source.get('source_id') != source_id
            or source.get('source_kind') != 'real_road' or source.get('fidelity') != 'recorded_real_road'
            or source.get('license_scope') != 'noncommercial_research_only'
            or source.get('external_publication_cleared') is not False
            or source.get('dataset_sha256') != dataset_sha256):
        raise ValueError('Real-road source identity, dataset hash or research-only scope differs')
    if (not isinstance(source.get('recording_id'), str) or not IDENTITY.fullmatch(source['recording_id'])
            or not _hash(source.get('recording_sha256')) or type(source.get('channel_id')) is not int
            or not 0 <= source['channel_id'] <= 65535):
        raise ValueError('Real-road source needs an exact imported recording, channel and SHA-256')
    start, end = _ns(source.get('start_time_ns')), _ns(source.get('end_time_ns'))
    count = source.get('frame_count')
    if (type(count) is not int or not 3 <= count <= 200 or (source.get('width'), source.get('height')) != (640, 360)
            or not _finite(source.get('fps')) or not 0 < source['fps'] <= 30
            or not isinstance(source.get('frames'), list) or len(source['frames']) != count):
        raise ValueError('Real-road source requires 3–200 front RGB frames at 640x360 and bounded FPS')
    previous, ordinals = -1, set()
    for index, frame in enumerate(source['frames']):
        timestamp = _ns(frame.get('log_time_ns'))
        if (type(frame.get('frame_index')) is not int or frame['frame_index'] != index
                or type(frame.get('source_frame')) is not int or frame['source_frame'] != index
                or frame.get('file_path') != f'images/{index:06d}.png'
                or not _hash(frame.get('png_sha256')) or not _hash(frame.get('original_image_sha256'))
                or type(frame.get('recording_ordinal')) is not int or not 0 <= frame['recording_ordinal'] <= 100000
                or frame['recording_ordinal'] in ordinals or not start <= timestamp <= end or timestamp <= previous
                or not _finite(frame.get('time_s')) or abs(frame['time_s'] - (timestamp - start) / 1e9) > 1e-8):
            raise ValueError('Real-road frame order, PNG, original recording mapping or source time differs')
        for key in ('sample_token', 'sample_data_token', 'calibrated_sensor_token', 'ego_pose_token'):
            if not isinstance(frame.get(key), str) or not frame[key].strip():
                raise ValueError('Real-road frame is missing original dataset/pose/calibration lineage')
        _pose(frame.get('camera_to_world'))
        previous = timestamp
        ordinals.add(frame['recording_ordinal'])
    if _ns(source['frames'][0]['log_time_ns']) != start or previous != end:
        raise ValueError('Real-road interval must match the exact first and last selected camera frames')


def load_source(source_id):
    """Return (public source manifest, verified manifest Path, verified dataset Path)."""
    if not isinstance(source_id, str) or not IDENTITY.fullmatch(source_id):
        raise ValueError('Unknown real-road source ID')
    configured = os.environ.get('AXIOM_NEURAL_REAL_ROAD_SOURCES')
    if not configured:
        raise ValueError('Configure AXIOM_NEURAL_REAL_ROAD_SOURCES with operator-owned source packages')
    catalog_path = Path(configured)
    _sha(catalog_path, JSON_LIMIT)
    catalog = _json(catalog_path.read_bytes())
    if (set(catalog) != {'schema_version', 'sources'} or catalog['schema_version'] != 'neural-real-road-catalog/1'
            or not isinstance(catalog['sources'], list) or not 1 <= len(catalog['sources']) <= 100):
        raise ValueError('Unsupported real-road source catalog')
    ids = [entry.get('source_id') for entry in catalog['sources'] if isinstance(entry, dict)]
    if len(ids) != len(catalog['sources']) or len(set(ids)) != len(ids):
        raise ValueError('Real-road source catalog contains ambiguous IDs')
    entry = next((entry for entry in catalog['sources'] if entry['source_id'] == source_id), None)
    if entry is None:
        raise ValueError('Real-road source ID is not registered by the operator')
    if set(entry) != {'source_id', 'manifest_path', 'dataset_path', 'manifest_sha256', 'dataset_sha256'}:
        raise ValueError('Unsupported real-road source catalog entry')
    paths = []
    for kind, maximum in (('manifest', JSON_LIMIT), ('dataset', DATASET_LIMIT)):
        if not isinstance(entry[kind + '_path'], str) or not _hash(entry[kind + '_sha256']):
            raise ValueError('Real-road source catalog requires absolute paths and SHA-256 values')
        path = Path(entry[kind + '_path'])
        if not path.is_absolute() or _sha(path, maximum) != entry[kind + '_sha256']:
            raise ValueError('Real-road source file differs from its registered SHA-256')
        paths.append(path)
    manifest_path, dataset_path = paths
    source = _json(manifest_path.read_bytes())
    _validate_manifest(source, source_id, entry['dataset_sha256'])
    expected = {frame['file_path'] for frame in source['frames']} | {'transforms.json', 'points.ply', 'neural-dataset.json'}
    with zipfile.ZipFile(dataset_path) as archive:
        members = archive.infolist()
        if (len(members) != len(expected) or {item.filename for item in members} != expected
                or any(item.is_dir() or item.compress_type != zipfile.ZIP_STORED or item.flag_bits & 1 for item in members)
                or sum(item.file_size for item in members) > DATASET_LIMIT):
            raise ValueError('Real-road dataset ZIP must contain exactly the unencrypted stored dataset files')
        for frame in source['frames']:
            member = archive.getinfo(frame['file_path'])
            if not 33 <= member.file_size <= 4 * 1024 ** 2:
                raise ValueError('Real-road frame PNG exceeds its byte limit')
            raw = archive.read(member)
            if (hashlib.sha256(raw).hexdigest() != frame['png_sha256']
                    or raw[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
                    or struct.unpack('>II', raw[16:24]) != (640, 360) or raw[24:26] != b'\x08\x02'):
                raise ValueError('Real-road dataset PNG hash or RGB dimensions differ')
        for name in ('transforms.json', 'neural-dataset.json'):
            if archive.getinfo(name).file_size > JSON_LIMIT:
                raise ValueError('Real-road dataset JSON exceeds its byte limit')
        evidence = _json(archive.read('neural-dataset.json'))
        if evidence.get('schema_version') != 'neural-dataset/2' or len(evidence.get('frames', [])) != source['frame_count']:
            raise ValueError('Real-road dataset evidence has a different frame denominator')
        for original, dataset in zip(source['frames'], evidence['frames']):
            if any(original.get(key) != dataset.get(key) for key in ('frame_index', 'file_path', 'png_sha256',
                    'original_image_sha256', 'log_time_ns', 'time_s', 'camera_to_world', 'recording_ordinal')):
                raise ValueError('Public source and dataset frame lineage differ')
        transforms = _json(archive.read('transforms.json'))
        if (transforms.get('w'), transforms.get('h')) != (640, 360) or len(transforms.get('frames', [])) != source['frame_count']:
            raise ValueError('Real-road transforms have different image dimensions or frame count')
        for frame, transformed in zip(source['frames'], transforms['frames']):
            if transformed.get('file_path') != frame['file_path'] or transformed.get('transform_matrix') != frame['camera_to_world']:
                raise ValueError('Real-road transforms differ from the pinned camera poses')
    return source, manifest_path, dataset_path
