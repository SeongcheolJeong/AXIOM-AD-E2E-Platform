"""Versioned HTTP evidence contracts; these mocks never validate a CARLA engine."""
import base64
import copy
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from ade2e.adapters import AdapterExecutionError, AdapterUnavailable
from ade2e.schemas import ArtifactPayload
from axiom_carla import wire, worker
from axiom_carla.remote import RemoteCarlaAdapter
from axiom_carla.version import ADAPTER_VERSION


def ready():
    return {"status": "ready", "version": "0.9.16", "worker_adapter_version": ADAPTER_VERSION}


def failure():
    return wire.encode_failure("Sensor capture attempted; inspect retained diagnostics", [ArtifactPayload(
        name="execution.json", kind="execution_diagnostics", media_type="application/json",
        content=b'{"cleanup":"unconfirmed","sensor_frames":2}')])


@pytest.mark.parametrize("version", [None, "0.2.0", "0.3.1", False, {"version": "0.3.0"}])
def test_health_version_must_match_before_post_and_public_ready(version, carla_scenario):
    calls = []
    data = ready()
    if version is None:
        data.pop("worker_adapter_version")
    else:
        data["worker_adapter_version"] = version
    def handle(request):
        calls.append(request)
        assert request.method == "GET"
        return httpx.Response(200, json=data)
    adapter = RemoteCarlaAdapter("http://worker.test", transport=httpx.MockTransport(handle))
    assert adapter.check_health()["status"] == "unreachable"
    with pytest.raises(AdapterUnavailable, match="adapter version mismatch"):
        adapter.execute(carla_scenario)
    assert len(calls) == 2


def test_worker_health_pins_adapter_version_separately_from_engine(monkeypatch):
    monkeypatch.delenv("ADE2E_CARLA_WORKER_TOKEN", raising=False)
    monkeypatch.setattr(worker.CarlaRunner, "preflight", lambda _: {"status": "ready", "version": "0.9.16", "worker_adapter_version": "spoof"})
    with TestClient(worker.app) as client:
        data = client.get("/health").json()
    assert data == ready()
    assert worker.app.version == ADAPTER_VERSION == "0.8.0"


def test_worker_failure_preserves_exact_bytes_then_remote_raises_evidence(monkeypatch, carla_scenario):
    monkeypatch.delenv("ADE2E_CARLA_WORKER_TOKEN", raising=False)
    lock = threading.Lock()
    monkeypatch.setattr(worker, "_lock", lock)
    payload = ArtifactPayload(name="sensor.bin", kind="partial_sensor", media_type="application/octet-stream", content=b"\x00\xffcaptured")
    def fail(_self, _scenario):
        raise AdapterExecutionError("Capture failed after the sensor callback", artifact_payloads=[payload])
    monkeypatch.setattr(worker.CarlaRunner, "execute", fail)
    with TestClient(worker.app) as client:
        response = client.post("/v1/simulate", json=carla_scenario.model_dump())
    assert response.status_code == 500 and not lock.locked()
    data = response.json()
    assert set(data) == {"schema_version", "worker_adapter_version", "detail", "artifact_payloads"}
    assert data["schema_version"] == wire.FAILURE_SCHEMA
    assert base64.b64decode(data["artifact_payloads"][0]["content_base64"]) == payload.content
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=ready()) if request.method == "GET" else httpx.Response(500, json=data)
    adapter = RemoteCarlaAdapter("http://worker.test", transport=httpx.MockTransport(handle))
    with pytest.raises(AdapterExecutionError, match="Capture failed") as caught:
        adapter.execute(carla_scenario)
    assert caught.value.artifact_payloads[0].content == payload.content
    assert len(calls) == 2


@pytest.mark.parametrize("damage", ["old_version", "missing_version", "schema", "missing_artifacts", "base64", "count", "extra_artifact_field", "metrics", "bad_detail"])
def test_unverified_failure_never_transmits_upstream_detail_or_artifacts(damage, carla_scenario):
    data = failure()
    if damage == "old_version": data["worker_adapter_version"] = "0.2.0"
    elif damage == "missing_version": data.pop("worker_adapter_version")
    elif damage == "schema": data["schema_version"] = "carla-execution-failure/99"
    elif damage == "missing_artifacts": data.pop("artifact_payloads")
    elif damage == "base64": data["artifact_payloads"][0]["content_base64"] = "@@@"
    elif damage == "count": data["artifact_payloads"] *= 101
    elif damage == "extra_artifact_field": data["artifact_payloads"][0]["content"] = "not bytes"
    elif damage == "metrics": data["metrics"] = [{"passed": True}]
    elif damage == "bad_detail": data["detail"] = {"message": "untrusted"}
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=ready()) if request.method == "GET" else httpx.Response(500, json=data)
    with pytest.raises(AdapterExecutionError, match="unverified failure response") as caught:
        RemoteCarlaAdapter("http://worker.test", transport=httpx.MockTransport(handle)).execute(carla_scenario)
    assert caught.value.artifact_payloads == () and len(calls) == 2


@pytest.mark.parametrize("damage", ["old_version", "missing_version", "schema", "legacy_result", "extra_field"])
def test_success_version_is_rechecked_after_the_preflight(damage, carla_scenario, worker_payload):
    data = {"schema_version": wire.RESULT_SCHEMA, "worker_adapter_version": ADAPTER_VERSION, "result": worker_payload}
    if damage == "old_version": data["worker_adapter_version"] = "0.2.0"
    elif damage == "missing_version": data.pop("worker_adapter_version")
    elif damage == "schema": data["schema_version"] = "carla-execution-result/2"
    elif damage == "legacy_result": data = worker_payload
    elif damage == "extra_field": data["passed"] = True
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=ready()) if request.method == "GET" else httpx.Response(200, json=data)
    with pytest.raises(AdapterExecutionError, match="invalid result contract"):
        RemoteCarlaAdapter("http://worker.test", transport=httpx.MockTransport(handle)).execute(carla_scenario)
    assert len(calls) == 2


class OverflowStream(httpx.SyncByteStream):
    def __init__(self, limit):
        self.limit, self.produced = limit, 0
    def __iter__(self):
        for _ in range(self.limit // 65536 + 10):
            self.produced += 65536
            yield b"x" * 65536


@pytest.mark.parametrize("stage", ["health", "failure", "success"])
def test_chunked_wire_cap_aborts_without_reading_the_remainder(stage, monkeypatch, carla_scenario):
    # The production constants remain explicit; shrink only the execution bound
    # to exercise incremental termination without allocating a 48 MiB fixture.
    from axiom_carla import remote
    assert wire.WIRE_BYTES_LIMIT == 48 * 1024 * 1024
    assert wire.HEALTH_BYTES_LIMIT == 64 * 1024
    monkeypatch.setattr(remote, "WIRE_BYTES_LIMIT", 128 * 1024)
    limit = wire.HEALTH_BYTES_LIMIT if stage == "health" else remote.WIRE_BYTES_LIMIT
    stream = OverflowStream(limit)
    calls = []
    def handle(request):
        calls.append(request)
        assert request.headers["Accept-Encoding"] == "identity"
        if stage != "health" and request.method == "GET": return httpx.Response(200, json=ready())
        return httpx.Response(500 if stage == "failure" else 200, stream=stream)
    with pytest.raises(AdapterUnavailable if stage == "health" else AdapterExecutionError):
        RemoteCarlaAdapter("http://worker.test", transport=httpx.MockTransport(handle)).execute(carla_scenario)
    assert stream.produced == limit + 65536
    assert len(calls) == (1 if stage == "health" else 2)


@pytest.mark.parametrize("body", [b'{"schema_version":"bad","schema_version":"carla-execution-failure/1"}', b"upstream private error", b'{"value":NaN}', b'{"detail":"\\ud800"}', b"[" * 2000 + b"0" + b"]" * 2000])
def test_upstream_500_json_ambiguity_remains_generic(body, carla_scenario):
    def handle(request):
        return httpx.Response(200, json=ready()) if request.method == "GET" else httpx.Response(500, content=body)
    with pytest.raises(AdapterExecutionError, match="unverified failure response") as caught:
        RemoteCarlaAdapter("http://worker.test", transport=httpx.MockTransport(handle)).execute(carla_scenario)
    assert not caught.value.artifact_payloads


def test_artifact_budget_is_checked_before_decoding_and_on_total(monkeypatch):
    assert wire.ARTIFACT_TOTAL_LIMIT == 32 * 1024 * 1024
    assert wire.ARTIFACT_BYTES_LIMIT == 20 * 1024 * 1024
    monkeypatch.setattr(wire, "ARTIFACT_BYTES_LIMIT", 8)
    monkeypatch.setattr(wire, "ARTIFACT_TOTAL_LIMIT", 12)
    artifact = {"name": "data", "kind": "test", "media_type": "application/octet-stream", "content_base64": base64.b64encode(b"12345678").decode()}
    assert len(wire.decode_artifacts([artifact])[0].content) == 8
    with pytest.raises(ValueError): wire.decode_artifacts([artifact, copy.deepcopy(artifact)])
    artifact["content_base64"] = "A" * 100
    monkeypatch.setattr(wire.base64, "b64decode", lambda *args, **kwargs: pytest.fail("Over-bound base64 reached decoding"))
    with pytest.raises(ValueError): wire.decode_artifacts([artifact])
