"""Actual registered detector inference on pinned Neural render triplets.

The output is model-output sensitivity, without transferred labels or an
accuracy/failure verdict. Execution uses the common Experiment queue.
"""
import hashlib
import json
import struct
import zipfile
from typing import Annotated, Literal

from fastapi import Header, HTTPException, Request
from pydantic import Field, model_validator
from sqlalchemy import select

from . import experiment_jobs, jobs
from .adapters import AdapterCancelled, AdapterCancelledBeforeDispatch
from .base_schemas import StrictModel
from .case_schemas import CaseFields, RecordingSelection
from .experiment_runtime import ExperimentExecution, ExperimentHandlerMetadata, ExperimentOutputFile
from .experiment_schemas import ExperimentCreate
from .inference_runtime import RuntimeMetadata, RuntimeRegistry
from .models import get_model, verified_model_path
from .neural_demo import execution_pin, resolve_neural_execution
from .perception_jobs import validate_prediction
from .scenario_v2_schemas import Identity
from .service import canonical_bytes, digest, uid, utcnow
from .team_access import project_authorize

ADAPTER = 'neural-onnx-sensitivity'
FIDELITY = 'neural_model_output_sensitivity'
RESULT = 'neural-perception-result.json'
VIEWS = ('original', 'baseline', 'novel')


class NeuralPerceptionInput(StrictModel):
    render_execution_id: Identity
    model_id: Identity
    model_revision: int = Field(strict=True, ge=1)
    frame_indices: list[Annotated[int, Field(strict=True, ge=0, le=199)]] = Field(
        default_factory=lambda: [0, 15, 30, 45, 60], min_length=3, max_length=5)
    score_threshold: float = Field(default=.35, strict=True, ge=0, le=1, allow_inf_nan=False)
    iou_threshold: float = Field(default=.45, strict=True, ge=0, le=1, allow_inf_nan=False)
    max_detections: int = Field(default=100, strict=True, ge=1, le=300)

    @model_validator(mode='after')
    def ordered_unique(self):
        if self.frame_indices != sorted(set(self.frame_indices)):
            raise ValueError('Select 3–5 distinct render frame indices in increasing order')
        return self


class NeuralPerceptionPreview(NeuralPerceptionInput):
    project_id: Identity = 'default'
    seed: int = Field(default=42, strict=True, ge=0, le=2147483647)


class NeuralPerceptionCase(CaseFields):
    project_id: Identity = 'default'
    frame_index: int = Field(strict=True, ge=0, le=199)
    selected_variant: Literal['original', 'baseline', 'novel'] = 'novel'


def _artifact(record, name):
    item = next((item for item in record['artifacts'] if item['name'] == name), None)
    if item is None:
        raise ValueError(f'Required artifact is absent: {name}')
    return item


def _json_artifact(record, name):
    item = _artifact(record, name)
    if item['size_bytes'] > 4 * 1024 ** 2:
        raise ValueError('Neural JSON exceeds its bounded manifest size')
    value = json.loads(item['path'].read_bytes())
    if not isinstance(value, dict):
        raise ValueError('Neural JSON must be an object')
    return value


def _render(db, project_id, execution_id, indices):
    render = resolve_neural_execution(db, project_id, execution_id, 'neural_render')
    if render['adapter_id'] != 'neural-splatfacto':
        raise ValueError('Sensitivity requires a Neural Splatfacto render')
    result = _json_artifact(render, 'neural-render-result.json')
    if result.get('schema_version') not in ('neural-render-result/1', 'neural-render-result/2'):
        raise ValueError('Unsupported Neural render result version')
    for key, value in render['summary'].items():
        if key not in ('schema_version', 'elapsed_seconds') and result.get(key) != value:
            raise ValueError('Neural render result differs from its sealed summary')
    archive = _artifact(render, 'neural-render-frames.zip')
    if result.get('frames_archive_sha256') != archive['sha256']:
        raise ValueError('Render archive identity differs from its result')
    width, height = result['width'], result['height']
    if type(width) is not int or type(height) is not int or not (32 <= width <= 4096 and 32 <= height <= 4096):
        raise ValueError('Unsupported Neural render image dimensions')
    frames = result['frames']
    if not isinstance(frames, list) or not 1 <= len(frames) <= 200:
        raise ValueError('Invalid Neural render frame denominator')
    by_index = {frame['frame_index']: frame for frame in frames}
    if len(by_index) != len(frames) or any(index not in by_index for index in indices):
        raise ValueError('Selected indices must identify existing distinct render frames')
    reconstruction = resolve_neural_execution(db, project_id, result['reconstruction_execution_id'], 'neural_reconstruction')
    source = resolve_neural_execution(db, project_id, result['source_execution_id'])
    if (render['snapshot'].get('source_pin') != execution_pin(source)
            or render['snapshot'].get('reconstruction_pin') != execution_pin(reconstruction)
            or reconstruction['snapshot'].get('source_pin') != execution_pin(source)):
        raise ValueError('Render, checkpoint and original source lineage differ')
    selected = [by_index[index] for index in indices]
    expected_names = {f'{view}/{frame["frame_index"]:06d}.png' for frame in frames for view in VIEWS}
    with zipfile.ZipFile(archive['path']) as zipped:
        members = zipped.infolist()
        if len(members) != len(expected_names) or {item.filename for item in members} != expected_names:
            raise ValueError('Render ZIP has missing, extra or duplicate frame members')
        for frame in selected:
            for view in VIEWS:
                _read_png(zipped, frame, view, width, height)
    return render, source, reconstruction, result, selected


def _read_png(zipped, frame, view, width, height):
    image = frame[view]
    if image.get('name') != f'{view}/{frame["frame_index"]:06d}.png':
        raise ValueError('Render PNG name differs from the selected frame')
    member = zipped.getinfo(image['name'])
    if (member.compress_type != zipfile.ZIP_STORED or member.flag_bits & 1
            or type(image['size_bytes']) is not int or not 33 <= image['size_bytes'] <= 4 * 1024 ** 2
            or member.file_size != image['size_bytes']):
        raise ValueError('Render PNG size or ZIP storage is unsupported')
    png = zipped.read(member)
    if (hashlib.sha256(png).hexdigest() != image['sha256']
            or png[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
            or struct.unpack('>II', png[16:24]) != (width, height) or png[24:26] != b'\x08\x02'):
        raise ValueError('Render PNG bytes, RGB format or dimensions differ from its pinned descriptor')
    return png


class NeuralPerceptionHandler:
    def __init__(self, database, runtimes=None):
        self.db, self.runtimes = database, runtimes or RuntimeRegistry()

    def metadata(self):
        runtime = self.runtimes.get('onnx-cpu')
        available = runtime is not None and runtime.metadata().status == 'available'
        return ExperimentHandlerMetadata(id=ADAPTER, version='1.0.0', kinds=['neural_perception'],
            status='available' if available else 'not_configured', fidelity=FIDELITY,
            cancellation_supported=True, description='Actual registered ONNX detector on original/reconstructed/novel PNGs; model-output sensitivity without ground truth.')

    def prepare(self, spec):
        if (spec.kind != 'neural_perception' or spec.adapter_id != ADAPTER or spec.comparison_kind != 'single'
                or any(value is not None for value in (spec.model, spec.sensor, spec.evaluator))):
            raise ValueError('Sensitivity accepts one pinned render and one registered detector')
        inputs = NeuralPerceptionInput.model_validate(spec.input)
        render, source, reconstruction, result, frames = _render(self.db, spec.project_id,
            inputs.render_execution_id, inputs.frame_indices)
        with self.db.read_session() as session:
            model = get_model(session, inputs.model_id, inputs.model_revision).model_dump(mode='json')
        verified_model_path(self.db, model['artifact_id'])
        runtime = RuntimeMetadata.model_validate(self.runtimes.get('onnx-cpu').metadata()).model_dump(mode='json')
        if runtime['status'] != 'available' or model['profile'] not in runtime['profiles']:
            raise ValueError('Registered detector profile is unsupported by the available ONNX CPU runtime')
        return dict(schema_version='neural-perception-input/1', project_id=spec.project_id,
            request=inputs.model_dump(mode='json') | {'seed': spec.seed}, model_snapshot=model, runtime_snapshot=runtime,
            render_pin=execution_pin(render), source_pin=execution_pin(source), reconstruction_pin=execution_pin(reconstruction),
            width=result['width'], height=result['height'], frames=frames,
            source_kind=source['summary'].get('source_kind', 'synthetic_carla'),
            source_use_scope=source['summary'].get('source_use_scope', {}),
            license_scope=source['summary'].get('license_scope', 'not_declared'),
            external_publication_cleared=source['summary'].get('external_publication_cleared', False),
            case_supported=source['kind'] == 'neural_source_import' and bool(source['summary'].get('recording_id')),
            scope='model_output_sensitivity', ground_truth_used=False, accuracy_evaluated=False)

    def execute(self, snapshot, work_dir, should_cancel):
        if should_cancel():
            raise AdapterCancelledBeforeDispatch()
        request = snapshot['request']
        inputs = {key: value for key, value in request.items() if key != 'seed'}
        fresh = self.prepare(ExperimentCreate(project_id=snapshot['project_id'], name='Pinned sensitivity',
            kind='neural_perception', adapter_id=ADAPTER, adapter_version='1.0.0', input=inputs, seed=request['seed']))
        if fresh != snapshot:
            raise ValueError('Sensitivity source, model or runtime changed after preview')
        model = snapshot['model_snapshot']
        render = resolve_neural_execution(self.db, snapshot['project_id'], request['render_execution_id'], 'neural_render')
        runtime = self.runtimes.get('onnx-cpu')
        width, height = snapshot['width'], snapshot['height']
        frames = []
        with zipfile.ZipFile(_artifact(render, 'neural-render-frames.zip')['path']) as zipped, runtime.open(
                verified_model_path(self.db, model['artifact_id']), model, model['artifact']['sha256'], request['seed'], should_cancel) as engine:
            for selected in snapshot['frames']:
                frame = {key: value for key, value in selected.items() if key not in VIEWS}
                frame.setdefault('source_frame_index', selected['frame_index'])
                frame.setdefault('source_time_s', selected['time_s'])
                frame.setdefault('applied_lateral_m', render['summary']['lateral_m'])
                frame.setdefault('applied_height_m', render['summary']['height_m'])
                for view in VIEWS:
                    if should_cancel():
                        raise AdapterCancelled()
                    png = _read_png(zipped, selected, view, width, height)
                    prediction = engine.infer(png, width=width, height=height, score_threshold=request['score_threshold'],
                        iou_threshold=request['iou_threshold'], max_detections=request['max_detections'], should_cancel=should_cancel)
                    prediction = validate_prediction(prediction, snapshot, png, width, height)
                    frame[view] = dict(image=selected[view] | {'width': width, 'height': height},
                        detections=[item.model_dump(mode='json') for item in prediction.detections],
                        runtime=prediction.evidence.model_dump(mode='json'))
                counts = {view: len(frame[view]['detections']) for view in VIEWS}
                frame['summary'] = dict(original_count=counts['original'], baseline_count=counts['baseline'], novel_count=counts['novel'],
                    baseline_count_delta=counts['baseline'] - counts['original'], novel_count_delta=counts['novel'] - counts['baseline'],
                    novel_vs_original_count_delta=counts['novel'] - counts['original'])
                frames.append(frame)
        result = {key: value for key, value in snapshot.items() if key not in ('frames', 'schema_version')}
        result.update(schema_version='neural-perception-result/1', frames=frames,
            limitations=['Prediction changes may come from reconstruction artifacts, visibility or perspective changes.',
                        'No transferred ground truth, accuracy, safety or model failure verdict is produced.'])
        path = work_dir / RESULT
        path.write_bytes(canonical_bytes(result))
        summary = dict(schema_version='neural-perception-summary/1', scope='model_output_sensitivity',
            render_execution_id=request['render_execution_id'], source_execution_id=snapshot['source_pin']['execution_id'],
            reconstruction_execution_id=snapshot['reconstruction_pin']['execution_id'], model_id=model['id'], model_revision=model['revision'],
            model_manifest_hash=model['manifest_hash'], model_sha256=model['artifact']['sha256'],
            frame_indices=request['frame_indices'], frame_count=len(frames), image_count=len(frames) * 3,
            result_artifact=RESULT, result_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            ground_truth_used=False, accuracy_evaluated=False)
        return ExperimentExecution(summary=summary, artifacts=[ExperimentOutputFile(path, RESULT, 'application/json')], fidelity=FIDELITY)


def read_result(db, project_id, execution_id):
    execution = resolve_neural_execution(db, project_id, execution_id, 'neural_perception')
    result = _json_artifact(execution, RESULT)
    if (execution['adapter_id'] != ADAPTER or execution['fidelity'] != FIDELITY
            or result.get('schema_version') != 'neural-perception-result/1'
            or _artifact(execution, RESULT)['sha256'] != execution['summary'].get('result_sha256')
            or any(result.get(key) != value for key, value in execution['snapshot'].items() if key not in ('frames', 'schema_version'))):
        raise HTTPException(409, 'Sensitivity result differs from its exact execution input')
    return result | {'execution_id': execution_id, 'execution_pin': execution_pin(execution)}


def register_neural_perception_routes(api, db, registry):
    @api.post('/neural-sim/perception/preview', tags=['Neural Sim'],
              openapi_extra={'x-axiom-min-role': 'Engineer', 'x-axiom-read-only': True})
    def preview(body: NeuralPerceptionPreview, request: Request):
        project_authorize(request, db, body.project_id, 'Engineer')
        experiment = ExperimentCreate(project_id=body.project_id, name='Neural Sim · model-output sensitivity',
            kind='neural_perception', adapter_id=ADAPTER, adapter_version='1.0.0', seed=body.seed,
            input=body.model_dump(mode='json', exclude={'project_id', 'seed'}))
        prepared = experiment_jobs.prepare(registry, experiment)
        value = prepared.to_payload()
        return dict(schema_version='neural-sim-perception-preview/1', experiment=experiment.model_dump(mode='json'),
            preview=dict(preview_hash=prepared.input_hash, prepared=value, job_count=1,
                resource_id=value['handler']['resource_id'], execution_fidelity=FIDELITY,
                requires_explicit_submit=True, evaluation_status='not_evaluated'))

    @api.get('/neural-sim/perception/{execution_id}', tags=['Neural Sim'])
    def result(execution_id: str, request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        return read_result(db, project_id, execution_id)

    @api.post('/neural-sim/perception/{execution_id}/case', status_code=201, tags=['Neural Sim'],
              openapi_extra={'x-axiom-min-role': 'Engineer'})
    def capture(execution_id: str, body: NeuralPerceptionCase, request: Request,
                idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        project_authorize(request, db, body.project_id, 'Engineer')
        return capture_case(db, request, execution_id, body, idempotency_key)


def capture_case(db, request, execution_id, body, key):
    from .cases import _get, _make_segment, _publish
    from .case_models import CaseRow
    from .project_recordings import authorize_recording_source
    result = read_result(db, body.project_id, execution_id)
    if not result.get('case_supported'):
        raise HTTPException(422, 'Case capture requires a real-road source with its original recording')
    frame = next((frame for frame in result['frames'] if frame['frame_index'] == body.frame_index), None)
    if frame is None:
        raise HTTPException(422, 'Case frame must be one of the completed sensitivity variants')
    source = resolve_neural_execution(db, body.project_id, result['source_pin']['execution_id'])
    source_manifest = _json_artifact(source, 'neural-real-road-source.json')
    context = source_manifest.get('recording_context', source_manifest)
    if not all(key in context for key in ('recording_id', 'channel_id', 'start_time_ns', 'end_time_ns')):
        raise HTTPException(422, 'A real-road original recording must be imported and pinned before saving a Case')
    authority = authorize_recording_source(request, db, body.project_id, context['recording_id'])
    if context.get('recording_sha256') != authority['recording_sha256']:
        raise HTTPException(409, 'Case recording differs from the pinned Neural source')
    mappings = source_manifest.get('frames', [])
    mapping = next((item for item in mappings if item.get('source_frame_index', item.get('frame_index')) == frame['source_frame_index']), None)
    if mapping is None or 'log_time_ns' not in mapping:
        raise HTTPException(422, 'The selected Neural frame has no exact original recording timestamp')
    selection = RecordingSelection(recording_id=context['recording_id'], channel_id=context['channel_id'],
        start_time_ns=mapping['log_time_ns'], end_time_ns=mapping['log_time_ns'])
    request_hash = digest({'execution_id': execution_id, **body.model_dump(mode='json')})
    creation_key = experiment_jobs._key(body.project_id, key, 'neural-case')
    with jobs._write(db) as session:
        existing = session.scalar(select(CaseRow).where(CaseRow.creation_key == creation_key))
        if existing:
            if existing.request_hash != request_hash:
                raise HTTPException(409, 'Idempotency-Key already identifies another Neural Case')
            return _get(session, existing.id, 1)
        segment, segment_hash, links = _make_segment(session, db, selection)
        if (mapping.get('recording_ordinal') is None
                or not any(item['ordinal'] == mapping['recording_ordinal'] for item in segment['samples'])):
            raise HTTPException(409, 'Neural source ordinal does not belong to the pinned recording timestamp')
        link = dict(schema_version='neural-sensitivity-case-link/1', project_id=body.project_id,
            sensitivity_pin=result['execution_pin'], render_pin=result['render_pin'], source_pin=result['source_pin'],
            reconstruction_pin=result['reconstruction_pin'], model_manifest_hash=result['model_snapshot']['manifest_hash'],
            model_id=result['model_snapshot']['id'], model_revision=result['model_snapshot']['revision'],
            frame_index=body.frame_index, source_frame_index=frame['source_frame_index'], source_time_s=frame['source_time_s'],
            selected_variant=body.selected_variant, image=frame[body.selected_variant]['image'],
            recording_ordinal=mapping['recording_ordinal'], log_time_ns=mapping['log_time_ns'],
            summary=frame['summary'], scope='model_output_sensitivity', ground_truth_used=False, accuracy_evaluated=False)
        value = dict(schema_version='axiom-investigation-case/1', id=uid('case'), revision=1, created_at=utcnow(),
            title=body.title, note=body.note, assignee=body.assignee, state='open', disposition_note='',
            segment=segment, segment_hash=segment_hash, evaluations=links, previous_manifest_hash=None,
            workflow_state_is_test_verdict=False, assignee_is_authenticated_identity=False,
            neural_sensitivity=link)
        head = CaseRow(id=value['id'], revision=1, segment_id=segment['id'], title=body.title, state='open',
            created_at=value['created_at'], creation_key=creation_key, request_hash=request_hash)
        session.add(head)
        session.flush()
        return _publish(session, head, value, 'create:' + head.id, request_hash)


def validate_case_link(session, case):
    """Validate optional versioned context while preserving ordinary Case revisions."""
    link = case.get('neural_sensitivity')
    if link is None:
        return
    from .neural_demo import _resolve
    try:
        execution = _resolve(session, None, link['project_id'], link['sensitivity_pin']['execution_id'], 'neural_perception')
        snapshot = execution['snapshot']
        source = _resolve(session, None, link['project_id'], link['source_pin']['execution_id'], 'neural_source_import')
        selected = next(frame for frame in snapshot['frames'] if frame['frame_index'] == link['frame_index'])
        expected_image = selected[link['selected_variant']] | {'width': snapshot['width'], 'height': snapshot['height']}
        original_mapping = next(frame for frame in source['summary']['frames']
            if frame.get('source_frame_index', frame.get('frame_index')) == link['source_frame_index'])
        if (link['schema_version'] != 'neural-sensitivity-case-link/1' or link['scope'] != 'model_output_sensitivity'
                or link['ground_truth_used'] is not False or link['accuracy_evaluated'] is not False
                or link['sensitivity_pin'] != execution_pin(execution) or link['source_pin'] != execution_pin(source)
                or any(link[key] != snapshot[key] for key in ('render_pin', 'source_pin', 'reconstruction_pin'))
                or link['model_manifest_hash'] != snapshot['model_snapshot']['manifest_hash']
                or link['model_id'] != snapshot['model_snapshot']['id'] or link['model_revision'] != snapshot['model_snapshot']['revision']
                or link['source_frame_index'] != selected.get('source_frame_index', selected['frame_index'])
                or link['source_time_s'] != selected.get('source_time_s', selected['time_s'])
                or link['recording_ordinal'] != original_mapping['recording_ordinal'] or link['log_time_ns'] != original_mapping['log_time_ns']
                or link['image'] != expected_image or case['segment']['recording_id'] != source['summary']['recording_id']
                or case['segment']['recording_sha256'] != source['summary']['recording_sha256']
                or not any(item['ordinal'] == link['recording_ordinal'] and item['log_time_ns'] == link['log_time_ns']
                    for item in case['segment']['samples'])):
            raise ValueError('Case source or result relationship differs')
    except (KeyError, ValueError, TypeError, StopIteration, HTTPException) as exc:
        raise HTTPException(409, 'Neural sensitivity Case link failed its pinned evidence check') from exc
