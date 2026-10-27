"""Connection checks must never create simulation evidence or run a plugin."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from ade2e.adapters import Registry
from ade2e.database import Settings
from ade2e.main import create_app
from ade2e.reference import ReferenceAdapter


class NoProbeAdapter(ReferenceAdapter):
    def metadata(self):
        return super().metadata().model_copy(update={"id": "probe-test", "name": "Connection test adapter"})

    def execute(self, scenario):
        pytest.fail("A connection probe must not execute any scenario")


class ProbeAdapter(NoProbeAdapter):
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def check_health(self):
        self.calls += 1
        return self.result


def test_reference_health_is_read_only_and_never_executes(settings, monkeypatch):
    registry = Registry(load_plugins=False)
    monkeypatch.setattr(registry.get("reference"), "execute", lambda _: pytest.fail("Probe ran a scenario"))
    with TestClient(create_app(settings, registry)) as client:
        response = client.get("/api/v1/adapters/reference/health")
        data = response.json()
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert data["adapter_id"] == "reference"
        assert data["status"] == "ready"
        assert data["metadata"]["version"] == registry.get("reference").metadata().version
        assert datetime.fromisoformat(data["checked_at"]).utcoffset() is not None
        assert client.get("/api/v1/runs").json() == []
        assert client.get("/api/v1/artifacts").json() == []


def test_unconfigured_carla_does_not_probe_or_change_metadata(settings):
    registry = Registry(load_plugins=False)
    before = registry.get("carla").metadata().model_dump()
    with TestClient(create_app(settings, registry)) as client:
        data = client.get("/api/v1/adapters/carla/health").json()
        assert data["status"] == "not_configured"
        assert data["detail"]
        assert registry.get("carla").metadata().model_dump() == before
        assert client.get("/api/v1/runs").json() == []


def test_plugin_without_probe_is_unknown_even_when_configured(settings):
    registry = Registry(load_plugins=False)
    registry.register(NoProbeAdapter())
    with TestClient(create_app(settings, registry)) as client:
        result = client.get("/api/v1/adapters/probe-test/health").json()
        assert result["status"] == "unknown"
        assert "does not provide" in result["detail"]


def test_plugin_probe_returns_actual_metadata_without_persisting_runs(settings):
    registry = Registry(load_plugins=False)
    adapter = ProbeAdapter({"status": "ready", "detail": "Worker ready", "metadata": {"version": "0.9.16", "map": "Town04"}})
    registry.register(adapter)
    with TestClient(create_app(settings, registry)) as client:
        result = client.get("/api/v1/adapters/probe-test/health").json()
        assert result["status"] == "ready"
        assert result["metadata"] == {"version": "0.9.16", "map": "Town04"}
        assert adapter.calls == 1
        assert client.get("/api/v1/runs").json() == []
        assert client.get("/api/v1/artifacts").json() == []


@pytest.mark.parametrize("bad_result", [None, [], {"status": "available", "detail": "not an actual probe status"}, {"status": "ready", "detail": None}])
def test_malformed_probe_result_never_becomes_ready(settings, bad_result):
    registry = Registry(load_plugins=False)
    registry.register(ProbeAdapter(bad_result))
    with TestClient(create_app(settings, registry)) as client:
        result = client.get("/api/v1/adapters/probe-test/health").json()
        assert result["status"] == "unknown"


def test_probe_exception_is_normalized_without_leaking_secret(settings):
    class BrokenProbe(NoProbeAdapter):
        def check_health(self):
            raise RuntimeError("private credential must not escape")

    registry = Registry(load_plugins=False)
    registry.register(BrokenProbe())
    with TestClient(create_app(settings, registry)) as client:
        response = client.get("/api/v1/adapters/probe-test/health")
        assert response.status_code == 200
        assert response.json()["status"] == "unreachable"
        assert "private credential" not in response.text


def test_health_endpoint_uses_existing_authentication(tmp_path):
    settings = Settings(data_dir=tmp_path, api_token="local-secret", seed_examples=False)
    with TestClient(create_app(settings, Registry(load_plugins=False))) as client:
        path = "/api/v1/adapters/reference/health"
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer local-secret"}).json()["status"] == "ready"


def test_missing_adapter_is_404_and_health_contract_is_in_openapi(client):
    assert client.get("/api/v1/adapters/missing/health").status_code == 404
    spec = client.get("/openapi.json").json()
    operation = spec["paths"]["/api/v1/adapters/{adapter_id}/health"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/AdapterHealthReport")
    properties = spec["components"]["schemas"]["AdapterHealthReport"]["properties"]
    assert set(properties) == {"adapter_id", "checked_at", "status", "detail", "metadata"}
