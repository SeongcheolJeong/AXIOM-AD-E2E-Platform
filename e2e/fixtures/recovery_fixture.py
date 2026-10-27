"""Original native artifact bytes for recovery UI checks; the world is mocked.

This does not contact CARLA or exercise the remote worker transport. Recovery
HTTP records are authored by the browser test, while native success/failure
artifacts below are emitted and validated by the actual bridge and core.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integrations/carla/tests"))
from test_live_stack import install_live_world, live_request
from ade2e.adapters import AdapterExecutionError
from ade2e.execution_attempt import ExecutionAttemptRequest
from ade2e.schemas import Scenario
from ade2e.simulation_evidence import validate_simulation_artifacts
from axiom_carla.runner import CarlaRunner
from axiom_carla.version import ADAPTER_VERSION

destination = Path(sys.argv[1])
destination.mkdir(parents=True, exist_ok=True)
name = sys.argv[2]
partial = len(sys.argv) > 3 and sys.argv[3] == "partial"
scenario = Scenario(id=f"browser-recovery-{name}-scenario", name=f"Native recovery UI fixture {name}",
    category="lane_follow", description="Mocked world; no native CARLA dynamics acceptance", map_name="Town01",
    duration_s=4, seed=17, revision=1, created_at="2026-09-13T00:00:00Z", updated_at="2026-09-13T00:00:00Z")
with pytest.MonkeyPatch.context() as patch:
    install_live_world(patch, fault="readback" if partial else None)
    request = live_request(scenario).model_copy(update={"execution_id": f"browser-recovery-{name}-source-run"})
    try:
        result = CarlaRunner().execute_with_context(request)
        if partial:
            raise AssertionError("Expected injected readback failure")
        payloads, trajectory = result.artifact_payloads, result.trajectory
    except AdapterExecutionError as error:
        if not partial:
            raise
        payloads, trajectory = error.artifact_payloads, []
    validate_simulation_artifacts(request, payloads, completed=not partial,
        trajectory=trajectory if not partial else None, adapter_version=ADAPTER_VERSION)
attempt = ExecutionAttemptRequest(execution_id=request.execution_id, input_hash=request.input_hash,
    adapter_id="carla", adapter_version=ADAPTER_VERSION, scenario=request.scenario, context=request.context)
artifacts = []
for index, payload in enumerate(payloads):
    (destination / payload.name).write_bytes(payload.content)
    artifact_id = f"browser-recovery-{name}-artifact-{index}"
    artifacts.append({"id": artifact_id, "run_id": request.execution_id, "name": payload.name, "kind": payload.kind,
        "media_type": payload.media_type, "size_bytes": len(payload.content), "sha256": hashlib.sha256(payload.content).hexdigest(),
        "created_at": scenario.created_at, "download_url": f"/api/v1/artifacts/{artifact_id}/download",
        "source": "Original native bridge output over a mocked world; recovery UI contract only"})
run = {"id": request.execution_id, "scenario_id": request.scenario.id, "scenario_name": request.scenario.name,
    "scenario_revision": request.scenario.revision, "adapter_id": "carla", "status": "error" if partial else "completed",
    "verdict": "error" if partial else "failed", "created_at": scenario.created_at, "duration_s": scenario.duration_s,
    "execution_ms": 0, "metrics": [], "provenance": {"input_hash": request.input_hash, "output_hash": "2" * 64,
    "seed": scenario.seed, "adapter_version": ADAPTER_VERSION, "model_fidelity": "Native bridge over mocked world; authored recovery protocol fixture"},
    "scenario_snapshot": request.scenario.model_dump(mode="json"), "execution_context": request.context.model_dump(mode="json"),
    "trajectory": [frame.model_dump(mode="json") for frame in trajectory], "events": [], "artifacts": artifacts}
(destination / "fixture.json").write_text(json.dumps({"run": run, "attempt": attempt.model_dump(mode="json"),
    "request_hash": attempt.request_hash}), encoding="utf-8")
print(json.dumps({"path": str(destination / "fixture.json"), "scope": "authored recovery over mocked world"}))
