"""Product review contracts over explicitly authored fake evaluator data."""
from copy import deepcopy
import hashlib
import json

import pytest

from ade2e.evaluation_reviews import build_review, render_html
from ade2e.detection_evaluation_models import DetectionEvaluationRow
from test_detection_evaluations import evaluation_context, execute, submit, revise
from test_perception_jobs import context


def pair(ec, changes=None):
    a = execute(ec, submit(ec, key="review-A"))
    b = execute(ec, submit(ec, key="review-B", body=ec[-1].model_copy(update={"name": "Candidate B", **(changes or {})})))
    return a, b


def get(ec, a, b):
    return ec[0][0].get("/api/v1/evaluation-comparisons", params={"baseline_id": a.id, "candidate_id": b.id})


def test_api_review_and_hash_verified_exports_are_read_only_and_stable(evaluation_context):
    ec = evaluation_context; a, b = pair(ec)
    client = ec[0][0]
    original = client.get(a.download_url).content
    result = get(ec, a, b)
    assert result.status_code == 200, result.text
    review = result.json()
    assert review["status"] == "comparable" and review["row_count"] == 3
    assert all(r["outcome"] == "unchanged" for r in review["rows"])
    for kind, descriptor in review["exports"].items():
        response = client.get(descriptor["url"])
        assert response.status_code == 200
        assert hashlib.sha256(response.content).hexdigest() == descriptor["sha256"]
        assert len(response.content) == descriptor["size_bytes"]
        if kind == "html": assert "default-src 'none'" in response.text
    revise(ec, lambda body: body.update(note="New labels revision does not rewrite old evidence"))
    assert get(ec, a, b).json() == review
    assert client.get(a.download_url).content == original


@pytest.mark.parametrize("change", [{"score_threshold": .7}, {"diagnostic_iou": .75}])
def test_changed_metric_policy_withholds_every_delta(evaluation_context, change):
    a, b = pair(evaluation_context, change)
    result = get(evaluation_context, a, b).json()
    assert result["status"] == "not_comparable"
    assert all(m["delta"] is None for m in result["metrics"])
    assert all(r["delta"] is None for r in result["rows"])


def test_incomplete_coverage_never_becomes_negative_or_partial_improvement(evaluation_context):
    ec = evaluation_context
    revision = revise(ec, lambda v: v["images"][0].update(review_status="unreviewed"))
    body = ec[-1].model_copy(update={"dataset_revision": revision["revision"]})
    a = execute(ec, submit(ec, "partial-A", body))
    b = execute(ec, submit(ec, "partial-B", body))
    review = get(ec, a, b).json()
    assert review["status"] == "not_evaluated" and review["row_count"] == 3
    assert review["rows"][0]["baseline"]["status"] == "unreviewed"
    assert all(row["delta"] is None for row in review["rows"])


def test_model_is_comparison_variable_but_runtime_policy_is_not(evaluation_context):
    a, b = pair(evaluation_context)
    altered = deepcopy(b.source_context)
    altered["inference_configuration"]["model_manifest_hash"] = "b" * 64
    b = b.model_copy(update={"source_context": altered})
    assert build_review(a, b)["status"] == "comparable"
    altered["inference_configuration"]["iou_threshold"] = .91
    assert build_review(a, b)["status"] == "not_comparable"


def test_unfinished_export_is_rejected_and_missing_id_is_404(evaluation_context):
    ec = evaluation_context; a = execute(ec, submit(ec, "A")); b = submit(ec, "B")
    client = ec[0][0]
    assert client.get("/api/v1/evaluation-comparisons/export", params={"baseline_id": a.id, "candidate_id": b["id"]}).status_code == 409
    assert client.get("/api/v1/evaluation-comparisons", params={"baseline_id": a.id, "candidate_id": "missing"}).status_code == 404
    assert get(ec, a, a).status_code == 422


def test_report_text_cannot_execute_html(evaluation_context):
    a, b = pair(evaluation_context)
    b = b.model_copy(update={"name": "<script>alert(1)</script> & candidate"})
    html = render_html(build_review(a, b)).decode()
    assert "<script>" not in html
    assert "&lt;script&gt;" in html and "form-action 'none'" in html


def test_tampered_source_manifest_is_rejected(evaluation_context):
    ec = evaluation_context; a, b = pair(ec)
    with ec[0][1].state.db.session.begin() as session:
        session.get(DetectionEvaluationRow, b.id).manifest_hash = "0" * 64
    assert get(ec, a, b).status_code == 409


def test_comparison_uses_existing_bearer_auth_and_is_discoverable(evaluation_context):
    ec = evaluation_context; a, b = pair(ec)
    client, app = ec[0][:2]
    names = [t["name"] for t in client.get("/api/v1/agent/tools").json()["tools"]]
    assert "compare_detection_evaluations" in names
    assert "/api/v1/evaluation-comparisons" in client.get("/openapi.json").json()["paths"]
    app.state.settings.api_token = "test-only-token"
    assert get(ec, a, b).status_code == 401
    response = client.get("/api/v1/evaluation-comparisons", params={"baseline_id": a.id, "candidate_id": b.id}, headers={"Authorization": "Bearer test-only-token"})
    assert response.status_code == 200
