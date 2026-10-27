"""Reactive/native transport contracts. Authored fixtures, not CARLA validation.

The control-wiring check executes the actual native runner branch extracted from
its AST, with only the world boundary removed. It therefore detects a renewed
base-speed clamp without implementing a second copy of the integration logic.
"""
import ast
import copy
import inspect
import math
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from ade2e.scenario_v2_schemas import ScenarioReactive,parse_scenario
from ade2e.scenario_v2_xml import digest
from axiom_carla import city,city_native,city_control,reactive_planner
from test_city_contract import fixture_pair,point


def reactive_fixture():
    snapshot,scene=fixture_pair(False)
    document=copy.deepcopy(snapshot['scenario']);document.update(schema_version='scenario-v2/3',duration_s=1.,timestep_s=.05)
    document['actors'][0]['controller']['kind']='reactive'
    document['actors'][0]['route']=[point(x) for x in range(10)]
    scenario=ScenarioReactive.model_validate(document).model_dump(mode='json')
    snapshot['scenario']=scenario;snapshot['source']['configuration_hash']=digest(scenario)
    snapshot['planner_script_sha256']=city.file_sha(reactive_planner.__file__)
    snapshot['map_definition']={'nodes':[dict(id=str(x),**point(x),road_id=1,section_id=0,lane_id=-1,
        lane_width_m=3.5,is_junction=False,next_ids=[str(x+1)] if x<9 else []) for x in range(10)]}
    scene['source'].update(snapshot['source']);scene['source']['planner_sha256']=snapshot['planner_script_sha256']
    scene['actors'][0].update(scenario['actors'][0]);scene['routes']={'ego':scenario['actors'][0]['route']}
    scene['planner_scope']='simulator_ground_truth_low_speed_reactive_no_v2x'
    scene['planned_paths']={'ego':[{'index':0,'points':scenario['actors'][0]['route'],'route_revision':0,'reason':'route_follow'}]}
    scene['planner_events']=[{'time_s':0.,'actor_id':'ego','type':'decision_changed','reason':'route_follow',
        'action_id':None,'route_revision':0,'index':0,'input_frame':1000,'provenance':'observed_state_reactive_planner'}]
    scene['frames']=[copy.deepcopy(scene['frames'][0]) for _ in range(20)]
    for index,frame in enumerate(scene['frames']):
        frame.update(index=index,frame=1001+index,input_frame=1000+index,time_s=(index+1)*.05,simulation_time_s=100+(index+1)*.05)
        actor=frame['actors'][0];x=(index+1)*.15
        actor.update(x=x,route_progress_m=x,goal_distance_m=9-x)
        actor['plan']={'reactive':{'planner_version':reactive_planner.VERSION,'route_revision':0,
            'state':'route_follow','reason':'route_follow','speed_mps':3.,'stop':False,
            'action_id':None,'diagnostics':{},'destination':point(9)}}
    scene['metrics']=city._measured_metrics(scene,scenario)
    return snapshot,scene


def test_reactive_fixture_preserves_original_guide_plan_and_planner_identity():
    snapshot,scene=reactive_fixture()
    assert city.validate_scene(scene,snapshot) is scene
    scene['source']['planner_sha256']='0'*64
    with pytest.raises(ValueError,match='planner source'):city.validate_scene(scene,snapshot)


def test_completed_stop_sign_event_is_valid_planner_evidence():
    snapshot,scene=reactive_fixture()
    scene['planner_events'].append({**scene['planner_events'][0],'type':'stop_sign_completed','reason':'native-stop-1'})
    assert city.validate_scene(scene,snapshot) is scene


def test_adding_optional_destination_preserves_existing_v3_configuration_hash():
    snapshot,_=reactive_fixture();original=copy.deepcopy(snapshot['scenario'])
    for actor in original['actors']:actor.pop('destination',None)
    parsed=parse_scenario(original).model_dump(mode='json')
    assert digest(parsed)==digest(original)
    original['actors'][0]['destination']=point(9,3.5)
    assert parse_scenario(original).model_dump(mode='json')['actors'][0]['destination']==point(9,3.5)


def test_destination_goal_metrics_do_not_use_guide_endpoint_or_replanned_endpoint():
    snapshot,scene=reactive_fixture();scenario=snapshot['scenario']
    scenario['actors'][0]['destination']=point(9,3.5)
    measured=city._measured_metrics(scene,scenario)
    final=scene['frames'][-1]['actors'][0]
    assert measured[0]['goal_error_m']==pytest.approx(math.hypot(9-final['x'],3.5))
    assert measured[0]['goal_error_m']!=pytest.approx(9-final['x'])
    scene['planned_paths']['ego'][0]['points']=[point(),point(1.5)]
    assert city._measured_metrics(scene,scenario)[0]['goal_error_m']==measured[0]['goal_error_m']


def _native_feedback_branch():
    module=ast.parse(inspect.getsource(city_native.capture))
    choices=[]
    for node in ast.walk(module):
        if not isinstance(node,ast.If) or not node.orelse:continue
        first=node.orelse[0]
        if isinstance(first,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='phys' for target in first.targets):
            choices.append(node.orelse)
    assert len(choices)==1,'Native control seam changed; update integration fixture to execute its real branch'
    return compile(ast.fix_missing_locations(ast.Module(body=choices[0],type_ignores=[])),'native-feedback-branch','exec')


def test_native_feedback_honors_target_speed_above_authored_initial_speed_and_caches_events():
    route=[point(x) for x in range(0,121,2)]
    nodes=[dict(id=str(x),**point(x),road_id=1,section_id=0,lane_id=-1,lane_width_m=3.5,
                next_ids=[str(x+2)] if x<120 else []) for x in range(0,121,2)]
    spec={'id':'ego','kind':'vehicle','controller':{'kind':'reactive','end_behavior':'stop'},'speed_mps':1,
        'route':route,'behavior':{'actions':[{'id':'accelerate','kind':'target_speed','speed_mps':6}]}}
    planner=reactive_planner.ReactivePlanner([spec],{'nodes':nodes})
    state={'id':'ego',**point(),'speed_mps':2}
    first=planner.step([state],0)
    scope={'city_control':city_control,'actor_physics':{},'identity':'ego','decisions':first,'mode':'reactive',
        'active_routes':{'ego':route},'desired':spec['speed_mps'],'stop':False,'route_revisions':{},
        'route_progress':{},'steering_progress':{},'scene':{'planned_paths':{},'planner_events':[]},
        'index':0,'snap':SimpleNamespace(frame=1000),'state':state,'control_spec':spec['controller']}
    code=_native_feedback_branch();exec(code,scope)
    assert scope['plan']['desired_speed_mps']==6
    assert scope['command']['throttle']>0 and scope['command']['brake']==0
    assert len(scope['scene']['planned_paths']['ego'])==1
    event_count=len(scope['scene']['planner_events']);assert event_count>0
    scope.update(decisions=planner.step([state],.05),index=1,snap=SimpleNamespace(frame=1001))
    exec(code,scope)
    assert len(scope['scene']['planner_events'])==event_count
    assert len(scope['scene']['planned_paths']['ego'])==1


@pytest.mark.parametrize('mutation',[
    lambda event:event.update(actor_id='unregistered'),
    lambda event:event.update(index=-1),
    lambda event:event.update(index=100),
    lambda event:event.update(time_s=99),
    lambda event:event.update(input_frame=999),
    lambda event:event.update(route_revision=99),
    lambda event:event.update(provenance='authored_not_observed'),
])
def test_reactive_event_identity_clock_revision_and_provenance_fail_closed(mutation):
    snapshot,scene=reactive_fixture();mutation(scene['planner_events'][0])
    with pytest.raises(ValueError):city.validate_scene(scene,snapshot)


@pytest.mark.parametrize('mutation',[
    lambda scene:scene['planned_paths']['ego'][0].update(index=-1),
    lambda scene:scene['planned_paths']['ego'].append(copy.deepcopy(scene['planned_paths']['ego'][0])),
    lambda scene:scene['planned_paths']['ego'][0].update(route_revision=-1),
    lambda scene:scene['planned_paths']['ego'][0]['points'][0].update(heading_rad=float('nan')),
])
def test_reactive_route_history_rejects_invalid_denominator_or_nonfinite_path(mutation):
    snapshot,scene=reactive_fixture();mutation(scene)
    with pytest.raises(ValueError):city.validate_scene(scene,snapshot)


def test_rehashed_offroad_path_cannot_hide_behind_recomputed_tracking_metrics():
    snapshot,scene=reactive_fixture()
    scene['planned_paths']['ego'][0]['points']=[point(x,100) for x in range(10)]
    for frame in scene['frames']:frame['actors'][0]['route_error_m']=100
    scene['metrics']=city._measured_metrics(scene,snapshot['scenario'])
    with pytest.raises(ValueError):city.validate_scene(scene,snapshot)


@pytest.mark.parametrize('override',[
    {'wait_s':1},{'headway_s':1},{'stop_time_s':.2},{'obey_signals':False},{'end_behavior':'repeat'},
])
def test_v3_rejects_legacy_second_control_owner(override):
    snapshot,_=reactive_fixture();scenario=snapshot['scenario'];scenario['actors'][0]['controller'].update(override)
    with pytest.raises(ValidationError):parse_scenario(scenario)
