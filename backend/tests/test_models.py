"""Actual streamed bytes, immutable model authoring, and integrity boundaries."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json

from fastapi import APIRouter, HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select
from starlette.requests import ClientDisconnect, Request

from ade2e import models
from ade2e.adapters import Registry
from ade2e.main import create_app
from ade2e.model_models import ModelArtifactRow, ModelRevisionRow, ModelRow
from ade2e.model_schemas import ModelCreate, ModelDetail
from ade2e.service import canonical_bytes, digest

PREFIX = "/api/v1/perception"
# Registration deliberately accepts opaque original bytes; only the optional
# runtime can claim graph compatibility. These bytes are not a detector fixture.
OPAQUE = b"opaque model registration bytes\x00\xff"


def upload(client, content=OPAQUE, name="candidate.onnx", **headers):
    return client.post(f"{PREFIX}/model-artifacts", params={"name": name}, content=content,
                       headers={"Content-Type": "application/octet-stream", **headers})


def definition(artifact, **values):
    return {"name": "Declared candidate", "artifact_id": artifact["id"], "profile": "opencv_yolox_rgb_v1",
            "input_width": 640, "input_height": 640, "labels": ["person", "car"],
            "note": "Declared weights; runtime compatibility not evaluated", **values}


def create(client, artifact=None, **values):
    artifact = artifact or upload(client).json()
    response = client.post(f"{PREFIX}/models", json=definition(artifact, **values))
    assert response.status_code == 201, response.text
    return response.json()


def test_original_bytes_repeat_name_and_exact_download(client):
    first, repeat = upload(client), upload(client)
    assert first.status_code == 201, first.text
    assert repeat.status_code == 200, repeat.text
    artifact = first.json()
    assert repeat.json() == artifact
    assert artifact["size_bytes"] == len(OPAQUE)
    assert artifact["sha256"] == hashlib.sha256(OPAQUE).hexdigest()
    downloaded = client.get(artifact["download_url"])
    assert downloaded.content == OPAQUE and downloaded.headers["x-content-sha256"] == artifact["sha256"]
    assert int(downloaded.headers["content-length"]) == len(OPAQUE)
    assert "nosniff" == downloaded.headers["x-content-type-options"]
    other_name = upload(client, name="different-name.onnx")
    changed = upload(client, content=OPAQUE + b"changed")
    assert other_name.status_code == changed.status_code == 201
    assert len({artifact["id"], other_name.json()["id"], changed.json()["id"]}) == 3
    with client.app.state.db.session() as session:
        assert session.scalar(select(func.count()).select_from(ModelArtifactRow)) == 3
    assert list(client.app.state.db.model_staging_dir.iterdir()) == []


def test_parallel_duplicate_upload_has_one_owner(client):
    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(lambda _: upload(client), range(4)))
    assert sorted(response.status_code for response in responses) == [200, 200, 200, 201]
    assert len({response.json()["id"] for response in responses}) == 1
    assert len(list(client.app.state.db.model_dir.iterdir())) == 1
    assert list(client.app.state.db.model_staging_dir.iterdir()) == []


def test_pinned_revision_export_and_ordered_label_map(client):
    artifact = upload(client).json()
    first = create(client, artifact, profile="future_unknown_profile", source="declared://source",
                   weights_license="unknown", code_license="Apache-2.0")
    assert first["label_map_hash"] == digest(["person", "car"])
    assert ModelDetail.model_validate(first).model_dump(mode="json") == first
    content = client.get(first["download_url"]).content
    assert len(content) == first["manifest_size_bytes"]
    assert hashlib.sha256(content).hexdigest() == first["manifest_hash"]
    snapshot = json.loads(content)
    assert canonical_bytes(snapshot) == content
    assert snapshot["artifact"] == artifact and "manifest_hash" not in snapshot
    assert "verified" not in json.dumps(snapshot).lower()
    revision = client.post(f"{PREFIX}/models/{first['id']}/revisions", json=definition(
        artifact, name="New declared name", labels=["car", "person"], expected_revision=1))
    assert revision.status_code == 201, revision.text
    second = revision.json()
    assert second["revision"] == 2 and second["manifest_hash"] != first["manifest_hash"]
    assert second["label_map_hash"] != first["label_map_hash"]
    assert client.get(first["download_url"]).content == content
    assert client.get(f"{PREFIX}/models/{first['id']}", params={"revision": 1}).json() == first
    assert client.get(f"{PREFIX}/models/{first['id']}").json() == second
    assert client.get(f"{PREFIX}/models/{first['id']}/revisions").json() == [second, first]
    assert client.get(f"{PREFIX}/models").json() == [second]
    assert client.get(f"{PREFIX}/models/{first['id']}", params={"revision": 99}).status_code == 404


def test_concurrent_revision_conflict_never_overwrites(client):
    artifact = upload(client).json()
    first = create(client, artifact)
    before = client.get(first["download_url"]).content
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda name: client.post(f"{PREFIX}/models/{first['id']}/revisions",
            json=definition(artifact, name=name, expected_revision=1)), ["first editor", "second editor"]))
    assert sorted(response.status_code for response in responses) == [201, 409]
    history = client.get(f"{PREFIX}/models/{first['id']}/revisions").json()
    assert [item["revision"] for item in history] == [2, 1]
    assert client.get(first["download_url"]).content == before


@pytest.mark.parametrize("changes", [
    {"labels": []}, {"labels": ["car", " car "]}, {"labels": [""]}, {"labels": ["a" * 129]},
    {"labels": [str(i) for i in range(257)]}, {"input_width": 31}, {"input_height": 1281},
    {"input_width": True}, {"input_width": 640.0}, {"note": "   "}, {"name": ""},
    {"source_revision": "a" * 301}, {"profile": "../../model"}, {"weights_verified": True},
])
def test_invalid_definition_fails_before_authoring(client, changes):
    artifact = upload(client).json()
    response = client.post(f"{PREFIX}/models", json=definition(artifact, **changes))
    assert response.status_code == 422, response.text
    assert client.get(f"{PREFIX}/models").json() == []


@pytest.mark.parametrize("name", ["../model.onnx", "folder/model.onnx", "folder\\model.onnx", "foo\nbar.onnx", "   "])
def test_upload_filename_never_becomes_storage_path(client, name):
    assert upload(client, name=name).status_code == 422
    assert list(client.app.state.db.model_dir.iterdir()) == []
    assert list(client.app.state.db.model_staging_dir.iterdir()) == []


@pytest.mark.parametrize("headers,content,status", [
    ({"Content-Type": "application/json"}, b"bytes", 415),
    ({}, b"", 422), ({"Content-Length": "9" * 5000}, b"x", 413),
    ({"Content-Length": "-1"}, b"x", 422), ({"Content-Length": "100"}, b"x", 422),
])
def test_upload_headers_empty_and_declared_length(client, headers, content, status):
    assert upload(client, content=content, **headers).status_code == status
    assert list(client.app.state.db.model_dir.iterdir()) == []
    assert list(client.app.state.db.model_staging_dir.iterdir()) == []


def _stream_request(messages):
    async def receive():
        message = next(messages)
        if isinstance(message, BaseException):
            raise message
        return message
    return Request({"type": "http", "method": "POST", "path": f"{PREFIX}/model-artifacts",
                    "headers": [(b"content-type", b"application/octet-stream")]}, receive)


def _upload_endpoint(client):
    # Exercise the exact handler with a controlled ASGI receive stream. Router
    # composition is separately covered through actual authenticated HTTP calls.
    api = APIRouter(prefix="/api/v1")
    models.register_model_routes(api, client.app.state.db)
    return next(route.endpoint for route in api.routes if route.path == f"{PREFIX}/model-artifacts")


@pytest.mark.parametrize("ending", ["overflow", "disconnect", "cancel"])
def test_streamed_limits_and_interruption_remove_owned_staging(client, monkeypatch, ending):
    monkeypatch.setattr(models, "MAX_MODEL_BYTES", 10)
    tail = {"overflow": {"type": "http.request", "body": b"123456", "more_body": False},
            "disconnect": {"type": "http.disconnect"}, "cancel": asyncio.CancelledError()}[ending]
    messages = iter([{"type": "http.request", "body": b"123456", "more_body": True}, tail])
    exception = {"overflow": HTTPException, "disconnect": ClientDisconnect, "cancel": asyncio.CancelledError}[ending]
    with pytest.raises(exception) as caught:
        asyncio.run(_upload_endpoint(client)(_stream_request(messages), name="stream.onnx"))
    if ending == "overflow":
        assert caught.value.status_code == 413
    assert list(client.app.state.db.model_dir.iterdir()) == []
    assert list(client.app.state.db.model_staging_dir.iterdir()) == []


def test_chunked_upload_at_limit_hashes_actual_stream(client, monkeypatch):
    monkeypatch.setattr(models, "MAX_MODEL_BYTES", 10)
    messages = iter([{"type": "http.request", "body": b"1234", "more_body": True},
                     {"type": "http.request", "body": b"567890", "more_body": False}])
    response = asyncio.run(_upload_endpoint(client)(_stream_request(messages), name="stream.onnx"))
    assert response.status_code == 201
    artifact = json.loads(response.body)
    assert artifact["sha256"] == hashlib.sha256(b"1234567890").hexdigest()
    assert client.get(artifact["download_url"]).content == b"1234567890"


def test_partial_writes_are_completed_and_zero_progress_rejected():
    class Partial(io.BytesIO):
        def write(self, value):
            return super().write(value[:3])
    output = Partial()
    models._write_exact(output, OPAQUE)
    assert output.getvalue() == OPAQUE
    class NoProgress(io.BytesIO):
        def write(self, _):
            return None
    with pytest.raises(OSError, match="progress"):
        models._write_exact(NoProgress(), OPAQUE)


@pytest.mark.parametrize("failure", ["commit", "write"])
def test_failed_upload_rolls_back_only_new_files(settings, monkeypatch, failure):
    app = create_app(settings, Registry(load_plugins=False))
    with TestClient(app, raise_server_exceptions=False) as client:
        committed = upload(client).json()
        initial = set(app.state.db.model_dir.iterdir())
        def fail_commit(session):
            if session.bind is app.state.db.engine and "ade2e.artifact_transactions" in session.info:
                raise OSError("Injected model commit failure")
        def fail_write(stream, data):
            stream.write(data[:3])
            raise OSError("Injected model write failure")
        if failure == "commit":
            event.listen(app.state.db.session.class_, "before_commit", fail_commit)
        else:
            monkeypatch.setattr(models, "_write_exact", fail_write)
        try:
            response = upload(client, content=b"other uncommitted bytes")
            assert response.status_code == 500
        finally:
            if failure == "commit":
                event.remove(app.state.db.session.class_, "before_commit", fail_commit)
        assert set(app.state.db.model_dir.iterdir()) == initial
        assert list(app.state.db.model_staging_dir.iterdir()) == []
        assert client.get(committed["download_url"]).content == OPAQUE
        with app.state.db.session() as session:
            assert session.scalar(select(func.count()).select_from(ModelArtifactRow)) == 1


@pytest.mark.parametrize("damage", ["same_size", "truncated", "missing", "symlink"])
def test_corrupted_artifact_is_never_executed_downloaded_or_replaced(client, tmp_path, damage):
    artifact = upload(client).json()
    path = models.verified_model_path(client.app.state.db, artifact["id"])
    if damage == "same_size":
        path.write_bytes(b"X" * len(OPAQUE))
    elif damage == "truncated":
        path.write_bytes(OPAQUE[:4])
    elif damage == "missing":
        path.unlink()
    else:
        original = tmp_path / "outside.onnx"
        original.write_bytes(OPAQUE)
        path.unlink()
        path.symlink_to(original)
    assert client.get(artifact["download_url"]).status_code == 409
    assert upload(client).status_code == 409
    assert client.post(f"{PREFIX}/models", json=definition(artifact)).status_code == 409
    with pytest.raises(HTTPException) as caught:
        models.verified_model_path(client.app.state.db, artifact["id"])
    assert caught.value.status_code == 409
    with client.app.state.db.session() as session:
        assert session.scalar(select(func.count()).select_from(ModelArtifactRow)) == 1


@pytest.mark.parametrize("damage", ["manifest_bytes", "label_hash", "snapshot_identity", "relation", "created_at", "head"])
def test_manifest_and_relational_corruption_fail_closed(client, damage):
    artifact = upload(client).json()
    model = create(client, artifact)
    other = upload(client, name="other.onnx").json()
    with client.app.state.db.session.begin() as session:
        row = session.get(ModelRevisionRow, (model["id"], 1))
        if damage == "manifest_bytes":
            row.manifest += b" "
        elif damage in {"label_hash", "snapshot_identity"}:
            payload = json.loads(row.manifest)
            payload["label_map_hash" if damage == "label_hash" else "id"] = "0" * 64 if damage == "label_hash" else "model_other"
            row.manifest = canonical_bytes(payload)
            row.manifest_hash = hashlib.sha256(row.manifest).hexdigest()
            row.manifest_size_bytes = len(row.manifest)
        elif damage == "relation":
            row.artifact_id = other["id"]
        elif damage == "created_at":
            row.created_at = "a different timestamp"
        else:
            session.get(ModelRow, model["id"]).revision = 99
    assert client.get(f"{PREFIX}/models/{model['id']}").status_code == 409
    assert client.get(model["download_url"]).status_code == 409
    assert client.get(f"{PREFIX}/models").status_code == 409


def test_auth_applies_to_upload_authoring_and_exact_exports(settings):
    settings.api_token = "model-private-test-token"
    with TestClient(create_app(settings, Registry(load_plugins=False))) as client:
        assert upload(client).status_code == 401
        assert client.get(f"{PREFIX}/models").status_code == 401
        client.headers["Authorization"] = f"Bearer {settings.api_token}"
        artifact = upload(client).json()
        model = create(client, artifact)
        assert client.get(model["download_url"]).status_code == 200
        client.headers.pop("Authorization")
        assert client.get(model["download_url"]).status_code == 401
        assert client.get(artifact["download_url"]).status_code == 401
        assert client.post(f"{PREFIX}/models/{model['id']}/revisions", json=definition(artifact, expected_revision=1)).status_code == 401


def test_registry_survives_api_restart_and_openapi_is_typed(settings):
    with TestClient(create_app(settings, Registry(load_plugins=False))) as client:
        model = create(client)
        before = client.get(model["download_url"]).content
    with TestClient(create_app(settings, Registry(load_plugins=False))) as client:
        assert client.get(model["download_url"]).content == before
        assert client.get(model["artifact"]["download_url"]).content == OPAQUE
        contract = client.get("/openapi.json").json()
        assert contract["paths"][f"{PREFIX}/models"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/ModelCreate")
        assert "application/octet-stream" in contract["paths"][f"{PREFIX}/model-artifacts"]["post"]["requestBody"]["content"]
