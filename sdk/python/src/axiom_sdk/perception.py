"""Versioned model authoring and recorded-image inference through the public API."""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import re
import stat
import time
from typing import Any

MODEL_BYTES_LIMIT = 128 * 1024 * 1024
MANIFEST_BYTES_LIMIT = 32 * 1024 * 1024
IMAGE_BYTES_LIMIT = 24 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def _identity(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,180}", value):
        raise ValueError("Resource ID must be a nonempty API identifier, not a path or URL")
    return value


def _integer(value: int, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _digest_metadata(sha256: str, size_bytes: int, maximum: int, *, allow_empty: bool = False) -> None:
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("Evidence metadata must contain a SHA-256 digest")
    _integer(size_bytes, "size_bytes", 0 if allow_empty else 1, maximum)


class PerceptionClientMixin:
    def upload_model_artifact(self, path: str | Path, *, name: str | None = None) -> dict[str, Any]:
        """Stream original local bytes and verify the returned artifact identity.

        Registration never executes a model. Retrying identical bytes/name is
        deduplicated by the API; this client makes no automatic retries.
        """
        path = Path(path)
        name = path.name if name is None else name
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Model artifact name must be nonempty")
        with path.open("rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Upload a regular local model file")
            size = _integer(info.st_size, "Model file size", 1, MODEL_BYTES_LIMIT)
            sha, count = hashlib.sha256(), 0
            def chunks():
                nonlocal count
                for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
                    count += len(chunk)
                    if count > size:
                        raise ValueError("Model file grew during upload")
                    sha.update(chunk)
                    yield chunk
            artifact = self._request("POST", "/perception/model-artifacts", params={"name": name},
                headers={"Content-Type": "application/octet-stream", "Content-Length": str(size)}, content=chunks()).json()
        if (count != size or not isinstance(artifact, dict) or artifact.get("sha256") != sha.hexdigest()
                or artifact.get("size_bytes") != size or artifact.get("name") != name.strip()):
            raise ValueError("Uploaded model artifact size, SHA-256, or name differs from the sent bytes")
        _identity(artifact.get("id"))
        return artifact

    def models(self) -> list[dict[str, Any]]:
        return self._request("GET", "/perception/models").json()

    def create_model(self, **definition: Any) -> dict[str, Any]:
        """Store one model definition. License fields remain declarations."""
        return self._request("POST", "/perception/models", json=definition).json()

    def get_model(self, model_id: str, *, revision: int | None = None) -> dict[str, Any]:
        params = {} if revision is None else {"revision": _integer(revision, "revision", 1, 2**31 - 1)}
        return self._request("GET", f"/perception/models/{_identity(model_id)}", params=params).json()

    def model_revisions(self, model_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/perception/models/{_identity(model_id)}/revisions").json()

    def revise_model(self, model_id: str, *, expected_revision: int, **definition: Any) -> dict[str, Any]:
        """Submit a complete new revision; stale edits fail with 409."""
        _integer(expected_revision, "expected_revision", 1, 2**31 - 1)
        return self._request("POST", f"/perception/models/{_identity(model_id)}/revisions",
                             json={**definition, "expected_revision": expected_revision}).json()

    def _download_perception(self, path: str, destination: str | Path, *, sha256: str, size_bytes: int,
                             maximum: int, params: dict | None = None, allow_empty: bool = False) -> Path:
        from .client import AxiomError

        _digest_metadata(sha256, size_bytes, maximum, allow_empty=allow_empty)
        destination = Path(destination)
        created = False
        try:
            with self._http.stream("GET", "/api/v1" + path, params=params, follow_redirects=False,
                                   headers={"Accept-Encoding": "identity"}) as response:
                if response.status_code != 200:
                    raise AxiomError(response.status_code, "Perception evidence download failed")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ValueError("Unexpected encoded perception download")
                declared = response.headers.get("content-length")
                if declared is not None and (not declared.isascii() or not declared.isdigit()
                        or len(declared) > 20 or int(declared) != size_bytes):
                    raise ValueError("Perception download Content-Length differs from its manifest")
                stream = destination.open("xb")
                created = True
                sha, count = hashlib.sha256(), 0
                with stream:
                    for chunk in response.iter_bytes(chunk_size=CHUNK_BYTES):
                        count += len(chunk)
                        if count > size_bytes:
                            raise ValueError("Perception download exceeds its declared size")
                        sha.update(chunk)
                        if stream.write(chunk) != len(chunk):
                            raise OSError("Incomplete perception evidence file write")
                if count != size_bytes or sha.hexdigest() != sha256:
                    raise ValueError("Perception evidence size or SHA-256 mismatch")
        except BaseException:
            if created:
                destination.unlink(missing_ok=True)
            raise
        return destination

    def download_model_artifact(self, artifact: dict[str, Any], destination: str | Path) -> Path:
        """Download by ID from this API; never follow a returned download_url."""
        return self._download_perception(f"/perception/model-artifacts/{_identity(artifact['id'])}/download", destination,
            sha256=artifact["sha256"], size_bytes=artifact["size_bytes"], maximum=MODEL_BYTES_LIMIT)

    def export_model(self, model_id: str, destination: str | Path, *, revision: int | None = None) -> Path:
        model = self.get_model(model_id, revision=revision)
        pinned = _integer(model.get("revision"), "Model revision", 1, 2**31 - 1)
        if model.get("id") != model_id or (revision is not None and pinned != revision):
            raise ValueError("Model revision identity mismatch; nothing written")
        return self._download_perception(f"/perception/models/{_identity(model_id)}/export", destination,
            params={"revision": pinned}, sha256=model["manifest_hash"], size_bytes=model["manifest_size_bytes"], maximum=256 * 1024)

    def perception_runtimes(self) -> list[dict[str, Any]]:
        return self._request("GET", "/perception/runtimes").json()

    def submit_perception_job(self, *, label: str, model_id: str, model_revision: int, recording_id: str,
                             channel_id: int, ordinals: list[int], decoder_id: str, decoder_version: str,
                             idempotency_key: str, runtime_id: str = "onnx-cpu", seed: int = 42,
                             score_threshold: float = 0.35, iou_threshold: float = 0.45,
                             max_detections: int = 100) -> dict[str, Any]:
        """Pin explicit model/message selections and return a durable queued job.

        Retry /perception/jobs with the same explicit key and body after a lost
        response. Submission never performs local inference or chooses latest.
        """
        headers = self._intent_headers(idempotency_key)
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must not be blank")
        _integer(model_revision, "model_revision", 1, 2**31 - 1)
        _integer(channel_id, "channel_id", 0, 65535)
        _integer(seed, "seed", 0, 2**31 - 1)
        _integer(max_detections, "max_detections", 1, 300)
        if not isinstance(ordinals, list) or not 1 <= len(ordinals) <= 64:
            raise ValueError("ordinals must contain 1..64 explicit file ordinals")
        for ordinal in ordinals:
            _integer(ordinal, "ordinal", 0, 100000)
        if len(set(ordinals)) != len(ordinals):
            raise ValueError("ordinals must be unique; supplied order is preserved")
        for name, value in (("score_threshold", score_threshold), ("iou_threshold", iou_threshold)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be finite and within 0..1")
        return self._request("POST", "/perception/jobs", headers=headers, json={
            "label": label, "model_id": model_id, "model_revision": model_revision, "runtime_id": runtime_id,
            "recording_id": recording_id, "channel_id": channel_id, "ordinals": list(ordinals), "decoder_id": decoder_id,
            "decoder_version": decoder_version, "seed": seed, "score_threshold": score_threshold,
            "iou_threshold": iou_threshold, "max_detections": max_detections,
        }).json()

    def perception_jobs(self, *, status: str | None = None, limit: int = 100, before_seq: int | None = None) -> dict[str, Any]:
        _integer(limit, "limit", 1, 100)
        if before_seq is not None:
            _integer(before_seq, "before_seq", 1, 2**63 - 1)
        return self._request("GET", "/perception/jobs", params={key: value for key, value in {
            "status": status, "limit": limit, "before_seq": before_seq}.items() if value is not None}).json()

    def get_perception_job(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/perception/jobs/{_identity(job_id)}").json()

    def cancel_perception_job(self, job_id: str) -> dict[str, Any]:
        """Request cancellation; retain committed frames and inspect actual status."""
        return self._request("POST", f"/perception/jobs/{_identity(job_id)}/cancel").json()

    def wait_perception_job(self, job_id: str, *, timeout: float = 120, poll_interval: float = 0.5) -> dict[str, Any]:
        """GET-only polling. Timeout does not cancel or resubmit a job.

        A stalled GET retains the client's HTTP timeout and may extend wall time.
        Failed, cancelled and interrupted terminal results remain unchanged.
        """
        if not math.isfinite(timeout) or timeout < 0 or not math.isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError("timeout must be finite/nonnegative and poll_interval finite/positive")
        deadline = time.monotonic() + timeout
        while True:
            job = self.get_perception_job(job_id)
            if job.get("id") != job_id:
                raise ValueError("Perception job identity mismatch")
            if job["status"] in TERMINAL:
                return job
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Perception job {job_id} is still {job['status']}; it was not cancelled or resubmitted")
            time.sleep(min(poll_interval, remaining))

    def export_perception_job(self, job_id: str, destination: str | Path) -> Path:
        job = self.get_perception_job(job_id)
        if job.get("id") != job_id or job.get("status") not in TERMINAL:
            raise ValueError("Perception export requires the selected terminal job")
        return self._download_perception(f"/perception/jobs/{_identity(job_id)}/export", destination,
            sha256=job["manifest_hash"], size_bytes=job["manifest_size_bytes"], maximum=MANIFEST_BYTES_LIMIT)

    def export_perception_image(self, job_id: str, selection_index: int, destination: str | Path) -> Path:
        """Export the exact saved input PNG for one completed frame, not an overlay."""
        _integer(selection_index, "selection_index", 0, 63)
        job = self.get_perception_job(job_id)
        frames = [frame for frame in job.get("frames", []) if frame.get("selection_index") == selection_index]
        inputs = [item for item in job.get("inputs", []) if item.get("selection_index") == selection_index]
        if (job.get("id") != job_id or len(frames) != 1 or len(inputs) != 1 or frames[0].get("input") != inputs[0]
                or frames[0].get("status") != "completed" or not isinstance(frames[0].get("image"), dict)):
            raise ValueError("Selected frame has no matching committed input PNG")
        image = frames[0]["image"]
        return self._download_perception(f"/perception/jobs/{_identity(job_id)}/frames/{selection_index}/image", destination,
            sha256=image["sha256"], size_bytes=image["size_bytes"], maximum=IMAGE_BYTES_LIMIT)

    def capture_perception_comparison(self, left_job_id: str, right_job_id: str, *, name: str, note: str,
                                      idempotency_key: str) -> dict[str, Any]:
        """Capture two terminal prediction reports; this is not an accuracy verdict."""
        headers = self._intent_headers(idempotency_key)
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must not be blank")
        return self._request("POST", "/perception/comparisons", headers=headers, json={
            "left_job_id": left_job_id, "right_job_id": right_job_id, "name": name, "note": note}).json()

    def perception_comparisons(self) -> list[dict[str, Any]]:
        return self._request("GET", "/perception/comparisons").json()

    def get_perception_comparison(self, comparison_id: str) -> dict[str, Any]:
        return self._request("GET", f"/perception/comparisons/{_identity(comparison_id)}").json()

    def export_perception_comparison(self, comparison_id: str, destination: str | Path) -> Path:
        comparison = self.get_perception_comparison(comparison_id)
        if comparison.get("id") != comparison_id:
            raise ValueError("Perception comparison identity mismatch; nothing written")
        return self._download_perception(f"/perception/comparisons/{_identity(comparison_id)}/export", destination,
            sha256=comparison["manifest_hash"], size_bytes=comparison["manifest_size_bytes"], maximum=MANIFEST_BYTES_LIMIT)
