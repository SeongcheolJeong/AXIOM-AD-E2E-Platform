"""Durable attempt transport; every recovery operation addresses one identity."""
import hashlib
from urllib.parse import quote

import httpx

from ade2e.adapters import AdapterExecutionError, AdapterInputError, AdapterUnavailable
from ade2e.execution_attempt import ExecutionAttemptRequest, RemoteExecutionArchive, RemoteExecutionStatus
from ade2e.schemas import AdapterFailureEvidence
from ade2e.simulation_context import validate_simulation_adapter_context
from ade2e.simulation_evidence import validate_simulation_artifacts
from .version import ADAPTER_VERSION, require_durable_version
from .wire import (ARCHIVE_SCHEMA, HEALTH_BYTES_LIMIT, WIRE_BYTES_LIMIT, decode_failure, decode_result,
                   parse_json, read_bytes, read_json)


def _validate_request(request, *, dispatch=False):
    request = ExecutionAttemptRequest.model_validate(request.model_dump(mode='json'))
    try:
        require_durable_version(request.adapter_version, dispatch=dispatch)
        if request.context:
            validate_simulation_adapter_context(request.context, request.adapter_version)
    except ValueError as error:
        raise AdapterInputError('Durable CARLA attempt has an unsupported adapter identity/version') from error
    if request.adapter_id != 'carla':
        raise AdapterInputError('Durable CARLA attempt has an unsupported adapter identity/version')
    if request.scenario.category not in {'lane_follow', 'emergency_brake'} or request.scenario.duration_s > 20:
        raise AdapterInputError('Durable CARLA attempt exceeds the straight-road adapter capabilities')
    return request


def inspect_execution(adapter, request):
    request = _validate_request(request)
    try:
        with adapter._client(10) as client:
            with client.stream('POST', '/v3/inspect', json=request.model_dump(mode='json')) as response:
                response.raise_for_status()
                status = RemoteExecutionStatus.model_validate(read_json(response, HEALTH_BYTES_LIMIT))
        status.validate_request(request)
        return status
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as error:
        raise AdapterUnavailable('Remote execution status unavailable or unverified; no execution was submitted') from error


def decode_archive(request, status, content):
    request = _validate_request(request)
    status.validate_request(request)
    if hashlib.sha256(content).hexdigest() != status.archive_sha256 or len(content) != status.archive_size_bytes:
        raise ValueError('Exact archive bytes differ from the inspected terminal outcome')
    payload = parse_json(content)
    expected = {'schema_version': ARCHIVE_SCHEMA, 'store_id': status.store_id,
        'execution_id': request.execution_id, 'request_hash': request.request_hash,
        'input_hash': request.input_hash, 'context_hash': request.context_hash,
        'worker_adapter_version': request.adapter_version, 'outcome': status.state}
    if (not isinstance(payload, dict) or set(payload) != set(expected) | {'payload', 'execution_ms'}
            or any(payload.get(key) != value for key, value in expected.items())):
        raise ValueError('Archive schema or immutable correlation mismatch')
    context = request.simulation_request() if request.context else None
    if status.state == 'completed':
        result = decode_result(payload['payload'], request=context, adapter_version=request.adapter_version)
        if context:
            validate_simulation_artifacts(context, result.artifact_payloads, completed=True,
                trajectory=result.trajectory, adapter_version=request.adapter_version)
        return RemoteExecutionArchive(status=status, archive_bytes=content, result=result, execution_ms=payload['execution_ms'])
    if status.state not in {'failed', 'interrupted'}:
        raise ValueError('Archive has no terminal outcome')
    detail, artifacts = decode_failure(payload['payload'], request=context, adapter_version=request.adapter_version)
    if context:
        validate_simulation_artifacts(context, artifacts, completed=False, adapter_version=request.adapter_version)
    return RemoteExecutionArchive(status=status, archive_bytes=content,
        failure=AdapterFailureEvidence(artifact_payloads=artifacts), error=detail, execution_ms=payload['execution_ms'])


def retrieve_execution(adapter, request, *, expected_store_id, expected_archive_sha256):
    request = _validate_request(request)
    try:
        status = inspect_execution(adapter, request)
        status.validate_request(request, expected_store_id)
        if status.archive_sha256 != expected_archive_sha256 or status.archive_kind is None:
            raise ValueError('The inspected archive is unavailable or changed')
        with adapter._client(60) as client:
            with client.stream('GET', f'/v3/executions/{quote(request.execution_id, safe='')}/archive', params={
                    'store_id': expected_store_id, 'archive_sha256': expected_archive_sha256}) as response:
                response.raise_for_status()
                content = read_bytes(response, WIRE_BYTES_LIMIT)
        return decode_archive(request, status, content)
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as error:
        raise AdapterUnavailable('Remote archive could not be verified; no simulation or cleanup was attempted') from error


def execute_attempt(adapter, request, *, expected_store_id=None):
    request = _validate_request(request, dispatch=True)
    if request.context:
        adapter.validate_execution_context(request.scenario, request.context)
    # Without a queue caller, still pin the immutable store before dispatch.
    before = inspect_execution(adapter, request)
    if expected_store_id is None:
        expected_store_id = before.store_id
    try:
        before.validate_request(request, expected_store_id)
    except ValueError as error:
        raise AdapterUnavailable('Remote store changed before dispatch; execution was not submitted') from error
    if before.state != 'not_found':
        raise AdapterExecutionError('Execution ID already exists; inspect/retrieve the recorded outcome. No simulation was repeated.')
    try:
        with adapter._client(180) as client:
            with client.stream('POST', '/v3/simulate', params={'store_id': expected_store_id},
                               json=request.model_dump(mode='json')) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError('Unexpected durable dispatch response')
                content = read_bytes(response, WIRE_BYTES_LIMIT)
        status = inspect_execution(adapter, request)
        status.validate_request(request, expected_store_id)
        archive = decode_archive(request, status, content)
    except (httpx.HTTPError, AdapterUnavailable, ValueError, TypeError, KeyError) as error:
        raise AdapterExecutionError('CARLA durable execution outcome is uncertain; inspect this execution ID. No retry or reference fallback.') from error
    if archive.failure is not None:
        raise AdapterExecutionError(archive.error, artifact_payloads=archive.failure.artifact_payloads)
    return archive.result
