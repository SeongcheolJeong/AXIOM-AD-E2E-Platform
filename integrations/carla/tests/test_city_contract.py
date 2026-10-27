"""Authored/mock fixtures only: no simulator, sensor or Autoware validation."""
import copy
import math
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from ade2e.scenario_v2_schemas import ScenarioCity
from ade2e.scenario_v2_xml import digest
from ade2e.city_motion import scenario_fidelity
from axiom_carla import city,city_native,city_control


def point(x=0.,y=0.,z=0.,heading_rad=0.):
    return dict(x=x,y=y,z=z,heading_rad=heading_rad)


def fixture_pair(with_camera=True):
    reference={'id':'fixture','revision':1,'sha256':'a'*64}
    scenario=ScenarioCity.model_validate({'name':'Authored contract fixture','map':reference,
        'duration_s':1,'timestep_s':.1,'actors':[{'id':'ego','role':'ego','asset':reference,
            'pose':point(),'route':[point(),point(9)],'controller':{'kind':'route_follow'},'speed_mps':3}],
        'sensors':[{'id':'front','width':64,'height':64,'period_s':.2}] if with_camera else []}).model_dump(mode='json')
    source={'scenario_id':'scenario2_fixture','scenario_revision':1,'scenario_manifest_hash':'b'*64,
            'configuration_hash':digest(scenario),'map_sha256':'a'*64,'seed':42}
    snapshot={'source':source,'scenario':scenario,'native_script_sha256':city.file_sha(city_native.__file__),
              'control_script_sha256':city.file_sha(city_control.__file__),
              'map':{'native_name':'FixtureTown','opendrive_sha256':'c'*64},
              'assets':{'ego':{'blueprint_id':'vehicle.fixture'}},'server_version':'fixture-build'}
    scene={'schema_version':'carla-city-scene/1','fidelity':'native_carla_object_physics',
        'physical_sensor_validation':False,'coordinate_system':scenario['coordinate_system'],
        'source':{**source,'runner_sha256':snapshot['native_script_sha256'],'control_sha256':snapshot['control_script_sha256'],
                  'xodr_sha256':'c'*64,'adapter_version':city.VERSION,'engine_version':'fixture-build'},
        'map':{'native_name':'FixtureTown','opendrive_sha256':'c'*64},
        'actors':[{**scenario['actors'][0],'native_actor_id':1,'native_blueprint':'vehicle.fixture',
                   'dimensions_m':{'length_m':4.5,'width_m':1.8,'height_m':1.5},'bbox_center':{'x':0.,'y':0.,'z':.75},
                   'geometry_source':'native_actor_bounding_box','motion_fidelity':'native_carla_physics'}],
        'frames':[],'events':[],'routes':{'ego':scenario['actors'][0]['route']},'planned_paths':{},'camera_records':[],
        'sensors':copy.deepcopy(scenario['sensors']),'limitations':['Authored fixture; no native observation'],
        'evaluation_status':'not_evaluated','verification_verdict':'not_evaluated','metrics':[]}
    for index in range(10):
        x=.3*(index+1)
        state={'id':'ego',**point(x),'velocity':{'x':3.,'y':0.,'z':0.},'acceleration':{'x':0.,'y':0.,'z':0.},
               'speed_mps':3.,'yaw_rate_rps':0.,'route_error_m':0.,'route_progress_m':x,'goal_distance_m':9-x,
               'control':{'throttle':.12,'brake':0.,'steer':0.},'desired_speed_mps':3.,'controller_state':'tracking','plan':{}}
        frame={'index':index,'frame':1001+index,'input_frame':1000+index,'time_s':(index+1)*.1,
               'simulation_time_s':100+(index+1)*.1,'actors':[state],'cameras':[]}
        if with_camera and index%2==0:
            record={'sensor_id':'front','index':index,'frame':frame['frame'],'time_s':frame['time_s'],
                    'simulation_time_s':frame['simulation_time_s'],'width':64,'height':64,'size_bytes':64*64*4,
                    'encoding':'bgra8','archive':'city-camera-0000.zip','member':f'front/{index:06d}.bgra',
                    'sha256':'d'*64,'world_pose':point(x,0,1.7)}
            frame['cameras'].append(record);scene['camera_records'].append(record)
        scene['frames'].append(frame)
    scene['metrics']=[{'actor_id':'ego','distance_travelled_m':2.7,'maximum_speed_mps':3.,'native_contact':False,
        'lateral_error_p95_m':0.,'goal_error_m':6.,'final_speed_mps':3.}]
    return snapshot,scene


def test_valid_fixture_has_complete_clock_camera_actor_denominator_and_failing_goal():
    snapshot,scene=fixture_pair()
    assert city.validate_scene(scene,snapshot) is scene
    evaluation=city.evaluate(scene,snapshot['scenario'])
    assert evaluation['verdict']=='fail'
    assert evaluation['classification']=='sut_performance_failure'
    assert evaluation['physical_sensor_validation'] is False
    assert evaluation['criteria']==snapshot['scenario']['acceptance']


@pytest.mark.parametrize('mutation',[
    lambda s:s['source'].update(configuration_hash='0'*64),
    lambda s:s['source'].update(runner_sha256='0'*64),
    lambda s:s['source'].update(xodr_sha256='0'*64),
    lambda s:s['frames'].pop(),
    lambda s:s['frames'][2].update(input_frame=1000),
    lambda s:s['frames'][2]['actors'][0].update(id='other'),
    lambda s:s['frames'][2]['actors'][0].update(x=float('nan')),
    lambda s:s['frames'][2]['actors'][0]['control'].update(steer=2),
    lambda s:s['frames'][0]['cameras'][0].update(frame=88),
    lambda s:s['frames'][0]['cameras'][0].update(width=65),
    lambda s:s['camera_records'].pop(),
    lambda s:s['actors'][0].update(native_blueprint='vehicle.wrong'),
    lambda s:s['routes']['ego'][1].update(x=999),
])
def test_source_identity_and_complete_sampling_reject_corrupted_result(mutation):
    snapshot,scene=fixture_pair();scene=copy.deepcopy(scene);mutation(scene)
    with pytest.raises(ValueError):city.validate_scene(scene,snapshot)


@pytest.mark.parametrize('mutation',[
    lambda s:s['metrics'][0].update(goal_error_m=0,final_speed_mps=0),
    lambda s:s['metrics'][0].update(lateral_error_p95_m=.25),
    lambda s:s['metrics'][0].update(distance_travelled_m=400),
    lambda s:s['metrics'].clear(),
    lambda s:s['frames'][2].update(simulation_time_s=500),
    lambda s:s['frames'][2].update(simulation_time_s=float('nan')),
    lambda s:s['frames'][2]['actors'][0].update(route_error_m=9),
    lambda s:s['frames'][2]['actors'][0].update(goal_distance_m=0),
    lambda s:s['actors'][0]['dimensions_m'].pop('length_m'),
    lambda s:s['events'].append({'kind':'native_contact','actor_ids':['missing'],'frame':5000,'time_s':99,
                               'provenance':'native_carla_collision_sensor','details':{}}),
    lambda s:s['frames'][0]['cameras'][0].update(encoding='rgb8'),
    lambda s:s['source'].update(engine_version='wrong-build'),
])
def test_rehashed_but_physically_inconsistent_metrics_clock_events_or_encoding_are_rejected(mutation):
    # This boundary must rederive diagnostics; artifact hash validity alone is
    # not enough to admit fabricated metrics as a test pass.
    snapshot,scene=fixture_pair();scene=copy.deepcopy(scene);mutation(scene)
    with pytest.raises(ValueError):city.validate_scene(scene,snapshot)


def test_coincident_junction_nodes_require_a_connected_candidate_sequence():
    snapshot,scene=fixture_pair(False);scenario=snapshot['scenario']
    scenario['actors'][0]['route']=[point(0),point(3),point(6)]
    nodes=[dict(point(0),id='start',next_ids=['junction-good']),
           dict(point(3),id='junction-wrong',next_ids=[]),
           dict(point(3),id='junction-good',next_ids=['end']),
           dict(point(6),id='end',next_ids=[])]
    city.validate_routes(scenario,{'nodes':nodes})
    nodes[2]['next_ids']=[]
    with pytest.raises(ValueError,match='unreachable'):city.validate_routes(scenario,{'nodes':nodes})


def test_controller_catalog_does_not_silently_run_a_different_math_revision():
    declaration={'adapter_id':'carla-city-object','adapter_version':city.VERSION,'mode':'route_follow','capabilities':['native_trace']}
    with pytest.raises(ValueError,match='math is unavailable'):city.validate_controller_revision(declaration)
    declaration['capabilities'].append('control_math:'+city_control.VERSION)
    city.validate_controller_revision(declaration)
    declaration['capabilities'].append('control_math:axiom-city-pure-pursuit/1')
    with pytest.raises(ValueError,match='math is unavailable'):city.validate_controller_revision(declaration)


def test_routes_preserve_direction_source_graph_and_native_profile_bounds():
    snapshot,_=fixture_pair(False);scenario=snapshot['scenario']
    nodes=[dict(id=str(i),**point(i*3),next_ids=[str(i+1)] if i<3 else []) for i in range(4)]
    scenario['actors'][0]['route']=[point(i*3) for i in range(4)]
    city.validate_routes(scenario,{'nodes':nodes})
    for mutation in [lambda a:a['route'].pop(1),lambda a:a['route'][1].update(heading_rad=math.pi),
                     lambda a:a['route'][1].update(y=1),lambda a:a.update(speed_mps=13),
                     lambda a:a['controller'].update(end_behavior='repeat')]:
        bad=copy.deepcopy(scenario);mutation(bad['actors'][0])
        with pytest.raises(ValueError):city.validate_routes(bad,{'nodes':nodes})


@pytest.mark.parametrize('unsupported',['legacy_observer','autoware_signal_override'])
def test_preview_rejects_ignored_semantics_before_catalog_or_native_probe(monkeypatch,unsupported):
    snapshot,_=fixture_pair(False);scenario=snapshot['scenario']
    if unsupported=='legacy_observer':
        scenario['observers']=[{'kind':'collision','actor_id':'ego'}]
        message='Legacy scenario observers'
    else:
        scenario['actors'][0]['controller'].update(kind='autoware',ref=scenario['map'],obey_signals=False)
        message='always obeys signals'
    scenario=ScenarioCity.model_validate(scenario).model_dump(mode='json')
    monkeypatch.setattr(city,'get_scenario',lambda *a:{'project_id':'default','scenario':scenario})
    monkeypatch.setattr(city,'validate_city_catalogs',lambda *a:pytest.fail('Invalid semantics reached catalog resolution'))
    handler=city.CityExperimentHandler(database=SimpleNamespace(read_session=lambda:nullcontext(None)))
    monkeypatch.setattr(handler,'native_probe',lambda:pytest.fail('Invalid semantics reached native simulator'))
    spec=SimpleNamespace(kind='object_sim',comparison_kind='single',model=None,sensor=None,evaluator=None,
        project_id='default',seed=scenario['seed'],input={'scenario_id':'scenario2_fixture','scenario_revision':1,'configuration_hash':digest(scenario)})
    with pytest.raises(ValueError,match=message):handler.prepare(spec)


@pytest.mark.parametrize('replayed_ego',[False,True])
def test_replay_is_retained_as_diagnostics_without_physical_contact_pass(replayed_ego):
    snapshot,scene=fixture_pair(False);scenario=snapshot['scenario']
    actor=copy.deepcopy(scenario['actors'][0]);actor.update(id='replay',role='other',route=[],
        controller={**actor['controller'],'kind':'timed_replay'},trajectory=[{**point(),'time_s':0.},{**point(3),'time_s':1.}])
    if replayed_ego:
        actor.update(id='ego',role='ego');scenario['actors']=[actor]
    else:
        scenario['actors'].append(actor)
        for frame in scene['frames']:frame['actors'].append({**frame['actors'][0],'id':'replay'})
    result=city.evaluate(scene,scenario)
    assert result['verdict']=='not_evaluated'
    assert result['diagnostic_only_actor_ids']==[actor['id']]
    assert next(c for c in result['checks'] if c['metric']=='all_actor_native_contact_count')['value'] is None
    assert next(m for m in result['measured_metrics'] if m['actor_id']==actor['id'])['distance_travelled_m']==pytest.approx(2.7)
    assert actor['id'] not in result['physical_qualification_actor_ids']


@pytest.mark.parametrize('modes,expected',[
    (['route_follow'],'native_carla_object_physics'),(['static'],'native_carla_object_physics'),
    (['timed_replay'],'native_carla_timed_position_replay'),(['timed_replay','static'],'native_carla_timed_position_replay'),
    (['timed_replay','route_follow'],'native_carla_mixed_physics_and_replay'),
    (['timed_replay','autoware'],'native_carla_mixed_physics_and_replay')])
def test_native_and_core_fidelity_classify_frozen_controller_intent_identically(modes,expected):
    scenario={'actors':[{'controller':{'kind':mode}} for mode in modes]}
    assert city_native.scenario_fidelity(scenario)==scenario_fidelity(scenario)==expected


def test_replay_scene_cannot_mislabel_global_motion_as_physical():
    snapshot,scene=fixture_pair(False);scenario=snapshot['scenario'];actor=scenario['actors'][0]
    actor.update(route=[],trajectory=[{**point(),'time_s':0.},{**point(3),'time_s':1.}])
    actor['controller']['kind']='timed_replay'
    scene['actors'][0].update(actor);scene['actors'][0]['motion_fidelity']='timed_position_replay_no_physics'
    scene['routes']['ego']=[]
    for frame in scene['frames']:frame['actors'][0]['control']=None
    scene['metrics']=city._measured_metrics(scene,scenario)
    snapshot['execution_fidelity']=scenario_fidelity(scenario)
    with pytest.raises(ValueError,match='contract mismatch'):city.validate_scene(scene,snapshot)
    scene['fidelity']=snapshot['execution_fidelity']
    assert city.validate_scene(scene,snapshot) is scene
