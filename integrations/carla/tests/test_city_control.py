"""Independent geometric/controller properties; no measured vehicle claim."""
import math
import pytest
from axiom_carla import city_control as control


def p(x=0.,y=0.,z=0.,heading_rad=0.):return dict(x=x,y=y,z=z,heading_rad=heading_rad)


def test_polyline_projection_progress_and_interpolation_are_continuous():
    route=[p(),p(10),p(10,10)]
    cumulative=control.lengths(route)
    assert cumulative==[0,10,20]
    projection=control.project(p(6,2),route)
    assert projection['progress_m']==pytest.approx(6)
    assert projection['error_m']==pytest.approx(2)
    assert projection['remaining_m']==pytest.approx(14)
    assert control.interpolate(route,cumulative,12)==dict(x=10,y=2,z=0)
    assert control.interpolate(route,cumulative,50)==dict(x=10,y=10,z=0)


def test_straight_feedback_brakes_at_goal_and_turns_toward_route_in_carla_coordinates():
    route=[p(),p(30)]
    command,plan=control.feedback({**p(5),'speed_mps':2},route,3)
    assert command['steer']==pytest.approx(0)
    assert command['throttle']>0 and command['brake']==0
    right,_=control.feedback({**p(5,-1),'speed_mps':2},route,3)
    left,_=control.feedback({**p(5,1),'speed_mps':2},route,3)
    assert right['steer']>0 and left['steer']<0
    at_goal,plan=control.feedback({**p(29.5),'speed_mps':1},route,3)
    assert at_goal['brake']>0 and at_goal['throttle']==0 and plan['desired_speed_mps']==0
    stopped,plan=control.feedback({**p(29.5),'speed_mps':0},route,3)
    assert stopped['brake']==1 and plan['state']=='stopped'


def test_stop_is_braking_without_teleport_and_end_deceleration_reduces_target_speed():
    route=[p(),p(30)]
    state={**p(5),'speed_mps':3};original=dict(state)
    command,plan=control.feedback(state,route,3,stop=True)
    assert state==original
    assert command['brake']>0 and command['throttle']==0
    near=control.feedback({**p(28),'speed_mps':3},route,6)[1]
    far=control.feedback({**p(5),'speed_mps':3},route,6)[1]
    assert near['desired_speed_mps']<far['desired_speed_mps']


def test_timed_replay_uses_shortest_heading_arc_and_stops_at_last_pose():
    trajectory=[{**p(heading_rad=math.radians(179)),'time_s':0},
                {**p(10,heading_rad=math.radians(-179)),'time_s':2}]
    pose,speed=control.replay_pose(trajectory,1)
    assert pose['x']==5 and speed==5
    assert abs(abs(pose['heading_rad'])-math.pi)<1e-9
    endpoint,speed=control.replay_pose(trajectory,20)
    assert endpoint['x']==10 and speed==0
    repeated,speed=control.replay_pose(trajectory,3,'repeat')
    assert repeated['x']==5 and speed==5


@pytest.mark.parametrize('speed',[13,-1,float('nan'),float('inf')])
def test_control_rejects_unsupported_speed(speed):
    with pytest.raises(ValueError):control.feedback({**p(),'speed_mps':0},[p(),p(10)],speed)


def test_degenerate_route_has_no_synthetic_direction():
    with pytest.raises(ValueError):control.lengths([p(),p()])


@pytest.mark.parametrize('sign',[-1,1])
def test_rear_axle_pure_pursuit_recovers_known_circle_curvature(sign):
    radius=5.;wheelbase=2.8;offset=1.4;maximum=1.2
    route=[p(radius*math.sin(i*.001),sign*radius*(1-math.cos(i*.001))) for i in range(2001)]
    state={**p(offset),'speed_mps':3.}
    command,plan=control.feedback(state,route,3,wheelbase_m=wheelbase,max_steer_rad=maximum,rear_axle_offset_m=offset)
    assert math.tan(command['steer']*maximum)/wheelbase==pytest.approx(sign/radius,abs=1e-7)
    assert plan['steering_reference']=='rear_axle'
    assert plan['steering_point']==pytest.approx({'x':0.,'y':0.})
    assert plan['steering_progress_m']==pytest.approx(0.)
    # Qualification remains at actor origin rather than replacing its error
    # with the perfectly on-path rear axle's zero error.
    assert plan['error_m']==pytest.approx(control.project(state,route)['error_m'])
    assert plan['error_m']>.15 and plan['steering_error_m']==pytest.approx(0.)


def test_rear_axle_steering_is_rigid_transform_invariant_and_preserves_stop_geometry():
    route=[p(),p(10),p(15,5),p(15,20)];state={**p(8,.4,heading_rad=.12),'speed_mps':2.5}
    command,plan=control.feedback(state,route,3,rear_axle_offset_m=1.4)
    theta=1.1
    def transform(q):
        return {**q,'x':10+math.cos(theta)*q['x']-math.sin(theta)*q['y'],
            'y':-7+math.sin(theta)*q['x']+math.cos(theta)*q['y'],'heading_rad':q['heading_rad']+theta}
    moved,moved_plan=control.feedback(transform(state),[transform(q) for q in route],3,rear_axle_offset_m=1.4)
    assert moved==pytest.approx(command)
    assert moved_plan['steering_progress_m']==pytest.approx(plan['steering_progress_m'])
    legacy,legacy_plan=control.feedback(state,route,3)
    assert {k:plan[k] for k in ('error_m','progress_m','remaining_m','goal_distance_m','desired_speed_mps')}==pytest.approx(
        {k:legacy_plan[k] for k in ('error_m','progress_m','remaining_m','goal_distance_m','desired_speed_mps')})
    goal={**p(29.5),'speed_mps':1.}
    for offset in [0.,1.4]:
        stopped,stop_plan=control.feedback(goal,[p(),p(30)],3,rear_axle_offset_m=offset)
        assert stop_plan['goal_reached'] and stop_plan['desired_speed_mps']==0 and stopped['brake']>0
        assert stop_plan['remaining_m']==pytest.approx(.5)


@pytest.mark.parametrize('physics',[{'rear_axle_offset_m':-1},{'rear_axle_offset_m':3},
    {'rear_axle_offset_m':float('nan')},{'wheelbase_m':0},{'wheelbase_m':float('inf')},
    {'max_steer_rad':0},{'max_steer_rad':math.pi/2}])
def test_invalid_rear_axle_and_steering_geometry_is_rejected(physics):
    with pytest.raises(ValueError):control.feedback({**p(),'speed_mps':0},[p(),p(10)],3,**physics)


def test_rear_axle_and_origin_have_independent_route_continuity_progress():
    route=[p(),p(10),p(20),p(30)]
    _,plan=control.feedback({**p(15),'speed_mps':3},route,3,rear_axle_offset_m=1.4,previous=15,previous_steering=13.6)
    assert plan['progress_m']==pytest.approx(15)
    assert plan['steering_progress_m']==pytest.approx(13.6)
