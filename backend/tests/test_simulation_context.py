"""Pinned stack dispatch and backwards compatibility; fake adapter, no CARLA engine."""
import hashlib
import json
from copy import deepcopy
from dataclasses import replace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from ade2e import jobs, service
from ade2e.adapters import AdapterExecutionError, AdapterInputError
from ade2e.database import RunRow, IdempotencyRow
from ade2e.job_models import JobRow, EngineResourceRow
from ade2e.reference import ReferenceAdapter
from ade2e.schemas import ArtifactPayload, RunCreate, RunDetail
from ade2e.simulation_context import (PreparedSimulationContext, SimulationControlSelection, SimulationExecutionRequest,
    execution_correlation, simulation_input_hash, sensor_profile_for_seed, validate_execution_correlation, validate_simulation_context)
from ade2e.stack_components import LidarCorridor
from ade2e.stack_runtime import StackRegistry
from ade2e.worker import run_once, run_claim
from test_stack_replays import definition
from live_stack_fixture import make_artifacts
from test_jobs import clock


class ContextAdapter(ReferenceAdapter):
    """Returns labelled reference output solely for core lifecycle tests."""
    def __init__(self, db):
        self.db, self.requests, self.preflights = db, [], []
        self.on_execute = self.on_preflight = None

    def metadata(self):
        return super().metadata().model_copy(update={'id': 'carla', 'version': 'context-fixture/1', 'name': 'Fake context engine'})

    def validate_execution_context(self, scenario, context):
        assert self.db.engine.pool.checkedout() == 0
        self.preflights.append(context.context_hash)
        if self.on_preflight:
            self.on_preflight(scenario, context)

    def execute_with_context(self, request):
        assert self.db.engine.pool.checkedout() == 0
        self.requests.append(request.model_copy(deep=True))
        if self.on_execute:
            self.on_execute(request)
        result = ReferenceAdapter.execute(self, request.scenario)
        result.model_fidelity = 'Authored protocol fixture; no CARLA physics or sensors'
        result.artifact_payloads = make_artifacts(request, result.trajectory, self.metadata().version)
        return result


@pytest.fixture
def context(client, make_scenario):
    scenario = make_scenario(duration_s=2, parameters={'trigger_time_s': 1, 'ego_speed_mps': 4, 'target_speed_mps': 4})
    stack = client.post('/api/v1/stacks', json=definition()).json()
    rig_body = {'name': 'Declared CARLA profile mount', 'reference_frame': 'ego', 'coordinate_convention': 'right_handed_z_up',
        'source_kind': 'simulation', 'source': 'Authored fixture', 'note': 'No physical calibration',
        'sensors': [{'key': 'roof', 'name': 'Roof', 'kind': 'lidar', 'frame_id': 'lidar_roof',
            'transform': {'translation_m': [0, 0, 2.2], 'rotation_xyzw': [0, 0, 0, 1]}}]}
    response = client.post('/api/v1/sensor-rigs', json=rig_body)
    assert response.status_code == 201, response.text
    rig = response.json()
    body = RunCreate(scenario_id=scenario['id'], adapter_id='carla', control={'mode': 'stack', 'stack_id': stack['id'],
        'stack_revision': 1, 'rig_id': rig['id'], 'rig_revision': 1, 'lidar_sensor_key': 'roof'})
    adapter = ContextAdapter(client.app.state.db)
    client.app.state.registry.register(adapter, replace=True)
    return client, client.app.state.db, client.app.state.registry, body, adapter, stack, rig, rig_body


def submit(ctx, key='job:context', body=None):
    return jobs.submit_job(ctx[1], ctx[2], body or ctx[3], key)


def execute(ctx, job):
    return run_once(ctx[1], ctx[2], 'context-worker', job_id=job.id)


def test_legacy_null_and_omitted_control_preserve_exact_request_and_prepared_hash(context):
    _, db, registry, body, *_ = context
    legacy = RunCreate(scenario_id=body.scenario_id)
    old = {'scenario_id': body.scenario_id, 'adapter_id': 'reference', 'seed': None,
        'parameter_overrides': {key: None for key in type(legacy.parameter_overrides).model_fields}}
    assert legacy.model_dump(mode='json') == old
    assert service.run_request_hash(legacy) == service.digest(old)
    assert service.run_request_hash(RunCreate(scenario_id=body.scenario_id, control=None)) == service.digest(old)
    with db.session() as session:
        prepared = service.prepare_run(session, registry, legacy)
    assert set(prepared.to_payload()) == {'scenario', 'adapter_metadata', 'request_hash'}
    restored = service.PreparedRun.from_payload(prepared.to_payload())
    assert restored == prepared
    assert prepared.input_hash == service.digest({'scenario': prepared.scenario.model_dump(mode='json'),
        'adapter_id': 'reference', 'adapter_version': prepared.adapter_metadata.version})


def test_context_pins_revisions_seed_and_roundtrips_without_latest_reads(context):
    client, db, registry, body, adapter, stack, rig, rig_body = context
    body = body.model_copy(update={'seed': 987})
    job = submit(context, body=body)
    snapshot = job.execution_context.model_dump(mode='json')
    assert snapshot['sensor_profile']['attributes']['noise_seed'] == '987'
    client.post(f'/api/v1/stacks/{stack["id"]}/revisions', json={**definition(), 'expected_revision': 1, 'note': 'New revision'}).raise_for_status()
    client.post(f'/api/v1/sensor-rigs/{rig["id"]}/revisions', json={**rig_body, 'expected_revision': 1, 'note': 'New rig revision'}).raise_for_status()
    client.patch(f'/api/v1/scenarios/{body.scenario_id}', json={'seed': 22}).raise_for_status()
    repeated = submit(context, body=body)
    assert repeated.id == job.id and repeated.execution_context.model_dump(mode='json') == snapshot
    result = execute(context, job)
    assert result.status == 'completed'
    request = adapter.requests[0]
    assert request.scenario.seed == 987 and request.context.stack_snapshot.revision == request.context.rig_snapshot.revision == 1
    with db.session() as session:
        row = session.scalar(select(JobRow).where(JobRow.id == job.id))
        assert row.prepared['schema_version'] == 'prepared-simulation/2'
        assert service.PreparedRun.from_payload(row.prepared).input_hash == job.input_hash
        run = service.run_get(session, result.run_id)
    assert run.execution_context.model_dump(mode='json') == snapshot
    assert service.completed_run_integrity_error(run) is None
    assert request.execution_id == result.run_id and request.input_hash == job.input_hash


def test_idempotent_replay_precedes_removed_adapter_and_control_change_conflicts(context):
    original = submit(context)
    context[2]._adapters.pop('carla')
    assert submit(context).id == original.id
    legacy = context[3].model_copy(update={'control': None})
    with pytest.raises(HTTPException) as info:
        submit(context, body=legacy)
    assert info.value.status_code == 409


def test_execution_id_is_correlation_only_and_mismatches_fail(context):
    job = submit(context)
    first = SimulationExecutionRequest(execution_id='run_one', input_hash=job.input_hash, scenario=job.scenario_snapshot, context=job.execution_context)
    second = first.model_copy(update={'execution_id': 'run_two'})
    assert simulation_input_hash(first.scenario, 'carla', 'context-fixture/1', first.context) == job.input_hash
    assert second.input_hash == first.input_hash
    validate_execution_correlation(execution_correlation(first), first)
    with pytest.raises(ValueError):
        validate_execution_correlation(execution_correlation(second), first)


@pytest.mark.parametrize('mutation', ['stack_hash', 'rig_hash', 'context_hash', 'profile', 'seed'])
def test_nested_snapshot_or_recipe_tamper_fails_closed(context, mutation):
    job = submit(context)
    value = job.execution_context.model_dump(mode='json')
    if mutation == 'stack_hash': value['stack_snapshot']['manifest_hash'] = '0'*64
    elif mutation == 'rig_hash': value['rig_snapshot']['definition_hash'] = '0'*64
    elif mutation == 'context_hash': value['context_hash'] = '0'*64
    elif mutation == 'profile': value['sensor_profile']['attributes']['range'] = '100'
    elif mutation == 'seed': value['sensor_profile']['attributes']['noise_seed'] = '43'
    with pytest.raises(ValueError):
        PreparedSimulationContext.model_validate(value)


@pytest.mark.parametrize('change', [{'coordinate_convention': 'unknown'}, {'reference_frame': 'map'},
    {'sensors': [{'key': 'roof', 'name': 'Roof', 'kind': 'lidar', 'frame_id': 'other', 'transform': {'translation_m': [0,0,2.2], 'rotation_xyzw': [0,0,0,1]}}]},
    {'sensors': [{'key': 'roof', 'name': 'Roof', 'kind': 'lidar', 'frame_id': 'lidar_roof', 'transform': {'translation_m': [0,0,3], 'rotation_xyzw': [0,0,0,1]}}]}])
def test_unsupported_rig_cannot_create_a_job(context, change):
    client, db, _, body, _, _, rig, rig_body = context
    changed = client.post(f'/api/v1/sensor-rigs/{rig["id"]}/revisions', json={**rig_body, **change, 'expected_revision': 1}).json()
    body = body.model_copy(update={'control': body.control.model_copy(update={'rig_revision': changed['revision']})})
    with pytest.raises(HTTPException) as info:
        submit(context, body=body)
    assert info.value.status_code == 422
    with db.session() as session:
        assert not list(session.scalars(select(JobRow)))


@pytest.mark.parametrize('revision', [0, -1, True, 'latest'])
def test_explicit_positive_integer_revisions_required(context, revision):
    data = context[3].model_dump(mode='json')
    data['control']['stack_revision'] = revision
    with pytest.raises(ValidationError):
        RunCreate.model_validate(data)


def test_other_adapter_and_legacy_only_hook_rejected(context):
    body = context[3].model_copy(update={'adapter_id': 'reference'})
    with pytest.raises(HTTPException) as info:
        submit(context, body=body)
    assert info.value.status_code == 422
    class LegacyOnly(ReferenceAdapter):
        def metadata(self):
            return super().metadata().model_copy(update={'id': 'carla'})
    context[2].register(LegacyOnly(), replace=True)
    with pytest.raises(HTTPException) as info:
        submit(context)
    assert info.value.status_code == 422


def test_preflight_is_read_only_before_dispatch_and_failure_keeps_resource_reusable(context):
    client, db, registry, body, adapter, *_ = context
    job = submit(context)
    def reject(scenario, ctx):
        with db.read_session() as session:
            assert session.scalar(select(JobRow).where(JobRow.id == job.id)).dispatched_at is None
        raise AdapterInputError('Scripted missing LiDAR blueprint attribute')
    adapter.on_preflight = reject
    outcome = execute(context, job)
    assert outcome.status == 'failed' and outcome.failure_http_status == 422
    assert adapter.requests == [] and not outcome.outcome_uncertain
    with db.session() as session:
        assert session.scalar(select(JobRow).where(JobRow.id == job.id)).evidence_eligible is False
        assert session.get(EngineResourceRow, 'carla').state == 'idle'


def test_metadata_drift_blocks_before_remote_preflight(context):
    job = submit(context)
    registry = StackRegistry(load_plugins=False)
    class Changed(LidarCorridor):
        def metadata(self):
            return super().metadata().model_copy(update={'name': 'Changed implementation'})
    registry.register(Changed(), replace=True)
    claim = jobs.claim_job(context[1], 'worker', job_id=job.id)
    outcome = run_claim(context[1], context[2], claim, stack_registry=registry)
    assert outcome.status == 'failed' and outcome.failure_http_status == 422
    assert context[4].preflights == context[4].requests == []


def test_same_carla_resource_serializes_stack_and_legacy(context):
    legacy = context[3].model_copy(update={'control': None})
    first = submit(context, 'job:legacy', legacy)
    second = submit(context, 'job:stack')
    claim = jobs.claim_job(context[1], 'one', job_id=first.id)
    assert claim is not None and jobs.claim_job(context[1], 'two', job_id=second.id) is None
    result = run_claim(context[1], context[2], claim)
    assert result.status == 'completed'
    assert jobs.claim_job(context[1], 'two', job_id=second.id) is not None


def test_preflight_cancellation_never_dispatches_and_cannot_call_legacy_hook(context):
    job = submit(context)
    context[4].on_preflight = lambda *_: jobs.cancel_job(context[1], job.id)
    outcome = execute(context, job)
    assert outcome.status == 'cancelled' and context[4].requests == []
    with context[1].session() as session:
        assert session.scalar(select(JobRow).where(JobRow.id == job.id)).dispatched_at is None


def test_late_cancellation_retains_actual_complete_result(context):
    job = submit(context)
    assert job.cancellation_supported is False
    context[4].on_execute = lambda *_: jobs.cancel_job(context[1], job.id)
    outcome = execute(context, job)
    assert outcome.status == 'completed' and outcome.run_id is not None
    assert any(event.type == 'cancel_not_honoured' for event in outcome.events)


def test_lost_lease_cannot_publish_context_result_or_artifacts(context, clock):
    job = submit(context)
    context[4].on_execute = lambda *_: clock(40)
    outcome = execute(context, job)
    assert outcome.status == 'interrupted' and outcome.run_id is None
    with context[1].session() as session:
        assert not list(session.scalars(select(RunRow)))
        assert session.get(EngineResourceRow, 'carla').state == 'reconcile_required'
    assert not list(context[1].artifact_dir.rglob('*.blob'))


def test_failed_stack_run_keeps_context_diagnostics_and_blocks_old_evidence(context):
    first = execute(context, submit(context, 'job:old'))
    job = submit(context, 'job:failure')
    def fail(request):
        content = service.canonical_bytes(execution_correlation(request))
        raise AdapterExecutionError('Scripted partial sensor failure', artifact_payloads=[ArtifactPayload(name='partial.json', kind='execution_diagnostic', media_type='application/json', content=content)])
    context[4].on_execute = fail
    outcome = execute(context, job)
    assert outcome.status == 'failed' and outcome.run_id != first.run_id
    with context[1].session() as session:
        run = service.run_get(session, outcome.run_id)
        assert run.status == 'error' and run.metrics == run.trajectory == []
        assert run.execution_context.context_hash == job.execution_context.context_hash
        assert service.latest_run_details(session)[job.scenario_id] is None  # Latest failed dispatch blocks older evidence.
    artifact = next(item for item in run.artifacts if item.kind == 'execution_diagnostic')
    content = context[0].get(artifact.download_url).content
    assert hashlib.sha256(content).hexdigest() == artifact.sha256
    assert json.loads(content)['context_hash'] == run.execution_context.context_hash


def test_run_recording_origin_pins_context_and_old_manifest_stays_exact(context):
    job = execute(context, submit(context))
    run = context[0].get(f'/api/v1/runs/{job.run_id}').json()
    artifact = next(item for item in run['artifacts'] if item['kind'] == 'sensor_recording')
    response = context[0].post(f'/api/v1/runs/{job.run_id}/recordings', json={'artifact_id': artifact['id']})
    assert response.status_code == 201, response.text
    origin = context[0].get(f'/api/v1/recordings/{response.json()["id"]}/origin').json()
    assert origin['snapshot']['execution_context_hash'] == run['execution_context']['context_hash']
    assert origin['snapshot']['execution_context'] == run['execution_context']
    tampered = deepcopy(run)
    tampered['execution_context']['context_hash'] = '0'*64
    with pytest.raises(ValueError):
        RunDetail.model_validate(tampered)


def test_legacy_run_json_and_export_bytes_remain_exact_after_context_execution(context):
    client, db, registry, body, *_ = context
    legacy = RunCreate(scenario_id=body.scenario_id)
    first = jobs.submit_job(db,registry,legacy,'job:legacy-bytes')
    first = run_once(db,registry,'legacy-worker',job_id=first.id)
    before = client.get('/api/v1/runs/'+first.run_id).json()
    assert 'execution_context' not in before
    manifest = client.get(before['artifacts'][-1]['download_url']).content
    assert service.canonical_bytes(RunDetail.model_validate(before).model_dump(mode='json')) == service.canonical_bytes(before)
    execute(context,submit(context))
    assert client.get('/api/v1/runs/'+first.run_id).json() == before
    assert client.get(before['artifacts'][-1]['download_url']).content == manifest
    assert hashlib.sha256(manifest).hexdigest() == before['artifacts'][-1]['sha256']
    replay = jobs.submit_job(db,registry,RunCreate(scenario_id=body.scenario_id,control=None),'job:legacy-bytes')
    assert replay.id == first.id


def test_control_works_through_both_versioned_api_entrypoints(context):
    client, _, _, body, *_ = context
    payload = body.model_dump(mode='json',exclude_none=True)
    response = client.post('/api/v1/jobs',json=payload,headers={'Idempotency-Key':'http-context'})
    assert response.status_code == 202, response.text
    job = response.json()
    assert job['request']['control'] == payload['control'] and job['execution_context']['context_hash']
    response = client.post('/api/v1/runs',json=payload,headers={'Idempotency-Key':'http-context'})
    assert response.status_code == 201, response.text
    run = response.json()
    assert run['execution_context'] == job['execution_context']
    replay = client.post('/api/v1/runs',json=payload,headers={'Idempotency-Key':'http-context'})
    assert replay.status_code == 201 and replay.json() == run  # Existing /runs response status is preserved.


def test_historical_baseline_and_comparison_do_not_invent_control_identity(client):
    from test_suites import make_suite, campaign, finish, baseline
    from ade2e.suites import compare_reports
    from ade2e.suite_schemas import BaselineDetail, CampaignDetail
    suite, *_ = make_suite(client,count=1)
    report = finish(client,campaign(client,suite))
    frozen = baseline(client,report)
    sealed_bytes = client.get(frozen['download_url']).content
    assert 'control_context_hash' not in report['cases'][0]['execution_context']
    detail = BaselineDetail.model_validate(frozen)
    assert service.canonical_bytes(detail.manifest.model_dump(mode='json')) == sealed_bytes
    current = CampaignDetail.model_validate(report)
    assert compare_reports(current,detail).rows[0].status == 'unchanged_pass'
    current.cases[0].execution_context.control_context_hash = 'e'*64
    compared = compare_reports(current,detail)
    assert compared.rows[0].status == 'not_comparable'
    assert compared.rows[0].metrics == []
    assert 'context changed' in compared.rows[0].reasons[0]
    assert client.get(frozen['download_url']).content == sealed_bytes
