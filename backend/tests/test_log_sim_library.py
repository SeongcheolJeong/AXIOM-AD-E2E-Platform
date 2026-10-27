"""Authored metadata fixtures test library boundaries, not real-road validity."""
import copy
import hashlib
import io
from threading import RLock

import pytest
from fastapi import APIRouter, Depends, Request
from mcap.writer import Writer
from pydantic import ValidationError

from ade2e import log_sim, log_sim_library as library
from ade2e.database import Settings
from ade2e.recording_schemas import RecordingImportMetadata
from ade2e.recordings import import_recording
from ade2e.service import canonical_bytes, digest
from ade2e.team_access import authenticate_request
from test_object_demo import env


def scene():
    return dict(scene_token='authored-scene', name='Authored test scene', description='Fixture only',
        original_scene_record_sha256='b'*64, original_frame_count=41, duration_s=20.,
        excluded=[dict(index=0, reason='missing_causal_CAN')], segments=[])


@pytest.fixture
def corpus(env, tmp_path):
    db, client, *_ = env
    def auth(request: Request):
        authenticate_request(request, db, Settings(api_token='test-bootstrap', team_mode=True, seed_examples=False),
            request.headers.get('authorization', '')[7:])
    api = APIRouter(prefix='/api/v1', dependencies=[Depends(auth)])
    library.register_log_sim_library_routes(api, db)
    client.app.include_router(api)
    metadata = dict(scene_token='authored-scene', scene_name='Authored test scene', description='Fixture only',
        original_scene_record_sha256='b'*64, original_frame_count=41, sample_start_index=1, sample_end_index=40,
        start_time_s=.5, end_time_s=20.)
    output = io.BytesIO(); writer = Writer(output); writer.start()
    channels = []
    for name in ('axiom.PlanningEgo.v1', 'axiom.ObjectTracks.v1', 'foxglove.CompressedImage'):
        schema = writer.register_schema(name, 'jsonschema', b'{}')
        channels.append(writer.register_channel('/'+name, 'json', schema))
    full = canonical_bytes({'authored_fixture': True})
    summary = dict(library_metadata=metadata, original_archive={'sha256': 'a'*64},
        original_lineage_metadata={'name': 'axiom.original_lineage', 'field': 'lineage_json', 'sha256': hashlib.sha256(full).hexdigest()})
    writer.add_metadata('axiom.source_provenance', dict(schema_version='axiom.source-provenance/1',
        use_scope='isolated_noncommercial_research', commercial_use='false', source_dataset='nuScenes mini',
        converter_version='authored-test-only', lineage_json=canonical_bytes(summary).decode()))
    writer.add_metadata('axiom.original_lineage', dict(schema_version='axiom.original-lineage/1', lineage_json=full.decode()))
    for index in range(40):
        for channel in channels:
            at=1_000_000_000+index*500_000_000
            writer.add_message(channel, at, b'{}', at, index)
    writer.finish(); raw=output.getvalue(); path=tmp_path/'fixture.mcap'; path.write_bytes(raw)
    recording, _ = import_recording(db, RLock(), path, RecordingImportMetadata(name='Authored forty-frame fixture'),
        hashlib.sha256(raw).hexdigest(), len(raw))
    s=scene(); s['segments']=[dict(recording_id=recording.id, sha256=recording.sha256,
        sample_start_index=1, sample_end_index=40, start_time_s=.5, end_time_s=20.)]
    body=dict(project_id='default', original_archive_sha256='a'*64, scenes=[s])
    return db, client, body


def test_forty_frame_registration_preserves_original_denominator_and_idempotence(corpus):
    db, client, body=corpus
    response=client.post('/api/v1/log-sim/library', json=body)
    assert response.status_code==201, response.text
    value=response.json()
    assert value['counts']==dict(scene_count=1, segment_count=1, original_frame_count=41, available_frame_count=40, excluded_frame_count=1)
    assert value['items'][0]['status']=='partial'
    assert client.post('/api/v1/log-sim/library', json=body).json()==value
    assert client.get('/api/v1/log-sim/library').json()==value
    with db.read_session() as session:
        source=log_sim._source(session, 'default', value['items'][0]['segments'][0]['source_id'])
        assert source['frame_count']==40 and source['library_metadata']['original_frame_count']==41


@pytest.mark.parametrize('mutation', ['overlap', 'missing', 'duplicate_scene'])
def test_denominators_cannot_duplicate_or_omit_original_frames(corpus, mutation):
    _, _, body=corpus
    if mutation=='overlap': body['scenes'][0]['excluded'].append(dict(index=1, reason='overlap'))
    if mutation=='missing': body['scenes'][0]['excluded']=[]
    if mutation=='duplicate_scene': body['scenes']*=2
    with pytest.raises(ValidationError): library.LibraryCreate.model_validate(body)


def test_provenance_mismatch_rejected_before_library_created(corpus):
    _, client, body=corpus
    body['scenes'][0]['description']='Invented scene description'
    assert client.post('/api/v1/log-sim/library', json=body).status_code==422
    assert client.get('/api/v1/log-sim/library').json()['items']==[]


def test_phantom_all_excluded_scene_and_forged_duration_are_rejected(corpus):
    _, _, body = corpus
    unsupported = copy.deepcopy(body)
    unsupported['scenes'][0]['segments'] = []
    unsupported['scenes'][0]['excluded'] = [dict(index=index, reason='unavailable') for index in range(41)]
    with pytest.raises(ValidationError):
        library.LibraryCreate.model_validate(unsupported)
    body['scenes'][0]['duration_s'] = 200.
    with pytest.raises(ValidationError, match='retained final frame'):
        library.LibraryCreate.model_validate(body)


def test_bad_recorded_clock_is_rejected_before_source_binding(corpus):
    from sqlalchemy import select
    from ade2e.recording_models import RecordingMessageRow
    from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow
    db, client, body = corpus
    identity = body['scenes'][0]['segments'][0]['recording_id']
    with db.session.begin() as session:
        row = session.scalar(select(RecordingMessageRow).where(
            RecordingMessageRow.recording_id == identity, RecordingMessageRow.channel_id == 1)
            .order_by(RecordingMessageRow.log_time_key.desc()).limit(1))
        row.log_time_key = str(int(row.log_time_key) + 1_000_000_000).zfill(20)
    result = client.post('/api/v1/log-sim/library', json=body)
    assert result.status_code == 422
    with db.read_session() as session:
        assert not list(session.scalars(select(SimulationCatalogRevisionRow).where(
            SimulationCatalogRevisionRow.kind == log_sim.SOURCE_KIND,
            SimulationCatalogRevisionRow.id == 'nuscenes-' + identity)))
    assert client.get('/api/v1/log-sim/library').json()['items'] == []


def test_project_admin_cannot_register_or_read_ungranted_library(corpus):
    _, client, body=corpus
    assert client.post('/api/v1/log-sim/library', json=body).status_code==201
    token=client.post('/api/v1/projects/default/tokens', json={'user_label':'Fixture', 'role':'Admin'}).json()['token']
    headers={'Authorization':'Bearer '+token}
    assert client.post('/api/v1/log-sim/library', json=body, headers=headers).status_code==403
    assert client.get('/api/v1/log-sim/library', headers=headers).status_code==404
    assert client.get('/api/v1/log-sim/library?project_id=foreign', headers=headers).status_code==404
