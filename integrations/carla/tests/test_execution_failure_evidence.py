"""Legacy v1 wire failures invalidate old passes and consume their retry key.

v3 durable attempts have separate transport/recovery tests. These fixtures
intentionally retain the historical uncorrelated execute() protocol.
"""

import hashlib

import httpx
import pytest
from fastapi.testclient import TestClient

from ade2e.adapters import Registry
from ade2e.database import Settings
from ade2e.main import create_app
from ade2e.schemas import ArtifactPayload
from axiom_carla.remote import RemoteCarlaAdapter
from axiom_carla.version import ADAPTER_VERSION
from axiom_carla.wire import encode_failure


class LegacyWireAdapter(RemoteCarlaAdapter):
    execute_attempt = None
    inspect_execution = None
    retrieve_execution = None


@pytest.mark.parametrize("failure", ["http_503", "read_timeout", "malformed_json", "invalid_contract", "diagnostic_envelope"])
def test_attempted_worker_failure_is_persisted_and_idempotent(tmp_path, failure):
    submissions = []

    def worker(request):
        if request.method == "GET":
            return httpx.Response(200, json={"status": "ready", "worker_adapter_version": ADAPTER_VERSION})
        assert request.url.path == "/v1/simulate"
        submissions.append(request)
        if failure == "diagnostic_envelope":
            return httpx.Response(500, json=encode_failure("A recorded sensor attempt failed", [ArtifactPayload(
                name="sensor-attempt.bin", kind="execution_diagnostics", media_type="application/octet-stream",
                content=b"\x00\xffcaptured before failure")]))
        if failure == "http_503":
            return httpx.Response(503, json={"detail": "Sensor timed out after starting trial"})
        if failure == "read_timeout":
            raise httpx.ReadTimeout("Response lost after possible execution", request=request)
        if failure == "malformed_json":
            return httpx.Response(200, content=b"{broken")
        return httpx.Response(200, json={"trajectory": []})

    registry = Registry(load_plugins=False)
    registry.register(LegacyWireAdapter("http://worker.test", transport=httpx.MockTransport(worker)))
    app = create_app(Settings(data_dir=tmp_path, seed_examples=False), registry)
    with TestClient(app) as client:
        scenario = client.post("/api/v1/scenarios", json={"name": "Failure boundary", "category": "lane_follow", "parameters": {"ego_speed_mps": 15, "target_speed_mps": 15, "initial_gap_m": 50}}).json()
        reference = client.post("/api/v1/runs", json={"scenario_id": scenario["id"]}).json()
        assert reference["verdict"] == "passed"
        requirement = client.post("/api/v1/requirements", json={"name": "Current collision evidence", "metric_name": "collision_count", "operator": "eq", "threshold": 0, "scenario_ids": [scenario["id"]]}).json()
        assert requirement["status"] == "passed"
        body, headers = {"scenario_id": scenario["id"], "adapter_id": "carla"}, {"Idempotency-Key": "failed-attempt"}
        failed = client.post("/api/v1/runs", json=body, headers=headers)
        assert failed.status_code == 201
        failed_run = failed.json()
        assert failed_run["status"] == failed_run["verdict"] == "error"
        assert failed_run["adapter_id"] == "carla"
        assert failed_run["trajectory"] == [] and failed_run["metrics"] == []
        assert failed_run["artifacts"] and failed_run["events"][0]["type"] == "execution_error"
        if failure == "diagnostic_envelope":
            artifact = next(item for item in failed_run["artifacts"] if item["name"] == "sensor-attempt.bin")
            content = client.get(artifact["download_url"]).content
            assert content == b"\x00\xffcaptured before failure"
            assert artifact["sha256"] == hashlib.sha256(content).hexdigest()
        assert client.post("/api/v1/runs", json=body, headers=headers).json()["id"] == failed_run["id"]
        assert len(submissions) == 1
        assert len(client.get("/api/v1/runs").json()) == 2
        evaluated = client.get("/api/v1/requirements").json()[0]
        assert evaluated["status"] == "untested" and evaluated["evidence_count"] == 0
        cell = next(cell for cell in client.get("/api/v1/overview").json()["coverage"] if cell["category"] == "lane_follow" and cell["weather"] == "clear")
        assert cell["tested"] == 0 and cell["passed"] == 0 and cell["untested"] == 1


@pytest.mark.parametrize("rejection", ["preflight_offline", "worker_busy", "unsupported_map"])
def test_preflight_or_explicit_rejection_creates_no_execution_error(tmp_path, rejection):
    submissions = []

    def worker(request):
        if request.method == "GET":
            return httpx.Response(200, json={"worker_adapter_version": ADAPTER_VERSION, **({"status": "not_ready", "detail": "offline"} if rejection == "preflight_offline" else {"status": "ready"})})
        assert request.url.path == "/v1/simulate"
        submissions.append(request)
        return httpx.Response(409 if rejection == "worker_busy" else 422, json={"detail": "Execution rejected before starting"})

    registry = Registry(load_plugins=False)
    registry.register(LegacyWireAdapter("http://worker.test", transport=httpx.MockTransport(worker)))
    with TestClient(create_app(Settings(data_dir=tmp_path, seed_examples=False), registry)) as client:
        scenario = client.post("/api/v1/scenarios", json={"name": "Rejected", "category": "lane_follow"}).json()
        response = client.post("/api/v1/runs", json={"scenario_id": scenario["id"], "adapter_id": "carla"})
        assert response.status_code == 422
        assert client.get("/api/v1/runs").json() == []
        assert len(submissions) == (0 if rejection == "preflight_offline" else 1)
