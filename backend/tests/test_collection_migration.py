"""Schema12→13 adds collection tables only, with rollback and concurrent-start checks."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event

from ade2e.database import Database, Settings, SCHEMA_VERSION

TABLES = ('data_collection_datasets', 'data_collection_revisions', 'data_collections')


def snapshot(db):
    with db.engine.connect() as connection:
        names=connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").scalars().all()
        return {name:sorted(connection.exec_driver_sql('SELECT * FROM "'+name+'"').all(),key=repr) for name in names}


@pytest.fixture
def previous(tmp_path):
    db=Database(Settings(data_dir=tmp_path,seed_examples=False));db.initialize()
    with db.engine.begin() as c:
        for table in TABLES:c.exec_driver_sql('DROP TABLE '+table)
        c.exec_driver_sql('UPDATE schema_versions SET version=12')
        # Preserve the exact JSON representation, not just semantic equality.
        c.exec_driver_sql("INSERT INTO scenarios(id,category,payload) VALUES ('retained','lane_follow', ?)", ('{ "name" : "한글 · retained", "count" : 3 }',))
    yield db
    db.engine.dispose()


def test_schema13_only_adds_collection_tables_and_keeps_old_rows(previous):
    before=snapshot(previous)
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:previous.initialize(),range(2)))
    after=snapshot(previous)
    assert set(after)-set(before)==set(TABLES)
    assert after['schema_versions']==[(SCHEMA_VERSION,)]
    assert all(rows==after[name] for name,rows in before.items() if name!='schema_versions')
    previous.initialize();assert snapshot(previous)==after


@pytest.mark.parametrize('boundary',['ddl','commit'])
def test_schema13_failure_restores_exact_original_tables_and_version(previous,boundary):
    before=snapshot(previous)
    def ddl(_c,_cu,statement,*_args):
        if statement.strip().startswith('CREATE TABLE data_collections'):raise RuntimeError('Injected migration failure')
    def commit(c):
        if c.exec_driver_sql('SELECT version FROM schema_versions').scalar()==SCHEMA_VERSION:raise RuntimeError('Injected migration failure')
    name,callback=('after_cursor_execute',ddl) if boundary=='ddl' else ('commit',commit)
    event.listen(previous.engine,name,callback)
    try:
        with pytest.raises(RuntimeError,match='Injected migration'):previous.initialize()
    finally:event.remove(previous.engine,name,callback)
    assert snapshot(previous)==before
