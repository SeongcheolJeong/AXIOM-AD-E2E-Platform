"""Recipe provenance and API/queue boundaries; no real sensor or engine runs."""
import hashlib
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from ade2e import service
from ade2e.job_models import JobRow
from ade2e.simulation_context import (
    BASELINE_SENSOR_PROFILE, DENSE_SENSOR_PROFILE, NEAR_SENSOR_PROFILE, PreparedSimulationContext,
    sensor_profile_for_seed, validate_sensor_profile_adapter,
)
from test_simulation_context import context, submit


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def test_baseline_recipe_preserves_historical_seed42_hash():
    # Literal from the independently retained 0.5 native baseline archive.
    assert sensor_profile_for_seed(42).profile_hash == '8acf977bad61ec1231dd50c12192a97f8e2b8b3dc07486c888aa0445b99a2d22'


def test_dense_changes_only_identity_sampling_and_content_hash():
    old = sensor_profile_for_seed(42).model_dump(mode='json')
    new = sensor_profile_for_seed(42, DENSE_SENSOR_PROFILE).model_dump(mode='json')
    assert new['attributes']['points_per_second'] == '128000'
    assert new['profile_hash'] != old['profile_hash']
    new.update(id=old['id'], profile_hash=old['profile_hash'])
    new['attributes']['points_per_second'] = old['attributes']['points_per_second']
    assert new == old
    assert sensor_profile_for_seed(43, DENSE_SENSOR_PROFILE).profile_hash != sensor_profile_for_seed(42, DENSE_SENSOR_PROFILE).profile_hash


def test_catalog_is_seedless_discoverable_and_does_not_create_jobs(client):
    catalog = client.get('/api/v1/simulation-sensor-profiles').json()
    assert [p['id'] for p in catalog] == [BASELINE_SENSOR_PROFILE, DENSE_SENSOR_PROFILE, NEAR_SENSOR_PROFILE]
    assert [p['nominal_points_per_frame'] for p in catalog] == [1600, 6400, 12800]
    assert all('noise_seed' not in p['attributes'] and p['seed_policy'] == 'effective_scenario_seed' for p in catalog)
    assert catalog[1]['supported_adapter_versions'] == ['0.6.0', '0.7.0', '0.8.0']
    assert catalog[2]['supported_adapter_versions'] == ['0.7.0', '0.8.0']
    assert '/api/v1/simulation-sensor-profiles' in client.get('/openapi.json').json()['paths']
    assert '/api/v1/simulation-sensor-profiles' in client.get('/api/v1/agent/tools').text
    with client.app.state.db.session() as session:
        assert not list(session.scalars(select(JobRow)))


@pytest.mark.parametrize('version', ['0.4.1', '0.5.0', '0.6.1', '9.0.0'])
def test_dense_rejects_unsupported_contract_before_queue_commit(context, monkeypatch, version):
    adapter = context[4]
    metadata = adapter.metadata().model_copy(update={'version': version})
    monkeypatch.setattr(adapter, 'metadata', lambda: metadata)
    body = context[3].model_copy(update={'control': context[3].control.model_copy(update={'sensor_profile_id': DENSE_SENSOR_PROFILE})})
    with pytest.raises(HTTPException) as error:
        submit(context, body=body)
    assert error.value.status_code == 422
    assert '0.6.0' in error.value.detail
    with context[1].session() as session:
        assert not list(session.scalars(select(JobRow)))
    assert adapter.preflights == adapter.requests == []


def test_profile_selection_is_pinned_in_prepared_input_and_idempotency(context, monkeypatch):
    adapter = context[4]
    metadata = adapter.metadata().model_copy(update={'version': '0.6.0'})
    monkeypatch.setattr(adapter, 'metadata', lambda: metadata)
    baseline = submit(context, key='baseline')
    body = context[3].model_copy(update={'seed': 987, 'control': context[3].control.model_copy(update={'sensor_profile_id': DENSE_SENSOR_PROFILE})})
    dense = submit(context, key='dense', body=body)
    assert dense.execution_context.sensor_profile == sensor_profile_for_seed(987, DENSE_SENSOR_PROFILE)
    assert dense.input_hash != baseline.input_hash
    assert dense.execution_context.context_hash != baseline.execution_context.context_hash
    assert submit(context, key='dense', body=body).id == dense.id
    with pytest.raises(HTTPException) as error:
        submit(context, key='dense')
    assert error.value.status_code == 409
    with context[1].session() as session:
        stored = session.scalar(select(JobRow).where(JobRow.id == dense.id))
        assert service.PreparedRun.from_payload(stored.prepared).input_hash == dense.input_hash
    # Rehashing a forged dense recipe cannot hide changes to dropout or limits.
    value = dense.execution_context.model_dump(mode='json')
    value['sensor_profile']['attributes']['dropoff_general_rate'] = '0.0'
    profile = value['sensor_profile']
    profile['profile_hash'] = hashlib.sha256(canonical({k: v for k, v in profile.items() if k != 'profile_hash'})).hexdigest()
    value['context_hash'] = hashlib.sha256(canonical({k: v for k, v in value.items() if k != 'context_hash'})).hexdigest()
    with pytest.raises(ValueError, match='complete supported recipe'):
        PreparedSimulationContext.model_validate(value)


def test_unknown_profile_is_not_coerced_to_baseline():
    with pytest.raises(ValueError):
        sensor_profile_for_seed(42, 'carla-lidar-ego/2')
    with pytest.raises(ValueError):
        validate_sensor_profile_adapter('carla-lidar-ego/2', '0.6.0')


def test_near_recipe_is_a_distinct_explicit_geometry_without_relaxing_bounds():
    baseline = sensor_profile_for_seed(42).model_dump(mode='json')
    near = sensor_profile_for_seed(42, NEAR_SENSOR_PROFILE).model_dump(mode='json')
    assert near['profile_hash'] != baseline['profile_hash']
    assert near['attributes'] == baseline['attributes'] | {
        'channels': '64', 'points_per_second': '256000', 'lower_fov': '-65.0'}
    assert near['mount'] == baseline['mount']
    assert near['max_points'] == baseline['max_points'] == 20000
    near.update(id=baseline['id'], profile_hash=baseline['profile_hash'], attributes=baseline['attributes'])
    assert near == baseline


@pytest.mark.parametrize('version', ['0.4.1', '0.5.0', '0.6.0', '0.6.1', '0.7.1', '9.0.0'])
def test_near_recipe_requires_exact_native_contract_before_queue(context, monkeypatch, version):
    adapter = context[4]
    metadata = adapter.metadata().model_copy(update={'version': version})
    monkeypatch.setattr(adapter, 'metadata', lambda: metadata)
    body = context[3].model_copy(update={'control': context[3].control.model_copy(update={'sensor_profile_id': NEAR_SENSOR_PROFILE})})
    with pytest.raises(HTTPException) as error:
        submit(context, body=body)
    assert error.value.status_code == 422 and '0.7.0' in error.value.detail
    assert adapter.preflights == adapter.requests == []
    with context[1].session() as session:
        assert not list(session.scalars(select(JobRow)))


@pytest.mark.parametrize('profile_id', [BASELINE_SENSOR_PROFILE, DENSE_SENSOR_PROFILE, NEAR_SENSOR_PROFILE])
@pytest.mark.parametrize('version', ['0.5.0', '0.6.0', '0.7.0'])
def test_explicit_kernel2_pins_queue_and_rejects_older_native_adapters(context, monkeypatch, profile_id, version):
    client, db, _, body, adapter, *_ = context
    catalog = client.get('/api/v1/stack-components').json()
    selections = {item['role']: {'id': item['id'], 'version': item['version'], 'parameters': item['default_parameters']}
                  for item in catalog if item['version'] == '2'}
    response = client.post('/api/v1/stacks', json={'name': 'Explicit native v2 fixture', 'note': 'Authored API version gate, no simulator execution',
        'definition': {'schema_version': '2', 'kernel_version': 'stack-kernel/2', **selections}})
    assert response.status_code == 201, response.text
    stack = response.json()
    metadata = adapter.metadata().model_copy(update={'version': version})
    monkeypatch.setattr(adapter, 'metadata', lambda: metadata)
    body = body.model_copy(update={'control': body.control.model_copy(update={
        'stack_id': stack['id'], 'stack_revision': 1, 'sensor_profile_id': profile_id})})
    if version != '0.7.0':
        with pytest.raises(HTTPException) as error:
            submit(context, body=body)
        assert error.value.status_code == 422
        assert adapter.preflights == adapter.requests == []
        with db.session() as session:
            assert not list(session.scalars(select(JobRow)))
    else:
        job = submit(context, body=body)
        assert job.execution_context.stack_snapshot.kernel_version == 'stack-kernel/2'
        assert job.execution_context.sensor_profile.id == profile_id
        with db.session() as session:
            row = session.scalar(select(JobRow).where(JobRow.id == job.id))
            restored = service.PreparedRun.from_payload(row.prepared)
            assert restored.input_hash == job.input_hash
            assert restored.execution_context.stack_snapshot.definition.schema_version == '2'
