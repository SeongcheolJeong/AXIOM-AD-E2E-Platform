"""Run on a CARLA-compatible Python host, never on an unsupported local wheel.

python -m uvicorn axiom_carla.worker:app --host 127.0.0.1 --port 8100 --workers 1
"""
import hmac
import os
import threading

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from ade2e.adapters import AdapterExecutionError, AdapterInputError, AdapterUnavailable
from ade2e.schemas import Scenario, StrictModel
from ade2e.simulation_context import (AnyPreparedSimulationContext, AnySimulationExecutionRequest,
    simulation_input_hash)
from .runner import CarlaRunner
from .version import ADAPTER_VERSION
from .wire import encode_failure, encode_result

app = FastAPI(title="AXIOM CARLA worker · experimental", version=ADAPTER_VERSION)
_lock = threading.Lock()


class ContextPreflightRequest(StrictModel):
    scenario: Scenario
    context: AnyPreparedSimulationContext


def authenticate(authorization: str = Header(default="")) -> None:
    token = os.getenv("ADE2E_CARLA_WORKER_TOKEN")
    if token and not hmac.compare_digest(authorization, f"Bearer {token}"):
        raise HTTPException(401, "Worker bearer token required")


@app.get("/health", dependencies=[Depends(authenticate)])
def health():
    try:
        return {**CarlaRunner().preflight(), "worker_adapter_version": ADAPTER_VERSION}
    except (AdapterUnavailable, AdapterInputError) as exc:
        return {"status": "not_ready", "detail": str(exc), "worker_adapter_version": ADAPTER_VERSION}


def failed_response(detail, payloads=(), *, request=None):
    try:
        data = encode_failure(detail, payloads, request=request)
    except (ValueError, TypeError, AttributeError):
        data = encode_failure("CARLA execution failed; worker failure artifacts could not be encoded within the evidence contract.", request=request)
    return JSONResponse(status_code=500, content=data)


@app.post("/v1/simulate", dependencies=[Depends(authenticate)])
def simulate(scenario: Scenario):
    if os.getenv("ADE2E_CARLA_EXECUTION_STORE"):
        raise HTTPException(422, "Durable worker requires a reserved v3 execution attempt")
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "Worker is executing another trial; retry only after checking its outcome")
    try:
        result = CarlaRunner().execute(scenario)
        return encode_result(result)
    except AdapterInputError as exc:
        raise HTTPException(422, str(exc)) from exc
    except AdapterUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except AdapterExecutionError as exc:
        return failed_response(str(exc), exc.artifact_payloads)
    except Exception:
        return failed_response("Unexpected CARLA worker failure after the execution request; inspect the worker log.")
    finally:
        _lock.release()


@app.post("/v2/preflight", dependencies=[Depends(authenticate)])
def context_preflight(body: ContextPreflightRequest):
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "Worker is executing another trial; inspect its outcome before dispatch")
    try:
        evidence = CarlaRunner().preflight_with_context(body.scenario.model_copy(deep=True), body.context.model_copy(deep=True))
        if (not isinstance(evidence, dict) or evidence.get("status") != "ready"
                or not isinstance(evidence.get("version"), str) or not evidence["version"]
                or not isinstance(evidence.get("map"), str)
                or evidence["map"].split("/")[-1] != body.scenario.map_name):
            raise AdapterUnavailable("Native context preflight did not return matching engine/map evidence")
        preflight_schema = ('carla-execution-preflight/2' if body.context.schema_version == 'simulation-control-context/2'
                            else 'carla-execution-preflight/1')
        return {"schema_version": preflight_schema, "status": "ready",
                "worker_adapter_version": ADAPTER_VERSION,
                "input_hash": simulation_input_hash(body.scenario, "carla", ADAPTER_VERSION, body.context),
                "context_hash": body.context.context_hash,
                "kernel_version": body.context.stack_snapshot.kernel_version,
                "version": evidence["version"], "map": evidence["map"]}
    except AdapterInputError as exc:
        raise HTTPException(422, str(exc)) from exc
    except AdapterUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    finally:
        _lock.release()


@app.post("/v2/simulate", dependencies=[Depends(authenticate)])
def simulate_context(request: AnySimulationExecutionRequest):
    if os.getenv("ADE2E_CARLA_EXECUTION_STORE"):
        raise HTTPException(422, "Durable worker requires a reserved v3 execution attempt")
    if request.input_hash != simulation_input_hash(request.scenario, "carla", ADAPTER_VERSION, request.context):
        raise HTTPException(422, "Execution input hash does not match the pinned CARLA request")
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "Worker is executing another trial; retry only after checking its outcome")
    try:
        result = CarlaRunner().execute_with_context(request.model_copy(deep=True))
        return encode_result(result, request=request)
    except AdapterInputError as exc:
        raise HTTPException(422, str(exc)) from exc
    except AdapterUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except AdapterExecutionError as exc:
        return failed_response(str(exc), exc.artifact_payloads, request=request)
    except Exception:
        return failed_response("Unexpected CARLA worker failure after the execution request; inspect the worker log.", request=request)
    finally:
        _lock.release()

# Legacy wire protocols stay explicit; platform attempts use the durable v3
# envelope so both controller modes carry the reserved execution identity.
from .durable_worker import router as durable_router
app.include_router(durable_router(authenticate, _lock))
