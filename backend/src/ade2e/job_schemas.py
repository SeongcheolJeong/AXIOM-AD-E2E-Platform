"""Queue state is separate from completed-run metric verdicts."""

from typing import Literal

from pydantic import Field, field_serializer

from .schemas import RunCreate, Scenario, StrictModel
from .simulation_context import AnyPreparedSimulationContext

JobCreate = RunCreate
JobStatus = Literal["queued", "running", "cancel_requested", "completed", "failed", "cancelled", "interrupted"]


class JobEvent(StrictModel):
    at: str
    type: str
    message: str


class JobSummary(StrictModel):
    id: str
    submission_seq: int
    scenario_id: str
    scenario_name: str
    scenario_revision: int
    adapter_id: str
    adapter_version: str
    status: JobStatus
    created_at: str
    updated_at: str
    started_at: str | None = None
    dispatched_at: str | None = None
    finished_at: str | None = None
    run_id: str | None = None
    error: str | None = None
    input_hash: str
    worker_id: str | None = None
    lease_expires_at: str | None = None
    cancel_requested_at: str | None = None
    cancellation_supported: bool
    outcome_uncertain: bool


class JobDetail(JobSummary):
    request: RunCreate
    scenario_snapshot: Scenario
    events: list[JobEvent]
    events_total: int
    failure_http_status: int | None = None
    execution_context: AnyPreparedSimulationContext | None = None

    @field_serializer("request")
    def serialize_request(self, value: RunCreate) -> dict:
        # Omitted parameter overrides must stay omitted when this request is replayed.
        return value.model_dump(mode="json", exclude_none=True)


class JobPage(StrictModel):
    items: list[JobSummary]
    next_before_seq: int | None = None


class WorkerSummary(StrictModel):
    id: str
    heartbeat_at: str
    state: Literal["idle", "running", "stopped"]
    job_id: str | None = None
    alive: bool


class ExecutionResource(StrictModel):
    adapter_id: str
    state: Literal["idle", "reserved", "reconcile_required"]
    job_id: str | None = None
    updated_at: str
    note: str


class ReconcileRequest(StrictModel):
    job_id: str
    note: str = Field(min_length=1, max_length=1000)
    confirmed_stopped: bool = False
