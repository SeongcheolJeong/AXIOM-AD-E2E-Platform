"""Pinned HIL Workbench presets over the normal durable Experiment API.

Listing and binding validate immutable authoring sources without starting CARLA.
Preview invokes native preflight. Physical hardware and safety validation remain
outside this software-DUT demonstration's evidence scope.
"""
import hashlib
import json
import math
from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from . import experiment_jobs
from .experiment_models import ExperimentExecutionRow
from .experiment_schemas import ExperimentCreate
from .hil_demo_schemas import HilDemoBinding, HilDemoDefinition, HilDemoPreview
from .hil_scene_profiles import list_scene_profiles, resolve_scene_profile, validate_scene_profile
from .job_models import JobRow
from .scenario_v2 import get_scenario
from .service import canonical_bytes
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .team_access import project_authorize

KIND = 'hil-demo-preset'
ADAPTER = 'carla-software-dut'
FIDELITY = 'actual_carla_software_dut'
PRESETS = {
    'input_delay': dict(title='객체 입력 지연', description='객체 입력을 지연시키고 Software DUT의 정지와 복구 반응을 비교합니다.',
        key='delay_ms', label='객체 입력 지연', unit='ms', min=50, max=1000, step=50, default=300),
    'input_repeat': dict(title='객체 패킷 반복', description='마지막 패킷의 sequence·timestamp·내용을 그대로 반복 전달하고 ECU 중복 검출과 회복을 검증합니다.',
        key='duration_s', label='패킷 반복 시간', unit='s', min=.5, max=4, step=.5, default=4),
    'input_dropout': dict(title='객체 입력 단절', description='객체 입력을 잠시 끊고 Software DUT의 상태와 제동 반응을 비교합니다.',
        key='duration_s', label='입력 단절 시간', unit='s', min=.5, max=4, step=.5, default=1),
}


def _preset(identity):
    if identity not in PRESETS:
        raise HTTPException(404, 'HIL Workbench preset not found')
    return PRESETS[identity]


def _metadata(registry):
    handler = registry.get(ADAPTER)
    if handler is None:
        raise HTTPException(422, 'Software DUT adapter is not installed')
    metadata = handler.metadata()
    if metadata.fidelity != FIDELITY or 'hil_demo' not in metadata.kinds:
        raise HTTPException(422, 'Preset requires the native CARLA software-DUT adapter')
    return metadata


def _binding(session, project, identity):
    revision = session.scalar(select(func.max(Row.revision)).where(Row.project_id == project, Row.kind == KIND, Row.id == identity))
    row = session.get(Row, (project, KIND, identity, revision)) if revision else None
    if row is None:
        return None
    try:
        if hashlib.sha256(row.manifest).hexdigest() != row.manifest_hash:
            raise ValueError('hash')
        value = json.loads(row.manifest)
        if (value['project_id'], value['kind'], value['id'], value['revision'], value['schema_version']) != (
                project, KIND, identity, revision, 'hil-sim-preset-binding/1'):
            raise ValueError('identity')
        HilDemoDefinition.model_validate(value['definition'])
        return {**value, 'sha256': row.manifest_hash}
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(409, 'HIL Workbench preset binding failed its integrity check') from exc


def _bound_source(session, project, source):
    ref = source['scenario_ref']
    scenario = get_scenario(session, ref['id'], ref['revision'])
    if scenario['project_id'] != project:
        raise HTTPException(404, 'Preset scenario not found in project')
    if any(scenario[key] != ref[key] for key in ('configuration_hash', 'manifest_hash')):
        raise HTTPException(409, 'Preset scenario pin differs from its immutable revision')
    if scenario['scenario']['schema_version'] != 'scenario-v2/3':
        raise HTTPException(422, 'HIL Workbench requires a native city scenario-v2/3 source')
    if source['adapter_id'] != ADAPTER:
        raise HTTPException(409, 'Preset software-DUT adapter identity changed')
    return scenario


def _fault_timing(identity):
    _preset(identity)
    return dict(schema_version='hil-fault-timing/1', start_min_s=1, start_max_s=17,
        end_max_s=17.5, duration_min_s=.5, duration_max_s=4, step_s=.5,
        default_start_s=3 if identity == 'input_delay' else 3.5,
        default_duration_s=4 if identity == 'input_delay' else 1)


def _parameters(identity, supplied):
    preset, timing = _preset(identity), _fault_timing(identity)
    if set(supplied) - {preset['key'], 'start_s', 'duration_s'}:
        raise HTTPException(422, 'Unsupported parameter for this HIL Workbench preset')
    limits = {preset['key']: (preset['min'], preset['max'], preset['step']),
              'start_s': (timing['start_min_s'], timing['start_max_s'], timing['step_s']),
              'duration_s': (timing['duration_min_s'], timing['duration_max_s'], timing['step_s'])}
    result = {preset['key']: supplied.get(preset['key'], preset['default']), **supplied}
    for key, value in result.items():
        lower, upper, step = limits[key]
        if type(value) not in (int, float) or not lower <= value <= upper or not math.isfinite(value):
            raise HTTPException(422, 'HIL parameter is outside its supported type, limits or step')
        # Half seconds are exactly representable; near-grid timing is not rounded.
        on_grid = (value % .5 == 0 if key in {'start_s', 'duration_s'} else
                   math.isclose((value - lower) / step, round((value - lower) / step), abs_tol=1e-9))
        if not on_grid:
            raise HTTPException(422, 'HIL parameter is outside its supported type, limits or step')
        result[key] = int(value) if float(value).is_integer() else value
    if result.get('start_s', timing['default_start_s']) + result.get('duration_s', timing['default_duration_s']) > timing['end_max_s']:
        raise HTTPException(422, 'HIL fault must end by17.5 seconds to leave a recovery interval')
    # Do not add optional timing keys to old one-parameter definitions/receipts.
    return result


def _changes(identity, parameters):
    preset, timing = _preset(identity), _fault_timing(identity)
    result = [dict(key=preset['key'], label=preset['label'], unit=preset['unit'], baseline=0, value=parameters[preset['key']])]
    for key, label, baseline in [('start_s', '고장 시작 시각', timing['default_start_s']),
                                 ('duration_s', '고장 지속 시간', timing['default_duration_s'])]:
        if key in parameters and key != preset['key']:
            result.append(dict(key=key, label=label, unit='s', baseline=baseline, value=parameters[key]))
    return result


def _definition(identity, project, source, parameters, dut_profiles=None, scene_profile=None):
    preset = _preset(identity)
    timing = _fault_timing(identity)
    fault = dict(kind={'input_delay':'delay','input_dropout':'drop','input_repeat':'repeat'}[identity],
        start_s=parameters.get('start_s', timing['default_start_s']),
        duration_s=parameters.get('duration_s', timing['default_duration_s']),
        delay_ms=parameters['delay_ms'] if identity == 'input_delay' else 0)
    inputs = dict(scenario_id=source['scenario_ref']['id'], scenario_revision=source['scenario_ref']['revision'], fault=fault)
    if dut_profiles is not None:
        inputs['dut_profiles'] = dut_profiles
    if scene_profile is not None:
        inputs['scene_profile'] = validate_scene_profile(scene_profile)
    return ExperimentCreate(project_id=project, name=f"HIL Workbench · {preset['title']} · {parameters[preset['key']]} {preset['unit']}",
        kind='hil_demo', adapter_id=ADAPTER, adapter_version=source['adapter_version'],
        input=inputs, comparison_kind='same_fault_dut_ab' if dut_profiles is not None else 'same_scene_fault_ab', seed=source['seed'])


def _snapshot_source(snapshot, source):
    ref = source['scenario_ref']
    expected = dict(scenario_id=ref['id'], scenario_revision=ref['revision'],
        configuration_hash=ref['configuration_hash'], scenario_manifest_hash=ref['manifest_hash'], seed=source['seed'])
    actual = snapshot.get('scenario_source', {})
    if not isinstance(actual, dict) or any(actual.get(key) != value for key, value in expected.items()):
        raise HTTPException(409, 'Software DUT snapshot differs from the pinned scenario source')


def _resolve_scene(reference):
    if reference is None:
        return None
    try:
        return resolve_scene_profile(reference.model_dump(mode='json') if hasattr(reference, 'model_dump') else reference)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


def _snapshot_scene(snapshot, scene_profile):
    if scene_profile is None:
        if 'scene_profile' in snapshot:
            raise HTTPException(409, 'Legacy HIL input cannot acquire a scene overlay')
    elif canonical_bytes(snapshot.get('scene_profile')) != canonical_bytes(scene_profile):
        raise HTTPException(409, 'Software DUT snapshot differs from the frozen scene profile')


def _validate_saved(session, project, identity, source):
    saved = source.get('saved_execution_id')
    if not saved:
        return
    subject = session.get(ExperimentExecutionRow, saved)
    job = session.scalar(select(JobRow).where(JobRow.id == saved))
    if subject is None or subject.project_id != project or job is None:
        raise HTTPException(404, 'Saved HIL Workbench execution not found in project')
    detail = experiment_jobs.detail(session, job)
    expected = _definition(identity, project, source, _parameters(identity, {})).model_dump(mode='json')
    if any(job.prepared['spec'].get(key) != value for key, value in expected.items() if key != 'name'):
        raise HTTPException(422, 'Saved execution must use this preset scenario and default fault settings')
    snapshot = job.prepared['input']
    if (detail.status != 'completed' or detail.outcome_uncertain or detail.fidelity != FIDELITY
            or snapshot.get('schema_version') != 'carla-software-dut-experiment/1'
            or snapshot.get('comparison_kind') != 'same_scene_fault_ab'):
        raise HTTPException(422, 'Saved preset requires a completed native software-DUT A/B execution')
    _snapshot_source(snapshot, source)
    summary = detail.summary or {}
    sides = summary.get('sides', {})
    names = {artifact['name'] for artifact in detail.artifacts}
    required = {f'{side}-{suffix}' for side in ('baseline', 'faulted') for suffix in ('city-scene.json', 'hil-trace.json')}
    if (summary.get('schema_version') != 'hil-demo-result/1' or summary.get('execution_target') != 'software_dut'
            or summary.get('fault') != expected['input']['fault'] or set(sides) != {'baseline', 'faulted'}
            or not required.issubset(names)
            or any(side.get('schema_version') != 'carla-city-result/1' or side.get('cleanup_status') != 'confirmed'
                   or side.get('execution_status') != 'completed' for side in sides.values())):
        raise HTTPException(422, 'Saved execution lacks complete software-DUT A/B scene and trace evidence')


def _item(session, registry, project, identity):
    preset = _preset(identity)
    parameter = {key: value for key, value in preset.items() if key not in ('title', 'description')}
    result = dict(id=identity, version=1, title=preset['title'], description=preset['description'],
        ready=False, status='unbound', reason='이 프로젝트에 HIL 데모 시나리오가 아직 연결되지 않았습니다.',
        parameters=[{**parameter, 'type': 'number', 'baseline': 0}], defaults={preset['key']: preset['default']},
        fault_timing=_fault_timing(identity),
        binding_revision=None, binding_sha256=None, saved_execution_id=None, source=None)
    try:
        binding = _binding(session, project, identity)
        if binding is None:
            return result
        source = binding['definition']
        result.update(binding_revision=binding['revision'], binding_sha256=binding['sha256'],
            saved_execution_id=source.get('saved_execution_id'), source=source)
        _bound_source(session, project, source)
        _validate_saved(session, project, identity, source)
        if _metadata(registry).version != source['adapter_version']:
            raise HTTPException(409, 'Pinned software-DUT adapter version changed; rebind explicitly')
        result.update(ready=True, status='ready', reason=None)
    except (HTTPException, ValueError, TypeError, KeyError) as exc:
        result.update(status='invalid', reason=str(exc.detail if isinstance(exc, HTTPException) else exc))
    return result


def register_hil_demo_routes(api, db, registry):
    @api.get('/hil-sim/scenes', tags=['HIL Workbench'])
    def scenes(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        return dict(schema_version='hil-sim-scenes/1', project_id=project_id, default_scene_id='lead_stop',
                    source_scope='existing_preset_bound_base_scene', items=list_scene_profiles())

    @api.get('/hil-sim/presets', tags=['HIL Workbench'])
    def presets(request: Request, project_id: str = 'default'):
        """List explicitly pinned Software DUT demo inputs and saved native results."""
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return dict(schema_version='hil-sim-presets/1', project_id=project_id,
                items=[_item(session, registry, project_id, identity) for identity in PRESETS],
                ready_scope='pinned_authoring_inputs_only_native_preflight_required', execution_target='software_dut')

    @api.post('/hil-sim/presets/{identity}/bindings', tags=['HIL Workbench'], openapi_extra={'x-axiom-min-role': 'Engineer'})
    def bind(identity: str, body: HilDemoBinding, request: Request):
        """Pin a scenario revision and optional completed default A/B result."""
        _preset(identity)
        project_authorize(request, db, body.project_id, 'Engineer')
        definition = HilDemoDefinition(scenario_ref=body.scenario_ref, seed=body.seed,
            adapter_version=_metadata(registry).version, saved_execution_id=body.saved_execution_id).model_dump(mode='json')
        try:
            with db.session() as session:
                _bound_source(session, body.project_id, definition)
                _validate_saved(session, body.project_id, identity, definition)
                previous = _binding(session, body.project_id, identity)
                revision = previous['revision'] if previous else 0
                if previous and definition == previous['definition'] and body.expected_revision in (revision, revision - 1):
                    return previous
                if revision != body.expected_revision:
                    raise HTTPException(409, 'Preset binding changed; reload before binding')
                value = dict(schema_version='hil-sim-preset-binding/1', project_id=body.project_id,
                    kind=KIND, id=identity, revision=revision + 1, definition=definition)
                raw = canonical_bytes(value)
                session.add(Row(project_id=body.project_id, kind=KIND, id=identity, revision=revision + 1,
                    manifest=raw, manifest_hash=hashlib.sha256(raw).hexdigest()))
                session.flush()
                result = _binding(session, body.project_id, identity)
                session.commit()
                return result
        except IntegrityError as exc:
            raise HTTPException(409, 'Concurrent preset binding; reload and retry') from exc

    @api.post('/hil-sim/presets/{identity}/preview', tags=['HIL Workbench'],
              openapi_extra={'x-axiom-min-role': 'Engineer', 'x-axiom-read-only': True})
    def preview(identity: str, body: HilDemoPreview, request: Request):
        """Preflight the native source and return a draft for explicit Experiment submission."""
        preset = _preset(identity)
        project_authorize(request, db, body.project_id, 'Engineer')
        with db.read_session() as session:
            binding = _binding(session, body.project_id, identity)
            if binding is None or binding['revision'] != body.expected_binding_revision:
                raise HTTPException(409, 'Preset binding changed or is missing; reload before preview')
            source = binding['definition']
            _bound_source(session, body.project_id, source)
            parameters = _parameters(identity, body.parameters)
            scene_profile = _resolve_scene(body.scene_profile)
            definition = _definition(identity, body.project_id, source, parameters, scene_profile=scene_profile)
        prepared = experiment_jobs.prepare(registry, definition)
        payload = prepared.to_payload()
        _snapshot_source(payload['input'], source)
        _snapshot_scene(payload['input'], scene_profile)
        return dict(schema_version='hil-sim-preset-preview/1', project_id=body.project_id, preset_id=identity,
            binding_revision=binding['revision'], binding_sha256=binding['sha256'], parameters=parameters,
            changes=_changes(identity, parameters), **({'scene_profile': scene_profile} if scene_profile is not None else {}),
            experiment=definition.model_dump(mode='json'), preview=dict(preview_hash=prepared.input_hash,
                prepared=payload, job_count=1, resource_id=payload['handler']['resource_id'],
                execution_fidelity=payload['input'].get('execution_fidelity', payload['handler']['fidelity']),
                requires_explicit_submit=True, evaluation_status='not_evaluated'))
