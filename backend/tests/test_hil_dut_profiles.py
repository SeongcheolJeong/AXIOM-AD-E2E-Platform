"""DUT profile authoring/evidence contracts; authored handlers never run a DUT."""
import hashlib
import json

import pytest
from ade2e.hil_beta import WRITE
from ade2e.hil_beta_schemas import HilTestSave
from ade2e.hil_demo import ADAPTER
from ade2e.service import canonical_bytes, digest
from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow
from test_hil_beta import env, base_env, body, save, ref, preview, execute, attach, note


def profile_body(**changes):
    return dict(project_id='default', name='DUT A', note='Input age response study',
                parameters=dict(max_object_age_ms=250, recovery_fresh_steps=3), **changes)


def save_profile(env, value=None, key='profile-a'):
    response = env[1].post('/api/v1/hil-sim/dut-profiles', json=value or profile_body(), headers={'Idempotency-Key': key})
    assert response.status_code == 201, response.text
    return response.json()


def pair(env):
    a = save_profile(env)
    b = save_profile(env, {**profile_body(), 'name': 'DUT B',
        'parameters': dict(max_object_age_ms=750, recovery_fresh_steps=8)}, 'profile-b')
    return dict(baseline=a, faulted=b)


def profile_test(env, profiles=None, key='profile-test'):
    profiles = profiles or pair(env)
    return save(env, {**body(env), 'dut_profiles': {side: ref(value) for side, value in profiles.items()}}, key)


def test_profile_offline_revision_hash_idempotency_and_controls(env):
    handler = env[3]._handlers.pop(ADAPTER)
    first = save_profile(env)
    assert set(first) == {'schema_version', 'id', 'project_id', 'revision', 'name', 'note', 'parameters', 'created_at', 'author', 'sha256'}
    assert first['schema_version'] == 'hil-dut-profile/1' and first['revision'] == 1
    assert hashlib.sha256(canonical_bytes({key: value for key, value in first.items() if key != 'sha256'})).hexdigest() == first['sha256']
    assert save_profile(env) == first
    changed = {**profile_body(), 'id': first['id'], 'expected_revision': 1,
               'parameters': dict(max_object_age_ms=500, recovery_fresh_steps=5)}
    second = save_profile(env, changed, 'revise-profile')
    assert second['revision'] == 2 and second['sha256'] != first['sha256']
    assert env[1].get('/api/v1/hil-sim/dut-profiles/' + first['id'], params={'revision': 1}).json() == first
    assert env[1].get('/api/v1/hil-sim/dut-profiles/' + first['id']).json() == second
    assert env[1].post('/api/v1/hil-sim/dut-profiles', json=changed, headers={'Idempotency-Key': 'stale-revision'}).status_code == 409
    assert env[1].post('/api/v1/hil-sim/dut-profiles', json=changed, headers={'Idempotency-Key': 'profile-a'}).status_code == 409
    listed = env[1].get('/api/v1/hil-sim/dut-profiles').json()
    assert listed['items'] == [second]
    assert {item['key']: (item['min'], item['max'], item['default']) for item in listed['parameter_definitions']} == {
        'max_object_age_ms': (50, 1000, 250), 'recovery_fresh_steps': (1, 10, 3)}
    assert handler.probes == 0


@pytest.mark.parametrize('parameters', [
    dict(max_object_age_ms=True, recovery_fresh_steps=3), dict(max_object_age_ms=250., recovery_fresh_steps=3),
    dict(max_object_age_ms='250', recovery_fresh_steps=3), dict(max_object_age_ms=75, recovery_fresh_steps=3),
    dict(max_object_age_ms=1050, recovery_fresh_steps=3), dict(max_object_age_ms=250, recovery_fresh_steps=0),
    dict(max_object_age_ms=250, recovery_fresh_steps=11), dict(max_object_age_ms=250, recovery_fresh_steps=True),
    dict(max_object_age_ms=250, recovery_fresh_steps=3, other=1), {'max_object_age_ms': 250},
])
def test_profile_values_strict_and_offline(env, parameters):
    response = env[1].post('/api/v1/hil-sim/dut-profiles', json={**profile_body(), 'parameters': parameters},
                          headers={'Idempotency-Key': 'invalid'})
    assert response.status_code == 422, response.text
    assert env[2].probes == 0


def test_saved_profiles_remain_pinned_after_latest_profile_edit_and_wrong_attachment_fails(env):
    profiles = pair(env)
    handler = env[3]._handlers.pop(ADAPTER)
    test = profile_test(env, profiles)
    assert test['dut_profiles'] == profiles
    assert test['experiment']['input']['dut_profiles'] == profiles
    assert test['experiment']['comparison_kind'] == 'same_fault_dut_ab' and handler.probes == 0
    revised = save_profile(env, {**profile_body(), 'id': profiles['faulted']['id'], 'expected_revision': 1,
        'parameters': dict(max_object_age_ms=1000, recovery_fresh_steps=10)}, 'profile-r2')
    env[3]._handlers[ADAPTER] = handler
    document = preview(env, test).json()
    assert document['dut_profiles'] == profiles and document['experiment'] == test['experiment']
    assert env[1].get('/api/v1/hil-sim/tests/' + test['id']).json() == test
    job = execute(env, test)
    assert attach(env, test, job).status_code == 201
    other = profile_test(env, dict(baseline=profiles['baseline'], faulted=revised), 'changed-profile-test')
    assert attach(env, other, job, 'wrong-profile').status_code == 409
    observation = note(env, test, job)
    assert observation.status_code == 201, observation.text
    assert observation.json()['execution']['dut_profiles'] == profiles
    exported = env[1].get('/api/v1/hil-sim/tests/' + test['id'] + '/export').json()
    assert exported['test']['dut_profiles'] == profiles
    assert exported['executions'][0]['execution']['comparison_kind'] == 'same_fault_dut_ab'
    assert exported['executions'][0]['execution']['dut_profiles'] == profiles


def test_profile_scope_hash_and_roles(env):
    profiles = pair(env)
    other = env[1].post('/api/v1/projects', json={'name': 'Other profile project'}).json()['id']
    assert env[1].get('/api/v1/hil-sim/dut-profiles/' + profiles['baseline']['id'], params={'project_id': other}).status_code == 404
    foreign = save_profile(env, {**profile_body(), 'project_id': other}, 'foreign-profile')
    for wrong, status in [({**ref(profiles['baseline']), 'sha256': 'a' * 64}, 409), (ref(foreign), 404)]:
        response = env[1].post('/api/v1/hil-sim/tests', json={**body(env),
            'dut_profiles': dict(baseline=wrong, faulted=ref(profiles['faulted']))}, headers={'Idempotency-Key': 'invalid-profile'})
        assert response.status_code == status, response.text
    for role in ('Viewer', 'Reviewer', 'Engineer'):
        token = env[1].post('/api/v1/projects/default/tokens', json={'user_label': role, 'role': role}).json()['token']
        headers = {'Authorization': 'Bearer ' + token}
        assert env[1].get('/api/v1/hil-sim/dut-profiles', headers=headers).status_code == 200
        assert env[1].get('/api/v1/hil-sim/dut-profiles', params={'project_id': other}, headers=headers).status_code == 404
        response = env[1].post('/api/v1/hil-sim/dut-profiles', json=profile_body(), headers={**headers, 'Idempotency-Key': role})
        assert response.status_code == (201 if role == 'Engineer' else 403)
    assert env[2].probes == 0


def test_legacy_save_retry_hash_and_records_do_not_gain_profile_defaults(env):
    request = body(env)
    original = save(env, request)
    legacy_payload = HilTestSave.model_validate(request).model_dump(mode='json')
    legacy_payload.pop('dut_profiles')
    legacy_payload.pop('scene_profile')
    operation = 'write_' + digest(['default', 'save', 'save'])[:48]
    with env[0].read_session() as session:
        write = session.get(SimulationCatalogRevisionRow, ('default', WRITE, operation, 1))
        assert json.loads(write.manifest)['request_hash'] == digest(legacy_payload)
    assert save(env, request) == original
    assert save(env, {**request, 'dut_profiles': None}) == original
    assert 'dut_profiles' not in original and 'dut_profiles' not in original['experiment']['input']
    assert original['experiment']['comparison_kind'] == 'same_scene_fault_ab'


def test_profile_trace_echo_must_match_even_when_trace_and_outer_receipt_are_sealed(env, monkeypatch):
    profiles = pair(env)
    test = profile_test(env, profiles)
    original_execute = env[2].execute
    def wrong_trace(snapshot, directory, cancel):
        output = original_execute(snapshot, directory, cancel)
        for artifact in output.artifacts:
            if artifact.name == 'faulted-hil-trace.json':
                trace = json.loads(artifact.path.read_bytes())
                trace['dut_profile'] = profiles['baseline']
                raw = canonical_bytes(trace)
                artifact.path.write_bytes(raw)
                output.summary['sides']['faulted']['hil_trace_sha256'] = hashlib.sha256(raw).hexdigest()
        return output
    monkeypatch.setattr(env[2], 'execute', wrong_trace)
    job = execute(env, test)
    assert attach(env, test, job).status_code == 201
    assert note(env, test, job).status_code == 409


def test_profile_side_summary_echo_is_required_on_attachment(env, monkeypatch):
    profiles = pair(env)
    test = profile_test(env, profiles)
    original_execute = env[2].execute
    def wrong_summary(snapshot, directory, cancel):
        output = original_execute(snapshot, directory, cancel)
        output.summary['sides']['faulted']['dut_profile'] = profiles['baseline']
        return output
    monkeypatch.setattr(env[2], 'execute', wrong_summary)
    job = execute(env, test)
    assert attach(env, test, job).status_code == 409
