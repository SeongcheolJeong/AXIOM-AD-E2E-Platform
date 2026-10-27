"""Immutable ECU profiles and input tests, backed by the shared Experiment runtime."""
import json
from fastapi import Header, HTTPException, Query, Request
from sqlalchemy import func, select
from . import experiment_jobs, jobs
from .experiment_models import ExperimentRow, ExperimentExecutionRow
from .experiment_schemas import ExperimentCreate
from .job_models import JobRow
from .ecu_lab_schemas import (ADAPTER, FIDELITY, EcuProfileConfig, EcuProfileSave,
                              EcuTestSave, RevisionRef, DEFAULT_COMMANDS, validate_runtime)
from .fpga_lab import resolve_design
from .fpga_lab_schemas import DesignAction, TimingConfig
from .service import canonical_bytes, digest, uid, utcnow
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .team_access import project_authorize

SOURCES = [
    dict(id='article-board', status='article_observed', title='SA8650 domain-controller teardown',
         url='https://en.eeworld.com.cn/news/qcdz/eic706483.html',
         claim='Reference diagram: 11 cameras, three DES groups, SA8650, S32K342, Ethernet, storage and display outputs; exact board wiring unverified.'),
    dict(id='adi-des', status='manufacturer_verified', title='ADI MAX96717R data sheet',
         url='https://www.analog.com/media/en/technical-documentation/data-sheets/max96717r.pdf',
         claim='MAX96712/MAX96722 are quad deserializers; the article serializer designation is not used.'),
    dict(id='adi-display', status='manufacturer_verified', title='ADI GMSL serializer guidance',
         url='https://ez.analog.com/interface-isolation/gigabit-multimedia-serial-link-gmsl/a/faqs/FA285/which-gmsl-serializers-can-support-concurrent-gmsl1-and-gmsl2-operation-when-two-links-are-active',
         claim='MAX96755 is a DSI serializer family; no camera receiver model is inferred from that label.'),
    dict(id='nxp-monitor', status='manufacturer_verified', title='NXP S32K3xx data sheet',
         url='https://www.nxp.com/docs/en/data-sheet/S32K3xx.pdf',
         claim='S32K342 is an MCU; the watchdog here is a logical behavioral monitor, not its firmware.'),
    dict(id='board-unknowns', status='unknown', title='Unpublished target-specific interfaces', url=None,
         claim='Register addresses, CAN IDs, firmware, ISP behavior, exact CSI routing and measured performance are unknown.'),
]
ASSUMPTIONS = [
    'All editable timing, bandwidth, queue, startup and watchdog values are model assumptions, not SA8650 measurements.',
    'One to four active synthetic RAW channels share one logical CSI port and one serial processing resource.',
    'Remaining camera groups, Ethernet, GNSS/IMU, display and storage are schematic context only.',
    'Synthetic line lengths and frame IDs model payload timing; no RAW byte stream, electrical PHY, RTL or chip firmware executes.',
    'Health and completion outputs are logical records, not physical CAN or a functional-safety assessment.',
]


def read_revision(row, kind):
    if row is None:
        raise HTTPException(404, 'ECU revision not found in project')
    value = json.loads(row.manifest)
    schema = 'ecu-profile/1' if kind == 'ecu-profile' else 'ecu-input-test/1'
    if (digest(value) != row.manifest_hash or value['id'] != row.id or value['project_id'] != row.project_id
            or value['revision'] != row.revision or value['schema_version'] != schema):
        raise HTTPException(409, 'ECU revision integrity check failed')
    if kind == 'ecu-profile':
        EcuProfileConfig.model_validate(value['config'])
    else:
        EcuTestSave.model_validate({k: value[k] for k in ('project_id', 'name', 'profile_ref', 'channels', 'commands', 'faults', 'seed')})
    return value | {'sha256': row.manifest_hash}


def resolve_revision(session, project, ref, kind):
    ref = RevisionRef.model_validate(ref).model_dump()
    value = read_revision(session.get(Row, (project, kind, ref['id'], ref['revision'])), kind)
    if value['sha256'] != ref['sha256']:
        raise HTTPException(409, 'ECU revision hash differs')
    return value


def resolve_test(session, project, ref):
    return resolve_revision(session, project, ref, 'ecu-test')


def hydrate_test(session, test):
    profile = resolve_revision(session, test['project_id'], test['profile_ref'], 'ecu-profile')
    channels = [c | {'design': resolve_design(session, test['project_id'], c['design_ref'])} for c in test['channels']]
    validate_runtime(profile['config'], channels, test['commands'], test['faults'])
    return dict(schema_version='ecu-lab-input/1', test=test, profile=profile,
                channels=channels, execution_fidelity=FIDELITY)


def templates_payload():
    def preset(identity, name, count=1, profile=None, source=None, offset=120000, faults=None, invalid=False):
        return dict(id=identity, name=name, seed=42,
            profile=dict(config=EcuProfileConfig(**(profile or {})).model_dump()),
            channels=[dict(channel=i, vc=0 if invalid else i, source_offset_us=offset, duplicate_every=0,
                           config=TimingConfig(frames=30 if count > 1 else 60, **(source or {})).model_dump()) for i in range(count)],
            commands=DEFAULT_COMMANDS, faults=faults or [],
            expected_outcome='preflight_rejected' if invalid else 'completed_reference_simulation')
    return dict(schema_version='ecu-lab-templates/1', fidelity=FIDELITY,
        limits=dict(channels=4, total_frames=120, events=1000000), profile_sources=SOURCES, assumptions=ASSUMPTIONS,
        profile_schema=EcuProfileConfig.model_json_schema(), test_schema=EcuTestSave.model_json_schema(),
        items=[preset('normal', '정상 수신 · 1채널'),
               preset('before-ready', 'ECU 준비 전 송신', offset=0),
               preset('invalid-mapping', 'VC 중복 · 사전 검사 거부', count=2, invalid=True),
               preset('link-recovery', '링크 단절과 회복 · 2채널', count=2,
                      faults=[dict(kind='link_down', channel=0, at_us=350000, duration_us=70000)]),
               preset('csi-congestion', '공유 CSI 병목 · 4채널', count=4, profile={'csi_lane_Mbps':200}),
               preset('processor-backlog', '처리 큐 적체 · 4채널', count=4, profile={'processing_us':20000})])


def register_ecu_lab_routes(api, db, registry):
    prefix = '/hil-sim/ecu-lab'

    @api.get(prefix + '/templates', tags=['ecu-lab'])
    def templates(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        return templates_payload() | dict(adapter_installed=registry.get(ADAPTER) is not None, worker_readiness='not_verified')

    def save_revision(body, request, key, kind):
        project_authorize(request, db, body.project_id, 'Engineer')
        experiment_jobs._key(body.project_id, key, kind+'-save')
        receipt_kind = kind+'-write'
        key_id, body_hash = digest(key), digest(body.model_dump(mode='json'))
        with jobs._write(db) as session:
            existing = session.get(Row, (body.project_id, receipt_kind, key_id, 1))
            if existing:
                receipt = json.loads(existing.manifest)
                if digest(receipt) != existing.manifest_hash or receipt['request_hash'] != body_hash:
                    raise HTTPException(409, 'Idempotency key identifies another ECU write')
                return resolve_revision(session, body.project_id, receipt['ref'], kind)
            identity = body.id or uid('ecup' if kind == 'ecu-profile' else 'ecut')
            latest = session.scalar(select(func.max(Row.revision)).where(
                Row.project_id == body.project_id, Row.kind == kind, Row.id == identity)) or 0
            if body.expected_revision != latest or (body.id is None and body.expected_revision):
                raise HTTPException(409, 'ECU document changed; reopen its latest revision')
            content = body.model_dump(exclude={'id', 'expected_revision'})
            value = content | dict(schema_version='ecu-profile/1' if kind == 'ecu-profile' else 'ecu-input-test/1',
                id=identity, revision=latest+1, created_at=utcnow(), fidelity=FIDELITY)
            if kind == 'ecu-profile':
                value.update(sources=SOURCES, assumptions=ASSUMPTIONS, target='SA8650 기반 ECU 참조 동작 모델')
            else:
                try:
                    hydrate_test(session, value)
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
            sha = digest(value)
            session.add(Row(project_id=body.project_id, kind=kind, id=identity, revision=latest+1,
                            manifest=canonical_bytes(value), manifest_hash=sha))
            receipt = dict(request_hash=body_hash, ref=dict(id=identity, revision=latest+1, sha256=sha))
            session.add(Row(project_id=body.project_id, kind=receipt_kind, id=key_id, revision=1,
                            manifest=canonical_bytes(receipt), manifest_hash=digest(receipt)))
            return value | {'sha256': sha}

    @api.post(prefix + '/profiles', status_code=201, tags=['ecu-lab'])
    def save_profile(body: EcuProfileSave, request: Request, idempotency_key: str = Header(alias='Idempotency-Key')):
        return save_revision(body, request, idempotency_key, 'ecu-profile')

    @api.post(prefix + '/tests', status_code=201, tags=['ecu-lab'])
    def save_test(body: EcuTestSave, request: Request, idempotency_key: str = Header(alias='Idempotency-Key')):
        return save_revision(body, request, idempotency_key, 'ecu-test')

    def list_revisions(request, project_id, kind):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            rows = session.scalars(select(Row).where(Row.project_id == project_id, Row.kind == kind).order_by(Row.id, Row.revision.desc()))
            return {'items': [read_revision(row, kind) for row in rows]}

    @api.get(prefix + '/profiles', tags=['ecu-lab'])
    def profiles(request: Request, project_id: str = 'default'):
        return list_revisions(request, project_id, 'ecu-profile')

    @api.get(prefix + '/tests', tags=['ecu-lab'])
    def tests(request: Request, project_id: str = 'default'):
        return list_revisions(request, project_id, 'ecu-test')

    def get_revision(identity, revision, project_id, request, kind):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return read_revision(session.get(Row, (project_id, kind, identity, revision)), kind)

    @api.get(prefix + '/profiles/{identity}', tags=['ecu-lab'])
    def profile(identity: str, request: Request, revision: int = Query(ge=1), project_id: str = 'default'):
        return get_revision(identity, revision, project_id, request, 'ecu-profile')

    @api.get(prefix + '/tests/{identity}', tags=['ecu-lab'])
    def test(identity: str, request: Request, revision: int = Query(ge=1), project_id: str = 'default'):
        return get_revision(identity, revision, project_id, request, 'ecu-test')

    @api.post(prefix + '/tests/{identity}/preview', tags=['ecu-lab'])
    def preview(identity: str, body: DesignAction, request: Request):
        project_authorize(request, db, body.project_id, 'Engineer')
        reference = dict(id=identity, revision=body.revision, sha256=body.sha256)
        with db.read_session() as session:
            saved = resolve_test(session, body.project_id, reference)
        spec = ExperimentCreate(project_id=body.project_id, name=saved['name'], kind='ecu_timing',
            adapter_id=ADAPTER, input={'test_ref': reference}, seed=saved['seed'])
        prepared = experiment_jobs.prepare(registry, spec)
        return dict(experiment=prepared.to_payload()['spec'], preview_hash=prepared.input_hash,
                    fidelity=FIDELITY, worker_readiness='not_verified')

    @api.get(prefix + '/executions', tags=['ecu-lab'])
    def executions(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            rows = session.execute(select(ExperimentRow, ExperimentExecutionRow, JobRow).join(
                ExperimentExecutionRow, ExperimentExecutionRow.experiment_id == ExperimentRow.id).join(JobRow, JobRow.id == ExperimentExecutionRow.job_id).where(
                    ExperimentRow.project_id == project_id, ExperimentRow.kind == 'ecu_timing')
                    .order_by(JobRow.created_at.desc()).limit(100)).all()
            return {'items': [dict(id=execution.job_id, status=job.status, name=experiment.name,
                test_ref=experiment_jobs.experiment_get(session, experiment.id)[1]['prepared']['spec']['input']['test_ref'])
                for experiment, execution, job in rows]}
