"""Authored offline fixtures verify evidence/authorization policy, not native sensors."""
import json
from urllib.parse import parse_qs
import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from ade2e.database import Settings
from ade2e.demo_cases import register_demo_case_routes, _neural, _lead_wait, _object
from ade2e.demo_case_schemas import DemoCaseCuration
from ade2e.experiment_models import ExperimentExecutionRow, ExperimentArtifactRow
from ade2e.team_access import authenticate_request
from test_sensor_demo import env as sensor_env, bind, submit
from test_hil_demo import env as hil_env, bind as hil_bind, execute as hil_execute


def gallery(db, path=None, token='test-bootstrap'):
    settings = Settings(data_dir=db.experiment_dir.parent, api_token='test-bootstrap', team_mode=True, seed_examples=False)
    def authenticate(request: Request):
        header = request.headers.get('authorization', '')
        authenticate_request(request, db, settings, header[7:] if header.startswith('Bearer ') else None)
    router = APIRouter(prefix='/api/v1', dependencies=[Depends(authenticate)])
    register_demo_case_routes(router, db, curation_path=path)
    app = FastAPI(); app.include_router(router)
    return TestClient(app, headers={'Authorization': 'Bearer '+token})


def pin(client, identity, role='result'):
    detail = client.get('/api/v1/experiments/executions/'+identity).json()
    return dict(role=role, **{key: detail[key] for key in ('id', 'input_hash', 'manifest_hash')})


def manifest(tmp_path, cases, project='default'):
    path = tmp_path/'curation.json'
    path.write_text(json.dumps(dict(schema_version='demo-case-curation/1', projects=[dict(project_id=project, cases=cases)])))
    return path


def item(client, identity, project='default'):
    response = client.get('/api/v1/demo-cases', params={'project_id': project})
    assert response.status_code == 200, response.text
    return next(value for value in response.json()['items'] if value['id'] == identity)


def captured(sensor_env, preset='lidar_density', key='capture'):
    assert bind(sensor_env[1], preset).status_code == 200
    return submit(sensor_env, preset, key=key)


def test_ten_unavailable_templates_no_adapter_probe_or_execution(sensor_env):
    db, _, handler, _ = sensor_env
    result = gallery(db).get('/api/v1/demo-cases').json()
    assert result['schema_version'] == 'demo-cases/1'
    assert result['counts'] == dict(templates=10, ready=0, unavailable=10, unique_executions=0, source_families=0)
    assert all(value['reason'] and value['primary_link'] is None for value in result['items'])
    assert handler.probes == 0


def test_explicit_binding_reused_offline_and_missing_run_not_guessed(sensor_env):
    db, client, handler, registry = sensor_env
    job = captured(sensor_env)
    assert not item(gallery(db), 'sensor_lidar_density')['ready']
    assert bind(client, 'lidar_density', expected_revision=1, saved_execution_id=job).status_code == 200
    probes = handler.probes
    registry._handlers.clear()
    value = item(gallery(db), 'sensor_lidar_density')
    assert value['ready'] and value['binding']['revision'] == 2
    assert value['executions'][0]['id'] == job
    assert handler.probes == probes


def test_pinned_result_note_links_and_group_count(sensor_env, tmp_path):
    db, client, handler, _ = sensor_env
    jobs = [captured(sensor_env, name, name) for name in ['lidar_density', 'radar_range']]
    cases = [dict(id=identity, executions=[pin(client, job)], key_moments=[dict(label='관측 비교', time_s=2)],
                  observation='고정 결과에 대한 조사자 관측 메모입니다.')
             for identity,job in zip(['sensor_lidar_density','sensor_radar_range'], jobs)]
    app = gallery(db, manifest(tmp_path,cases)); before=handler.probes
    result=app.get('/api/v1/demo-cases').json()
    assert result['counts']['ready'] == 2 and result['counts']['unique_executions'] == 2
    assert result['counts']['source_families'] == 1
    value=next(v for v in result['items'] if v['id']=='sensor_lidar_density')
    assert value['observation_source']=='curator_note_for_pinned_results'
    assert 'mode=demo' in value['primary_link']['href'] and 'result='+jobs[0] in value['primary_link']['href']
    assert 'demo_case=sensor_lidar_density' in value['key_moments'][0]['href']
    assert 'time_s=2.0' in value['key_moments'][0]['href']
    assert handler.probes==before


@pytest.mark.parametrize('failure', ['input_hash','manifest_hash','wrong_question','moment_range','missing_file','sealed_corruption'])
def test_invalid_evidence_fails_closed_and_hides_note(sensor_env,tmp_path,failure):
    db,client,_,_=sensor_env; job=captured(sensor_env)
    entry=dict(id='sensor_lidar_density',executions=[pin(client,job)],observation='Must never leak as a verified observation')
    if failure in ['input_hash','manifest_hash']:entry['executions'][0][failure]='a'*64
    elif failure=='wrong_question':entry['id']='sensor_radar_range'
    elif failure=='moment_range':entry['key_moments']=[dict(label='outside',time_s=99)]
    elif failure=='missing_file':
        with db.read_session() as session:
            row=session.scalar(select(ExperimentArtifactRow).where(ExperimentArtifactRow.job_id==job))
            (db.experiment_dir/'artifacts'/row.storage_key).unlink()
    elif failure=='sealed_corruption':
        with db.session() as session:
            row=session.get(ExperimentExecutionRow,job);row.summary={'edited':True};session.commit()
    result=item(gallery(db,manifest(tmp_path,[entry])),entry['id'])
    assert result['status']=='invalid' and not result['ready'] and not result['executions']
    assert result['primary_link'] is None and result['observation_source']=='investigation_guidance'


def test_authorization_and_cross_project_pin_do_not_disclose_results(sensor_env,tmp_path):
    db,client,_,_=sensor_env;job=captured(sensor_env)
    project=client.post('/api/v1/projects',json={'name':'Separate'}).json()['id']
    token=client.post(f'/api/v1/projects/{project}/tokens',json={'user_label':'viewer','role':'Viewer'}).json()['token']
    path=manifest(tmp_path,[dict(id='sensor_lidar_density',executions=[pin(client,job)])],project)
    app=gallery(db,path,token)
    assert app.get('/api/v1/demo-cases?project_id=default').status_code==404
    value=item(app,'sensor_lidar_density',project)
    assert not value['ready'] and value['executions']==[]
    assert app.get('/api/v1/demo-cases?project_id='+project,headers={'Authorization':''}).status_code==401
    assert app.post('/api/v1/demo-cases?project_id='+project,json={}).status_code==405


def test_malformed_curation_does_not_fallback_to_binding(sensor_env,tmp_path):
    db,client,_,_=sensor_env;job=captured(sensor_env)
    assert bind(client,'lidar_density',expected_revision=1,saved_execution_id=job).status_code==200
    path=tmp_path/'bad.json';path.write_text('{')
    assert item(gallery(db,path),'sensor_lidar_density')['status']=='invalid'


@pytest.mark.parametrize('delay_ms', [200, 400])
def test_hil_actual_fault_value_is_required_and_no_preflight(hil_env,tmp_path,delay_ms):
    db,client,handler,_,_=hil_env
    assert hil_bind(hil_env).status_code==200
    job=hil_execute(hil_env,parameters={'delay_ms':delay_ms})
    identity=f'hil_delay_{delay_ms}'
    entry=dict(id=identity,executions=[pin(client,job)],
               key_moments=[dict(label='원본 고장 구간 조사',time_s=3.05)])
    probes=handler.probes
    value=item(gallery(db,manifest(tmp_path,[entry])),identity)
    assert value['ready'] and value['status']=='ready'
    href=value['primary_link']['href']
    assert href.startswith('#hil-sim?')
    expected=dict(project=['default'],demo_case=[identity],preset=['input_delay'],
                  value=[str(delay_ms)],result=[job])
    assert parse_qs(href.split('?',1)[1])==expected
    moment=value['key_moments'][0]
    assert moment['clock']=='simulation' and moment['time_s']==3.05
    assert parse_qs(moment['href'].split('?',1)[1])==expected | dict(time_s=['3.05'])
    assert handler.probes==probes
    entry['id']=f'hil_delay_{400 if delay_ms==200 else 200}'
    mismatched=item(gallery(db,manifest(tmp_path,[entry])),entry['id'])
    assert not mismatched['ready'] and mismatched['status']=='invalid'
    assert mismatched['primary_link'] is None


@pytest.mark.parametrize('document', [
    dict(schema_version='wrong',projects=[]),
    dict(schema_version='demo-case-curation/1',projects=[dict(project_id='p',cases=[]),dict(project_id='p',cases=[])]),
    dict(schema_version='demo-case-curation/1',projects=[],token='forbidden'),
])
def test_manifest_strict_schema(document):
    with pytest.raises(ValueError):DemoCaseCuration.model_validate(document)


@pytest.mark.parametrize('fidelity', ['neural_reconstruction_of_real_road', 'recorded_real_road', 'reference'])
def test_neural_external_collection_rejects_real_road_before_source_lookup(fidelity):
    value=dict(detail=dict(experiment_kind='neural_render',adapter_id='neural-splatfacto',fidelity=fidelity),
               spec=dict(input=dict(mode='camera_sweep')),snapshot={})
    with pytest.raises(ValueError,match='CARLA'):_neural(None,None,'default',value)


@pytest.mark.parametrize('action', [dict(id='lead_wait',kind='wait',duration_s=0),
                                   dict(id='lead_wait',kind='set_speed',duration_s=6)])
def test_zero_baseline_requires_absent_wait_action(action):
    with pytest.raises(ValueError):_lead_wait(dict(actors=[dict(id='lead',behavior=dict(actions=[action]))]))
    assert _lead_wait(dict(actors=[dict(id='lead',behavior=dict(actions=[]))]))==0


def test_log_pair_cannot_hide_changed_wait_trigger(monkeypatch):
    from ade2e import object_demo
    def scenario(duration):
        return dict(duration_s=24,actors=[dict(id='lead',behavior=dict(actions=[dict(id='lead_wait',kind='wait',duration_s=duration)]))])
    pair=dict(compatibility=dict(compatible=True),left=dict(scenario=scenario(6)),right=dict(scenario=scenario(2)),
        changes=[dict(path='/actors/lead/behavior/actions/lead_wait/duration_s',left=6,right=2),
                 dict(path='/actors/lead/behavior/actions/lead_wait/trigger/value',left=2,right=5)])
    monkeypatch.setattr(object_demo,'_comparison',lambda *args:pair)
    detail=dict(id='job',adapter_id='carla-city-object',fidelity='native_carla_object_physics',
        summary=dict(schema_version='carla-city-result/1',execution_status='completed',cleanup_status='confirmed',scene_sha256='a'*64),
        artifacts=[dict(name=name,sha256='a'*64) for name in ['city-scene.json','city-evaluation.json','map.xodr']])
    resolved={side:dict(detail=detail) for side in ['left','right']}
    with pytest.raises(ValueError,match='외의 설정'):_object(None,'default','log_lead_wait',resolved)
    pair['changes'].pop()
    assert _object(None,'default','log_lead_wait',resolved)[1]==24
    detail['artifacts']=[]
    with pytest.raises(ValueError,match='원본 근거'):_object(None,'default','log_lead_wait',resolved)
