"""Authored PNG/runtime fixtures test queue and lineage; not trained-model evidence."""
import hashlib
import io
import json
import zipfile

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from ade2e.adapters import Registry
from ade2e.database import Database
from ade2e.case_models import CaseRevisionRow
from ade2e.experiment_runtime import ExperimentExecution, ExperimentHandlerMetadata, ExperimentOutputFile, ExperimentRegistry
from ade2e.inference_runtime import RuntimeRegistry
from ade2e.main import create_app
from ade2e.neural_demo import execution_pin, resolve_neural_execution
from ade2e.neural_perception import NeuralPerceptionHandler
from ade2e.service import canonical_bytes
from ade2e.worker import run_once
from test_perception_jobs import FakeRuntime
from test_sensors import BASE, make_recording


class ContractRuntime(FakeRuntime):
    def metadata(self):
        return super().metadata().model_copy(update={'id': 'onnx-cpu'})

    def infer(self, *args, **kwargs):
        result = super().infer(*args, **kwargs)
        result['evidence']['runtime_id'] = 'onnx-cpu'
        return result


class AuthoredNeural:
    """Explicit source/render contract fixture: no real-road/GPU claim."""
    def __init__(self, db):
        self.db, self.context = db, {}

    def metadata(self):
        return ExperimentHandlerMetadata(id='neural-splatfacto', version='1.0.0',
            kinds=['neural_source_import', 'neural_reconstruction', 'neural_render'],
            status='available', fidelity='neural_reconstruction_of_carla')

    def prepare(self, spec):
        result = {'kind': spec.kind, **spec.input}
        if spec.kind == 'neural_reconstruction':
            source = resolve_neural_execution(self.db, spec.project_id, spec.input['source_execution_id'])
            result.update(source_pin=execution_pin(source))
        if spec.kind == 'neural_render':
            scene = resolve_neural_execution(self.db, spec.project_id, spec.input['reconstruction_execution_id'])
            source = resolve_neural_execution(self.db, spec.project_id, scene['summary']['source_execution_id'])
            result.update(source_execution_id=source['execution_id'], source_pin=execution_pin(source), reconstruction_pin=execution_pin(scene))
        return result

    def execute(self, snapshot, work_dir, should_cancel):
        if snapshot['kind'] == 'neural_source_import':
            document = dict(schema_version='neural-real-road-source/1', source_kind='real_road', frame_count=3,
                fps=2, width=32, height=32, **self.context,
                frames=[dict(frame_index=index, recording_ordinal=1, log_time_ns=str(BASE)) for index in range(3)])
            names = ['neural-real-road-source.json', 'neural-real-road-dataset.zip']
            (work_dir / names[0]).write_bytes(canonical_bytes(document))
            (work_dir / names[1]).write_bytes(b'Authored source archive fixture')
            return ExperimentExecution(summary=document, artifacts=[ExperimentOutputFile(work_dir / name, name) for name in names], fidelity='recorded_real_road')
        if snapshot['kind'] == 'neural_reconstruction':
            ckpt = work_dir / 'neural-checkpoint.ckpt'
            ckpt.write_bytes(b'Authored checkpoint fixture')
            summary = dict(schema_version='neural-reconstruction-result/1', source_execution_id=snapshot['source_execution_id'],
                checkpoint_sha256=hashlib.sha256(ckpt.read_bytes()).hexdigest())
            path = work_dir / 'neural-reconstruction-result.json'
            path.write_bytes(canonical_bytes(summary))
            return ExperimentExecution(summary=summary, artifacts=[ExperimentOutputFile(path, path.name), ExperimentOutputFile(ckpt, ckpt.name)], fidelity='neural_reconstruction_of_real_road')
        archive = work_dir / 'neural-render-frames.zip'
        frames = []
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as zipped:
            for index in (0, 15, 30, 45, 60):
                frame = dict(frame_index=index, time_s=index / 10, source_frame_index=1, source_time_s=.5,
                    source_frame=1, applied_lateral_m=index / 20 - 1.5, applied_height_m=0)
                for view, color in [('original', 'red'), ('baseline', 'blue'), ('novel', 'red')]:
                    stream = io.BytesIO()
                    Image.new('RGB', (32, 32), color).save(stream, format='PNG')
                    png = stream.getvalue()
                    name = f'{view}/{index:06d}.png'
                    zipped.writestr(name, png)
                    frame[view] = dict(name=name, size_bytes=len(png), sha256=hashlib.sha256(png).hexdigest())
                frames.append(frame)
        summary = dict(schema_version='neural-render-summary/2', source_execution_id=snapshot['source_execution_id'],
            reconstruction_execution_id=snapshot['reconstruction_execution_id'], mode='camera_sweep', frame_index=1,
            lateral_m=1.5, height_m=0, width=32, height=32, fps=10, frame_count=5)
        document = dict(summary, schema_version='neural-render-result/2', frames=frames,
            frames_archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
        path = work_dir / 'neural-render-result.json'
        path.write_bytes(canonical_bytes(document))
        return ExperimentExecution(summary=summary, artifacts=[ExperimentOutputFile(path, path.name), ExperimentOutputFile(archive, archive.name)], fidelity='neural_reconstruction_of_real_road')


def execute(context, document, key, complete=True):
    client, app, runtime, registry, _ = context
    created = client.post('/api/v1/experiments', json=document, headers={'Idempotency-Key': key})
    assert created.status_code == 201, created.text
    job = client.post('/api/v1/experiments/' + created.json()['id'] + '/executions',
        json={'preview_hash': created.json()['preview_hash']}, headers={'Idempotency-Key': key})
    assert job.status_code == 202, job.text
    if complete:
        return run_once(app.state.db, Registry(load_plugins=False), 'sensitivity-contract-worker',
            job_id=job.json()['id'], experiment_registry=registry)
    return job.json()


@pytest.fixture
def context(settings):
    db = Database(settings)
    runtime = ContractRuntime()
    runtimes = RuntimeRegistry(load_plugins=False)
    runtimes.register(runtime, replace=True)
    authored = AuthoredNeural(db)
    registry = ExperimentRegistry([authored, NeuralPerceptionHandler(db, runtimes)], load_plugins=False)
    app = create_app(settings, Registry(load_plugins=False), runtime_registry=runtimes, experiment_registry=registry)
    with TestClient(app) as client:
        recording, channels, _ = make_recording(client)
        authored.context = dict(recording_id=recording['id'], recording_sha256=recording['sha256'], channel_id=channels[0],
            start_time_ns=str(BASE), end_time_ns=str(BASE + 20))
        env = client, app, runtime, registry, {}
        source = execute(env, dict(name='Authored source', kind='neural_source_import', adapter_id='neural-splatfacto', input={'source_id': 'fixture'}), 'source')
        scene = execute(env, dict(name='Authored checkpoint', kind='neural_reconstruction', adapter_id='neural-splatfacto',
            input=dict(source_execution_id=source.id, rig='rig_a', camera='front')), 'scene')
        render = execute(env, dict(name='Authored render', kind='neural_render', adapter_id='neural-splatfacto',
            input=dict(reconstruction_execution_id=scene.id, mode='camera_sweep', frame_index=1, lateral_m=1.5, height_m=0)), 'render')
        artifact = client.post('/api/v1/perception/model-artifacts?name=explicit-contract-fixture.onnx', content=b'explicit-fixture',
            headers={'Content-Type': 'application/octet-stream'}).json()
        model = client.post('/api/v1/perception/models', json=dict(name='Explicit contract model', artifact_id=artifact['id'],
            profile='boxes_xyxy_v1', input_width=32, input_height=32, labels=['test-object'], note='Authored test only')).json()
        request = dict(render_execution_id=render.id, model_id=model['id'], model_revision=1)
        yield client, app, runtime, registry, request
    db.engine.dispose()


def preview(context, **changes):
    return context[0].post('/api/v1/neural-sim/perception/preview', json=context[4] | changes)


def test_triplet_inference_keeps_exact_model_images_and_sensitivity_labels(context):
    client, _, runtime, _, _ = context
    response = preview(context)
    assert response.status_code == 200, response.text
    assert runtime.calls == 0
    document = response.json()
    assert document['preview'] == client.post('/api/v1/experiments/preview', json=document['experiment']).json()
    job = execute(context, document['experiment'], 'sensitivity')
    assert job.status == 'completed', job.error
    assert runtime.calls == 15 and runtime.closed
    result = client.get(f'/api/v1/neural-sim/perception/{job.id}').json()
    assert result['scope'] == 'model_output_sensitivity' and result['case_supported']
    assert result['ground_truth_used'] is False and result['accuracy_evaluated'] is False
    assert len(result['frames']) == 5
    for frame in result['frames']:
        assert frame['summary'] == dict(original_count=1, baseline_count=0, novel_count=1,
            baseline_count_delta=-1, novel_count_delta=1, novel_vs_original_count_delta=0)
        assert frame['novel']['runtime']['image_sha256'] == frame['novel']['image']['sha256']
        assert frame['novel']['detections'][0]['bbox_xyxy'] == [0, 0, 32, 32]
    source_list = client.get('/api/v1/neural-sim/sources').json()
    assert source_list['perceptions'][0]['id'] == job.id
    assert source_list['sources'][0]['frame_count'] == 3


def test_real_road_preset_uses_source_bounded_frame_and_smaller_camera_sweep(context):
    client = context[0]
    sources = client.get('/api/v1/neural-sim/sources').json()
    response = client.post('/api/v1/neural-sim/presets/sweep/bindings', json=dict(
        source_execution_id=sources['sources'][0]['id'], reconstruction_execution_id=sources['reconstructions'][0]['id']))
    assert response.status_code == 200, response.text
    preset = next(item for item in client.get('/api/v1/neural-sim/presets').json()['items'] if item['id'] == 'sweep')
    assert preset['defaults']['lateral_m'] == .5 and preset['parameters'][0]['default'] == .5
    assert preset['default_frame_index'] == 1 and preset['source_frame_count'] == 3
    previewed = client.post('/api/v1/neural-sim/presets/sweep/preview', json={'expected_binding_revision': 1})
    assert previewed.status_code == 200, previewed.text
    assert previewed.json()['experiment']['input']['lateral_m'] == .5
    assert previewed.json()['experiment']['input']['frame_index'] == 1


def test_neural_case_pins_source_and_survives_ordinary_revision_and_export(context):
    client = context[0]
    job = execute(context, preview(context).json()['experiment'], 'case-sensitivity')
    path = f'/api/v1/neural-sim/perception/{job.id}/case'
    body = dict(title='Investigate view sensitivity', note='Outputs vary; no accuracy verdict.', frame_index=30)
    response = client.post(path, json=body, headers={'Idempotency-Key': 'case'})
    assert response.status_code == 201, response.text
    case = response.json()
    assert case['neural_sensitivity']['sensitivity_pin']['execution_id'] == job.id
    assert case['segment']['selector']['start_time_ns'] == str(BASE)
    assert client.post(path, json=body, headers={'Idempotency-Key': 'case'}).json() == case
    assert client.post(path, json=body | {'frame_index': 15}, headers={'Idempotency-Key': 'case'}).status_code == 409
    revised = client.post(f'/api/v1/cases/{case["id"]}/revisions', headers={'Idempotency-Key': 'revise'},
        json=dict(expected_revision=1, title=case['title'], note='Still investigating', state='investigating'))
    assert revised.status_code == 201, revised.text
    assert revised.json()['neural_sensitivity'] == case['neural_sensitivity']
    exported = client.get(revised.json()['download_url'])
    assert exported.status_code == 200 and hashlib.sha256(exported.content).hexdigest() == revised.json()['manifest_hash']


@pytest.mark.parametrize('change', [dict(frame_indices=[0, 0, 30]), dict(frame_indices=[0, 15]),
    dict(frame_indices=[0, 15, 199]), dict(score_threshold=True), dict(model_revision=99)])
def test_invalid_variant_or_model_is_rejected_before_inference(context, change):
    assert preview(context, **change).status_code in (404, 422)
    assert context[2].calls == 0


def test_runtime_failure_does_not_publish_partial_sensitivity_or_fake_empty_predictions(context):
    context[2].failure_at = 2
    job = execute(context, preview(context).json()['experiment'], 'failure')
    assert job.status == 'failed' and not job.artifacts
    assert context[0].get(f'/api/v1/neural-sim/perception/{job.id}').status_code == 422


def test_project_boundary_rejects_other_project_before_inference(context):
    client = context[0]
    project = client.post('/api/v1/projects', json={'name': 'Other project'}).json()
    assert preview(context, project_id=project['id']).status_code == 404
    assert context[2].calls == 0


@pytest.mark.parametrize('change', ['image', 'recording_ordinal'])
def test_case_relationship_rejects_tampered_image_or_original_mapping_even_when_resealed(context, change):
    client, app, *_ = context
    job = execute(context, preview(context).json()['experiment'], 'tamper-sensitivity')
    response = client.post(f'/api/v1/neural-sim/perception/{job.id}/case',
        json=dict(title='Inspect', note='Authored contract fixture only', frame_index=30), headers={'Idempotency-Key': 'case'})
    assert response.status_code == 201, response.text
    case = response.json()
    with app.state.db.session.begin() as session:
        row = session.get(CaseRevisionRow, (case['id'], 1))
        value = json.loads(row.manifest)
        if change == 'image':
            value['neural_sensitivity']['image']['sha256'] = '0' * 64
        else:
            value['neural_sensitivity']['recording_ordinal'] = 2  # Same timestamp; different original message.
        row.manifest = canonical_bytes(value)
        row.manifest_hash = hashlib.sha256(row.manifest).hexdigest()
        row.manifest_size_bytes = len(row.manifest)
    assert client.get(f'/api/v1/cases/{case["id"]}').status_code == 409
