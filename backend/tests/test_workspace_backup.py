"""Cold-backup boundary checks. No source service or remote engine is stopped."""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import sqlite3

import pytest

from ade2e import workspace_backup as tool


@pytest.fixture
def source(tmp_path, monkeypatch):
    source = tmp_path / "source"; source.mkdir()
    with sqlite3.connect(str(source / "platform.db")) as db:
        db.executescript("CREATE TABLE schema_versions(version INTEGER); INSERT INTO schema_versions VALUES(11);"
            "CREATE TABLE execution_jobs(status TEXT); CREATE TABLE execution_resources(state TEXT);"
            "INSERT INTO execution_resources VALUES('idle'); CREATE TABLE notes(id INTEGER, body TEXT);"
            "INSERT INTO notes VALUES(1,'preserve this evidence');")
    (source / "artifacts").mkdir(); (source / "artifacts/example.bin").write_bytes(b"original evidence")
    monkeypatch.setattr(tool.shutil, "which", lambda _: "/usr/sbin/lsof")
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=""))
    return source


def test_backup_verify_restore_preserve_source_and_full_inventory(source, tmp_path):
    before = tool.inventory(source)
    bundle = tmp_path / "backup"; restored = tmp_path / "restored"
    outcome = tool.backup(source, bundle, offline=True)
    assert outcome["status"] == "verified" and outcome["file_count"] == 2
    assert tool.inventory(source) == before
    restored_result = tool.restore(bundle, restored)
    assert restored_result["status"] == "restored" and not restored_result["services_started"]
    assert tool.database_info(restored / "platform.db") == tool.database_info(source / "platform.db")
    assert (restored / "artifacts/example.bin").read_bytes() == b"original evidence"


def test_committed_wal_is_recovered_only_in_private_copy(source, tmp_path):
    connection = sqlite3.connect(str(source / "platform.db"))
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("INSERT INTO notes VALUES(2,'committed WAL content')"); connection.commit()
    before = tool.inventory(source)
    try:
        # Fake lsof is deliberately injected; actual CLI refuses this open DB.
        tool.backup(source, tmp_path / "bundle", offline=True)
        assert tool.inventory(source) == before
        info = tool.verify(tmp_path / "bundle")
        assert info["database"]["table_counts"]["notes"] == 2
        assert not list((tmp_path / "bundle/payload").glob("platform.db-*"))
    finally:
        connection.close()


@pytest.mark.parametrize("mode", ["no_confirmation", "live_handle", "missing_lsof", "lsof_error"])
def test_offline_state_must_be_established(source, tmp_path, monkeypatch, mode):
    if mode == "live_handle": monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="123\n", stderr=""))
    if mode == "missing_lsof": monkeypatch.setattr(tool.shutil, "which", lambda _: None)
    if mode == "lsof_error": monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="permission error"))
    with pytest.raises(ValueError): tool.backup(source, tmp_path / "bundle", offline=mode != "no_confirmation")
    assert not (tmp_path / "bundle").exists()


@pytest.mark.parametrize("kind", ["tamper", "extra", "missing", "symlink", "manifest"])
def test_damaged_backup_never_creates_restore_target(source, tmp_path, kind):
    bundle = tmp_path / "bundle"; tool.backup(source, bundle, offline=True)
    artifact = bundle / "payload/artifacts/example.bin"
    if kind == "tamper": artifact.write_bytes(b"changed")
    if kind == "extra": (bundle / "payload/unlisted").write_bytes(b"extra")
    if kind == "missing": artifact.unlink()
    if kind == "symlink": artifact.unlink(); artifact.symlink_to(source / "artifacts/example.bin")
    if kind == "manifest": (bundle / "manifest.json").write_bytes(b"{}")
    with pytest.raises(ValueError): tool.restore(bundle, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize("statement", ["INSERT INTO execution_jobs VALUES('queued')", "INSERT INTO execution_jobs VALUES('running')", "UPDATE execution_resources SET state='quarantined'", "UPDATE schema_versions SET version=999"])
def test_active_jobs_reserved_engines_and_future_schemas_are_refused(source, tmp_path, statement):
    with sqlite3.connect(str(source / "platform.db")) as db: db.execute(statement)
    with pytest.raises(ValueError): tool.backup(source, tmp_path / "bundle", offline=True)
    assert not (tmp_path / "bundle").exists()


def test_existing_destination_is_never_replaced(source, tmp_path):
    existing = tmp_path / "existing"; existing.mkdir(); (existing / "keep").write_bytes(b"keep")
    with pytest.raises(ValueError): tool.backup(source, existing, offline=True)
    bundle = tmp_path / "bundle"; tool.backup(source, bundle, offline=True)
    with pytest.raises(ValueError): tool.restore(bundle, existing)
    assert (existing / "keep").read_bytes() == b"keep"


@pytest.mark.parametrize("key", ["../outside", "/absolute", "a/../outside", "a\\outside", "a//b", "./x"])
def test_path_escape_is_rejected(key):
    with pytest.raises(ValueError): tool.safe_key(key)


def test_source_changes_cancel_backup(source, tmp_path, monkeypatch):
    original = tool.assert_offline; calls = []
    def changed(path, **kwargs):
        original(path, **kwargs); calls.append(path)
        if len(calls) == 2: (path / "artifacts/example.bin").write_bytes(b"new source")
    monkeypatch.setattr(tool, "assert_offline", changed)
    with pytest.raises(ValueError, match="changed"): tool.backup(source, tmp_path / "bundle", offline=True)
    assert not (tmp_path / "bundle").exists()
