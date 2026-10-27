"""Queue/storage contract tests use an explicit fake runtime, not an ML claim."""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import multiprocessing
import os
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import event, func, select

from ade2e import jobs, perception_jobs as perception
from ade2e.adapters import AdapterCancelled, AdapterOwnershipLost, Registry
from ade2e.database import Database, RunRow, ScenarioRow, Settings
from ade2e.inference_runtime import InferenceCancelled, InferenceExecutionError, RuntimeEvidence, RuntimeMetadata, RuntimeRegistry
from ade2e.job_models import JobRow
from ade2e.main import create_app
from ade2e.perception_models import PerceptionFrameRow, PerceptionJobRow, PerceptionComparisonRow
from ade2e.perception_schemas import PerceptionJobCreate, PerceptionComparisonCreate
from ade2e.recording_models import RecordingMessageRow, RecordingRow
from ade2e.service import canonical_bytes, digest
from ade2e.worker import run_claim, run_once
from test_sensors import BASE, make_recording


class FakeRuntime:
    """Test-only PNG-dependent output. It does not execute ONNX or a trained model."""
    def __init__(self):
        self.calls = 0
        self.corrupt = None
        self.failure_at = None
        self.after_result = None
        self.version = "0.1.0"
        self.closed = False

    def metadata(self):
        return RuntimeMetadata(id="test-inference", version=self.version, name="Explicit contract fake", status="available", profiles=["boxes_xyxy_v1"])

    @contextmanager
    def open(self, path, model, sha, seed, should_cancel):
        self.model, self.sha, self.seed = model, sha, seed
        try:
            yield self
        finally:
            self.closed = True

    def infer(self, png, *, width, height, score_threshold, iou_threshold, max_detections, should_cancel):
        self.calls += 1
        if self.failure_at == self.calls:
            raise InferenceExecutionError("Explicit fake failure")
        color = Image.open(io.BytesIO(png)).getpixel((0, 0))
        iw, ih = self.model["input_width"], self.model["input_height"]
        detections = [] if color[2] else [{"id": "prediction-0", "class_id": 0, "label": self.model["labels"][0], "score": 0.75,
                                          "bbox_xyxy": [0, 0, width, height]}]
        if score_threshold > 0.75:
            detections = []
        evidence = RuntimeEvidence(runtime_id="test-inference", runtime_version=self.version, engine="contract-fake", engine_version="1", provider="test",
            model_sha256=self.sha, profile="boxes_xyxy_v1", preprocessing_version="opencv_inter_linear_v1", postprocessing_version="class_aware_xyxy_v1",
            image_sha256=hashlib.sha256(png).hexdigest(), tensor_sha256=hashlib.sha256(b"reported-test-tensor").hexdigest(), tensor_shape=[1, 3, ih, iw],
            transform={"source_width": width, "source_height": height, "input_width": iw, "input_height": ih,
                "resized_width": iw, "resized_height": ih, "scale_x": iw / width, "scale_y": ih / height,
                "pad_left": 0, "pad_top": 0, "pad_right": 0, "pad_bottom": 0, "resize": "opencv_inter_linear",
                "color_order": "RGB", "layout": "NCHW", "value_scale": 1 / 255, "padding_value": 0.0,
                "box_inverse": "xyxy_divide_scale_then_clip", "nms": "class_aware_continuous_iou_v1"},
            source_candidates=1, threshold_candidates=len(detections), suppressed_candidates=0, clipped_detections=0,
            discarded_empty_boxes=0, max_detections_truncated=0, inference_ms=1, preprocessing_ms=1, postprocessing_ms=1, seed=self.seed)
        output = {"detections": detections, "evidence": evidence.model_dump(mode="json")}
        if self.corrupt:
            self.corrupt(output)
        if self.after_result:
            self.after_result(self.calls)
        return output


@pytest.fixture
def context(settings):
    runtime = FakeRuntime()
    runtimes = RuntimeRegistry(load_plugins=False)
    runtimes.register(runtime)
    app = create_app(settings, Registry(load_plugins=False), runtime_registry=runtimes)
    with TestClient(app) as client:
        artifact = client.post("/api/v1/perception/model-artifacts?name=contract-fixture.onnx", content=b"contract-only-model-bytes",
                               headers={"Content-Type": "application/octet-stream"})
        assert artifact.status_code == 201, artifact.text
        response = client.post("/api/v1/perception/models", json={"name": "Contract test model", "artifact_id": artifact.json()["id"],
            "profile": "boxes_xyxy_v1", "input_width": 32, "input_height": 32, "labels": ["test-object"], "note": "Explicit fake runtime contract test"})
        assert response.status_code == 201, response.text
        model = response.json()
        recording, channels, _ = make_recording(client)
        body = PerceptionJobCreate(label="Contract fake inference", model_id=model["id"], model_revision=1,
            runtime_id="test-inference", recording_id=recording["id"], channel_id=channels[0], ordinals=[1, 2, 0], decoder_id="foxglove-json", decoder_version="1")
        yield client, app, runtime, body


def submit(context, key="request-1", body=None):
    client, _, _, original = context
    response = client.post("/api/v1/perception/jobs", json=(body or original).model_dump(mode="json"), headers={"Idempotency-Key": key})
    assert response.status_code == 202, response.text
    return response.json()


def execute(context, job):
    _, app, _, _ = context
    return run_once(app.state.db, app.state.registry, "inference-test-worker", job_id=job["id"],
                    runtime_registry=app.state.runtime_registry, sensor_registry=app.state.sensor_registry)


def claim(context, job):
    _, app, _, _ = context
    result = jobs.claim_job(app.state.db, "test-owner", job_id=job["id"], capabilities={("perception", "test-inference", "0.1.0")})
    assert result is not None
    assert jobs.mark_dispatched(app.state.db, result)
    return result


def test_actual_source_png_is_persisted_with_predictions_and_full_denominator(context):
    client, app, runtime, body = context
    job = submit(context)
    assert runtime.calls == 0 and job["counts"] == {"total": 3, "running": 0, "completed": 0, "failed": 0, "not_attempted": 3}
    assert [item["ordinal"] for item in job["inputs"]] == [1, 2, 0]
    assert job["inputs"][0]["log_time_ns"] == str(BASE)
    result = execute(context, job)
    assert result.status == "completed", result
    assert result.counts.model_dump() == {"total": 3, "running": 0, "completed": 3, "failed": 0, "not_attempted": 0}
    assert [len(frame.detections) for frame in result.frames] == [1, 1, 0]
    assert result.frames[0].image.sha256 != result.frames[1].image.sha256
    for frame, color in zip(result.frames, [(255, 0, 0), (0, 255, 0), (0, 0, 255)]):
        image = client.get(frame.image.download_url)
        assert image.status_code == 200
        assert hashlib.sha256(image.content).hexdigest() == frame.image.sha256
        assert Image.open(io.BytesIO(image.content)).getpixel((0, 0)) == color
    export = client.get(result.download_url)
    assert hashlib.sha256(export.content).hexdigest() == result.manifest_hash
    assert len(export.content) == result.manifest_size_bytes
    assert client.get(f'/api/v1/jobs/{job["id"]}').status_code == 404
    assert client.post(f'/api/v1/jobs/{job["id"]}/cancel').status_code == 404
    assert client.get("/api/v1/jobs").json()["items"] == []
    with app.state.db.session() as session:
        assert session.scalar(select(func.count()).select_from(RunRow)) == 0
        assert session.scalar(select(func.count()).select_from(ScenarioRow)) == 0
        assert jobs.latest_job_evidence(session) == {}


def test_idempotency_replays_before_model_runtime_or_source_checks(context):
    client, app, _, body = context
    job = submit(context)
    app.state.runtime_registry._runtimes.clear()
    assert submit(context)["id"] == job["id"]
    assert client.post("/api/v1/perception/jobs", json=body.model_copy(update={"seed": 91}).model_dump(), headers={"Idempotency-Key": "request-1"}).status_code == 409
    assert client.post("/api/v1/perception/jobs", json=body.model_dump()).status_code == 422


@pytest.mark.parametrize("change", [{"ordinals": [1, 1]}, {"ordinals": []}, {"ordinals": [3]}, {"ordinals": [999]},
    {"decoder_version": "unknown"}, {"decoder_id": "unavailable"}, {"model_revision": 999}, {"runtime_id": "onnx-cpu"}, {"score_threshold": 2}])
def test_invalid_input_leaves_no_queued_job(context, change):
    client, app, _, body = context
    value = body.model_dump()
    value.update(change)
    response = client.post("/api/v1/perception/jobs", json=value, headers={"Idempotency-Key": "invalid"})
    assert response.status_code in {404, 422}, response.text
    with app.state.db.session() as session:
        assert session.scalar(select(func.count()).select_from(JobRow)) == 0


def test_concurrent_duplicate_submission_creates_one_intent(context):
    _, app, _, body = context
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: perception.submit(app.state.db, app.state.runtime_registry, app.state.sensor_registry, body, "parallel").id, range(4)))
    assert len(set(results)) == 1


def test_worker_capability_filter_keeps_uninstalled_or_changed_version_queued(context):
    _, app, runtime, _ = context
    job = submit(context)
    runtime.version = "changed"
    assert execute(context, job) is None
    assert jobs.job_get(app.state.db, job["id"], kind="perception").status == "queued"
    assert jobs.claim_job(app.state.db, "legacy-worker") is None
    assert runtime.calls == 0


def test_failed_frame_is_not_empty_success_and_prior_results_survive(context):
    client, _, runtime, _ = context
    runtime.failure_at = 2
    result = execute(context, submit(context))
    assert result.status == "failed" and result.counts.failed == 1 and result.counts.completed == 2
    failed = result.frames[1]
    assert failed.status == "failed" and "Explicit fake failure" in failed.error and not failed.detections and failed.runtime is None
    assert failed.image is not None
    assert client.get(result.download_url).status_code == 200


@pytest.mark.parametrize("mutation", [lambda p: p["evidence"].update(model_sha256="0" * 64),
    lambda p: p["evidence"].update(image_sha256="0" * 64), lambda p: p["evidence"].update(runtime_version="changed"),
    lambda p: p["evidence"].update(tensor_shape=[1, 3, 999, 999]), lambda p: p["evidence"]["transform"].update(scale_x=999),
    lambda p: p["evidence"].update(threshold_candidates=999), lambda p: p["detections"][0].update(label="wrong"),
    lambda p: p["detections"][0].update(bbox_xyxy=[0, 0, 999, 999]), lambda p: p["detections"].append(p["detections"][0])])
def test_bad_plugin_outputs_fail_closed(context, mutation):
    _, _, runtime, body = context
    runtime.corrupt = mutation
    result = execute(context, submit(context, body=body.model_copy(update={"ordinals": [1]})))
    assert result.status == "failed" and result.frames[0].status == "failed"
    assert result.frames[0].detections == [] and result.frames[0].runtime is None


def test_cancelled_before_dispatch_has_immutable_full_input_export(context):
    client, _, runtime, _ = context
    job = submit(context)
    response = client.post(f'/api/v1/perception/jobs/{job["id"]}/cancel')
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["status"] == "cancelled" and value["counts"]["not_attempted"] == 3 and runtime.calls == 0
    original = client.get(value["download_url"]).content
    assert client.post(f'/api/v1/perception/jobs/{job["id"]}/cancel').json() == value
    assert client.get(value["download_url"]).content == original


def test_acknowledged_cancellation_retains_committed_frames(context):
    _, app, runtime, _ = context
    job = submit(context)
    owner = claim(context, job)
    runtime.after_result = lambda n: jobs.cancel_job(app.state.db, job["id"], kind="perception") if n == 1 else None
    with pytest.raises(AdapterCancelled):
        perception.execute_claim(app.state.db, owner, runtime, app.state.sensor_registry, lambda: jobs.cancellation_requested(app.state.db, owner))
    assert runtime.closed
    result = jobs.fail_job(app.state.db, owner, "Native test context stopped before acknowledgement", status="cancelled")
    assert result.status == "cancelled" and result.counts.completed == 1 and result.counts.not_attempted == 2
    assert result.manifest_hash and result.frames[0].image


def test_lost_owner_cannot_publish_and_expiry_seals_partial_evidence(context, monkeypatch):
    _, app, runtime, _ = context
    job = submit(context)
    owner = claim(context, job)
    original = perception.publish_frame
    def publish_then_lose(db, claim, frame, png=None):
        original(db, claim, frame, png)
        with jobs._write(db) as session:
            jobs._row(session, job["id"]).lease_expires_at = "2000-01-01T00:00:00+00:00"
    monkeypatch.setattr(perception, "publish_frame", publish_then_lose)
    with pytest.raises(AdapterOwnershipLost):
        perception.execute_claim(app.state.db, owner, runtime, app.state.sensor_registry, lambda: jobs.cancellation_requested(app.state.db, owner))
    assert jobs.expire_jobs(app.state.db) == 1
    result = jobs.job_get(app.state.db, job["id"], kind="perception")
    assert result.status == "interrupted" and result.counts.completed == 1 and result.counts.not_attempted == 2
    assert result.outcome_uncertain and result.manifest_hash
    with pytest.raises(jobs.LostLease):
        jobs.fail_job(app.state.db, owner, "late failure")
    assert jobs.claim_job(app.state.db, "new-worker", capabilities={("perception", "test-inference", "0.1.0")}) is None


def test_original_message_hash_failure_is_a_failed_frame(context):
    _, app, _, body = context
    job = submit(context, body=body.model_copy(update={"ordinals": [1]}))
    with app.state.db.session() as session:
        recording = session.get(RecordingRow, body.recording_id)
        entry = session.get(RecordingMessageRow, (body.recording_id, 1))
        path, offset = app.state.db.recording_dir / recording.index_storage_key, entry.payload_offset
    with path.open("r+b") as stream:
        stream.seek(offset)
        stream.write(b"X")
    result = execute(context, job)
    assert result.status == "failed" and result.counts.failed == 1 and result.frames[0].image is None


def test_terminal_frame_and_image_tampering_fail_closed(context):
    client, app, _, _ = context
    result = execute(context, submit(context))
    with app.state.db.session() as session:
        entry = session.get(PerceptionFrameRow, (result.id, 0))
        image = app.state.db.perception_dir / entry.image_storage_key
    original = image.read_bytes()
    image.write_bytes(original[:-1] + b"X")
    assert client.get(result.frames[0].image.download_url).status_code == 409
    with app.state.db.session.begin() as session:
        entry = session.get(PerceptionFrameRow, (result.id, 0))
        changed = deepcopy(entry.payload)
        changed["detections"][0]["score"] = 0.9
        entry.payload = changed
    assert client.get(f"/api/v1/perception/jobs/{result.id}").status_code == 409
    assert client.get(result.download_url).status_code == 409
    assert client.get("/api/v1/perception/jobs").status_code == 409


def test_comparison_is_immutable_same_input_prediction_change_not_accuracy(context):
    client, _, _, body = context
    left = execute(context, submit(context))
    right = execute(context, submit(context, "right", body.model_copy(update={"score_threshold": 0.9, "ordinals": [1, 0]})))
    request = {"name": "Threshold comparison", "note": "Prediction changes only", "left_job_id": left.id, "right_job_id": right.id}
    response = client.post("/api/v1/perception/comparisons", json=request, headers={"Idempotency-Key": "compare"})
    assert response.status_code == 201, response.text
    value = response.json()
    assert value["counts"] == {"total": 3, "same_input": 2, "input_changed": 0, "left_only": 1, "right_only": 0, "not_evaluated": 0}
    assert value["rows"][0]["detection_count_delta"] == -1 and value["rows"][0]["predictions_identical"] is False
    assert {item["field"] for item in value["configuration_differences"]} == {"score_threshold"}
    content = client.get(value["download_url"]).content
    assert hashlib.sha256(content).hexdigest() == value["manifest_hash"]
    assert client.post("/api/v1/perception/comparisons", json=request, headers={"Idempotency-Key": "compare"}).json() == value
    execute(context, submit(context, "third"))
    assert client.get(value["download_url"]).content == content


def test_comparison_preserves_cancelled_union_inputs(context):
    client, _, _, body = context
    left = execute(context, submit(context))
    pending = submit(context, "pending", body.model_copy(update={"ordinals": [1, 2]}))
    request = {"name": "Incomplete pair", "note": "No invented empty predictions", "left_job_id": left.id, "right_job_id": pending["id"]}
    assert client.post("/api/v1/perception/comparisons", json=request, headers={"Idempotency-Key": "compare"}).status_code == 409
    client.post(f'/api/v1/perception/jobs/{pending["id"]}/cancel')
    response = client.post("/api/v1/perception/comparisons", json=request, headers={"Idempotency-Key": "compare"})
    assert response.status_code == 201, response.text
    assert response.json()["counts"]["not_evaluated"] == 2
    assert all(row["detection_count_delta"] is None for row in response.json()["rows"])


def test_running_frame_and_counts_are_durable_before_native_call(context):
    _, app, runtime, _ = context
    job = submit(context)
    observed = []
    def inspect_running(n):
        current = jobs.job_get(app.state.db, job["id"], kind="perception")
        observed.append(current.counts.model_dump())
        assert current.frames[n - 1].status == "running" and current.frames[n - 1].started_at
        assert current.counts.completed == n - 1 and current.counts.running == 1
    runtime.after_result = inspect_running
    result = execute(context, job)
    assert result.status == "completed" and result.counts.running == 0 and len(observed) == 3


def test_cancel_during_inference_records_attempted_failure_not_unattempted(context):
    _, app, runtime, _ = context
    job = submit(context)
    def cancel_running(_):
        jobs.cancel_job(app.state.db, job["id"], kind="perception")
        raise InferenceCancelled("Explicit test native stop acknowledgement")
    runtime.after_result = cancel_running
    result = execute(context, job)
    assert result.status == "cancelled" and runtime.closed
    assert result.counts.model_dump() == {"total": 3, "running": 0, "completed": 0, "failed": 1, "not_attempted": 2}
    assert result.frames[0].started_at and "no prediction was published" in result.frames[0].error


def _killable_worker(directory, job_id, connection):
    from ade2e.sensor_decoders import SensorDecoderRegistry
    db = Database(Settings(data_dir=Path(directory), seed_examples=False))
    db.initialize()
    runtime = FakeRuntime()
    def pause_second(n):
        if n == 2:
            connection.send("second-frame-running")
            connection.recv()
    runtime.after_result = pause_second
    registry = RuntimeRegistry(load_plugins=False)
    registry.register(runtime)
    run_once(db, Registry(load_plugins=False), "killable-perception-worker", job_id=job_id,
             runtime_registry=registry, sensor_registry=SensorDecoderRegistry())


def test_process_kill_preserves_completed_frame_and_marks_inflight_interrupted(context, monkeypatch):
    _, app, _, _ = context
    job = submit(context)
    mp = multiprocessing.get_context("spawn")
    parent, child = mp.Pipe()
    process = mp.Process(target=_killable_worker, args=(str(app.state.settings.data_dir), job["id"], child))
    process.start()
    try:
        assert parent.poll(15) and parent.recv() == "second-frame-running"
        live = jobs.job_get(app.state.db, job["id"], kind="perception")
        assert live.counts.model_dump() == {"total": 3, "running": 1, "completed": 1, "failed": 0, "not_attempted": 1}
        first_hash = live.frames[0].output_hash
        process.kill()
        process.join(timeout=5)
        assert not process.is_alive()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="microseconds")
        monkeypatch.setattr(jobs, "utcnow", lambda: future)
        jobs.expire_jobs(app.state.db)
        result = jobs.job_get(app.state.db, job["id"], kind="perception")
        assert result.status == "interrupted" and result.outcome_uncertain
        assert result.counts.model_dump() == {"total": 3, "running": 0, "completed": 1, "failed": 1, "not_attempted": 1}
        assert result.frames[0].output_hash == first_hash and result.manifest_hash
        assert "interrupted" in result.frames[1].error and result.frames[2].started_at is None
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        parent.close()
        child.close()


def test_publication_commit_failure_cleans_only_its_image_and_seals_failed_attempt(context):
    _, app, _, body = context
    preserved = execute(context, submit(context, body=body.model_copy(update={"ordinals": [1]})))
    before = {path.name: path.read_bytes() for path in app.state.db.perception_dir.iterdir()}
    job = submit(context, "commit-failure", body.model_copy(update={"ordinals": [2]}))
    failed = [False]
    def fail_publication_commit(connection):
        if failed[0]:
            return
        payload = connection.execute(select(PerceptionFrameRow.payload).where(PerceptionFrameRow.job_id == job["id"])).scalar()
        if payload and payload["status"] == "completed":
            failed[0] = True
            raise OSError("Injected image publication commit failure")
    event.listen(app.state.db.engine, "commit", fail_publication_commit)
    try:
        result = execute(context, job)
    finally:
        event.remove(app.state.db.engine, "commit", fail_publication_commit)
    assert failed[0] and result.status == "failed"
    assert result.counts.failed == 1 and result.frames[0].image is None and result.manifest_hash
    assert {path.name: path.read_bytes() for path in app.state.db.perception_dir.iterdir()} == before
    assert list(app.state.db.perception_staging_dir.iterdir()) == []
    assert jobs.job_get(app.state.db, preserved.id, kind="perception").status == "completed"


def test_short_staged_image_write_is_cleaned_without_publishing_empty_image(context, monkeypatch):
    _, app, _, body = context
    job = submit(context, body=body.model_copy(update={"ordinals": [1]}))
    original = os.fdopen
    class ShortWriter:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *_): self.stream.close()
        def write(self, content): return self.stream.write(content[:3])
    monkeypatch.setattr(os, "fdopen", lambda fd, mode="r", **kwargs: ShortWriter(original(fd, mode, **kwargs)) if mode == "wb" else original(fd, mode, **kwargs))
    result = execute(context, job)
    assert result.status == "failed" and result.frames[0].image is None and result.counts.failed == 1
    assert list(app.state.db.perception_dir.iterdir()) == [] and list(app.state.db.perception_staging_dir.iterdir()) == []


def test_terminal_timing_metadata_and_comparison_tampering_fail_closed(context):
    client, app, _, _ = context
    left, right = execute(context, submit(context)), execute(context, submit(context, "right"))
    body = PerceptionComparisonCreate(name="Frozen capture", note="Integrity fixture", left_job_id=left.id, right_job_id=right.id)
    comparison = perception.create_comparison(app.state.db, body, "capture")
    with app.state.db.session.begin() as session:
        entry = session.get(PerceptionFrameRow, (left.id, 0))
        payload = deepcopy(entry.payload)
        payload["runtime"]["inference_ms"] = 1000
        entry.payload = payload
    assert client.get(left.download_url).status_code == 409
    # Captured source manifests survive corruption of a later database view.
    assert client.get(comparison.download_url).status_code == 200
    with app.state.db.session.begin() as session:
        row = session.get(PerceptionComparisonRow, comparison.id)
        row.left_job_id = right.id
    assert client.get(comparison.download_url).status_code == 409


def test_pinned_model_revision_survives_authoring_head_change(context):
    client, _, _, body = context
    queued = submit(context)
    model = queued["model_snapshot"]
    definition = {key: model[key] for key in ("name", "description", "artifact_id", "profile", "input_width", "input_height", "labels", "source", "source_revision", "weights_license", "weights_license_url", "code_license", "training_data_note", "note")}
    definition.update(expected_revision=1, labels=["new-label"])
    revised = client.post(f'/api/v1/perception/models/{body.model_id}/revisions', json=definition)
    assert revised.status_code == 201, revised.text
    result = execute(context, queued)
    assert result.model_revision == 1 and result.frames[0].detections[0].label == "test-object"


def test_all_perception_inspection_and_mutation_routes_require_auth(tmp_path):
    settings = Settings(data_dir=tmp_path, seed_examples=False, api_token="private-inference-token")
    with TestClient(create_app(settings, Registry(load_plugins=False), runtime_registry=RuntimeRegistry(load_plugins=False))) as client:
        for path in ("/runtimes", "/jobs", "/jobs/x", "/jobs/x/export", "/jobs/x/frames/0/image", "/comparisons", "/comparisons/x", "/comparisons/x/export"):
            assert client.get("/api/v1/perception" + path).status_code == 401
        for path in ("/jobs", "/jobs/x/cancel", "/comparisons"):
            assert client.post("/api/v1/perception" + path, json={}).status_code == 401
        client.headers["Authorization"] = "Bearer private-inference-token"
        assert client.get("/api/v1/perception/runtimes").status_code == 200
