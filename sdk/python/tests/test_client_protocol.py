"""SDK contract tests use a mock HTTP server and real temporary file writes."""
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from axiom_sdk.client import AxiomClient, AxiomError


def test_execution_forwards_explicit_key_seed_zero_and_overrides():
    calls = []

    def api(request):
        calls.append(request)
        return httpx.Response(201, json={"id": "run-1"})

    with AxiomClient("https://api.test/", token="api-secret", transport=httpx.MockTransport(api)) as client:
        assert client.run("scenario-1", adapter_id="carla", seed=0,
                          parameter_overrides={"response_delay_s": 0}, idempotency_key="retry-same-key") == {"id": "run-1"}
    assert len(calls) == 1
    request = calls[0]
    assert request.url.path == "/api/v1/runs"
    assert request.headers["Authorization"] == "Bearer api-secret"
    assert request.headers["Idempotency-Key"] == "retry-same-key"
    assert json.loads(request.content) == {"scenario_id": "scenario-1", "adapter_id": "carla", "seed": 0,
                                          "parameter_overrides": {"response_delay_s": 0}}


def test_failed_execution_is_not_retried_automatically():
    calls = []

    def api(request):
        calls.append(request)
        raise httpx.ReadTimeout("response lost after possible execution", request=request)

    with AxiomClient(transport=httpx.MockTransport(api)) as client:
        with pytest.raises(httpx.ReadTimeout):
            client.run("scenario-1", idempotency_key="stable-key")
    assert len(calls) == 1


def test_conflicting_idempotency_key_preserves_structured_error():
    detail = {"code": "idempotency_conflict", "message": "request changed"}
    with AxiomClient(transport=httpx.MockTransport(lambda _: httpx.Response(409, json={"detail": detail}))) as client:
        with pytest.raises(AxiomError) as caught:
            client.run("scenario-1", idempotency_key="same-key")
    assert caught.value.status_code == 409
    assert caught.value.detail == detail


@pytest.mark.parametrize("body", [b"upstream proxy failure", b"[]", b"null", b'"error string"'])
def test_nonstandard_error_bodies_still_raise_axiom_error(body):
    with AxiomClient(transport=httpx.MockTransport(lambda _: httpx.Response(502, content=body))) as client:
        with pytest.raises(AxiomError) as caught:
            client.health()
    assert caught.value.status_code == 502


def test_artifact_download_uses_resource_id_and_checks_exact_bytes(tmp_path):
    calls, content = [], b"\x00\xffsensor\x00"
    artifact = {"id": "artifact-1", "sha256": hashlib.sha256(content).hexdigest(),
                "download_url": "https://external.test/steal-credential"}

    def api(request):
        calls.append(request)
        return httpx.Response(200, content=content)

    with AxiomClient("https://api.test", token="api-secret", transport=httpx.MockTransport(api)) as client:
        output = client.download_artifact(artifact, tmp_path / "sensor.bin")
    assert output.read_bytes() == content
    assert len(calls) == 1
    assert calls[0].url.host == "api.test"
    assert calls[0].url.path == "/api/v1/artifacts/artifact-1/download"
    assert calls[0].headers["Authorization"] == "Bearer api-secret"


def test_artifact_hash_mismatch_leaves_no_output(tmp_path):
    target = tmp_path / "sensor.bin"
    with AxiomClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"corrupt"))) as client:
        with pytest.raises(ValueError, match="SHA-256"):
            client.download_artifact({"id": "artifact-1", "sha256": hashlib.sha256(b"expected").hexdigest()}, target)
    assert not target.exists()


@pytest.mark.parametrize("operation", ["artifact", "manifest"])
def test_download_does_not_overwrite_existing_file(tmp_path, operation):
    target, content = tmp_path / "sensor.bin", b"downloaded"
    target.write_bytes(b"user-file")
    with AxiomClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))) as client:
        with pytest.raises(FileExistsError):
            if operation == "artifact":
                client.download_artifact({"id": "a", "sha256": hashlib.sha256(content).hexdigest()}, target)
            else:
                client.export_run("run-1", target)
    assert target.read_bytes() == b"user-file"


@pytest.mark.parametrize("operation", ["artifact", "manifest"])
@pytest.mark.parametrize("failure_stage", ["write", "close"])
def test_failed_local_write_cleans_created_file_and_allows_retry(tmp_path, monkeypatch, operation, failure_stage):
    target, content = tmp_path / "evidence.bin", b"complete-evidence"
    original_open = Path.open
    failure = OSError(f"injected {failure_stage} failure")
    opened_streams = []

    class FailingStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def write(self, data):
            if failure_stage == "write":
                self.stream.write(data[:3])
                self.stream.flush()
                assert target.read_bytes() == data[:3]
                raise failure
            return self.stream.write(data)

        def __exit__(self, *args):
            self.stream.__exit__(*args)
            if failure_stage == "close":
                assert target.read_bytes() == content
                raise failure

    def failing_open(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        if path == target and mode == "xb":
            opened_streams.append(stream)
            return FailingStream(stream)
        return stream

    with AxiomClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))) as client:
        def save():
            if operation == "artifact":
                return client.download_artifact({"id": "a", "sha256": hashlib.sha256(content).hexdigest()}, target)
            return client.export_run("run-1", target)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "open", failing_open)
            with pytest.raises(OSError) as caught:
                save()
        assert caught.value is failure
        assert len(opened_streams) == 1
        assert opened_streams[0].closed
        assert not target.exists()
        assert save() == target
        assert target.read_bytes() == content


def test_artifact_redirect_never_forwards_credentials(tmp_path):
    calls = []

    def api(request):
        calls.append(request)
        return httpx.Response(307, headers={"Location": "https://external.test/collect"})

    target = tmp_path / "sensor.bin"
    with AxiomClient("https://api.test", token="secret", transport=httpx.MockTransport(api)) as client:
        with pytest.raises((AxiomError, ValueError)):
            client.download_artifact({"id": "a", "sha256": hashlib.sha256(b"actual-data").hexdigest()}, target)
    assert len(calls) == 1
    assert calls[0].url.host == "api.test"
    assert not target.exists()


def test_sweep_is_bounded_and_resumes_with_stable_parameter_order():
    calls = []

    def api(request):
        calls.append(request)
        return httpx.Response(201, json={"id": "test-run"})

    with AxiomClient(transport=httpx.MockTransport(api)) as client:
        list(client.sweep("s", {"target_speed_mps": [3, 6], "ego_speed_mps": [4, 8]}, campaign_key="campaign"))
        list(client.sweep("s", {"ego_speed_mps": [4, 8], "target_speed_mps": [3, 6]}, campaign_key="campaign"))
    first, resumed = calls[:4], calls[4:]
    assert [(r.headers["Idempotency-Key"], json.loads(r.content)) for r in first] == [
        (r.headers["Idempotency-Key"], json.loads(r.content)) for r in resumed]
    assert [r.headers["Idempotency-Key"] for r in first] == ["campaign:0", "campaign:1", "campaign:2", "campaign:3"]
    assert {tuple(sorted(json.loads(r.content)["parameter_overrides"].items())) for r in first} == {
        (("ego_speed_mps", 4), ("target_speed_mps", 3)), (("ego_speed_mps", 4), ("target_speed_mps", 6)),
        (("ego_speed_mps", 8), ("target_speed_mps", 3)), (("ego_speed_mps", 8), ("target_speed_mps", 6))}


@pytest.mark.parametrize("parameters,max_runs", [({}, 5), ({"ego_speed_mps": []}, 5), ({"ego_speed_mps": [1, 2, 3]}, 2), ({"ego_speed_mps": [1]}, 1001)])
def test_invalid_sweep_fails_before_any_request(parameters, max_runs):
    def api(request):
        pytest.fail("Invalid sweep submitted a run")

    with AxiomClient(transport=httpx.MockTransport(api)) as client:
        with pytest.raises(ValueError):
            list(client.sweep("s", parameters, max_runs=max_runs))
