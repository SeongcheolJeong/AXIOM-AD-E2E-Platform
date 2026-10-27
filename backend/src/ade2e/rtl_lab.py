"""Immutable FIFO RTL tests backed by the shared Experiment authoring and queue API."""
import json
import random
from fastapi import Header, HTTPException, Query, Request
from sqlalchemy import func, select
from . import experiment_jobs, jobs
from .experiment_models import ExperimentRow, ExperimentExecutionRow
from .experiment_schemas import ExperimentCreate
from .fpga_lab_schemas import DesignAction
from .job_models import JobRow
from .rtl_lab_schemas import ADAPTER, FIDELITY, CLOCK_HZ, DATA_WIDTH, RtlConfig, RtlTestSave, RevisionRef
from .service import canonical_bytes, digest, uid, utcnow
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .team_access import project_authorize

ASSUMPTIONS = [
    'A provided single-clock, 32-bit reference FIFO RTL; not Qualcomm RTL, CSI PHY or measured board timing.',
    '100 MHz virtual clock; rising-edge valid/ready transfers, synchronous active-high reset has priority.',
    'No empty fall-through; full FIFO permits an input when an output is consumed on the same edge.',
    'Independent SimPy and Verilator executions use identical frozen input words and receiver schedules.',
    'Source valid/data and sink output data remain stable during backpressure; reset losses are counted separately.',
]


def read_test(row):
    if row is None:
        raise HTTPException(404, 'RTL test revision not found in project')
    try:
        value = json.loads(row.manifest)
        if (digest(value) != row.manifest_hash or value['id'] != row.id
                or value['project_id'] != row.project_id or value['revision'] != row.revision
                or value['schema_version'] != 'rtl-test/1' or value['fidelity'] != FIDELITY):
            raise ValueError('Revision identity differs')
        RtlTestSave.model_validate({k: value[k] for k in ('project_id', 'name', 'config', 'seed')})
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(409, 'RTL test revision integrity check failed') from exc
    return value | {'sha256': row.manifest_hash}


def resolve_test(session, project_id, ref):
    reference = RevisionRef.model_validate(ref).model_dump()
    value = read_test(session.get(Row, (project_id, 'rtl-test', reference['id'], reference['revision'])))
    if value['sha256'] != reference['sha256']:
        raise HTTPException(409, 'RTL test revision hash differs')
    return value


def hydrate_test(test):
    RtlConfig.model_validate(test['config'])
    return dict(schema_version='rtl-input/1', test=test, execution_fidelity=FIDELITY)


def templates_payload():
    rng = random.Random(42)
    repeated = []
    cycle = 96
    for _ in range(12):
        cycle += rng.randrange(48, 112)
        duration = rng.randrange(32, 128)
        repeated.append(dict(start_cycle=cycle, end_cycle=cycle + duration))
        cycle += duration
    def preset(identity, name, **changes):
        return dict(id=identity, name=name, config=RtlConfig(**changes).model_dump(), seed=42)
    return dict(schema_version='rtl-templates/1', fidelity=FIDELITY,
                constants=dict(clock_hz=CLOCK_HZ, data_width=DATA_WIDTH),
                config_schema=RtlConfig.model_json_schema(), assumptions=ASSUMPTIONS,
                items=[preset('normal', 'FIFO · 정상 전송'),
                       preset('stall', 'FIFO · 수신 정체·회복', stalls=[dict(start_cycle=128, end_cycle=384)]),
                       preset('repeated', 'FIFO · 반복 정체', stalls=repeated),
                       preset('reset', 'FIFO · 실행 중 리셋', stalls=[dict(start_cycle=128, end_cycle=384)], resets=[0, 1, 160])])


def register_rtl_lab_routes(api, db, registry):
    prefix = '/hil-sim/rtl-lab'

    def readiness_payload():
        handler = registry.get(ADAPTER)
        value = dict(adapter_installed=handler is not None, worker_readiness='not_verified')
        if handler is None:
            return value | dict(status='not_configured', available=False, reason='Verilator FIFO adapter is not installed')
        metadata = handler.metadata()
        value.update(status=metadata.status, adapter_version=metadata.version)
        probe = getattr(handler, 'readiness', None)
        if callable(probe):
            try:
                value.update(probe())
            except (ValueError, OSError, RuntimeError) as exc:
                value.update(status='not_configured', available=False, reason=str(exc))
        # A local tool probe never establishes a live worker's readiness.
        value['worker_readiness'] = 'not_verified'
        return value

    @api.get(prefix + '/templates', tags=['rtl-lab'])
    def templates(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        return templates_payload() | readiness_payload()

    @api.get(prefix + '/readiness', tags=['rtl-lab'])
    def readiness(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        return readiness_payload()

    @api.post(prefix + '/tests', status_code=201, tags=['rtl-lab'])
    def save_test(body: RtlTestSave, request: Request, idempotency_key: str = Header(alias='Idempotency-Key')):
        project_authorize(request, db, body.project_id, 'Engineer')
        experiment_jobs._key(body.project_id, idempotency_key, 'rtl-test-save')
        key_id, body_hash = digest(idempotency_key), digest(body.model_dump(mode='json'))
        with jobs._write(db) as session:
            existing = session.get(Row, (body.project_id, 'rtl-test-write', key_id, 1))
            if existing:
                receipt = json.loads(existing.manifest)
                if digest(receipt) != existing.manifest_hash or receipt['request_hash'] != body_hash:
                    raise HTTPException(409, 'Idempotency key identifies another RTL test write')
                return resolve_test(session, body.project_id, receipt['ref'])
            identity = body.id or uid('rtlt')
            latest = session.scalar(select(func.max(Row.revision)).where(
                Row.project_id == body.project_id, Row.kind == 'rtl-test', Row.id == identity)) or 0
            if body.expected_revision != latest or (body.id is None and body.expected_revision):
                raise HTTPException(409, 'RTL test changed; reopen its latest revision')
            content = body.model_dump(exclude={'id', 'expected_revision'})
            value = content | dict(schema_version='rtl-test/1', id=identity, revision=latest + 1,
                                   created_at=utcnow(), fidelity=FIDELITY)
            sha = digest(value)
            session.add(Row(project_id=body.project_id, kind='rtl-test', id=identity, revision=latest + 1,
                            manifest=canonical_bytes(value), manifest_hash=sha))
            receipt = dict(request_hash=body_hash, ref=dict(id=identity, revision=latest + 1, sha256=sha))
            session.add(Row(project_id=body.project_id, kind='rtl-test-write', id=key_id, revision=1,
                            manifest=canonical_bytes(receipt), manifest_hash=digest(receipt)))
            return value | {'sha256': sha}

    @api.get(prefix + '/tests', tags=['rtl-lab'])
    def tests(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            rows = session.scalars(select(Row).where(Row.project_id == project_id, Row.kind == 'rtl-test')
                                   .order_by(Row.id, Row.revision.desc()))
            return {'items': [read_test(row) for row in rows]}

    @api.get(prefix + '/tests/{identity}', tags=['rtl-lab'])
    def test(identity: str, request: Request, revision: int = Query(ge=1), project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return read_test(session.get(Row, (project_id, 'rtl-test', identity, revision)))

    @api.post(prefix + '/tests/{identity}/preview', tags=['rtl-lab'])
    @api.post(prefix + '/tests/{identity}/preflight', tags=['rtl-lab'])
    def preview(identity: str, body: DesignAction, request: Request):
        project_authorize(request, db, body.project_id, 'Engineer')
        reference = dict(id=identity, revision=body.revision, sha256=body.sha256)
        with db.read_session() as session:
            saved = resolve_test(session, body.project_id, reference)
        spec = ExperimentCreate(project_id=body.project_id, name=saved['name'], kind='fpga_rtl',
                                adapter_id=ADAPTER, input={'test_ref': reference}, seed=saved['seed'])
        prepared = experiment_jobs.prepare(registry, spec)
        return dict(experiment=prepared.to_payload()['spec'], preview_hash=prepared.input_hash,
                    fidelity=FIDELITY, worker_readiness='not_verified')

    @api.get(prefix + '/executions', tags=['rtl-lab'])
    def executions(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            rows = session.execute(select(ExperimentRow, ExperimentExecutionRow, JobRow).join(
                ExperimentExecutionRow, ExperimentExecutionRow.experiment_id == ExperimentRow.id).join(
                JobRow, JobRow.id == ExperimentExecutionRow.job_id).where(
                ExperimentRow.project_id == project_id, ExperimentRow.kind == 'fpga_rtl')
                .order_by(JobRow.created_at.desc()).limit(100)).all()
            return {'items': [dict(id=execution.job_id, status=job.status, name=experiment.name,
                                  test_ref=experiment_jobs.experiment_get(session, experiment.id)[1]
                                  ['prepared']['spec']['input']['test_ref'])
                              for experiment, execution, job in rows]}
