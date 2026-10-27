"""Executable authored MCAP checks, distinct from measured road acceptance."""
import copy
import io
import json
from threading import RLock

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.security import HTTPBearer
from fastapi.testclient import TestClient
from mcap.writer import Writer, CompressionType

from ade2e.database import Database, Settings
from ade2e.experiment_schemas import ExperimentCreate
from ade2e.planning_replay import PlanningReplayHandler, run_planning_replay, _collides, _ttc
from ade2e.recording_models import RecordingMessageRow
from ade2e.recordings import register_recording_routes
from ade2e.replay_specs import register_replay_spec_routes
from ade2e.scene_sequences import EGO_SCHEMA, EGO_SCHEMA_NAME, OBJECT_SCHEMA, OBJECT_SCHEMA_NAME
from ade2e.service import digest
from ade2e.team_access import authenticate_request, register_team_routes

BASE = 2**63+123


def ego(at,index=0,**values):
    return {'timestamp_ns':str(at),'frame_id':'map','x_m':float(index),'y_m':0.,'yaw_rad':0.,
        'speed_mps':10.,'length_m':4.,'width_m':2.,'source':'authored source fixture'}|values


def objects(at,*,relative=False,missing_velocity=False):
    obj = {'id':'lead','x_m':10.,'y_m':0.,'yaw_rad':0.,'length_m':4.,'width_m':2.}
    if not missing_velocity:
        obj.update(vx_mps=0.,vy_mps=0.)
    return {'timestamp_ns':str(at),'frame_id':'ego' if relative else 'map',
        'coordinate_frame':'ego' if relative else 'world','velocity_semantics':'absolute_ground_velocity',
        'objects':[obj],'source':'authored object fixture'}


def authored(**values):
    return {'name':'Authored planar reference','project_id':'default','source_type':'authored_reference',
        'world_frame':'map','clock_domain':'authored-clock','note':'Fixture, not measured driving',
        'authored_frames':[{'at_ns':str(BASE+i*100_000_000),'ego':ego(BASE+i*100_000_000,i),
            'objects':objects(BASE+i*100_000_000)} for i in range(4)]}|values


@pytest.fixture
def log_client(tmp_path):
    settings = Settings(data_dir=tmp_path,seed_examples=False,api_token='test-operator')
    db = Database(settings)
    db.initialize()
    app = FastAPI()
    app.state.db = db
    security = HTTPBearer(auto_error=False)
    def auth(request:Request, credentials=Depends(security)):
        return authenticate_request(request,db,settings,credentials.credentials if credentials else None)
    api = APIRouter(prefix='/api/v1',dependencies=[Depends(auth)])
    register_team_routes(api,db)
    register_recording_routes(api,db,RLock())
    register_replay_spec_routes(api,db)
    app.include_router(api)
    with TestClient(app,headers={'Authorization':'Bearer test-operator'}) as client:
        yield client
    db.engine.dispose()


def sequence(client,body=None):
    response = client.post('/api/v1/scene-sequences',json=body or authored())
    assert response.status_code==201,response.text
    return response.json()


def replay(client,seq,mode='planning_closed_loop',**values):
    body = {'name':'Frozen planning replay','project_id':seq['project_id'],'sequence_id':seq['id'],
        'sequence_sha256':seq['sha256'],'mode':mode,'note':'Reference feedback, not safety acceptance'}|values
    response = client.post('/api/v1/replay-specs',json=body)
    assert response.status_code==201,response.text
    return response.json()


def recorded(client,*,relative=False,skip_first=False,stale=False,ego_values=None,object_values=None,times=None):
    output = io.BytesIO()
    writer = Writer(output,compression=CompressionType.ZSTD,enable_crcs=True,enable_data_crcs=True)
    writer.start()
    es = writer.register_schema(EGO_SCHEMA_NAME,'jsonschema',json.dumps(EGO_SCHEMA).encode())
    os = writer.register_schema(OBJECT_SCHEMA_NAME,'jsonschema',json.dumps(OBJECT_SCHEMA).encode())
    ec = writer.register_channel('/ego','json',es)
    oc = writer.register_channel('/tracks','json',os)
    ordinals,n = [],0
    times = times or [BASE+i*100_000_000 for i in range(4)]
    for i,at in enumerate(times):
        if not (skip_first and i==0) and not (stale and i>0):
            writer.add_message(oc,at,json.dumps(objects(at,relative=relative) | (object_values or {})).encode(),at-1)
            n+=1
        writer.add_message(ec,at,json.dumps(ego(at,i,**(ego_values or {}))).encode(),at-2)
        ordinals.append(n)
        n+=1
    writer.finish()
    response = client.post('/api/v1/recordings?name=PlanningFixture&source_kind=synthetic',content=output.getvalue(),
        headers={'Content-Type':'application/octet-stream'})
    assert response.status_code==201,response.text
    recording = response.json()
    return {'name':'Retained authored MCAP','project_id':'default','source_type':'recording','world_frame':'map',
        'clock_domain':'original-log-clock','note':'Authored MCAP contract acceptance only',
        'recording_id':recording['id'],'ego_channel_id':ec,'object_channel_id':oc,'ego_ordinals':ordinals},recording


def test_authored_feedback_has_next_observation_and_repeat_hash(log_client):
    seq = sequence(log_client)
    spec = replay(log_client,seq)
    check = log_client.post('/api/v1/replay-specs/'+spec['id']+'/preflight').json()
    assert check['executable'] and check['source_kind']=='synthetic'
    summary,ticks = run_planning_replay(spec['sequence_snapshot'],spec['request'],seed=42)
    repeat_summary,repeat_ticks = run_planning_replay(spec['sequence_snapshot'],spec['request'],seed=42)
    assert summary==repeat_summary and ticks==repeat_ticks
    assert ticks[0]['control']['acceleration_mps2']<0
    assert ticks[1]['ego']==ticks[0]['next_ego']
    assert ticks[1]['observation_objects'][0]['x_m']==pytest.approx(10-ticks[1]['ego']['x_m'])
    assert ticks[1]['ego']['x_m']!=ticks[1]['logged_ego']['x_m']
    assert summary['verification_verdict']=='diagnostic_not_safety_validation'
    assert summary['observer_counts']['jerk']=={'evaluated':2,'unevaluated':2}


@pytest.mark.parametrize('mode',['original_replay','open_loop'])
def test_original_and_open_loop_do_not_apply_controls(log_client,mode):
    spec = replay(log_client,sequence(log_client),mode)
    summary,ticks = run_planning_replay(spec['sequence_snapshot'],spec['request'])
    assert ticks[1]['ego']['x_m']==ticks[1]['logged_ego']['x_m']
    assert summary['max_trajectory_error_m']==0
    assert (ticks[0]['control'] is None)==(mode=='original_replay')
    if mode=='open_loop':
        assert ticks[0]['control_application']=='predicted_not_applied_open_loop'
        assert ticks[1]['ego']!=ticks[0]['next_ego']


def test_retained_mcap_exact_uint64_clocks_and_relative_world_transform(log_client):
    body,recording = recorded(log_client,relative=True,ego_values={'yaw_rad':1.5707963267948966})
    seq = sequence(log_client,body)
    source = log_client.get('/api/v1/scene-sequences/sources/'+recording['id']).json()
    assert {item['role'] for item in source['channels']}=={'ego','objects'}
    frame = seq['snapshot']['frames'][1]
    assert frame['ego_source']['log_time_ns']==str(BASE+100_000_000)
    assert frame['ego_source']['publish_time_ns']==str(BASE+100_000_000-2)
    assert frame['object_age_ns']=='0'
    spec = replay(log_client,seq)
    _,ticks = run_planning_replay(spec['sequence_snapshot'],spec['request'])
    assert ticks[1]['world_objects'][0]['x_m']==pytest.approx(1.)
    assert ticks[1]['world_objects'][0]['y_m']==pytest.approx(10.)
    assert ticks[1]['world_objects'][0]['yaw_rad']==pytest.approx(1.5707963267948966)


@pytest.mark.parametrize('missing',['first','stale'])
def test_missing_or_stale_objects_no_future_fallback_no_free_space(log_client,missing):
    body,_ = recorded(log_client,skip_first=missing=='first',stale=missing=='stale')
    body['object_max_age_ns']='0'
    seq = sequence(log_client,body)
    spec = replay(log_client,seq)
    preflight = log_client.post('/api/v1/replay-specs/'+spec['id']+'/preflight').json()
    assert not preflight['executable'] and preflight['object_steps_unevaluated']>0
    open_spec = replay(log_client,seq,'open_loop')
    summary,ticks = run_planning_replay(open_spec['sequence_snapshot'],open_spec['request'])
    tick = next(tick for tick in ticks if tick['object_drop'])
    assert tick['control']['decision']=='unavailable_objects_stop'
    assert tick['metrics']['collision']['status']=='unevaluated'
    assert summary['unevaluated_object_steps']>0


def test_missing_original_pose_is_rejected(log_client):
    body,_ = recorded(log_client)
    db = log_client.app.state.db
    with db.session() as session:
        entry = session.get(RecordingMessageRow,(body['recording_id'],body['ego_ordinals'][0]))
        entry.channel_id=body['object_channel_id']
        session.commit()
    response = log_client.post('/api/v1/scene-sequences',json=body)
    assert response.status_code==422 and 'ego ordinal' in response.text


@pytest.mark.parametrize('times',[[BASE,BASE],[BASE+100,BASE],[BASE,BASE+1_000_000_001]])
def test_duplicate_backward_or_oversize_gaps_rejected(log_client,times):
    body,_ = recorded(log_client,times=times)
    assert log_client.post('/api/v1/scene-sequences',json=body).status_code==422


def test_ttc_requires_velocity_and_lane_or_goal_remains_unevaluated(log_client):
    data = authored()
    data['authored_frames'][0]['objects']=objects(BASE,missing_velocity=True)
    spec = replay(log_client,sequence(log_client,data),observers=['ttc','lane_departure','goal_arrival'])
    _,ticks = run_planning_replay(spec['sequence_snapshot'],spec['request'])
    assert ticks[0]['metrics']['ttc']['reason']=='object_velocity_missing'
    assert all(tick['metrics']['lane_departure']['status']=='unevaluated' for tick in ticks)
    assert all(tick['metrics']['goal_arrival']['status']=='unevaluated' for tick in ticks)


def test_orientation_collision_and_rectangle_ttc():
    vehicle = ego(BASE)
    object_body = objects(BASE)['objects'][0]
    assert not _collides(vehicle,object_body)
    assert _ttc(vehicle,object_body)==pytest.approx(.6)
    object_body['y_m']=5.
    assert _ttc(vehicle,object_body)==float('inf')
    object_body.update(x_m=0.,y_m=0.,yaw_rad=1.5707963267948966)
    assert _collides(vehicle,object_body)


def test_handler_artifacts_repeat_cancellation_and_input_integrity(log_client,tmp_path):
    spec = replay(log_client,sequence(log_client))
    handler = PlanningReplayHandler(log_client.app.state.db)
    experiment = ExperimentCreate(name='Planning',kind='planning_replay',adapter_id='reference-planning-replay',
        input={'replay_spec_id':spec['id']},seed=42)
    frozen = handler.prepare(experiment)
    first = handler.execute(frozen,tmp_path/'first',lambda:False)
    second = handler.execute(frozen,tmp_path/'second',lambda:False)
    assert first.summary==second.summary
    assert first.artifacts[0].path.read_bytes()==second.artifacts[0].path.read_bytes()
    from ade2e.adapters import AdapterCancelled
    with pytest.raises(AdapterCancelled):
        handler.execute(frozen,tmp_path/'cancelled',lambda:True)
    changed = copy.deepcopy(frozen)
    changed['replay_spec_sha256']='0'*64
    with pytest.raises(ValueError,match='changed after submission'):
        handler.execute(changed,tmp_path/'changed',lambda:False)


def test_retained_payload_corruption_preflight_blocks(log_client):
    body,recording = recorded(log_client)
    spec = replay(log_client,sequence(log_client,body))
    db = log_client.app.state.db
    from ade2e.recording_models import RecordingRow
    with db.read_session() as session:
        row = session.get(RecordingRow,recording['id'])
        path = db.recording_dir/row.index_storage_key
    content = path.read_bytes()
    path.write_bytes(bytes([content[0]^1])+content[1:])
    result = log_client.post('/api/v1/replay-specs/'+spec['id']+'/preflight').json()
    assert not result['executable'] and 'integrity' in result['blocking_reasons'][0].lower()


def test_original_mcap_bytes_corruption_preflight_blocks(log_client):
    body,recording = recorded(log_client)
    spec = replay(log_client,sequence(log_client,body))
    db = log_client.app.state.db
    from ade2e.recording_models import RecordingRow
    with db.read_session() as session:
        path = db.recording_dir/session.get(RecordingRow,recording['id']).storage_key
    content = path.read_bytes()
    path.write_bytes(content[:-1]+bytes([content[-1]^1]))
    result = log_client.post('/api/v1/replay-specs/'+spec['id']+'/preflight').json()
    assert not result['executable'] and 'original' in result['blocking_reasons'][0].lower()


def test_relative_tracks_without_exact_original_pose_are_rejected(log_client):
    body = authored()
    for frame in body['authored_frames']:
        frame['objects']=objects(int(frame['at_ns'])-1,relative=True)
    response = log_client.post('/api/v1/scene-sequences',json=body)
    assert response.status_code==422 and 'original ego pose at their exact capture timestamp' in response.text


def test_explicit_pose_and_velocity_semantics_are_required(log_client):
    body,_=recorded(log_client,ego_values={'x_m':None})
    response=log_client.post('/api/v1/scene-sequences',json=body)
    assert response.status_code==422 and 'x_m' in response.text
    data=authored()
    del data['authored_frames'][0]['objects']['velocity_semantics']
    assert log_client.post('/api/v1/scene-sequences',json=data).status_code==422


def test_unknown_evaluator_and_camera_policy_are_not_silently_used(log_client):
    spec=replay(log_client,sequence(log_client))
    handler=PlanningReplayHandler(log_client.app.state.db)
    body={'name':'Unsupported substitution','kind':'planning_replay','adapter_id':'reference-planning-replay',
        'input':{'replay_spec_id':spec['id']}}
    with pytest.raises(ValueError,match='evaluator|observers'):
        handler.prepare(ExperimentCreate(**body,evaluator={'id':'imaginary-safety-pass'}))
    with pytest.raises(ValueError,match='camera|learned model'):
        handler.prepare(ExperimentCreate(**body,model={'id':'camera-e2e'}))


def test_project_scope_roles_and_cross_project_input(log_client):
    project = log_client.post('/api/v1/projects',json={'name':'Other'}).json()
    body = authored(project_id=project['id'])
    seq = sequence(log_client,body)
    spec = replay(log_client,seq)
    viewer = log_client.post('/api/v1/projects/'+project['id']+'/tokens',json={'user_label':'reader','role':'Viewer'}).json()['token']
    reader = {'Authorization':'Bearer '+viewer}
    assert log_client.get('/api/v1/replay-specs/'+spec['id'],headers=reader).status_code==200
    assert log_client.post('/api/v1/scene-sequences',json=body,headers=reader).status_code==403
    assert log_client.get('/api/v1/replay-specs?project_id=default',headers=reader).status_code==404
    handler = PlanningReplayHandler(log_client.app.state.db)
    with pytest.raises(ValueError,match='another project'):
        handler.prepare(ExperimentCreate(name='Wrong project',kind='planning_replay',adapter_id='reference-planning-replay',
            input={'replay_spec_id':spec['id']}))
    # Legacy unscoped recordings cannot be copied into the new project's boundary.
    recorded_body,_ = recorded(log_client)
    assert log_client.post('/api/v1/scene-sequences',json=recorded_body|{'project_id':project['id']}).status_code==404
