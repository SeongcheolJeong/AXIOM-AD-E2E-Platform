"""Project-owned Neural Sim sources and explicit presentation presets.

The normal Experiment queue owns execution and artifacts. This module never
imports CUDA or a reconstruction engine, and never guesses a latest saved run.
"""
import hashlib
import json
from fastapi import HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from . import experiment_jobs
from .experiment_models import ExperimentArtifactRow, ExperimentExecutionRow, ExperimentRow
from .experiment_schemas import ExperimentCreate
from .job_models import JobRow
from .neural_demo_schemas import NeuralDemoBinding, NeuralDemoPreview, NeuralReconstructionInput, NeuralRenderInput
from .service import canonical_bytes
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .team_access import project_authorize

KIND = 'neural-demo-preset'
ADAPTER = 'neural-splatfacto'
FIDELITY = 'neural_reconstruction_of_carla'
REAL_ROAD_FIDELITY = 'neural_reconstruction_of_real_road'
SOURCE_ADAPTER = 'carla-driving-multisensor'
SOURCE_FIDELITY = 'actual_carla_reference_driving_multisensor'
PRESETS = {
    'left': dict(title='왼쪽 시점', description='전방 카메라를 왼쪽으로 30 cm 이동해 시점 차이를 봅니다.', lateral_m=-.3, height_m=0),
    'right': dict(title='오른쪽 시점', description='전방 카메라를 오른쪽으로 30 cm 이동해 시점 차이를 봅니다.', lateral_m=.3, height_m=0),
    'raised': dict(title='높은 시점', description='전방 카메라를 20 cm 높여 새 시점을 생성합니다.', lateral_m=0, height_m=.2),
    'sweep': dict(title='시간 정지 · 카메라 이동', description='한 시각을 고정하고 카메라를 좌우로 이동해 장면의 입체감을 봅니다.',
        lateral_m=1.5, height_m=0, mode='camera_sweep', frame_index=50),
}
PARAMETERS = [
    dict(key='lateral_m', label='좌우 이동', unit='m', min=-.5, max=.5, step=.05, baseline=0, type='number'),
    dict(key='height_m', label='높이 이동', unit='m', min=-.3, max=.3, step=.05, baseline=0, type='number'),
]


def _resolve(session, db, project_id, execution_id, expected_kind=None, verify_artifacts=False):
    subject = session.get(ExperimentExecutionRow, execution_id)
    job = session.scalar(select(JobRow).where(JobRow.id == execution_id))
    if subject is None or subject.project_id != project_id or job is None:
        raise HTTPException(404, 'Neural Sim source execution not found in project')
    detail = experiment_jobs.detail(session, job)
    if detail.status != 'completed' or detail.outcome_uncertain:
        raise HTTPException(422, 'Neural Sim requires a completed execution with a confirmed outcome')
    if expected_kind is not None and detail.experiment_kind != expected_kind:
        raise HTTPException(422, 'Neural Sim source has an unsupported experiment kind')
    artifacts = []
    names = set()
    for row in session.scalars(select(ExperimentArtifactRow).where(
            ExperimentArtifactRow.job_id == execution_id).order_by(ExperimentArtifactRow.name, ExperimentArtifactRow.id)):
        if row.name in names:
            raise HTTPException(409, 'Neural source has ambiguous duplicate artifact names')
        names.add(row.name)
        item = dict(id=row.id, name=row.name, sha256=row.sha256, size_bytes=row.size_bytes, media_type=row.media_type)
        if verify_artifacts:
            root = db.experiment_dir / 'artifacts'
            candidate = root / row.storage_key
            if candidate.parent.resolve() != root.resolve():
                raise HTTPException(409, 'Neural source artifact is outside its owned storage')
            item['path'] = experiment_jobs.artifact_path(db, row)
        artifacts.append(item)
    return dict(execution_id=execution_id, project_id=project_id, input_hash=detail.input_hash,
        manifest_hash=detail.manifest_hash, adapter_id=detail.adapter_id, adapter_version=detail.adapter_version,
        kind=detail.experiment_kind, fidelity=detail.fidelity, spec=job.prepared['spec'],
        snapshot=job.prepared['input'], summary=detail.summary or {}, artifacts=artifacts)


def resolve_neural_execution(db, project_id, execution_id, expected_kind=None, *, verify_artifacts=True):
    """Resolve only sealed, same-project files; adapters receive verified owned paths.

    Browser input never controls a local path. Metadata-only callers can skip file
    hashing, while native prepare/execute callers retain the default hash check.
    """
    with db.read_session() as session:
        return _resolve(session, db, project_id, execution_id, expected_kind, verify_artifacts)


def execution_pin(source):
    """Small stable identity without filesystem paths, suitable for snapshots."""
    return {key: source[key] for key in ('execution_id', 'project_id', 'input_hash', 'manifest_hash',
            'adapter_id', 'adapter_version', 'kind', 'fidelity')} | {
        'artifacts': [{key: artifact[key] for key in ('id', 'name', 'sha256', 'size_bytes', 'media_type')}
                      for artifact in source['artifacts']]}


def _source(source):
    spec, snapshot, summary = source['spec'], source['snapshot'], source['summary']
    if source['kind'] == 'neural_source_import':
        names = {item['name'] for item in source['artifacts']}
        if (source['adapter_id'] != ADAPTER or source['fidelity'] != 'recorded_real_road'
                or summary.get('schema_version') != 'neural-real-road-source/1'
                or summary.get('source_kind') != 'real_road'
                or type(summary.get('frame_count')) is not int or not 3 <= summary['frame_count'] <= 200
                or not {'neural-real-road-source.json', 'neural-real-road-dataset.zip'}.issubset(names)):
            raise HTTPException(422, 'Neural Sim requires a sealed real-road source import with calibrated front-camera images')
        return
    rig = spec.get('input', {}).get('rig_a', {})
    capture = summary.get('captures', {}).get('rig_a', {})
    names = {item['name'] for item in source['artifacts']}
    if (source['adapter_id'] != SOURCE_ADAPTER or source['kind'] != 'sensor_capture'
            or source['fidelity'] != SOURCE_FIDELITY
            or snapshot.get('schema_version') != 'carla-driving-multisensor-experiment/1'
            or rig.get('duration_seconds') != 20
            or not any(sensor.get('key') == 'front' and sensor.get('kind') == 'rgb' for sensor in rig.get('sensors', []))
            or capture.get('cleanup_status') != 'confirmed' or capture.get('frame_count') != 200
            or not {'rig_a-capture-manifest.json', 'rig_a-native-capture.zip'}.issubset(names)):
        raise HTTPException(422, 'Neural Sim requires the complete 20-second CARLA driving rig A with front RGB')


def _reconstruction(reconstruction, source):
    names = {item['name']: item for item in reconstruction['artifacts']}
    spec, summary, snapshot = reconstruction['spec'], reconstruction['summary'], reconstruction['snapshot']
    try:
        inputs = NeuralReconstructionInput.model_validate(spec.get('input')).model_dump(mode='json')
    except ValueError as exc:
        raise HTTPException(422, 'Reconstruction has an unsupported source input') from exc
    if (reconstruction['adapter_id'] != ADAPTER or reconstruction['kind'] != 'neural_reconstruction'
            or reconstruction['fidelity'] != (REAL_ROAD_FIDELITY if source['kind'] == 'neural_source_import' else FIDELITY)
            or inputs != dict(source_execution_id=source['execution_id'], rig='rig_a', camera='front')
            or snapshot.get('source_pin') != execution_pin(source)
            or summary.get('schema_version') != 'neural-reconstruction-result/1'
            or summary.get('source_execution_id') != source['execution_id']
            or not {'neural-reconstruction-result.json', 'neural-checkpoint.ckpt'}.issubset(names)
            or summary.get('checkpoint_sha256') != names.get('neural-checkpoint.ckpt', {}).get('sha256')):
        raise HTTPException(422, 'Reconstruction must contain a checkpoint from this exact source')


def _metadata(registry):
    handler = registry.get(ADAPTER)
    if handler is None:
        raise HTTPException(422, 'Neural Splatfacto adapter is not installed')
    metadata = handler.metadata()
    if metadata.fidelity not in (FIDELITY, REAL_ROAD_FIDELITY) or not {'neural_reconstruction', 'neural_render'}.issubset(metadata.kinds):
        raise HTTPException(422, 'Neural Sim requires the Splatfacto reconstruction/render adapter')
    return metadata


def _preset(identity):
    if identity not in PRESETS:
        raise HTTPException(404, 'Neural Sim preset not found')
    return PRESETS[identity]


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
                project, KIND, identity, revision, 'neural-sim-preset-binding/1'):
            raise ValueError('identity')
        return {**value, 'sha256': row.manifest_hash}
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(409, 'Neural Sim binding failed its integrity check') from exc


def _bound_sources(session, db, project, definition):
    source = _resolve(session, db, project, definition['source_execution_id'])
    _source(source)
    if definition['source_pin'] != execution_pin(source):
        raise HTTPException(409, 'Neural Sim source differs from its pinned execution')
    reconstruction = None
    if definition.get('reconstruction_execution_id'):
        reconstruction = _resolve(session, db, project, definition['reconstruction_execution_id'], 'neural_reconstruction')
        _reconstruction(reconstruction, source)
        if definition['reconstruction_pin'] != execution_pin(reconstruction):
            raise HTTPException(409, 'Neural scene differs from its pinned execution')
    return source, reconstruction


def _render_input(identity, reconstruction_id, parameters, mode=None, frame_index=None):
    if set(parameters) - {'lateral_m', 'height_m'}:
        raise HTTPException(422, 'Unsupported Neural Sim parameter')
    preset = _preset(identity)
    mode = preset.get('mode', 'sequence') if mode is None else mode
    frame_index = preset.get('frame_index', 0) if frame_index is None else frame_index
    try:
        result = NeuralRenderInput(reconstruction_execution_id=reconstruction_id,
            lateral_m=parameters.get('lateral_m', preset['lateral_m']), height_m=parameters.get('height_m', preset['height_m']),
            mode=mode, frame_index=frame_index).model_dump(mode='json')
        # JSON.stringify encodes integral floats as integers. Keep input hashes
        # stable across Python SDK and browser authoring.
        return {key: int(value) if type(value) is float and value.is_integer() else value for key, value in result.items()}
    except ValueError as exc:
        raise HTTPException(422, 'Neural view edits must be finite and within their mode bounds: frame/sequence lateral +/-0.5 m, sweep amplitude >0 to 2 m, height +/-0.3 m') from exc


def _definition(identity, project, source, parameters, mode=None, frame_index=None):
    if not source.get('reconstruction_execution_id'):
        raise HTTPException(422, 'Train and explicitly bind a completed neural scene before rendering')
    if frame_index is None and identity == 'sweep':
        frame_index = source.get('default_frame_index', PRESETS['sweep']['frame_index'])
    if identity == 'sweep' and source.get('source_kind') == 'real_road':
        parameters = {'lateral_m': .5, **parameters}
    return ExperimentCreate(project_id=project, name=f"Neural Sim · {_preset(identity)['title']}",
        kind='neural_render', adapter_id=ADAPTER, adapter_version=source['adapter_version'],
        input=_render_input(identity, source['reconstruction_execution_id'], parameters, mode, frame_index), seed=source['seed'])


def _saved(session, db, project, identity, definition, source, reconstruction):
    if not definition.get('saved_execution_id'):
        return
    saved = _resolve(session, db, project, definition['saved_execution_id'], 'neural_render')
    expected = _definition(identity, project, definition, {}).model_dump(mode='json')
    summary, snapshot = saved['summary'], saved['snapshot']
    names = {item['name'] for item in saved['artifacts']}
    if (any(saved['spec'].get(key) != value for key, value in expected.items() if key != 'name')
            or saved['adapter_id'] != ADAPTER or saved['fidelity'] != reconstruction['fidelity']
            or snapshot.get('source_pin') != execution_pin(source)
            or snapshot.get('reconstruction_pin') != execution_pin(reconstruction)
            or summary.get('schema_version') != ('neural-render-summary/2' if expected['input']['mode'] == 'camera_sweep' else 'neural-render-summary/1')
            or any(summary.get(key) != value for key, value in expected['input'].items())
            or summary.get('source_execution_id') != source['execution_id']
            or not {'neural-render-result.json', 'neural-render-frames.zip'}.issubset(names)):
        raise HTTPException(422, 'Saved render must use this pinned scene and the preset default mode, frame and camera settings')


def _item(session, db, registry, project, identity):
    preset = _preset(identity)
    parameters = [dict(item) for item in PARAMETERS]
    if identity == 'sweep':
        parameters[0].update(label='좌우 이동 반경', min=.1, max=2, step=.1)
    result = dict(id=identity, version=1, title=preset['title'], description=preset['description'],
        defaults={key: preset[key] for key in ('lateral_m', 'height_m')},
        default_mode=preset.get('mode', 'sequence'), default_frame_index=preset.get('frame_index', 0),
        parameters=[{**item, 'default': preset[item['key']]} for item in parameters],
        ready=False, status='unbound', reason='원본 주행 로그와 학습된 장면을 연결하세요.',
        binding_revision=None, binding_sha256=None, source=None, saved_execution_id=None)
    try:
        binding = _binding(session, project, identity)
        if binding is None:
            return result
        definition = binding['definition']
        result.update(binding_revision=binding['revision'], binding_sha256=binding['sha256'], source=definition,
            saved_execution_id=definition.get('saved_execution_id'))
        source, reconstruction = _bound_sources(session, db, project, definition)
        result.update(source_kind=definition.get('source_kind', 'synthetic_carla'),
            source_frame_count=definition.get('source_frame_count', 200),
            default_frame_index=definition.get('default_frame_index', 50) if identity == 'sweep' else 0)
        if identity == 'sweep' and definition.get('source_kind') == 'real_road':
            result['defaults']['lateral_m'] = .5
            result['parameters'][0]['default'] = .5
        metadata = _metadata(registry)
        if metadata.version != definition['adapter_version']:
            raise HTTPException(409, 'Pinned Neural Sim adapter version changed; rebind explicitly')
        if reconstruction is None:
            result.update(status='needs_reconstruction', reason='원본이 연결되었습니다. 장면을 학습한 뒤 결과를 연결하세요.')
        else:
            _saved(session, db, project, identity, definition, source, reconstruction)
            result.update(ready=metadata.status == 'available', status='ready' if metadata.status == 'available' else 'not_configured',
                reason=None if metadata.status == 'available' else 'Neural GPU 실행 환경을 설정하세요.')
    except (HTTPException, ValueError, TypeError, KeyError) as exc:
        result.update(status='invalid', reason=str(exc.detail if isinstance(exc, HTTPException) else exc))
    return result


def register_neural_demo_routes(api, db, registry):
    @api.get('/neural-sim/sources', tags=['Neural Sim'])
    def sources(request: Request, project_id: str = 'default', limit: int = Query(default=100, ge=1, le=100)):
        """List completed project-owned sources and scenes for explicit selection."""
        project_authorize(request, db, project_id)
        result = dict(schema_version='neural-sim-sources/1', project_id=project_id, sources=[], reconstructions=[], renders=[], perceptions=[])
        with db.read_session() as session:
            query = select(JobRow, ExperimentRow).join(ExperimentExecutionRow, ExperimentExecutionRow.job_id == JobRow.id).join(
                ExperimentRow, ExperimentRow.id == ExperimentExecutionRow.experiment_id).where(
                ExperimentExecutionRow.project_id == project_id, JobRow.status == 'completed',
                JobRow.adapter_id.in_([SOURCE_ADAPTER, ADAPTER, 'neural-onnx-sensitivity'])).order_by(JobRow.submission_seq.desc()).limit(limit)
            for job, experiment in session.execute(query):
                item = dict(id=job.id, name=experiment.name, experiment_kind=experiment.kind, adapter_id=job.adapter_id,
                    adapter_version=job.adapter_version, created_at=job.created_at, ready=True, reason=None)
                try:
                    record = _resolve(session, db, project_id, job.id)
                    item.update(input_hash=record['input_hash'], manifest_hash=record['manifest_hash'])
                    if experiment.kind in ('sensor_capture', 'neural_source_import'):
                        _source(record)
                        real_road = experiment.kind == 'neural_source_import'
                        frame_count = record['summary'].get('frame_count', 200)
                        item.update(source_kind='real_road' if real_road else 'synthetic_carla', frame_count=frame_count,
                            fps=record['summary'].get('fps', 10), default_frame_index=min(50, frame_count // 2) if real_road else 50,
                            license_scope=record['summary'].get('license_scope'),
                            external_publication_cleared=record['summary'].get('external_publication_cleared', False))
                        result['sources'].append(item)
                    elif experiment.kind == 'neural_perception':
                        item.update(status='completed', **{key: record['summary'].get(key)
                            for key in ('render_execution_id', 'model_id', 'model_revision')})
                        result['perceptions'].append(item)
                    elif experiment.kind in ('neural_reconstruction', 'neural_render'):
                        item.update(source_execution_id=record['summary'].get('source_execution_id'),
                            reconstruction_execution_id=record['summary'].get('reconstruction_execution_id'),
                            parameters={key: record['summary'].get(key) for key in ('lateral_m', 'height_m', 'mode', 'frame_index')})
                        result['reconstructions' if experiment.kind == 'neural_reconstruction' else 'renders'].append(item)
                except (HTTPException, ValueError, TypeError, KeyError):
                    # Corrupt or unsupported completed runs are not selectable.
                    continue
        return result

    @api.get('/neural-sim/presets', tags=['Neural Sim'])
    def presets(request: Request, project_id: str = 'default'):
        """List explicit source, checkpoint and representative render bindings."""
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return dict(schema_version='neural-sim-presets/1', project_id=project_id,
                label='고정 원본 로그의 Neural 재구성',
                ready_scope='pinned_inputs_only_native_preflight_required',
                items=[_item(session, db, registry, project_id, identity) for identity in PRESETS])

    @api.post('/neural-sim/presets/{identity}/bindings', tags=['Neural Sim'], openapi_extra={'x-axiom-min-role': 'Engineer'})
    def bind(identity: str, body: NeuralDemoBinding, request: Request):
        """Pin a completed original source, optional trained scene and default render."""
        _preset(identity)
        project_authorize(request, db, body.project_id, 'Engineer')
        metadata = _metadata(registry)
        try:
            with db.session() as session:
                source = _resolve(session, db, body.project_id, body.source_execution_id)
                _source(source)
                reconstruction = None
                if body.reconstruction_execution_id:
                    reconstruction = _resolve(session, db, body.project_id, body.reconstruction_execution_id, 'neural_reconstruction')
                    _reconstruction(reconstruction, source)
                if body.saved_execution_id and reconstruction is None:
                    raise HTTPException(422, 'A saved render requires an explicitly bound reconstruction')
                definition = dict(source_execution_id=body.source_execution_id, source_pin=execution_pin(source),
                    reconstruction_execution_id=body.reconstruction_execution_id,
                    reconstruction_pin=execution_pin(reconstruction) if reconstruction else None,
                    saved_execution_id=body.saved_execution_id, seed=body.seed,
                    adapter_id=ADAPTER, adapter_version=metadata.version)
                if source['kind'] == 'neural_source_import':
                    definition.update(source_kind='real_road', source_frame_count=source['summary']['frame_count'],
                        default_frame_index=min(50, source['summary']['frame_count'] // 2))
                _saved(session, db, body.project_id, identity, definition, source, reconstruction)
                previous = _binding(session, body.project_id, identity)
                revision = previous['revision'] if previous else 0
                if previous and definition == previous['definition'] and body.expected_revision in (revision, revision - 1):
                    return previous
                if revision != body.expected_revision:
                    raise HTTPException(409, 'Neural Sim binding changed; reload before binding')
                value = dict(schema_version='neural-sim-preset-binding/1', project_id=body.project_id, kind=KIND,
                    id=identity, revision=revision + 1, definition=definition)
                raw = canonical_bytes(value)
                session.add(Row(project_id=body.project_id, kind=KIND, id=identity, revision=revision + 1,
                    manifest=raw, manifest_hash=hashlib.sha256(raw).hexdigest()))
                session.flush()
                result = _binding(session, body.project_id, identity)
                session.commit()
                return result
        except IntegrityError as exc:
            raise HTTPException(409, 'Concurrent Neural Sim binding; reload and retry') from exc

    @api.post('/neural-sim/presets/{identity}/preview', tags=['Neural Sim'],
              openapi_extra={'x-axiom-min-role': 'Engineer', 'x-axiom-read-only': True})
    def preview(identity: str, body: NeuralDemoPreview, request: Request):
        """Prepare a changed camera view for explicit normal Experiment submission."""
        _preset(identity)
        project_authorize(request, db, body.project_id, 'Engineer')
        with db.read_session() as session:
            binding = _binding(session, body.project_id, identity)
            if binding is None or binding['revision'] != body.expected_binding_revision:
                raise HTTPException(409, 'Neural Sim binding changed or is missing; reload before preview')
            definition = binding['definition']
            source, reconstruction = _bound_sources(session, db, body.project_id, definition)
            experiment = _definition(identity, body.project_id, definition, body.parameters, body.mode, body.frame_index)
        prepared = experiment_jobs.prepare(registry, experiment)
        payload = prepared.to_payload()
        if (payload['input'].get('source_pin') != execution_pin(source)
                or payload['input'].get('reconstruction_pin') != execution_pin(reconstruction)):
            raise HTTPException(409, 'Neural renderer snapshot differs from the bound scene')
        parameters = {key: experiment.input[key] for key in ('lateral_m', 'height_m')}
        return dict(schema_version='neural-sim-preset-preview/1', project_id=body.project_id, preset_id=identity,
            binding_revision=binding['revision'], binding_sha256=binding['sha256'], parameters=parameters,
            changes=[dict(key=item['key'], label=item['label'], unit='m', baseline=0, value=parameters[item['key']]) for item in PARAMETERS],
            experiment=experiment.model_dump(mode='json'), preview=dict(preview_hash=prepared.input_hash, prepared=payload,
                job_count=1, resource_id=payload['handler']['resource_id'],
                execution_fidelity=payload['input'].get('execution_fidelity', payload['handler']['fidelity']),
                requires_explicit_submit=True, evaluation_status='not_evaluated'))
