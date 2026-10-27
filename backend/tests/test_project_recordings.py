"""Explicit source grants and bounded project reads, using authored MCAP bytes."""
from contextlib import closing
import hashlib
import io
import json
import shutil
import sqlite3
from threading import RLock

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.security import HTTPBearer
from fastapi.testclient import TestClient
from mcap.writer import CompressionType, Writer
import pytest
from sqlalchemy import select

from ade2e.database import Database, Settings, SCHEMA_VERSION
from ade2e.project_recording_models import ProjectRecordingGrantRow
from ade2e.project_recordings import register_project_recording_routes, recording_use_scope
from ade2e.recording_models import RecordingRow
from ade2e.recordings import register_recording_routes
from ade2e.replay_specs import register_replay_spec_routes
from ade2e.scene_sequences import EGO_SCHEMA, EGO_SCHEMA_NAME, OBJECT_SCHEMA, OBJECT_SCHEMA_NAME
from ade2e.team_access import authenticate_request, register_team_routes
from ade2e.service import canonical_bytes


@pytest.fixture
def env(tmp_path):
    settings = Settings(data_dir=tmp_path/'source', seed_examples=False, api_token='operator-for-contract-test', team_mode=True)
    db = Database(settings); db.initialize()
    app = FastAPI(); app.state.db = db
    bearer = HTTPBearer(auto_error=False)
    def authenticate(request: Request, credentials=Depends(bearer)):
        return authenticate_request(request, db, settings, credentials.credentials if credentials else None)
    api = APIRouter(prefix='/api/v1', dependencies=[Depends(authenticate)])
    register_team_routes(api, db); register_recording_routes(api, db, RLock())
    register_project_recording_routes(api, db); register_replay_spec_routes(api, db)
    app.include_router(api)
    with TestClient(app, headers={'Authorization': 'Bearer operator-for-contract-test'}) as client:
        yield db, client
    db.engine.dispose()


def import_source(client, *, restricted=False):
    output = io.BytesIO(); writer = Writer(output, compression=CompressionType.NONE)
    writer.start()
    es = writer.register_schema(EGO_SCHEMA_NAME, 'jsonschema', json.dumps(EGO_SCHEMA).encode())
    os = writer.register_schema(OBJECT_SCHEMA_NAME, 'jsonschema', json.dumps(OBJECT_SCHEMA).encode())
    ec, oc = writer.register_channel('/ego', 'json', es), writer.register_channel('/objects', 'json', os)
    if restricted:
        writer.add_metadata('axiom.source_provenance', {'schema_version': 'axiom.source-provenance/1',
            'use_scope': 'isolated_noncommercial_research', 'commercial_use': 'false', 'source_dataset': 'nuScenes mini',
            'converter_version': 'authored-metadata-contract-fixture/1',
            'lineage_json': json.dumps({'annotation_source': 'authored fixture, not actual nuScenes',
                                      'footprint': {'status': 'declared', 'source': 'test'}})})
    for index in range(3):
        at = str(1_000_000_000 + index * 100_000_000)
        ego = {'timestamp_ns': at, 'frame_id': 'map', 'x_m': float(index), 'y_m': 0., 'yaw_rad': 0.,
               'speed_mps': 3., 'length_m': 4., 'width_m': 2., 'source': 'authored grant contract fixture'}
        objects = {'timestamp_ns': at, 'frame_id': 'map', 'coordinate_frame': 'world',
                   'velocity_semantics': 'absolute_ground_velocity', 'objects': [], 'source': 'authored empty object fixture'}
        writer.add_message(oc, int(at), json.dumps(objects).encode(), int(at))
        writer.add_message(ec, int(at), json.dumps(ego).encode(), int(at))
    writer.finish()
    response = client.post('/api/v1/recordings?name=GrantFixture&source_kind=synthetic',
        content=output.getvalue(), headers={'Content-Type': 'application/octet-stream'})
    assert response.status_code == 201, response.text
    source = response.json()
    sequence = {'name': 'Granted sequence', 'source_type': 'recording', 'project_id': 'default',
        'recording_id': source['id'], 'ego_channel_id': ec, 'object_channel_id': oc, 'ego_ordinals': [1,3,5],
        'world_frame': 'map', 'clock_domain': 'fixture-clock', 'note': 'Authored MCAP; not measured road data'}
    return source, sequence, ec


def token(client, project='default', role='Viewer'):
    response = client.post(f'/api/v1/projects/{project}/tokens', json={'user_label': 'test-'+role, 'role': role})
    assert response.status_code == 201, response.text
    return {'Authorization': 'Bearer '+response.json()['token']}


def grant(client, source, project='default', key='grant-source'):
    return client.post('/api/v1/project-recordings/grants', json={'project_id': project,
        'recording_id': source['id'], 'recording_sha256': source['sha256']}, headers={'Idempotency-Key': key})


def test_operator_grants_exact_source_team_reads_bounded_no_legacy_api(env):
    db, client = env; source, sequence, channel = import_source(client)
    viewer = token(client)
    assert client.get('/api/v1/project-recordings', headers=viewer).json()['items'] == []
    assert client.get('/api/v1/project-recordings/'+source['id'], headers=viewer).status_code == 404
    assert client.get('/api/v1/scene-sequences/sources/'+source['id'], headers=viewer).status_code == 404
    engineer = token(client, role='Engineer')
    assert client.post('/api/v1/scene-sequences/preview', json=sequence, headers=engineer).status_code == 404
    assert client.post('/api/v1/scene-sequences/freeze', json={'definition': sequence, 'preview_sha256': '0'*64}, headers=engineer).status_code == 404
    assert client.post('/api/v1/scene-sequences/freeze', json={'definition': sequence, 'preview_sha256': '0'*64}, headers=viewer).status_code == 403
    saved = grant(client, source); assert saved.status_code == 201, saved.text
    repeated = grant(client, source); assert repeated.json() == saved.json()
    assert grant(client, source, key='second-logical-key').json()['id'] == saved.json()['id']
    alias_changed = client.post('/api/v1/project-recordings/grants', json={'project_id': 'default', 'recording_id': source['id'],
        'recording_sha256': source['sha256'], 'note': 'different alias request'}, headers={'Idempotency-Key': 'second-logical-key'})
    assert alias_changed.status_code == 409
    listing = client.get('/api/v1/project-recordings', headers=viewer).json()
    assert listing['items'][0]['id'] == source['id'] and listing['legacy_catalog_exposed'] is False
    assert listing['items'][0]['download_url'] == f"/api/v1/project-recordings/{source['id']}/download?project_id=default"
    assert 'source_metadata' not in listing['items'][0]['grant']
    detail = client.get('/api/v1/project-recordings/'+source['id'], headers=viewer).json()
    assert detail['sha256'] == source['sha256'] and detail['access_scope'] == 'explicit_project_grant'
    samples = client.get('/api/v1/project-recordings/'+source['id']+'/samples',
        params={'channel_id': channel, 'from_start': True, 'limit': 2}, headers=viewer).json()
    assert [item['ordinal'] for item in samples['samples']] == [1,3] and samples['next_after_ordinal'] == 3
    following = client.get('/api/v1/project-recordings/'+source['id']+'/samples',
        params={'channel_id': channel, 'after_ordinal': 3, 'limit': 2}, headers=viewer).json()
    assert [item['ordinal'] for item in following['samples']] == [5]
    assert client.get('/api/v1/project-recordings/'+source['id']+'/samples',
        params={'channel_id': channel, 'limit': 101}, headers=viewer).status_code == 422
    assert client.get('/api/v1/recordings/'+source['id'], headers=viewer).status_code == 403
    assert client.get('/api/v1/project-recordings', params={'q': 'GrantFixture'}, headers=viewer).json()['items']
    assert client.get('/api/v1/project-recordings', params={'q': 'not-present'}, headers=viewer).json()['items'] == []
    with db.read_session() as session:
        assert len(list(session.scalars(select(ProjectRecordingGrantRow)))) == 1


def test_grant_operator_only_nondefault_isolation_and_sequence_authoring(env):
    _, client = env; source, sequence, _ = import_source(client)
    project = client.post('/api/v1/projects', json={'name': 'Explicit source team'}).json()['id']
    engineer, admin = token(client, project, 'Engineer'), token(client, project, 'Admin')
    default_viewer = token(client)
    body = {'project_id': project, 'recording_id': source['id'], 'recording_sha256': source['sha256']}
    assert client.post('/api/v1/project-recordings/grants', json=body,
        headers=admin|{'Idempotency-Key': 'cannot-grant'}).status_code == 403
    assert grant(client, source, project).status_code == 201
    assert client.get('/api/v1/project-recordings', params={'project_id': project}, headers=engineer).json()['items']
    assert client.get('/api/v1/project-recordings/'+source['id'], params={'project_id': project}, headers=default_viewer).status_code == 404
    assert client.get('/api/v1/project-recordings/'+source['id'], headers=engineer).status_code == 404
    response = client.post('/api/v1/scene-sequences', json=sequence|{'project_id': project}, headers=engineer)
    assert response.status_code == 201, response.text
    assert response.json()['project_id'] == project


def test_identity_idempotency_and_native_byte_changes_fail_closed(env):
    db, client = env; source, _, _ = import_source(client)
    wrong = client.post('/api/v1/project-recordings/grants', json={'project_id': 'default', 'recording_id': source['id'],
        'recording_sha256': '0'*64}, headers={'Idempotency-Key': 'wrong-sha'})
    assert wrong.status_code == 409
    original = grant(client, source); assert original.status_code == 201
    changed = client.post('/api/v1/project-recordings/grants', json={'project_id': 'default', 'recording_id': source['id'],
        'recording_sha256': source['sha256'], 'note': 'changed body'}, headers={'Idempotency-Key': 'grant-source'})
    assert changed.status_code == 409
    viewer = token(client)
    with db.read_session() as session:
        row = session.get(RecordingRow, source['id']); path = db.recording_dir / row.storage_key
    raw = path.read_bytes(); path.write_bytes(bytes([raw[0] ^ 1])+raw[1:])
    assert client.get('/api/v1/project-recordings/'+source['id'], headers=viewer).status_code == 409
    assert grant(client, source).status_code == 409


def test_viewer_download_exact_original_project_scope_and_corruption(env):
    db, client = env; source, _, _ = import_source(client)
    project = client.post('/api/v1/projects', json={'name': 'Source download project'}).json()['id']
    viewer, default_viewer = token(client, project), token(client)
    route = f"/api/v1/project-recordings/{source['id']}/download"
    assert client.get(route, params={'project_id': project}, headers=viewer).status_code == 404
    assert grant(client, source, project).status_code == 201
    detail = client.get('/api/v1/project-recordings/'+source['id'], params={'project_id': project}, headers=viewer).json()
    assert detail['download_url'] == route+'?project_id='+project
    assert client.get(route, params={'project_id': project}, headers=default_viewer).status_code == 404
    assert client.get(route, headers=viewer).status_code == 404
    assert client.get('/api/v1/recordings/'+source['id']+'/download', headers=viewer).status_code == 403
    downloaded = client.get(detail['download_url'], headers=viewer)
    assert downloaded.status_code == 200
    assert downloaded.headers['x-content-sha256'] == source['sha256']
    assert downloaded.headers['content-length'] == str(source['size_bytes'])
    assert downloaded.headers['content-disposition'] == f"attachment; filename=\"{source['id']}.mcap\""
    assert downloaded.headers['cache-control'] == 'private, no-store'
    assert hashlib.sha256(downloaded.content).hexdigest() == source['sha256']
    with db.read_session() as session:
        row = session.get(RecordingRow, source['id']); original = db.recording_dir/row.storage_key
    assert downloaded.content == original.read_bytes()
    raw = original.read_bytes(); original.write_bytes(bytes([raw[0]^1])+raw[1:])
    assert client.get(route, params={'project_id': project}, headers=viewer).status_code == 409
    original.write_bytes(raw[:-1])
    assert client.get(route, params={'project_id': project}, headers=viewer).status_code == 409


def test_restricted_source_metadata_preserved_never_grant_relaxed(env):
    db, client = env; source, _, _ = import_source(client, restricted=True)
    saved = grant(client, source); assert saved.status_code == 201
    scope = saved.json()['source_use_scope']
    assert scope['use_scope'] == 'isolated_noncommercial_research' and scope['commercial_use'] is False
    assert scope['lineage']['footprint']['status'] == 'declared'
    viewer = token(client)
    detail = client.get('/api/v1/project-recordings/'+source['id'], headers=viewer).json()
    assert detail['source_use_scope'] == scope
    assert recording_use_scope({'metadata': []})['use_scope'] == 'unknown'
    malformed = {'metadata': [{'name': 'axiom.source_provenance', 'metadata':
        {'schema_version': 'axiom.source-provenance/1', 'commercial_use': 'true'}}]}
    with pytest.raises(Exception) as error:recording_use_scope(malformed)
    assert error.value.status_code == 409
    with db.session.begin() as session:
        row = session.get(RecordingRow, source['id']); payload = dict(row.payload)
        payload['metadata'] = []; row.payload = payload
    assert client.get('/api/v1/project-recordings/'+source['id'], headers=viewer).status_code == 409


def test_original_lineage_descriptor_canonical_hash_and_conflicts():
    full = canonical_bytes({'original_rows': [{'id':'annotation-test', 'raw':True}], 'footprint': {'status':'declared'}}).decode()
    summary = {'original_lineage_metadata': {'name':'axiom.original_lineage', 'field':'lineage_json',
                                            'sha256':hashlib.sha256(full.encode()).hexdigest()}}
    payload = {'metadata': [{'name':'axiom.source_provenance','metadata':{
        'schema_version':'axiom.source-provenance/1','use_scope':'isolated_noncommercial_research',
        'commercial_use':'false','source_dataset':'nuScenes mini','converter_version':'contract-fixture/1',
        'lineage_json':canonical_bytes(summary).decode()}}, {'name':'axiom.original_lineage','metadata':{
        'schema_version':'axiom.original-lineage/1','lineage_json':full}}]}
    scope = recording_use_scope(payload)
    assert scope['full_lineage_metadata_verified'] is True and scope['commercial_use'] is False
    payload['metadata'][1]['metadata']['lineage_json'] = full + ' '
    with pytest.raises(Exception) as error:recording_use_scope(payload)
    assert error.value.status_code == 409
    payload['metadata'][1]['metadata']['lineage_json'] = full
    payload['metadata'].append(payload['metadata'][1])
    with pytest.raises(Exception) as error:recording_use_scope(payload)
    assert error.value.status_code == 409


def test_schema17_cold_copy_adds_empty_grants_preserves_all_source_rows(env, tmp_path):
    db, client = env; source, _, _ = import_source(client)
    db.engine.dispose()
    original = tmp_path/'source'
    with closing(sqlite3.connect(original/'platform.db')) as connection:
        connection.execute('DROP TABLE project_recording_grant_requests')
        connection.execute('DROP TABLE project_recording_grants')
        connection.execute('UPDATE schema_versions SET version=17'); connection.commit()
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'") if row[0] != 'schema_versions']
        previous = {table: connection.execute('SELECT * FROM '+table).fetchall() for table in tables}
    before = (original/'platform.db').read_bytes()
    copied = tmp_path/'restored-copy'; shutil.copytree(original, copied)
    restored = Database(Settings(data_dir=copied, seed_examples=False)); restored.initialize(); restored.engine.dispose()
    with closing(sqlite3.connect(copied/'platform.db')) as connection:
        assert connection.execute('SELECT version FROM schema_versions').fetchall() == [(SCHEMA_VERSION,)]
        assert connection.execute('SELECT COUNT(*) FROM project_recording_grants').fetchone()[0] == 0
        for table, rows in previous.items():assert connection.execute('SELECT * FROM '+table).fetchall() == rows
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
    assert (original/'platform.db').read_bytes() == before
    assert hashlib.sha256(next(path for path in (copied/'recordings').iterdir() if path.suffix=='.mcap').read_bytes()).hexdigest() == source['sha256']
