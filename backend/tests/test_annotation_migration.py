"""Schema13→14 adds two evidence tables; old rows and failed DDL remain intact."""
import pytest
from sqlalchemy import event
from ade2e.database import Database, Settings, SCHEMA_VERSION
from test_collection_migration import snapshot

TABLES = ('annotation_publications', 'annotation_tasks')


@pytest.fixture
def previous(tmp_path):
    db = Database(Settings(data_dir=tmp_path, seed_examples=False)); db.initialize()
    with db.engine.begin() as c:
        for table in TABLES: c.exec_driver_sql('DROP TABLE '+table)
        c.exec_driver_sql('UPDATE schema_versions SET version=13')
    yield db
    db.engine.dispose()


def test_additive_upgrade_and_repeated_start(previous):
    before = snapshot(previous); previous.initialize(); after = snapshot(previous)
    assert set(after)-set(before) == set(TABLES)
    assert after['schema_versions'] == [(SCHEMA_VERSION,)]
    assert all(after[k] == v for k,v in before.items() if k != 'schema_versions')
    previous.initialize(); assert snapshot(previous) == after


@pytest.mark.parametrize('boundary', ['ddl', 'commit'])
def test_failed_upgrade_rolls_back(previous, boundary):
    before = snapshot(previous)
    def ddl(_c,_cu,sql,*_):
        if sql.strip().startswith('CREATE TABLE annotation_tasks'): raise RuntimeError('Injected upgrade failure')
    def commit(c):
        if c.exec_driver_sql('SELECT version FROM schema_versions').scalar() == SCHEMA_VERSION: raise RuntimeError('Injected upgrade failure')
    name, fn = ('after_cursor_execute', ddl) if boundary == 'ddl' else ('commit', commit)
    event.listen(previous.engine,name,fn)
    try:
        with pytest.raises(RuntimeError,match='Injected'):previous.initialize()
    finally:event.remove(previous.engine,name,fn)
    assert snapshot(previous) == before
