"""Verification plans join existing immutable inputs, jobs and investigation tools.

Only explicit writes and the worker advance executions; all GETs are read-only.
Evidence references are context, not new safety verdicts.
"""
import hashlib
import json
from collections import defaultdict
from fastapi import Header, HTTPException, Response
from sqlalchemy import select, func
from . import jobs, perception_jobs, detection_evaluations
from .database import RunRow
from .datasets import get_dataset
from .models import get_model, verified_model_path
from .job_models import JobRow
from .perception_schemas import PerceptionJobCreate
from .detection_evaluation_schemas import DetectionEvaluationCreate
from .evaluation_reviews import build_review
from .service import canonical_bytes, digest, uid, utcnow
from .verification_models import VerificationPlanRow, VerificationRevisionRow, VerificationAttemptRow
from .verification_schemas import VerificationPlanCreate, PlanAttemptCreate

VERSION = 'verification-plan/1'
LIMITATIONS = ['Human image-condition declarations are not measured ODD or independent events.',
    'Context/diagnostic evidence does not satisfy detection criteria or closed-loop safety.',
    'Minimum counts and thresholds are user-defined; passing is not release or safety approval.',
    'COCO size metrics use native bbox-area ranges; arbitrary size boundaries are unsupported.',
    'Reviewer/author names are declarations, not authenticated identities.']

def _key(key):
    if not key or len(key) > 160:
        raise HTTPException(422, 'Stable Idempotency-Key of at most 160 characters required')
    return key

def _verified(raw, expected):
    if raw is None or hashlib.sha256(raw).hexdigest() != expected:
        raise HTTPException(409, 'Verification manifest integrity check failed')
    return json.loads(raw)

def plan_detail(session, identifier, revision=None):
    head = session.get(VerificationPlanRow, identifier)
    if head is None:
        raise HTTPException(404, 'Verification plan not found')
    latest=session.scalar(select(func.max(VerificationRevisionRow.revision)).where(VerificationRevisionRow.plan_id==identifier))
    if head.revision!=latest:
        raise HTTPException(409, 'Plan head differs from its immutable revision history')
    row = session.get(VerificationRevisionRow, (identifier, revision or head.revision))
    if row is None:
        raise HTTPException(404, 'Verification plan revision not found')
    value = _verified(row.manifest, row.manifest_hash)
    if value['id'] != identifier or value['revision'] != row.revision:
        raise HTTPException(409, 'Plan revision identity mismatch')
    if row.revision>1:
        previous=session.get(VerificationRevisionRow,(identifier,row.revision-1))
        if previous is None or value['previous_manifest_hash']!=previous.manifest_hash:
            raise HTTPException(409, 'Plan revision chain mismatch')
        _verified(previous.manifest,previous.manifest_hash)
    elif value['previous_manifest_hash'] is not None:
        raise HTTPException(409, 'First plan revision has a predecessor')
    return {**value, 'manifest_hash': row.manifest_hash,
        'download_url': f'/api/v1/verification-plans/{identifier}/export?revision={row.revision}'}

def _resolve_evidence(session, ref):
    if ref.kind in {'dataset','case','collection','suite'} and ref.revision is None:
        raise HTTPException(422, 'Revisioned evidence requires an exact revision')
    if ref.kind == 'dataset':
        value = get_dataset(session, ref.id, ref.revision).model_dump(mode='json')
    elif ref.kind == 'case':
        from .cases import _get
        value = _get(session, ref.id, ref.revision)
    elif ref.kind == 'collection':
        from .collections import get_collection
        value = get_collection(session, ref.id, ref.revision)
    elif ref.kind == 'suite':
        from .suites import suite_get
        value = suite_get(session,ref.id,ref.revision).model_dump(mode='json')
    elif ref.kind == 'campaign':
        from .suites import campaign_report
        value = campaign_report(session,ref.id).model_dump(mode='json')
        if value['status'] != 'finished':
            raise HTTPException(422, 'Only terminal campaign snapshots can be linked')
    elif ref.kind == 'evaluation':
        value = detection_evaluations.detail(session, jobs._row(session, ref.id)).model_dump(mode='json')
        if not value['manifest_hash']:
            raise HTTPException(422, 'Only sealed terminal evaluation evidence can be linked')
    else:
        row = session.get(RunRow, ref.id)
        if row is None:
            raise HTTPException(404, 'Evidence run not found')
        value = row.payload
    return {**ref.model_dump(mode='json'), 'snapshot': value,
        'content_hash': digest(value), 'satisfies_criteria': False}

def _snapshot(session, body):
    dataset = get_dataset(session, body.dataset_id, body.dataset_revision)
    members = [m for m in dataset.images if m.split == body.split]
    if not members:
        raise HTTPException(422, 'Selected dataset split is empty')
    ids = {m.image_id for m in members}
    for cell in body.cells:
        if not set(cell.image_ids) <= ids or not set(cell.labels) <= set(dataset.labels):
            raise HTTPException(422, 'Cell selections must belong to the pinned dataset split and labels')
    models = [get_model(session, item.model_id, item.revision) for item in body.models]
    for ref, model in zip(body.models, models):
        if len({m.model_label for m in ref.class_mapping}) != len(ref.class_mapping) or set(m.model_label for m in ref.class_mapping) != set(model.labels):
            raise HTTPException(422, 'Every model label requires exactly one explicit mapping')
        if not any(m.dataset_label is not None for m in ref.class_mapping):
            raise HTTPException(422, 'Verification requires at least one supported mapped dataset class per model')
        if any(m.dataset_label is not None and m.dataset_label not in dataset.labels for m in ref.class_mapping):
            raise HTTPException(422, 'Mapped classes must belong to the pinned dataset')
    return {'dataset': dataset.model_dump(mode='json'), 'models': [m.model_dump(mode='json') for m in models],
        'evidence': [_resolve_evidence(session, ref) for ref in body.evidence]}

def save_plan(db, body, key, identifier=None):
    key = 'plan:' + (identifier or 'new') + ':' + _key(key)
    request_hash = digest(body.model_dump(mode='json'))
    with jobs._write(db) as session:
        existing = session.scalar(select(VerificationRevisionRow).where(VerificationRevisionRow.request_key == key))
        if existing:
            if existing.request_hash != request_hash:
                raise HTTPException(409, 'Idempotency key already used for another plan definition')
            return plan_detail(session, existing.plan_id, existing.revision)
        head = session.get(VerificationPlanRow, identifier) if identifier else None
        if identifier and head is None:
            raise HTTPException(404, 'Plan not found')
        if head and body.expected_revision != head.revision:
            raise HTTPException(409, 'Plan changed; reload before revising')
        if not head and body.expected_revision is not None:
            raise HTTPException(422, 'A new plan has no expected revision')
        snapshot = _snapshot(session, body)
        previous = plan_detail(session, identifier) if head else None
        if head is None:
            head = VerificationPlanRow(id=uid('vp'), revision=1)
            session.add(head); session.flush()
        else:
            head.revision += 1
        value = {'schema_version': VERSION, 'id': head.id, 'revision': head.revision, 'created_at': utcnow(),
            'previous_manifest_hash': previous['manifest_hash'] if previous else None,
            'definition': body.model_dump(mode='json'), 'snapshots': snapshot, 'limitations': LIMITATIONS}
        raw = canonical_bytes(value)
        if len(raw) > 8*1024*1024:
            raise HTTPException(422, 'Plan snapshot exceeds 8 MiB')
        session.add(VerificationRevisionRow(plan_id=head.id, revision=head.revision, request_key=key,
            request_hash=request_hash, manifest=raw, manifest_hash=hashlib.sha256(raw).hexdigest()))
        session.flush()
        return plan_detail(session, head.id, head.revision)

def _tasks(plan):
    definition, snapshot = plan['definition'], plan['snapshots']
    assets = {a['id']: a for a in snapshot['dataset']['image_assets']}
    groups = defaultdict(list)
    for member in snapshot['dataset']['images']:
        if member['split'] != definition['split']:
            continue
        inp = assets[member['image_id']]['input']
        groups[(inp['recording_id'], inp['channel_id'], inp['decoder_id'], inp['decoder_version'])].append(inp['ordinal'])
    selections = []
    for (recording, channel, decoder, version), ordinals in sorted(groups.items()):
        if len(set(ordinals)) != len(ordinals):
            raise HTTPException(422, 'Dataset repeats an exact original input')
        for offset in range(0, len(ordinals), 64):
            selections.append({'recording_id': recording, 'channel_id': channel, 'decoder_id': decoder,
                'decoder_version': version, 'ordinals': ordinals[offset:offset+64]})
    if len(selections) > 16:
        raise HTTPException(422, 'V1 supports at most 16 source inference jobs per model')
    return [[PerceptionJobCreate(label=definition['name'][:165] + (' / baseline' if i == 0 else ' / candidate'),
        model_id=model['model_id'], model_revision=model['revision'], runtime_id=definition['runtime_id'],
        seed=definition['seed'], score_threshold=definition['score_threshold'], iou_threshold=definition['iou_threshold'],
        max_detections=definition['max_detections'], **selection).model_dump(mode='json') for selection in selections]
        for i, model in enumerate(definition['models'])]

def _cell_counts(plan, cell=None):
    definition, dataset = plan['definition'], plan['snapshots']['dataset']
    members = [m for m in dataset['images'] if m['split'] == definition['split'] and
        (cell is None or m['image_id'] in cell['image_ids'])]
    assets = {a['id']: a for a in dataset['image_assets']}
    labels = (cell['labels'] if cell else []) or dataset['labels']
    criterion = cell['criterion'] if cell else definition['criterion']
    metric = criterion['metric']
    def in_area(a):
        x,y,u,v = a['bbox_xyxy']; area = (u-x)*(v-y)
        return {'ap_small': area <= 1024, 'ap_medium': 1024 <= area <= 9216,
            'ap_large': area >= 9216}.get(metric, True)
    reviewed = [m for m in members if m['review_status'] == 'reviewed']
    objects = sum(a['label'] in labels and not a['iscrowd'] and in_area(a) for m in reviewed for a in m['annotations'])
    return {'images': len(members), 'reviewed_images': len(reviewed), 'objects': objects,
        'unique_recordings': len({assets[m['image_id']]['input']['recording_sha256'] for m in reviewed}),
        'independent_events': None}

def _condition_conflicts(plan):
    single={a['name'] for a in plan['definition']['axes'] if a.get('cardinality','single')=='single'}
    values=defaultdict(lambda: defaultdict(set))
    for cell in plan['definition']['cells']:
        if cell['review_status']!='reviewed': continue
        for image in cell['image_ids']:
            for axis in single: values[image][axis].add(cell['conditions'][axis])
    return {image: {axis: sorted(v) for axis,v in axes.items() if len(v)>1}
        for image,axes in values.items() if any(len(v)>1 for v in axes.values())}


def _cell_preparation(plan, cell):
    if cell['review_status']!='reviewed' or any(v in {'unknown','unclassified'} for v in cell['conditions'].values()):
        return 'condition_review_needed'
    if set(cell['image_ids']) & set(_condition_conflicts(plan)):
        return 'condition_conflict'
    if not cell['image_ids']: return 'data_missing'
    if _cell_counts(plan,cell)['reviewed_images']<len(cell['image_ids']): return 'label_review_needed'
    return 'ready'


def preview(session, db, plan, runtimes, evaluators, retry_of=None):
    definition = plan['definition']
    runtime = runtimes.get(definition['runtime_id']); evaluator = evaluators.get(definition['evaluator_id'])
    if runtime is None or evaluator is None:
        raise HTTPException(422, 'Selected runtime/evaluator is not installed')
    rm = runtime.metadata(); em = evaluator.metadata()
    rm = rm.model_dump(mode='json') if hasattr(rm, 'model_dump') else rm
    em = em.model_dump(mode='json') if hasattr(em, 'model_dump') else em
    if rm['status'] != 'available' or em['status'] != 'available':
        raise HTTPException(422, 'Selected runtime/evaluator is unavailable')
    from types import SimpleNamespace
    from .annotation_assist import check_evaluation_independence
    check_evaluation_independence(session, get_dataset(session, definition['dataset_id'], definition['dataset_revision']),
        [SimpleNamespace(model_snapshot=m) for m in plan['snapshots']['models']], definition['split'])
    for model in plan['snapshots']['models']:
        if model['profile'] not in rm['profiles']:
            raise HTTPException(422, 'Model profile is unsupported')
        verified_model_path(db, model['artifact_id'])
    cells = [{'id': c['id'], 'name': c['name'], 'counts': _cell_counts(plan,c),
        'preparation': _cell_preparation(plan,c)}
        for c in definition['cells']]
    tasks = _tasks(plan)
    eligible = [c for c in definition['cells'] if _cell_preparation(plan,c) in {'ready','label_review_needed'}]
    reuse = [[None for _ in side] for side in tasks]
    if retry_of:
        previous = session.get(VerificationAttemptRow, retry_of)
        if previous is None or previous.plan_id != plan['id'] or not previous.manifest:
            raise HTTPException(422, 'Retry requires a terminal attempt of this plan')
        prior = _attempt_detail(previous)
        if prior['plan_hash'] != plan['manifest_hash']:
            raise HTTPException(422, 'Retry must use the exact previous plan revision')
        if prior['preview']['runtime'] == rm and prior['preview']['evaluator'] == em:
            for side, ids in enumerate(prior['inference_job_ids']):
                for index, identifier in enumerate(ids):
                    job = perception_jobs.detail(session, jobs._row(session,identifier))
                    if job.status == 'completed' and job.manifest_hash and job.request.model_dump(mode='json') == tasks[side][index]:
                        reuse[side][index] = identifier
    reused = sum(identifier is not None for side in reuse for identifier in side)
    value = {'schema_version': 'verification-preview/1', 'plan_id': plan['id'], 'revision': plan['revision'],
        'plan_hash': plan['manifest_hash'], 'retry_of': retry_of, 'reuse_job_ids': reuse, 'runtime': rm, 'evaluator': em, 'tasks': tasks,
        'counts': _cell_counts(plan), 'cells': cells, 'jobs_created': 0,
        'workload': {'inference_jobs': sum(map(len,tasks))-reused, 'reused_inference_jobs': reused, 'evaluation_jobs': 2*(1+len(eligible)),
            'images_per_model': _cell_counts(plan)['images']}, 'limitations': LIMITATIONS}
    return {**value, 'preview_hash': digest(value)}

def _attempt_detail(row):
    if row.payload.get('id')!=row.id or row.payload.get('plan_id')!=row.plan_id or digest(row.payload) != row.payload_hash:
        raise HTTPException(409, 'Attempt receipt integrity check failed')
    value = _verified(row.manifest, row.manifest_hash) if row.manifest else row.payload
    return {**value, 'manifest_hash': row.manifest_hash,
        'download_url': f'/api/v1/verification-attempts/{row.id}/export' if row.manifest else None}

def _seal_attempt(row, value):
    row.payload = value
    row.payload_hash = digest(value)
    row.manifest = canonical_bytes(value)
    row.manifest_hash = hashlib.sha256(row.manifest).hexdigest()

def submit_attempt(db, runtimes, decoders, evaluators, identifier, body, key):
    if not body.confirm_trusted_models:
        raise HTTPException(422, 'Explicit confirmation of trusted models and workload required')
    request_key = 'attempt:' + identifier + ':' + _key(key)
    request_hash = digest(body.model_dump(mode='json'))
    with jobs._write(db) as session:
        prior = session.scalar(select(VerificationAttemptRow).where(VerificationAttemptRow.request_key == request_key))
        if prior:
            if prior.request_hash != request_hash:
                raise HTTPException(409, 'Idempotency key reused with another attempt')
            return _attempt_detail(prior)
        plan = plan_detail(session, identifier, body.revision)
        proposed = preview(session, db, plan, runtimes, evaluators, body.retry_of)
        if proposed['preview_hash'] != body.expected_preview_hash:
            raise HTTPException(409, 'Preview changed; inspect workload again')
        if body.retry_of:
            previous = session.get(VerificationAttemptRow, body.retry_of)
            if previous is None or previous.plan_id != identifier or not previous.manifest or previous.payload['plan_hash'] != plan['manifest_hash']:
                raise HTTPException(422, 'Retry requires a terminal attempt of this exact plan revision')
        aid = uid('va')
        children = [[proposed['reuse_job_ids'][side][index] or perception_jobs.submit_in_session(db, session, runtimes, decoders, PerceptionJobCreate.model_validate(task),
            f'{aid}:inference:{side}:{index}').id for index, task in enumerate(tasks)]
            for side, tasks in enumerate(proposed['tasks'])]
        value = {'schema_version':'verification-attempt/1','id':aid,'plan_id':identifier,'revision':body.revision,
            'plan_hash':plan['manifest_hash'],'plan_snapshot':plan,'preview':proposed,'created_at':utcnow(),
            'updated_at':utcnow(),'status':'running','stage':'inference','retry_of':body.retry_of,
            'inference_job_ids':children,'evaluation_job_ids':{},'cancel_requested':False,'report':None,'error':None}
        row = VerificationAttemptRow(id=aid, plan_id=identifier, request_key=request_key, request_hash=request_hash, payload=value, payload_hash=digest(value))
        session.add(row); session.flush()
        return _attempt_detail(row)

def _criterion_result(criterion, counts, review, evaluations):
    reason = None
    if counts['images'] == 0:
        reason = 'data_missing'
    elif counts['reviewed_images'] != counts['images']:
        reason = 'label_review_needed'
    elif any(criterion[k] is None for k in ('minimum_score','maximum_drop','minimum_images','minimum_objects','minimum_recordings')):
        reason = 'criteria_unconfigured'
    elif counts['images'] < criterion['minimum_images'] or counts['objects'] < criterion['minimum_objects'] or counts['unique_recordings'] < criterion['minimum_recordings']:
        reason = 'data_insufficient'
    if reason:
        return {'status':reason,'baseline':None,'candidate':None,'delta':None}
    if review['status'] != 'comparable':
        return {'status':review['status'],'baseline':None,'candidate':None,'delta':None}
    metric = criterion['metric']
    values = [getattr(e.report.metrics,metric) if e.report and e.report.metrics else None for e in evaluations]
    if any(v is None for v in values):
        return {'status':'not_evaluated','baseline':values[0],'candidate':values[1],'delta':None}
    delta = values[1]-values[0]
    status = 'passed' if values[1] >= criterion['minimum_score'] and delta >= -criterion['maximum_drop'] else 'degraded'
    return {'status':status,'baseline':values[0],'candidate':values[1],'delta':delta}

def _report(session, value):
    plan = value['plan_snapshot']; definition=plan['definition']
    rows=[]
    for cell in [None, *definition['cells']]:
        identifier = cell['id'] if cell else '__overall__'
        counts = _cell_counts(plan,cell)
        criterion = cell['criterion'] if cell else definition['criterion']
        ids = value['evaluation_job_ids'].get(identifier)
        if not ids:
            status = _cell_preparation(plan,cell) if cell else 'not_evaluated'
            rows.append({'id':identifier,'name':cell['name'] if cell else 'Overall','counts':counts,
                'verdict':{'status':status},'comparison':None,'evaluation_ids':[], 'criterion':criterion})
            continue
        evaluations=[detection_evaluations.detail(session,jobs._row(session,i)) for i in ids]
        review=build_review(*evaluations)
        rows.append({'id':identifier,'name':cell['name'] if cell else 'Overall','counts':counts,
            'verdict':_criterion_result(criterion,counts,review,evaluations),'comparison':review,
            'evaluation_ids':ids,'evaluation_manifests':[e.manifest_hash for e in evaluations], 'criterion':criterion})
    statuses=[r['verdict']['status'] for r in rows]
    overall='passed' if all(s=='passed' for s in statuses) else 'degraded' if 'degraded' in statuses else 'inconclusive'
    memberships=defaultdict(list)
    for cell in definition['cells']:
        for image in cell['image_ids']: memberships[image].append(cell['id'])
    classified={image for cell in definition['cells'] if _cell_preparation(plan,cell) in {'ready','label_review_needed'} for image in cell['image_ids']}
    children=[jobs._row(session,i) for pair in value['inference_job_ids'] for i in pair]
    evaluation_children=[jobs._row(session,i) for pair in value['evaluation_job_ids'].values() for i in pair]
    execution={status:sum(c.status==status for c in children) for status in sorted(jobs.TERMINAL)}
    evaluation={status:sum(c.status==status for c in evaluation_children) for status in sorted(jobs.TERMINAL)}
    return {'status':overall,'execution_outcomes':execution,'evaluation_outcomes':evaluation,'cells':rows,'overlapping_images':{k:v for k,v in memberships.items() if len(v)>1},
        'condition_conflicts':_condition_conflicts(plan),
        'unclassified_image_ids':[m['image_id'] for m in plan['snapshots']['dataset']['images'] if
            m['split']==definition['split'] and m['image_id'] not in classified], 'limitations':LIMITATIONS}

def advance(db, runtimes, decoders, evaluators):
    """One transaction per polling pass; child publication is durable and idempotent."""
    with jobs._write(db) as session:
        rows=list(session.scalars(select(VerificationAttemptRow).where(VerificationAttemptRow.manifest == None)))
        for row in rows:
            _attempt_detail(row)
            value=dict(row.payload)
            if value['status'] != 'running': continue
            try:
                plan=value['plan_snapshot']; definition=plan['definition']
                if digest({k:v for k,v in plan.items() if k not in {'manifest_hash','download_url'}}) != value['plan_hash']:
                    raise HTTPException(409,'Pinned plan hash mismatch')
                ids=[i for side in value['inference_job_ids'] for i in side]+[i for pair in value['evaluation_job_ids'].values() for i in pair]
                children=[jobs._row(session,i) for i in ids]
                if value['cancel_requested']:
                    for child in children:
                        if child.status not in jobs.TERMINAL: jobs.cancel_job_row(session,child)
                    if all(c.status in jobs.TERMINAL for c in children):
                        value.update(status='cancelled',stage='terminal',updated_at=utcnow())
                        _seal_attempt(row,value)
                    continue
                if any(c.status not in jobs.TERMINAL for c in children): continue
                if value['stage']=='inference':
                    current_rm=runtimes.get(definition['runtime_id']).metadata()
                    current_em=evaluators.get(definition['evaluator_id']).metadata()
                    for current,pinned in ((current_rm,value['preview']['runtime']),(current_em,value['preview']['evaluator'])):
                        current=current.model_dump(mode='json') if hasattr(current,'model_dump') else current
                        if current!=pinned: raise HTTPException(409,'Pinned execution capability changed')
                    with session.begin_nested():
                        evs={}
                        for cell in [None,*definition['cells']]:
                            if cell and _cell_preparation(plan,cell) not in {'ready','label_review_needed'}: continue
                            cid=cell['id'] if cell else '__overall__'
                            evs[cid]=[]
                            for side in range(2):
                                request={'name':(definition['name']+' / '+cid)[:180],'note':'Verification plan '+value['plan_hash'],
                                    'dataset_id':definition['dataset_id'],'dataset_revision':definition['dataset_revision'],
                                    'split':definition['split'],'inference_job_ids':value['inference_job_ids'][side],
                                    'evaluator_id':definition['evaluator_id'],'class_mapping':definition['models'][side]['class_mapping'],
                                    'score_threshold':0.0,'diagnostic_iou':.5}
                                if cell: request['selection']={'image_ids':cell['image_ids'],'labels':cell['labels'] or plan['snapshots']['dataset']['labels']}
                                evs[cid].append(detection_evaluations.submit_in_session(db,session,evaluators,
                                    DetectionEvaluationCreate.model_validate(request),f'{row.id}:evaluation:{cid}:{side}').id)
                    value.update(evaluation_job_ids=evs,stage='evaluation',updated_at=utcnow())
                    row.payload=value; row.payload_hash=digest(value)
                else:
                    value.update(report=_report(session,value),status='completed',stage='terminal',updated_at=utcnow())
                    _seal_attempt(row,value)
            except (HTTPException,ValueError,KeyError,AttributeError) as error:
                value.update(status='failed',stage='terminal',error=str(getattr(error,'detail',error))[:1500],updated_at=utcnow())
                _seal_attempt(row,value)

def register_verification_routes(api, db, runtimes, decoders, evaluators):
    @api.get('/verification-plans')
    def listing():
        with db.session() as session:
            return [plan_detail(session,row.id) for row in session.scalars(select(VerificationPlanRow).order_by(VerificationPlanRow.id))]

    @api.post('/verification-plans',status_code=201)
    def create(body:VerificationPlanCreate,idempotency_key:str|None=Header(default=None)):
        return save_plan(db,body,idempotency_key)

    @api.post('/verification-plans/{identifier}/revisions',status_code=201)
    def revise(identifier:str,body:VerificationPlanCreate,idempotency_key:str|None=Header(default=None)):
        return save_plan(db,body,idempotency_key,identifier)

    @api.get('/verification-plans/{identifier}')
    def get(identifier:str,revision:int|None=None):
        with db.session() as session: return plan_detail(session,identifier,revision)

    @api.get('/verification-plans/{identifier}/export')
    def export(identifier:str,revision:int|None=None):
        with db.session() as session:
            value=plan_detail(session,identifier,revision)
            row=session.get(VerificationRevisionRow,(identifier,value['revision']))
            return Response(row.manifest,media_type='application/json',headers={'ETag':value['manifest_hash']})

    @api.post('/verification-plans/{identifier}/preview')
    def proposed(identifier:str,body:dict):
        if set(body)-{'revision','retry_of'} or 'revision' not in body or type(body['revision']) is not int or body['revision']<1:
            raise HTTPException(422,'Select an exact revision')
        with db.session() as session: return preview(session,db,plan_detail(session,identifier,body['revision']),runtimes,evaluators,body.get('retry_of'))

    @api.post('/verification-plans/{identifier}/attempts',status_code=202)
    def execute(identifier:str,body:PlanAttemptCreate,idempotency_key:str|None=Header(default=None)):
        return submit_attempt(db,runtimes,decoders,evaluators,identifier,body,idempotency_key)

    @api.get('/verification-plans/{identifier}/attempts')
    def attempts(identifier:str):
        with db.session() as session:
            plan_detail(session,identifier)
            return [_attempt_detail(r) for r in session.scalars(select(VerificationAttemptRow).where(VerificationAttemptRow.plan_id==identifier))]

    @api.get('/verification-attempts/{identifier}')
    def attempt(identifier:str):
        with db.session() as session:
            row=session.get(VerificationAttemptRow,identifier)
            if row is None: raise HTTPException(404,'Attempt not found')
            return _attempt_detail(row)

    @api.post('/verification-attempts/{identifier}/cancel')
    def cancel(identifier:str):
        with jobs._write(db) as session:
            row=session.get(VerificationAttemptRow,identifier)
            if row is None: raise HTTPException(404,'Attempt not found')
            _attempt_detail(row)
            if row.manifest: return _attempt_detail(row)
            value=dict(row.payload); value['cancel_requested']=True; value['updated_at']=utcnow()
            row.payload=value; row.payload_hash=digest(value)
            for child_id in [i for pair in value['inference_job_ids'] for i in pair]+[i for pair in value['evaluation_job_ids'].values() for i in pair]:
                child=jobs._row(session,child_id)
                if child.status not in jobs.TERMINAL: jobs.cancel_job_row(session,child)
            return _attempt_detail(row)

    @api.get('/verification-attempts/{identifier}/export')
    def attempt_export(identifier:str):
        with db.session() as session:
            row=session.get(VerificationAttemptRow,identifier)
            if row is None: raise HTTPException(404,'Attempt not found')
            _verified(row.manifest,row.manifest_hash)
            return Response(row.manifest,media_type='application/json',headers={'ETag':row.manifest_hash})
