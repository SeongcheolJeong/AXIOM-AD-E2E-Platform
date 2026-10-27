"""Project-scoped stunt designs; native execution uses the shared durable queue."""
import json

from fastapi import Header, HTTPException, Query, Request
from sqlalchemy import func, select

from . import experiment_jobs, jobs
from .experiment_models import ExperimentExecutionRow, ExperimentRow
from .experiment_runtime import ExperimentHandlerMetadata
from .experiment_schemas import ExperimentCreate
from .job_models import JobRow
from .service import canonical_bytes, digest, uid, utcnow
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .stunt_lab_schemas import (ADAPTER, FIDELITY, PHYSICAL_VALIDATION, PRESETS, RAMP_BLOCKED_REASON,
                                DesignAction, DesignRef, DesignSave, StuntConfig, model_definition)
from .team_access import project_authorize

KIND = 'stunt-design'


def read_design(row):
    if row is None:
        raise HTTPException(404, 'Stunt design revision not found in project')
    try:
        value = json.loads(row.manifest)
        if (digest(value) != row.manifest_hash or value['id'] != row.id or value['project_id'] != row.project_id
                or value['revision'] != row.revision or value['schema_version'] != 'stunt-lab-design/1'
                or value['fidelity'] != FIDELITY or value['physical_validation'] != PHYSICAL_VALIDATION
                or value['model_definition'] != model_definition()):
            raise ValueError('Revision identity or model definition differs')
        DesignSave.model_validate({key: value[key] for key in ('project_id', 'name', 'config', 'seed')})
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(409, 'Stunt design integrity check failed') from exc
    return value | {'sha256': row.manifest_hash}


def resolve_design(session, project_id, ref):
    reference = DesignRef.model_validate(ref).model_dump()
    value = read_design(session.get(Row, (project_id, KIND, reference['id'], reference['revision'])))
    if value['sha256'] != reference['sha256']:
        raise HTTPException(409, 'Stunt design hash differs')
    return value


def readiness_payload(registry):
    handler = registry.get(ADAPTER)
    details = {}
    configured = False
    version = None
    reason = 'Native CARLA stunt adapter is not installed'
    if handler is not None:
        metadata = ExperimentHandlerMetadata.model_validate(handler.metadata())
        configured, version = metadata.status == 'available', metadata.version
        reason = 'Native CARLA stunt adapter is not configured'
        if configured:
            reason = 'Native force, torque and contact response have not been verified'
        if callable(getattr(handler, 'readiness', None)):
            try:
                details = handler.readiness()
                if not isinstance(details, dict):
                    raise ValueError('Readiness must return an object')
                reason = str(details.get('reason') or reason)
            except Exception:
                details = {'available': False, 'physics_verified': False, 'status': 'probe_failed'}
                reason = 'Native readiness probe failed; execution readiness is not established'
    physics_verified = details.get('physics_verified') is True
    available = configured and details.get('available') is True and physics_verified
    return dict(schema_version='stunt-lab-readiness/1', adapter_installed=handler is not None,
                adapter_version=version, configured=configured, available=available,
                physics_verified=physics_verified, worker_readiness='not_verified',
                fidelity=FIDELITY, physical_validation=PHYSICAL_VALIDATION,
                reason=None if available else reason, details=details,
                presets=dict(kickflip=dict(available=available, reason=None if available else reason),
                             ramp=dict(available=False, reason=RAMP_BLOCKED_REASON)))


def register_stunt_lab_routes(api, db, registry):
    prefix = '/object-sim/stunt-lab'

    @api.get(prefix + '/templates', tags=['stunt-lab'])
    def templates(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        return dict(schema_version='stunt-lab-templates/1', fidelity=FIDELITY,
            physical_validation=PHYSICAL_VALIDATION, adapter_installed=registry.get(ADAPTER) is not None,
            worker_readiness='not_verified', model_definition=model_definition(),
            config_schema=StuntConfig.model_json_schema(),
            items=[dict(id=key, name=name, seed=42, config=StuntConfig(**changes).model_dump(),
                        available=key != 'ramp', blocked_reason=RAMP_BLOCKED_REASON if key == 'ramp' else None)
                   for key, name, changes in PRESETS])

    @api.get(prefix + '/readiness', tags=['stunt-lab'])
    def readiness(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        return readiness_payload(registry)

    @api.post(prefix + '/designs', status_code=201, tags=['stunt-lab'])
    def save(body: DesignSave, request: Request, idempotency_key: str = Header(alias='Idempotency-Key')):
        project_authorize(request, db, body.project_id, 'Engineer')
        experiment_jobs._key(body.project_id, idempotency_key, 'stunt-save')
        key_id, body_hash = digest(idempotency_key), digest(body.model_dump(mode='json'))
        with jobs._write(db) as session:
            existing = session.get(Row, (body.project_id, 'stunt-write', key_id, 1))
            if existing:
                try:
                    receipt = json.loads(existing.manifest)
                    if digest(receipt) != existing.manifest_hash or receipt['request_hash'] != body_hash:
                        raise ValueError('Request hash differs')
                    reference = DesignRef.model_validate(receipt['ref']).model_dump()
                except (ValueError, TypeError, KeyError) as exc:
                    raise HTTPException(409, 'Idempotency key identifies another or invalid design write') from exc
                return resolve_design(session, body.project_id, reference)
            identity = body.id or uid('stunt')
            latest = session.scalar(select(func.max(Row.revision)).where(
                Row.project_id == body.project_id, Row.kind == KIND, Row.id == identity)) or 0
            if body.expected_revision != latest or (body.id is None and body.expected_revision):
                raise HTTPException(409, 'Design changed; reopen its latest revision')
            value = dict(schema_version='stunt-lab-design/1', id=identity, project_id=body.project_id,
                revision=latest + 1, name=body.name, config=body.config.model_dump(), seed=body.seed,
                model_definition=model_definition(), fidelity=FIDELITY,
                physical_validation=PHYSICAL_VALIDATION, created_at=utcnow())
            sha = digest(value)
            session.add(Row(project_id=body.project_id, kind=KIND, id=identity, revision=latest + 1,
                            manifest=canonical_bytes(value), manifest_hash=sha))
            receipt = dict(request_hash=body_hash, ref=dict(id=identity, revision=latest + 1, sha256=sha))
            session.add(Row(project_id=body.project_id, kind='stunt-write', id=key_id, revision=1,
                            manifest=canonical_bytes(receipt), manifest_hash=digest(receipt)))
            return value | {'sha256': sha}

    @api.get(prefix + '/designs', tags=['stunt-lab'])
    def designs(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            rows = session.scalars(select(Row).where(Row.project_id == project_id, Row.kind == KIND)
                                   .order_by(Row.id, Row.revision.desc()))
            return {'items': [read_design(row) for row in rows]}

    @api.get(prefix + '/designs/{identity}', tags=['stunt-lab'])
    def design(identity: str, request: Request, revision: int = Query(ge=1), project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return read_design(session.get(Row, (project_id, KIND, identity, revision)))

    @api.post(prefix + '/designs/{identity}/preview', tags=['stunt-lab'])
    def preview(identity: str, body: DesignAction, request: Request):
        project_authorize(request, db, body.project_id, 'Engineer')
        reference = dict(id=identity, revision=body.revision, sha256=body.sha256)
        with db.read_session() as session:
            saved = resolve_design(session, body.project_id, reference)
        if saved['config']['preset'] == 'ramp':
            raise HTTPException(422, RAMP_BLOCKED_REASON)
        spec = ExperimentCreate(project_id=body.project_id, name=saved['name'], kind='object_sim',
            adapter_id=ADAPTER, input={'design_ref': reference}, seed=saved['seed'])
        prepared = experiment_jobs.prepare(registry, spec)
        return dict(experiment=prepared.to_payload()['spec'], preview_hash=prepared.input_hash,
                    fidelity=FIDELITY, physical_validation=PHYSICAL_VALIDATION, worker_readiness='not_verified')

    @api.get(prefix + '/executions', tags=['stunt-lab'])
    def executions(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            rows = session.execute(select(ExperimentRow, ExperimentExecutionRow, JobRow).join(
                ExperimentExecutionRow, ExperimentExecutionRow.experiment_id == ExperimentRow.id).join(
                JobRow, JobRow.id == ExperimentExecutionRow.job_id).where(
                    ExperimentRow.project_id == project_id, ExperimentRow.kind == 'object_sim', JobRow.adapter_id == ADAPTER)
                    .order_by(JobRow.created_at.desc()).limit(100)).all()
            return {'items': [dict(id=execution.job_id, status=job.status, name=experiment.name,
                created_at=job.created_at, fidelity=execution.fidelity,
                design_ref=experiment_jobs.experiment_get(session, experiment.id)[1]['prepared']['spec']['input']['design_ref'])
                for experiment, execution, job in rows]}

    @api.get(prefix + '/comparison', tags=['stunt-lab'])
    def comparison(request: Request, left_execution_id: str, right_execution_id: str, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            values = []
            for identity in (left_execution_id, right_execution_id):
                subject = session.get(ExperimentExecutionRow, identity)
                job = session.scalar(select(JobRow).where(JobRow.id == identity))
                if subject is None or job is None or subject.project_id != project_id or job.adapter_id != ADAPTER:
                    raise HTTPException(404, 'Stunt execution not found in project')
                values.append(experiment_jobs.detail(session, job).model_dump(mode='json'))
            return dict(schema_version='stunt-lab-comparison/1', left=values[0], right=values[1],
                        fidelity=FIDELITY, physical_validation=PHYSICAL_VALIDATION,
                        comparison_scope='independent_saved_runs', evaluation_status='not_evaluated')
