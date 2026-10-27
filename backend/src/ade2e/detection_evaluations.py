"""Immutable dataset/source snapshots and evaluation on the shared job queue."""

from dataclasses import dataclass
import hashlib
import json
import math

from fastapi import Header, HTTPException, Query, Response
from sqlalchemy import func, select

from . import jobs
from .adapters import AdapterCancelledBeforeDispatch
from .dataset_schemas import DatasetDetail, DatasetSnapshot
from .detection_evaluation_models import DetectionEvaluationRow, DetectionEvaluationSourceRow
from .detection_evaluation_schemas import DetectionEvaluationCreate, DetectionEvaluationDetail, DetectionEvaluationPage, DetectionEvaluationSummary
from .job_models import JobEventRow, JobRow
from .job_schemas import JobStatus
from .evaluator_runtime import EvaluationInput, EvaluationReport, EvaluatorMetadata
from .perception_jobs import _terminal_copy, detail as perception_detail
from .service import canonical_bytes, digest, uid, utcnow

MANIFEST_LIMIT = 32 * 1024 * 1024
PREPARED_LIMIT = 16 * 1024 * 1024
REPORT_LIMIT = 12 * 1024 * 1024
MANIFEST_FIELDS = {"manifest_hash", "manifest_size_bytes", "download_url"}
ROW_STATUSES = {"evaluated", "unreviewed", "missing_prediction", "failed_prediction", "input_changed"}


def source_key(value):
    return (value.recording_sha256, value.channel_id, value.ordinal, value.message_sha256)


def source_context(source):
    """Compare measurement/inference settings, independent of UI names or paths."""
    request = source.request
    return {"model_manifest_hash": source.model_snapshot["manifest_hash"],
        "runtime_id": source.runtime_id, "runtime_version": source.runtime_version,
        "seed": request.seed, "score_threshold": request.score_threshold,
        "iou_threshold": request.iou_threshold, "max_detections": request.max_detections}


def observed_contexts(source):
    fields = ("runtime_id", "runtime_version", "engine", "engine_version", "provider", "library_versions",
              "model_sha256", "profile", "preprocessing_version", "postprocessing_version")
    return {canonical_bytes({key: frame.runtime.get(key) for key in fields})
            for frame in source.frames if frame.status == "completed" and frame.runtime}


def match_inputs(dataset, sources, body):
    """Match observations by exact source identity, never nearest time or name."""
    members = [member for member in dataset.images if member.split == body.split]
    labels = dataset.labels
    if body.selection is not None:
        selected = set(body.selection.image_ids)
        if not selected <= {member.image_id for member in members}:
            raise HTTPException(422, 'Cohort images must belong to the pinned dataset split')
        if not set(body.selection.labels) <= set(dataset.labels):
            raise HTTPException(422, 'Cohort classes must belong to the pinned dataset')
        members = [member for member in members if member.image_id in selected]
        labels = [label for label in dataset.labels if label in body.selection.labels]
    if not members:
        raise HTTPException(422, "The selected dataset split contains no images")
    assets = {asset.id: asset for asset in dataset.image_assets}
    source_frames = {}
    for source in sources:
        for frame in source.frames:
            key = source_key(frame.input)
            if key in source_frames:
                raise HTTPException(422, "Source jobs repeat an original input selection; choose one prediction result per input")
            source_frames[key] = (source.id, frame)
    class_mapping = {item.model_label: item.dataset_label for item in body.class_mapping}
    rows, lineage, selected_keys = [], [], set()
    excluded_classes = 0
    for member in members:
        asset = assets[member.image_id]
        identity = source_key(asset.input)
        selected_keys.add(identity)
        match = source_frames.get(identity)
        status, reason = "evaluated", None
        if member.review_status != "reviewed":
            status, reason = "unreviewed", "Annotations are draft or unreviewed; an empty list is not a reviewed negative image."
        elif match is None:
            status, reason = "missing_prediction", "No selected source job contains this original image identity."
        elif match[1].status != "completed":
            status, reason = "failed_prediction", f"Source frame status is {match[1].status}; no successful prediction is available."
        else:
            frame = match[1]
            if (frame.image is None or frame.image.sha256 != asset.image.sha256
                    or (frame.image.width, frame.image.height) != (asset.image.width, asset.image.height)
                    or (frame.input.decoder_id, frame.input.decoder_version) != (asset.input.decoder_id, asset.input.decoder_version)):
                status, reason = "input_changed", "Source PNG pixels/dimensions or decoder identity differ from the annotated image."
        predictions, excluded = [], 0
        if status == "evaluated":
            for detection in match[1].detections:
                label = class_mapping[detection.label]
                if label is None or label not in labels:
                    excluded += 1
                    continue
                predictions.append({"id": detection.id, "label": label, "score": detection.score, "bbox_xyxy": list(detection.bbox_xyxy)})
        excluded_classes += excluded
        rows.append({"image_id": member.image_id, "status": status, "width": asset.image.width, "height": asset.image.height,
            "annotations": [annotation.model_dump(mode="json") for annotation in member.annotations if annotation.label in labels], "predictions": predictions})
        lineage.append({"image_id": member.image_id, "status": status, "reason": reason,
            "inference_job_id": match[0] if match else None, "selection_index": match[1].selection_index if match else None,
            "image_sha256": asset.image.sha256, "excluded_class_predictions": excluded,
            "input": asset.input.model_dump(mode="json")})
    outside = [{"inference_job_id": source_id, "selection_index": frame.selection_index,
                "input": frame.input.model_dump(mode="json"), "status": frame.status}
               for identity, (source_id, frame) in source_frames.items() if identity not in selected_keys]
    return {"labels": labels, "score_threshold": body.score_threshold, "diagnostic_iou": body.diagnostic_iou, "rows": rows}, {
        "rows": lineage, "outside_split_sources": outside, "excluded_class_predictions": excluded_classes}


@dataclass(frozen=True)
class PreparedEvaluation:
    payload_json: bytes

    def to_payload(self):
        return json.loads(self.payload_json)

    @property
    def input_hash(self):
        return hashlib.sha256(self.payload_json).hexdigest()

    @classmethod
    def from_payload(cls, value):
        if set(value) != {"request", "dataset_snapshot", "source_jobs", "evaluator_snapshot", "evaluation_input", "source_context"}:
            raise ValueError("Unknown prepared evaluation contract")
        body = DetectionEvaluationCreate.model_validate(value["request"])
        dataset = DatasetDetail.model_validate(value["dataset_snapshot"])
        original = canonical_bytes(DatasetSnapshot.model_validate(dataset.model_dump(exclude=MANIFEST_FIELDS)).model_dump(mode="json"))
        if (dataset.id != body.dataset_id or dataset.revision != body.dataset_revision
                or len(original) != dataset.manifest_size_bytes or hashlib.sha256(original).hexdigest() != dataset.manifest_hash
                or digest(dataset.labels) != dataset.label_map_hash):
            raise ValueError("Dataset snapshot differs from its canonical definition")
        sources = [_terminal_copy(item) for item in value["source_jobs"]]
        if [item.id for item in sources] != body.inference_job_ids:
            raise ValueError("Source manifest list differs from the requested jobs")
        if set(item.model_label for item in body.class_mapping) != set(sources[0].model_snapshot["labels"]):
            raise ValueError("Every model label must appear exactly once in the class mapping")
        if any(item.dataset_label is not None and item.dataset_label not in dataset.labels for item in body.class_mapping):
            raise ValueError("Mapped classes must belong to the pinned dataset taxonomy")
        context = source_context(sources[0])
        if any(source_context(item) != context for item in sources):
            raise ValueError("Source jobs use different model or inference configurations")
        actual_contexts = set().union(*(observed_contexts(item) for item in sources))
        if len(actual_contexts) > 1:
            raise ValueError("Source predictions use different observed runtime contexts")
        evaluation_input, lineage = match_inputs(dataset, sources, body)
        expected_context = {"inference_configuration": context,
            "observed_runtime_context": json.loads(next(iter(actual_contexts))) if actual_contexts else None, **lineage}
        # Optional extension for new evidence only. Historical manifests keep their exact bytes.
        if 'annotation_lineage' in value['source_context']:
            from .annotation_schemas import AnnotationLineageSnapshot
            origin = AnnotationLineageSnapshot.model_validate(value['source_context']['annotation_lineage'])
            if (origin.dataset_id != dataset.id or origin.dataset_revision != dataset.revision
                    or origin.dataset_manifest_hash != dataset.manifest_hash
                    or not set(origin.images) <= {m.image_id for m in dataset.images}):
                raise ValueError('Annotation provenance differs from the evaluated dataset')
            expected_context['annotation_lineage'] = origin.model_dump(mode='json')
        if value["evaluation_input"] != evaluation_input or value["source_context"] != expected_context:
            raise ValueError("Derived evaluation rows differ from the pinned source identities")
        if value["evaluator_snapshot"].get("id") != body.evaluator_id:
            raise ValueError("Evaluator identity differs from its request")
        EvaluatorMetadata.model_validate(value["evaluator_snapshot"])
        EvaluationInput.model_validate(evaluation_input)
        content = canonical_bytes(value)
        if len(content) > PREPARED_LIMIT:
            raise ValueError("Prepared evaluation exceeds 16 MiB")
        return cls(content)


def _subject(session, row):
    subject = session.get(DetectionEvaluationRow, row.id)
    if row.kind != "evaluation" or subject is None:
        raise HTTPException(404, "Detection evaluation not found")
    return subject


def _prepared(row):
    try:
        prepared = PreparedEvaluation.from_payload(row.prepared)
        value = prepared.to_payload()
        if (prepared.input_hash != row.input_hash or value["request"] != row.request
                or digest(row.request) != row.request_hash or row.adapter_id != value["evaluator_snapshot"]["id"]
                or row.adapter_version != value["evaluator_snapshot"]["version"]):
            raise ValueError("Queue request differs from its pinned evaluation input")
        return value
    except (ValueError, KeyError, TypeError, IndexError) as error:
        raise HTTPException(409, "Evaluation input snapshot failed its integrity check") from error


def _unsealed_detail(session, row):
    subject, prepared = _subject(session, row), _prepared(row)
    if (subject.dataset_id != prepared["request"]["dataset_id"] or subject.dataset_revision != prepared["request"]["dataset_revision"]):
        raise HTTPException(409, "Evaluation dataset relationship differs from its pinned definition")
    links = {item.inference_job_id: item.manifest_hash for item in session.scalars(select(DetectionEvaluationSourceRow).where(DetectionEvaluationSourceRow.evaluation_id == row.id))}
    if links != {item["id"]: item["manifest_hash"] for item in prepared["source_jobs"]}:
        raise HTTPException(409, "Evaluation source relationships differ from their pinned manifests")
    if (subject.report is None) != (subject.report_hash is None) or (subject.report is not None and digest(subject.report) != subject.report_hash):
        raise HTTPException(409, "Evaluation report failed its content integrity check")
    events = list(session.scalars(select(JobEventRow).where(JobEventRow.job_id == row.id).order_by(JobEventRow.sequence.desc()).limit(jobs.EVENT_LIMIT)))
    event_count = session.scalar(select(func.count()).select_from(JobEventRow).where(JobEventRow.job_id == row.id))
    extra = {"name", "dataset_id", "dataset_revision", "dataset_name", "evaluator_id", "evaluator_version"}
    fields = {name: getattr(row, name) for name in DetectionEvaluationSummary.model_fields if name not in extra}
    return DetectionEvaluationDetail(**fields, name=prepared["request"]["name"], dataset_id=subject.dataset_id,
        dataset_revision=subject.dataset_revision, dataset_name=prepared["dataset_snapshot"]["name"], evaluator_id=row.adapter_id, evaluator_version=row.adapter_version,
        request=prepared["request"], dataset_snapshot=prepared["dataset_snapshot"], source_jobs=prepared["source_jobs"],
        evaluator_snapshot=prepared["evaluator_snapshot"], report=subject.report,
        evaluation_input=prepared["evaluation_input"], source_context=prepared["source_context"],
        events=[{"at": item.at, "type": item.type, "message": item.message} for item in reversed(events)], events_total=event_count)


def seal_terminal(session, row):
    session.flush()
    subject = _subject(session, row)
    if row.status not in jobs.TERMINAL or subject.manifest is not None:
        raise HTTPException(409, "Only an unsealed terminal evaluation can be sealed")
    content = canonical_bytes(_unsealed_detail(session, row).model_dump(mode="json", exclude=MANIFEST_FIELDS))
    if len(content) > MANIFEST_LIMIT:
        raise HTTPException(413, "Evaluation evidence manifest exceeds 32 MiB")
    subject.manifest, subject.manifest_hash, subject.manifest_size_bytes = content, hashlib.sha256(content).hexdigest(), len(content)


def detail(session, row):
    session.flush()
    value = _unsealed_detail(session, row)
    subject = _subject(session, row)
    if row.status in jobs.TERMINAL:
        content = canonical_bytes(value.model_dump(mode="json", exclude=MANIFEST_FIELDS))
        if (subject.manifest != content or subject.manifest_hash != hashlib.sha256(content).hexdigest()
                or subject.manifest_size_bytes != len(content)):
            raise HTTPException(409, "Terminal evaluation manifest failed its integrity check")
        return value.model_copy(update={"manifest_hash": subject.manifest_hash, "manifest_size_bytes": subject.manifest_size_bytes,
                                        "download_url": f"/api/v1/detection-evaluations/{row.id}/export"})
    if subject.manifest is not None or subject.manifest_hash is not None or subject.manifest_size_bytes is not None:
        raise HTTPException(409, "A nonterminal evaluation has sealed evidence")
    return value


def submit(db, evaluators, body: DetectionEvaluationCreate, key: str):
    with jobs._write(db) as session:
        return submit_in_session(db, session, evaluators, body, key)


def submit_in_session(db, session, evaluators, body: DetectionEvaluationCreate, key: str):
    """Compose queue publication within the caller transaction; no independent commit."""
    from .datasets import get_dataset

    if not key or len(key) > 200:
        raise HTTPException(422, "A stable Idempotency-Key of at most 200 characters is required")
    request_hash, storage_key = digest(body.model_dump(mode="json")), "evaluation:" + key

    existing = session.scalar(select(JobRow).where(JobRow.idempotency_key == storage_key))
    if existing:
        if existing.kind != "evaluation" or existing.request_hash != request_hash:
            raise HTTPException(409, "Idempotency-Key was already used for a different evaluation")
        return detail(session, existing)
    evaluator = evaluators.get(body.evaluator_id)
    if evaluator is None:
        raise HTTPException(422, "Selected detection evaluator is not installed")
    metadata = EvaluatorMetadata.model_validate(evaluator.metadata())
    if metadata.status != "available":
        raise HTTPException(422, "Selected detection evaluator is unavailable")
    dataset = get_dataset(session, body.dataset_id, body.dataset_revision)
    sources = []
    for identifier in body.inference_job_ids:
        source = perception_detail(session, jobs._row(session, identifier))
        if source.status not in jobs.TERMINAL:
            raise HTTPException(422, "Every source inference job must be terminal before evaluation")
        sources.append(source)
    from .annotation_assist import check_evaluation_independence
    annotation_lineage = check_evaluation_independence(session, dataset, sources, body.split)
    try:
        if set(item.model_label for item in body.class_mapping) != set(sources[0].model_snapshot["labels"]):
            raise ValueError("Every model label must appear exactly once in class_mapping")
        if any(item.dataset_label is not None and item.dataset_label not in dataset.labels for item in body.class_mapping):
            raise ValueError("Class mappings must name pinned dataset labels or explicit null")
        evaluation_input, lineage = match_inputs(dataset, sources, body)
        contexts = set().union(*(observed_contexts(source) for source in sources))
        context = {"inference_configuration": source_context(sources[0]),
                   "observed_runtime_context": json.loads(next(iter(contexts))) if contexts else None, **lineage}
        if annotation_lineage is not None:
            context['annotation_lineage'] = annotation_lineage
        prepared = PreparedEvaluation.from_payload({"request": body.model_dump(mode="json"),
            "dataset_snapshot": dataset.model_dump(mode="json"), "source_jobs": [item.model_dump(mode="json") for item in sources],
            "evaluator_snapshot": metadata.model_dump(mode="json"), "evaluation_input": evaluation_input, "source_context": context})
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(422, str(error)[:1500]) from error
    now = utcnow()
    row = JobRow(id=uid("eval"), kind="evaluation", idempotency_key=storage_key, request_hash=request_hash,
        request=body.model_dump(mode="json"), prepared=prepared.to_payload(), input_hash=prepared.input_hash,
        adapter_id=metadata.id, adapter_version=metadata.version, status="queued", created_at=now, updated_at=now,
        cancellation_supported=True, resource_required=False, outcome_uncertain=False, evidence_eligible=False)
    session.add(row)
    session.flush()
    session.add(DetectionEvaluationRow(job_id=row.id, dataset_id=dataset.id, dataset_revision=dataset.revision))
    session.flush()
    for source in sources:
        session.add(DetectionEvaluationSourceRow(evaluation_id=row.id, inference_job_id=source.id, manifest_hash=source.manifest_hash))
    jobs._event(session, row, "queued", "Dataset annotations/images, terminal prediction manifests, label mapping and evaluator version pinned. No metrics have been computed.", now)
    return detail(session, row)


def _overlap(prediction, annotation):
    x1, y1, x2, y2 = prediction.bbox_xyxy
    a1, b1, a2, b2 = annotation.bbox_xyxy
    intersection = max(0, min(x2, a2) - max(x1, a1)) * max(0, min(y2, b2) - max(y1, b1))
    area = (x2 - x1) * (y2 - y1)
    denominator = area if annotation.iscrowd else area + (a2 - a1) * (b2 - b1) - intersection
    return intersection / denominator if denominator else 0.0


def validate_report(report, prepared):
    """Check identity, complete denominators and ID/count/IoU consistency.

    This validates the plugin result contract; it does not independently
    reimplement the official AP interpolation or optimal COCO match ordering.
    """
    report = EvaluationReport.model_validate(report)
    data = EvaluationInput.model_validate(prepared["evaluation_input"])
    metadata = prepared["evaluator_snapshot"]
    if report.policy_version != metadata["policy_version"] or report.implementation != metadata["implementation"]:
        raise ValueError("Evaluator policy or native implementation differs from the pinned capability")
    coverage = {"total": len(data.rows), **{status: sum(row.status == status for row in data.rows) for status in ROW_STATUSES}}
    coverage["complete"] = coverage["evaluated"] == coverage["total"]
    if report.coverage.model_dump() != coverage or report.diagnostic_scope != "evaluated_rows":
        raise ValueError("Evaluator coverage differs from the full selected input denominator")
    if (report.metrics is not None) != coverage["complete"]:
        raise ValueError("Aggregate metrics must be withheld exactly when selected input coverage is incomplete")
    expected = {"max_dets": [1, 10, 100], "area_ranges": {"all": [0, 1e10], "small": [0, 1024],
        "medium": [1024, 9216], "large": [9216, 1e10]}, "area_source": "bbox_width_times_height", "use_categories": True,
        "score_threshold": data.score_threshold, "diagnostic_iou": data.diagnostic_iou, "diagnostic_max_dets": 300,
        "max_dets_scope": "image_category", "crowd_iou": "intersection_over_detection_area"}
    params = report.parameters
    if set(params) != set(expected) | {"iou_thresholds", "recall_thresholds"} or any(params.get(key) != value for key, value in expected.items()):
        raise ValueError("Evaluator metric parameters differ from the supported stored-prediction policy")
    for name, values in (("iou_thresholds", [0.5 + index * .05 for index in range(10)]), ("recall_thresholds", [index / 100 for index in range(101)])):
        actual = params[name]
        if len(actual) != len(values) or any(not math.isclose(a, b, rel_tol=0, abs_tol=1e-12) for a, b in zip(actual, values)):
            raise ValueError("Evaluator interpolation or IoU thresholds differ from the pinned policy")
    if len(report.rows) != len(data.rows) or [item.label for item in report.classes] != data.labels:
        raise ValueError("Evaluator rows/classes must retain their complete input order")
    fields = ("gt", "crowd_gt", "predictions", "tp", "fp", "fn", "crowd_ignored")
    totals = {label: {field: 0 for field in fields} for label in data.labels}
    for result, source in zip(report.rows, data.rows):
        if (result.image_id, result.status) != (source.image_id, source.status):
            raise ValueError("Evaluator row identity/status differs from the selected dataset input")
        if source.status != "evaluated":
            if result.diagnostics is not None:
                raise ValueError("Unavailable or unreviewed evidence cannot acquire match diagnostics")
            continue
        diag = result.diagnostics
        if diag is None:
            raise ValueError("An evaluated row must include explicit diagnostics")
        annotations = {item.id: item for item in source.annotations}
        predictions = {item.id: item for item in source.predictions if item.score >= data.score_threshold}
        count = {field: 0 for field in fields}
        for annotation in annotations.values():
            field = "crowd_gt" if annotation.iscrowd else "gt"
            count[field] += 1
            totals[annotation.label][field] += 1
        for prediction in predictions.values():
            count["predictions"] += 1
            totals[prediction.label]["predictions"] += 1
        seen_predictions, seen_gt = set(), set()
        for match in diag.matches:
            prediction = predictions.get(match.prediction_id)
            annotation = annotations.get(match.annotation_id)
            if match.outcome == "false_negative":
                if prediction is not None or match.prediction_id is not None or match.iou is not None or annotation is None or annotation.iscrowd or annotation.id in seen_gt:
                    raise ValueError("False-negative match is not an unmatched ordinary annotation")
                seen_gt.add(annotation.id)
                label, field = annotation.label, "fn"
            else:
                if prediction is None or prediction.id in seen_predictions:
                    raise ValueError("Every retained prediction must have exactly one diagnostic outcome")
                seen_predictions.add(prediction.id)
                label = prediction.label
                if match.outcome == "false_positive":
                    if annotation is not None or match.annotation_id is not None or match.iou is not None:
                        raise ValueError("False positives cannot claim an annotation match")
                    field = "fp"
                else:
                    if (annotation is None or annotation.label != prediction.label or match.iou is None
                            or not math.isclose(match.iou, _overlap(prediction, annotation), rel_tol=0, abs_tol=1e-9)
                            or match.iou + 1e-12 < data.diagnostic_iou):
                        raise ValueError("Matched prediction/annotation label or overlap is inconsistent")
                    if match.outcome == "crowd_ignored":
                        if not annotation.iscrowd:
                            raise ValueError("Crowd-ignored prediction requires a crowd annotation")
                        field = "crowd_ignored"
                    else:
                        if annotation.iscrowd or annotation.id in seen_gt:
                            raise ValueError("True positives must uniquely match ordinary annotations")
                        seen_gt.add(annotation.id)
                        field = "tp"
            count[field] += 1
            totals[label][field] += 1
        if seen_predictions != set(predictions) or seen_gt != {item.id for item in annotations.values() if not item.iscrowd}:
            raise ValueError("Diagnostic matches silently omit retained predictions or ordinary annotations")
        if any(getattr(diag, field) != value for field, value in count.items()) or diag.score_filtered != len(source.predictions) - len(predictions):
            raise ValueError("Per-image diagnostic counts differ from their input and match identities")
    for category in report.classes:
        if any(getattr(category, field) != value for field, value in totals[category.label].items()):
            raise ValueError("Per-class diagnostics differ from the evaluated image subset")
        if not coverage["complete"] and (category.ap is not None or category.pr_curve is not None):
            raise ValueError("Incomplete datasets cannot publish per-class aggregate AP/PR curves")
        if category.gt == 0 and (category.ap is not None or category.pr_curve is not None):
            raise ValueError("Categories without ordinary ground truth require null AP/PR")
        if category.pr_curve is not None:
            curve = category.pr_curve
            if len(curve.recall) != 101 or len(curve.precision) != 101 or any(not math.isclose(value, index / 100, rel_tol=0, abs_tol=1e-12) for index, value in enumerate(curve.recall)):
                raise ValueError("Per-class PR interpolation has an invalid recall grid")
    if report.metrics is not None:
        if any(category.gt and category.ap is None for category in report.classes):
            raise ValueError("A complete category with ordinary ground truth requires a defined AP")
        category_ap = [category.ap for category in report.classes if category.ap is not None]
        if ((not category_ap and report.metrics.ap is not None)
                or (category_ap and (report.metrics.ap is None
                    or not math.isclose(report.metrics.ap, sum(category_ap) / len(category_ap), rel_tol=0, abs_tol=1e-12)))):
            raise ValueError("Overall AP must average the defined category AP values, or remain null with no ordinary ground truth")
        tp, fp, fn = (sum(row[field] for row in totals.values()) for field in ("tp", "fp", "fn"))
        ratios = {"precision": (tp, tp + fp), "recall": (tp, tp + fn), "f1": (2 * tp, 2 * tp + fp + fn)}
        for name, (numerator, denominator) in ratios.items():
            value = getattr(report.metrics, name)
            if (denominator == 0 and value is not None) or (denominator and (value is None or not math.isclose(value, numerator / denominator, rel_tol=0, abs_tol=1e-12))):
                raise ValueError("Aggregate diagnostic ratio differs from its TP/FP/FN denominator")
    return report


def execute_claim(db, claim, evaluator, should_cancel):
    from .datasets import dataset_image_bytes, get_dataset_image

    if should_cancel():
        raise AdapterCancelledBeforeDispatch()
    prepared = claim.prepared.to_payload()
    metadata = EvaluatorMetadata.model_validate(evaluator.metadata())
    pinned = prepared["evaluator_snapshot"]
    if (metadata.id != pinned["id"] or metadata.version != pinned["version"] or metadata.policy_version != pinned["policy_version"]
            or metadata.implementation != pinned["implementation"] or metadata.status != "available"):
        raise HTTPException(422, "Pinned evaluator policy or native implementation is unavailable; evaluation was not invoked")
    selected = {row["image_id"] for row in prepared["evaluation_input"]["rows"]}
    # Verify each immutable PNG sequentially, outside a DB transaction spanning
    # file reads or the native evaluation. No image arrays are retained here.
    for asset in prepared["dataset_snapshot"]["image_assets"]:
        if asset["id"] not in selected:
            continue
        if should_cancel():
            raise AdapterCancelledBeforeDispatch()
        with db.read_session() as session:
            current = get_dataset_image(session, asset["id"])
        if current.model_dump(mode="json") != asset:
            raise ValueError("Dataset image metadata differs from the pinned evaluation snapshot")
        dataset_image_bytes(db, current)
    result = evaluator.evaluate(EvaluationInput.model_validate(prepared["evaluation_input"]), should_cancel)
    report = validate_report(result, prepared)
    # Content validation and serialization precede the short fenced publication.
    payload = report.model_dump(mode="json")
    if len(canonical_bytes(payload)) > REPORT_LIMIT:
        raise ValueError("Evaluation report exceeds 12 MiB")
    with jobs._write(db) as session:
        row = jobs._live(session, claim)
        if row.kind != "evaluation" or row.dispatched_at is None or row.input_hash != claim.prepared.input_hash:
            raise ValueError("Evaluation publication requires the exact dispatched input claim")
        _prepared(row)
        subject = _subject(session, row)
        if subject.report is not None:
            raise HTTPException(409, "This evaluation already has a published report")
        subject.report, subject.report_hash = payload, digest(payload)
        session.flush()
        jobs._live(session, claim)
        now = utcnow()
        if row.cancel_requested_at:
            jobs._event(session, row, "cancel_not_honoured", "A complete evaluator report arrived before cancellation acknowledgement; source and report evidence are retained.", now)
        jobs._terminal(session, row, "completed", now, "Evaluation report published with the full selected-image denominator. Completed does not imply complete metric coverage or real-world validity.")
        return detail(session, row)


def register_evaluation_routes(api, db, evaluators):
    from .evaluation_reviews import register_evaluation_review_routes
    register_evaluation_review_routes(api, db)

    @api.get("/detection-evaluators", response_model=list[EvaluatorMetadata], tags=["detection evaluation"])
    def list_evaluators():
        return evaluators.list()

    @api.post("/detection-evaluations", response_model=DetectionEvaluationDetail, status_code=202, tags=["detection evaluation"])
    def create_evaluation(body: DetectionEvaluationCreate, idempotency_key: str = Header(min_length=1, max_length=200)):
        return submit(db, evaluators, body, idempotency_key)

    @api.get("/detection-evaluations", response_model=DetectionEvaluationPage, tags=["detection evaluation"])
    def list_evaluations(status: JobStatus | None = None, limit: int = Query(100, ge=1, le=100), before_seq: int | None = Query(None, ge=1)):
        with db.read_session() as session:
            query = select(JobRow).where(JobRow.kind == "evaluation").order_by(JobRow.submission_seq.desc())
            if status is not None:
                query = query.where(JobRow.status == status)
            if before_seq is not None:
                query = query.where(JobRow.submission_seq < before_seq)
            rows = list(session.scalars(query.limit(limit + 1)))
            items = [DetectionEvaluationSummary.model_validate(detail(session, row).model_dump(include=set(DetectionEvaluationSummary.model_fields))) for row in rows[:limit]]
            return {"items": items, "next_before_seq": rows[limit - 1].submission_seq if len(rows) > limit else None}

    @api.get("/detection-evaluations/{evaluation_id}", response_model=DetectionEvaluationDetail, tags=["detection evaluation"])
    def get_evaluation(evaluation_id: str):
        return jobs.job_get(db, evaluation_id, kind="evaluation")

    @api.post("/detection-evaluations/{evaluation_id}/cancel", response_model=DetectionEvaluationDetail, tags=["detection evaluation"])
    def cancel_evaluation(evaluation_id: str):
        return jobs.cancel_job(db, evaluation_id, kind="evaluation")

    @api.get("/detection-evaluations/{evaluation_id}/export", tags=["detection evaluation"])
    def export_evaluation(evaluation_id: str):
        with db.read_session() as session:
            row = jobs._row(session, evaluation_id)
            value = detail(session, row)
            if value.status not in jobs.TERMINAL:
                raise HTTPException(409, "Evaluation export requires a terminal outcome")
            return Response(_subject(session, row).manifest, media_type="application/json",
                            headers={"Content-Disposition": f'attachment; filename="{value.id}.evaluation.json"'})
