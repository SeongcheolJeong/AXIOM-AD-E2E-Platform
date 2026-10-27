from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import event
from ade2e.database import Database, Settings, SCHEMA_VERSION
from test_collection_migration import snapshot

TABLES=('verification_attempts','verification_plan_revisions','verification_plans')

def previous(tmp_path):
    db=Database(Settings(data_dir=tmp_path,seed_examples=False));db.initialize()
    with db.engine.begin() as c:
        for table in TABLES:c.exec_driver_sql('DROP TABLE '+table)
        c.exec_driver_sql('UPDATE schema_versions SET version=15')
        c.exec_driver_sql("INSERT INTO scenarios(id,category,payload) VALUES ('retained','lane_follow', ?)",('{ "note" : "한글 · exact bytes" }',))
    return db

def test_additive_migration_preserves_every_existing_row(tmp_path):
    db=previous(tmp_path);before=snapshot(db)
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:db.initialize(),range(2)))
    after=snapshot(db)
    assert set(after)-set(before)==set(TABLES)
    assert after['schema_versions']==[(SCHEMA_VERSION,)]
    assert all(after[t]==rows for t,rows in before.items() if t!='schema_versions')
    db.initialize();assert snapshot(db)==after
    db.engine.dispose()

def test_failed_commit_rolls_back_additive_tables(tmp_path):
    db=previous(tmp_path);before=snapshot(db)
    def refuse(connection):raise RuntimeError('injected commit failure')
    event.listen(db.engine,'commit',refuse)
    try:
        with pytest.raises(RuntimeError,match='injected'):db.initialize()
    finally:event.remove(db.engine,'commit',refuse)
    assert snapshot(db)==before
    db.engine.dispose()
