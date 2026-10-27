"""Demo API contracts with authored fixtures; these do not execute a simulator."""
import copy
import hashlib
import json
import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient
from ade2e import experiment_jobs
from ade2e.adapters import Registry
from ade2e.database import Database, Settings
from ade2e.experiment_routes import register_experiment_routes
from ade2e.experiment_runtime import ExperimentExecution, ExperimentHandlerMetadata, ExperimentRegistry
from ade2e.object_demo import register_object_demo_routes
from ade2e.scenario_v2 import register_scenario_v2_routes, get_scenario
from ade2e.simulation_catalog import register_catalog, register_simulation_catalog_routes
from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow
from ade2e.team_access import authenticate_request, register_team_routes
from ade2e.worker import run_once
from test_scenario_city import pose, map_definition, asset_definition, reference


class DemoHandler:
    def __init__(self, db):
        self.db = db
        self.fail = False
        self.planner_hash = 'a' * 64

    def metadata(self):
        return ExperimentHandlerMetadata(id='demo-contract', version='1', kinds=['object_sim'], status='available',
            fidelity='authored_demo_contract', cancellation_supported=True)

    def prepare(self, spec):
        with self.db.read_session() as session:
            original = get_scenario(session, spec.input['scenario_id'], spec.input['scenario_revision'])
        scenario = original['scenario']
        return {'schema_version':'carla-city-input/1', 'scenario':scenario,
            'source': {'scenario_id':original['id'], 'scenario_revision':original['revision'],
                       'scenario_manifest_hash':original['manifest_hash'], 'configuration_hash':original['configuration_hash'],
                       'map_sha256':scenario['map']['sha256'], 'seed':spec.seed},
            'execution_fidelity':'authored_demo_contract', 'server_version':'fixture',
            **{key:'a'*64 for key in ('adapter_script_sha256','native_script_sha256','control_script_sha256','maneuver_script_sha256')},
            'planner_script_sha256': self.planner_hash}

    def execute(self, snapshot, work_dir, should_cancel):
        if self.fail:
            raise RuntimeError('Authored test failure, not a simulator run')
        return ExperimentExecution(summary={'metrics':{'ego':{'distance_m':snapshot['scenario']['actors'][0]['speed_mps']*10},
            'contacts':0}, 'verification_verdict':'not_evaluated'}, artifacts=[], fidelity='authored_demo_contract')


@pytest.fixture
def env(tmp_path):
    settings = Settings(data_dir=tmp_path, api_token='test-bootstrap', team_mode=True, seed_examples=False)
    db = Database(settings); db.initialize()
    handler = DemoHandler(db); registry = ExperimentRegistry([handler], load_plugins=False)
    def authenticate(request: Request):
        header = request.headers.get('authorization','')
        authenticate_request(request, db, settings, header[7:] if header.startswith('Bearer ') else None)
    router = APIRouter(prefix='/api/v1', dependencies=[Depends(authenticate)])
    register_team_routes(router, db); register_simulation_catalog_routes(router, db)
    register_scenario_v2_routes(router, db); register_object_demo_routes(router, db)
    register_experiment_routes(router, db, registry)
    app = FastAPI(); app.include_router(router); client = TestClient(app)
    client.headers['Authorization'] = 'Bearer test-bootstrap'
    native = map_definition()
    native['nodes'] = [dict(id=f'{lane}:{i}', **pose(i*5,lane*3.5), road_id=1, section_id=0, lane_id=-lane-1,
        lane_width_m=3.5, next_ids=[f'{lane}:{i+1}'] if i<20 else []) for lane in range(2) for i in range(21)]
    def catalog(kind, identity, definition):
        result = client.post('/api/v1/'+kind, json={'id':identity,'definition':definition})
        assert result.status_code == 201, result.text
        return reference(result.json())
    map_ref = catalog('maps','demo-map',native); asset_ref = catalog('assets','vehicle',asset_definition())
    scenario = {'schema_version':'scenario-v2/3','name':'Authored demo fixture','map':map_ref,
        'actors':[{'id':'ego','role':'ego','asset':asset_ref,'pose':pose(30), 'route':[pose(30),pose(90)],
                   'controller':{'kind':'reactive'}, 'speed_mps':4},
                  {'id':'parked','asset':asset_ref,'pose':pose(50),'controller':{'kind':'static'},'speed_mps':0},
                  {'id':'rear','asset':asset_ref,'pose':pose(10,3.5),'route':[pose(10,3.5),pose(90,3.5)],
                   'controller':{'kind':'reactive'},'speed_mps':3}]}
    yield db, client, handler, registry, scenario
    db.engine.dispose()


def save(client, scenario, key='baseline'):
    response=client.post('/api/v1/scenario-v2',json={'scenario':scenario},headers={'Idempotency-Key':key})
    assert response.status_code == 201, response.text
    return {k:response.json()[k] for k in ('id','revision','configuration_hash','manifest_hash')}


def bind(client, ref, preset='obstacle_bypass', **extra):
    return client.post(f'/api/v1/object-sim/presets/{preset}/bind',json={
        'scenario_ref':ref,'actor_roles':{'ego':'ego','obstacle':'parked','rear':'rear'},**extra})


def execute(env, ref, key='one', complete=True):
    db,client,handler,registry,_=env
    definition={'name':key,'kind':'object_sim','adapter_id':'demo-contract','seed':42,
        'input':{'scenario_id':ref['id'],'scenario_revision':ref['revision'],'configuration_hash':ref['configuration_hash']}}
    created=client.post('/api/v1/experiments',json=definition,headers={'Idempotency-Key':key})
    assert created.status_code==201,created.text
    submitted=client.post('/api/v1/experiments/'+created.json()['id']+'/executions',
        json={'preview_hash':created.json()['preview_hash']},headers={'Idempotency-Key':key})
    assert submitted.status_code==202,submitted.text
    identity=submitted.json()['id']
    if complete: run_once(db,Registry(),'fixture-worker',job_id=identity,experiment_registry=registry)
    return identity


def comparison(client,left,right):
    return client.get('/api/v1/object-sim/comparison',params={'left_execution_id':left,'right_execution_id':right})


def test_explicit_binding_idempotence_and_no_name_discovery(env):
    _,client,_,_,scenario=env
    ref=save(client,scenario)
    items = client.get('/api/v1/object-sim/presets').json()['items']
    assert [item['id'] for item in items] == ['obstacle_bypass', 'gap_release', 'junction_eight', 'urban_city', 'urban_right_turn',
                                            'lead_stop_start', 'cut_in', 'urban_left_turn']
    assert all(not item['ready'] for item in items)
    first=bind(client,ref); assert first.status_code==200,first.text
    assert bind(client,ref).json()==first.json()
    item=client.get('/api/v1/object-sim/presets').json()['items'][0]
    assert item['ready'] and item['defaults']['ego_speed_mps']==4
    changed=copy.deepcopy(scenario); changed['actors'][0]['speed_mps']=5
    second=save(client,changed,'second')
    assert bind(client,second).status_code==409
    assert bind(client,second,expected_revision=1).json()['revision']==2
    preview=client.post('/api/v1/object-sim/presets/obstacle_bypass/preview',json={}).json()
    assert preview['scenario_ref']==second
    assert preview['execution_status']=='not_executed'


def urban_scenario(client, scenario, db=None, **map_changes):
    """Authored metadata fixture; catalog checks here do not qualify native CARLA."""
    result = client.get('/api/v1/maps/demo-map').json()
    native = copy.deepcopy(result['definition'])
    native.update(native_name='Carla/Maps/Town10HD_Opt', rights={**native['rights'],
        'source_url': 'https://github.com/carla-simulator/carla'})
    native.update(map_changes)
    if native.get('generated') is not None:
        # Seed an authored generated-catalog fixture through the same storage
        # helper as the compiler; public map import correctly rejects this field.
        registered = register_catalog(db, 'map', {'id': 'urban-map', 'definition': native})
    else:
        result = client.post('/api/v1/maps', json={'id': 'urban-map', 'definition': native})
        assert result.status_code == 201, result.text
        registered = result.json()
    urban = copy.deepcopy(scenario); urban['map'] = reference(registered)
    urban['actors'] = [urban['actors'][0]]
    urban['actors'][0]['behavior'] = {'actions': [
        {'id': 'cruise', 'kind': 'target_speed', 'speed_mps': 4},
        {'id': 'wait', 'kind': 'wait', 'trigger': {'kind': 'time', 'value': 5}, 'duration_s': 2},
        {'id': 'resume', 'kind': 'resume', 'trigger': {'kind': 'action_complete', 'action_id': 'wait'}}]}
    return urban


def test_urban_native_binding_and_speed_preview_preserve_existing_pins(env):
    db,client,_,_,scenario = env
    baseline = save(client, scenario)
    previous = bind(client, baseline).json()
    urban = urban_scenario(client, scenario)
    ref = save(client, urban, 'urban')
    response = bind(client, ref, 'urban_city', actor_roles={'ego': 'ego'})
    assert response.status_code == 200, response.text
    assert bind(client, ref, 'urban_city', actor_roles={'ego': 'ego'}).json() == response.json()
    items = {item['id']: item for item in client.get('/api/v1/object-sim/presets').json()['items']}
    assert items['urban_city']['ready'] and items['urban_city']['defaults'] == {'ego_speed_mps': 4}
    assert items['obstacle_bypass']['scenario_ref'] == baseline
    assert items['obstacle_bypass']['binding_revision'] == previous['revision']
    assert not items['gap_release']['ready'] and not items['junction_eight']['ready']
    response = client.post('/api/v1/object-sim/presets/urban_city/preview', json={'parameters': {'ego_speed_mps': 6}})
    assert response.status_code == 200, response.text
    value = response.json(); actor = value['scenario']['actors'][0]
    assert value['scenario_ref'] == ref and value['scenario']['map'] == urban['map']
    assert actor['speed_mps'] == 6 and actor['behavior']['actions'][0]['speed_mps'] == 6
    assert actor['route'] == urban['actors'][0]['route'] and actor['pose'] == urban['actors'][0]['pose']
    assert actor['behavior']['actions'][1]['duration_s'] == 2
    assert value['native_preflight_required'] and value['execution_status'] == 'not_executed'
    assert client.post('/api/v1/object-sim/presets/urban_city/preview', json={
        'parameters': {'allow_lane_changes': True}}).status_code == 422
    with db.read_session() as session:
        assert get_scenario(session, ref['id'])['scenario']['actors'][0]['speed_mps'] == 4


def interaction_scenario(client, scenario):
    urban = urban_scenario(client, scenario)
    ego = urban['actors'][0]
    ego['behavior'] = {'actions': []}
    lead = copy.deepcopy(ego)
    lead.update(id='lead', role='other', pose=pose(45), route=[pose(45), pose(95)])
    lead['behavior'] = {'actions': [{'id': 'lead_wait', 'kind': 'wait',
        'trigger': {'kind': 'time', 'value': 2}, 'duration_s': 6, 'timeout_s': 14}]}
    urban['actors'].append(lead)
    return urban


def test_interaction_zero_wait_removes_only_lead_action_and_preserves_ego(env):
    _, client, _, _, scenario = env
    urban = interaction_scenario(client, scenario)
    ref = save(client, urban)
    assert bind(client, ref, 'urban_right_turn', actor_roles={'ego': 'ego', 'lead': 'lead'}).status_code == 200
    item = next(item for item in client.get('/api/v1/object-sim/presets').json()['items'] if item['id'] == 'urban_right_turn')
    assert item['ready'] and item['defaults'] == {'lead_wait_s': 6}
    baseline = client.post('/api/v1/object-sim/presets/urban_right_turn/preview', json={}).json()['scenario']
    for duration in (0, 3, 6):
        response = client.post('/api/v1/object-sim/presets/urban_right_turn/preview', json={'parameters': {'lead_wait_s': duration}})
        assert response.status_code == 200, response.text
        preview = response.json()['scenario']
        assert preview['actors'][0] == baseline['actors'][0]
        assert preview['map'] == baseline['map'] and preview['sensors'] == baseline['sensors']
        actions = preview['actors'][1]['behavior']['actions']
        assert len(actions) == int(duration > 0)
        if duration:
            assert actions[0] == {**baseline['actors'][1]['behavior']['actions'][0], 'duration_s': duration}
        preview['actors'][1]['behavior'] = baseline['actors'][1]['behavior']
        assert preview == baseline
    for params in ({'lead_wait_s': -1}, {'lead_wait_s': 7}, {'lead_wait_s': True}, {'ego_speed_mps': 5}):
        assert client.post('/api/v1/object-sim/presets/urban_right_turn/preview', json={'parameters': params}).status_code == 422


@pytest.mark.parametrize('malformation', ['ego_wait', 'missing_lead_wait', 'wrong_action', 'short_timeout'])
def test_interaction_rejects_misleading_or_incomplete_role_binding(env, malformation):
    _, client, _, _, scenario = env
    urban = interaction_scenario(client, scenario)
    action = urban['actors'][1]['behavior']['actions'][0]
    if malformation == 'ego_wait':
        urban['actors'][0]['behavior']['actions'] = [copy.deepcopy(action)]
    elif malformation == 'missing_lead_wait':
        urban['actors'][1]['behavior']['actions'] = []
    elif malformation == 'wrong_action':
        action['kind'] = 'stop'
    else:
        action.update(duration_s=1, timeout_s=2)
    ref = save(client, urban)
    assert bind(client, ref, 'urban_right_turn', actor_roles={'ego': 'ego', 'lead': 'lead'}).status_code == 422


def test_saved_interaction_pair_requires_completed_compatible_inputs(env):
    _, client, handler, _, scenario = env
    urban = interaction_scenario(client, scenario)
    b = save(client, urban, 'B')
    continuous = copy.deepcopy(urban)
    continuous['actors'][1]['behavior']['actions'] = []
    a = save(client, continuous, 'A')
    b_job = execute(env, b, 'B')
    a_job = execute(env, a, 'A')
    kwargs = {'actor_roles': {'ego': 'ego', 'lead': 'lead'}, 'saved_execution_id': b_job,
              'saved_comparison_execution_id': a_job}
    response = bind(client, b, 'urban_right_turn', **kwargs)
    assert response.status_code == 200, response.text
    assert bind(client, b, 'urban_right_turn', **kwargs).json() == response.json()
    item = next(item for item in client.get('/api/v1/object-sim/presets').json()['items'] if item['id'] == 'urban_right_turn')
    assert item['ready'] and item['saved_comparison_execution_id'] == a_job
    compared = comparison(client, a_job, b_job).json()
    assert compared['compatibility']['compatible']
    assert {change['path'] for change in compared['changes']} == {'/actors/lead/behavior/actions/lead_wait'}
    assert bind(client, b, 'urban_right_turn', expected_revision=1,
        **{**kwargs, 'saved_comparison_execution_id': b_job}).status_code == 422
    pending = execute(env, a, 'pending', complete=False)
    assert bind(client, b, 'urban_right_turn', expected_revision=1,
        **{**kwargs, 'saved_comparison_execution_id': pending}).status_code == 422
    handler.planner_hash = 'b' * 64
    other = execute(env, a, 'other-planner')
    assert bind(client, b, 'urban_right_turn', expected_revision=1,
        **{**kwargs, 'saved_comparison_execution_id': other}).status_code == 422


@pytest.mark.parametrize('map_changes', [
    {'generated': {'source': {'id': 'authored-road', 'revision': 1, 'sha256': 'a' * 64},
        'compiler_id': 'object-road', 'compiler_version': '1', 'build_hash': 'b' * 64}},
    {'native_name': 'OpenDriveMap'},
    {'native_name': 'generated__1234567890abcdef'},
    {'rights': {'license': 'test-fixture-only', 'source_url': 'https://example.test/authored-road'}},
    {'rights': {'license': 'test-fixture-only'}},
])
def test_urban_binding_rejects_generated_or_unattributed_town_catalog(env, map_changes):
    db,client,_,_,scenario = env
    ref = save(client, urban_scenario(client, scenario, db=db, **map_changes))
    response = bind(client, ref, 'urban_city', actor_roles={'ego': 'ego'})
    assert response.status_code == 422, response.text
    assert 'native CARLA Town catalog' in response.json()['detail']
    item = next(item for item in client.get('/api/v1/object-sim/presets').json()['items'] if item['id'] == 'urban_city')
    assert item['status'] == 'unbound'


def test_preview_modifies_speed_lane_flag_and_returns_catalog_without_saving(env):
    db,client,_,_,scenario=env
    ref=save(client,scenario);bind(client,ref)
    changed=client.post('/api/v1/object-sim/presets/obstacle_bypass/preview',json={
        'parameters':{'ego_speed_mps':7,'allow_lane_changes':False,'obstacle_offset_m':5}})
    assert changed.status_code==200,changed.text
    value=changed.json();ego,obstacle,_=value['scenario']['actors']
    assert ego['speed_mps']==7 and not ego['planner']['allow_lane_changes']
    assert obstacle['pose']['x']==55
    assert value['map']['id']==scenario['map']['id']
    assert len(value['map']['lanes'])==2 and value['map']['lanes'][0]['points']
    with db.read_session() as session:
        assert get_scenario(session,ref['id'])['scenario']['actors'][0]['speed_mps']==4


def test_drag_snap_replans_to_same_destination_and_rejects_off_map(env):
    _,client,_,_,scenario=env
    bind(client,save(client,scenario))
    response=client.post('/api/v1/object-sim/presets/obstacle_bypass/preview',json={'actor_poses':{'ego':pose(34,.2)}})
    assert response.status_code==200,response.text
    actor=response.json()['scenario']['actors'][0]
    assert actor['pose']==pose(35) and actor['route'][0]==pose(35)
    assert actor['destination']==pose(90) and actor['route'][-1]==pose(90)
    assert client.post('/api/v1/object-sim/presets/obstacle_bypass/preview',json={'actor_poses':{'ego':pose(200)}}).status_code==422
    assert client.post('/api/v1/object-sim/presets/obstacle_bypass/preview',json={'actor_poses':{'missing':pose(30)}}).status_code==422


def test_gap_parameter_moves_rear_only_and_conflicting_drag_rejected(env):
    _,client,_,_,scenario=env
    ref=save(client,scenario); assert bind(client,ref,'gap_release').status_code==200
    response=client.post('/api/v1/object-sim/presets/gap_release/preview',json={'parameters':{'rear_gap_m':10,'rear_speed_mps':8}})
    assert response.status_code==200,response.text
    rear=response.json()['scenario']['actors'][2]
    assert rear['pose']==pose(20,3.5) and rear['speed_mps']==8
    assert client.post('/api/v1/object-sim/presets/gap_release/preview',json={
        'parameters':{'rear_gap_m':10},'actor_poses':{'rear':pose(25,3.5)}}).status_code==422


@pytest.mark.parametrize('parameters',[{'ego_speed_mps':13},{'ego_speed_mps':True},{'ego_speed_mps':'4'},
    {'allow_lane_changes':1},{'allow_lane_changes':'false'},{'unrecognized':2},{'rear_speed_mps':3}])
def test_parameter_type_and_range_fail_closed(env,parameters):
    _,client,_,_,scenario=env;bind(client,save(client,scenario))
    response=client.post('/api/v1/object-sim/presets/obstacle_bypass/preview',json={'parameters':parameters})
    assert response.status_code==422,response.text


def test_project_tokens_get_preview_but_viewers_cannot_bind(env):
    _,client,_,_,scenario=env;ref=save(client,scenario);bind(client,ref)
    token=client.post('/api/v1/projects/default/tokens',json={'role':'Viewer','user_label':'demo-viewer'}).json()['token']
    client.headers['Authorization']='Bearer '+token
    assert client.get('/api/v1/object-sim/presets').status_code==200
    assert client.post('/api/v1/object-sim/presets/obstacle_bypass/preview',json={}).status_code==200
    assert bind(client,ref).status_code==403
    assert client.get('/api/v1/object-sim/presets?project_id=other').status_code==404


def test_corrupted_binding_and_bad_scenario_pin_fail_closed(env):
    db,client,_,_,scenario=env;ref=save(client,scenario)
    assert bind(client,{**ref,'manifest_hash':'0'*64}).status_code==409
    bind(client,ref)
    with db.session() as session:
        row=session.get(SimulationCatalogRevisionRow,('default','object-demo-preset','obstacle_bypass',1))
        row.manifest=b'corrupted';session.commit()
    assert client.post('/api/v1/object-sim/presets/obstacle_bypass/preview',json={}).status_code==409


def test_completed_comparison_actual_delta_and_saved_binding(env):
    _,client,_,_,scenario=env
    ref=save(client,scenario);left=execute(env,ref)
    changed=copy.deepcopy(scenario);changed['actors'][0]['speed_mps']=6
    second=save(client,changed,'second');right=execute(env,second,'two')
    result=comparison(client,left,right);assert result.status_code==200,result.text
    value=result.json();assert value['compatibility']['compatible']
    assert {'path':'/actors/ego/speed_mps','left':4.,'right':6.} in value['changes']
    assert {'path':'/ego/distance_m','left':40.,'right':60.,'delta':20.} in value['metrics']
    assert bind(client,ref,saved_execution_id=left).status_code==200
    assert bind(client,ref,saved_execution_id=right,expected_revision=1).status_code==422


def test_comparison_retains_failure_and_no_delta_on_partial_or_build_mismatch(env):
    _,client,handler,_,scenario=env;ref=save(client,scenario);left=execute(env,ref)
    right=execute(env,ref,'pending',complete=False)
    value=comparison(client,left,right).json()
    assert value['right']['execution']['status']=='queued'
    assert all(item['delta'] is None for item in value['metrics'])
    handler.fail=True;failed=execute(env,ref,'failed')
    value=comparison(client,left,failed).json()
    assert value['right']['execution']['status']=='failed'
    assert value['right']['execution']['error'] == client.get('/api/v1/experiments/executions/'+failed).json()['error']
    assert all(item['delta'] is None for item in value['metrics'])
    handler.fail=False;handler.planner_hash='b'*64
    drift=execute(env,ref,'drift');value=comparison(client,left,drift).json()
    assert not value['compatibility']['compatible']
    assert any('planner_script_sha256' in reason for reason in value['compatibility']['reasons'])
    assert all(item['delta'] is None for item in value['metrics'])


def test_comparison_denies_other_project_execution(env):
    _,client,_,_,scenario=env;ref=save(client,scenario);left=execute(env,ref)
    project=client.post('/api/v1/projects',json={'name':'Other'}).json()['id']
    token=client.post(f'/api/v1/projects/{project}/tokens',json={'role':'Viewer','user_label':'other'}).json()['token']
    client.headers['Authorization']='Bearer '+token
    result=client.get('/api/v1/object-sim/comparison',params={'project_id':project,'left_execution_id':left,'right_execution_id':left})
    assert result.status_code==404


def test_native_actor_metrics_align_by_actor_identity():
    from ade2e.object_demo import _numbers
    left=[{'actor_id':'ego','distance_travelled_m':30},{'actor_id':'rear','distance_travelled_m':40}]
    right=list(reversed(left))
    assert _numbers(left)==_numbers(right)=={'/ego/distance_travelled_m':30,'/rear/distance_travelled_m':40}
