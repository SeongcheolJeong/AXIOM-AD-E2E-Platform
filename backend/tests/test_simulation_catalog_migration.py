"""Schema 18 -> 19 adds a catalog table without rewriting sealed evidence."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import event
from ade2e.database import Database, Settings, SCHEMA_VERSION
from ade2e.scenario_v2 import save_scenario
from ade2e.scenario_v2_schemas import ScenarioV2Create
from ade2e.scenario_v2_templates import templates
from test_collection_migration import snapshot


def previous(tmp_path):
    db=Database(Settings(data_dir=tmp_path,seed_examples=False));db.initialize()
    scenario=save_scenario(db,ScenarioV2Create(scenario=templates()[0]['scenario']),'pre-migration-scenario')
    with db.engine.begin() as connection:
        connection.exec_driver_sql('DROP TABLE simulation_catalog_revisions')
        connection.exec_driver_sql('UPDATE schema_versions SET version=18')
        connection.exec_driver_sql("INSERT INTO scenarios(id,category,payload) VALUES ('retained','lane_follow', ?)",
                                   ('{ "note" : "원본 bytes unchanged" }',))
    return db,scenario


def test_concurrent_additive_catalog_migration_preserves_all_existing_rows(tmp_path):
    db,scenario=previous(tmp_path);before=snapshot(db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _:db.initialize(),range(2)))
    after=snapshot(db)
    assert set(after)-set(before)=={'simulation_catalog_revisions'}
    assert after['schema_versions']==[(SCHEMA_VERSION,)]
    assert all(after[name]==rows for name,rows in before.items() if name!='schema_versions')
    assert any(scenario['manifest_hash'] in row for row in after['scenario_v2_revisions'])
    db.initialize();assert snapshot(db)==after
    db.engine.dispose()


def test_failed_catalog_migration_rolls_back_table_and_version(tmp_path):
    db,_=previous(tmp_path);before=snapshot(db)
    def refuse(connection):raise RuntimeError('injected catalog commit failure')
    event.listen(db.engine,'commit',refuse)
    try:
        with pytest.raises(RuntimeError,match='injected catalog'):
            db.initialize()
    finally:
        event.remove(db.engine,'commit',refuse)
    assert snapshot(db)==before
    db.engine.dispose()
