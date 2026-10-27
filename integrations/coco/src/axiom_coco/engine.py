"""COCO bbox arithmetic. Imported only inside the optional native child.

The API supplies verified identities; this boundary independently validates
bounded numeric inputs before calling pycocotools' native bbox IoU routine.
"""
from __future__ import annotations

import contextlib
import io
import math
from importlib.metadata import version

POLICY_VERSION = "coco-bbox-stored/1"
IOU_THRESHOLDS = [0.5 + i * 0.05 for i in range(10)]
STATUSES = ("evaluated", "unreviewed", "missing_prediction", "failed_prediction", "input_changed")
METRIC_NAMES = ("ap", "ap50", "ap75", "ap_small", "ap_medium", "ap_large", "ar1", "ar10", "ar100", "ar_small", "ar_medium", "ar_large")
MAX_IMAGES = 256
MAX_LABELS = 256
MAX_ANNOTATIONS = 100
MAX_PREDICTIONS = 300


def _number(value, name, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f"{name} must be a finite number in [{lo}, {hi}]")
    return value


def _text(value, name, limit=128):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be nonempty text, at most {limit} characters")
    return value


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise ValueError("Evaluation input has missing or unsupported fields")


def validate_request(request):
    _keys(request, ("labels", "score_threshold", "diagnostic_iou", "rows"))
    labels = request["labels"]
    if not isinstance(labels, list) or not 1 <= len(labels) <= MAX_LABELS:
        raise ValueError("Evaluation requires 1..256 labels")
    for label in labels:
        _text(label, "label")
    if len(set(labels)) != len(labels):
        raise ValueError("Evaluation labels must be unique")
    _number(request["score_threshold"], "score_threshold", 0, 1)
    iou = _number(request["diagnostic_iou"], "diagnostic_iou", 0.5, 0.95)
    if not any(abs(iou - expected) <= 1e-12 for expected in IOU_THRESHOLDS):
        raise ValueError("diagnostic_iou must be one of 0.50:0.05:0.95")
    rows = request["rows"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_IMAGES:
        raise ValueError("Evaluation requires 1..256 image rows")
    seen_images = set()
    for row in rows:
        _keys(row, ("image_id", "status", "width", "height", "annotations", "predictions"))
        image_id = _text(row["image_id"], "image_id")
        if image_id in seen_images:
            raise ValueError("Evaluation image IDs must be unique")
        seen_images.add(image_id)
        if row["status"] not in STATUSES:
            raise ValueError("Unsupported evaluation row status")
        for field in ("width", "height"):
            if type(row[field]) is not int or not 1 <= row[field] <= 2_000_000:
                raise ValueError("Image dimensions must be positive bounded integers")
        if row["width"] * row["height"] > 2_000_000:
            raise ValueError("Evaluation images exceed the pixel limit")
        for kind, maximum in (("annotations", MAX_ANNOTATIONS), ("predictions", MAX_PREDICTIONS)):
            objects = row[kind]
            if not isinstance(objects, list) or len(objects) > maximum:
                raise ValueError(f"{kind} exceeds the per-image bound")
            seen = set()
            for obj in objects:
                _keys(obj, ("id", "label", "bbox_xyxy", "iscrowd" if kind == "annotations" else "score"))
                identifier = _text(obj["id"], "object id")
                if identifier in seen:
                    raise ValueError("Object IDs must be unique within each image and object kind")
                seen.add(identifier)
                if obj["label"] not in labels:
                    raise ValueError("Object label is outside the pinned taxonomy")
                box = obj["bbox_xyxy"]
                if not isinstance(box, (list, tuple)) or len(box) != 4:
                    raise ValueError("bbox_xyxy must contain four coordinates")
                for i, coordinate in enumerate(box):
                    _number(coordinate, "bbox coordinate", 0, row["width"] if i % 2 == 0 else row["height"])
                if box[2] <= box[0] or box[3] <= box[1]:
                    raise ValueError("Boxes must have positive continuous pixel area")
                if kind == "annotations":
                    if type(obj["iscrowd"]) is not bool:
                        raise ValueError("iscrowd must be a boolean; generic ignore is unsupported")
                else:
                    _number(obj["score"], "score", 0, 1)


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _valid_mean(values):
    present = values[values >= 0]
    return float(present.mean()) if present.size else None


def evaluate(request: dict) -> dict:
    validate_request(request)
    if version("pycocotools") != "2.0.11" or version("numpy") != "2.5.3":
        raise ValueError("Evaluator package versions differ from the pinned policy")
    import numpy as np
    from pycocotools.coco import COCO
    from pycocotools.cocoeval import COCOeval
    from pycocotools.mask import iou as bbox_iou

    labels, rows = request["labels"], request["rows"]
    category_ids = {label: i + 1 for i, label in enumerate(labels)}
    categories = [{"id": identifier, "name": label} for label, identifier in category_ids.items()]
    images, ground_truth, detections = [], [], []
    gt_sources, dt_sources = {}, {}
    row_by_number = {}
    report_rows = []
    class_counts = {label: {"label": label, "gt": 0, "crowd_gt": 0, "predictions": 0,
                            "tp": 0, "fp": 0, "fn": 0, "crowd_ignored": 0,
                            "ap": None, "pr_curve": None} for label in labels}
    coverage = {"total": len(rows), **{status: 0 for status in STATUSES}}
    for number, row in enumerate(rows, 1):
        coverage[row["status"]] += 1
        output = {"image_id": row["image_id"], "status": row["status"], "diagnostics": None}
        report_rows.append(output)
        if row["status"] != "evaluated":
            continue
        images.append({"id": number, "width": row["width"], "height": row["height"]})
        row_by_number[number] = output
        filtered = [prediction for prediction in row["predictions"] if prediction["score"] >= request["score_threshold"]]
        output["diagnostics"] = {"gt": 0, "crowd_gt": 0, "predictions": len(filtered),
                                 "score_filtered": len(row["predictions"]) - len(filtered),
                                 "tp": 0, "fp": 0, "fn": 0, "crowd_ignored": 0, "matches": []}
        for obj in row["annotations"]:
            x1, y1, x2, y2 = obj["bbox_xyxy"]
            identifier = len(ground_truth) + 1
            entry = {"id": identifier, "image_id": number, "category_id": category_ids[obj["label"]],
                     "bbox": [x1, y1, x2 - x1, y2 - y1], "area": (x2 - x1) * (y2 - y1), "iscrowd": int(obj["iscrowd"])}
            ground_truth.append(entry)
            gt_sources[identifier] = obj
            field = "crowd_gt" if obj["iscrowd"] else "gt"
            class_counts[obj["label"]][field] += 1
            output["diagnostics"][field] += 1
        for obj in filtered:
            x1, y1, x2, y2 = obj["bbox_xyxy"]
            identifier = len(detections) + 1
            detections.append({"id": identifier, "image_id": number, "category_id": category_ids[obj["label"]],
                               "bbox": [x1, y1, x2 - x1, y2 - y1], "area": (x2 - x1) * (y2 - y1),
                               "iscrowd": 0, "score": obj["score"]})
            dt_sources[identifier] = obj
            class_counts[obj["label"]]["predictions"] += 1
    coverage["complete"] = coverage["evaluated"] == coverage["total"]
    parameters = {"iou_thresholds": np.linspace(.5, .95, 10).tolist(), "recall_thresholds": np.linspace(0, 1, 101).tolist(),
                  "max_dets": [1, 10, 100], "area_ranges": {"all": [0, 1e10], "small": [0, 1024], "medium": [1024, 9216], "large": [9216, 1e10]},
                  "area_source": "bbox_width_times_height", "use_categories": True, "score_threshold": request["score_threshold"],
                  "diagnostic_iou": request["diagnostic_iou"], "diagnostic_max_dets": 300,
                  "max_dets_scope": "image_category", "crowd_iou": "intersection_over_detection_area"}
    result = {"policy_version": POLICY_VERSION, "implementation": {"pycocotools": version("pycocotools"), "numpy": version("numpy")},
              "parameters": parameters, "coverage": coverage, "metrics": None,
              "diagnostic_scope": "evaluated_rows", "classes": list(class_counts.values()), "rows": report_rows,
              "warnings": ["Metrics evaluate saved predictions after their original threshold, NMS and limit.",
                           "Object area is bounding-box width times height; scale metrics differ from mask-area stratification."]}
    if not coverage["complete"]:
        result["warnings"].append("Coverage is incomplete. Counts and matches cover evaluated rows only; aggregate metrics and PR curves are withheld.")
    if not images:
        return result

    # Empty detections need an explicit COCO index: loadRes([]) indexes anns[0].
    with contextlib.redirect_stdout(io.StringIO()):
        gt, dt = COCO(), COCO()
        gt.dataset = {"images": images, "categories": categories, "annotations": ground_truth}
        dt.dataset = {"images": images, "categories": categories, "annotations": detections}
        gt.createIndex()
        dt.createIndex()
        diagnostic = COCOeval(gt, dt, "bbox")
        diagnostic.params.iouThrs = np.array([request["diagnostic_iou"]])
        diagnostic.params.areaRng, diagnostic.params.areaRngLbl = [[0, 1e10]], ["all"]
        diagnostic.params.maxDets = [MAX_PREDICTIONS]
        diagnostic.evaluate()
    for entry in diagnostic.evalImgs:
        if entry is None:
            continue
        counts = class_counts[labels[entry["category_id"] - 1]]
        per_image = row_by_number[entry["image_id"]]["diagnostics"]
        for i, detection_id in enumerate(entry["dtIds"]):
            annotation_id = int(entry["dtMatches"][0, i])
            ignored = bool(entry["dtIgnore"][0, i])
            field = "crowd_ignored" if ignored else "tp" if annotation_id else "fp"
            outcome = "crowd_ignored" if ignored else "true_positive" if annotation_id else "false_positive"
            prediction = dt_sources[detection_id]
            annotation = gt_sources.get(annotation_id)
            overlap = None
            if annotation is not None:
                db, gb = prediction["bbox_xyxy"], annotation["bbox_xyxy"]
                overlap = float(bbox_iou([[db[0], db[1], db[2] - db[0], db[3] - db[1]]],
                                         [[gb[0], gb[1], gb[2] - gb[0], gb[3] - gb[1]]], [int(annotation["iscrowd"])])[0, 0])
            counts[field] += 1
            per_image[field] += 1
            per_image["matches"].append({"outcome": outcome, "prediction_id": prediction["id"],
                                           "annotation_id": annotation["id"] if annotation else None, "iou": overlap})
        for i, annotation_id in enumerate(entry["gtIds"]):
            if not entry["gtIgnore"][i] and not entry["gtMatches"][0, i]:
                counts["fn"] += 1
                per_image["fn"] += 1
                per_image["matches"].append({"outcome": "false_negative", "prediction_id": None,
                                               "annotation_id": gt_sources[annotation_id]["id"], "iou": None})
    if coverage["complete"]:
        with contextlib.redirect_stdout(io.StringIO()):
            evaluator = COCOeval(gt, dt, "bbox")
            evaluator.evaluate()
            evaluator.accumulate()
            evaluator.summarize()
        result["metrics"] = {name: float(value) if value >= 0 else None for name, value in zip(METRIC_NAMES, evaluator.stats)}
        tp, fp, fn = (sum(item[field] for item in class_counts.values()) for field in ("tp", "fp", "fn"))
        result["metrics"].update(precision=_ratio(tp, tp + fp), recall=_ratio(tp, tp + fn), f1=_ratio(2 * tp, 2 * tp + fp + fn))
        selected_iou = int(np.argmin(np.abs(evaluator.params.iouThrs - request["diagnostic_iou"])))
        for label, category_id in category_ids.items():
            precision = evaluator.eval["precision"][:, :, category_id - 1, 0, 2]
            class_counts[label]["ap"] = _valid_mean(precision)
            curve = precision[selected_iou]
            if np.any(curve >= 0):
                class_counts[label]["pr_curve"] = {"recall": evaluator.params.recThrs.tolist(), "precision": curve.tolist()}
    return result
