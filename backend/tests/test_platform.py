import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ade2e.adapters import AdapterInputError, Registry
from ade2e.database import ArtifactRow, Settings
from ade2e.main import create_app
from ade2e.reference import ReferenceAdapter, contact_time
from ade2e.schemas import ArtifactPayload, Scenario, SimulationResult
from ade2e.service import canonical_bytes, digest


def execute(client, scenario, **body):
    response = client.post("/api/v1/runs", json={"scenario_id": scenario["id"], **body})
    assert response.status_code == 201, response.text
    return response.json()


def test_deterministic_hashes_and_export(client, make_scenario):
    scenario = make_scenario(category="cut_in")
    first, second = execute(client, scenario), execute(client, scenario)
    assert first["id"] != second["id"]
    assert first["provenance"]["input_hash"] == second["provenance"]["input_hash"]
    assert first["provenance"]["output_hash"] == second["provenance"]["output_hash"]
    assert first["trajectory"] == second["trajectory"]
    assert first["metrics"] == second["metrics"]
    assert first["scenario_snapshot"] == scenario
    exported = client.get(f'/api/v1/runs/{first["id"]}/export')
    assert exported.status_code == 200
    assert exported.json() == first
    assert "attachment" in exported.headers["content-disposition"]
    assert first["provenance"]["input_hash"] == digest({"scenario": scenario, "adapter_id": "reference", "adapter_version": "0.1.0"})
    assert digest({"trajectory": first["trajectory"], "metrics": first["metrics"], "events": first["events"], "model_fidelity": first["provenance"]["model_fidelity"], "artifact_hashes": []}) == first["provenance"]["output_hash"]


def test_parameters_change_actual_trajectory_and_metrics(client, make_scenario):
    scenario = make_scenario(category="emergency_brake", parameters={"ego_speed_mps": 23, "target_speed_mps": 20, "initial_gap_m": 15, "trigger_time_s": 1, "response_delay_s": 1.4, "friction": 0.3})
    short = execute(client, scenario)
    long = execute(client, scenario, parameter_overrides={"initial_gap_m": 100, "response_delay_s": 0.1, "ego_speed_mps": 12})
    assert short["verdict"] == "failed"
    assert long["verdict"] == "passed"
    assert short["trajectory"] != long["trajectory"]
    assert short["metrics"] != long["metrics"]
    assert short["provenance"]["output_hash"] != long["provenance"]["output_hash"]
    assert client.get(f'/api/v1/scenarios/{scenario["id"]}').json()["parameters"] == scenario["parameters"]


def test_friction_and_delay_change_physical_outputs(client, make_scenario):
    scenario = make_scenario(category="emergency_brake", parameters={"ego_speed_mps": 23, "target_speed_mps": 20, "initial_gap_m": 30, "trigger_time_s": 1, "response_delay_s": 0.2, "friction": 0.3})
    original = execute(client, scenario)
    wet = execute(client, scenario, parameter_overrides={"friction": 0.9})
    late = execute(client, scenario, parameter_overrides={"response_delay_s": 2.0})
    assert original["trajectory"] != wet["trajectory"]
    assert original["metrics"] != wet["metrics"]
    assert original["trajectory"] != late["trajectory"]
    assert original["metrics"] != late["metrics"]


def test_weather_metadata_does_not_claim_sensor_physics(client, make_scenario):
    scenario = make_scenario()
    before = execute(client, scenario)
    changed = client.patch(f'/api/v1/scenarios/{scenario["id"]}', json={"weather": "fog", "time_of_day": "night"}).json()
    after = execute(client, changed)
    assert before["provenance"]["input_hash"] != after["provenance"]["input_hash"]
    assert before["provenance"]["output_hash"] == after["provenance"]["output_hash"]
    assert "no sensor" in after["provenance"]["model_fidelity"]


@pytest.mark.parametrize("payload", [
    {"name": ""}, {"name": "X", "category": "unsupported"},
    {"name": "X", "parameters": {"ego_speed_mps": -1}},
    {"name": "X", "parameters": {"friction": 0}},
    {"name": "X", "duration_s": 3, "parameters": {"trigger_time_s": 3}},
    {"name": "X", "seed": -1}, {"name": "X", "unknown": 3},
])
def test_invalid_scenario_validation(client, payload):
    assert client.post("/api/v1/scenarios", json=payload).status_code == 422
    assert client.get("/api/v1/scenarios").json() == []


def test_invalid_references_and_adapter_fail_without_run(client, make_scenario):
    scenario = make_scenario()
    assert client.post("/api/v1/runs", json={"scenario_id": "missing"}).status_code == 404
    for adapter_id in ("missing", "carla"):
        assert client.post("/api/v1/runs", json={"scenario_id": scenario["id"], "adapter_id": adapter_id}).status_code == 422
    assert client.get("/api/v1/runs").json() == []
    assert client.post("/api/v1/requirements", json={"name": "invalid", "metric_name": "collision_count", "operator": "eq", "threshold": 0, "scenario_ids": ["missing"]}).status_code == 404
    assert client.get("/api/v1/requirements").json() == []
    assert client.get("/api/v1/runs/missing").status_code == 404
    assert client.get("/api/v1/artifacts/missing/download").status_code == 404


def test_null_patch_override_and_trigger_validation(client, make_scenario):
    scenario = make_scenario()
    path = f'/api/v1/scenarios/{scenario["id"]}'
    for body in ({"parameters": {"friction": None}}, {"name": None}, {"parameters": None}, {"duration_s": 2}):
        assert client.patch(path, json=body).status_code == 422
    assert client.post("/api/v1/runs", json={"scenario_id": scenario["id"], "parameter_overrides": {"friction": None}}).status_code == 422
    assert client.post("/api/v1/runs", json={"scenario_id": scenario["id"], "parameter_overrides": {"trigger_time_s": 50}}).status_code == 422
    assert client.get(path).json()["revision"] == 1


def test_idempotency_conflict_replay_and_concurrency(client, make_scenario):
    scenario = make_scenario()
    body, headers = {"scenario_id": scenario["id"]}, {"Idempotency-Key": "logical-request-1"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.post("/api/v1/runs", json=body, headers=headers), range(4)))
    assert {response.status_code for response in responses} == {201}
    assert len({response.json()["id"] for response in responses}) == 1
    assert len(client.get("/api/v1/runs").json()) == 1
    changed = client.post("/api/v1/runs", json={**body, "seed": 44}, headers=headers)
    assert changed.status_code == 409
    client.patch(f'/api/v1/scenarios/{scenario["id"]}', json={"name": "Changed"})
    replay = client.post("/api/v1/runs", json=body, headers=headers).json()
    assert replay["id"] == responses[0].json()["id"]
    assert replay["scenario_revision"] == 1


def test_persistence_revisions_and_independent_clone(settings):
    with TestClient(create_app(settings, Registry(load_plugins=False))) as client:
        scenario = client.post("/api/v1/scenarios", json={"name": "Original"}).json()
        run = execute(client, scenario)
        revised = client.patch(f'/api/v1/scenarios/{scenario["id"]}', json={"parameters": {"friction": 0.4}}).json()
        assert revised["revision"] == 2
        assert revised["parameters"]["ego_speed_mps"] == scenario["parameters"]["ego_speed_mps"]
        clone = client.post(f'/api/v1/scenarios/{scenario["id"]}/clone').json()
        assert clone["id"] != scenario["id"] and clone["revision"] == 1
    with TestClient(create_app(settings, Registry(load_plugins=False))) as client:
        assert len(client.get("/api/v1/scenarios").json()) == 2
        assert client.get(f'/api/v1/runs/{run["id"]}').json()["scenario_snapshot"] == scenario
        assert client.get(f'/api/v1/scenarios/{scenario["id"]}').json()["parameters"]["friction"] == 0.4
        assert [item["revision"] for item in client.get(f'/api/v1/scenarios/{scenario["id"]}/revisions').json()] == [2, 1]


def test_latest_current_baseline_evidence_no_cherry_picking(client, make_scenario):
    scenario = make_scenario(parameters={"ego_speed_mps": 10, "target_speed_mps": 10})
    requirement = client.post("/api/v1/requirements", json={"name": "Distance", "metric_name": "distance_m", "operator": "gte", "threshold": 100, "scenario_ids": [scenario["id"]]}).json()
    assert requirement["status"] == "untested"
    first = execute(client, scenario)
    current = lambda: client.get("/api/v1/requirements").json()[0]
    assert current()["status"] == "passed" and current()["last_run_id"] == first["id"]
    variant = execute(client, scenario, parameter_overrides={"ego_speed_mps": 2})
    assert current()["status"] == "untested" and current()["evidence_count"] == 0
    revised = client.patch(f'/api/v1/scenarios/{scenario["id"]}', json={"parameters": {"ego_speed_mps": 2}}).json()
    assert current()["status"] == "untested"
    latest = execute(client, revised)
    assert current()["status"] == "failed" and current()["last_run_id"] == latest["id"]
    assert current()["last_run_id"] != first["id"]


def test_dynamic_category_new_unrun_scenario_reduces_coverage(client, make_scenario):
    scenario = make_scenario()
    execute(client, scenario)
    created = client.post("/api/v1/requirements", json={"name": "All lane following", "metric_name": "collision_count", "operator": "eq", "threshold": 0, "category": "lane_follow"}).json()
    assert created["status"] == "passed"
    make_scenario(name="New uncovered baseline")
    requirement = client.get("/api/v1/requirements").json()[0]
    assert requirement["status"] == "untested" and requirement["evidence_count"] == 1
    assert requirement["linked_scenario_count"] == 2
    assert client.get("/api/v1/overview").json()["covered_requirement_count"] == 0


def test_artifact_bytes_hash_and_tamper_detection(client, make_scenario):
    run = execute(client, make_scenario())
    artifact = run["artifacts"][0]
    response = client.get(artifact["download_url"])
    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
    assert len(response.content) == artifact["size_bytes"]
    assert response.json()["provenance"] == run["provenance"]
    with client.app.state.db.session() as session:
        row = session.get(ArtifactRow, artifact["id"])
        path = client.app.state.db.artifact_dir / row.storage_key
        path.write_bytes(b"tampered")
    assert client.get(artifact["download_url"]).status_code == 409


def test_storage_path_traversal_rejected(client, make_scenario, tmp_path):
    run = execute(client, make_scenario())
    artifact = run["artifacts"][0]
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    with client.app.state.db.session.begin() as session:
        row = session.get(ArtifactRow, artifact["id"])
        row.storage_key = "../outside.txt"
    assert client.get(artifact["download_url"]).status_code == 404


def test_registered_adapter_executes_and_persists_sensor_bytes(settings):
    class Plugin(ReferenceAdapter):
        def metadata(self):
            return super().metadata().model_copy(update={"id": "test-plugin", "name": "Fixture adapter"})
        def execute(self, scenario):
            result = super().execute(scenario)
            result.artifact_payloads = [ArtifactPayload(name="../../unsafe-name.json", kind="fixture", media_type="application/json", content=b'{"fixture":true}')]
            return result
    registry = Registry(load_plugins=False)
    registry.register(Plugin())
    with TestClient(create_app(settings, registry)) as client:
        scenario = client.post("/api/v1/scenarios", json={"name": "Plugin"}).json()
        run = execute(client, scenario, adapter_id="test-plugin")
        assert run["adapter_id"] == "test-plugin"
        assert run["artifacts"][0]["source"] == "simulation/test-plugin"
        assert client.get(run["artifacts"][0]["download_url"]).content == b'{"fixture":true}'


def test_adapter_errors_are_persisted_honestly(settings):
    class Broken(ReferenceAdapter):
        def execute(self, scenario):
            raise RuntimeError("private dependency details")
    registry = Registry(load_plugins=False)
    registry.register(Broken(), replace=True)
    with TestClient(create_app(settings, registry)) as client:
        scenario = client.post("/api/v1/scenarios", json={"name": "Failure"}).json()
        run = execute(client, scenario)
        assert run["status"] == "error" and run["verdict"] == "error"
        assert run["trajectory"] == [] and run["metrics"] == []
        assert "private dependency" not in json.dumps(run)
        assert run["artifacts"]


def test_adapter_input_error_returns_422_without_failed_run(settings):
    class Unsupported(ReferenceAdapter):
        def execute(self, scenario):
            raise AdapterInputError("Category is not supported")
    registry = Registry(load_plugins=False)
    registry.register(Unsupported(), replace=True)
    with TestClient(create_app(settings, registry)) as client:
        scenario = client.post("/api/v1/scenarios", json={"name": "Unsupported"}).json()
        response = client.post("/api/v1/runs", json={"scenario_id": scenario["id"]})
        assert response.status_code == 422
        assert client.get("/api/v1/runs").json() == []


@pytest.mark.parametrize("fault", ["truncated", "missing_metric", "false_predicate", "negative_speed"])
def test_incomplete_adapter_result_cannot_be_passing_evidence(settings, fault):
    class Incomplete(ReferenceAdapter):
        def execute(self, scenario):
            result = super().execute(scenario)
            if fault == "truncated":
                result.trajectory = result.trajectory[:-1]
            elif fault == "missing_metric":
                result.metrics = result.metrics[:-1]
            elif fault == "negative_speed":
                result.trajectory[0].ego.speed_mps = -1
            else:
                result.metrics[0].passed = not result.metrics[0].passed
            return result
    registry = Registry(load_plugins=False)
    registry.register(Incomplete(), replace=True)
    with TestClient(create_app(settings, registry)) as client:
        scenario = client.post("/api/v1/scenarios", json={"name": "Incomplete"}).json()
        run = execute(client, scenario)
        assert run["status"] == "error" and run["verdict"] == "error"
        assert run["metrics"] == []


def test_auth_guards_all_data_routes(tmp_path):
    settings = Settings(data_dir=tmp_path, api_token="local-test-secret", seed_examples=False)
    with TestClient(create_app(settings, Registry(load_plugins=False))) as client:
        for path in ("overview", "scenarios", "runs", "requirements", "artifacts", "adapters", "agent/tools"):
            assert client.get(f"/api/v1/{path}").status_code == 401
            assert client.get(f"/api/v1/{path}", headers={"Authorization": "Bearer wrong"}).status_code == 401
            assert client.get(f"/api/v1/{path}", headers={"Authorization": "Bearer local-test-secret"}).status_code == 200
        assert client.post("/api/v1/scenarios", json={"name": "Unauthorized"}).status_code == 401
        assert client.get("/health").status_code == 200
        assert client.get("/openapi.json").status_code == 200


def test_seeded_analytics_are_real_and_seed_is_not_repeated(tmp_path):
    settings = Settings(data_dir=tmp_path)
    for _ in range(2):
        with TestClient(create_app(settings, Registry(load_plugins=False))) as client:
            runs = client.get("/api/v1/runs").json()
            overview = client.get("/api/v1/overview").json()
            assert overview["scenario_count"] == 8 and overview["run_count"] == 8
            assert overview["pass_rate"] == sum(run["verdict"] == "passed" for run in runs) / 8 * 100
            assert {run["verdict"] for run in runs} == {"passed", "failed"}
            assert overview["artifact_count"] == 8
            assert {item["status"] for item in client.get("/api/v1/requirements").json()} == {"passed", "failed", "untested"}
            assert sum(cell["total"] for cell in overview["coverage"]) == 8
            assert sum(cell["passed"] for cell in overview["coverage"]) == sum(run["verdict"] == "passed" for run in runs)


def test_continuous_collision_geometry():
    assert contact_time(10, 0, -5, 0, 4.5, 1.9) == pytest.approx(1.1)
    assert contact_time(10, 3.7, -5, 0, 4.5, 1.9) is None
    assert contact_time(0, 0, 0, 0, 4.5, 1.9) == 0
    # Swept contact catches a small actor crossing between stored samples.
    assert contact_time(10, 0, -20, 0, 2.55, 1.25) == pytest.approx(0.3725)


def test_agent_discovery_and_openapi(client):
    tools = client.get("/api/v1/agent/tools").json()["tools"]
    assert any(item["name"] == "execute_scenario" and not item["read_only"] for item in tools)
    assert client.get("/openapi.json").json()["paths"]["/api/v1/runs"]["post"]["requestBody"]
    assert "Idempotency-Key" in client.get("/llms.txt").text
