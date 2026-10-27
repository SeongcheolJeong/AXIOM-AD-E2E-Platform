"""HTTP protocol and file ownership tests; no fake model inference claims."""
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from axiom_sdk import AxiomClient, AxiomError
from axiom_sdk import perception

BASE = "/api/v1/perception"


def job_body(**changes):
    return {"label": "Recorded image review", "model_id": "model_one", "model_revision": 4,
            "recording_id": "rec_one", "channel_id": 0, "ordinals": [8, 2, 0], "decoder_id": "ros2-cdr",
            "decoder_version": "1", "seed": 0, "score_threshold": 0, "iou_threshold": 1,
            "max_detections": 50, **changes}


def artifact(content):
    return {"id": "model_artifact_one", "name": "weights.onnx", "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(), "download_url": "https://outside.invalid/collect"}


def test_upload_streams_original_bytes_and_verifies_actual_returned_hash(tmp_path):
    content = bytes(range(256)) * 6000
    source = tmp_path / "weights.onnx"
    source.write_bytes(content)
    calls = []
    def handler(request):
        calls.append(request)
        assert request.read() == content
        return httpx.Response(201, json=artifact(content))
    with AxiomClient("https://api.test", token="private", transport=httpx.MockTransport(handler)) as client:
        assert client.upload_model_artifact(source) == artifact(content)
    assert len(calls) == 1
    assert calls[0].url.path == BASE + "/model-artifacts"
    assert calls[0].url.params["name"] == "weights.onnx"
    assert calls[0].headers["content-type"] == "application/octet-stream"
    assert calls[0].headers["content-length"] == str(len(content))
    assert calls[0].headers["authorization"] == "Bearer private"
    assert source.read_bytes() == content


@pytest.mark.parametrize("damage", ["sha256", "size_bytes", "name", "id"])
def test_upload_rejects_mismatched_registration_response(tmp_path, damage):
    content = b"model bytes"
    source = tmp_path / "weights.onnx"
    source.write_bytes(content)
    result = artifact(content)
    result[damage] = {"sha256": "0" * 64, "size_bytes": len(content) + 1, "name": "different.onnx", "id": "../../other"}[damage]
    with AxiomClient(transport=httpx.MockTransport(lambda request: request.read() and httpx.Response(201, json=result))) as client:
        with pytest.raises(ValueError):
            client.upload_model_artifact(source)


def test_upload_limit_and_empty_file_fail_before_network(tmp_path, monkeypatch):
    monkeypatch.setattr(perception, "MODEL_BYTES_LIMIT", 10)
    source = tmp_path / "weights.onnx"
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail("must not upload"))) as client:
        for content in (b"", b"x" * 11):
            source.write_bytes(content)
            with pytest.raises(ValueError): client.upload_model_artifact(source)


@pytest.mark.parametrize("operation", ["job", "comparison"])
def test_lost_response_requires_explicit_retry_of_same_endpoint_key_and_body(operation):
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path, request.headers["Idempotency-Key"], request.content))
        if len(calls) == 1:
            raise httpx.ReadError("response lost after server accepted intent")
        return httpx.Response(202 if operation == "job" else 201, json={"id": "persisted_one"})
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        def invoke():
            if operation == "job":
                return client.submit_perception_job(**job_body(), idempotency_key="stable-perception-intent")
            return client.capture_perception_comparison("left_job", "right_job", name="Saved prediction comparison",
                note="No accuracy claim", idempotency_key="stable-perception-intent")
        with pytest.raises(httpx.ReadError): invoke()
        assert len(calls) == 1
        assert invoke()["id"] == "persisted_one"
    assert calls[0] == calls[1]
    assert calls[0][1] == BASE + ("/jobs" if operation == "job" else "/comparisons")
    body = json.loads(calls[0][3])
    if operation == "job":
        assert body["model_revision"] == 4 and body["ordinals"] == [8, 2, 0]
        assert body["channel_id"] == body["seed"] == body["score_threshold"] == 0
        assert body["decoder_version"] == "1" and body["runtime_id"] == "onnx-cpu"
    else:
        assert body["left_job_id"] == "left_job" and body["right_job_id"] == "right_job"


@pytest.mark.parametrize("key", ["", " ", "x" * 201, None])
def test_both_write_intents_require_explicit_nonblank_keys(key):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail("must not submit"))) as client:
        with pytest.raises(ValueError): client.submit_perception_job(**job_body(), idempotency_key=key)
        with pytest.raises(ValueError): client.capture_perception_comparison("l", "r", name="name", note="note", idempotency_key=key)


@pytest.mark.parametrize("changes", [
    {"model_revision": 0}, {"model_revision": True}, {"model_revision": 1.0},
    {"ordinals": []}, {"ordinals": [0, 0]}, {"ordinals": [True]}, {"ordinals": [100001]}, {"ordinals": list(range(65))},
    {"channel_id": -1}, {"seed": -1}, {"score_threshold": float("nan")}, {"iou_threshold": float("inf")},
    {"score_threshold": True}, {"max_detections": 301},
])
def test_invalid_pins_and_bounds_fail_before_network(changes):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail("must not submit"))) as client:
        with pytest.raises(ValueError):
            client.submit_perception_job(**job_body(**changes), idempotency_key="key")


def test_authoring_and_lists_use_separate_perception_routes_and_revision_guards():
    calls = []
    def handler(request):
        assert request.headers["authorization"] == "Bearer private"
        calls.append((request.method, request.url.path, dict(request.url.params), json.loads(request.content) if request.content else None))
        return httpx.Response(200, json={})
    with AxiomClient(token="private", transport=httpx.MockTransport(handler)) as client:
        client.models()
        client.create_model(name="Declared", artifact_id="a", weights_license="unknown")
        client.get_model("model_one", revision=2)
        client.model_revisions("model_one")
        client.revise_model("model_one", expected_revision=2, name="New definition", labels=["car", "person"])
        client.perception_runtimes()
        client.perception_jobs(status="interrupted", limit=20, before_seq=50)
        client.cancel_perception_job("job_one")
        client.perception_comparisons()
        client.get_perception_comparison("comparison_one")
    assert calls[2] == ("GET", BASE + "/models/model_one", {"revision": "2"}, None)
    assert calls[4][3] == {"name": "New definition", "labels": ["car", "person"], "expected_revision": 2}
    assert calls[6] == ("GET", BASE + "/jobs", {"status": "interrupted", "limit": "20", "before_seq": "50"}, None)
    assert calls[7] == ("POST", BASE + "/jobs/job_one/cancel", {}, None)
    assert all(path.startswith(BASE) for _, path, _, _ in calls)


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled", "interrupted"])
def test_polling_preserves_partial_terminal_status_and_never_resubmits(terminal):
    calls, states = [], iter(["running", "cancel_requested", terminal])
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "job_one", "status": next(states), "counts": {"completed": 1, "not_attempted": 2}})
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        result = client.wait_perception_job("job_one", poll_interval=0.001)
    assert result["status"] == terminal and result["counts"]["not_attempted"] == 2
    assert len(calls) == 3 and all(request.method == "GET" for request in calls)


def test_polling_timeout_is_not_cancellation():
    calls = []
    with AxiomClient(transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200, json={"id": "job_one", "status": "running"}))) as client:
        with pytest.raises(TimeoutError, match="not cancelled or resubmitted"):
            client.wait_perception_job("job_one", timeout=0)
    assert len(calls) == 1 and calls[0].method == "GET"


def metadata(content, operation):
    summary = {"manifest_hash": hashlib.sha256(content).hexdigest(), "manifest_size_bytes": len(content),
               "download_url": "https://outside.invalid/collect"}
    if operation == "artifact": return artifact(content)
    if operation == "model": return {**summary, "id": "model_one", "revision": 4}
    if operation == "comparison": return {**summary, "id": "comparison_one"}
    item = {"selection_index": 2, "recording_id": "rec_one", "channel_id": 3, "ordinal": 17}
    return {**summary, "id": "job_one", "status": "interrupted", "inputs": [item], "frames": [
        {"selection_index": 2, "status": "completed", "input": item,
         "image": {"sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content),
                   "download_url": "https://outside.invalid/image", "width": 2, "height": 2}}]}


def download(client, operation, expected, destination):
    if operation == "artifact": return client.download_model_artifact(expected, destination)
    if operation == "model": return client.export_model("model_one", destination)
    if operation == "job": return client.export_perception_job("job_one", destination)
    if operation == "image": return client.export_perception_image("job_one", 2, destination)
    return client.export_perception_comparison("comparison_one", destination)


def is_download(request):
    return request.url.path.endswith(("/download", "/export", "/image"))


@pytest.mark.parametrize("operation", ["artifact", "model", "job", "image", "comparison"])
def test_exports_pin_exact_identity_ignore_external_urls_and_preserve_existing_files(tmp_path, operation):
    content = b"exact stored evidence\x00" * 70_000
    if operation == "model": content = b'{"revision":4,"declared":true}'
    expected, calls = metadata(content, operation), []
    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer private"
        if is_download(request):
            if operation == "model": assert request.url.params["revision"] == "4"
            if operation == "image": assert request.url.path == BASE + "/jobs/job_one/frames/2/image"
            return httpx.Response(200, content=content)
        return httpx.Response(200, json=expected)
    destination = tmp_path / "evidence.bin"
    with AxiomClient("https://api.test", token="private", transport=httpx.MockTransport(handler)) as client:
        assert download(client, operation, expected, destination).read_bytes() == content
        with pytest.raises(FileExistsError): download(client, operation, expected, destination)
    assert destination.read_bytes() == content
    assert all(request.url.host == "api.test" for request in calls)


@pytest.mark.parametrize("failure", ["wrong_hash", "short", "long", "length_header", "redirect", "encoded", "read_failure", "close_failure"])
def test_failed_download_cleans_owned_file_and_never_follows_redirect(tmp_path, failure):
    content = b"a" * (2 * 1024 * 1024)
    expected = artifact(content)
    calls = []
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            if failure == "read_failure":
                yield content[:1024 * 1024 + 1]
                raise httpx.ReadError("transfer interrupted")
            yield {"short": content[:-1], "long": content + b"x", "wrong_hash": b"b" * len(content)}.get(failure, content)
        def close(self):
            if failure == "close_failure": raise OSError("response close failure")
    def handler(request):
        calls.append(request)
        if failure == "redirect": return httpx.Response(307, headers={"location": "https://outside.invalid/collect"})
        headers = {"content-length": "1"} if failure == "length_header" else {"content-encoding": "gzip"} if failure == "encoded" else {}
        return httpx.Response(200, stream=Body(), headers=headers)
    destination = tmp_path / "model.onnx"
    with AxiomClient("https://api.test", token="private", transport=httpx.MockTransport(handler)) as client:
        client._http.follow_redirects = True
        with pytest.raises((ValueError, AxiomError, httpx.HTTPError, OSError)):
            client.download_model_artifact(expected, destination)
    assert len(calls) == 1 and calls[0].url.host == "api.test"
    assert not destination.exists()


@pytest.mark.parametrize("operation,damage", [("model", "wrong_id"), ("model", "wrong_size"), ("job", "pending"),
    ("image", "wrong_selection"), ("image", "wrong_input"), ("comparison", "wrong_id")])
def test_unpinned_or_incomplete_metadata_cannot_export(tmp_path, operation, damage):
    value = metadata(b"exact bytes", operation)
    if damage == "wrong_id": value["id"] = "another_identity"
    elif damage == "wrong_size": value["manifest_size_bytes"] = 128 * 1024 * 1024
    elif damage == "pending": value["status"] = "running"
    elif damage == "wrong_selection": value["frames"][0]["selection_index"] = 3
    else: value["frames"][0]["input"] = {"selection_index": 2, "ordinal": 18}
    calls = []
    with AxiomClient(transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200, json=value))) as client:
        with pytest.raises(ValueError): download(client, operation, value, tmp_path / "invalid")
    assert len(calls) == 1 and not is_download(calls[0])
    assert list(tmp_path.iterdir()) == []


def test_short_local_write_is_never_reported_as_success(tmp_path, monkeypatch):
    destination, content = tmp_path / "model.onnx", b"actual bytes"
    original_open = Path.open
    class ShortWriter:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): self.stream.__enter__(); return self
        def __exit__(self, *args): return self.stream.__exit__(*args)
        def write(self, data): return self.stream.write(data[:-1])
    def open_file(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        return ShortWriter(stream) if path == destination and mode == "xb" else stream
    monkeypatch.setattr(Path, "open", open_file)
    with AxiomClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))) as client:
        with pytest.raises(OSError, match="Incomplete"):
            client.download_model_artifact(artifact(content), destination)
    assert not destination.exists()


def test_cli_uses_same_http_contract_with_explicit_json_pins_and_key(tmp_path, monkeypatch, capsys):
    from axiom_sdk import cli
    definition = tmp_path / "job.json"
    definition.write_text(json.dumps(job_body()))
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(202, json={"id": "job_one", "status": "queued"})
    monkeypatch.setenv("ADE2E_API_TOKEN", "private")
    monkeypatch.setattr(cli, "AxiomClient", lambda url, **kwargs: AxiomClient(url, **kwargs, transport=httpx.MockTransport(handler)))
    monkeypatch.setattr("sys.argv", ["axiom", "--url", "https://api.test", "submit-perception-job", str(definition), "--idempotency-key", "explicit-key"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["id"] == "job_one"
    assert calls[0].url.path == BASE + "/jobs" and calls[0].url.host == "api.test"
    assert calls[0].headers["Idempotency-Key"] == "explicit-key"
    assert calls[0].headers["Authorization"] == "Bearer private"
    assert json.loads(calls[0].content)["model_revision"] == 4


def test_cli_model_revision_keeps_guard_outside_definition(tmp_path, monkeypatch, capsys):
    from axiom_sdk import cli
    definition = tmp_path / "model.json"
    definition.write_text(json.dumps({"name": "Revised model", "labels": ["car", "person"]}))
    calls = []
    monkeypatch.setattr(cli, "AxiomClient", lambda url, **kwargs: AxiomClient(url, **kwargs,
        transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(201, json={"revision": 5}))))
    monkeypatch.setattr("sys.argv", ["axiom", "revise-model", "model_one", str(definition), "--expected-revision", "4"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["revision"] == 5
    assert json.loads(calls[0].content) == {"name": "Revised model", "labels": ["car", "person"], "expected_revision": 4}


def test_cli_invalid_job_json_reports_structured_error_without_submission(tmp_path, monkeypatch, capsys):
    from axiom_sdk import cli
    definition = tmp_path / "job.json"
    definition.write_text('{"label":"missing pins"}')
    monkeypatch.setattr("sys.argv", ["axiom", "submit-perception-job", str(definition), "--idempotency-key", "key"])
    with pytest.raises(SystemExit) as caught: cli.main()
    assert caught.value.code == 1
    assert "explicit model revision" in json.loads(capsys.readouterr().err)["error"]
