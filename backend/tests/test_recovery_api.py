"""Authenticated API and immutable downloaded recovery evidence; fake remote journal."""
import hashlib
from copy import deepcopy

import pytest
from sqlalchemy import select

from ade2e import jobs, service
from ade2e.adapters import AdapterUnavailable
from ade2e.database import ArtifactRow, RunRow
from ade2e.job_models import JobRow
from ade2e.recovery_models import RecoveryRow, RemoteAttemptRow
from ade2e.schemas import RunCreate
from ade2e.worker import run_once
from test_recoveries import JournalAdapter


@pytest.fixture
def api(client, make_scenario):
    scenario = make_scenario(duration_s=2, parameters={'trigger_time_s': 1})
    db, registry = client.app.state.db, client.app.state.registry
    adapter = JournalAdapter(db)
    registry.register(adapter)
    body = {'scenario_id': scenario['id'], 'adapter_id': 'journal'}
    response = client.post('/api/v1/jobs', json=body, headers={'Idempotency-Key': 'lost'})
    assert response.status_code == 202, response.text
    source = run_once(db, registry, 'source', job_id=response.json()['id'])
    assert source.status == 'failed'
    return client, db, registry, adapter, source


def submit(api, key='recover'):
    client, _, _, _, source = api
    response = client.post('/api/v1/execution-recoveries', json={'source_job_id': source.id, 'note': 'Retrieve existing evidence'},
                           headers={'Idempotency-Key': key})
    assert response.status_code == 202, response.text
    return response.json()


def complete(api):
    _, db, registry, *_ = api
    job = submit(api)
    result = run_once(db, registry, 'retriever', job_id=job['id'])
    assert result.outcome == 'recovered', result
    return result


def test_explicit_inspect_queue_export_original_identity_and_summary_marker(api):
    client, _, _, adapter, source = api
    status = client.get(f'/api/v1/jobs/{source.id}/remote-execution')
    assert status.status_code == 200 and status.json()['can_retrieve']
    assert client.post('/api/v1/execution-recoveries', json={'source_job_id': source.id, 'note': 'x'}).status_code == 422
    queued = submit(api)
    assert client.get(f'/api/v1/jobs/{queued["id"]}').status_code == 404
    assert client.get(f'/api/v1/execution-recoveries/{queued["id"]}/export').status_code == 409
    assert client.get('/api/v1/execution-recoveries', params={'source_job_id': 'absent'}).json()['items'] == []
    done = complete(api)
    snapshot = client.get(f'/api/v1/execution-recoveries/{done.id}/export').content
    assert hashlib.sha256(snapshot).hexdigest() == done.manifest_hash
    archive = client.get(f'/api/v1/execution-recoveries/{done.id}/archive')
    assert archive.status_code == 200 and hashlib.sha256(archive.content).hexdigest() == done.archive_hash
    assert archive.content == adapter.saved[source.run_id][1]
    assert client.post(f'/api/v1/execution-recoveries/{done.id}/cancel').json()['status'] == 'completed'
    assert client.get(f'/api/v1/execution-recoveries/{done.id}/export').content == snapshot
    assert client.get(f'/api/v1/jobs/{source.id}').json()['status'] == 'failed'
    marker = next(r for r in client.get('/api/v1/runs').json() if r['id'] == done.run_id)
    assert marker['recovery']['source_execution_id'] == source.run_id
    original = client.get(f'/api/v1/runs/{source.run_id}').json()
    assert 'recovery' not in original
    tool = next(x for x in client.get('/api/v1/agent/tools').json()['tools'] if x['name'] == 'retrieve_remote_execution')
    assert tool['required_headers'] == ['Idempotency-Key']
    assert tool['idempotency_scope'] == '/api/v1/execution-recoveries'


@pytest.mark.parametrize('mutation', ['summary', 'manifest', 'archive', 'run', 'binding'])
def test_corruption_is_rejected_at_authoritative_read_boundaries(api, mutation):
    client, db, _, _, source = api
    done = complete(api)
    with db.session.begin() as session:
        subject = session.get(RecoveryRow, done.id)
        if mutation == 'summary':
            subject.outcome = 'diagnostics_only'
        elif mutation == 'manifest':
            subject.manifest += b' '
        elif mutation == 'archive':
            (db.recovery_dir / subject.archive_storage_key).write_bytes(b'changed')
        elif mutation == 'binding':
            row = session.get(RemoteAttemptRow, source.id)
            row.store_id = 'substitute-store'
        else:
            row = session.get(RunRow, done.run_id)
            value = deepcopy(row.payload)
            value['metrics'][0]['value'] = 50
            row.payload = value
    route = (f'/api/v1/execution-recoveries/{done.id}/archive' if mutation == 'archive'
             else f'/api/v1/runs/{done.run_id}' if mutation in {'binding', 'run'}
             else f'/api/v1/execution-recoveries/{done.id}')
    assert client.get(route).status_code == 409
    if mutation in {'summary', 'manifest'}:
        assert client.get('/api/v1/execution-recoveries').status_code == 409
        assert client.get(f'/api/v1/execution-recoveries/{done.id}/export').status_code == 409


def test_preflight_unavailable_has_no_dispatch_or_uncertain_engine_claim(api):
    _, db, registry, adapter, source = api
    jobs.reconcile_resource(db, 'journal', source.id, 'Fixture stopped', confirmed_stopped=True)
    body = source.request
    queued = jobs.submit_job(db, registry, body, 'unavailable')
    def unavailable(_):
        raise AdapterUnavailable('Remote journal cannot be reached')
    adapter.inspect_execution = unavailable
    ended = run_once(db, registry, 'unavailable-worker', job_id=queued.id)
    assert ended.status == 'failed' and ended.failure_http_status == 422
    assert ended.dispatched_at is None and ended.run_id is None and not ended.outcome_uncertain
    assert adapter.executions == 1


def test_all_new_data_routes_obey_configured_auth(settings):
    from fastapi.testclient import TestClient
    from ade2e.main import create_app
    from ade2e.adapters import Registry
    settings.api_token = 'test-api-secret'
    with TestClient(create_app(settings, Registry(load_plugins=False))) as client:
        for method, path in [('GET', '/jobs/missing/remote-execution'), ('POST', '/execution-recoveries'),
                             ('GET', '/execution-recoveries'), ('GET', '/execution-recoveries/missing'),
                             ('POST', '/execution-recoveries/missing/cancel'), ('GET', '/execution-recoveries/missing/export'),
                             ('GET', '/execution-recoveries/missing/archive')]:
            assert client.request(method, '/api/v1'+path).status_code == 401
        assert client.get('/api/v1/execution-recoveries', headers={'Authorization': 'Bearer test-api-secret'}).status_code == 200


def test_sdk_example_uses_real_queue_and_verified_download_routes(api, tmp_path):
    """In-process HTTP transport with an independent worker; authored remote data."""
    import httpx
    import importlib.util
    from pathlib import Path
    import threading
    from axiom_sdk import AxiomClient
    client, db, registry, adapter, source = api
    path = Path(__file__).resolve().parents[2] / 'examples' / 'recovery_workflow.py'
    spec = importlib.util.spec_from_file_location('recovery_workflow_example', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stop = threading.Event()
    errors = []
    def worker():
        try:
            while not stop.wait(.02):
                run_once(db, registry, 'example-worker')
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=worker)
    thread.start()
    def handler(request):
        response = client.request(request.method, request.url.raw_path.decode(), content=request.content,
                                  headers=dict(request.headers))
        return httpx.Response(response.status_code, headers=response.headers, content=response.content)
    try:
        with AxiomClient(transport=httpx.MockTransport(handler)) as sdk:
            summary = module.run(sdk, source.id, tmp_path / 'example', note='Retain authored test result', idempotency_key='example')
        assert summary['outcome'] == 'recovered' and summary['source_status'] == 'failed'
        assert adapter.executions == 1
    finally:
        stop.set()
        thread.join(timeout=10)
    assert not thread.is_alive() and errors == []


def test_campaign_resolves_supplemental_evidence_but_existing_baseline_bytes_stay_fixed(client, make_scenario):
    scenario = make_scenario(duration_s=2, parameters={'trigger_time_s': 1})
    db, registry = client.app.state.db, client.app.state.registry
    adapter = JournalAdapter(db)
    registry.register(adapter)
    requirement = client.post('/api/v1/requirements', json={'name': 'No reference collision',
        'metric_name': 'collision_count', 'operator': 'lte', 'threshold': 0, 'scenario_ids': [scenario['id']]})
    assert requirement.status_code == 201, requirement.text
    suite = client.post('/api/v1/suites', json={'name': 'Recovery case', 'cases': [{'key': 'one', 'name': 'One case',
        'scenario_id': scenario['id'], 'scenario_revision': scenario['revision'], 'requirement_ids': [requirement.json()['id']]}]})
    assert suite.status_code == 201, suite.text
    campaign = client.post('/api/v1/campaigns', json={'suite_id': suite.json()['id'], 'suite_revision': 1,
        'adapter_id': 'journal', 'label': 'Response loss'}, headers={'Idempotency-Key': 'campaign'})
    assert campaign.status_code == 202, campaign.text
    case_id = campaign.json()['cases'][0]['job_id']
    source = run_once(db, registry, 'campaign-source', job_id=case_id)
    assert source.status == 'failed'
    baseline = client.post('/api/v1/baselines', json={'campaign_id': campaign.json()['id'], 'name': 'Before recovery',
        'note': 'Sealed incomplete evidence'}, headers={'Idempotency-Key': 'baseline'})
    assert baseline.status_code == 201, baseline.text
    baseline_bytes = client.get(f'/api/v1/baselines/{baseline.json()["id"]}/export').content
    queued = client.post('/api/v1/execution-recoveries', json={'source_job_id': case_id, 'note': 'Retrieve case evidence'},
        headers={'Idempotency-Key': 'recovery'}).json()
    recovered = run_once(db, registry, 'campaign-retriever', job_id=queued['id'])
    assert recovered.outcome == 'recovered'
    report = client.get(f'/api/v1/campaigns/{campaign.json()["id"]}').json()
    assert report['status'] == 'finished' and report['verdict'] == 'passed'
    assert report['counts']['failed'] == 1 and report['counts']['passed_cases'] == 1
    case = report['cases'][0]
    assert case['job_status'] == 'failed' and case['run_id'] == recovered.run_id
    assert case['recovery']['source_execution_id'] == source.run_id
    assert client.get(f'/api/v1/baselines/{baseline.json()["id"]}/export').content == baseline_bytes
