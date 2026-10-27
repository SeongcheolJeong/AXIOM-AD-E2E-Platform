"""Actual pinned COCO arithmetic, not mocked aggregate metric responses."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ade2e.evaluator_runtime import EvaluationCancelled, EvaluationInput, EvaluationInputError, EvaluationReport
from axiom_coco import CocoBboxEvaluator
from axiom_coco.engine import evaluate


def annotation(identifier="gt", label="car", box=(0, 0, 10, 10), crowd=False):
    return {"id": identifier, "label": label, "bbox_xyxy": list(box), "iscrowd": crowd}


def prediction(identifier="dt", label="car", box=(0, 0, 10, 10), score=.9):
    return {"id": identifier, "label": label, "bbox_xyxy": list(box), "score": score}


def row(identifier="image1", annotations=None, predictions=None, status="evaluated"):
    return {"image_id": identifier, "status": status, "width": 640, "height": 480,
            "annotations": [annotation()] if annotations is None else annotations,
            "predictions": [prediction()] if predictions is None else predictions}


def request(rows=None, labels=None, threshold=0, iou=.5):
    return {"labels": labels or ["car"], "score_threshold": threshold, "diagnostic_iou": iou,
            "rows": rows if rows is not None else [row()]}


def run(data):
    result = evaluate(data)
    EvaluationReport.model_validate(result)
    return result


def test_perfect_boxes_have_full_ap_and_explicit_matching_evidence():
    result = run(request())
    assert result["metrics"]["ap"] == pytest.approx(1)
    assert result["metrics"]["ap50"] == pytest.approx(1)
    assert result["classes"][0]["tp"] == 1
    assert result["classes"][0]["fp"] == result["classes"][0]["fn"] == 0
    assert result["rows"][0]["diagnostics"]["matches"] == [
        {"outcome": "true_positive", "prediction_id": "dt", "annotation_id": "gt", "iou": 1}]
    assert result["parameters"]["max_dets"] == [1, 10, 100]
    assert len(result["classes"][0]["pr_curve"]["recall"]) == 101


def test_positive_image_with_successful_empty_predictions_is_a_miss():
    result = run(request([row(predictions=[])]))
    assert result["metrics"]["ap"] == 0
    assert result["metrics"]["recall"] == 0
    assert result["metrics"]["precision"] is None
    assert result["metrics"]["f1"] == 0
    assert result["classes"][0]["fn"] == 1
    assert result["rows"][0]["diagnostics"]["matches"][0]["outcome"] == "false_negative"


def test_empty_negative_dataset_is_complete_but_ap_is_undefined():
    result = run(request([row(annotations=[], predictions=[])]))
    assert result["coverage"]["complete"]
    assert all(value is None for value in result["metrics"].values())
    assert result["classes"][0]["ap"] is None
    assert result["classes"][0]["pr_curve"] is None
    assert result["classes"][0]["gt"] == result["classes"][0]["fp"] == 0


def test_absent_class_false_positive_remains_visible_even_when_coco_excludes_class():
    result = run(request([row(predictions=[prediction(), prediction("p2", "pedestrian")])], labels=["car", "pedestrian"]))
    assert result["metrics"]["ap"] == pytest.approx(1)
    assert result["metrics"]["precision"] == .5
    absent = result["classes"][1]
    assert absent["gt"] == 0 and absent["fp"] == 1
    assert absent["ap"] is None and absent["pr_curve"] is None


def test_high_score_false_positive_on_negative_image_lowers_ap():
    result = run(request([row("negative", annotations=[], predictions=[prediction(score=.99)]), row("positive")]))
    assert result["metrics"]["ap"] == pytest.approx(.5)
    assert result["classes"][0]["tp"] == result["classes"][0]["fp"] == 1


def test_low_score_duplicate_can_leave_ap_perfect_but_not_precision():
    result = run(request([row(predictions=[prediction(score=.9), prediction("duplicate", score=.8)])]))
    assert result["metrics"]["ap"] == pytest.approx(1)
    assert result["metrics"]["precision"] == .5
    assert [item["outcome"] for item in result["rows"][0]["diagnostics"]["matches"]] == ["true_positive", "false_positive"]


def test_continuous_pixels_and_inclusive_iou_threshold():
    result = run(request([row(predictions=[prediction(box=(0, 0, 5, 10))])]))
    assert result["metrics"]["ap50"] == pytest.approx(1)
    assert result["metrics"]["ap75"] == 0
    assert result["metrics"]["ap"] == pytest.approx(.1)
    assert result["rows"][0]["diagnostics"]["matches"][0]["iou"] == .5


def test_score_ties_use_frozen_row_then_original_detection_order():
    false = prediction("fp", box=(20, 20, 30, 30), score=.9)
    true = prediction("tp", score=.9)
    fp_first = run(request([row(predictions=[false, true])]))
    tp_first = run(request([row(predictions=[true, false])]))
    assert fp_first["metrics"]["ap"] == pytest.approx(.5)
    assert tp_first["metrics"]["ap"] == pytest.approx(1)
    # Image identifiers are not lexically re-sorted; canonical row order defines numeric COCO IDs.
    assert run(request([row("z", annotations=[], predictions=[false]), row("a", predictions=[true])]))["metrics"]["ap"] == pytest.approx(.5)


def test_exact_gt_overlap_tie_keeps_official_last_gt_match():
    result = run(request([row(annotations=[annotation("g1"), annotation("g2")])]))
    matches = result["rows"][0]["diagnostics"]["matches"]
    assert matches[0]["annotation_id"] == "g2"
    assert matches[1]["annotation_id"] == "g1" and matches[1]["outcome"] == "false_negative"


def test_crowd_uses_detection_area_and_can_ignore_multiple_detections():
    result = run(request([row(annotations=[annotation(box=(0, 0, 100, 100), crowd=True)],
                             predictions=[prediction(), prediction("d2", box=(20, 20, 30, 30))])]))
    counts = result["classes"][0]
    assert counts["gt"] == 0 and counts["crowd_gt"] == 1
    assert counts["crowd_ignored"] == 2 and counts["tp"] == counts["fp"] == counts["fn"] == 0
    assert counts["ap"] is None
    assert all(pair["iou"] == 1 and pair["outcome"] == "crowd_ignored" for pair in result["rows"][0]["diagnostics"]["matches"])


def test_regular_ground_truth_is_preferred_over_crowd():
    result = run(request([row(annotations=[annotation("crowd", crowd=True), annotation("regular")])]))
    assert result["rows"][0]["diagnostics"]["matches"][0]["annotation_id"] == "regular"
    assert result["classes"][0]["tp"] == 1


def test_area_strata_use_bbox_area_and_boundary_belongs_to_both_ranges():
    result = run(request([row(annotations=[annotation(box=(0, 0, 32, 32))], predictions=[])]))
    assert result["metrics"]["ap_small"] == result["metrics"]["ap_medium"] == 0
    assert result["metrics"]["ap_large"] is None
    assert result["parameters"]["area_source"] == "bbox_width_times_height"


def test_smaller_area_does_not_import_larger_unmatched_false_positive():
    result = run(request([row(predictions=[prediction("large-fp", box=(100, 100, 300, 300), score=.99), prediction()])]))
    assert result["metrics"]["ap"] == pytest.approx(.5)
    assert result["metrics"]["ap_small"] == pytest.approx(1)
    assert result["metrics"]["ap_large"] is None


def test_coco_cap100_and_diagnostic300_are_distinct():
    predictions = [prediction(f"fp-{i}", box=(20, 20, 30, 30), score=.99 - i / 1000) for i in range(100)]
    predictions.append(prediction("rank101", score=.1))
    result = run(request([row(predictions=predictions)]))
    assert result["metrics"]["ap"] == 0
    assert result["metrics"]["ar100"] == 0
    assert result["classes"][0]["tp"] == 1 and result["classes"][0]["fp"] == 100
    assert result["metrics"]["recall"] == 1


def test_coco_maxdets_are_per_image_category():
    predictions = [prediction(f"car-{i}", box=(20, 20, 30, 30), score=.99) for i in range(100)]
    predictions.append(prediction("ped", "pedestrian", score=.1))
    result = run(request([row(annotations=[annotation(label="pedestrian")], predictions=predictions)], ["car", "pedestrian"]))
    assert result["metrics"]["ap"] == pytest.approx(1)
    assert result["classes"][0]["fp"] == 100
    assert result["classes"][1]["tp"] == 1


def test_additional_score_filter_is_inclusive_and_counts_removed_boxes():
    result = run(request([row(predictions=[prediction(score=.5), prediction("removed", score=.499)])], threshold=.5))
    assert result["rows"][0]["diagnostics"]["score_filtered"] == 1
    assert result["classes"][0]["predictions"] == 1
    assert result["metrics"]["ap"] == pytest.approx(1)


@pytest.mark.parametrize("status", ["unreviewed", "missing_prediction", "failed_prediction", "input_changed"])
def test_incomplete_row_is_never_turned_into_empty_prediction_or_hidden(status):
    result = run(request([row("good"), row("unavailable", status=status)]))
    assert result["coverage"]["total"] == 2
    assert result["coverage"]["evaluated"] == result["coverage"][status] == 1
    assert not result["coverage"]["complete"] and result["metrics"] is None
    assert result["rows"][1]["diagnostics"] is None
    assert result["classes"][0]["gt"] == 1 and result["classes"][0]["tp"] == 1
    assert result["classes"][0]["ap"] is result["classes"][0]["pr_curve"] is None


def test_all_unavailable_has_null_metrics_and_full_denominator():
    result = run(request([row(status="failed_prediction")]))
    assert result["coverage"]["total"] == 1 and result["coverage"]["failed_prediction"] == 1
    assert result["metrics"] is None and result["rows"][0]["diagnostics"] is None


@pytest.mark.parametrize("mutate", [
    lambda data: data["rows"][0]["predictions"][0].update(score=float("nan")),
    lambda data: data["rows"][0]["predictions"][0].update(bbox_xyxy=[0, 0, float("inf"), 10]),
    lambda data: data["rows"][0]["predictions"][0].update(bbox_xyxy=[0, 0, 641, 10]),
    lambda data: data["rows"][0]["predictions"][0].update(bbox_xyxy=[0, 0, 0, 10]),
    lambda data: data["rows"][0]["annotations"][0].update(ignore=1),
    lambda data: data["rows"][0]["annotations"][0].update(iscrowd=1),
    lambda data: data["rows"][0]["predictions"][0].update(label="not-in-taxonomy"),
    lambda data: data["rows"].append(copy.deepcopy(data["rows"][0])),
    lambda data: data["rows"][0]["predictions"].append(copy.deepcopy(data["rows"][0]["predictions"][0])),
    lambda data: data.update(diagnostic_iou=.51),
    lambda data: data.update(rows=[]),
    lambda data: data.update(labels=["car", "car"]),
    lambda data: data["rows"][0].update(height=2000000),
    lambda data: data["rows"][0].update(predictions=[prediction(str(i)) for i in range(301)]),
    lambda data: data["rows"][0].update(annotations=[annotation(str(i)) for i in range(101)]),
])
def test_native_boundary_rejects_invalid_or_unbounded_inputs(mutate):
    data = request()
    mutate(data)
    with pytest.raises(ValueError):
        evaluate(data)


def test_runtime_runs_actual_child_and_returns_typed_report():
    runtime = CocoBboxEvaluator()
    result = runtime.evaluate(EvaluationInput.model_validate(request()), lambda: False)
    assert result.metrics.ap == pytest.approx(1)
    assert result.implementation == runtime.metadata().implementation
    assert result.elapsed_ms > 0


def test_runtime_rejects_geometry_at_child_boundary():
    data = request()
    data["rows"][0]["predictions"][0]["bbox_xyxy"] = [0, 0, 999, 10]
    with pytest.raises(EvaluationInputError, match="bbox coordinate"):
        CocoBboxEvaluator().evaluate(EvaluationInput.model_validate(data), lambda: False)


def test_parent_metadata_does_not_import_native_packages():
    script = "from axiom_coco import CocoBboxEvaluator; import sys; assert CocoBboxEvaluator().metadata().status == 'available'; assert 'numpy' not in sys.modules; assert not any(x.startswith('pycocotools') for x in sys.modules)"
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, timeout=10)


def test_cancellation_stops_owned_process_before_acknowledgement(monkeypatch):
    import axiom_coco.runtime as runtime_module
    opened = []
    cancellation_pids = []
    original = runtime_module.ManagedJsonProcess
    class TrackedProcess(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            opened.append(self)
    monkeypatch.setattr(runtime_module, "ManagedJsonProcess", TrackedProcess)

    def cancel_after_owned_spawn():
        # __init__ registers the wrapper before __enter__ creates its child.
        # Gate on the retained Popen handle, not input-validation wall time.
        if not opened or opened[0].process is None:
            return False
        process = opened[0].process
        assert process.poll() is None
        cancellation_pids.append(process.pid)
        return True

    with pytest.raises(EvaluationCancelled):
        runtime_module.CocoBboxEvaluator().evaluate(EvaluationInput.model_validate(request()), cancel_after_owned_spawn)
    assert len(opened) == 1 and opened[0].process is not None
    child = opened[0]
    assert cancellation_pids == [child.process.pid]
    assert child.process.poll() is not None and child.closed
    assert not child.reader.is_alive() and not child.writer.is_alive()
    assert child.process.stdin.closed and child.process.stdout.closed and child.stderr.closed


def test_cancellation_before_spawn_does_not_require_or_create_a_child(monkeypatch):
    import ade2e.process_transport as transport

    def forbidden_spawn(*args, **kwargs):
        pytest.fail("Pre-spawn cancellation must not start a native child")

    monkeypatch.setattr(transport.subprocess, "Popen", forbidden_spawn)
    with pytest.raises(EvaluationCancelled, match="before model initialization"):
        CocoBboxEvaluator().evaluate(EvaluationInput.model_validate(request()), lambda: True)


def test_pinned_library_drift_is_unavailable_without_native_import(monkeypatch):
    import axiom_coco.runtime as runtime_module
    monkeypatch.setattr(runtime_module, "version", lambda name: "2.0.10" if name == "pycocotools" else "2.5.3")
    assert runtime_module.CocoBboxEvaluator().metadata().status == "unavailable"
    with pytest.raises(EvaluationInputError, match="Pinned"):
        runtime_module.CocoBboxEvaluator().evaluate(EvaluationInput.model_validate(request()), lambda: False)
