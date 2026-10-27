"""Pinned definitions, complete evidence denominators and atomic queue batches."""

import hashlib
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, inspect, select

from ade2e import jobs, suites
from ade2e.adapters import Registry
from ade2e.database import Database, RequirementRow, RunRow, SchemaVersionRow, Settings, SCHEMA_VERSION
from ade2e.job_models import JobRow
from ade2e.main import create_app
from ade2e.reference import ReferenceAdapter, compare_metric
from ade2e.schemas import ArtifactPayload
from ade2e.service import canonical_bytes
from ade2e.suite_models import BaselineRow, CampaignMemberRow, CampaignRow, SuiteRevisionRow, SuiteRow
from ade2e.suite_schemas import BaselineCreate
from ade2e.worker import run_once


def make_suite(client, *, count=2, name="Suite", threshold=0, metric="collision_count"):
    scenario = client.post("/api/v1/scenarios", json={"name": "Pinned scenario", "category": "lane_follow", "duration_s": 2,
                                                     "parameters": {"trigger_time_s": 1}}).json()
    requirement = client.post("/api/v1/requirements", json={"name": "Pinned criterion", "metric_name": metric, "operator": "eq" if metric == "collision_count" else "gte",
                                                           "threshold": threshold, "scenario_ids": [scenario["id"]]}).json()
    body = {"name": name, "cases": [{"key": f"case-{i}", "name": f"Case {i}", "scenario_id": scenario["id"],
                                    "scenario_revision": 1, "requirement_ids": [requirement["id"]], "seed": 40 + i} for i in range(count)]}
    response = client.post("/api/v1/suites", json=body)
    assert response.status_code == 201, response.text
    return response.json(), body, scenario, requirement


def campaign(client, suite, key="campaign", adapter_id="reference"):
    response = client.post("/api/v1/campaigns", json={"suite_id": suite["id"], "suite_revision": suite["revision"], "adapter_id": adapter_id, "label": key},
                           headers={"Idempotency-Key": key})
    assert response.status_code == 202, response.text
    return response.json()


def finish(client, report):
    for case in report["cases"]:
        result = run_once(client.app.state.db, client.app.state.registry, "suite-test-worker", job_id=case["job_id"])
        assert result is not None
    return client.get("/api/v1/campaigns/" + report["id"]).json()


def baseline(client, report, key="baseline"):
    response = client.post("/api/v1/baselines", json={"campaign_id": report["id"], "name": key, "note": "Observed test evidence, not approval"},
                           headers={"Idempotency-Key": key})
    assert response.status_code == 201, response.text
    return response.json()


def test_suite_pins_historical_scenario_and_requirement_definitions(client):
    suite, body, scenario, requirement = make_suite(client)
    original = deepcopy(suite)
    client.patch("/api/v1/scenarios/" + scenario["id"], json={"parameters": {"ego_speed_mps": 2}}).raise_for_status()
    with client.app.state.db.session.begin() as session:
        row = session.get(RequirementRow, requirement["id"])
        row.payload = {**row.payload, "threshold": 99}
    report = finish(client, campaign(client, suite))
    assert report["verdict"] == "passed" and report["evaluation_complete"]
    assert report["suite_snapshot"] == original
    assert report["cases"][0]["checks"][0]["threshold"] == 0
    assert client.get("/api/v1/suites/" + suite["id"]).json() == original
    revised = client.post(f'/api/v1/suites/{suite["id"]}/revisions', json={**body, "expected_revision": 1})
    assert revised.status_code == 201, revised.text
    assert revised.json()["revision"] == 2
    assert revised.json()["cases"][0]["scenario_revision"] == 1
    assert revised.json()["cases"][0]["requirements"][0]["definition"]["threshold"] == 99
    assert client.get(f'/api/v1/suites/{suite["id"]}/revisions').json()[1] == original
    assert client.post(f'/api/v1/suites/{suite["id"]}/revisions', json={**body, "expected_revision": 1}).status_code == 409


@pytest.mark.parametrize("fault", ["duplicate_key", "missing_revision", "missing_requirement", "duplicate_requirement", "out_of_scope", "invalid_override", "empty_criteria", "empty_cases"])
def test_invalid_suite_definitions_create_no_revision(client, fault):
    suite, body, scenario, requirement = make_suite(client)
    if fault == "duplicate_key": body["cases"][1]["key"] = body["cases"][0]["key"]
    elif fault == "missing_revision": body["cases"][0]["scenario_revision"] = 999
    elif fault == "missing_requirement": body["cases"][0]["requirement_ids"] = ["missing"]
    elif fault == "duplicate_requirement": body["cases"][0]["requirement_ids"] *= 2
    elif fault == "invalid_override": body["cases"][0]["parameter_overrides"] = {"trigger_time_s": 3}
    elif fault == "empty_criteria": body["cases"][0]["requirement_ids"] = []
    elif fault == "empty_cases": body["cases"] = []
    else:
        other = client.post("/api/v1/requirements", json={"name": "Wrong scope", "metric_name": "collision_count", "operator": "eq", "threshold": 0, "category": "cut_in"}).json()
        body["cases"][0]["requirement_ids"] = [other["id"]]
    response = client.post("/api/v1/suites", json=body)
    assert response.status_code == 422, response.text
    assert len(client.get("/api/v1/suites").json()) == 1


def test_category_requirement_applies_to_selected_historical_revision(client):
    suite, body, scenario, requirement = make_suite(client, count=1)
    scoped = client.post("/api/v1/requirements", json={"name": "Historical lane", "metric_name": "collision_count", "operator": "eq", "threshold": 0, "category": "lane_follow"}).json()
    client.patch("/api/v1/scenarios/" + scenario["id"], json={"category": "cut_in"}).raise_for_status()
    body["cases"][0]["requirement_ids"] = [scoped["id"]]
    assert client.post("/api/v1/suites", json=body).status_code == 201
    body["cases"][0]["scenario_revision"] = 2
    assert client.post("/api/v1/suites", json=body).status_code == 422


def test_one_pass_never_hides_pending_or_cancelled_cases(client):
    suite, *_ = make_suite(client, count=3)
    report = campaign(client, suite)
    assert report["counts"]["total"] == report["counts"]["checks_total"] == report["counts"]["pending"] == 3
    assert client.post("/api/v1/baselines", json={"campaign_id": report["id"], "name": "Too soon", "note": "pending"}, headers={"Idempotency-Key": "early"}).status_code == 409
    run_once(client.app.state.db, client.app.state.registry, "one-case", job_id=report["cases"][0]["job_id"])
    partial = client.get("/api/v1/campaigns/" + report["id"]).json()
    assert partial["status"] == "running" and partial["verdict"] == "pending"
    assert partial["counts"]["passed_cases"] == 1 and partial["counts"]["checks_not_evaluated"] == 2
    cancelled = client.post("/api/v1/campaigns/" + report["id"] + "/cancel").json()
    assert cancelled["status"] == "finished" and cancelled["verdict"] == "incomplete"
    assert cancelled["counts"]["completed"] == 1 and cancelled["counts"]["cancelled"] == 2
    assert cancelled["cases"][0]["run_id"] == partial["cases"][0]["run_id"]
    captured = baseline(client, cancelled)
    assert captured["verdict"] == "incomplete" and captured["total_cases"] == 3


def test_suite_evaluation_uses_pinned_predicate_not_engine_verdict(client):
    suite, *_ = make_suite(client, count=1, threshold=999, metric="distance_m")
    report = finish(client, campaign(client, suite))
    run = client.get("/api/v1/runs/" + report["cases"][0]["run_id"]).json()
    assert run["verdict"] == "passed"
    assert report["status"] == "finished" and report["verdict"] == "failed"
    assert report["counts"]["checks_failed"] == 1
    assert baseline(client, report)["verdict"] == "failed"


def test_campaign_batch_and_cancel_roll_back_completely(client, monkeypatch):
    suite, *_ = make_suite(client, count=3)
    original = jobs.insert_prepared_job
    calls = 0
    def fail_second(*args, **kwargs):
        nonlocal calls
        row = original(*args, **kwargs)
        calls += 1
        if calls == 2: raise OSError("Injected mid-batch failure")
        return row
    with monkeypatch.context() as patch:
        patch.setattr(jobs, "insert_prepared_job", fail_second)
        with pytest.raises(OSError): campaign(client, suite)
    with client.app.state.db.session() as session:
        for model in (CampaignRow, CampaignMemberRow, JobRow):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    report = campaign(client, suite)
    original_cancel = jobs.cancel_job_row
    calls = 0
    def fail_cancel(*args, **kwargs):
        nonlocal calls
        original_cancel(*args, **kwargs)
        calls += 1
        if calls == 2: raise OSError("Injected partial cancellation failure")
    with monkeypatch.context() as patch:
        patch.setattr(jobs, "cancel_job_row", fail_cancel)
        with pytest.raises(OSError): client.post("/api/v1/campaigns/" + report["id"] + "/cancel")
    assert {case["job_status"] for case in client.get("/api/v1/campaigns/" + report["id"]).json()["cases"]} == {"queued"}


def test_unavailable_or_known_unsupported_campaign_has_zero_jobs(client):
    suite, body, scenario, requirement = make_suite(client)
    for adapter in ("missing", "carla"):
        response = client.post("/api/v1/campaigns", json={"suite_id": suite["id"], "suite_revision": 1, "adapter_id": adapter, "label": adapter}, headers={"Idempotency-Key": adapter})
        assert response.status_code == 422
    class Unsupported(ReferenceAdapter):
        def metadata(self): return super().metadata().model_copy(update={"id": "unsupported", "capabilities": ["emergency_brake"]})
        def execute(self, scenario): raise AssertionError("Submission must not dispatch")
    client.app.state.registry.register(Unsupported())
    response = client.post("/api/v1/campaigns", json={"suite_id": suite["id"], "suite_revision": 1, "adapter_id": "unsupported", "label": "unsupported"}, headers={"Idempotency-Key": "unsupported"})
    assert response.status_code == 422
    assert client.get("/api/v1/jobs").json()["items"] == []


def test_concurrent_campaign_and_baseline_retries_commit_once(client):
    suite, *_ = make_suite(client, count=3)
    with ThreadPoolExecutor(max_workers=4) as pool:
        reports = list(pool.map(lambda _: campaign(client, suite), range(4)))
    assert len({report["id"] for report in reports}) == 1
    assert len(client.get("/api/v1/jobs").json()["items"]) == 3
    finished = finish(client, reports[0])
    with ThreadPoolExecutor(max_workers=4) as pool:
        captures = list(pool.map(lambda _: baseline(client, finished), range(4)))
    assert len({capture["id"] for capture in captures}) == 1
    assert len(client.get("/api/v1/baselines").json()) == 1
    # Replay happens before today's registry and authoring state are resolved.
    client.app.state.registry._adapters.clear()
    assert campaign(client, suite)["id"] == finished["id"]
    assert baseline(client, finished)["manifest_hash"] == captures[0]["manifest_hash"]
    conflict = client.post("/api/v1/campaigns", json={"suite_id": suite["id"], "suite_revision": 1, "label": "changed"}, headers={"Idempotency-Key": "campaign"})
    assert conflict.status_code == 409
    assert client.post("/api/v1/baselines", json={"campaign_id": finished["id"], "name": "changed", "note": "changed"}, headers={"Idempotency-Key": "baseline"}).status_code == 409


def test_baseline_exact_bytes_survive_new_definitions_runs_and_reopen(client, settings):
    suite, body, scenario, requirement = make_suite(client)
    captured = baseline(client, finish(client, campaign(client, suite)))
    content = client.get(captured["download_url"]).content
    assert hashlib.sha256(content).hexdigest() == captured["manifest_hash"]
    assert len(content) == captured["manifest_size_bytes"]
    client.patch("/api/v1/scenarios/" + scenario["id"], json={"name": "Later edit"}).raise_for_status()
    body["cases"][0]["scenario_revision"] = 2
    revised = client.post(f'/api/v1/suites/{suite["id"]}/revisions', json={**body, "expected_revision": 1}).json()
    finish(client, campaign(client, revised, "later"))
    assert client.get(captured["download_url"]).content == content
    with TestClient(create_app(settings, Registry(load_plugins=False))) as reopened:
        assert reopened.get(captured["download_url"]).content == content
        assert reopened.get("/api/v1/baselines/" + captured["id"]).json() == captured


@pytest.mark.parametrize("fault", ["verdict", "total_cases", "name", "note", "suite_revision", "campaign_id", "bad_json", "bad_shape"])
def test_baseline_metadata_and_structural_tampering_fails_closed(client, fault):
    suite, *_ = make_suite(client, count=1, threshold=999, metric="distance_m")
    captured = baseline(client, finish(client, campaign(client, suite)))
    with client.app.state.db.session.begin() as session:
        row = session.get(BaselineRow, captured["id"])
        if fault in {"bad_json", "bad_shape"}:
            row.manifest = b"invalid" if fault == "bad_json" else b'{}'
            row.manifest_hash = hashlib.sha256(row.manifest).hexdigest()
            row.manifest_size_bytes = len(row.manifest)
        else:
            value = {"verdict": "passed", "total_cases": 99, "name": "Wrong", "note": "Wrong", "suite_revision": 999, "campaign_id": "wrong"}[fault]
            row.summary = {**row.summary, fault: value}
    for path in ("/api/v1/baselines", "/api/v1/baselines/" + captured["id"], captured["download_url"]):
        assert client.get(path).status_code == 409


@pytest.mark.parametrize("fault", ["id", "revision", "case_count"])
def test_suite_metadata_is_checked_against_identity_and_denominator(client, fault):
    suite, *_ = make_suite(client)
    with client.app.state.db.session.begin() as session:
        row = session.get(SuiteRevisionRow, (suite["id"], 1))
        row.payload = {**row.payload, fault: "wrong" if fault == "id" else 99}
    assert client.get("/api/v1/suites/" + suite["id"]).status_code == 409


def test_changed_run_payload_is_unevaluated_and_custom_artifact_hashes_verify(client):
    class WithArtifact(ReferenceAdapter):
        def metadata(self): return super().metadata().model_copy(update={"id": "with-artifact"})
        def execute(self, scenario):
            result = super().execute(scenario)
            result.artifact_payloads = [ArtifactPayload(name="fixture.bin", kind="fixture", media_type="application/octet-stream", content=b"actual bytes")]
            return result
    client.app.state.registry.register(WithArtifact())
    suite, *_ = make_suite(client, count=1)
    report = finish(client, campaign(client, suite, adapter_id="with-artifact"))
    assert report["verdict"] == "passed"
    with client.app.state.db.session.begin() as session:
        row = session.get(RunRow, report["cases"][0]["run_id"])
        payload = deepcopy(row.payload)
        payload["metrics"][0]["value"] = 99
        row.payload = payload
    changed = client.get("/api/v1/campaigns/" + report["id"]).json()
    assert changed["verdict"] == "incomplete" and not changed["evaluation_complete"]
    assert "integrity" in changed["cases"][0]["checks"][0]["reason"]
    assert baseline(client, changed)["verdict"] == "incomplete"


def test_campaign_filter_before_pagination_and_single_report_evaluation(client, monkeypatch):
    selected, *_ = make_suite(client, name="Selected")
    other, *_ = make_suite(client, name="Other")
    first, second = campaign(client, selected, "first"), campaign(client, selected, "second")
    campaign(client, other, "third")
    original = suites.campaign_report
    calls = []
    def observe(session, campaign_id):
        calls.append(campaign_id)
        return original(session, campaign_id)
    monkeypatch.setattr(suites, "campaign_report", observe)
    page = client.get("/api/v1/campaigns", params={"suite_id": selected["id"], "limit": 1}).json()
    assert [item["id"] for item in page["items"]] == [second["id"]]
    assert calls == [second["id"]]
    next_page = client.get("/api/v1/campaigns", params={"suite_id": selected["id"], "limit": 1, "before_seq": page["next_before_seq"]}).json()
    assert [item["id"] for item in next_page["items"]] == [first["id"]]
    assert next_page["next_before_seq"] is None


def test_suite_campaign_baseline_auth_and_required_idempotency(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, seed_examples=False, api_token="secret"), Registry(load_plugins=False))) as client:
        for path in ("suites", "campaigns", "baselines", "suites/missing", "campaigns/missing", "baselines/missing", "baselines/missing/export"):
            assert client.get("/api/v1/" + path).status_code == 401
        for path in ("suites", "campaigns", "baselines", "campaigns/missing/cancel"):
            assert client.post("/api/v1/" + path, json={}).status_code == 401
        client.headers["Authorization"] = "Bearer secret"
        suite, *_ = make_suite(client)
        assert client.post("/api/v1/campaigns", json={"suite_id": suite["id"], "suite_revision": 1, "label": "key required"}).status_code == 422
        assert client.post("/api/v1/baselines", json={"campaign_id": "missing", "name": "key", "note": "note"}).status_code == 422


class ReportedMetricFixture(ReferenceAdapter):
    """Controlled observer values for comparison policy, not physical evidence."""
    value = 10
    version = "fixture-1"
    fidelity = "test-only reported measurement; no physical validity"
    unit = "m"

    def metadata(self):
        return super().metadata().model_copy(update={"id": "measurement-fixture", "version": self.version})

    def execute(self, scenario):
        result = super().execute(scenario)
        metric = next(metric for metric in result.metrics if metric.name == "distance_m")
        metric.value, metric.unit = self.value, self.unit
        metric.passed = compare_metric(metric.value, metric.operator, metric.threshold, name=metric.name)
        result.model_fidelity = self.fidelity
        return result


def test_observed_regression_improvement_and_context_changes_are_distinct(client):
    adapter = ReportedMetricFixture()
    client.app.state.registry.register(adapter)
    suite, body, scenario, requirement = make_suite(client, count=1, metric="distance_m", threshold=8)
    passed = baseline(client, finish(client, campaign(client, suite, "pass", adapter.metadata().id)), "passed")
    adapter.value = 5
    failed = finish(client, campaign(client, suite, "fail", adapter.metadata().id))
    compared = client.get(f'/api/v1/campaigns/{failed["id"]}/compare', params={"baseline_id": passed["id"]}).json()
    assert compared["counts"]["regressed"] == 1
    distance = next(metric for metric in compared["rows"][0]["metrics"] if metric["metric_name"] == "distance_m")
    assert distance["delta"] == -5 and distance["unit"] == "m"
    failed_baseline = baseline(client, failed, "failed")
    adapter.value = 12
    improved = finish(client, campaign(client, suite, "improved", adapter.metadata().id))
    assert client.get(f'/api/v1/campaigns/{improved["id"]}/compare', params={"baseline_id": failed_baseline["id"]}).json()["counts"]["improved"] == 1
    adapter.version = "fixture-2"
    changed = finish(client, campaign(client, suite, "version", adapter.metadata().id))
    version = client.get(f'/api/v1/campaigns/{changed["id"]}/compare', params={"baseline_id": passed["id"]}).json()["rows"][0]
    assert version["status"] == "not_comparable" and version["adapter_version_changed"]
    assert any("measurement contract" in reason for reason in version["reasons"])
    assert version["metrics"] == []
    adapter.version, adapter.fidelity = "fixture-1", "different observer model"
    changed = finish(client, campaign(client, suite, "fidelity", adapter.metadata().id))
    fidelity = client.get(f'/api/v1/campaigns/{changed["id"]}/compare', params={"baseline_id": passed["id"]}).json()["rows"][0]
    assert fidelity["status"] == "not_comparable" and "Result fidelity changed" in fidelity["reasons"]


def test_comparison_preserves_case_union_and_rejects_changed_criteria(client):
    suite, body, scenario, requirement = make_suite(client, count=2)
    old = baseline(client, finish(client, campaign(client, suite)))
    with client.app.state.db.session.begin() as session:
        row = session.get(RequirementRow, requirement["id"])
        row.payload = {**row.payload, "threshold": 1}
    body["cases"] = [body["cases"][0], {**body["cases"][1], "key": "added"}]
    revised = client.post(f'/api/v1/suites/{suite["id"]}/revisions', json={**body, "expected_revision": 1}).json()
    current = finish(client, campaign(client, revised, "changed"))
    compared = client.get(f'/api/v1/campaigns/{current["id"]}/compare', params={"baseline_id": old["id"]}).json()
    assert compared["counts"]["total"] == 3
    assert compared["counts"]["added"] == compared["counts"]["removed"] == compared["counts"]["not_comparable"] == 1
    assert compared["counts"]["regressed"] == 0
    unchanged_key = next(row for row in compared["rows"] if row["key"] == "case-0")
    assert "Pinned requirement definitions changed" in unchanged_key["reasons"]
    other, *_ = make_suite(client, count=1)
    unrelated = finish(client, campaign(client, other, "other"))
    assert client.get(f'/api/v1/campaigns/{unrelated["id"]}/compare', params={"baseline_id": old["id"]}).status_code == 422


def test_null_ttc_has_no_fabricated_numeric_delta(client):
    suite, body, *_ = make_suite(client, count=1, metric="min_ttc_s", threshold=1)
    body["cases"][0]["parameter_overrides"] = {"ego_speed_mps": 15, "target_speed_mps": 15, "initial_gap_m": 70}
    suite = client.post(f'/api/v1/suites/{suite["id"]}/revisions', json={**body, "expected_revision": 1}).json()
    first = baseline(client, finish(client, campaign(client, suite)))
    second = finish(client, campaign(client, suite, "again"))
    compared = client.get(f'/api/v1/campaigns/{second["id"]}/compare', params={"baseline_id": first["id"]}).json()
    ttc = next(metric for metric in compared["rows"][0]["metrics"] if metric["metric_name"] == "min_ttc_s")
    assert ttc["baseline_value"] is None and ttc["current_value"] is None and ttc["delta"] is None
    assert first["manifest"]["report"]["cases"][0]["checks"][0]["unbounded"]
    assert "Infinity" not in json.dumps(compared) and "NaN" not in json.dumps(compared)


def test_failed_but_incomplete_criteria_never_become_comparable(client):
    adapter = ReportedMetricFixture()
    adapter.unit = "cm"
    client.app.state.registry.register(adapter)
    suite, body, scenario, _ = make_suite(client, count=1, threshold=1)
    distance = client.post("/api/v1/requirements", json={"name": "Distance in pinned SI unit", "metric_name": "distance_m", "operator": "gte",
                                                       "threshold": 8, "scenario_ids": [scenario["id"]]}).json()
    body["cases"][0]["requirement_ids"].append(distance["id"])
    suite = client.post(f'/api/v1/suites/{suite["id"]}/revisions', json={**body, "expected_revision": 1}).json()
    current = finish(client, campaign(client, suite, adapter_id=adapter.metadata().id))
    assert current["verdict"] == "failed" and not current["evaluation_complete"]
    assert current["cases"][0]["verdict"] == "failed" and not current["cases"][0]["evaluation_complete"]
    assert current["counts"]["checks_total"] == 2
    assert current["counts"]["checks_failed"] == current["counts"]["checks_not_evaluated"] == 1
    captured = baseline(client, current)
    comparison = client.get(f'/api/v1/campaigns/{current["id"]}/compare', params={"baseline_id": captured["id"]}).json()
    assert comparison["counts"]["not_evaluated"] == 1 and comparison["counts"]["unchanged_fail"] == 0


def test_baseline_commit_failure_preserves_key_for_retry(client):
    suite, *_ = make_suite(client, count=1)
    report = finish(client, campaign(client, suite))
    body = BaselineCreate(campaign_id=report["id"], name="Retry", note="Lost response")
    db = client.app.state.db
    def fail_commit(*_): raise OSError("Injected baseline commit failure")
    event.listen(db.session.class_, "before_commit", fail_commit)
    try:
        with pytest.raises(OSError): suites.create_baseline(db, body, "retry")
    finally:
        event.remove(db.session.class_, "before_commit", fail_commit)
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(BaselineRow)) == 0
    assert suites.create_baseline(db, body, "retry").campaign_id == report["id"]


def test_killed_campaign_worker_retains_interrupted_case_and_never_replays(tmp_path):
    from queue_worker_fixture import registry as process_registry
    marker = tmp_path / "dispatch-calls"
    settings = Settings(data_dir=tmp_path / "data", seed_examples=False)
    with TestClient(create_app(settings, process_registry(marker, 10))) as client:
        suite, *_ = make_suite(client, count=2)
        report = campaign(client, suite, adapter_id="observable-test")
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("queue_worker_fixture.py")),
                                    str(settings.data_dir), str(marker), "10", "1"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 8
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert marker.exists()
        finally:
            process.kill()
            process.communicate(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = client.get("/api/v1/campaigns/" + report["id"]).json()
            if current["counts"]["interrupted"] == 1: break
            time.sleep(0.05)
        assert current["counts"]["interrupted"] == 1 and current["counts"]["pending"] == 1
        assert marker.read_bytes() == b"dispatched\n"
        replay = campaign(client, suite, adapter_id="observable-test")
        assert [case["job_id"] for case in replay["cases"]] == [case["job_id"] for case in report["cases"]]
        cancelled = client.post("/api/v1/campaigns/" + report["id"] + "/cancel").json()
        captured = baseline(client, cancelled)
        assert captured["total_cases"] == 2 and captured["verdict"] == "incomplete"
        counts = captured["manifest"]["report"]["counts"]
        assert counts["interrupted"] == counts["cancelled"] == 1 and counts["checks_not_evaluated"] == 2
        assert run_once(client.app.state.db, client.app.state.registry, "later-worker") is None
        assert marker.read_bytes() == b"dispatched\n"


def test_schema3_migration_preserves_completed_jobs_and_run_artifacts(client):
    from ade2e.comparison_models import ComparisonEvidenceRow
    from ade2e.database import ArtifactRow
    db = client.app.state.db
    scenario = client.post("/api/v1/scenarios", json={"name": "Pre-suite history"}).json()
    run = client.post("/api/v1/runs", json={"scenario_id": scenario["id"]}, headers={"Idempotency-Key": "legacy"}).json()
    original_jobs = client.get("/api/v1/jobs").json()
    artifact = client.get(run["artifacts"][0]["download_url"]).content
    with db.engine.begin() as connection:
        for model in (ComparisonEvidenceRow, BaselineRow, CampaignMemberRow, CampaignRow, SuiteRevisionRow, SuiteRow):
            model.__table__.drop(connection)
        connection.execute(SchemaVersionRow.__table__.update().values(version=3))
    db.initialize()
    with db.session() as session:
        assert list(session.scalars(select(SchemaVersionRow.version))) == [SCHEMA_VERSION]
        assert session.get(RunRow, run["id"]).payload == run
        assert session.scalar(select(func.count()).select_from(ArtifactRow)) == 1
    assert client.get("/api/v1/jobs").json() == original_jobs
    assert client.get(run["artifacts"][0]["download_url"]).content == artifact
    assert "test_suites" in inspect(db.engine).get_table_names()
