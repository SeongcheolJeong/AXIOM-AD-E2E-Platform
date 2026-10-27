"""Prepare a bounded library from the ten already acquired nuScenes mini scenes.

Originals are read once over an existing SSH connection or locally. Every sample
gets a retained/excluded receipt. Valid contiguous runs become separate sources;
byte-bound splits are explicit and never discard objects or inflate scene counts.
This uses nuscenes-log-selection/1, leaving VAD's driving-input/1 unchanged.
"""
from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

from prepare_log_sim_road import ROOT, canonical, sha, write_collected

MAX_TRANSFER_LINE_BYTES = 64 * 1024 * 1024


def valid_runs(frames):
    """Preserve original order and split around exclusions or time discontinuity."""
    runs, current = [], []
    for frame in frames:
        if frame['reasons']:
            if current:
                runs.append(current)
                current = []
            continue
        if current and (frame['sample_start_index'] != current[-1]['sample_start_index'] + 1
                        or frame['timestamp_us'] - current[-1]['timestamp_us'] > 1_000_000):
            runs.append(current)
            current = []
        current.append(frame)
    if current:
        runs.append(current)
    return runs


def deterministic_parts(items):
    """Balanced consecutive parts; never duplicate a boundary frame."""
    middle = len(items) // 2
    return items[:middle], items[middle:]


def collect_library(request, emit):
    """Self-contained read-only remote collector; emit one bounded JSONL item."""
    import base64
    import bisect
    from collections import defaultdict
    import hashlib
    import io
    import json
    import math
    from pathlib import Path
    import sys
    import types
    from PIL import Image

    converter = types.ModuleType('log_sim_library_converter')
    sys.modules[converter.__name__] = converter
    exec(compile(request['converter_source'], converter.__name__, 'exec'), converter.__dict__)
    root, archive = Path(request['data_root']), Path(request['archive'])
    approved = request['approved_input']
    if (approved['dataset']['version'] != 'v1.0-mini'
            or approved['dataset']['use_scope'] != 'research_isolated'
            or not approved['dataset'].get('rights_note')):
        raise ValueError('Existing approved isolated research input is required')
    context = converter.collect_source_context(root, archive, expected_archive_sha256=request['archive_sha256'])
    if context['table_digest'] != approved['dataset']['metadata_sha256']:
        raise ValueError('Original metadata differs from the existing approved dataset')
    tables = context['tables']
    scenes = sorted(tables['scene'].values(), key=lambda row: (row['name'], row['token']))
    if len(scenes) != 10:
        raise ValueError('Expected the ten original mini scenes')
    converter_hash = hashlib.sha256(request['converter_source'].encode()).hexdigest()
    geometry = {'length_m': 4.5, 'width_m': 1.9, 'origin_offset_xy_m': [0., 0.],
                'provenance': 'authored_reference_footprint', 'original_measured_geometry_available': False}
    annotations, channels = defaultdict(list), defaultdict(dict)
    for row in tables['sample_annotation'].values():
        annotations[row['sample_token']].append(row)
    for row in tables['sample_data'].values():
        if row['is_key_frame']:
            calibration = tables['calibrated_sensor'][row['calibrated_sensor_token']]
            channel = tables['sensor'][calibration['sensor_token']]['channel']
            if channel in channels[row['sample_token']]:
                raise ValueError('Original sample has ambiguous duplicate keyframe channel')
            channels[row['sample_token']][channel] = row
    emit({'type': 'library', 'schema_version': 'log-sim-library/1', 'scene_count': len(scenes),
          'source_scope': 'isolated_noncommercial_research', 'commercial_use': False,
          'dataset': 'nuScenes mini', 'original_scene_table': context['identities']['scene'],
          'original_metadata_sha256': context['table_digest'],
          'original_archive': {'sha256': context['archive_sha'], 'size_bytes': context['archive_size']},
          'approved_input_sha256': request['approved_input_sha256'],
          'converter_source_sha256': converter_hash, 'collector_source_sha256': request['collector_source_sha256'],
          'selection_schema': 'nuscenes-log-selection/1',
          'bounds': {'frames_per_segment': 64, 'objects_per_frame': 128,
                     'compact_lineage_bytes': 1048576, 'expanded_lineage_bytes': 16777216}})
    for scene in scenes:
        samples, current = [], tables['sample'][scene['first_sample_token']]
        while True:
            if current['scene_token'] != scene['token'] or any(row['token'] == current['token'] for row in samples):
                raise ValueError('Invalid original scene sample chain')
            samples.append(current)
            if not current['next']:
                break
            current = tables['sample'][current['next']]
        if len(samples) != scene['nbr_samples'] or samples[-1]['token'] != scene['last_sample_token']:
            raise ValueError('Original scene denominator differs from its sample chain')
        can_rows, can_receipt = converter._read(root / 'can_bus' / (scene['name'] + '_pose.json'))
        if (not can_rows or any(type(row.get('utime')) is not int for row in can_rows)
                or any(b['utime'] <= a['utime'] for a, b in zip(can_rows, can_rows[1:]))):
            raise ValueError('Original scene CAN chronology is invalid')
        can_times = [row['utime'] for row in can_rows]
        first_us = samples[0]['timestamp']
        scene_info = {'scene_token': scene['token'], 'name': scene['name'], 'description': scene['description'],
            'original_scene': scene, 'original_frame_count': len(samples),
            'duration_s': (samples[-1]['timestamp'] - first_us) / 1e6,
            'original_start_timestamp_us': first_us, 'original_end_timestamp_us': samples[-1]['timestamp'],
            'original_location': tables['log'][scene['log_token']]['location'],
            'description_source': 'original_scene.description', 'excluded': [], 'segments': [], 'unsupported': [],
            'frames': []}
        collected_frames = []
        for index, sample in enumerate(samples):
            at, reasons = sample['timestamp'], []
            lidar = channels[sample['token']].get('LIDAR_TOP')
            camera = channels[sample['token']].get('CAM_FRONT')
            count = len(annotations[sample['token']])
            if not 1 <= count <= 128:
                reasons.append('object_count_exceeds_128' if count > 128 else 'empty_original_objects')
            can_index = bisect.bisect_right(can_times, at) - 1
            can = can_rows[can_index] if can_index >= 0 else None
            if can is None:
                reasons.append('no_causal_CAN_before_anchor')
            elif at - can['utime'] > 100000:
                reasons.append('causal_CAN_older_than_100ms')
            elif can['vel'][0] < 0:
                reasons.append('reverse_ego_motion_unsupported')
            if not lidar:
                reasons.append('missing_original_LIDAR_TOP_keyframe')
            elif at != lidar['timestamp'] or tables['ego_pose'][lidar['ego_pose_token']]['timestamp'] != at:
                reasons.append('ego_LiDAR_sample_anchor_mismatch')
            if not camera:
                reasons.append('missing_original_CAM_FRONT_keyframe')
            elif not 0 <= at - camera['timestamp'] <= 500000:
                reasons.append('no_causal_front_image_within_500ms')
            camera_record = None
            if camera:
                image_path = (root / camera['filename']).resolve()
                try:
                    image_path.relative_to(root.resolve())
                    if not image_path.is_file() or image_path.stat().st_size > 8 * 1024 * 1024:
                        raise ValueError('missing or oversized JPEG')
                    raw = image_path.read_bytes()
                    with Image.open(io.BytesIO(raw)) as image:
                        if image.format != 'JPEG' or image.size != (camera['width'], camera['height']):
                            raise ValueError('original JPEG shape differs')
                        image.verify()
                    camera_record = {'name': 'CAM_FRONT', 'path': camera['filename'],
                        'timestamp_us': camera['timestamp'], 'width': camera['width'], 'height': camera['height'],
                        'sample_data_token': camera['token'], 'sample_token': sample['token'],
                        'sha256': hashlib.sha256(raw).hexdigest(), 'data_base64': base64.b64encode(raw).decode(),
                        'original_sample_data': camera,
                        'original_calibrated_sensor': tables['calibrated_sensor'][camera['calibrated_sensor_token']],
                        'original_ego_pose': tables['ego_pose'][camera['ego_pose_token']]}
                except (OSError, ValueError) as error:
                    reasons.append('original_front_image_unavailable_or_invalid: ' + str(error))
            frame_info = {'sample_start_index': index, 'sample_token': sample['token'], 'timestamp_us': at,
                          'time_s': (at - first_us) / 1e6, 'object_count': count, 'reasons': reasons}
            if reasons:
                scene_info['excluded'].append({**frame_info, 'reason': '; '.join(reasons)})
            collected_frames.append({**frame_info, 'sample': sample, 'lidar': lidar, 'camera': camera_record, 'can': can})
            scene_info['frames'].append(frame_info)

        def make_segment(run, split_reasons):
            first, last = run[0], run[-1]
            metadata = {'scene_token': scene['token'], 'name': scene['name'], 'scene_name': scene['name'],
                'description': scene['description'], 'original_scene_record_sha256': converter.digest(scene),
                'original_frame_count': len(samples), 'sample_start_index': first['sample_start_index'],
                'sample_end_index': last['sample_start_index'], 'frame_count': len(run),
                'start_time_s': first['time_s'], 'end_time_s': last['time_s'],
                'duration_s': (last['timestamp_us'] - first['timestamp_us']) / 1e6,
                'split_reasons': split_reasons, 'original_scene_duration_s': scene_info['duration_s'],
                'original_location': scene_info['original_location']}
            if len(run) < 2:
                frame = run[0]
                frame['reasons'].append('isolated_valid_frame_requires_two')
                scene_info['excluded'].append({key: frame[key] for key in
                    ('sample_start_index', 'sample_token', 'timestamp_us', 'time_s', 'object_count', 'reasons')}
                    | {'reason': 'isolated_valid_frame_requires_two'})
                return
            if len(run) > 64:
                left, right = deterministic_parts(run)
                make_segment(left, split_reasons + ['frame_count_bound_64'])
                make_segment(right, split_reasons + ['frame_count_bound_64'])
                return
            selection = {'schema_version': 'nuscenes-log-selection/1',
                'dataset': {**approved['dataset'], 'scene_token': scene['token'], 'scene_name': scene['name']},
                'selection': metadata, 'frames': []}
            for frame in run:
                selection['frames'].append({'sample_token': frame['sample_token'], 'timestamp_us': frame['timestamp_us'],
                    'lidar_frame': {'sample_data_token': frame['lidar']['token'],
                                    'ego_pose_token': frame['lidar']['ego_pose_token'],
                                    'calibration_token': frame['lidar']['calibrated_sensor_token']},
                    'can_bus_source': {'sha256': can_receipt['sha256'], 'timestamp_us': frame['can']['utime']},
                    'front_camera': {key: value for key, value in frame['camera'].items() if key != 'data_base64'}})
            try:
                bundle = converter.collect_source_bundle(root, archive, converter.canonical(selection),
                    expected_archive_sha256=request['archive_sha256'], converter_source_sha256=converter_hash,
                    ego_geometry=geometry, compact=True, source_context=context)
                frames, lineage = converter.convert_bundle(bundle)
                converter.compact_lineage(lineage)
            except ValueError as error:
                reason = str(error)
                if ('lineage exceeds' in reason or 'selection bytes' in reason) and len(run) >= 4:
                    left, right = deterministic_parts(run)
                    make_segment(left, split_reasons + ['lineage_byte_bound'])
                    make_segment(right, split_reasons + ['lineage_byte_bound'])
                else:
                    scene_info['unsupported'].append({**metadata, 'reason': reason})
                return
            identity = '{}-{:03d}-{:03d}'.format(scene['name'], first['sample_start_index'], last['sample_start_index'])
            collected = {'bundle': bundle, 'cameras': [frame['camera'] for frame in run],
                'approved_input_sha256': request['approved_input_sha256'],
                'collector_source_sha256': request['collector_source_sha256'], 'selected_frame_count': len(run)}
            segment = {**metadata, 'id': identity, 'sample_tokens': [frame['sample_token'] for frame in run],
                       'object_counts': [frame['object_count'] for frame in run]}
            emit({'type': 'segment', 'scene_token': scene['token'], 'segment': segment, 'collected': collected})
            scene_info['segments'].append(segment)

        for run in valid_runs(collected_frames):
            make_segment(run, ['invalid_original_frame_gap'] if scene_info['excluded'] else [])
        retained = {token for segment in scene_info['segments'] for token in segment['sample_tokens']}
        scene_info['retained_frame_count'] = len(retained)
        scene_info['excluded_frame_count'] = len(scene_info['excluded'])
        scene_info['unsupported_frame_count'] = sum(segment['frame_count'] for segment in scene_info['unsupported'])
        if len(retained) + scene_info['excluded_frame_count'] + scene_info['unsupported_frame_count'] != len(samples):
            raise ValueError('Original scene denominator is not fully accounted for')
        emit({'type': 'scene', 'scene': scene_info})
    emit({'type': 'complete'})


def prepare_library(request, output: Path, *, ssh: str | None = None):
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'schema_version': 'log-sim-library/1', 'status': 'preparing', 'scenes': []}
    segments = {}

    def receive(item):
        if item['type'] == 'library':
            manifest.update({key: value for key, value in item.items() if key != 'type'})
        elif item['type'] == 'segment':
            segment = item['segment']
            result = write_collected(item['collected'], output / segment['id'])
            entry = {**segment, **result, 'path': result['mcap'], 'status': 'ready'}
            segments[segment['id']] = entry
            (output / segment['id'] / 'library-metadata.json').write_bytes(canonical(entry))
            print(json.dumps({'status': 'segment_ready', 'id': segment['id'], 'path': result['mcap'],
                              'proposal': result['proposal'], 'frame_count': entry['frame_count']}), flush=True)
        elif item['type'] == 'scene':
            scene = item['scene']
            scene['segments'] = [segments[segment['id']] for segment in scene['segments']]
            manifest['scenes'].append(scene)
            print(json.dumps({'status': 'scene_ready', 'name': scene['name'],
                              'retained_frames': scene['retained_frame_count'],
                              'excluded_frames': scene['excluded_frame_count'],
                              'unsupported_frames': scene['unsupported_frame_count']}), flush=True)
        elif item['type'] == 'complete':
            manifest['status'] = 'complete'
            manifest['segment_count'] = len(segments)
            manifest['retained_frame_count'] = sum(scene['retained_frame_count'] for scene in manifest['scenes'])
            manifest['original_frame_count'] = sum(scene['original_frame_count'] for scene in manifest['scenes'])
            manifest['excluded_frame_count'] = sum(scene['excluded_frame_count'] for scene in manifest['scenes'])
            manifest['unsupported_frame_count'] = sum(scene['unsupported_frame_count'] for scene in manifest['scenes'])
        (output / 'library-progress.json').write_bytes(canonical(manifest))

    if ssh:
        if ssh.startswith('-') or any(character.isspace() for character in ssh):
            raise ValueError('Use an explicit existing SSH destination')
        program = '\n'.join(inspect.getsource(function) for function in
                            (valid_runs, deterministic_parts, collect_library))
        program += "\nimport json,sys\ncollect_library(json.load(sys.stdin),lambda value:print(json.dumps(value,separators=(',',':')),flush=True))\n"
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', ssh,
                                        'python3 -c ' + shlex.quote(program)],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
            try:
                process.stdin.write(canonical(request))
                process.stdin.close()
                while line := process.stdout.readline(MAX_TRANSFER_LINE_BYTES + 1):
                    if len(line) > MAX_TRANSFER_LINE_BYTES:
                        raise ValueError('Remote selected segment exceeds its transfer byte bound')
                    receive(json.loads(line))
                code = process.wait(timeout=60)
                if code:
                    errors.seek(0)
                    raise RuntimeError('Read-only original collection failed: ' + errors.read().decode(errors='replace')[-1800:])
            except BaseException:
                process.terminate()
                process.wait(timeout=15)
                raise
    else:
        collect_library(request, receive)
    if manifest['status'] != 'complete' or len(manifest['scenes']) != 10:
        raise ValueError('Library collection did not complete all ten original scenes')
    (output / 'library-manifest.json').write_bytes(canonical(manifest))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', required=True)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--archive-sha256', required=True)
    parser.add_argument('--approved-input', type=Path, required=True)
    parser.add_argument('--ssh')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    for path in (ROOT / 'backend/src', ROOT / 'integrations/nuscenes/src'):
        sys.path.insert(0, str(path))
    approved_raw = args.approved_input.read_bytes()
    request = {'data_root': args.data_root, 'archive': args.archive, 'archive_sha256': args.archive_sha256,
        'approved_input': json.loads(approved_raw), 'approved_input_sha256': sha(approved_raw),
        'collector_source_sha256': sha(Path(__file__).read_bytes()),
        'converter_source': (ROOT / 'integrations/nuscenes/src/axiom_nuscenes_planning/converter.py').read_text()}
    manifest = prepare_library(request, args.output, ssh=args.ssh)
    print(json.dumps({key: manifest[key] for key in ('status', 'scene_count', 'segment_count',
        'original_frame_count', 'retained_frame_count', 'excluded_frame_count', 'unsupported_frame_count')}, indent=2))


if __name__ == '__main__':
    main()
