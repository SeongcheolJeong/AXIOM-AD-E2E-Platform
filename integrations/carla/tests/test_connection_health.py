import httpx
import pytest

from axiom_carla.remote import RemoteCarlaAdapter
from axiom_carla.version import ADAPTER_VERSION


def test_health_probe_calls_only_worker_health_with_short_timeout_and_bearer():
    calls = []

    def worker(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == "/health"
        assert request.headers["Authorization"] == "Bearer worker-secret"
        assert all(timeout <= 5 for timeout in request.extensions["timeout"].values())
        return httpx.Response(200, json={"status": "ready", "version": "0.9.16", "map": "Town04", "scope": "Connection only", "worker_adapter_version": ADAPTER_VERSION})

    adapter = RemoteCarlaAdapter("https://worker.test", token="worker-secret", transport=httpx.MockTransport(worker))
    before = adapter.metadata().model_dump()
    result = adapter.check_health()
    assert result["status"] == "ready"
    assert result["metadata"] == {"version": "0.9.16", "map": "Town04", "scope": "Connection only", "worker_adapter_version": ADAPTER_VERSION}
    assert len(calls) == 1
    assert adapter.metadata().model_dump() == before


def test_unconfigured_probe_does_not_attempt_network(monkeypatch):
    monkeypatch.delenv("ADE2E_CARLA_WORKER_URL", raising=False)
    adapter = RemoteCarlaAdapter(transport=httpx.MockTransport(lambda _: pytest.fail("Unconfigured probe reached network")))
    assert adapter.check_health()["status"] == "not_configured"


def test_worker_not_ready_returns_its_actual_reason():
    adapter = RemoteCarlaAdapter("http://worker.test", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"status": "not_ready", "detail": "CARLA client/server version mismatch", "worker_adapter_version": ADAPTER_VERSION})))
    result = adapter.check_health()
    assert result["status"] == "unreachable"
    assert "version mismatch" in result["detail"]


@pytest.mark.parametrize("status", [401, 503, 307])
def test_http_failures_are_normalized_and_redirects_never_followed(status):
    calls = []

    def worker(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://external.test/collect"}, content=b"private proxy body")

    result = RemoteCarlaAdapter("https://worker.test", token="secret", transport=httpx.MockTransport(worker)).check_health()
    assert result["status"] == "unreachable"
    assert str(status) in result["detail"]
    assert "private proxy body" not in result["detail"]
    assert len(calls) == 1
    assert calls[0].url.host == "worker.test"


@pytest.mark.parametrize("body", [b"not-json", b"[]", b"null", b'{"status":false}', b'{}'])
def test_malformed_health_response_never_becomes_ready(body):
    adapter = RemoteCarlaAdapter("http://worker.test", transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body)))
    assert adapter.check_health()["status"] == "unreachable"


def test_network_failure_is_normalized_without_retry_or_secret_echo():
    calls = []

    def worker(request):
        calls.append(request)
        raise httpx.ReadTimeout("secret-in-upstream-exception", request=request)

    result = RemoteCarlaAdapter("http://worker.test", transport=httpx.MockTransport(worker)).check_health()
    assert result["status"] == "unreachable"
    assert "ReadTimeout" in result["detail"]
    assert "secret-in-upstream-exception" not in result["detail"]
    assert len(calls) == 1


@pytest.mark.parametrize("url", ["http://name:secret@worker.test", "ftp://worker.test", "http://[broken-ipv6"])
def test_invalid_configuration_never_reaches_network_or_echoes_credentials(url):
    adapter = RemoteCarlaAdapter(url, transport=httpx.MockTransport(lambda _: pytest.fail("Invalid configuration reached network")))
    result = adapter.check_health()
    assert result["status"] == "unreachable"
    assert "secret" not in result["detail"]
