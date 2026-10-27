import json

import httpx
import pytest

from axiom_sdk import AxiomClient


def test_submit_poll_terminal_never_resubmits():
    calls = []
    states = iter(["running", "cancel_requested", "completed"])

    def handler(request):
        calls.append((request.method, request.url.path, request))
        if request.method == "POST":
            assert request.headers["Idempotency-Key"] == "experiment-42"
            assert json.loads(request.content) == {
                "scenario_id": "scn_one", "adapter_id": "reference", "seed": 0,
                "parameter_overrides": {"initial_gap_m": 20},
            }
            return httpx.Response(202, json={"id": "job_one", "status": "queued"})
        return httpx.Response(200, json={"id": "job_one", "status": next(states), "run_id": "run_one"})

    with AxiomClient(transport=httpx.MockTransport(handler)) as api:
        submitted = api.submit_job("scn_one", idempotency_key="experiment-42", seed=0,
                                   parameter_overrides={"initial_gap_m": 20})
        result = api.wait_job(submitted["id"], poll_interval=0.001)
    assert result["status"] == "completed"
    assert [method for method, _, _ in calls] == ["POST", "GET", "GET", "GET"]


def test_timeout_retains_identity_without_cancellation_or_retry():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"id": "job_one", "status": "running"})

    with AxiomClient(transport=httpx.MockTransport(handler)) as api:
        with pytest.raises(TimeoutError, match="not cancelled or resubmitted"):
            api.wait_job("job_one", timeout=0)
    assert calls == [("GET", "/api/v1/jobs/job_one")]


@pytest.mark.parametrize("state", ["failed", "cancelled", "interrupted"])
def test_wait_returns_non_success_terminal_state(state):
    with AxiomClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"status": state}))) as api:
        assert api.wait_job("job_one")["status"] == state


@pytest.mark.parametrize("key", ["", "x" * 201, None])
def test_submit_requires_reusable_explicit_key(key):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail("must not submit"))) as api:
        with pytest.raises(ValueError):
            api.submit_job("scn_one", idempotency_key=key)


@pytest.mark.parametrize("kwargs", [{"timeout": -1}, {"timeout": float("inf")}, {"poll_interval": 0}, {"poll_interval": float("nan")}])
def test_wait_rejects_unbounded_or_busy_polling(kwargs):
    with AxiomClient() as api, pytest.raises(ValueError):
        api.wait_job("job_one", **kwargs)


def test_queue_filters_and_explicit_cancel_use_same_authenticated_api():
    calls = []

    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-token"
        calls.append((request.method, request.url.path, dict(request.url.params)))
        return httpx.Response(200, json={"items": [], "next_before_seq": None})

    with AxiomClient(token="test-token", transport=httpx.MockTransport(handler)) as api:
        api.jobs(status="running", limit=10, before_seq=40)
        api.cancel_job("job_one")
        api.workers()
        api.execution_resources()
    assert calls == [
        ("GET", "/api/v1/jobs", {"status": "running", "limit": "10", "before_seq": "40"}),
        ("POST", "/api/v1/jobs/job_one/cancel", {}),
        ("GET", "/api/v1/workers", {}),
        ("GET", "/api/v1/execution-resources", {}),
    ]


def test_reconciliation_requires_explicit_confirmation_and_preserves_note():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"adapter_id": "carla", "state": "idle"})

    with AxiomClient(transport=httpx.MockTransport(handler)) as api:
        for confirmed, note in [(False, "checked"), (True, "  ")]:
            with pytest.raises(ValueError):
                api.reconcile_execution_resource("carla", job_id="job_one", note=note, confirmed_stopped=confirmed)
        assert not calls
        api.reconcile_execution_resource("carla", job_id="job_one", note="Dispatcher stopped; remote cleanup independently checked", confirmed_stopped=True)
    assert len(calls) == 1 and calls[0].method == "POST"
    assert json.loads(calls[0].content)["job_id"] == "job_one"
