"""Versioned remote execution/outcome endpoints; no implicit resimulation."""
import os
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from ade2e.adapters import AdapterExecutionError, AdapterInputError, AdapterUnavailable
from ade2e.execution_attempt import ExecutionAttemptRequest
from ade2e.simulation_context import validate_simulation_adapter_context
from .durability import recording
from .runner import CarlaRunner
from .spool import Store, StoreConflict, StoreUnavailable
from .version import ADAPTER_VERSION, require_durable_version
from .wire import encode_failure, encode_result, initial_sensor_configuration_payload


def store():
    root = os.getenv('ADE2E_CARLA_EXECUTION_STORE')
    if not root:
        raise HTTPException(503, 'A private durable execution store must be configured before v3 execution')
    return Store(root)


def router(authenticate, legacy_lock):
    routes = APIRouter(prefix='/v3', dependencies=[Depends(authenticate)])

    def valid(request, *, dispatch=False):
        try:
            require_durable_version(request.adapter_version, dispatch=dispatch)
            if request.context:
                validate_simulation_adapter_context(request.context, request.adapter_version)
        except ValueError as error:
            raise HTTPException(422, 'Unsupported pinned adapter identity/version') from error
        if request.adapter_id != 'carla':
            raise HTTPException(422, 'Unsupported pinned adapter identity/version')

    @routes.post('/inspect')
    def inspect(request: ExecutionAttemptRequest):
        valid(request)
        try:
            return store().inspect(request)
        except StoreConflict as error:
            raise HTTPException(409, str(error)) from error
        except (StoreUnavailable, OSError, ValueError) as error:
            raise HTTPException(503, 'Durable execution status unavailable; no engine operation was performed') from error

    @routes.get('/executions/{execution_id}/archive')
    def archive(execution_id: str, store_id: str = Query(), archive_sha256: str = Query()):
        try:
            owned = store()
            if owned.id != store_id:
                raise StoreConflict('Store identity differs from the inspected execution')
            attempt = owned.find(execution_id)
            if attempt is None:
                raise HTTPException(404, 'Execution archive is not present in this store')
            attempt.recover_abandoned()
            if attempt.meta()['archive_sha256'] != archive_sha256:
                raise StoreConflict('Archive hash differs from the inspected terminal outcome')
            content = attempt.archive()
            return Response(content, media_type='application/json', headers={'X-Content-SHA256': archive_sha256})
        except StoreConflict as error:
            raise HTTPException(409, str(error)) from error
        except (StoreUnavailable, OSError, ValueError) as error:
            raise HTTPException(503, 'Durable archive could not be verified') from error

    @routes.get('/executions/{execution_id}/journal')
    def journal(execution_id: str):
        # Bounded metadata only; native source blobs remain in the private store.
        try:
            attempt = store().find(execution_id)
            if attempt is None:
                raise HTTPException(404, 'Execution journal is not present')
            return attempt.journal()
        except (StoreUnavailable, OSError, ValueError) as error:
            raise HTTPException(503, 'Native journal could not be verified') from error

    @routes.post('/simulate')
    def simulate(request: ExecutionAttemptRequest, store_id: str = Query()):
        valid(request, dispatch=True)
        if not legacy_lock.acquire(blocking=False):
            raise HTTPException(409, 'Another trial owns this worker; inspect its outcome')
        try:
            owned = store()
            if owned.id != store_id:
                raise StoreConflict('Durable store changed after dispatch binding; no execution accepted')
            with owned.begin(request) as attempt:
                started = time.perf_counter()
                context = request.simulation_request() if request.context is not None else None
                configurable = context is not None and context.context.schema_version == 'simulation-control-context/2'
                try:
                    if configurable:
                        # Preserve an explicit absent-readback prefix before
                        # entering any native connection or mutation path.
                        attempt.checkpoint([initial_sensor_configuration_payload(context)], 'before_native_entry')
                    with recording(attempt):
                        runner = CarlaRunner()
                        result = runner.execute_with_context(context) if context else runner.execute(request.scenario.model_copy(deep=True))
                    payload = encode_result(result, request=context)
                    attempt.seal('completed', payload, cleanup_status='confirmed',
                                 execution_ms=round((time.perf_counter()-started)*1000, 3))
                except (AdapterInputError, AdapterUnavailable, AdapterExecutionError) as error:
                    # Even a rejected/failed accepted identity is immutable. It
                    # can only be inspected/retrieved, not replayed by retry.
                    payloads = error.artifact_payloads if isinstance(error, AdapterExecutionError) else ()
                    if configurable and not any(p.kind == 'sensor_configuration' for p in payloads):
                        if attempt.meta()['mutations_started']:
                            raise StoreUnavailable('Native context/2 failure lost its sensor evidence; retain the committed prefix') from error
                        payloads = [*payloads, initial_sensor_configuration_payload(context)]
                    payload = encode_failure(str(error), payloads, request=context)
                    attempt.seal('failed', payload, execution_ms=round((time.perf_counter()-started)*1000, 3))
                except Exception:
                    # Preserve the last committed full prefix on unexpected
                    # failure, rather than replacing it with an empty response.
                    raise
                return Response(attempt.archive(), media_type='application/json')
        except StoreConflict as error:
            raise HTTPException(409, str(error)) from error
        except (StoreUnavailable, OSError, ValueError) as error:
            raise HTTPException(503, 'Durable execution could not finish; inspect this exact execution ID before taking action') from error
        finally:
            legacy_lock.release()

    return routes
