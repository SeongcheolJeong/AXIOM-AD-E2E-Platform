"""Actual HTTP/DTO composition with authored sensor fixtures; no CARLA engine."""
import copy
import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from ade2e.adapters import AdapterExecutionError, AdapterInputError, AdapterUnavailable
from ade2e.database import Database, Settings
from ade2e.schemas import ArtifactPayload, SimulationResult
from ade2e.sensor_schemas import RigCreate
from ade2e.sensors import rig_create
from ade2e.simulation_context import (SimulationControlSelection, SimulationExecutionRequest,
    build_simulation_context, simulation_input_hash)
from ade2e.stack_runtime import StackRegistry
from ade2e.stack_schemas import StackCreate
from ade2e.stacks import create_stack
from axiom_carla import wire, worker
from axiom_carla.remote import RemoteCarlaAdapter
from axiom_carla.version import ADAPTER_VERSION
from test_live_stack import install_live_world


@pytest.fixture
def execution_request(tmp_path, carla_scenario):
    db = Database(Settings(data_dir=tmp_path, seed_examples=False))
    db.initialize()
    registry = StackRegistry(load_plugins=False)
    selections = {meta.role: {"id": meta.id, "version": meta.version, "parameters": meta.default_parameters}
                  for meta in registry.list() if meta.version == '1'}
    stack = create_stack(db, registry, StackCreate(name="Authored protocol stack", note="No engine evidence",
                         definition={"reference_frame": "ego", **selections}))
    rig = rig_create(db, RigCreate(name="Authored protocol rig", note="Declared fixture mount",
        reference_frame="ego", coordinate_convention="right_handed_z_up", source_kind="synthetic",
        sensors=[{"key": "roof", "name": "Roof LiDAR", "kind": "lidar", "frame_id": "lidar_roof",
                  "transform": {"translation_m": [0, 0, 2.2], "rotation_xyzw": [0, 0, 0, 1]}}]))
    selection = SimulationControlSelection(stack_id=stack.id, stack_revision=1, rig_id=rig.id,
                                           rig_revision=1, lidar_sensor_key="roof")
    context = build_simulation_context(stack, rig, selection, carla_scenario.seed)
    return SimulationExecutionRequest(execution_id="run_reserved", scenario=carla_scenario, context=context,
        input_hash=simulation_input_hash(carla_scenario, "carla", ADAPTER_VERSION, context))


@pytest.fixture
def result(execution_request, monkeypatch):
    install_live_world(monkeypatch)
    return worker.CarlaRunner().execute_with_context(execution_request)


def install_runner(monkeypatch, result):
    monkeypatch.delenv("ADE2E_CARLA_WORKER_TOKEN", raising=False)
    monkeypatch.setattr(worker, "_lock", threading.Lock())
    health = {"status": "ready", "version": "matching-native-build", "map": "Carla/Maps/Town01"}
    monkeypatch.setattr(worker.CarlaRunner, "preflight", lambda self: health.copy())
    monkeypatch.setattr(worker.CarlaRunner, "preflight_with_context", lambda self, scenario, context: health.copy(), raising=False)
    monkeypatch.setattr(worker.CarlaRunner, "execute_with_context", lambda self, request: result.model_copy(deep=True), raising=False)


def proxy(client, calls, mutate=None):
    def handler(request):
        calls.append(request.url.path)
        response = client.request(request.method, request.url.path, content=request.content,
                                  headers=dict(request.headers))
        if mutate:
            return mutate(request, response)
        return response
    return httpx.MockTransport(handler)


def test_v2_roundtrip_pins_context_reserved_identity_and_keeps_sources(execution_request, result, monkeypatch):
    install_runner(monkeypatch, result)
    calls = []
    with TestClient(worker.app) as client:
        adapter = RemoteCarlaAdapter("http://worker.test", transport=proxy(client, calls))
        received = adapter.execute_with_context(execution_request)
    assert [(p.name, p.content) for p in received.artifact_payloads] == [(p.name, p.content) for p in result.artifact_payloads]
    assert calls == ["/health", "/v2/preflight", "/health", "/v2/simulate"]
    assert not worker._lock.locked()


@pytest.mark.parametrize("field", ["input_hash", "context_hash", "worker_adapter_version", "kernel_version", "map", "version"])
def test_wrong_preflight_identity_never_dispatches(field, execution_request, result, monkeypatch):
    install_runner(monkeypatch, result)
    calls = []
    def mutate(request, response):
        if request.url.path == "/v2/preflight":
            data = response.json(); data[field] = "wrong"
            return httpx.Response(200, json=data)
        return response
    with TestClient(worker.app) as client:
        adapter = RemoteCarlaAdapter("http://worker.test", transport=proxy(client, calls, mutate))
        with pytest.raises(AdapterUnavailable, match="preflight"):
            adapter.execute_with_context(execution_request)
    assert "/v2/simulate" not in calls


@pytest.mark.parametrize("field", ["execution_id", "input_hash", "context_hash", "schema_version", "worker_adapter_version"])
@pytest.mark.parametrize("failure", [False, True])
def test_foreign_success_or_failure_identity_never_returns_artifacts(field, failure, execution_request, result, monkeypatch):
    install_runner(monkeypatch, result)
    calls = []
    def mutate(request, response):
        if request.url.path == "/v2/simulate":
            data = (wire.encode_failure("foreign sensitive detail", result.artifact_payloads, request=execution_request)
                    if failure else response.json())
            data[field] = "wrong"
            return httpx.Response(500 if failure else 200, json=data)
        return response
    with TestClient(worker.app) as client:
        adapter = RemoteCarlaAdapter("http://worker.test", transport=proxy(client, calls, mutate))
        with pytest.raises(AdapterExecutionError) as caught:
            adapter.execute_with_context(execution_request)
    assert caught.value.artifact_payloads == ()
    assert "foreign sensitive detail" not in str(caught.value)
    assert calls.count("/v2/simulate") == 1


def test_matching_attempted_failure_keeps_partial_source_and_unlocks(execution_request, result, monkeypatch):
    install_live_world(monkeypatch, fault="readback")
    with pytest.raises(AdapterExecutionError) as native:
        worker.CarlaRunner().execute_with_context(execution_request)
    partial = native.value.artifact_payloads
    install_runner(monkeypatch, result)
    def fail(self, request):
        raise AdapterExecutionError("No next physics frame was observed", artifact_payloads=partial)
    monkeypatch.setattr(worker.CarlaRunner, "execute_with_context", fail)
    with TestClient(worker.app) as client:
        adapter = RemoteCarlaAdapter("http://worker.test", transport=proxy(client, []))
        with pytest.raises(AdapterExecutionError) as caught:
            adapter.execute_with_context(execution_request)
    assert [(p.name, p.content) for p in caught.value.artifact_payloads] == [(p.name, p.content) for p in partial]
    assert not worker._lock.locked()


def test_changed_request_hash_is_rejected_before_network_or_runner(execution_request, result, monkeypatch):
    install_runner(monkeypatch, result)
    wrong = execution_request.model_copy(deep=True); wrong.input_hash = "0" * 64
    adapter = RemoteCarlaAdapter("http://worker.test", transport=httpx.MockTransport(lambda _: pytest.fail("Network used")))
    with pytest.raises(AdapterInputError, match="input hash"):
        adapter.execute_with_context(wrong)
    monkeypatch.setattr(worker.CarlaRunner, "execute_with_context", lambda *_: pytest.fail("World mutated"))
    with TestClient(worker.app) as client:
        response = client.post("/v2/simulate", json=wrong.model_dump(mode="json"))
    assert response.status_code == 422


def test_preflight_and_both_execution_modes_share_auth_and_lock(execution_request, result, monkeypatch):
    install_runner(monkeypatch, result)
    endpoints = [("/v2/preflight", {"scenario": execution_request.scenario.model_dump(mode="json"),
                                   "context": execution_request.context.model_dump(mode="json")}),
                 ("/v2/simulate", execution_request.model_dump(mode="json")),
                 ("/v1/simulate", execution_request.scenario.model_dump(mode="json"))]
    monkeypatch.setenv("ADE2E_CARLA_WORKER_TOKEN", "test-secret")
    with TestClient(worker.app) as client:
        for path, body in endpoints:
            assert client.post(path, json=body).status_code == 401
        worker._lock.acquire()
        try:
            for path, body in endpoints:
                assert client.post(path, json=body, headers={"Authorization": "Bearer test-secret"}).status_code == 409
        finally:
            worker._lock.release()


def test_native_mutation_cannot_change_response_attempt_identity(execution_request, result, monkeypatch):
    install_runner(monkeypatch, result)
    def execute(self, request):
        request.execution_id = "mutated"
        request.context.context_hash = "0" * 64
        return result
    monkeypatch.setattr(worker.CarlaRunner, "execute_with_context", execute)
    with TestClient(worker.app) as client:
        response = client.post("/v2/simulate", json=execution_request.model_dump(mode="json"))
    assert response.status_code == 200
    assert response.json()["execution_id"] == execution_request.execution_id
    assert response.json()["context_hash"] == execution_request.context.context_hash
    assert wire.decode_result(response.json(), request=execution_request).artifact_payloads[0].content == result.artifact_payloads[0].content


@pytest.mark.parametrize("failure", [False, True])
def test_matching_envelope_with_invalid_inner_chain_discards_artifacts(failure, execution_request, result, monkeypatch):
    install_runner(monkeypatch, result)
    invalid = [ArtifactPayload(name="carla-stack-trace.json", kind="live_stack_trace",
                               media_type="application/json", content=b'{"status":"partial"}')]
    def mutate(request, response):
        if request.url.path == "/v2/simulate":
            if failure:
                return httpx.Response(500, json=wire.encode_failure("unverified detail", invalid, request=execution_request))
            malformed = result.model_copy(update={"artifact_payloads": invalid})
            return httpx.Response(200, json=wire.encode_result(malformed, request=execution_request))
        return response
    with TestClient(worker.app) as client:
        adapter = RemoteCarlaAdapter("http://worker.test", transport=proxy(client, [], mutate))
        with pytest.raises(AdapterExecutionError) as caught:
            adapter.execute_with_context(execution_request)
    assert caught.value.artifact_payloads == ()


def test_v1_envelopes_cannot_be_accepted_for_context_execution(execution_request, result):
    with pytest.raises(ValueError, match="identity"):
        wire.decode_result(wire.encode_result(result), request=execution_request)
    with pytest.raises(ValueError, match="identity"):
        wire.decode_failure(wire.encode_failure("failed", result.artifact_payloads), request=execution_request)
