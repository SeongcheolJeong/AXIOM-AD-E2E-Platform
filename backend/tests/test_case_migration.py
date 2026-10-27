"""Schema11→12 is additive and rollback-safe; existing evidence bytes survive."""
import pytest
from sqlalchemy import event

from ade2e.database import Database, Settings, SCHEMA_VERSION

TABLES=('investigation_case_evaluations','investigation_case_revisions','investigation_cases','investigation_segments')


def snapshot(db):
    with db.engine.connect() as conn:
        tables=conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").scalars().all()
        return {name:sorted(conn.exec_driver_sql('SELECT * FROM "'+name+'"').all(),key=repr) for name in tables}


@pytest.fixture
def old_db(tmp_path):
    db=Database(Settings(data_dir=tmp_path,seed_examples=False));db.initialize()
    with db.engine.begin() as conn:
        for name in TABLES: conn.exec_driver_sql('DROP TABLE '+name)
        conn.exec_driver_sql('UPDATE schema_versions SET version=11')
    yield db
    db.engine.dispose()


def test_case_migration_adds_only_its_tables_and_preserves_existing_rows(old_db):
    before=snapshot(old_db);old_db.initialize();after=snapshot(old_db)
    assert set(after)-set(before)==set(TABLES)
    assert after['schema_versions']==[(SCHEMA_VERSION,)]
    for name,rows in before.items():
        if name!='schema_versions': assert after[name]==rows
    old_db.initialize();assert snapshot(old_db)==after


@pytest.mark.parametrize('boundary',['create','commit'])
def test_case_migration_failure_rolls_back_all_new_tables_and_schema(old_db,boundary):
    before=snapshot(old_db)
    def statement(_c,_cu,sql,*_args):
        if sql.strip().startswith('CREATE TABLE investigation_cases'):
            raise RuntimeError('Injected case migration failure')
    def commit(conn):
        if conn.exec_driver_sql('SELECT version FROM schema_versions').scalar()==SCHEMA_VERSION:
            raise RuntimeError('Injected case migration failure')
    name,fn=('after_cursor_execute',statement) if boundary=='create' else ('commit',commit)
    event.listen(old_db.engine,name,fn)
    try:
        with pytest.raises(RuntimeError,match='Injected case migration'):old_db.initialize()
    finally:event.remove(old_db.engine,name,fn)
    assert snapshot(old_db)==before
