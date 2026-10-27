"""Typed recorded-image predictions, separate from driving validation evidence."""

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .job_schemas import JobEvent, JobStatus
from .schemas import StrictModel


class PerceptionJobCreate(StrictModel):
    label: str = Field(min_length=1, max_length=180)
    model_id: str = Field(min_length=1, max_length=64)
    model_revision: int = Field(ge=1)
    runtime_id: str = Field(default="onnx-cpu", min_length=1, max_length=180)
    recording_id: str = Field(min_length=1, max_length=64)
    channel_id: int = Field(ge=0, le=65535)
    ordinals: list[Annotated[int, Field(strict=True, ge=0, le=100_000)]] = Field(min_length=1, max_length=64)
    decoder_id: str = Field(min_length=1, max_length=180)
    decoder_version: str = Field(min_length=1, max_length=180)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    score_threshold: float = Field(default=0.35, ge=0, le=1, allow_inf_nan=False)
    iou_threshold: float = Field(default=0.45, ge=0, le=1, allow_inf_nan=False)
    max_detections: int = Field(default=100, ge=1, le=300)

    @field_validator("label")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Label must be nonempty")
        return value.strip()

    @field_validator("ordinals")
    @classmethod
    def unique_ordinals(cls, value):
        if len(set(value)) != len(value) or any(item < 0 or item > 100_000 for item in value):
            raise ValueError("Ordinals must be unique nonnegative file ordinals, at most 100000")
        return value


class PerceptionInput(StrictModel):
    selection_index: int
    recording_id: str
    recording_sha256: str
    channel_id: int
    ordinal: int
    log_time_ns: str
    publish_time_ns: str
    message_sha256: str
    message_size_bytes: int
    decoder_id: str
    decoder_version: str


class Detection(StrictModel):
    id: str
    class_id: int = Field(ge=0)
    label: str
    score: float = Field(ge=0, le=1, allow_inf_nan=False)
    bbox_xyxy: tuple[float, float, float, float]


class PerceptionImage(StrictModel):
    sha256: str
    size_bytes: int
    width: int
    height: int
    download_url: str
    capture_time_ns: str | None
    frame_id: str | None


class PerceptionFrame(StrictModel):
    selection_index: int
    status: Literal["not_attempted", "running", "completed", "failed"]
    error: str | None = None
    input: PerceptionInput
    image: PerceptionImage | None = None
    detections: list[Detection] = Field(default_factory=list)
    runtime: dict | None = None
    output_hash: str | None = None
    created_at: str | None = None
    started_at: str | None = None


class PerceptionCounts(StrictModel):
    total: int
    running: int
    completed: int
    failed: int
    not_attempted: int


class PerceptionJobSummary(StrictModel):
    id: str
    submission_seq: int
    label: str
    model_id: str
    model_revision: int
    model_name: str
    runtime_id: str
    runtime_version: str
    status: JobStatus
    created_at: str
    updated_at: str
    started_at: str | None = None
    dispatched_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    input_hash: str
    worker_id: str | None = None
    lease_expires_at: str | None = None
    cancel_requested_at: str | None = None
    cancellation_supported: bool
    outcome_uncertain: bool
    counts: PerceptionCounts


class PerceptionJobDetail(PerceptionJobSummary):
    request: PerceptionJobCreate
    model_snapshot: dict
    recording_snapshot: dict
    inputs: list[PerceptionInput]
    frames: list[PerceptionFrame]
    events: list[JobEvent]
    events_total: int
    manifest_hash: str | None = None
    manifest_size_bytes: int | None = None
    download_url: str | None = None


class PerceptionJobPage(StrictModel):
    items: list[PerceptionJobSummary]
    next_before_seq: int | None = None


class PerceptionComparisonCreate(StrictModel):
    name: str = Field(min_length=1, max_length=180)
    note: str = Field(min_length=1, max_length=4000)
    left_job_id: str = Field(min_length=1, max_length=64)
    right_job_id: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def nonblank(self):
        if not self.name.strip() or not self.note.strip():
            raise ValueError("Name and note must be nonempty")
        return self


class PerceptionConfigurationDifference(StrictModel):
    field: str
    left: object
    right: object


ComparisonRowStatus = Literal["same_input", "input_changed", "left_only", "right_only", "not_evaluated"]


class PerceptionComparisonRow(StrictModel):
    key: str
    status: ComparisonRowStatus
    left: PerceptionFrame | None
    right: PerceptionFrame | None
    detection_count_delta: int | None = None
    predictions_identical: bool | None = None


class PerceptionComparisonCounts(StrictModel):
    total: int
    same_input: int
    input_changed: int
    left_only: int
    right_only: int
    not_evaluated: int


class PerceptionComparisonSummary(StrictModel):
    id: str
    name: str
    note: str
    created_at: str
    left_job_id: str
    right_job_id: str
    left_manifest_hash: str
    right_manifest_hash: str
    policy_version: str
    counts: PerceptionComparisonCounts
    manifest_hash: str
    manifest_size_bytes: int
    download_url: str


class PerceptionComparisonDetail(PerceptionComparisonSummary):
    configuration_differences: list[PerceptionConfigurationDifference]
    rows: list[PerceptionComparisonRow]
