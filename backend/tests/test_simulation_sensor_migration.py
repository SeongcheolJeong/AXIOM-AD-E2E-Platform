from ade2e.database import SCHEMA_VERSION
"""Schema9 to10 adds only configuration tables; all old rows/files are retained."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event

from test_queue_migration_v6 import storage_snapshot

TABLES=('simulation_sensor_configuration_idempotency','simulation_sensor_configuration_revisions','simulation_sensor_configurations')


def schema9(client):
    scenario=client.post('/api/v1/scenarios',json={'name':'Existing scenario','category':'lane_follow'}).json()
    run=client.post('/api/v1/runs',json={'scenario_id':scenario['id']},headers={'Idempotency-Key':'old-run'})
    assert run.status_code==201,run.text
    job=client.post('/api/v1/jobs',json={'scenario_id':scenario['id']},headers={'Idempotency-Key':'old-job'})
    assert job.status_code==202,job.text
    db=client.app.state.db
    with db.engine.begin() as connection:
        for table in TABLES:connection.exec_driver_sql('DROP TABLE '+table)
        connection.exec_driver_sql('UPDATE schema_versions SET version=9')
        connection.exec_driver_sql("UPDATE sqlite_sequence SET seq=777 WHERE name='execution_jobs'")
    return db


def test_additive_schema9_migration_does_not_rebuild_queue_or_change_rows_files(client):
    db=schema9(client);before=storage_snapshot(db)[0]
    files={p:p.read_bytes() for p in db.artifact_dir.rglob('*') if p.is_file()}
    statements=[]
    def record(_,__,statement,*___):statements.append(statement)
    event.listen(db.engine,'before_cursor_execute',record)
    try:db.initialize()
    finally:event.remove(db.engine,'before_cursor_execute',record)
    after=storage_snapshot(db)[0]
    assert after['schema_versions']==[(SCHEMA_VERSION,)]
    assert all(after[table]==rows for table,rows in before.items() if table!='schema_versions')
    assert not any('DROP TABLE' in statement or 'execution_jobs_v9' in statement for statement in statements)
    assert all(path.read_bytes()==content for path,content in files.items())
    assert all(table in after and after[table]==[] for table in TABLES)
    with db.engine.connect() as connection:
        assert connection.exec_driver_sql('PRAGMA foreign_key_check').all()==[]
        assert connection.exec_driver_sql("SELECT seq FROM sqlite_sequence WHERE name='execution_jobs'").scalar()==777


@pytest.mark.parametrize('boundary',['after_create','before_commit'])
def test_additive_migration_failure_rolls_back_new_tables_version_and_rows(client,boundary):
    db=schema9(client);before=storage_snapshot(db)
    def on_statement(_,__,statement,*___):
        if 'CREATE TABLE simulation_sensor_configurations' in statement:raise RuntimeError('migration failure')
    def on_commit(connection):
        if connection.exec_driver_sql('SELECT version FROM schema_versions').scalar()==SCHEMA_VERSION:raise RuntimeError('migration failure')
    name,callback=('after_cursor_execute',on_statement) if boundary=='after_create' else ('commit',on_commit)
    event.listen(db.engine,name,callback)
    try:
        with pytest.raises(RuntimeError,match='migration failure'):db.initialize()
    finally:event.remove(db.engine,name,callback)
    assert storage_snapshot(db)==before
    db.initialize()


def test_concurrent_schema9_upgrades_are_idempotent(client):
    db=schema9(client)
    before=storage_snapshot(db)[0]
    with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(lambda _:db.initialize(),range(3)))
    after=storage_snapshot(db)[0]
    assert after['schema_versions']==[(SCHEMA_VERSION,)]
    assert all(after[table]==rows for table,rows in before.items() if table!='schema_versions')
