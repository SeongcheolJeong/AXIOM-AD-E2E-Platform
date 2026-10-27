"""Version2 sampled-grid pilot and explicit, uncalibrated S-curve stop model.

Ground samples are not a ray visibility mask. Model acceleration is never an
observation of vehicle acceleration or calibrated brake authority.
"""
from __future__ import annotations
import math
from typing import Annotated
from pydantic import Field, model_validator
from .base_schemas import StrictModel
from .stack_common_schemas import Finite, StackComponentMetadata, StackControl
from .stack_v2_schemas import (CorridorObservationV2, GridCoverageV2, GridCellV2,
    UnknownRegionV2, PositiveSurfaceV2, MotionEnvelopeV2, StopPlanV2, StackStateV2)


class CorridorParametersV2(StrictModel):
    corridor_half_width_m: Finite = Field(default=1.2, gt=0)
    ego_front_x_m: Finite = Field(default=2.5, ge=0, le=10)
    lookahead_m: Finite = Field(default=15.0, gt=0, le=80)
    longitudinal_cell_m: Finite = 1.0
    lateral_cell_m: Finite = 0.8
    min_ground_hits_per_cell: int = Field(default=2, strict=True)
    ground_reference_z_m: Finite | None = Field(default=None, ge=-10, le=10)
    ground_reference_source: str = Field(default='', max_length=1000)
    ground_tolerance_m: Finite = Field(default=0.18, gt=0, le=0.5)
    min_obstacle_height_m: Finite = Field(default=0.2, gt=0, le=2)
    max_obstacle_height_m: Finite = Field(default=2.5, gt=0, le=10)
    max_lidar_age_ms: Finite = Field(default=100.0, ge=0, le=1000)
    max_ego_age_ms: Finite = Field(default=100.0, ge=0, le=1000)
    max_input_skew_ms: Finite = Field(default=50.0, ge=0, le=1000)
    max_abs_pitch_roll_rad: Finite = Field(default=0.08, gt=0, le=0.3)

    @model_validator(mode='after')
    def geometry(self):
        if (self.corridor_half_width_m, self.longitudinal_cell_m, self.lateral_cell_m,
                self.min_ground_hits_per_cell) != (1.2, 1.0, 0.8, 2):
            raise ValueError('ground-hit-grid/1 fixes 1m by0.8m cells, width2.4m and min2 hits')
        if self.lookahead_m <= self.ego_front_x_m or math.ceil(self.lookahead_m-self.ego_front_x_m)*3>256:
            raise ValueError('Ground grid must be nonempty and bounded to256 cells')
        if self.ground_reference_z_m is not None and not self.ground_reference_source.strip():
            raise ValueError('An authored ground plane requires an explicit source')
        if not self.ground_tolerance_m < self.min_obstacle_height_m < self.max_obstacle_height_m:
            raise ValueError('Ground and obstacle height bands must be disjoint and ordered')
        return self


class HeadwayParametersV2(StrictModel):
    cruise_speed_mps: Finite = Field(default=2.0, ge=0, le=10)
    reserve_m: Finite = Field(default=2.0, ge=0, le=20)
    response_time_s: Finite = Field(default=0.1, ge=0, le=2)
    assumed_deceleration_mps2: Finite = Field(default=2.0, gt=0, le=8)
    jerk_limit_mps3: Finite = Field(default=2.0, gt=0, le=20)
    allowed_positive_acceleration_mps2: Finite = Field(default=0.5, ge=0, le=4)
    controlled_stop_margin_m: Finite = Field(default=1.0, gt=0, le=10)
    max_speed_tracking_error_mps: Finite = Field(default=0.75, gt=0, le=3)
    standstill_speed_mps: Finite = Field(default=0.03, ge=0, le=0.1)
    max_step_s: Finite = Field(default=0.2, gt=0, le=1)
    max_ego_speed_mps: Finite = Field(default=10.0, gt=0, le=20)
    allow_no_obstacle_observed_motion: bool = Field(default=False, strict=True)

    @model_validator(mode='after')
    def odd(self):
        if self.cruise_speed_mps > self.max_ego_speed_mps:
            raise ValueError('Cruise exceeds declared speed ODD')
        return self


class ControlParametersV2(StrictModel):
    assumed_full_brake_deceleration_mps2: Finite | None = Field(default=None, gt=0, le=20)
    brake_response_source: str = Field(default='', max_length=1000)
    brake_speed_gain: Finite = Field(default=0.3, ge=0, le=5)
    throttle_gain: Finite = Field(default=0.2, ge=0, le=5)
    steering_gain: Finite = Field(default=1.0, ge=0, le=10)
    max_throttle: Finite = Field(default=0.5, ge=0, le=1)
    max_steer: Finite = Field(default=0.5, ge=0, le=1)

    @model_validator(mode='after')
    def source(self):
        if self.assumed_full_brake_deceleration_mps2 is not None and not self.brake_response_source.strip():
            raise ValueError('Assumed brake response requires a nonblank source')
        return self


def initial_state():
    return StackStateV2(mode='ready',latched=False,latch_reason=None,stop_plan=None,
        last_sequence=None,last_at_ns=None,reset_policy='new_session_only',previous_speed_mps=None,
        previous_target_acceleration_mps2=None)


def grid_observation(points, p: CorridorParametersV2, reasons=()):
    invalid=bool(reasons) or p.ground_reference_z_m is None
    reasons=list(dict.fromkeys([*reasons,*(['ground_reference_unknown'] if p.ground_reference_z_m is None else [])]))
    forward=[] if invalid else [v for v in points if 0<=v[0]<=p.lookahead_m and abs(v[1])<=1.2]
    ground=[v for v in forward if v[0]>=p.ego_front_x_m and abs(v[2]-p.ground_reference_z_m)<=p.ground_tolerance_m]
    obstacles=[v for v in forward if p.min_obstacle_height_m<=v[2]-p.ground_reference_z_m<=p.max_obstacle_height_m]
    row_count=math.ceil(p.lookahead_m-p.ego_front_x_m)
    buckets=[[[] for _ in range(3)] for _ in range(row_count)]
    for point in ground:
        i=min(row_count-1,int((point[0]-p.ego_front_x_m)/p.longitudinal_cell_m))
        j=0 if point[1]<-0.4 else 1 if point[1]<0.4 else 2
        buckets[i][j].append(point)
    cells=[];unknown=[];prefix=0.;prefix_open=True
    for i,row in enumerate(buckets):
        low=p.ego_front_x_m+i;high=min(low+1,p.lookahead_m)
        row_qualified=all(len(v)>=2 for v in row) and not invalid
        if prefix_open and row_qualified:prefix=high-p.ego_front_x_m
        else:prefix_open=False
        for j,(yl,yh) in enumerate(((-1.2,-0.4),(-0.4,0.4),(0.4,1.2))):
            values=row[j];qualified=not invalid and len(values)>=2
            cell_reasons=[] if qualified else ['invalid_input'] if invalid else ['fewer_than_two_ground_hits']
            cells.append(GridCellV2(longitudinal_index=i,lateral_index=j,x_bounds_m=(low,high),
                y_bounds_m=(yl,yh),ground_hits=len(values),point_min_xyz_m=tuple(min(v[k] for v in values) for k in range(3)) if values else None,
                point_max_xyz_m=tuple(max(v[k] for v in values) for k in range(3)) if values else None,
                state='qualified' if qualified else 'unknown',reasons=cell_reasons))
            if not qualified:unknown.append(UnknownRegionV2(x_bounds_m=(low,high),y_bounds_m=(yl,yh),reasons=cell_reasons))
    qualified=sum(c.state=='qualified' for c in cells)
    nearest=min(obstacles,key=lambda v:(v[0],v[1],v[2])) if obstacles else None
    return CorridorObservationV2(schema_version='2',input_valid=not invalid,status='invalid' if invalid else 'sampled',
        input_points=len(points),forward_points=len(forward),ground_points=len(ground),
        positive_surface=PositiveSurfaceV2(distance_from_bumper_m=max(0.,nearest[0]-p.ego_front_x_m),
            nearest_point_xyz_m=nearest,observed_points=len(obstacles),meaning='retained_height-band_surface_not_object_identity') if nearest else None,
        coverage=GridCoverageV2(status='invalid' if invalid else 'qualified' if qualified==len(cells) else 'partial',
            ego_front_x_m=p.ego_front_x_m,lookahead_x_m=p.lookahead_m,longitudinal_cell_m=1.,lateral_cell_m=.8,
            min_ground_hits_per_cell=2,expected_cells=len(cells),qualified_cells=qualified,
            qualified_prefix_m=None if invalid else prefix,cells=cells,unknown_regions=unknown,
            sample_model='ground-hit-grid/1',free_space_certified=False),reasons=reasons)


def make_stop_plan(speed:float, at_ns:str, p:HeadwayParametersV2):
    speed=float(speed)
    ramp=min(p.assumed_deceleration_mps2/p.jerk_limit_mps3,math.sqrt(speed/p.jerk_limit_mps3))
    peak=p.jerk_limit_mps3*ramp
    hold=max(0.,speed/peak-ramp) if peak else 0.
    duration=2*ramp+hold
    return StopPlanV2(schema_version='symmetric-s-curve-stop/1',started_at_ns=at_ns,initial_speed_mps=speed,
        jerk_limit_mps3=p.jerk_limit_mps3,deceleration_limit_mps2=p.assumed_deceleration_mps2,
        peak_deceleration_mps2=peak,latency_s=p.response_time_s if speed else 0.,ramp_s=ramp,
        constant_deceleration_s=hold,duration_s=duration+(p.response_time_s if speed else 0.),
        distance_m=speed*(duration/2+(p.response_time_s if speed else 0.)),
        model_status='assumed_uncalibrated',initial_acceleration_mps2=0.,initial_acceleration_source='model_assumption_not_measurement')


def plan_sample(plan:StopPlanV2, elapsed:float):
    """Analytic integration of the three jerk phases, plus explicit latency."""
    t=max(0.,elapsed);v=plan.initial_speed_mps;a=0.;distance=0.
    duration=min(t,plan.latency_s);distance+=v*duration;t-=duration
    phases=((plan.ramp_s,-plan.jerk_limit_mps3),(plan.constant_deceleration_s,0.),(plan.ramp_s,plan.jerk_limit_mps3))
    jerk=0.
    for length,j in phases:
        if t<=0:break
        used=min(t,length);distance+=v*used+a*used**2/2+j*used**3/6
        v+=a*used+j*used**2/2;a+=j*used;t-=used;jerk=j
        if used<length:break
    if elapsed>=plan.duration_s:
        return 0.,0.,0.,plan.distance_m
    # At exact phase boundaries report the next phase's jerk, a right-limit.
    tt=max(0.,elapsed-plan.latency_s)
    if elapsed<plan.latency_s:jerk=0.
    elif tt<plan.ramp_s:jerk=-plan.jerk_limit_mps3
    elif tt<plan.ramp_s+plan.constant_deceleration_s:jerk=0.
    else:jerk=plan.jerk_limit_mps3
    return max(0.,v),min(0.,a),jerk,min(plan.distance_m,max(0.,distance))


def stopping_distance_bound(speed:float,p:HeadwayParametersV2):
    """Bound includes permitted positive acceleration and its jerk-limited release.

    +a persists through latency, then jerk -J reduces it to0 (with additional
    distance/speed), then the zero-acceleration S-curve stops that larger speed.
    This is an assumed model, not a guaranteed continuous vehicle bound.
    """
    latency=p.response_time_s;positive=p.allowed_positive_acceleration_mps2
    delayed_speed=speed+positive*latency
    distance=speed*latency+.5*positive*latency**2
    release=positive/p.jerk_limit_mps3
    distance+=delayed_speed*release+.5*positive*release**2-p.jerk_limit_mps3*release**3/6
    released_speed=delayed_speed+positive*release-.5*p.jerk_limit_mps3*release**2
    curve=make_stop_plan(released_speed,'0',p)
    return distance+curve.distance_m-released_speed*curve.latency_s


class _V2Component:
    version='2'
    def metadata(self):
        return StackComponentMetadata(id=self.id,name=self.name,version='2',role=self.role,
            parameter_schema=self.parameters.model_json_schema(),default_parameters=self.parameters().model_dump(mode='json'),
            implementation={'package':'ade2e','algorithm':self.id+'/2','kernel':'stack-kernel/2'},
            limitations=self.limitations)
    def validate_parameters(self,parameters):return self.parameters.model_validate(parameters).model_dump(mode='json')


class LidarCorridorV2(_V2Component):
    id='lidar-corridor';name='Sampled ground grid and positive surface';role='perception';parameters=CorridorParametersV2
    limitations=['Fixed hit-count cells are an experimental sampling rule, not free-space or road certification.',
        'Missing cells are unknown; far returns never fill interior gaps. No temporal accumulation.',
        'The ground plane is an explicit uncalibrated assumption; low objects, occlusion and self-returns remain limitations.']
    def observe(self,packet,points,parameters):return grid_observation(points,CorridorParametersV2.model_validate(parameters))


class StoppingHeadwayV2(_V2Component):
    id='stopping-headway';name='Latched S-curve stopping envelope';role='policy';parameters=HeadwayParametersV2
    limitations=['The S-curve starts at assumed acceleration0, not a measured acceleration state.',
        'Positive acceleration during latency is bounded by an authored assumption; finite-difference checks cannot certify continuous bounds.',
        'Stopping distance and jerk describe an uncalibrated desired plan, not demonstrated vehicle capability.',
        'Any stop latches until a new session; a single new return cannot restart motion.']
    def plan(self,packet,observation,parameters,*,state=None,dt_s=None):
        p=HeadwayParametersV2.model_validate(parameters);state=state or initial_state()
        speed=packet.ego.speed_mps if packet.ego else 0.
        prefix=observation.coverage.qualified_prefix_m
        surface=observation.positive_surface.distance_from_bumper_m if observation.positive_surface else None
        horizon=min(prefix,surface) if prefix is not None and surface is not None else prefix
        available=max(0.,horizon-p.reserve_m) if horizon is not None else None
        current=stopping_distance_bound(speed,p);planned=stopping_distance_bound(max(speed,p.cruise_speed_mps),p)
        acceleration=(speed-state.previous_speed_mps)/dt_s if dt_s and state.previous_speed_mps is not None and packet.ego else None
        common=dict(schema_version='2',qualified_prefix_m=prefix,positive_surface_distance_m=surface,
            limiting_horizon_m=horizon,reserve_m=p.reserve_m,available_stopping_distance_m=available,
            current_stopping_distance_m=current,planned_stopping_distance_m=planned,plan_elapsed_s=None,
            plan_remaining_distance_m=None,speed_tracking_error_mps=None,stop_plan=state.stop_plan,
            applied_dt_s=dt_s,observed_speed_acceleration_mps2=acceleration,
            allowed_positive_acceleration_mps2=p.allowed_positive_acceleration_mps2,
            acceleration_source='speed_norm_finite_difference_or_initial_unknown',model_status='assumed_uncalibrated',
            stopping_bound_model='positive_accel_latency_jerk_release_then_zero_accel_s_curve/1')
        def result(mode,reason,force=True,target=0.,a=0.,j=0.,**extra):
            return MotionEnvelopeV2(**{**common,**extra},mode=mode,target_speed_mps=target,
                target_acceleration_mps2=a,target_jerk_mps3=j,force_brake=force,reasons=[reason])
        if not observation.input_valid or packet.ego is None:
            return result('unknown_stop','invalid_input')
        if state.latched and state.mode in ('unknown_stop','emergency_stop','hold'):
            return result(state.mode,'latched_'+str(state.latch_reason))
        if speed>p.max_ego_speed_mps:return result('unknown_stop','speed_odd_exceeded')
        if dt_s is not None and dt_s>p.max_step_s:return result('unknown_stop','step_time_limit_exceeded')
        if acceleration is not None and acceleration>p.allowed_positive_acceleration_mps2+1e-9:
            return result('unknown_stop','positive_acceleration_assumption_exceeded')
        if prefix is None or prefix<=0:return result('unknown_stop','near_field_prefix_unknown')
        if horizon is None or horizon<p.reserve_m or current>available+1e-9:
            return result('emergency_stop','current_stopping_envelope_exceeds_horizon')
        plan=state.stop_plan
        if plan is None and not state.latched:
            if speed<=p.standstill_speed_mps and p.cruise_speed_mps==0:
                return result('hold','zero_cruise_hold')
            if observation.positive_surface is None and not p.allow_no_obstacle_observed_motion:
                return result('hold','no_obstacle_motion_not_authorized')
            # Trigger while the entire CURRENT stop still fits. No continuation
            # at a cruise speed whose future stop no longer fits the horizon.
            if planned+p.controlled_stop_margin_m>available:
                plan=make_stop_plan(speed,packet.at_ns,p)
            else:
                return result('pilot_cruise','current_and_cruise_envelopes_with_margin_fit',False,p.cruise_speed_mps)
        if plan is None:return result('unknown_stop','missing_latched_stop_plan')
        elapsed=(int(packet.at_ns)-int(plan.started_at_ns))/1e9
        target,a,j,distance=plan_sample(plan,elapsed)
        error=speed-target;remaining=max(0.,plan.distance_m-distance)
        extra=dict(stop_plan=plan,plan_elapsed_s=elapsed,plan_remaining_distance_m=remaining,speed_tracking_error_mps=error)
        if abs(error)>p.max_speed_tracking_error_mps:
            return result('emergency_stop','stop_plan_speed_tracking_limit_exceeded',**extra)
        if remaining>available+1e-9:
            return result('emergency_stop','remaining_plan_exceeds_horizon',**extra)
        if elapsed>=plan.duration_s:
            if speed>p.standstill_speed_mps:return result('emergency_stop','plan_expired_before_observed_standstill',**extra)
            return result('hold','planned_stop_observed_standstill',**extra)
        return result('controlled_stop','latched_s_curve_stop',False,target,a,j,**extra)


class ProportionalControlV2(_V2Component):
    id='proportional-control';name='Assumed brake-response plan tracking';role='controller';parameters=ControlParametersV2
    limitations=['Brake fraction to deceleration mapping is an explicit uncalibrated assumption, not measured force/torque.',
        'Measured speed feedback does not certify the S-curve acceleration or jerk on an actual vehicle.',
        'Controlled stops never apply positive throttle; stop latch can only reset in a new session.']
    def control(self,packet,decision,parameters):
        p=ControlParametersV2.model_validate(parameters)
        if decision.force_brake or packet.ego is None or p.assumed_full_brake_deceleration_mps2 is None:
            return StackControl(throttle=0.,brake=1.,steer=0.)
        steer=max(-p.max_steer,min(p.max_steer,packet.ego.route_heading_error_rad*p.steering_gain))
        error=packet.ego.speed_mps-decision.target_speed_mps
        if decision.mode=='controlled_stop':
            brake=max(0.,min(1.,-decision.target_acceleration_mps2/p.assumed_full_brake_deceleration_mps2+p.brake_speed_gain*error))
            return StackControl(throttle=0.,brake=brake,steer=steer)
        return StackControl(throttle=min(p.max_throttle,max(0.,-error*p.throttle_gain)),
            brake=min(1.,max(0.,error*p.brake_speed_gain)),steer=steer)


def v2_components():return (LidarCorridorV2(),StoppingHeadwayV2(),ProportionalControlV2())
