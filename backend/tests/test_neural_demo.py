"""Authored backend/queue fixtures only; no GPU training or rendering occurs."""
import hashlib
import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from ade2e.adapters import Registry
from ade2e.agent_catalog import catalog
from ade2e.database import Database, Settings
from ade2e.driving_sensor_capture_schemas import driving_capture_template
from ade2e.experiment_models import ExperimentArtifactRow
from ade2e.experiment_routes import register_experiment_routes
from ade2e.experiment_runtime import ExperimentExecution, ExperimentHandlerMetadata, ExperimentOutputFile, ExperimentRegistry
from ade2e.neural_demo import ADAPTER, FIDELITY, KIND, PRESETS, execution_pin, register_neural_demo_routes, resolve_neural_execution
from ade2e.neural_demo_schemas import NeuralReconstructionInput, NeuralRenderInput
from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow
from ade2e.team_access import authenticate_request, register_team_routes
from ade2e.worker import run_once
from test_sensor_demo import CaptureContractFixture


class NeuralContractFixture:
    """Native-shaped authored outputs used solely to check API policy."""
    version = '1.0.0'
    probes = 0
    fail = False

    def __init__(self, db):
        self.db = db

    def metadata(self):
        return ExperimentHandlerMetadata(id=ADAPTER, version=self.version,
            kinds=['neural_reconstruction', 'neural_render'], status='available', fidelity=FIDELITY)

    def prepare(self, spec):
        self.probes += 1
        if spec.kind == 'neural_reconstruction':
            inputs = NeuralReconstructionInput.model_validate(spec.input).model_dump(mode='json')
            source = resolve_neural_execution(self.db, spec.project_id, inputs['source_execution_id'])
            return dict(kind=spec.kind, **inputs, source_pin=execution_pin(source))
        inputs = NeuralRenderInput.model_validate(spec.input).model_dump(mode='json')
        reconstruction = resolve_neural_execution(self.db, spec.project_id, inputs['reconstruction_execution_id'])
        source = resolve_neural_execution(self.db, spec.project_id, reconstruction['summary']['source_execution_id'])
        return dict(kind=spec.kind, **inputs, source_execution_id=source['execution_id'],
            source_pin=execution_pin(source), reconstruction_pin=execution_pin(reconstruction))

    def execute(self, snapshot, work_dir, should_cancel):
        if self.fail:
            raise RuntimeError('Authored fixture failure; no GPU execution')
        outputs = []
        names = (['neural-reconstruction-result.json', 'neural-checkpoint.ckpt'] if snapshot['kind'] == 'neural_reconstruction'
                 else ['neural-render-result.json', 'neural-render-frames.zip'])
        for name in names:
            path = work_dir / name
            path.write_text('Authored contract fixture; no real trained model or image.')
            outputs.append(ExperimentOutputFile(path, name))
        summary = dict(source_execution_id=snapshot['source_execution_id'])
        if snapshot['kind'] == 'neural_reconstruction':
            summary.update(schema_version='neural-reconstruction-result/1',
                checkpoint_sha256=hashlib.sha256(outputs[1].path.read_bytes()).hexdigest())
        else:
            summary.update(schema_version='neural-render-summary/2' if snapshot['mode'] == 'camera_sweep' else 'neural-render-summary/1', **{key: snapshot[key] for key in (
                'reconstruction_execution_id', 'lateral_m', 'height_m', 'mode', 'frame_index')})
        return ExperimentExecution(summary=summary, artifacts=outputs, fidelity=FIDELITY)


@pytest.fixture
def env(tmp_path):
    settings = Settings(data_dir=tmp_path, api_token='test-bootstrap', team_mode=True, seed_examples=False)
    db = Database(settings)
    db.initialize()
    capture = CaptureContractFixture()
    capture.driving = True
    neural = NeuralContractFixture(db)
    registry = ExperimentRegistry([capture, neural], load_plugins=False)
    def authenticate(request: Request):
        header = request.headers.get('authorization', '')
        authenticate_request(request, db, settings, header[7:] if header.startswith('Bearer ') else None)
    api = APIRouter(prefix='/api/v1', dependencies=[Depends(authenticate)])
    register_team_routes(api, db)
    register_neural_demo_routes(api, db, registry)
    register_experiment_routes(api, db, registry)
    app = FastAPI()
    @api.get('/agent/tools')
    def tools(request: Request):
        return catalog(app, request, [], '')
    app.include_router(api)
    client = TestClient(app, headers={'Authorization': 'Bearer test-bootstrap'})
    environment = db, client, neural, registry
    source = execute(environment, dict(name='Authored CARLA source', kind='sensor_capture',
        adapter_id='carla-driving-multisensor', comparison_kind='same_scene_sensor_ab',
        input={side: driving_capture_template() for side in ('rig_a', 'rig_b')}), 'source')
    reconstruction = execute(environment, dict(name='Authored neural checkpoint', kind='neural_reconstruction',
        adapter_id=ADAPTER, input=dict(source_execution_id=source, rig='rig_a', camera='front')), 'train')
    neural.probes = 0
    yield (*environment, source, reconstruction)
    db.engine.dispose()


def execute(env, document, key, complete=True):
    db, client, _, registry = env[:4]
    created = client.post('/api/v1/experiments', json=document, headers={'Idempotency-Key': key})
    assert created.status_code == 201, created.text
    submitted = client.post('/api/v1/experiments/' + created.json()['id'] + '/executions',
        json=dict(preview_hash=created.json()['preview_hash']), headers={'Idempotency-Key': key})
    assert submitted.status_code == 202, submitted.text
    job = submitted.json()['id']
    if complete:
        run_once(db, Registry(load_plugins=False), 'neural-authored-fixture', job_id=job, experiment_registry=registry)
    return job


def bind(env, identity='left', **changes):
    return env[1].post(f'/api/v1/neural-sim/presets/{identity}/bindings', json=dict(
        source_execution_id=env[4], reconstruction_execution_id=env[5], **changes))


def preview(env, identity='left', **changes):
    return env[1].post(f'/api/v1/neural-sim/presets/{identity}/preview',
        json=dict(expected_binding_revision=1, **changes))


def test_sources_and_explicit_optimistic_binding_without_gpu_probe(env):
    client = env[1]
    items = client.get('/api/v1/neural-sim/presets').json()['items']
    assert list(PRESETS) == [item['id'] for item in items]
    assert all(item['status'] == 'unbound' for item in items)
    sources = client.get('/api/v1/neural-sim/sources').json()
    assert sources['sources'][0]['id'] == env[4]
    assert sources['reconstructions'][0]['source_execution_id'] == env[4]
    first = bind(env)
    assert first.status_code == 200, first.text
    assert bind(env).json() == first.json()
    assert bind(env, seed=43).status_code == 409
    assert bind(env, expected_revision=1, seed=43).json()['revision'] == 2
    assert preview(env).status_code == 409
    assert env[2].probes == 0
    current = client.get('/api/v1/neural-sim/presets').json()['items'][0]
    assert current['ready'] and current['saved_execution_id'] is None


@pytest.mark.parametrize('identity', list(PRESETS))
def test_preview_matches_normal_experiment_and_saved_default_is_explicit(env, identity):
    assert bind(env, identity).status_code == 200
    response = preview(env, identity)
    assert response.status_code == 200, response.text
    document = response.json()
    assert document['preview'] == env[1].post('/api/v1/experiments/preview', json=document['experiment']).json()
    assert document['experiment']['input']['lateral_m'] == PRESETS[identity]['lateral_m']
    job = execute(env, document['experiment'], 'render')
    assert bind(env, identity, expected_revision=1, saved_execution_id=job).status_code == 200
    assert next(item for item in env[1].get('/api/v1/neural-sim/presets').json()['items'] if item['id'] == identity)['saved_execution_id'] == job
    other = 'raised' if identity != 'raised' else 'left'
    assert bind(env, other, saved_execution_id=job).status_code == 422


@pytest.mark.parametrize('changes', [dict(parameters={'lateral_m': True}), dict(parameters={'height_m': '0.2'}),
    dict(parameters={'lateral_m': .51}), dict(parameters={'height_m': -.31}), dict(parameters={'yaw': 1}),
    dict(mode='video'), dict(frame_index=True), dict(frame_index=200)])
def test_unsupported_changes_fail_before_renderer_probe(env, changes):
    bind(env)
    assert preview(env, **changes).status_code == 422
    assert env[2].probes == 0


def test_frame_preview_and_browser_numeric_roundtrip_preserve_hash(env):
    bind(env)
    response = preview(env, mode='frame', frame_index=19, parameters={'lateral_m': 0, 'height_m': .1})
    assert response.status_code == 200, response.text
    document = response.json()
    assert document['experiment']['input']['mode'] == 'frame'
    def numbers(value):
        if isinstance(value, dict):
            return {key: numbers(item) for key, item in value.items()}
        if isinstance(value, list):
            return [numbers(item) for item in value]
        return int(value) if type(value) is float and value.is_integer() else value
    created = env[1].post('/api/v1/experiments', json=numbers(document['experiment']), headers={'Idempotency-Key': 'browser'})
    assert created.status_code == 201
    assert created.json()['preview_hash'] == document['preview']['preview_hash']


def test_camera_sweep_preset_has_explicit_frozen_source_defaults_and_bounded_amplitude(env):
    bind(env, 'sweep')
    item = next(item for item in env[1].get('/api/v1/neural-sim/presets').json()['items'] if item['id'] == 'sweep')
    assert item['default_mode'] == 'camera_sweep' and item['default_frame_index'] == 50
    assert item['defaults'] == dict(lateral_m=1.5, height_m=0)
    assert item['parameters'][0]['max'] == 2
    document = preview(env, 'sweep').json()
    assert document['experiment']['input'] == dict(reconstruction_execution_id=env[5], lateral_m=1.5,
        height_m=0, mode='camera_sweep', frame_index=50)
    edited = preview(env, 'sweep', parameters=dict(lateral_m=2), frame_index=199)
    assert edited.status_code == 200, edited.text
    assert edited.json()['experiment']['input']['frame_index'] == 199


@pytest.mark.parametrize('identity,changes', [
    ('sweep', dict(parameters={'lateral_m': 0})), ('sweep', dict(parameters={'lateral_m': -.1})),
    ('sweep', dict(parameters={'lateral_m': 2.01})), ('sweep', dict(parameters={'height_m': .31})),
    ('left', dict(parameters={'lateral_m': 1.5}, mode='frame')),
    ('right', dict(parameters={'lateral_m': 1.5}, mode='sequence')),
])
def test_camera_sweep_bounds_do_not_widen_standard_frame_or_sequence(env, identity, changes):
    bind(env, identity)
    assert preview(env, identity, **changes).status_code == 422
    assert env[2].probes == 0


def test_saved_camera_sweep_requires_the_exact_default_frozen_source_frame(env):
    bind(env, 'sweep')
    request = preview(env, 'sweep', frame_index=80).json()['experiment']
    job = execute(env, request, 'other-source-frame')
    assert bind(env, 'sweep', expected_revision=1, saved_execution_id=job).status_code == 422


def test_source_only_binding_explains_required_training(env):
    response = env[1].post('/api/v1/neural-sim/presets/left/bindings', json=dict(source_execution_id=env[4]))
    assert response.status_code == 200
    item = env[1].get('/api/v1/neural-sim/presets').json()['items'][0]
    assert item['status'] == 'needs_reconstruction' and not item['ready']
    assert preview(env).status_code == 422


def test_hash_checked_owned_source_paths_and_corruption(env):
    resolved = resolve_neural_execution(env[0], 'default', env[4])
    assert all(item['path'].is_file() for item in resolved['artifacts'])
    assert 'path' not in execution_pin(resolved)['artifacts'][0]
    resolved['artifacts'][0]['path'].write_bytes(b'changed')
    with pytest.raises(Exception, match='409'):
        resolve_neural_execution(env[0], 'default', env[4])


def test_source_storage_cannot_escape_owned_artifact_directory(env):
    with env[0].session() as session:
        row = session.scalar(select(ExperimentArtifactRow).where(ExperimentArtifactRow.job_id == env[4]))
        row.storage_key = '../outside-owned-storage'
        session.commit()
    with pytest.raises(Exception, match='outside its owned storage'):
        resolve_neural_execution(env[0], 'default', env[4])


def test_invalid_binding_content_and_checkpoint_lineage_rejected(env):
    db, client = env[:2]
    assert bind(env, saved_execution_id=env[4]).status_code == 422
    bind(env)
    with db.session() as session:
        row = session.get(SimulationCatalogRevisionRow, ('default', KIND, 'left', 1))
        row.manifest += b' '
        session.commit()
    items = client.get('/api/v1/neural-sim/presets').json()['items']
    assert items[0]['status'] == 'invalid' and items[1]['status'] == 'unbound'
    assert preview(env).status_code == 409


def test_cross_project_and_role_boundaries_and_agent_api(env):
    client = env[1]
    project = client.post('/api/v1/projects', json={'name': 'Other'}).json()['id']
    assert bind(env, project_id=project).status_code == 404
    bind(env)
    for role in ('Viewer', 'Engineer'):
        token = client.post('/api/v1/projects/default/tokens', json=dict(user_label=role, role=role)).json()['token']
        headers = {'Authorization': 'Bearer ' + token}
        assert client.get('/api/v1/neural-sim/sources', headers=headers).status_code == 200
        assert client.get('/api/v1/neural-sim/sources', params={'project_id': project}, headers=headers).status_code == 404
        tools = client.get('/api/v1/agent/tools', headers=headers).json()['tools']
        assert len([tool for tool in tools if '/neural-sim/' in tool['path']]) == (4 if role == 'Engineer' else 2)
        assert client.post('/api/v1/neural-sim/presets/left/preview', headers=headers,
            json=dict(expected_binding_revision=1)).status_code == (200 if role == 'Engineer' else 403)


def test_nondefault_or_pending_render_cannot_be_saved_as_representative(env):
    bind(env)
    document = preview(env, parameters={'lateral_m': -.1}).json()['experiment']
    job = execute(env, document, 'custom')
    assert bind(env, expected_revision=1, saved_execution_id=job).status_code == 422
    pending = execute(env, preview(env).json()['experiment'], 'pending', complete=False)
    assert bind(env, expected_revision=1, saved_execution_id=pending).status_code == 422
