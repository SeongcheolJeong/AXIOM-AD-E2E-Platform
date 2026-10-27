"""Dataset → ordinary inference jobs → unreviewed model-assisted labels.

Preserve every dataset member. Only empty, unreviewed members are targets. The
model, policy, original inputs, all failures and raw scored predictions remain
separate from labels. Publishing never declares a human-reviewed negative.
"""
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from urllib.parse import urlencode

from fastapi import Header, HTTPException, Query, Response
from sqlalchemy import select

from . import jobs, perception_jobs
from .annotation_models import AnnotationTaskRow, AnnotationPublicationRow
from .annotation_schemas import AnnotationSelection, AnnotationCreate, AnnotationPublish
from .case_schemas import Identity
from .dataset_models import DatasetRow
from .dataset_schemas import DatasetCreate, DatasetMember
from .datasets import get_dataset, dataset_image_bytes, _insert_revision, _source
from .inference_runtime import RuntimeMetadata
from .models import get_model
from .perception_schemas import PerceptionJobCreate
from .recordings import read_message_bytes
from .service import canonical_bytes, digest, uid, utcnow

VERSION = 'annotation-assist/1'
MAX_BYTES = 64 * 1024 * 1024
MAX_JOBS = 16
MAX_MANIFEST = 16 * 1024 * 1024
LIMITATIONS = [
    'Only empty unreviewed images are selected; existing boxes and reviewed labels are never overwritten.',
    'Predictions, including zero detections, are unreviewed suggestions, NOT ground truth or reviewed negatives.',
    'Model scores are not calibrated probabilities. Model, decoder and policy versions are pinned.',
    'Missing, failed, cancelled or changed inputs are retained. No predictions are borrowed from another model.',
    'Review is a user declaration, not an authenticated independent audit. No accuracy or coverage certification.',
    'Known model-origin lineage survives ordinary revisions and collection label copies; arbitrary external copies cannot be traced.',
]


def _seal(value):
    raw = canonical_bytes(value)
    if len(raw) > MAX_MANIFEST:
        raise HTTPException(413, 'Annotation evidence exceeds 16 MiB')
    return dict(manifest=raw, manifest_hash=hashlib.sha256(raw).hexdigest(), manifest_size_bytes=len(raw))


def _sealed(row):
    if (not 0 < len(row.manifest) <= MAX_MANIFEST or len(row.manifest) != row.manifest_size_bytes
            or hashlib.sha256(row.manifest).hexdigest() != row.manifest_hash):
        raise HTTPException(409, 'Annotation evidence failed its hash/size check')
    try:
        value = json.loads(row.manifest)
        if canonical_bytes(value) != row.manifest:
            raise ValueError('Not canonical')
        return value
    except (ValueError, TypeError) as error:
        raise HTTPException(409, 'Annotation evidence is not canonical JSON') from error


def _pin(dataset):
    return {'id': dataset.id, 'revision': dataset.revision, 'manifest_hash': dataset.manifest_hash}


def _key(path, key):
    if not isinstance(key, str) or not 1 <= len(key) <= 180:
        raise HTTPException(422, 'Use a stable Idempotency-Key of 1..180 characters')
    return 'annotation:' + digest({'path': path, 'key': key})


def proposal(db, session, runtimes, decoders, dataset_id, body):
    dataset = get_dataset(session, dataset_id)
    if dataset.revision != body.expected_revision:
        raise HTTPException(409, 'Dataset changed; reload and inspect the new workload')
    model = get_model(session, body.model_id, body.model_revision)
    runtime = runtimes.get(body.runtime_id)
    if runtime is None:
        raise HTTPException(422, 'Selected inference runtime is not installed')
    metadata = RuntimeMetadata.model_validate(runtime.metadata())
    if metadata.status != 'available' or model.profile not in metadata.profiles or model.profile not in ('boxes_xyxy_v1', 'opencv_yolox_rgb_v1'):
        raise HTTPException(422, 'Model profile is not available in this runtime')
    if (set(m.model_label for m in body.class_mapping) != set(model.labels)
            or any(m.dataset_label is not None and m.dataset_label not in dataset.labels for m in body.class_mapping)
            or not any(m.dataset_label is not None for m in body.class_mapping)):
        raise HTTPException(422, 'Map every model label to an existing dataset class or explicit exclusion; at least one class must remain')
    selected = [m for m in dataset.images if m.review_status == 'unreviewed' and not m.annotations]
    if not selected:
        raise HTTPException(422, 'No empty unreviewed images; existing labels are preserved')
    assets = {a.id: a for a in dataset.image_assets}
    groups, total_bytes, identities = {}, 0, set()
    targets = []
    for member in selected:
        asset = assets[member.image_id]; inp = asset.input
        identity = (inp.recording_sha256, inp.channel_id, inp.ordinal, inp.message_sha256)
        if identity in identities:
            raise HTTPException(422, 'Repeated original identity cannot be labelled with two interpretations')
        identities.add(identity)
        group = (inp.recording_id, inp.channel_id, inp.decoder_id, inp.decoder_version)
        groups.setdefault(group, []).append(asset)
        total_bytes += inp.message_size_bytes
        targets.append({'image_id': asset.id, 'input': inp.model_dump(mode='json'),
                        'image_sha256': asset.image.sha256, 'width': asset.image.width, 'height': asset.image.height})
    if total_bytes > MAX_BYTES:
        raise HTTPException(413, 'Selected payload exceeds the 64 MiB label-assist bound; no partial selection is used')
    requests = []
    for group, group_assets in groups.items():
        for offset in range(0, len(group_assets), 64):
            chunk = group_assets[offset:offset+64]
            request = PerceptionJobCreate(label=f'Annotation draft · {dataset.name}'[:180],
                model_id=model.id, model_revision=model.revision, runtime_id=metadata.id,
                recording_id=group[0], channel_id=group[1], decoder_id=group[2], decoder_version=group[3],
                ordinals=[a.input.ordinal for a in chunk], seed=body.seed, score_threshold=body.score_threshold,
                iou_threshold=body.iou_threshold, max_detections=body.max_detections)
            requests.append({'request': request.model_dump(mode='json'), 'image_ids': [a.id for a in chunk]})
    if len(requests) > MAX_JOBS:
        raise HTTPException(413, 'Workload needs more than 16 inference jobs; split the dataset explicitly')
    # Verify bounded source bytes and exact PNGs before approving a workload.
    from .dataset_schemas import DatasetImageCreate
    for member in selected:
        asset = assets[member.image_id]
        selection = DatasetImageCreate.model_validate(asset.input.model_dump(include=set(DatasetImageCreate.model_fields)))
        rec, entry, current, snapshot = _source(session, decoders, selection)
        if current != asset.input or snapshot != asset.recording_snapshot:
            raise HTTPException(409, 'Original image identity changed since capture')
        read_message_bytes(db, rec, entry); dataset_image_bytes(db, asset)
    selection = AnnotationSelection.model_validate(body.model_dump(include=set(AnnotationSelection.model_fields)))
    value = {'schema_version': VERSION, 'request': selection.model_dump(mode='json'), 'dataset': _pin(dataset),
        'model': model.model_dump(mode='json'), 'runtime': metadata.model_dump(mode='json'),
        'targets': targets, 'batches': requests,
        'workload': {'total_images': len(dataset.images), 'target_images': len(selected),
            'preserved_images': len(dataset.images)-len(selected), 'inference_jobs': len(requests),
            'recordings': len({a['input']['recording_id'] for a in targets}), 'payload_bytes': total_bytes},
        'limitations': LIMITATIONS}
    _seal(value)
    return value


def task_get(session, task_id):
    row = session.get(AnnotationTaskRow, task_id)
    if row is None:
        raise HTTPException(404, 'Annotation task not found')
    value = _sealed(row)
    try:
        definition = value['definition']
        dataset = get_dataset(session, row.dataset_id, row.dataset_revision)
        if (value['schema_version'] != VERSION or value['id'] != row.id or value['created_at'] != row.created_at
                or definition['dataset'] != _pin(dataset) or digest(definition) != value['definition_hash']
                or not 1 <= len(value['jobs']) == len(definition['batches']) <= MAX_JOBS):
            raise ValueError('Source or definition differs')
        for link, batch in zip(value['jobs'], definition['batches']):
            job = jobs._row(session, link['id']); task = perception_jobs.detail(session, job)
            if (task.input_hash != link['input_hash'] or task.request.model_dump(mode='json') != batch['request']
                    or task.model_snapshot != definition['model'] or job.prepared['runtime_snapshot'] != definition['runtime']):
                raise ValueError('Job differs from its annotation intent')
        return value
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(409, 'Annotation task linkage differs from its pinned definition') from error


def start(db, runtimes, decoders, dataset_id, body, key):
    request_key, request_hash = _key(dataset_id+'/create', key), digest(body.model_dump(mode='json'))
    with jobs._write(db) as session:
        prior = session.scalar(select(AnnotationTaskRow).where(AnnotationTaskRow.request_key == request_key))
        if prior:
            if prior.request_hash != request_hash:
                raise HTTPException(409, 'Idempotency-Key already identifies a different annotation request')
            return task_get(session, prior.id)
        value = proposal(db, session, runtimes, decoders, dataset_id, body)
        if digest(value) != body.expected_preview_hash:
            raise HTTPException(409, 'Annotation preview changed; inspect the full workload again')
        task_id, now, links = uid('annotation'), utcnow(), []
        for index, batch in enumerate(value['batches']):
            task = perception_jobs.submit_in_session(db, session, runtimes, decoders,
                PerceptionJobCreate.model_validate(batch['request']), f'{task_id}:{index}')
            links.append({'id': task.id, 'input_hash': task.input_hash})
        intent = {'schema_version': VERSION, 'id': task_id, 'created_at': now,
                  'definition': value, 'definition_hash': digest(value), 'jobs': links}
        session.add(AnnotationTaskRow(id=task_id, dataset_id=dataset_id, dataset_revision=body.expected_revision,
            created_at=now, request_key=request_key, request_hash=request_hash, **_seal(intent)))
        session.flush()
        return task_get(session, task_id)


def publication_get(session, task_id):
    row = session.get(AnnotationPublicationRow, task_id)
    if row is None:
        return None
    value = _sealed(row)
    task = task_get(session, task_id)
    dataset = get_dataset(session, row.dataset_id, row.dataset_revision)
    if (value['schema_version'] != 'annotation-publication/1' or value['task_id'] != task_id
            or value['dataset'] != _pin(dataset) or value['definition_hash'] != task['definition_hash']
            or digest(value['report']) != value['report_hash']
            or row.dataset_id != task['definition']['dataset']['id']
            or row.dataset_revision != task['definition']['dataset']['revision']+1):
        raise HTTPException(409, 'Annotation publication differs from the dataset or task')
    return {**value, 'manifest_hash': row.manifest_hash,
            'download_url': f'/api/v1/annotation-tasks/{task_id}/publication'}


def report(session, task_id):
    task = task_get(session, task_id); d = task['definition']
    mapping = {m['model_label']: m['dataset_label'] for m in d['request']['class_mapping']}
    by_image, job_states = {}, []
    for link, batch in zip(task['jobs'], d['batches']):
        job = perception_jobs.detail(session, jobs._row(session, link['id']))
        job_states.append({'id': job.id, 'status': job.status, 'manifest_hash': job.manifest_hash, 'error': job.error})
        for image_id, frame in zip(batch['image_ids'], job.frames):
            by_image[image_id] = (job, frame)
    rows = []
    for target in d['targets']:
        job, frame = by_image[target['image_id']]
        status, reason, suggestions = frame.status, frame.error, []
        if status == 'completed':
            inp = {k: v for k, v in frame.input.model_dump(mode='json').items() if k != 'selection_index'}
            if (inp != target['input'] or frame.image is None or frame.image.sha256 != target['image_sha256']
                    or (frame.image.width, frame.image.height) != (target['width'], target['height'])):
                status, reason = 'input_changed', 'Decoded image differs from the image being annotated'
            else:
                for index, prediction in enumerate(frame.detections):
                    label = mapping[prediction.label]
                    if label is not None:
                        suggestions.append({'id': f'auto-{index}', 'label': label,
                            'bbox_xyxy': list(prediction.bbox_xyxy), 'iscrowd': False})
                status = 'suggested' if suggestions else 'empty_prediction'
        rows.append({'image_id': target['image_id'], 'status': status, 'reason': reason,
            'job_id': job.id, 'selection_index': frame.selection_index,
            'suggestions': suggestions, 'raw_predictions': [p.model_dump(mode='json') for p in frame.detections],
            'runtime': frame.runtime, 'source_output_hash': frame.output_hash})
    terminal = all(j['status'] in jobs.TERMINAL for j in job_states)
    result = {'schema_version': 'annotation-report/1', 'id': task_id, 'definition_hash': task['definition_hash'],
        'dataset': d['dataset'], 'model': d['model'], 'policy': d['request'], 'workload': d['workload'],
        'jobs': job_states, 'rows': rows, 'terminal': terminal,
        'counts': {s: sum(r['status'] == s for r in rows) for s in
            ('suggested', 'empty_prediction', 'input_changed', 'failed', 'not_attempted', 'running')},
        'publication': publication_get(session, task_id), 'limitations': LIMITATIONS}
    _seal(result)
    return result


def publish(db, task_id, body, key):
    request_key, request_hash = _key(task_id+'/publish', key), digest(body.model_dump(mode='json'))
    with jobs._write(db) as session:
        old = session.get(AnnotationPublicationRow, task_id)
        if old:
            if old.request_key != request_key or old.request_hash != request_hash:
                raise HTTPException(409, 'This task was already published; read the existing publication')
            return publication_get(session, task_id)
        current_report = report(session, task_id)
        if not current_report['terminal']:
            raise HTTPException(409, 'Wait until all source inference jobs are terminal')
        if digest(current_report) != body.expected_report_hash:
            raise HTTPException(409, 'Draft report changed; inspect the complete report before publication')
        current = get_dataset(session, current_report['dataset']['id'])
        if _pin(current) != current_report['dataset'] or current.revision != body.expected_revision:
            raise HTTPException(409, 'Dataset was edited since submission; no labels are overwritten or automatically rebased')
        usable = {r['image_id']: r for r in current_report['rows'] if r['status'] in ('suggested', 'empty_prediction')}
        if not usable:
            raise HTTPException(409, 'No valid predictions to publish; failed inputs remain in the task report')
        if current.revision >= 2**31-1:
            raise HTTPException(409, 'Dataset revision limit reached')
        members = []
        for member in current.images:
            if member.image_id in usable:
                if member.review_status != 'unreviewed' or member.annotations:
                    raise HTTPException(409, 'Refusing to overwrite existing labels')
                member = DatasetMember(image_id=member.image_id, split=member.split,
                    annotation_source='imported', annotations=usable[member.image_id]['suggestions'],
                    review_note=f'Model-assisted draft {task_id}; NOT reviewed. Inspect model lineage and original pixels.')
            members.append(member)
        for asset in current.image_assets:
            if asset.id in usable: dataset_image_bytes(db, asset)
        values = current.model_dump(include=set(DatasetCreate.model_fields), mode='json')
        values.update(images=[m.model_dump(mode='json') for m in members], note=body.note)
        session.get(DatasetRow, current.id).revision += 1
        dataset = _insert_revision(session, current.id, current.revision+1, DatasetCreate.model_validate(values), current.image_assets)
        receipt = {'schema_version': 'annotation-publication/1', 'task_id': task_id, 'created_at': utcnow(),
            'definition_hash': current_report['definition_hash'], 'dataset': _pin(dataset),
            'report_hash': digest(current_report), 'report': current_report,
            'applied_image_ids': list(usable), 'reviewed_by_publication': 0}
        session.add(AnnotationPublicationRow(task_id=task_id, dataset_id=dataset.id, dataset_revision=dataset.revision,
            request_key=request_key, request_hash=request_hash, **_seal(receipt)))
        session.flush()
        report(session, task_id)  # The complete post-publication report must fit before commit.
        return publication_get(session, task_id)


def annotation_origins(session, dataset, *, memo=None, visiting=None):
    """Conservative model-origin retention through edits and explicit Collection copies.

    Independent relabeling cannot silently erase an origin in this first slice.
    This is evidence lineage, not protection against deliberate external laundering.
    """
    memo = {} if memo is None else memo
    visiting = set() if visiting is None else visiting
    key = (dataset.id, dataset.revision)
    if key in memo: return memo[key]
    if key in visiting or len(visiting) >= 16 or len(memo) >= 256:
        raise HTTPException(409, 'Annotation lineage exceeds a safe acyclic traversal bound')
    visiting.add(key)
    result = defaultdict(list); assets = {a.id: a for a in dataset.image_assets}
    publications = list(session.scalars(select(AnnotationPublicationRow).where(
        AnnotationPublicationRow.dataset_id == dataset.id, AnnotationPublicationRow.dataset_revision <= dataset.revision).limit(65)))
    if len(publications) > 64:
        raise HTTPException(413, 'Annotation lineage exceeds 64 publications')
    for row in publications:
        p = publication_get(session, row.task_id)
        for image_id in p['applied_image_ids']:
            if image_id in assets:
                result[image_id].append({'task_id': row.task_id, 'model_sha256': p['report']['model']['artifact']['sha256'],
                    'model_id': p['report']['model']['id'], 'model_revision': p['report']['model']['revision'],
                    'publication_dataset_id': row.dataset_id, 'publication_dataset_revision': row.dataset_revision,
                    'publication_hash': p['manifest_hash']})
    from .collection_models import CollectionDatasetRow
    from .collections import publication_get as collection_publication, get_collection
    copies = list(session.scalars(select(CollectionDatasetRow).where(CollectionDatasetRow.dataset_id == dataset.id,
        CollectionDatasetRow.dataset_revision <= dataset.revision).limit(65)))
    if len(copies) > 64: raise HTTPException(413, 'Too many collection label sources')
    for copy in copies:
        p = collection_publication(session, copy.id)
        collection = get_collection(session, copy.collection_id, copy.collection_revision)
        rows = {r['key']: r for r in collection['selection']['rows']}
        for decision in p['proposal']['request']['decisions']:
            image_id = decision.get('image_id')
            if decision['action'] != 'include' or image_id not in assets or not decision.get('label_option_id'):
                continue
            option = next(o for o in rows[decision['key']]['label_options'] if o['id'] == decision['label_option_id'])
            parent = get_dataset(session, option['dataset_id'], option['dataset_revision'])
            result[image_id].extend(annotation_origins(session, parent, memo=memo, visiting=visiting).get(image_id, []))
    visiting.remove(key)
    memo[key] = {image_id: list({digest(v): v for v in origins}.values()) for image_id, origins in result.items() if origins}
    return memo[key]


def check_evaluation_independence(session, dataset, sources, split):
    origins = annotation_origins(session, dataset)
    selected = {m.image_id for m in dataset.images if m.split == split and m.review_status == 'reviewed'}
    model_hashes = {s.model_snapshot['artifact']['sha256'] for s in sources}
    if any(o['model_sha256'] in model_hashes for image_id, rows in origins.items() if image_id in selected for o in rows):
        raise HTTPException(422, 'Self-evaluation blocked: this model produced known label drafts in the selected reviewed images. Human review does not prove independent ground truth; use an independently labelled benchmark.')
    if not origins:
        return None
    from .annotation_schemas import AnnotationLineageSnapshot
    return AnnotationLineageSnapshot(dataset_id=dataset.id, dataset_revision=dataset.revision,
        dataset_manifest_hash=dataset.manifest_hash, images=origins).model_dump(mode='json')


def _download(raw, name):
    return Response(raw, media_type='application/json', headers={'Content-Disposition': f'attachment; filename="{name}.json"',
        'X-Content-Type-Options': 'nosniff', 'X-Content-SHA256': hashlib.sha256(raw).hexdigest(), 'Cache-Control': 'no-store'})


def register_annotation_routes(api, db, runtimes, decoders):
    @api.post('/datasets/{dataset_id}/annotation-tasks/preview', tags=['annotation assist'])
    def preview(dataset_id: Identity, body: AnnotationSelection):
        with db.read_session() as session:
            value = proposal(db, session, runtimes, decoders, dataset_id, body)
            return {**value, 'preview_hash': digest(value), 'jobs_created': 0}

    @api.post('/datasets/{dataset_id}/annotation-tasks', status_code=202, tags=['annotation assist'])
    def create(dataset_id: Identity, body: AnnotationCreate, idempotency_key: str = Header(min_length=1, max_length=180)):
        return start(db, runtimes, decoders, dataset_id, body, idempotency_key)

    @api.get('/datasets/{dataset_id}/annotation-tasks', tags=['annotation assist'])
    def listing(dataset_id: Identity, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)):
        with db.read_session() as session:
            get_dataset(session, dataset_id)
            rows = list(session.scalars(select(AnnotationTaskRow).where(AnnotationTaskRow.dataset_id == dataset_id)
                .order_by(AnnotationTaskRow.created_at.desc(), AnnotationTaskRow.id).offset(offset).limit(limit+1)))
            return {'items': [{'id': r.id, 'dataset_revision': r.dataset_revision, 'created_at': r.created_at} for r in rows[:limit]],
                    'next_offset': offset+limit if len(rows) > limit else None}

    @api.get('/annotation-tasks/{task_id}', tags=['annotation assist'])
    def read(task_id: Identity):
        with db.read_session() as session:
            value = report(session, task_id); raw = canonical_bytes(value)
            query = urlencode({'expected_hash': digest(value)})
            return {**value, 'manifest_hash': digest(value), 'manifest_size_bytes': len(raw),
                    'download_url': f'/api/v1/annotation-tasks/{task_id}/export?{query}'}

    @api.get('/annotation-tasks/{task_id}/export', tags=['annotation assist'])
    def export(task_id: Identity, expected_hash: str = Query(..., pattern=r'^[a-f0-9]{64}$')):
        with db.read_session() as session:
            value = report(session, task_id)
            if digest(value) != expected_hash: raise HTTPException(409, 'Report changed; reload before export')
            return _download(canonical_bytes(value), task_id)

    @api.post('/annotation-tasks/{task_id}/publish', status_code=201, tags=['annotation assist'])
    def materialize(task_id: Identity, body: AnnotationPublish, idempotency_key: str = Header(min_length=1, max_length=180)):
        return publish(db, task_id, body, idempotency_key)

    @api.get('/annotation-tasks/{task_id}/publication', tags=['annotation assist'])
    def receipt(task_id: Identity):
        with db.read_session() as session:
            if publication_get(session, task_id) is None: raise HTTPException(404, 'Drafts not published')
            return _download(session.get(AnnotationPublicationRow, task_id).manifest, task_id+'-publication')

    @api.get('/datasets/{dataset_id}/annotation-lineage', tags=['annotation assist'])
    def lineage(dataset_id: Identity, revision: int | None = Query(None, ge=1, le=2**31-1)):
        with db.read_session() as session:
            dataset = get_dataset(session, dataset_id, revision)
            return {'dataset': _pin(dataset), 'images': annotation_origins(session, dataset),
                    'limitations': LIMITATIONS, 'independence_verified': False}
