"""Authored package fixtures check source transport, not actual road data quality."""
import copy
import hashlib
import io
import json
import zipfile

from PIL import Image
import pytest

from axiom_neural.real_road import load_source


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_package(tmp_path, monkeypatch, *, source_change=None, dataset_change=None, duplicate=False):
    output = io.BytesIO()
    Image.new('RGB', (640, 360), 'gray').save(output, format='PNG')
    png = output.getvalue()
    pose = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    frames = [dict(frame_index=index, source_frame=index, file_path=f'images/{index:06d}.png',
        png_sha256=sha(png), original_image_sha256=sha(b'Authored original fixture'), recording_ordinal=index,
        log_time_ns=str(1000000000 + index * 500000000), time_s=index * .5, camera_to_world=pose,
        sample_token=f'sample{index}', sample_data_token=f'camera{index}', calibrated_sensor_token='calibration', ego_pose_token=f'pose{index}') for index in range(3)]
    source = dict(schema_version='neural-real-road-source/1', source_id='authored-source', source_kind='real_road', fidelity='recorded_real_road',
        license_scope='noncommercial_research_only', external_publication_cleared=False,
        recording_id='rec_fixture', recording_sha256=sha(b'Authored recording fixture'), channel_id=1,
        start_time_ns='1000000000', end_time_ns='2000000000', frame_count=3, width=640, height=360, fps=2, frames=frames)
    evidence = copy.deepcopy(source) | {'schema_version': 'neural-dataset/2'}
    if dataset_change:
        dataset_change(evidence)
    dataset = tmp_path / 'dataset.zip'
    with zipfile.ZipFile(dataset, 'w', compression=zipfile.ZIP_STORED) as archive:
        for frame in frames:
            archive.writestr(frame['file_path'], png)
        if duplicate:
            archive.writestr(frames[0]['file_path'], png)
        archive.writestr('points.ply', b'ply\nformat ascii 1.0\nelement vertex 0\nend_header\n')
        archive.writestr('neural-dataset.json', json.dumps(evidence))
        archive.writestr('transforms.json', json.dumps(dict(w=640, h=360,
            frames=[dict(file_path=frame['file_path'], transform_matrix=pose) for frame in frames])))
    source['dataset_sha256'] = sha(dataset.read_bytes())
    if source_change:
        source_change(source)
    manifest = tmp_path / 'source.json'
    manifest.write_text(json.dumps(source))
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps(dict(schema_version='neural-real-road-catalog/1', sources=[dict(
        source_id='authored-source', manifest_path=str(manifest), dataset_path=str(dataset),
        manifest_sha256=sha(manifest.read_bytes()), dataset_sha256=sha(dataset.read_bytes()))])))
    monkeypatch.setenv('AXIOM_NEURAL_REAL_ROAD_SOURCES', str(catalog))
    return source, manifest, dataset, catalog


def test_registered_package_returns_exact_source_and_paths(tmp_path, monkeypatch):
    source, manifest, dataset, _ = write_package(tmp_path, monkeypatch)
    assert load_source('authored-source') == (source, manifest, dataset)


@pytest.mark.parametrize('change', [lambda source: source.update(external_publication_cleared=True),
    lambda source: source.update(license_scope='commercial'), lambda source: source.update(start_time_ns=1000000000),
    lambda source: source['frames'][1].update(recording_ordinal=0),
    lambda source: source['frames'][0].update(png_sha256='0' * 64)])
def test_bad_scope_time_mapping_or_png_is_rejected(tmp_path, monkeypatch, change):
    write_package(tmp_path, monkeypatch, source_change=change)
    with pytest.raises(ValueError):
        load_source('authored-source')


def test_dataset_and_public_frame_lineage_must_match(tmp_path, monkeypatch):
    write_package(tmp_path, monkeypatch, dataset_change=lambda source: source['frames'][0].update(recording_ordinal=8))
    with pytest.raises(ValueError, match='lineage differ'):
        load_source('authored-source')


def test_source_catalog_hash_and_unknown_id_are_fail_closed(tmp_path, monkeypatch):
    _, manifest, _, _ = write_package(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='not registered'):
        load_source('not-registered')
    manifest.write_bytes(manifest.read_bytes() + b' ')
    with pytest.raises(ValueError, match='SHA-256'):
        load_source('authored-source')


def test_duplicate_zip_members_are_rejected(tmp_path, monkeypatch):
    with pytest.warns(UserWarning, match='Duplicate name'):
        write_package(tmp_path, monkeypatch, duplicate=True)
    with pytest.raises(ValueError, match='ZIP'):
        load_source('authored-source')
