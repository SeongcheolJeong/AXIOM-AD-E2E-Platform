"""Authored input contracts with real CPU reference jobs; no native/sensor claims."""
import io
import json
import pytest
from fastapi import APIRouter, Depends, Request
from mcap.reader import make_reader
from mcap.writer import Writer
from ade2e import log_sim, log_sim_beta as beta, log_sim_beta_packs as packs, log_sim_batches as batches, log_sim_replays as replay
from ade2e.log_sim_library import register_log_sim_library_routes
from ade2e.database import Settings
from ade2e.team_access import authenticate_request
from ade2e.planning_replay import PlanningReplayHandler
from ade2e.worker import run_once
from ade2e.adapters import Registry
from test_object_demo import env

@pytest.fixture
def beta_env(env):
    db,client,_,registry,_=env;registry.register(PlanningReplayHandler(db))
    def auth(request:Request):authenticate_request(request,db,Settings(api_token='test-bootstrap',team_mode=True,seed_examples=False),request.headers.get('authorization','')[7:])
    api=APIRouter(prefix='/api/v1',dependencies=[Depends(auth)])
    log_sim.register_log_sim_routes(api,db,registry);replay.register_log_sim_replay_routes(api,db,registry)
    batches.register_log_sim_batch_routes(api,db,registry);register_log_sim_library_routes(api,db)
    beta.register_log_sim_beta_routes(api,db);packs.register_log_sim_pack_routes(api,db)
    from ade2e.recordings import register_recording_routes
    from threading import RLock
    register_recording_routes(api,db,RLock())
    client.app.include_router(api)
    project=client.post('/api/v1/projects',json={'name':'Log beta fixture'}).json()['id']
    token=client.post('/api/v1/projects/'+project+'/tokens',json={'user_label':'Beta tester','role':'Engineer'}).json()['token']
    client.headers['Authorization']='Bearer '+token
    return db,client,registry,project


def upload(client,project,invalid=False):
    r=client.post('/api/v1/log-sim/uploads',params={'project_id':project,'name':'Example.mcap'},content=packs.example_mcap(invalid),headers={'Content-Type':'application/octet-stream'})
    assert r.status_code==201,r.text
    return r.json()


def selection(project,recording):
    return dict(project_id=project,recording_id=recording['id'],title='User interval',ego_channel_id=1,objects_channel_id=2,first_sample=0,sample_count=12)


def register(client,body):
    p=client.post('/api/v1/log-sim/inputs/inspect',json=body);assert p.status_code==200,p.text
    r=client.post('/api/v1/log-sim/inputs',json=body|{'preview_hash':p.json()['preview_hash']});assert r.status_code==201,r.text
    return r.json()


def test_owned_upload_inspect_edit_execute_review_export(beta_env):
    db,c,registry,project=beta_env
    r=upload(c,project);assert upload(c,project)['id']==r['id']
    assert c.get('/api/v1/recordings/'+r['id']).status_code==403
    body=selection(project,r);source=register(c,body)
    assert register(c,body)==source
    library=c.get('/api/v1/log-sim/library',params={'project_id':project}).json()
    assert library['counts']['available_frame_count']==12
    s=c.get('/api/v1/log-sim/sources/'+source['id'],params={'project_id':project}).json()
    assert s['kind']=='recorded' and len(s['road_frames'])==12 and 'image_url' not in s['road_frames'][0]
    knobs=dict(project_id=project,name='Edited headway',baseline={'time_headway_s':1.5},candidate={'time_headway_s':4.})
    recipe=c.post('/api/v1/log-sim/comparison-settings',json=knobs,headers={'Idempotency-Key':'settings'});assert recipe.status_code==201,recipe.text
    v=recipe.json();ref={k:v[k] for k in ('id','revision','sha256')}
    assert c.post('/api/v1/log-sim/comparison-settings',json=knobs,headers={'Idempotency-Key':'settings'}).json()==v
    b=dict(project_id=project,source_ids=[source['id']],comparison_ref=ref)
    preview=c.post('/api/v1/log-sim/batches/preview',json=b);assert preview.status_code==200,preview.text
    assert preview.json()['profiles']['candidate']['controller']['time_headway_s']==4
    response=c.post('/api/v1/log-sim/batches',json=b|{'preview_hash':preview.json()['preview_hash']},headers={'Idempotency-Key':'batch'});assert response.status_code==202,response.text
    batch=response.json()
    for job in batch['sources'][0]['execution_ids'].values():assert run_once(db,Registry(),'beta-reference',job_id=job,experiment_registry=registry)
    result=c.get('/api/v1/log-sim/batches/'+batch['id'],params={'project_id':project}).json()
    assert result['status']=='completed' and result['candidates']
    bundle=c.post('/api/v1/log-sim/batches/'+batch['id']+'/case-bundles',json=dict(project_id=project,name='Investigate',report_hash=result['report_hash'],candidate_ids=[result['candidates'][0]['id']]),headers={'Idempotency-Key':'bundle'})
    assert bundle.status_code==201,bundle.text
    case=bundle.json()['items'][0]['case_id']
    review=dict(project_id=project,source_id=source['id'],case_id=case,note='Request difference, not an applied brake',tags=['follow'],status='investigate',time_s=1.)
    saved=c.post('/api/v1/log-sim/reviews',json=review,headers={'Idempotency-Key':'review'});assert saved.status_code==201,saved.text
    assert saved.json()['verdict']=='not_evaluated' and saved.json()['case_ref']['comparison_id']
    assert c.post('/api/v1/log-sim/reviews',json=review,headers={'Idempotency-Key':'review'}).json()==saved.json()
    assert c.post('/api/v1/log-sim/reviews',json=review|{'note':'stale'},headers={'Idempotency-Key':'stale'}).status_code==409
    assert c.get('/api/v1/log-sim/reviews',params={'project_id':project,'case_id':case}).json()['items'][0]['note']==review['note']
    exported=c.get('/api/v1/log-sim/batches/'+batch['id']+'/export',params={'project_id':project,'format':'csv'})
    assert exported.status_code==200 and 'not_evaluated' in exported.text


def test_input_errors_stale_preview_and_project_boundary(beta_env):
    _,c,_,project=beta_env
    bad=upload(c,project,True)
    assert c.post('/api/v1/log-sim/inputs/inspect',json=selection(project,bad)).status_code==422
    good=upload(c,project);body=selection(project,good)
    assert c.post('/api/v1/log-sim/inputs/inspect',json=body|{'ego_channel_id':2}).status_code==422
    p=c.post('/api/v1/log-sim/inputs/inspect',json=body).json()
    assert c.post('/api/v1/log-sim/inputs',json=body|{'sample_count':11,'preview_hash':p['preview_hash']}).status_code==409
    assert c.post('/api/v1/log-sim/inputs/inspect',json=body|{'project_id':'default'}).status_code==404
    assert c.post('/api/v1/log-sim/inputs/inspect',json=body|{'recording_id':'rec_foreign'}).status_code==404
    assert c.post('/api/v1/log-sim/inputs/inspect',json=body|{'sample_count':513}).status_code==422
    register(c,body)
    overlap=body|{'first_sample':1,'sample_count':10}
    p=c.post('/api/v1/log-sim/inputs/inspect',json=overlap).json()
    assert c.post('/api/v1/log-sim/inputs',json=overlap|{'preview_hash':p['preview_hash']}).status_code==409


def test_settings_revision_pin_and_viewer_writes(beta_env):
    _,c,_,project=beta_env
    body=dict(project_id=project,name='v1')
    v=c.post('/api/v1/log-sim/comparison-settings',json=body,headers={'Idempotency-Key':'v1'}).json()
    v2=c.post('/api/v1/log-sim/comparison-settings',json=body|{'id':v['id'],'expected_revision':1,'candidate':{'time_headway_s':5}},headers={'Idempotency-Key':'v2'})
    assert v2.status_code==201 and v2.json()['revision']==2
    source=register(c,selection(project,upload(c,project)))
    p=c.post('/api/v1/log-sim/batches/preview',json={'project_id':project,'source_ids':[source['id']],'comparison_ref':{k:v[k] for k in ('id','revision','sha256')}})
    assert p.status_code==200 and p.json()['profiles']['candidate']['controller']['time_headway_s']==3
    token=c.post('/api/v1/projects/'+project+'/tokens',json={'user_label':'Reader','role':'Viewer'},headers={'Authorization':'Bearer test-bootstrap'}).json()['token']
    c.headers['Authorization']='Bearer '+token
    assert c.post('/api/v1/log-sim/comparison-settings',json=body,headers={'Idempotency-Key':'forbidden'}).status_code==403
    assert c.post('/api/v1/log-sim/uploads',params={'project_id':project},content=packs.example_mcap(),headers={'Content-Type':'application/octet-stream'}).status_code==403
    assert c.get('/api/v1/log-sim/library',params={'project_id':project}).status_code==200
