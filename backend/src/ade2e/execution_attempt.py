"""Engine-neutral remote attempt identities and read-only recovery hooks.

An attempt is never retried by these hooks. Installed adapters are trusted
codecs; the core independently validates their decoded result and its lineage.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from .base_schemas import Name, StrictModel
from .model_schemas import Sha256
from .scenario_schemas import Scenario
from .simulation_context import (AnyPreparedSimulationContext, AnySimulationExecutionRequest,
    make_simulation_execution_request, simulation_input_hash, validate_simulation_adapter_context)
from .schemas import AdapterFailureEvidence, SimulationResult

MAX_REMOTE_ARCHIVE_BYTES = 128 * 1024 * 1024


class ExecutionAttemptRequest(StrictModel):
    schema_version: Literal['execution-attempt/1', 'execution-attempt/2'] = 'execution-attempt/1'
    execution_id: Name
    input_hash: Sha256
    adapter_id: Name
    adapter_version: Name
    scenario: Scenario
    context: AnyPreparedSimulationContext | None = None

    @model_validator(mode='after')
    def valid_input(self):
        configurable = self.context is not None and self.context.schema_version == 'simulation-control-context/2'
        if (self.schema_version == 'execution-attempt/2') != configurable:
            raise ValueError('Attempt schema differs from its pinned context version')
        if configurable:
            if self.adapter_id != 'carla':
                raise ValueError('Configurable sensor attempt requires its exact CARLA adapter')
            validate_simulation_adapter_context(self.context, self.adapter_version)
        if self.input_hash != simulation_input_hash(self.scenario, self.adapter_id, self.adapter_version, self.context):
            raise ValueError('Attempt input hash differs from its pinned inputs')
        if self.context is not None:
            self.simulation_request()
        return self

    @property
    def request_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(mode='json'), sort_keys=True,
            separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()

    @property
    def context_hash(self) -> str | None:
        return self.context.context_hash if self.context is not None else None

    def simulation_request(self) -> AnySimulationExecutionRequest:
        if self.context is None:
            raise ValueError('This attempt has no stack execution context')
        return make_simulation_execution_request(execution_id=self.execution_id, input_hash=self.input_hash,
                                                scenario=self.scenario, context=self.context)


class RemoteExecutionStatus(StrictModel):
    schema_version: Literal['remote-execution-status/1'] = 'remote-execution-status/1'
    store_id: Name
    execution_id: Name
    request_hash: Sha256
    input_hash: Sha256
    context_hash: Sha256 | None
    adapter_id: Name
    adapter_version: Name
    state: Literal['not_found', 'accepted', 'running', 'completed', 'failed', 'interrupted']
    observed_at: Name
    archive_kind: Literal['complete', 'partial'] | None = None
    archive_sha256: Sha256 | None = None
    archive_size_bytes: int | None = Field(default=None, ge=1, le=MAX_REMOTE_ARCHIVE_BYTES, strict=True)
    mutations_started: bool = Field(strict=True)
    cleanup_status: Literal['not_started', 'confirmed', 'failed', 'unknown']
    error: str | None = Field(default=None, max_length=4000)

    @model_validator(mode='after')
    def coherent_terminal(self):
        values = (self.archive_kind, self.archive_sha256, self.archive_size_bytes)
        if any(x is not None for x in values) != all(x is not None for x in values):
            raise ValueError('Archive identity must be complete or absent')
        terminal = self.state in {'completed', 'failed', 'interrupted'}
        if not terminal and self.archive_kind is not None:
            raise ValueError('Only sealed terminal executions can expose an archive')
        if self.state == 'completed' and self.archive_kind != 'complete':
            raise ValueError('Completed execution requires a sealed complete archive')
        if self.state == 'interrupted' and self.archive_kind not in {None, 'partial'}:
            raise ValueError('Interrupted execution cannot expose a successful complete archive')
        return self

    def validate_request(self, request: ExecutionAttemptRequest, expected_store_id: str | None = None) -> None:
        expected = {'execution_id': request.execution_id, 'request_hash': request.request_hash,
                    'input_hash': request.input_hash, 'context_hash': request.context_hash,
                    'adapter_id': request.adapter_id, 'adapter_version': request.adapter_version}
        if any(getattr(self, key) != value for key, value in expected.items()):
            raise ValueError('Remote execution status differs from the pinned attempt')
        if expected_store_id is not None and self.store_id != expected_store_id:
            raise ValueError('Remote execution store changed; recovery was refused')


class RemoteExecutionArchive(StrictModel):
    """Exact remote archive plus its adapter-verified decoded result or failure."""
    status: RemoteExecutionStatus
    archive_bytes: bytes = Field(min_length=1, max_length=MAX_REMOTE_ARCHIVE_BYTES)
    result: SimulationResult | None = None
    failure: AdapterFailureEvidence | None = None
    error: str | None = Field(default=None, max_length=4000)
    execution_ms: float | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def exact_archive(self):
        if (hashlib.sha256(self.archive_bytes).hexdigest() != self.status.archive_sha256
                or len(self.archive_bytes) != self.status.archive_size_bytes):
            raise ValueError('Retrieved archive does not match its terminal digest and size')
        if self.status.state == 'completed':
            if self.result is None or self.failure is not None or self.error is not None or self.execution_ms is None:
                raise ValueError('Complete recovery requires a decoded result and remote execution duration')
        elif self.status.state in {'failed', 'interrupted'}:
            if self.result is not None or self.failure is None or not self.error:
                raise ValueError('Failed or partial recovery requires explicit diagnostic failure evidence')
        else:
            raise ValueError('Only terminal archives may be retrieved')
        return self


def supports_execution_attempt(adapter) -> bool:
    return callable(getattr(adapter, 'execute_attempt', None))


def remote_recovery_versions(adapter) -> tuple[str, ...]:
    """Declared source/reader contracts, never permission for new simulation.

    Older plugins default to their installed version. An explicit malformed
    declaration fails closed instead of broadening compatibility implicitly.
    """
    if not supports_execution_attempt(adapter) or not all(callable(getattr(adapter, name, None))
            for name in ('inspect_execution', 'retrieve_execution')):
        return ()
    try:
        hook = getattr(adapter, 'remote_recovery_versions', None)
        values = hook() if callable(hook) else (adapter.metadata().version,) if hook is None else ()
        if (not isinstance(values, (tuple, list)) or not 1 <= len(values) <= 32
                or any(not isinstance(value, str) or not 1 <= len(value) <= 180 or value.strip() != value for value in values)
                or len(set(values)) != len(values)):
            return ()
        return tuple(values)
    except Exception:
        return ()


def supports_remote_recovery(adapter, *, version: str | None = None) -> bool:
    versions = remote_recovery_versions(adapter)
    return bool(versions) and (version is None or version in versions)
