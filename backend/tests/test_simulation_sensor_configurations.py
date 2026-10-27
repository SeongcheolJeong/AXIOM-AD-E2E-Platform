"""Isolated configuration/API/queue contracts; authored adapters, no CARLA run."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select

from ade2e import jobs, service
from ade2e.job_models import JobRow
from ade2e.simulation_context import (SimulationControlSelectionV2, PreparedSimulationContextV2,
    validate_prepared_context, make_simulation_execution_request, validate_simulation_execution_request,
    sensor_profile_for_seed)
from ade2e.simulation_sensor_schemas import SensorConfigurationCreate, CarlaLidarParameters
from ade2e.simulation_sensor_configurations import save_sensor_configuration
from ade2e.simulation_sensor_capabilities import simulation_sensor_capabilities, estimate_full_execution_evidence
from ade2e.simulation_sensor_models import SensorConfigurationRevisionRow
from ade2e.schemas import RunCreate
from test_simulation_context import context

BASE = '/api/v1/simulation-sensor-configurations'


def definition(**parameters):
    return {'name':'Custom ray cast', 'note':'Authored uncalibrated fixture', 'parameters':parameters}


def create(client, **parameters):
    result=client.post(BASE,json=definition(**parameters),headers={'Idempotency-Key':'config-one'})
    assert result.status_code==201,result.text
    return result.json()


@pytest.fixture
def configured(context, monkeypatch):
    client, db, registry, body, adapter, *_ = context
    metadata = adapter.metadata().model_copy(update={'version':'0.8.0'})
    monkeypatch.setattr(adapter,'metadata',lambda:metadata)
    monkeypatch.setattr(adapter,'execution_context_capabilities',simulation_sensor_capabilities,raising=False)
    config=create(client, horizontal_fov=180.0, upper_fov=20.0, lower_fov=-55.0, points_per_second=40000)
    control=SimulationControlSelectionV2(**body.control.model_dump(mode='json',exclude={'sensor_profile_id'}),
        schema_version='simulation-control-selection/2',sensor_configuration_id=config['id'],sensor_configuration_revision=1)
    return context, body.model_copy(update={'control':control}), config


def test_required_key_full_recipe_revision_replay_export(client):
    assert client.post(BASE,json=definition()).status_code==422
    assert client.post(BASE,json=definition(),headers={'Idempotency-Key':' '}).status_code==422
    saved=create(client)
    assert len(saved['parameters'])==13 and 'noise_seed' not in saved['parameters']
    content=client.get(saved['download_url']).content
    assert hashlib.sha256(content).hexdigest()==saved['manifest_hash'] and len(content)==saved['manifest_size_bytes']
    revision={**definition(range=100.),'expected_revision':1}
    second=client.post(f"{BASE}/{saved['id']}/revisions",json=revision,headers={'Idempotency-Key':'config-one'})
    assert second.status_code==201
    assert second.json()['revision']==2
    assert client.post(BASE,json=definition(),headers={'Idempotency-Key':'config-one'}).json()==saved
    assert client.post(f"{BASE}/{saved['id']}/revisions",json=revision,headers={'Idempotency-Key':'config-one'}).json()==second.json()
    assert client.post(f"{BASE}/{saved['id']}/revisions",json=revision,headers={'Idempotency-Key':'different'}).status_code==409
    assert client.post(BASE,json=definition(range=100.),headers={'Idempotency-Key':'config-one'}).status_code==409
    assert client.get(saved['download_url']).content==content
    assert [r['revision'] for r in client.get(f"{BASE}/{saved['id']}/revisions").json()]==[2,1]
    assert client.get(BASE).json()==[second.json()]


@pytest.mark.parametrize('changes',[{'channels':True},{'channels':'16'},{'range':True},{'noise_seed':42},
    {'sensor_tick':0.05},{'rotation_frequency':10},{'channels':128,'points_per_second':20},
    {'lower_fov':20.,'upper_fov':10.},{'points_per_second':400000,'channels':127},
    {'range':201.},{'noise_stddev':2.},{'horizontal_fov':0.}])
def test_bounds_reject_before_registry_write(client,changes):
    response=client.post(BASE,json=definition(**changes),headers={'Idempotency-Key':'invalid'})
    assert response.status_code==422,response.text
    assert client.get(BASE).json()==[]


def test_concurrent_duplicate_publishes_one_revision(client):
    body=SensorConfigurationCreate.model_validate(definition())
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(lambda _:save_sensor_configuration(client.app.state.db,body,'shared'),range(6)))
    assert len({item.id for item,_ in results})==1
    assert sum(created for _,created in results)==1
    assert len(client.get(BASE).json())==1


def test_commit_failure_rolls_back_manifest_head_and_key(client):
    db=client.app.state.db
    def fail(_): raise RuntimeError('publication rejected')
    event.listen(db.engine,'commit',fail)
    try:
        with pytest.raises(RuntimeError,match='publication rejected'):
            save_sensor_configuration(db,SensorConfigurationCreate.model_validate(definition()),'retry')
    finally:event.remove(db.engine,'commit',fail)
    assert client.get(BASE).json()==[]
    assert save_sensor_configuration(db,SensorConfigurationCreate.model_validate(definition()),'retry')[1]


@pytest.mark.parametrize('mutation',['hash','metadata','definition','malformed'])
def test_corruption_fails_closed_all_read_paths(client,mutation):
    saved=create(client);db=client.app.state.db
    with db.session() as session:
        row=session.get(SensorConfigurationRevisionRow,(saved['id'],1))
        if mutation=='hash':row.manifest_hash='0'*64
        elif mutation=='metadata':row.created_at='changed'
        elif mutation=='malformed':row.manifest=b'not json'
        else:
            value=json.loads(row.manifest);value['parameters']['range']=90.
            row.manifest=service.canonical_bytes(value);row.manifest_hash=hashlib.sha256(row.manifest).hexdigest();row.manifest_size_bytes=len(row.manifest)
        session.commit()
    for path in (BASE,f"{BASE}/{saved['id']}",f"{BASE}/{saved['id']}/revisions",saved['download_url']):
        assert client.get(path).status_code==409,path


def test_context2_pins_config_and_request_versions_without_altering_legacy(configured):
    ctx,body,config=configured;client,db,registry=ctx[:3]
    job=jobs.submit_job(db,registry,body,'ctx2')
    snapshot=job.execution_context
    assert isinstance(snapshot,PreparedSimulationContextV2)
    assert snapshot.sensor_profile.attributes['horizontal_fov']=='180.0'
    assert snapshot.sensor_profile.max_points==snapshot.sensor_profile.max_points_per_frame==20000
    with db.read_session() as session:
        row=session.scalar(select(JobRow).where(JobRow.id==job.id));payload=deepcopy(row.prepared)
    assert payload['schema_version']=='prepared-simulation/3'
    prepared=service.PreparedRun.from_payload(payload)
    assert prepared.input_hash==job.input_hash
    assert prepared.to_payload()==payload
    request=make_simulation_execution_request(execution_id='fixed',input_hash=job.input_hash,scenario=job.scenario_snapshot,context=snapshot)
    assert request.schema_version=='simulation-execution-request/2'
    assert validate_simulation_execution_request(request.model_dump(mode='json'))==request
    assert service.execution_attempt_request(prepared,'fixed').schema_version=='execution-attempt/2'
    client.post(f"{BASE}/{config['id']}/revisions",json={**definition(range=99.),'expected_revision':1},headers={'Idempotency-Key':'new'}).raise_for_status()
    assert jobs.submit_job(db,registry,body,'ctx2').execution_context==snapshot
    assert service.PreparedRun.from_payload(payload).execution_context==snapshot
    assert sensor_profile_for_seed(42).profile_hash=='8acf977bad61ec1231dd50c12192a97f8e2b8b3dc07486c888aa0445b99a2d22'


@pytest.mark.parametrize('kind',['missing','changed','duplicate','version'])
def test_unsupported_capability_rejects_without_jobs(configured,monkeypatch,kind):
    ctx,body,_=configured;adapter=ctx[4]
    if kind=='missing':monkeypatch.setattr(adapter,'execution_context_capabilities',None)
    elif kind=='version':monkeypatch.setattr(adapter,'metadata',lambda:ctx[4].__class__.metadata(adapter).model_copy(update={'version':'0.7.0'}))
    else:
        caps=simulation_sensor_capabilities()
        if kind=='changed':caps[0].limits['max_points_per_frame']=999
        else:caps*=2
        monkeypatch.setattr(adapter,'execution_context_capabilities',lambda:caps)
    with pytest.raises(HTTPException) as failure:jobs.submit_job(ctx[1],ctx[2],body,'rejected')
    assert failure.value.status_code==422
    with ctx[1].read_session() as session:assert list(session.scalars(select(JobRow)))==[]


def test_capability_drift_is_rechecked_before_dispatch(configured,monkeypatch):
    ctx,body,_=configured
    job=jobs.submit_job(ctx[1],ctx[2],body,'later-drift')
    with ctx[1].read_session() as session:
        prepared=service.PreparedRun.from_payload(session.scalar(select(JobRow).where(JobRow.id==job.id)).prepared)
    monkeypatch.setattr(ctx[4],'execution_context_capabilities',lambda:[])
    with pytest.raises(HTTPException,match='capability'):service.validate_prepared_execution(prepared,ctx[4])
    assert ctx[4].preflights==[]


def test_full_run_budget_rejects_full_return_dense_despite_legal_single_frame(configured):
    ctx,body,config=configured;client=ctx[0]
    revision=client.post(f"{BASE}/{config['id']}/revisions",json={**definition(channels=64,points_per_second=256000,lower_fov=-65.),'expected_revision':1},headers={'Idempotency-Key':'large'}).json()
    body=body.model_copy(update={'control':body.control.model_copy(update={'sensor_configuration_revision':2})})
    client.patch(f'/api/v1/scenarios/{body.scenario_id}',json={'duration_s':4}).raise_for_status()
    with pytest.raises(HTTPException,match='budget'):jobs.submit_job(ctx[1],ctx[2],body,'overbudget')
    with ctx[1].read_session() as session:assert list(session.scalars(select(JobRow)))==[]


@pytest.mark.parametrize('change',['profile','mount','capability','config','request_version','prepared_version'])
def test_nested_tamper_and_wrong_envelope_fail(configured,change):
    ctx,body,_=configured
    job=jobs.submit_job(ctx[1],ctx[2],body,'tamper')
    value=job.execution_context.model_dump(mode='json')
    if change=='request_version':
        with pytest.raises(ValueError):validate_simulation_execution_request({'schema_version':'simulation-execution-request/1','execution_id':'a','input_hash':job.input_hash,'scenario':job.scenario_snapshot.model_dump(mode='json'),'context':value})
        return
    if change=='prepared_version':
        with ctx[1].read_session() as session:p=deepcopy(session.scalar(select(JobRow).where(JobRow.id==job.id)).prepared)
        p['schema_version']='prepared-simulation/2'
        with pytest.raises(ValueError):service.PreparedRun.from_payload(p)
        return
    if change=='profile':value['sensor_profile']['attributes']['range']='90.0'
    elif change=='mount':value['sensor_profile']['mount']['translation_m'][0]=1
    elif change=='capability':value['sensor_capability_snapshot']['supported_adapter_versions'].append('0.7.0')
    else:value['sensor_configuration_snapshot']['parameters']['range']=99
    value['context_hash']=service.digest({k:v for k,v in value.items() if k!='context_hash'})
    with pytest.raises(ValueError):validate_prepared_context(value)


def test_discovery_and_header_metadata(client):
    caps=client.get('/api/v1/simulation-sensor-capabilities?adapter_id=carla').json()
    assert len(caps)==1 and caps[0]['default_parameters']==CarlaLidarParameters().model_dump(mode='json')
    assert client.get('/api/v1/simulation-sensor-capabilities?adapter_id=reference').json()==[]
    tools=client.get('/api/v1/agent/tools').json()['tools']
    selected=[item for item in tools if item['path'].startswith(BASE) and item['method']=='POST']
    assert len(selected)==2 and all(item['required_headers']==['Idempotency-Key'] for item in selected)


def test_failed_dispatched_execution_preserves_context2_without_a_successful_run(configured):
    from ade2e.adapters import AdapterExecutionError
    from ade2e.worker import run_once
    ctx,body,_=configured
    def fail(_):raise AdapterExecutionError('Authored failure after request reception')
    ctx[4].on_execute=fail
    job=jobs.submit_job(ctx[1],ctx[2],body,'failed-execution')
    terminal=run_once(ctx[1],ctx[2],'configured-worker',job_id=job.id)
    assert terminal.status=='failed' and len(ctx[4].requests)==1
    assert ctx[4].requests[0].schema_version=='simulation-execution-request/2'
    with ctx[1].read_session() as session:run=service.run_get(session,terminal.run_id)
    assert run.status=='error' and run.metrics==[] and run.trajectory==[]
    assert run.execution_context==job.execution_context
    assert run.provenance.input_hash==job.input_hash


def test_cancel_before_dispatch_retains_pinned_configuration_without_engine_call(configured):
    ctx,body,_=configured
    job=jobs.submit_job(ctx[1],ctx[2],body,'cancel-configured')
    cancelled=jobs.cancel_job(ctx[1],job.id)
    assert cancelled.status=='cancelled' and cancelled.execution_context==job.execution_context
    assert ctx[4].requests==[]


def test_authentication_matches_existing_local_data_routes(settings):
    from fastapi.testclient import TestClient
    from ade2e.main import create_app
    from ade2e.adapters import Registry
    settings.api_token='authored-secret'
    with TestClient(create_app(settings,Registry(load_plugins=False))) as client:
        assert client.get('/api/v1/simulation-sensor-capabilities').status_code==401
        assert client.post(BASE,json=definition(),headers={'Idempotency-Key':'auth'}).status_code==401
        headers={'Authorization':'Bearer authored-secret','Idempotency-Key':'auth'}
        saved=client.post(BASE,json=definition(),headers=headers)
        assert saved.status_code==201
        assert client.get(saved.json()['download_url']).status_code==401
        assert client.get(saved.json()['download_url'],headers=headers).status_code==200
