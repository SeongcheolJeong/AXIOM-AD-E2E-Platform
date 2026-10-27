"""Guided Sensor Sim presets using the existing native experiment boundary.

Catalog rows pin authoring inputs and explicit saved results. Listing/binding do
not probe or start CARLA; preview calls the existing handler's native preflight.
"""
import copy
import hashlib
import json
import math
from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from . import experiment_jobs
from .experiment_models import ExperimentExecutionRow
from .experiment_schemas import ExperimentCreate
from .job_models import JobRow
from .sensor_capture_schemas import SensorCaptureSpec, capture_template
from .driving_sensor_capture_schemas import DrivingSensorCaptureSpec, driving_capture_template
from .sensor_demo_schemas import SensorDemoBinding, SensorDemoDefinition, SensorDemoPreview
from .service import canonical_bytes
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .team_access import project_authorize

KIND = 'sensor-demo-preset'
ADAPTER = 'carla-streaming-capture'
FIDELITY = 'actual_carla_native_sensor_model'
DRIVING_ADAPTER = 'carla-driving-multisensor'
DRIVING_FIDELITY = 'actual_carla_reference_driving_multisensor'
PRESETS = {
    'camera_fov': dict(title='카메라 시야각', description='전방 카메라 시야각을 바꾸고 관측 영역과 객체 크기를 비교합니다.',
        key='front_fov_deg', sensor_key='front', native_key='fov', label='전방 카메라 시야각', unit='°',
        min=50, max=110, step=1, baseline=90, default=70),
    'lidar_density': dict(title='LiDAR 밀도', description='초당 발사점 수를 바꾸고 실제 반환점의 분포와 밀도를 비교합니다.',
        key='lidar_points_per_second', sensor_key='lidar', native_key='points_per_second', label='LiDAR 초당 발사점', unit='points/s',
        min=8000, max=32000, step=1000, baseline=32000, default=8000),
    'radar_range': dict(title='Radar 범위', description='Radar 관측 범위를 바꾸고 원본 반환점의 차이를 조사합니다.',
        key='radar_range_m', sensor_key='radar', native_key='range', label='Radar 최대 관측 범위', unit='m',
        min=10, max=100, step=1, baseline=80, default=30),
    'driving_camera_fov': dict(title='도심 주행 · 카메라 시야각', description='20초 도심 주행에서 전후방 RGB·LiDAR·Radar를 함께 수집하고 시야각 차이를 비교합니다.',
        key='front_fov_deg', sensor_key='front', native_key='fov', label='전방 카메라 시야각', unit='°',
        min=50, max=110, step=1, baseline=90, default=70),
}


def _preset(identity):
    if identity not in PRESETS:
        raise HTTPException(404, 'Sensor Sim preset not found')
    return PRESETS[identity]


def _driving(identity):
    return identity == 'driving_camera_fov'


def _adapter(identity):
    return DRIVING_ADAPTER if _driving(identity) else ADAPTER


def _fidelity(identity):
    return DRIVING_FIDELITY if _driving(identity) else FIDELITY


def _model(identity):
    return DrivingSensorCaptureSpec if _driving(identity) else SensorCaptureSpec


def _metadata(registry, identity):
    handler = registry.get(_adapter(identity))
    if handler is None:
        raise HTTPException(422, 'The requested native sensor capture adapter is not installed')
    metadata = handler.metadata()
    if metadata.fidelity != _fidelity(identity) or 'sensor_capture' not in metadata.kinds:
        raise HTTPException(422, 'Preset requires the matching native sensor capture adapter')
    return metadata


def _capture(value, identity):
    """Only the documented four-sensor demo rig; allow an explicit build pin."""
    expected = driving_capture_template() if _driving(identity) else capture_template()
    model_type = _model(identity)
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    try:
        rig = model_type.model_validate(expected if value is None else value)
    except ValueError as exc:
        raise HTTPException(422, 'Capture schema does not match the selected demo mode') from exc
    actual = rig.model_dump(mode='json')
    expected['expected_server_version'] = actual['expected_server_version']
    expected['rig_revision'] = actual['rig_revision']
    # Expand native defaults to accept explicit and implicit equivalent values.
    def expanded(document):
        normalized = model_type.model_validate(document).model_dump(mode='json')
        for sensor, model in zip(normalized['sensors'], model_type.model_validate(document).sensors):
            sensor['parameters'] = model.native_parameters()
        return normalized
    if expanded(actual) != expanded(expected):
        raise HTTPException(422, 'Demo baseline requires its fixed Town10HD_Opt spawn 0, duration/controller, 10 Hz, front/rear RGB, 16-channel LiDAR and radar rig')
    return actual


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
                project, KIND, identity, revision, 'sensor-sim-preset-binding/1'):
            raise ValueError('identity')
        SensorDemoDefinition.model_validate(value['definition'])
        return {**value, 'sha256': row.manifest_hash}
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(409, 'Sensor Sim preset binding failed its integrity check') from exc


def _parameters(identity, supplied):
    preset = _preset(identity)
    if set(supplied) - {preset['key']}:
        raise HTTPException(422, 'Unsupported parameter for this Sensor Sim preset')
    value = supplied.get(preset['key'], preset['default'])
    if (type(value) not in (int, float) or not math.isfinite(value)
            or not preset['min'] <= value <= preset['max']
            or not float(value).is_integer()
            or (value - preset['min']) % preset['step'] != 0):
        raise HTTPException(422, 'Preset parameter is outside its supported type, limits or step')
    if value == preset['baseline']:
        raise HTTPException(422, 'Choose a value different from baseline A to run a sensor comparison')
    return {preset['key']: int(value)}


def _browser_numbers(value):
    """Make new demo inputs stable across Python JSON and JSON.stringify.

    ExperimentCreate.input is deliberately untyped; a 0.0 authored here would
    otherwise become 0 in the browser and change the reviewed spec hash. Keep
    this normalization local to new demo drafts, preserving historical hashes.
    """
    if isinstance(value, dict):
        return {key: _browser_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_browser_numbers(item) for item in value]
    if type(value) is float and value.is_integer():
        return int(value)
    return value


def _definition(identity, project, source, parameters):
    preset = _preset(identity)
    baseline = _browser_numbers(_capture(source['capture_spec'], identity))
    changed = copy.deepcopy(baseline)
    target = next(sensor for sensor in changed['sensors'] if sensor['key'] == preset['sensor_key'])
    target['parameters'][preset['native_key']] = str(parameters[preset['key']])
    changed['rig_revision'] += 1
    return ExperimentCreate(project_id=project, name=f"Sensor Sim · {preset['title']} · {preset['baseline']} → {parameters[preset['key']]}",
        kind='sensor_capture', adapter_id=_adapter(identity), adapter_version=source['adapter_version'],
        input={'rig_a': baseline, 'rig_b': changed}, comparison_kind='same_scene_sensor_ab', seed=source['seed'])


def _validate_saved(session, project, identity, source):
    saved = source.get('saved_execution_id')
    if not saved:
        return
    subject = session.get(ExperimentExecutionRow, saved)
    job = session.scalar(select(JobRow).where(JobRow.id == saved))
    if subject is None or subject.project_id != project or job is None:
        raise HTTPException(404, 'Saved Sensor Sim execution not found in project')
    detail = experiment_jobs.detail(session, job)
    expected = _definition(identity, project, source, _parameters(identity, {})).model_dump(mode='json')
    spec = job.prepared['spec']
    # The descriptive experiment name is free; the actual source and inputs are pinned.
    if any(spec.get(key) != value for key, value in expected.items() if key != 'name'):
        raise HTTPException(422, 'Saved execution must use this preset baseline and default changed rig')
    snapshot = job.prepared['input']
    if (detail.status != 'completed' or detail.outcome_uncertain or detail.fidelity != _fidelity(identity)
            or snapshot.get('schema_version') != ('carla-driving-multisensor-experiment/1' if _driving(identity) else 'carla-streaming-experiment/1')
            or snapshot.get('comparison_kind') != 'same_scene_sensor_ab'):
        raise HTTPException(422, 'Saved preset requires a completed native sensor A/B execution')
    for name, rig in expected['input'].items():
        frozen = copy.deepcopy(rig)
        frozen['seed'] = expected['seed']
        for sensor, model in zip(frozen['sensors'], _model(identity).model_validate(rig).sensors):
            sensor['native_parameters'] = model.native_parameters()
        if snapshot.get('rigs', {}).get(name) != frozen:
            raise HTTPException(409, 'Saved native input differs from the pinned demo rig')
    summary = detail.summary or {}
    captures = summary.get('captures', {})
    names = {artifact['name'] for artifact in detail.artifacts}
    required = {f'{side}-{suffix}' for side in ('rig_a', 'rig_b') for suffix in ('native-capture.zip', 'capture-manifest.json')}
    if (summary.get('schema_version') != 'sensor-experiment-result/1' or set(captures) != {'rig_a', 'rig_b'}
            or not required.issubset(names)
            or any(capture.get('frame_count') != int(expected['input']['rig_a']['duration_seconds'] * expected['input']['rig_a']['sample_hz']) or capture.get('sensor_count') != 4
                   or capture.get('drop_count') != 0 or capture.get('cleanup_status') != 'confirmed'
                   for capture in captures.values())):
        raise HTTPException(422, 'Saved execution does not contain the complete two-rig native capture')


def _item(session, registry, project, identity):
    preset = _preset(identity)
    parameter = {key: value for key, value in preset.items() if key not in ('title', 'description', 'native_key')}
    result = dict(id=identity, version=1, title=preset['title'], description=preset['description'],
        ready=False, status='unbound', reason='이 프로젝트에 데모 센서 설정이 아직 고정되지 않았습니다.',
        parameters=[{**parameter, 'type': 'number'}], defaults={preset['key']: preset['default']},
        binding_revision=None, binding_sha256=None, saved_execution_id=None, source=None)
    try:
        binding = _binding(session, project, identity)
        if binding is None:
            return result
        source = binding['definition']
        result.update(binding_revision=binding['revision'], binding_sha256=binding['sha256'],
            saved_execution_id=source.get('saved_execution_id'),
            source={**{key: source[key] for key in ('capture_spec', 'seed', 'adapter_version')}, 'adapter_id': source.get('adapter_id', ADAPTER)})
        _capture(source['capture_spec'], identity)
        if source.get('adapter_id', ADAPTER) != _adapter(identity):
            raise HTTPException(409, 'Preset adapter identity does not match its capture mode')
        _validate_saved(session, project, identity, source)
        metadata = _metadata(registry, identity)
        if metadata.version != source['adapter_version']:
            raise HTTPException(409, 'Pinned capture adapter version changed; rebind the preset explicitly')
        result.update(ready=True, status='ready', reason=None)
    except (HTTPException, ValueError, TypeError, KeyError) as exc:
        result.update(status='invalid', reason=str(exc.detail if isinstance(exc, HTTPException) else exc))
    return result


def register_sensor_demo_routes(api, db, registry):
    @api.get('/sensor-sim/presets', tags=['Sensor Sim'])
    def presets(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return dict(schema_version='sensor-sim-presets/1', project_id=project_id,
                items=[_item(session, registry, project_id, identity) for identity in PRESETS],
                ready_scope='pinned_authoring_inputs_only_native_preflight_required')

    @api.post('/sensor-sim/presets/{identity}/bindings', tags=['Sensor Sim'], openapi_extra={'x-axiom-min-role': 'Engineer'})
    def bind(identity: str, body: SensorDemoBinding, request: Request):
        _preset(identity)
        project_authorize(request, db, body.project_id, 'Engineer')
        definition = SensorDemoDefinition(capture_spec=_capture(body.capture_spec, identity), seed=body.seed,
            adapter_id=_adapter(identity),
            adapter_version=_metadata(registry, identity).version, saved_execution_id=body.saved_execution_id).model_dump(mode='json')
        try:
            with db.session() as session:
                _validate_saved(session, body.project_id, identity, definition)
                previous = _binding(session, body.project_id, identity)
                revision = previous['revision'] if previous else 0
                if previous and definition == previous['definition'] and body.expected_revision in (revision, revision - 1):
                    return previous
                if revision != body.expected_revision:
                    raise HTTPException(409, 'Preset binding changed; reload before binding')
                value = dict(schema_version='sensor-sim-preset-binding/1', project_id=body.project_id,
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

    @api.post('/sensor-sim/presets/{identity}/preview', tags=['Sensor Sim'],
              openapi_extra={'x-axiom-min-role': 'Engineer', 'x-axiom-read-only': True})
    def preview(identity: str, body: SensorDemoPreview, request: Request):
        preset = _preset(identity)
        project_authorize(request, db, body.project_id, 'Engineer')
        with db.read_session() as session:
            binding = _binding(session, body.project_id, identity)
            if binding is None or binding['revision'] != body.expected_binding_revision:
                raise HTTPException(409, 'Preset binding changed or is missing; reload before preview')
            source = binding['definition']
            parameters = _parameters(identity, body.parameters)
            definition = _definition(identity, body.project_id, source, parameters)
        prepared = experiment_jobs.prepare(registry, definition)
        payload = prepared.to_payload()
        return dict(schema_version='sensor-sim-preset-preview/1', project_id=body.project_id, preset_id=identity,
            binding_revision=binding['revision'], binding_sha256=binding['sha256'], parameters=parameters,
            changes=[dict(key=preset['key'], label=preset['label'], unit=preset['unit'], baseline=preset['baseline'], value=parameters[preset['key']])],
            experiment=definition.model_dump(mode='json'), preview=dict(preview_hash=prepared.input_hash,
                prepared=payload, job_count=1, resource_id=payload['handler']['resource_id'],
                execution_fidelity=payload['input'].get('execution_fidelity', payload['handler']['fidelity']),
                requires_explicit_submit=True, evaluation_status='not_evaluated'))
