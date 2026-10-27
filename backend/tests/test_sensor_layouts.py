"""Authored API/queue/archive fixtures only; these tests never execute CARLA."""
import copy
import hashlib
import json
import zipfile
import pytest
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from ade2e import experiment_jobs
from ade2e.adapters import Registry
from ade2e.database import Settings
from ade2e.experiment_models import ExperimentArtifactRow, ExperimentRow
from ade2e.experiment_runtime import ExperimentExecution, ExperimentOutputFile
from ade2e.sensor_layouts import LAYOUT, LINK, OBSERVATION, _ref, register_sensor_layout_routes
from ade2e.sensor_layout_schemas import ADAPTER, VERSION, FIDELITY, baseline_rig
from ade2e.service import canonical_bytes, digest
from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow as Row
from ade2e.team_access import authenticate_request
from ade2e.worker import run_once
from test_sensor_demo import env as base_env


@pytest.fixture
def env(base_env, tmp_path, monkeypatch):
    db, client, _, registry = base_env
    handler = registry.get(ADAPTER)
    handler.version = VERSION
    settings = Settings(data_dir=tmp_path, api_token='test-bootstrap', team_mode=True, seed_examples=False)
    def authenticate(request: Request):
        header = request.headers.get('authorization', '')
        authenticate_request(request, db, settings, header[7:] if header.startswith('Bearer ') else None)
    api = APIRouter(prefix='/api/v1', dependencies=[Depends(authenticate)])
    register_sensor_layout_routes(api, db, registry)
    client.app.include_router(api)
    original_prepare = handler.prepare
    def prepare(spec):
        return {**original_prepare(spec), 'private_transport': '/private/operator/ssh-secret'}
    monkeypatch.setattr(handler, 'prepare', prepare)
    def execute(snapshot, work_dir, should_cancel):
        if handler.fail:
            raise RuntimeError('Authored failure; no native execution')
        captures, outputs = {}, []
        for stage, rig in snapshot['rigs'].items():
            records, artifacts, payloads = [], [], {}
            for index in range(200):
                for sensor in rig['sensors']:
                    key = sensor['key']
                    name = f'frames/{index:04d}-{key}.' + ('bgra8' if sensor['kind'] == 'rgb' else 'f32')
                    raw = b'Authored fixture; not native sensor evidence.'
                    descriptor = dict(name=name, size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
                    artifacts.append(descriptor); payloads[name] = raw
                    stamp = 100 + index / 10
                    records.append(dict(index=index, key=key, frame=index + 1000, timestamp_seconds=stamp,
                        simulation_timestamp_ns=str(round(stamp * 1e9)), raw_artifact=descriptor))
            manifest = dict(schema_version='carla-driving-multisensor-result/1', adapter_version=VERSION,
                configuration_hash=digest(rig), rig_revision=rig['rig_revision'], seed=42, status='completed', fidelity=FIDELITY,
                external_execution_id='fixture-' + stage, frame_count=200, records=records, artifacts=artifacts)
            if getattr(handler, 'corrupt_frame', False):
                next(r for r in records if r['key'] == 'lidar' and r['index'] == 12)['frame'] += 1
            raw = canonical_bytes(manifest)
            path = work_dir / (stage + '-capture-manifest.json'); path.write_bytes(raw)
            archive = work_dir / (stage + '-native-capture.zip')
            with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as bundle:
                bundle.writestr('capture-manifest.json', raw)
                for name, data in payloads.items(): bundle.writestr(name, data)
            outputs.extend([ExperimentOutputFile(path, path.name, 'application/json'), ExperimentOutputFile(archive, archive.name, 'application/zip')])
            captures[stage] = dict(configuration_hash=digest(rig), frame_count=200, sensor_count=4, drop_count=0,
                cleanup_status='confirmed', external_execution_id=manifest['external_execution_id'],
                archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
        return ExperimentExecution(summary=dict(schema_version='sensor-experiment-result/1', captures=captures),
            artifacts=outputs, fidelity=FIDELITY)
    monkeypatch.setattr(handler, 'execute', execute)
    return db, client, handler, registry


def draft(**changes):
    rig = baseline_rig()
    rig['sensors'][0]['rotation_deg'][1] = -15
    return dict(project_id='default', name='Placement study', note='Authored fixture', rig_b=rig) | changes


def save(env, body=None, key='save'):
    response = env[1].post('/api/v1/sensor-sim/layouts', json=body or draft(), headers={'Idempotency-Key': key})
    assert response.status_code == 201, response.text
    return response.json()


def preview(env, layout):
    return env[1].post(f'/api/v1/sensor-sim/layouts/{layout["id"]}/preview', json=dict(project_id='default', layout_ref=_ref(layout)))


def submit(env, layout, *, complete=True, key='execute'):
    checked = preview(env, layout)
    assert checked.status_code == 200, checked.text
    response = env[1].post(f'/api/v1/sensor-sim/layouts/{layout["id"]}/executions',
        json=dict(project_id='default', layout_ref=_ref(layout), preview_hash=checked.json()['preview']['preview_hash']),
        headers={'Idempotency-Key': key})
    assert response.status_code == 202, response.text
    if complete:
        run_once(env[0], Registry(), 'placement-authored-fixture', job_id=response.json()['execution_id'], experiment_registry=env[3])
    return response.json(), checked.json()


def observe(env, layout, execution_id, **changes):
    return env[1].post('/api/v1/sensor-sim/layout-observations', json=dict(
        project_id='default', layout_ref=_ref(layout), execution_id=execution_id, sensor_key='lidar', side='both', time_s=1.25,
        title='Mounted point distribution', note='User-authored comparison note') | changes, headers={'Idempotency-Key': 'note'})


def count(env, model):
    with env[0].read_session() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_offline_template_save_revision_clone_and_restore(env):
    handler = env[3]._handlers.pop(ADAPTER)
    template = env[1].get('/api/v1/sensor-sim/layouts/templates').json()
    assert template['template']['rig_a'] == baseline_rig() and template['bounds']['translation_m']['step'] == .05
    assert [t['id'] for t in template['tasks']] == ['camera_direction', 'lidar_height', 'radar_direction']
    first = save(env)
    assert handler.probes == 0 and first['rig_a'] == baseline_rig()
    assert save(env) == first
    assert preview(env, first).status_code == 422
    changed = draft(id=first['id'], expected_revision=1)
    changed['rig_b']['sensors'][2]['translation_m'][2] = 2.5
    second = save(env, changed, 'r2')
    assert second['revision'] == 2
    assert env[1].get('/api/v1/sensor-sim/layouts/' + first['id'], params={'revision': 1}).json() == first
    assert env[1].get('/api/v1/sensor-sim/layouts/' + first['id']).json() == second
    revisions = env[1].get('/api/v1/sensor-sim/layouts/' + first['id'] + '/revisions').json()['items']
    assert [v['revision'] for v in revisions] == [2, 1]
    clone = save(env, draft(copied_from=_ref(first)), 'copy')
    assert clone['id'] != first['id'] and clone['copied_from'] == _ref(first)
    assert env[1].post('/api/v1/sensor-sim/layouts', json=changed, headers={'Idempotency-Key': 'stale'}).status_code == 409
    assert env[1].post('/api/v1/sensor-sim/layouts', json=draft(name='Other'), headers={'Idempotency-Key': 'save'}).status_code == 409
    assert env[1].get('/api/v1/sensor-sim/layouts', params={'limit': 1}).json()['next_offset'] == 1


@pytest.mark.parametrize('field,value', [('duration_seconds', 30), ('spawn_index', 1), ('sample_hz', 20),
    ('expected_map', 'Town01'), ('rig_revision', 2), ('vehicle_blueprint', 'vehicle.other.car'), ('expected_server_version', 'other')])
def test_fixed_profile_cannot_be_changed(env, field, value):
    body = draft(); body['rig_b'][field] = value
    assert env[1].post('/api/v1/sensor-sim/layouts', json=body, headers={'Idempotency-Key': 'bad'}).status_code == 422
    assert count(env, Row) == 0 and env[2].probes == 0


@pytest.mark.parametrize('change', ['missing', 'extra', 'reorder', 'key', 'kind', 'parameters', 'translation_bounds', 'translation_step', 'rotation_step', 'controller'])
def test_only_four_sensor_mounts_can_change(env, change):
    body = draft(); rig = body['rig_b']
    if change == 'missing': rig['sensors'].pop()
    elif change == 'extra': rig['sensors'].append(copy.deepcopy(rig['sensors'][0]))
    elif change == 'reorder': rig['sensors'].reverse()
    elif change == 'key': rig['sensors'][0]['key'] = 'other'
    elif change == 'kind': rig['sensors'][0]['kind'] = 'radar'
    elif change == 'parameters': rig['sensors'][0]['parameters']['fov'] = '70'
    elif change == 'translation_bounds': rig['sensors'][0]['translation_m'][0] = 10.05
    elif change == 'translation_step': rig['sensors'][0]['translation_m'][0] = 1.51
    elif change == 'rotation_step': rig['sensors'][0]['rotation_deg'][0] = .5
    elif change == 'controller': rig['controller']['target_speed_mps'] = 4
    assert env[1].post('/api/v1/sensor-sim/layouts', json=body, headers={'Idempotency-Key': 'bad'}).status_code == 422


def test_zero_delta_can_save_offline_but_cannot_preview(env):
    saved = save(env, draft(rig_b=baseline_rig()))
    assert preview(env, saved).status_code == 422 and env[2].probes == 0


def test_atomic_execution_exact_revision_and_retry_without_engine(env):
    layout = save(env)
    link, reviewed = submit(env, layout, complete=False)
    assert link['layout_ref'] == _ref(layout) and link['execution']['input_hash'] == reviewed['preview']['preview_hash']
    assert count(env, ExperimentRow) == 1
    env[3]._handlers.pop(ADAPTER)
    repeated = env[1].post(f'/api/v1/sensor-sim/layouts/{layout["id"]}/executions',
        json=dict(project_id='default', layout_ref=_ref(layout), preview_hash=reviewed['preview']['preview_hash']),
        headers={'Idempotency-Key': 'execute'})
    assert repeated.status_code == 202 and repeated.json()['execution_id'] == link['execution_id']
    assert count(env, ExperimentRow) == 1
    revised = save(env, draft(id=layout['id'], expected_revision=1, name='Renamed'), 'r2')
    assert env[1].get(f'/api/v1/sensor-sim/layouts/{layout["id"]}/executions', params={'revision': 2}).json()['items'] == []
    assert env[1].get(f'/api/v1/sensor-sim/layouts/{layout["id"]}/executions', params={'revision': 1}).json()['items'][0]['layout_ref'] == _ref(layout)
    assert observe(env, revised, link['execution_id']).status_code == 409


def test_changed_preview_creates_no_experiment_or_queue_record(env):
    layout = save(env); checked = preview(env, layout).json()
    env[2].source += 1
    response = env[1].post(f'/api/v1/sensor-sim/layouts/{layout["id"]}/executions',
        json=dict(project_id='default', layout_ref=_ref(layout), preview_hash=checked['preview']['preview_hash']),
        headers={'Idempotency-Key': 'changed'})
    assert response.status_code == 409 and count(env, ExperimentRow) == 0
    with env[0].read_session() as session:
        assert not session.scalars(select(Row).where(Row.kind == LINK)).all()


def test_submission_failure_rolls_back_experiment_and_link(env, monkeypatch):
    layout = save(env); checked = preview(env, layout).json()
    def fail(*args, **kwargs): raise HTTPException(503, 'Authored admission failure')
    monkeypatch.setattr(experiment_jobs, 'submit_row', fail)
    response = env[1].post(f'/api/v1/sensor-sim/layouts/{layout["id"]}/executions',
        json=dict(project_id='default', layout_ref=_ref(layout), preview_hash=checked['preview']['preview_hash']), headers={'Idempotency-Key': 'failure'})
    assert response.status_code == 503 and count(env, ExperimentRow) == 0


def test_preview_json_is_browser_stable_and_bad_reference_fails(env):
    layout = save(env); result = preview(env, layout).json()
    js_input = json.loads(json.dumps(result['experiment']))
    # The local serializer already makes integral input floats browser-compatible.
    assert type(js_input['input']['rig_a']['sensors'][0]['translation_m'][1]) is int
    created = env[1].post('/api/v1/experiments', json=js_input, headers={'Idempotency-Key': 'normal-api'})
    assert created.json()['preview_hash'] == result['preview']['preview_hash']
    bad = env[1].post(f'/api/v1/sensor-sim/layouts/{layout["id"]}/preview',
        json=dict(project_id='default', layout_ref={**_ref(layout), 'sha256': 'a' * 64}))
    assert bad.status_code == 409


def test_completed_observation_references_exact_retained_frames_and_export(env):
    layout = save(env); link, _ = submit(env, layout)
    response = observe(env, layout, link['execution_id'])
    assert response.status_code == 201, response.text
    observation = response.json()
    assert observation['verification_status'] == 'not_a_test_verdict' and observation['layout_ref'] == _ref(layout)
    assert set(observation['samples']) == {'rig_a', 'rig_b'}
    for sample in observation['samples'].values():
        assert sample['index'] == 12 and sample['time_s'] == 1.2 and sample['frame'] == 1012
        assert sample['artifact'] == 'frames/0012-lidar.f32' and sample['manifest_sha256'] and sample['archive_sha256']
    assert observe(env, layout, link['execution_id']).json() == observation
    values = env[1].get('/api/v1/sensor-sim/layout-observations', params={'layout_id': layout['id'], 'revision': 1}).json()['items']
    assert values == [observation]
    exported = env[1].get(f'/api/v1/sensor-sim/layouts/{layout["id"]}/export', params={'revision': 1})
    assert exported.status_code == 200 and exported.json()['observations'] == [observation]
    assert hashlib.sha256(exported.content).hexdigest() == exported.headers['X-Content-SHA256']
    assert b'private_transport' not in exported.content and b'ssh-secret' not in exported.content


def test_malformed_sealed_native_frame_fails_closed(env):
    env[2].corrupt_frame = True
    layout = save(env); link, _ = submit(env, layout)
    response = observe(env, layout, link['execution_id'])
    assert response.status_code == 409 and 'frame reference' in response.text
    with env[0].read_session() as session:
        assert not session.scalars(select(Row).where(Row.kind == OBSERVATION)).all()


def test_changed_artifact_bytes_are_not_accepted_as_observation(env):
    layout = save(env); link, _ = submit(env, layout)
    with env[0].read_session() as session:
        artifact = session.scalar(select(ExperimentArtifactRow).where(ExperimentArtifactRow.job_id == link['execution_id'],
            ExperimentArtifactRow.name == 'rig_a-native-capture.zip'))
        (env[0].experiment_dir / 'artifacts' / artifact.storage_key).write_bytes(b'changed archive')
    assert observe(env, layout, link['execution_id']).status_code == 409


def test_queued_and_out_of_range_observations_rejected(env):
    layout = save(env); link, _ = submit(env, layout, complete=False)
    assert observe(env, layout, link['execution_id']).status_code == 422
    assert observe(env, layout, link['execution_id'], time_s=20).status_code == 422


def test_project_roles_and_foreign_clone_cannot_cross_scope(env):
    client = env[1]; layout = save(env)
    project = client.post('/api/v1/projects', json={'name': 'Foreign'}).json()['id']
    for role in ('Viewer', 'Reviewer', 'Engineer'):
        token = client.post('/api/v1/projects/default/tokens', json={'user_label': role, 'role': role}).json()['token']
        headers = {'Authorization': 'Bearer ' + token, 'Idempotency-Key': role}
        assert client.get('/api/v1/sensor-sim/layouts/templates', headers=headers).status_code == 200
        assert client.get('/api/v1/sensor-sim/layouts', params={'project_id': project}, headers=headers).status_code == 404
        result = client.post('/api/v1/sensor-sim/layouts', json=draft(), headers=headers)
        assert result.status_code == (201 if role == 'Engineer' else 403)
    foreign = client.post('/api/v1/sensor-sim/layouts', json=draft(project_id=project, copied_from=_ref(layout)), headers={'Idempotency-Key': 'foreign'})
    assert foreign.status_code == 404
