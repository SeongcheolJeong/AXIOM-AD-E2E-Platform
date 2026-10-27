"""Immutable reviewed datasets and saved evaluation through the versioned API."""
from __future__ import annotations

import math
from pathlib import Path
import re
import time
from typing import Any

from .perception import TERMINAL, _digest_metadata, _identity, _integer

IMAGE_LIMIT = 8 * 1024 * 1024
DATASET_LIMIT = 16 * 1024 * 1024
EVALUATION_LIMIT = 32 * 1024 * 1024
SPLITS = {"train", "validation", "test", "unspecified"}
STATUSES = TERMINAL | {"queued", "running", "cancel_requested"}
DIAGNOSTIC_IOUS = (.5, .55, .6, .65, .7, .75, .8, .85, .9, .95)


def _text(value, name, maximum=180):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must contain nonblank text, at most {maximum} characters")
    return value.strip()


def _uint64_text(value):
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]{0,19}", value) or int(value) > 2**64 - 1:
        raise ValueError("Source clock must be an exact canonical uint64 decimal string")


def _checked_image(asset, *, image_id=None, source=None):
    if not isinstance(asset, dict) or not all(isinstance(asset.get(key), dict) for key in ("input", "image", "recording_snapshot")):
        raise ValueError("Dataset image response has no complete source/image identity")
    _identity(asset.get("id"))
    if image_id is not None and asset["id"] != image_id:
        raise ValueError("Dataset image identity mismatch")
    captured, image, recording = asset["input"], asset["image"], asset["recording_snapshot"]
    _identity(captured.get("recording_id"))
    _integer(captured.get("channel_id"), "channel_id", 0, 65535)
    _integer(captured.get("ordinal"), "ordinal", 0, 100000)
    _text(captured.get("decoder_id"), "decoder_id")
    _text(captured.get("decoder_version"), "decoder_version")
    _digest_metadata(captured.get("message_sha256"), captured.get("message_size_bytes"), 32 * 1024 * 1024)
    if not isinstance(captured.get("recording_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", captured["recording_sha256"]):
        raise ValueError("Dataset image source has no recording SHA-256")
    for name in ("log_time_ns", "publish_time_ns"):
        _uint64_text(captured.get(name))
    if (recording.get("id") != captured["recording_id"] or recording.get("sha256") != captured["recording_sha256"]
            or not isinstance(recording.get("channel"), dict) or recording["channel"].get("id") != captured["channel_id"]):
        raise ValueError("Dataset image recording snapshot and source identity differ")
    _integer(recording["channel"].get("id"), "snapshot channel_id", 0, 65535)
    if source is not None and any(captured.get(key) != value for key, value in source.items()):
        raise ValueError("Captured dataset image does not match the requested source selection")
    _digest_metadata(image.get("sha256"), image.get("size_bytes"), IMAGE_LIMIT)
    width = _integer(image.get("width"), "width", 1, 2_000_000)
    height = _integer(image.get("height"), "height", 1, 2_000_000)
    if width * height > 2_000_000:
        raise ValueError("Dataset image exceeds the pixel bound")
    return asset


class DatasetClientMixin:
    def capture_dataset_image(self, recording_id: str, channel_id: int, ordinal: int, *,
                              decoder_id: str, decoder_version: str) -> dict[str, Any]:
        """Capture one exact camera message; this does not run a detector or label it."""
        body = {"recording_id": _identity(recording_id),
                "channel_id": _integer(channel_id, "channel_id", 0, 65535),
                "ordinal": _integer(ordinal, "ordinal", 0, 100000),
                "decoder_id": _text(decoder_id, "decoder_id"),
                "decoder_version": _text(decoder_version, "decoder_version")}
        return _checked_image(self._request("POST", "/dataset-images", json=body).json(), source=body)

    def get_dataset_image(self, image_id: str) -> dict[str, Any]:
        image_id = _identity(image_id)
        return _checked_image(self._request("GET", f"/dataset-images/{image_id}").json(), image_id=image_id)

    def export_dataset_image(self, image_id: str, destination: str | Path) -> Path:
        """Verify and save original PNG bytes, using this API's fixed image route."""
        asset = self.get_dataset_image(image_id)
        return self._download_perception(f"/dataset-images/{_identity(image_id)}/image", destination,
            sha256=asset["image"]["sha256"], size_bytes=asset["image"]["size_bytes"], maximum=IMAGE_LIMIT)

    def datasets(self) -> list[dict[str, Any]]:
        return self._request("GET", "/datasets").json()

    def create_dataset(self, **definition: Any) -> dict[str, Any]:
        """Store the complete author-declared revision; review is never inferred."""
        return self._request("POST", "/datasets", json=definition).json()

    def get_dataset(self, dataset_id: str, *, revision: int | None = None) -> dict[str, Any]:
        dataset_id = _identity(dataset_id)
        params = {} if revision is None else {"revision": _integer(revision, "revision", 1, 2**31 - 1)}
        result = self._request("GET", f"/datasets/{dataset_id}", params=params).json()
        if not isinstance(result, dict) or result.get("id") != dataset_id:
            raise ValueError("Dataset identity mismatch")
        actual = _integer(result.get("revision"), "Dataset revision", 1, 2**31 - 1)
        if revision is not None and revision != actual:
            raise ValueError("Dataset revision identity mismatch")
        return result

    def dataset_revisions(self, dataset_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/datasets/{_identity(dataset_id)}/revisions").json()

    def revise_dataset(self, dataset_id: str, *, expected_revision: int, **definition: Any) -> dict[str, Any]:
        _integer(expected_revision, "expected_revision", 1, 2**31 - 1)
        return self._request("POST", f"/datasets/{_identity(dataset_id)}/revisions",
            json={**definition, "expected_revision": expected_revision}).json()

    def export_dataset(self, dataset_id: str, destination: str | Path, *, revision: int | None = None) -> Path:
        dataset = self.get_dataset(dataset_id, revision=revision)
        return self._download_perception(f"/datasets/{_identity(dataset_id)}/export", destination,
            params={"revision": dataset["revision"]}, sha256=dataset["manifest_hash"],
            size_bytes=dataset["manifest_size_bytes"], maximum=DATASET_LIMIT)

    def detection_evaluators(self) -> list[dict[str, Any]]:
        return self._request("GET", "/detection-evaluators").json()

    def submit_detection_evaluation(self, *, name: str, note: str, dataset_id: str, dataset_revision: int,
                                    split: str, inference_job_ids: list[str], class_mapping: list[dict[str, Any]],
                                    idempotency_key: str, evaluator_id: str = "coco-bbox", score_threshold: float = 0.0,
                                    diagnostic_iou: float = 0.5) -> dict[str, Any]:
        """Queue saved-prediction evaluation against one explicit dataset revision.

        Retry this endpoint with the same key and exact body after a lost reply.
        No inference, annotation review, class mapping or revision is generated.
        """
        headers = self._intent_headers(idempotency_key)
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must not be blank")
        _text(name, "name")
        _text(note, "note", 4000)
        _identity(dataset_id)
        _integer(dataset_revision, "dataset_revision", 1, 2**31 - 1)
        _text(evaluator_id, "evaluator_id")
        if split not in SPLITS:
            raise ValueError("An explicit supported dataset split is required")
        if not isinstance(inference_job_ids, list) or not 1 <= len(inference_job_ids) <= 16:
            raise ValueError("inference_job_ids must contain 1..16 explicit source jobs")
        for identifier in inference_job_ids:
            _identity(identifier)
        if len(set(inference_job_ids)) != len(inference_job_ids):
            raise ValueError("Source inference jobs must be unique")
        if not isinstance(class_mapping, list) or not 1 <= len(class_mapping) <= 256:
            raise ValueError("class_mapping must contain 1..256 explicit source label mappings")
        source_labels = set()
        for mapping in class_mapping:
            if not isinstance(mapping, dict) or set(mapping) != {"model_label", "dataset_label"}:
                raise ValueError("Each mapping needs model_label and an explicit dataset_label or null")
            source_label = _text(mapping["model_label"], "model_label").strip()
            if source_label in source_labels:
                raise ValueError("Each source model label may appear only once")
            source_labels.add(source_label)
            if mapping["dataset_label"] is not None:
                _text(mapping["dataset_label"], "dataset_label")
        if isinstance(score_threshold, bool) or not isinstance(score_threshold, (int, float)) or not math.isfinite(score_threshold) or not 0 <= score_threshold <= 1:
            raise ValueError("score_threshold must be finite and within 0..1")
        if isinstance(diagnostic_iou, bool) or diagnostic_iou not in DIAGNOSTIC_IOUS:
            raise ValueError("diagnostic_iou must be one of 0.50:0.05:0.95")
        return self._request("POST", "/detection-evaluations", headers=headers, json={
            "name": name, "note": note, "dataset_id": dataset_id, "dataset_revision": dataset_revision,
            "split": split, "inference_job_ids": list(inference_job_ids), "class_mapping": class_mapping,
            "evaluator_id": evaluator_id, "score_threshold": score_threshold, "diagnostic_iou": diagnostic_iou}).json()

    def detection_evaluations(self, *, status: str | None = None, limit: int = 100,
                              before_seq: int | None = None) -> dict[str, Any]:
        _integer(limit, "limit", 1, 100)
        if before_seq is not None:
            _integer(before_seq, "before_seq", 1, 2**63 - 1)
        if status is not None and status not in STATUSES:
            raise ValueError("Unsupported evaluation job status")
        return self._request("GET", "/detection-evaluations", params={key: value for key, value in {
            "status": status, "limit": limit, "before_seq": before_seq}.items() if value is not None}).json()

    def get_detection_evaluation(self, evaluation_id: str) -> dict[str, Any]:
        evaluation_id = _identity(evaluation_id)
        result = self._request("GET", f"/detection-evaluations/{evaluation_id}").json()
        if not isinstance(result, dict) or result.get("id") != evaluation_id:
            raise ValueError("Detection evaluation identity mismatch")
        return result

    def cancel_detection_evaluation(self, evaluation_id: str) -> dict[str, Any]:
        return self._request("POST", f"/detection-evaluations/{_identity(evaluation_id)}/cancel").json()

    def wait_detection_evaluation(self, evaluation_id: str, *, timeout: float = 180,
                                   poll_interval: float = .5) -> dict[str, Any]:
        """GET-only polling; timeout never cancels, resubmits or recomputes evidence.

        A stalled GET retains the client's HTTP timeout and can extend wall time.
        Failed/cancelled/interrupted outcomes remain terminal results.
        """
        _identity(evaluation_id)
        for key, value in (("timeout", timeout), ("poll_interval", poll_interval)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (key == "poll_interval" and value == 0):
                raise ValueError("timeout must be finite/nonnegative and poll_interval finite/positive")
        deadline = time.monotonic() + timeout
        while True:
            evaluation = self.get_detection_evaluation(evaluation_id)
            if evaluation.get("status") not in STATUSES:
                raise ValueError("Invalid evaluation lifecycle status")
            if evaluation["status"] in TERMINAL:
                return evaluation
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Evaluation {evaluation_id} is still {evaluation['status']}; it was not cancelled or resubmitted")
            time.sleep(min(poll_interval, remaining))

    def export_detection_evaluation(self, evaluation_id: str, destination: str | Path) -> Path:
        evaluation = self.get_detection_evaluation(evaluation_id)
        if evaluation.get("status") not in TERMINAL:
            raise ValueError("Detection evaluation export requires a terminal sealed job")
        return self._download_perception(f"/detection-evaluations/{_identity(evaluation_id)}/export", destination,
            sha256=evaluation.get("manifest_hash"), size_bytes=evaluation.get("manifest_size_bytes"), maximum=EVALUATION_LIMIT)

    def compare_detection_evaluations(self, baseline_id: str, candidate_id: str) -> dict[str, Any]:
        """Read-only A/B review. A mismatch or incomplete coverage never becomes a score delta."""
        baseline_id, candidate_id = _identity(baseline_id), _identity(candidate_id)
        if baseline_id == candidate_id:
            raise ValueError("Choose two different evaluations")
        value = self._request("GET", "/evaluation-comparisons", params={
            "baseline_id": baseline_id, "candidate_id": candidate_id}).json()
        if (value.get("schema_version") != "detection-evaluation-comparison/1"
                or value.get("baseline", {}).get("id") != baseline_id
                or value.get("candidate", {}).get("id") != candidate_id):
            raise ValueError("Evaluation comparison identity mismatch")
        return value

    def export_evaluation_comparison(self, baseline_id: str, candidate_id: str,
                                    destination: str | Path, *, format: str = "json") -> Path:
        """Export a full-denominator review, verifying server-declared bytes/hash."""
        if format not in {"json", "html"}:
            raise ValueError("Comparison export format must be json or html")
        review = self.compare_detection_evaluations(baseline_id, candidate_id)
        descriptor = review.get("exports", {}).get(format)
        if not descriptor:
            raise ValueError("Comparison export requires two sealed terminal evaluations")
        return self._download_perception("/evaluation-comparisons/export", destination,
            params={"baseline_id": _identity(baseline_id), "candidate_id": _identity(candidate_id), "format": format},
            sha256=descriptor["sha256"], size_bytes=descriptor["size_bytes"], maximum=EVALUATION_LIMIT)
