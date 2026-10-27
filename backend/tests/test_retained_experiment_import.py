"""Retained-result transactions use authored fixtures, never simulator evidence."""
import hashlib
import json
import pytest
from sqlalchemy import select

from ade2e import experiment_jobs, jobs, retained_experiment_import as retention
from ade2e.database import Database, Settings, SCHEMA_VERSION
from ade2e.experiment_models import ExperimentArtifactRow, ExperimentExecutionRow
from ade2e.experiment_runtime import ExperimentHandlerMetadata, ExperimentRegistry
from ade2e.experiment_schemas import ExperimentCreate
from ade2e.job_models import JobRow
from ade2e.service import uid, utcnow


class FixtureHandler:
    def metadata(self):
        return ExperimentHandlerMetadata(id='retained-contract-fixture', version='fixture/1', kinds=['sensor_capture'],
                                        status='available', fidelity='authored_test_only', resource_id='carla')

    def prepare(self, spec):
        return {'authored_fixture': True, 'seed': spec.seed}

    def execute(self, *_):
        raise AssertionError('Retention must never execute an adapter')


def completed(db, key):
    registry = ExperimentRegistry([FixtureHandler()], load_plugins=False)
    spec = ExperimentCreate(project_id='default', name=key, kind='sensor_capture',
                            adapter_id='retained-contract-fixture', input={'authored_fixture': True}, seed=42)
    parent = experiment_jobs.create(db, registry, spec, key)
    detail = experiment_jobs.submit(db, registry, parent['id'], parent['preview_hash'], key)
    content = ('authored artifact ' + key).encode()
    identity = uid('artifact')
    directory = db.experiment_dir / 'artifacts'
    directory.mkdir(exist_ok=True)
    path = directory / identity
    path.write_bytes(content)
    with jobs._write(db) as session:
        row = session.scalar(select(JobRow).where(JobRow.id == detail.id))
        now = utcnow()
        row.started_at = row.dispatched_at = row.finished_at = row.updated_at = now
        row.status = 'completed'
        row.worker_id = 'authored-retention-test-worker'
        subject = session.get(ExperimentExecutionRow, row.id)
        subject.summary = {'fixture_only': True, 'evaluation_status': 'not_evaluated'}
        subject.fidelity = 'authored_test_only'
        session.add(ExperimentArtifactRow(id=identity, job_id=row.id, name='authored-output.bin',
            media_type='application/octet-stream', storage_key=identity,
            sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content)))
        jobs._event(session, row, 'completed', 'Authored retention fixture; no simulator was run.', now)
        experiment_jobs.seal_terminal(session, row)
    result = jobs.job_get(db, detail.id, kind='experiment')
    return retention.RetainedExecutionSelection(result.id, result.manifest_hash), path, parent['id']


@pytest.fixture
def env(tmp_path):
    source = Database(Settings(data_dir=tmp_path / 'source', seed_examples=False))
    target = Database(Settings(data_dir=tmp_path / 'target', seed_examples=False))
    source.initialize()
    target.initialize()
    first = completed(source, 'first')
    second = completed(source, 'second')
    unrelated = completed(target, 'target unrelated')
    # The actual retained source runs schema 17. These selected table contracts
    # are identical; the importer must not initialize/migrate the source.
    with source.engine.begin() as connection:
        connection.exec_driver_sql('UPDATE schema_versions SET version=17')
    receipts = tmp_path / 'operator-receipts'
    receipts.mkdir(mode=0o700)
    yield source, target, [first, second], unrelated, receipts
    source.engine.dispose()
    target.engine.dispose()


def invoke(env, *, apply=True, receipt='retention.json', selections=None):
    source, target, fixtures, _, receipts = env
    return retention.retain_experiment_executions(source_data_dir=source.experiment_dir.parent,
        target_data_dir=target.experiment_dir.parent, project_id='default',
        selections=selections if selections is not None else [item[0] for item in fixtures],
        source_label='Authored fixture source; no CARLA execution',
        receipt_path=receipts / receipt if receipt else None, apply=apply)


def snapshot(db):
    with db.read_session() as session:
        tables = [row[0] for row in session.connection().exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {table: [tuple(row) for row in session.connection().exec_driver_sql(f'SELECT * FROM "{table}"')]
                for table in tables}


def payload_files(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*')
            if path.is_file() and path.name != 'platform.db' and not path.name.startswith('platform.db-')}


@pytest.mark.parametrize('source_version,target_version', [(18, 18), (18, 19), (19, 19)])
def test_current_retained_sources_preserve_schema_and_selected_receipts(env, source_version, target_version):
    source, target, fixtures, _, _ = env
    with source.engine.begin() as connection:
        connection.exec_driver_sql('UPDATE schema_versions SET version=?', (source_version,))
    with target.engine.begin() as connection:
        connection.exec_driver_sql('UPDATE schema_versions SET version=?', (target_version,))
    report = invoke(env)
    assert report['operation'] == 'new'
    assert report['private']['source_schema_version'] == source_version
    assert report['private']['target_schema_version'] == target_version
    for selection, _, _ in fixtures:
        assert jobs.job_get(target, selection.job_id, kind='experiment').manifest_hash == selection.manifest_sha256
    for db, expected in ((source, source_version), (target, target_version)):
        with db.engine.connect() as connection:
            assert connection.exec_driver_sql('SELECT version FROM schema_versions').scalar() == expected


def test_preview_has_no_mutation_and_public_metadata_has_no_roots(env):
    source, target, _, _, receipts = env
    before_source, before_target = snapshot(source), snapshot(target)
    source_files, target_files = payload_files(source.experiment_dir), payload_files(target.experiment_dir)
    report = invoke(env, apply=False, receipt=None)
    assert report['operation'] == 'preview'
    assert report['public']['execution_count'] == 2
    assert report['public']['new_execution_submitted'] is False
    assert str(source.experiment_dir.parent) not in json.dumps(report['public'])
    assert str(target.experiment_dir.parent) not in json.dumps(report['public'])
    assert snapshot(source) == before_source
    assert snapshot(target) == before_target
    assert payload_files(source.experiment_dir) == source_files
    assert payload_files(target.experiment_dir) == target_files
    assert list(receipts.iterdir()) == []


def test_exact_seals_artifacts_events_new_sequences_and_idempotent_retry(env):
    source, target, fixtures, unrelated, receipts = env
    before_source, before_target = snapshot(source), snapshot(target)
    report = invoke(env)
    assert report['operation'] == 'new'
    assert (receipts / 'retention.json').stat().st_mode & 0o777 == 0o600
    private = json.loads((receipts / 'retention.json').read_bytes())
    assert private == report and private['private']['source_schema_version'] == 17
    assert snapshot(source) == before_source
    after = snapshot(target)
    for table in before_target:
        if table not in retention.TABLES and table != 'sqlite_sequence':
            assert after[table] == before_target[table], table
    for selection, path, parent_id in fixtures:
        original = jobs.job_get(source, selection.job_id, kind='experiment')
        retained = jobs.job_get(target, selection.job_id, kind='experiment')
        assert original.model_dump() == retained.model_dump()
        assert retained.manifest_hash == selection.manifest_sha256
        assert (target.experiment_dir / 'artifacts' / path.name).read_bytes() == path.read_bytes()
        with source.read_session() as left, target.read_session() as right:
            original_row = left.scalar(select(JobRow).where(JobRow.id == selection.job_id))
            retained_row = right.scalar(select(JobRow).where(JobRow.id == selection.job_id))
            assert retained_row.submission_seq == original_row.submission_seq + 1
            original_subject = left.get(ExperimentExecutionRow, selection.job_id)
            retained_subject = right.get(ExperimentExecutionRow, selection.job_id)
            assert retained_subject.manifest == original_subject.manifest
    assert jobs.job_get(target, unrelated[0].job_id, kind='experiment').manifest_hash == unrelated[0].manifest_sha256
    before_retry, files_before_retry = snapshot(target), payload_files(target.experiment_dir)
    retry = invoke(env, receipt='verified-retry.json')
    assert retry['operation'] == 'already_retained'
    assert snapshot(target) == before_retry
    assert payload_files(target.experiment_dir) == files_before_retry
    assert retry['public'] == report['public']
    # Imported terminal jobs are not claimed and no source resource/worker rows
    # or permission material was introduced.
    assert jobs.claim_job(target, 'must-not-execute-import', capabilities={('experiment', 'retained-contract-fixture', 'fixture/1')}) is None


@pytest.mark.parametrize('corruption', ['pin', 'artifact_bytes', 'artifact_symlink', 'manifest', 'prepared', 'request'])
def test_source_corruption_fails_before_target_publication(env, corruption):
    source, target, fixtures, _, receipts = env
    selections = [item[0] for item in fixtures]
    selection, path, _ = fixtures[0]
    if corruption == 'pin':
        selections[0] = retention.RetainedExecutionSelection(selection.job_id, '0' * 64)
    elif corruption == 'artifact_bytes':
        path.write_bytes(b'wrong bytes')
    elif corruption == 'artifact_symlink':
        content = path.read_bytes()
        path.unlink()
        outside = receipts / 'source-outside.bin'
        outside.write_bytes(content)
        path.symlink_to(outside)
    else:
        with source.engine.begin() as connection:
            if corruption == 'manifest':
                connection.exec_driver_sql('UPDATE experiment_executions SET manifest=? WHERE job_id=?', (b'{}', selection.job_id))
            else:
                connection.exec_driver_sql(f'UPDATE execution_jobs SET {corruption}=? WHERE id=?', ('{}', selection.job_id))
    before, before_files = snapshot(target), payload_files(target.experiment_dir)
    with pytest.raises(retention.RetainedImportError):
        invoke(env, selections=selections)
    assert snapshot(target) == before
    assert payload_files(target.experiment_dir) == before_files
    assert not (receipts / 'retention.json').exists()


def test_partial_parent_collision_and_unowned_file_fail_closed(env):
    source, target, fixtures, _, receipts = env
    parent_id = fixtures[0][2]
    with source.read_session() as session:
        parent = retention._one(session.connection(), 'experiments', 'id', parent_id)
    with target.engine.begin() as connection:
        retention._insert(connection, 'experiments', parent)
    before, before_files = snapshot(target), payload_files(target.experiment_dir)
    with pytest.raises(retention.RetainedImportError, match='partial'):
        invoke(env)
    assert snapshot(target) == before and payload_files(target.experiment_dir) == before_files
    with target.engine.begin() as connection:
        connection.exec_driver_sql('DELETE FROM experiments WHERE id=?', (parent_id,))
    orphan = target.experiment_dir / 'artifacts' / fixtures[0][1].name
    orphan.write_bytes(fixtures[0][1].read_bytes())
    before = snapshot(target)
    with pytest.raises(retention.RetainedImportError, match='unowned'):
        invoke(env)
    assert snapshot(target) == before and orphan.read_bytes() == fixtures[0][1].read_bytes()


@pytest.mark.parametrize('failure', ['insert', 'seal', 'publish', 'commit'])
def test_transaction_failure_rolls_back_only_owned_files(env, monkeypatch, failure):
    _, target, fixtures, _, receipts = env
    before, before_files = snapshot(target), payload_files(target.experiment_dir)
    if failure == 'insert':
        original = retention._insert
        def fail_insert(connection, table, row):
            if table == 'experiment_executions':
                raise OSError('Injected row publication failure')
            original(connection, table, row)
        monkeypatch.setattr(retention, '_insert', fail_insert)
    elif failure == 'seal':
        original = retention._validate_execution
        def fail_target(session, identity, pin):
            if session.connection().exec_driver_sql('SELECT version FROM schema_versions').scalar() == SCHEMA_VERSION:
                raise retention.RetainedImportError('Injected target seal failure')
            return original(session, identity, pin)
        monkeypatch.setattr(retention, '_validate_execution', fail_target)
    elif failure == 'publish':
        original = retention._publish_file
        calls = []
        def collision(source, destination, owned):
            calls.append(destination)
            if len(calls) == 2:
                destination.write_bytes(b'concurrent unrelated file')
            original(source, destination, owned)
        monkeypatch.setattr(retention, '_publish_file', collision)
    else:
        from sqlalchemy.engine import Connection
        original = Connection.commit
        def fail_commit(connection):
            if connection.exec_driver_sql('SELECT version FROM schema_versions').scalar() == SCHEMA_VERSION:
                raise OSError('Injected commit failure')
            return original(connection)
        monkeypatch.setattr(Connection, 'commit', fail_commit)
    with pytest.raises((OSError, retention.RetainedImportError)):
        invoke(env)
    assert snapshot(target) == before
    after_files = payload_files(target.experiment_dir)
    if failure == 'publish':
        collision_path = 'artifacts/' + fixtures[1][1].name
        assert after_files.pop(collision_path) == b'concurrent unrelated file'
    assert after_files == before_files
    assert not (receipts / 'retention.json').exists()
    assert not list(target.experiment_dir.glob('retained-import-*'))


def test_lost_successful_commit_acknowledgement_never_deletes_committed_evidence(env, monkeypatch):
    _, target, fixtures, _, receipts = env
    from sqlalchemy.engine import Connection
    original = Connection.commit
    calls = []

    def lost_ack(connection):
        target_schema = connection.exec_driver_sql('SELECT version FROM schema_versions').scalar() == SCHEMA_VERSION
        original(connection)
        if target_schema:
            calls.append(True)
            raise OSError('Injected acknowledgement failure after COMMIT succeeded')

    with monkeypatch.context() as patch:
        patch.setattr(Connection, 'commit', lost_ack)
        with pytest.raises(retention.RetainedImportError, match='exact selected closure is retained'):
            invoke(env)
    assert calls == [True]
    assert (receipts / 'retention.json').is_file()
    for selection, path, _ in fixtures:
        assert jobs.job_get(target, selection.job_id, kind='experiment').manifest_hash == selection.manifest_sha256
        assert (target.experiment_dir / 'artifacts' / path.name).read_bytes() == path.read_bytes()
    assert invoke(env, receipt='after-lost-ack.json')['operation'] == 'already_retained'


@pytest.mark.parametrize('bad_boundary', ['same_workspace', 'missing_target', 'project', 'schema', 'receipt'])
def test_explicit_workspace_project_schema_and_receipt_boundaries(env, bad_boundary):
    source, target, fixtures, _, receipts = env
    source_dir, target_dir = source.experiment_dir.parent, target.experiment_dir.parent
    project, receipt = 'default', receipts / 'retention.json'
    if bad_boundary == 'same_workspace':
        target_dir = source_dir
    elif bad_boundary == 'missing_target':
        target_dir = target_dir / 'missing'
    elif bad_boundary == 'project':
        project = 'other-project'
    elif bad_boundary == 'schema':
        with target.engine.begin() as connection:
            connection.exec_driver_sql('UPDATE schema_versions SET version=17')
    else:
        receipt.write_bytes(b'existing operator receipt')
    before = snapshot(target)
    with pytest.raises(retention.RetainedImportError):
        retention.retain_experiment_executions(source_data_dir=source_dir, target_data_dir=target_dir,
            project_id=project, selections=[item[0] for item in fixtures], source_label='Authored source',
            receipt_path=receipt, apply=True)
    assert snapshot(target) == before
    if bad_boundary == 'missing_target':
        assert not target_dir.exists()
    if bad_boundary == 'receipt':
        assert receipt.read_bytes() == b'existing operator receipt'
