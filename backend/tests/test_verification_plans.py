import hashlib
import json
import pytest
from sqlalchemy import select, func
from ade2e import jobs
from ade2e.database import SCHEMA_VERSION
from ade2e.job_models import JobRow
from ade2e.worker import run_once
from ade2e.verification_models import VerificationAttemptRow
from test_detection_evaluations import context, evaluation_context


def definition(fixture):
    _,source,dataset,_,body=fixture
    model={'model_id':source.model_id,'revision':source.model_revision,'class_mapping':body.model_dump()['class_mapping']}
    criterion={'metric':'ap','minimum_score':0,'maximum_drop':1,'minimum_images':1,'minimum_objects':0,'minimum_recordings':1}
    return {'name':'Pedestrian migration decision','purpose':'Contract workflow, not road performance',
        'author':'Declared engineer','change_reason':'First plan','dataset_id':dataset['id'],'dataset_revision':dataset['revision'],
        'split':'test','models':[model,model],'runtime_id':source.runtime_id,'evaluator_id':body.evaluator_id,
        'criterion':criterion,'axes':[{'name':'lighting','values':['daylight','night','low_light','twilight']}],
        'cells':[{'id':'day','name':'Reviewed daylight','conditions':{'lighting':'daylight'},'image_ids':[dataset['images'][0]['image_id']],
            'labels':['target'],'review_status':'reviewed','reviewer':'Fixture reviewer','review_note':'Original image reviewed', 'criterion':criterion},
            {'id':'night','name':'Missing night data','conditions':{'lighting':'night'},'image_ids':[],'review_status':'reviewed',
                'reviewer':'Fixture reviewer','review_note':'No data collected','criterion':criterion}]}


def create(fixture):
    (client,app,*_),*_=fixture
    response=client.post('/api/v1/verification-plans',json=definition(fixture),headers={'Idempotency-Key':'first'})
    assert response.status_code==201,response.text
    return client,app,response.json()


def start(fixture):
    client,app,plan=create(fixture)
    proposed=client.post(f'/api/v1/verification-plans/{plan["id"]}/preview',json={'revision':1})
    assert proposed.status_code==200,proposed.text
    body={'revision':1,'expected_preview_hash':proposed.json()['preview_hash'],'confirm_trusted_models':True}
    response=client.post(f'/api/v1/verification-plans/{plan["id"]}/attempts',json=body,headers={'Idempotency-Key':'first-attempt'})
    assert response.status_code==202,response.text
    return client,app,plan,response.json(),body


def tick(app):
    return run_once(app.state.db,app.state.registry,'verification-test',runtime_registry=app.state.runtime_registry,
        sensor_registry=app.state.sensor_registry,evaluator_registry=app.state.evaluator_registry)


def finish(client,app,attempt):
    for _ in range(15):
        tick(app)
        value=client.get(f'/api/v1/verification-attempts/{attempt["id"]}').json()
        if value['status']!='running': return value
    pytest.fail('Attempt did not finish')


def test_plan_pipeline_cohort_and_readonly_export(evaluation_context):
    client,app,plan,attempt,body=start(evaluation_context)
    with app.state.db.session() as session: before=session.scalar(select(func.count()).select_from(JobRow))
    repeated=client.post(f'/api/v1/verification-plans/{plan["id"]}/attempts',json=body,headers={'Idempotency-Key':'first-attempt'})
    assert repeated.json()==attempt
    for _ in range(3): client.get(f'/api/v1/verification-attempts/{attempt["id"]}')
    with app.state.db.session() as session: assert session.scalar(select(func.count()).select_from(JobRow))==before
    result=finish(client,app,attempt)
    assert result['status']=='completed',result
    assert result['report']['status']=='inconclusive'
    rows=result['report']['cells']
    assert rows[0]['counts']['images']==3 and rows[0]['verdict']['status']=='passed'
    assert rows[1]['counts']['images']==1 and rows[1]['verdict']['status']=='passed'
    assert rows[2]['verdict']['status']=='data_missing'
    raw=client.get(result['download_url']).content
    assert hashlib.sha256(raw).hexdigest()==result['manifest_hash']
    assert client.get(plan['download_url']).content==client.get(plan['download_url']).content


def test_revision_pins_old_attempt_and_stale_preview(evaluation_context):
    client,app,plan,attempt,_=start(evaluation_context)
    revised={**definition(evaluation_context),'expected_revision':1,'change_reason':'Raise the acceptance minimum'}
    revised['criterion']['minimum_images']=256
    response=client.post(f'/api/v1/verification-plans/{plan["id"]}/revisions',json=revised,headers={'Idempotency-Key':'rev2'})
    assert response.status_code==201,response.text
    assert response.json()['previous_manifest_hash']==plan['manifest_hash']
    stale=client.post(f'/api/v1/verification-plans/{plan["id"]}/attempts',json={'revision':2,'expected_preview_hash':attempt['preview']['preview_hash'],
        'confirm_trusted_models':True},headers={'Idempotency-Key':'stale'})
    assert stale.status_code==409
    result=finish(client,app,attempt)
    assert result['revision']==1 and result['report']['cells'][0]['verdict']['status']=='passed'
    response=client.post(f'/api/v1/verification-plans/{plan["id"]}/revisions',json=revised,headers={'Idempotency-Key':'stale-write'})
    assert response.status_code==409


def test_partial_inference_failure_keeps_denominator(evaluation_context):
    client,app,_,attempt,_=start(evaluation_context)
    runtime=evaluation_context[0][2]
    runtime.failure_at=runtime.calls+2
    result=finish(client,app,attempt)
    assert result['status']=='completed',result
    overall=result['report']['cells'][0]
    assert overall['counts']['images']==3
    assert overall['verdict']['status']!='passed'
    assert overall['comparison']['status']!='comparable'
    assert all(m['delta'] is None for m in overall['comparison']['metrics'])


def test_cancel_and_explicit_retry(evaluation_context):
    client,app,plan,attempt,_=start(evaluation_context)
    response=client.post(f'/api/v1/verification-attempts/{attempt["id"]}/cancel')
    assert response.status_code==200
    result=finish(client,app,attempt)
    assert result['status']=='cancelled' and result['evaluation_job_ids']=={}
    proposed=client.post(f'/api/v1/verification-plans/{plan["id"]}/preview',json={'revision':1,'retry_of':attempt['id']}).json()
    body={'revision':1,'expected_preview_hash':proposed['preview_hash'],'confirm_trusted_models':True,'retry_of':attempt['id']}
    response=client.post(f'/api/v1/verification-plans/{plan["id"]}/attempts',json=body,headers={'Idempotency-Key':'retry'})
    assert response.status_code==202,response.text
    assert finish(client,app,response.json())['status']=='completed'


def test_unreviewed_condition_and_unconfigured_criterion_never_pass(evaluation_context):
    value=definition(evaluation_context);value['criterion']={};value['cells'][0]['review_status']='draft'
    client,app,*_=evaluation_context[0]
    plan=client.post('/api/v1/verification-plans',json=value,headers={'Idempotency-Key':'draft'}).json()
    proposed=client.post(f'/api/v1/verification-plans/{plan["id"]}/preview',json={'revision':1}).json()
    assert proposed['cells'][0]['preparation']=='condition_review_needed'
    response=client.post(f'/api/v1/verification-plans/{plan["id"]}/attempts',json={'revision':1,'expected_preview_hash':proposed['preview_hash'],
        'confirm_trusted_models':True},headers={'Idempotency-Key':'draft-attempt'})
    result=finish(client,app,response.json())
    assert result['report']['cells'][0]['verdict']['status']=='criteria_unconfigured'
    assert result['report']['cells'][1]['verdict']['status']=='condition_review_needed'


def test_unknown_mapping_and_foreign_cohort_are_refused(evaluation_context):
    client,*_=evaluation_context[0]
    value=definition(evaluation_context);value['cells'][0]['image_ids']=['not-in-dataset']
    assert client.post('/api/v1/verification-plans',json=value,headers={'Idempotency-Key':'foreign'}).status_code==422
    value=definition(evaluation_context);value['cells'][0]['source_taxonomy']='scene-discovery/1'
    assert client.post('/api/v1/verification-plans',json=value,headers={'Idempotency-Key':'mapping'}).status_code==422


def test_attempt_tampering_is_rejected(evaluation_context):
    client,app,_,attempt,_=start(evaluation_context)
    with jobs._write(app.state.db) as session:
        row=session.get(VerificationAttemptRow,attempt['id'])
        row.payload={**row.payload,'plan_hash':'0'*64}
    response=client.get(f'/api/v1/verification-attempts/{attempt["id"]}')
    assert response.status_code==409


def test_native_coco_recomputes_selected_image_class_cohort(evaluation_context):
    from axiom_coco.runtime import CocoBboxEvaluator
    evaluator=CocoBboxEvaluator()
    assert evaluator.metadata().status=='available'
    client,app,*_=evaluation_context[0]
    app.state.evaluator_registry.register(evaluator, replace=True)
    value=definition(evaluation_context);value['evaluator_id']='coco-bbox'
    response=client.post('/api/v1/verification-plans',json=value,headers={'Idempotency-Key':'native'})
    assert response.status_code==201,response.text
    plan=response.json()
    proposed=client.post(f'/api/v1/verification-plans/{plan["id"]}/preview',json={'revision':1}).json()
    response=client.post(f'/api/v1/verification-plans/{plan["id"]}/attempts',json={'revision':1,'expected_preview_hash':proposed['preview_hash'],
        'confirm_trusted_models':True},headers={'Idempotency-Key':'native-attempt'})
    result=finish(client,app,response.json())
    assert result['status']=='completed',result
    cell=result['report']['cells'][1]
    assert cell['verdict']['status']=='passed'
    for identifier in cell['evaluation_ids']:
        detail=client.get('/api/v1/detection-evaluations/'+identifier).json()
        assert detail['report']['implementation']['pycocotools']=='2.0.11'
        assert detail['report']['coverage']['total']==1
        assert [c['label'] for c in detail['report']['classes']]==['target']
        assert detail['report']['metrics']['ap']>0.99


def test_selection_comparison_refuses_different_cohorts_and_legacy_bytes(evaluation_context):
    from ade2e.detection_evaluation_schemas import DetectionEvaluationCreate
    _,source,dataset,_,body=evaluation_context
    legacy=body.model_dump(mode='json')
    assert 'selection' not in legacy
    assert DetectionEvaluationCreate.model_validate(legacy).model_dump(mode='json')==legacy
    client,app,*_=evaluation_context[0]
    ids=[]
    for number,member in enumerate(dataset['images'][:2]):
        payload={**legacy,'selection':{'image_ids':[member['image_id']],'labels':['target']}}
        response=client.post('/api/v1/detection-evaluations',json=payload,headers={'Idempotency-Key':f'cohort-{number}'})
        assert response.status_code==202,response.text
        identifier=response.json()['id'];ids.append(identifier)
        run_once(app.state.db,app.state.registry,'cohort-test',job_id=identifier,
            runtime_registry=app.state.runtime_registry,sensor_registry=app.state.sensor_registry,evaluator_registry=app.state.evaluator_registry)
    response=client.get('/api/v1/evaluation-comparisons',params={'baseline_id':ids[0],'candidate_id':ids[1]})
    assert response.status_code==200,response.text
    assert response.json()['status']=='not_comparable'
    assert any(d['field']=='selection' for d in response.json()['differences'])


def test_retry_reuses_only_sealed_success_and_recomputes_evaluations(evaluation_context):
    client,app,plan,attempt,_=start(evaluation_context)
    runtime=evaluation_context[0][2];runtime.failure_at=runtime.calls+2
    prior=finish(client,app,attempt)
    proposed=client.post(f'/api/v1/verification-plans/{plan["id"]}/preview',json={'revision':1,'retry_of':prior['id']}).json()
    assert proposed['workload']['reused_inference_jobs']==1
    assert proposed['workload']['inference_jobs']==1
    response=client.post(f'/api/v1/verification-plans/{plan["id"]}/attempts',json={'revision':1,'retry_of':prior['id'],
        'expected_preview_hash':proposed['preview_hash'],'confirm_trusted_models':True},headers={'Idempotency-Key':'failed-stage-retry'})
    assert response.status_code==202,response.text
    current=finish(client,app,response.json())
    assert current['report']['cells'][0]['verdict']['status']=='passed'
    assert current['inference_job_ids'][1]==prior['inference_job_ids'][1]
    assert current['inference_job_ids'][0]!=prior['inference_job_ids'][0]
    assert current['evaluation_job_ids']!=prior['evaluation_job_ids']
    assert client.get(f'/api/v1/verification-attempts/{prior["id"]}').json()['manifest_hash']==prior['manifest_hash']


def test_missing_labels_take_precedence_over_object_shortfall(evaluation_context):
    from ade2e.verification_plans import _criterion_result
    criterion=definition(evaluation_context)['criterion'];criterion['minimum_objects']=20
    result=_criterion_result(criterion,{'images':3,'reviewed_images':1,'objects':0,'unique_recordings':1},{},[])
    assert result['status']=='label_review_needed'


def test_reserved_cell_and_blank_author_refused(evaluation_context):
    client,*_=evaluation_context[0]
    value=definition(evaluation_context);value['cells'][0]['id']='__overall__'
    assert client.post('/api/v1/verification-plans',json=value,headers={'Idempotency-Key':'reserved'}).status_code==422
    value=definition(evaluation_context);value['author']=' '
    assert client.post('/api/v1/verification-plans',json=value,headers={'Idempotency-Key':'blank'}).status_code==422


def test_all_excluded_class_mapping_is_not_a_supported_verification(evaluation_context):
    client,*_=evaluation_context[0]
    value=definition(evaluation_context)
    for model in value['models']:
        model['class_mapping']=[{**m,'dataset_label':None} for m in model['class_mapping']]
    response=client.post('/api/v1/verification-plans',json=value,headers={'Idempotency-Key':'no-mapped-classes'})
    assert response.status_code==422


@pytest.mark.parametrize('mode',['unknown','conflict','coexisting'])
def test_condition_unknowns_and_conflicts_cannot_pass(evaluation_context,mode):
    import copy
    client,app,*_=evaluation_context[0]
    value=definition(evaluation_context)
    if mode=='unknown':
        value['axes'][0]['values'].append('unknown');value['cells'][0]['conditions']['lighting']='unknown'
    else:
        cell=copy.deepcopy(value['cells'][0]);cell.update(id='other',name='Other assignment',conditions={'lighting':'night'})
        value['cells'].append(cell)
        if mode=='coexisting':value['axes'][0]['cardinality']='multiple'
    response=client.post('/api/v1/verification-plans',json=value,headers={'Idempotency-Key':mode})
    assert response.status_code==201,response.text
    plan=response.json()
    proposed=client.post(f'/api/v1/verification-plans/{plan["id"]}/preview',json={'revision':1}).json()
    expected={'unknown':'condition_review_needed','conflict':'condition_conflict','coexisting':'ready'}[mode]
    assert proposed['cells'][0]['preparation']==expected
    response=client.post(f'/api/v1/verification-plans/{plan["id"]}/attempts',json={'revision':1,
        'expected_preview_hash':proposed['preview_hash'],'confirm_trusted_models':True},headers={'Idempotency-Key':'conditions'})
    result=finish(client,app,response.json())
    cell=result['report']['cells'][1]
    assert cell['verdict']['status']==('passed' if mode=='coexisting' else expected)
    if mode!='coexisting': assert cell['evaluation_ids']==[]


def test_unfinished_parent_blocks_cold_backup_between_job_stages(evaluation_context):
    from ade2e.workspace_backup import backup
    client,app,_,attempt,_=start(evaluation_context)
    for pair in attempt['inference_job_ids']:
        for identifier in pair:
            # Complete only already-published child jobs. Parent advancement waits for next worker pass.
            claim=jobs.claim_job(app.state.db,'gap-worker',job_id=identifier,
                capabilities={('perception',
                    evaluation_context[1].runtime_id,app.state.runtime_registry.get(evaluation_context[1].runtime_id).metadata().version)})
            from ade2e.worker import run_claim
            run_claim(app.state.db,app.state.registry,claim,runtime_registry=app.state.runtime_registry,sensor_registry=app.state.sensor_registry)
    app.state.db.engine.dispose()
    with pytest.raises(ValueError,match='Unfinished'):
        backup(app.state.db.artifact_dir.parent,app.state.db.artifact_dir.parent.parent/'active-plan-backup',offline=True)


def test_suite_and_terminal_campaign_are_pinned_context_not_criteria(evaluation_context):
    from test_suites import make_suite, campaign, finish as finish_campaign
    client,*_=evaluation_context[0]
    suite,*_=make_suite(client,count=1)
    queued=campaign(client,suite,key='verification-context')
    value=definition(evaluation_context)
    value['evidence']=[{'kind':'suite','id':suite['id'],'revision':1,'role':'context'},
        {'kind':'campaign','id':queued['id'],'role':'diagnostic'}]
    response=client.post('/api/v1/verification-plans',json=value,headers={'Idempotency-Key':'context'})
    assert response.status_code==422,response.text
    completed=finish_campaign(client,queued)
    assert completed['status']=='finished'
    response=client.post('/api/v1/verification-plans',json=value,headers={'Idempotency-Key':'context'})
    assert response.status_code==201,response.text
    snapshots=response.json()['snapshots']['evidence']
    assert snapshots[0]['snapshot']==suite
    assert {k:v for k,v in snapshots[1]['snapshot'].items() if k!='evaluated_at'}=={k:v for k,v in completed.items() if k!='evaluated_at'}
    assert all(e['satisfies_criteria'] is False for e in snapshots)


def test_completed_plan_backup_restore_preserves_exact_sealed_manifests(evaluation_context):
    from ade2e.workspace_backup import backup, restore
    from ade2e.database import Database, Settings
    from ade2e.verification_plans import plan_detail, _attempt_detail
    client,app,plan,attempt,_=start(evaluation_context)
    result=finish(client,app,attempt)
    original_plan=client.get(plan['download_url']).content
    original_attempt=client.get(result['download_url']).content
    source=app.state.db.artifact_dir.parent
    app.state.db.engine.dispose()
    bundle=source.parent/'completed-plan-backup';target=source.parent/'restored-plan'
    assert backup(source,bundle,offline=True)['database']['schema_version']==SCHEMA_VERSION
    assert restore(bundle,target)['services_started'] is False
    db=Database(Settings(data_dir=target,seed_examples=False))
    db.initialize()
    with db.session() as session:
        assert plan_detail(session,plan['id'],1)['manifest_hash']==hashlib.sha256(original_plan).hexdigest()
        restored=session.get(VerificationAttemptRow,attempt['id'])
        assert restored.manifest==original_attempt
        assert _attempt_detail(restored)['manifest_hash']==result['manifest_hash']
    db.engine.dispose()


def test_cancel_does_not_reseal_a_tampered_receipt(evaluation_context):
    client,app,_,attempt,_=start(evaluation_context)
    with jobs._write(app.state.db) as session:
        row=session.get(VerificationAttemptRow,attempt['id'])
        row.payload={**row.payload,'plan_hash':'0'*64}
    assert client.post(f'/api/v1/verification-attempts/{attempt["id"]}/cancel').status_code==409


def test_pilot_checks_unfinished_parent_capabilities_even_without_pending_children(evaluation_context,monkeypatch):
    from ade2e import local_pilot as pilot
    from ade2e.service import digest
    client,app,_,attempt,_=start(evaluation_context)
    result=finish(client,app,attempt)
    with jobs._write(app.state.db) as session:
        row=session.get(VerificationAttemptRow,attempt['id'])
        row.manifest=None;row.manifest_hash=None
        row.payload={**row.payload,'status':'running','stage':'inference'}
        row.payload_hash=digest(row.payload)
    source=app.state.db.artifact_dir.parent
    app.state.db.engine.dispose()
    before={p.name:pilot.file_hash(p) for p in source.glob('platform.db*')}
    with pytest.raises(ValueError,match='Unfinished verification plan needs'):
        pilot.offline_data_check(source)
    assert {p.name:pilot.file_hash(p) for p in source.glob('platform.db*')}==before
    monkeypatch.setattr(pilot,'explicit_registries',lambda:{
        'registry':app.state.registry,'runtime_registry':app.state.runtime_registry,'evaluator_registry':app.state.evaluator_registry})
    assert pilot.offline_data_check(source)['unfinished_verification_attempts']==1
