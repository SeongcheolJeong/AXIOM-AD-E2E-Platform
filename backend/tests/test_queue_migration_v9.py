from ade2e.database import SCHEMA_VERSION
"""Real schema8 queue constraint migration, preserving old storage and evidence."""
import pytest
from sqlalchemy import event

from ade2e import jobs
from test_stack_replays import context, execute, submit
from test_queue_migration_v6 import storage_snapshot


def schema8(client):
    ctx = context(client)
    result = execute(ctx, submit(ctx))
    assert result.status == 'completed'
    scenario = client.post('/api/v1/scenarios', json={'name': 'Old simulation', 'category': 'lane_follow'}).json()
    run = client.post('/api/v1/runs', json={'scenario_id': scenario['id']}, headers={'Idempotency-Key': 'old-run'})
    assert run.status_code == 201, run.text
    db = client.app.state.db
    with db.engine.connect() as connection:
        connection.exec_driver_sql('PRAGMA foreign_keys=OFF')
        connection.commit()
        connection.exec_driver_sql('BEGIN IMMEDIATE')
        # Include persisted typed intent for both other preexisting domains;
        # their full child evidence is covered by the schema6/7 migration tests.
        for kind in ('perception', 'evaluation'):
            connection.exec_driver_sql("INSERT INTO execution_jobs (id,kind,idempotency_key,request_hash,request,prepared,input_hash,adapter_id,adapter_version,status,created_at,updated_at,cancellation_supported,resource_required,outcome_uncertain,evidence_eligible) VALUES (?,?,?,'hash','{}','{}','input','old-runtime','1','queued','time','time',1,0,0,0)", (kind, kind, f'old-{kind}'))
        ddl = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name='execution_jobs'").scalar()
        assert "'recovery'" in ddl
        old = ddl.replace('execution_jobs (', 'execution_jobs_old (', 1).replace(", 'recovery'", '')
        assert "'recovery'" not in old
        connection.exec_driver_sql(old)
        connection.exec_driver_sql('INSERT INTO execution_jobs_old SELECT * FROM execution_jobs')
        connection.exec_driver_sql('DROP TABLE execution_jobs')
        connection.exec_driver_sql('ALTER TABLE execution_jobs_old RENAME TO execution_jobs')
        from ade2e.job_models import JobRow
        for index in JobRow.__table__.indexes:
            index.create(connection)
        connection.exec_driver_sql('DROP TABLE execution_recoveries')
        connection.exec_driver_sql('DROP TABLE remote_execution_attempts')
        connection.exec_driver_sql("UPDATE sqlite_sequence SET seq=999 WHERE name='execution_jobs'")
        connection.exec_driver_sql('UPDATE schema_versions SET version=8')
        connection.commit()
        connection.exec_driver_sql('PRAGMA foreign_keys=ON')
        assert connection.exec_driver_sql('PRAGMA foreign_key_check').all() == []
    return db, result


def test_schema8_preserves_all_prior_kinds_exact_child_evidence_and_sequence(client):
    db, result = schema8(client)
    before = storage_snapshot(db)[0]
    files = {path: path.read_bytes() for root in (db.artifact_dir, db.stack_replay_dir, db.recording_dir)
             for path in root.rglob('*') if path.is_file()}
    db.initialize()
    after = storage_snapshot(db)[0]
    assert after['schema_versions'] == [(SCHEMA_VERSION,)]
    for table, rows in before.items():
        if table != 'schema_versions':
            assert after[table] == rows, table
    assert jobs.job_get(db, result.id, kind='stack_replay').model_dump(mode='json') == result.model_dump(mode='json')
    assert all(path.read_bytes() == content for path, content in files.items())
    with db.engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO execution_jobs (id,kind,idempotency_key,request_hash,request,prepared,input_hash,adapter_id,adapter_version,status,created_at,updated_at,cancellation_supported,resource_required,outcome_uncertain,evidence_eligible) VALUES ('new-recovery','recovery','new-key','a','{}','{}','b','recovery','1','queued','time','time',1,0,0,0)")
        assert connection.exec_driver_sql("SELECT submission_seq FROM execution_jobs WHERE id='new-recovery'").scalar() == 1000
        assert connection.exec_driver_sql('PRAGMA foreign_key_check').all() == []


@pytest.mark.parametrize('point', ['after_drop', 'before_commit'])
def test_schema8_failure_rolls_back_parent_children_and_version(client, point):
    db, _ = schema8(client)
    before = storage_snapshot(db)
    def after_drop(connection, cursor, statement, *_):
        if statement == 'DROP TABLE execution_jobs':
            raise RuntimeError('Injected schema9 failure')
    def before_commit(connection):
        if connection.exec_driver_sql('SELECT version FROM schema_versions').scalar() == SCHEMA_VERSION:
            raise RuntimeError('Injected schema9 failure')
    name, callback = ('after_cursor_execute', after_drop) if point == 'after_drop' else ('commit', before_commit)
    event.listen(db.engine, name, callback)
    try:
        with pytest.raises(RuntimeError, match='schema9'):
            db.initialize()
    finally:
        event.remove(db.engine, name, callback)
    assert storage_snapshot(db) == before
    db.initialize()


def test_unknown_future_schema_is_rejected_without_writes(client):
    db, _ = schema8(client)
    with db.engine.begin() as connection:
        connection.exec_driver_sql('UPDATE schema_versions SET version=999')
    before = storage_snapshot(db)
    with pytest.raises(RuntimeError, match='Unsupported database schema'):
        db.initialize()
    assert storage_snapshot(db) == before
