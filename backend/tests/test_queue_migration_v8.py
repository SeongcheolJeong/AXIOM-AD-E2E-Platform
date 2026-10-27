from ade2e.database import SCHEMA_VERSION
"""Real schema7 queue+completed perception/evaluation -> schema8, exact child bytes."""
import pytest
from sqlalchemy import event

from ade2e import jobs
from test_perception_jobs import context
from test_detection_evaluations import evaluation_context, execute, submit
from test_queue_migration_v6 import storage_snapshot


def schema7(evaluation_context):
    result = execute(evaluation_context, submit(evaluation_context))
    (client, app, *_), *_ = evaluation_context
    scenario = client.post('/api/v1/scenarios', json={'name': 'Old simulation', 'category': 'lane_follow'}).json()
    assert client.post('/api/v1/jobs', json={'scenario_id': scenario['id']}, headers={'Idempotency-Key': 'legacy'}).status_code == 202
    db = app.state.db
    with db.engine.connect() as conn:
        conn.exec_driver_sql('PRAGMA foreign_keys=OFF')
        conn.commit()
        conn.exec_driver_sql('BEGIN IMMEDIATE')
        ddl = conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name='execution_jobs'").scalar()
        assert "'stack_replay'" in ddl
        old = ddl.replace('execution_jobs (', 'execution_jobs_old (', 1).replace(", 'recovery'", '').replace(", 'stack_replay'", '')
        assert "'stack_replay'" not in old
        conn.exec_driver_sql(old)
        conn.exec_driver_sql('INSERT INTO execution_jobs_old SELECT * FROM execution_jobs')
        conn.exec_driver_sql('DROP TABLE execution_jobs')
        conn.exec_driver_sql('ALTER TABLE execution_jobs_old RENAME TO execution_jobs')
        from ade2e.job_models import JobRow
        for index in JobRow.__table__.indexes:
            index.create(conn)
        for table in ('stack_replay_ticks', 'stack_replays', 'component_stack_revisions', 'component_stacks'):
            conn.exec_driver_sql(f'DROP TABLE "{table}"')
        conn.exec_driver_sql("UPDATE sqlite_sequence SET seq=777 WHERE name='execution_jobs'")
        conn.exec_driver_sql('UPDATE schema_versions SET version=7')
        conn.commit()
        conn.exec_driver_sql('PRAGMA foreign_keys=ON')
        assert conn.exec_driver_sql('PRAGMA foreign_key_check').all() == []
    return db, result


def test_schema7_preserves_three_kinds_exact_evidence_and_sequence(evaluation_context):
    db, result = schema7(evaluation_context)
    rows = storage_snapshot(db)[0]
    files = {path: path.read_bytes() for folder in (db.perception_dir, db.dataset_image_dir, db.artifact_dir)
        for path in folder.rglob('*') if path.is_file()}
    db.initialize()
    after = storage_snapshot(db)[0]
    assert after['schema_versions'] == [(SCHEMA_VERSION,)]
    for name, values in rows.items():
        if name != 'schema_versions':
            assert after[name] == values, name
    assert jobs.job_get(db, result.id, kind='evaluation').model_dump() == result.model_dump()
    assert all(path.read_bytes() == value for path, value in files.items())
    with db.engine.begin() as conn:
        assert conn.exec_driver_sql('PRAGMA foreign_key_check').all() == []
        conn.exec_driver_sql("INSERT INTO execution_jobs (id,kind,idempotency_key,request_hash,request,prepared,input_hash,adapter_id,adapter_version,status,created_at,updated_at,cancellation_supported,resource_required,outcome_uncertain,evidence_eligible) VALUES ('stack-sequence','stack_replay','stack:key','a','{}','{}','b','recorded-stack','1','queued','time','time',1,0,0,0)")
        assert conn.exec_driver_sql("SELECT submission_seq FROM execution_jobs WHERE id='stack-sequence'").scalar() == 778


@pytest.mark.parametrize('point', ['after_drop', 'before_commit'])
def test_schema7_migration_failure_restores_exact_database(evaluation_context, point):
    db, _ = schema7(evaluation_context)
    before = storage_snapshot(db)
    def drop(conn, cursor, statement, *_):
        if statement == 'DROP TABLE execution_jobs':
            raise RuntimeError('Injected schema8 failure')
    def commit(conn):
        if conn.exec_driver_sql('SELECT version FROM schema_versions').scalar() == SCHEMA_VERSION:
            raise RuntimeError('Injected schema8 failure')
    name, hook = ('after_cursor_execute', drop) if point == 'after_drop' else ('commit', commit)
    event.listen(db.engine, name, hook)
    try:
        with pytest.raises(RuntimeError, match='Injected schema8'):
            db.initialize()
    finally:
        event.remove(db.engine, name, hook)
    assert storage_snapshot(db) == before
    db.initialize()
