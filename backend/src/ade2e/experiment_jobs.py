"""Experiments use the existing durable queue and fenced artifact publication."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from fastapi import HTTPException
from sqlalchemy import func, select
from . import jobs
from .adapters import AdapterCancelledBeforeDispatch, AdapterOwnershipLost
from .experiment_models import ExperimentRow, ExperimentExecutionRow, ExperimentArtifactRow
from .experiment_runtime import ExperimentHandlerMetadata, ExperimentExecution
from .experiment_schemas import ExperimentCreate, ExperimentJobDetail
from .job_models import JobRow, JobEventRow
from .service import canonical_bytes, digest, uid, utcnow

MANIFEST_LIMIT = 4 * 1024 * 1024
INPUT_MANIFEST_LIMIT = 2 * 1024 * 1024
SUMMARY_LIMIT = 1024 * 1024
MAX_NATIVE_FILES = 256
ADAS_MODULE_NATIVE_FILES = 512
ARTIFACT_LIMIT = 2 * 1024 ** 3
TOTAL_ARTIFACT_LIMIT = 8 * 1024 ** 3


def native_file_limit(prepared):
    """Only the pinned four-camera simulation needs 240 per-frame image previews."""
    spec = prepared.get('spec', {})
    config = prepared.get('input', {}).get('test', {}).get('config', {})
    if (spec.get('kind') == 'engineering_lab' and spec.get('adapter_id') == 'native-engineering-lab'
            and config.get('engine') == 'adas_module_rtl' and config.get('pipeline_id') == 'adas-camera-4ch/1'):
        return ADAS_MODULE_NATIVE_FILES
    if (spec.get('kind') == 'engineering_lab' and spec.get('adapter_id') == 'native-engineering-lab'
            and config.get('engine') == 'adas_validation_campaign' and config.get('campaign_version') == 'adas-preboard-campaign/1'):
        return 8192
    return MAX_NATIVE_FILES

@dataclass(frozen=True)
class PreparedExperiment:
    content: bytes

    @classmethod
    def from_payload(cls, value):
        if not isinstance(value, dict) or set(value) != {'schema_version', 'spec', 'handler', 'input'} or value['schema_version'] != 1:
            raise ValueError('Unknown prepared experiment contract')
        spec = ExperimentCreate.model_validate(value['spec'])
        metadata = ExperimentHandlerMetadata.model_validate(value['handler'])
        if (metadata.id != spec.adapter_id or spec.kind not in metadata.kinds or metadata.status != 'available'
                or spec.adapter_version != metadata.version or not isinstance(value['input'], dict)):
            raise ValueError('Prepared experiment differs from its pinned handler')
        content = canonical_bytes(value)
        if len(content) > INPUT_MANIFEST_LIMIT:
            raise ValueError('Experiment input snapshot exceeds 2 MiB; use immutable file references')
        return cls(content)

    def to_payload(self):
        return json.loads(self.content)

    @property
    def input_hash(self):
        return hashlib.sha256(self.content).hexdigest()

def prepare(registry, body: ExperimentCreate):
    handler = registry.get(body.adapter_id)
    if handler is None:
        raise HTTPException(422, 'Experiment handler is not installed')
    metadata = ExperimentHandlerMetadata.model_validate(handler.metadata())
    if metadata.status != 'available' or body.kind not in metadata.kinds:
        raise HTTPException(422, 'Experiment handler is not configured for this execution kind')
    if body.adapter_version is not None and body.adapter_version != metadata.version:
        raise HTTPException(422, 'Requested experiment handler version is unavailable')
    spec = body.model_copy(update={'adapter_version': metadata.version})
    try:
        snapshot = handler.prepare(spec.model_copy(deep=True))
        return PreparedExperiment.from_payload({'schema_version': 1, 'spec': spec.model_dump(mode='json'),
                                                'handler': metadata.model_dump(mode='json'), 'input': snapshot})
    except HTTPException:
        raise
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc

def experiment_get(session, identity):
    row = session.get(ExperimentRow, identity)
    if row is None:
        raise HTTPException(404, 'Experiment not found')
    try:
        if hashlib.sha256(row.manifest).hexdigest() != row.manifest_hash:
            raise ValueError('Manifest content hash differs')
        value = json.loads(row.manifest)
        prepared = PreparedExperiment.from_payload(value['prepared'])
        spec = prepared.to_payload()['spec']
        if (value['id'] != row.id or value['project_id'] != row.project_id or spec['project_id'] != row.project_id
                or spec['name'] != row.name or spec['kind'] != row.kind or value['preview_hash'] != prepared.input_hash):
            raise ValueError('Experiment identity differs from its frozen manifest')
        return row, value, prepared
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(409, 'Experiment manifest failed its integrity check') from exc

def _key(project_id, key, domain):
    if not isinstance(key, str) or not key or len(key) > 200:
        raise HTTPException(422, 'Idempotency-Key must contain 1..200 characters')
    return domain + ':' + project_id + ':' + hashlib.sha256(key.encode()).hexdigest()

def create(db, registry, body, key):
    request_key = _key(body.project_id, key, 'experiment-create')
    request_hash = digest(body.model_dump(mode='json'))
    with jobs._write(db) as session:
        existing = session.scalar(select(ExperimentRow).where(ExperimentRow.request_key == request_key))
        if existing:
            if existing.request_hash != request_hash:
                raise HTTPException(409, 'Idempotency-Key identifies a different experiment definition')
            return experiment_get(session, existing.id)[1] | {'manifest_hash': existing.manifest_hash}
    prepared = prepare(registry, body)
    with jobs._write(db) as session:
        return create_prepared_row(session, prepared, key, request_hash=request_hash)


def create_prepared_row(session, prepared, key, *, request_hash=None):
    """Insert a frozen experiment in an existing queue transaction; no engine calls."""
    body = ExperimentCreate.model_validate(prepared.to_payload()['spec'])
    request_key = _key(body.project_id, key, 'experiment-create')
    request_hash = request_hash or digest(body.model_dump(mode='json'))
    existing = session.scalar(select(ExperimentRow).where(ExperimentRow.request_key == request_key))
    if existing:
        if existing.request_hash != request_hash:
            raise HTTPException(409, 'Idempotency-Key identifies a different experiment definition')
        return experiment_get(session, existing.id)[1] | {'manifest_hash': existing.manifest_hash}
    identity, now = uid('experiment'), utcnow()
    value = {'schema_version': 1, 'id': identity, 'project_id': body.project_id, 'created_at': now,
             'preview_hash': prepared.input_hash, 'prepared': prepared.to_payload()}
    content = canonical_bytes(value)
    row = ExperimentRow(id=identity, project_id=body.project_id, name=body.name, kind=body.kind, created_at=now,
                        manifest=content, manifest_hash=hashlib.sha256(content).hexdigest(), request_key=request_key,
                        request_hash=request_hash)
    session.add(row)
    return value | {'manifest_hash': row.manifest_hash}

def submit(db, registry, identity, preview_hash, key):
    with jobs._write(db) as session:
        return submit_row(session, registry, identity, preview_hash, key)


def submit_row(session, registry, identity, preview_hash, key):
    """Queue one frozen experiment atomically with a caller's durable membership."""
    experiment, _, prepared = experiment_get(session, identity)
    if preview_hash != prepared.input_hash:
        raise HTTPException(409, 'Submission does not match the frozen experiment preview')
    request = {'experiment_id': identity, 'preview_hash': preview_hash, 'project_id': experiment.project_id}
    request_hash = digest(request)
    request_key = _key(experiment.project_id, key, 'experiment-submit')
    existing = session.scalar(select(JobRow).where(JobRow.idempotency_key == request_key))
    if existing:
        if existing.kind != 'experiment' or existing.request_hash != request_hash:
            raise HTTPException(409, 'Idempotency-Key identifies another experiment execution')
        return detail(session, existing)
    value = prepared.to_payload()
    metadata = ExperimentHandlerMetadata.model_validate(value['handler'])
    current = registry.get(metadata.id)
    current_metadata = ExperimentHandlerMetadata.model_validate(current.metadata()) if current is not None else None
    if (current_metadata is None or current_metadata.version != metadata.version or current_metadata.status != 'available'):
        raise HTTPException(422, 'Pinned experiment handler is unavailable; no job was submitted')
    from .object_beta_access import admit_experiment
    admit_experiment(session, experiment.project_id, value['spec'], metadata)
    now = utcnow()
    row = JobRow(id=uid('job'), idempotency_key=request_key, request_hash=request_hash, request=request,
                 prepared=value, input_hash=prepared.input_hash, kind='experiment', adapter_id=metadata.id,
                 adapter_version=metadata.version, status='queued', created_at=now, updated_at=now,
                 resource_required=metadata.resource_id is not None, resource_key=metadata.resource_id,
                 cancellation_supported=metadata.cancellation_supported, evidence_eligible=False, outcome_uncertain=False)
    session.add(row)
    session.flush()
    session.add(ExperimentExecutionRow(job_id=row.id, experiment_id=identity, project_id=experiment.project_id))
    session.flush()
    jobs._event(session, row, 'queued', 'Immutable experiment input and handler version saved; no execution dispatched.', now)
    return detail(session, row)

def _subject(session, row):
    subject = session.get(ExperimentExecutionRow, row.id)
    if row.kind != 'experiment' or subject is None:
        raise HTTPException(404, 'Experiment execution not found')
    prepared = PreparedExperiment.from_payload(row.prepared)
    spec = prepared.to_payload()['spec']
    if (prepared.input_hash != row.input_hash or spec['project_id'] != subject.project_id
            or row.request['experiment_id'] != subject.experiment_id or row.request['project_id'] != subject.project_id
            or digest(row.request) != row.request_hash or spec['adapter_id'] != row.adapter_id
            or spec['adapter_version'] != row.adapter_version or row.request['preview_hash'] != prepared.input_hash
            or row.resource_key != prepared.to_payload()['handler']['resource_id']
            or row.resource_required != (row.resource_key is not None)
            or row.cancellation_supported != prepared.to_payload()['handler']['cancellation_supported']):
        raise HTTPException(409, 'Experiment execution input failed its integrity check')
    experiment, _, original = experiment_get(session, subject.experiment_id)
    if original.content != prepared.content or experiment.project_id != subject.project_id:
        raise HTTPException(409, 'Execution input differs from its immutable experiment')
    return subject, prepared

def _artifact(row):
    return {'id': row.id, 'name': row.name, 'media_type': row.media_type, 'sha256': row.sha256,
            'size_bytes': row.size_bytes, 'download_url': f'/api/v1/experiments/executions/{row.job_id}/artifacts/{row.id}'}

def _unsealed_detail(session, row):
    subject, prepared = _subject(session, row)
    events = list(session.scalars(select(JobEventRow).where(JobEventRow.job_id == row.id).order_by(JobEventRow.sequence)))
    artifacts = [_artifact(item) for item in session.scalars(select(ExperimentArtifactRow).where(
        ExperimentArtifactRow.job_id == row.id).order_by(ExperimentArtifactRow.id))]
    return ExperimentJobDetail(id=row.id, experiment_id=subject.experiment_id, project_id=subject.project_id,
        status=row.status, experiment_kind=prepared.to_payload()['spec']['kind'], adapter_id=row.adapter_id,
        adapter_version=row.adapter_version, created_at=row.created_at, updated_at=row.updated_at,
        started_at=row.started_at, finished_at=row.finished_at, dispatched_at=row.dispatched_at, worker_id=row.worker_id,
        input_hash=row.input_hash, error=row.error, outcome_uncertain=row.outcome_uncertain,
        cancellation_supported=row.cancellation_supported, cancel_requested_at=row.cancel_requested_at,
        failure_http_status=row.failure_http_status, summary=subject.summary, fidelity=subject.fidelity, artifacts=artifacts,
        events=[{'at': item.at, 'type': item.type, 'message': item.message} for item in events])

def seal_terminal(session, row):
    subject, prepared = _subject(session, row)
    if row.status not in jobs.TERMINAL or subject.manifest is not None:
        raise HTTPException(409, 'Only an unsealed terminal experiment execution can be sealed')
    session.flush()
    content = canonical_bytes({'schema_version': 1, 'execution': _unsealed_detail(session, row).model_dump(mode='json'),
                               'prepared': prepared.to_payload()})
    if len(content) > MANIFEST_LIMIT:
        raise ValueError('Experiment output manifest exceeds 4 MiB')
    subject.manifest, subject.manifest_hash = content, hashlib.sha256(content).hexdigest()

def detail(session, row):
    session.flush()
    value = _unsealed_detail(session, row)
    subject = session.get(ExperimentExecutionRow, row.id)
    if row.status in jobs.TERMINAL:
        content = canonical_bytes({'schema_version': 1, 'execution': value.model_dump(mode='json'), 'prepared': row.prepared})
        if subject.manifest != content or subject.manifest_hash != hashlib.sha256(content).hexdigest():
            raise HTTPException(409, 'Terminal experiment execution manifest failed its integrity check')
        value = value.model_copy(update={'manifest_hash': subject.manifest_hash,
                                        'download_url': f'/api/v1/experiments/executions/{row.id}/export'})
    return value

def execute_claim(db, claim, handler, should_cancel):
    with db.read_session() as session:
        row = jobs._live(session, claim)
        _, prepared = _subject(session, row)
    if should_cancel():
        raise AdapterCancelledBeforeDispatch()
    work_dir = db.experiment_dir / 'attempts' / claim.job.id
    work_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    (work_dir / 'input-manifest.json').write_bytes(prepared.content)
    output = handler.execute(prepared.to_payload()['input'], work_dir, should_cancel)
    if not isinstance(output, ExperimentExecution):
        raise ValueError('Experiment handler returned an invalid execution result')
    if not isinstance(output.summary, dict) or len(canonical_bytes(output.summary)) > SUMMARY_LIMIT:
        raise ValueError('Experiment summary must fit the bounded JSON manifest')
    if not output.fidelity or output.fidelity == 'unclassified':
        raise ValueError('Experiment result must declare its actual execution fidelity')
    file_limit = native_file_limit(prepared.to_payload())
    if len(output.artifacts) > file_limit:
        raise ValueError(f'Experiment output exceeds {file_limit} files; use a native bundle/index')
    stage = Path(tempfile.mkdtemp(prefix=claim.job.id + '-', dir=db.experiment_dir))
    staged, total = [], 0
    try:
        for item in output.artifacts:
            path = Path(item.path)
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(work_dir.resolve()):
                raise ValueError('Experiment artifacts must be native files inside the owned execution directory')
            if not item.name or len(item.name) > 180 or len(item.media_type) > 180:
                raise ValueError('Invalid experiment artifact name or media type')
            size = path.stat().st_size
            total += size
            if size > ARTIFACT_LIMIT or total > TOTAL_ARTIFACT_LIMIT:
                raise ValueError('Experiment artifact exceeds supported file/bundle capacity')
            if shutil.disk_usage(stage).free < size + 64 * 1024 ** 2:
                raise ValueError('Insufficient storage for durable experiment output')
            identity, hash_value, written = uid('artifact'), hashlib.sha256(), 0
            destination = stage / identity
            with path.open('rb') as source, destination.open('xb') as target:
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    if should_cancel() and claim.job.cancellation_supported:
                        # A complete result is retained if the handler finished
                        # without acknowledging cancellation.
                        pass
                    written += len(chunk)
                    if written > size:
                        raise ValueError('Native artifact changed during publication')
                    hash_value.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            if written != size:
                raise ValueError('Native artifact changed during publication')
            staged.append((identity, item.name, item.media_type, destination, written, hash_value.hexdigest()))
        with jobs._write(db) as session:
            row = jobs._live(session, claim)
            subject, _ = _subject(session, row)
            artifact_dir = db.experiment_dir / 'artifacts'
            artifact_dir.mkdir(exist_ok=True)
            for identity, name, media_type, path, size, checksum in staged:
                target = artifact_dir / identity
                os.replace(path, target)
                session.add(ExperimentArtifactRow(id=identity, job_id=row.id, name=name, media_type=media_type,
                                                storage_key=identity, size_bytes=size, sha256=checksum))
            subject.summary, subject.fidelity = output.summary, output.fidelity
            if row.cancel_requested_at:
                jobs._event(session, row, 'cancel_not_honoured', 'Complete native result arrived without cancellation acknowledgement; evidence retained.', utcnow())
            jobs._live(session, claim)
            jobs._terminal(session, row, 'completed', utcnow(), 'Native outputs and immutable execution receipt published atomically.')
            return detail(session, row)
    finally:
        shutil.rmtree(stage, ignore_errors=True)

def artifact_path(db, artifact):
    path = db.experiment_dir / 'artifacts' / artifact.storage_key
    checksum, size = hashlib.sha256(), 0
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError('Native output is missing')
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                checksum.update(chunk)
                size += len(chunk)
        if size != artifact.size_bytes or checksum.hexdigest() != artifact.sha256:
            raise ValueError('Native output differs from stored content hash')
        return path
    except (OSError, ValueError) as exc:
        raise HTTPException(409, 'Experiment artifact failed its integrity check') from exc

def record_failure_diagnostics(db, session, row):
    """Retain bounded external diagnostics without treating them as evidence of success.

    Failed/lost executions keep their full private work directory. A bounded
    dispatch receipt and explicitly named native logs are copied for inspection when this worker
    still owns terminal publication. It does not acknowledge remote cleanup.
    """
    directory = db.experiment_dir / 'attempts' / row.id
    sources = {directory / 'external-dispatch.json'}
    for pattern in ('*-external-dispatch.json', '*-native-stdout.log', '*-native-stderr.log', '*-native-diagnostics.json'):
        sources.update(directory.glob(pattern))
    target_dir = db.experiment_dir / 'artifacts'
    retained = 0
    for source in sorted(sources)[:16]:
        if (not source.is_file() or source.is_symlink() or len(source.name) > 180
                or source.stat().st_size > 1024 * 1024):
            continue
        content = source.read_bytes()
        if len(content) > 1024 * 1024:
            continue
        media_type = 'text/plain'
        if source.suffix == '.json':
            try:
                json.loads(content)
            except (ValueError, UnicodeDecodeError):
                continue
            media_type = 'application/json'
        identity = uid('artifact')
        target_dir.mkdir(exist_ok=True)
        with (target_dir / identity).open('xb') as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        session.add(ExperimentArtifactRow(id=identity, job_id=row.id, name=source.name,
            media_type=media_type, storage_key=identity, size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest()))
        retained += 1
    if retained:
        jobs._event(session, row, 'external_dispatch_diagnostic',
            'Bounded external diagnostics retained; they do not confirm execution completion or remote cleanup.', utcnow())
