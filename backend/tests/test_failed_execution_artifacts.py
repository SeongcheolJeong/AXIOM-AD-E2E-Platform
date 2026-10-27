"""Attempt diagnostics remain immutable error evidence through the real queue."""
import hashlib
import json

import pytest
from sqlalchemy import event

from ade2e.adapters import AdapterExecutionError
from ade2e.reference import ReferenceAdapter
from ade2e.schemas import ArtifactPayload
from ade2e.service import digest
from ade2e.worker import run_once


def diagnostic(content=b'{"status":"not_stable","measured_frames":0}'):
    return ArtifactPayload(name="startup.json", kind="startup_manifest", media_type="application/json", content=content)


class FailingAdapter(ReferenceAdapter):
    calls = 0

    def __init__(self, error):
        self.error = error

    def execute(self, scenario):
        self.calls += 1
        raise self.error


@pytest.mark.parametrize("queued", [False, True])
def test_failure_diagnostics_survive_queue_publication_download_and_same_key(client, make_scenario, queued):
    scenario = make_scenario()
    content = diagnostic().content
    adapter = FailingAdapter(AdapterExecutionError("An attempted setup did not become stable", artifact_payloads=[diagnostic()]))
    client.app.state.registry.register(adapter, replace=True)
    endpoint = "/api/v1/jobs" if queued else "/api/v1/runs"
    request = {"scenario_id": scenario["id"]}
    headers = {"Idempotency-Key": "diagnostic-attempt"}
    submitted = client.post(endpoint, json=request, headers=headers)
    assert submitted.status_code == (202 if queued else 201), submitted.text
    if queued:
        job = run_once(client.app.state.db, client.app.state.registry, "diagnostic-worker", job_id=submitted.json()["id"])
        result = client.get(f"/api/v1/runs/{job.run_id}").json()
    else:
        result = submitted.json()
    assert result["status"] == result["verdict"] == "error"
    assert result["trajectory"] == result["metrics"] == []
    assert "diagnostic artifacts only" in result["provenance"]["model_fidelity"]
    artifact = next(item for item in result["artifacts"] if item["kind"] == "startup_manifest")
    assert artifact["sha256"] == hashlib.sha256(content).hexdigest()
    assert client.get(artifact["download_url"]).content == content
    expected = {"error": result["error"], "events": result["events"], "artifact_hashes": [artifact["sha256"]]}
    assert result["provenance"]["output_hash"] == digest(expected)
    manifest = json.loads(client.get(result["artifacts"][-1]["download_url"]).content)
    assert manifest["artifacts"] == result["artifacts"][:-1]
    assert manifest["provenance"] == result["provenance"]
    assert client.post(endpoint, json=request, headers=headers).json()["id"] == submitted.json()["id"]
    assert adapter.calls == 1


def test_exception_copies_caller_artifact_model():
    original = diagnostic()
    error = AdapterExecutionError("setup failed", artifact_payloads=[original])
    original.content = b"changed after construction"
    assert error.artifact_payloads[0].content == diagnostic().content


@pytest.mark.parametrize("corruption", ["oversized", "invalid_kind", "not_a_sequence"])
def test_mutated_failure_diagnostics_are_withheld_without_erasing_attempt(client, make_scenario, corruption):
    scenario = make_scenario()
    error = AdapterExecutionError("setup failed", artifact_payloads=[diagnostic()])
    if corruption == "oversized":
        error.artifact_payloads[0].content = b"x" * (20 * 1024 * 1024 + 1)
    elif corruption == "invalid_kind":
        error.artifact_payloads[0].kind = None
    else:
        error.artifact_payloads = None
    client.app.state.registry.register(FailingAdapter(error), replace=True)
    response = client.post("/api/v1/runs", json={"scenario_id": scenario["id"]})
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["verdict"] == "error" and result["metrics"] == []
    assert [item["kind"] for item in result["artifacts"]] == ["evidence_manifest"]
    assert any(item["type"] == "diagnostic_evidence_rejected" for item in result["events"])


@pytest.mark.parametrize("artifacts", [lambda: [diagnostic()] * 101,
                                       lambda: [diagnostic(b"x" * (17 * 1024 * 1024))] * 2])
def test_failure_artifact_count_and_total_bytes_are_bounded(artifacts):
    with pytest.raises(ValueError):
        AdapterExecutionError("setup failed", artifact_payloads=artifacts())


def test_failure_diagnostic_files_roll_back_with_failed_publication(client, make_scenario):
    scenario = make_scenario()
    client.app.state.registry.register(FailingAdapter(AdapterExecutionError("setup failed", artifact_payloads=[diagnostic()])), replace=True)
    db = client.app.state.db
    job_id = client.post("/api/v1/jobs", json={"scenario_id": scenario["id"]},
                         headers={"Idempotency-Key": "failed-diagnostic-publication"}).json()["id"]

    def fail_after_promotion(session):
        if any(db.artifact_dir.glob("*.blob")):
            raise OSError("injected diagnostic publication commit failure")

    event.listen(db.session.class_, "before_commit", fail_after_promotion)
    try:
        result = run_once(db, client.app.state.registry, "diagnostic-worker", job_id=job_id)
        assert result.status == "failed" and result.run_id is None
    finally:
        event.remove(db.session.class_, "before_commit", fail_after_promotion)
    assert client.get("/api/v1/runs").json() == []
    assert client.get("/api/v1/artifacts").json() == []
    assert not any(path.is_file() for path in db.artifact_dir.rglob("*"))
