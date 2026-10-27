import base64
import threading

import pytest
from fastapi.testclient import TestClient

from ade2e.adapters import AdapterExecutionError, AdapterInputError, AdapterUnavailable
from ade2e.schemas import SimulationResult
from axiom_carla import worker
from axiom_carla.version import ADAPTER_VERSION
from axiom_carla.wire import RESULT_SCHEMA


def test_worker_checks_credential_before_preflight_or_simulation(monkeypatch, carla_scenario):
    monkeypatch.setenv("ADE2E_CARLA_WORKER_TOKEN", "worker-secret")
    monkeypatch.setattr(worker.CarlaRunner, "preflight", lambda _: pytest.fail("Unauthenticated preflight"))
    monkeypatch.setattr(worker.CarlaRunner, "execute", lambda *_: pytest.fail("Unauthenticated simulation"))
    with TestClient(worker.app) as client:
        assert client.get("/health").status_code == 401
        assert client.post("/v1/simulate", json=carla_scenario.model_dump(), headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_worker_health_reports_unready_without_claiming_an_execution(monkeypatch):
    monkeypatch.delenv("ADE2E_CARLA_WORKER_TOKEN", raising=False)

    def offline(_):
        raise AdapterUnavailable("engine is not configured")

    monkeypatch.setattr(worker.CarlaRunner, "preflight", offline)
    with TestClient(worker.app) as client:
        response = client.get("/health")
    assert response.json() == {"status": "not_ready", "detail": "engine is not configured", "worker_adapter_version": ADAPTER_VERSION}


@pytest.mark.parametrize("error,status", [(AdapterInputError("bad map"), 422), (AdapterUnavailable("offline"), 503), (AdapterExecutionError("trial failed"), 500)])
def test_worker_releases_exclusive_lock_after_failure(error, status, monkeypatch, carla_scenario):
    monkeypatch.delenv("ADE2E_CARLA_WORKER_TOKEN", raising=False)
    lock = threading.Lock()
    monkeypatch.setattr(worker, "_lock", lock)

    def fail(*_):
        raise error

    monkeypatch.setattr(worker.CarlaRunner, "execute", fail)
    with TestClient(worker.app) as client:
        response = client.post("/v1/simulate", json=carla_scenario.model_dump())
    assert response.status_code == status
    assert response.json()["detail"] == str(error)
    assert lock.acquire(blocking=False)
    lock.release()


def test_busy_worker_rejects_second_trial_without_entering_runner(monkeypatch, carla_scenario):
    monkeypatch.delenv("ADE2E_CARLA_WORKER_TOKEN", raising=False)
    lock = threading.Lock()
    lock.acquire()
    monkeypatch.setattr(worker, "_lock", lock)
    monkeypatch.setattr(worker.CarlaRunner, "execute", lambda *_: pytest.fail("Concurrent trial entered runner"))
    try:
        with TestClient(worker.app) as client:
            response = client.post("/v1/simulate", json=carla_scenario.model_dump())
        assert response.status_code == 409
        assert lock.locked()
    finally:
        lock.release()


def test_worker_serializes_arbitrary_artifact_bytes_as_base64(monkeypatch, carla_scenario, worker_payload):
    monkeypatch.delenv("ADE2E_CARLA_WORKER_TOKEN", raising=False)
    monkeypatch.setattr(worker, "_lock", threading.Lock())
    payload = worker_payload
    artifact = payload["artifact_payloads"][0]
    artifact["content"] = base64.b64decode(artifact.pop("content_base64"))
    result = SimulationResult.model_validate(payload)
    monkeypatch.setattr(worker.CarlaRunner, "execute", lambda *_: result)
    with TestClient(worker.app) as client:
        response = client.post("/v1/simulate", json=carla_scenario.model_dump())
    assert response.status_code == 200
    assert response.json()["schema_version"] == RESULT_SCHEMA
    assert response.json()["worker_adapter_version"] == ADAPTER_VERSION
    data = response.json()["result"]["artifact_payloads"][0]
    assert "content" not in data
    assert base64.b64decode(data["content_base64"]) == b"\x00\xffCAR"
