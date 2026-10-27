"""Coverage separates registered scope, executed evidence and missing evidence."""

import pytest

from ade2e.reference import ReferenceAdapter


def _execute(client, scenario, **overrides):
    response = client.post("/api/v1/runs", json={"scenario_id": scenario["id"], **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def _cell(client, category="lane_follow", weather="clear"):
    cells = client.get("/api/v1/overview").json()["coverage"]
    for cell in cells:
        assert cell["total"] == cell["tested"] + cell["untested"]
        assert cell["tested"] == cell["passed"] + cell["failed"]
    return next(cell for cell in cells if cell["category"] == category and cell["weather"] == weather)


def _counts(cell):
    return {key: cell[key] for key in ("total", "tested", "passed", "failed", "untested")}


def test_unrun_scenario_is_registered_but_neither_tested_nor_failed(client, make_scenario):
    make_scenario()
    assert _counts(_cell(client)) == {"total": 1, "tested": 0, "passed": 0, "failed": 0, "untested": 1}
    assert _counts(_cell(client, weather="fog")) == {"total": 0, "tested": 0, "passed": 0, "failed": 0, "untested": 0}


@pytest.mark.parametrize("invalid_latest", ["stale_revision", "parameter_override", "seed_override", "execution_error"])
def test_invalid_latest_evidence_does_not_fall_back_to_older_pass(client, make_scenario, invalid_latest):
    scenario = make_scenario(parameters={"ego_speed_mps": 15, "target_speed_mps": 15, "initial_gap_m": 50})
    run = _execute(client, scenario)
    assert run["verdict"] == "passed"
    assert _counts(_cell(client)) == {"total": 1, "tested": 1, "passed": 1, "failed": 0, "untested": 0}
    if invalid_latest == "stale_revision":
        response = client.patch(f'/api/v1/scenarios/{scenario["id"]}', json={"description": "A new authoring revision"})
        assert response.status_code == 200
    elif invalid_latest == "parameter_override":
        _execute(client, scenario, parameter_overrides={"initial_gap_m": 100})
    elif invalid_latest == "seed_override":
        _execute(client, scenario, seed=scenario["seed"] + 1)
    else:
        class BrokenAdapter(ReferenceAdapter):
            def execute(self, scenario):
                raise RuntimeError("Injected latest execution failure")
        client.app.state.registry.register(BrokenAdapter(), replace=True)
        assert _execute(client, scenario)["status"] == "error"
    assert _counts(_cell(client)) == {"total": 1, "tested": 0, "passed": 0, "failed": 0, "untested": 1}


def test_mixed_cell_and_requirements_share_current_baseline_scope(client, make_scenario):
    safe = {"ego_speed_mps": 15, "target_speed_mps": 15, "initial_gap_m": 50}
    scenarios = {name: make_scenario(name=name, parameters=safe) for name in ("passed", "unrun", "stale", "variant", "error")}
    scenarios["failed"] = make_scenario(name="failed", parameters={"ego_speed_mps": 20, "target_speed_mps": 0, "initial_gap_m": 1, "response_delay_s": 3})
    for name in ("passed", "stale", "variant", "error"):
        assert _execute(client, scenarios[name])["verdict"] == "passed"
    assert _execute(client, scenarios["failed"])["verdict"] == "failed"
    assert client.patch(f'/api/v1/scenarios/{scenarios["stale"]["id"]}', json={"description": "Edited scope"}).status_code == 200
    _execute(client, scenarios["variant"], parameter_overrides={"initial_gap_m": 100})

    class BrokenAdapter(ReferenceAdapter):
        def execute(self, scenario):
            raise RuntimeError("Injected latest execution failure")
    client.app.state.registry.register(BrokenAdapter(), replace=True)
    assert _execute(client, scenarios["error"])["verdict"] == "error"
    assert _counts(_cell(client)) == {"total": 6, "tested": 2, "passed": 1, "failed": 1, "untested": 4}
    requirement = client.post("/api/v1/requirements", json={"name": "Collision baseline", "metric_name": "collision_count", "operator": "eq", "threshold": 0, "category": "lane_follow"}).json()
    assert requirement["linked_scenario_count"] == 6
    assert requirement["evidence_count"] == 2
    assert requirement["status"] == "failed"


def test_new_scenario_changes_total_without_changing_tested_count(client, make_scenario):
    existing = make_scenario(parameters={"ego_speed_mps": 15, "target_speed_mps": 15, "initial_gap_m": 50})
    assert _execute(client, existing)["verdict"] == "passed"
    make_scenario(name="New pending scenario")
    assert _counts(_cell(client)) == {"total": 2, "tested": 1, "passed": 1, "failed": 0, "untested": 1}
