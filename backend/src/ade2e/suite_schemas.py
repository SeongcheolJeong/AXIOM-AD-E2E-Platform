"""Versioned suite definitions and evidence reports; lifecycle is not verdict."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_serializer, model_validator, model_serializer

from .job_schemas import JobStatus
from .simulation_context import AnySimulationControlSelection, AnyPreparedSimulationContext
from .model_schemas import Sha256
from .schemas import Adapter, Metric, MetricName, Name, Operator, ParameterOverrides, RequirementCreate, Scenario, StrictModel

CaseKey = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]
CaseVerdict = Literal["pending", "passed", "failed", "not_evaluated"]
CampaignVerdict = Literal["pending", "passed", "failed", "incomplete"]
ComparisonStatus = Literal["added", "removed", "not_evaluated", "not_comparable", "regressed", "improved", "unchanged_pass", "unchanged_fail"]


class SuiteCaseCreate(StrictModel):
    key: CaseKey
    name: Name
    scenario_id: Name
    scenario_revision: int = Field(ge=1)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    parameter_overrides: ParameterOverrides = Field(default_factory=ParameterOverrides)
    requirement_ids: list[Name] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique_requirements(self):
        if len(set(self.requirement_ids)) != len(self.requirement_ids):
            raise ValueError("Case requirement_ids must be unique")
        return self

    @field_serializer("parameter_overrides")
    def serialize_overrides(self, value: ParameterOverrides) -> dict:
        return value.model_dump(mode="json", exclude_none=True)


class SuiteCreate(StrictModel):
    name: Name
    description: str = Field(default="", max_length=4000)
    cases: list[SuiteCaseCreate] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_keys(self):
        if len({case.key for case in self.cases}) != len(self.cases):
            raise ValueError("Suite case keys must be unique")
        return self


class SuiteRevisionCreate(SuiteCreate):
    expected_revision: int = Field(ge=1)


class RequirementSnapshot(StrictModel):
    id: str
    definition: RequirementCreate
    definition_hash: str
    unit: str


class SuiteCaseSnapshot(SuiteCaseCreate):
    scenario_snapshot: Scenario
    effective_input_hash: str
    requirements: list[RequirementSnapshot]
    criteria_hash: str


class SuiteSummary(StrictModel):
    id: str
    name: str
    description: str
    revision: int
    case_count: int
    created_at: str
    updated_at: str
    definition_hash: str
    evaluator_version: str


class SuiteDetail(SuiteSummary):
    cases: list[SuiteCaseSnapshot]


class CampaignCreate(StrictModel):
    suite_id: Name
    suite_revision: int = Field(ge=1)
    adapter_id: Name = "reference"
    label: Name
    control: AnySimulationControlSelection | None = None

    @model_serializer(mode='wrap')
    def preserve_legacy_request(self, handler):
        value = handler(self)
        if self.control is None:
            value.pop('control', None)
        return value


class CampaignMemberExpectation(StrictModel):
    key: CaseKey
    job_id: Name
    input_hash: Sha256
    context_hash: Sha256
    request_hash: Sha256


class CampaignExecutionPlan(StrictModel):
    schema_version: Literal['campaign-execution-plan/1'] = 'campaign-execution-plan/1'
    control: AnySimulationControlSelection
    context_template: AnyPreparedSimulationContext
    metric_contract_version: Literal['carla-observed-contact-count/1']
    members: list[CampaignMemberExpectation] = Field(min_length=1, max_length=100)
    plan_hash: Sha256

    @model_validator(mode='after')
    def unique_members(self):
        if len({item.key for item in self.members})!=len(self.members) or len({item.job_id for item in self.members})!=len(self.members):
            raise ValueError('Campaign execution plan requires unique case keys and job IDs')
        return self


class CampaignCounts(StrictModel):
    total: int
    pending: int
    completed: int
    failed: int
    cancelled: int
    interrupted: int
    missing: int
    passed_cases: int
    failed_cases: int
    not_evaluated_cases: int
    checks_total: int
    checks_passed: int
    checks_failed: int
    checks_not_evaluated: int


class CriterionCheck(StrictModel):
    requirement_id: str
    name: str
    metric_name: MetricName
    operator: Operator
    threshold: float
    expected_unit: str
    status: Literal["passed", "failed", "not_evaluated"]
    value: float | None = None
    unit: str | None = None
    unbounded: bool = False
    reason: str | None = None
    measurement_contract: Literal['carla-observed-contact-count/1'] | None = None

    @model_serializer(mode='wrap')
    def preserve_legacy_check(self, handler):
        value = handler(self)
        if self.measurement_contract is None:
            value.pop('measurement_contract', None)
        return value


class ExecutionContext(StrictModel):
    adapter_id: str
    adapter_version: str
    model_fidelity: str | None = None
    result_schema_version: str | None = None
    input_hash: str | None = None
    output_hash: str | None = None
    control_context_hash: str | None = None

    @model_serializer(mode='wrap')
    def preserve_historical_manifest(self, handler):
        value = handler(self)
        if self.control_context_hash is None:
            value.pop('control_context_hash', None)
        return value


class CampaignCaseReport(StrictModel):
    key: str
    name: str
    scenario_id: str
    scenario_revision: int
    effective_input_hash: str
    criteria_hash: str
    evaluator_version: str
    job_id: str | None
    run_id: str | None
    job_status: JobStatus | Literal["missing"]
    verdict: CaseVerdict
    evaluation_complete: bool
    checks: list[CriterionCheck]
    metrics: list[Metric]
    execution_context: ExecutionContext
    reason: str | None = None
    recovery: dict | None = None
    expected_execution: CampaignMemberExpectation | None = None

    @model_serializer(mode='wrap')
    def preserve_legacy_case(self, handler):
        value = handler(self)
        if self.recovery is None:
            value.pop('recovery', None)
        if self.expected_execution is None:
            value.pop('expected_execution', None)
        return value


class CampaignSummary(StrictModel):
    id: str
    submission_seq: int
    label: str
    suite_id: str
    suite_revision: int
    suite_name: str
    adapter_id: str
    adapter_version: str
    created_at: str
    status: Literal["queued", "running", "finished"]
    verdict: CampaignVerdict
    evaluation_complete: bool
    counts: CampaignCounts


class CampaignDetail(CampaignSummary):
    evaluated_at: str
    evaluator_version: str
    suite_snapshot: SuiteDetail
    adapter_snapshot: Adapter
    cases: list[CampaignCaseReport]
    execution_plan: CampaignExecutionPlan | None = None

    @model_serializer(mode='wrap')
    def preserve_legacy_report(self, handler):
        value = handler(self)
        if self.execution_plan is None:
            value.pop('execution_plan', None)
        return value


class CampaignPage(StrictModel):
    items: list[CampaignSummary]
    next_before_seq: int | None


class BaselineCreate(StrictModel):
    campaign_id: Name
    name: Name
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class BaselineSummary(StrictModel):
    id: str
    name: str
    note: str
    campaign_id: str
    suite_id: str
    suite_revision: int
    created_at: str
    manifest_hash: str
    manifest_size_bytes: int
    total_cases: int
    verdict: CampaignVerdict
    download_url: str


class BaselineManifest(StrictModel):
    schema_version: str = "1"
    baseline_id: str
    name: str
    note: str
    captured_at: str
    report: CampaignDetail


class BaselineDetail(BaselineSummary):
    manifest: BaselineManifest


class MetricDelta(StrictModel):
    metric_name: MetricName
    unit: str | None
    baseline_value: float | None
    current_value: float | None
    delta: float | None


class ComparisonRow(StrictModel):
    key: str
    name: str
    status: ComparisonStatus
    reasons: list[str]
    baseline_verdict: CaseVerdict | None
    current_verdict: CaseVerdict | None
    baseline_job_id: str | None
    current_job_id: str | None
    baseline_run_id: str | None
    current_run_id: str | None
    baseline_adapter_version: str | None
    current_adapter_version: str | None
    adapter_version_changed: bool
    metrics: list[MetricDelta]


class ComparisonCounts(StrictModel):
    total: int
    added: int
    removed: int
    not_evaluated: int
    not_comparable: int
    regressed: int
    improved: int
    unchanged_pass: int
    unchanged_fail: int


class CampaignComparison(StrictModel):
    campaign_id: str
    baseline_id: str
    baseline_manifest_hash: str
    suite_id: str
    compared_at: str
    counts: ComparisonCounts
    rows: list[ComparisonRow]
