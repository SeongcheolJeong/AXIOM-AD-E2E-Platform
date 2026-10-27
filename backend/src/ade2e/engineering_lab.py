"""Isolated native Engineering Lab authoring through the shared immutable queue."""
import json
import hashlib
from fastapi import Header, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy import func, select
from . import experiment_jobs, jobs
from .experiment_models import ExperimentRow, ExperimentExecutionRow
from .experiment_schemas import ExperimentCreate
from .fpga_lab_schemas import DesignAction
from .job_models import JobRow
from .engineering_lab_schemas import ADAPTER, FIDELITY, EngineeringTestSave, RevisionRef, IspRtlConfig, EngineeringBuildAction
from .adas_module_schemas import adas_templates
from .adas_validation_schemas import validation_templates, campaign_plan, AdasCampaignConfig
from .service import canonical_bytes, digest, uid, utcnow
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .team_access import project_authorize

ASSUMPTIONS = [
    'Local trusted RTL source execution with Stream32 ports; no verified OS sandbox or physical board claims.',
    'Native ngspice self-contained R/C/L/V/I/D transient analysis; units and assumptions are explicit.',
    'Qualcomm targets are connection profiles only until an actual supported runtime is connected.',
    'Bundled open ISP RTL follows a documented stage mapping; it is not Qualcomm RTL or a bit-exact Qualcomm model. FPGA builds are separate OOC checks, not pixel validation.',
    'Four-channel ADAS module RTL uses a fixed single-clock RAW12 streaming contract. Reduced dimensions are functional tests, not 4 x 1080p30 acceptance; physical target binding is unavailable.',
]


def source_identity(config):
    identity = {'config_hash': digest(config)}
    if config['engine'] == 'rtl':
        identity['source_hashes'] = {s['name']: hashlib.sha256(s['content'].encode('ascii')).hexdigest() for s in config['sources']}
    elif config['engine'] == 'spice':
        identity['netlist_sha256'] = hashlib.sha256(config['netlist'].encode('ascii')).hexdigest()
    return identity


def read_test(row):
    if row is None:
        raise HTTPException(404, 'Engineering test revision not found in project')
    try:
        value = json.loads(row.manifest)
        if (digest(value) != row.manifest_hash or value['id'] != row.id
                or value['project_id'] != row.project_id or value['revision'] != row.revision
                or value['schema_version'] != 'engineering-test/1' or value['fidelity'] != FIDELITY):
            raise ValueError('Revision identity differs')
        EngineeringTestSave.model_validate({k: value[k] for k in ('project_id', 'name', 'config', 'seed')})
        if any(value.get(k) != v for k, v in source_identity(value['config']).items()):
            raise ValueError('Source content identity differs')
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_url=False)
        detail = '; '.join(str(error['msg']) for error in errors)[:1000]
        raise HTTPException(409, 'Engineering test configuration is unsupported by this runtime: ' + detail) from exc
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(409, 'Engineering test revision integrity check failed') from exc
    return value | {'sha256': row.manifest_hash}


def resolve_test(session, project_id, ref):
    reference = RevisionRef.model_validate(ref).model_dump()
    value = read_test(session.get(Row, (project_id, 'engineering-test', reference['id'], reference['revision'])))
    if value['sha256'] != reference['sha256']:
        raise HTTPException(409, 'Engineering test revision hash differs')
    return value


def hydrate_test(test):
    EngineeringTestSave.model_validate({k: test[k] for k in ('project_id', 'name', 'config', 'seed')})
    return dict(schema_version='engineering-input/1', test=test, execution_fidelity=FIDELITY)


def resolve_isp_asset(session, test):
    """Resolve existing Apollo RAW identity without importing an imaging engine."""
    from .apollo_lab import read_asset
    config = test['config']
    if config['engine'] != 'isp_rtl' or config['source_kind'] != 'raw_asset':
        return None
    ref = config['raw_asset_ref']
    asset = read_asset(session.get(Row, (test['project_id'], 'apollo-raw', ref['id'], ref['revision'])))
    if ref['revision'] != 1 or asset['sha256'] != ref['sha256']:
        raise HTTPException(409, 'ISP RAW asset revision hash differs')
    metadata = asset['metadata']
    if (metadata['bits'] != 12 or metadata['width'] != config['width'] or metadata['height'] != config['height']
            or metadata['bayer'] != config['bayer_pattern']):
        raise HTTPException(422, 'ISP RAW requires RAW12 with matching width, height and Bayer order; no implicit resizing or crop')
    if config['raw_frame_index'] + config['frames'] > asset['frames']:
        raise HTTPException(422, 'ISP requested frame range exceeds the RAW asset; repetition is not implicit')
    return asset


def resolve_adas_assets(session, test):
    """Reuse project-scoped immutable RAW assets per channel without resizing/repetition."""
    config = test['config']
    if config['engine'] != 'adas_module_rtl':
        return {}
    result = {}
    for channel in config['channels']:
        if channel['source_kind'] != 'raw_asset':
            continue
        # Asset format checks are identical to the single-channel RAW12 contract.
        channel_test = test | {'config': dict(engine='isp_rtl', width=config['width'],
            height=config['height'], frames=1 if channel.get('raw_repeat') else config['frames'], **channel)}
        result[str(channel['channel'])] = resolve_isp_asset(session, channel_test)
    return result


def templates_payload():
    def rtl(identity, name, transform='in_data', expectation='identity', mask=0):
        code = """module user_top (
    input logic clk, input logic reset,
    input logic in_valid, output logic in_ready, input logic [31:0] in_data,
    output logic out_valid, input logic out_ready, output logic [31:0] out_data
);
assign in_ready = out_ready && !reset;
assign out_valid = in_valid && !reset;
assign out_data = TRANSFORM;
endmodule
""".replace('TRANSFORM', transform)
        return dict(id=identity, name=name, seed=42, config=dict(engine='rtl', top='user_top',
            sources=[dict(name='user_top.sv', content=code)], words=256, max_cycles=2048,
            stalls=[dict(start_cycle=32, end_cycle=64)], resets=[0, 1], expectation=expectation, xor_mask=mask))
    def spice(identity, name, circuit):
        return dict(id=identity, name=name, seed=42, config=dict(engine='spice', netlist=circuit, step_s=1e-6, stop_s=.005, probes=['v(in)', 'v(out)']))
    return dict(schema_version='engineering-templates/1', fidelity=FIDELITY,
        config_schema=EngineeringTestSave.model_json_schema(), assumptions=ASSUMPTIONS,
        items=[rtl('passthrough', 'RTL · Stream32 passthrough'),
               rtl('xor-transform', 'RTL · XOR transform', "in_data ^ 32'h00ff00ff", 'xor', 16711935),
               rtl('buggy-xor', 'RTL · Incorrect XOR / identity expectation', "in_data ^ 32'h00ff00ff"),
               spice('rc-step', 'SPICE · RC step', '* RC step reference\nVstep in 0 PULSE(0 1 0 1n 1n 10m 20m)\nR1 in out 1k\nC1 out 0 1u\n.end\n'),
               spice('rc-slow', 'SPICE · RC slower response', '* RC slower response\nVstep in 0 PULSE(0 1 0 1n 1n 10m 20m)\nR1 in out 2k\nC1 out 0 1u\n.end\n'),
               spice('rlc', 'SPICE · RLC step', '* RLC reference\nVstep in 0 PULSE(0 1 0 1n 1n 10m 20m)\nR1 in mid 10\nL1 mid out 10m\nC1 out 0 1u\n.end\n'),
               dict(id='qualcomm-sa8650', name='Qualcomm · SA8650 connection profile', seed=42, config=dict(engine='qualcomm', target='SA8650')),
               dict(id='open-isp', name='Open ISP · RAW12 BLC / WB / MHC', seed=42, config=IspRtlConfig(engine='isp_rtl').model_dump()),
               dict(id='open-isp-wb', name='Open ISP · White balance A/B', seed=42, config=IspRtlConfig(engine='isp_rtl', gains=[192,128,128,160]).model_dump())] + adas_templates() + validation_templates(),
        adas_catalog=[dict(id='adas-camera-4ch/1', channels=4, width=1920, height=1080, fps=30,
            clock_hz=100000000, fifo_depth=4096, source_kind='bundled_open_rtl',
            scope='Four independent RAW12 streaming channels; no physical CSI/PHY, DDR, ECU or Qualcomm implementation validation',
            hardware_target_binding='not_configured')],
        isp_catalog=[dict(id='mini-isp-blc-wb-mhc/1', source_kind='pinned_open_source',
            stages=['Black level subtraction', 'Bayer white balance', 'Frame RAM barrier', 'MHC demosaic', 'RGB tap'],
            mapping='Apollo TFE-like preprocessing and OFE-like reconstruction; IPE is not implemented',
            scope='Single clock RAW12 / RGB12, bounded frames; not Qualcomm internal implementation')])


def register_engineering_lab_routes(api, db, registry):
    prefix = '/hil-sim/engineering-lab'

    def readiness_payload():
        handler = registry.get(ADAPTER)
        value = dict(adapter_installed=handler is not None, worker_readiness='not_verified')
        build_handler = registry.get('open-isp-ecp5-build')
        value['build_profiles'] = build_handler.profiles() if build_handler is not None else []
        if handler is None:
            return value | dict(status='not_configured', available=False, reason='Engineering adapter is not installed')
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

    @api.get(prefix + '/templates', tags=['engineering-lab'])
    def templates(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        return templates_payload() | readiness_payload()

    @api.post(prefix + '/campaign-plan', tags=['engineering-lab'])
    def plan(body: AdasCampaignConfig, request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        items = campaign_plan(body.model_dump())
        return dict(schema_version='adas-preboard-plan/1', config=body.model_dump(), items=items,
                    total=len(items) + int(body.include_preboard), plan_sha256=digest(items))

    @api.get(prefix + '/readiness', tags=['engineering-lab'])
    def readiness(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        return readiness_payload()

    @api.get(prefix + '/executions/{identity}/campaign-progress', tags=['engineering-lab'])
    def campaign_progress(identity: str, request: Request, project_id: str = 'default'):
        """Provisional worker counters only; terminal verdicts come from sealed evidence."""
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            subject = session.get(ExperimentExecutionRow, identity)
            if subject is None or subject.project_id != project_id:
                raise HTTPException(404, 'Campaign execution not found in project')
            row = jobs._row(session, identity)
            _, prepared = experiment_jobs._subject(session, row)
            config = prepared.to_payload()['input'].get('test', {}).get('config', {})
            if config.get('engine') != 'adas_validation_campaign':
                raise HTTPException(422, 'Execution is not an ADAS validation campaign')
            plan_items = campaign_plan(config)
            response = dict(schema_version='adas-campaign-progress/1', execution_id=identity,
                status=row.status, provisional=row.status not in jobs.TERMINAL,
                total=len(plan_items)+int(config['include_preboard']), completed=0, current_case=None)
            if row.status in jobs.TERMINAL:
                sealed = experiment_jobs.detail(session, row)
                response.update(completed=(sealed.summary or {}).get('total_cases', 0),
                                verdict=(sealed.summary or {}).get('verdict'), manifest_hash=sealed.manifest_hash)
                return response
            directory = db.experiment_dir / 'attempts' / identity
            def bounded_json(path):
                try:
                    if path.is_symlink() or path.stat().st_size > 1048576:
                        return {}
                    value = json.loads(path.read_bytes())
                    return value if isinstance(value, dict) else {}
                except (OSError, ValueError):
                    return {}
            progress = bounded_json(directory / 'campaign-progress.json')
            completed = progress.get('completed')
            if type(completed) is int and 0 <= completed <= response['total']:
                response['completed'] = completed
            case_id = progress.get('current_case')
            cases = {item['id']: item for item in plan_items}
            if isinstance(case_id, str) and case_id in cases:
                response['current_case'] = case_id
                native = bounded_json(directory / 'cases' / case_id / 'progress.json')
                # Only bounded numeric counters cross this live, unsealed API.
                response['counters'] = {key: value for key, value in native.items()
                    if type(value) in (int, float) and 0 <= value <= 6100000000}
                response['max_cycles'] = cases[case_id]['config']['max_cycles']
            elif case_id == 'preboard':
                response['current_case'] = case_id
            return response

    @api.post(prefix + '/tests', status_code=201, tags=['engineering-lab'])
    def save_test(body: EngineeringTestSave, request: Request, idempotency_key: str = Header(alias='Idempotency-Key')):
        project_authorize(request, db, body.project_id, 'Engineer')
        experiment_jobs._key(body.project_id, idempotency_key, 'engineering-test-save')
        key_id, body_hash = digest(idempotency_key), digest(body.model_dump(mode='json'))
        with jobs._write(db) as session:
            existing = session.get(Row, (body.project_id, 'engineering-test-write', key_id, 1))
            if existing:
                receipt = json.loads(existing.manifest)
                if digest(receipt) != existing.manifest_hash or receipt['request_hash'] != body_hash:
                    raise HTTPException(409, 'Idempotency key identifies another Engineering test write')
                return resolve_test(session, body.project_id, receipt['ref'])
            identity = body.id or uid('engt')
            latest = session.scalar(select(func.max(Row.revision)).where(
                Row.project_id == body.project_id, Row.kind == 'engineering-test', Row.id == identity)) or 0
            if body.expected_revision != latest or (body.id is None and body.expected_revision):
                raise HTTPException(409, 'Engineering test changed; reopen its latest revision')
            content = body.model_dump(exclude={'id', 'expected_revision'})
            value = content | dict(schema_version='engineering-test/1', id=identity, revision=latest + 1,
                                   created_at=utcnow(), fidelity=FIDELITY)
            resolve_isp_asset(session, value)
            resolve_adas_assets(session, value)
            value.update(source_identity(value['config']))
            sha = digest(value)
            session.add(Row(project_id=body.project_id, kind='engineering-test', id=identity, revision=latest + 1,
                            manifest=canonical_bytes(value), manifest_hash=sha))
            receipt = dict(request_hash=body_hash, ref=dict(id=identity, revision=latest + 1, sha256=sha))
            session.add(Row(project_id=body.project_id, kind='engineering-test-write', id=key_id, revision=1,
                            manifest=canonical_bytes(receipt), manifest_hash=digest(receipt)))
            return value | {'sha256': sha}

    @api.get(prefix + '/tests', tags=['engineering-lab'])
    def tests(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            rows = session.scalars(select(Row).where(Row.project_id == project_id, Row.kind == 'engineering-test')
                                   .order_by(Row.id, Row.revision.desc()))
            items, unavailable = [], []
            for row in rows:
                try:
                    items.append(read_test(row))
                except HTTPException as exc:
                    if exc.status_code != 409:
                        raise
                    unavailable.append(dict(id=row.id, revision=row.revision,
                        status_code=exc.status_code, error=exc.detail))
            return {'items': items, 'unavailable_revisions': unavailable}

    @api.get(prefix + '/tests/{identity}', tags=['engineering-lab'])
    def test(identity: str, request: Request, revision: int = Query(ge=1), project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return read_test(session.get(Row, (project_id, 'engineering-test', identity, revision)))

    @api.post(prefix + '/tests/{identity}/preview', tags=['engineering-lab'])
    @api.post(prefix + '/tests/{identity}/preflight', tags=['engineering-lab'])
    def preview(identity: str, body: DesignAction, request: Request):
        project_authorize(request, db, body.project_id, 'Engineer')
        reference = dict(id=identity, revision=body.revision, sha256=body.sha256)
        with db.read_session() as session:
            saved = resolve_test(session, body.project_id, reference)
        spec = ExperimentCreate(project_id=body.project_id, name=saved['name'], kind='engineering_lab',
                                adapter_id=ADAPTER, input={'test_ref': reference}, seed=saved['seed'])
        prepared = experiment_jobs.prepare(registry, spec)
        return dict(experiment=prepared.to_payload()['spec'], preview_hash=prepared.input_hash,
                    fidelity=prepared.to_payload()['input'].get('execution_fidelity', FIDELITY), worker_readiness='not_verified')

    @api.post(prefix + '/tests/{identity}/build-preview', tags=['engineering-lab'])
    def build_preview(identity: str, body: EngineeringBuildAction, request: Request):
        project_authorize(request, db, body.project_id, 'Engineer')
        reference = dict(id=identity, revision=body.revision, sha256=body.sha256)
        with db.read_session() as session:
            saved = resolve_test(session, body.project_id, reference)
        if saved['config']['engine'] not in {'isp_rtl', 'adas_module_rtl'}:
            raise HTTPException(422, 'FPGA build requires a bundled Open ISP or ADAS module RTL test')
        spec = ExperimentCreate(project_id=body.project_id, name=saved['name'][:155] + ' · FPGA OOC build', kind='fpga_build',
            adapter_id='open-isp-ecp5-build', input={'test_ref': reference, 'build_profile_id': body.build_profile_id}, seed=saved['seed'])
        prepared = experiment_jobs.prepare(registry, spec)
        return dict(experiment=prepared.to_payload()['spec'], preview_hash=prepared.input_hash,
                    fidelity='fpga_ooc_implementation', worker_readiness='not_verified')

    @api.get(prefix + '/executions', tags=['engineering-lab'])
    def executions(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            rows = session.execute(select(ExperimentRow, ExperimentExecutionRow, JobRow).join(
                ExperimentExecutionRow, ExperimentExecutionRow.experiment_id == ExperimentRow.id).join(
                JobRow, JobRow.id == ExperimentExecutionRow.job_id).where(
                ExperimentRow.project_id == project_id, ExperimentRow.kind.in_(['engineering_lab', 'fpga_build']))
                .order_by(JobRow.created_at.desc()).limit(100)).all()
            items, unavailable = [], []
            for experiment, execution, job in rows:
                prepared = experiment_jobs.experiment_get(session, experiment.id)[1]['prepared']
                ref = prepared['spec']['input']['test_ref']
                try:
                    saved = resolve_test(session, project_id, ref)
                except HTTPException as exc:
                    if exc.status_code not in {404, 409}:
                        raise
                    unavailable.append(dict(id=ref['id'], revision=ref['revision'], execution_id=execution.job_id,
                        status_code=exc.status_code, error=exc.detail))
                    continue
                snapshot = prepared.get('input', {})
                items.append(dict(id=execution.job_id, status=job.status, name=experiment.name, test_ref=ref,
                    operation='build' if experiment.kind == 'fpga_build' else 'simulate', engine=saved['config']['engine'],
                    design_hash=snapshot.get('design_hash'), input_hash=(execution.summary or {}).get('input_sha256'),
                    prepared_input_hash=job.input_hash,
                    build_profile_id=prepared['spec']['input'].get('build_profile_id')))
            return {'items': items, 'unavailable_revisions': unavailable}
