"""Contract/queue/access checks; authored test handler is not simulator evidence."""
import hashlib
import json
from pathlib import Path
import sqlite3
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from ade2e.adapters import Registry
from ade2e.database import Database, Settings, SchemaVersionRow, SCHEMA_VERSION
from ade2e.experiment_models import ExperimentExecutionRow
from ade2e.experiment_runtime import ExperimentExecution, ExperimentHandlerMetadata, ExperimentOutputFile, ExperimentRegistry
from ade2e.experiment_routes import register_experiment_routes
from ade2e.experiment_schemas import ExperimentCreate
from ade2e.job_models import JobRow, EngineResourceRow
from ade2e.team_access import authenticate_request, register_team_routes
from ade2e.team_models import ProjectTokenRow
from ade2e import experiment_jobs, jobs
from ade2e.worker import run_once

class AuthoredHandler:
    def __init__(self, identity='authored-contract', resource=None, fail=False):
        self.identity, self.resource, self.fail = identity, resource, fail
    def metadata(self):
        return ExperimentHandlerMetadata(id=self.identity, version='1', kinds=['object_sim'], status='available',
            fidelity='authored_contract_test', resource_id=self.resource, cancellation_supported=True)
    def prepare(self, spec):
        return {'seed': spec.seed, 'project_id': spec.project_id, 'source': spec.input}
    def execute(self, snapshot, work_dir, should_cancel):
        if self.fail:
            (work_dir / 'external-dispatch.json').write_text(json.dumps({'external_id': 'test-only', 'completed': False}))
            raise RuntimeError('authored contract failure')
        output = work_dir / 'native-output.json'
        output.write_text(json.dumps({'seed': snapshot['seed'], 'value': snapshot['source']}))
        return ExperimentExecution(summary={'execution_completed': True, 'evaluation_status': 'not_evaluated'},
            artifacts=[ExperimentOutputFile(output, 'native-output.json', 'application/json')], fidelity='authored_contract_test')

@pytest.fixture
def env(tmp_path):
    settings = Settings(data_dir=tmp_path, api_token='bootstrap-test-only', seed_examples=False, team_mode=True)
    db = Database(settings)
    db.initialize()
    handlers = ExperimentRegistry([AuthoredHandler()], load_plugins=False)
    app = FastAPI()
    def authenticate(request: Request):
        header = request.headers.get('authorization', '')
        return authenticate_request(request, db, settings, header[7:] if header.startswith('Bearer ') else None)
    api = APIRouter(prefix='/api/v1', dependencies=[Depends(authenticate)])
    register_team_routes(api, db)
    register_experiment_routes(api, db, handlers)
    from ade2e.experiment_verification import register_experiment_verification_routes
    register_experiment_verification_routes(api, db)
    @api.get('/legacy-private')
    def legacy():
        return {'private': True}
    app.include_router(api)
    client = TestClient(app)
    client.headers['Authorization'] = 'Bearer bootstrap-test-only'
    yield db, handlers, client
    db.engine.dispose()

def definition(project='default', adapter='authored-contract'):
    return {'project_id': project, 'name': 'Contract experiment', 'kind': 'object_sim', 'adapter_id': adapter,
            'input': {'scenario_revision': 1, 'fixture': True}, 'seed': 42}

def create_submit(client, payload=None, key='one'):
    payload = payload or definition()
    preview = client.post('/api/v1/experiments/preview', json=payload)
    assert preview.status_code == 200, preview.text
    created = client.post('/api/v1/experiments', json=payload, headers={'Idempotency-Key': key})
    assert created.status_code == 201, created.text
    assert created.json()['preview_hash'] == preview.json()['preview_hash']
    identity = created.json()['id']
    submitted = client.post(f'/api/v1/experiments/{identity}/executions',
                            json={'preview_hash': created.json()['preview_hash']}, headers={'Idempotency-Key': key})
    assert submitted.status_code == 202, submitted.text
    return created.json(), submitted.json()

def test_immutable_input_queue_result_hash_and_streaming_download(env):
    db, handlers, client = env
    created, submitted = create_submit(client)
    result = run_once(db, Registry(), 'contract-worker', job_id=submitted['id'], experiment_registry=handlers)
    assert result.status == 'completed'
    assert result.fidelity == 'authored_contract_test'
    assert result.summary['evaluation_status'] == 'not_evaluated'
    receipt = client.get(result.download_url)
    assert receipt.status_code == 200
    assert hashlib.sha256(receipt.content).hexdigest() == receipt.headers['X-Content-SHA256']
    artifact = result.artifacts[0]
    download = client.get(artifact['download_url'])
    assert download.status_code == 200
    assert hashlib.sha256(download.content).hexdigest() == artifact['sha256']
    (db.experiment_dir / 'artifacts' / artifact['id']).write_bytes(b'corruption')
    assert client.get(artifact['download_url']).status_code == 409

def test_project_scope_roles_revocation_and_secret_storage(env):
    db, _, client = env
    project = client.post('/api/v1/projects', json={'name': 'Private study'}).json()['id']
    engineer = client.post(f'/api/v1/projects/{project}/tokens', json={'user_label': 'engineer-one', 'role': 'Engineer'}).json()
    viewer = client.post(f'/api/v1/projects/{project}/tokens', json={'user_label': 'reviewer-two', 'role': 'Viewer'}).json()
    with db.read_session() as session:
        row = session.get(ProjectTokenRow, engineer['id'])
        assert row.token_sha256 == hashlib.sha256(engineer['token'].encode()).hexdigest()
        assert engineer['token'] not in row.__dict__.values()
    client.headers['Authorization'] = 'Bearer ' + engineer['token']
    assert client.get('/api/v1/projects').json()['items'][0]['id'] == project
    assert client.get('/api/v1/legacy-private').status_code == 403
    assert client.get('/api/v1/experiments?project_id=default').status_code == 404
    created, submitted = create_submit(client, definition(project))
    assert client.post(f'/api/v1/projects/{project}/tokens', json={'user_label': 'illegal', 'role': 'Admin'}).status_code == 403
    client.headers['Authorization'] = 'Bearer ' + viewer['token']
    assert client.get(f'/api/v1/experiments/{created["id"]}').status_code == 200
    assert client.post('/api/v1/experiments/preview', json=definition(project)).status_code == 403
    assert client.post(f'/api/v1/experiments/executions/{submitted["id"]}/cancel').status_code == 403
    client.headers['Authorization'] = 'Bearer bootstrap-test-only'
    assert client.post(f'/api/v1/projects/{project}/tokens/{engineer["id"]}/revoke').status_code == 200
    client.headers['Authorization'] = 'Bearer ' + engineer['token']
    assert client.get('/api/v1/team/whoami').status_code == 401

def test_idempotent_create_submit_preview_mismatch_and_cancel(env):
    db, handlers, client = env
    created, submitted = create_submit(client)
    same = client.post('/api/v1/experiments', json=definition(), headers={'Idempotency-Key': 'one'}).json()
    assert same['id'] == created['id']
    different = definition()
    different['seed'] = 17
    assert client.post('/api/v1/experiments', json=different, headers={'Idempotency-Key': 'one'}).status_code == 409
    path = f'/api/v1/experiments/{created["id"]}/executions'
    same_job = client.post(path, json={'preview_hash': created['preview_hash']}, headers={'Idempotency-Key': 'one'}).json()
    assert same_job['id'] == submitted['id']
    assert client.post(path, json={'preview_hash': '0' * 64}, headers={'Idempotency-Key': 'two'}).status_code == 409
    cancelled = client.post(f'/api/v1/experiments/executions/{submitted["id"]}/cancel').json()
    assert cancelled['status'] == 'cancelled' and cancelled['dispatched_at'] is None
    assert run_once(db, Registry(), 'cancel-worker', experiment_registry=handlers) is None
    assert client.get(cancelled['download_url']).status_code == 200

def test_shared_resource_exclusion_failure_receipt_and_quarantine(env):
    db, handlers, client = env
    handlers.register(AuthoredHandler('gpu-one', 'carla', fail=True))
    handlers.register(AuthoredHandler('gpu-two', 'carla'))
    _, first = create_submit(client, definition(adapter='gpu-one'), 'one')
    _, second = create_submit(client, definition(adapter='gpu-two'), 'two')
    capabilities = {('experiment', item.id, item.version) for item in handlers.list()}
    claim = jobs.claim_job(db, 'gpu-worker', job_id=first['id'], capabilities=capabilities)
    assert claim is not None
    assert jobs.claim_job(db, 'other-worker', job_id=second['id'], capabilities=capabilities) is None
    from ade2e.worker import run_claim
    result = run_claim(db, Registry(), claim, experiment_registry=handlers)
    assert result.status == 'failed' and result.outcome_uncertain
    assert result.artifacts[0]['name'] == 'external-dispatch.json'
    assert (db.experiment_dir / 'attempts' / first['id'] / 'external-dispatch.json').is_file()
    with db.read_session() as session:
        assert session.get(EngineResourceRow, 'carla').state == 'reconcile_required'
    assert jobs.claim_job(db, 'next-worker', job_id=second['id'], capabilities=capabilities) is None
    sealed_hash = result.manifest_hash
    jobs.reconcile_resource(db, 'carla', first['id'], 'Authored test adapter has no remote engine; cleanup checked')
    assert jobs.job_get(db, first['id'], kind='experiment').manifest_hash == sealed_hash
    assert jobs.job_get(db, first['id'], kind='experiment').outcome_uncertain

def test_failed_native_logs_are_downloadable_but_private_and_unbounded_files_are_excluded(env):
    db, handlers, client = env
    class DiagnosticHandler(AuthoredHandler):
        def execute(self, snapshot, work_dir, should_cancel):
            (work_dir / 'single-external-dispatch.json').write_text('{"external_id":"authored-test-only"}')
            (work_dir / 'single-native-stderr.log').write_bytes(b'Authored native failure diagnostic\n')
            (work_dir / 'single-native-diagnostics.json').write_text('{"status":"failed"}')
            (work_dir / 'single-native-stdout.log').write_bytes(b'x'*(1024*1024+1))
            (work_dir / 'credentials.json').write_text('{"private":"must not publish"}')
            (work_dir / 'other-native-stderr.log').symlink_to(work_dir / 'credentials.json')
            raise RuntimeError('Authored native adapter failure')
    handlers.register(DiagnosticHandler('native-diagnostic-contract'))
    _, submitted = create_submit(client, definition(adapter='native-diagnostic-contract'), 'diagnostics')
    result = run_once(db, Registry(), 'diagnostic-worker', job_id=submitted['id'], experiment_registry=handlers)
    assert result.status == 'failed' and result.summary is None and result.outcome_uncertain
    artifacts = result.artifacts
    assert {a['name'] for a in artifacts} == {'single-external-dispatch.json', 'single-native-stderr.log', 'single-native-diagnostics.json'}
    for artifact in artifacts:
        response = client.get(artifact['download_url'])
        assert response.status_code == 200
        assert hashlib.sha256(response.content).hexdigest() == artifact['sha256']
    assert client.get(result.download_url).status_code == 200

def test_missing_handler_rejected_before_authoring(env):
    _, _, client = env
    assert client.post('/api/v1/experiments/preview', json=definition(adapter='not-installed')).status_code == 422
    assert client.post('/api/v1/experiments', json=definition(adapter='not-installed'), headers={'Idempotency-Key': 'none'}).status_code == 422
    with pytest.raises(ValueError):
        Settings(data_dir=Path('/tmp/unused-contract-path'), team_mode=True, api_token=None)
    oversized = definition()
    oversized['input'] = {'oversized_inline_content': 'x' * (2 * 1024 * 1024)}
    assert client.post('/api/v1/experiments/preview', json=oversized).status_code == 422

def test_expired_worker_is_fenced_and_receipt_retains_uncertainty(env):
    db, handlers, client = env
    _, submitted = create_submit(client)
    claim = jobs.claim_job(db, 'expired-worker', job_id=submitted['id'],
                           capabilities={('experiment', 'authored-contract', '1')})
    assert claim is not None
    assert jobs.mark_dispatched(db, claim)
    with jobs._write(db) as session:
        row = session.scalar(select(JobRow).where(JobRow.id == submitted['id']))
        row.lease_expires_at = '2020-01-01T00:00:00+00:00'
    assert jobs.expire_jobs(db) == 1
    terminal = jobs.job_get(db, submitted['id'], kind='experiment')
    assert terminal.status == 'interrupted' and terminal.outcome_uncertain
    with pytest.raises(jobs.LostLease):
        experiment_jobs.execute_claim(db, claim, handlers.get('authored-contract'), lambda: False)
    assert not terminal.artifacts
    assert run_once(db, Registry(), 'replacement-worker', experiment_registry=handlers) is None

def test_schema16_backup_restore_preserves_queue_identity_and_events(tmp_path):
    settings = Settings(data_dir=tmp_path / 'original', seed_examples=False)
    db = Database(settings)
    db.initialize()
    db.engine.dispose()
    original = settings.data_dir / 'platform.db'
    with sqlite3.connect(original) as connection:
        connection.execute('PRAGMA foreign_keys=OFF')
        ddl = connection.execute("SELECT sql FROM sqlite_master WHERE name='execution_jobs'").fetchone()[0]
        ddl = ddl.replace('resource_key VARCHAR(180), ', '').replace('resource_key VARCHAR(180),\n', '')
        ddl = ddl.replace(", 'experiment'", '')
        connection.execute('DROP TABLE execution_jobs')
        connection.execute(ddl)
        connection.execute("UPDATE schema_versions SET version=16")
        connection.execute("INSERT INTO execution_jobs (submission_seq,id,idempotency_key,request_hash,request,prepared,input_hash,kind,adapter_id,adapter_version,status,created_at,updated_at,cancellation_supported,resource_required,outcome_uncertain,evidence_eligible) VALUES(90,'old-job','old-key','old-hash','{}','{}','old-input','perception','old-adapter','1','queued','2026-10-07T00:00:00+00:00','2026-10-07T00:00:00+00:00',0,0,0,0)")
        connection.execute("INSERT INTO execution_job_events(job_id,at,type,message) VALUES('old-job','2026-10-07T00:00:00+00:00','queued','original event')")
        connection.commit()
        with sqlite3.connect(tmp_path / 'restored.db') as restored:
            connection.backup(restored)
    restored_db = Database(Settings(data_dir=tmp_path / 'restored', database_url=f'sqlite:///{tmp_path / "restored.db"}', seed_examples=False))
    restored_db.initialize()
    restored_db.initialize()
    with restored_db.engine.connect() as connection:
        assert connection.exec_driver_sql('SELECT version FROM schema_versions').scalar() == SCHEMA_VERSION
        assert connection.exec_driver_sql("SELECT submission_seq,id,idempotency_key,request_hash,prepared FROM execution_jobs WHERE id='old-job'").first() == (90, 'old-job', 'old-key', 'old-hash', '{}')
        assert connection.exec_driver_sql("SELECT message FROM execution_job_events WHERE job_id='old-job'").scalar() == 'original event'
        assert connection.exec_driver_sql('PRAGMA foreign_key_check').first() is None
    restored_db.engine.dispose()
