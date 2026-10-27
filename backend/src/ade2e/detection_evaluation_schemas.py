"""Dataset evaluation jobs retain the full selected-image denominator."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_serializer

from .job_schemas import JobEvent, JobStatus
from .schemas import StrictModel
from .evaluator_runtime import DiagnosticIoU, EvaluationInput, EvaluationReport

Nonblank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
Split = Literal["train", "validation", "test", "unspecified"]


class ClassMapping(StrictModel):
    model_label: Nonblank
    dataset_label: Nonblank | None


class EvaluationSelection(StrictModel):
    """Explicit image/class cohort; original labels and split stay pinned."""
    policy_version: Literal['image_class_cohort_v1'] = 'image_class_cohort_v1'
    image_ids: list[Nonblank] = Field(min_length=1, max_length=256)
    labels: list[Nonblank] = Field(min_length=1, max_length=256)

    @field_validator('image_ids', 'labels')
    @classmethod
    def unique_values(cls, values):
        if len(set(values)) != len(values):
            raise ValueError('Cohort selections must be unique')
        return values


class DetectionEvaluationCreate(StrictModel):
    name: Nonblank
    note: str = Field(min_length=1, max_length=4000)
    dataset_id: Nonblank
    dataset_revision: int = Field(ge=1, strict=True)
    split: Split
    inference_job_ids: list[Nonblank] = Field(min_length=1, max_length=16)
    evaluator_id: Nonblank = "coco-bbox"
    class_mapping: list[ClassMapping] = Field(min_length=1, max_length=256)
    score_threshold: float = Field(default=0.0, ge=0, le=1, allow_inf_nan=False)
    diagnostic_iou: DiagnosticIoU = 0.5
    selection: EvaluationSelection | None = None

    @model_serializer(mode='wrap')
    def preserve_legacy_request(self, handler):
        value = handler(self)
        if self.selection is None:
            value.pop('selection', None)
        return value

    @field_validator("note")
    @classmethod
    def nonblank_note(cls, value):
        if not value.strip():
            raise ValueError("Evaluation note must be nonempty")
        return value.strip()

    @field_validator("inference_job_ids")
    @classmethod
    def unique_jobs(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("Source inference jobs must be unique")
        return values

    @field_validator("class_mapping")
    @classmethod
    def unique_mapping(cls, values):
        if len({item.model_label for item in values}) != len(values):
            raise ValueError("Each source model label may appear exactly once")
        return values


class DetectionEvaluationSummary(StrictModel):
    id: str
    submission_seq: int
    status: JobStatus
    created_at: str
    updated_at: str
    started_at: str | None = None
    dispatched_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    worker_id: str | None = None
    lease_expires_at: str | None = None
    cancel_requested_at: str | None = None
    cancellation_supported: bool
    outcome_uncertain: bool
    input_hash: str
    name: str
    dataset_id: str
    dataset_revision: int
    dataset_name: str
    evaluator_id: str
    evaluator_version: str


class DetectionEvaluationDetail(DetectionEvaluationSummary):
    request: DetectionEvaluationCreate
    dataset_snapshot: dict
    source_jobs: list[dict]
    evaluator_snapshot: dict
    evaluation_input: EvaluationInput
    source_context: dict
    report: EvaluationReport | None = None
    events: list[JobEvent]
    events_total: int
    manifest_hash: str | None = None
    manifest_size_bytes: int | None = None
    download_url: str | None = None


class DetectionEvaluationPage(StrictModel):
    items: list[DetectionEvaluationSummary]
    next_before_seq: int | None = None
