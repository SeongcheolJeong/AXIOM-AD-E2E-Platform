"""HTTP boundaries; passing these tests does not validate a CARLA server."""
import copy
import json

import httpx
import pytest

from ade2e.adapters import AdapterExecutionError, AdapterInputError, AdapterUnavailable
from axiom_carla.remote import RemoteCarlaAdapter
from axiom_carla.version import ADAPTER_VERSION
from axiom_carla.wire import RESULT_SCHEMA


def ready():
    return {"status": "ready", "worker_adapter_version": ADAPTER_VERSION}


def result_envelope(payload):
    return {"schema_version": RESULT_SCHEMA, "worker_adapter_version": ADAPTER_VERSION, "result": payload}


def test_preflight_then_execution_forwards_token_and_exact_scenario(carla_scenario, worker_payload):
    calls = []

    def worker(request):
        calls.append(request)
        assert request.url.host == "worker.test"
        assert request.headers["Authorization"] == "Bearer worker-secret"
        if request.method == "GET":
            return httpx.Response(200, json=ready())
        assert json.loads(request.content) == carla_scenario.model_dump(mode="json")
        return httpx.Response(200, json=result_envelope(worker_payload))

    adapter = RemoteCarlaAdapter("https://worker.test", token="worker-secret", transport=httpx.MockTransport(worker))
    result = adapter.execute(carla_scenario)
    assert [(r.method, r.url.path) for r in calls] == [("GET", "/health"), ("POST", "/v1/simulate")]
    assert result.artifact_payloads[0].content == b"\x00\xffCAR"
    assert result.trajectory[0].ego.y == -2
    assert "no engine execution" in result.model_fidelity


def test_non_ready_worker_never_runs_or_falls_back(carla_scenario):
    calls = []

    def worker(request):
        calls.append(request)
        return httpx.Response(200, json={"status": "not_ready", "detail": "engine offline", "worker_adapter_version": ADAPTER_VERSION})

    adapter = RemoteCarlaAdapter("http://worker.test", transport=httpx.MockTransport(worker))
    with pytest.raises(AdapterUnavailable, match="engine offline"):
        adapter.execute(carla_scenario)
    assert len(calls) == 1
    assert calls[0].url.path == "/health"


@pytest.mark.parametrize("category", ["cut_in", "pedestrian_crossing"])
def test_unsupported_scenario_is_rejected_before_network(category, carla_scenario):
    def network_must_not_run(request):
        pytest.fail("Unsupported scenario reached an engine")

    scenario = carla_scenario.model_copy(update={"category": category})
    adapter = RemoteCarlaAdapter("http://worker.test", transport=httpx.MockTransport(network_must_not_run))
    with pytest.raises(AdapterInputError):
        adapter.execute(scenario)


@pytest.mark.parametrize("url", ["ftp://worker.test", "http://name:password@worker.test", "http:///missing-host", "http://[broken-ipv6"])
def test_invalid_worker_url_is_rejected_without_network(url, carla_scenario):
    def network_must_not_run(request):
        pytest.fail("Invalid URL reached network")

    with pytest.raises(AdapterUnavailable):
        RemoteCarlaAdapter(url, transport=httpx.MockTransport(network_must_not_run)).execute(carla_scenario)


def test_redirect_does_not_forward_worker_credential(carla_scenario):
    calls = []

    def worker(request):
        calls.append(request)
        return httpx.Response(307, headers={"Location": "https://external.test/collect"})

    with pytest.raises(AdapterUnavailable):
        RemoteCarlaAdapter("https://worker.test", token="worker-secret", transport=httpx.MockTransport(worker)).execute(carla_scenario)
    assert len(calls) == 1
    assert calls[0].url.host == "worker.test"


@pytest.mark.parametrize("status", [409, 422])
def test_worker_rejections_keep_their_reason_without_retry(status, carla_scenario):
    calls = []

    def worker(request):
        calls.append(request)
        return httpx.Response(200, json=ready()) if request.method == "GET" else httpx.Response(status, json={"detail": "unsupported map"})

    with pytest.raises(AdapterInputError, match="unsupported map"):
        RemoteCarlaAdapter("http://worker.test", transport=httpx.MockTransport(worker)).execute(carla_scenario)
    assert len(calls) == 2


def test_network_failure_is_not_retried_or_replaced(carla_scenario):
    calls = []

    def worker(request):
        calls.append(request)
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(AdapterUnavailable):
        RemoteCarlaAdapter("http://worker.test", transport=httpx.MockTransport(worker)).execute(carla_scenario)
    assert len(calls) == 1


@pytest.mark.parametrize("stage", ["health", "execution"])
@pytest.mark.parametrize("bad_body", [b"not-json", b"[]", b"null"])
def test_malformed_worker_response_preserves_execution_boundary(stage, bad_body, carla_scenario):
    def worker(request):
        if stage == "execution" and request.method == "GET":
            return httpx.Response(200, json=ready())
        return httpx.Response(200, content=bad_body)

    with pytest.raises(AdapterExecutionError if stage == "execution" else AdapterUnavailable):
        RemoteCarlaAdapter("http://worker.test", transport=httpx.MockTransport(worker)).execute(carla_scenario)


@pytest.mark.parametrize("corruption", ["base64", "nonmonotonic_frames", "nonfinite_state"])
def test_invalid_success_payload_is_rejected(corruption, carla_scenario, worker_payload):
    payload = copy.deepcopy(worker_payload)
    if corruption == "base64":
        payload["artifact_payloads"][0]["content_base64"] = "***not-base64***"
    elif corruption == "nonmonotonic_frames":
        payload["trajectory"].append(copy.deepcopy(payload["trajectory"][0]))
    else:
        payload["trajectory"][0]["ego"]["x"] = "NaN"

    def worker(request):
        return httpx.Response(200, json=ready() if request.method == "GET" else result_envelope(payload))

    with pytest.raises(AdapterExecutionError):
        RemoteCarlaAdapter("http://worker.test", transport=httpx.MockTransport(worker)).execute(carla_scenario)
