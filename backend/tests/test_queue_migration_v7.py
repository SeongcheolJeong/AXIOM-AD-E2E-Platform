from ade2e.database import SCHEMA_VERSION
"""Schema6 parent constraint migration with actual stored perception evidence."""

import pytest
from sqlalchemy import event

from ade2e import jobs
from test_perception_jobs import context, execute, submit
from test_queue_migration_v6 import storage_snapshot


def make_schema6(context):
    client, app, _, body = context
    result = execute(context, submit(context))
    scenario = client.post('/api/v1/scenarios', json={'name': 'Preserved schema6 simulation', 'category': 'lane_follow'}).json()
    simulation = client.post('/api/v1/jobs', json={'scenario_id': scenario['id']}, headers={'Idempotency-Key': 'legacy-simulation'})
    assert simulation.status_code == 202, simulation.text
    db = app.state.db
    with db.engine.connect() as connection:
        connection.exec_driver_sql('PRAGMA foreign_keys=OFF')
        connection.commit()
        connection.exec_driver_sql('BEGIN IMMEDIATE')
        ddl = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name='execution_jobs'").scalar()
        assert "'evaluation'" in ddl
        old_ddl = ddl.replace('execution_jobs (', 'execution_jobs_old (', 1).replace(", 'experiment'", '').replace(", 'recovery'", '').replace(", 'stack_replay'", "").replace("'simulation', 'perception', 'evaluation'", "'simulation', 'perception'").replace("kind IN ('perception', 'evaluation')", "kind = 'perception'")
        assert "'evaluation'" not in old_ddl
        connection.exec_driver_sql(old_ddl)
        connection.exec_driver_sql('INSERT INTO execution_jobs_old SELECT * FROM execution_jobs')
        connection.exec_driver_sql('DROP TABLE execution_jobs')
        connection.exec_driver_sql('ALTER TABLE execution_jobs_old RENAME TO execution_jobs')
        from ade2e.job_models import JobRow
        for index in JobRow.__table__.indexes:
            index.create(connection)
        connection.exec_driver_sql("UPDATE sqlite_sequence SET seq=777 WHERE name='execution_jobs'")
        # No schema7 tables are present in the old fixture; this is real old
        # queue DDL, not simply a changed version marker on a compatible table.
        for table in ('detection_evaluation_sources', 'detection_evaluations', 'dataset_memberships', 'dataset_revisions', 'datasets', 'dataset_images'):
            connection.exec_driver_sql(f'DROP TABLE "{table}"')
        connection.exec_driver_sql('UPDATE schema_versions SET version=6')
        connection.commit()
        connection.exec_driver_sql('PRAGMA foreign_keys=ON')
        assert connection.exec_driver_sql('PRAGMA foreign_key_check').all() == []
    return db, result


def test_schema6_preserves_simulation_and_perception_evidence_and_next_sequence(context):
    db, result = make_schema6(context)
    before = storage_snapshot(db)[0]
    files = {path: path.read_bytes() for path in db.perception_dir.rglob('*') if path.is_file()}
    db.initialize()
    after = storage_snapshot(db)[0]
    for table, rows in before.items():
        if table != 'schema_versions':
            assert after[table] == rows, table
    assert after['schema_versions'] == [(SCHEMA_VERSION,)]
    restored = jobs.job_get(db, result.id, kind='perception')
    assert restored.model_dump() == result.model_dump()
    assert all(path.read_bytes() == value for path, value in files.items())
    with db.engine.begin() as connection:
        assert connection.exec_driver_sql('PRAGMA foreign_key_check').all() == []
        connection.exec_driver_sql("INSERT INTO execution_jobs (id,kind,idempotency_key,request_hash,request,prepared,input_hash,adapter_id,adapter_version,status,created_at,updated_at,cancellation_supported,resource_required,outcome_uncertain,evidence_eligible) VALUES ('eval-sequence','evaluation','eval:key','a','{}','{}','b','test','1','queued','time','time',1,0,0,0)")
        assert connection.exec_driver_sql("SELECT submission_seq FROM execution_jobs WHERE id='eval-sequence'").scalar() == 778


@pytest.mark.parametrize('point', ['after_drop', 'before_commit'])
def test_schema6_failed_migration_rolls_back_both_kinds_and_exact_child_manifests(context, point):
    db, _ = make_schema6(context)
    before = storage_snapshot(db)
    def fail_after_drop(_conn, _cursor, statement, *_args):
        if statement == 'DROP TABLE execution_jobs':
            raise RuntimeError('Injected schema7 DDL failure')
    def fail_commit(connection):
        if connection.exec_driver_sql('SELECT version FROM schema_versions').scalar() == SCHEMA_VERSION:
            raise RuntimeError('Injected schema7 commit failure')
    name, callback = ('after_cursor_execute', fail_after_drop) if point == 'after_drop' else ('commit', fail_commit)
    event.listen(db.engine, name, callback)
    try:
        with pytest.raises(RuntimeError, match='Injected schema7'):
            db.initialize()
    finally:
        event.remove(db.engine, name, callback)
    assert storage_snapshot(db) == before
    db.initialize()
    assert storage_snapshot(db)[0]['schema_versions'] == [(SCHEMA_VERSION,)]
