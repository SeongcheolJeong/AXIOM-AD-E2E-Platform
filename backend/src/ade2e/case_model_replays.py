"""Case → exact recorded images → registered model → ordinary evaluation queue.

Explicit user transitions, not a second scheduler. Replay uses the WHOLE pinned
baseline split; partial inference never borrows predictions from the baseline.
No sensor perturbation, training, closed-loop policy or scene reconstruction.
"""
from copy import deepcopy
from urllib.parse import urlencode

from fastapi import Header, HTTPException, Query, Response
from pydantic import Field, field_validator
from sqlalchemy import select

from . import jobs, perception_jobs, detection_evaluations
from .base_schemas import StrictModel
from .case_models import CaseRow, CaseRevisionRow
from .case_schemas import Identity
from .cases import _get, _publish, _sealed, _evaluation, _link_matches, MAX_EVALUATIONS
from .datasets import get_dataset
from .detection_evaluation_schemas import DetectionEvaluationCreate
from .evaluation_reviews import build_review
from .models import get_model
from .perception_schemas import PerceptionJobCreate
from .recording_models import RecordingMessageRow
from .recordings import _get_recording, read_message_bytes
from .service import canonical_bytes, digest, uid, utcnow

VERSION = 'case-model-replay/1'
MAX_REPLAYS = 8
MAX_IMAGES = 64
MAX_BYTES = 64 * 1024 * 1024
SCOPE = 'Full baseline split, original recorded camera bytes; not training, closed-loop driving or changed sensor conditions.'


class ReplaySelection(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    baseline_evaluation_id: Identity
    model_id: Identity
    model_revision: int = Field(ge=1, strict=True)
    note: str = Field(min_length=1, max_length=1500)

    @field_validator('note')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Record the purpose of this model replay')
        return value.strip()


class ReplayCreate(ReplaySelection):
    expected_preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirm_trusted_model: bool = Field(strict=True)

    @field_validator('confirm_trusted_model')
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError('Explicitly acknowledge trusted model execution and the full split workload')
        return value


class ReplayAction(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    plan_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


def _baseline(session, identifier):
    row = jobs._row(session, identifier)
    if row.kind != 'evaluation':
        raise HTTPException(422, 'Baseline must be a detection evaluation, not another job kind')
    return detection_evaluations.detail(session, row)


def _current(session, case_id, revision):
    case = _get(session, case_id)
    if case['revision'] != revision:
        raise HTTPException(409, 'Case changed; reload and review the new source revision')
    return case


def _new(prior):
    value = deepcopy({k: v for k, v in prior.items() if k not in ('manifest_hash', 'manifest_size_bytes', 'download_url')})
    value.update(schema_version='axiom-investigation-case/5' if prior.get('stack_replays') else 'axiom-investigation-case/4', revision=prior['revision'] + 1,
                 created_at=utcnow(), previous_manifest_hash=prior['manifest_hash'])
    value.setdefault('model_replays', [])
    return value


def _retry(session, case_id, operation, body, key):
    storage = 'model-replay:' + digest({'case_id': case_id, 'operation': operation, 'key': key})
    request_hash = digest({'case_id': case_id, 'operation': operation, 'body': body.model_dump(mode='json')})
    old = session.scalar(select(CaseRevisionRow).where(CaseRevisionRow.request_key == storage))
    if old and (old.case_id != case_id or old.request_hash != request_hash):
        raise HTTPException(409, 'Idempotency-Key identifies a different model replay request')
    return (_get(session, case_id, old.revision) if old else None), storage, request_hash


def _proposal(db, session, runtimes, evaluators, case, body):
    baseline = _baseline(session, body.baseline_evaluation_id)
    if not any(e['evaluation_id'] == baseline.id and e['manifest_hash'] == baseline.manifest_hash for e in case['evaluations']):
        raise HTTPException(422, 'Select a sealed baseline evaluation already linked to this case')
    if (baseline.status != 'completed' or not baseline.manifest_hash or baseline.report is None
            or not baseline.report.coverage.complete or baseline.report.metrics is None):
        raise HTTPException(422, 'The baseline must have complete reviewed coverage; do not silently evaluate a subset')
    dataset = get_dataset(session, baseline.dataset_id, baseline.dataset_revision)
    if dataset.manifest_hash != baseline.dataset_snapshot['manifest_hash']:
        raise HTTPException(409, 'Baseline dataset identity changed')
    selected = [m.image_id for m in dataset.images if m.split == baseline.request.split]
    assets = {a.id: a for a in dataset.image_assets}
    inputs = [assets[i].input.model_dump(mode='json') for i in selected]
    if not 1 <= len(inputs) <= MAX_IMAGES:
        raise HTTPException(413, 'This replay supports a complete split of 1–64 images; no truncation is performed')
    identities = {(x['recording_id'], x['channel_id'], x['decoder_id'], x['decoder_version']) for x in inputs}
    if len(identities) != 1 or len({x['ordinal'] for x in inputs}) != len(inputs):
        raise HTTPException(422, 'First model replay requires one camera channel/decoder and unique original inputs')
    model = get_model(session, body.model_id, body.model_revision)
    original_model = baseline.source_jobs[0]['model_snapshot']
    if model.profile != original_model['profile'] or set(model.labels) != set(original_model['labels']):
        raise HTTPException(422, 'Candidate must retain the baseline profile and label vocabulary; mapping is not guessed')
    config = baseline.source_context['inference_configuration']
    runtime, evaluator = runtimes.get(config['runtime_id']), evaluators.get(baseline.evaluator_id)
    if runtime is None or evaluator is None:
        raise HTTPException(422, 'Pinned runtime or evaluator is not installed')
    runtime_meta, evaluator_meta = runtime.metadata().model_dump(mode='json'), evaluator.metadata().model_dump(mode='json')
    if (runtime_meta['status'] != 'available' or runtime_meta['version'] != config['runtime_version']
            or model.profile not in runtime_meta['profiles'] or evaluator_meta != baseline.evaluator_snapshot):
        raise HTTPException(422, 'Runtime/evaluator availability or pinned policy changed')
    case_images = []
    for identifier in selected:
        asset = assets[identifier]
        try:
            _link_matches(case['segment'], {'input': asset.input.model_dump(mode='json')})
            case_images.append(identifier)
        except HTTPException as error:
            if error.status_code != 422:
                raise
    if not case_images or len(case['evaluations']) + len(case_images) > MAX_EVALUATIONS:
        raise HTTPException(413, 'Case has no matching images or insufficient capacity for the new evaluation links')
    recording_id, channel_id, decoder_id, decoder_version = next(iter(identities))
    recording = _get_recording(session, recording_id)
    total = 0
    for source in inputs:
        entry = session.get(RecordingMessageRow, (recording_id, source['ordinal']))
        if (entry is None or entry.channel_id != channel_id or entry.sha256 != source['message_sha256']
                or recording.sha256 != source['recording_sha256']
                or str(int(entry.log_time_key)) != source['log_time_ns']
                or str(int(entry.publish_time_key)) != source['publish_time_ns']
                or entry.payload_length != source['message_size_bytes']):
            raise HTTPException(409, 'Original message identity differs from the baseline')
        total += entry.payload_length
        if total > MAX_BYTES:
            raise HTTPException(413, 'Selected original payload exceeds 64 MiB; no partial replay is submitted')
        read_message_bytes(db, recording, entry)
    inference = PerceptionJobCreate(label='Case model replay', model_id=model.id, model_revision=model.revision,
        runtime_id=config['runtime_id'], recording_id=recording_id, channel_id=channel_id,
        ordinals=[x['ordinal'] for x in inputs], decoder_id=decoder_id, decoder_version=decoder_version,
        **{k: config[k] for k in ('seed', 'score_threshold', 'iou_threshold', 'max_detections')})
    evaluation = baseline.request.model_dump(mode='json')
    evaluation.pop('inference_job_ids')
    evaluation.update(name='Case model replay evaluation', note=body.note)
    return {'schema_version': VERSION, 'source': {'case_id': case['id'], 'case_revision': case['revision'],
            'case_manifest_hash': case['manifest_hash'], 'segment_hash': case['segment_hash']},
        'baseline_id': baseline.id, 'baseline_manifest_hash': baseline.manifest_hash,
        'dataset_manifest_hash': dataset.manifest_hash, 'split': baseline.request.split,
        'image_ids': selected, 'case_image_ids': case_images, 'inputs': inputs,
        'model': model.model_dump(mode='json'), 'runtime': runtime_meta, 'evaluator': evaluator_meta,
        'inference_request': inference.model_dump(mode='json'), 'evaluation_request': evaluation,
        'source_conditions': case.get('conditions'), 'scope': SCOPE,
        'workload': {'full_split_images': len(inputs), 'case_images': len(case_images), 'source_bytes': total},
        'note': body.note, 'automatic_case_resolution': False}


def preview(db, runtimes, evaluators, case_id, body):
    with db.read_session() as session:
        proposal = _proposal(db, session, runtimes, evaluators, _current(session, case_id, body.expected_revision), body)
        return {**proposal, 'preview_hash': digest(proposal), 'jobs_created': 0}


def submit(db, runtimes, decoders, evaluators, case_id, body, key):
    with jobs._write(db) as session:
        old, storage, request_hash = _retry(session, case_id, 'submit', body, key)
        if old:
            return old
        prior = _current(session, case_id, body.expected_revision)
        if len(prior.get('model_replays', [])) >= MAX_REPLAYS:
            raise HTTPException(413, 'Case supports at most eight model replay attempts')
        proposal = _proposal(db, session, runtimes, evaluators, prior, body)
        if digest(proposal) != body.expected_preview_hash:
            raise HTTPException(409, 'Preview changed; inspect the full workload again before execution')
        task = perception_jobs.submit_in_session(db, session, runtimes, decoders,
            PerceptionJobCreate.model_validate(proposal['inference_request']), storage)
        if task.model_snapshot != proposal['model'] or jobs._row(session, task.id).prepared['runtime_snapshot'] != proposal['runtime']:
            raise HTTPException(409, 'Model/runtime changed during queue publication')
        value = _new(prior)
        value['model_replays'].append({'id': uid('modelreplay'), 'definition': proposal,
            'definition_hash': digest(proposal), 'inference': {'job_id': task.id, 'input_hash': task.input_hash},
            'evaluation': None, 'capture': None})
        return _publish(session, session.get(CaseRow, case_id), value, storage, request_hash)


def _plan(case, identifier, expected=None):
    plan = next((p for p in case.get('model_replays', []) if p['id'] == identifier), None)
    if plan is None:
        raise HTTPException(404, 'Model replay is not in this case revision')
    if expected is not None and plan['definition_hash'] != expected:
        raise HTTPException(409, 'Model replay plan hash changed')
    return plan


def evaluate(db, evaluators, case_id, identifier, body, key):
    with jobs._write(db) as session:
        old, storage, request_hash = _retry(session, case_id, 'evaluate:' + identifier, body, key)
        if old:
            return old
        prior = _current(session, case_id, body.expected_revision)
        value = _new(prior); plan = _plan(value, identifier, body.plan_hash)
        if plan['evaluation'] is not None:
            raise HTTPException(409, 'This inference already has a linked evaluation; no duplicate is queued')
        source = perception_jobs.detail(session, jobs._row(session, plan['inference']['job_id']))
        if source.status not in jobs.TERMINAL:
            raise HTTPException(409, 'Wait for inference termination before evaluating stored predictions')
        definition = plan['definition']
        evaluator = evaluators.get(definition['evaluation_request']['evaluator_id'])
        if evaluator is None or evaluator.metadata().model_dump(mode='json') != definition['evaluator']:
            raise HTTPException(409, 'Evaluator no longer matches the reviewed plan')
        task = detection_evaluations.submit_in_session(db, session, evaluators,
            DetectionEvaluationCreate(**definition['evaluation_request'], inference_job_ids=[source.id]), storage)
        if task.dataset_snapshot['manifest_hash'] != definition['dataset_manifest_hash']:
            raise HTTPException(409, 'Dataset identity changed after inference')
        plan['evaluation'] = {'job_id': task.id, 'input_hash': task.input_hash, 'inference_manifest_hash': source.manifest_hash}
        return _publish(session, session.get(CaseRow, case_id), value, storage, request_hash)


def capture(db, case_id, identifier, body, key):
    with jobs._write(db) as session:
        old, storage, request_hash = _retry(session, case_id, 'capture:' + identifier, body, key)
        if old:
            return old
        prior = _current(session, case_id, body.expected_revision)
        value = _new(prior); plan = _plan(value, identifier, body.plan_hash)
        if plan['capture'] is not None or plan['evaluation'] is None:
            raise HTTPException(409, 'Capture requires an uncaptured evaluation')
        candidate = _baseline(session, plan['evaluation']['job_id'])
        if candidate.status not in jobs.TERMINAL or not candidate.manifest_hash:
            raise HTTPException(409, 'Wait for sealed terminal evaluation evidence')
        baseline = _baseline(session, plan['definition']['baseline_id'])
        review = build_review(baseline, candidate)
        for image_id in plan['definition']['case_image_ids']:
            from .case_schemas import EvaluationSelection
            link, _ = _evaluation(session, EvaluationSelection(evaluation_id=candidate.id, image_id=image_id))
            _link_matches(value['segment'], link)
            if not any(e['evaluation_id'] == candidate.id and e['image_id'] == image_id for e in value['evaluations']):
                value['evaluations'].append(link)
        if len(value['evaluations']) > MAX_EVALUATIONS:
            raise HTTPException(413, 'Case evaluation link capacity exceeded; evidence remains in the evaluation job')
        plan['capture'] = {'evaluation_manifest_hash': candidate.manifest_hash, 'comparison_hash': digest(review),
            'comparison_status': review['status'], 'captured_at': utcnow()}
        return _publish(session, session.get(CaseRow, case_id), value, storage, request_hash)


def validate_links(session, case):
    plans = case.get('model_replays', [])
    if not plans:
        return
    try:
        if (case['schema_version'] not in ('axiom-investigation-case/4', 'axiom-investigation-case/5') or not isinstance(plans, list)
                or len(plans) > MAX_REPLAYS or len({p['id'] for p in plans}) != len(plans)):
            raise ValueError('Invalid model replay list')
        for plan in plans:
            d = plan['definition']; source = d['source']
            original = session.get(CaseRevisionRow, (case['id'], source['case_revision']))
            if (digest(d) != plan['definition_hash'] or d['schema_version'] != VERSION or d['scope'] != SCOPE
                    or source['case_id'] != case['id'] or source['segment_hash'] != case['segment_hash']
                    or not 1 <= source['case_revision'] < case['revision'] or original is None
                    or original.manifest_hash != source['case_manifest_hash']):
                raise ValueError('Source/plan pin differs')
            old = _sealed(original)
            baseline = _baseline(session, d['baseline_id'])
            if (d['source_conditions'] != old.get('conditions') or baseline.manifest_hash != d['baseline_manifest_hash']
                    or not any(e['evaluation_id'] == baseline.id and e['manifest_hash'] == baseline.manifest_hash for e in old['evaluations'])):
                raise ValueError('Baseline or source condition differs')
            job = jobs._row(session, plan['inference']['job_id'])
            task = perception_jobs.detail(session, job)
            if (task.input_hash != plan['inference']['input_hash'] or task.request.model_dump(mode='json') != d['inference_request']
                    or task.model_snapshot != d['model'] or job.prepared['runtime_snapshot'] != d['runtime']
                    or [{k: v for k, v in i.model_dump(mode='json').items() if k != 'selection_index'} for i in task.inputs]
                    != [{k: v for k, v in i.items() if k != 'selection_index'} for i in d['inputs']]):
                raise ValueError('Inference identity differs')
            if plan['evaluation']:
                evaluation = _baseline(session, plan['evaluation']['job_id'])
                expected = {**d['evaluation_request'], 'inference_job_ids': [task.id]}
                if (evaluation.request.model_dump(mode='json') != expected
                        or evaluation.input_hash != plan['evaluation']['input_hash']
                        or evaluation.dataset_snapshot['manifest_hash'] != d['dataset_manifest_hash']
                        or evaluation.evaluator_snapshot != d['evaluator']
                        or task.manifest_hash != plan['evaluation']['inference_manifest_hash']):
                    raise ValueError('Evaluation input differs')
                if plan['capture']:
                    review = build_review(baseline, evaluation)
                    if (evaluation.manifest_hash != plan['capture']['evaluation_manifest_hash']
                            or digest(review) != plan['capture']['comparison_hash']
                            or review['status'] != plan['capture']['comparison_status']):
                        raise ValueError('Captured evidence differs')
            elif plan['capture']:
                raise ValueError('Capture has no evaluation')
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(409, 'Model replay source or execution linkage failed its integrity check') from error


def report(session, case_id, revision=None):
    case = _get(session, case_id, revision)
    plans = []
    for plan in case.get('model_replays', []):
        task = perception_jobs.detail(session, jobs._row(session, plan['inference']['job_id']))
        evaluation = _baseline(session, plan['evaluation']['job_id']) if plan['evaluation'] else None
        comparison = build_review(_baseline(session, plan['definition']['baseline_id']), evaluation) if evaluation and evaluation.manifest_hash else None
        plans.append({**plan, 'inference_status': task.status, 'inference_counts': task.counts.model_dump(mode='json'),
            'inference_manifest_hash': task.manifest_hash, 'inference_error': task.error,
            'evaluation_status': evaluation.status if evaluation else None,
            'evaluation_error': evaluation.error if evaluation else None, 'comparison': comparison})
    return {'schema_version': 'case-model-replay-report/1', 'case_id': case_id, 'case_revision': case['revision'],
        'case_manifest_hash': case['manifest_hash'], 'scope': SCOPE, 'plans': plans,
        'limitations': ['Independent recorded-image object detection only; no temporal policy or closed-loop control.',
            'The full baseline split is replayed, not just the selected case. Original labels/mapping/thresholds stay fixed.',
            'Failed or cancelled inference is not retried; evaluation preserves missing/failed predictions.',
            'Captured candidate evidence does not erase baseline failures or resolve the case.',
            'Model files must be trusted; a subprocess is not a security sandbox.']}


def register_model_replay_routes(api, db, runtimes, decoders, evaluators):
    @api.post('/cases/{case_id}/model-replays/preview', tags=['case model replay'])
    def preview_route(case_id: str, body: ReplaySelection):
        return preview(db, runtimes, evaluators, case_id, body)

    @api.post('/cases/{case_id}/model-replays', status_code=202, tags=['case model replay'])
    def submit_route(case_id: str, body: ReplayCreate, idempotency_key: str = Header(min_length=1, max_length=180)):
        return submit(db, runtimes, decoders, evaluators, case_id, body, idempotency_key)

    @api.post('/cases/{case_id}/model-replays/{plan_id}/evaluate', status_code=202, tags=['case model replay'])
    def evaluate_route(case_id: str, plan_id: str, body: ReplayAction, idempotency_key: str = Header(min_length=1, max_length=180)):
        return evaluate(db, evaluators, case_id, plan_id, body, idempotency_key)

    @api.post('/cases/{case_id}/model-replays/{plan_id}/capture', status_code=201, tags=['case model replay'])
    def capture_route(case_id: str, plan_id: str, body: ReplayAction, idempotency_key: str = Header(min_length=1, max_length=180)):
        return capture(db, case_id, plan_id, body, idempotency_key)

    @api.get('/cases/{case_id}/model-replays', tags=['case model replay'])
    def report_route(case_id: str, revision: int | None = Query(None, ge=1)):
        with db.read_session() as session:
            data = report(session, case_id, revision); raw = canonical_bytes(data)
            if len(raw) > 16 * 1024 * 1024:
                raise HTTPException(413, 'Model replay report exceeds 16 MiB')
            query = urlencode({'revision': data['case_revision'], 'expected_hash': digest(data)})
            return {**data, 'manifest_hash': digest(data), 'manifest_size_bytes': len(raw),
                'download_url': f'/api/v1/cases/{case_id}/model-replays/export?{query}'}

    @api.get('/cases/{case_id}/model-replays/export', tags=['case model replay'])
    def export_route(case_id: str, revision: int = Query(..., ge=1), expected_hash: str = Query(..., pattern=r'^[a-f0-9]{64}$')):
        with db.read_session() as session:
            data = report(session, case_id, revision); raw = canonical_bytes(data)
            if len(raw) > 16 * 1024 * 1024:
                raise HTTPException(413, 'Model replay report exceeds 16 MiB')
            if digest(data) != expected_hash:
                raise HTTPException(409, 'Execution state changed; refresh the model replay report')
            return Response(raw, media_type='application/json', headers={'X-Content-Type-Options': 'nosniff',
                'Content-Disposition': f'attachment; filename="model-replay-{expected_hash[:12]}.json"'})
