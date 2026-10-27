"""Supplemental recovery keeps the original execution history immutable."""
from typing import Literal
from pydantic import Field
from .base_schemas import Name, StrictModel
from .execution_attempt import ExecutionAttemptRequest, RemoteExecutionStatus
from .job_schemas import JobEvent, JobStatus
from .model_schemas import Sha256


class RecoveryCreate(StrictModel):
    source_job_id: Name
    note: str = Field(min_length=1, max_length=1000, pattern=r'.*\S.*')


class RemoteExecutionBinding(StrictModel):
    store_id: Name
    attempt: ExecutionAttemptRequest
    request_hash: Sha256


class RemoteExecutionInspection(StrictModel):
    source_job_id: str
    source_run_id: str | None
    source_execution_id: str | None
    supported: bool
    can_inspect: bool
    can_retrieve: bool
    reason: str | None
    binding: RemoteExecutionBinding | None
    status: RemoteExecutionStatus | None
    recovered_run_id: str | None = None


class RecoverySummary(StrictModel):
    id: str
    submission_seq: int
    source_job_id: str
    source_run_id: str | None
    source_execution_id: str
    status: JobStatus
    outcome: Literal['pending', 'recovered', 'diagnostics_only', 'not_available', 'failed']
    created_at: str
    updated_at: str
    finished_at: str | None
    note: str
    run_id: str | None
    error: str | None
    remote_status: RemoteExecutionStatus | None
    manifest_hash: Sha256 | None
    manifest_size_bytes: int | None
    download_url: str | None
    archive_hash: Sha256 | None
    archive_size_bytes: int | None
    archive_download_url: str | None


class RecoveryDetail(RecoverySummary):
    binding: RemoteExecutionBinding
    events: list[JobEvent]
    events_total: int
    cancellation_supported: bool = True
    worker_id: str | None
    lease_expires_at: str | None
    cancel_requested_at: str | None


class RecoveryPage(StrictModel):
    items: list[RecoverySummary]
    next_before_seq: int | None = None
