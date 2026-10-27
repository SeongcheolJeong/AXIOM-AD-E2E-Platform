"""Kernel contract checks; these do not establish actual CARLA vehicle results."""
import copy
import math
import pytest
from axiom_carla.reactive_planner import LaneGraph, ReactivePlanner, bumper_gap, trigger_matches, ttc


def pose(x=0.,y=0.,heading_rad=0.):
    return dict(x=x,y=y,z=0.,heading_rad=heading_rad)


def state(identity,x=0.,y=0.,speed=0.,heading_rad=0.,length=4.,width=2.):
    return dict(id=identity,**pose(x,y,heading_rad),speed_mps=speed,dimensions={'length_m':length,'width_m':width})


def map_definition(length=160):
    nodes=[]
    for lane in (1,2):
        for x in range(0,length+1,2):
            identity=f'{lane}:{x}'
            node=dict(id=identity,**pose(x,(lane-1)*3.5),road_id=1,section_id=0,lane_id=lane,
                lane_width_m=3.5,is_junction=False,next_ids=[f'{lane}:{x+2}'] if x<length else [],
                speed_limit_mps=12,priority=0,conflict_ids=[],closed=False)
            side='right' if lane==1 else 'left'
            node[side+'_id']=f'{3-lane}:{x}';node['lane_change_'+side]=True
            nodes.append(node)
    return {'nodes':nodes,'signals':[]}


def actor(identity='ego',actions=None,*,route=None,kind='vehicle',risk=False):
    return dict(id=identity,kind=kind,controller={'kind':'reactive'},speed_mps=5,
        route=route or [pose(x) for x in range(0,141,2)],
        behavior={'risk_mode':risk,'actions':actions or []},planner={})


def crossing_map():
    """Four perpendicular directed lanes; junction tags span 48m connectors."""
    nodes=[]
    for road,heading,start in [(1,0,(-60,1.75)),(2,math.pi/2,(-1.75,-60)),
                               (3,math.pi,(60,-1.75)),(4,-math.pi/2,(1.75,60))]:
        for distance in range(0,121,2):
            junction=36<=distance<=84
            nodes.append({'id':f'r{road}:{distance}',**pose(start[0]+distance*math.cos(heading),start[1]+distance*math.sin(heading),heading),
                'road_id':road,'section_id':0,'lane_id':-1,'lane_width_m':3.5,'is_junction':junction,
                'next_ids':[f'r{road}:{distance+2}'] if distance<120 else [],'priority':0,
                'conflict_ids':['crossing'] if junction else []})
    return {'nodes':nodes,'signals':[]}


def action(kind,**extra):
    return {'id':'a1','kind':kind,'trigger':{'kind':'time','value':0},'timeout_s':30,'on_timeout':'stop','duration_s':0,**extra}


def test_footprints_and_ttc_use_bumper_clearance_and_orientation():
    a=state('ego',speed=5);b=state('lead',x=10,speed=2)
    assert bumper_gap(a,b)['gap_m']==6
    assert ttc(a,b)==2
    b['heading_rad']=math.pi/2
    assert bumper_gap(a,b)['gap_m']==7
    assert ttc(a,state('side',10,4,speed=0)) is None


@pytest.mark.parametrize('trigger,expected',[
    ({'kind':'time','value':2},True),
    ({'kind':'distance','target_actor_id':'lead','value':6,'operator':'lte'},True),
    ({'kind':'relative_speed','target_actor_id':'lead','value':4,'operator':'gte'},False),
    ({'kind':'ttc','target_actor_id':'lead','value':2,'operator':'lte'},True),
    ({'kind':'region','region':{'x':0,'y':0,'radius_m':1}},True),
    ({'kind':'signal','signal_id':'s1','signal_state':'green'},True),
    ({'kind':'action_complete','action_id':'a0'},True),
])
def test_all_trigger_sources_are_observed_and_explicit(trigger,expected):
    own=state('ego',speed=5);other=state('lead',10,speed=2)
    assert trigger_matches(trigger,own,{'ego':own,'lead':other},2,{'s1':'green'},{'a0'}) is expected


def test_follow_gap_caps_speed_and_keeps_same_observation_immutable():
    specs=[actor(),{'id':'lead','controller':{'kind':'static'}}]
    planner=ReactivePlanner(specs,map_definition())
    snapshot=[state('ego',speed=5),state('lead',10,speed=0)];before=copy.deepcopy(snapshot)
    plan=planner.step(snapshot,0)['ego']
    assert plan['speed_mps']==pytest.approx(1.5)
    assert plan['reason']=='follow_gap' and plan['diagnostics']['lead']['gap_m']==6
    assert snapshot==before


def test_stopped_lead_hold_enters_near_minimum_gap_without_an_ego_action():
    planner=ReactivePlanner([actor(),{'id':'lead','kind':'vehicle','controller':{'kind':'static'}}],map_definition())
    initial=planner.step([state('ego'),state('lead',12,speed=0)],0)['ego']
    assert initial['reason']=='follow_gap' and not initial['stop']
    observed=[state('ego',speed=.4),state('lead',7.9,speed=.02)];original=copy.deepcopy(observed)
    held=planner.step(observed,.1)['ego']
    assert held['reason']==held['state']=='follow_lead_stop' and held['stop'] and held['speed_mps']==0
    assert held['action_id'] is None and held['diagnostics']['observation_basis']=='current_snapshot_ground_truth'
    assert held['diagnostics']['lead_hold']=={'actor_id':'lead','gap_m':pytest.approx(3.9),'speed_mps':.02,
        'enter_gap_m':4.,'release_gap_m':4.5,'enter_speed_mps':.1,'release_speed_mps':.3,'state':'holding'}
    assert any(e['type']=='decision_changed' and e['reason']=='follow_lead_stop' and e['action_id'] is None for e in held['events'])
    assert observed==original and not planner.specs['ego']['behavior']['actions']


def test_stopped_lead_hold_hysteresis_absorbs_speed_and_clearance_noise():
    planner=ReactivePlanner([actor(),{'id':'lead','controller':{'kind':'static'}}],map_definition())
    planner.step([state('ego'),state('lead',7.9)],0)
    for time,x,speed in [(.1,8.1,.12),(.2,8.4,.29),(.3,7.95,.09),(.4,8.2,.2)]:
        held=planner.step([state('ego'),state('lead',x,speed=speed)],time)['ego']
        assert held['reason']=='follow_lead_stop' and held['stop'] and held['speed_mps']==0
        assert held['diagnostics']['lead_hold']['state']=='holding'
        assert not held['events']


@pytest.mark.parametrize('x,speed,release',[(7.9,.3,'lead_moving'),(8.5,.02,'gap_opened')])
def test_stopped_lead_releases_only_on_observed_motion_or_open_gap(x,speed,release):
    planner=ReactivePlanner([actor(),{'id':'lead','controller':{'kind':'static'}}],map_definition())
    planner.step([state('ego'),state('lead',7.9)],0)
    result=planner.step([state('ego'),state('lead',x,speed=speed)],.1)['ego']
    assert result['reason']=='follow_gap' and not result['stop']
    proof=result['diagnostics']['lead_hold']
    assert proof['state']=='released' and proof['release_reason']==release and proof['actor_id']=='lead'
    assert proof['gap_m']==pytest.approx(x-4) and proof['speed_mps']==speed
    assert planner.memory['ego']['lead_hold_actor_id'] is None
    assert any(e['reason']=='follow_gap' for e in result['events'])
    assert not any(e['type']=='lead_released' for e in result['events'])
    # Release proof belongs to its actual transition, not every later plan.
    next_plan=planner.step([state('ego'),state('lead',x,speed=speed)],.2)['ego']
    assert 'lead_hold' not in next_plan['diagnostics']


def test_stopped_lead_latch_clears_when_leader_changes_or_disappears():
    actors=[actor(),{'id':'old','controller':{'kind':'static'}},{'id':'new','controller':{'kind':'static'}}]
    planner=ReactivePlanner(actors,map_definition())
    planner.step([state('ego'),state('old',7.9)],0)
    changed=planner.step([state('ego'),state('new',8.2,speed=.2)],.1)['ego']
    assert changed['reason']=='follow_gap' and not changed['stop']
    assert changed['diagnostics']['lead_hold']=={'actor_id':'old','state':'cleared','release_reason':'lead_changed','current_actor_id':'new'}
    # A new stationary close leader can establish its own hold immediately.
    new=planner.step([state('ego'),state('new',7.9)],.2)['ego']
    assert new['stop'] and new['diagnostics']['lead_hold']['actor_id']=='new'
    missing=planner.step([state('ego')],.3)['ego']
    assert missing['reason']=='route_follow' and not missing['stop']
    assert missing['diagnostics']['lead_hold']=={'actor_id':'new','state':'cleared','release_reason':'lead_missing','current_actor_id':None}
    assert 'lead' not in missing['diagnostics']


def test_lead_hold_release_does_not_release_red_signal_or_footprint_stop():
    planner=ReactivePlanner([actor(),{'id':'lead','controller':{'kind':'static'}}],map_definition())
    planner.step([state('ego'),state('lead',7.9)],0)
    red=planner.step([state('ego'),state('lead',7.9,speed=1)],.1,{'ego':{'state':'red'}})['ego']
    assert red['stop'] and red['reason']=='signal_wait'
    assert red['diagnostics']['lead_hold']['release_reason']=='lead_moving'
    overlap=planner.step([state('ego'),state('lead',4.1,speed=1)],.2)['ego']
    assert overlap['stop'] and overlap['reason']=='footprint_emergency_hold'


def test_planning_rate_and_input_order_are_deterministic():
    specs=[actor('a'),actor('b')];snapshot=[state('a'),state('b',50)]
    first=ReactivePlanner(specs,map_definition());second=ReactivePlanner(list(reversed(specs)),map_definition())
    assert first.step(snapshot,0)==second.step(list(reversed(snapshot)),0)
    held=first.step([state('a',4),state('b',80)],.05)
    assert held['a']['events']==[]
    assert held['a']['speed_mps']==5
    first.step(snapshot,.2)
    with pytest.raises(ValueError,match='backwards'):first.step(snapshot,.1)


def test_stop_wait_then_resume_sequence_preserves_actor_ownership():
    actions=[action('stop',duration_s=1),{**action('resume',speed_mps=4),'id':'resume'}]
    planner=ReactivePlanner([actor(actions=actions)],map_definition())
    assert planner.step([state('ego',speed=3)],0)['ego']['stop']
    assert planner.step([state('ego')],.5)['ego']['stop']
    assert planner.step([state('ego')],1.1)['ego']['stop']
    planner.step([state('ego')],1.5)
    resumed=planner.step([state('ego')],1.6)['ego']
    assert resumed['speed_mps']==4 and not resumed['stop']
    assert planner.memory['ego']['completed']=={'a1','resume'}


def test_timeout_stops_and_explains_instead_of_teleporting_or_completing():
    planner=ReactivePlanner([actor(actions=[action('yield',target_actor_id='lead',timeout_s=1)]),
        {'id':'lead','controller':{'kind':'static'}}],map_definition())
    planner.step([state('ego'),state('lead',10)],0)
    plan=planner.step([state('ego'),state('lead',10)],1)['ego']
    assert plan['stop']
    assert any(e['type']=='action_timeout' for e in plan['events'])
    assert 'a1' not in planner.memory['ego']['completed']


def test_native_red_light_hold_and_explicit_risk_signal_violation():
    signal={'ego':{'state':'red','signal_id':'native42'}}
    normal=ReactivePlanner([actor()],map_definition()).step([state('ego')],0,signal)['ego']
    assert normal['stop'] and normal['reason']=='signal_wait'
    risky=ReactivePlanner([actor(actions=[action('signal_violation',duration_s=3)],risk=True)],map_definition())
    plan=risky.step([state('ego')],0,signal)['ego']
    assert not plan['stop']
    with pytest.raises(ValueError,match='Risk action'):
        ReactivePlanner([actor(actions=[action('signal_violation')])],map_definition())


def test_dijkstra_closure_reroutes_but_never_changes_pinned_goal():
    graph=map_definition(20)
    nodes={n['id']:n for n in graph['nodes']}
    nodes['1:4']['next_ids'].append('2:6');nodes['2:16']['next_ids'].append('1:18')
    path=LaneGraph(graph).path('1:0','1:20',{'1:10'})
    assert path[0]['id']=='1:0' and path[-1]['id']=='1:20'
    assert '1:10' not in [n['id'] for n in path] and any(n['lane_id']==2 for n in path)
    assert LaneGraph(graph).path('1:0','1:20',{'1:20'})==[]


def test_reroute_action_reports_no_legal_route_and_holds():
    spec=actor(actions=[action('reroute')]);spec['planner']['closed_node_ids']=['1:60']
    planner=ReactivePlanner([spec],map_definition())
    plan=planner.step([state('ego')],0)['ego']
    assert plan['stop'] and plan['reason']=='no_legal_route'
    assert plan['route_revision']==0 and plan['route'][-1]==pose(140)


def test_lane_change_requires_explicit_permission():
    definition=map_definition()
    for n in definition['nodes']:n.pop('lane_change_left',None);n.pop('lane_change_right',None)
    planner=ReactivePlanner([actor(actions=[action('lane_change',target_lane_node_id='2:0')])],definition)
    result=planner.step([state('ego')],0)['ego']
    assert result['stop'] and result['reason']=='no_legal_adjacent_lane'


def test_adjacent_fast_rear_actor_prevents_merge_then_clear_gap_allows():
    definition=map_definition()
    spec=actor(actions=[action('merge',target_lane_node_id='2:20')],route=[pose(x,3.5) for x in range(20,141,2)])
    spec['route'][0]=pose(20,0)
    planner=ReactivePlanner([spec,{'id':'rear','controller':{'kind':'static'}}],definition)
    waiting=planner.step([state('ego',20,speed=3),state('rear',10,3.5,speed=12)],0)['ego']
    assert waiting['reason']=='gap_wait' and waiting['stop']
    moving=planner.step([state('ego',20,speed=3),state('rear',0,3.5,speed=0)],.1)['ego']
    assert moving['route_revision']==1
    assert moving['diagnostics']['planned_curvature_inv_m']<=.12
    assert moving['route'][-1]==pose(140,3.5)


def test_obstacle_bypass_builds_two_transitions_and_preserves_goal():
    spec=actor(actions=[action('obstacle_bypass')])
    planner=ReactivePlanner([spec,{'id':'obstacle','controller':{'kind':'static'}}],map_definition())
    plan=planner.step([state('ego',speed=3),state('obstacle',30)],0)['ego']
    assert plan['route_revision']==1
    assert max(p['y'] for p in plan['route'])==pytest.approx(3.5)
    assert plan['route'][-1]==pose(140)
    assert plan['diagnostics']['planned_curvature_inv_m']<=.12


def test_junction_priority_and_arrival_break_ties_without_future_routes():
    definition=crossing_map()
    actors=[actor('a',route=[pose(-60,1.75),pose(60,1.75)]),
            actor('b',route=[pose(-1.75,-60,math.pi/2),pose(-1.75,60,math.pi/2)])]
    result=ReactivePlanner(actors,definition).step([state('a',-12,1.75),state('b',-1.75,-12,heading_rad=math.pi/2)],0)
    assert result['b']['reason']=='yield_conflict'
    assert result['b']['diagnostics']['conflicts'][0]['rule']=='priority_arrival_id_tiebreak'
    assert result['a']['reason']=='route_follow'


def test_observed_heading_continuation_does_not_choose_turn_with_equal_initial_tangent():
    nodes=[dict(id='approach',**pose(-2),next_ids=['a-turn','z-straight']),
           dict(id='a-turn',**pose(),next_ids=['a-turn-2']),
           dict(id='a-turn-2',**pose(2,1,math.pi/4),next_ids=['a-turn-4']),
           dict(id='a-turn-4',**pose(3,3,math.pi/2),next_ids=[]),
           dict(id='z-straight',**pose(0,0,1e-15),next_ids=['z-straight-2']),
           dict(id='z-straight-2',**pose(2,0,1e-15),next_ids=['z-straight-4']),
           dict(id='z-straight-4',**pose(4),next_ids=[])]
    graph=LaneGraph({'nodes':nodes})
    path=graph.forward(graph.nodes['approach'],6)
    assert [node['id'] for node in path]==['approach','z-straight','z-straight-2','z-straight-4']


def test_junction_occupancy_releases_when_body_clears_crossing_not_connector_end():
    definition=crossing_map()
    actors=[actor('a',route=[pose(-60,1.75),pose(60,1.75)]),
            actor('b',route=[pose(-1.75,-60,math.pi/2),pose(-1.75,60,math.pi/2)])]
    planner=ReactivePlanner(actors,definition)
    waiting=planner.step([state('a',-12,1.75),state('b',-1.75,0,heading_rad=math.pi/2)],0)['a']
    assert waiting['reason']=='yield_conflict'
    # y=10 is still within the authored [-24,+24] junction connector. The
    # actual crossing corridor and observed rear bumper have already cleared.
    cleared=planner.step([state('a',-12,1.75),state('b',-1.75,10,heading_rad=math.pi/2)],.1)['a']
    assert cleared['reason']=='route_follow' and cleared['speed_mps']==5


def test_parallel_lane_group_without_geometric_crossing_has_no_junction_mutex():
    actors=[actor('east',route=[pose(-60,1.75),pose(60,1.75)]),
            actor('west',route=[pose(60,-1.75,math.pi),pose(-60,-1.75,math.pi)])]
    definition=crossing_map();definition['nodes']=[n for n in definition['nodes'] if n['road_id'] in (1,3)]
    result=ReactivePlanner(actors,definition).step([state('east',-12,1.75),state('west',12,-1.75,heading_rad=math.pi)],0)
    assert all(p['speed_mps']==5 and p['reason']=='route_follow' for p in result.values())


def test_priority_wait_does_not_emit_fictitious_alternate_route_events():
    actors=[actor('a',route=[pose(-60,1.75),pose(60,1.75)]),
            actor('b',route=[pose(-1.75,-60,math.pi/2),pose(-1.75,60,math.pi/2)])]
    planner=ReactivePlanner(actors,crossing_map())
    observed=[state('a',-1.75,1.75),state('b',-1.75,-8,heading_rad=math.pi/2)]
    planner.step(observed,0)
    plan=planner.step(observed,12)['b']
    assert plan['reason']=='yield_conflict' and plan['route_revision']==0
    assert not any(e['type']=='route_replanned' for e in plan['events'])


def test_eight_actor_crossing_kernel_fixture_reaches_all_goals_without_overlap():
    """Deterministic kinematic diagnostic only; native vehicle run remains required."""
    from axiom_carla import city_control
    from axiom_carla.reactive_planner import _rectangles_overlap
    definition=crossing_map();specs=[];observed={};goals={}
    for road in range(1,5):
        lane=[n for n in definition['nodes'] if n['road_id']==road]
        for follower,(start,end) in enumerate(((22,112),(6,96))):
            identity=f'vehicle-{road}-{follower}'
            route=[{k:n[k] for k in ('x','y','z','heading_rad')} for n in lane if start<=int(n['id'].split(':')[1])<=end]
            spec=actor(identity,route=route);specs.append(spec);goals[identity]=route[-1]
            observed[identity]=state(identity,route[0]['x'],route[0]['y'],heading_rad=route[0]['heading_rad'],length=4.5,width=1.8)
    planner=ReactivePlanner(specs,definition);yielded=False
    for index in range(1200):
        plans=planner.step(observed,index*.05)
        for identity,value in observed.items():
            decision=plans[identity];yielded=yielded or decision['reason']=='yield_conflict'
            command,_=city_control.feedback(value,decision['route'],decision['speed_mps'],stop=decision['stop'])
            speed=max(0.,value['speed_mps']+.05*(3.5*command['throttle']-6*command['brake']-.06*value['speed_mps']))
            heading=value['heading_rad']+.05*speed/2.7*math.tan(command['steer']*.6)
            value.update(x=value['x']+.05*speed*math.cos(heading),y=value['y']+.05*speed*math.sin(heading),heading_rad=heading,speed_mps=speed)
        values=list(observed.values())
        assert not any(_rectangles_overlap(a,b,0) for i,a in enumerate(values) for b in values[i+1:])
    assert yielded
    for identity,value in observed.items():
        goal=goals[identity]
        assert math.hypot(value['x']-goal['x'],value['y']-goal['y'])<=2
        assert value['speed_mps']<=.2


def test_deadlock_is_explicit_no_hidden_rule_violation():
    spec=actor();spec['planner']['deadlock_timeout_s']=2
    planner=ReactivePlanner([spec,{'id':'obstacle','controller':{'kind':'static'}}],map_definition())
    snapshot=[state('ego'),state('obstacle',5)]
    planner.step(snapshot,0);result=planner.step(snapshot,2)['ego']
    assert result['reason']=='deadlock_no_legal_progress' and result['stop']
    assert result['route_revision']==0


def test_invalid_snapshot_and_wrong_rate_fail_closed():
    with pytest.raises(ValueError,match='20Hz'):ReactivePlanner([actor()],map_definition(),.1)
    planner=ReactivePlanner([actor()],map_definition())
    with pytest.raises(ValueError,match='missing controlled actor'):planner.step([],0)


def test_local_native_bbox_offset_is_rotated_for_gap_geometry():
    own=state('ego',speed=3,heading_rad=math.pi/2)
    own['bbox_center']={'x':1,'y':0,'z':.5}
    other=state('lead',0,10,heading_rad=math.pi/2)
    assert bumper_gap(own,other)['gap_m']==pytest.approx(5)


def test_timeout_event_is_terminal_and_not_repeated():
    planner=ReactivePlanner([actor(actions=[action('route_follow',timeout_s=1)])],map_definition())
    planner.step([state('ego')],0)
    first=planner.step([state('ego')],1)['ego']
    later=planner.step([state('ego')],1.2)['ego']
    assert sum(e['type']=='action_timeout' for e in first['events'])==1
    assert not any(e['type']=='action_timeout' for e in later['events'])
    assert later['stop'] and later['reason']=='action_timeout_stop'


def test_auto_bypass_only_static_obstacles_and_not_a_signal_queue():
    obstacle={'id':'obstacle','kind':'static','controller':{'kind':'static'}}
    snapshot=[state('ego',speed=3),state('obstacle',30)]
    planner=ReactivePlanner([actor(),obstacle],map_definition())
    planner.step(snapshot,0);plan=planner.step(snapshot,2)['ego']
    assert plan['route_revision']==1
    assert plan['diagnostics']['automatic_bypass']['status']=='planned'
    red=ReactivePlanner([actor(),obstacle],map_definition())
    red.step(snapshot,0,{'ego':{'state':'red'}})
    assert red.step(snapshot,2,{'ego':{'state':'red'}})['ego']['route_revision']==0
    vehicle=ReactivePlanner([actor(),{**obstacle,'kind':'vehicle'}],map_definition())
    vehicle.step(snapshot,0)
    assert vehicle.step(snapshot,2)['ego']['route_revision']==0


def test_bypass_candidate_cannot_intersect_obstruction_during_initial_transition():
    planner=ReactivePlanner([actor(actions=[action('obstacle_bypass')]),
        {'id':'obstacle','kind':'static','controller':{'kind':'static'}}],map_definition())
    plan=planner.step([state('ego'),state('obstacle',6)],0)['ego']
    assert plan['route_revision']==0
    assert plan['diagnostics']['obstacle_actor_id']=='obstacle'
    assert plan['stop']


def test_late_vehicle_in_return_lane_is_rechecked_before_rejoining():
    actors=[actor(actions=[action('obstacle_bypass')]),
        {'id':'obstacle','kind':'static','controller':{'kind':'static'}},
        {'id':'rear','kind':'vehicle','controller':{'kind':'static'}}]
    planner=ReactivePlanner(actors,map_definition())
    first=planner.step([state('ego',speed=3),state('obstacle',30),state('rear',-100,0,speed=0)],0)['ego']
    assert first['route_revision']==1
    active=planner.memory['ego']['action_route'];return_start=active['transitions'][1]['start']
    x=return_start['x']-3
    second=planner.step([state('ego',x,3.5,speed=3),state('obstacle',30),state('rear',x-7,0,speed=12)],.1)['ego']
    assert second['reason']=='active_lane_gap_wait'
    assert second['diagnostics']['active_lateral_gap']['gap_checks'][0]['actor_id']=='rear'
    assert second['speed_mps']<first['speed_mps']


def test_crossing_uses_observed_velocity_without_future_actor_route():
    own=actor(kind='pedestrian',route=[pose(x) for x in range(0,41,2)],actions=[action('cross')])
    own['speed_mps']=1.5
    planner=ReactivePlanner([own,{'id':'car','kind':'vehicle','controller':{'kind':'static'}}],map_definition())
    plan=planner.step([state('ego',speed=0,length=.6,width=.6),state('car',5,-10,speed=4,heading_rad=math.pi/2)],0)['ego']
    assert plan['stop'] and plan['reason']=='yield_predicted_crossing'
    assert plan['diagnostics']['predicted_conflicts'][0]['prediction']=='observed_constant_velocity'


def test_explicit_destination_allows_legal_lane_change_without_rewriting_original_guide():
    spec=actor(actions=[action('lane_change',target_lane_node_id='2:0')]);spec['destination']=pose(140,3.5)
    planner=ReactivePlanner([spec],map_definition())
    plan=planner.step([state('ego',speed=3)],0)['ego']
    assert plan['route_revision']==1 and plan['route'][-1]==pose(140,3.5)
    assert plan['original_route'][-1]==pose(140,0)
    assert plan['destination']==pose(140,3.5)
    assert planner.step([state('ego',140,0)],.1)['ego']['reason']!='arrived'
    assert planner.step([state('ego',140,3.5)],.2)['ego']['reason']=='arrived'


def test_destination_cannot_silently_snap_across_lane_or_reverse_direction():
    spec=actor();spec['destination']=pose(140,5)
    with pytest.raises(ValueError,match='Explicit destination'):ReactivePlanner([spec],map_definition())
    spec['destination']=pose(140,3.5,math.pi)
    with pytest.raises(ValueError,match='Explicit destination'):ReactivePlanner([spec],map_definition())


def test_stop_sign_anticipates_braking_and_requires_actual_one_second_dwell():
    definition=map_definition();definition['signals']=[{'id':'stop-1','kind':'stop','node_ids':['1:30'],'pose':pose(30,1.75)}]
    planner=ReactivePlanner([actor()],definition)
    approaching=planner.step([state('ego',24,speed=5)],0)['ego']
    assert approaching['speed_mps']<5 and approaching['reason']=='stop_sign_wait'
    assert planner.step([state('ego',26)],.1)['ego']['stop']
    assert planner.step([state('ego',26)],1.)['ego']['stop']
    departed=planner.step([state('ego',26)],1.1)['ego']
    assert not departed['stop']
    assert any(e['type']=='stop_sign_completed' for e in departed['events'])
    assert not any(e['type']=='stop_sign_completed' for e in planner.step([state('ego',26)],1.2)['ego']['events'])


def test_mapped_red_light_is_anticipated_before_its_exact_node():
    definition=map_definition();definition['signals']=[{'id':'red-1','kind':'traffic_light','node_ids':['1:30'],'pose':pose(30,1.75)}]
    planner=ReactivePlanner([actor()],definition)
    result=planner.step([state('ego',24,speed=5)],0,{'red-1':'red'})['ego']
    assert result['reason']=='signal_wait' and result['speed_mps']<5
    assert result['diagnostics']['signal']['distance_m']==4


def test_standstill_lane_entry_reserves_a_vehicle_length_margin_before_intrusion():
    actors=[actor(actions=[action('obstacle_bypass')]),
        {'id':'parked','kind':'vehicle','controller':{'kind':'static'}},
        {'id':'rear','kind':'vehicle','controller':{'kind':'static'}}]
    planner=ReactivePlanner(actors,map_definition())
    result=planner.step([state('ego',10),state('parked',46),state('rear',2,3.5)],0)['ego']
    assert result['reason']=='gap_wait' and result['stop'] and result['route_revision']==0
    assert result['diagnostics']['gap_checks'][0]['required_gap_m']==5
    released=planner.step([state('ego',10),state('parked',46),state('rear',35,3.5,speed=7)],3)['ego']
    assert released['route_revision']==1 and not released['stop']


@pytest.mark.parametrize('rear_speed,expected_reason,expected_speed',[(0,'committed_merge_clearance',1.),(3,'active_lane_gap_wait',0.)])
def test_committed_merge_clears_only_observed_stationary_rear_yield(rear_speed,expected_reason,expected_speed):
    actors=[actor(actions=[action('obstacle_bypass')]),
        {'id':'parked','kind':'vehicle','controller':{'kind':'static'}},
        {'id':'rear','kind':'vehicle','controller':{'kind':'static'}}]
    planner=ReactivePlanner(actors,map_definition())
    planner.step([state('ego'),state('parked',36),state('rear',-100,3.5)],0)
    snapshot=[state('ego',7.63,.92,heading_rad=.24),state('parked',36),state('rear',1.15,3.5,speed=rear_speed)]
    original=copy.deepcopy(snapshot)
    result=planner.step(snapshot,4)['ego']
    assert result['reason']==expected_reason and result['speed_mps']==expected_speed
    assert snapshot==original
    assert result['diagnostics']['active_lateral_gap']['gap_checks'][0]['position']=='rear'


def test_committed_merge_creep_never_overrides_a_front_conflict():
    actors=[actor(actions=[action('obstacle_bypass')]),
        {'id':'parked','kind':'vehicle','controller':{'kind':'static'}},
        {'id':'rear','kind':'vehicle','controller':{'kind':'static'}},
        {'id':'front','kind':'vehicle','controller':{'kind':'static'}}]
    planner=ReactivePlanner(actors,map_definition())
    planner.step([state('ego'),state('parked',36),state('rear',-100,3.5),state('front',150,3.5)],0)
    result=planner.step([state('ego',7.63,.92,heading_rad=.24),state('parked',36),
        state('rear',1.15,3.5),state('front',14,3.5)],4)['ego']
    assert result['stop'] and result['speed_mps']==0
    assert result['reason']!='committed_merge_clearance'


def test_explicit_risk_cut_in_retains_its_smaller_gap_and_cannot_ignore_overlap():
    spec=actor(actions=[action('cut_in',target_lane_node_id='2:0')],risk=True)
    spec['destination']=pose(140,3.5)
    specs=[spec,{'id':'rear','kind':'vehicle','controller':{'kind':'static'}}]
    planner=ReactivePlanner(specs,map_definition())
    result=planner.step([state('ego'),state('rear',-6,3.5)],0)['ego']
    assert result['route_revision']==1
    normal=copy.deepcopy(spec);normal['behavior']['actions'][0]['kind']='lane_change'
    held=ReactivePlanner([normal,specs[1]],map_definition()).step([state('ego'),state('rear',-6,3.5)],0)['ego']
    assert held['stop'] and held['reason']=='gap_wait'
    overlapping_target=ReactivePlanner(specs,map_definition()).step([state('ego'),state('rear',-3,3.5)],0)['ego']
    assert overlapping_target['stop'] and overlapping_target['route_revision']==0
