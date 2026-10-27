"""Recorded-image inference on the common durable queue, with fenced frames."""

import base64
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile

from fastapi import Header, HTTPException, Query, Response
from pydantic import ValidationError
from sqlalchemy import func, select

from . import jobs
from .adapters import AdapterCancelled, AdapterCancelledBeforeDispatch, AdapterOwnershipLost
from .artifact_transactions import track_created_artifact
from .inference_runtime import InferenceInputError, InferencePrediction, RuntimeMetadata
from .job_models import JobEventRow, JobRow
from .job_schemas import JobStatus
from .model_schemas import ModelDetail, ModelSnapshot
from .models import get_model, verified_model_path
from .perception_models import PerceptionComparisonRow as ComparisonRow, PerceptionFrameRow, PerceptionJobRow
from .perception_schemas import (PerceptionComparisonCreate, PerceptionComparisonDetail, PerceptionComparisonSummary,
    PerceptionCounts, PerceptionFrame, PerceptionImage, PerceptionInput, PerceptionJobCreate, PerceptionJobDetail,
    PerceptionJobPage, PerceptionJobSummary)
from .recording_models import RecordingMessageRow
from .recordings import _get_recording, read_message_bytes
from .sensor_decoders import SensorDecoderRegistry
from .sensors import _channel, _resolve_decoder, sensor_frame
from .service import canonical_bytes, digest, uid, utcnow

MANIFEST_LIMIT = 32 * 1024 * 1024
JOB_IMAGE_LIMIT = 128 * 1024 * 1024
COMPARISON_POLICY = "same_recorded_image_predictions_v1"
_MANIFEST_FIELDS = {"manifest_hash", "manifest_size_bytes", "download_url"}


@dataclass(frozen=True)
class PreparedPerception:
    payload_json: bytes

    def to_payload(self):
        return json.loads(self.payload_json)

    @property
    def input_hash(self):
        return hashlib.sha256(self.payload_json).hexdigest()

    @classmethod
    def from_payload(cls, value):
        if set(value) != {"request", "model_snapshot", "recording_snapshot", "runtime_snapshot", "inputs"}:
            raise ValueError("Unknown prepared perception contract")
        body = PerceptionJobCreate.model_validate(value["request"])
        model = ModelDetail.model_validate(value["model_snapshot"])
        runtime = RuntimeMetadata.model_validate(value["runtime_snapshot"])
        inputs = [PerceptionInput.model_validate(item) for item in value["inputs"]]
        model_bytes = canonical_bytes(ModelSnapshot.model_validate(model.model_dump(exclude=_MANIFEST_FIELDS)).model_dump(mode="json"))
        if (len(model_bytes) != model.manifest_size_bytes or hashlib.sha256(model_bytes).hexdigest() != model.manifest_hash
                or digest(model.labels) != model.label_map_hash or model.artifact_id != model.artifact.id
                or model.id != body.model_id or model.revision != body.model_revision
                or runtime.id != body.runtime_id or model.profile not in runtime.profiles
                or len(inputs) != len(body.ordinals)):
            raise ValueError("Pinned perception definitions are inconsistent")
        for index, item in enumerate(inputs):
            if (item.selection_index != index or item.ordinal != body.ordinals[index]
                    or item.recording_id != body.recording_id or item.channel_id != body.channel_id
                    or item.decoder_id != body.decoder_id or item.decoder_version != body.decoder_version
                    or item.recording_sha256 != value["recording_snapshot"]["sha256"]):
                raise ValueError("Pinned frame identity differs from its request")
        return cls(canonical_bytes(value))


def _subject(session, row):
    subject = session.get(PerceptionJobRow, row.id)
    if row.kind != "perception" or subject is None:
        raise HTTPException(404, "Perception job not found")
    return subject


def _prepared(row):
    try:
        prepared = PreparedPerception.from_payload(row.prepared)
        value = prepared.to_payload()
        body = PerceptionJobCreate.model_validate(value["request"])
        if (prepared.input_hash != row.input_hash or digest(body.model_dump(mode="json")) != row.request_hash
                or row.request != body.model_dump(mode="json") or row.adapter_id != body.runtime_id
                or row.adapter_version != value["runtime_snapshot"]["version"]):
            raise ValueError("Queue request differs from its pinned input")
        return value
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(409, "Perception input snapshot failed its integrity check") from exc


def _frame_hash(frame):
    value = frame.model_dump(mode="json", exclude={"output_hash", "created_at", "started_at"})
    if value["runtime"]:
        value["runtime"] = {key: item for key, item in value["runtime"].items() if key not in {"inference_ms", "preprocessing_ms", "postprocessing_ms"}}
    return digest(value)


def _frames(session, row, prepared):
    entries = list(session.scalars(select(PerceptionFrameRow).where(PerceptionFrameRow.job_id == row.id).order_by(PerceptionFrameRow.selection_index)))
    try:
        if len(entries) != len(prepared["inputs"]):
            raise ValueError("Input denominator differs from stored frames")
        frames = []
        for index, entry in enumerate(entries):
            frame = PerceptionFrame.model_validate(entry.payload)
            if (entry.payload_hash != digest(entry.payload) or entry.selection_index != index or frame.selection_index != index
                    or frame.input.model_dump(mode="json") != prepared["inputs"][index]
                    or entry.recording_id != frame.input.recording_id or entry.ordinal != frame.input.ordinal):
                raise ValueError("Frame input relationship differs from its pinned identity")
            if frame.status in {"not_attempted", "running"}:
                if frame.output_hash or frame.image or frame.runtime or frame.detections or frame.created_at or frame.error:
                    raise ValueError("Unfinished frame contains execution results")
                if (frame.status == "not_attempted") != (frame.started_at is None):
                    raise ValueError("Frame dispatch time differs from its lifecycle state")
            elif not frame.created_at or not frame.started_at or frame.output_hash != _frame_hash(frame):
                raise ValueError("Frame output content differs from its hash")
            if frame.status == "completed":
                if frame.image is None or frame.runtime is None or not entry.image_storage_key or frame.error is not None:
                    raise ValueError("Completed frame is missing its input image or inference evidence")
            elif frame.detections or frame.runtime:
                raise ValueError("Unsuccessful frame cannot contain predictions")
            if frame.status == "failed" and not frame.error:
                raise ValueError("A failed frame must explain the absent prediction")
            if frame.image is not None and (not entry.image_storage_key or frame.image.download_url != _image_url(row.id, index)):
                raise ValueError("Frame image relationship is inconsistent")
            frames.append(frame)
        return frames
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(409, "Perception frame evidence failed its integrity check") from exc


def _unsealed_detail(session, row):
    subject = _subject(session, row)
    prepared = _prepared(row)
    if (subject.model_id != prepared["model_snapshot"]["id"] or subject.model_revision != prepared["model_snapshot"]["revision"]
            or subject.recording_id != prepared["request"]["recording_id"] or subject.channel_id != prepared["request"]["channel_id"]):
        raise HTTPException(409, "Perception subject relationship failed its integrity check")
    frames = _frames(session, row, prepared)
    counts = PerceptionCounts(total=len(frames), running=sum(item.status == "running" for item in frames), completed=sum(item.status == "completed" for item in frames),
                              failed=sum(item.status == "failed" for item in frames), not_attempted=sum(item.status == "not_attempted" for item in frames))
    events = list(session.scalars(select(JobEventRow).where(JobEventRow.job_id == row.id).order_by(JobEventRow.sequence.desc()).limit(jobs.EVENT_LIMIT)))
    events_total = session.scalar(select(func.count()).select_from(JobEventRow).where(JobEventRow.job_id == row.id))
    common = {name: getattr(row, name) for name in PerceptionJobSummary.model_fields
              if name not in {"label", "model_id", "model_revision", "model_name", "runtime_id", "runtime_version", "counts"}}
    return PerceptionJobDetail(**common, label=prepared["request"]["label"], model_id=subject.model_id, model_revision=subject.model_revision,
        model_name=prepared["model_snapshot"]["name"], runtime_id=row.adapter_id, runtime_version=row.adapter_version, counts=counts,
        request=prepared["request"], model_snapshot=prepared["model_snapshot"], recording_snapshot=prepared["recording_snapshot"],
        inputs=prepared["inputs"], frames=frames,
        events=[{"at": item.at, "type": item.type, "message": item.message} for item in reversed(events)], events_total=events_total)


def seal_terminal(session, row):
    """Called by the shared queue in the same transaction as terminal status."""
    session.flush()
    subject = _subject(session, row)
    if row.status not in jobs.TERMINAL or subject.manifest is not None:
        raise HTTPException(409, "Only an unsealed terminal perception job can be sealed")
    # A started frame without a committed prediction was attempted. Preserve
    # that fact on cancellation, process loss or a publication failure.
    prepared = _prepared(row)
    for frame in _frames(session, row, prepared):
        if frame.status == "running":
            frame.status, frame.created_at = "failed", row.finished_at
            frame.error = f"Job {row.status} while this frame was running; no prediction was published. " + (row.error or "")
            frame.output_hash = _frame_hash(frame)
            entry = session.get(PerceptionFrameRow, (row.id, frame.selection_index))
            entry.payload = frame.model_dump(mode="json")
            entry.payload_hash = digest(entry.payload)
            jobs._event(session, row, "frame_failed", f"Started input {frame.selection_index} ended without a published prediction ({row.status}).", row.finished_at)
    session.flush()
    content = canonical_bytes(_unsealed_detail(session, row).model_dump(mode="json", exclude=_MANIFEST_FIELDS))
    if len(content) > MANIFEST_LIMIT:
        raise HTTPException(413, "Perception evidence manifest exceeds 32 MiB")
    subject.manifest, subject.manifest_hash, subject.manifest_size_bytes = content, hashlib.sha256(content).hexdigest(), len(content)


def detail(session, row):
    session.flush()
    value = _unsealed_detail(session, row)
    subject = _subject(session, row)
    if row.status in jobs.TERMINAL:
        content = canonical_bytes(value.model_dump(mode="json", exclude=_MANIFEST_FIELDS))
        if (subject.manifest != content or subject.manifest_size_bytes != len(content)
                or subject.manifest_hash != hashlib.sha256(content).hexdigest()):
            raise HTTPException(409, "Terminal perception manifest failed its integrity check")
        value = value.model_copy(update={"manifest_hash": subject.manifest_hash, "manifest_size_bytes": subject.manifest_size_bytes,
                                         "download_url": f"/api/v1/perception/jobs/{row.id}/export"})
    elif subject.manifest is not None or subject.manifest_hash is not None or subject.manifest_size_bytes is not None:
        raise HTTPException(409, "A nonterminal job has sealed evidence")
    return value


def submit(db, runtimes, decoders, body: PerceptionJobCreate, key: str):
    with jobs._write(db) as session:
        return submit_in_session(db, session, runtimes, decoders, body, key)


def submit_in_session(db, session, runtimes, decoders, body: PerceptionJobCreate, key: str):
    """Compose queue publication within the caller transaction; no independent commit."""
    if not key or len(key) > 200:
        raise HTTPException(422, "A stable Idempotency-Key of at most 200 characters is required")
    request_hash, storage_key = digest(body.model_dump(mode="json")), "perception:" + key

    existing = session.scalar(select(JobRow).where(JobRow.idempotency_key == storage_key))
    if existing:
        if existing.kind != "perception" or existing.request_hash != request_hash:
            raise HTTPException(409, "Idempotency-Key was already used for a different perception request")
        return detail(session, existing)
    model = get_model(session, body.model_id, body.model_revision)
    runtime = runtimes.get(body.runtime_id)
    if runtime is None:
        raise HTTPException(422, "Requested inference runtime is not installed")
    metadata = RuntimeMetadata.model_validate(runtime.metadata())
    if metadata.status != "available" or model.profile not in metadata.profiles or model.profile not in {"opencv_yolox_rgb_v1", "boxes_xyxy_v1"}:
        raise HTTPException(422, "Inference runtime is unavailable or does not support the model profile")
    recording = _get_recording(session, body.recording_id)
    channel, schema = _channel(session, body.recording_id, body.channel_id)
    decoder = _resolve_decoder(decoders, channel, schema, body.decoder_id, body.decoder_version)
    if decoder.kind_for(channel, schema) != "camera":
        raise HTTPException(422, "Perception requires a supported camera channel")
    inputs = []
    for index, ordinal in enumerate(body.ordinals):
        entry = session.get(RecordingMessageRow, (recording.id, ordinal))
        if entry is None or entry.channel_id != channel.id:
            raise HTTPException(422, "Every ordinal must belong to the declared recording and camera channel")
        inputs.append(PerceptionInput(selection_index=index, recording_id=recording.id, recording_sha256=recording.sha256,
            channel_id=body.channel_id, ordinal=ordinal, log_time_ns=str(int(entry.log_time_key)), publish_time_ns=str(int(entry.publish_time_key)),
            message_sha256=entry.sha256, message_size_bytes=entry.payload_length, decoder_id=body.decoder_id, decoder_version=body.decoder_version))
    recording_snapshot = {"id": recording.id, "sha256": recording.sha256, "name": recording.payload["name"],
        "source_kind": recording.payload.get("source_kind", "unknown"), "source": recording.payload.get("source", ""),
        "channel": channel.model_dump(mode="json"), "schema": schema.model_dump(mode="json") if schema else None}
    prepared = PreparedPerception.from_payload({"request": body.model_dump(mode="json"), "model_snapshot": model.model_dump(mode="json"),
        "recording_snapshot": recording_snapshot, "runtime_snapshot": metadata.model_dump(mode="json"), "inputs": [item.model_dump(mode="json") for item in inputs]})
    now = utcnow()
    row = JobRow(id=uid("pjob"), kind="perception", idempotency_key=storage_key, request_hash=request_hash,
        request=body.model_dump(mode="json"), prepared=prepared.to_payload(), input_hash=prepared.input_hash,
        adapter_id=metadata.id, adapter_version=metadata.version, status="queued", created_at=now, updated_at=now,
        cancellation_supported=True, resource_required=False, outcome_uncertain=False, evidence_eligible=False)
    session.add(row)
    session.flush()
    session.add(PerceptionJobRow(job_id=row.id, model_id=model.id, model_revision=model.revision,
                                 recording_id=recording.id, channel_id=body.channel_id))
    session.flush()
    for item in inputs:
        frame = PerceptionFrame(selection_index=item.selection_index, status="not_attempted", input=item)
        payload = frame.model_dump(mode="json")
        session.add(PerceptionFrameRow(job_id=row.id, selection_index=item.selection_index, recording_id=recording.id,
                                       ordinal=item.ordinal, payload=payload, payload_hash=digest(payload)))
    jobs._event(session, row, "queued", "Model, runtime, camera decoder and exact original message identities pinned; inference has not started.", now)
    return detail(session, row)


def _image_url(job_id, index):
    return f"/api/v1/perception/jobs/{job_id}/frames/{index}/image"


def start_frame(db, claim, selection_index):
    """Persist attempted-frame identity before decoding or native inference."""
    with jobs._write(db) as session:
        row = jobs._live(session, claim)
        if row.kind != "perception" or row.dispatched_at is None or row.input_hash != claim.prepared.input_hash:
            raise ValueError("A frame requires its live dispatched perception claim")
        frames = _frames(session, row, _prepared(row))
        if not 0 <= selection_index < len(frames) or frames[selection_index].status != "not_attempted" or any(frame.status == "running" for frame in frames):
            raise HTTPException(409, "Only one previously unstarted input can be started by this claim")
        frame = frames[selection_index]
        frame.status, frame.started_at = "running", utcnow()
        entry = session.get(PerceptionFrameRow, (row.id, selection_index))
        entry.payload = frame.model_dump(mode="json")
        entry.payload_hash = digest(entry.payload)
        row.updated_at = frame.started_at
        jobs._event(session, row, "frame_started", f"Starting input {selection_index} (file ordinal {frame.input.ordinal}).", frame.started_at)
        session.flush()
        jobs._live(session, claim)
        return frame.started_at


def validate_prediction(prediction, prepared, png, width, height):
    """Validate reported content and policy; hashes alone do not prove inference."""
    value = InferencePrediction.model_validate(prediction)
    body, model, runtime = prepared["request"], prepared["model_snapshot"], prepared["runtime_snapshot"]
    evidence = value.evidence
    if (evidence.runtime_id != runtime["id"] or evidence.runtime_version != runtime["version"]
            or evidence.model_sha256 != model["artifact"]["sha256"] or evidence.profile != model["profile"]
            or evidence.seed != body["seed"] or evidence.image_sha256 != hashlib.sha256(png).hexdigest()
            or not re.fullmatch("[0-9a-f]{64}", evidence.tensor_sha256)
            or evidence.tensor_shape != [1, 3, model["input_height"], model["input_width"]]
            or not all(str(item).strip() for item in [evidence.engine, evidence.engine_version, evidence.provider])
            or evidence.preprocessing_version != "opencv_inter_linear_v1" or evidence.postprocessing_version != "class_aware_xyxy_v1"):
        raise ValueError("Runtime evidence differs from the pinned inference identity or policy")
    durations = (evidence.inference_ms, evidence.preprocessing_ms, evidence.postprocessing_ms)
    if any(not math.isfinite(item) or item < 0 for item in durations):
        raise ValueError("Runtime durations must be finite and nonnegative")
    iw, ih = model["input_width"], model["input_height"]
    yolo = model["profile"] == "opencv_yolox_rgb_v1"
    ratio = min(iw / width, ih / height)
    rw, rh = (int(width * ratio), int(height * ratio)) if yolo else (iw, ih)
    expected_transform = {"source_width": width, "source_height": height, "input_width": iw, "input_height": ih,
        "resized_width": rw, "resized_height": rh, "scale_x": rw / width, "scale_y": rh / height,
        "pad_left": 0, "pad_top": 0, "pad_right": iw - rw, "pad_bottom": ih - rh,
        "resize": "opencv_inter_linear", "color_order": "RGB", "layout": "NCHW", "value_scale": 1.0 if yolo else 1 / 255,
        "padding_value": 114.0 if yolo else 0.0, "box_inverse": "xyxy_divide_scale_then_clip", "nms": "class_aware_continuous_iou_v1"}
    if evidence.transform != expected_transform:
        raise ValueError("Reported image/tensor inverse transform differs from the pinned profile")
    if (len(value.detections) > body["max_detections"] or len({item.id for item in value.detections}) != len(value.detections)
            or evidence.source_candidates < evidence.threshold_candidates
            or evidence.threshold_candidates != evidence.suppressed_candidates + evidence.discarded_empty_boxes + evidence.max_detections_truncated + len(value.detections)
            or evidence.clipped_detections > len(value.detections)):
        raise ValueError("Runtime detection count accounting is inconsistent")
    for item in value.detections:
        x1, y1, x2, y2 = item.bbox_xyxy
        if (not item.id or item.class_id >= len(model["labels"]) or model["labels"][item.class_id] != item.label
                or not all(math.isfinite(number) for number in (*item.bbox_xyxy, item.score))
                or not 0 <= x1 < x2 <= width or not 0 <= y1 < y2 <= height or item.score < body["score_threshold"]):
            raise ValueError("Prediction labels, scores or decoded-image pixel bounds are invalid")
    return value


def publish_frame(db, claim, frame: PerceptionFrame, png: bytes | None = None):
    """Stage bytes without a DB transaction, then fence both sides of publication."""
    prepared = claim.prepared.to_payload()
    frame = frame.model_copy(deep=True)
    if frame.input.model_dump(mode="json") != prepared["inputs"][frame.selection_index] or frame.status not in {"completed", "failed"}:
        raise ValueError("Frame publication differs from this claim's pinned input")
    if frame.status == "completed":
        if png is None or frame.image is None or frame.runtime is None:
            raise ValueError("Completed inference must include saved input pixels and runtime evidence")
        validate_prediction({"detections": [item.model_dump(mode="json") for item in frame.detections], "evidence": frame.runtime},
                            prepared, png, frame.image.width, frame.image.height)
        if frame.error is not None:
            raise ValueError("A completed frame cannot carry a failure reason")
    elif not frame.error or frame.detections or frame.runtime is not None:
        raise ValueError("Failed inference requires a reason and cannot contain predictions")
    if png is not None:
        from .sensor_decoders import validate_png_preview
        if frame.image is None or len(png) != frame.image.size_bytes or hashlib.sha256(png).hexdigest() != frame.image.sha256:
            raise ValueError("Saved image bytes differ from frame metadata")
        validate_png_preview(png, frame.image.width, frame.image.height)
        frame.image.download_url = _image_url(claim.job.id, frame.selection_index)
    elif frame.image is not None:
        raise ValueError("Image metadata cannot exist without saved image bytes")
    staged = None
    try:
        if png is not None:
            fd, name = tempfile.mkstemp(prefix="frame_", suffix=".png", dir=db.perception_staging_dir)
            staged = Path(name)
            import os
            with os.fdopen(fd, "wb") as stream:
                if stream.write(png) != len(png):
                    raise OSError("Incomplete staged inference image write")
        with jobs._write(db) as session:
            row = jobs._live(session, claim)
            if row.kind != "perception" or row.dispatched_at is None or row.input_hash != claim.prepared.input_hash:
                raise ValueError("Frame publication requires its live dispatched perception claim")
            _prepared(row)
            entry = session.get(PerceptionFrameRow, (row.id, frame.selection_index))
            if entry is None or entry.payload_hash != digest(entry.payload) or entry.payload["status"] != "running":
                raise HTTPException(409, "A frame result has already been published or does not belong to this job")
            frame.started_at = entry.payload["started_at"]
            frame.created_at, frame.output_hash = utcnow(), None
            frame.output_hash = _frame_hash(frame)
            if staged is not None:
                saved_bytes = sum(item.payload["image"]["size_bytes"] for item in session.scalars(select(PerceptionFrameRow).where(PerceptionFrameRow.job_id == row.id)) if item.payload.get("image"))
                if saved_bytes + len(png) > JOB_IMAGE_LIMIT:
                    raise ValueError("Perception job input PNG copies exceed 128 MiB")
                storage_key = uid("image") + ".png"
                destination = db.perception_dir / storage_key
                destination.hardlink_to(staged)
                track_created_artifact(session, destination)
                entry.image_storage_key = storage_key
            entry.payload = frame.model_dump(mode="json")
            entry.payload_hash = digest(entry.payload)
            row.updated_at = frame.created_at
            jobs._event(session, row, "frame_" + frame.status, f"Input {frame.selection_index} (file ordinal {frame.input.ordinal}) {frame.status}.", frame.created_at)
            session.flush()
            jobs._live(session, claim)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def _load_image(db, decoders, prepared, item):
    with db.session() as session:
        recording = _get_recording(session, item.recording_id)
        entry = session.get(RecordingMessageRow, (item.recording_id, item.ordinal))
        channel, schema = _channel(session, item.recording_id, item.channel_id)
        pinned = prepared["recording_snapshot"]
        if (recording.sha256 != item.recording_sha256 or entry is None or entry.channel_id != item.channel_id
                or entry.sha256 != item.message_sha256 or entry.payload_length != item.message_size_bytes
                or str(int(entry.log_time_key)) != item.log_time_ns or str(int(entry.publish_time_key)) != item.publish_time_ns
                or channel.model_dump(mode="json") != pinned["channel"] or (schema.model_dump(mode="json") if schema else None) != pinned["schema"]):
            raise ValueError("Source message or camera declaration differs from the queued snapshot")
        read_message_bytes(db, recording, entry)
        decoded = sensor_frame(session, db, decoders, item.recording_id, item.channel_id, item.ordinal, 1,
                               decoder_id=item.decoder_id, decoder_version=item.decoder_version)
    if decoded.status != "decoded" or decoded.decoded is None or decoded.decoded.camera is None:
        raise ValueError("Camera decode failed: " + (decoded.reason or decoded.status))
    camera = decoded.decoded.camera
    png = base64.b64decode(camera.data_base64, validate=True)
    image = PerceptionImage(sha256=camera.sha256, size_bytes=camera.size_bytes, width=camera.width, height=camera.height,
        download_url="", capture_time_ns=decoded.decoded.capture_time_ns, frame_id=decoded.decoded.frame_id)
    return png, image


def execute_claim(db, claim, runtime, decoders, should_cancel):
    """Called inside the common worker heartbeat/exception lifecycle."""
    prepared = claim.prepared.to_payload()
    body, model = prepared["request"], prepared["model_snapshot"]
    if should_cancel():
        raise AdapterCancelledBeforeDispatch()
    path = verified_model_path(db, model["artifact_id"])
    # Native context exit must stop its child before acknowledging cancellation.
    with runtime.open(path, model, model["artifact"]["sha256"], body["seed"], should_cancel) as engine:
        for value in prepared["inputs"]:
            if should_cancel():
                raise AdapterCancelled()
            item = PerceptionInput.model_validate(value)
            started = start_frame(db, claim, item.selection_index)
            frame = PerceptionFrame(selection_index=item.selection_index, status="failed", input=item, started_at=started)
            png = None
            try:
                png, frame.image = _load_image(db, decoders, prepared, item)
                prediction = engine.infer(png, width=frame.image.width, height=frame.image.height,
                    score_threshold=body["score_threshold"], iou_threshold=body["iou_threshold"],
                    max_detections=body["max_detections"], should_cancel=should_cancel)
                prediction = validate_prediction(prediction, prepared, png, frame.image.width, frame.image.height)
                frame.status, frame.detections, frame.runtime = "completed", prediction.detections, prediction.evidence.model_dump(mode="json")
            except (AdapterCancelled, AdapterOwnershipLost):
                raise
            except Exception as exc:
                frame.error = f"{type(exc).__name__}: {str(exc)[:1500]}"
            publish_frame(db, claim, frame, png)
    with jobs._write(db) as session:
        row = jobs._live(session, claim)
        value = _unsealed_detail(session, row)
        if value.counts.not_attempted or value.counts.running:
            raise ValueError("Cannot complete a job with unattempted input frames")
        if row.cancel_requested_at:
            jobs._event(session, row, "cancel_not_honoured", "All selected frame results arrived before cancellation was acknowledged; completed evidence is retained.", utcnow())
        status = "failed" if value.counts.failed else "completed"
        row.error = f"{value.counts.failed} selected frames failed; inspect frame errors." if value.counts.failed else None
        jobs._terminal(session, row, status, utcnow(), "All selected frame outcomes are stored; no ground-truth or accuracy verdict was calculated.")
        return detail(session, row)


def _terminal_copy(value):
    """Validate an embedded frozen source without consulting mutable job rows."""
    source = PerceptionJobDetail.model_validate(value)
    content = canonical_bytes(source.model_dump(mode="json", exclude=_MANIFEST_FIELDS))
    if (source.status not in jobs.TERMINAL or source.manifest_hash != hashlib.sha256(content).hexdigest()
            or source.manifest_size_bytes != len(content)):
        raise ValueError("Captured source does not match its terminal manifest")
    return source


def _comparison_report(comparison_id, body, now, left, right):
    def key(frame):
        item = frame.input
        return f"{item.recording_sha256}:{item.channel_id}:{item.ordinal}:{item.message_sha256}"
    lhs, rhs = {key(frame): frame for frame in left.frames}, {key(frame): frame for frame in right.frames}
    rows = []
    for identity in dict.fromkeys([*lhs, *rhs]):
        a, b = lhs.get(identity), rhs.get(identity)
        status = "right_only" if a is None else "left_only" if b is None else "not_evaluated"
        delta = identical = None
        if a and b and a.status == b.status == "completed":
            status = "same_input" if a.image.sha256 == b.image.sha256 and (a.image.width, a.image.height) == (b.image.width, b.image.height) else "input_changed"
            if status == "same_input":
                delta = len(b.detections) - len(a.detections)
                # Generated detection IDs are references, not prediction values.
                identical = [item.model_dump(exclude={"id"}) for item in a.detections] == [item.model_dump(exclude={"id"}) for item in b.detections]
        rows.append({"key": identity, "status": status, "left": a.model_dump(mode="json") if a else None,
            "right": b.model_dump(mode="json") if b else None, "detection_count_delta": delta, "predictions_identical": identical})
    fields = ("model_id", "model_revision", "runtime_id", "seed", "score_threshold", "iou_threshold", "max_detections", "decoder_id", "decoder_version")
    differences = [{"field": name, "left": getattr(left.request, name), "right": getattr(right.request, name)}
                   for name in fields if getattr(left.request, name) != getattr(right.request, name)]
    for name, a, b in [("runtime_version", left.runtime_version, right.runtime_version),
                       ("model_manifest_hash", left.model_snapshot["manifest_hash"], right.model_snapshot["manifest_hash"]),
                       ("label_map_hash", left.model_snapshot["label_map_hash"], right.model_snapshot["label_map_hash"]),
                       ("model_artifact_sha256", left.model_snapshot["artifact"]["sha256"], right.model_snapshot["artifact"]["sha256"])]:
        if a != b:
            differences.append({"field": name, "left": a, "right": b})
    # Actual runtime libraries/provider can differ even when package versions
    # have not changed. Disclose those observed contexts without causal claims.
    context_fields = ("engine", "engine_version", "provider", "library_versions", "profile", "preprocessing_version", "postprocessing_version")
    def contexts(job):
        values = {canonical_bytes({name: frame.runtime.get(name) for name in context_fields})
                  for frame in job.frames if frame.status == "completed" and frame.runtime}
        return [json.loads(value) for value in sorted(values)]
    a, b = contexts(left), contexts(right)
    if a != b:
        differences.append({"field": "observed_runtime_contexts", "left": a, "right": b})
    return {"id": comparison_id, "name": body.name, "note": body.note, "created_at": now,
        "left_job_id": left.id, "right_job_id": right.id, "left_manifest_hash": left.manifest_hash, "right_manifest_hash": right.manifest_hash,
        "policy_version": COMPARISON_POLICY, "configuration_differences": differences, "rows": rows,
        "counts": {"total": len(rows), **{status: sum(row["status"] == status for row in rows)
            for status in ("same_input", "input_changed", "left_only", "right_only", "not_evaluated")}}}


def comparison_get(session, comparison_id):
    row = session.get(ComparisonRow, comparison_id)
    if row is None:
        raise HTTPException(404, "Perception comparison not found")
    try:
        if (len(row.manifest) != row.manifest_size_bytes or not 0 < len(row.manifest) <= MANIFEST_LIMIT
                or hashlib.sha256(row.manifest).hexdigest() != row.manifest_hash):
            raise ValueError("Comparison content differs from its hash")
        manifest = json.loads(row.manifest)
        if set(manifest) != {"schema_version", "comparison", "left", "right"} or manifest["schema_version"] != "1" or canonical_bytes(manifest) != row.manifest:
            raise ValueError("Unknown or noncanonical comparison manifest")
        left, right = _terminal_copy(manifest["left"]), _terminal_copy(manifest["right"])
        report = manifest["comparison"]
        body = PerceptionComparisonCreate(name=report["name"], note=report["note"], left_job_id=left.id, right_job_id=right.id)
        expected = _comparison_report(row.id, body, row.created_at, left, right)
        if (report != expected or row.left_job_id != left.id or row.right_job_id != right.id or row.request_hash != digest(body.model_dump(mode="json"))):
            raise ValueError("Comparison report differs from its captured inputs and policy")
        return PerceptionComparisonDetail(**report, manifest_hash=row.manifest_hash, manifest_size_bytes=row.manifest_size_bytes,
                                          download_url=f"/api/v1/perception/comparisons/{row.id}/export")
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(409, "Perception comparison failed its integrity check") from exc


def create_comparison(db, body, key):
    if not key or len(key) > 200:
        raise HTTPException(422, "A stable Idempotency-Key of at most 200 characters is required")
    request_hash = digest(body.model_dump(mode="json"))
    with jobs._write(db) as session:
        existing = session.scalar(select(ComparisonRow).where(ComparisonRow.idempotency_key == key))
        if existing:
            if existing.request_hash != request_hash:
                raise HTTPException(409, "Idempotency-Key was already used for a different comparison")
            return comparison_get(session, existing.id)
        left, right = detail(session, jobs._row(session, body.left_job_id)), detail(session, jobs._row(session, body.right_job_id))
        if left.status not in jobs.TERMINAL or right.status not in jobs.TERMINAL:
            raise HTTPException(409, "Both perception jobs must be terminal before capturing a comparison")
        identifier, now = uid("pcmp"), utcnow()
        report = _comparison_report(identifier, body, now, left, right)
        content = canonical_bytes({"schema_version": "1", "comparison": report, "left": left.model_dump(mode="json"), "right": right.model_dump(mode="json")})
        if len(content) > MANIFEST_LIMIT:
            raise HTTPException(413, "Perception comparison manifest exceeds 32 MiB")
        session.add(ComparisonRow(id=identifier, idempotency_key=key, request_hash=request_hash,
            left_job_id=left.id, right_job_id=right.id, created_at=now, manifest=content,
            manifest_hash=hashlib.sha256(content).hexdigest(), manifest_size_bytes=len(content)))
        session.flush()
        return comparison_get(session, identifier)


def image_bytes(session, db, job_id, index):
    value = detail(session, jobs._row(session, job_id))
    if not 0 <= index < len(value.frames) or value.frames[index].image is None:
        raise HTTPException(404, "Saved inference image not found")
    frame = value.frames[index]
    entry = session.get(PerceptionFrameRow, (job_id, index))
    path = (db.perception_dir / entry.image_storage_key).resolve()
    if path.parent != db.perception_dir.resolve() or not path.is_file():
        raise HTTPException(409, "Saved image is outside managed storage or unavailable")
    if path.stat().st_size != frame.image.size_bytes or not 0 < path.stat().st_size <= 16 * 1024 * 1024:
        raise HTTPException(409, "Saved image size integrity check failed")
    content = path.read_bytes()
    if len(content) != frame.image.size_bytes or hashlib.sha256(content).hexdigest() != frame.image.sha256:
        raise HTTPException(409, "Saved image SHA-256 integrity check failed")
    return content


def register_perception_routes(api, db, runtimes, decoders):
    prefix = "/perception"

    @api.get(prefix + "/runtimes", response_model=list[RuntimeMetadata], tags=["perception"])
    def runtime_list():
        return runtimes.list()

    @api.post(prefix + "/jobs", response_model=PerceptionJobDetail, status_code=202, tags=["perception"])
    def post_job(body: PerceptionJobCreate, idempotency_key: str = Header(min_length=1, max_length=200)):
        return submit(db, runtimes, decoders, body, idempotency_key)

    @api.get(prefix + "/jobs", response_model=PerceptionJobPage, tags=["perception"])
    def list_jobs(status: JobStatus | None = None, limit: int = Query(100, ge=1, le=100), before_seq: int | None = Query(None, ge=1)):
        with db.session() as session:
            query = select(JobRow).where(JobRow.kind == "perception").order_by(JobRow.submission_seq.desc())
            if status is not None:
                query = query.where(JobRow.status == status)
            if before_seq is not None:
                query = query.where(JobRow.submission_seq < before_seq)
            rows = list(session.scalars(query.limit(limit + 1)))
            items = []
            for row in rows[:limit]:
                value = detail(session, row)
                items.append(PerceptionJobSummary.model_validate(value.model_dump(include=set(PerceptionJobSummary.model_fields))))
            return {"items": items, "next_before_seq": rows[limit - 1].submission_seq if len(rows) > limit else None}

    @api.get(prefix + "/jobs/{job_id}", response_model=PerceptionJobDetail, tags=["perception"])
    def get_job(job_id: str):
        return jobs.job_get(db, job_id, kind="perception")

    @api.post(prefix + "/jobs/{job_id}/cancel", response_model=PerceptionJobDetail, tags=["perception"])
    def cancel_job(job_id: str):
        return jobs.cancel_job(db, job_id, kind="perception")

    @api.get(prefix + "/jobs/{job_id}/export", tags=["perception"])
    def export_job(job_id: str):
        with db.session() as session:
            value = detail(session, jobs._row(session, job_id))
            if value.status not in jobs.TERMINAL:
                raise HTTPException(409, "Perception export requires a terminal job")
            return Response(_subject(session, jobs._row(session, job_id)).manifest, media_type="application/json",
                            headers={"Content-Disposition": f'attachment; filename="{value.id}.perception.json"'})

    @api.get(prefix + "/jobs/{job_id}/frames/{selection_index}/image", tags=["perception"])
    def download_image(job_id: str, selection_index: int):
        with db.session() as session:
            return Response(image_bytes(session, db, job_id, selection_index), media_type="image/png", headers={"X-Content-Type-Options": "nosniff"})

    @api.post(prefix + "/comparisons", response_model=PerceptionComparisonDetail, status_code=201, tags=["perception"])
    def post_comparison(body: PerceptionComparisonCreate, idempotency_key: str = Header(min_length=1, max_length=200)):
        return create_comparison(db, body, idempotency_key)

    @api.get(prefix + "/comparisons", response_model=list[PerceptionComparisonSummary], tags=["perception"])
    def list_comparisons():
        with db.session() as session:
            return [PerceptionComparisonSummary.model_validate(comparison_get(session, row.id).model_dump(include=set(PerceptionComparisonSummary.model_fields)))
                    for row in session.scalars(select(ComparisonRow).order_by(ComparisonRow.created_at.desc(), ComparisonRow.id.desc()))]

    @api.get(prefix + "/comparisons/{comparison_id}", response_model=PerceptionComparisonDetail, tags=["perception"])
    def get_comparison(comparison_id: str):
        with db.session() as session:
            return comparison_get(session, comparison_id)

    @api.get(prefix + "/comparisons/{comparison_id}/export", tags=["perception"])
    def export_comparison(comparison_id: str):
        with db.session() as session:
            value = comparison_get(session, comparison_id)
            return Response(session.get(ComparisonRow, comparison_id).manifest, media_type="application/json",
                            headers={"Content-Disposition": f'attachment; filename="{value.id}.perception-comparison.json"'})
