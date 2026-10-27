"""Bounded checks of downloaded Neural artifacts before durable publication.

These are file/lineage/pose contracts, not reconstruction quality evaluation.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import zipfile

import numpy as np
from .data import SCALE, SWEEP_FRAME_COUNT, camera_sweep_lateral_m, changed_pose, matrix, sha_file

HASH = re.compile(r'[a-f0-9]{64}')
PNG_HEADER = b'\x89PNG\r\n\x1a\n'


def _json(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 4 * 1024 ** 2:
        raise ValueError('Neural JSON artifact is missing or oversized')
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError('Neural JSON artifact must be an object')
    return value


def _same_summary(document, summary, schema_version):
    if document.get('schema_version') != schema_version:
        raise ValueError('Neural result schema differs from its summary')
    if any(document.get(key) != value for key, value in summary.items()
           if key not in ('schema_version', 'elapsed_seconds')):
        raise ValueError('Neural result artifact differs from its native summary')


def validate_downloaded_output(output, directory, *, source_manifest=None):
    """Check checkpoint/result consistency and every bounded published PNG byte."""
    directory = Path(directory)
    summary = output['summary']
    real = summary.get('source_kind') == 'real_road'
    if real:
        if not isinstance(source_manifest, dict) or source_manifest.get('schema_version') != 'neural-real-road-source/1':
            raise ValueError('Real-road output requires the sealed source manifest')
        for key in ('source_id', 'recording_id', 'channel_id', 'recording_sha256', 'source_use_scope'):
            if summary.get(key) != source_manifest.get(key):
                raise ValueError('Real-road source anchor differs from output')
    if summary['schema_version'] == 'neural-reconstruction-result/1':
        _same_summary(_json(directory / 'neural-reconstruction-result.json'), summary, 'neural-reconstruction-result/1')
        if sha_file(directory / 'neural-checkpoint.ckpt') != summary['checkpoint_sha256']:
            raise ValueError('Published checkpoint hash differs from the trained scene')
        evidence = _json(directory / 'neural-dataset.json')
        if real:
            if (evidence.get('schema_version') != 'neural-dataset/2'
                    or evidence.get('source_id') != source_manifest['source_id']
                    or evidence.get('frames') != source_manifest['frames']
                    or evidence.get('recording_sha256') != source_manifest['recording_sha256']):
                raise ValueError('Published real-road dataset differs from the sealed source')
        elif (evidence.get('schema_version') != 'neural-dataset/1'
                or evidence.get('source_execution_id') != summary['source_execution_id']
                or evidence.get('source_manifest_sha256') != summary['source_manifest_sha256']
                or len(evidence.get('frames', [])) != summary['frame_count']):
            raise ValueError('Published dataset identity differs from the trained scene')
        return
    document = _json(directory / 'neural-render-result.json')
    sweep = summary['mode'] == 'camera_sweep'
    _same_summary(document, summary, 'neural-render-result/2' if sweep else 'neural-render-result/1')
    archive_path = directory / 'neural-render-frames.zip'
    if document.get('frames_archive_sha256') != sha_file(archive_path):
        raise ValueError('Neural frames archive hash differs from the render result')
    frames = document.get('frames')
    indices = list(range(SWEEP_FRAME_COUNT)) if sweep else list(range(source_manifest['frame_count'] if real else 200)) if summary['mode'] == 'sequence' else [summary['frame_index']]
    if not isinstance(frames, list) or len(frames) != len(indices) or summary['frame_count'] != len(indices):
        raise ValueError('Neural frame count differs from the requested rendering mode')
    expected = {}
    first = frames[0]
    for frame, index in zip(frames, indices):
        source_row = source_manifest['frames'][summary['frame_index'] if sweep else index] if real else None
        expected_source_time = source_row['time_s'] if real else summary['frame_index'] / 10
        expected_time = index / 10 if sweep or not real else source_row['time_s']
        if (type(frame.get('frame_index')) is not int or frame['frame_index'] != index
                or type(frame.get('source_frame')) is not int or frame['source_frame'] < 0
                or type(frame.get('time_s')) not in (int, float)
                or not math.isclose(frame['time_s'], expected_time, abs_tol=1e-6)):
            raise ValueError('Neural output frame identity or time differs')
        baseline = matrix(frame['baseline_camera_to_world'])
        novel = matrix(frame['novel_camera_to_world'])
        if real:
            if (any(frame.get(key) != source_row.get(key) for key in
                    ('recording_ordinal', 'log_time_ns', 'sample_data_token', 'original_image_sha256', 'source_frame'))
                    or frame.get('source_frame_index') != source_row['frame_index']
                    or frame.get('source_time_s') != source_row['time_s']
                    or frame.get('original', {}).get('sha256') != source_row['png_sha256']
                    or not np.array_equal(baseline, np.asarray(source_row['camera_to_world']))):
                raise ValueError('Rendered frame differs from its real-road recording anchor')
        lateral = camera_sweep_lateral_m(index, summary['lateral_m']) if sweep else summary['lateral_m']
        if sweep:
            if (type(frame.get('source_frame_index')) is not int
                    or frame['source_frame_index'] != summary['frame_index']
                    or summary.get('source_frame_index') != summary['frame_index']
                    or type(frame.get('source_time_s')) not in (int, float)
                    or not math.isclose(frame['source_time_s'], expected_source_time, abs_tol=1e-8)
                    or summary.get('source_time_s') != expected_source_time
                    or frame['source_frame'] != first['source_frame']
                    or type(frame.get('applied_lateral_m')) not in (int, float)
                    or not math.isclose(frame['applied_lateral_m'], lateral, abs_tol=1e-8)
                    or frame.get('applied_height_m') != summary['height_m']
                    or not np.array_equal(baseline, np.asarray(first['baseline_camera_to_world']))):
                raise ValueError('Frozen source identity or camera sweep position differs')
        if not np.allclose(novel, changed_pose(baseline, lateral, summary['height_m'], SCALE), rtol=0, atol=1e-8):
            raise ValueError('Neural output camera pose differs from the requested edit')
        for kind in ('original', 'baseline', 'novel'):
            item = frame[kind]
            name = f'{kind}/{index:06d}.png'
            if (item.get('name') != name or type(item.get('size_bytes')) is not int
                    or not 33 <= item['size_bytes'] <= 4 * 1024 ** 2
                    or not isinstance(item.get('sha256'), str) or not HASH.fullmatch(item['sha256'])):
                raise ValueError('Neural PNG identity, size or hash is invalid')
            if sweep and kind in ('original', 'baseline') and item['sha256'] != first[kind]['sha256']:
                raise ValueError('Camera sweep must preserve the frozen original and baseline pixels')
            expected[name] = item
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if len(members) != len(expected) or {item.filename for item in members} != set(expected):
            raise ValueError('Neural frames archive has missing, extra or duplicate members')
        for member in members:
            item = expected[member.filename]
            if (member.is_dir() or member.compress_type != zipfile.ZIP_STORED
                    or member.file_size != item['size_bytes'] or member.flag_bits & 1):
                raise ValueError('Neural frames must use unencrypted ZIP_STORED PNG files')
            raw = archive.read(member)
            if hashlib.sha256(raw).hexdigest() != item['sha256']:
                raise ValueError('Neural PNG bytes differ from their recorded hash')
            if (raw[:8] != PNG_HEADER or raw[8:16] != b'\x00\x00\x00\rIHDR'
                    or struct.unpack('>II', raw[16:24]) != (summary['width'], summary['height'])
                    or raw[24:26] != b'\x08\x02'):
                raise ValueError('Neural PNG header must describe the expected 8-bit RGB image')
