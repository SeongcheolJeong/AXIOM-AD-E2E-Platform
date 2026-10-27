"""Explicit experimental kernel/2 contracts; no defaults fabricate output evidence."""
from __future__ import annotations

from typing import Literal
from pydantic import Field, model_validator

from .base_schemas import Name, StrictModel
from .model_schemas import Sha256
from .sensor_schemas import UnsignedNs
from .stack_common_schemas import Finite, ModuleSelection, StackComponentMetadata, StackControl

KERNEL_VERSION_V2 = 'stack-kernel/2'


class StackDefinitionV2(StrictModel):
    schema_version: Literal['2']
    kernel_version: Literal['stack-kernel/2']
    reference_frame: Literal['ego'] = 'ego'
    perception: ModuleSelection
    policy: ModuleSelection
    controller: ModuleSelection


class GridCellV2(StrictModel):
    longitudinal_index: int = Field(ge=0, le=255, strict=True)
    lateral_index: int = Field(ge=0, le=2, strict=True)
    x_bounds_m: tuple[Finite, Finite]
    y_bounds_m: tuple[Finite, Finite]
    ground_hits: int = Field(ge=0, le=20000, strict=True)
    point_min_xyz_m: tuple[Finite, Finite, Finite] | None
    point_max_xyz_m: tuple[Finite, Finite, Finite] | None
    state: Literal['qualified', 'unknown']
    reasons: list[str]

    @model_validator(mode="after")
    def consistency(self):
        return _grid_cell_check(self)


class UnknownRegionV2(StrictModel):
    x_bounds_m: tuple[Finite, Finite]
    y_bounds_m: tuple[Finite, Finite]
    reasons: list[str]


class GridCoverageV2(StrictModel):
    status: Literal['invalid', 'partial', 'qualified']
    ego_front_x_m: Finite
    lookahead_x_m: Finite
    longitudinal_cell_m: Finite
    lateral_cell_m: Finite
    min_ground_hits_per_cell: int = Field(ge=1, strict=True)
    expected_cells: int = Field(ge=0, le=256, strict=True)
    qualified_cells: int = Field(ge=0, le=256, strict=True)
    qualified_prefix_m: Finite | None
    cells: list[GridCellV2] = Field(max_length=256)
    unknown_regions: list[UnknownRegionV2] = Field(max_length=256)
    sample_model: Literal['ground-hit-grid/1']
    free_space_certified: Literal[False]

    @model_validator(mode="after")
    def consistency(self):
        return _coverage_check(self)


class PositiveSurfaceV2(StrictModel):
    distance_from_bumper_m: Finite = Field(ge=0)
    nearest_point_xyz_m: tuple[Finite, Finite, Finite]
    observed_points: int = Field(ge=1, le=20000, strict=True)
    meaning: Literal['retained_height-band_surface_not_object_identity']


class CorridorObservationV2(StrictModel):
    schema_version: Literal['2']
    input_valid: bool = Field(strict=True)
    status: Literal['invalid', 'sampled']
    input_points: int = Field(ge=0, le=20000, strict=True)
    forward_points: int = Field(ge=0, le=20000, strict=True)
    ground_points: int = Field(ge=0, le=20000, strict=True)
    positive_surface: PositiveSurfaceV2 | None
    coverage: GridCoverageV2
    reasons: list[str]

    @model_validator(mode="after")
    def consistency(self):
        return _observation_check(self)


class StopPlanV2(StrictModel):
    schema_version: Literal['symmetric-s-curve-stop/1']
    started_at_ns: UnsignedNs
    initial_speed_mps: Finite = Field(ge=0)
    jerk_limit_mps3: Finite = Field(gt=0)
    deceleration_limit_mps2: Finite = Field(gt=0)
    peak_deceleration_mps2: Finite = Field(ge=0)
    latency_s: Finite = Field(ge=0)
    ramp_s: Finite = Field(ge=0)
    constant_deceleration_s: Finite = Field(ge=0)
    duration_s: Finite = Field(ge=0)
    distance_m: Finite = Field(ge=0)
    model_status: Literal['assumed_uncalibrated']
    initial_acceleration_mps2: Literal[0.0]
    initial_acceleration_source: Literal['model_assumption_not_measurement']

    @model_validator(mode="after")
    def consistency(self):
        return _plan_check(self)


class StackStateV2(StrictModel):
    mode: Literal['ready', 'controlled_stop', 'hold', 'emergency_stop', 'unknown_stop']
    latched: bool = Field(strict=True)
    latch_reason: str | None
    stop_plan: StopPlanV2 | None
    last_sequence: int | None = Field(ge=0, strict=True)
    last_at_ns: UnsignedNs | None
    reset_policy: Literal['new_session_only']
    previous_speed_mps: Finite | None = Field(ge=0, le=100)
    previous_target_acceleration_mps2: Finite | None

    @model_validator(mode="after")
    def consistency(self):
        return _state_check(self)


class MotionEnvelopeV2(StrictModel):
    schema_version: Literal['2']
    mode: Literal['pilot_cruise', 'controlled_stop', 'hold', 'emergency_stop', 'unknown_stop']
    target_speed_mps: Finite = Field(ge=0, le=20)
    target_acceleration_mps2: Finite
    target_jerk_mps3: Finite
    force_brake: bool = Field(strict=True)
    qualified_prefix_m: Finite | None
    positive_surface_distance_m: Finite | None
    limiting_horizon_m: Finite | None
    reserve_m: Finite = Field(ge=0)
    available_stopping_distance_m: Finite | None
    current_stopping_distance_m: Finite | None
    planned_stopping_distance_m: Finite | None
    plan_elapsed_s: Finite | None
    plan_remaining_distance_m: Finite | None
    speed_tracking_error_mps: Finite | None
    applied_dt_s: Finite | None
    observed_speed_acceleration_mps2: Finite | None
    allowed_positive_acceleration_mps2: Finite
    acceleration_source: Literal['speed_norm_finite_difference_or_initial_unknown']
    stopping_bound_model: Literal['positive_accel_latency_jerk_release_then_zero_accel_s_curve/1']
    stop_plan: StopPlanV2 | None
    reasons: list[str]
    model_status: Literal['assumed_uncalibrated']

    @model_validator(mode="after")
    def consistency(self):
        return _decision_check(self)


class StackOutputV2(StrictModel):
    kernel_version: Literal['stack-kernel/2']
    sequence: int = Field(ge=0, le=2**63-1, strict=True)
    at_ns: UnsignedNs
    components: list[StackComponentMetadata] = Field(min_length=3, max_length=3)
    input_points_sha256: Sha256 | None
    observation: CorridorObservationV2
    decision: MotionEnvelopeV2
    control: StackControl
    state_before: StackStateV2
    state_after: StackStateV2
    warnings: list[str]
    elapsed_ms: Finite = Field(ge=0)

    @model_validator(mode="after")
    def consistency(self):
        return _output_check(self)


# Reusable validation is deliberately part of the evidence models: API/native
# readers enforce the same internal identities as a locally executed session.
def _same(a, b):
    import math
    return math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-9)


def _grid_cell_check(self):
    if self.x_bounds_m[0]>=self.x_bounds_m[1] or self.y_bounds_m[0]>=self.y_bounds_m[1]:
        raise ValueError('Grid cell bounds must be increasing')
    if self.ground_hits==0:
        if self.point_min_xyz_m is not None or self.point_max_xyz_m is not None:
            raise ValueError('Empty cell cannot claim point extents')
    else:
        if self.point_min_xyz_m is None or self.point_max_xyz_m is None:
            raise ValueError('Nonempty cell requires actual point extents')
        if any(a>b for a,b in zip(self.point_min_xyz_m,self.point_max_xyz_m)):
            raise ValueError('Cell point extents are reversed')
        for k,bounds in enumerate((self.x_bounds_m,self.y_bounds_m)):
            if self.point_min_xyz_m[k]<bounds[0] or self.point_max_xyz_m[k]>bounds[1]:
                raise ValueError('Cell point extents are outside its bounds')
    if self.state=='qualified' and self.reasons:
        raise ValueError('Qualified cell cannot contain unknown reasons')
    if self.state=='unknown' and not self.reasons:
        raise ValueError('Unknown cell needs a reason')
    return self


def _coverage_check(self):
    import math
    if not (self.lookahead_x_m>self.ego_front_x_m>=0):
        raise ValueError('Grid must extend beyond the nonnegative bumper plane')
    if (self.longitudinal_cell_m,self.lateral_cell_m,self.min_ground_hits_per_cell)!=(1.,.8,2):
        raise ValueError('ground-hit-grid/1 requires fixed 1m/0.8m/min2 sampling')
    rows=math.ceil(self.lookahead_x_m-self.ego_front_x_m)
    if rows*3!=self.expected_cells or len(self.cells)!=self.expected_cells:
        raise ValueError('Grid denominator differs from explicit geometry/cells')
    prefix=0.;open_prefix=True
    for i in range(rows):
        row=self.cells[i*3:i*3+3]
        for j,c in enumerate(row):
            _grid_cell_check(c)
            bounds=((self.ego_front_x_m+i,min(self.lookahead_x_m,self.ego_front_x_m+i+1)),((-1.2,-.4),(-.4,.4),(.4,1.2))[j])
            if (c.longitudinal_index,c.lateral_index)!=(i,j) or any(not _same(a,b) for actual,expected in zip((c.x_bounds_m,c.y_bounds_m),bounds) for a,b in zip(actual,expected)):
                raise ValueError('Grid row/band ordering or geometry differs')
            expected='qualified' if c.ground_hits>=2 and self.status!='invalid' else 'unknown'
            if c.state!=expected:raise ValueError('Cell state differs from retained hit threshold')
        if open_prefix and all(c.state=='qualified' for c in row):prefix=row[-1].x_bounds_m[1]-self.ego_front_x_m
        else:open_prefix=False
    count=sum(c.state=='qualified' for c in self.cells)
    if count!=self.qualified_cells:raise ValueError('Qualified cell count differs from cells')
    unknown=[UnknownRegionV2(x_bounds_m=c.x_bounds_m,y_bounds_m=c.y_bounds_m,reasons=c.reasons) for c in self.cells if c.state=='unknown']
    if self.unknown_regions!=unknown:raise ValueError('Unknown region denominator differs from cells')
    if self.status=='invalid':
        if self.qualified_prefix_m is not None or count or any(c.ground_hits for c in self.cells):
            raise ValueError('Invalid input cannot claim qualified samples/prefix')
    else:
        expected='qualified' if count==len(self.cells) else 'partial'
        if self.status!=expected or self.qualified_prefix_m is None or not _same(self.qualified_prefix_m,prefix):
            raise ValueError('Coverage status or prefix bridges unknown cells')
    return self


def _observation_check(self):
    _coverage_check(self.coverage)
    if self.input_valid!=(self.status=='sampled') or self.input_valid!=(self.coverage.status!='invalid'):
        raise ValueError('Observation input validity/status/coverage differ')
    if not self.input_valid and (self.positive_surface is not None or self.forward_points or self.ground_points):
        raise ValueError('Invalid input cannot contribute positive/ground evidence')
    if not 0<=self.ground_points<=self.forward_points<=self.input_points:
        raise ValueError('Observation point denominator differs')
    if sum(c.ground_hits for c in self.coverage.cells)!=self.ground_points:
        raise ValueError('Ground hit count differs from full cell denominator')
    if self.positive_surface is not None:
        s=self.positive_surface
        if s.observed_points+self.ground_points>self.forward_points:
            raise ValueError('Disjoint surface and ground counts exceed forward samples')
        x,y,_=s.nearest_point_xyz_m
        if x<0 or x>self.coverage.lookahead_x_m or abs(y)>1.2 or not _same(s.distance_from_bumper_m,max(0.,x-self.coverage.ego_front_x_m)):
            raise ValueError('Positive surface distance or coordinates differ')
    return self


def _plan_check(self):
    import math
    ramp=min(self.deceleration_limit_mps2/self.jerk_limit_mps3,math.sqrt(self.initial_speed_mps/self.jerk_limit_mps3))
    peak=self.jerk_limit_mps3*ramp
    hold=max(0.,self.initial_speed_mps/peak-ramp) if peak else 0.
    duration=self.latency_s+2*ramp+hold
    distance=self.initial_speed_mps*(self.latency_s+(2*ramp+hold)/2)
    for actual,expected in ((self.ramp_s,ramp),(self.peak_deceleration_mps2,peak),(self.constant_deceleration_s,hold),(self.duration_s,duration),(self.distance_m,distance)):
        if not _same(actual,expected):raise ValueError('Stop plan violates its analytic S-curve model')
    return self


def _state_check(self):
    if self.latched!=(self.mode!='ready') or self.latched!=(self.latch_reason is not None):
        raise ValueError('State latch, mode and reason differ')
    if self.mode=='ready' and self.stop_plan is not None:
        raise ValueError('Unlatched state cannot contain a stop plan')
    if self.mode=='controlled_stop' and self.stop_plan is None:
        raise ValueError('Controlled stop requires a preserved plan')
    if (self.last_sequence is None)!=(self.last_at_ns is None):
        raise ValueError('State history sequence/time must coexist')
    if self.last_sequence is None and (self.previous_speed_mps is not None or self.previous_target_acceleration_mps2 is not None):
        raise ValueError('Initial state cannot claim previous measurements')
    if self.stop_plan is not None:_plan_check(self.stop_plan)
    return self


def _decision_check(self):
    prefix=self.qualified_prefix_m;surface=self.positive_surface_distance_m
    if (prefix is not None and prefix<0) or (surface is not None and surface<0):raise ValueError('Negative observed horizon')
    horizon=min(prefix,surface) if prefix is not None and surface is not None else prefix
    if (horizon is None)!=(self.limiting_horizon_m is None) or horizon is not None and not _same(horizon,self.limiting_horizon_m):
        raise ValueError('Limiting horizon differs from prefix and surface')
    available=max(0.,horizon-self.reserve_m) if horizon is not None else None
    if (available is None)!=(self.available_stopping_distance_m is None) or available is not None and not _same(available,self.available_stopping_distance_m):
        raise ValueError('Available stopping distance differs from reserve')
    if self.force_brake!=(self.mode in ('hold','unknown_stop','emergency_stop')):
        raise ValueError('Decision mode contradicts force-brake policy')
    if self.force_brake and any((self.target_speed_mps,self.target_acceleration_mps2,self.target_jerk_mps3)):
        raise ValueError('Force-brake fallback cannot claim a moving plan target')
    if not self.force_brake:
        if available is None or self.current_stopping_distance_m is None or self.current_stopping_distance_m>available+1e-9:
            raise ValueError('Motion lacks a sufficient current stopping envelope')
        if self.mode=='pilot_cruise' and (self.planned_stopping_distance_m is None or self.planned_stopping_distance_m>available+1e-9):
            raise ValueError('Cruise lacks a sufficient planned stopping envelope')
    if self.mode=='controlled_stop' and (self.stop_plan is None or self.plan_elapsed_s is None or self.plan_remaining_distance_m is None):
        raise ValueError('Controlled stop must retain its full plan and timing')
    if self.stop_plan is not None:
        _plan_check(self.stop_plan)
        if self.plan_elapsed_s is not None:
            from .stack_v2_components import plan_sample
            if self.plan_elapsed_s<0:raise ValueError('Plan elapsed time cannot be negative')
            speed,acceleration,jerk,distance=plan_sample(self.stop_plan,self.plan_elapsed_s)
            if self.plan_remaining_distance_m is None or not _same(self.plan_remaining_distance_m,max(0.,self.stop_plan.distance_m-distance)):
                raise ValueError('Plan remaining distance differs from analytic phase')
            if self.mode=='controlled_stop':
                if self.plan_elapsed_s>=self.stop_plan.duration_s:raise ValueError('Expired plan cannot keep controlled motion')
                for actual,expected in ((self.target_speed_mps,speed),(self.target_acceleration_mps2,acceleration),(self.target_jerk_mps3,jerk)):
                    if not _same(actual,expected):raise ValueError('Plan targets differ from retained S-curve phase')
    return self


def _output_check(self):
    _observation_check(self.observation);_decision_check(self.decision)
    _state_check(self.state_before);_state_check(self.state_after)
    if self.state_after.last_sequence!=self.sequence or self.state_after.last_at_ns!=self.at_ns:
        raise ValueError('Output and state sequence/time differ')
    if [m.role for m in self.components]!=['perception','policy','controller'] or any(m.implementation.get('kernel')!='stack-kernel/2' for m in self.components):
        raise ValueError('Output component identities require explicit ordered kernel2 roles')
    if self.decision.force_brake and (self.control.throttle!=0 or self.control.brake!=1):
        raise ValueError('Required maximum brake was not commanded')
    if not self.observation.input_valid and self.decision.mode not in ('unknown_stop','emergency_stop','hold'):
        raise ValueError('Invalid input cannot drive')
    if self.decision.qualified_prefix_m!=self.observation.coverage.qualified_prefix_m:
        raise ValueError('Decision changed observation prefix')
    surface=self.observation.positive_surface.distance_from_bumper_m if self.observation.positive_surface else None
    if self.decision.positive_surface_distance_m!=surface:raise ValueError('Decision changed positive surface')
    if self.state_after.stop_plan!=self.decision.stop_plan:raise ValueError('State lost or changed the decision plan')
    if self.state_before.latched and not self.state_after.latched:raise ValueError('Stop latch cannot reset within a session')
    if self.state_before.stop_plan is not None and self.state_after.stop_plan!=self.state_before.stop_plan:
        raise ValueError('Latched plan cannot silently restart')
    if self.state_before.stop_plan is None and self.state_after.stop_plan is not None:
        if self.state_after.stop_plan.started_at_ns!=self.at_ns or self.state_after.stop_plan.initial_speed_mps!=self.state_after.previous_speed_mps:
            raise ValueError('New stop plan must begin at the current actual speed and aligned time')
    if self.state_after.mode!=('ready' if self.decision.mode=='pilot_cruise' else self.decision.mode):
        raise ValueError('State and decision mode differ')
    dt=None if self.state_before.last_at_ns is None else (int(self.at_ns)-int(self.state_before.last_at_ns))/1e9
    if dt is not None and (dt<=0 or self.sequence!=self.state_before.last_sequence+1):
        raise ValueError('Stateful outputs require consecutive sequence and increasing time')
    if self.decision.applied_dt_s!=dt:raise ValueError('Applied dt differs from prior aligned timestamp')
    if self.state_before.last_sequence is None and self.sequence!=0:raise ValueError('Stateful session starts at sequence0')
    if self.state_before.last_sequence is None and (self.state_before.mode!='ready' or self.state_before.latched or self.state_before.stop_plan is not None):
        raise ValueError('Initial history cannot contain a preexisting stop latch')
    if self.state_after.previous_target_acceleration_mps2!=self.decision.target_acceleration_mps2:
        raise ValueError('State target acceleration differs from decision')
    if self.decision.stop_plan is not None and self.decision.plan_elapsed_s is not None:
        elapsed=(int(self.at_ns)-int(self.decision.stop_plan.started_at_ns))/1e9
        if not _same(elapsed,self.decision.plan_elapsed_s):raise ValueError('Plan elapsed time differs from aligned clock')
    speed=self.state_after.previous_speed_mps
    previous=self.state_before.previous_speed_mps
    observed=None if dt is None or speed is None or previous is None else (speed-previous)/dt
    if (observed is None)!=(self.decision.observed_speed_acceleration_mps2 is None) or observed is not None and not _same(observed,self.decision.observed_speed_acceleration_mps2):
        raise ValueError('Observed speed finite difference differs from history')
    if self.decision.speed_tracking_error_mps is not None:
        if speed is None or self.decision.stop_plan is None or self.decision.plan_elapsed_s is None:
            raise ValueError('Plan tracking error lacks measured speed or model phase')
        from .stack_v2_components import plan_sample
        expected=speed-plan_sample(self.decision.stop_plan,self.decision.plan_elapsed_s)[0]
        if not _same(expected,self.decision.speed_tracking_error_mps):raise ValueError('Plan tracking error differs from speed/phase')
    return self


