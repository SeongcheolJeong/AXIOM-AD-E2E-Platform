#!/usr/bin/env python3
"""Verify sealed local city evidence and export lossless presentation derivatives.

SQLite is opened read-only. Artifact paths come from the artifact table and are
checked against the sealed execution manifest; camera members are never guessed.
No engine, API, source DB, or original artifact is modified.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlencode
import zipfile
import zlib

from PIL import Image, __version__ as PIL_VERSION


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def digest_file(path):
    checksum = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
            size += len(chunk)
    return checksum.hexdigest(), size


def preserve(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        require(path.read_bytes() == raw, f'Existing derivative differs; choose another output: {path}')
    else:
        with path.open('xb') as stream:
            stream.write(raw)


def write_json(path, value):
    preserve(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode() + b'\n')


def relative(path, reports):
    return path.relative_to(reports).as_posix()


def pose(actor):
    return {key: actor[key] for key in ('x', 'y', 'z', 'heading_rad')}


def compact_actor(actor):
    keys = ('id', 'x', 'y', 'z', 'heading_rad', 'speed_mps', 'desired_speed_mps',
            'route_error_m', 'route_progress_m', 'goal_distance_m', 'controller_state', 'control')
    return {key: actor[key] for key in keys if key in actor}


def export_run(connection, runtime, output, ui_base, key, filename):
    receipt_path = (runtime / filename).resolve()
    exported_raw = receipt_path.read_bytes()
    exported = json.loads(exported_raw)
    identity = exported['id']
    subject = connection.execute('SELECT * FROM experiment_executions WHERE job_id=?', (identity,)).fetchone()
    job = connection.execute('SELECT * FROM execution_jobs WHERE id=?', (identity,)).fetchone()
    require(subject is not None and job is not None, 'Execution missing from source database')
    require(job['status'] == 'completed' and not job['outcome_uncertain'], 'Execution is not certain and completed')
    sealed_raw = bytes(subject['manifest'])
    require(sha(sealed_raw) == subject['manifest_hash'] == exported['manifest_hash'], 'Sealed execution hash mismatch')
    sealed = json.loads(sealed_raw)
    execution, prepared = sealed['execution'], sealed['prepared']
    require(execution.get('manifest_hash') is None and execution.get('download_url') is None,
            'Unexpected self-reference in sealed execution')
    require(exported | {'manifest_hash': None, 'download_url': None} == execution,
            'Exported job JSON differs from sealed execution')
    require(json.loads(job['prepared']) == prepared, 'Current prepared input differs from sealed execution')
    require(execution['fidelity'] == 'native_carla_object_physics', 'Unexpected execution fidelity')
    require(execution['summary']['cleanup_status'] == 'confirmed', 'Native cleanup is not confirmed')
    artifacts = connection.execute('SELECT * FROM experiment_artifacts WHERE job_id=?', (identity,)).fetchall()
    descriptor_keys = ('id', 'name', 'media_type', 'sha256', 'size_bytes')
    descriptor_by_id = {item['id']: item for item in execution['artifacts']}
    require({item['id'] for item in artifacts} == set(descriptor_by_id), 'Artifact closure mismatch')
    source_root = (runtime / 'data/experiments/artifacts').resolve()
    paths, verified_artifacts = {}, []
    for row in artifacts:
        descriptor = {field: row[field] for field in descriptor_keys}
        require(descriptor == {field: descriptor_by_id[row['id']][field] for field in descriptor_keys},
                'Artifact row differs from sealed manifest')
        path = source_root / row['storage_key']
        require(not path.is_symlink() and path.is_file() and path.resolve().is_relative_to(source_root),
                'Artifact storage path escapes its recorded store')
        require(digest_file(path) == (row['sha256'], row['size_bytes']), 'Artifact bytes differ: ' + row['name'])
        require(row['name'] not in paths, 'Duplicate artifact name')
        paths[row['name']] = path
        verified_artifacts.append(descriptor)

    scene = json.loads(paths['city-scene.json'].read_bytes())
    evaluation = json.loads(paths['city-evaluation.json'].read_bytes())
    snapshot = prepared['input']
    scenario = snapshot['scenario']
    expected_source = snapshot['source'] | dict(adapter_version=execution['adapter_version'],
        control_sha256=snapshot['control_script_sha256'], engine_version=snapshot['server_version'],
        maneuver_sha256=snapshot['maneuver_script_sha256'], runner_sha256=snapshot['native_script_sha256'],
        xodr_sha256=snapshot['map']['opendrive_sha256'])
    require(scene['source'] == expected_source and snapshot['source'] == execution['summary']['source'],
            'Scene/source pins differ')
    require(scene['map']['external'] == snapshot['external_map_provenance'], 'External map provenance differs')
    require(sha(paths['map.xodr'].read_bytes()) == scene['map']['opendrive_sha256'] == snapshot['map']['opendrive_sha256'],
            'Map bytes differ from execution map')
    require(evaluation['source_scene_sha256'] == sha(paths['city-scene.json'].read_bytes()), 'Evaluation scene pin differs')
    packed = snapshot['map_definition_pack']
    require(packed['encoding'] == 'zlib+base64', 'Unexpected frozen map encoding')
    decoder = zlib.decompressobj()
    frozen_raw = decoder.decompress(base64.b64decode(packed['data'], validate=True), 64 * 1024**2 + 1)
    require(len(frozen_raw) <= 64 * 1024**2 and decoder.eof and not decoder.unused_data and sha(frozen_raw) == packed['sha256'],
            'Frozen native map pack mismatch')
    frozen_map = json.loads(frozen_raw)
    require(frozen_map['opendrive']['content'].encode() == paths['map.xodr'].read_bytes(), 'Frozen map content differs')
    require(scene['map']['nodes'] == frozen_map['nodes'], 'Scene native lane graph differs from frozen map')
    require(scene['routes'] == {a['id']: a['route'] for a in scenario['actors']}, 'Scene route differs from frozen authoring')

    records = scene['camera_records']
    require(records == [camera for frame in scene['frames'] for camera in frame['cameras']], 'Camera frame denominator mismatch')
    require(len(records) == execution['summary']['camera_frame_count'], 'Camera summary count mismatch')
    require(len(scene['sensors']) == 1 and scene['sensors'][0]['id'] == 'front', 'Exporter requires one front RGB stream')
    sensor = scene['sensors'][0]
    require(sensor['period_s'] == .2 and (sensor['width'], sensor['height']) == (640, 360), 'Expected native 640x360 at 5 Hz')
    expected_indices = list(range(0, len(scene['frames']), round(sensor['period_s'] / scenario['timestep_s'])))
    require([r['index'] for r in records] == expected_indices, 'Capture interval or frame closure mismatch')
    archives = {r['archive'] for r in records}
    require(archives == {name for name in paths if name.endswith('.zip')}, 'ZIP artifact closure mismatch')
    frames, frame_provenance = [], []
    run_output = output / key
    reports = output.parent
    with ExitStack() as stack:
        bundles = {name: stack.enter_context(zipfile.ZipFile(paths[name])) for name in archives}
        for name, bundle in bundles.items():
            infos = bundle.infolist()
            require(len(infos) == len({i.filename for i in infos}), 'Duplicate camera ZIP member')
            require({i.filename for i in infos} == {r['member'] for r in records if r['archive'] == name},
                    'Camera ZIP members differ from scene records')
        for number, record in enumerate(records):
            frame = scene['frames'][record['index']]
            require(record['frame'] == frame['frame'] and record['time_s'] == frame['time_s']
                    and record['simulation_time_s'] == frame['simulation_time_s'] and record['encoding'] == 'bgra8',
                    'Camera clock/encoding differs from response frame')
            require(record['width'] == 640 and record['height'] == 360 and record['size_bytes'] == 640 * 360 * 4,
                    'Camera dimensions or raw size differ')
            info = bundles[record['archive']].getinfo(record['member'])
            require(info.file_size == record['size_bytes'], 'Camera member size mismatch')
            with bundles[record['archive']].open(info) as member:
                raw = member.read(record['size_bytes'] + 1)
            require(len(raw) == record['size_bytes'] and sha(raw) == record['sha256'], 'Raw BGRA hash mismatch')
            image = Image.frombytes('RGBA', (640, 360), raw, 'raw', 'BGRA')
            buffer = BytesIO()
            image.save(buffer, format='PNG', compress_level=6)
            png = buffer.getvalue()
            with Image.open(BytesIO(png)) as decoded:
                require(decoded.size == (640, 360) and decoded.convert('RGBA').tobytes('raw', 'BGRA') == raw,
                        'Lossless PNG pixel roundtrip differs from raw BGRA')
            path = run_output / 'frames' / f'{number:06d}.png'
            preserve(path, png)
            actors = [compact_actor(a) for a in frame['actors']]
            ego = next(a for a in actors if a['id'] == 'ego')
            frames.append(dict(index=number, scene_index=record['index'], world_frame=record['frame'],
                time_s=record['time_s'], simulation_time_s=record['simulation_time_s'], src=relative(path, reports),
                width=640, height=360, actors=actors, pose=pose(ego), actual_speed_mps=ego['speed_mps'],
                target_speed_mps=ego.get('desired_speed_mps'), path_error_m=ego.get('route_error_m'),
                camera_world_pose=record['world_pose']))
            frame_provenance.append(dict(index=number, scene_index=record['index'], time_s=record['time_s'],
                archive=record['archive'], member=record['member'], raw_bgra_sha256=record['sha256'],
                raw_size_bytes=len(raw), png=relative(path, reports), png_sha256=sha(png), png_size_bytes=len(png),
                lossless_rgba_roundtrip_verified=True))

    nodes = scene['map']['nodes']
    by_id = {node['id']: node for node in nodes}
    edges = [[node['x'], node['y'], by_id[end]['x'], by_id[end]['y']] for node in nodes for end in node['next_ids']]
    map_view = dict(coordinate_system=scene['coordinate_system'], edges=edges, lanes=scene['map']['lanes'],
        bbox=[min(n['x'] for n in nodes), min(n['y'] for n in nodes), max(n['x'] for n in nodes), max(n['y'] for n in nodes)],
        opendrive_sha256=scene['map']['opendrive_sha256'], node_count=len(nodes))
    labels = {'straight': '직진 · 3 m/s', 'left_turn': '좌회전 · 3 m/s', 'right_turn': '우회전 · 3 m/s',
              'left_turn_2_ui': '좌회전 · UI 수정 2 m/s'}
    telemetry = [dict(index=f['index'], world_frame=f['frame'], time_s=f['time_s'],
                      actors=[compact_actor(a) for a in f['actors']]) for f in scene['frames']]
    run = dict(key=key, label=labels.get(key, key), execution_id=identity, manifest_hash=subject['manifest_hash'],
        fidelity=execution['fidelity'], verdict=evaluation['verdict'], evaluation=evaluation, metrics=scene['metrics'],
        source=scene['source'], map_ref=scenario['map'], external_map_provenance=scene['map']['external'],
        ui_url=ui_base.rstrip('/') + '/#object-sim?' + urlencode({'project': execution['project_id'], 'execution': identity}),
        frame_rate_hz=5, timestep_s=scenario['timestep_s'], duration_s=scenario['duration_s'],
        telemetry_frame_count=len(telemetry), camera_frame_count=len(frames), drop_count=execution['summary']['drop_count'],
        frames=frames, route=scene['routes']['ego'], routes=scene['routes'], map=map_view,
        physical_sensor_validation=False, limitations=scene['limitations'],
        summary_src=relative(run_output / 'native-scene-summary.json', reports))
    write_json(run_output / 'native-scene-summary.json', dict(schema_version='korea-map-native-scene-summary/1',
        **run, telemetry=telemetry, actors=scene['actors'], sensors=scene['sensors'],
        scope='presentation_derivative_of_sealed_native_simulation_not_real_road_or_sensor_validation'))
    provenance = dict(key=key, execution_id=identity, manifest_hash=subject['manifest_hash'], ui_url=run['ui_url'],
        source_receipt_filename=receipt_path.name, source_receipt_sha256=sha(exported_raw),
        artifact_locator='read-only experiment_artifacts.storage_key lookup; exact sealed artifact descriptors verified',
        artifacts=sorted(verified_artifacts, key=lambda a: a['name']), frames=frame_provenance,
        frozen_map_pack_sha256=packed['sha256'], native_source_hashes={k: v for k, v in snapshot.items() if k.endswith('_script_sha256')},
        source=scene['source'], map_ref=scenario['map'], external_map_provenance=scene['map']['external'])
    return run, provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=Path('.runtime/korea-map-20261024'))
    parser.add_argument('--output', type=Path, default=Path('reports/korea-map-assets-20261024'))
    parser.add_argument('--ui-base', default='http://127.0.0.1:5360')
    parser.add_argument('--run', action='append', help='key=run-receipt.json; repeat for an explicit selection')
    args = parser.parse_args()
    runtime, output = args.runtime.resolve(), args.output.resolve()
    selections = args.run or ['straight=run-straight-3.json', 'left_turn=run-left_turn-3.json',
        'right_turn=run-right_turn-3.json', 'left_turn_2_ui=run-left_turn-2-ui.json']
    runs, provenance = [], []
    keys = set()
    connection = sqlite3.connect((runtime / 'data/platform.db').as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        for selection in selections:
            key, filename = selection.split('=', 1)
            require(re.fullmatch('[a-z0-9_]+', key) is not None and key not in keys, 'Unique safe run key required')
            keys.add(key)
            run, proof = export_run(connection, runtime, output, args.ui_base, key, filename)
            runs.append(run)
            provenance.append(proof)
            print(json.dumps({'key': key, 'execution_id': run['execution_id'], 'png_frames': len(run['frames']),
                              'verdict': run['verdict']}), flush=True)
    finally:
        connection.close()
    assets = dict(schema_version='korea-map-report-assets/1',
        scope='presentation_derivative_of_sealed_native_simulation_not_real_road_or_sensor_validation', runs=runs)
    write_json(output / 'assets.json', assets)
    write_json(output / 'provenance.json', dict(schema_version='korea-map-report-assets-provenance/1',
        exporter_sha256=sha(Path(__file__).read_bytes()), pillow_version=PIL_VERSION,
        transformation='BGRA8 byte order decoded to RGBA PNG; original 640x360 grid; no resize, crop, interpolation, color adjustment or invented frame; all four channels roundtrip verified',
        assets_sha256=sha((output / 'assets.json').read_bytes()), runs=provenance))
    print(json.dumps({'assets': str(output / 'assets.json'), 'runs': len(runs),
                      'png_frames': sum(len(run['frames']) for run in runs)}), flush=True)


if __name__ == '__main__':
    main()
