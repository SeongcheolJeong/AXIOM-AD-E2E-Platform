"""Comparison captures use actual suites, jobs and reference computations."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select

from ade2e import comparisons
from ade2e.adapters import Registry
from ade2e.comparison_models import ComparisonEvidenceRow
from ade2e.comparison_schemas import ComparisonCreate
from ade2e.database import Settings
from ade2e.main import create_app
from ade2e.service import canonical_bytes
from ade2e.worker import run_once


def post(client, path, body, *, key=None, expected=201):
    response = client.post("/api/v1" + path, json=body,
                           headers={"Idempotency-Key": key} if key else {})
    assert response.status_code == expected, response.text
    return response.json()


def execute_campaign(client, campaign):
    db, registry = client.app.state.db, client.app.state.registry
    for case in campaign["cases"]:
        assert run_once(db, registry, "comparison-test-worker", job_id=case["job_id"]) is not None
    response = client.get(f"/api/v1/campaigns/{campaign['id']}")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def campaign_context(client):
    scenarios = [post(client, "/scenarios", {
        "name": f"Comparison {category}", "category": category, "duration_s": 2,
        "parameters": {"trigger_time_s": 0.5},
    }) for category in ("lane_follow", "cut_in", "emergency_brake", "pedestrian_crossing")]
    requirement = post(client, "/requirements", {
        "name": "Observed object collision count", "metric_name": "collision_count",
        "operator": "eq", "threshold": 0, "scenario_ids": [item["id"] for item in scenarios],
    })
    suite = post(client, "/suites", {
        "name": "Four behaviors, two seeds each", "cases": [
            {"key": f"case-{index}-{seed}", "name": f"{scenario['category']} seed {seed}",
             "scenario_id": scenario["id"], "scenario_revision": scenario["revision"],
             "seed": seed, "requirement_ids": [requirement["id"]]}
            for index, scenario in enumerate(scenarios) for seed in (71, 72)
        ],
    })
    baseline_campaign = post(client, "/campaigns", {
        "suite_id": suite["id"], "suite_revision": suite["revision"], "label": "Baseline run",
    }, key="baseline-campaign", expected=202)
    finished = execute_campaign(client, baseline_campaign)
    assert finished["status"] == "finished"
    baseline = post(client, "/baselines", {
        "campaign_id": finished["id"], "name": "Captured baseline", "note": "Actual reference run evidence",
    }, key="baseline")
    candidate = post(client, "/campaigns", {
        "suite_id": suite["id"], "suite_revision": suite["revision"], "label": "Candidate run",
    }, key="candidate-campaign", expected=202)
    return client, suite, baseline, candidate, scenarios


@pytest.fixture
def finished_context(campaign_context):
    client, suite, baseline, candidate, scenarios = campaign_context
    return client, suite, baseline, execute_campaign(client, candidate), scenarios


def capture_body(context):
    _, _, baseline, candidate, _ = context
    return {"campaign_id": candidate["id"], "baseline_id": baseline["id"],
            "name": "Reviewable comparison", "note": "Captured observed results; no approval claim"}


def capture(context, key="comparison"):
    return post(context[0], "/comparisons", capture_body(context), key=key)


def test_capture_contains_actual_multicase_evidence_and_exact_source_bytes(finished_context):
    client, suite, baseline, candidate, _ = finished_context
    result = capture(finished_context)
    manifest = result["manifest"]
    assert result["counts"]["total"] == 8
    assert len(manifest["candidate"]["cases"]) == 8
    assert len({case["run_id"] for case in manifest["candidate"]["cases"]}) == 8
    assert all(case["run_id"] for case in manifest["candidate"]["cases"])
    assert manifest["report"]["campaign_id"] == candidate["id"]
    assert manifest["report"]["baseline_id"] == baseline["id"]
    assert result["suite_id"] == suite["id"]
    baseline_bytes = client.get(baseline["download_url"]).content
    assert canonical_bytes(manifest["baseline"]) == baseline_bytes
    assert manifest["baseline_manifest_hash"] == hashlib.sha256(baseline_bytes).hexdigest()
    response = client.get(result["download_url"])
    assert response.status_code == 200
    assert response.content == canonical_bytes(manifest)
    assert len(response.content) == result["manifest_size_bytes"]
    assert hashlib.sha256(response.content).hexdigest() == result["manifest_hash"]
    assert response.headers["x-content-sha256"] == result["manifest_hash"]


def test_only_terminal_candidate_can_be_captured_without_consuming_key(campaign_context):
    client, _, _, candidate, _ = campaign_context
    response = client.post("/api/v1/comparisons", json=capture_body(campaign_context),
                           headers={"Idempotency-Key": "waiting"})
    assert response.status_code == 409
    with client.app.state.db.session() as session:
        assert session.scalar(select(func.count()).select_from(ComparisonEvidenceRow)) == 0
    execute_campaign(client, candidate)
    assert capture(campaign_context, "waiting")["campaign_id"] == candidate["id"]


def test_cancelled_incomplete_candidate_retains_all_unevaluated_cases(campaign_context):
    client, _, _, candidate, _ = campaign_context
    response = client.post(f"/api/v1/campaigns/{candidate['id']}/cancel")
    assert response.status_code == 200, response.text
    captured = capture(campaign_context)
    report = captured["manifest"]["candidate"]
    assert report["status"] == "finished"
    assert report["evaluation_complete"] is False
    assert report["counts"]["total"] == report["counts"]["cancelled"] == 8
    assert report["counts"]["checks_not_evaluated"] == report["counts"]["checks_total"] == 8
    assert captured["counts"]["total"] == captured["counts"]["not_evaluated"] == 8


def test_capture_survives_source_edits_and_never_calls_live_helpers_on_replay(finished_context, monkeypatch):
    from ade2e import suites
    from ade2e.suite_models import BaselineRow

    client, _, baseline, _, scenarios = finished_context
    captured = capture(finished_context)
    original = client.get(captured["download_url"]).content
    response = client.patch(f"/api/v1/scenarios/{scenarios[0]['id']}", json={"name": "Edited after capture"})
    assert response.status_code == 200
    # Even a subsequently damaged source baseline cannot rewrite saved evidence.
    with client.app.state.db.session.begin() as session:
        session.get(BaselineRow, baseline["id"]).manifest = b"damaged source"

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Captured evidence must not resolve current source state")

    for helper in ("campaign_report", "baseline_get", "compare_reports"):
        monkeypatch.setattr(suites, helper, forbidden)
    assert capture(finished_context)["id"] == captured["id"]
    assert client.get(f"/api/v1/comparisons/{captured['id']}").json() == captured
    assert client.get(captured["download_url"]).content == original


def test_concurrent_identical_capture_commits_once(finished_context):
    client = finished_context[0]
    body = ComparisonCreate.model_validate(capture_body(finished_context))
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: comparisons.comparison_create(client.app.state.db, body, "concurrent"), range(3)))
    assert len({result.id for result in results}) == 1
    assert len({result.manifest_hash for result in results}) == 1
    with client.app.state.db.session() as session:
        assert session.scalar(select(func.count()).select_from(ComparisonEvidenceRow)) == 1


def test_same_key_different_body_conflicts(finished_context):
    client = finished_context[0]
    capture(finished_context)
    response = client.post("/api/v1/comparisons", json={**capture_body(finished_context), "note": "Different intent"},
                           headers={"Idempotency-Key": "comparison"})
    assert response.status_code == 409


def test_capture_requires_key_and_missing_source_does_not_consume_it(finished_context):
    client = finished_context[0]
    body = capture_body(finished_context)
    for headers in ({}, {"Idempotency-Key": ""}, {"Idempotency-Key": "x" * 201}):
        assert client.post("/api/v1/comparisons", json=body, headers=headers).status_code == 422
    for missing in ("campaign_id", "baseline_id"):
        response = client.post("/api/v1/comparisons", json={**body, missing: "missing"},
                               headers={"Idempotency-Key": "retry-source"})
        assert response.status_code == 404
    assert capture(finished_context, "retry-source")["campaign_id"] == body["campaign_id"]


@pytest.mark.parametrize("damage", ["bytes", "hash", "size", "summary", "indexed_metadata", "embedded_baseline"])
def test_reads_reject_corrupted_bytes_metadata_and_lineage(finished_context, damage):
    client, _, baseline, _, _ = finished_context
    captured = capture(finished_context)
    with client.app.state.db.session.begin() as session:
        row = session.get(ComparisonEvidenceRow, captured["id"])
        if damage == "bytes":
            row.manifest = row.manifest[:-1] + b"!"
        elif damage == "hash":
            row.manifest_hash = "0" * 64
        elif damage == "size":
            row.manifest_size_bytes += 1
        elif damage == "summary":
            row.summary = {**row.summary, "name": "Wrong metadata"}
        elif damage == "indexed_metadata":
            row.campaign_id = baseline["campaign_id"]
        else:
            manifest = json.loads(row.manifest)
            manifest["baseline"]["note"] = "Changed inner evidence"
            row.manifest = canonical_bytes(manifest)
            row.manifest_hash = hashlib.sha256(row.manifest).hexdigest()
            row.manifest_size_bytes = len(row.manifest)
    for path in (f"/api/v1/comparisons/{captured['id']}", captured["download_url"], "/api/v1/comparisons"):
        assert client.get(path).status_code == 409


def test_size_limit_rejection_does_not_save_partial_capture(finished_context, monkeypatch):
    monkeypatch.setattr(comparisons, "MAX_COMPARISON_BYTES", 1)
    client = finished_context[0]
    response = client.post("/api/v1/comparisons", json=capture_body(finished_context),
                           headers={"Idempotency-Key": "too-large"})
    assert response.status_code == 413
    with client.app.state.db.session() as session:
        assert session.scalar(select(func.count()).select_from(ComparisonEvidenceRow)) == 0


def test_commit_failure_rolls_back_capture_and_key(finished_context):
    db = finished_context[0].app.state.db
    body = ComparisonCreate.model_validate(capture_body(finished_context))

    def reject_commit(*_):
        raise OSError("Injected comparison commit failure")

    event.listen(db.session.class_, "before_commit", reject_commit)
    try:
        with pytest.raises(OSError, match="comparison commit"):
            comparisons.comparison_create(db, body, "retry-after-rollback")
    finally:
        event.remove(db.session.class_, "before_commit", reject_commit)
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(ComparisonEvidenceRow)) == 0
    assert comparisons.comparison_create(db, body, "retry-after-rollback").campaign_id == body.campaign_id


def test_list_filters_and_limits(finished_context):
    client, suite, _, candidate, _ = finished_context
    one, two = capture(finished_context, "one"), capture(finished_context, "two")
    response = client.get("/api/v1/comparisons", params={"campaign_id": candidate["id"], "suite_id": suite["id"]})
    assert response.status_code == 200
    assert {row["id"] for row in response.json()} == {one["id"], two["id"]}
    assert all("manifest" not in row for row in response.json())
    assert len(client.get("/api/v1/comparisons", params={"limit": 1}).json()) == 1
    assert client.get("/api/v1/comparisons", params={"suite_id": "missing"}).json() == []
    assert client.get("/api/v1/comparisons", params={"limit": 101}).status_code == 422
    assert client.get("/api/v1/comparisons/missing").status_code == 404


def test_every_capture_endpoint_uses_existing_authentication(finished_context):
    client = finished_context[0]
    captured = capture(finished_context)
    settings = Settings(data_dir=client.app.state.settings.data_dir, seed_examples=False, api_token="comparison-secret")
    with TestClient(create_app(settings, Registry(load_plugins=False))) as secured:
        paths = ("/api/v1/comparisons", f"/api/v1/comparisons/{captured['id']}", captured["download_url"])
        for path in paths:
            assert secured.get(path).status_code == 401
            assert secured.get(path, headers={"Authorization": "Bearer comparison-secret"}).status_code == 200
        assert secured.post("/api/v1/comparisons", json=capture_body(finished_context),
                            headers={"Idempotency-Key": "secured"}).status_code == 401
        assert secured.post("/api/v1/comparisons", json=capture_body(finished_context),
                            headers={"Authorization": "Bearer comparison-secret", "Idempotency-Key": "secured"}).status_code == 201


@pytest.mark.parametrize("change", [{"note": "   "}, {"name": ""}])
def test_capture_requires_reviewable_name_and_note(finished_context, change):
    response = finished_context[0].post("/api/v1/comparisons", json={**capture_body(finished_context), **change},
                                        headers={"Idempotency-Key": "invalid"})
    assert response.status_code == 422
