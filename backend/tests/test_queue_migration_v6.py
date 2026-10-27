"""Schema-5 queue DDL -> current schema preserves real relational storage boundaries.

The old queue is written explicitly, rather than creating current ORM tables
and changing only the version marker. Payloads are authored storage fixtures;
this test does not claim they are simulator or perception evidence.
"""

import multiprocessing
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import event, inspect

from ade2e import comparison_models, job_models, recording_models, run_recording_models, sensor_models, suite_models  # noqa: F401
from ade2e.database import Base, Database, Settings, SCHEMA_VERSION


# Captured from the shipped schema-5 SQLite DDL. Unlike schema 6, each
# simulation subject field is NOT NULL and no kind discriminator exists.
OLD_QUEUE_DDL = """
CREATE TABLE execution_jobs (
 submission_seq INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
 id VARCHAR(64) NOT NULL,
 idempotency_key VARCHAR(240) NOT NULL,
 request_hash VARCHAR(64) NOT NULL,
 request JSON NOT NULL,
 prepared JSON NOT NULL,
 input_hash VARCHAR(64) NOT NULL,
 scenario_id VARCHAR(64) NOT NULL,
 scenario_name VARCHAR(180) NOT NULL,
 scenario_revision INTEGER NOT NULL,
 adapter_id VARCHAR(180) NOT NULL,
 adapter_version VARCHAR(180) NOT NULL,
 status VARCHAR(24) NOT NULL,
 created_at VARCHAR(40) NOT NULL,
 updated_at VARCHAR(40) NOT NULL,
 started_at VARCHAR(40),
 dispatched_at VARCHAR(40),
 finished_at VARCHAR(40),
 run_id VARCHAR(64),
 reserved_run_id VARCHAR(64),
 legacy_key VARCHAR(200),
 error VARCHAR(2000),
 worker_id VARCHAR(180),
 owner_token VARCHAR(64),
 lease_expires_at VARCHAR(40),
 cancel_requested_at VARCHAR(40),
 cancellation_supported BOOLEAN NOT NULL,
 resource_required BOOLEAN NOT NULL,
 outcome_uncertain BOOLEAN NOT NULL,
 evidence_eligible BOOLEAN NOT NULL,
 failure_http_status INTEGER,
 UNIQUE (idempotency_key),
 FOREIGN KEY(scenario_id) REFERENCES scenarios (id),
 FOREIGN KEY(run_id) REFERENCES runs (id)
)
"""
NOW = "2026-09-13T01:02:03.456789+00:00"
OPAQUE = '{ "authored": true, "unicode": "보존", "value": 9007199254740993 }'
MANIFEST = b'\x00exact stored manifest bytes\xff'


def _insert(connection, table, values):
    names = ",".join(f'"{name}"' for name in values)
    placeholders = ",".join("?" for _ in values)
    connection.exec_driver_sql(f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})', tuple(values.values()))


def _job(sequence, job_id, **changes):
    values = {
        "submission_seq": sequence, "id": job_id, "idempotency_key": f"job:{job_id}",
        "request_hash": "a" * 64, "request": OPAQUE, "prepared": OPAQUE, "input_hash": "b" * 64,
        "scenario_id": "scn_preserved", "scenario_name": "원본 시나리오", "scenario_revision": 7,
        "adapter_id": "external-adapter", "adapter_version": "0.2.0", "status": "interrupted",
        "created_at": NOW, "updated_at": NOW, "started_at": NOW, "dispatched_at": NOW,
        "finished_at": NOW, "run_id": None, "reserved_run_id": "run_reserved",
        "legacy_key": "legacy:key", "error": "Original uncertain engine outcome",
        "worker_id": "worker-preserved", "owner_token": "owned-attempt-token",
        "lease_expires_at": NOW, "cancel_requested_at": NOW, "cancellation_supported": 0,
        "resource_required": 1, "outcome_uncertain": 1, "evidence_eligible": 1, "failure_http_status": 409,
    }
    values.update(changes)
    return values


def old_database(directory: Path, *, empty_queue=False) -> Database:
    db = Database(Settings(data_dir=directory, seed_examples=False))
    with db.engine.begin() as connection:
        connection.exec_driver_sql(OLD_QUEUE_DDL)
        connection.exec_driver_sql("CREATE UNIQUE INDEX ix_execution_jobs_id ON execution_jobs (id)")
        connection.exec_driver_sql("CREATE INDEX ix_job_queue ON execution_jobs (status, submission_seq)")
        connection.exec_driver_sql("CREATE INDEX ix_job_evidence ON execution_jobs (scenario_id, submission_seq)")
        # All non-queue schema-5 tables use their unchanged shipped schemas.
        for table in Base.metadata.sorted_tables:
            if table.name != "execution_jobs" and not table.name.startswith("perception_"):
                table.create(connection)
        _insert(connection, "schema_versions", {"version": 5})
        _insert(connection, "scenarios", {"id": "scn_preserved", "category": "lane_follow", "payload": OPAQUE})
        _insert(connection, "scenario_revisions", {"scenario_id": "scn_preserved", "revision": 7, "input_hash": "a" * 64, "payload": OPAQUE})
        _insert(connection, "runs", {"id": "run_preserved", "scenario_id": "scn_preserved", "created_at": NOW, "verdict": "failed", "payload": OPAQUE})
        _insert(connection, "artifacts", {"id": "art_preserved", "run_id": "run_preserved", "storage_key": "preserved.blob", "payload": OPAQUE})
        _insert(connection, "requirements", {"id": "req_preserved", "payload": OPAQUE})
        _insert(connection, "requirement_scenarios", {"requirement_id": "req_preserved", "scenario_id": "scn_preserved"})
        _insert(connection, "idempotency_keys", {"key": "old-run-key", "request_hash": "a" * 64, "run_id": "run_preserved"})
        _insert(connection, "recordings", {"id": "rec_preserved", "import_key": "c" * 64, "created_at": NOW,
                "sha256": "d" * 64, "storage_key": "preserved.mcap", "index_storage_key": "preserved.payload", "payload": OPAQUE})
        _insert(connection, "recording_schemas", {"recording_id": "rec_preserved", "schema_id": 1, "payload": OPAQUE})
        _insert(connection, "recording_channels", {"recording_id": "rec_preserved", "channel_id": 2, "schema_ref": 1, "payload": OPAQUE})
        _insert(connection, "recording_messages", {"recording_id": "rec_preserved", "ordinal": 13, "channel_id": 2,
                "log_time_key": "18446744073709551615", "publish_time_key": "18446744073709551614", "sequence": 42,
                "payload_offset": 0, "payload_length": 7, "sha256": "e" * 64})
        _insert(connection, "recording_origins", {"recording_id": "rec_preserved", "run_id": "run_preserved",
                "artifact_id": "art_preserved", "created_at": NOW, "definition_hash": "e" * 64, "payload": OPAQUE})
        _insert(connection, "sensor_rigs", {"id": "rig_preserved", "revision": 1, "created_at": NOW})
        _insert(connection, "sensor_rig_revisions", {"rig_id": "rig_preserved", "revision": 1, "created_at": NOW,
                "manifest": MANIFEST, "manifest_hash": "a" * 64, "manifest_size_bytes": len(MANIFEST)})
        _insert(connection, "recording_sensor_bindings", {"recording_id": "rec_preserved", "revision": 1, "created_at": NOW})
        _insert(connection, "recording_sensor_binding_revisions", {"recording_id": "rec_preserved", "revision": 1,
                "rig_id": "rig_preserved", "rig_revision": 1, "created_at": NOW, "manifest": MANIFEST,
                "manifest_hash": "a" * 64, "manifest_size_bytes": len(MANIFEST)})
        _insert(connection, "recording_sensor_binding_channels", {"recording_id": "rec_preserved", "revision": 1, "channel_id": 2, "sensor_key": "camera"})
        _insert(connection, "test_suites", {"id": "suite_preserved", "revision": 3, "created_at": NOW, "updated_at": NOW})
        _insert(connection, "test_suite_revisions", {"suite_id": "suite_preserved", "revision": 3, "definition_hash": "a" * 64, "payload": OPAQUE})
        _insert(connection, "test_campaigns", {"submission_seq": 13, "id": "campaign_preserved", "idempotency_key": "campaign:key",
                "request_hash": "a" * 64, "suite_id": "suite_preserved", "suite_revision": 3,
                "label": "Authored migration campaign", "created_at": NOW, "suite_snapshot": OPAQUE, "adapter_snapshot": OPAQUE})
        _insert(connection, "test_baselines", {"id": "baseline_preserved", "idempotency_key": "baseline:key", "request_hash": "a" * 64,
                "campaign_id": "campaign_preserved", "suite_id": "suite_preserved", "created_at": NOW,
                "manifest_hash": "a" * 64, "manifest_size_bytes": len(MANIFEST), "manifest": MANIFEST, "summary": OPAQUE})
        _insert(connection, "test_comparisons", {"id": "comparison_preserved", "idempotency_key": "compare:key", "request_hash": "a" * 64,
                "campaign_id": "campaign_preserved", "baseline_id": "baseline_preserved", "suite_id": "suite_preserved", "created_at": NOW,
                "manifest_hash": "a" * 64, "manifest_size_bytes": len(MANIFEST), "manifest": MANIFEST, "summary": OPAQUE})
        if not empty_queue:
            _insert(connection, "execution_jobs", _job(17, "job_completed", status="completed", run_id="run_preserved",
                    error=None, owner_token=None, lease_expires_at=None, cancellation_supported=1, resource_required=0,
                    outcome_uncertain=0, failure_http_status=None))
            _insert(connection, "execution_jobs", _job(31, "job_interrupted"))
            _insert(connection, "execution_job_events", {"sequence": 9, "job_id": "job_completed", "at": NOW, "type": "completed", "message": "Preserve exact old event"})
            _insert(connection, "execution_job_events", {"sequence": 19, "job_id": "job_interrupted", "at": NOW, "type": "interrupted", "message": "Unknown external outcome"})
            _insert(connection, "execution_resources", {"adapter_id": "external-adapter", "state": "reconcile_required",
                    "job_id": "job_interrupted", "owner_token": "owned-attempt-token", "updated_at": NOW, "note": "Do not automatically dispatch"})
            _insert(connection, "execution_workers", {"id": "worker-preserved", "heartbeat_at": NOW, "state": "stopped", "job_id": "job_interrupted"})
            for key, job_id in (("completed-case", "job_completed"), ("uncertain-case", "job_interrupted")):
                _insert(connection, "test_campaign_members", {"campaign_id": "campaign_preserved", "case_key": key, "job_id": job_id})
        _insert(connection, "execution_jobs", _job(777, "deleted-high-sequence"))
        connection.exec_driver_sql("DELETE FROM execution_jobs WHERE submission_seq=777")
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    (db.artifact_dir / "preserved.blob").write_bytes(MANIFEST)
    (db.recording_dir / "preserved.mcap").write_bytes(b"original recording bytes")
    (db.recording_dir / "preserved.payload").write_bytes(b"message")
    return db


def storage_snapshot(db):
    with db.engine.connect() as connection:
        tables = inspect(connection).get_table_names()
        data = {table: sorted(connection.exec_driver_sql(f'SELECT * FROM "{table}"').all(), key=repr) for table in tables}
        schema = connection.exec_driver_sql("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").all()
        sequences = connection.exec_driver_sql("SELECT name,seq FROM sqlite_sequence ORDER BY name").all()
    return data, schema, sequences


def test_real_schema5_rebuild_preserves_every_old_column_and_business_row(tmp_path):
    db = old_database(tmp_path)
    before, _, sequences = storage_snapshot(db)
    files = {path: path.read_bytes() for directory in (db.artifact_dir, db.recording_dir) for path in directory.iterdir()}
    with db.engine.connect() as connection:
        old_columns = [item["name"] for item in inspect(connection).get_columns("execution_jobs")]
        assert "kind" not in old_columns
        assert all(not column["nullable"] for column in inspect(connection).get_columns("execution_jobs")
                   if column["name"] in {"scenario_id", "scenario_name", "scenario_revision"})
    db.initialize()
    with db.engine.connect() as connection:
        after_tables = set(inspect(connection).get_table_names())
        assert set(before) <= after_tables and "perception_jobs" in after_tables
        names = ",".join(f'"{name}"' for name in old_columns)
        assert sorted(connection.exec_driver_sql(f"SELECT {names} FROM execution_jobs").all(), key=repr) == before["execution_jobs"]
        assert connection.exec_driver_sql("SELECT kind FROM execution_jobs ORDER BY submission_seq").all() == [("simulation",), ("simulation",)]
        for table, rows in before.items():
            if table not in {"execution_jobs", "schema_versions"}:
                actual = sorted(connection.exec_driver_sql(f'SELECT * FROM "{table}"').all(), key=repr)
                if table == 'team_projects':
                    # Global model registration makes this future table exist
                    # empty in the artificial schema5 fixture. Schema17 adds
                    # the explicit default project without altering old rows.
                    assert all(row in actual for row in rows), table
                    assert all(row in rows or row[0] == 'default' for row in actual), table
                else:
                    assert actual == rows, table
        assert connection.exec_driver_sql("SELECT version FROM schema_versions").all() == [(SCHEMA_VERSION,)]
        assert connection.exec_driver_sql("SELECT name,seq FROM sqlite_sequence ORDER BY name").all() == sequences
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert {index["name"] for index in inspect(connection).get_indexes("execution_jobs")} >= {"ix_execution_jobs_id", "ix_job_queue", "ix_job_evidence"}
        for table in ("execution_job_events", "execution_resources", "execution_workers", "test_campaign_members"):
            assert "execution_jobs_v7" not in connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name=?", (table,)).scalar()
    assert all(path.read_bytes() == content for path, content in files.items())
    unchanged = storage_snapshot(db)
    db.initialize()
    assert storage_snapshot(db) == unchanged
    db.engine.dispose()


@pytest.mark.parametrize("empty_queue", [False, True])
def test_deleted_sequence_high_watermark_survives_even_empty_queue(tmp_path, empty_queue):
    db = old_database(tmp_path, empty_queue=empty_queue)
    db.initialize()
    with db.engine.begin() as connection:
        value = _job(None, "first-perception", scenario_id=None, scenario_name=None, scenario_revision=None, run_id=None)
        value["kind"] = "perception"
        _insert(connection, "execution_jobs", value)
        assert connection.exec_driver_sql("SELECT submission_seq FROM execution_jobs WHERE id='first-perception'").scalar() == 778
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    db.engine.dispose()


@pytest.mark.parametrize("point", ["after_drop", "before_commit", "foreign_key_check"])
def test_migration_failure_restores_old_parent_indexes_children_and_version(tmp_path, point):
    db = old_database(tmp_path)
    if point == "foreign_key_check":
        # An existing corrupt child must cause an explicit failed migration,
        # without dropping its old evidence or publishing the new version.
        with sqlite3.connect(tmp_path / "platform.db") as connection:
            connection.execute("INSERT INTO execution_job_events(sequence,job_id,at,type,message) VALUES(99,'missing-job',?,?,?)", (NOW, "bad-old-reference", "preserve for operator"))
    before = storage_snapshot(db)
    def after_statement(_conn, _cursor, statement, _parameters, _context, _many):
        if statement == "DROP TABLE execution_jobs":
            raise RuntimeError("Injected failure after dropping original queue")
    def before_commit(_conn):
        raise RuntimeError("Injected failure before publishing migration")
    if point == "after_drop":
        event.listen(db.engine, "after_cursor_execute", after_statement)
    elif point == "before_commit":
        # The initializer commits PRAGMA configuration before the transaction;
        # inject only when the new discriminator has actually been created.
        def commit_when_migrated(connection):
            if "kind" in {column["name"] for column in inspect(connection).get_columns("execution_jobs")}:
                before_commit(connection)
        event.listen(db.engine, "commit", commit_when_migrated)
    try:
        with pytest.raises(RuntimeError, match="Injected failure|foreign-key integrity"):
            db.initialize()
    finally:
        if point == "after_drop":
            event.remove(db.engine, "after_cursor_execute", after_statement)
        elif point == "before_commit":
            event.remove(db.engine, "commit", commit_when_migrated)
    assert storage_snapshot(db) == before
    with db.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    if point != "foreign_key_check":
        db.initialize()
        with db.engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT version FROM schema_versions").scalar() == SCHEMA_VERSION
    db.engine.dispose()


def _initialize_process(directory, barrier, results):
    db = Database(Settings(data_dir=Path(directory), seed_examples=False))
    try:
        barrier.wait(timeout=15)
        db.initialize()
        with db.engine.connect() as connection:
            results.put(("ok", connection.exec_driver_sql("SELECT version FROM schema_versions").scalar(),
                         connection.exec_driver_sql("SELECT count(*) FROM execution_jobs").scalar(),
                         connection.exec_driver_sql("PRAGMA foreign_key_check").all()))
    except BaseException as exc:
        results.put(("error", repr(exc)))
    finally:
        db.engine.dispose()


def test_independent_processes_migrate_schema5_only_once(tmp_path):
    db = old_database(tmp_path)
    before = storage_snapshot(db)[0]
    db.engine.dispose()
    context = multiprocessing.get_context("spawn")
    barrier, results = context.Barrier(3), context.Queue()
    processes = [context.Process(target=_initialize_process, args=(str(tmp_path), barrier, results)) for _ in range(3)]
    for process in processes:
        process.start()
    try:
        assert [results.get(timeout=30) for _ in processes] == [("ok", SCHEMA_VERSION, 2, [])] * 3
    finally:
        for process in processes:
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
        results.close()
    after = storage_snapshot(db)[0]
    assert after["test_campaign_members"] == before["test_campaign_members"]
    assert after["execution_job_events"] == before["execution_job_events"]
    assert after["execution_resources"] == before["execution_resources"]
    db.engine.dispose()


@pytest.mark.parametrize("version", [0, SCHEMA_VERSION + 1, 999])
def test_unknown_version_rejects_before_any_schema_or_business_mutation(tmp_path, version):
    db = old_database(tmp_path)
    with db.engine.begin() as connection:
        connection.exec_driver_sql("UPDATE schema_versions SET version=?", (version,))
    before = storage_snapshot(db)
    with pytest.raises(RuntimeError, match="Unsupported database schema"):
        db.initialize()
    assert storage_snapshot(db) == before
    with db.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    db.engine.dispose()
