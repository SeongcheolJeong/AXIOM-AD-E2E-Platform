"""Read-only A/B review over sealed evaluation evidence, shared by UI and SDK.

No model is executed and no historical result is rewritten. Different models
are the comparison variable; dataset, scoring and execution-policy changes
block deltas. Missing images stay in the original denominator.
"""
from __future__ import annotations

import hashlib
from html import escape
import json
from typing import Literal
from urllib.parse import urlencode

from fastapi import HTTPException, Query, Response

from . import jobs
from .detection_evaluations import detail
from .service import canonical_bytes
from .schemas import StrictModel

VERSION = "detection-evaluation-comparison/1"
METRICS = ("ap", "ap50", "ap75", "precision", "recall", "f1")
LIMITATIONS = [
    "Differences are candidate minus baseline; they are not release approval or statistical significance.",
    "Models may differ. Dataset revision, input identities, class mapping, scoring and runtime policy must match.",
    "Incomplete coverage withholds all aggregate and per-image deltas; no missing image is treated as a negative.",
    "This is a portable summary pinned to source manifest hashes, not a backup of source images or model files.",
    "Synthetic fixtures demonstrate the workflow, not real-world detection accuracy.",
]


class EvaluationComparison(StrictModel):
    schema_version: Literal["detection-evaluation-comparison/1"]
    status: Literal["comparable", "not_comparable", "not_evaluated"]
    reasons: list[str]
    baseline: dict
    candidate: dict
    conditions: dict
    differences: list[dict]
    metrics: list[dict]
    rows: list[dict]
    row_count: int
    limitations: list[str]
    exportable: bool
    exports: dict


def _plain(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _conditions(evaluation):
    request = evaluation.request
    context = evaluation.source_context
    inference = dict(context.get("inference_configuration", {}))
    inference.pop("model_manifest_hash", None)
    observed = dict(context.get("observed_runtime_context") or {})
    observed.pop("model_sha256", None)
    return {
        "dataset_manifest": evaluation.dataset_snapshot.get("manifest_hash"),
        "split": request.split,
        "selection": request.selection.model_dump(mode="json") if request.selection else None,
        "class_mapping": sorted((_plain(x) for x in request.class_mapping), key=lambda x: x["model_label"]),
        "score_threshold": request.score_threshold,
        "diagnostic_iou": request.diagnostic_iou,
        "evaluator": {k: evaluation.evaluator_snapshot.get(k) for k in ("id", "version", "policy_version", "implementation")},
        "inference_policy": inference,
        "observed_runtime_policy": observed,
        "report_policy": None if evaluation.report is None else {
            "version": evaluation.report.policy_version, "parameters": evaluation.report.parameters,
            "implementation": evaluation.report.implementation},
    }


def _pin(evaluation):
    return {"id": evaluation.id, "name": evaluation.name, "status": evaluation.status,
        "manifest_hash": evaluation.manifest_hash, "input_hash": evaluation.input_hash,
        "dataset_id": evaluation.dataset_id, "dataset_revision": evaluation.dataset_revision,
        "dataset_manifest_hash": evaluation.dataset_snapshot.get("manifest_hash"),
        "split": evaluation.request.split,
        "coverage": None if evaluation.report is None else _plain(evaluation.report.coverage),
        "models": [{"job_id": s["id"], "source_manifest_hash": s.get("manifest_hash"),
            "model_id": s["model_snapshot"].get("id"), "model_name": s["model_snapshot"].get("name"),
            "model_revision": s["model_snapshot"].get("revision"),
            "model_manifest_hash": s["model_snapshot"].get("manifest_hash")} for s in evaluation.source_jobs],
        "warnings": [] if evaluation.report is None else evaluation.report.warnings}


def build_review(baseline, candidate):
    if baseline.id == candidate.id:
        raise HTTPException(422, "Choose two different evaluation IDs")
    conditions = [_conditions(x) for x in (baseline, candidate)]
    differences = [{"field": key, "baseline": conditions[0][key], "candidate": conditions[1][key]}
                   for key in conditions[0] if conditions[0][key] != conditions[1][key]]
    ready = all(x.status == "completed" and x.report is not None and x.manifest_hash
                and x.report.coverage.complete and x.report.metrics is not None for x in (baseline, candidate))
    status = "not_comparable" if differences else "comparable" if ready else "not_evaluated"
    comparable = status == "comparable"
    reasons = ["Comparison conditions differ: " + x["field"] for x in differences]
    if not ready:
        reasons.append("Both evaluations must be completed, sealed and have complete reviewed input coverage.")
    metrics = []
    for key in METRICS:
        values = [getattr(x.report.metrics, key) if x.report and x.report.metrics else None for x in (baseline, candidate)]
        metrics.append({"name": key, "baseline": values[0], "candidate": values[1],
            "delta": values[1] - values[0] if comparable and all(v is not None for v in values) else None})
    reports = [{x.image_id: x for x in evaluation.report.rows} if evaluation.report else {}
               for evaluation in (baseline, candidate)]
    inputs = [{x.image_id: x for x in evaluation.evaluation_input.rows} for evaluation in (baseline, candidate)]
    identities = list(dict.fromkeys([*inputs[0], *inputs[1]]))
    rows = []
    for image_id in identities:
        sides = []
        for i in range(2):
            row = reports[i].get(image_id)
            sides.append({"status": row.status if row else "not_evaluated" if image_id in inputs[i] else "not_selected",
                          "counts": {k: getattr(row.diagnostics, k) for k in ("gt", "crowd_gt", "tp", "fp", "fn", "crowd_ignored")}
                          if row and row.diagnostics else None})
        delta = None
        outcome = "not_evaluated"
        if comparable and all(s["counts"] is not None and s["status"] == "evaluated" for s in sides):
            delta = {k: sides[1]["counts"][k] - sides[0]["counts"][k] for k in ("tp", "fp", "fn")}
            fp, fn = delta["fp"], delta["fn"]
            outcome = ("unchanged" if fp == fn == 0 else "more_errors" if fp >= 0 and fn >= 0
                       else "fewer_errors" if fp <= 0 and fn <= 0 else "tradeoff")
        rows.append({"image_id": image_id, "baseline": sides[0], "candidate": sides[1], "delta": delta, "outcome": outcome})
    return {"schema_version": VERSION, "status": status, "reasons": reasons,
        "baseline": _pin(baseline), "candidate": _pin(candidate),
        "conditions": {"baseline": conditions[0], "candidate": conditions[1]},
        "differences": differences, "metrics": metrics, "rows": rows,
        "row_count": len(rows), "limitations": LIMITATIONS,
        "exportable": all(x.status in jobs.TERMINAL and x.manifest_hash for x in (baseline, candidate))}


def render_html(review):
    """Static, script-free export. All names and metadata are untrusted text."""
    def number(value):
        return "not evaluated" if value is None else f"{value:.6f}"
    metrics = "".join("<tr>" + "".join(f"<td>{escape(str(x))}</td>" for x in
        (m["name"], number(m["baseline"]), number(m["candidate"]), number(m["delta"]))) + "</tr>" for m in review["metrics"])
    rows = "".join("<tr>" + "".join(f"<td>{escape(str(x))}</td>" for x in
        (r["image_id"], r["baseline"]["status"], r["candidate"]["status"],
         json.dumps(r["baseline"]["counts"]), json.dumps(r["candidate"]["counts"]),
         json.dumps(r["delta"]), r["outcome"])) + "</tr>" for r in review["rows"])
    evidence = escape(json.dumps(review, ensure_ascii=False, indent=2, allow_nan=False))
    return ("<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
        "<title>AXIOM evaluation A/B review</title><style>body{font:16px/1.6 system-ui;margin:auto;max-width:1100px;padding:24px}"
        "table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:8px;border:1px solid #ccc;text-align:left}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere}section{overflow:auto;margin:24px 0}h1{font-size:26px}</style>"
        "<h1>AXIOM · Evaluation A/B review</h1><p>" + escape(review["baseline"]["name"]) + " → " + escape(review["candidate"]["name"]) + "</p>"
        "<p><strong>" + escape(review["status"]) + "</strong> · " + escape(" ".join(review["reasons"])) + "</p>"
        "<p>Candidate minus baseline. Scores use 0–1, not percentages. This summary is not release approval.</p>"
        "<section><h2>Aggregate metrics</h2><table><tr><th>Metric</th><th>Baseline</th><th>Candidate</th><th>Delta</th></tr>" + metrics + "</table></section>"
        f"<section><h2>All {review['row_count']} selected images</h2><table><tr><th>Image</th><th>A state</th><th>B state</th>"
        "<th>A counts</th><th>B counts</th><th>Delta</th><th>Diagnostic</th></tr>" + rows + "</table></section>"
        "<section><h2>Source identities, conditions and limitations</h2><pre>" + evidence + "</pre></section></html>").encode()


def register_evaluation_review_routes(api, db):
    def load(baseline_id, candidate_id):
        with db.read_session() as session:
            a = detail(session, jobs._row(session, baseline_id))
            b = detail(session, jobs._row(session, candidate_id))
            return build_review(a, b)

    @api.get("/evaluation-comparisons", response_model=EvaluationComparison, tags=["detection evaluation"])
    def comparison(baseline_id: str = Query(min_length=1, max_length=180), candidate_id: str = Query(min_length=1, max_length=180)):
        review = load(baseline_id, candidate_id)
        formats = {"json": canonical_bytes(review), "html": render_html(review)}
        params = {"baseline_id": baseline_id, "candidate_id": candidate_id}
        exports = {kind: {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
            "url": "/api/v1/evaluation-comparisons/export?" + urlencode({**params, "format": kind})}
            for kind, raw in formats.items()} if review["exportable"] else {}
        return {**review, "exports": exports}

    @api.get("/evaluation-comparisons/export", tags=["detection evaluation"])
    def export(baseline_id: str = Query(min_length=1, max_length=180), candidate_id: str = Query(min_length=1, max_length=180),
               format: Literal["json", "html"] = "json"):
        review = load(baseline_id, candidate_id)
        if not review["exportable"]:
            raise HTTPException(409, "Export requires two terminal, sealed evaluations")
        raw = canonical_bytes(review) if format == "json" else render_html(review)
        digest = hashlib.sha256(raw).hexdigest()
        return Response(raw, media_type="application/json" if format == "json" else "text/html",
            headers={"Content-Disposition": f'attachment; filename="evaluation-review-{digest[:12]}.{format}"',
                     "X-Content-SHA256": digest, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
