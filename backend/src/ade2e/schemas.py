"""Public application contract. SI units; not an ASAM compliance schema."""

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator, model_serializer


from .base_schemas import Name, StrictModel
from .scenario_schemas import (Category, Weather, TimeOfDay, Operator, MetricName, ScenarioParameters, ParameterOverrides, ScenarioCreate, ScenarioPatch, Scenario)
from .simulation_context import AnySimulationControlSelection, AnyPreparedSimulationContext


class Metric(StrictModel):
    name: MetricName
    value: float | None
    unit: str
    threshold: float
    operator: Operator
    passed: bool


class EgoState(StrictModel):
    x: float
    y: float
    yaw: float
    speed_mps: float = Field(ge=0)


class Actor(EgoState):
    id: str
    type: str


class Frame(StrictModel):
    time_s: float = Field(ge=0)
    ego: EgoState
    actors: list[Actor]
    ttc_s: float | None = Field(ge=0)
    acceleration_mps2: float


class Event(StrictModel):
    time_s: float
    type: str
    message: str


class Provenance(StrictModel):
    input_hash: str
    output_hash: str
    seed: int
    adapter_version: str
    model_fidelity: str
    schema_version: str = "0.1"


class Artifact(StrictModel):
    id: str
    run_id: str
    name: str
    kind: str
    media_type: str
    size_bytes: int
    sha256: str
    created_at: str
    download_url: str
    source: str


class RecoveryLineage(StrictModel):
    verification_version: Literal['remote-recovery/1'] = 'remote-recovery/1'
    recovery_id: Name
    source_job_id: Name
    source_run_id: Name | None
    source_execution_id: Name
    source_submission_seq: int = Field(ge=1, strict=True)
    store_id: Name
    request_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    archive_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    archive_kind: Literal['complete', 'partial']
    recovered_at: Name


class RunSummary(StrictModel):
    id: str
    scenario_id: str
    scenario_name: str
    scenario_revision: int
    adapter_id: str
    status: Literal["completed", "error"]
    verdict: Literal["passed", "failed", "error"]
    created_at: str
    duration_s: float
    execution_ms: float
    metrics: list[Metric]
    provenance: Provenance
    error: str | None = None
    recovery: RecoveryLineage | None = None

    @model_serializer(mode='wrap')
    def preserve_legacy_summary(self, handler):
        value = handler(self)
        if self.recovery is None:
            value.pop('recovery', None)
        return value




class RunDetail(RunSummary):
    scenario_snapshot: Scenario
    trajectory: list[Frame]
    events: list[Event]
    artifacts: list[Artifact]
    execution_context: AnyPreparedSimulationContext | None = None

    @model_serializer(mode='wrap')
    def preserve_legacy_payload(self, handler):
        value = handler(self)
        if self.execution_context is None:
            value.pop('execution_context', None)
        if self.recovery is None:
            value.pop('recovery', None)
        return value


class RunCreate(StrictModel):
    scenario_id: Name
    adapter_id: Name = "reference"
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    parameter_overrides: ParameterOverrides = Field(default_factory=ParameterOverrides)
    control: AnySimulationControlSelection | None = None

    @model_serializer(mode='wrap')
    def preserve_legacy_request(self, handler):
        value = handler(self)
        if self.control is None:
            value.pop('control', None)
        return value


class RequirementCreate(StrictModel):
    name: Name
    description: str = Field(default="", max_length=4000)
    metric_name: MetricName
    operator: Operator
    threshold: float
    category: Category | None = None
    scenario_ids: list[Name] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def unique_scenarios(self):
        if len(self.scenario_ids) != len(set(self.scenario_ids)):
            raise ValueError("scenario_ids must be unique")
        return self


class Requirement(RequirementCreate):
    id: str
    status: Literal["passed", "failed", "untested"]
    evidence_count: int
    last_run_id: str | None
    linked_scenario_count: int = 0
    evidence_scope: str = "Latest baseline run of the current scenario revision; reference evidence is not sensor validation"


class Adapter(StrictModel):
    id: str
    name: str
    version: str
    kind: str
    status: Literal["available", "not_configured", "planned"]
    description: str
    capabilities: list[str]
    limitations: list[str]
    license: str
    source_url: str


class ArtifactPayload(StrictModel):
    """Adapter-produced bytes; core assigns a safe managed path and digest."""

    name: Name
    kind: Name
    media_type: Name
    content: bytes = Field(max_length=20 * 1024 * 1024)


class AdapterFailureEvidence(StrictModel):
    """Bounded diagnostics from an attempted execution, never a passed result."""

    artifact_payloads: list[ArtifactPayload] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def bounded_diagnostics(self):
        if sum(len(item.content) for item in self.artifact_payloads) > 32 * 1024 * 1024:
            raise ValueError("Failed execution diagnostic artifacts exceed 32 MiB")
        return self


class SimulationResult(StrictModel):
    trajectory: list[Frame]
    events: list[Event]
    metrics: list[Metric]
    model_fidelity: str
    artifact_payloads: list[ArtifactPayload] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def ordered_frames(self):
        if not self.trajectory:
            raise ValueError("A successful adapter result must include trajectory frames")
        times = [frame.time_s for frame in self.trajectory]
        if times[0] != 0 or any(b <= a for a, b in zip(times, times[1:])):
            raise ValueError("Trajectory must start at zero with strictly increasing timestamps")
        if len({metric.name for metric in self.metrics}) != len(self.metrics):
            raise ValueError("Metric names must be unique")
        return self
