"""Shared-queue/evidence boundary tests; fake reports are not benchmark results."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest
from sqlalchemy import func, select

from ade2e import detection_evaluations as evaluations, jobs
from ade2e.detection_evaluation_models import DetectionEvaluationRow
from ade2e.detection_evaluation_schemas import DetectionEvaluationCreate
from ade2e.evaluator_runtime import EvaluationCancelled, EvaluationMetrics, EvaluationReport, EvaluatorMetadata
from ade2e.job_models import JobRow
from ade2e.service import canonical_bytes, digest
from ade2e.worker import run_once
from test_perception_jobs import context, execute as execute_perception, submit as submit_perception


class FakeEvaluator:
    """Only exact-box fixture matching; independently tested COCO is optional."""
    def __init__(self):
        self.calls = 0
        self.mutate = None
        self.callback = None
        self.version = "test-1"

    def metadata(self):
        return EvaluatorMetadata(id="contract-evaluator", name="Explicit evaluation contract fake", version=self.version,
            policy_version="coco-bbox-stored/1", status="available", implementation={"contract-fake": "1"})

    def evaluate(self, data, should_cancel):
        self.calls += 1
        if self.callback:
            self.callback()
        fields = ("gt", "crowd_gt", "predictions", "tp", "fp", "fn", "crowd_ignored")
        classes = {label: {"label": label, **dict.fromkeys(fields, 0), "ap": None, "pr_curve": None} for label in data.labels}
        coverage = {"total": len(data.rows), **{status: sum(item.status == status for item in data.rows) for status in evaluations.ROW_STATUSES}}
        coverage["complete"] = coverage["evaluated"] == coverage["total"]
        rows = []
        for item in data.rows:
            row = {"image_id": item.image_id, "status": item.status, "diagnostics": None}
            if item.status != "evaluated":
                rows.append(row)
                continue
            diag = {**dict.fromkeys(fields, 0), "score_filtered": 0, "matches": []}
            matched = set()
            for annotation in item.annotations:
                name = "crowd_gt" if annotation.iscrowd else "gt"
                diag[name] += 1
                classes[annotation.label][name] += 1
            for prediction in item.predictions:
                if prediction.score < data.score_threshold:
                    diag["score_filtered"] += 1
                    continue
                diag["predictions"] += 1
                classes[prediction.label]["predictions"] += 1
                annotation = next((a for a in item.annotations if a.label == prediction.label and a.bbox_xyxy == prediction.bbox_xyxy and (a.iscrowd or a.id not in matched)), None)
                outcome, field = ("crowd_ignored", "crowd_ignored") if annotation and annotation.iscrowd else ("true_positive", "tp") if annotation else ("false_positive", "fp")
                if annotation and not annotation.iscrowd:
                    matched.add(annotation.id)
                diag[field] += 1
                classes[prediction.label][field] += 1
                diag["matches"].append({"outcome": outcome, "prediction_id": prediction.id,
                    "annotation_id": annotation.id if annotation else None, "iou": 1 if annotation else None})
            for annotation in item.annotations:
                if not annotation.iscrowd and annotation.id not in matched:
                    diag["fn"] += 1
                    classes[annotation.label]["fn"] += 1
                    diag["matches"].append({"outcome": "false_negative", "prediction_id": None, "annotation_id": annotation.id, "iou": None})
            row["diagnostics"] = diag
            rows.append(row)
        metrics = None
        if coverage["complete"]:
            metrics = dict.fromkeys(EvaluationMetrics.model_fields)
            tp, fp, fn = (sum(item[name] for item in classes.values()) for name in ("tp", "fp", "fn"))
            metrics.update(precision=tp / (tp + fp) if tp + fp else None, recall=tp / (tp + fn) if tp + fn else None,
                f1=2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None)
            # Values here are canned only for contract fixtures, not COCO AP.
            metrics["ap"] = metrics["ap50"] = metrics["ap75"] = 1.0 if tp and not fn else 0.0 if fn else None
            for item in classes.values():
                item["ap"] = 1.0 if item["gt"] and not item["fn"] else 0.0 if item["gt"] else None
        result = {"policy_version": self.metadata().policy_version, "implementation": self.metadata().implementation,
            "parameters": {"iou_thresholds": [.5 + i * .05 for i in range(10)], "recall_thresholds": [i / 100 for i in range(101)],
                "max_dets": [1, 10, 100], "area_ranges": {"all": [0, 1e10], "small": [0, 1024], "medium": [1024, 9216], "large": [9216, 1e10]},
                "area_source": "bbox_width_times_height", "use_categories": True, "score_threshold": data.score_threshold,
                "diagnostic_iou": data.diagnostic_iou, "diagnostic_max_dets": 300, "max_dets_scope": "image_category", "crowd_iou": "intersection_over_detection_area"},
            "coverage": coverage, "metrics": metrics, "classes": list(classes.values()), "rows": rows, "warnings": ["Explicit contract fake; no native AP computation."], "elapsed_ms": 1}
        if self.mutate:
            self.mutate(result)
        return EvaluationReport.model_validate(result)


@pytest.fixture
def evaluation_context(context):
    client, app, _, perception_body = context
    source = execute_perception(context, submit_perception(context))
    assets = []
    for ordinal in perception_body.ordinals:
        response = client.post("/api/v1/dataset-images", json={"recording_id": perception_body.recording_id,
            "channel_id": perception_body.channel_id, "ordinal": ordinal, "decoder_id": "foxglove-json", "decoder_version": "1"})
        assert response.status_code == 201, response.text
        assets.append(response.json())
    members = [{"image_id": asset["id"], "split": "test", "review_status": "reviewed", "reviewer": "Authored fixture reviewer",
        "review_note": "Original fake detector contract", "annotation_source": "synthetic",
        "annotations": [{"id": "truth", "label": "target", "bbox_xyxy": [0, 0, 2, 1]}] if index != 2 else []} for index, asset in enumerate(assets)]
    response = client.post("/api/v1/datasets", json={"name": "Evaluation contract dataset", "labels": ["target", "absent-class"],
        "note": "Synthetic test declarations, not measured road accuracy", "images": members})
    assert response.status_code == 201, response.text
    dataset = response.json()
    evaluator = FakeEvaluator()
    app.state.evaluator_registry.register(evaluator)
    body = DetectionEvaluationCreate(name="Contract evaluation", note="Contract fixture only", dataset_id=dataset["id"], dataset_revision=1,
        split="test", inference_job_ids=[source.id], evaluator_id="contract-evaluator", class_mapping=[{"model_label": "test-object", "dataset_label": "target"}])
    return context, source, dataset, evaluator, body


def submit(context, key="evaluation", body=None):
    (client, *_), _, _, _, original = context
    response = client.post("/api/v1/detection-evaluations", json=(body or original).model_dump(mode="json"), headers={"Idempotency-Key": key})
    assert response.status_code == 202, response.text
    return response.json()


def execute(context, job):
    (_, app, *_), *_ = context
    return run_once(app.state.db, app.state.registry, "evaluation-worker", job_id=job["id"], runtime_registry=app.state.runtime_registry,
                    sensor_registry=app.state.sensor_registry, evaluator_registry=app.state.evaluator_registry)


def revise(context, update):
    (client, *_), _, dataset, _, _ = context
    body = {name: dataset[name] for name in ("name", "description", "labels", "source", "source_revision", "license", "note", "images")}
    body = deepcopy(body)
    update(body)
    body["expected_revision"] = 1
    response = client.post(f'/api/v1/datasets/{dataset["id"]}/revisions', json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_evaluation_pins_sources_and_publishes_same_queue_terminal_evidence(evaluation_context):
    (client, app, *_), source, dataset, evaluator, _ = evaluation_context
    job = submit(evaluation_context)
    assert evaluator.calls == 0 and job["report"] is None
    assert job["source_jobs"][0]["manifest_hash"] == source.manifest_hash
    assert job["dataset_snapshot"]["manifest_hash"] == dataset["manifest_hash"]
    result = execute(evaluation_context, job)
    assert result.status == "completed", result
    assert result.report.coverage.total == result.report.coverage.evaluated == 3 and result.report.coverage.complete
    assert result.report.classes[0].tp == 2 and result.report.classes[1].ap is None
    assert result.report.rows[2].diagnostics.gt == result.report.rows[2].diagnostics.predictions == 0
    content = client.get(result.download_url).content
    assert hashlib.sha256(content).hexdigest() == result.manifest_hash and len(content) == result.manifest_size_bytes
    assert client.get(f"/api/v1/jobs/{result.id}").status_code == 404
    assert client.get(f"/api/v1/perception/jobs/{result.id}").status_code == 404
    assert client.get("/api/v1/jobs").json()["items"] == []
    assert jobs.latest_job_evidence(app.state.db.session()) == {}


def test_unreviewed_images_withhold_metrics_and_preserve_all_rows(evaluation_context):
    (_, _, *_), _, _, _, body = evaluation_context
    revised = revise(evaluation_context, lambda value: value["images"][1].update(review_status="unreviewed"))
    result = execute(evaluation_context, submit(evaluation_context, body=body.model_copy(update={"dataset_revision": revised["revision"]})))
    assert result.status == "completed" and result.report.metrics is None
    assert result.report.coverage.total == 3 and result.report.coverage.evaluated == 2 and result.report.coverage.unreviewed == 1
    assert result.report.rows[1].diagnostics is None
    assert all(item.ap is None and item.pr_curve is None for item in result.report.classes)


def test_missing_and_failed_predictions_are_not_negative_results(evaluation_context):
    context, _, _, _, body = evaluation_context
    _, _, runtime, perception_body = context
    runtime.failure_at = runtime.calls + 1
    partial = execute_perception(context, submit_perception(context, "partial-source", perception_body.model_copy(update={"ordinals": [1, 0]})))
    result = execute(evaluation_context, submit(evaluation_context, body=body.model_copy(update={"inference_job_ids": [partial.id]})))
    assert result.report.metrics is None
    assert [row.status for row in result.report.rows] == ["failed_prediction", "missing_prediction", "evaluated"]
    assert result.report.coverage.total == 3


def test_null_mapping_excludes_predictions_but_keeps_ground_truth_denominator(evaluation_context):
    *_, body = evaluation_context
    changed = DetectionEvaluationCreate.model_validate({**body.model_dump(), "class_mapping": [{"model_label": "test-object", "dataset_label": None}]})
    result = execute(evaluation_context, submit(evaluation_context, body=changed))
    assert result.report.coverage.complete and result.report.classes[0].fn == 2
    assert result.source_context["excluded_class_predictions"] == 2 and result.report.metrics.recall == 0


@pytest.mark.parametrize("change", [{"class_mapping": []}, {"class_mapping": [{"model_label": "missing", "dataset_label": "target"}]},
    {"class_mapping": [{"model_label": "test-object", "dataset_label": "unknown"}]}, {"split": "train"},
    {"dataset_revision": 999}, {"diagnostic_iou": .51}, {"score_threshold": 2}, {"evaluator_id": "missing"}])
def test_invalid_requests_create_no_evaluation_job(evaluation_context, change):
    (client, app, *_), _, _, _, body = evaluation_context
    request = {**body.model_dump(mode="json"), **change}
    response = client.post("/api/v1/detection-evaluations", json=request, headers={"Idempotency-Key": "invalid"})
    assert response.status_code in {404, 422}, response.text
    with app.state.db.session() as session:
        assert session.scalar(select(func.count()).select_from(JobRow).where(JobRow.kind == "evaluation")) == 0


def test_idempotent_replay_ignores_later_dataset_head_or_unavailable_plugin(evaluation_context):
    (client, app, *_), _, _, _, body = evaluation_context
    first = submit(evaluation_context)
    revise(evaluation_context, lambda value: value["images"][0].update(annotations=[], review_note="Renewed negative review"))
    app.state.evaluator_registry._evaluators.clear()
    assert submit(evaluation_context)["id"] == first["id"]
    assert client.post("/api/v1/detection-evaluations", json={**body.model_dump(), "note": "changed"}, headers={"Idempotency-Key": "evaluation"}).status_code == 409


@pytest.mark.parametrize("mutation", [lambda r: r["coverage"].update(total=1), lambda r: r["rows"].reverse(),
    lambda r: r["classes"][0].update(tp=999), lambda r: r["rows"][0]["diagnostics"]["matches"][0].update(annotation_id="missing"),
    lambda r: r["rows"][0]["diagnostics"]["matches"][0].update(iou=.1), lambda r: r["parameters"].update(max_dets=[1, 10, 300]),
    lambda r: r.update(policy_version="changed"), lambda r: r["implementation"].update(extra="unreported"),
    lambda r: r["metrics"].update(precision=0.25)])
def test_bad_plugin_report_is_failed_not_published(evaluation_context, mutation):
    *_, evaluator, _ = evaluation_context
    evaluator.mutate = mutation
    result = execute(evaluation_context, submit(evaluation_context))
    assert result.status == "failed" and result.report is None and result.manifest_hash


def test_dataset_revision_pin_and_terminal_export_survive_revisions(evaluation_context):
    (client, *_), _, _, _, _ = evaluation_context
    job = submit(evaluation_context)
    revise(evaluation_context, lambda value: value["images"][0].update(annotations=[], review_note="Renewed negative review"))
    result = execute(evaluation_context, job)
    assert result.dataset_revision == 1 and result.report.classes[0].gt == 2
    content = client.get(result.download_url).content
    assert client.get(result.download_url).content == content


def test_cancel_before_dispatch_and_native_ack_preserve_terminal_input_snapshots(evaluation_context):
    (client, app, *_), _, _, evaluator, _ = evaluation_context
    first = submit(evaluation_context)
    cancelled = client.post(f'/api/v1/detection-evaluations/{first["id"]}/cancel').json()
    assert cancelled["status"] == "cancelled" and cancelled["report"] is None and evaluator.calls == 0
    second = submit(evaluation_context, "native-cancel")
    def stop():
        jobs.cancel_job(app.state.db, second["id"], kind="evaluation")
        raise EvaluationCancelled("Contract fake acknowledged stop")
    evaluator.callback = stop
    result = execute(evaluation_context, second)
    assert result.status == "cancelled" and result.report is None and result.manifest_hash


def test_lost_lease_discards_report_and_retains_interrupted_full_inputs(evaluation_context):
    (_, app, *_), _, _, evaluator, _ = evaluation_context
    job = submit(evaluation_context)
    def expire():
        with jobs._write(app.state.db) as session:
            jobs._row(session, job["id"]).lease_expires_at = "2000-01-01T00:00:00+00:00"
    evaluator.callback = expire
    result = execute(evaluation_context, job)
    assert result.status == "interrupted" and result.outcome_uncertain and result.report is None
    assert len(result.evaluation_input.rows) == 3 and result.manifest_hash


def test_persisted_report_tampering_is_rejected(evaluation_context):
    (client, app, *_), *_ = evaluation_context
    result = execute(evaluation_context, submit(evaluation_context))
    with app.state.db.session.begin() as session:
        row = session.get(DetectionEvaluationRow, result.id)
        payload = deepcopy(row.report)
        payload["metrics"]["ap"] = .2
        row.report = payload
    assert client.get(result.download_url).status_code == 409
    assert client.get("/api/v1/detection-evaluations").status_code == 409


def test_concurrent_duplicate_submission_is_one_job(evaluation_context):
    from concurrent.futures import ThreadPoolExecutor
    (_, app, *_), _, _, _, body = evaluation_context
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: evaluations.submit(app.state.db, app.state.evaluator_registry, body, "concurrent").id, range(6)))
    assert len(set(results)) == 1
    with app.state.db.session() as session:
        assert session.scalar(select(func.count()).select_from(DetectionEvaluationRow)) == 1


def test_changed_source_configuration_or_duplicate_selection_rejects_atomically(evaluation_context):
    context, source, _, _, body = evaluation_context
    client, app, _, perception_body = context
    second = execute_perception(context, submit_perception(context, "different-seed", perception_body.model_copy(update={"ordinals": [1], "seed": perception_body.seed + 1})))
    for source_ids in ([source.id, second.id], [source.id, source.id]):
        response = client.post("/api/v1/detection-evaluations", json={**body.model_dump(), "inference_job_ids": source_ids}, headers={"Idempotency-Key": "invalid-sources"})
        assert response.status_code == 422, response.text
    with app.state.db.session() as session:
        assert session.scalar(select(func.count()).select_from(DetectionEvaluationRow)) == 0


def test_selected_split_reports_outside_sources_and_score_filtering(evaluation_context):
    *_, body = evaluation_context
    revised = revise(evaluation_context, lambda value: value["images"][2].update(split="train"))
    request = body.model_copy(update={"dataset_revision": revised["revision"], "score_threshold": .8})
    result = execute(evaluation_context, submit(evaluation_context, body=request))
    assert result.report.coverage.total == 2 and result.report.coverage.complete
    assert len(result.source_context["outside_split_sources"]) == 1
    assert all(row.diagnostics.score_filtered == 1 for row in result.report.rows)
    assert result.report.classes[0].fn == 2 and result.report.metrics.recall == 0


def test_input_png_hash_mismatch_fails_before_evaluator_call(evaluation_context):
    (_, app, *_), _, dataset, evaluator, _ = evaluation_context
    job = submit(evaluation_context)
    path = app.state.db.dataset_image_dir / (dataset["image_assets"][0]["id"] + ".png")
    original = path.read_bytes()
    path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    result = execute(evaluation_context, job)
    assert evaluator.calls == 0 and result.status == "failed" and result.report is None and result.manifest_hash


def test_unavailable_version_is_not_claimed_and_policy_drift_fails_closed(evaluation_context):
    (_, app, *_), _, _, evaluator, _ = evaluation_context
    job = submit(evaluation_context)
    evaluator.version = "changed"
    assert execute(evaluation_context, job) is None and evaluator.calls == 0
    evaluator.version = "test-1"
    previous = evaluator.metadata
    evaluator.metadata = lambda: previous().model_copy(update={"implementation": {"contract-fake": "2"}})
    result = execute(evaluation_context, job)
    assert result.status == "failed" and evaluator.calls == 0 and result.report is None


def test_publication_failure_rolls_report_back_but_preserves_terminal_sources(evaluation_context):
    from sqlalchemy import event
    (_, app, *_), *_ = evaluation_context
    job = submit(evaluation_context)
    failed = [False]
    def fail_commit(connection):
        if not failed[0] and connection.execute(select(DetectionEvaluationRow.report).where(DetectionEvaluationRow.job_id == job["id"])).scalar():
            failed[0] = True
            raise OSError("Injected report publication failure")
    event.listen(app.state.db.engine, "commit", fail_commit)
    try:
        result = execute(evaluation_context, job)
    finally:
        event.remove(app.state.db.engine, "commit", fail_commit)
    assert failed[0] and result.status == "failed" and result.report is None and result.manifest_hash
    assert len(result.evaluation_input.rows) == 3 and len(result.source_jobs[0]["frames"]) == 3


def test_report_size_failure_can_still_seal_full_failure_input(evaluation_context, monkeypatch):
    job = submit(evaluation_context)
    monkeypatch.setattr(evaluations, "REPORT_LIMIT", 1)
    result = execute(evaluation_context, job)
    assert result.status == "failed" and result.report is None and result.manifest_hash


@pytest.mark.parametrize("mode", ["complete", "incomplete", "crowd", "negative"])
def test_real_optional_coco_child_report_passes_core_queue_validation(evaluation_context, mode):
    native = pytest.importorskip("axiom_coco.runtime").CocoBboxEvaluator()
    if native.metadata().status != "available":
        pytest.skip("Pinned optional native COCO runtime is not available")
    (_, app, *_), _, _, _, body = evaluation_context
    app.state.evaluator_registry.register(native, replace=True)
    update = {}
    if mode != "complete":
        def change(value):
            if mode == "incomplete":
                value["images"][0]["review_status"] = "unreviewed"
            else:
                for item in value["images"]:
                    item["review_note"] = "Renewed authored COCO fixture declaration"
                    if mode == "negative":
                        item["annotations"] = []
                    else:
                        for annotation in item["annotations"]:
                            annotation["iscrowd"] = True
        update["dataset_revision"] = revise(evaluation_context, change)["revision"]
    request = body.model_copy(update={**update, "evaluator_id": "coco-bbox"})
    result = execute(evaluation_context, submit(evaluation_context, body=request))
    assert result.status == "completed", result.error
    assert result.report.implementation == native.metadata().implementation
    assert result.report.coverage.total == 3
    if mode == "complete":
        assert result.report.metrics.ap == pytest.approx(1) and result.report.metrics.recall == 1
    elif mode == "incomplete":
        assert result.report.metrics is None
    elif mode == "crowd":
        assert result.report.classes[0].crowd_gt == 2 and result.report.classes[0].crowd_ignored == 2
        assert result.report.metrics.ap is None
    else:
        assert result.report.metrics.ap is None and result.report.classes[0].fp == 2


def test_evaluation_routes_require_configured_authentication(tmp_path):
    from fastapi.testclient import TestClient
    from ade2e.main import create_app
    from ade2e.database import Settings
    app = create_app(Settings(data_dir=tmp_path, seed_examples=False, api_token="evaluation-private"))
    with TestClient(app) as client:
        for method, path in [("GET", "/detection-evaluators"), ("GET", "/detection-evaluations"),
            ("POST", "/detection-evaluations"), ("GET", "/detection-evaluations/unknown"),
            ("POST", "/detection-evaluations/unknown/cancel"), ("GET", "/detection-evaluations/unknown/export")]:
            assert client.request(method, "/api/v1" + path).status_code == 401
        assert client.get("/api/v1/detection-evaluators", headers={"Authorization": "Bearer evaluation-private"}).status_code == 200


def test_same_original_input_with_changed_decoded_pixels_is_unavailable(evaluation_context, monkeypatch):
    import base64
    import io
    from PIL import Image
    context, _, _, _, body = evaluation_context
    _, app, _, perception_body = context
    decoder = app.state.sensor_registry.get("foxglove-json", "1")
    original_decode = decoder.decode
    stream = io.BytesIO()
    Image.new("RGB", (2, 1), (200, 200, 0)).save(stream, format="PNG")
    png = stream.getvalue()
    def changed_decode(*args, **kwargs):
        value = original_decode(*args, **kwargs)
        if value.camera:
            value.camera = value.camera.model_copy(update={"data_base64": base64.b64encode(png).decode(), "sha256": hashlib.sha256(png).hexdigest(), "size_bytes": len(png)})
        return value
    monkeypatch.setattr(decoder, "decode", changed_decode)
    changed = execute_perception(context, submit_perception(context, "changed-decoder-pixels", perception_body))
    assert changed.status == "completed"
    request = body.model_copy(update={"inference_job_ids": [changed.id]})
    result = execute(evaluation_context, submit(evaluation_context, body=request))
    assert result.status == "completed" and result.report.metrics is None
    assert result.report.coverage.input_changed == result.report.coverage.total == 3


def _blocking_evaluation_worker(directory, identifier, pipe):
    from ade2e.database import Database, Settings
    from ade2e.adapters import Registry
    from ade2e.evaluator_runtime import EvaluatorRegistry
    from ade2e.inference_runtime import RuntimeRegistry
    database = Database(Settings(data_dir=directory, seed_examples=False))
    database.initialize()
    evaluator = FakeEvaluator()
    def block():
        pipe.send("evaluator-running")
        pipe.recv()
    evaluator.callback = block
    registry = EvaluatorRegistry(load_plugins=False)
    registry.register(evaluator)
    run_once(database, Registry(load_plugins=False), "killable-evaluation-worker", job_id=identifier,
             runtime_registry=RuntimeRegistry(load_plugins=False), evaluator_registry=registry)


def test_process_kill_retains_full_input_and_seals_interruption(evaluation_context, monkeypatch):
    import multiprocessing
    (client, app, *_), *_ = evaluation_context
    job = submit(evaluation_context)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_blocking_evaluation_worker, args=(str(app.state.settings.data_dir), job["id"], child))
    process.start()
    try:
        assert parent.poll(15) and parent.recv() == "evaluator-running"
        live = jobs.job_get(app.state.db, job["id"], kind="evaluation")
        assert live.status == "running" and live.report is None
        process.kill()
        process.join(timeout=5)
        assert not process.is_alive()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="microseconds")
        monkeypatch.setattr(jobs, "utcnow", lambda: future)
        jobs.expire_jobs(app.state.db)
        result = jobs.job_get(app.state.db, job["id"], kind="evaluation")
        assert result.status == "interrupted" and result.outcome_uncertain and result.report is None
        assert result.input_hash == job["input_hash"] and len(result.evaluation_input.rows) == 3
        assert result.manifest_hash and client.get(result.download_url).status_code == 200
        assert execute(evaluation_context, job) is None
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        parent.close()
        child.close()


def test_late_cancellation_keeps_completed_report_and_frozen_export(evaluation_context):
    (client, app, *_), _, _, evaluator, _ = evaluation_context
    job = submit(evaluation_context)
    evaluator.callback = lambda: jobs.cancel_job(app.state.db, job["id"], kind="evaluation")
    result = execute(evaluation_context, job)
    assert result.status == "completed" and result.report.coverage.complete
    before = client.get(result.download_url).content
    response = client.post(f'/api/v1/detection-evaluations/{job["id"]}/cancel')
    assert response.status_code == 200 and response.json()["manifest_hash"] == result.manifest_hash
    assert client.get(result.download_url).content == before
    assert any(item.type == "cancel_not_honoured" for item in result.events)


def test_agent_discovery_describes_full_evaluation_lifecycle(evaluation_context):
    (client, *_), *_ = evaluation_context
    catalog = client.get('/api/v1/agent/tools').json()['tools']
    assert {'capture_dataset_image', 'create_detection_dataset', 'list_detection_evaluators', 'submit_detection_evaluation', 'read_detection_evaluation'} <= {item['name'] for item in catalog}
    guide = client.get('/llms.txt').text
    assert '/dataset-images' in guide and '/detection-evaluations/{id}/export' in guide and 'metrics are null' in guide


def test_polling_pins_one_snapshot_while_worker_finishes_between_selects(evaluation_context):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import event
    (client, app, *_), *_ = evaluation_context
    job = submit(evaluation_context)
    triggered = [False]
    def complete_between_reads(_connection, _cursor, statement, parameters, _context, _many):
        if not triggered[0] and 'FROM execution_jobs' in statement and job['id'] in parameters:
            triggered[0] = True
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(execute, evaluation_context, job).result(timeout=10)
            assert result.status == 'completed', result.error
    event.listen(app.state.db.engine, 'after_cursor_execute', complete_between_reads)
    try:
        response = client.get(f'/api/v1/detection-evaluations/{job["id"]}')
    finally:
        event.remove(app.state.db.engine, 'after_cursor_execute', complete_between_reads)
    assert triggered[0] and response.status_code == 200, response.text
    assert response.json()['status'] == 'queued' and response.json()['report'] is None
    following = client.get(f'/api/v1/detection-evaluations/{job["id"]}')
    assert following.status_code == 200 and following.json()['status'] == 'completed'
    assert following.json()['manifest_hash']


@pytest.mark.parametrize('mode', ['wrong_average', 'no_ground_truth', 'undefined_positive_category'])
def test_global_ap_respects_category_eligibility_and_mean(evaluation_context, mode):
    *_, evaluator, body = evaluation_context
    if mode == 'no_ground_truth':
        def negatives(value):
            for item in value['images']:
                item.update(annotations=[], review_note='Renewed explicit negative declarations')
        revised = revise(evaluation_context, negatives)
        body = body.model_copy(update={'dataset_revision': revised['revision']})
    def corrupt(value):
        if mode == 'undefined_positive_category':
            value['classes'][0]['ap'] = None
        else:
            value['metrics']['ap'] = .25
    evaluator.mutate = corrupt
    result = execute(evaluation_context, submit(evaluation_context, body=body))
    assert result.status == 'failed' and result.report is None and result.manifest_hash
