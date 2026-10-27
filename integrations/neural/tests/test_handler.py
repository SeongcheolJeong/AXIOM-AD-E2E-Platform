"""Authored receipt/file fixtures; these tests never train or invoke a GPU."""
import copy
import hashlib
import io
import json
import zipfile

import numpy as np
from PIL import Image
import pytest

from axiom_neural.data import SWEEP_FRAME_COUNT, camera_sweep_lateral_m, canonical, changed_pose, sha_file
from axiom_neural.handler import validate_output, FIDELITY
from axiom_neural.results import validate_downloaded_output


def render_fixture(tmp_path):
    request = dict(kind='neural_render', run_id='neural_' + 'a' * 32, seed=42, source_execution_id='job_source',
        environment_sha256='e' * 64, reconstruction_execution_id='job_scene', checkpoint_sha256='c' * 64,
        parameters=dict(reconstruction_execution_id='job_scene', lateral_m=-.3, height_m=.2, mode='frame', frame_index=8))
    summary = dict(schema_version='neural-render-summary/1', fidelity=FIDELITY, source_kind='simulation',
        source_execution_id='job_source', source_manifest_sha256='b' * 64,
        width=640, height=360, fps=10, seed=42, environment_sha256='e' * 64, checkpoint_sha256='c' * 64,
        frame_count=1, result_artifact='neural-render-result.json', frames_artifact='neural-render-frames.zip', **request['parameters'])
    baseline = np.eye(4)
    frame = dict(frame_index=8, time_s=.8, source_frame=91, baseline_camera_to_world=baseline.tolist(),
        novel_camera_to_world=changed_pose(baseline, -.3, .2).tolist())
    archive_path = tmp_path / 'neural-render-frames.zip'
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_STORED) as archive:
        for kind in ('original', 'baseline', 'novel'):
            image = io.BytesIO()
            Image.new('RGB', (640, 360), '#2468ab').save(image, format='PNG')
            raw = image.getvalue()
            name = f'{kind}/000008.png'
            archive.writestr(name, raw)
            frame[kind] = dict(name=name, size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    document = {**summary, 'schema_version': 'neural-render-result/1', 'frames_archive_sha256': sha_file(archive_path), 'frames': [frame]}
    (tmp_path / 'neural-render-result.json').write_bytes(canonical(document))
    output = dict(summary=summary, artifacts=[dict(name=name, size_bytes=(tmp_path / name).stat().st_size,
        sha256=sha_file(tmp_path / name), media_type=media) for name, media in (
            ('neural-render-result.json', 'application/json'), ('neural-render-frames.zip', 'application/zip'))])
    return request, output, document


def test_native_summary_and_downloaded_artifact_contract(tmp_path):
    request, output, _ = render_fixture(tmp_path)
    validate_output(output, request, 'b' * 64)
    validate_downloaded_output(output, tmp_path)


def sweep_fixture(tmp_path):
    request, output, document = render_fixture(tmp_path)
    request['parameters'].update(mode='camera_sweep', lateral_m=1.5, height_m=0., frame_index=50)
    output['summary'].update(**request['parameters'], schema_version='neural-render-summary/2',
        frame_count=SWEEP_FRAME_COUNT, source_frame_index=50, source_time_s=5.)
    archive_path = tmp_path / 'neural-render-frames.zip'
    with zipfile.ZipFile(archive_path) as old:
        raw = old.read('original/000008.png')
    frames = []
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_STORED) as archive:
        for index in range(SWEEP_FRAME_COUNT):
            lateral = camera_sweep_lateral_m(index, 1.5)
            frame = dict(frame_index=index, time_s=index / 10, source_frame=133,
                source_frame_index=50, source_time_s=5., applied_lateral_m=lateral, applied_height_m=0.,
                baseline_camera_to_world=np.eye(4).tolist(),
                novel_camera_to_world=changed_pose(np.eye(4), lateral, 0).tolist())
            for kind in ('original', 'baseline', 'novel'):
                name = f'{kind}/{index:06d}.png'
                archive.writestr(name, raw)
                frame[kind] = dict(name=name, size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            frames.append(frame)
    document = {**output['summary'], 'schema_version': 'neural-render-result/2',
        'frames_archive_sha256': sha_file(archive_path), 'frames': frames}
    (tmp_path / 'neural-render-result.json').write_bytes(canonical(document))
    for entry in output['artifacts']:
        path = tmp_path / entry['name']; entry.update(size_bytes=path.stat().st_size, sha256=sha_file(path))
    return request, output, document


def test_camera_sweep_preserves_frozen_source_and_applies_each_native_pose(tmp_path):
    request, output, document = sweep_fixture(tmp_path)
    validate_output(output, request, 'b' * 64)
    validate_downloaded_output(output, tmp_path)
    frames = document['frames']
    assert (frames[0]['time_s'], frames[-1]['time_s']) == (0, 6)
    assert {frame['source_frame_index'] for frame in frames} == {50}
    assert frames[0]['applied_lateral_m'] == -1.5
    assert frames[-1]['applied_lateral_m'] == 1.5


@pytest.mark.parametrize('mutation', ['source_index', 'source_time', 'source_frame', 'applied_lateral',
    'applied_height', 'baseline_pose', 'novel_pose', 'frozen_pixels', 'schema'])
def test_camera_sweep_rejects_source_or_motion_drift(tmp_path, mutation):
    _, output, document = sweep_fixture(tmp_path)
    frame = document['frames'][30]
    if mutation == 'source_index': frame['source_frame_index'] = 51
    elif mutation == 'source_time': frame['source_time_s'] = 5.1
    elif mutation == 'source_frame': frame['source_frame'] = 134
    elif mutation == 'applied_lateral': frame['applied_lateral_m'] = .1
    elif mutation == 'applied_height': frame['applied_height_m'] = .1
    elif mutation == 'baseline_pose': frame['baseline_camera_to_world'][0][3] += .1
    elif mutation == 'novel_pose': frame['novel_camera_to_world'][0][3] += .1
    elif mutation == 'frozen_pixels': frame['original']['sha256'] = 'd' * 64
    elif mutation == 'schema': document['schema_version'] = 'neural-render-result/1'
    (tmp_path / 'neural-render-result.json').write_bytes(canonical(document))
    with pytest.raises(ValueError): validate_downloaded_output(output, tmp_path)


@pytest.mark.parametrize('field,value', [('frame_count', 1), ('source_frame_index', 51),
    ('source_time_s', 5.1), ('schema_version', 'neural-render-summary/1')])
def test_camera_sweep_summary_rejects_frozen_anchor_or_count_drift(tmp_path, field, value):
    request, output, _ = sweep_fixture(tmp_path)
    output['summary'][field] = value
    with pytest.raises(ValueError): validate_output(output, request, 'b' * 64)


@pytest.mark.parametrize('field,value', [('source_execution_id', 'other'), ('source_manifest_sha256', 'd' * 64),
    ('checkpoint_sha256', 'd' * 64), ('lateral_m', .3), ('frame_count', 200), ('environment_sha256', 'd' * 64)])
def test_native_summary_drift_is_rejected(tmp_path, field, value):
    request, output, _ = render_fixture(tmp_path)
    output['summary'][field] = value
    with pytest.raises(ValueError):
        validate_output(output, request, 'b' * 64)


@pytest.mark.parametrize('mutation', ['summary', 'archive_hash', 'frame_id', 'time', 'pose', 'member_hash', 'member_size'])
def test_result_file_drift_is_rejected(tmp_path, mutation):
    _, output, document = render_fixture(tmp_path)
    frame = document['frames'][0]
    if mutation == 'summary': document['height_m'] = -.2
    elif mutation == 'archive_hash': document['frames_archive_sha256'] = 'd' * 64
    elif mutation == 'frame_id': frame['frame_index'] = 9
    elif mutation == 'time': frame['time_s'] = .9
    elif mutation == 'pose': frame['novel_camera_to_world'][0][3] += .1
    elif mutation == 'member_hash': frame['novel']['sha256'] = 'd' * 64
    elif mutation == 'member_size': frame['novel']['size_bytes'] += 1
    (tmp_path / 'neural-render-result.json').write_bytes(canonical(document))
    with pytest.raises(ValueError):
        validate_downloaded_output(output, tmp_path)


@pytest.mark.parametrize('mutation', ['extra', 'deflated', 'wrong_png'])
def test_archive_members_are_exact_uncompressed_rgb(tmp_path, mutation):
    _, output, document = render_fixture(tmp_path)
    archive_path = tmp_path / 'neural-render-frames.zip'
    with zipfile.ZipFile(archive_path) as archive:
        members = {item.filename: archive.read(item) for item in archive.infolist()}
    if mutation == 'extra': members['extra.txt'] = b'not a rendered image'
    if mutation == 'wrong_png':
        raw = bytearray(members['novel/000008.png'])
        raw[16:20] = (123).to_bytes(4, 'big')
        members['novel/000008.png'] = bytes(raw)
        document['frames'][0]['novel']['sha256'] = hashlib.sha256(raw).hexdigest()
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED if mutation == 'deflated' else zipfile.ZIP_STORED) as archive:
        for name, raw in members.items(): archive.writestr(name, raw)
    document['frames_archive_sha256'] = sha_file(archive_path)
    (tmp_path / 'neural-render-result.json').write_bytes(canonical(document))
    with pytest.raises(ValueError):
        validate_downloaded_output(output, tmp_path)


def test_real_road_frame_preserves_irregular_recording_clock_and_exact_source(tmp_path):
    request, output, document = render_fixture(tmp_path)
    frame = document['frames'][0]
    row = dict(frame_index=8, source_frame=91, recording_ordinal=8, log_time_ns='1535385092800404000',
        sample_data_token='native_token', original_image_sha256='f' * 64, time_s=.688,
        png_sha256=frame['original']['sha256'], camera_to_world=frame['baseline_camera_to_world'])
    source = dict(schema_version='neural-real-road-source/1', source_id='real_scene', recording_id='rec_native',
        channel_id=1, recording_sha256='a' * 64, source_use_scope=dict(commercial_use=False),
        frame_count=96, fps=12, frames=[dict(row, frame_index=i) for i in range(96)])
    metadata = {key: source[key] for key in ('source_id', 'recording_id', 'channel_id', 'recording_sha256', 'source_use_scope')}
    metadata.update(source_frame_count=96, source_fps=12)
    request.update(source_kind='real_road', source_metadata=metadata)
    output['summary'].update(fidelity='neural_reconstruction_of_real_road', source_kind='real_road', fps=12, **metadata)
    frame.update(time_s=.688, source_time_s=.688, source_frame_index=8,
        **{key: row[key] for key in ('recording_ordinal', 'log_time_ns', 'sample_data_token', 'original_image_sha256')})
    document.update(output['summary'])
    document['schema_version'] = 'neural-render-result/1'
    (tmp_path / 'neural-render-result.json').write_bytes(canonical(document))
    validate_output(output, request, 'b' * 64)
    validate_downloaded_output(output, tmp_path, source_manifest=source)
    frame['log_time_ns'] = str(int(frame['log_time_ns']) + 1)
    (tmp_path / 'neural-render-result.json').write_bytes(canonical(document))
    with pytest.raises(ValueError, match='recording anchor'):
        validate_downloaded_output(output, tmp_path, source_manifest=source)


def test_real_road_sweep_freezes_recorded_time_while_render_timeline_advances(tmp_path):
    request, output, document = sweep_fixture(tmp_path)
    original = document['frames'][0]
    row = dict(frame_index=50, source_frame=133, recording_ordinal=50, log_time_ns='1535385096329070000',
        sample_data_token='recorded_cam', original_image_sha256='f' * 64, time_s=4.216666,
        png_sha256=original['original']['sha256'], camera_to_world=original['baseline_camera_to_world'])
    source = dict(schema_version='neural-real-road-source/1', source_id='road', recording_id='rec_road',
        channel_id=1, recording_sha256='a' * 64, source_use_scope=dict(commercial_use=False),
        frame_count=96, fps=12, frames=[dict(row, frame_index=i) for i in range(96)])
    metadata = {key: source[key] for key in ('source_id', 'recording_id', 'channel_id', 'recording_sha256', 'source_use_scope')}
    metadata.update(source_frame_count=96, source_fps=12)
    request.update(source_kind='real_road', source_metadata=metadata, source_anchor_time_s=row['time_s'])
    output['summary'].update(fidelity='neural_reconstruction_of_real_road', source_kind='real_road',
        source_time_s=row['time_s'], **metadata)
    for frame in document['frames']:
        frame.update(source_time_s=row['time_s'], **{key: row[key] for key in
            ('recording_ordinal', 'log_time_ns', 'sample_data_token', 'original_image_sha256')})
    document.update(output['summary']);document['schema_version']='neural-render-result/2'
    (tmp_path/'neural-render-result.json').write_bytes(canonical(document))
    validate_output(output, request, 'b' * 64)
    validate_downloaded_output(output, tmp_path, source_manifest=source)
    assert document['frames'][-1]['time_s'] == 6
    assert {f['source_time_s'] for f in document['frames']} == {4.216666}
    assert output['summary']['fps'] == 10 and output['summary']['source_fps'] == 12
