"""Persistent authoring, adapter execution and derived validation evidence."""

import hashlib
import json
import logging
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .adapters import AdapterCancelled, AdapterCancelledBeforeDispatch, AdapterExecutionError, AdapterInputError, AdapterOwnershipLost, AdapterUnavailable, Registry, SimulationAdapter, supports_cancellation
from .artifact_transactions import track_created_artifact
from .database import ArtifactRow, Database, IdempotencyRow, RequirementLinkRow, RequirementRow, RevisionRow, RunRow, ScenarioRow
from .reference import compare_metric
from .schemas import Adapter, AdapterFailureEvidence, Artifact, ArtifactPayload, Event, Provenance, Requirement, RequirementCreate, RunCreate, RunDetail, RunSummary, Scenario, ScenarioCreate, ScenarioParameters, ScenarioPatch, SimulationResult
from .simulation_context import (PreparedSimulationContext, PreparedSimulationContextV2, AnyPreparedSimulationContext,
                                 SimulationControlSelectionV2, validate_prepared_context, make_simulation_execution_request,
                                 build_simulation_context,
                                 simulation_input_hash, validate_simulation_context, validate_simulation_adapter_context)

logger = logging.getLogger(__name__)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def run_request_hash(body: RunCreate) -> str:
    payload = body.model_dump(mode='json')
    if body.control is None:
        payload.pop('control', None)
    return digest(payload)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def validated(cls, payload):
    try:
        return cls.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(422, detail=exc.errors(include_context=False)) from exc


def scenario_get(session: Session, scenario_id: str) -> Scenario:
    row = session.get(ScenarioRow, scenario_id)
    if not row:
        raise HTTPException(404, "Scenario not found")
    return Scenario.model_validate(row.payload)


def scenario_create(session: Session, body: ScenarioCreate) -> Scenario:
    now = utcnow()
    scenario = Scenario(**body.model_dump(), id=uid("scn"), revision=1, created_at=now, updated_at=now)
    payload = scenario.model_dump(mode="json")
    session.add(ScenarioRow(id=scenario.id, category=scenario.category, payload=payload))
    session.flush()
    session.add(RevisionRow(scenario_id=scenario.id, revision=1, input_hash=digest(payload), payload=payload))
    session.flush()
    return scenario


def scenario_patch(session: Session, scenario_id: str, body: ScenarioPatch) -> Scenario:
    current = scenario_get(session, scenario_id)
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        return current
    payload = current.model_dump()
    if "parameters" in patch:
        payload["parameters"].update(patch.pop("parameters"))
    payload.update(patch)
    payload.update(revision=current.revision + 1, updated_at=utcnow())
    scenario = validated(Scenario, payload)
    row = session.get(ScenarioRow, scenario_id)
    row.payload, row.category = scenario.model_dump(mode="json"), scenario.category
    session.add(RevisionRow(scenario_id=scenario_id, revision=scenario.revision, input_hash=digest(row.payload), payload=row.payload))
    session.flush()
    return scenario


def run_get(session: Session, run_id: str) -> RunDetail:
    row = session.get(RunRow, run_id)
    if not row:
        raise HTTPException(404, "Run not found")
    run = RunDetail.model_validate(row.payload)
    if run.recovery is not None:
        from .recoveries import verify_recovered_run
        verify_recovered_run(session, run)
    return run


def run_summaries(session: Session) -> list[RunSummary]:
    result = []
    for row in session.scalars(select(RunRow).order_by(RunRow.created_at.desc(), RunRow.id.desc())):
        payload = run_get(session, row.id).model_dump(mode='json') if row.payload.get('recovery') else row.payload
        result.append(RunSummary.model_validate({k: v for k, v in payload.items() if k in RunSummary.model_fields}))
    return result


def completed_run_integrity_error(run: RunDetail) -> str | None:
    """Verify recorded result content, not the separate artifact file bytes."""
    if run.status != "completed":
        return "Run is not completed"
    if (not run.artifacts or run.artifacts[-1].kind != "evidence_manifest"
            or run.artifacts[-1].name != f"{run.id}.evidence.json"
            or run.artifacts[-1].media_type != "application/json"
            or any(artifact.run_id != run.id for artifact in run.artifacts)):
        return "Run evidence manifest metadata is missing or inconsistent"
    if simulation_input_hash(run.scenario_snapshot, run.adapter_id, run.provenance.adapter_version, run.execution_context) != run.provenance.input_hash:
        return "Run input content failed its integrity check"
    output = {"trajectory": [frame.model_dump(mode="json") for frame in run.trajectory],
              "events": [event.model_dump(mode="json") for event in run.events],
              "metrics": [metric.model_dump(mode="json") for metric in run.metrics],
              "model_fidelity": run.provenance.model_fidelity,
              "artifact_hashes": [artifact.sha256 for artifact in run.artifacts[:-1]]}
    return "Run output content failed its integrity check" if digest(output) != run.provenance.output_hash else None


@dataclass(frozen=True)
class PreparedRun:
    """Pinned inputs that can outlive the transaction that read the scenario.

    JSON bytes make nested data immutable. Model properties return independent
    copies, including the copy supplied to an installed adapter. Durable jobs
    persist to_payload() and reconstruct with from_payload().
    """

    scenario_json: bytes
    adapter_metadata_json: bytes
    request_hash: str
    execution_context_json: bytes | None = None

    @property
    def scenario(self) -> Scenario:
        return Scenario.model_validate_json(self.scenario_json)

    @property
    def adapter_metadata(self) -> Adapter:
        return Adapter.model_validate_json(self.adapter_metadata_json)

    @property
    def input_hash(self) -> str:
        metadata = self.adapter_metadata
        return simulation_input_hash(self.scenario, metadata.id, metadata.version, self.execution_context)

    @property
    def execution_context(self) -> PreparedSimulationContext | None:
        return validate_prepared_context(self.execution_context_json) if self.execution_context_json is not None else None

    def to_payload(self) -> dict:
        value = {"scenario": json.loads(self.scenario_json), "adapter_metadata": json.loads(self.adapter_metadata_json),
                 "request_hash": self.request_hash}
        if self.execution_context_json is not None:
            value.update(schema_version='prepared-simulation/3' if isinstance(self.execution_context, PreparedSimulationContextV2) else 'prepared-simulation/2', execution_context=json.loads(self.execution_context_json))
        return value

    @classmethod
    def from_payload(cls, payload: dict) -> "PreparedRun":
        legacy_fields = {"scenario", "adapter_metadata", "request_hash"}
        context = None
        if set(payload) == legacy_fields | {'schema_version', 'execution_context'} and payload['schema_version'] in {'prepared-simulation/2', 'prepared-simulation/3'}:
            context = validate_prepared_context(payload['execution_context'])
            if (payload['schema_version'] == 'prepared-simulation/3') != isinstance(context, PreparedSimulationContextV2):
                raise ValueError('Prepared simulation envelope differs from its context version')
        elif set(payload) != legacy_fields:
            raise ValueError("Prepared run has an unsupported envelope")
        scenario = Scenario.model_validate(payload["scenario"])
        metadata = Adapter.model_validate(payload["adapter_metadata"])
        request_hash = payload["request_hash"]
        if not isinstance(request_hash, str) or len(request_hash) != 64 or any(c not in "0123456789abcdef" for c in request_hash):
            raise ValueError("Prepared run request_hash must be a SHA-256 hex digest")
        if context is not None:
            if metadata.id != 'carla':
                raise ValueError('Stack execution must retain the carla resource identity')
            validate_simulation_adapter_context(context, metadata.version)
            validate_simulation_context(scenario, context, verify_components=False)
        return cls(canonical_bytes(scenario.model_dump(mode="json")), canonical_bytes(metadata.model_dump(mode="json")), request_hash,
                   canonical_bytes(context.model_dump(mode='json')) if context is not None else None)


@dataclass(frozen=True)
class ComputedRun:
    """Detached execution result; publishing copies it before adding artifacts."""

    run: RunDetail
    artifact_payloads: tuple[ArtifactPayload, ...]


@dataclass(frozen=True)
class StagedArtifact:
    artifact: Artifact
    storage_key: str


@dataclass(frozen=True)
class StagedRun:
    """Files owned by one staging attempt, prior to transactional promotion."""

    directory: Path
    run: RunDetail
    artifacts: tuple[StagedArtifact, ...]
    input_hash: str
    request_hash: str
    run_payload: dict


def _new_artifact(run_id: str, timestamp: str, payload: ArtifactPayload, source: str) -> StagedArtifact:
    artifact_id = uid("art")
    # Never derive a filesystem location from an adapter or API-supplied name.
    storage_key = f"{artifact_id}.blob"
    sha = hashlib.sha256(payload.content).hexdigest()
    artifact = Artifact(id=artifact_id, run_id=run_id, name=payload.name, kind=payload.kind, media_type=payload.media_type,
                        size_bytes=len(payload.content), sha256=sha, created_at=timestamp,
                        download_url=f"/api/v1/artifacts/{artifact_id}/download", source=source)
    return StagedArtifact(artifact, storage_key)


def _artifact_write(db: Database, session: Session, run_id: str, timestamp: str, payload: ArtifactPayload, source: str, written: list[Path]) -> Artifact:
    item = _new_artifact(run_id, timestamp, payload, source)
    artifact = item.artifact
    path = db.artifact_dir / item.storage_key
    stream = path.open("xb")
    # Register immediately after successful exclusive creation. A partial
    # write (or even context entry failure) must not leave an unowned file.
    written.append(path)
    track_created_artifact(session, path)
    with stream:
        if stream.write(payload.content) != len(payload.content):
            raise OSError("Incomplete artifact write")
    session.add(ArtifactRow(id=artifact.id, run_id=run_id, storage_key=item.storage_key, payload=artifact.model_dump(mode="json")))
    return artifact


def _idempotent_run(session: Session, request_hash: str, key: str | None) -> RunDetail | None:
    if key:
        existing = session.get(IdempotencyRow, key)
        if existing:
            if existing.request_hash != request_hash:
                raise HTTPException(409, "Idempotency-Key was already used for a different request")
            return run_get(session, existing.run_id)
    return None


def resolve_simulation_control(session: Session, selection) -> dict:
    """Resolve all immutable selected assets within the caller's one snapshot.

    This performs database reads only. Adapters never receive a session here.
    A campaign resolves once and derives each member context with its own seed.
    """
    from .stacks import get_stack
    from .sensors import rig_get
    from .simulation_sensor_configurations import get_sensor_configuration
    return {'stack':get_stack(session, selection.stack_id, selection.stack_revision),
        'rig':rig_get(session, selection.rig_id, selection.rig_revision),
        'sensor_configuration':get_sensor_configuration(session, selection.sensor_configuration_id,
            selection.sensor_configuration_revision) if isinstance(selection, SimulationControlSelectionV2) else None}


def prepare_run(session: Session, registry: Registry, body: RunCreate) -> PreparedRun:
    """Resolve and validate effective inputs without executing an engine."""
    context = None
    scenario = scenario_get(session, body.scenario_id)
    if body.control is not None:
        if body.adapter_id != 'carla':
            raise HTTPException(422, 'Stack control is supported only on the shared carla adapter/resource')
        try:
            context = build_simulation_context(**resolve_simulation_control(session, body.control),
                selection=body.control, seed=body.seed if body.seed is not None else scenario.seed)
        except (ValueError, TypeError, KeyError) as error:
            raise HTTPException(422, str(error)) from error
    return prepare_run_snapshot(scenario, registry, body, execution_context=context)


def prepare_run_snapshot(scenario_snapshot: Scenario, registry: Registry, body: RunCreate, *, execution_context: PreparedSimulationContext | None = None) -> PreparedRun:
    """Prepare a fixed revision without reading today's mutable scenario row."""
    if scenario_snapshot.id != body.scenario_id:
        raise HTTPException(422, "Scenario snapshot does not match the requested scenario")
    snapshot = scenario_snapshot.model_dump()
    snapshot["parameters"].update(body.parameter_overrides.model_dump(exclude_unset=True))
    if body.seed is not None:
        snapshot["seed"] = body.seed
    scenario = validated(Scenario, snapshot)
    adapter = registry.get(body.adapter_id)
    if adapter is None:
        raise HTTPException(422, f"Unknown adapter: {body.adapter_id}")
    metadata = Adapter.model_validate(adapter.metadata())
    if metadata.status != "available":
        raise HTTPException(422, f"Adapter {body.adapter_id} is {metadata.status}: {metadata.description}")
    if (body.control is None) != (execution_context is None):
        raise HTTPException(422, 'Pinned simulation context is required exactly when stack control is selected')
    if execution_context is not None:
        try:
            from .adapters import supports_execution_context
            if metadata.id != 'carla' or not supports_execution_context(adapter):
                raise ValueError('Selected adapter does not implement the stack execution context contract')
            validate_simulation_adapter_context(execution_context, metadata.version)
            expected = build_simulation_context(execution_context.stack_snapshot, execution_context.rig_snapshot, body.control, scenario.seed,
                sensor_configuration=execution_context.sensor_configuration_snapshot if isinstance(execution_context, PreparedSimulationContextV2) else None)
            if expected != execution_context:
                raise ValueError('Prepared simulation context differs from its explicit selection')
            validate_simulation_context(scenario, execution_context)
            validate_adapter_sensor_capability(adapter, execution_context)
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(422, str(error)) from error
    return PreparedRun(canonical_bytes(scenario.model_dump(mode="json")), canonical_bytes(metadata.model_dump(mode="json")),
                       run_request_hash(body), canonical_bytes(execution_context.model_dump(mode='json')) if execution_context else None)


def validate_adapter_sensor_capability(adapter, context):
    if not isinstance(context, PreparedSimulationContextV2):
        return
    from .simulation_sensor_schemas import SensorExecutionCapability
    hook = getattr(adapter, 'execution_context_capabilities', None)
    if not callable(hook):
        raise ValueError('Adapter does not declare the configurable sensor capability')
    values = hook()
    if not isinstance(values, list) or len(values)>32:
        raise ValueError('Adapter sensor capability metadata must be a bounded list')
    metadata = [SensorExecutionCapability.model_validate(item) for item in values]
    identities = [(item.id,item.version) for item in metadata]
    if len(identities)!=len(set(identities)) or context.sensor_capability_snapshot not in metadata:
        raise ValueError('Adapter sensor capability metadata differs from the pinned contract')


def validate_prepared_execution(prepared: PreparedRun, adapter: SimulationAdapter, *, stack_registry=None) -> None:
    """Read-only optional preflight, outside DB transactions and before dispatch."""
    context = prepared.execution_context
    if context is None:
        return
    from .adapters import supports_execution_context
    try:
        current = Adapter.model_validate(adapter.metadata())
        if current.id != 'carla' or current.id != prepared.adapter_metadata.id or current.version != prepared.adapter_metadata.version or current.status != 'available':
            raise ValueError('Pinned CARLA adapter identity/version is unavailable')
        if not supports_execution_context(adapter):
            raise ValueError('Adapter does not support explicit simulation execution context')
        validate_simulation_adapter_context(context, current.version)
        validate_simulation_context(prepared.scenario, context, stack_registry=stack_registry)
        validate_adapter_sensor_capability(adapter, context)
        adapter.validate_execution_context(prepared.scenario, context)
    except (AdapterInputError, AdapterUnavailable, ValueError, TypeError, KeyError) as error:
        raise HTTPException(422, str(error)) from error


def compute_run(prepared: PreparedRun, adapter: SimulationAdapter, *, run_id: str | None = None,
                created_at: str | None = None, should_cancel: Callable[[], bool] | None = None,
                expected_store_id: str | None = None) -> ComputedRun:
    """Execute and check the adapter contract without a database or filesystem.

    Workers close their preparation transaction before calling this function.
    Reserved IDs/timestamps preserve dispatch ordering if completion is delayed.
    A cancellation exception propagates; adapters without a cooperative hook
    finish normally and their result is not discarded by a late cancellation.
    """
    scenario, metadata = prepared.scenario, prepared.adapter_metadata
    current = Adapter.model_validate(adapter.metadata())
    if (current.id, current.version) != (metadata.id, metadata.version):
        raise HTTPException(422, "Adapter identity or version changed after run preparation")
    if current.status != "available":
        raise HTTPException(422, f"Adapter {metadata.id} is {current.status}: {current.description}")
    context = prepared.execution_context
    if context is not None:
        from .adapters import supports_execution_context
        try:
            if metadata.id != 'carla' or not supports_execution_context(adapter):
                raise ValueError('Stack execution requires the explicit shared CARLA context hook')
            validate_simulation_adapter_context(context, metadata.version)
            validate_simulation_context(scenario, context)
            validate_adapter_sensor_capability(adapter, context)
        except (ValueError, TypeError, KeyError) as failure:
            raise HTTPException(422, str(failure)) from failure
    if should_cancel is not None and should_cancel():
        raise AdapterCancelledBeforeDispatch("Execution cancelled before dispatch")
    started = time.perf_counter()
    timestamp = created_at if created_at is not None else utcnow()
    run_id = run_id if run_id is not None else uid("run")
    error = None
    execution_request = None if context is None else make_simulation_execution_request(execution_id=run_id,
        input_hash=prepared.input_hash, scenario=prepared.scenario, context=prepared.execution_context)
    try:
        # A plugin may mutate its argument; hashes and evidence always describe
        # the prepared input, not that plugin's temporary working state.
        adapter_input = prepared.scenario
        from .execution_attempt import supports_execution_attempt, supports_remote_recovery, RemoteExecutionStatus
        if supports_execution_attempt(adapter):
            attempt = execution_attempt_request(prepared, run_id)
            if supports_remote_recovery(adapter) and expected_store_id is None:
                observed = RemoteExecutionStatus.model_validate(adapter.inspect_execution(attempt).model_dump())
                observed.validate_request(attempt)
                expected_store_id = observed.store_id
            raw_result = adapter.execute_attempt(attempt.model_copy(deep=True), expected_store_id=expected_store_id)
        elif context is not None:
            raw_result = adapter.execute_with_context(execution_request.model_copy(deep=True))
        elif should_cancel is not None and supports_cancellation(adapter):
            raw_result = adapter.execute_cancellable(adapter_input, should_cancel)
        else:
            raw_result = adapter.execute(adapter_input)
        # Revalidate nested values even when a plugin mutated a previously
        # valid Pydantic instance; model_validate(instance) can reuse them.
        result = SimulationResult.model_validate(raw_result.model_dump() if isinstance(raw_result, SimulationResult) else raw_result)
        if abs(result.trajectory[-1].time_s - scenario.duration_s) > 0.001:
            raise ValueError("Adapter trajectory does not cover the complete requested duration")
        required_metrics = {"collision_count", "min_ttc_s", "max_deceleration_mps2", "max_jerk_mps3", "distance_m"}
        if {metric.name for metric in result.metrics} != required_metrics:
            raise ValueError("Adapter must return all five contract metrics")
        for metric in result.metrics:
            if metric.value is None and metric.name != "min_ttc_s":
                raise ValueError("Only unbounded TTC may have a null metric value")
            if metric.passed != compare_metric(metric.value, metric.operator, metric.threshold, name=metric.name):
                raise ValueError("Adapter metric verdict contradicts its predicate")
        if execution_request is not None:
            from .simulation_evidence import validate_simulation_artifacts
            validate_simulation_artifacts(execution_request, result.artifact_payloads, completed=True,
                                          trajectory=result.trajectory, adapter_version=metadata.version)
        result_data = result.model_dump(mode="json", exclude={"artifact_payloads"})
        output_payload = {**result_data, "artifact_hashes": [hashlib.sha256(item.content).hexdigest() for item in result.artifact_payloads]}
        status = "completed"
        verdict = "passed" if all(metric.passed for metric in result.metrics) else "failed"
        metrics, trajectory, events, fidelity = result.metrics, result.trajectory, result.events, result.model_fidelity
        adapter_artifacts = result.artifact_payloads
    except (AdapterCancelled, AdapterOwnershipLost):
        raise
    except (AdapterInputError, AdapterUnavailable) as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as failure:
        logger.exception("Adapter %s failed for run %s", metadata.id, run_id)
        error = "Adapter execution failed. Inspect the server log using this run ID."
        status, verdict, metrics, trajectory, fidelity, adapter_artifacts = "error", "error", [], [], "execution failed; no validation evidence", []
        events = [Event(time_s=0, type="execution_error", message=error)]
        if isinstance(failure, AdapterExecutionError):
            try:
                evidence = AdapterFailureEvidence.model_validate({"artifact_payloads": [
                    item.model_dump() if isinstance(item, ArtifactPayload) else item for item in failure.artifact_payloads
                ]})
                if execution_request is not None:
                    from .simulation_evidence import validate_simulation_artifacts
                    validate_simulation_artifacts(execution_request, evidence.artifact_payloads, completed=False,
                                                  adapter_version=metadata.version)
                adapter_artifacts = evidence.artifact_payloads
            except (ValueError, TypeError, AttributeError, KeyError, IndexError, RecursionError):
                # Preserve the failed attempt even when its optional diagnostics
                # violate the contract; never publish unchecked bytes or metrics.
                events.append(Event(time_s=0, type="diagnostic_evidence_rejected",
                                    message="Failed-execution diagnostics did not satisfy the artifact contract."))
            if adapter_artifacts:
                fidelity = "execution failed; diagnostic artifacts only; no validation evidence"
                events.append(Event(time_s=0, type="diagnostic_evidence",
                                    message=f"Preserved {len(adapter_artifacts)} diagnostic artifact(s) from the failed attempt."))
        output_payload = {"error": error, "events": [item.model_dump() for item in events]}
        if adapter_artifacts:
            output_payload["artifact_hashes"] = [hashlib.sha256(item.content).hexdigest() for item in adapter_artifacts]
    run = RunDetail(
        id=run_id, scenario_id=scenario.id, scenario_name=scenario.name, scenario_revision=scenario.revision, adapter_id=metadata.id,
        status=status, verdict=verdict, created_at=timestamp, duration_s=scenario.duration_s,
        execution_ms=round((time.perf_counter() - started) * 1000, 3), metrics=metrics,
        provenance=Provenance(input_hash=prepared.input_hash,
                              output_hash=digest(output_payload), seed=scenario.seed, adapter_version=metadata.version, model_fidelity=fidelity),
        error=error, scenario_snapshot=scenario, trajectory=trajectory, events=events, artifacts=[], execution_context=prepared.execution_context,
    )
    return ComputedRun(run, tuple(adapter_artifacts))


def execution_attempt_request(prepared: PreparedRun, execution_id: str):
    from .execution_attempt import ExecutionAttemptRequest
    metadata = prepared.adapter_metadata
    return ExecutionAttemptRequest(schema_version='execution-attempt/2' if isinstance(prepared.execution_context, PreparedSimulationContextV2) else 'execution-attempt/1',
        execution_id=execution_id, input_hash=prepared.input_hash,
        adapter_id=metadata.id, adapter_version=metadata.version, scenario=prepared.scenario,
        context=prepared.execution_context)


def run_execution_id(run: RunDetail) -> str:
    """Local storage identity and immutable remote evidence identity may differ."""
    return run.recovery.source_execution_id if run.recovery else run.id


def compute_recovered_run(prepared: PreparedRun, archive, *, run_id: str, created_at: str, lineage) -> ComputedRun:
    """Validate a sealed retrieval without invoking, loading, or retrying an engine.

    The source execution ID anchors original artifact bytes. A new local run
    preserves the failed attempt and explicitly records retrieval provenance.
    """
    from .execution_attempt import RemoteExecutionArchive
    from .schemas import RecoveryLineage
    archive = RemoteExecutionArchive.model_validate(archive.model_dump())
    lineage = RecoveryLineage.model_validate(lineage.model_dump())
    request = execution_attempt_request(prepared, lineage.source_execution_id)
    archive.status.validate_request(request, lineage.store_id)
    if archive.status.archive_sha256 != lineage.archive_sha256 or request.request_hash != lineage.request_hash:
        raise ValueError('Recovery lineage differs from its sealed archive')
    result = archive.result
    payloads = result.artifact_payloads if result is not None else archive.failure.artifact_payloads
    if (sum(len(p.content) for p in payloads) > 32*1024*1024
            or len({p.name for p in payloads}) != len(payloads)):
        raise ValueError('Recovered diagnostic/source artifacts exceed their budget or duplicate names')
    if result is not None:
        result = SimulationResult.model_validate(result.model_dump())
        if abs(result.trajectory[-1].time_s-prepared.scenario.duration_s) > .001:
            raise ValueError('Recovered trajectory does not cover the requested duration')
        if {m.name for m in result.metrics} != {'collision_count', 'min_ttc_s', 'max_deceleration_mps2', 'max_jerk_mps3', 'distance_m'}:
            raise ValueError('Recovered result must include all five metrics')
        for metric in result.metrics:
            if ((metric.value is None and metric.name != 'min_ttc_s')
                    or metric.passed != compare_metric(metric.value, metric.operator, metric.threshold, name=metric.name)):
                raise ValueError('Recovered metric value or verdict violates its predicate')
        artifacts = result.artifact_payloads
        if prepared.execution_context is not None:
            from .simulation_evidence import validate_simulation_artifacts
            validate_simulation_artifacts(request.simulation_request(), artifacts, completed=True,
                trajectory=result.trajectory, adapter_version=prepared.adapter_metadata.version)
        output = {**result.model_dump(mode='json', exclude={'artifact_payloads'}),
                  'artifact_hashes': [hashlib.sha256(p.content).hexdigest() for p in artifacts]}
        status, verdict, error = 'completed', 'passed' if all(m.passed for m in result.metrics) else 'failed', None
        trajectory, metrics, events, fidelity = result.trajectory, result.metrics, result.events, result.model_fidelity
    else:
        artifacts = archive.failure.artifact_payloads
        if prepared.execution_context is not None:
            from .simulation_evidence import validate_simulation_artifacts
            validate_simulation_artifacts(request.simulation_request(), artifacts, completed=False,
                                          adapter_version=prepared.adapter_metadata.version)
        status, verdict, error = 'error', 'error', archive.error
        trajectory, metrics = [], []
        events = [Event(time_s=0, type='recovered_diagnostics', message='Retrieved sealed failure evidence; no completed validation result.')]
        fidelity = 'execution failed; recovered diagnostic artifacts only; no validation evidence'
        output = {'error': error, 'events': [e.model_dump() for e in events],
                  'artifact_hashes': [hashlib.sha256(p.content).hexdigest() for p in artifacts]}
    scenario, metadata = prepared.scenario, prepared.adapter_metadata
    run = RunDetail(id=run_id, scenario_id=scenario.id, scenario_name=scenario.name, scenario_revision=scenario.revision,
        adapter_id=metadata.id, status=status, verdict=verdict, created_at=created_at, duration_s=scenario.duration_s,
        execution_ms=archive.execution_ms if archive.execution_ms is not None else 0,
        metrics=metrics, trajectory=trajectory, events=events, artifacts=[], error=error, scenario_snapshot=scenario,
        execution_context=prepared.execution_context, recovery=lineage,
        provenance=Provenance(input_hash=prepared.input_hash, output_hash=digest(output), seed=scenario.seed,
                              adapter_version=metadata.version, model_fidelity=fidelity))
    return ComputedRun(run, tuple(artifacts))


def _unpublished_run(prepared: PreparedRun, computed: ComputedRun) -> RunDetail:
    run = computed.run.model_copy(deep=True)
    if run.provenance.input_hash != prepared.input_hash or canonical_bytes(run.scenario_snapshot.model_dump(mode="json")) != prepared.scenario_json:
        raise ValueError("Computed run does not match its prepared input")
    if run.execution_context != prepared.execution_context:
        raise ValueError('Computed run execution context differs from its pinned input')
    if run.artifacts:
        raise ValueError("Computed run already contains published artifacts")
    return run


def _cleanup_staging(db: Database, directory: Path, items: list[StagedArtifact] | tuple[StagedArtifact, ...]) -> None:
    staging_root = (db.artifact_dir / ".staging").resolve()
    if directory.resolve().parent != staging_root or directory.is_symlink():
        raise ValueError("Staged run directory is outside managed staging")
    for item in items:
        if Path(item.storage_key).name != item.storage_key:
            raise ValueError("Invalid staged artifact storage key")
        (directory / item.storage_key).unlink(missing_ok=True)
    try:
        directory.rmdir()
    except FileNotFoundError:
        pass


def cleanup_staged_run(db: Database, staged: StagedRun) -> None:
    """Remove only this attempt's remaining files, never final artifact paths.

    Call in finally after publication succeeds or fails. Process-kill leftovers
    require explicit reconciliation against durable attempts and DB references;
    this helper deliberately does not sweep other directories or .blob files.
    """
    _cleanup_staging(db, staged.directory, staged.artifacts)


def stage_run(db: Database, prepared: PreparedRun, computed: ComputedRun, attempt_key: str) -> StagedRun:
    """Hash and write payloads/manifests without an open database transaction."""
    run = _unpublished_run(prepared, computed)
    staging_root = db.artifact_dir / ".staging"
    staging_root.mkdir(exist_ok=True)
    # A supplied key is an ownership label, never a filesystem component.
    prefix = f"attempt_{hashlib.sha256(attempt_key.encode('utf-8')).hexdigest()[:16]}_"
    directory = Path(tempfile.mkdtemp(prefix=prefix, dir=staging_root))
    items: list[StagedArtifact] = []
    source = "synthetic/reference" if run.adapter_id == "reference" else f"simulation/{run.adapter_id}"

    def write(payload: ArtifactPayload) -> None:
        item = _new_artifact(run.id, run.created_at, payload, source)
        stream = (directory / item.storage_key).open("xb")
        items.append(item)
        with stream:
            if stream.write(payload.content) != len(payload.content):
                raise OSError("Incomplete staged artifact write")
        run.artifacts.append(item.artifact)

    try:
        for payload in computed.artifact_payloads:
            write(payload)
        # The manifest references payload artifacts, excluding its own hash.
        write(ArtifactPayload(name=f"{run.id}.evidence.json", kind="evidence_manifest", media_type="application/json",
                              content=canonical_bytes(run.model_dump(mode="json"))))
    except Exception:
        _cleanup_staging(db, directory, items)
        raise
    return StagedRun(directory, run, tuple(items), prepared.input_hash, prepared.request_hash, run.model_dump(mode="json"))


def publish_run(session: Session, db: Database, prepared: PreparedRun, computed: ComputedRun,
                key: str | None = None, *, staged: StagedRun | None = None) -> RunDetail:
    """Publish one detached result and owned files in the caller's transaction.

    Commit belongs to the caller so a durable job's terminal state and its run
    can commit together. Existing artifact transaction hooks clean up rollback.
    """
    existing = _idempotent_run(session, prepared.request_hash, key)
    if existing is not None:
        return existing
    if staged is not None:
        if (staged.input_hash != prepared.input_hash or staged.request_hash != prepared.request_hash
                or staged.run.id != computed.run.id):
            raise ValueError("Staged run does not match its prepared execution")
        if staged.directory.resolve().parent != (db.artifact_dir / ".staging").resolve() or staged.directory.is_symlink():
            raise ValueError("Staged run directory is outside managed staging")
        run = staged.run
    else:
        run = _unpublished_run(prepared, computed)
    run_id, timestamp = run.id, run.created_at
    row = RunRow(id=run.id, scenario_id=run.scenario_id, created_at=timestamp, verdict=run.verdict,
                 payload=staged.run_payload if staged is not None else run.model_dump(mode="json"))
    session.add(row)
    session.flush()
    written: list[Path] = []
    try:
        if staged is not None:
            for item in staged.artifacts:
                if Path(item.storage_key).name != item.storage_key:
                    raise ValueError("Invalid staged artifact storage key")
                path = db.artifact_dir / item.storage_key
                # Same-filesystem hardlink promotion is atomic, copies no bytes
                # and refuses to overwrite any existing evidence file.
                path.hardlink_to(staged.directory / item.storage_key)
                written.append(path)
                track_created_artifact(session, path)
                (staged.directory / item.storage_key).unlink()
                session.add(ArtifactRow(id=item.artifact.id, run_id=run_id, storage_key=item.storage_key,
                                        payload=item.artifact.model_dump(mode="json")))
        else:
            source = "synthetic/reference" if run.adapter_id == "reference" else f"simulation/{run.adapter_id}"
            for payload in computed.artifact_payloads:
                run.artifacts.append(_artifact_write(db, session, run_id, timestamp, payload, source, written))
            evidence = ArtifactPayload(name=f"{run_id}.evidence.json", kind="evidence_manifest", media_type="application/json", content=canonical_bytes(run.model_dump(mode="json")))
            run.artifacts.append(_artifact_write(db, session, run_id, timestamp, evidence, source, written))
        if staged is None:
            row.payload = run.model_dump(mode="json")
        if key:
            session.add(IdempotencyRow(key=key, request_hash=prepared.request_hash, run_id=run_id))
        session.flush()
    except Exception:
        for path in written:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not immediately remove failed artifact write: %s", path)
        raise
    return run


def execute_run(session: Session, db: Database, registry: Registry, body: RunCreate, key: str | None = None) -> RunDetail:
    # Replay is checked before reading today's scenario or adapter availability.
    existing = _idempotent_run(session, run_request_hash(body), key)
    if existing is not None:
        return existing
    prepared = prepare_run(session, registry, body)
    validate_prepared_execution(prepared, registry.get(body.adapter_id))
    computed = compute_run(prepared, registry.get(body.adapter_id))
    return publish_run(session, db, prepared, computed, key)


def requirement_create(session: Session, body: RequirementCreate) -> RequirementRow:
    for scenario_id in body.scenario_ids:
        scenario_get(session, scenario_id)
    row = RequirementRow(id=uid("req"), payload=body.model_dump(mode="json"))
    session.add(row)
    session.flush()
    for scenario_id in body.scenario_ids:
        session.add(RequirementLinkRow(requirement_id=row.id, scenario_id=scenario_id))
    session.flush()
    return row


def latest_run_details(session: Session) -> dict[str, RunDetail | None]:
    """Legacy runs remain evidence until a newer durable dispatch takes over.

    Job submission order, rather than completion time, selects the attempt.
    An unfinished/failed dispatched job explicitly blocks earlier passing runs.
    """
    from .jobs import latest_job_evidence

    latest: dict[str, RunDetail | None] = {}
    for row in session.scalars(select(RunRow).order_by(RunRow.created_at.desc(), RunRow.id.desc())):
        value = RunDetail.model_validate(row.payload)
        if row.scenario_id not in latest and value.recovery is None:
            latest[row.scenario_id] = value
    for scenario_id, job in latest_job_evidence(session).items():
        from .recoveries import resolved_job_run
        latest[scenario_id] = resolved_job_run(session, job)
    return latest


def is_current_baseline(scenario: Scenario | None, run: RunDetail | None) -> bool:
    """A completed current-condition run is evidence; errors and variants are not."""
    if scenario is None or run is None or run.status != "completed" or run.verdict not in {"passed", "failed"}:
        return False
    snapshot = run.scenario_snapshot
    return (snapshot.revision == scenario.revision
            and snapshot.parameters == scenario.parameters
            and snapshot.seed == scenario.seed)


def requirements_evaluated(session: Session) -> list[Requirement]:
    scenarios = {row.id: Scenario.model_validate(row.payload) for row in session.scalars(select(ScenarioRow))}
    latest = latest_run_details(session)
    requirements = []
    for row in session.scalars(select(RequirementRow)):
        body = RequirementCreate.model_validate(row.payload)
        # Explicit links define scope. A category is a dynamic selector only
        # when no explicit links were supplied; new matching scenarios count.
        ids = body.scenario_ids or ([key for key, value in scenarios.items() if value.category == body.category] if body.category else [])
        evidence = []
        failed = False
        for scenario_id in ids:
            scenario, run = scenarios.get(scenario_id), latest.get(scenario_id)
            # Never use old-revision runs, parameter variants or an older pass
            # after a new failure/exception. Baseline conditions are explicit.
            if not is_current_baseline(scenario, run):
                continue
            metric = next((metric for metric in run.metrics if metric.name == body.metric_name), None)
            if metric is None or (metric.value is None and metric.name != "min_ttc_s"):
                continue
            evidence.append(run)
            failed = failed or not compare_metric(metric.value, body.operator, body.threshold, name=metric.name)
        status = "failed" if failed else "passed" if ids and len(evidence) == len(ids) else "untested"
        requirements.append(Requirement(**body.model_dump(), id=row.id, status=status, evidence_count=len(evidence),
                                        linked_scenario_count=len(ids), last_run_id=max(evidence, key=lambda run: (run.created_at, run.id)).id if evidence else None))
    return requirements


def overview(session: Session) -> dict:
    scenarios = [Scenario.model_validate(row.payload) for row in session.scalars(select(ScenarioRow))]
    runs = run_summaries(session)
    requirements = requirements_evaluated(session)
    latest = latest_run_details(session)
    categories = ("cut_in", "emergency_brake", "pedestrian_crossing", "lane_follow")
    cells = []
    for category in categories:
        for weather in ("clear", "rain", "fog"):
            members = [scenario for scenario in scenarios if scenario.category == category and scenario.weather == weather]
            passed, failed = 0, 0
            for scenario in members:
                run = latest.get(scenario.id)
                if is_current_baseline(scenario, run):
                    passed += run.verdict == "passed"
                    failed += run.verdict == "failed"
            tested = passed + failed
            cells.append({"category": category, "weather": weather, "total": len(members),
                          "tested": tested, "passed": passed, "failed": failed, "untested": len(members) - tested})
    artifacts = list(session.scalars(select(ArtifactRow.id)))
    return {"scenario_count": len(scenarios), "run_count": len(runs), "pass_rate": round(sum(run.verdict == "passed" for run in runs) / len(runs) * 100, 1) if runs else 0,
            "artifact_count": len(artifacts), "requirement_count": len(requirements),
            "covered_requirement_count": sum(req.linked_scenario_count > 0 and req.evidence_count == req.linked_scenario_count for req in requirements),
            "recent_runs": [run.model_dump(mode="json") for run in runs[:6]],
            "category_distribution": [{"category": category, "count": sum(scenario.category == category for scenario in scenarios)} for category in categories], "coverage": cells}
