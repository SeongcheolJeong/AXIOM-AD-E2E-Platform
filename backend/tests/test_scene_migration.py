"""Scene schema migration in temporary databases, with existing rows retained."""
from concurrent.futures import ThreadPoolExecutor

from ade2e.database import Database, Settings, SCHEMA_VERSION
from test_collection_migration import snapshot


def test_additive_scene_tables_keep_all_existing_records(tmp_path):
    db=Database(Settings(data_dir=tmp_path,seed_examples=False));db.initialize()
    tables=('scene_collection_selections','scene_tag_reviews','scene_indexes')
    with db.engine.begin() as connection:
        for table in tables:connection.exec_driver_sql('DROP TABLE '+table)
        connection.exec_driver_sql('UPDATE schema_versions SET version=14')
    before=snapshot(db)
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:db.initialize(),range(2)))
    after=snapshot(db)
    assert set(after)-set(before)==set(tables)
    assert after['schema_versions']==[(SCHEMA_VERSION,)]
    assert all(after[name]==rows for name,rows in before.items() if name!='schema_versions')
    db.initialize();assert snapshot(db)==after
    db.engine.dispose()
