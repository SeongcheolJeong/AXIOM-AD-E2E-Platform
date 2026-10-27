"""Captured comparison evidence is independent of future live evaluations."""

from typing import Annotated

from pydantic import StringConstraints

from .schemas import Name, StrictModel
from .suite_schemas import BaselineManifest, CampaignComparison, CampaignDetail, CampaignVerdict, ComparisonCounts


class ComparisonCreate(StrictModel):
    campaign_id: Name
    baseline_id: Name
    name: Name
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class ComparisonManifest(StrictModel):
    schema_version: str = "1"
    comparison_id: str
    name: str
    note: str
    captured_at: str
    policy_version: str
    baseline_manifest_hash: str
    baseline_manifest_size_bytes: int
    baseline: BaselineManifest
    candidate: CampaignDetail
    report: CampaignComparison


class ComparisonSummary(StrictModel):
    id: str
    name: str
    note: str
    campaign_id: str
    campaign_label: str
    baseline_id: str
    baseline_name: str
    suite_id: str
    suite_revision: int
    created_at: str
    policy_version: str
    candidate_verdict: CampaignVerdict
    candidate_evaluation_complete: bool
    manifest_hash: str
    manifest_size_bytes: int
    counts: ComparisonCounts
    download_url: str


class ComparisonDetail(ComparisonSummary):
    manifest: ComparisonManifest
