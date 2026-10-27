"""Integration fault injection for adapter evidence persistence on local SQLite."""
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import event

from ade2e.adapters import Registry
from ade2e.database import Settings
from ade2e.main import create_app


def test_database_commit_failure_removes_uncommitted_artifact_bytes(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, seed_examples=False), Registry(load_plugins=False))
    with TestClient(app, raise_server_exceptions=False) as client:
        scenario = client.post("/api/v1/scenarios", json={"name": "Commit failure trial"}).json()

        def fail_commit(session):
            # Submission and claim now commit before execution. Inject only
            # after a final artifact was promoted in the publish transaction.
            if any(app.state.db.artifact_dir.glob("*.blob")):
                raise OSError("injected local DB commit failure")

        session_class = app.state.db.session.class_
        event.listen(session_class, "before_commit", fail_commit)
        try:
            response = client.post("/api/v1/runs", json={"scenario_id": scenario["id"]})
        finally:
            event.remove(session_class, "before_commit", fail_commit)
        assert response.status_code == 500
        assert client.get("/api/v1/runs").json() == []
        assert client.get("/api/v1/artifacts").json() == []
        assert not any(path.is_file() for path in app.state.db.artifact_dir.rglob("*"))


def test_partial_artifact_write_is_removed_on_storage_failure(tmp_path, monkeypatch):
    app = create_app(Settings(data_dir=tmp_path, seed_examples=False), Registry(load_plugins=False))
    real_open = Path.open

    class PartialFailure:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def write(self, content):
            self.stream.write(content[:5])
            self.stream.flush()
            raise OSError("injected disk full after partial artifact write")

        def __exit__(self, *_):
            self.stream.close()

    def fault_open(path, *args, **kwargs):
        stream = real_open(path, *args, **kwargs)
        return PartialFailure(stream) if path.suffix == ".blob" and args and args[0] == "xb" else stream

    with TestClient(app, raise_server_exceptions=False) as client:
        scenario = client.post("/api/v1/scenarios", json={"name": "Artifact failure trial"}).json()
        monkeypatch.setattr(Path, "open", fault_open)
        response = client.post("/api/v1/runs", json={"scenario_id": scenario["id"]})
        assert response.status_code == 500
        assert client.get("/api/v1/runs").json() == []
        assert client.get("/api/v1/artifacts").json() == []
        assert not any(path.is_file() for path in app.state.db.artifact_dir.rglob("*"))
