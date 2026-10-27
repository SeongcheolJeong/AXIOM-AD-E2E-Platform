from __future__ import annotations

import hashlib
import itertools
import math
import time
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

import httpx

from .sensors import SensorClientMixin
from .perception import PerceptionClientMixin
from .datasets import DatasetClientMixin
from .stacks import StackClientMixin
from .recovery import RecoveryClientMixin
from .simulation_sensors import SimulationSensorClientMixin
from .cases import CaseClientMixin
from .case_model_replays import CaseModelReplayClientMixin
from .case_stack_replays import CaseStackReplayClientMixin
from .collections import CollectionClientMixin
from .annotations import AnnotationClientMixin
from .scenes import SceneClientMixin
from .verification import VerificationClientMixin
from .driving import DrivingPolicyClientMixin
from .experiments import ExperimentClientMixin
from .log_sim import LogSimClientMixin
from .log_sim_batches import LogSimBatchClientMixin
from .sensor_demo import SensorDemoClientMixin
from .sensor_layouts import SensorLayoutsClientMixin
from .fpga_lab import FpgaLabClientMixin
from .stunt_lab import StuntLabClientMixin
from .apollo_lab import ApolloLabClientMixin
from .rtl_lab import RtlLabClientMixin
from .fpga_workbench import FpgaWorkbenchClientMixin
from .engineering_lab import EngineeringLabClientMixin
from .vecu import VecuClientMixin
from .vecu_network import VecuNetworkClientMixin
from .hil_bridge import HilBridgeClientMixin
from .ecu_lab import EcuLabClientMixin
from .hil_demo import HilDemoClientMixin
from .hil_beta import HilBetaClientMixin
from .hil_batches import HilBatchClientMixin
from .neural_demo import NeuralDemoClientMixin
from .demo_cases import DemoCaseClientMixin
from .beta_workspace import BetaWorkspaceClientMixin
from .map_sources import MapSourcesClientMixin
from .object_scene_cases import ObjectSceneCasesClientMixin


class AxiomError(RuntimeError):
    def __init__(self, status_code: int, detail: Any):
        super().__init__(f"AXIOM API {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class AxiomClient(ObjectSceneCasesClientMixin, MapSourcesClientMixin, StuntLabClientMixin, HilBridgeClientMixin, VecuNetworkClientMixin, VecuClientMixin, EngineeringLabClientMixin, FpgaWorkbenchClientMixin, RtlLabClientMixin, ApolloLabClientMixin, EcuLabClientMixin, FpgaLabClientMixin, SensorClientMixin, PerceptionClientMixin, DatasetClientMixin, StackClientMixin, RecoveryClientMixin, SimulationSensorClientMixin, CaseClientMixin, CaseModelReplayClientMixin, CaseStackReplayClientMixin, CollectionClientMixin, AnnotationClientMixin, SceneClientMixin, VerificationClientMixin, DrivingPolicyClientMixin, ExperimentClientMixin, LogSimClientMixin, LogSimBatchClientMixin, SensorDemoClientMixin, SensorLayoutsClientMixin, HilDemoClientMixin, HilBetaClientMixin, HilBatchClientMixin, NeuralDemoClientMixin, DemoCaseClientMixin, BetaWorkspaceClientMixin):
    """Synchronous typed-entry-point client; execution is bounded by timeout.

    Writes are explicit. A retry must reuse the same idempotency_key. This client
    never automatically retries a simulator execution or sends data to an LLM.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8000", *, token: str | None = None,
                 timeout: float = 120, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout,
                                  headers={"Authorization": f"Bearer {token}"} if token else {},
                                  transport=transport)

    def __enter__(self) -> AxiomClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._http.request(method, "/api/v1" + path, **kwargs)
        if response.is_error:
            try:
                payload = response.json()
                detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
            except ValueError:
                detail = response.text
            raise AxiomError(response.status_code, detail)
        return response

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health").json()

    def scenarios(self, *, query: str = "", category: str = "") -> list[dict[str, Any]]:
        params = {k: v for k, v in {"q": query, "category": category}.items() if v}
        return self._request("GET", "/scenarios", params=params).json()

    def create_scenario(self, **definition: Any) -> dict[str, Any]:
        return self._request("POST", "/scenarios", json=definition).json()

    def update_scenario(self, scenario_id: str, **changes: Any) -> dict[str, Any]:
        return self._request("PATCH", f"/scenarios/{scenario_id}", json=changes).json()

    def run(self, scenario_id: str, *, adapter_id: str = "reference", seed: int | None = None,
            parameter_overrides: dict[str, float] | None = None,
            idempotency_key: str | None = None, control: dict[str, Any] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"scenario_id": scenario_id, "adapter_id": adapter_id}
        if seed is not None:
            body["seed"] = seed
        if parameter_overrides is not None:
            body["parameter_overrides"] = parameter_overrides
        if control is not None:
            body["control"] = control
        return self._request("POST", "/runs", json=body,
                             headers={"Idempotency-Key": idempotency_key or str(uuid4())}).json()

    def runs(self, *, scenario_id: str = "", verdict: str = "") -> list[dict[str, Any]]:
        params = {k: v for k, v in {"scenario_id": scenario_id, "verdict": verdict}.items() if v}
        return self._request("GET", "/runs", params=params).json()

    def submit_job(self, scenario_id: str, *, idempotency_key: str,
                   adapter_id: str = "reference", seed: int | None = None,
                   parameter_overrides: dict[str, float] | None = None,
                   control: dict[str, Any] | None = None) -> dict[str, Any]:
        """Persist one execution request and return without waiting for its engine.

        Keep the explicit key if a response is lost. A timeout is not cancellation.
        Keys are scoped to /jobs: switching to run() is a new execution, not a retry.
        """
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise ValueError("idempotency_key must contain 1..200 characters")
        body: dict[str, Any] = {"scenario_id": scenario_id, "adapter_id": adapter_id}
        if seed is not None:
            body["seed"] = seed
        if parameter_overrides is not None:
            body["parameter_overrides"] = parameter_overrides
        if control is not None:
            body["control"] = control
        return self._request("POST", "/jobs", json=body,
                             headers={"Idempotency-Key": idempotency_key}).json()

    def jobs(self, *, status: str | None = None, limit: int = 100,
             before_seq: int | None = None) -> dict[str, Any]:
        params = {k: v for k, v in {"status": status, "limit": limit, "before_seq": before_seq}.items() if v is not None}
        return self._request("GET", "/jobs", params=params).json()

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/jobs/{job_id}").json()

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        """Request cancellation; inspect status rather than assuming remote stop."""
        return self._request("POST", f"/jobs/{job_id}/cancel").json()

    def workers(self) -> list[dict[str, Any]]:
        return self._request("GET", "/workers").json()

    def execution_resources(self) -> list[dict[str, Any]]:
        return self._request("GET", "/execution-resources").json()

    def reconcile_execution_resource(self, adapter_id: str, *, job_id: str,
                                     note: str, confirmed_stopped: bool) -> dict[str, Any]:
        """Unlock only after the former dispatcher and remote engine are stopped.

        This records the operator's confirmation; it does not stop an engine,
        inspect its health, rerun a job, or resolve the old result's uncertainty.
        """
        if confirmed_stopped is not True or not isinstance(note, str) or not note.strip():
            raise ValueError("Explicit stop/cleanup confirmation and a note are required")
        return self._request("POST", f"/execution-resources/{adapter_id}/reconcile", json={
            "job_id": job_id, "note": note, "confirmed_stopped": True,
        }).json()

    def wait_job(self, job_id: str, *, timeout: float = 120,
                 poll_interval: float = 0.5) -> dict[str, Any]:
        """Poll read-only until terminal; timeout never cancels or resubmits work.

        HTTP requests retain the client's transport timeout, so a stalled GET can
        extend the wall-clock wait. Failed/cancelled/interrupted jobs are returned
        with their original state; only incomplete polling raises TimeoutError.
        """
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be a finite nonnegative number")
        if not math.isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError("poll_interval must be finite and positive")
        deadline = time.monotonic() + timeout
        terminal = {"completed", "failed", "cancelled", "interrupted"}
        while True:
            job = self.get_job(job_id)
            if job["status"] in terminal:
                return job
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Job {job_id} is still {job['status']}; it was not cancelled or resubmitted")
            time.sleep(min(poll_interval, remaining))

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._request("GET", f"/runs/{run_id}").json()

    def suites(self) -> list[dict[str, Any]]:
        return self._request("GET", "/suites").json()

    def create_suite(self, **definition: Any) -> dict[str, Any]:
        """Save explicit cases, scenario revisions and requirement definitions."""
        return self._request("POST", "/suites", json=definition).json()

    def get_suite(self, suite_id: str) -> dict[str, Any]:
        return self._request("GET", f"/suites/{suite_id}").json()

    def suite_revisions(self, suite_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/suites/{suite_id}/revisions").json()

    def revise_suite(self, suite_id: str, *, expected_revision: int, **definition: Any) -> dict[str, Any]:
        """Create a revision; stale edits fail rather than overwrite another edit."""
        return self._request("POST", f"/suites/{suite_id}/revisions",
                             json={**definition, "expected_revision": expected_revision}).json()

    @staticmethod
    def _intent_headers(key: str) -> dict[str, str]:
        if not isinstance(key, str) or not 1 <= len(key) <= 200:
            raise ValueError("idempotency_key must contain 1..200 characters")
        return {"Idempotency-Key": key}

    def submit_campaign(self, suite_id: str, *, suite_revision: int, label: str,
                        idempotency_key: str, adapter_id: str = "reference", control: dict[str, Any] | None = None) -> dict[str, Any]:
        """Atomically persist every suite case as a job; never dispatch in this client.

        Retry this endpoint with the same key and body after a lost response.
        Cases keep the suite's saved inputs even if current scenarios change.
        """
        body = {"suite_id": suite_id, "suite_revision": suite_revision, "adapter_id": adapter_id, "label": label}
        if control is not None:
            body["control"] = control
        return self._request("POST", "/campaigns", headers=self._intent_headers(idempotency_key), json=body).json()

    def campaigns(self, *, limit: int = 100, before_seq: int | None = None,
                  suite_id: str | None = None) -> dict[str, Any]:
        params = {key: value for key, value in {"limit": limit, "before_seq": before_seq,
                                               "suite_id": suite_id}.items() if value is not None}
        return self._request("GET", "/campaigns", params=params).json()

    def get_campaign(self, campaign_id: str) -> dict[str, Any]:
        return self._request("GET", f"/campaigns/{campaign_id}").json()

    def cancel_campaign(self, campaign_id: str) -> dict[str, Any]:
        """Request member cancellations and preserve results that already exist."""
        return self._request("POST", f"/campaigns/{campaign_id}/cancel").json()

    def wait_campaign(self, campaign_id: str, *, timeout: float = 120,
                      poll_interval: float = 0.5) -> dict[str, Any]:
        """Wait for all member jobs to settle, retaining failed/incomplete verdicts.

        Uses GET only. Timeout never cancels or submits work; HTTP requests retain
        the client's transport timeout and may extend the wall-clock wait.
        """
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be a finite nonnegative number")
        if not math.isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError("poll_interval must be finite and positive")
        deadline = time.monotonic() + timeout
        while True:
            report = self.get_campaign(campaign_id)
            if report["status"] == "finished":
                return report
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Campaign {campaign_id} is still {report['status']}; it was not cancelled or resubmitted")
            time.sleep(min(poll_interval, remaining))

    def baselines(self, *, suite_id: str | None = None) -> list[dict[str, Any]]:
        return self._request("GET", "/baselines", params={"suite_id": suite_id} if suite_id else {}).json()

    def create_baseline(self, campaign_id: str, *, name: str, note: str,
                        idempotency_key: str) -> dict[str, Any]:
        """Seal a settled campaign, including every failed or unevaluated case.

        This records a reference snapshot, not an approval or safety certificate.
        """
        return self._request("POST", "/baselines", headers=self._intent_headers(idempotency_key),
                             json={"campaign_id": campaign_id, "name": name, "note": note}).json()

    def get_baseline(self, baseline_id: str) -> dict[str, Any]:
        return self._request("GET", f"/baselines/{baseline_id}").json()

    def compare_campaign(self, campaign_id: str, *, baseline_id: str) -> dict[str, Any]:
        return self._request("GET", f"/campaigns/{campaign_id}/compare", params={"baseline_id": baseline_id}).json()

    def export_campaign(self, campaign_id: str, destination: str | Path) -> Path:
        """Export the currently observed report; use a baseline to freeze evidence."""
        return self._write_new(destination, self._request("GET", f"/campaigns/{campaign_id}/export").content)

    def export_baseline(self, baseline_id: str, destination: str | Path) -> Path:
        """Verify immutable manifest bytes before writing a new local file."""
        baseline = self.get_baseline(baseline_id)
        content = self._request("GET", f"/baselines/{baseline_id}/export").content
        if hashlib.sha256(content).hexdigest() != baseline["manifest_hash"]:
            raise ValueError("Baseline SHA-256 mismatch; nothing written")
        return self._write_new(destination, content)

    def capture_comparison(self, campaign_id: str, *, baseline_id: str, name: str,
                           note: str, idempotency_key: str) -> dict[str, Any]:
        """Freeze a settled candidate and baseline comparison as review evidence."""
        return self._request("POST", "/comparisons", headers=self._intent_headers(idempotency_key), json={
            "campaign_id": campaign_id, "baseline_id": baseline_id, "name": name, "note": note,
        }).json()

    def comparisons(self, *, campaign_id: str | None = None, suite_id: str | None = None) -> list[dict[str, Any]]:
        return self._request("GET", "/comparisons", params={key: value for key, value in {
            "campaign_id": campaign_id, "suite_id": suite_id,
        }.items() if value is not None}).json()

    def get_comparison(self, comparison_id: str) -> dict[str, Any]:
        return self._request("GET", f"/comparisons/{comparison_id}").json()

    def export_comparison(self, comparison_id: str, destination: str | Path) -> Path:
        comparison = self.get_comparison(comparison_id)
        content = self._request("GET", f"/comparisons/{comparison_id}/export").content
        if hashlib.sha256(content).hexdigest() != comparison["manifest_hash"]:
            raise ValueError("Comparison SHA-256 mismatch; nothing written")
        return self._write_new(destination, content)

    def requirements(self) -> list[dict[str, Any]]:
        return self._request("GET", "/requirements").json()

    def create_requirement(self, **definition: Any) -> dict[str, Any]:
        return self._request("POST", "/requirements", json=definition).json()

    def adapters(self) -> list[dict[str, Any]]:
        return self._request("GET", "/adapters").json()

    def simulation_sensor_profiles(self) -> list[dict[str, Any]]:
        """Read immutable recipes; each run pins the effective scenario seed."""
        return self._request("GET", "/simulation-sensor-profiles").json()

    def adapter_health(self, adapter_id: str) -> dict[str, Any]:
        """Check connectivity without executing a scenario or creating a run."""
        return self._request("GET", f"/adapters/{adapter_id}/health").json()

    def artifacts(self, *, query: str = "", kind: str = "") -> list[dict[str, Any]]:
        return self._request("GET", "/artifacts", params={
            k: v for k, v in {"q": query, "kind": kind}.items() if v
        }).json()

    def recordings(self, *, query: str = "") -> list[dict[str, Any]]:
        return self._request("GET", "/recordings", params={"q": query}).json()

    def get_recording(self, recording_id: str) -> dict[str, Any]:
        return self._request("GET", f"/recordings/{recording_id}").json()

    def import_recording(self, path: str | Path, *, name: str | None = None,
                         source_kind: str = "unknown", source: str = "") -> dict[str, Any]:
        """Upload original MCAP bytes; provenance is declared, not verified.

        Exact bytes plus identical metadata return the existing recording on a
        retry. No automatic retries, format conversion, or remote URL ingestion.
        """
        path = Path(path)
        if source_kind not in {"unknown", "synthetic", "simulation", "measured"}:
            raise ValueError("Unsupported source_kind")
        size = path.stat().st_size
        if size > 128 * 1024 * 1024:
            raise ValueError("Recording exceeds the local 128 MiB upload limit")
        with path.open("rb") as stream:
            return self._request("POST", "/recordings", params={
                "name": name if name is not None else path.name,
                "source_kind": source_kind, "source": source,
            }, headers={"Content-Type": "application/octet-stream", "Content-Length": str(size)},
                content=iter(lambda: stream.read(1024 * 1024), b"")).json()

    def recording_samples(self, recording_id: str, channel_id: int, *,
                          at_ns: int | str | None = None, limit: int = 1,
                          from_start: bool = False, after_ordinal: int | None = None) -> dict[str, Any]:
        """Seek by log time without converting precise timestamps to floats."""
        if not 1 <= limit <= 100 or isinstance(limit, bool):
            raise ValueError("Sample limit must be 1..100")
        if sum((at_ns is not None, from_start, after_ordinal is not None)) > 1:
            raise ValueError("Choose one of at_ns, from_start, or after_ordinal")
        params: dict[str, Any] = {"channel_id": channel_id, "limit": limit}
        if at_ns is not None:
            if isinstance(at_ns, bool) or not str(at_ns).isdigit() or not 0 <= int(at_ns) < 2 ** 64:
                raise ValueError("at_ns must be a uint64 decimal timestamp")
            params["at_ns"] = str(int(at_ns))
        if from_start:
            params["from_start"] = "true"
        if after_ordinal is not None:
            if isinstance(after_ordinal, bool) or not isinstance(after_ordinal, int) or after_ordinal < 0:
                raise ValueError("after_ordinal must be a nonnegative integer")
            params["after_ordinal"] = after_ordinal
        return self._request("GET", f"/recordings/{recording_id}/samples", params=params).json()

    def iter_recording_samples(self, recording_id: str, channel_id: int, *,
                               page_size: int = 100) -> Iterator[dict[str, Any]]:
        """Read every message in chronological/file-ordinal order, including ties."""
        page = self.recording_samples(recording_id, channel_id, from_start=True, limit=page_size)
        seen = set()
        while True:
            yield from page["samples"]
            cursor = page["next_after_ordinal"]
            if cursor is None:
                return
            if cursor in seen:
                raise ValueError("Repeated recording page cursor; stopping incomplete traversal")
            seen.add(cursor)
            page = self.recording_samples(recording_id, channel_id, after_ordinal=cursor, limit=page_size)

    def download_recording(self, recording: dict[str, Any], destination: str | Path) -> Path:
        """Stream the original by ID and validate size/hash before success.

        A failed transfer or integrity check removes only this call's new file.
        Catalog-provided URLs never select the authenticated destination host.
        """
        path = Path(destination)
        created = False
        try:
            with self._http.stream("GET", f"/api/v1/recordings/{recording['id']}/download") as response:
                if response.status_code != 200:
                    raise AxiomError(response.status_code, "Recording download failed")
                stream = path.open("xb")
                created = True
                digest, count = hashlib.sha256(), 0
                with stream:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        count += len(chunk)
                        if count > recording["size_bytes"]:
                            raise ValueError("Recording size exceeds its manifest")
                        digest.update(chunk)
                        if stream.write(chunk) != len(chunk):
                            raise OSError("Incomplete recording file write")
                if count != recording["size_bytes"] or digest.hexdigest() != recording["sha256"]:
                    raise ValueError("Recording size or SHA-256 mismatch")
        except BaseException:
            if created:
                path.unlink(missing_ok=True)
            raise
        return path

    def export_run(self, run_id: str, destination: str | Path) -> Path:
        """Write one evidence manifest to an explicitly selected local path."""
        content = self._request("GET", f"/runs/{run_id}/export").content
        return self._write_new(destination, content)

    def download_artifact(self, artifact: dict[str, Any], destination: str | Path) -> Path:
        """Download by resource ID, verify stored SHA-256, then write bytes.

        The server-provided download URL is intentionally not followed: a catalog
        entry cannot redirect an authenticated client to an arbitrary host.
        """
        content = self._request("GET", f"/artifacts/{artifact['id']}/download").content
        if hashlib.sha256(content).hexdigest() != artifact["sha256"]:
            raise ValueError("Artifact SHA-256 mismatch; nothing written")
        return self._write_new(destination, content)

    @staticmethod
    def _write_new(destination: str | Path, content: bytes) -> Path:
        path = Path(destination)
        # Opening outside the cleanup scope leaves preexisting files untouched.
        stream = path.open("xb")
        try:
            with stream:
                if stream.write(content) != len(content):
                    raise OSError("Incomplete evidence file write")
        except BaseException:
            # A failed write or buffered close must not block an explicit retry.
            path.unlink(missing_ok=True)
            raise
        return path

    def sweep(self, scenario_id: str, parameters: dict[str, list[float]], *,
              adapter_id: str = "reference", max_runs: int = 50,
              campaign_key: str | None = None) -> Iterator[dict[str, Any]]:
        """Explicit bounded Cartesian sweep; no distributed scheduling claim.

        Reuse campaign_key and identical ordered parameter values to resume.
        Request-body conflicts are enforced by the server on changed retries.
        """
        keys = sorted(parameters)
        count = 1
        for key in keys:
            count *= len(parameters[key])
        if not keys or count == 0 or count > max_runs or max_runs > 1000:
            raise ValueError("Sweep must contain 1..max_runs combinations (cap 1000)")
        campaign = campaign_key or str(uuid4())
        for index, values in enumerate(itertools.product(*(parameters[k] for k in keys))):
            yield self.run(scenario_id, adapter_id=adapter_id,
                           parameter_overrides=dict(zip(keys, values)),
                           idempotency_key=f"{campaign}:{index}")
