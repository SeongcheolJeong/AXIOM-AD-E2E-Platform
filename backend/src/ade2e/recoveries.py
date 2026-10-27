"""Fenced, read-only retrieval of immutable remote execution archives.

The source job and its original run remain unchanged. A successful retrieval
publishes a new run with the original execution identity in explicit lineage.
No function in this module invokes execute/execute_attempt or releases an
engine quarantine. Remote cleanup acknowledgements are not world-state proof.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from dataclasses import dataclass

from fastapi import Header, HTTPException, Query
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import func, select

from . import jobs, service
from .adapters import AdapterCancelled, AdapterInputError, AdapterUnavailable
from .artifact_transactions import track_created_artifact
from .database import RunRow
from .execution_attempt import ExecutionAttemptRequest, RemoteExecutionArchive, RemoteExecutionStatus, supports_remote_recovery
from .job_models import JobEventRow, JobRow
from .recovery_models import RecoveryRow, RemoteAttemptRow
from .recovery_schemas import (RecoveryCreate, RecoveryDetail, RecoveryPage, RecoverySummary,
                               RemoteExecutionBinding, RemoteExecutionInspection)
from .schemas import RecoveryLineage, RunDetail
from .service import PreparedRun, canonical_bytes, digest, uid, utcnow

RECOVERY_VERSION = 'remote-recovery/1'


def _integrity(message='Remote recovery evidence failed its integrity check'):
    return HTTPException(409, message)


def _binding(session, source: JobRow) -> tuple[RemoteAttemptRow | None, RemoteExecutionBinding | None]:
    row = session.get(RemoteAttemptRow, source.id)
    if row is None:
        return None, None
    try:
        payload = RemoteExecutionBinding.model_validate(row.payload)
        request = service.execution_attempt_request(PreparedRun.from_payload(source.prepared), source.reserved_run_id)
        if (digest(row.payload) != row.payload_hash or payload.attempt != request
                or row.execution_id != source.reserved_run_id or row.input_hash != source.input_hash
                or row.store_id != payload.store_id or row.request_hash != request.request_hash
                or payload.request_hash != request.request_hash):
            raise ValueError('Stored attempt differs from original job')
    except (ValueError, KeyError, TypeError) as error:
        raise _integrity() from error
    return row, payload


def bind_attempt(db, claim, status: RemoteExecutionStatus) -> str:
    """Pin the remote store before dispatch under the original live lease."""
    request = service.execution_attempt_request(claim.prepared, claim.reserved_run_id)
    status = RemoteExecutionStatus.model_validate(status.model_dump())
    status.validate_request(request)
    with jobs._write(db) as session:
        source = jobs._live(session, claim)
        if source.kind != 'simulation' or source.dispatched_at is not None:
            raise _integrity('Remote attempt binding must precede simulation dispatch')
        existing, binding = _binding(session, source)
        if existing is not None:
            status.validate_request(binding.attempt, binding.store_id)
            return existing.store_id
        if status.state != 'not_found':
            raise _integrity('A new reserved execution already exists remotely; dispatch was refused')
        binding = RemoteExecutionBinding(store_id=status.store_id, attempt=request, request_hash=request.request_hash)
        payload = binding.model_dump(mode='json')
        session.add(RemoteAttemptRow(source_job_id=source.id, execution_id=request.execution_id,
            store_id=status.store_id, request_hash=request.request_hash, input_hash=request.input_hash,
            payload=payload, payload_hash=digest(payload), created_at=utcnow()))
        jobs._event(session, source, 'remote_store_bound', 'Pinned remote execution store and request identity before dispatch.', utcnow())
    return status.store_id


def inspect_execution(db, registry, source_job_id: str) -> RemoteExecutionInspection:
    with db.read_session() as session:
        source = jobs._row(session, source_job_id)
        if source.kind != 'simulation':
            raise HTTPException(404, 'Simulation job not found')
        row, binding = _binding(session, source)
        adapter = registry.get(source.adapter_id)
        supported = binding is not None and adapter is not None and supports_remote_recovery(adapter, version=binding.attempt.adapter_version)
        reason = None if supported else 'This job has no durable remote binding or its recovery adapter is unavailable.'
        eligible = source.status in jobs.TERMINAL and source.status != 'completed' and source.dispatched_at is not None
        reply = RemoteExecutionInspection(source_job_id=source.id, source_run_id=source.run_id,
            source_execution_id=source.reserved_run_id, supported=supported, can_inspect=supported,
            can_retrieve=False, reason=reason, binding=binding, status=None,
            recovered_run_id=row.recovered_run_id if row else None)
    if not supported:
        return reply
    try:
        status = RemoteExecutionStatus.model_validate(adapter.inspect_execution(binding.attempt.model_copy(deep=True)).model_dump())
        status.validate_request(binding.attempt, binding.store_id)
    except Exception as error:
        # A failed status read is an explicit observation error, not a fabricated
        # remote terminal state or evidence that the old execution stopped.
        reply.reason = f'Remote status could not be verified: {str(error)[:500]}'
        return reply
    reply.status = status
    reply.can_retrieve = eligible and reply.recovered_run_id is None and status.archive_sha256 is not None
    if not eligible:
        reply.reason = 'Recovery requires a dispatched terminal source job without an already completed local result.'
    elif reply.recovered_run_id:
        reply.reason = 'Verified remote evidence is already linked; inspect the recovered run.'
    elif status.archive_sha256 is None:
        reply.reason = 'The remote store has no sealed terminal archive to retrieve.'
    return reply


@dataclass(frozen=True)
class PreparedRecovery:
    payload_json: bytes

    @classmethod
    def from_payload(cls, value):
        if not isinstance(value, dict) or set(value) != {'schema_version', 'binding', 'source_prepared', 'source_job_id', 'source_run_id', 'source_submission_seq', 'source_created_at', 'recovered_run_id'} or value['schema_version'] != RECOVERY_VERSION:
            raise ValueError('Invalid prepared recovery envelope')
        binding = RemoteExecutionBinding.model_validate(value['binding'])
        prepared = PreparedRun.from_payload(value['source_prepared'])
        request = service.execution_attempt_request(prepared, binding.attempt.execution_id)
        if request != binding.attempt or request.request_hash != binding.request_hash:
            raise ValueError('Recovery input differs from its source attempt')
        return cls(canonical_bytes(value))

    def to_payload(self):
        return json.loads(self.payload_json)


def _subject(session, job: JobRow) -> RecoveryRow:
    row = session.get(RecoveryRow, job.id)
    if job.kind != 'recovery' or row is None:
        raise HTTPException(404, 'Execution recovery not found')
    payload = PreparedRecovery.from_payload(job.prepared).to_payload()
    request = RecoveryCreate.model_validate(job.request)
    if (digest(payload) != job.input_hash or row.source_job_id != payload['source_job_id']
            or row.source_run_id != payload['source_run_id'] or row.source_execution_id != payload['binding']['attempt']['execution_id']
            or request.source_job_id != row.source_job_id or job.adapter_id != payload['binding']['attempt']['adapter_id']
            or job.request_hash != digest(job.request)):
        raise _integrity()
    return row


def _detail_data(session, job: JobRow, row: RecoveryRow) -> RecoveryDetail:
    prepared = PreparedRecovery.from_payload(job.prepared).to_payload()
    events = list(session.scalars(select(JobEventRow).where(JobEventRow.job_id == job.id)
        .order_by(JobEventRow.sequence.desc()).limit(jobs.EVENT_LIMIT)))
    count = session.scalar(select(func.count()).select_from(JobEventRow).where(JobEventRow.job_id == job.id))
    return RecoveryDetail(id=job.id, submission_seq=job.submission_seq, source_job_id=row.source_job_id,
        source_run_id=row.source_run_id, source_execution_id=row.source_execution_id, status=job.status,
        outcome=row.outcome, created_at=job.created_at, updated_at=job.updated_at, finished_at=job.finished_at,
        note=job.request['note'], run_id=row.run_id, error=job.error, remote_status=row.remote_status,
        manifest_hash=row.manifest_hash, manifest_size_bytes=row.manifest_size_bytes,
        download_url=f'/api/v1/execution-recoveries/{job.id}/export' if row.manifest_hash else None,
        archive_hash=row.archive_hash, archive_size_bytes=row.archive_size_bytes,
        archive_download_url=f'/api/v1/execution-recoveries/{job.id}/archive' if row.archive_hash else None,
        binding=prepared['binding'], events=[{'at': e.at, 'type': e.type, 'message': e.message} for e in reversed(events)],
        events_total=count, worker_id=job.worker_id, lease_expires_at=job.lease_expires_at, cancel_requested_at=job.cancel_requested_at)


def detail(session, job: JobRow) -> RecoveryDetail:
    try:
        row = _subject(session, job)
        result = _detail_data(session, job, row)
        if job.status in jobs.TERMINAL:
            if (row.manifest is None or hashlib.sha256(row.manifest).hexdigest() != row.manifest_hash
                    or len(row.manifest) != row.manifest_size_bytes):
                raise ValueError('Terminal recovery manifest is missing or corrupt')
            manifest = json.loads(row.manifest)
            current = result.model_dump(mode='json')
            for name in ('manifest_hash', 'manifest_size_bytes', 'download_url'):
                current[name] = None
            if (manifest.get('schema_version') != RECOVERY_VERSION or manifest.get('detail') != current
                    or manifest.get('prepared_hash') != job.input_hash):
                raise ValueError('Recovery metadata differs from its sealed manifest')
        elif row.manifest is not None:
            raise ValueError('A nonterminal recovery has sealed evidence')
        return result
    except (ValueError, TypeError, KeyError) as error:
        raise _integrity() from error


def seal_terminal(session, job: JobRow):
    row = _subject(session, job)
    if row.manifest is not None:
        return
    if row.outcome == 'pending':
        row.outcome = 'failed'
    payload = {'schema_version': RECOVERY_VERSION, 'detail': _detail_data(session, job, row).model_dump(mode='json'),
               'prepared_hash': job.input_hash, 'source_prepared': job.prepared['source_prepared']}
    if row.run_id is not None:
        payload['run'] = RunDetail.model_validate(session.get(RunRow, row.run_id).payload).model_dump(mode='json')
    content = canonical_bytes(payload)
    if len(content) > 32*1024*1024:
        raise ValueError('Recovery manifest exceeds 32 MiB')
    row.manifest, row.manifest_hash, row.manifest_size_bytes = content, hashlib.sha256(content).hexdigest(), len(content)


def submit_recovery(db, registry, body: RecoveryCreate, key: str) -> RecoveryDetail:
    if not isinstance(key, str) or not key.strip() or len(key) > 200:
        raise HTTPException(422, 'Recovery requires a nonempty Idempotency-Key of at most 200 characters')
    request = body.model_dump(mode='json')
    request_hash = digest(request)
    with jobs._write(db) as session:
        existing = session.scalar(select(JobRow).where(JobRow.idempotency_key == f'recovery:{key}'))
        if existing is not None:
            if existing.kind != 'recovery' or existing.request_hash != request_hash:
                raise HTTPException(409, 'Idempotency-Key was used for a different recovery request')
            return detail(session, existing)
        source = jobs._row(session, body.source_job_id)
        if source.kind != 'simulation' or source.status not in jobs.TERMINAL or source.status == 'completed' or source.dispatched_at is None:
            raise HTTPException(409, 'Recovery requires a dispatched terminal simulation job without a completed local result')
        attempt, binding = _binding(session, source)
        adapter = registry.get(source.adapter_id)
        if (binding is None or adapter is None or not supports_remote_recovery(adapter, version=binding.attempt.adapter_version)
                or not supports_remote_recovery(adapter, version=adapter.metadata().version)):
            raise HTTPException(422, 'This source job has no available durable remote recovery capability')
        if attempt.recovered_run_id is not None:
            raise HTTPException(409, {'message': 'Remote result was already recovered', 'run_id': attempt.recovered_run_id})
        prepared = {'schema_version': RECOVERY_VERSION, 'binding': binding.model_dump(mode='json'),
            'source_prepared': source.prepared, 'source_job_id': source.id, 'source_run_id': source.run_id,
            'source_submission_seq': source.submission_seq, 'source_created_at': source.created_at, 'recovered_run_id': uid('run')}
        PreparedRecovery.from_payload(prepared)
        now = utcnow()
        job = JobRow(id=uid('recovery'), kind='recovery', idempotency_key=f'recovery:{key}', request=request,
            request_hash=request_hash, prepared=prepared, input_hash=digest(prepared),
            adapter_id=adapter.metadata().id, adapter_version=adapter.metadata().version,
            status='queued', created_at=now, updated_at=now, cancellation_supported=True,
            resource_required=False, outcome_uncertain=False, evidence_eligible=False)
        session.add(job)
        session.flush()
        session.add(RecoveryRow(job_id=job.id, source_job_id=source.id, source_run_id=source.run_id,
                                source_execution_id=source.reserved_run_id, outcome='pending'))
        jobs._event(session, job, 'queued', 'Read-only remote retrieval queued. The original execution will not be dispatched again.', now)
        session.flush()
        return detail(session, job)


def _check_source(session, prepared):
    source = jobs._row(session, prepared['source_job_id'])
    if source.status not in jobs.TERMINAL or source.status == 'completed' or source.run_id != prepared['source_run_id']:
        raise _integrity('Source execution is not the pinned terminal attempt')
    row, binding = _binding(session, source)
    if binding is None or binding.model_dump(mode='json') != prepared['binding'] or source.prepared != prepared['source_prepared'] or source.submission_seq != prepared['source_submission_seq']:
        raise _integrity()
    return source, row


def execute_claim(db, claim, adapter, should_cancel):
    def checkpoint():
        if should_cancel():
            raise AdapterCancelled('Read-only recovery cancelled before publication')
    prepared = claim.prepared.to_payload()
    binding = RemoteExecutionBinding.model_validate(prepared['binding'])
    checkpoint()
    with db.read_session() as session:
        reader = jobs._live(session, claim)
        if (not supports_remote_recovery(adapter, version=reader.adapter_version)
                or not supports_remote_recovery(adapter, version=binding.attempt.adapter_version)):
            raise HTTPException(422, 'Pinned remote source/reader version is unsupported; no retrieval was attempted')
    status = RemoteExecutionStatus.model_validate(adapter.inspect_execution(binding.attempt.model_copy(deep=True)).model_dump())
    status.validate_request(binding.attempt, binding.store_id)
    checkpoint()
    if status.archive_sha256 is None:
        with jobs._write(db) as session:
            job = jobs._live(session, claim)
            if job.cancel_requested_at is not None:
                raise AdapterCancelled('Recovery cancelled before publication')
            _check_source(session, prepared)
            row = _subject(session, job)
            row.remote_status, row.outcome = status.model_dump(mode='json'), 'not_available'
            jobs._terminal(session, job, 'completed', utcnow(), 'No sealed terminal archive is available. No engine was dispatched.')
            return detail(session, job)
    raw = adapter.retrieve_execution(binding.attempt.model_copy(deep=True), expected_store_id=binding.store_id,
                                     expected_archive_sha256=status.archive_sha256)
    archive = RemoteExecutionArchive.model_validate(raw.model_dump() if isinstance(raw, RemoteExecutionArchive) else raw)
    archive.status.validate_request(binding.attempt, binding.store_id)
    # The observation timestamp can advance; every terminal identity and state
    # must match the exact inspected immutable archive.
    if archive.status.model_dump(exclude={'observed_at'}) != status.model_dump(exclude={'observed_at'}):
        raise ValueError('Remote terminal evidence changed between inspection and retrieval')
    checkpoint()
    lineage = RecoveryLineage(recovery_id=claim.job.id, source_job_id=prepared['source_job_id'],
        source_run_id=prepared['source_run_id'], source_execution_id=binding.attempt.execution_id,
        source_submission_seq=prepared['source_submission_seq'], store_id=binding.store_id,
        request_hash=binding.request_hash, archive_sha256=status.archive_sha256,
        archive_kind=status.archive_kind, recovered_at=utcnow())
    source_prepared = PreparedRun.from_payload(prepared['source_prepared'])
    computed = service.compute_recovered_run(source_prepared, archive, run_id=prepared['recovered_run_id'],
        created_at=prepared['source_created_at'], lineage=lineage)
    checkpoint()
    staged = service.stage_run(db, source_prepared, computed, claim.token)
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix='recovery_', dir=db.recovery_staging_dir)
        temporary = Path(name)
        with os.fdopen(fd, 'wb') as stream:
            if stream.write(archive.archive_bytes) != len(archive.archive_bytes):
                raise OSError('Incomplete remote archive write')
            stream.flush()
            os.fsync(stream.fileno())
        checkpoint()
        with jobs._write(db) as session:
            job = jobs._live(session, claim)
            if job.cancel_requested_at is not None:
                raise AdapterCancelled('Recovery cancelled before publication')
            source, attempt = _check_source(session, prepared)
            row = _subject(session, job)
            if attempt.accepted_archive_hash is not None and attempt.accepted_archive_hash != status.archive_sha256:
                raise _integrity('A different terminal archive was already accepted for this execution')
            if attempt.recovered_run_id is None:
                run = service.publish_run(session, db, source_prepared, computed, staged=staged)
                attempt.recovered_run_id, attempt.accepted_archive_hash = run.id, status.archive_sha256
            else:
                # Concurrent identical retrievals converge on the one accepted
                # run, retaining each operation's own immutable audit manifest.
                run = service.run_get(session, attempt.recovered_run_id)
            row.run_id = run.id
            row.remote_status = archive.status.model_dump(mode='json')
            row.outcome = 'recovered' if run.status == 'completed' else 'diagnostics_only'
            storage_key = f'{uid("archive")}.json'
            final = db.recovery_dir / storage_key
            final.hardlink_to(temporary)
            track_created_artifact(session, final)
            row.archive_storage_key, row.archive_hash, row.archive_size_bytes = storage_key, status.archive_sha256, len(archive.archive_bytes)
            jobs._live(session, claim)
            jobs._terminal(session, job, 'completed', utcnow(), 'Verified remote archive and supplemental run published. Original history and engine quarantine were preserved.')
            return detail(session, job)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        service.cleanup_staged_run(db, staged)


def resolved_job_run(session, source: JobRow) -> RunDetail | None:
    """Resolve within a selected original job; never change submission order."""
    if source.kind != 'simulation':
        return None
    if source.status == 'completed' and source.run_id:
        return service.run_get(session, source.run_id)
    attempt, binding = _binding(session, source)
    if attempt is None or attempt.recovered_run_id is None:
        return None
    run = service.run_get(session, attempt.recovered_run_id)
    if (run.recovery is None or run.recovery.source_job_id != source.id
            or run.recovery.source_execution_id != binding.attempt.execution_id
            or run.recovery.archive_sha256 != attempt.accepted_archive_hash
            or run.provenance.input_hash != source.input_hash):
        raise _integrity()
    recovery_job = jobs._row(session, run.recovery.recovery_id)
    evidence = detail(session, recovery_job)
    if evidence.run_id != run.id or evidence.archive_hash != attempt.accepted_archive_hash:
        raise _integrity()
    manifest = json.loads(session.get(RecoveryRow, recovery_job.id).manifest)
    if manifest.get('run') != run.model_dump(mode='json'):
        raise _integrity('Recovered run differs from its sealed recovery evidence')
    return run if run.status == 'completed' else None


def verify_recovered_run(session, run: RunDetail) -> None:
    if run.recovery is None:
        return
    lineage = run.recovery
    job = jobs._row(session, lineage.recovery_id)
    evidence = detail(session, job)
    subject = session.get(RecoveryRow, job.id)
    source = jobs._row(session, lineage.source_job_id)
    attempt, binding = _binding(session, source)
    if (attempt is None or attempt.recovered_run_id != run.id or evidence.run_id != run.id
            or lineage.source_run_id != source.run_id or lineage.source_execution_id != source.reserved_run_id
            or lineage.source_submission_seq != source.submission_seq or lineage.store_id != binding.store_id
            or lineage.request_hash != binding.request_hash or lineage.archive_sha256 != attempt.accepted_archive_hash
            or json.loads(subject.manifest).get('run') != run.model_dump(mode='json')):
        raise _integrity('Recovered run differs from its source and sealed retrieval evidence')


def register_recovery_routes(api, db, registry):
    @api.get('/jobs/{job_id}/remote-execution', response_model=RemoteExecutionInspection, tags=['recovery'])
    def inspect_remote(job_id: str):
        return inspect_execution(db, registry, job_id)

    @api.post('/execution-recoveries', response_model=RecoveryDetail, status_code=202, tags=['recovery'])
    def post_recovery(body: RecoveryCreate, idempotency_key: str = Header(alias='Idempotency-Key')):
        return submit_recovery(db, registry, body, idempotency_key)

    @api.get('/execution-recoveries', response_model=RecoveryPage, tags=['recovery'])
    def list_recoveries(source_job_id: str | None = None, limit: int = Query(100, ge=1, le=100), before_seq: int | None = Query(None, ge=1)):
        with db.read_session() as session:
            query = select(JobRow).join(RecoveryRow, RecoveryRow.job_id == JobRow.id).order_by(JobRow.submission_seq.desc())
            if source_job_id is not None:
                query = query.where(RecoveryRow.source_job_id == source_job_id)
            if before_seq is not None:
                query = query.where(JobRow.submission_seq < before_seq)
            rows = list(session.scalars(query.limit(limit+1)))
            items = []
            for row in rows[:limit]:
                value = detail(session, row)
                items.append(RecoverySummary(**{key: getattr(value, key) for key in RecoverySummary.model_fields}))
            return RecoveryPage(items=items, next_before_seq=rows[limit-1].submission_seq if len(rows)>limit else None)

    @api.get('/execution-recoveries/{recovery_id}', response_model=RecoveryDetail, tags=['recovery'])
    def get_recovery(recovery_id: str):
        return jobs.job_get(db, recovery_id, kind='recovery')

    @api.post('/execution-recoveries/{recovery_id}/cancel', response_model=RecoveryDetail, tags=['recovery'])
    def cancel_recovery(recovery_id: str):
        return jobs.cancel_job(db, recovery_id, kind='recovery')

    @api.get('/execution-recoveries/{recovery_id}/export', tags=['recovery'])
    def export_recovery(recovery_id: str):
        with db.read_session() as session:
            job = jobs._row(session, recovery_id)
            evidence = detail(session, job)
            if evidence.manifest_hash is None:
                raise HTTPException(409, 'Recovery evidence is not yet terminal')
            content = session.get(RecoveryRow, recovery_id).manifest
        return Response(content, media_type='application/json', headers={'Content-Disposition': f'attachment; filename="{recovery_id}.json"', 'X-Content-SHA256': evidence.manifest_hash})

    @api.get('/execution-recoveries/{recovery_id}/archive', tags=['recovery'])
    def export_archive(recovery_id: str):
        with db.read_session() as session:
            evidence = detail(session, jobs._row(session, recovery_id))
            row = session.get(RecoveryRow, recovery_id)
            if row.archive_storage_key is None:
                raise HTTPException(404, 'This recovery has no verified archive')
            path = db.recovery_dir / row.archive_storage_key
            if Path(row.archive_storage_key).name != row.archive_storage_key or path.parent.resolve() != db.recovery_dir.resolve() or path.is_symlink() or not path.is_file() or path.stat().st_size != row.archive_size_bytes:
                raise _integrity('Managed recovery archive is missing or has invalid size')
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != row.archive_hash:
                raise _integrity('Managed recovery archive digest changed')
        return Response(content, media_type='application/json', headers={'Content-Disposition': f'attachment; filename="{recovery_id}.remote.json"', 'X-Content-SHA256': evidence.archive_hash})
