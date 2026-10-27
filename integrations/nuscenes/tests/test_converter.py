"""Authored contract fixtures; real retained nuScenes acceptance is separate."""
import copy
import io
import json
import math
import tarfile
from pathlib import Path

import pytest
from mcap.reader import make_reader

from axiom_nuscenes_planning.converter import (canonical, collect_source_bundle, convert_bundle,
    digest, file_hash, mcap_bytes, parse_json, validate_geometry)
from axiom_nuscenes_planning.__main__ import write_outputs


def test_optional_compact_lineage_preserves_original_rows_and_source_scope(original):
    from axiom_nuscenes_planning.converter import compact_lineage, expand_lineage
    from ade2e.project_recordings import recording_source_scope
    from ade2e.mcap_ingest import parse_mcap
    root, archive, manifest = original
    bundle = collect_source_bundle(root, archive, manifest, expected_archive_sha256=file_hash(archive)[0],
        converter_source_sha256='a'*64, ego_geometry=GEOMETRY, compact=True)
    frames, lineage = convert_bundle(bundle)
    assert expand_lineage(compact_lineage(lineage)) == lineage
    raw, _ = mcap_bytes(frames, lineage)
    parsed = parse_mcap(raw, io.BytesIO())
    scope = recording_source_scope(parsed.metadata)
    assert scope['commercial_use'] is False and scope['full_lineage_metadata_verified'] is True
    original_metadata = next(item for item in parsed.metadata if item['name'] == 'axiom.original_lineage')
    retained = expand_lineage(json.loads(original_metadata['metadata']['lineage_json']))
    assert retained['source_bundle']['frames'] == bundle['frames']
    assert retained['converted_frames'] == lineage['converted_frames']


def test_compact_lineage_rejects_tamper_oversize_and_trailing_compressed_data():
    import base64
    from axiom_nuscenes_planning.converter import compact_lineage, expand_lineage, MAX_EXPANDED_LINEAGE_BYTES
    original = compact_lineage({'original_rows': [{'token': token(1), 'description': 'retained'}]})
    for updates in ({'uncompressed_sha256': '0'*64},
                    {'uncompressed_size_bytes': MAX_EXPANDED_LINEAGE_BYTES + 1},
                    {'uncompressed_size_bytes': 1},
                    {'data_base64': base64.b64encode(base64.b64decode(original['data_base64']) + b'trailing').decode()}):
        with pytest.raises(ValueError):
            expand_lineage(original | updates)


def test_batch_context_hashes_original_archive_once_and_keeps_default_input_bound(original, monkeypatch):
    from axiom_nuscenes_planning import converter
    root, archive, manifest = original
    original_hash = file_hash(archive)[0]
    checked = []
    native = converter.file_hash
    def count_hash(path, **kwargs):
        if Path(path) == archive:
            checked.append(str(path))
        return native(path, **kwargs)
    monkeypatch.setattr(converter, 'file_hash', count_hash)
    context = converter.collect_source_context(root, archive, expected_archive_sha256=original_hash)
    for _ in range(2):
        converter.collect_source_bundle(root, archive, manifest, expected_archive_sha256=original_hash,
            converter_source_sha256='a'*64, ego_geometry=GEOMETRY, source_context=context)
    assert len(checked) == 1
    assert converter.MAX_FRAMES == 16 and converter.MAX_LOG_FRAMES == 64
    oversized = json.loads(manifest)
    oversized['frames'] *= 6
    with pytest.raises(ValueError, match='input contract'):
        converter.collect_source_bundle(root, archive, canonical(oversized), expected_archive_sha256=original_hash,
            converter_source_sha256='a'*64, ego_geometry=GEOMETRY, source_context=context)


def test_library_selection_metadata_is_derived_from_original_scene_rows(original):
    from axiom_nuscenes_planning import converter
    root, archive, manifest_raw = original
    context = converter.collect_source_context(root, archive, expected_archive_sha256=file_hash(archive)[0])
    # Extend the authored fixture scene receipt without changing the existing source files.
    scene = context['tables']['scene'][token(1)]
    scene.update(nbr_samples=4, description='Original authored description')
    manifest = json.loads(manifest_raw)
    manifest['schema_version'] = converter.LOG_SELECTION_SCHEMA
    metadata = {'scene_token': scene['token'], 'scene_name': scene['name'], 'description': scene['description'],
        'original_scene_record_sha256': digest(scene), 'original_frame_count': 4,
        'sample_start_index': 1, 'sample_end_index': 3, 'start_time_s': .5, 'end_time_s': 1.5}
    manifest['selection'] = metadata
    kwargs = dict(expected_archive_sha256=file_hash(archive)[0], converter_source_sha256='a'*64,
                  ego_geometry=GEOMETRY, compact=True, source_context=context)
    bundle = converter.collect_source_bundle(root, archive, canonical(manifest), **kwargs)
    assert bundle['library_metadata'] == metadata
    frames, lineage = converter.convert_bundle(bundle)
    raw, _ = converter.mcap_bytes(frames, lineage)
    summary = next(value for value in make_reader(io.BytesIO(raw)).iter_metadata() if value.name == 'axiom.source_provenance')
    assert json.loads(summary.metadata['lineage_json'])['library_metadata'] == metadata
    manifest['selection'] = metadata | {'description': 'Rewritten description'}
    with pytest.raises(ValueError, match='original scene/sample rows'):
        converter.collect_source_bundle(root, archive, canonical(manifest), **kwargs)


def token(index):
    return f'{index:032x}'


GEOMETRY = {'length_m': 4.5, 'width_m': 1.9, 'origin_offset_xy_m': [0.,0.],
            'provenance': 'authored_reference_footprint', 'original_measured_geometry_available': False}


@pytest.fixture
def original(tmp_path):
    root = tmp_path/'source'; meta = root/'v1.0-mini'; meta.mkdir(parents=True)
    scene = {'token': token(1), 'name': 'scene-fixture', 'log_token': token(2)}
    sensor = {'token': token(3), 'channel': 'LIDAR_TOP'}
    calibration = {'token': token(4), 'sensor_token': token(3), 'translation': [0.,0.,1.], 'rotation': [1.,0.,0.,0.]}
    samples, data, poses, annotations, can, frames = [], [], [], [], [], []
    for index in range(4):
        at = 1_000_000+index*500_000
        sample = {'token': token(10+index), 'timestamp': at, 'scene_token': scene['token'],
                  'prev': token(9+index) if index else '', 'next': token(11+index) if index<3 else ''}
        pose = {'token': token(20+index), 'timestamp': at, 'translation': [float(index),0.,0.], 'rotation': [1.,0.,0.,0.]}
        datum = {'token': token(30+index), 'sample_token': sample['token'], 'timestamp': at,
                 'ego_pose_token': pose['token'], 'calibrated_sensor_token': calibration['token'], 'is_key_frame': True}
        annotation = {'token': token(40+index), 'sample_token': sample['token'], 'instance_token': token(50),
                      'translation': [10.+index,2.,1.], 'size': [2.,4.,1.5], 'rotation': [math.sqrt(.5),0.,0.,math.sqrt(.5)],
                      'prev': token(39+index) if index else '', 'next': token(41+index) if index<3 else '',
                      'visibility_token': '4', 'attribute_tokens': []}
        can.append({'utime': at-1000, 'vel': [2.,0.,0.], 'pos': [float(index),0.,0.]})
        samples.append(sample); poses.append(pose); data.append(datum); annotations.append(annotation)
        if index:
            frames.append({'sample_token': sample['token'], 'timestamp_us': at,
                'lidar_frame': {'sample_data_token': datum['token'], 'ego_pose_token': pose['token'], 'calibration_token': calibration['token']},
                'can_bus_source': {'timestamp_us': at-1000, 'sha256': digest(can)}})
    tables = {'scene': [scene], 'sample': samples, 'sample_data': data, 'ego_pose': poses,
              'sensor': [sensor], 'calibrated_sensor': [calibration]}
    for name,rows in tables.items(): (meta/(name+'.json')).write_bytes(canonical(rows))
    metadata_digest = digest({name: file_hash(meta/(name+'.json'))[0] for name in tables})
    bus = root/'can_bus'; bus.mkdir(); (bus/'scene-fixture_pose.json').write_bytes(canonical(can))
    for frame in frames:frame['can_bus_source']['sha256']=digest(can)
    manifest = {'schema_version': 'driving-input/1', 'dataset': {'use_scope': 'research_isolated', 'version': 'v1.0-mini',
                'name': 'nuScenes', 'rights_note': 'Authored fixture, isolated research only', 'scene_token': scene['token'],
                'scene_name': scene['name'], 'metadata_sha256': metadata_digest}, 'frames': frames}
    extra = {'sample_annotation': annotations, 'instance': [{'token': token(50), 'category_token': token(51)}],
             'category': [{'token': token(51), 'name': 'vehicle.fixture'}], 'attribute': [],
             'visibility': [{'token': '4', 'level': 'v80-100'}], 'log': [{'token': token(2), 'vehicle': 'fixture'}]}
    archive = tmp_path/'original.tgz'
    with tarfile.open(archive, 'w:gz') as stream:
        for name, rows in extra.items():
            raw = canonical(rows); info = tarfile.TarInfo('v1.0-mini/'+name+'.json'); info.size = len(raw)
            stream.addfile(info, io.BytesIO(raw))
    return root,archive,canonical(manifest)


def source(original):
    root,archive,manifest = original
    return collect_source_bundle(root,archive,manifest,expected_archive_sha256=file_hash(archive)[0],
                                 converter_source_sha256='a'*64,ego_geometry=GEOMETRY)


def test_original_rows_world_geometry_past_only_velocity_and_exact_clocks(original):
    bundle = source(original); frames,provenance = convert_bundle(bundle)
    assert bundle['original_tables']['sample_annotation']['rows']==4
    assert len(frames)==3 and frames[0]['at_ns']=='1500000000'
    obj = frames[0]['objects']['objects'][0]
    assert obj['length_m']==4. and obj['width_m']==2.
    assert obj['yaw_rad']==pytest.approx(math.pi/2)
    assert obj['vx_mps']==2. and obj['vy_mps']==0.
    assert provenance['converted_frames'][0]['can_age_ns']=='1000000'
    assert provenance['converted_frames'][0]['object_motion'][0]['uses_future_annotations'] is False
    assert provenance['commercial_use'] is False
    assert bundle['ego_geometry']['original_measured_geometry_available'] is False


def test_future_annotation_never_used_and_missing_velocity_is_null(original):
    bundle=parse_json(canonical(source(original)))
    entry=bundle['frames'][0]['objects'][0]
    entry['annotation']['prev']=''; entry['annotation_record_sha256']=digest(entry['annotation'])
    entry['previous_annotation']=None;entry['previous_sample']=None;entry['previous_annotation_record_sha256']=None
    # The untouched next foreign key cannot make a velocity available.
    frames,provenance=convert_bundle(bundle)
    assert frames[0]['objects']['objects'][0]['vx_mps'] is None
    assert provenance['converted_frames'][0]['unknown_velocity_count']==1


def test_current_velocity_does_not_depend_on_future_frame_position(original):
    bundle=parse_json(canonical(source(original)))
    before,_=convert_bundle(bundle)
    entry=bundle['frames'][-1]['objects'][0]
    entry['annotation']['translation'][0]+=5.
    entry['annotation_record_sha256']=digest(entry['annotation'])
    after,_=convert_bundle(bundle)
    assert before[0]['objects']==after[0]['objects']


def test_previous_gap_leaves_velocity_unknown(original):
    bundle=source(original);bundle['frames'][0]['objects'][0]['previous_sample']['timestamp']=-1_000_000
    frames,provenance=convert_bundle(bundle)
    assert frames[0]['objects']['objects'][0]['vy_mps'] is None
    assert provenance['converted_frames'][0]['object_motion'][0]['reason']=='previous_annotation_gap_exceeds_1_5s'


@pytest.mark.parametrize('change',['future_can','duplicate_time','quaternion','future_previous','annotation_hash','negative_speed'])
def test_bad_original_input_rejected(original,change):
    bundle=source(original)
    if change=='future_can':bundle['frames'][0]['can_pose']['utime']+=2000
    if change=='duplicate_time':bundle['frames'][1]['sample']['timestamp']=bundle['frames'][0]['sample']['timestamp']
    if change=='quaternion':bundle['frames'][0]['ego_pose']['rotation']=[2.,0.,0.,0.]
    if change=='future_previous':bundle['frames'][0]['objects'][0]['previous_sample']['timestamp']+=2_000_000
    if change=='annotation_hash':bundle['frames'][0]['objects'][0]['annotation']['translation'][0]=999.
    if change=='negative_speed':bundle['frames'][0]['can_pose']['vel'][0]=-1.
    with pytest.raises(ValueError):convert_bundle(bundle)


def test_archive_and_approved_metadata_identity_checked(original):
    root,archive,manifest=original
    with pytest.raises(ValueError,match='archive'):
        collect_source_bundle(root,archive,manifest,expected_archive_sha256='0'*64,converter_source_sha256='a'*64,ego_geometry=GEOMETRY)
    value=parse_json(manifest);value['dataset']['metadata_sha256']='0'*64
    with pytest.raises(ValueError,match='metadata'):
        collect_source_bundle(root,archive,canonical(value),expected_archive_sha256=file_hash(archive)[0],converter_source_sha256='a'*64,ego_geometry=GEOMETRY)


def test_no_default_or_measured_ego_geometry_and_duplicate_json_keys():
    for geometry in [{},GEOMETRY|{'original_measured_geometry_available':True},GEOMETRY|{'origin_offset_xy_m':[1.,0.]}]:
        with pytest.raises(ValueError):validate_geometry(geometry)
    with pytest.raises(ValueError,match='Duplicate'):parse_json(b'{"a":1,"a":2}')


def test_exact_mcap_contract_provenance_and_no_overwrite(original,tmp_path):
    bundle=source(original);frames,lineage=convert_bundle(bundle);raw,receipt=mcap_bytes(frames,lineage)
    reader=make_reader(io.BytesIO(raw));messages=list(reader.iter_messages(log_time_order=False))
    assert [m[2].log_time for m in messages]==[int(frame['at_ns']) for frame in frames for _ in range(2)]
    assert receipt['ego_ordinals']==[1,3,5]
    metadata=list(reader.iter_metadata())
    record=next(item for item in metadata if item.name=='axiom.source_provenance')
    assert record.metadata['commercial_use']=='false'
    assert parse_json(record.metadata['lineage_json'])['source_bundle_sha256']==digest(bundle)
    original_lineage=next(item for item in metadata if item.name=='axiom.original_lineage')
    assert len(record.metadata['lineage_json'].encode())<128*1024
    assert parse_json(record.metadata['lineage_json'])['original_lineage_metadata']['sha256']==digest(parse_json(original_lineage.metadata['lineage_json']))
    output=tmp_path/'converted';write_outputs(bundle,output)
    with pytest.raises(FileExistsError):write_outputs(bundle,output)
