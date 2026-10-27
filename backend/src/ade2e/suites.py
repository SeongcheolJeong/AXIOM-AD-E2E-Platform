"""Pinned test definitions evaluated against specific durable execution jobs."""

import hashlib
import math
from collections import Counter
from typing import Annotated

from fastapi import Header, HTTPException, Query, Response
from pydantic import ValidationError
from sqlalchemy import select

from . import jobs
from .adapters import Registry
from .database import Database, RequirementRow, RevisionRow, RunRow
from .job_models import JobRow
from .reference import compare_metric
from .schemas import Adapter, RequirementCreate, RunCreate, RunDetail, Scenario
from .service import (canonical_bytes, completed_run_integrity_error, digest, prepare_run_snapshot,
    resolve_simulation_control, run_request_hash, PreparedRun, uid, utcnow)
from .simulation_context import (build_simulation_context, simulation_input_hash, PreparedSimulationContextV2,
    validate_simulation_context, validate_simulation_adapter_context)
from .suite_models import BaselineRow, CampaignMemberRow, CampaignRow, SuiteRevisionRow, SuiteRow
from .suite_schemas import (BaselineCreate, BaselineDetail, BaselineManifest, BaselineSummary, CampaignCaseReport,
                            CampaignComparison, CampaignCounts, CampaignCreate, CampaignDetail, CampaignPage, CampaignSummary,
                            CampaignExecutionPlan, CampaignMemberExpectation,
                            ComparisonCounts, ComparisonRow, CriterionCheck, ExecutionContext, MetricDelta, RequirementSnapshot,
                            SuiteCaseSnapshot, SuiteCreate, SuiteDetail, SuiteRevisionCreate, SuiteSummary)

EVALUATOR_VERSION = "suite-evaluator/1"
COMPARISON_VERSION = "suite-comparison/1"
CONTACT_COUNT_CONTRACT = 'carla-observed-contact-count/1'
CONTACT_COUNT_ADAPTER_VERSIONS = frozenset({'0.8.0'})
METRIC_UNITS = {"collision_count": "count", "min_ttc_s": "s", "max_deceleration_mps2": "m/s²",
                "max_jerk_mps3": "m/s³", "distance_m": "m"}
MAX_SUITE_BYTES = 8 * 1024 * 1024
MAX_BASELINE_BYTES = 16 * 1024 * 1024
CATEGORIES = {"cut_in", "emergency_brake", "pedestrian_crossing", "lane_follow"}


def _key(key: str) -> None:
    if not isinstance(key, str) or not key or len(key) > 200:
        raise HTTPException(422, "Idempotency-Key must contain 1..200 characters")


def _same_request(existing_hash: str, requested_hash: str) -> None:
    if existing_hash != requested_hash:
        raise HTTPException(409, "Idempotency-Key was already used for a different request")


def _suite_hash(payload: dict) -> str:
    return digest({key: payload[key] for key in ("name", "description", "evaluator_version", "cases")})


def suite_get(session, suite_id: str, revision: int | None = None) -> SuiteDetail:
    suite = session.get(SuiteRow, suite_id)
    if suite is None:
        raise HTTPException(404, "Test suite not found")
    row = session.get(SuiteRevisionRow, (suite_id, revision if revision is not None else suite.revision))
    if row is None:
        raise HTTPException(404, "Test suite revision not found")
    try:
        detail = SuiteDetail.model_validate(row.payload)
        if (row.definition_hash != _suite_hash(row.payload) or detail.definition_hash != row.definition_hash
                or detail.id != row.suite_id or detail.revision != row.revision or detail.case_count != len(detail.cases)
                or detail.created_at != suite.created_at):
            raise ValueError("Stored suite revision metadata mismatch")
        return detail
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(409, "Stored suite revision failed its integrity check") from exc


def _definition(session, body: SuiteCreate, suite_id: str, revision: int, created_at: str, now: str) -> SuiteDetail:
    cases = []
    size = 0
    for definition in body.cases:
        row = session.get(RevisionRow, (definition.scenario_id, definition.scenario_revision))
        if row is None:
            raise HTTPException(422, f"Case {definition.key}: scenario revision not found")
        if (digest(row.payload) != row.input_hash or row.payload.get("id") != definition.scenario_id
                or row.payload.get("revision") != definition.scenario_revision):
            raise HTTPException(409, f"Case {definition.key}: scenario revision integrity check failed")
        scenario_payload = dict(row.payload)
        scenario_payload["parameters"] = {**scenario_payload["parameters"], **definition.parameter_overrides.model_dump(exclude_unset=True)}
        if definition.seed is not None:
            scenario_payload["seed"] = definition.seed
        try:
            scenario = Scenario.model_validate(scenario_payload)
        except ValidationError as exc:
            raise HTTPException(422, f"Case {definition.key}: invalid effective scenario: {exc.errors(include_context=False)}") from exc
        requirements = []
        for requirement_id in definition.requirement_ids:
            required = session.get(RequirementRow, requirement_id)
            if required is None:
                raise HTTPException(422, f"Case {definition.key}: requirement {requirement_id} not found")
            criterion = RequirementCreate.model_validate(required.payload)
            applies = (scenario.id in criterion.scenario_ids if criterion.scenario_ids else
                       criterion.category is not None and criterion.category == scenario.category)
            if not applies:
                raise HTTPException(422, f"Case {definition.key}: requirement {requirement_id} does not apply to the selected scenario revision")
            requirements.append(RequirementSnapshot(id=requirement_id, definition=criterion,
                                                    definition_hash=digest(criterion.model_dump(mode="json")), unit=METRIC_UNITS[criterion.metric_name]))
        criteria_hash = digest([item.model_dump(mode="json") for item in sorted(requirements, key=lambda item: item.id)])
        case = SuiteCaseSnapshot(**definition.model_dump(mode="json"), scenario_snapshot=scenario,
                                 effective_input_hash=digest(scenario.model_dump(mode="json")), requirements=requirements, criteria_hash=criteria_hash)
        size += len(canonical_bytes(case.model_dump(mode="json")))
        if size > MAX_SUITE_BYTES:
            raise HTTPException(413, "Pinned suite definition exceeds the 8 MiB limit")
        cases.append(case)
    payload = {"id": suite_id, "name": body.name, "description": body.description, "revision": revision, "case_count": len(cases),
               "created_at": created_at, "updated_at": now, "evaluator_version": EVALUATOR_VERSION,
               "cases": [case.model_dump(mode="json") for case in cases]}
    return SuiteDetail(**payload, definition_hash=_suite_hash(payload))


def create_suite(db: Database, body: SuiteCreate) -> SuiteDetail:
    with jobs._write(db) as session:
        return create_suite_in_session(session, body)


def create_suite_in_session(session, body: SuiteCreate):
    """Compose inside one caller-owned transaction; never commit independently."""
    now, suite_id = utcnow(), uid("suite")
    detail = _definition(session, body, suite_id, 1, now, now)
    session.add(SuiteRow(id=suite_id, revision=1, created_at=now, updated_at=now))
    session.flush()
    session.add(SuiteRevisionRow(suite_id=suite_id, revision=1, definition_hash=detail.definition_hash, payload=detail.model_dump(mode="json")))
    return detail


def revise_suite(db: Database, suite_id: str, body: SuiteRevisionCreate) -> SuiteDetail:
    with jobs._write(db) as session:
        current = suite_get(session, suite_id)
        if current.revision != body.expected_revision:
            raise HTTPException(409, "Suite revision changed; reload before creating a new revision")
        detail = _definition(session, SuiteCreate.model_validate(body.model_dump(mode="json", exclude={"expected_revision"})),
                             suite_id, current.revision + 1, current.created_at, utcnow())
        row = session.get(SuiteRow, suite_id)
        row.revision, row.updated_at = detail.revision, detail.updated_at
        session.add(SuiteRevisionRow(suite_id=suite_id, revision=detail.revision, definition_hash=detail.definition_hash, payload=detail.model_dump(mode="json")))
        return detail


def create_campaign(db: Database, registry: Registry, body: CampaignCreate, key: str) -> CampaignDetail:
    with jobs._write(db) as session:
        return create_campaign_in_session(session, registry, body, key)


def create_campaign_in_session(session, registry: Registry, body: CampaignCreate, key: str):
    """Compose inside one caller-owned transaction; never commit independently."""
    _key(key)
    request_hash = digest(body.model_dump(mode="json"))
    existing = session.scalar(select(CampaignRow).where(CampaignRow.idempotency_key == key))
    if existing is not None:
        _same_request(existing.request_hash, request_hash)
        return campaign_report(session, existing.id)
    suite = suite_get(session, body.suite_id, body.suite_revision)
    prepared_cases = []
    metadata = None
    if body.control is not None and body.adapter_id != 'carla':
        raise HTTPException(422, 'Campaign stack control requires the shared carla adapter/resource')
    snapshots = resolve_simulation_control(session, body.control) if body.control is not None else None
    for case in suite.cases:
        request = RunCreate(scenario_id=case.scenario_id, adapter_id=body.adapter_id, seed=case.seed,
                            parameter_overrides=case.parameter_overrides, control=body.control)
        try:
            context = (build_simulation_context(**snapshots, selection=body.control, seed=case.scenario_snapshot.seed)
                       if snapshots is not None else None)
            prepared = prepare_run_snapshot(case.scenario_snapshot, registry, request, execution_context=context)
        except HTTPException as error:
            raise HTTPException(error.status_code, f'Case {case.key}: {error.detail}') from error
        except (ValueError, TypeError, KeyError) as error:
            raise HTTPException(422, f'Case {case.key}: {error}') from error
        selected = prepared.adapter_metadata
        known_categories = set(selected.capabilities) & CATEGORIES
        if known_categories and case.scenario_snapshot.category not in known_categories:
            raise HTTPException(422, f"Case {case.key}: adapter does not declare support for {case.scenario_snapshot.category}")
        if metadata is not None and selected != metadata:
            raise HTTPException(409, "Adapter metadata changed during campaign preparation")
        metadata = selected
        prepared_cases.append((case, request, prepared))
    campaign_id = uid("campaign")
    row = CampaignRow(id=campaign_id, idempotency_key=key, request_hash=request_hash, suite_id=suite.id, suite_revision=suite.revision,
                      label=body.label, created_at=utcnow(), suite_snapshot=suite.model_dump(mode="json"), adapter_snapshot=metadata.model_dump(mode="json"))
    session.add(row)
    session.flush()
    expected_members = []
    for case, request, prepared in prepared_cases:
        job = jobs.insert_prepared_job(session, registry, request, prepared, f"campaign:{campaign_id}:{case.key}")
        session.add(CampaignMemberRow(campaign_id=campaign_id, case_key=case.key, job_id=job.id))
        if body.control is not None:
            expected_members.append(CampaignMemberExpectation(key=case.key, job_id=job.id,
                input_hash=prepared.input_hash, context_hash=prepared.execution_context.context_hash,
                request_hash=prepared.request_hash))
    if body.control is not None:
        value = {'schema_version':'campaign-execution-plan/1', 'control':body.control.model_dump(mode='json'),
            'context_template':prepared_cases[0][2].execution_context.model_dump(mode='json'),
            'metric_contract_version':CONTACT_COUNT_CONTRACT,
            'members':[item.model_dump(mode='json') for item in expected_members]}
        plan = CampaignExecutionPlan(**value, plan_hash=digest(value))
        row.execution_plan = plan.model_dump(mode='json')
    session.flush()
    return campaign_report(session, campaign_id)


def _campaign_row(session, campaign_id: str) -> CampaignRow:
    row = session.scalar(select(CampaignRow).where(CampaignRow.id == campaign_id))
    if row is None:
        raise HTTPException(404, "Test campaign not found")
    return row


def _execution_plan(row, suite, adapter):
    """Verify the campaign's own immutable intent without consulting member jobs.

    A valid job payload is not the authority for what this campaign selected.
    Every seed-specific context is rederived from this shared pinned template.
    Current asset heads or adapter plugins are never read here.
    """
    if row.execution_plan is None:
        legacy = CampaignCreate(suite_id=row.suite_id, suite_revision=row.suite_revision,
            adapter_id=adapter.id, label=row.label)
        if row.request_hash != digest(legacy.model_dump(mode="json")):
            raise HTTPException(409, "Campaign execution plan is missing or differs from its original request")
        return None, {}, {}
    try:
        plan = CampaignExecutionPlan.model_validate(row.execution_plan)
        if plan.plan_hash != digest(plan.model_dump(mode='json', exclude={'plan_hash'})):
            raise ValueError('Campaign execution plan hash mismatch')
        original = CampaignCreate(suite_id=row.suite_id, suite_revision=row.suite_revision,
            adapter_id=adapter.id, label=row.label, control=plan.control)
        if row.request_hash != digest(original.model_dump(mode='json')):
            raise ValueError('Campaign control selection differs from the original request')
        if adapter.id != 'carla' or [item.key for item in plan.members] != [case.key for case in suite.cases]:
            raise ValueError('Campaign execution plan differs from the full suite membership')
        template = plan.context_template
        expected, contexts = {}, {}
        for case, member in zip(suite.cases, plan.members):
            context = build_simulation_context(template.stack_snapshot, template.rig_snapshot, plan.control,
                case.scenario_snapshot.seed,
                sensor_configuration=template.sensor_configuration_snapshot if isinstance(template, PreparedSimulationContextV2) else None)
            validate_simulation_adapter_context(context, adapter.version)
            validate_simulation_context(case.scenario_snapshot, context, verify_components=False)
            request = RunCreate(scenario_id=case.scenario_id, adapter_id=adapter.id, seed=case.seed,
                parameter_overrides=case.parameter_overrides, control=plan.control)
            if (member.context_hash != context.context_hash
                    or member.input_hash != simulation_input_hash(case.scenario_snapshot, adapter.id, adapter.version, context)
                    or member.request_hash != run_request_hash(request)):
                raise ValueError('Campaign member hashes differ from the independently pinned execution')
            expected[case.key], contexts[case.key] = member, context
        if template != contexts[suite.cases[0].key]:
            raise ValueError('Campaign context template must describe the first pinned case')
        return plan, expected, contexts
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(409, 'Campaign execution plan failed its integrity check') from error


def _member_input_error(case, adapter, job, expected, expected_context):
    if expected is None:
        return None
    try:
        prepared = PreparedRun.from_payload(job.prepared)
        request = RunCreate.model_validate(job.request)
        if (job.id != expected.job_id or job.kind != 'simulation' or job.request_hash != expected.request_hash
                or job.input_hash != expected.input_hash or prepared.request_hash != expected.request_hash
                or run_request_hash(request) != expected.request_hash or prepared.input_hash != expected.input_hash
                or prepared.execution_context != expected_context or prepared.adapter_metadata != adapter
                or prepared.scenario != case.scenario_snapshot
                or (job.adapter_id, job.adapter_version, job.scenario_id, job.scenario_revision) !=
                   (adapter.id, adapter.version, case.scenario_id, case.scenario_revision)):
            return "Campaign member job differs from its independently pinned execution plan"
    except (ValueError, TypeError, KeyError):
        return "Campaign member prepared input failed its integrity check"
    return None


def _case_report(case: SuiteCaseSnapshot, suite: SuiteDetail, adapter: Adapter, job: JobRow | None, run: RunDetail | None, *, expected=None, expected_context=None, metric_contract=None) -> CampaignCaseReport:
    job_status = job.status if job is not None else "missing"
    reason = None
    pending = job_status in ("queued", *jobs.ACTIVE)
    context = ExecutionContext(adapter_id=adapter.id, adapter_version=adapter.version)
    metrics = []
    if job is None:
        reason = "Campaign member job is missing"
    elif (member_error := _member_input_error(case, adapter, job, expected, expected_context)) is not None:
        reason = member_error
    elif pending:
        reason = f"Execution is {job.status}"
    elif job.status != "completed" and (run is None or run.recovery is None):
        reason = job.error or f"Execution ended as {job.status} without completed validation evidence"
    elif run is None:
        job_status, reason = "missing", "Completed job has no available run evidence"
    elif run.status != "completed":
        reason = "Run did not complete successfully"
    elif expected is None and run.execution_context is not None:
        reason = "This campaign revision does not pin a stack control selection"
    elif expected is not None and (run.execution_context != expected_context or run.provenance.input_hash != expected.input_hash):
        reason = "Run control context or input differs from the campaign's independent execution plan"
    elif (run.scenario_snapshot.model_dump(mode="json") != case.scenario_snapshot.model_dump(mode="json")
          or run.provenance.input_hash != job.input_hash or run.adapter_id != adapter.id
          or run.provenance.adapter_version != adapter.version):
        reason = "Run evidence does not match this campaign's pinned execution"
    elif (integrity_error := completed_run_integrity_error(run)) is not None:
        reason = integrity_error
    if run is not None:
        metrics = run.metrics
        context = ExecutionContext(adapter_id=run.adapter_id, adapter_version=run.provenance.adapter_version,
                                   model_fidelity=run.provenance.model_fidelity, result_schema_version=run.provenance.schema_version,
                                   input_hash=run.provenance.input_hash, output_hash=run.provenance.output_hash,
                                   control_context_hash=run.execution_context.context_hash if run.execution_context else None)
    if suite.evaluator_version != EVALUATOR_VERSION:
        reason = "Pinned evaluator version is not supported by this runtime"
    checks = []
    for criterion in case.requirements:
        definition = criterion.definition
        candidates = [metric for metric in metrics if metric.name == definition.metric_name]
        metric = candidates[0] if len(candidates) == 1 else None
        check_reason = reason
        if check_reason is None and metric is None:
            check_reason = "Required metric is missing or duplicated"
        measurement_contract = None
        if (check_reason is None and metric_contract is not None and definition.metric_name == 'collision_count'
                and (metric.value is None or metric.value < 0 or not math.isfinite(metric.value) or not float(metric.value).is_integer())):
            check_reason = 'Observed contact count requires a finite nonnegative integer'
        if check_reason is None and metric.unit != criterion.unit:
            # A count of observed distinct contact actors remains an observation,
            # not proof of collision-stream completeness. Preserve its raw unit.
            if (metric_contract == CONTACT_COUNT_CONTRACT and adapter.id == 'carla'
                    and adapter.version in CONTACT_COUNT_ADAPTER_VERSIONS
                    and definition.metric_name == 'collision_count'
                    and metric.unit == 'observed contacts' and criterion.unit == 'count'):
                measurement_contract = CONTACT_COUNT_CONTRACT
            else:
                check_reason = f"Metric unit {metric.unit!r} differs from pinned unit {criterion.unit!r}"
        if check_reason is None and metric.value is None and definition.metric_name != "min_ttc_s":
            check_reason = "Required metric has no evaluated value"
        status = "not_evaluated" if check_reason else ("passed" if compare_metric(metric.value, definition.operator, definition.threshold, name=definition.metric_name) else "failed")
        checks.append(CriterionCheck(requirement_id=criterion.id, name=definition.name, metric_name=definition.metric_name,
                                     operator=definition.operator, threshold=definition.threshold, expected_unit=criterion.unit,
                                     status=status, value=metric.value if metric else None, unit=metric.unit if metric else None,
                                     unbounded=metric is not None and metric.value is None and definition.metric_name == "min_ttc_s" and check_reason is None,
                                     reason=check_reason, measurement_contract=measurement_contract))
    complete = all(check.status != "not_evaluated" for check in checks)
    verdict = "failed" if any(check.status == "failed" for check in checks) else "passed" if complete else "pending" if pending else "not_evaluated"
    return CampaignCaseReport(key=case.key, name=case.name, scenario_id=case.scenario_id, scenario_revision=case.scenario_revision,
                              effective_input_hash=case.effective_input_hash, criteria_hash=case.criteria_hash, evaluator_version=suite.evaluator_version,
                              job_id=job.id if job else None, run_id=run.id if run else job.run_id if job else None, job_status=job_status,
                              verdict=verdict, evaluation_complete=complete, checks=checks, metrics=metrics, execution_context=context, reason=reason,
                              recovery=run.recovery.model_dump(mode='json') if run and run.recovery else None, expected_execution=expected)


def campaign_report(session, campaign_id: str) -> CampaignDetail:
    row = _campaign_row(session, campaign_id)
    try:
        suite = SuiteDetail.model_validate(row.suite_snapshot)
        if (_suite_hash(row.suite_snapshot) != suite.definition_hash or suite.id != row.suite_id
                or suite.revision != row.suite_revision or suite.case_count != len(suite.cases)):
            raise ValueError("Campaign suite metadata mismatch")
    except (ValidationError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(409, "Campaign suite snapshot failed its integrity check") from exc
    adapter = Adapter.model_validate(row.adapter_snapshot)
    plan, expected, contexts = _execution_plan(row, suite, adapter)
    members = dict(session.execute(select(CampaignMemberRow.case_key, JobRow).outerjoin(JobRow, JobRow.id == CampaignMemberRow.job_id)
                                   .where(CampaignMemberRow.campaign_id == campaign_id)).all())
    if plan is not None and set(members) - set(expected):
        raise HTTPException(409, "Campaign has members outside its pinned execution plan")
    reports = []
    for case in suite.cases:
        job = members.get(case.key)
        from .recoveries import resolved_job_run
        run = resolved_job_run(session, job) if job else None
        if run is None and job and job.run_id:
            run_row = session.get(RunRow, job.run_id)
            run = RunDetail.model_validate(run_row.payload) if run_row else None
        reports.append(_case_report(case, suite, adapter, job, run, expected=expected.get(case.key), expected_context=contexts.get(case.key),
            metric_contract=plan.metric_contract_version if plan is not None else None))
    states = Counter(report.job_status for report in reports)
    checks = Counter(check.status for report in reports for check in report.checks)
    pending = sum(states[state] for state in ("queued", *jobs.ACTIVE))
    counts = CampaignCounts(total=len(reports), pending=pending, completed=states["completed"], failed=states["failed"],
                            cancelled=states["cancelled"], interrupted=states["interrupted"], missing=states["missing"],
                            passed_cases=sum(report.verdict == "passed" for report in reports), failed_cases=sum(report.verdict == "failed" for report in reports),
                            not_evaluated_cases=sum(report.verdict in {"pending", "not_evaluated"} for report in reports),
                            checks_total=sum(len(report.checks) for report in reports), checks_passed=checks["passed"],
                            checks_failed=checks["failed"], checks_not_evaluated=checks["not_evaluated"])
    status = "finished" if pending == 0 else "queued" if states["queued"] == len(reports) else "running"
    verdict = "failed" if counts.checks_failed else "passed" if counts.passed_cases == counts.total else "pending" if pending else "incomplete"
    return CampaignDetail(id=row.id, submission_seq=row.submission_seq, label=row.label, suite_id=suite.id, suite_revision=suite.revision,
                          suite_name=suite.name, adapter_id=adapter.id, adapter_version=adapter.version, created_at=row.created_at,
                          status=status, verdict=verdict, evaluation_complete=counts.checks_not_evaluated == 0, counts=counts,
                          evaluated_at=utcnow(), evaluator_version=suite.evaluator_version, suite_snapshot=suite, adapter_snapshot=adapter, cases=reports, execution_plan=plan)


def cancel_campaign(db: Database, campaign_id: str) -> CampaignDetail:
    with jobs._write(db) as session:
        report = campaign_report(session, campaign_id)
        if report.execution_plan is not None:
            expected = {item.key: item.job_id for item in report.execution_plan.members}
            actual = session.execute(select(CampaignMemberRow.case_key, CampaignMemberRow.job_id)
                .where(CampaignMemberRow.campaign_id == campaign_id)).all()
            if any(expected.get(key) != job_id for key, job_id in actual):
                raise HTTPException(409, 'Campaign cancellation member differs from the independent execution plan')
        now = utcnow()
        jobs._expire(session, now)
        members = session.scalars(select(JobRow).join(CampaignMemberRow, CampaignMemberRow.job_id == JobRow.id)
                                  .where(CampaignMemberRow.campaign_id == campaign_id))
        for member in members:
            jobs.cancel_job_row(session, member, now=now)
        session.flush()
        return campaign_report(session, campaign_id)


def _baseline_row(session, baseline_id: str) -> BaselineRow:
    row = session.get(BaselineRow, baseline_id)
    if row is None:
        raise HTTPException(404, "Baseline not found")
    if len(row.manifest) != row.manifest_size_bytes or hashlib.sha256(row.manifest).hexdigest() != row.manifest_hash:
        raise HTTPException(409, "Baseline manifest failed its integrity check")
    return row


def baseline_get(session, baseline_id: str) -> BaselineDetail:
    row = _baseline_row(session, baseline_id)
    try:
        manifest = BaselineManifest.model_validate_json(row.manifest)
        report = manifest.report
        expected = BaselineSummary(id=manifest.baseline_id, name=manifest.name, note=manifest.note, campaign_id=report.id,
                                   suite_id=report.suite_id, suite_revision=report.suite_revision, created_at=manifest.captured_at,
                                   manifest_hash=row.manifest_hash, manifest_size_bytes=row.manifest_size_bytes, total_cases=report.counts.total,
                                   verdict=report.verdict, download_url=f"/api/v1/baselines/{manifest.baseline_id}/export")
        if (row.summary != expected.model_dump(mode="json") or row.id != manifest.baseline_id
                or report.id != row.campaign_id or report.suite_id != row.suite_id or row.created_at != manifest.captured_at
                or report.status != "finished" or canonical_bytes(manifest.model_dump(mode="json")) != row.manifest):
            raise ValueError("Baseline metadata mismatch")
        return BaselineDetail(**expected.model_dump(), manifest=manifest)
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(409, "Baseline metadata does not match its sealed manifest") from exc


def create_baseline(db: Database, body: BaselineCreate, key: str) -> BaselineDetail:
    with jobs._write(db) as session:
        return create_baseline_in_session(session, body, key)


def create_baseline_in_session(session, body: BaselineCreate, key: str):
    """Compose inside one caller-owned transaction; never commit independently."""
    _key(key)
    request_hash = digest(body.model_dump(mode="json"))
    existing = session.scalar(select(BaselineRow).where(BaselineRow.idempotency_key == key))
    if existing is not None:
        _same_request(existing.request_hash, request_hash)
        return baseline_get(session, existing.id)
    report = campaign_report(session, body.campaign_id)
    if report.status != "finished":
        raise HTTPException(409, "Only a finished campaign can be captured as a baseline")
    baseline_id, now = uid("baseline"), utcnow()
    manifest = BaselineManifest(baseline_id=baseline_id, name=body.name, note=body.note, captured_at=now, report=report)
    content = canonical_bytes(manifest.model_dump(mode="json"))
    if len(content) > MAX_BASELINE_BYTES:
        raise HTTPException(413, "Baseline manifest exceeds the 16 MiB limit")
    summary = BaselineSummary(id=baseline_id, name=body.name, note=body.note, campaign_id=report.id, suite_id=report.suite_id,
                              suite_revision=report.suite_revision, created_at=now, manifest_hash=hashlib.sha256(content).hexdigest(),
                              manifest_size_bytes=len(content), total_cases=report.counts.total, verdict=report.verdict,
                              download_url=f"/api/v1/baselines/{baseline_id}/export")
    session.add(BaselineRow(id=baseline_id, idempotency_key=key, request_hash=request_hash, campaign_id=report.id, suite_id=report.suite_id,
                            created_at=now, manifest_hash=summary.manifest_hash, manifest_size_bytes=len(content), manifest=content,
                            summary=summary.model_dump(mode="json")))
    session.flush()
    return baseline_get(session, baseline_id)


def compare_reports(current: CampaignDetail, baseline: BaselineDetail) -> CampaignComparison:
    previous = baseline.manifest.report
    if previous.suite_id != current.suite_id:
        raise HTTPException(422, "Campaign and baseline must belong to the same suite")
    old_cases = {case.key: case for case in previous.cases}
    new_cases = {case.key: case for case in current.cases}
    rows = []
    for key in sorted(old_cases.keys() | new_cases.keys()):
        old, new = old_cases.get(key), new_cases.get(key)
        reasons, deltas = [], []
        version_changed = old is not None and new is not None and old.execution_context.adapter_version != new.execution_context.adapter_version
        if old is None:
            status, reasons = "added", ["Case key is absent from the baseline"]
        elif new is None:
            status, reasons = "removed", ["Case key is absent from the current suite revision"]
        elif not old.evaluation_complete or not new.evaluation_complete:
            status, reasons = "not_evaluated", ["Both cases require every pinned criterion to be evaluated"]
        else:
            old_contract = previous.execution_plan.metric_contract_version if previous.execution_plan else None
            new_contract = current.execution_plan.metric_contract_version if current.execution_plan else None
            if (old_contract != new_contract or
                    {c.requirement_id: c.measurement_contract for c in old.checks} !=
                    {c.requirement_id: c.measurement_contract for c in new.checks}):
                reasons.append('Pinned metric measurement contract changed')
            for field, label in (("effective_input_hash", "Effective scenario inputs or revision changed"), ("criteria_hash", "Pinned requirement definitions changed"),
                                 ("evaluator_version", "Evaluator contract version changed")):
                if getattr(old, field) != getattr(new, field):
                    reasons.append(label)
            for field, label in (("adapter_id", "Adapter identity changed"), ("adapter_version", "Adapter version changed without an independent measurement contract"),
                                 ("model_fidelity", "Result fidelity changed"), ("result_schema_version", "Result schema version changed"),
                                 ("control_context_hash", "Pinned control/stack/rig context changed")):
                if getattr(old.execution_context, field) != getattr(new.execution_context, field):
                    reasons.append(label)
            old_metrics, new_metrics = {metric.name: metric for metric in old.metrics}, {metric.name: metric for metric in new.metrics}
            if {name: metric.unit for name, metric in old_metrics.items()} != {name: metric.unit for name, metric in new_metrics.items()}:
                reasons.append("Metric names or units changed")
            status = "not_comparable" if reasons else ("regressed" if old.verdict == "passed" and new.verdict == "failed" else
                       "improved" if old.verdict == "failed" and new.verdict == "passed" else
                       "unchanged_pass" if new.verdict == "passed" else "unchanged_fail")
            if not reasons:
                for name in sorted(old_metrics.keys() & new_metrics.keys()):
                    left, right = old_metrics[name], new_metrics[name]
                    delta = right.value - left.value if left.value is not None and right.value is not None and math.isfinite(left.value) and math.isfinite(right.value) else None
                    if delta is not None and not math.isfinite(delta):
                        delta = None
                    deltas.append(MetricDelta(metric_name=name, unit=left.unit, baseline_value=left.value, current_value=right.value, delta=delta))
        rows.append(ComparisonRow(key=key, name=(new or old).name, status=status, reasons=reasons,
                                  baseline_verdict=old.verdict if old else None, current_verdict=new.verdict if new else None,
                                  baseline_job_id=old.job_id if old else None, current_job_id=new.job_id if new else None,
                                  baseline_run_id=old.run_id if old else None, current_run_id=new.run_id if new else None,
                                  baseline_adapter_version=old.execution_context.adapter_version if old else None,
                                  current_adapter_version=new.execution_context.adapter_version if new else None,
                                  adapter_version_changed=version_changed, metrics=deltas))
    counts = Counter(row.status for row in rows)
    return CampaignComparison(campaign_id=current.id, baseline_id=baseline.id, baseline_manifest_hash=baseline.manifest_hash,
                              suite_id=current.suite_id, compared_at=utcnow(), counts=ComparisonCounts(total=len(rows), **{name: counts[name] for name in ComparisonCounts.model_fields if name != "total"}), rows=rows)


def compare_campaign(session, campaign_id: str, baseline_id: str) -> CampaignComparison:
    return compare_reports(campaign_report(session, campaign_id), baseline_get(session, baseline_id))


def register_suite_routes(api, db: Database, registry: Registry) -> None:
    @api.post("/suites", response_model=SuiteDetail, status_code=201, tags=["suites"])
    def post_suite(body: SuiteCreate):
        return create_suite(db, body)

    @api.get("/suites", response_model=list[SuiteSummary], tags=["suites"])
    def list_suites():
        with db.read_session() as session:
            return [SuiteSummary.model_validate(suite_get(session, row.id).model_dump(exclude={"cases"}))
                    for row in session.scalars(select(SuiteRow).order_by(SuiteRow.updated_at.desc(), SuiteRow.id))]

    @api.get("/suites/{suite_id}", response_model=SuiteDetail, tags=["suites"])
    def get_suite(suite_id: str):
        with db.read_session() as session:
            return suite_get(session, suite_id)

    @api.post("/suites/{suite_id}/revisions", response_model=SuiteDetail, status_code=201, tags=["suites"])
    def post_revision(suite_id: str, body: SuiteRevisionCreate):
        return revise_suite(db, suite_id, body)

    @api.get("/suites/{suite_id}/revisions", response_model=list[SuiteDetail], tags=["suites"])
    def get_revisions(suite_id: str):
        with db.read_session() as session:
            current = suite_get(session, suite_id)
            return [suite_get(session, suite_id, revision) for revision in range(current.revision, 0, -1)]

    @api.post("/campaigns", response_model=CampaignDetail, status_code=202, tags=["campaigns"])
    def post_campaign(body: CampaignCreate, idempotency_key: Annotated[str, Header(min_length=1, max_length=200)]):
        return create_campaign(db, registry, body, idempotency_key)

    @api.get("/campaigns", response_model=CampaignPage, tags=["campaigns"])
    def list_campaigns(limit: int = Query(100, ge=1, le=100), before_seq: int | None = Query(None, ge=1), suite_id: str | None = None):
        with db.read_session() as session:
            query = select(CampaignRow).order_by(CampaignRow.submission_seq.desc())
            if before_seq is not None:
                query = query.where(CampaignRow.submission_seq < before_seq)
            if suite_id is not None:
                query = query.where(CampaignRow.suite_id == suite_id)
            rows = list(session.scalars(query.limit(limit + 1)))
            reports = [campaign_report(session, row.id) for row in rows[:limit]]
            return CampaignPage(items=[CampaignSummary.model_validate({name: getattr(report, name) for name in CampaignSummary.model_fields}) for report in reports],
                                next_before_seq=rows[limit - 1].submission_seq if len(rows) > limit else None)

    @api.get("/campaigns/{campaign_id}", response_model=CampaignDetail, tags=["campaigns"])
    def get_campaign(campaign_id: str):
        with db.read_session() as session:
            return campaign_report(session, campaign_id)

    @api.post("/campaigns/{campaign_id}/cancel", response_model=CampaignDetail, tags=["campaigns"])
    def post_cancel_campaign(campaign_id: str):
        return cancel_campaign(db, campaign_id)

    @api.get("/campaigns/{campaign_id}/export", tags=["campaigns"])
    def export_campaign(campaign_id: str):
        with db.read_session() as session:
            return Response(canonical_bytes(campaign_report(session, campaign_id).model_dump(mode="json")), media_type="application/json",
                            headers={"Content-Disposition": f'attachment; filename="{campaign_id}.campaign.json"'})

    @api.post("/baselines", response_model=BaselineDetail, status_code=201, tags=["baselines"])
    def post_baseline(body: BaselineCreate, idempotency_key: Annotated[str, Header(min_length=1, max_length=200)]):
        return create_baseline(db, body, idempotency_key)

    @api.get("/baselines", response_model=list[BaselineSummary], tags=["baselines"])
    def list_baselines(suite_id: str | None = None):
        with db.read_session() as session:
            query = select(BaselineRow).order_by(BaselineRow.created_at.desc(), BaselineRow.id)
            if suite_id is not None:
                query = query.where(BaselineRow.suite_id == suite_id)
            return [BaselineSummary.model_validate(baseline_get(session, row.id).model_dump(exclude={"manifest"})) for row in session.scalars(query)]

    @api.get("/baselines/{baseline_id}", response_model=BaselineDetail, tags=["baselines"])
    def get_baseline(baseline_id: str):
        with db.read_session() as session:
            return baseline_get(session, baseline_id)

    @api.get("/baselines/{baseline_id}/export", tags=["baselines"])
    def export_baseline(baseline_id: str):
        with db.read_session() as session:
            baseline_get(session, baseline_id)
            return Response(_baseline_row(session, baseline_id).manifest, media_type="application/json",
                            headers={"Content-Disposition": f'attachment; filename="{baseline_id}.baseline.json"'})

    @api.get("/campaigns/{campaign_id}/compare", response_model=CampaignComparison, tags=["campaigns"])
    def get_comparison(campaign_id: str, baseline_id: str):
        with db.read_session() as session:
            return compare_campaign(session, campaign_id, baseline_id)
