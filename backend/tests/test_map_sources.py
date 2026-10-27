"""External map contracts with authored native-receipt fixtures, not CARLA evidence."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from ade2e.adapters import Registry
from ade2e.database import Database, Settings
from ade2e.experiment_runtime import ExperimentExecution, ExperimentHandlerMetadata, ExperimentOutputFile, ExperimentRegistry
from ade2e.experiment_routes import register_experiment_routes
from ade2e.map_sources import register_map_source_routes, resolve_conversion, NATIVE_FIDELITY, QUALIFIER, read_blob, _blob_path
from ade2e.service import canonical_bytes, digest
from ade2e.simulation_catalog import register_simulation_catalog_routes
from ade2e.simulation_catalog_schemas import MapDefinition
from ade2e.team_access import Principal
from ade2e.worker import run_once
from ade2e import map_sources

XODR = b'<OpenDRIVE><header name="test"/><road id="1" name="fixture" length="20" junction="-1"><planView><geometry s="0" x="0" y="0" hdg="0" length="20"><line/></geometry></planView></road></OpenDRIVE>'
RIGHTS = {'license': 'authored test fixture', 'redistribution': 'restricted', 'source_url': 'local:test'}


class FixtureQualifier:
    """Exercises queue/publication contract with a fake native output."""
    def __init__(self, db):
        self.db, self.executions, self.corrupt = db, 0, False
    def metadata(self):
        return ExperimentHandlerMetadata(id=QUALIFIER, version='fixture-only/1', kinds=['object_sim'],
            status='available', fidelity=NATIVE_FIDELITY, cancellation_supported=True)
    def prepare(self, spec):
        source, conversion, _ = resolve_conversion(self.db, spec.project_id, spec.input['source_ref'], spec.input['conversion_ref'])
        return spec.input | {'project_id': spec.project_id, 'candidate_sha256': conversion['candidate_sha256']}
    def execute(self, snapshot, work, should_cancel):
        self.executions += 1
        _, _, candidate = resolve_conversion(self.db, snapshot['project_id'], snapshot['source_ref'], snapshot['conversion_ref'])
        checksum = hashlib.sha256(candidate).hexdigest()
        definition = {'name': 'Fixture', 'native_name': 'OpenDriveMap', 'server_version': snapshot['server_version'],
            'opendrive': {'content': candidate.decode(), 'sha256': checksum}, 'rights': RIGHTS,
            'nodes': [{'id': '1', 'x': 0, 'y': 0, 'z': 0, 'heading_rad': 0, 'road_id': 1, 'section_id': 0,
                       'lane_id': -1, 'lane_width_m': 3.5, 'next_ids': []}], 'signals': [], 'spawn_points': []}
        report = dict(schema_version='carla-map-qualification/1', status='completed', cleanup_status='confirmed',
            source_ref=snapshot['source_ref'], conversion_ref=snapshot['conversion_ref'],
            input_opendrive_sha256=checksum, native_opendrive_sha256='0'*64 if self.corrupt else checksum,
            map_catalog_sha256=hashlib.sha256(canonical_bytes(definition)).hexdigest(), physical_validation=False,
            driving_evaluated=False, qualification_scope='native_map_load_readback_not_driving')
        outputs = []
        for name, data in [('map.xodr', candidate), ('map-catalog.json', canonical_bytes(definition)),
                           ('map-qualification.json', canonical_bytes(report))]:
            path = work/name
            path.write_bytes(data)
            outputs.append(ExperimentOutputFile(path, name))
        return ExperimentExecution(summary=report, artifacts=outputs, fidelity=NATIVE_FIDELITY)


@pytest.fixture
def env(tmp_path):
    db = Database(Settings(data_dir=tmp_path, seed_examples=False))
    db.initialize()
    handler = FixtureQualifier(db)
    registry = ExperimentRegistry([handler], load_plugins=False)
    principal = {'value': Principal('legacy')}
    def auth(request: Request):
        request.state.principal = principal['value']
    api = APIRouter(prefix='/api/v1', dependencies=[Depends(auth)])
    register_map_source_routes(api, db, registry)
    register_simulation_catalog_routes(api, db)
    register_experiment_routes(api, db, registry)
    app = FastAPI()
    app.include_router(api)
    yield db, registry, handler, principal, TestClient(app)
    db.engine.dispose()


def upload(client, raw=XODR, **extra):
    response = client.post('/api/v1/map-sources', json=dict(id='external-fixture', name='Korean pilot fixture',
        source_kind='opendrive', filename='fixture.xodr', content_base64=base64.b64encode(raw).decode(), rights=RIGHTS, **extra))
    assert response.status_code == 201, response.text
    return response.json()


def ref(value):
    return {key: value[key] for key in ('id', 'revision', 'sha256')}


def action(source, **extra):
    return dict(revision=source['revision'], sha256=source['sha256'], **extra)


def convert(client, source, **extra):
    response = client.post('/api/v1/map-sources/'+source['id']+'/convert', json=action(source, **extra))
    assert response.status_code == 200, response.text
    return response.json()


def qualify(client, source, conversion, key='q1'):
    return client.post('/api/v1/map-sources/'+source['id']+'/qualify',
        json=action(source, conversion_ref=ref(conversion)), headers={'Idempotency-Key': key})


def test_original_binary_and_lineage_attachments_survive_export_and_revisions(env):
    _, _, _, _, client = env
    attachment = b'\xff\xfeoriginal OSM or SHP bytes\x00'
    source = upload(client, attachments=[{'filename': 'original.osm', 'role': 'upstream-source',
                    'content_base64': base64.b64encode(attachment).decode()}], metadata={'origin': 'declared-source'})
    assert client.get('/api/v1/map-sources/external-fixture/export?revision=1').content == XODR
    assert client.get('/api/v1/map-sources/external-fixture/export?revision=1&attachment=0').content == attachment
    changed = upload(client, XODR+b'\n', expected_revision=1)
    assert changed['revision'] == 2 and changed['source_sha256'] != source['source_sha256']
    assert len(client.get('/api/v1/map-sources/external-fixture/revisions').json()['items']) == 2
    assert client.get('/api/v1/map-sources/external-fixture?revision=1').json()['sha256'] == source['sha256']


def test_source_immutable_retry_and_invalid_base64_or_filename(env):
    *_, client = env
    source = upload(client)
    assert upload(client)['sha256'] == source['sha256']
    payload = dict(name='x', source_kind='opendrive', filename='x.xodr', content_base64='invalid***', rights=RIGHTS)
    assert client.post('/api/v1/map-sources', json=payload).status_code == 422
    payload.update(content_base64=base64.b64encode(XODR).decode(), filename='../unsafe.xodr')
    assert client.post('/api/v1/map-sources', json=payload).status_code == 422


def test_source_pages_return_latest_revisions_once_and_stop_at_end(env):
    *_, client = env
    for identity in ['source-d', 'source-b', 'source-a', 'source-c']:
        response = client.post('/api/v1/map-sources', json=dict(id=identity, name=identity,
            source_kind='opendrive', filename='fixture.xodr', rights=RIGHTS,
            content_base64=base64.b64encode(XODR).decode()))
        assert response.status_code == 201, response.text
    changed = client.post('/api/v1/map-sources', json=dict(id='source-a', name='source-a revision 2',
        expected_revision=1, source_kind='opendrive', filename='fixture.xodr', rights=RIGHTS,
        content_base64=base64.b64encode(XODR + b'\n').decode())).json()
    first = client.get('/api/v1/map-sources?limit=2').json()
    assert [(s['id'], s['revision']) for s in first['items']] == [('source-a', 2), ('source-b', 1)]
    assert first['items'][0]['sha256'] == changed['sha256'] and first['next_offset'] == 2
    second = client.get('/api/v1/map-sources?limit=2&offset=' + str(first['next_offset'])).json()
    assert [s['id'] for s in second['items']] == ['source-c', 'source-d']
    assert second['next_offset'] is None
    assert client.get('/api/v1/map-sources?limit=2&offset=4').json() == {'items': [], 'next_offset': None}
    assert client.get('/api/v1/map-sources?offset=-1').status_code == 422


def test_unsupported_or_dtd_source_preserved_and_not_qualified(env):
    *_, client = env
    raw = b'<!DOCTYPE OpenDRIVE [<!ENTITY ext SYSTEM "file:///private/secret">]><OpenDRIVE>&ext;</OpenDRIVE>'
    source = upload(client, raw)
    conversion = convert(client, source)
    assert conversion['ready'] is False and conversion['candidate_sha256'] is None
    assert qualify(client, source, conversion).status_code == 422
    assert client.get('/api/v1/map-sources/external-fixture/export').content == raw


def test_unicode_source_kept_distinct_from_ascii_execution_bytes(env):
    db, *_, client = env
    raw = XODR.replace(b'fixture', '한국'.encode())
    source = upload(client, raw)
    conversion = convert(client, source)
    assert conversion['ready'] and conversion['candidate_sha256'] != source['source_sha256']
    candidate = read_blob(db, conversion['candidate_sha256'])
    assert candidate.isascii() and b'&#' in candidate
    assert client.get('/api/v1/map-sources/external-fixture/export').content == raw


def test_queue_is_explicit_idempotent_and_publish_requires_matching_completed_receipt(env):
    db, registry, handler, _, client = env
    source = upload(client)
    conversion = convert(client, source)
    assert convert(client, source)['sha256'] == conversion['sha256']
    queued = qualify(client, source, conversion)
    assert queued.status_code == 202, queued.text
    job = queued.json()
    assert handler.executions == 0 and job['status'] == 'queued'
    assert qualify(client, source, conversion).json()['id'] == job['id']
    publish = action(source, conversion_ref=ref(conversion), execution_id=job['id'])
    assert client.post('/api/v1/map-sources/external-fixture/publish', json=publish).status_code == 409
    result = run_once(db, Registry(), 'map-fixture-worker', job_id=job['id'], experiment_registry=registry)
    assert result.status == 'completed', result.error
    response = client.post('/api/v1/map-sources/external-fixture/publish', json=publish)
    assert response.status_code == 201, response.text
    published = response.json()
    definition = published['map']['definition']
    assert 'generated' not in definition and definition['external']['source'] == ref(source)
    assert definition['external']['qualification_scope'] == 'native_map_load_readback_not_driving'
    assert client.post('/api/v1/map-sources/external-fixture/publish', json=publish).json()['map_ref'] == published['map_ref']
    detail = client.get('/api/v1/map-sources/external-fixture').json()
    assert detail['native_ready'] is True and detail['published_maps'] == [published['map_ref']]
    assert client.post('/api/v1/maps', json={'id': 'forged-external', 'definition': definition}).status_code == 422


def test_publish_rejects_report_hash_mismatch_even_completed_queue_job(env):
    db, registry, handler, _, client = env
    source = upload(client)
    conversion = convert(client, source)
    handler.corrupt = True
    job = qualify(client, source, conversion).json()
    result = run_once(db, Registry(), 'map-fixture-worker', job_id=job['id'], experiment_registry=registry)
    assert result.status == 'completed'
    response = client.post('/api/v1/map-sources/external-fixture/publish',
        json=action(source, conversion_ref=ref(conversion), execution_id=job['id']))
    assert response.status_code == 409
    assert client.get('/api/v1/maps').json()['items'] == []


def test_project_roles_stale_pins_and_blob_tampering_fail_closed(env):
    db, _, _, principal, client = env
    source = upload(client)
    principal['value'] = Principal('project', 'default', 'Viewer')
    assert client.post('/api/v1/map-sources/external-fixture/convert', json=action(source)).status_code == 403
    assert client.get('/api/v1/map-sources/external-fixture?project_id=other').status_code == 404
    principal['value'] = Principal('legacy')
    assert client.post('/api/v1/map-sources/external-fixture/convert', json=action(source)|{'sha256':'0'*64}).status_code == 409
    _blob_path(db, source['source_sha256']).write_bytes(b'corrupted')
    assert client.get('/api/v1/map-sources/external-fixture/export').status_code == 409


def test_historical_catalog_serialization_does_not_gain_external_null(env):
    candidate = {'name':'legacy', 'native_name':'Town01', 'server_version':'1',
        'opendrive':{'content':XODR.decode(),'sha256':hashlib.sha256(XODR).hexdigest()}, 'rights':RIGHTS,
        'nodes':[dict(id='1',x=0,y=0,road_id=1,section_id=0,lane_id=-1,lane_width_m=3.5)]}
    value = MapDefinition.model_validate(candidate).model_dump(mode='json')
    assert 'external' not in value and 'generated' not in value


def test_optional_ngii_result_contract_keeps_report_and_origin_without_claiming_native(env, monkeypatch):
    db, *_, client = env
    raw=b'authored ZIP adapter boundary fixture'
    payload=dict(id='ngii',name='Synthetic adapter contract',source_kind='ngii_shp',filename='fixture.zip',
        content_base64=base64.b64encode(raw).decode(),rights=RIGHTS)
    source=client.post('/api/v1/map-sources',json=payload).json()
    def inspect(data, *, source_name, options):
        assert data==raw and source_name==source['name'] and options=={'selected_link_ids':['L1']}
        return dict(source_sha256=hashlib.sha256(data).hexdigest(),ready=True,xodr_text=XODR.decode(),
            diagnostics=[],coordinate_transform={'local_origin_target_m':[200000,600000,12]},
            inference_flags=['authored_test_only'],normalized_network={'lanes':[{'source_id':'L1'}]},
            converter={'id':'fixture-ngii','version':'1'},fidelity={'kind':'source_derived_road_geometry','native_validation':'not_evaluated'})
    monkeypatch.setitem(sys.modules,'axiom_geospatial',SimpleNamespace(inspect_ngii_zip=inspect))
    converted=convert(client,source,options={'selected_link_ids':['L1']})
    assert converted['ready'] and converted['native_ready'] is False
    assert converted['fidelity']=='source_derived_road_geometry'
    report=client.get('/api/v1/map-sources/ngii/conversions/'+converted['id']+'/export').json()
    assert report['normalized_network']['lanes'][0]['source_id']=='L1'
    assert report['fidelity']['native_validation']=='not_evaluated'


def test_blob_failure_before_publication_does_not_poison_retry(env, monkeypatch):
    db, *_ = env
    raw = b'complete immutable source bytes'
    checksum = hashlib.sha256(raw).hexdigest()
    path = _blob_path(db, checksum)
    original_fsync = map_sources.os.fsync
    def interrupted(_fd):
        raise OSError('authored write interruption')
    monkeypatch.setattr(map_sources.os, 'fsync', interrupted)
    with pytest.raises(OSError, match='authored write interruption'):
        map_sources.write_blob(db, raw)
    assert not path.exists() and not list(path.parent.iterdir())
    monkeypatch.setattr(map_sources.os, 'fsync', original_fsync)
    assert map_sources.write_blob(db, raw) == checksum
    assert read_blob(db, checksum) == raw


def test_blob_is_complete_before_atomic_publication_and_concurrent_retry(env, monkeypatch):
    db, *_ = env
    raw = b'preserved original' * 1000
    checksum = hashlib.sha256(raw).hexdigest()
    path = _blob_path(db, checksum)
    original_link = map_sources.os.link
    def checked_link(source, destination):
        assert Path(source).read_bytes() == raw
        assert Path(source).stat().st_mode & 0o777 == 0o600
        assert Path(destination) == path
        return original_link(source, destination)
    monkeypatch.setattr(map_sources.os, 'link', checked_link)
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(lambda _: map_sources.write_blob(db, raw), range(12))) == [checksum] * 12
    assert read_blob(db, checksum) == raw
    assert list(path.parent.iterdir()) == [path]
    path.write_bytes(b'corruption must not be silently repaired')
    with pytest.raises(HTTPException) as caught:
        map_sources.write_blob(db, raw)
    assert caught.value.status_code == 409
    assert path.read_bytes() == b'corruption must not be silently repaired'


@pytest.mark.parametrize('source_size,attachment_size,expected_decodes', [(40, 40, 0), (33, 32, 1)])
def test_aggregate_upload_bound_precedes_excess_allocation(env, monkeypatch, source_size, attachment_size, expected_decodes):
    db, *_, client = env
    monkeypatch.setattr(map_sources, 'MAX_UPLOAD_BYTES', 64)
    original_decode = map_sources.base64.b64decode
    decoded = []
    def tracked_decode(*args, **kwargs):
        result = original_decode(*args, **kwargs)
        decoded.append(len(result))
        return result
    monkeypatch.setattr(map_sources.base64, 'b64decode', tracked_decode)
    payload = dict(name='bound fixture', source_kind='opendrive', filename='a.xodr', rights=RIGHTS,
        content_base64=base64.b64encode(b'a' * source_size).decode(), attachments=[dict(
            filename='b.osm', role='upstream-source', content_base64=base64.b64encode(b'b' * attachment_size).decode())])
    response = client.post('/api/v1/map-sources', json=payload)
    assert response.status_code == 422, response.text
    assert len(decoded) == expected_decodes and sum(decoded) <= 64
    assert not (db.artifact_dir.parent / 'map-sources').exists()
    assert client.get('/api/v1/map-sources').json()['items'] == []


def test_aggregate_upload_accepts_exact_boundary_with_independent_padding(env, monkeypatch):
    db, *_, client = env
    monkeypatch.setattr(map_sources, 'MAX_UPLOAD_BYTES', 64)
    source = upload(client, b'a' * 31, attachments=[dict(filename='b.osm', role='upstream-source',
                    content_base64=base64.b64encode(b'b' * 33).decode())])
    assert source['size_bytes'] + source['attachments'][0]['size_bytes'] == 64
    assert read_blob(db, source['source_sha256']) == b'a' * 31
    assert client.get('/api/v1/map-sources/external-fixture/export?attachment=0').content == b'b' * 33


@pytest.mark.parametrize('stage', ['qualify', 'publish'])
@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_conversion_report_closure_required_before_qualification_and_publication(env, stage, damage):
    db, registry, handler, _, client = env
    source = upload(client)
    conversion = convert(client, source)
    if stage == 'publish':
        job = qualify(client, source, conversion).json()
        result = run_once(db, Registry(), 'map-fixture-worker', job_id=job['id'], experiment_registry=registry)
        assert result.status == 'completed'
    path = _blob_path(db, conversion['report_sha256'])
    if damage == 'missing':
        path.unlink()
    else:
        path.write_bytes(b'{"tampered":true}')
    if stage == 'qualify':
        response = qualify(client, source, conversion)
        assert handler.executions == 0
        assert client.get('/api/v1/map-sources/external-fixture').json()['executions'] == []
    else:
        response = client.post('/api/v1/map-sources/external-fixture/publish',
            json=action(source, conversion_ref=ref(conversion), execution_id=job['id']))
    assert response.status_code == 409, response.text
    assert client.get('/api/v1/maps').json()['items'] == []
    detail = client.get('/api/v1/map-sources/external-fixture').json()
    assert ref(detail) == ref(source) and ref(detail['conversions'][0]) == ref(conversion)


def test_real_central_auth_allows_project_workflow_but_enforces_roles_and_isolation(tmp_path):
    from ade2e.main import create_app
    registry = ExperimentRegistry([], load_plugins=False)
    app = create_app(Settings(data_dir=tmp_path, seed_examples=False, team_mode=True,
                              api_token='authored-operator-fixture'),
                     Registry(load_plugins=False), experiment_registry=registry)
    db = app.state.db
    registry.register(FixtureQualifier(db))
    with TestClient(app) as client:
        assert client.get('/api/v1/map-sources/capabilities').status_code == 401
        client.headers['Authorization'] = 'Bearer authored-operator-fixture'
        other = client.post('/api/v1/projects', json={'name': 'Other project'}).json()['id']
        tokens = {}
        for label, project, role in [('engineer', 'default', 'Engineer'), ('viewer', 'default', 'Viewer'),
                                     ('other', other, 'Engineer')]:
            response = client.post(f'/api/v1/projects/{project}/tokens',
                                   json={'user_label': label, 'role': role})
            assert response.status_code == 201, response.text
            tokens[label] = response.json()['token']

        client.headers['Authorization'] = 'Bearer ' + tokens['engineer']
        assert client.get('/api/v1/map-sources/capabilities').status_code == 200
        source = upload(client)
        conversion = convert(client, source)
        queued = qualify(client, source, conversion)
        assert queued.status_code == 202, queued.text
        job = queued.json()
        result = run_once(db, Registry(), 'authenticated-fixture-worker', job_id=job['id'], experiment_registry=registry)
        assert result.status == 'completed', result.error
        publish = action(source, conversion_ref=ref(conversion), execution_id=job['id'])
        assert client.post('/api/v1/map-sources/external-fixture/publish', json=publish).status_code == 201

        client.headers['Authorization'] = 'Bearer ' + tokens['viewer']
        for suffix in ['', '/capabilities', '/external-fixture', '/external-fixture/revisions',
                       '/external-fixture/export', '/external-fixture/conversions/' + conversion['id'] + '/export']:
            assert client.get('/api/v1/map-sources' + suffix).status_code == 200
        create_body = dict(name='forbidden', source_kind='opendrive', filename='test.xodr', rights=RIGHTS,
                           content_base64=base64.b64encode(XODR).decode())
        assert client.post('/api/v1/map-sources', json=create_body).status_code == 403
        assert client.post('/api/v1/map-sources/external-fixture/convert', json=action(source)).status_code == 403
        assert qualify(client, source, conversion, 'viewer-cannot-execute').status_code == 403
        assert client.post('/api/v1/map-sources/external-fixture/publish', json=publish).status_code == 403

        client.headers['Authorization'] = 'Bearer ' + tokens['other']
        assert client.get('/api/v1/map-sources/external-fixture').status_code == 404
        assert client.get('/api/v1/map-sources/external-fixture?project_id=' + other).status_code == 404
        assert client.get('/api/v1/map-sources?project_id=' + other).json()['items'] == []
        assert client.post('/api/v1/map-sources/external-fixture/convert', json=action(source)).status_code == 404
        assert client.post('/api/v1/map-sources/external-fixture/publish', json=publish).status_code == 404
        # Adding this project-scoped prefix must not expose legacy domains.
        assert client.get('/api/v1/scenarios').status_code == 403
