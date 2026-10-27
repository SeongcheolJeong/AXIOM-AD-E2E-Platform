"""Filesystem evidence follows the transaction that created it."""

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from ade2e.adapters import Registry
from ade2e.artifact_transactions import track_created_artifact
from ade2e.database import Database, Settings
from ade2e.main import create_app


def _artifact_files(directory):
    # Empty staging directories are infrastructure; any stranded payload,
    # including one under a staging attempt, must still fail the assertion.
    return {str(path.relative_to(directory)): path.read_bytes() for path in directory.rglob("*") if path.is_file()}


@pytest.mark.parametrize("failure_stage", ["before_commit", "connection_commit"])
def test_commit_failure_removes_only_uncommitted_artifacts(tmp_path, failure_stage):
    app = create_app(Settings(data_dir=tmp_path, seed_examples=False), Registry(load_plugins=False))
    with TestClient(app, raise_server_exceptions=False) as client:
        scenario = client.post("/api/v1/scenarios", json={"name": "Rollback boundary"}).json()
        committed = client.post("/api/v1/runs", json={"scenario_id": scenario["id"]}).json()
        committed_artifact = committed["artifacts"][0]
        original_files = _artifact_files(app.state.db.artifact_dir)

        def fail_commit(*_):
            # Queue submission/claim also commit. Fail only after publication
            # has promoted a new final artifact, so rollback is really tested.
            if any(path.name not in original_files for path in app.state.db.artifact_dir.glob("*.blob")):
                raise OSError("Injected database commit failure")

        target, event_name = ((app.state.db.session.class_, "before_commit") if failure_stage == "before_commit"
                              else (app.state.db.engine, "commit"))
        event.listen(target, event_name, fail_commit)
        try:
            response = client.post("/api/v1/runs", json={"scenario_id": scenario["id"]})
        finally:
            event.remove(target, event_name, fail_commit)
        assert response.status_code == 500
        assert [run["id"] for run in client.get("/api/v1/runs").json()] == [committed["id"]]
        assert [artifact["id"] for artifact in client.get("/api/v1/artifacts").json()] == [committed_artifact["id"]]
        assert _artifact_files(app.state.db.artifact_dir) == original_files
        download = client.get(committed_artifact["download_url"])
        assert download.status_code == 200
        assert hashlib.sha256(download.content).hexdigest() == committed_artifact["sha256"]
        # A subsequent successful transaction must still persist its own files.
        recovered = client.post("/api/v1/runs", json={"scenario_id": scenario["id"]})
        assert recovered.status_code == 201
        assert len(_artifact_files(app.state.db.artifact_dir)) == 2


@pytest.mark.parametrize("write_failure", ["raises", "short_count"])
def test_partial_write_does_not_remove_previously_committed_artifact(tmp_path, monkeypatch, write_failure):
    app = create_app(Settings(data_dir=tmp_path, seed_examples=False), Registry(load_plugins=False))
    original_open = Path.open

    class BrokenStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def write(self, content):
            self.stream.write(content[:7])
            self.stream.flush()
            if write_failure == "short_count":
                return 7
            raise OSError("Injected disk full")

        def __exit__(self, *_):
            self.stream.close()

    def fail_write(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return BrokenStream(stream) if args and args[0] == "xb" and path.suffix == ".blob" else stream

    with TestClient(app, raise_server_exceptions=False) as client:
        scenario = client.post("/api/v1/scenarios", json={"name": "Partial write boundary"}).json()
        committed = client.post("/api/v1/runs", json={"scenario_id": scenario["id"]}).json()
        original_files = _artifact_files(app.state.db.artifact_dir)
        with monkeypatch.context() as patch:
            patch.setattr(Path, "open", fail_write)
            response = client.post("/api/v1/runs", json={"scenario_id": scenario["id"]})
        assert response.status_code == 500
        assert [run["id"] for run in client.get("/api/v1/runs").json()] == [committed["id"]]
        assert _artifact_files(app.state.db.artifact_dir) == original_files


def test_failed_exclusive_creation_never_deletes_an_existing_file(tmp_path, monkeypatch):
    from ade2e import service

    app = create_app(Settings(data_dir=tmp_path, seed_examples=False), Registry(load_plugins=False))
    with TestClient(app, raise_server_exceptions=False) as client:
        scenario = client.post("/api/v1/scenarios", json={"name": "Exclusive creation boundary"}).json()
        existing = app.state.db.artifact_dir / "art_existing.blob"
        existing.write_bytes(b"existing unrelated evidence")
        original_uid = service.uid
        monkeypatch.setattr(service, "uid", lambda prefix: "art_existing" if prefix == "art" else original_uid(prefix))
        response = client.post("/api/v1/runs", json={"scenario_id": scenario["id"]})
        assert response.status_code == 500
        assert existing.read_bytes() == b"existing unrelated evidence"
        assert client.get("/api/v1/runs").json() == []


def _new_file(session, path):
    with path.open("xb") as stream:
        track_created_artifact(session, path)
        stream.write(b"new evidence")


def test_session_close_removes_files_before_commit(tmp_path):
    db = Database(Settings(data_dir=tmp_path, seed_examples=False))
    path = db.artifact_dir / "uncommitted.blob"
    with db.session() as session:
        session.begin()
        _new_file(session, path)
        assert path.exists()
    assert not path.exists()
    db.engine.dispose()


def test_released_savepoint_files_follow_outer_rollback(tmp_path):
    db = Database(Settings(data_dir=tmp_path, seed_examples=False))
    parent_file, child_file = db.artifact_dir / "parent.blob", db.artifact_dir / "child.blob"
    with db.session() as session:
        session.begin()
        _new_file(session, parent_file)
        with session.begin_nested():
            _new_file(session, child_file)
        assert parent_file.exists() and child_file.exists()
        session.rollback()
    assert not parent_file.exists() and not child_file.exists()
    db.engine.dispose()


def test_savepoint_rollback_preserves_parent_successful_commit(tmp_path):
    db = Database(Settings(data_dir=tmp_path, seed_examples=False))
    parent_file, child_file = db.artifact_dir / "parent.blob", db.artifact_dir / "child.blob"
    with db.session.begin() as session:
        _new_file(session, parent_file)
        with pytest.raises(RuntimeError):
            with session.begin_nested():
                _new_file(session, child_file)
                raise RuntimeError("Abort savepoint")
        assert parent_file.exists() and not child_file.exists()
    assert parent_file.read_bytes() == b"new evidence"
    assert not child_file.exists()
    db.engine.dispose()
