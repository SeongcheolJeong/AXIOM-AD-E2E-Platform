"""Separate local execution worker. A lease loss never resubmits an engine call."""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import signal
import socket
import threading
import time
from uuid import uuid4

from fastapi import HTTPException

from .adapters import AdapterCancelled, AdapterCancelledBeforeDispatch, AdapterOwnershipLost, AdapterInputError, AdapterUnavailable, Registry
from .database import Database, Settings
from . import jobs
from .service import compute_run, validate_prepared_execution, run_request_hash
from .inference_runtime import RuntimeRegistry
from .sensor_decoders import SensorDecoderRegistry
from .evaluator_runtime import EvaluatorRegistry
from .stack_runtime import StackRegistry
from .worker_supervision import configure_worker_logging, run_polling_loop

logger = logging.getLogger(__name__)


def worker_identity(prefix: str = "worker") -> str:
    return f"{prefix}_{socket.gethostname()[:40]}_{os.getpid()}_{uuid4().hex[:12]}"


def run_claim(db: Database, registry: Registry, claim, *, lease_seconds: float = 30,
              runtime_registry=None, sensor_registry=None, evaluator_registry=None, stack_registry=None, experiment_registry=None):
    """Execute exactly this live claim, retaining distinct cancel/lost-owner states."""
    lost, done = threading.Event(), threading.Event()
    worker_id = claim.job.worker_id

    def heartbeat():
        while not done.wait(min(lease_seconds / 3, 5)):
            try:
                if not jobs.heartbeat_job(db, claim, lease_seconds):
                    lost.set()
                    return
            except Exception:
                logger.exception("Heartbeat failed for %s; fencing this worker", claim.job.id)
                lost.set()
                return

    next_check, cancelled = 0.0, False

    def should_cancel():
        nonlocal next_check, cancelled
        if lost.is_set():
            raise AdapterOwnershipLost("Worker lost its lease; engine outcome may be unknown")
        now = time.monotonic()
        if now >= next_check:
            try:
                cancelled = jobs.cancellation_requested(db, claim)
            except jobs.LostLease as exc:
                lost.set()
                raise AdapterOwnershipLost("Worker no longer owns publication") from exc
            next_check = now + 0.05
        return cancelled

    dispatched = False
    thread = threading.Thread(target=heartbeat, name=f"heartbeat-{claim.job.id}", daemon=True)
    jobs.touch_worker(db, worker_id, "running", claim.job.id)
    thread.start()
    try:
        if claim.kind == 'experiment':
            from .experiment_jobs import execute_claim, PreparedExperiment
            from .experiment_runtime import ExperimentHandlerMetadata
            from .experiment_defaults import default_experiment_registry
            experiment_registry = experiment_registry or default_experiment_registry(db)
            prepared = PreparedExperiment.from_payload(claim.prepared.to_payload())
            handler = experiment_registry.get(claim.job.adapter_id)
            metadata = ExperimentHandlerMetadata.model_validate(handler.metadata()) if handler is not None else None
            if (metadata is None or metadata.status != 'available' or metadata.version != claim.job.adapter_version
                    or prepared.input_hash != claim.job.input_hash
                    or metadata.model_dump(mode='json') != prepared.to_payload()['handler']):
                raise HTTPException(422, 'Pinned experiment handler/capability is unavailable; no execution dispatched')
            dispatched = jobs.mark_dispatched(db, claim)
            if not dispatched:
                return jobs.job_get(db, claim.job.id, kind=claim.kind)
            return execute_claim(db, claim, handler, should_cancel)
        if claim.kind == 'recovery':
            from .recoveries import execute_claim
            from .execution_attempt import supports_remote_recovery
            adapter = registry.get(claim.job.binding.attempt.adapter_id)
            with db.read_session() as session:
                reader_version = jobs._live(session, claim).adapter_version
            if (adapter is None or not supports_remote_recovery(adapter, version=reader_version)
                    or not supports_remote_recovery(adapter, version=claim.job.binding.attempt.adapter_version)):
                raise HTTPException(422, 'Remote recovery adapter is unavailable; no retrieval was dispatched')
            dispatched = jobs.mark_dispatched(db, claim)
            if not dispatched:
                return jobs.job_get(db, claim.job.id, kind=claim.kind)
            return execute_claim(db, claim, adapter, should_cancel)
        if claim.kind == "stack_replay":
            from .stack_replays import execute_claim
            from .stack_schemas import validate_stack_definition, stack_kernel_version
            stack_registry = stack_registry or StackRegistry()
            prepared = claim.prepared.to_payload()
            try:
                definition = validate_stack_definition(prepared["stack_snapshot"]["definition"])
                metadata = stack_registry.validate_definition(definition)
                if (prepared["kernel_version"] != stack_kernel_version(definition)
                        or prepared["kernel_version"] != prepared["stack_snapshot"]["kernel_version"]
                        or [item.model_dump(mode="json") for item in metadata] != prepared["stack_snapshot"]["component_metadata"]):
                    raise ValueError("Pinned stack kernel/component metadata changed")
            except (ValueError, KeyError, TypeError) as error:
                raise HTTPException(422, str(error)) from error
            dispatched = jobs.mark_dispatched(db, claim)
            if not dispatched:
                return jobs.job_get(db, claim.job.id, kind=claim.kind)
            return execute_claim(db, claim, stack_registry, sensor_registry or SensorDecoderRegistry(), should_cancel)
        if claim.kind == "evaluation":
            from .detection_evaluations import execute_claim
            evaluator_registry = evaluator_registry or EvaluatorRegistry()
            evaluator = evaluator_registry.get(claim.job.evaluator_id)
            if evaluator is None or evaluator.metadata().status != "available" or evaluator.metadata().version != claim.job.evaluator_version:
                raise HTTPException(422, "Pinned evaluator version is unavailable in this worker; evaluation was not dispatched")
            dispatched = jobs.mark_dispatched(db, claim)
            if not dispatched:
                return jobs.job_get(db, claim.job.id, kind=claim.kind)
            return execute_claim(db, claim, evaluator, should_cancel)
        if claim.kind == "perception":
            from .perception_jobs import execute_claim
            runtime_registry = runtime_registry or RuntimeRegistry()
            runtime = runtime_registry.get(claim.job.runtime_id)
            if runtime is None or runtime.metadata().status != "available" or runtime.metadata().version != claim.job.runtime_version:
                raise HTTPException(422, "Pinned inference runtime version is unavailable in this worker; no inference was dispatched")
            dispatched = jobs.mark_dispatched(db, claim)
            if not dispatched:
                return jobs.job_get(db, claim.job.id, kind=claim.kind)
            return execute_claim(db, claim, runtime, sensor_registry or SensorDecoderRegistry(), should_cancel)
        adapter = registry.get(claim.job.adapter_id)
        if adapter is None:
            raise HTTPException(422, "Pinned adapter is not installed in this worker")
        if claim.prepared.input_hash != claim.job.input_hash or claim.prepared.request_hash != run_request_hash(claim.job.request):
            raise HTTPException(422, 'Stored simulation input differs from the accepted job hashes')
        metadata = adapter.metadata()
        if metadata.status != "available" or metadata.version != claim.job.adapter_version:
            raise HTTPException(422, "Pinned adapter version is unavailable in this worker; no engine was dispatched")
        validate_prepared_execution(claim.prepared, adapter, stack_registry=stack_registry)
        from .execution_attempt import supports_remote_recovery, RemoteExecutionStatus
        expected_store_id = None
        if supports_remote_recovery(adapter):
            from .recoveries import bind_attempt
            from .service import execution_attempt_request
            request = execution_attempt_request(claim.prepared, claim.reserved_run_id)
            try:
                observed = adapter.inspect_execution(request.model_copy(deep=True))
            except (AdapterUnavailable, AdapterInputError) as error:
                raise HTTPException(422, f'Remote store could not be inspected before dispatch: {error}') from error
            expected_store_id = bind_attempt(db, claim, RemoteExecutionStatus.model_validate(observed.model_dump()))
        dispatched = jobs.mark_dispatched(db, claim)
        if not dispatched:
            return jobs.job_get(db, claim.job.id)
        computed = compute_run(claim.prepared, adapter, run_id=claim.reserved_run_id,
                               created_at=claim.job.created_at, should_cancel=should_cancel, expected_store_id=expected_store_id)
        if lost.is_set():
            raise AdapterOwnershipLost("Result arrived after worker ownership was lost")
        return jobs.finish_job(db, claim, computed)
    except AdapterCancelledBeforeDispatch:
        if lost.is_set():
            logger.warning("Pre-dispatch cancellation observed after ownership loss: %s", claim.job.id)
        else:
            try:
                return jobs.fail_job(db, claim, "Core stopped execution before the adapter call because cancellation was requested",
                                     status="cancelled", before_dispatch=True)
            except jobs.LostLease:
                pass
    except AdapterCancelled:
        if lost.is_set():
            logger.warning("Cancellation acknowledgement arrived after ownership loss: %s", claim.job.id)
        else:
            try:
                return jobs.fail_job(db, claim, "Adapter acknowledged cancellation", status="cancelled")
            except jobs.LostLease:
                pass
    except (AdapterOwnershipLost, jobs.LostLease):
        logger.warning("Discarding publication from expired owner: %s", claim.job.id)
    except Exception as exc:
        logger.exception("Job failed: %s", claim.job.id)
        message = str(exc.detail) if isinstance(exc, HTTPException) else "Worker failed to complete or publish execution; inspect its log using this job ID"
        try:
            known_preflight = (isinstance(exc, HTTPException) and exc.status_code == 422
                               and (claim.kind != 'experiment' or not dispatched))
            return jobs.fail_job(db, claim, message, outcome_uncertain=dispatched and not known_preflight,
                                 failure_http_status=exc.status_code if isinstance(exc, HTTPException) else 500)
        except jobs.LostLease:
            logger.warning("Failure arrived after ownership expiry: %s", claim.job.id)
    finally:
        done.set()
        thread.join(timeout=1)
        jobs.touch_worker(db, worker_id, "idle")
    jobs.expire_jobs(db)
    return jobs.job_get(db, claim.job.id, kind=claim.kind)


def run_once(db: Database, registry: Registry, worker_id: str, *, lease_seconds: float = 30,
             job_id: str | None = None, runtime_registry=None, sensor_registry=None, evaluator_registry=None, stack_registry=None, experiment_registry=None):
    jobs.expire_jobs(db)
    jobs.touch_worker(db, worker_id, "idle")
    runtime_registry = runtime_registry or RuntimeRegistry()
    evaluator_registry = evaluator_registry or EvaluatorRegistry()
    stack_registry = stack_registry or StackRegistry()
    from .experiment_defaults import default_experiment_registry
    experiment_registry = experiment_registry or default_experiment_registry(db)
    from .verification_plans import advance
    advance(db, runtime_registry, sensor_registry, evaluator_registry)
    from .hil_batches import advance as advance_hil_batches
    advance_hil_batches(db, experiment_registry)
    capabilities = {("simulation", item.id, item.version) for item in registry.list() if item.status == "available"}
    capabilities.update(("perception", item.id, item.version) for item in runtime_registry.list() if item.status == "available")
    capabilities.update(("evaluation", item.id, item.version) for item in evaluator_registry.list() if item.status == "available")
    capabilities.add(("stack_replay", "recorded-stack", "1"))
    capabilities.update(('experiment', item.id, item.version) for item in experiment_registry.list() if item.status == 'available')
    from .execution_attempt import remote_recovery_versions
    capabilities.update(('recovery', item.id, version) for item in registry.list()
                        for version in remote_recovery_versions(registry.get(item.id)))
    claim = jobs.claim_job(db, worker_id, lease_seconds, job_id, capabilities=capabilities)
    return run_claim(db, registry, claim, lease_seconds=lease_seconds,
                     runtime_registry=runtime_registry, sensor_registry=sensor_registry, evaluator_registry=evaluator_registry, stack_registry=stack_registry, experiment_registry=experiment_registry) if claim else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Claim at most one job then exit")
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--lease-seconds", type=float, default=30)
    parser.add_argument("--log-file", type=Path, help="Write private rotating logs to this file (parent must exist)")
    parser.add_argument("--log-max-bytes", type=int, default=10 * 1024 * 1024, help="Rotate at this many bytes (default: 10 MiB)")
    parser.add_argument("--log-backups", type=int, default=3, help="Number of rotated log files to retain (default: 3)")
    args = parser.parse_args()
    if not 0.05 <= args.poll_interval <= 60 or not 1 <= args.lease_seconds <= 300:
        parser.error("poll-interval must be 0.05..60 and lease-seconds 1..300")
    if not 1024 <= args.log_max_bytes <= 1024 * 1024 * 1024 or not 1 <= args.log_backups <= 20:
        parser.error("log-max-bytes must be 1024..1073741824 and log-backups 1..20")
    try:
        configure_worker_logging(args.log_file, max_bytes=args.log_max_bytes, backups=args.log_backups)
    except (OSError, ValueError) as error:
        parser.error(f"Cannot configure worker logging: {error}")
    db, identity = None, None
    try:
        try:
            db = Database(Settings())
            registry, runtime_registry, sensor_registry = Registry(), RuntimeRegistry(), SensorDecoderRegistry()
            evaluator_registry, stack_registry = EvaluatorRegistry(), StackRegistry()
            db.initialize()  # Never seed examples from a worker.
            # Installed entry points can change while this process is alive,
            # but Python processes editable .pth paths only at startup. Freeze
            # all handler factories before declaring readiness or claiming work.
            from .experiment_defaults import default_experiment_registry
            experiment_registry = default_experiment_registry(db)
        except ImportError as error:
            logger.error("Worker startup failed: required module/dependency is unavailable (%s). "
                         "Repair this environment and restart the worker; no jobs were claimed.", error)
            return 1
        except Exception as error:
            logger.error("Worker startup failed (%s): %s; no jobs were claimed", type(error).__name__, error)
            return 1
        identity, stopping = worker_identity(), threading.Event()
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: stopping.set())
        jobs.touch_worker(db, identity, "idle")
        print(f"AXIOM worker ready: {identity}", flush=True)
        return run_polling_loop(
            lambda: run_once(db, registry, identity, lease_seconds=args.lease_seconds,
                             runtime_registry=runtime_registry, sensor_registry=sensor_registry,
                             evaluator_registry=evaluator_registry, stack_registry=stack_registry,
                             experiment_registry=experiment_registry),
            stopping, poll_interval=args.poll_interval, once=args.once, logger=logger)
    finally:
        if db is not None:
            try:
                if identity is not None:
                    jobs.touch_worker(db, identity, "stopped")
            finally:
                db.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
