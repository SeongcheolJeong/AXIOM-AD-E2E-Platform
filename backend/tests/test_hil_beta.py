"""Offline beta authoring and evidence contracts; no CARLA or hardware runs."""
import copy
import hashlib
import json
import pytest
from fastapi import APIRouter, Depends, Request
from ade2e.adapters import Registry
from ade2e.database import Settings
from ade2e.experiment_models import ExperimentArtifactRow
from ade2e.experiment_runtime import ExperimentExecution, ExperimentOutputFile
from ade2e.hil_beta import TEST, register_hil_beta_routes
from ade2e.hil_demo import ADAPTER, FIDELITY
from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow
from ade2e.team_access import authenticate_request
from ade2e.worker import run_once
from test_hil_demo import bind, env as base_env


@pytest.fixture
def env(base_env, tmp_path, monkeypatch):
    db, client, handler, registry, _ = base_env
    settings = Settings(data_dir=tmp_path, api_token='test-bootstrap', team_mode=True, seed_examples=False)
    def authenticate(request: Request):
        header = request.headers.get('authorization', '')
        authenticate_request(request, db, settings, header[7:] if header.startswith('Bearer ') else None)
    router = APIRouter(prefix='/api/v1', dependencies=[Depends(authenticate)])
    register_hil_beta_routes(router, db, registry)
    client.app.include_router(router)
    original_prepare = handler.prepare
    def prepare(spec):
        return {**original_prepare(spec), 'private_transport': '/private/operator/runtime/connection',
                **({'dut_profiles': copy.deepcopy(spec.input['dut_profiles'])} if 'dut_profiles' in spec.input else {}),
                **({'scene_profile': copy.deepcopy(spec.input['scene_profile'])} if 'scene_profile' in spec.input else {})}
    monkeypatch.setattr(handler, 'prepare', prepare)
    def execute(snapshot, work_dir, should_cancel):
        if handler.fail:
            raise RuntimeError('Authored infrastructure failure; no simulator was executed')
        artifacts, sides = [], {}
        for side in ('baseline', 'faulted'):
            trace = dict(schema_version='hil-demo-trace/1', side=side, execution_target='software_dut',
                frames=[dict(sequence=i, response_frame=i + 100, time_s=t, input_time_s=i * .05,
                             input_age_ms=300 if side == 'faulted' else 0, dut_state='degraded' if side == 'faulted' else 'tracking',
                             ego_speed_mps=2., lead_gap_m=12., command=dict(throttle=0., brake=1. if side == 'faulted' else 0., steer=0.))
                        for i, t in enumerate(getattr(handler, 'trace_times', (.05, .1, .15)))])
            if 'dut_profiles' in snapshot:
                trace.update(comparison_kind=snapshot['comparison_kind'], dut_profile=snapshot['dut_profiles'][side], fault=snapshot['fault'])
            if 'scene_profile' in snapshot:
                trace['scene_profile'] = snapshot['scene_profile']
            path = work_dir / (side + '-hil-trace.json')
            raw = json.dumps(trace).encode()
            path.write_bytes(raw)
            artifacts.append(ExperimentOutputFile(path, path.name, 'application/json'))
            sides[side] = dict(hil_trace_artifact=path.name, hil_trace_sha256=hashlib.sha256(raw).hexdigest())
            if 'dut_profiles' in snapshot:
                sides[side]['dut_profile'] = snapshot['dut_profiles'][side]
            if 'scene_profile' in snapshot:
                sides[side]['scene_profile'] = snapshot['scene_profile']
        return ExperimentExecution(summary=dict(schema_version='hil-demo-result/1', execution_target='software_dut',
            fault=snapshot['fault'], sides=sides,
            **({'comparison_kind': snapshot['comparison_kind'], 'dut_profiles': snapshot['dut_profiles']} if 'dut_profiles' in snapshot else {}),
            **({'scene_profile': snapshot['scene_profile']} if 'scene_profile' in snapshot else {})),
            artifacts=artifacts, fidelity=FIDELITY)
    monkeypatch.setattr(handler, 'execute', execute)
    assert bind(base_env).status_code == 200
    yield base_env


def body(env, **changes):
    preset = env[1].get('/api/v1/hil-sim/presets').json()['items'][0]
    return dict(project_id='default', name='Input age investigation', note='Compare braking response',
        preset_id='input_delay', binding_revision=preset['binding_revision'], binding_sha256=preset['binding_sha256'],
        parameters={'delay_ms': 300}, **changes)


def save(env, value=None, key='save'):
    response = env[1].post('/api/v1/hil-sim/tests', json=value or body(env), headers={'Idempotency-Key': key})
    assert response.status_code == 201, response.text
    return response.json()


def ref(value):
    return {key: value[key] for key in ('id', 'revision', 'sha256')}


def action(test):
    return dict(project_id='default', test_ref=ref(test))


def preview(env, test):
    return env[1].post('/api/v1/hil-sim/tests/' + test['id'] + '/preview', json=action(test))


def execute(env, test, *, complete=True, key='run'):
    document = preview(env, test).json()
    created = env[1].post('/api/v1/experiments', json=document['experiment'], headers={'Idempotency-Key': key})
    assert created.status_code == 201, created.text
    submitted = env[1].post('/api/v1/experiments/' + created.json()['id'] + '/executions',
        json={'preview_hash': document['preview']['preview_hash']}, headers={'Idempotency-Key': key})
    assert submitted.status_code == 202, submitted.text
    identity = submitted.json()['id']
    if complete:
        run_once(env[0], Registry(), 'hil-beta-authored-fixture', job_id=identity, experiment_registry=env[3])
    return identity


def attach(env, test, execution, key='attach'):
    return env[1].post('/api/v1/hil-sim/tests/' + test['id'] + '/executions',
        json={**action(test), 'execution_id': execution}, headers={'Idempotency-Key': key})


def note(env, test, execution, key='note', **changes):
    return env[1].post('/api/v1/hil-sim/observations', json={**action(test), 'execution_id': execution,
        'title': 'Input was old', 'note': 'User observation of the response', 'side': 'both', 'time_s': .12, **changes},
        headers={'Idempotency-Key': key})


def test_offline_revision_save_reopen_duplicate_and_conflict(env):
    draft = body(env)
    handler = env[3]._handlers.pop(ADAPTER)
    first = save(env, draft)
    assert first['schema_version'] == 'hil-sim-test/1' and first['revision'] == 1
    assert first['source']['seed'] == 42 and handler.probes == 0
    assert save(env, draft) == first
    assert preview(env, first).status_code == 422
    revised = save(env, {**draft, 'id': first['id'], 'expected_revision': 1, 'parameters': {'delay_ms': 400}}, 'revise')
    assert revised['revision'] == 2 and revised['parameters']['delay_ms'] == 400
    assert env[1].get('/api/v1/hil-sim/tests/' + first['id'], params={'revision': 1}).json() == first
    assert env[1].get('/api/v1/hil-sim/tests/' + first['id']).json() == revised
    assert env[1].post('/api/v1/hil-sim/tests', json={**draft, 'id': first['id'], 'expected_revision': 1},
        headers={'Idempotency-Key': 'stale'}).status_code == 409
    assert env[1].post('/api/v1/hil-sim/tests', json={**draft, 'name': 'Different name'},
        headers={'Idempotency-Key': 'save'}).status_code == 409
    duplicate = save(env, {**draft, 'name': 'Copied input test', 'copied_from': ref(first)}, 'copy')
    assert duplicate['id'] != first['id'] and duplicate['copied_from'] == ref(first)
    assert duplicate['source'] == first['source'] and duplicate['revision'] == 1
    page = env[1].get('/api/v1/hil-sim/tests', params={'limit': 1}).json()
    assert len(page['items']) == 1 and page['next_offset'] == 1
    assert len(env[1].get('/api/v1/hil-sim/tests', params={'offset': 1}).json()['items']) == 1


def test_historical_binding_preview_survives_current_rebind_and_rejects_bad_pin(env):
    original = body(env)
    first = save(env, original)
    assert bind(env, expected_revision=1, seed=43).status_code == 200
    assert save(env, {**original, 'name': 'Historical copy'}, 'historical')['source']['seed'] == 42
    response = preview(env, first)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['schema_version'] == 'hil-sim-test-preview/1' and value['test_ref'] == ref(first)
    assert value['experiment']['seed'] == 42
    assert env[1].post('/api/v1/hil-sim/tests/' + first['id'] + '/preview',
        json={**action(first), 'test_ref': {**ref(first), 'sha256': 'b' * 64}}).status_code == 409
    assert env[1].post('/api/v1/hil-sim/tests', json={**original, 'binding_sha256': 'a' * 64},
        headers={'Idempotency-Key': 'wrong-binding'}).status_code == 409


def test_execution_association_exact_spec_and_idempotent_history(env):
    test = save(env)
    job = execute(env, test, complete=False)
    first = attach(env, test, job)
    assert first.status_code == 201, first.text
    assert first.json()['execution']['status'] == 'queued'
    assert attach(env, test, job).json() == first.json()
    assert attach(env, test, job, 'same-link-new-key').json()['id'] == first.json()['id']
    history = env[1].get('/api/v1/hil-sim/tests/' + test['id'] + '/executions').json()
    assert len(history['items']) == 1 and history['items'][0]['test_ref'] == ref(test)
    changed = save(env, {**body(env), 'parameters': {'delay_ms': 400}}, 'different')
    assert attach(env, changed, job, 'wrong-input').status_code == 409
    assert env[1].get('/api/v1/hil-sim/tests/' + test['id'] + '/executions', params={'revision': 8}).status_code == 404
    assert note(env, test, job).status_code == 422


def test_timing_save_is_offline_historical_revision_is_exact_and_wrong_window_cannot_attach(env):
    original = save(env)
    original_preview = preview(env, original).json()
    job = execute(env, original, complete=False)
    handler = env[3]._handlers.pop(ADAPTER)
    probes = handler.probes
    revised = save(env, {**body(env), 'id': original['id'], 'expected_revision': 1,
        'parameters': {'delay_ms': 300.0, 'start_s': 5.5, 'duration_s': 2.0}}, 'timing-edit')
    assert handler.probes == probes
    assert revised['parameters'] == {'delay_ms': 300, 'start_s': 5.5, 'duration_s': 2}
    assert type(revised['parameters']['duration_s']) is int
    assert revised['experiment']['input']['fault'] == dict(kind='delay', start_s=5.5, duration_s=2, delay_ms=300)
    assert revised['revision'] == 2 and revised['sha256'] != original['sha256']
    assert original['parameters'] == {'delay_ms': 300}
    assert env[1].get('/api/v1/hil-sim/tests/' + original['id'], params={'revision': 1}).json() == original
    env[3]._handlers[ADAPTER] = handler
    old_preview = preview(env, original).json()
    assert old_preview == original_preview
    new_preview = preview(env, revised).json()
    assert new_preview['experiment'] == revised['experiment']
    assert new_preview['preview']['preview_hash'] != old_preview['preview']['preview_hash']
    assert {item['key']: item['value'] for item in new_preview['changes']} == revised['parameters']
    assert attach(env, revised, job, 'wrong-timing-window').status_code == 409
    assert attach(env, original, job, 'original-window').status_code == 201


@pytest.mark.parametrize('parameters', [{'start_s': 14}, {'duration_s': 0}, {'start_s': 2.2},
                                      {'start_s': True}, {'duration_s': '2'}, {'unexpected': 1},
                                      {'start_s': 16.00000001, 'duration_s': .5},
                                      {'duration_s': 1.0000000001}])
def test_invalid_timing_save_does_not_probe_native(env, parameters):
    response = env[1].post('/api/v1/hil-sim/tests', json={**body(env), 'parameters': {'delay_ms': 300, **parameters}},
        headers={'Idempotency-Key': 'invalid-timing'})
    assert response.status_code == 422, response.text
    assert env[2].probes == 0


def test_observations_preserve_response_sample_and_export_sanitized_source(env):
    test = save(env)
    job = execute(env, test)
    assert note(env, test, job).status_code == 409
    assert attach(env, test, job).status_code == 201
    response = note(env, test, job)
    assert response.status_code == 201, response.text
    value = response.json()
    assert value['time_s'] == .12 and value['samples']['faulted']['time_s'] == .1
    assert value['samples']['faulted']['input_time_s'] == .05 and value['samples']['faulted']['response_frame'] == 101
    assert value['samples']['faulted']['input_age_ms'] == 300 and value['samples']['faulted']['command']['brake'] == 1
    assert value['execution']['manifest_hash'] and value['sha256']
    assert value['verification_status'] == 'not_a_test_verdict' and value['assessment_source'] == 'user_observation'
    assert note(env, test, job).json() == value
    assert note(env, test, job, title='Changed').status_code == 409
    early = note(env, test, job, key='early', time_s=0, side='baseline').json()
    assert early['samples']['baseline']['reason'] == 'before_first_retained_response'
    assert 'faulted' not in early['samples']
    listed = env[1].get('/api/v1/hil-sim/observations', params={'test_id': test['id'], 'revision': 1}).json()
    assert len(listed['items']) == 2
    exported = env[1].get('/api/v1/hil-sim/tests/' + test['id'] + '/export', params={'revision': 1})
    assert exported.status_code == 200
    assert hashlib.sha256(exported.content).hexdigest() == exported.headers['X-Content-SHA256']
    assert exported.json()['test'] == test and len(exported.json()['observations']) == 2
    assert exported.json()['executions'][0]['execution']['manifest_hash'] == value['execution']['manifest_hash']
    assert 'private_transport' not in exported.text and '/private/operator' not in exported.text
    assert 'prepared' not in exported.json()['executions'][0]['execution']


def test_failed_execution_note_has_no_fabricated_samples(env):
    test = save(env)
    env[2].fail = True
    job = execute(env, test)
    assert attach(env, test, job).status_code == 201
    response = note(env, test, job, kind='issue', time_s=0)
    assert response.status_code == 201, response.text
    value = response.json()
    assert value['execution']['status'] == 'failed' and value['execution']['outcome_uncertain']
    assert all(sample['status'] == 'unavailable' for sample in value['samples'].values())
    assert all('response_frame' not in sample for sample in value['samples'].values())


def test_decimal_observation_time_matches_retained_tick_without_selecting_future_step(env):
    env[2].trace_times = (3.5, 3.5500000000000003, 3.6)
    test = save(env)
    job = execute(env, test)
    assert attach(env, test, job).status_code == 201
    before = note(env, test, job, key='before-tick', time_s=3.549)
    assert before.status_code == 201, before.text
    prior = before.json()
    assert prior['samples']['faulted']['time_s'] == 3.5
    exact = note(env, test, job, key='decimal-tick', time_s=3.55)
    assert exact.status_code == 201, exact.text
    assert exact.json()['samples']['faulted']['time_s'] == 3.5500000000000003
    assert exact.json()['samples']['faulted']['response_frame'] == 101
    # More than the tiny absolute tolerance must not inherit a relative window.
    earlier = note(env, test, job, key='outside-float-tolerance', time_s=3.55 - 2e-9)
    assert earlier.status_code == 201, earlier.text
    assert earlier.json()['samples']['faulted']['time_s'] == 3.5
    assert note(env, test, job, key='before-tick', time_s=3.549).json() == prior
    saved = env[1].get('/api/v1/hil-sim/observations', params={'execution_id': job}).json()['items']
    assert next(item for item in saved if item['id'] == prior['id']) == prior


@pytest.mark.parametrize('changes', [{'time_s': -1}, {'time_s': 18.1}, {'time_s': True},
    {'note': 'Bearer confidential_token_value'}, {'title': '   '}, {'side': 'unrecognized'}])
def test_invalid_observation_rejected_without_execution_probe(env, changes):
    test = save(env)
    assert note(env, test, 'job_missing', **changes).status_code == 422
    assert env[2].probes == 0


def test_tampered_revision_and_trace_fail_closed(env):
    test = save(env)
    job = execute(env, test)
    attach(env, test, job)
    with env[0].read_session() as session:
        artifact = session.scalar(__import__('sqlalchemy').select(ExperimentArtifactRow).where(
            ExperimentArtifactRow.job_id == job, ExperimentArtifactRow.name == 'faulted-hil-trace.json'))
        path = env[0].experiment_dir / 'artifacts' / artifact.storage_key
    path.write_bytes(b'corrupt')
    assert note(env, test, job).status_code == 409
    with env[0].session() as session:
        row = session.get(SimulationCatalogRevisionRow, ('default', TEST, test['id'], 1))
        row.manifest += b' '
        session.commit()
    assert env[1].get('/api/v1/hil-sim/tests/' + test['id']).status_code == 409


def test_project_acl_and_openapi_agent_surface(env):
    test = save(env)
    job = execute(env, test)
    attach(env, test, job)
    other = env[1].post('/api/v1/projects', json={'name': 'Other project'}).json()['id']
    for endpoint in ['/hil-sim/tests/' + test['id'], '/hil-sim/tests/' + test['id'] + '/executions',
                     '/hil-sim/tests/' + test['id'] + '/export']:
        assert env[1].get('/api/v1' + endpoint, params={'project_id': other}).status_code == 404
    assert env[1].get('/api/v1/hil-sim/observations', params={'project_id': other, 'execution_id': job}).status_code == 404
    for role in ('Viewer', 'Reviewer', 'Engineer'):
        token = env[1].post('/api/v1/projects/default/tokens', json={'user_label': role, 'role': role}).json()['token']
        headers = {'Authorization': 'Bearer ' + token}
        assert env[1].get('/api/v1/hil-sim/tests/' + test['id'], headers=headers).status_code == 200
        response = env[1].post('/api/v1/hil-sim/tests', json=body(env), headers={**headers, 'Idempotency-Key': role})
        assert response.status_code == (201 if role == 'Engineer' else 403)
        tools = env[1].get('/api/v1/agent/tools', headers=headers).json()['tools']
        relevant = [tool for tool in tools if tool['path'].startswith('/api/v1/hil-sim/tests')]
        assert any(tool['method'] == 'POST' for tool in relevant) == (role == 'Engineer')
    assert env[1].get('/api/v1/hil-sim/tests', headers={'Authorization': ''}).status_code == 401
