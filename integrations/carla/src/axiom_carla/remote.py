"""HTTP control-plane adapter; importing this module never imports libcarla."""
from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx

from ade2e.adapters import AdapterExecutionError, AdapterInputError, AdapterUnavailable
from ade2e.schemas import Adapter, Scenario, SimulationResult
from ade2e.simulation_context import (AnyPreparedSimulationContext, AnySimulationExecutionRequest,
    simulation_input_hash, validate_simulation_context, validate_simulation_execution_request)
from ade2e.simulation_evidence import validate_simulation_artifacts
from .version import ADAPTER_VERSION, DURABLE_READ_VERSIONS
from .wire import HEALTH_BYTES_LIMIT, WIRE_BYTES_LIMIT, decode_failure, decode_result, read_json


def _readiness_error(readiness) -> str | None:
    if not isinstance(readiness, dict) or not isinstance(readiness.get("status"), str):
        return "CARLA worker returned an invalid readiness contract"
    if readiness.get("worker_adapter_version") != ADAPTER_VERSION:
        return f"CARLA worker adapter version mismatch: expected {ADAPTER_VERSION}; worker upgrade and a new health check are required"
    if readiness["status"] != "ready":
        detail = readiness.get("detail")
        return detail if isinstance(detail, str) and detail else "CARLA worker is not ready"
    return None


class RemoteCarlaAdapter:
    def execution_context_capabilities(self):
        """Installed local contract; actual worker readiness still needs preflight."""
        from ade2e.simulation_sensor_capabilities import simulation_sensor_capabilities
        return simulation_sensor_capabilities()

    def remote_recovery_versions(self):
        """Original execution/reader contracts supported by read-only hooks."""
        return DURABLE_READ_VERSIONS

    def __init__(self, url: str | None = None, token: str | None = None,
                 transport: httpx.BaseTransport | None = None):
        self.url = (url or os.getenv("ADE2E_CARLA_WORKER_URL", "")).rstrip("/")
        self.token = token or os.getenv("ADE2E_CARLA_WORKER_TOKEN")
        self.transport = transport

    def metadata(self) -> Adapter:
        return Adapter(
            id="carla", name="CARLA · remote worker", version=ADAPTER_VERSION, kind="driving_and_sensors",
            status="available" if self.url else "not_configured",
            description="Experimental HTTP bridge to an exclusive CARLA worker. Connectivity checked on execution.",
            capabilities=["lane_follow", "emergency_brake", "RGB PNG capture", "frame-aligned LiDAR", "collision events", "live_lidar_stack"],
            limitations=["Engine acceptance is deployment-specific; inspect the saved run and sensor evidence",
                         "Configured does not mean connected; runtime preflight is mandatory",
                         "2–20 s straight road trials; no cut-in or pedestrian adapter yet",
                         "Default ground-truth controller; optional 2–4 s LiDAR geometric stack with ego/map oracle",
                         "Live stack records LiDAR and ego state; no RGB input/capture or calibrated sensor physics",
                         "Requires a private, exclusive supported CARLA server and matching Python client"],
            license="CARLA code MIT; content and Unreal Engine terms are separate",
            source_url="https://github.com/carla-simulator/carla",
        )

    def check_health(self) -> dict:
        """Read worker readiness without starting a trial or changing metadata.

        A successful probe checks the worker's engine connection/version only;
        it is not sensor, scenario, or model validation.
        """
        if not self.url:
            return {"status": "not_configured", "detail": "Set ADE2E_CARLA_WORKER_URL to a private CARLA worker."}
        try:
            parsed = urlparse(self.url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                return {"status": "unreachable", "detail": "CARLA worker URL must use HTTP(S) without embedded credentials."}
            headers = {"Accept-Encoding": "identity", **({"Authorization": f"Bearer {self.token}"} if self.token else {})}
            with httpx.Client(base_url=self.url, headers=headers, timeout=5,
                              transport=self.transport, follow_redirects=False) as client:
                with client.stream("GET", "/health") as response:
                    if response.status_code != 200:
                        return {"status": "unreachable", "detail": f"CARLA worker health returned HTTP {response.status_code}."}
                    data = read_json(response, HEALTH_BYTES_LIMIT)
            metadata = {key: data[key] for key in ("version", "map", "scope", "worker_adapter_version")
                        if isinstance(data.get(key), str)}
            error = _readiness_error(data)
            return {"status": "unreachable" if error else "ready", "detail": error or
                    "Worker adapter version matches; worker reports a matching CARLA client/server connection. No simulation was run.",
                    "metadata": metadata}
        except httpx.HTTPError as exc:
            return {"status": "unreachable", "detail": f"CARLA connection check failed ({type(exc).__name__})."}
        except (ValueError, TypeError, AttributeError):
            return {"status": "unreachable", "detail": "CARLA worker URL or health response is invalid."}

    def execute(self, scenario: Scenario) -> SimulationResult:
        if scenario.category not in ("lane_follow", "emergency_brake"):
            raise AdapterInputError("CARLA adapter supports lane_follow and emergency_brake only")
        if scenario.duration_s > 20:
            raise AdapterInputError("CARLA adapter caps an execution at 20 simulation seconds")
        return self._execute(scenario)

    def _client(self, timeout):
        if not self.url:
            raise AdapterUnavailable("Set ADE2E_CARLA_WORKER_URL to an explicit private worker URL")
        try:
            parsed = urlparse(self.url)
        except ValueError as exc:
            raise AdapterUnavailable("CARLA worker URL is invalid; no execution was attempted") from exc
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise AdapterUnavailable("CARLA worker URL must be an http(s) URL without embedded credentials")
        headers = {"Accept-Encoding": "identity", **({"Authorization": f"Bearer {self.token}"} if self.token else {})}
        return httpx.Client(base_url=self.url, headers=headers, timeout=timeout,
                            transport=self.transport, follow_redirects=False)

    def validate_execution_context(self, scenario: Scenario, context: AnyPreparedSimulationContext) -> None:
        """Read-only remote validation before the queue commits dispatch intent."""
        try:
            context = validate_simulation_context(scenario, context)
        except (ValueError, KeyError, TypeError) as exc:
            raise AdapterInputError(f"Unsupported CARLA stack context: {exc}") from exc
        preflight_schema = ('carla-execution-preflight/2' if context.schema_version == 'simulation-control-context/2'
                            else 'carla-execution-preflight/1')
        expected = {"schema_version": preflight_schema, "status": "ready",
                    "worker_adapter_version": ADAPTER_VERSION,
                    "input_hash": simulation_input_hash(scenario, "carla", ADAPTER_VERSION, context),
                    "context_hash": context.context_hash, "kernel_version": context.stack_snapshot.kernel_version}
        try:
            with self._client(30) as client:
                with client.stream("GET", "/health", timeout=5) as response:
                    response.raise_for_status()
                    readiness = read_json(response, HEALTH_BYTES_LIMIT)
                error = _readiness_error(readiness)
                if error:
                    raise AdapterUnavailable(error)
                with client.stream("POST", "/v2/preflight", json={"scenario": scenario.model_dump(mode="json"),
                                  "context": context.model_dump(mode="json")}) as response:
                    if response.status_code in (409, 422):
                        rejection = read_json(response, HEALTH_BYTES_LIMIT)
                        if not isinstance(rejection, dict) or not isinstance(rejection.get("detail"), str):
                            raise ValueError("Invalid context preflight rejection")
                        raise AdapterInputError(rejection["detail"])
                    response.raise_for_status()
                    data = read_json(response, HEALTH_BYTES_LIMIT)
            if (not isinstance(data, dict) or set(data) != set(expected) | {"version", "map"}
                    or any(data.get(key) != value for key, value in expected.items())
                    or not isinstance(data.get("version"), str) or not data["version"]
                    or not isinstance(data.get("map"), str) or data["map"].split("/")[-1] != scenario.map_name
                    or data["version"] != readiness.get("version") or data["map"] != readiness.get("map")):
                raise ValueError("CARLA context preflight identity mismatch")
        except httpx.HTTPError as exc:
            raise AdapterUnavailable(f"CARLA context preflight failed ({type(exc).__name__}); no execution was attempted") from exc
        except (ValueError, TypeError, AttributeError) as exc:
            if isinstance(exc, AdapterInputError):
                raise
            raise AdapterUnavailable("CARLA context preflight returned an invalid contract; no execution was attempted") from exc

    def execute_with_context(self, request: AnySimulationExecutionRequest) -> SimulationResult:
        try:
            request = validate_simulation_execution_request(request.model_dump(mode="json"))
            if request.input_hash != simulation_input_hash(request.scenario, "carla", ADAPTER_VERSION, request.context):
                raise ValueError("Execution input hash does not match the pinned CARLA request")
        except (ValueError, TypeError, AttributeError) as exc:
            raise AdapterInputError(f"Invalid CARLA execution context: {exc}") from exc
        self.validate_execution_context(request.scenario, request.context)
        return self._execute(request.scenario, request=request)

    def _execute(self, scenario: Scenario, *, request: AnySimulationExecutionRequest | None = None) -> SimulationResult:
        execution_attempted = False
        try:
            with self._client(180) as client:
                with client.stream("GET", "/health", timeout=5) as health:
                    health.raise_for_status()
                    readiness = read_json(health, HEALTH_BYTES_LIMIT)
                error = _readiness_error(readiness)
                if error:
                    raise AdapterUnavailable(error)
                # Once submitted, timeout/HTTP failure/malformed evidence may
                # follow actual simulator side effects. Preserve an error run
                # and key instead of erasing the attempt as mere unavailability.
                execution_attempted = True
                route = "/v2/simulate" if request is not None else "/v1/simulate"
                body = request.model_dump(mode="json") if request is not None else scenario.model_dump(mode="json")
                with client.stream("POST", route, json=body) as response:
                    if response.status_code in (409, 422):
                        rejection = read_json(response, HEALTH_BYTES_LIMIT)
                        if not isinstance(rejection, dict) or not isinstance(rejection.get("detail"), str):
                            raise ValueError("Invalid worker rejection")
                        raise AdapterInputError(rejection["detail"])
                    if response.status_code == 500:
                        try:
                            detail, artifacts = decode_failure(read_json(response, WIRE_BYTES_LIMIT), request=request)
                            if request is not None:
                                validate_simulation_artifacts(request, artifacts, completed=False,
                                                              adapter_version=ADAPTER_VERSION)
                        except (ValueError, TypeError, KeyError):
                            raise AdapterExecutionError("CARLA worker returned an unverified failure response after execution; no retry or reference fallback") from None
                        raise AdapterExecutionError(detail, artifact_payloads=artifacts)
                    response.raise_for_status()
                    if response.status_code != 200:
                        raise ValueError("Unexpected worker execution status")
                    payload = read_json(response, WIRE_BYTES_LIMIT)
        except httpx.HTTPError as exc:
            error_type = AdapterExecutionError if execution_attempted else AdapterUnavailable
            raise error_type(f"CARLA worker request failed ({type(exc).__name__}); no reference fallback") from exc
        except AdapterInputError:
            raise
        except (ValueError, TypeError) as exc:
            error_type = AdapterExecutionError if execution_attempted else AdapterUnavailable
            raise error_type("CARLA worker returned malformed JSON") from exc
        try:
            result = decode_result(payload, request=request)
            if request is not None:
                validate_simulation_artifacts(request, result.artifact_payloads, completed=True,
                                              trajectory=result.trajectory, adapter_version=ADAPTER_VERSION)
            return result
        except (ValueError, KeyError, TypeError) as exc:
            raise AdapterExecutionError("CARLA worker returned an invalid result contract after execution") from exc

    def execute_attempt(self, request, *, expected_store_id=None):
        from .remote_durable import execute_attempt
        return execute_attempt(self, request, expected_store_id=expected_store_id)

    def inspect_execution(self, request):
        from .remote_durable import inspect_execution
        return inspect_execution(self, request)

    def retrieve_execution(self, request, *, expected_store_id, expected_archive_sha256):
        from .remote_durable import retrieve_execution
        return retrieve_execution(self, request, expected_store_id=expected_store_id,
                                  expected_archive_sha256=expected_archive_sha256)
