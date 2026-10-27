"""Seal one live comparison into verified bytes without later recomputation."""

from typing import Annotated
import hashlib

from fastapi import APIRouter, Header, HTTPException, Query, Response
from pydantic import ValidationError
from sqlalchemy import select

from .comparison_models import ComparisonEvidenceRow
from .comparison_schemas import ComparisonCreate, ComparisonDetail, ComparisonManifest, ComparisonSummary
from .database import Database
from .jobs import _write
from .service import canonical_bytes, digest, uid, utcnow

MAX_COMPARISON_BYTES = 32 * 1024 * 1024


def _summary(manifest: ComparisonManifest, sha: str, size: int) -> ComparisonSummary:
    candidate, baseline, report = manifest.candidate, manifest.baseline, manifest.report
    return ComparisonSummary(
        id=manifest.comparison_id, name=manifest.name, note=manifest.note,
        campaign_id=candidate.id, campaign_label=candidate.label,
        baseline_id=baseline.baseline_id, baseline_name=baseline.name,
        suite_id=candidate.suite_id, suite_revision=candidate.suite_revision,
        created_at=manifest.captured_at, policy_version=manifest.policy_version,
        candidate_verdict=candidate.verdict,
        candidate_evaluation_complete=candidate.evaluation_complete,
        manifest_hash=sha, manifest_size_bytes=size, counts=report.counts,
        download_url=f"/api/v1/comparisons/{manifest.comparison_id}/export",
    )


def _verified(row: ComparisonEvidenceRow) -> ComparisonDetail:
    """Verify both canonical evidence bytes and searchable captured metadata."""
    raw = row.manifest
    if (not isinstance(raw, bytes) or len(raw) > MAX_COMPARISON_BYTES
            or len(raw) != row.manifest_size_bytes
            or hashlib.sha256(raw).hexdigest() != row.manifest_hash):
        raise HTTPException(409, "Comparison evidence integrity check failed")
    try:
        manifest = ComparisonManifest.model_validate_json(raw)
        if canonical_bytes(manifest.model_dump(mode="json")) != raw:
            raise ValueError("Stored comparison is not canonical")
        baseline_raw = canonical_bytes(manifest.baseline.model_dump(mode="json"))
        baseline_hash = hashlib.sha256(baseline_raw).hexdigest()
        candidate, baseline, report = manifest.candidate, manifest.baseline, manifest.report
        if (len(baseline_raw) != manifest.baseline_manifest_size_bytes
                or baseline_hash != manifest.baseline_manifest_hash
                or report.baseline_manifest_hash != baseline_hash
                or candidate.status != "finished"
                or candidate.id != report.campaign_id
                or baseline.baseline_id != report.baseline_id
                or candidate.suite_id != baseline.report.suite_id
                or report.suite_id != candidate.suite_id):
            raise ValueError("Stored comparison lineage does not match its evidence")
        summary = _summary(manifest, row.manifest_hash, len(raw))
        if (ComparisonSummary.model_validate(row.summary) != summary
                or row.id != summary.id or row.campaign_id != summary.campaign_id
                or row.baseline_id != summary.baseline_id or row.suite_id != summary.suite_id
                or row.created_at != summary.created_at):
            raise ValueError("Stored comparison metadata does not match its evidence")
        return ComparisonDetail(**summary.model_dump(), manifest=manifest)
    except (ValidationError, ValueError, TypeError, OverflowError) as error:
        raise HTTPException(409, "Comparison evidence structure or lineage check failed") from error


def _get_row(session, comparison_id: str) -> ComparisonEvidenceRow:
    row = session.get(ComparisonEvidenceRow, comparison_id)
    if row is None:
        raise HTTPException(404, "Captured comparison not found")
    return row


def comparison_create(db: Database, body: ComparisonCreate, key: str) -> ComparisonDetail:
    if not isinstance(key, str) or not 1 <= len(key) <= 200:
        raise HTTPException(422, "A comparison Idempotency-Key of 1..200 characters is required")
    request_hash = digest(body.model_dump(mode="json"))
    with _write(db) as session:
        existing = session.scalar(select(ComparisonEvidenceRow).where(ComparisonEvidenceRow.idempotency_key == key))
        if existing is not None:
            if existing.request_hash != request_hash:
                raise HTTPException(409, "Idempotency-Key was already used for a different comparison capture")
            return _verified(existing)

        # Import here to keep model/route registration independent of helpers.
        from . import suites

        candidate = suites.campaign_report(session, body.campaign_id)
        if candidate.status != "finished":
            raise HTTPException(409, "Only a finished campaign can be captured; continue using the live comparison while jobs are active")
        baseline = suites.baseline_get(session, body.baseline_id)
        baseline_raw = canonical_bytes(baseline.manifest.model_dump(mode="json"))
        if (len(baseline_raw) != baseline.manifest_size_bytes
                or hashlib.sha256(baseline_raw).hexdigest() != baseline.manifest_hash):
            raise HTTPException(409, "Baseline canonical bytes differ from the verified original manifest")
        report = suites.compare_reports(candidate, baseline)
        manifest = ComparisonManifest(
            comparison_id=uid("cmp"), name=body.name, note=body.note,
            captured_at=utcnow(), policy_version=suites.COMPARISON_VERSION,
            baseline_manifest_hash=baseline.manifest_hash,
            baseline_manifest_size_bytes=baseline.manifest_size_bytes,
            baseline=baseline.manifest, candidate=candidate, report=report,
        )
        raw = canonical_bytes(manifest.model_dump(mode="json"))
        if len(raw) > MAX_COMPARISON_BYTES:
            raise HTTPException(413, "Comparison manifest exceeds the 32 MiB capture limit")
        sha = hashlib.sha256(raw).hexdigest()
        summary = _summary(manifest, sha, len(raw))
        row = ComparisonEvidenceRow(
            id=summary.id, idempotency_key=key, request_hash=request_hash,
            campaign_id=summary.campaign_id, baseline_id=summary.baseline_id,
            suite_id=summary.suite_id, created_at=summary.created_at,
            manifest=raw, manifest_hash=sha, manifest_size_bytes=len(raw),
            summary=summary.model_dump(mode="json"),
        )
        session.add(row)
        session.flush()
        return _verified(row)


def comparison_get(session, comparison_id: str) -> ComparisonDetail:
    return _verified(_get_row(session, comparison_id))


def comparison_list(session, *, campaign_id: str | None = None,
                    suite_id: str | None = None, limit: int = 100) -> list[ComparisonSummary]:
    if not 1 <= limit <= 100:
        raise HTTPException(422, "Comparison list limit must be 1..100")
    query = select(ComparisonEvidenceRow).order_by(ComparisonEvidenceRow.created_at.desc(), ComparisonEvidenceRow.id.desc())
    if campaign_id is not None:
        query = query.where(ComparisonEvidenceRow.campaign_id == campaign_id)
    if suite_id is not None:
        query = query.where(ComparisonEvidenceRow.suite_id == suite_id)
    # Verify one bounded manifest at a time; list responses contain summaries.
    return [ComparisonSummary.model_validate(_verified(row).model_dump(exclude={"manifest"}))
            for row in session.scalars(query.limit(limit).execution_options(yield_per=1))]


def comparison_export(session, comparison_id: str) -> tuple[bytes, ComparisonSummary]:
    row = _get_row(session, comparison_id)
    detail = _verified(row)
    return row.manifest, ComparisonSummary.model_validate(detail.model_dump(exclude={"manifest"}))


def register_comparison_routes(api: APIRouter, db: Database):
    """Register under the caller's authenticated /api/v1 router."""

    @api.post("/comparisons", response_model=ComparisonDetail, status_code=201, tags=["validation"])
    def post_comparison(body: ComparisonCreate, idempotency_key: Annotated[str, Header(min_length=1, max_length=200)]):
        return comparison_create(db, body, idempotency_key)

    @api.get("/comparisons", response_model=list[ComparisonSummary], tags=["validation"])
    def get_comparisons(campaign_id: str | None = None, suite_id: str | None = None,
                        limit: int = Query(100, ge=1, le=100)):
        with db.session() as session:
            return comparison_list(session, campaign_id=campaign_id, suite_id=suite_id, limit=limit)

    @api.get("/comparisons/{comparison_id}", response_model=ComparisonDetail, tags=["validation"])
    def get_comparison(comparison_id: str):
        with db.session() as session:
            return comparison_get(session, comparison_id)

    @api.get("/comparisons/{comparison_id}/export", tags=["validation"])
    def export_comparison(comparison_id: str):
        with db.session() as session:
            content, summary = comparison_export(session, comparison_id)
        return Response(content, media_type="application/json", headers={
            "Content-Disposition": f'attachment; filename="{summary.id}.json"',
            "X-Content-Type-Options": "nosniff",
            "X-Content-SHA256": summary.manifest_hash,
        })
