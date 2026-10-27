"""Bounded worker evidence envelopes; no CARLA imports or execution retries."""
from __future__ import annotations

import base64
import json

from ade2e.schemas import ArtifactPayload, SimulationResult
from ade2e.simulation_context import validate_simulation_adapter_context
from .version import ADAPTER_VERSION, require_durable_version

ARCHIVE_SCHEMA = "carla-execution-archive/1"
RESULT_SCHEMA = "carla-execution-result/1"
FAILURE_SCHEMA = "carla-execution-failure/1"
CONTEXT_RESULT_SCHEMA = "carla-execution-result/2"
CONTEXT_FAILURE_SCHEMA = "carla-execution-failure/2"
CONFIGURABLE_RESULT_SCHEMA = "carla-execution-result/3"
CONFIGURABLE_FAILURE_SCHEMA = "carla-execution-failure/3"
HEALTH_BYTES_LIMIT = 64 * 1024
WIRE_BYTES_LIMIT = 48 * 1024 * 1024
ARTIFACT_BYTES_LIMIT = 20 * 1024 * 1024
ARTIFACT_TOTAL_LIMIT = 32 * 1024 * 1024
ARTIFACT_COUNT_LIMIT = 100
DETAIL_LENGTH_LIMIT = 4000
_ARTIFACT_FIELDS = {"name", "kind", "media_type", "content_base64"}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        key.encode("utf-8")
        if isinstance(value, str):
            value.encode("utf-8")
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError("Nonfinite JSON constant")


def read_bytes(response, maximum: int):
    """Read identity bytes incrementally before parsing any upstream JSON."""
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise ValueError("Encoded worker responses are not supported")
    declared = response.headers.get("content-length")
    if declared is not None and (not declared.isascii() or not declared.isdigit()
                                 or len(declared) > 20 or int(declared) > maximum):
        raise ValueError("Worker response exceeds its wire bound")
    content = bytearray()
    for chunk in response.iter_bytes(chunk_size=64 * 1024):
        if len(content) + len(chunk) > maximum:
            raise ValueError("Worker response exceeds its wire bound")
        content.extend(chunk)
    if declared is not None and len(content) != int(declared):
        raise ValueError("Worker response length mismatch")
    return bytes(content)


def parse_json(content: bytes):
    try:
        return json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_invalid_constant)
    except RecursionError as exc:
        raise ValueError("Worker JSON nesting exceeds parser limits") from exc


def read_json(response, maximum: int):
    return parse_json(read_bytes(response, maximum))


def encode_artifacts(payloads) -> list[dict]:
    if not isinstance(payloads, (list, tuple)) or len(payloads) > ARTIFACT_COUNT_LIMIT:
        raise ValueError("Too many worker artifacts")
    result, total = [], 0
    for payload in payloads:
        # Revalidate mutable model instances instead of trusting prior validation.
        artifact = ArtifactPayload.model_validate(payload.model_dump() if isinstance(payload, ArtifactPayload) else payload)
        total += len(artifact.content)
        if total > ARTIFACT_TOTAL_LIMIT:
            raise ValueError("Worker artifacts exceed their total byte bound")
        result.append({"name": artifact.name, "kind": artifact.kind, "media_type": artifact.media_type,
                       "content_base64": base64.b64encode(artifact.content).decode("ascii")})
    return result


def decode_artifacts(payloads) -> list[ArtifactPayload]:
    if not isinstance(payloads, list) or len(payloads) > ARTIFACT_COUNT_LIMIT:
        raise ValueError("Worker artifact list is invalid")
    result, total = [], 0
    for payload in payloads:
        if not isinstance(payload, dict) or set(payload) != _ARTIFACT_FIELDS:
            raise ValueError("Worker artifact fields are invalid")
        encoded = payload["content_base64"]
        remaining = min(ARTIFACT_BYTES_LIMIT, ARTIFACT_TOTAL_LIMIT - total)
        if not isinstance(encoded, str) or len(encoded) > 4 * ((remaining + 2) // 3):
            raise ValueError("Worker artifact base64 exceeds its byte bound")
        content = base64.b64decode(encoded, validate=True)
        if len(content) > remaining or base64.b64encode(content).decode("ascii") != encoded:
            raise ValueError("Worker artifact base64 is not bounded canonical data")
        artifact = ArtifactPayload.model_validate({key: payload[key] for key in ("name", "kind", "media_type")} | {"content": content})
        result.append(artifact)
        total += len(content)
    return result


def _correlation(request) -> dict:
    """Identity belongs to the reserved attempt, independently of result bytes."""
    identity = {"execution_id": request.execution_id, "input_hash": request.input_hash,
                "context_hash": request.context.context_hash}
    if not isinstance(identity["execution_id"], str) or not 1 <= len(identity["execution_id"]) <= 200:
        raise ValueError("Invalid execution identity")
    for key in ("input_hash", "context_hash"):
        value = identity[key]
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("Invalid execution hash")
    return identity


def _envelope_schema(request, *, failure=False):
    """Dispatch from the pinned request/context, never from the installed writer."""
    if request is None:
        return FAILURE_SCHEMA if failure else RESULT_SCHEMA
    pair = (request.schema_version, request.context.schema_version)
    if pair == ('simulation-execution-request/1', 'simulation-control-context/1'):
        return CONTEXT_FAILURE_SCHEMA if failure else CONTEXT_RESULT_SCHEMA
    if pair == ('simulation-execution-request/2', 'simulation-control-context/2'):
        return CONFIGURABLE_FAILURE_SCHEMA if failure else CONFIGURABLE_RESULT_SCHEMA
    raise ValueError('Worker request/context version mismatch')


def initial_sensor_configuration_payload(request):
    """Explicit not-observed state, never reconstructed sensor measurements."""
    from ade2e.simulation_sensor_pose import initial_sensor_configuration_evidence
    value = initial_sensor_configuration_evidence(request)
    return ArtifactPayload(name='carla-sensor-configuration.json', kind='sensor_configuration',
        media_type='application/json', content=json.dumps(value, sort_keys=True, separators=(',', ':'),
            ensure_ascii=False, allow_nan=False).encode())


def _identity(payload, *, request, schema, fields, adapter_version=ADAPTER_VERSION):
    require_durable_version(adapter_version)
    if request is not None:
        validate_simulation_adapter_context(request.context, adapter_version)
    correlation = _correlation(request) if request is not None else {}
    if (not isinstance(payload, dict) or set(payload) != fields | set(correlation)
            or payload.get("schema_version") != schema
            or payload.get("worker_adapter_version") != adapter_version
            or any(payload.get(key) != value for key, value in correlation.items())):
        raise ValueError("Worker envelope execution identity mismatch")


def encode_result(result: SimulationResult, *, request=None, adapter_version=ADAPTER_VERSION) -> dict:
    require_durable_version(adapter_version)
    if request is not None:
        validate_simulation_adapter_context(request.context, adapter_version)
    data = result.model_dump(exclude={"artifact_payloads"})
    data["artifact_payloads"] = encode_artifacts(result.artifact_payloads)
    return {"schema_version": _envelope_schema(request),
            "worker_adapter_version": adapter_version, "result": data,
            **(_correlation(request) if request is not None else {})}


def decode_result(payload, *, request=None, adapter_version=ADAPTER_VERSION) -> SimulationResult:
    _identity(payload, request=request, schema=_envelope_schema(request),
              fields={"schema_version", "worker_adapter_version", "result"}, adapter_version=adapter_version)
    if not isinstance(payload["result"], dict):
        raise ValueError("Worker success result is invalid")
    result = dict(payload["result"])
    result["artifact_payloads"] = decode_artifacts(result["artifact_payloads"])
    return SimulationResult.model_validate(result)


def encode_failure(detail: str, payloads=(), *, request=None, adapter_version=ADAPTER_VERSION) -> dict:
    require_durable_version(adapter_version)
    if request is not None:
        validate_simulation_adapter_context(request.context, adapter_version)
    detail = detail.strip()[:DETAIL_LENGTH_LIMIT] or "CARLA execution failed."
    return {"schema_version": _envelope_schema(request, failure=True),
            "worker_adapter_version": adapter_version,
            "detail": detail, "artifact_payloads": encode_artifacts(payloads),
            **(_correlation(request) if request is not None else {})}


def decode_failure(payload, *, request=None, adapter_version=ADAPTER_VERSION) -> tuple[str, list[ArtifactPayload]]:
    _identity(payload, request=request, schema=_envelope_schema(request, failure=True),
              fields={"schema_version", "worker_adapter_version", "detail", "artifact_payloads"}, adapter_version=adapter_version)
    if (not isinstance(payload["detail"], str) or not payload["detail"].strip()
            or len(payload["detail"]) > DETAIL_LENGTH_LIMIT):
        raise ValueError("Worker failure envelope identity mismatch")
    return payload["detail"], decode_artifacts(payload["artifact_payloads"])
