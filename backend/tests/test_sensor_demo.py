"""Authored API/queue fixtures only: no simulator or sensor validation runs."""
import copy
import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from ade2e.adapters import Registry
from ade2e.database import Database, Settings
from ade2e.experiment_models import ExperimentRow
from ade2e.experiment_routes import register_experiment_routes
from ade2e.experiment_runtime import ExperimentExecution, ExperimentHandlerMetadata, ExperimentOutputFile, ExperimentRegistry
from ade2e.sensor_capture_schemas import SensorCaptureSpec, capture_template
from ade2e.driving_sensor_capture_schemas import DrivingSensorCaptureSpec, driving_capture_template
from ade2e.sensor_demo import KIND, PRESETS, register_sensor_demo_routes
from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow
from ade2e.team_access import authenticate_request, register_team_routes
from ade2e.worker import run_once


class CaptureContractFixture:
    """Mimics the native handler envelope so policy can be checked offline."""
    version = 'fixture-1'
    status = 'available'
    source = 1
    probes = 0
    fail = False

    driving = False

    def metadata(self):
        return ExperimentHandlerMetadata(id='carla-driving-multisensor' if self.driving else 'carla-streaming-capture', version=self.version,
            kinds=['sensor_capture'], status=self.status, fidelity='actual_carla_reference_driving_multisensor' if self.driving else 'actual_carla_native_sensor_model')

    def prepare(self, spec):
        self.probes += 1
        rigs = {}
        for key, rig in spec.input.items():
            models = (DrivingSensorCaptureSpec if self.driving else SensorCaptureSpec).model_validate(rig)
            snapshot = models.model_dump(mode='json')
            snapshot['seed'] = spec.seed
            for sensor, model in zip(snapshot['sensors'], models.sensors):
                sensor['native_parameters'] = model.native_parameters()
            rigs[key] = snapshot
        return dict(schema_version='carla-driving-multisensor-experiment/1' if self.driving else 'carla-streaming-experiment/1', rigs=rigs,
            comparison_kind=spec.comparison_kind, fixture_source_revision=self.source)

    def execute(self, snapshot, work_dir, should_cancel):
        if self.fail:
            raise RuntimeError('Authored fixture failure; no native execution')
        outputs = []
        for side in ('rig_a', 'rig_b'):
            for suffix in ('capture-manifest.json', 'native-capture.zip'):
                path = work_dir / f'{side}-{suffix}'
                path.write_text('Authored fixture only. This is not a CARLA artifact.')
                outputs.append(ExperimentOutputFile(path, path.name))
        return ExperimentExecution(summary=dict(schema_version='sensor-experiment-result/1',
            captures={side: dict(frame_count=200 if self.driving else 100, sensor_count=4, drop_count=0, cleanup_status='confirmed')
                      for side in ('rig_a', 'rig_b')}), artifacts=outputs,
            fidelity=self.metadata().fidelity)


@pytest.fixture
def env(tmp_path):
    settings = Settings(data_dir=tmp_path, api_token='test-bootstrap', team_mode=True, seed_examples=False)
    db = Database(settings)
    db.initialize()
    handler = CaptureContractFixture()
    driving_handler = CaptureContractFixture()
    driving_handler.driving = True
    registry = ExperimentRegistry([handler, driving_handler], load_plugins=False)
    def authenticate(request: Request):
        header = request.headers.get('authorization', '')
        authenticate_request(request, db, settings, header[7:] if header.startswith('Bearer ') else None)
    api = APIRouter(prefix='/api/v1', dependencies=[Depends(authenticate)])
    register_team_routes(api, db)
    register_sensor_demo_routes(api, db, registry)
    register_experiment_routes(api, db, registry)
    app = FastAPI()
    app.include_router(api)
    client = TestClient(app, headers={'Authorization': 'Bearer test-bootstrap'})
    yield db, client, handler, registry
    db.engine.dispose()


def bind(client, preset='camera_fov', **extra):
    return client.post(f'/api/v1/sensor-sim/presets/{preset}/bindings', json=extra)


def preview(client, preset='camera_fov', **extra):
    return client.post(f'/api/v1/sensor-sim/presets/{preset}/preview', json={
        'expected_binding_revision': 1, **extra})


def submit(env, preset='camera_fov', key='execute', complete=True):
    db, client, _, registry = env
    result = preview(client, preset)
    assert result.status_code == 200, result.text
    draft = result.json()
    created = client.post('/api/v1/experiments', json=draft['experiment'], headers={'Idempotency-Key': key})
    assert created.status_code == 201, created.text
    response = client.post('/api/v1/experiments/' + created.json()['id'] + '/executions',
        json={'preview_hash': draft['preview']['preview_hash']}, headers={'Idempotency-Key': key})
    assert response.status_code == 202, response.text
    identity = response.json()['id']
    if complete:
        run_once(db, Registry(), 'sensor-demo-fixture', job_id=identity, experiment_registry=registry)
    return identity


def test_explicit_binding_idempotence_and_no_probe_or_automatic_result_selection(env):
    _, client, handler, _ = env
    items = client.get('/api/v1/sensor-sim/presets').json()['items']
    assert [item['id'] for item in items] == list(PRESETS)
    assert all(item['status'] == 'unbound' and not item['ready'] for item in items)
    first = bind(client)
    assert first.status_code == 200, first.text
    assert first.json()['revision'] == 1
    assert bind(client).json() == first.json()
    assert bind(client, seed=43).status_code == 409
    second = bind(client, seed=43, expected_revision=1)
    assert second.json()['revision'] == 2
    assert preview(client).status_code == 409
    item = client.get('/api/v1/sensor-sim/presets').json()['items'][0]
    assert item['ready'] and item['source']['seed'] == 43
    assert item['saved_execution_id'] is None and item['binding_revision'] == 2
    assert handler.probes == 0


@pytest.mark.parametrize('identity', list(PRESETS))
def test_one_parameter_preview_reuses_exact_normal_experiment_contract(env, identity):
    db, client, _, _ = env
    assert bind(client, identity).status_code == 200
    result = preview(client, identity)
    assert result.status_code == 200, result.text
    document = result.json()
    spec = document['experiment']
    preset = PRESETS[identity]
    assert spec['adapter_id'] == ('carla-driving-multisensor' if identity == 'driving_camera_fov' else 'carla-streaming-capture')
    assert spec['adapter_version'] == 'fixture-1'
    assert spec['comparison_kind'] == 'same_scene_sensor_ab'
    assert spec['input']['rig_a'] == (driving_capture_template() if identity == 'driving_camera_fov' else capture_template())
    changed = copy.deepcopy(spec['input']['rig_a'])
    changed['rig_revision'] += 1
    next(s for s in changed['sensors'] if s['key'] == preset['sensor_key'])['parameters'][preset['native_key']] = str(preset['default'])
    assert changed == spec['input']['rig_b']
    assert document['changes'][0]['value'] == preset['default']
    ordinary = client.post('/api/v1/experiments/preview', json=spec)
    assert ordinary.status_code == 200
    assert ordinary.json() == document['preview']
    with db.read_session() as session:
        assert session.scalar(select(ExperimentRow)) is None


@pytest.mark.parametrize('preset,parameters', [
    ('camera_fov', {'front_fov_deg': 90}), ('camera_fov', {'front_fov_deg': 111}),
    ('camera_fov', {'front_fov_deg': True}), ('camera_fov', {'front_fov_deg': '70'}),
    ('camera_fov', {'front_fov_deg': 70.5}), ('camera_fov', {'radar_range_m': 30}),
    ('lidar_density', {'lidar_points_per_second': 8500}), ('radar_range', {'radar_range_m': 0}),
])
def test_unsupported_edits_fail_before_probe(env, preset, parameters):
    _, client, handler, _ = env
    assert bind(client, preset).status_code == 200
    assert preview(client, preset, parameters=parameters).status_code == 422
    assert handler.probes == 0


@pytest.mark.parametrize('field,value', [('duration_seconds', 20), ('spawn_index', 1), ('expected_map', 'Town03')])
def test_fixed_scene_cannot_be_changed_by_binding(env, field, value):
    _, client, _, _ = env
    rig = capture_template()
    rig[field] = value
    assert bind(client, capture_spec=rig).status_code == 422


def test_binding_rejects_changed_sensor_mount_and_accepts_explicit_build_pin(env):
    _, client, _, _ = env
    rig = capture_template()
    rig['sensors'][0]['translation_m'][0] = 2
    assert bind(client, capture_spec=rig).status_code == 422
    rig = capture_template()
    rig['expected_server_version'] = 'another-explicit-build'
    assert bind(client, capture_spec=rig).status_code == 200
    assert preview(client).json()['experiment']['input']['rig_a']['expected_server_version'] == 'another-explicit-build'


def test_native_source_drift_and_frozen_submission_hash(env):
    _, client, handler, _ = env
    bind(client)
    draft = preview(client).json()
    handler.source += 1
    created = client.post('/api/v1/experiments', json=draft['experiment'], headers={'Idempotency-Key': 'changed'})
    assert created.status_code == 201
    assert created.json()['preview_hash'] != draft['preview']['preview_hash']
    rejected = client.post('/api/v1/experiments/' + created.json()['id'] + '/executions',
        json={'preview_hash': draft['preview']['preview_hash']}, headers={'Idempotency-Key': 'changed'})
    assert rejected.status_code == 409
    handler.version = 'fixture-2'
    assert preview(client).status_code == 422
    assert client.get('/api/v1/sensor-sim/presets').json()['items'][0]['status'] == 'invalid'


def test_browser_json_number_roundtrip_preserves_preview_and_can_submit(env):
    """JSON.stringify serializes 0.0 as 0 inside the untyped Experiment input."""
    _, client, _, _ = env
    bind(client)
    draft = preview(client, parameters={'front_fov_deg': 80}).json()
    def browser_numbers(value):
        if isinstance(value, dict):
            return {key: browser_numbers(item) for key, item in value.items()}
        if isinstance(value, list):
            return [browser_numbers(item) for item in value]
        if type(value) is float and value.is_integer():
            return int(value)
        return value
    body = browser_numbers(draft['experiment'])
    created = client.post('/api/v1/experiments', json=body, headers={'Idempotency-Key': 'browser-roundtrip'})
    assert created.status_code == 201, created.text
    assert created.json()['preview_hash'] == draft['preview']['preview_hash']
    submitted = client.post('/api/v1/experiments/' + created.json()['id'] + '/executions',
        json={'preview_hash': draft['preview']['preview_hash']}, headers={'Idempotency-Key': 'browser-roundtrip'})
    assert submitted.status_code == 202, submitted.text


def test_saved_result_must_be_explicit_completed_and_match_its_preset(env):
    _, client, _, _ = env
    bind(client)
    saved = submit(env)
    assert client.get('/api/v1/sensor-sim/presets').json()['items'][0]['saved_execution_id'] is None
    attached = bind(client, expected_revision=1, saved_execution_id=saved)
    assert attached.status_code == 200, attached.text
    item = client.get('/api/v1/sensor-sim/presets').json()['items'][0]
    assert item['ready'] and item['saved_execution_id'] == saved
    assert bind(client, 'radar_range', saved_execution_id=saved).status_code == 422
    assert bind(client, expected_revision=2, saved_execution_id='job_missing').status_code == 404


def test_historical_float_encoded_native_preset_can_still_be_bound(env):
    db, client, _, registry = env
    bind(client)
    draft = preview(client).json()['experiment']
    # Previous Python-authored experiment specs stored 0.0 vectors verbatim.
    draft['input'] = {side: SensorCaptureSpec.model_validate(rig).model_dump(mode='json')
                      for side, rig in draft['input'].items()}
    assert type(draft['input']['rig_a']['sensors'][0]['rotation_deg'][0]) is float
    created = client.post('/api/v1/experiments', json=draft, headers={'Idempotency-Key': 'historical'})
    assert created.status_code == 201
    original_hash = created.json()['preview_hash']
    submitted = client.post('/api/v1/experiments/' + created.json()['id'] + '/executions',
        json={'preview_hash': original_hash}, headers={'Idempotency-Key': 'historical'})
    assert submitted.status_code == 202
    identity = submitted.json()['id']
    run_once(db, Registry(), 'historical-fixture', job_id=identity, experiment_registry=registry)
    attached = bind(client, expected_revision=1, saved_execution_id=identity)
    assert attached.status_code == 200, attached.text
    assert client.get('/api/v1/experiments/' + created.json()['id']).json()['preview_hash'] == original_hash


def test_pending_or_failed_saved_execution_rejected(env):
    _, client, handler, _ = env
    bind(client)
    pending = submit(env, key='pending', complete=False)
    assert bind(client, expected_revision=1, saved_execution_id=pending).status_code == 422
    handler.fail = True
    failed = submit(env, key='failed')
    assert bind(client, expected_revision=1, saved_execution_id=failed).status_code == 422


def test_tampered_binding_fails_closed_and_other_presets_remain_listable(env):
    db, client, _, _ = env
    bind(client)
    with db.session() as session:
        row = session.get(SimulationCatalogRevisionRow, ('default', KIND, 'camera_fov', 1))
        row.manifest = row.manifest + b' '
        session.commit()
    items = client.get('/api/v1/sensor-sim/presets').json()['items']
    assert items[0]['status'] == 'invalid' and not items[0]['ready']
    assert items[1]['status'] == 'unbound'
    assert preview(client).status_code == 409


def test_project_tokens_read_edit_and_execute_permissions(env):
    _, client, handler, _ = env
    bind(client)
    project = client.post('/api/v1/projects', json={'name': 'Another project'}).json()['id']
    for role in ('Viewer', 'Reviewer', 'Engineer'):
        token = client.post('/api/v1/projects/default/tokens', json={'user_label': role, 'role': role}).json()['token']
        headers = {'Authorization': 'Bearer ' + token}
        assert client.get('/api/v1/sensor-sim/presets', headers=headers).status_code == 200
        assert client.get('/api/v1/sensor-sim/presets', params={'project_id': project}, headers=headers).status_code == 404
        body = {'expected_binding_revision': 1}
        expected = 200 if role == 'Engineer' else 403
        assert client.post('/api/v1/sensor-sim/presets/camera_fov/preview', json=body, headers=headers).status_code == expected
        assert client.post('/api/v1/sensor-sim/presets/camera_fov/bindings', json={}, headers=headers).status_code == expected
    assert handler.probes == 1
    assert client.get('/api/v1/sensor-sim/presets', headers={'Authorization': ''}).status_code == 401


def test_saved_result_cannot_cross_project_boundary(env):
    _, client, _, _ = env
    bind(client)
    saved = submit(env)
    project = client.post('/api/v1/projects', json={'name': 'Another project'}).json()['id']
    assert bind(client, project_id=project, saved_execution_id=saved).status_code == 404


def test_driving_preset_binds_only_its_own_complete_native_contract(env):
    _, client, _, _ = env
    identity = 'driving_camera_fov'
    assert bind(client, identity, capture_spec=capture_template()).status_code == 422
    assert bind(client, identity).status_code == 200
    document = preview(client, identity).json()
    assert document['experiment']['input']['rig_a']['duration_seconds'] == 20
    assert document['experiment']['input']['rig_a']['controller']['target_speed_mps'] == 3
    saved = submit(env, identity, key='driving')
    assert bind(client, identity, expected_revision=1, saved_execution_id=saved).status_code == 200
    item = next(item for item in client.get('/api/v1/sensor-sim/presets').json()['items'] if item['id'] == identity)
    assert item['ready'] and item['source']['adapter_id'] == 'carla-driving-multisensor'
    assert item['saved_execution_id'] == saved
    assert bind(client, 'camera_fov', saved_execution_id=saved).status_code == 422
