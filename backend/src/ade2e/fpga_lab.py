"""Offline design revisions and shared Experiment API bindings for FPGA Lab."""
import json
from fastapi import Header, HTTPException, Query, Request
from sqlalchemy import func, select
from . import experiment_jobs, jobs
from .experiment_models import ExperimentRow, ExperimentExecutionRow
from .experiment_schemas import ExperimentCreate
from .job_models import JobRow
from .fpga_lab_schemas import ADAPTER, FIDELITY, DesignSave, DesignAction, TimingConfig, PRESETS
from .service import canonical_bytes, digest, uid, utcnow
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .team_access import project_authorize

KIND = 'fpga-design'

def read_design(row):
    if row is None:
        raise HTTPException(404, 'FPGA design revision not found in project')
    value = json.loads(row.manifest)
    if (digest(value) != row.manifest_hash or value['id'] != row.id or value['project_id'] != row.project_id
            or value['revision'] != row.revision or value['schema_version'] != 'fpga-lab-design/1'):
        raise HTTPException(409, 'FPGA design integrity check failed')
    TimingConfig.model_validate(value['config'])
    return value | {'sha256': row.manifest_hash}

def resolve_design(session, project, ref):
    if set(ref) != {'id', 'revision', 'sha256'}:
        raise ValueError('design_ref requires id, revision and sha256')
    value = read_design(session.get(Row, (project, KIND, ref['id'], ref['revision'])))
    if value['sha256'] != ref['sha256']:
        raise HTTPException(409, 'FPGA design hash differs')
    return value

def register_fpga_lab_routes(api, db, registry):
    prefix = '/hil-sim/fpga-lab'

    @api.get(prefix + '/templates', tags=['fpga-lab'])
    def templates(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        return dict(schema_version='fpga-lab-templates/1', fidelity=FIDELITY,
            adapter_installed=registry.get(ADAPTER) is not None, worker_readiness='not_verified',
            limits=dict(frames=120, source_seconds=10, events=1000000),
            config_schema=TimingConfig.model_json_schema(),
            items=[dict(id=key, name=name, config=TimingConfig(**changes).model_dump()) for key, name, changes in PRESETS])

    @api.post(prefix + '/designs', status_code=201, tags=['fpga-lab'])
    def save(body: DesignSave, request: Request, idempotency_key: str = Header(alias='Idempotency-Key')):
        project_authorize(request, db, body.project_id, 'Engineer')
        experiment_jobs._key(body.project_id, idempotency_key, 'fpga-save')
        key_id = digest(idempotency_key)
        body_hash = digest(body.model_dump(mode='json'))
        with jobs._write(db) as session:
            existing = session.get(Row, (body.project_id, 'fpga-write', key_id, 1))
            if existing:
                receipt = json.loads(existing.manifest)
                if digest(receipt) != existing.manifest_hash or receipt['request_hash'] != body_hash:
                    raise HTTPException(409, 'Idempotency key identifies another design write')
                return resolve_design(session, body.project_id, receipt['ref'])
            identity = body.id or uid('fpga')
            latest = session.scalar(select(func.max(Row.revision)).where(
                Row.project_id == body.project_id, Row.kind == KIND, Row.id == identity)) or 0
            if body.expected_revision != latest or (body.id is None and body.expected_revision):
                raise HTTPException(409, 'Design changed; reopen its latest revision')
            source = dict(generator='ramp/1', pattern='pixel=(x+y+frame+seed) mod 2**bits',
                          width=body.config.width, height=body.config.height, bits=body.config.bits,
                          seed=body.seed, frames=body.config.frames, fps=body.config.fps,
                          payload_mode='line_sizes_and_ids', preview='8-bit illustrative ramp thumbnail')
            source['sha256'] = digest(source)
            value = dict(schema_version='fpga-lab-design/1', id=identity, project_id=body.project_id,
                revision=latest + 1, name=body.name, config=body.config.model_dump(), seed=body.seed,
                source=source, fidelity=FIDELITY, created_at=utcnow())
            sha = digest(value)
            session.add(Row(project_id=body.project_id, kind=KIND, id=identity, revision=latest + 1,
                            manifest=canonical_bytes(value), manifest_hash=sha))
            receipt = dict(request_hash=body_hash, ref=dict(id=identity, revision=latest + 1, sha256=sha))
            session.add(Row(project_id=body.project_id, kind='fpga-write', id=key_id, revision=1,
                            manifest=canonical_bytes(receipt), manifest_hash=digest(receipt)))
            return value | {'sha256': sha}

    @api.get(prefix + '/designs', tags=['fpga-lab'])
    def designs(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            rows = session.scalars(select(Row).where(Row.project_id == project_id, Row.kind == KIND)
                                   .order_by(Row.id, Row.revision.desc()))
            return {'items': [read_design(row) for row in rows]}

    @api.get(prefix + '/designs/{identity}', tags=['fpga-lab'])
    def design(identity: str, request: Request, revision: int = Query(ge=1), project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return read_design(session.get(Row, (project_id, KIND, identity, revision)))

    @api.post(prefix + '/designs/{identity}/preview', tags=['fpga-lab'])
    def preview(identity: str, body: DesignAction, request: Request):
        project_authorize(request, db, body.project_id, 'Engineer')
        reference = dict(id=identity, revision=body.revision, sha256=body.sha256)
        with db.read_session() as session:
            saved = resolve_design(session, body.project_id, reference)
        spec = ExperimentCreate(project_id=body.project_id, name=saved['name'], kind='fpga_timing',
            adapter_id=ADAPTER, input={'design_ref': reference}, seed=saved['seed'])
        prepared = experiment_jobs.prepare(registry, spec)
        return dict(experiment=prepared.to_payload()['spec'], preview_hash=prepared.input_hash,
                    fidelity=FIDELITY, worker_readiness='not_verified')

    @api.get(prefix + '/executions', tags=['fpga-lab'])
    def executions(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            rows = session.execute(select(ExperimentRow, ExperimentExecutionRow, JobRow).join(
                ExperimentExecutionRow, ExperimentExecutionRow.experiment_id == ExperimentRow.id).join(JobRow, JobRow.id == ExperimentExecutionRow.job_id).where(
                    ExperimentRow.project_id == project_id, ExperimentRow.kind == 'fpga_timing')
                    .order_by(JobRow.created_at.desc()).limit(100)).all()
            return {'items': [dict(id=execution.job_id, status=job.status, name=experiment.name,
                design_ref=experiment_jobs.experiment_get(session, experiment.id)[1]['prepared']['spec']['input']['design_ref'])
                for experiment, execution, job in rows]}
