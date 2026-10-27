from ade2e.database import SCHEMA_VERSION
"""Schema 10 campaign storage survives the additive schema 11 column.

These are authored local SQLite fixtures, including reference-run evidence;
they neither use a user database nor claim native simulator validation.
The old table actually lacks execution_plan, not merely a version marker.
"""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event

from ade2e.service import canonical_bytes
from test_suites import baseline, campaign, finish, make_suite


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _snapshot(db):
    """Read raw SQL values, preserving JSON spelling and BLOB bytes."""
    with db.engine.connect() as connection:
        schema = connection.exec_driver_sql(
            "SELECT type,name,tbl_name,rootpage,sql FROM sqlite_master ORDER BY type,name"
        ).all()
        names = [row[1] for row in schema if row[0] == "table"]
        columns = {name: connection.exec_driver_sql(
            f"PRAGMA table_info({_quote(name)})"
        ).all() for name in names}
        rows = {name: sorted(connection.exec_driver_sql(
            f"SELECT * FROM {_quote(name)}"
        ).all(), key=repr) for name in names}
    return {"schema": schema, "columns": columns, "rows": rows}


def _without_evaluation_time(report):
    return {key: value for key, value in report.items() if key != "evaluated_at"}


@pytest.fixture
def schema10_campaigns(client):
    suite, *_ = make_suite(client, count=2, name="기존 캠페인 · schema10")
    completed = finish(client, campaign(client, suite, key="legacy-completed"))
    sealed = baseline(client, completed, key="legacy-baseline")
    queued = campaign(client, suite, key="legacy-queued")
    db = client.app.state.db
    # Different whitespace/unicode encoding deliberately detects JSON decode /
    # re-encode migrations even where model equality would conceal the change.
    with db.engine.begin() as connection:
        for campaign_id in (completed["id"], queued["id"]):
            raw = connection.exec_driver_sql(
                "SELECT suite_snapshot FROM test_campaigns WHERE id=?", (campaign_id,)
            ).scalar_one()
            connection.exec_driver_sql(
                "UPDATE test_campaigns SET suite_snapshot=? WHERE id=?",
                (json.dumps(json.loads(raw), ensure_ascii=False, indent=3), campaign_id),
            )
        connection.exec_driver_sql("ALTER TABLE test_campaigns DROP COLUMN execution_plan")
        connection.exec_driver_sql("UPDATE schema_versions SET version=10")
        # A historical deletion can leave the sequence above MAX(primary key).
        connection.exec_driver_sql("UPDATE sqlite_sequence SET seq=701 WHERE name='test_campaigns'")
        connection.exec_driver_sql("UPDATE sqlite_sequence SET seq=901 WHERE name='execution_jobs'")
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    old = _snapshot(db)
    assert "execution_plan" not in {column[1] for column in old["columns"]["test_campaigns"]}
    assert old["rows"]["schema_versions"] == [(10,)]
    files = {path: path.read_bytes() for path in db.artifact_dir.rglob("*") if path.is_file()}
    assert files and old["rows"]["test_baselines"] and old["rows"]["test_campaign_members"]
    return db, suite, completed, queued, sealed, old, files


def _assert_additive(db, before, files):
    after = _snapshot(db)
    assert after["rows"].keys() == before["rows"].keys()
    assert after["rows"]["schema_versions"] == [(SCHEMA_VERSION,)]
    for table, rows in before["rows"].items():
        if table == "schema_versions":
            continue
        if table == "test_campaigns":
            assert all(row[-1] is None for row in after["rows"][table])
            assert [row[:-1] for row in after["rows"][table]] == rows
            assert after["columns"][table][:-1] == before["columns"][table]
            added = after["columns"][table][-1]
            assert added[1:] == ("execution_plan", "JSON", 0, None, 0)
        else:
            assert after["rows"][table] == rows, table
            assert after["columns"][table] == before["columns"][table], table
    # Parent pages, indexes and child FK DDL retain their identity; only the
    # parent table SQL gains the nullable column.
    old_schema = {(row[0], row[1]): row for row in before["schema"]}
    new_schema = {(row[0], row[1]): row for row in after["schema"]}
    assert new_schema.keys() == old_schema.keys()
    for identity, row in old_schema.items():
        if identity == ("table", "test_campaigns"):
            assert new_schema[identity][:-1] == row[:-1]
        else:
            assert new_schema[identity] == row
    assert {path: path.read_bytes() for path in files} == files
    with db.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    return after


def test_schema10_adds_only_nullable_plan_preserving_raw_rows_legacy_hashes_and_evidence(client, schema10_campaigns):
    db, suite, completed, queued, sealed, before, files = schema10_campaigns
    statements = []
    def record(_connection, _cursor, statement, *_):
        statements.append(statement)
    event.listen(db.engine, "before_cursor_execute", record)
    try:
        db.initialize()
    finally:
        event.remove(db.engine, "before_cursor_execute", record)
    _assert_additive(db, before, files)
    assert sum("ALTER TABLE test_campaigns ADD COLUMN execution_plan JSON" in sql for sql in statements) == 1
    assert not any("DROP TABLE" in sql or "execution_jobs_v9" in sql for sql in statements)
    for original in (completed, queued):
        request = {"suite_id": suite["id"], "suite_revision": 1, "adapter_id": "reference", "label": original["label"]}
        with db.engine.connect() as connection:
            stored_hash, plan = connection.exec_driver_sql(
                "SELECT request_hash,execution_plan FROM test_campaigns WHERE id=?", (original["id"],)
            ).one()
        assert plan is None
        assert stored_hash == hashlib.sha256(canonical_bytes(request)).hexdigest()
        retried = client.post("/api/v1/campaigns", json=request,
                              headers={"Idempotency-Key": original["label"]})
        assert retried.status_code == 202, retried.text
        assert _without_evaluation_time(retried.json()) == _without_evaluation_time(original)
        assert "execution_plan" not in retried.json()
        assert all("expected_execution" not in case for case in retried.json()["cases"])
    assert client.get("/api/v1/baselines/" + sealed["id"]).json() == sealed
    # The next IDs must respect historical sequence gaps after migration.
    new = campaign(client, suite, key="after-migration")
    assert new["submission_seq"] == 702
    with db.engine.connect() as connection:
        sequences = connection.exec_driver_sql(
            "SELECT submission_seq FROM execution_jobs WHERE id IN (?,?) ORDER BY submission_seq",
            tuple(case["job_id"] for case in new["cases"]),
        ).scalars().all()
    assert sequences == [902, 903]


@pytest.mark.parametrize("boundary", ["after_alter", "after_version", "before_commit"])
def test_schema10_migration_failure_rolls_back_column_version_and_exact_storage(schema10_campaigns, boundary):
    db, _, _, _, _, before, files = schema10_campaigns
    def on_statement(_connection, _cursor, statement, *_):
        matched = (boundary == "after_alter" and statement.startswith("ALTER TABLE test_campaigns ADD COLUMN"))
        matched |= boundary == "after_version" and statement.startswith("UPDATE schema_versions SET version=")
        if matched:
            raise RuntimeError("Injected schema11 migration failure")
    def on_commit(connection):
        if connection.exec_driver_sql("SELECT version FROM schema_versions").scalar_one() == SCHEMA_VERSION:
            raise RuntimeError("Injected schema11 migration failure")
    name, callback = ("commit", on_commit) if boundary == "before_commit" else ("after_cursor_execute", on_statement)
    event.listen(db.engine, name, callback)
    try:
        with pytest.raises(RuntimeError, match="Injected schema11"):
            db.initialize()
    finally:
        event.remove(db.engine, name, callback)
    assert _snapshot(db) == before
    assert {path: path.read_bytes() for path in files} == files
    with db.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    db.initialize()
    _assert_additive(db, before, files)


def test_schema10_foreign_key_failure_does_not_publish_new_column_or_version(schema10_campaigns):
    db, _, _, _, _, _, files = schema10_campaigns
    with db.engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        connection.exec_driver_sql(
            "INSERT INTO test_campaign_members(campaign_id,case_key,job_id) VALUES ('missing-campaign','orphan','missing-job')"
        )
        connection.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.commit()
    before = _snapshot(db)
    with pytest.raises(RuntimeError, match="foreign-key integrity failed"):
        db.initialize()
    assert _snapshot(db) == before
    assert {path: path.read_bytes() for path in files} == files
    with db.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert len(connection.exec_driver_sql("PRAGMA foreign_key_check").all()) == 2


def test_schema10_concurrent_upgrade_adds_one_column_and_is_idempotent(schema10_campaigns):
    db, _, _, _, _, before, files = schema10_campaigns
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: db.initialize(), range(3)))
    after = _assert_additive(db, before, files)
    db.initialize()
    assert _snapshot(db) == after


def test_unknown_schema_does_not_apply_campaign_column_migration(schema10_campaigns):
    db, _, _, _, _, _, files = schema10_campaigns
    with db.engine.begin() as connection:
        connection.exec_driver_sql("UPDATE schema_versions SET version=999")
    before = _snapshot(db)
    with pytest.raises(RuntimeError, match="Unsupported database schema"):
        db.initialize()
    assert _snapshot(db) == before
    assert {path: path.read_bytes() for path in files} == files
