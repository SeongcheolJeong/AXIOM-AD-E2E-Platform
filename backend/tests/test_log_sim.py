"""Bounded workflow contracts use authored fixtures, not native simulator proof."""
import copy
import math
import pytest
from fastapi import APIRouter, Depends, Request
from ade2e import log_sim
from ade2e.log_sim import register_log_sim_routes, carla_planning_frames
from ade2e.team_access import authenticate_request
from ade2e.database import Settings
from test_object_demo import env, interaction_scenario, save, execute, bind


@pytest.fixture
def log_env(env):
    db,client,handler,registry,scenario=env
    def auth(request:Request):
        authenticate_request(request,db,Settings(api_token='test-bootstrap',team_mode=True,seed_examples=False),request.headers.get('authorization','')[7:])
    api=APIRouter(prefix='/api/v1',dependencies=[Depends(auth)])
    log_sim.cases.register_case_routes(api,db)
    register_log_sim_routes(api,db,registry);client.app.include_router(api)
    urban=interaction_scenario(client,scenario);ref=save(client,urban,'log-baseline')
    job=execute(env,ref,'log-source')
    response=bind(client,ref,'urban_right_turn',actor_roles={'ego':'ego','lead':'lead'},saved_execution_id=job)
    assert response.status_code==200,response.text
    return env,ref,job


def test_source_preview_submit_idempotence_and_bounds(log_env):
    env,ref,baseline=log_env;db,client,_,_,_=env
    source=client.get('/api/v1/log-sim/sources').json()['items'][0]
    assert source['ready'] and source['defaults']['lead_wait_s']==6
    body={'source_id':source['id'],'lead_wait_s':2}
    preview=client.post('/api/v1/log-sim/variants/preview',json=body)
    assert preview.status_code==200,preview.text
    p=preview.json();assert len(p['changes'])==1 and p['changes'][0]['path'].endswith('/duration_s')
    assert p['baseline_execution_id']==baseline and p['execution_status']=='not_executed'
    wrong=client.post('/api/v1/log-sim/variants',json=body|{'preview_hash':'0'*64},headers={'Idempotency-Key':'wrong'})
    assert wrong.status_code==409
    payload=body|{'preview_hash':p['preview_hash']}
    result=client.post('/api/v1/log-sim/variants',json=payload,headers={'Idempotency-Key':'variant1'})
    assert result.status_code==202,result.text
    value=result.json();assert value['execution']['status']=='queued' and value['baseline_execution_id']==baseline
    assert client.post('/api/v1/log-sim/variants',json=payload,headers={'Idempotency-Key':'variant1'}).json()==value
    assert client.get('/api/v1/log-sim/variants').json()['items'][0]['execution_id']==value['execution_id']
    assert client.post('/api/v1/log-sim/variants',json=payload|{'lead_wait_s':3},headers={'Idempotency-Key':'variant1'}).status_code==409
    assert client.post('/api/v1/log-sim/variants/preview',json=body|{'lead_wait_s':7}).status_code==422
    assert client.post('/api/v1/log-sim/variants/preview',json=body|{'case_id':'foreign','case_revision':1}).status_code==404


def trace():
    actors=[dict(id='ego',role='ego',bbox_center={'x':1.,'y':.5},dimensions_m={'length_m':4.,'width_m':2.}),
            dict(id='lead',role='other',bbox_center={'x':0.,'y':0.},dimensions_m={'length_m':4.,'width_m':2.})]
    return dict(coordinate_system='carla_world_m_x_forward_y_right_z_up',actors=actors,
        frames=[dict(time_s=t,actors=[dict(id='ego',x=10.,y=20.,heading_rad=math.pi/2,speed_mps=2.,velocity={'x':0.,'y':2.}),
                                    dict(id='lead',x=10.,y=25.,heading_rad=0.,speed_mps=0.,velocity={'x':0.,'y':0.})]) for t in (.05,.1,.15)])


def test_converter_bbox_clock_and_velocity_reflection():
    frames=carla_planning_frames(trace());ego,objects=frames[0]
    assert ego['timestamp_ns']=='50000000' and ego['x_m']==pytest.approx(9.5) and ego['y_m']==pytest.approx(-21)
    assert ego['yaw_rad']==pytest.approx(-math.pi/2) and ego['speed_mps']==2
    assert objects['coordinate_frame']=='world' and objects['objects'][0]['y_m']==-25


def test_case_conversion_and_owned_lineage(log_env,monkeypatch):
    env,ref,baseline=log_env;db,client,_,_,_=env
    monkeypatch.setattr(log_sim,'_scene',lambda *a:(trace(),[],{'execution_id':baseline,'artifact_sha256':'a'*64}))
    response=client.post('/api/v1/log-sim/sources/carla-urban-right-turn/cases',json={
        'title':'Observed wait','note':'Authored test trace only','start_time_s':0.,'end_time_s':.15},headers={'Idempotency-Key':'case1'})
    assert response.status_code==201,response.text
    case=response.json();assert case['segment']['sample_count']==3 and case['segment']['source_kind']=='simulation'
    assert client.get('/api/v1/log-sim/sources/carla-urban-right-turn/cases/'+case['id']).json()['manifest_hash']==case['manifest_hash']
    preview=client.post('/api/v1/log-sim/variants/preview',json={'source_id':'carla-urban-right-turn','lead_wait_s':2,
        'case_id':case['id'],'case_revision':1})
    assert preview.status_code==200,preview.text
    assert preview.json()['case_ref']['manifest_hash']==case['manifest_hash']


def test_engineer_case_facade_keeps_project_and_legacy_boundaries(log_env,monkeypatch):
    env,ref,baseline=log_env;db,client,_,_,_=env
    monkeypatch.setattr(log_sim,'_scene',lambda *a:(trace(),[],{'execution_id':baseline,'artifact_sha256':'a'*64}))
    other=client.post('/api/v1/projects',json={'name':'Other'}).json()['id']
    def token(project,role):
        return client.post(f'/api/v1/projects/{project}/tokens',json={'user_label':'Demo','role':role}).json()['token']
    engineer=token('default','Engineer');foreign=token(other,'Engineer');viewer=token('default','Viewer')
    headers={'Authorization':'Bearer '+engineer,'Idempotency-Key':'engineer-case'}
    body={'title':'Wait','note':'Authored test','start_time_s':0.,'end_time_s':.15}
    response=client.post('/api/v1/log-sim/sources/carla-urban-right-turn/cases',json=body,headers=headers)
    assert response.status_code==201,response.text
    case=response.json();url='/api/v1/log-sim/sources/carla-urban-right-turn/cases/'+case['id']
    assert client.get(url,headers=headers).status_code==200
    assert client.get('/api/v1/cases/'+case['id'],headers=headers).status_code==403
    assert client.get(url,headers={'Authorization':'Bearer '+foreign}).status_code==404
    assert client.post('/api/v1/log-sim/sources/carla-urban-right-turn/cases',json=body,headers={
        'Authorization':'Bearer '+viewer,'Idempotency-Key':'viewer-case'}).status_code==403
    payload={'source_id':'carla-urban-right-turn','lead_wait_s':2,'case_id':case['id'],'case_revision':1}
    assert client.post('/api/v1/log-sim/variants/preview',json=payload,headers=headers).status_code==200
    assert client.post('/api/v1/log-sim/variants/preview',json=payload|{'case_id':'case_foreign'},headers=headers).status_code==404
