"""Independent geometry, numerical integration and adversarial kernel2 tests."""
import copy
import hashlib
import math
import struct
from pathlib import Path
import json
import pytest
from pydantic import ValidationError
from ade2e.stack_common_schemas import StackPacket,StackEgo,StackLidar
from ade2e.stack_schemas import validate_stack_output
from ade2e.stack_runtime import StackSession,StackRegistry,StackExecutionError,StackInputError
from ade2e.stack_v2_schemas import StackDefinitionV2,StackOutputV2
from ade2e.stack_v2_components import (CorridorParametersV2,HeadwayParametersV2,
    ControlParametersV2,LidarCorridorV2,StoppingHeadwayV2,ProportionalControlV2,
    make_stop_plan,plan_sample,stopping_distance_bound,grid_observation,initial_state)


def definition(**policy):
    return StackDefinitionV2(schema_version='2',kernel_version='stack-kernel/2',
        perception={'id':'lidar-corridor','version':'2','parameters':{'ground_reference_z_m':0.,'ground_reference_source':'Independent authored flat-plane geometry fixture'}},
        policy={'id':'stopping-headway','version':'2','parameters':{'allow_no_obstacle_observed_motion':True,**policy}},
        controller={'id':'proportional-control','version':'2','parameters':{'assumed_full_brake_deceleration_mps2':8.,'brake_response_source':'Authored independent test plant; not CARLA calibration'}})


def full_grid(*,omit=(),hits=2,end=15.):
    # Points deliberately separated inside each fixed cell. No duplicate inflation.
    out=[]
    for i in range(math.ceil(end-2.5)):
        for j,y in enumerate((-.8,0.,.8)):
            if (i,j) in omit:continue
            for n in range(hits):out.append((2.5+i+min(.15+.2*n,(end-(2.5+i))*.75),y+n*.03,0.))
    return out


def packet(points,*,sequence=0,at_ns=None,speed=2.):
    at_ns=str(1_000_000_000+sequence*50_000_000) if at_ns is None else str(at_ns)
    blob=b''.join(struct.pack('<fff',*p) for p in points)
    ego=StackEgo(timestamp_ns=at_ns,frame_id='ego',speed_mps=float(speed),route_heading_error_rad=0.,pitch_rad=0.,roll_rad=0.,source='authored test')
    return StackPacket(sequence=sequence,at_ns=at_ns,ego=ego,lidar=StackLidar(timestamp_ns=at_ns,frame_id='ego',point_count=len(points),size_bytes=len(blob),points_sha256=hashlib.sha256(blob).hexdigest(),source_message_sha256='f'*64,complete_frame=True)),blob


def session(d=None):return StackSession(d or definition(),StackRegistry(load_plugins=False))


@pytest.mark.parametrize('speed',[0.,.1,1.999999,2.,2.000001,5.,10.])
def test_s_curve_matches_independent_midpoint_ode(speed):
    p=HeadwayParametersV2();plan=make_stop_plan(speed,'0',p)
    # Independent ODE integration using acceleration profile, not plan_sample.
    # Midpoint quadrature integrates v and x across each phase with <=0.1ms steps.
    x=0.;v=speed;a=0.
    phases=[(plan.latency_s,0.),(plan.ramp_s,-p.jerk_limit_mps3),(plan.constant_deceleration_s,0.),(plan.ramp_s,p.jerk_limit_mps3)]
    for duration,j in phases:
        n=max(1,math.ceil(duration/.0001));h=duration/n
        for _ in range(n):
            amid=a+j*h/2;vmid=v+amid*h/2
            x+=vmid*h;v+=amid*h;a+=j*h
    assert x==pytest.approx(plan.distance_m,abs=3e-7)
    assert v==pytest.approx(0.,abs=2e-10) and a==pytest.approx(0.,abs=2e-10)
    assert plan_sample(plan,plan.duration_s)==(0.,0.,0.,plan.distance_m)
    for k in range(101):
        v,a,j,x=plan_sample(plan,plan.duration_s*k/100)
        assert -p.assumed_deceleration_mps2-1e-12<=a<=0 and abs(j)<=p.jerk_limit_mps3
        assert v>=0 and x>=0


@pytest.mark.parametrize('positive',[0.,.5,2.])
def test_positive_acceleration_bound_includes_jerk_release(positive):
    p=HeadwayParametersV2(allowed_positive_acceleration_mps2=positive)
    speed=2.;x=0.;v=speed;a=positive
    phases=[(p.response_time_s,0.),(positive/p.jerk_limit_mps3,-p.jerk_limit_mps3)]
    for duration,j in phases:
        n=max(1,math.ceil(duration/.00005));h=duration/n
        for _ in range(n):
            amid=a+j*h/2;vmid=v+amid*h/2;x+=vmid*h;v+=amid*h;a+=j*h
    assert a==pytest.approx(0.,abs=1e-10)
    # Remaining numerical stop uses independently derived triangle/trapezoid durations.
    r=min(p.assumed_deceleration_mps2/p.jerk_limit_mps3,math.sqrt(v/p.jerk_limit_mps3))
    hold=max(0.,v/(p.jerk_limit_mps3*r)-r)
    for duration,j in [(r,-p.jerk_limit_mps3),(hold,0.),(r,p.jerk_limit_mps3)]:
        n=max(1,math.ceil(duration/.00005));h=duration/n
        for _ in range(n):
            amid=a+j*h/2;vmid=v+amid*h/2;x+=vmid*h;v+=amid*h;a+=j*h
    assert stopping_distance_bound(speed,p)==pytest.approx(x,abs=3e-7)
    if positive:assert stopping_distance_bound(speed,p)>make_stop_plan(speed,'0',p).distance_m


def test_grid_never_bridges_near_or_interior_hole_and_retains_positive():
    p=CorridorParametersV2(ground_reference_z_m=0.,ground_reference_source='authored')
    for missing,expected in [((0,0),0.),((3,1),3.),((12,2),12.)]:
        points=full_grid(omit=(missing,))+[(6.,0.,1.)]
        o=grid_observation(points,p)
        assert o.coverage.qualified_prefix_m==expected
        assert o.positive_surface.distance_from_bumper_m==3.5
        assert o.coverage.expected_cells==39 and o.coverage.qualified_cells==38
        assert len(o.coverage.unknown_regions)==1


def test_ground_tolerance_cell_boundary_and_partial_final_row():
    p=CorridorParametersV2(ground_reference_z_m=0.,ground_reference_source='authored')
    points=[(2.5,-1.2,.18),(2.5,-.4,-.18),(2.5,.4,0.),(15.,1.2,0.),(15.001,0.,0.),(3.,0.,.181)]
    o=grid_observation(points,p)
    assert [o.coverage.cells[j].ground_hits for j in range(3)]==[1,1,1]
    assert o.coverage.cells[-1].ground_hits==1
    assert o.coverage.cells[-1].x_bounds_m==(14.5,15.)
    assert o.ground_points==4 and o.coverage.qualified_prefix_m==0


def test_fixed_grid_cannot_be_tuned_in_place():
    for override in [{'min_ground_hits_per_cell':1},{'longitudinal_cell_m':2.},{'lateral_cell_m':1.},{'corridor_half_width_m':2.}]:
        with pytest.raises(ValidationError):CorridorParametersV2(**override)


def test_invalid_does_not_preserve_positive_as_trusted():
    p=CorridorParametersV2(ground_reference_z_m=0.,ground_reference_source='authored')
    o=grid_observation(full_grid()+[(3.,0.,1.)],p,['lidar_stale'])
    assert not o.input_valid and o.positive_surface is None
    assert o.coverage.qualified_prefix_m is None and o.coverage.qualified_cells==0
    assert len(o.coverage.cells)==39 and len(o.coverage.unknown_regions)==39


def test_normal_motion_and_unknown_latch_cannot_restart():
    s=session();p,b=packet(full_grid());first=s.step(p,b)
    assert first.decision.mode=='pilot_cruise'
    p,b=packet(full_grid(omit=((0,1),)),sequence=1);unknown=s.step(p,b)
    assert unknown.decision.mode=='unknown_stop' and unknown.control.brake==1
    p,b=packet(full_grid(),sequence=2);last=s.step(p,b)
    assert last.decision.mode=='unknown_stop' and last.state_before==unknown.state_after
    assert session().step(*packet(full_grid())).decision.mode=='pilot_cruise'


@pytest.mark.parametrize('reason',['missing','future','stale','skew','incomplete','frame','hash','size','nan','empty','tilt','undeclared_brake','undeclared_ground'])
def test_hard_input_gates_force_explicit_max_brake(reason):
    d=definition();p,b=packet(full_grid()+[(3.,0.,1.)])
    if reason=='missing':p.lidar=None
    elif reason=='future':p.lidar.timestamp_ns=str(int(p.at_ns)+1)
    elif reason=='stale':p.lidar.timestamp_ns=str(int(p.at_ns)-100_000_001)
    elif reason=='skew':p.lidar.timestamp_ns=str(int(p.at_ns)-75_000_000)
    elif reason=='incomplete':p.lidar.complete_frame=False
    elif reason=='frame':p.lidar.frame_id='other'
    elif reason=='hash':p.lidar.points_sha256='0'*64
    elif reason=='size':b=b[:-1]
    elif reason=='nan':
        p,b=packet([(math.nan,0.,0.)])
    elif reason=='empty':p,b=packet([])
    elif reason=='tilt':p.ego.pitch_rad=.081
    elif reason=='undeclared_brake':d.controller.parameters={}
    elif reason=='undeclared_ground':d.perception.parameters={}
    out=session(d).step(p,b)
    assert out.decision.mode=='unknown_stop' and out.control.brake==1 and out.control.throttle==0
    if reason=='undeclared_brake':
        assert out.observation.input_valid and out.observation.positive_surface is not None
        assert 'brake_response_assumption_unknown' in out.decision.reasons
    else:
        assert not out.observation.input_valid and out.observation.positive_surface is None


def test_current_envelope_emergency_and_cruise_margin_controlled_stop():
    # Clear positive surfaces at independent distances; no GT actor identity.
    near=session().step(*packet(full_grid()+[(4.5,0.,1.)]))
    assert near.decision.mode=='emergency_stop'
    p=HeadwayParametersV2();required=stopping_distance_bound(2.,p)
    observed_distance=required+p.reserve_m+p.controlled_stop_margin_m*.5
    controlled=session().step(*packet(full_grid()+[(observed_distance+2.5,0.,1.)]))
    assert controlled.decision.mode=='controlled_stop' and not controlled.decision.force_brake
    assert controlled.decision.current_stopping_distance_m<=controlled.decision.available_stopping_distance_m
    assert controlled.state_after.latched and controlled.decision.stop_plan.initial_acceleration_mps2==0


def test_controlled_stop_ideal_tracking_complete_then_hold_no_restart():
    s=session();first=s.step(*packet(full_grid()+[(8.,0.,1.)]));assert first.decision.mode=='controlled_stop'
    plan=first.decision.stop_plan
    for seq in range(1,45):
        elapsed=seq*.05;v=plan_sample(plan,elapsed)[0]
        out=s.step(*packet(full_grid(),sequence=seq,speed=v))
        assert out.control.throttle==0
    assert out.decision.mode=='hold' and out.state_after.stop_plan==plan


def test_plan_expiry_and_tracking_error_do_not_claim_standstill():
    s=session(definition(max_step_s=1.,max_speed_tracking_error_mps=3.))
    first=s.step(*packet(full_grid()+[(8.,0.,1.)]));plan=first.decision.stop_plan
    for i in range(1,4):out=s.step(*packet(full_grid(),sequence=i,at_ns=1_000_000_000+i*800_000_000,speed=.1 if i==3 else 2.))
    assert out.decision.mode=='emergency_stop' and 'plan_expired' in out.decision.reasons[0]
    s=session();s.step(*packet(full_grid()+[(8.,0.,1.)]))
    out=None
    for seq in range(1,40):
        out=s.step(*packet(full_grid(),sequence=seq,speed=2.))
        if out.decision.force_brake:break
    assert out.decision.mode=='emergency_stop' and 'tracking' in out.decision.reasons[0]


@pytest.mark.parametrize('sequence,at',[(0,1_050_000_000),(2,1_050_000_000),(1,1_000_000_000),(1,999_999_999)])
def test_history_violation_poison_session(sequence,at):
    s=session();s.step(*packet(full_grid()))
    with pytest.raises(StackInputError):s.step(*packet(full_grid(),sequence=sequence,at_ns=at))
    with pytest.raises(StackExecutionError,match='original prefix'):s.step(*packet(full_grid(),sequence=1))


def test_dt_and_positive_acceleration_limit_stop():
    s=session();s.step(*packet(full_grid()))
    out=s.step(*packet(full_grid(),sequence=1,speed=2.1))
    assert out.decision.observed_speed_acceleration_mps2==pytest.approx(2.)
    assert out.decision.mode=='unknown_stop'
    s=session();s.step(*packet(full_grid()))
    out=s.step(*packet(full_grid(),sequence=1,at_ns=1_500_000_000))
    assert out.decision.mode=='unknown_stop' and out.decision.applied_dt_s==.5


@pytest.mark.parametrize('mutation',['prefix','count','bounds','missing_cell','unknown_region','surface','plan','state','control','time','missing_status'])
def test_serialized_output_rejects_internal_forgery(mutation):
    out=session().step(*packet(full_grid()+[(8.,0.,1.)])).model_dump(mode='json')
    if mutation=='prefix':out['observation']['coverage']['qualified_prefix_m']=13.
    elif mutation=='count':out['observation']['coverage']['qualified_cells']=0
    elif mutation=='bounds':out['observation']['coverage']['cells'][0]['x_bounds_m'][0]=0.
    elif mutation=='missing_cell':out['observation']['coverage']['cells'].pop()
    elif mutation=='unknown_region':out['observation']['coverage']['unknown_regions']=[{'x_bounds_m':[0.,1.],'y_bounds_m':[-1.,1.],'reasons':['fabricated']}]
    elif mutation=='surface':out['observation']['positive_surface']['distance_from_bumper_m']=999.
    elif mutation=='plan':out['decision']['stop_plan']['distance_m']=0.
    elif mutation=='state':out['state_after']['latched']=False
    elif mutation=='control':out['decision']['force_brake']=True
    elif mutation=='time':out['state_after']['last_at_ns']='0'
    elif mutation=='missing_status':del out['observation']['input_valid']
    with pytest.raises((ValueError,ValidationError)):validate_stack_output(out)


def test_plugin_metadata_mutation_or_fabricated_actual_grid_fails():
    class Changed(LidarCorridorV2):
        changed=False
        def observe(self,*args):self.changed=True;return super().observe(*args)
        def metadata(self):
            m=super().metadata()
            if self.changed:m.name='different'
            return m
    r=StackRegistry(load_plugins=False);r.register(Changed(),replace=True)
    with pytest.raises(StackExecutionError,match='metadata'):StackSession(definition(),r).step(*packet(full_grid()))
    class Fabricated(LidarCorridorV2):
        def observe(self,packet,points,parameters):return super().observe(packet,tuple(full_grid()),parameters)
    r=StackRegistry(load_plugins=False);r.register(Fabricated(),replace=True)
    with pytest.raises(StackExecutionError,match='source geometry'):StackSession(definition(),r).step(*packet(full_grid(omit=((0,0),))))


def test_controller_assumption_source_required_and_no_acceleration_during_stop():
    with pytest.raises(ValidationError):ControlParametersV2(assumed_full_brake_deceleration_mps2=8.)
    first=session().step(*packet(full_grid()+[(8.,0.,1.)]))
    d=first.decision.model_copy(deep=True);d.target_speed_mps=10.
    c=ProportionalControlV2().control(packet(full_grid(),speed=1.)[0],d,{'assumed_full_brake_deceleration_mps2':8.,'brake_response_source':'test'})
    assert c.throttle==0

@pytest.mark.parametrize('actual_brake_gain,expected',[(8.,'hold'),(2.,'emergency_stop')])
def test_independent_actuator_plant_strong_vs_weak_model(actual_brake_gain,expected):
    # Independent sampled plant: dv/dt=-actual_gain*brake, with no use of desired
    # acceleration or plan_sample. Weak braking must not be reported as a stop.
    s=session();v=2.;out=None
    for seq in range(90):
        points=full_grid()+([(8.,0.,1.)] if seq==0 else [])
        out=s.step(*packet(points,sequence=seq,speed=v))
        v=max(0.,v-actual_brake_gain*out.control.brake*.05)
        if out.decision.mode in ('hold','emergency_stop'):break
    assert out.decision.mode==expected
    if expected=='hold':assert out.state_after.previous_speed_mps<=.03
    else:assert out.control.brake==1 and 'tracking' in out.decision.reasons[0]


def test_new_invalid_reason_retained_after_previous_latch():
    s=session();s.step(*packet(full_grid()+[(3.,0.,1.)]))
    p,b=packet(full_grid(),sequence=1);p.lidar=None
    out=s.step(p,b)
    assert out.decision.mode=='unknown_stop'
    assert 'lidar_unavailable' in out.observation.reasons
    assert 'lidar_unavailable' in out.warnings


def test_retained_source_checker_rejects_coherent_grid_and_distance_forgery():
    from ade2e.stack_v2_runtime import validate_v2_source_evidence
    d=definition();p,b=packet(full_grid());out=session(d).step(p,b)
    assert validate_v2_source_evidence(d,p,b,out)==out
    forged=out.model_dump(mode='json')
    # Changing a nonnearest point extent remains internally well formed but is
    # false relative to actual consumed bytes.
    forged['observation']['coverage']['cells'][0]['point_min_xyz_m'][0]+=.01
    valid=StackOutputV2.model_validate(forged)
    with pytest.raises(ValueError,match='actual consumed'):validate_v2_source_evidence(d,p,b,valid)
    forged=out.model_dump(mode='json');forged['decision']['current_stopping_distance_m']=.001
    valid=StackOutputV2.model_validate(forged)
    with pytest.raises(ValueError,match='pinned model'):validate_v2_source_evidence(d,p,b,valid)


@pytest.mark.parametrize('field,value',[('target_acceleration_mps2',-7.),('target_jerk_mps3',10.),('plan_elapsed_s',.02),('speed_tracking_error_mps',8.)])
def test_plan_phase_and_tracking_cannot_be_forged(field,value):
    out=session().step(*packet(full_grid()+[(8.,0.,1.)])).model_dump(mode='json')
    out['decision'][field]=value
    with pytest.raises(ValueError):StackOutputV2.model_validate(out)


def test_source_guard_catches_forged_motion_after_positive_acceleration():
    from ade2e.stack_v2_runtime import validate_v2_source_evidence
    d=definition();s=session(d);first=s.step(*packet(full_grid()))
    p,b=packet(full_grid(),sequence=1,speed=2.1);out=s.step(p,b)
    assert out.decision.mode=='unknown_stop'
    forged=out.model_dump(mode='json')
    forged['decision'].update(mode='pilot_cruise',force_brake=False,target_speed_mps=2.)
    forged['state_after'].update(mode='ready',latched=False,latch_reason=None)
    forged['control'].update(throttle=.1,brake=0.)
    coherent=StackOutputV2.model_validate(forged)
    with pytest.raises(ValueError,match='hard model'):validate_v2_source_evidence(d,p,b,coherent)


@pytest.mark.parametrize('field,value,cap',[
    ('throttle', .500001, 'max_throttle'),
    ('steer', .500001, 'max_steer'),
    ('steer', -.500001, 'max_steer'),
])
def test_compatible_controller_cannot_exceed_pinned_recipe_limits(field,value,cap):
    from ade2e.stack_common_schemas import StackControl
    class ExceedsRecipe(ProportionalControlV2):
        def control(self,*args):
            command={'throttle':0.,'brake':0.,'steer':0.}
            command[field]=value
            return StackControl(**command)
    registry=StackRegistry(load_plugins=False)
    registry.register(ExceedsRecipe(),replace=True)
    s=StackSession(definition(),registry)
    with pytest.raises(StackExecutionError,match=cap):
        s.step(*packet(full_grid(),speed=0.))
    # The rejected command is not published and cannot advance state.
    assert s.state==initial_state()
    with pytest.raises(StackExecutionError,match='original prefix'):
        s.step(*packet(full_grid(),speed=0.))


@pytest.mark.parametrize('field,value,cap',[
    ('throttle', .500001, 'max_throttle'),
    ('steer', .500001, 'max_steer'),
    ('steer', -.500001, 'max_steer'),
])
def test_retained_evidence_rejects_controller_recipe_limit_violation(field,value,cap):
    from ade2e.stack_v2_runtime import validate_v2_source_evidence
    d=definition();p,b=packet(full_grid(),speed=0.)
    payload=session(d).step(p,b).model_dump(mode='json')
    payload['control'].update(throttle=0.,brake=0.,steer=0.)
    payload['control'][field]=value
    # This remains a legal normalized command, but contradicts its saved recipe.
    internally_valid=StackOutputV2.model_validate(payload)
    with pytest.raises(ValueError,match=cap):
        validate_v2_source_evidence(d,p,b,internally_valid)


def test_retained_controlled_stop_rejects_positive_throttle_within_recipe_cap():
    from ade2e.stack_v2_runtime import validate_v2_source_evidence
    d=definition();p,b=packet(full_grid()+[(8.,0.,1.)])
    payload=session(d).step(p,b).model_dump(mode='json')
    assert payload['decision']['mode']=='controlled_stop'
    payload['control'].update(throttle=.1,brake=0.)
    internally_valid=StackOutputV2.model_validate(payload)
    with pytest.raises(ValueError,match='Controlled stop'):
        validate_v2_source_evidence(d,p,b,internally_valid)


def test_missing_packet_evidence_cannot_bypass_pinned_steer_cap():
    from ade2e.stack_v2_runtime import validate_v2_source_evidence
    d=definition();p,b=packet(full_grid());p.lidar=None
    payload=session(d).step(p,b).model_dump(mode='json')
    payload['control']['steer']=1.
    with pytest.raises(ValueError,match='max_steer'):
        validate_v2_source_evidence(d,None,None,payload)


def test_exact_recipe_caps_remain_valid_and_opposed_actuation_is_rejected():
    from ade2e.stack_v2_runtime import validate_v2_source_evidence
    d=definition();p,b=packet(full_grid(),speed=0.)
    payload=session(d).step(p,b).model_dump(mode='json')
    payload['control'].update(throttle=.5,brake=0.,steer=-.5)
    assert validate_v2_source_evidence(d,p,b,payload).control.throttle==.5
    payload['control']['brake']=.1
    with pytest.raises(ValueError,match='Throttle and brake'):
        validate_v2_source_evidence(d,p,b,payload)
