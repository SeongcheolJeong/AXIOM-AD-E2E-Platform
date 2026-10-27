"""Fixed scene authoring and sealed evidence checks; no native engine executes."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from ade2e import hil_scene_profiles as scenes
from ade2e.hil_demo import ADAPTER
from ade2e.service import canonical_bytes
from test_hil_batches import env, hil_env, base_env, batch, submit, work, get, advance
from test_hil_beta import body, save, ref, preview, execute, attach, note
from test_hil_demo import preview as preset_preview
from test_hil_dut_profiles import pair


def scene(index=0):
    return scenes.list_scene_profiles()[index]


def test_fixed_catalog_is_standalone_canonical_and_returns_independent_copies():
    spec = importlib.util.spec_from_file_location('standalone_hil_scene_profiles', Path(scenes.__file__))
    standalone = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(standalone)
    catalog = standalone.list_scene_profiles()
    assert [item['id'] for item in catalog] == ['lead_deceleration', 'lead_stop_go']
    assert catalog[0]['sha256'] == '4926394ca04838bb36f5303655725f1cd99c87ef723cd2c62465d4838fa27dc2'
    assert catalog[1]['sha256'] == 'a061779a405b1a659f2f2d0eaec68d0b6fa969f9a848388f280e2dadf0b99c75'
    for item in catalog:
        assert item['clock'] == 'simulation_input' and type(item['duration_s']) is int
        assert hashlib.sha256(canonical_bytes({key: value for key, value in item.items() if key != 'sha256'})).hexdigest() == item['sha256']
        assert standalone.resolve_scene_profile(ref(item)) == standalone.validate_scene_profile(item) == item
        assert all(type(step['at_s']) is int for step in item['lead_speed_schedule'])
    catalog[0]['lead_speed_schedule'][0]['target_speed_mps'] = 999
    resolved = standalone.resolve_scene_profile(ref(catalog[1]))
    resolved['title'] = 'Changed caller data'
    assert standalone.list_scene_profiles() == scenes.list_scene_profiles()


@pytest.mark.parametrize('change', [
    {'schema_version': 'hil-scene-profile/2'}, {'revision': True}, {'revision': 1.0},
    {'id': 'other'}, {'sha256': 'a' * 64}, {'duration_s': 18.0}, {'clock': 'response'},
    {'extra': None}, {'title': 'Changed'}, {'lead_speed_schedule': [{'at_s': 0, 'target_speed_mps': 3}]},
    {'lead_speed_schedule': [{'at_s': 0., 'target_speed_mps': 2}, {'at_s': 4, 'target_speed_mps': .5}]},
])
def test_frozen_scene_rejects_unknown_or_modified_contract(change):
    with pytest.raises(ValueError):
        scenes.validate_scene_profile({**scene(), **change})


def test_catalog_and_save_are_offline_and_legacy_null_is_omitted(env):
    handler = env[3]._handlers.pop(ADAPTER)
    response = env[1].get('/api/v1/hil-sim/scenes')
    assert response.status_code == 200
    assert response.json() == dict(schema_version='hil-sim-scenes/1', project_id='default',
        default_scene_id='lead_stop', source_scope='existing_preset_bound_base_scene', items=scenes.list_scene_profiles())
    original = save(env)
    assert save(env, {**body(env), 'scene_profile': None}) == original
    assert 'scene_profile' not in original and 'scene_profile' not in original['experiment']['input']
    selected = save(env, {**body(env), 'scene_profile': ref(scene())}, 'scene-offline')
    assert selected['scene_profile'] == selected['experiment']['input']['scene_profile'] == scene()
    assert selected['source'] == original['source'] and handler.probes == 0


@pytest.mark.parametrize('reference', [
    {'id': 'lead_stop', 'revision': 1, 'sha256': 'a' * 64},
    {**ref(scene()), 'sha256': 'a' * 64}, {**ref(scene()), 'revision': 2},
    {**ref(scene()), 'revision': True}, {**ref(scene()), 'revision': 1.0},
    {**ref(scene()), 'schedule': []}, scene(),
])
def test_unsupported_scene_save_and_preset_preview_do_not_probe(env, reference):
    response = env[1].post('/api/v1/hil-sim/tests', json={**body(env), 'scene_profile': reference},
                          headers={'Idempotency-Key': 'bad-scene'})
    assert response.status_code == 422, response.text
    assert preset_preview(env, scene_profile=reference).status_code == 422
    assert env[2].probes == 0


def test_scene_revision_duplicate_timing_profiles_and_preview_remain_exact(env):
    profiles = pair(env)
    draft = {**body(env), 'parameters': {'delay_ms': 400, 'start_s': 2, 'duration_s': 3},
        'dut_profiles': {side: ref(value) for side, value in profiles.items()}, 'scene_profile': ref(scene())}
    first = save(env, draft)
    before = preview(env, first).json()
    second = save(env, {**draft, 'id': first['id'], 'expected_revision': 1,
        'scene_profile': ref(scene(1))}, 'other-scene')
    assert second['scene_profile'] == scene(1) and second['dut_profiles'] == profiles
    assert second['parameters'] == first['parameters'] and second['source'] == first['source']
    assert env[1].get('/api/v1/hil-sim/tests/' + first['id'], params={'revision': 1}).json() == first
    assert preview(env, first).json() == before
    assert before['scene_profile'] == before['experiment']['input']['scene_profile'] == scene()
    assert preview(env, second).json()['preview']['preview_hash'] != before['preview']['preview_hash']
    duplicate = save(env, {**draft, 'copied_from': ref(first), 'name': 'Copied scene'}, 'copy-scene')
    assert duplicate['scene_profile'] == first['scene_profile'] and duplicate['source'] == first['source']
    legacy = save(env, {**draft, 'id': first['id'], 'expected_revision': 2, 'scene_profile': None}, 'remove-scene')
    assert 'scene_profile' not in legacy and 'scene_profile' not in legacy['experiment']['input']
    assert legacy['dut_profiles'] == profiles and legacy['parameters'] == first['parameters']
    preset = preset_preview(env, scene_profile=ref(scene(1))).json()
    assert preset['scene_profile'] == preset['experiment']['input']['scene_profile'] == scene(1)


def test_scene_preview_fails_closed_when_handler_drops_or_adds_overlay(env, monkeypatch):
    selected = save(env, {**body(env), 'scene_profile': ref(scene())})
    legacy = save(env, key='legacy')
    original_prepare = env[2].prepare
    def wrong_prepare(spec):
        snapshot = original_prepare(spec)
        if 'scene_profile' in spec.input:
            snapshot.pop('scene_profile')
        else:
            snapshot['scene_profile'] = scene()
        return snapshot
    monkeypatch.setattr(env[2], 'prepare', wrong_prepare)
    assert preview(env, selected).status_code == 409
    assert preview(env, legacy).status_code == 409
    assert preset_preview(env, scene_profile=ref(scene())).status_code == 409
    assert preset_preview(env).status_code == 409
    group = batch(env, [selected, legacy, save(env, {**body(env), 'scene_profile': ref(scene(1))}, 'third')])
    response = env[1].post('/api/v1/hil-sim/batches/' + group['id'] + '/preview', json={'expected_revision': 1})
    assert response.status_code == 409


def test_scene_execution_attachment_observation_and_export_preserve_identity(env):
    test = save(env, {**body(env), 'scene_profile': ref(scene())})
    other = save(env, {**body(env), 'scene_profile': ref(scene(1))}, 'other')
    job = execute(env, test)
    assert attach(env, other, job, 'wrong-scene').status_code == 409
    response = attach(env, test, job)
    assert response.status_code == 201, response.text
    assert response.json()['execution']['scene_profile'] == scene()
    observation = note(env, test, job)
    assert observation.status_code == 201, observation.text
    assert observation.json()['execution']['scene_profile'] == scene()
    exported = env[1].get('/api/v1/hil-sim/tests/' + test['id'] + '/export').json()
    assert exported['test']['scene_profile'] == exported['executions'][0]['execution']['scene_profile'] == scene()
    assert exported['observations'][0]['execution']['scene_profile'] == scene()
    assert exported['test']['source'] == test['source']


@pytest.mark.parametrize('location', ['top', 'side', 'trace'])
def test_scene_mismatch_is_rejected_even_with_authenticated_artifact_and_receipt(env, monkeypatch, location):
    test = save(env, {**body(env), 'scene_profile': ref(scene())})
    original_execute = env[2].execute
    def wrong_output(snapshot, directory, cancel):
        output = original_execute(snapshot, directory, cancel)
        if location == 'top':
            output.summary['scene_profile'] = scene(1)
        elif location == 'side':
            output.summary['sides']['faulted']['scene_profile'] = scene(1)
        else:
            artifact = next(item for item in output.artifacts if item.name == 'faulted-hil-trace.json')
            trace = json.loads(artifact.path.read_bytes())
            trace['scene_profile'] = scene(1)
            raw = canonical_bytes(trace)
            artifact.path.write_bytes(raw)
            output.summary['sides']['faulted']['hil_trace_sha256'] = hashlib.sha256(raw).hexdigest()
        return output
    monkeypatch.setattr(env[2], 'execute', wrong_output)
    job = execute(env, test)
    if location == 'trace':
        assert attach(env, test, job).status_code == 201
        assert note(env, test, job).status_code == 409
        from ade2e import hil_beta, hil_batches
        with env[0].read_session() as session:
            execution, _ = hil_beta._execution(session, 'default', job, test)
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as caught:
                hil_batches._trace_summary(env[0], session, execution)
            assert caught.value.status_code == 409
    else:
        assert attach(env, test, job).status_code == 409


def test_mixed_legacy_and_scene_batch_uses_exact_frozen_tests_and_exports(env):
    profiles = pair(env)
    tests = [save(env, key='legacy')]
    for index, descriptor in enumerate(scenes.list_scene_profiles()):
        tests.append(save(env, {**body(env), 'scene_profile': ref(descriptor),
            'dut_profiles': {side: ref(value) for side, value in profiles.items()}}, 'scene-' + str(index)))
    value, _ = submit(env, batch(env, tests))
    for _ in tests:
        assert work(env).status == 'completed'
    advance(env[0], env[3])
    complete = get(env, value)
    assert complete['status'] == 'completed'
    assert [item['test'] for item in complete['items']] == tests
    response = env[1].get('/api/v1/hil-sim/batches/' + value['id'] + '/export')
    assert response.status_code == 200, response.text
    exports = response.json()['tests']
    assert 'scene_profile' not in exports[0]['test']
    assert 'scene_profile' not in exports[0]['executions'][0]['execution']
    for exported, expected in zip(exports[1:], scenes.list_scene_profiles()):
        assert exported['test']['scene_profile'] == exported['executions'][0]['execution']['scene_profile'] == expected
        assert exported['test']['dut_profiles'] == profiles
    assert '/private/operator' not in response.text
