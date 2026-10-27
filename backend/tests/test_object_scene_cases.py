"""Authored native-shaped contracts only; no simulator execution in these tests."""
import hashlib
import json
import types

from fastapi import APIRouter, Depends, Request
import pytest

from ade2e.adapters import Registry
from ade2e.database import Settings
from ade2e.experiment_runtime import ExperimentExecution, ExperimentHandlerMetadata, ExperimentOutputFile
from ade2e.object_scene_cases import register_object_scene_case_routes
from ade2e.service import canonical_bytes
from ade2e.team_access import authenticate_request
from ade2e.worker import run_once
from test_object_demo import env, save


@pytest.fixture
def captured_source(env, tmp_path):
    db, client, handler, registry, scenario = env
    settings = Settings(data_dir=tmp_path, api_token='test-bootstrap', team_mode=True, seed_examples=False)
    def authenticate(request: Request):
        header = request.headers.get('authorization', '')
        authenticate_request(request, db, settings, header[7:] if header.startswith('Bearer ') else None)
    router = APIRouter(prefix='/api/v1', dependencies=[Depends(authenticate)])
    register_object_scene_case_routes(router, db)
    client.app.include_router(router)
    handler.metadata = types.MethodType(lambda self: ExperimentHandlerMetadata(id='carla-city-object', version='1',
        kinds=['object_sim'], status='available', fidelity='native_carla_object_physics'), handler)
    def output(self, snapshot, work_dir, should_cancel):
        dt = snapshot['scenario']['timestep_s']
        scene = {'schema_version': 'carla-city-scene/1', 'fidelity': 'native_carla_object_physics',
            'physical_sensor_validation': False, 'coordinate_system': snapshot['scenario']['coordinate_system'],
            'source': {**snapshot['source'], 'adapter_version': '1', 'planner_sha256': self.planner_hash},
            'map': {'native_name': 'authored_contract_fixture'},
            'frames': [{'index': i, 'time_s': (i + 1) * dt, 'input_frame': 1000 + i,
                'actors': [{'id': 'ego', 'x': 30 + i * .1, 'y': 0, 'z': 0, 'heading_rad': 0}]} for i in range(round(snapshot['scenario']['duration_s'] / dt))]}
        raw = canonical_bytes(scene); path = work_dir / 'city-scene.json'; path.write_bytes(raw)
        return ExperimentExecution(summary={'scene_artifact': 'city-scene.json', 'scene_sha256': hashlib.sha256(raw).hexdigest()},
            artifacts=[ExperimentOutputFile(path, path.name, 'application/json')], fidelity='native_carla_object_physics')
    handler.execute = types.MethodType(output, handler)
    registry.register(handler)
    scenario['duration_s'] = 1
    ref = save(client, scenario)
    created = client.post('/api/v1/experiments', json={'name': 'Authored Case contract', 'kind': 'object_sim',
        'adapter_id': 'carla-city-object', 'seed': 42, 'input': {'scenario_id': ref['id'], 'scenario_revision': ref['revision'], 'configuration_hash': ref['configuration_hash']}}, headers={'Idempotency-Key': 'city-case-fixture'}).json()
    job = client.post(f"/api/v1/experiments/{created['id']}/executions", json={'preview_hash': created['preview_hash']}, headers={'Idempotency-Key': 'city-case-fixture'}).json()
    run_once(db, Registry(load_plugins=False), 'fixture', job_id=job['id'], experiment_registry=registry)
    execution = client.get('/api/v1/experiments/executions/' + job['id']).json()
    assert execution['status'] == 'completed', execution
    body = {'project_id': 'default', 'execution_manifest_hash': execution['manifest_hash'],
        'scene_sha256': execution['summary']['scene_sha256'], 'frame_index': 3, 'time_s': .2,
        'title': 'Contract fixture frame', 'note': 'Authored fixture only; not a native run.'}
    return db, client, execution, body


def test_native_frame_becomes_real_case_with_exact_lineage_export_and_idempotence(captured_source):
    db, client, execution, body = captured_source
    path = f"/api/v1/object-sim/executions/{execution['id']}/cases"
    response = client.post(path, json=body, headers={'Idempotency-Key': 'capture'})
    assert response.status_code == 201, response.text
    value = response.json()
    assert value['schema_version'] == 'axiom-investigation-case/1'
    assert value['segment']['source_kind'] == 'simulation'
    assert value['segment']['sample_count'] == 1 and value['segment']['start_time_ns'] == '200000000'
    link = value['object_sim_evidence']
    assert link['execution_manifest_hash'] == execution['manifest_hash'] and link['scene_sha256'] == body['scene_sha256']
    assert link['frame_index'] == 3 and link['physical_validation'] is False and link['safety_verdict'] == 'not_evaluated'
    assert link['frame_sha256'] == value['segment']['samples'][0]['sha256']
    assert client.post(path, json=body, headers={'Idempotency-Key': 'capture'}).json()['id'] == value['id']
    assert client.post(path, json={**body, 'note': 'Changed note'}, headers={'Idempotency-Key': 'capture'}).status_code == 409
    read = client.get(path + '/' + value['id']).json()
    assert read['manifest_hash'] == value['manifest_hash']
    export = client.get(path + '/' + value['id'] + '/export')
    assert export.status_code == 200 and hashlib.sha256(export.content).hexdigest() == value['manifest_hash']


@pytest.mark.parametrize('change,status', [({'scene_sha256': '0' * 64}, 409), ({'execution_manifest_hash': '0' * 64}, 409),
    ({'time_s': .21}, 409), ({'frame_index': 500}, 422), ({'project_id': 'other'}, 404)])
def test_case_rejects_foreign_or_inexact_selection(captured_source, change, status):
    _, client, execution, body = captured_source
    response = client.post(f"/api/v1/object-sim/executions/{execution['id']}/cases", json=body | change, headers={'Idempotency-Key': 'bad'})
    assert response.status_code == status, response.text


def test_project_viewer_can_read_scoped_case_but_cannot_capture(captured_source):
    _, client, execution, body = captured_source
    path = f"/api/v1/object-sim/executions/{execution['id']}/cases"
    saved = client.post(path, json=body, headers={'Idempotency-Key': 'first'}).json()
    token = client.post('/api/v1/projects/default/tokens', json={'role': 'Viewer', 'user_label': 'case-viewer'}).json()['token']
    client.headers['Authorization'] = 'Bearer ' + token
    assert client.get(path + '/' + saved['id'] + '?project_id=default').status_code == 200
    assert client.post(path, json=body, headers={'Idempotency-Key': 'viewer'}).status_code == 403
    assert client.get(path + '/' + saved['id'] + '?project_id=other').status_code == 404


def test_source_artifact_corruption_prevents_case_read_and_capture(captured_source):
    db, client, execution, body = captured_source
    path = f"/api/v1/object-sim/executions/{execution['id']}/cases"
    saved = client.post(path, json=body, headers={'Idempotency-Key': 'first'}).json()
    from ade2e.experiment_models import ExperimentArtifactRow
    with db.read_session() as session:
        artifact = session.get(ExperimentArtifactRow, execution['artifacts'][0]['id'])
        (db.experiment_dir / 'artifacts' / artifact.storage_key).write_bytes(b'corrupted')
    assert client.get(path + '/' + saved['id']).status_code == 409
    assert client.post(path, json=body, headers={'Idempotency-Key': 'second'}).status_code == 409
