"""Bounded actor/behavior contract; independent of legacy scenario v1."""
from typing import Annotated, Literal
from pydantic import Field, StringConstraints, model_validator, model_serializer
from .base_schemas import Name, StrictModel
from .object_behavior_schemas import ObjectBehaviorPlan, ObjectPlannerSettings

Identity = Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9_-]{1,64}$')]
Number = Annotated[float, Field(ge=-10000, le=10000)]

class Pose(StrictModel):
    x: Number
    y: Number
    heading_rad: float = Field(default=0, ge=-6.284, le=6.284)

class Dimensions(StrictModel):
    length_m: float = Field(default=4.5, gt=0, le=30)
    width_m: float = Field(default=1.8, gt=0, le=5)
    height_m: float = Field(default=1.5, gt=0, le=5)

class Waypoint(Pose):
    time_s: float = Field(ge=0, le=60)

class Trigger(StrictModel):
    kind: Literal['time', 'distance', 'object_state'] = 'time'
    value: float = Field(default=0, ge=0, le=10000)
    actor_id: Identity | None = None
    target_actor_id: Identity | None = None
    rule: Literal['greaterThan', 'lessThan'] = 'greaterThan'
    state: Literal['speed'] = 'speed'

    @model_validator(mode='after')
    def references(self):
        if self.kind != 'time' and self.actor_id is None:
            raise ValueError('Actor triggers require actor_id')
        if self.kind == 'distance' and self.target_actor_id is None:
            raise ValueError('Distance trigger requires target_actor_id')
        return self

class Action(StrictModel):
    kind: Literal['speed', 'stop', 'lane_change', 'follow_path']
    trigger: Trigger = Field(default_factory=Trigger)
    target_speed_mps: float | None = Field(default=None, ge=0, le=60)
    target_lane: int | None = Field(default=None, ge=-2, le=-1)
    transition_s: float = Field(default=1, gt=0, le=20)
    path: list[Waypoint] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def action_payload(self):
        if self.kind == 'speed' and self.target_speed_mps is None:
            raise ValueError('Speed action requires target_speed_mps')
        if self.kind == 'lane_change' and self.target_lane is None:
            raise ValueError('Lane change requires target_lane')
        if self.kind == 'follow_path':
            if len(self.path) < 2 or any(a.time_s >= b.time_s for a,b in zip(self.path,self.path[1:])):
                raise ValueError('Path requires at least two strictly increasing timed waypoints')
        elif self.path:
            raise ValueError('Path only belongs to follow_path')
        if self.kind != 'speed' and self.target_speed_mps is not None:
            raise ValueError('Speed target only belongs to speed action')
        if self.kind != 'lane_change' and self.target_lane is not None:
            raise ValueError('Lane target only belongs to lane_change')
        return self

class Actor(StrictModel):
    id: Identity
    kind: Literal['vehicle','pedestrian'] = 'vehicle'
    role: Literal['ego','other'] = 'other'
    pose: Pose
    dimensions: Dimensions = Field(default_factory=Dimensions)
    speed_mps: float = Field(default=0, ge=0, le=60)
    route: list[Pose] = Field(default_factory=list, max_length=32)
    actions: list[Action] = Field(default_factory=list, max_length=16)

class MapReference(StrictModel):
    kind: Literal['authored_straight_road'] = 'authored_straight_road'
    id: Literal['axiom-straight-road-r1'] = 'axiom-straight-road-r1'
    length_m: float = Field(default=500, ge=100, le=2000)
    lane_width_m: float = Field(default=3.5, ge=2.5, le=5)

class Environment(StrictModel):
    lighting: Literal['day'] = 'day'
    weather: Literal['dry'] = 'dry'
    # Object kinematics has no physical optical/weather effects.

class Observer(StrictModel):
    kind: Literal['collision','minimum_distance','minimum_ttc','lane_departure','maximum_speed','maximum_acceleration','maximum_jerk','goal_arrival']
    actor_id: Identity = 'ego'
    threshold: float | None = Field(default=None, ge=0, le=10000)
    goal: Pose | None = None

    @model_validator(mode='after')
    def goal_payload(self):
        if self.kind == 'goal_arrival' and (self.goal is None or self.threshold is None):
            raise ValueError('Goal observer requires goal and threshold distance')
        return self

class SweepAxis(StrictModel):
    actor_id: Identity
    field: Literal['speed_mps','pose.x','pose.y']
    values: list[float] = Field(min_length=1, max_length=8)

class ScenarioV2(StrictModel):
    schema_version: Literal['scenario-v2/1'] = 'scenario-v2/1'
    name: Name
    template: Literal['lane_follow','cut_in','emergency_brake','pedestrian_crossing','merge','lead_stop','traffic_interaction'] | None = None
    map: MapReference = Field(default_factory=MapReference)
    coordinate_system: Literal['world_m_x_forward_y_left_z_up'] = 'world_m_x_forward_y_left_z_up'
    duration_s: float = Field(default=8, ge=1, le=30)
    timestep_s: float = Field(default=.1, ge=.02, le=.2)
    actors: list[Actor] = Field(min_length=1, max_length=8)
    environment: Environment = Field(default_factory=Environment)
    observers: list[Observer] = Field(default_factory=list, max_length=32)
    sweep: list[SweepAxis] = Field(default_factory=list, max_length=3)

    @model_validator(mode='after')
    def valid_links(self):
        ids = [a.id for a in self.actors]
        if len(set(ids)) != len(ids) or sum(a.role == 'ego' for a in self.actors) != 1:
            raise ValueError('Unique actors and exactly one ego are required')
        for actor in self.actors:
            for action in actor.actions:
                for ref in (action.trigger.actor_id, action.trigger.target_actor_id):
                    if ref and ref not in ids: raise ValueError('Trigger references an unknown actor')
                if action.kind == 'follow_path' and action.path[-1].time_s > self.duration_s:
                    raise ValueError('Path exceeds scenario duration')
            if actor.kind == 'pedestrian' and (actor.speed_mps != 0 or actor.route):
                raise ValueError('Pedestrian motion requires an explicit timed follow_path action')
        for observer in self.observers:
            if observer.actor_id not in ids: raise ValueError('Observer references unknown actor')
        total=1
        for axis in self.sweep:
            if axis.actor_id not in ids: raise ValueError('Sweep references unknown actor')
            total *= len(axis.values)
        if total > 64: raise ValueError('Sweep exceeds 64 configurations')
        if len({(a.actor_id,a.field) for a in self.sweep}) != len(self.sweep):
            raise ValueError('Sweep axes cannot repeat')
        return self

class CatalogReference(StrictModel):
    id: Identity
    revision: int = Field(ge=1)
    sha256: Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]

class CityPose(Pose):
    z: Number = 0

class CityWaypoint(CityPose):
    time_s: float = Field(ge=0, le=120)

class CityController(StrictModel):
    kind: Literal['timed_replay', 'route_follow', 'traffic', 'autoware', 'static', 'reactive']
    ref: CatalogReference | None = None
    end_behavior: Literal['stop', 'repeat'] = 'stop'
    headway_s: float = Field(default=2, ge=.5, le=10)
    obey_signals: bool = True
    wait_s: float = Field(default=0, ge=0, le=120)
    stop_time_s: float | None = Field(default=None, ge=0, le=120)
    required_gap_m: float = Field(default=0, ge=0, le=100)

    @model_serializer(mode='wrap')
    def compatible_defaults(self,serializer):
        value=serializer(self)
        if self.required_gap_m==0:value.pop('required_gap_m',None)
        return value

class CityActor(StrictModel):
    id: Identity
    kind: Literal['vehicle', 'pedestrian', 'static'] = 'vehicle'
    role: Literal['ego', 'other'] = 'other'
    asset: CatalogReference
    pose: CityPose
    route: list[CityPose] = Field(default_factory=list, max_length=2000)
    speed_mps: float = Field(default=3, ge=0, le=30)
    controller: CityController
    trajectory: list[CityWaypoint] = Field(default_factory=list, max_length=2400)
    route_mode: Literal['native_lane_graph','authored_lane_change'] = 'native_lane_graph'

    @model_serializer(mode='wrap')
    def compatible_defaults(self,serializer):
        value=serializer(self)
        if self.route_mode=='native_lane_graph':value.pop('route_mode',None)
        if value.get('destination') is None:value.pop('destination',None)
        return value

    @model_validator(mode='after')
    def motion_contract(self):
        mode = self.controller.kind
        if self.route_mode=='authored_lane_change' and (self.kind!='vehicle' or mode not in {'route_follow','traffic'}):
            raise ValueError('Authored lane changes require a physical vehicle route-follow or traffic controller')
        if self.controller.required_gap_m and self.route_mode!='authored_lane_change':
            raise ValueError('Required lane-change gap belongs only to authored_lane_change routes')
        if mode == 'timed_replay':
            if len(self.trajectory) < 2 or any(a.time_s >= b.time_s for a,b in zip(self.trajectory,self.trajectory[1:])):
                raise ValueError('Timed replay requires at least two strictly increasing timed waypoints')
            if self.route:
                raise ValueError('Timed replay has a trajectory, not a second route control owner')
        elif self.trajectory:
            raise ValueError('Timed trajectories require the timed_replay controller')
        if mode in {'route_follow','traffic','autoware','reactive'} and len(self.route) < 2:
            raise ValueError('Route controllers require at least two route points')
        if mode == 'autoware' and (self.role != 'ego' or self.kind != 'vehicle' or self.controller.ref is None):
            raise ValueError('Autoware requires an ego vehicle and a pinned controller revision')
        if mode == 'static' and (self.speed_mps != 0 or self.route or self.trajectory):
            raise ValueError('Static actors must have zero speed and no motion path')
        if self.kind == 'static' and mode != 'static':
            raise ValueError('Static assets require the static controller')
        if self.kind == 'pedestrian' and mode not in {'route_follow','timed_replay','static','reactive'}:
            raise ValueError('Pedestrians support route following, timed replay or static placement')
        return self

class CitySensor(StrictModel):
    id: Identity
    actor_id: Identity = 'ego'
    kind: Literal['camera_rgb'] = 'camera_rgb'
    pose: CityPose = Field(default_factory=lambda:CityPose(x=1.5,y=0,z=1.7))
    width: int = Field(default=640, ge=64, le=1920)
    height: int = Field(default=360, ge=64, le=1080)
    fov_deg: float = Field(default=90, ge=10, le=140)
    period_s: float = Field(default=.1, ge=.02, le=2)

class CityAcceptance(StrictModel):
    max_lateral_error_p95_m: float = Field(default=.5, gt=0, le=20)
    max_goal_error_m: float = Field(default=2, gt=0, le=100)
    max_final_speed_mps: float = Field(default=.2, ge=0, le=30)
    require_goal: bool = True
    require_no_contacts: bool = True

class CitySignalChange(StrictModel):
    time_s: float = Field(gt=0, le=120)
    state: Literal['red','yellow','green']

class CitySignalSchedule(StrictModel):
    id: Annotated[str,StringConstraints(min_length=1,max_length=160)]
    initial_state: Literal['red','yellow','green']
    changes: list[CitySignalChange] = Field(default_factory=list,max_length=32)

    @model_validator(mode='after')
    def chronological(self):
        if any(a.time_s>=b.time_s for a,b in zip(self.changes,self.changes[1:])):
            raise ValueError('Signal changes must have unique strictly increasing times')
        return self

class ScenarioCity(StrictModel):
    schema_version: Literal['scenario-v2/2'] = 'scenario-v2/2'
    name: Name
    description: str = Field(default='', max_length=4000)
    seed: int = Field(default=42, ge=0, le=2**31-1, strict=True)
    map: CatalogReference
    coordinate_system: Literal['carla_world_m_x_forward_y_right_z_up'] = 'carla_world_m_x_forward_y_right_z_up'
    duration_s: float = Field(default=20, ge=1, le=120)
    timestep_s: float = Field(default=.05, ge=.02, le=.1)
    actors: list[CityActor] = Field(min_length=1, max_length=32)
    sensors: list[CitySensor] = Field(default_factory=list, max_length=8)
    environment: Environment = Field(default_factory=Environment)
    observers: list[Observer] = Field(default_factory=list, max_length=64)
    acceptance: CityAcceptance = Field(default_factory=CityAcceptance)
    signals: list[CitySignalSchedule] = Field(default_factory=list,max_length=128)

    @model_serializer(mode='wrap')
    def compatible_defaults(self,serializer):
        value=serializer(self)
        if not self.signals:value.pop('signals',None)
        return value

    @model_validator(mode='after')
    def city_links(self):
        ids = [a.id for a in self.actors]
        if self.schema_version == 'scenario-v2/2' and any(a.controller.kind=='reactive' for a in self.actors):
            raise ValueError('Reactive controllers require scenario-v2/3')
        if len(set(ids)) != len(ids) or sum(a.role == 'ego' for a in self.actors) != 1:
            raise ValueError('Unique actors and exactly one ego are required')
        if any(a.trajectory and a.trajectory[-1].time_s > self.duration_s for a in self.actors):
            raise ValueError('Timed trajectory exceeds scenario duration')
        if any(a.controller.wait_s >= self.duration_s or (a.controller.stop_time_s is not None and
               not a.controller.wait_s <= a.controller.stop_time_s <= self.duration_s) for a in self.actors):
            raise ValueError('Controller wait and stop times must fit the scenario and be ordered')
        if len({s.id for s in self.sensors}) != len(self.sensors):
            raise ValueError('Sensor IDs must be unique')
        if len({s.id for s in self.signals})!=len(self.signals):
            raise ValueError('Signal schedules must have unique map signal IDs')
        # Commands precede the response tick: the last retained response at
        # duration_s follows the command at duration_s - timestep_s.
        if any(c.time_s>self.duration_s-self.timestep_s+1e-9 for s in self.signals for c in s.changes):
            raise ValueError('Signal changes exceed the last command time (duration_s - timestep_s)')
        if any(s.actor_id not in ids for s in self.sensors) or any(o.actor_id not in ids for o in self.observers):
            raise ValueError('Sensor or observer references an unknown actor')
        for sensor in self.sensors:
            ratio = sensor.period_s / self.timestep_s
            if abs(ratio-round(ratio)) > 1e-6:
                raise ValueError('Sensor period must be an integer multiple of the simulation timestep')
        return self

class ReactiveActor(CityActor):
    behavior: ObjectBehaviorPlan = Field(default_factory=ObjectBehaviorPlan)
    planner: ObjectPlannerSettings = Field(default_factory=ObjectPlannerSettings)
    destination: CityPose | None = None

    @model_validator(mode='after')
    def behavior_owner(self):
        if self.behavior.actions and self.controller.kind!='reactive':
            raise ValueError('Behavior actions require a reactive controller as the sole motion owner')
        if self.destination is not None and self.controller.kind!='reactive':
            raise ValueError('Separate destination requires a reactive controller')
        if self.controller.kind=='reactive' and (
            self.controller.headway_s!=2 or not self.controller.obey_signals or
            self.controller.wait_s or self.controller.stop_time_s is not None or
            self.controller.required_gap_m or self.controller.end_behavior!='stop'):
            raise ValueError('Reactive motion uses behavior actions and planner settings; legacy wait/stop/headway/repeat overrides are unsupported')
        if self.controller.kind!='reactive' and self.planner!=ObjectPlannerSettings():
            raise ValueError('Planner settings require a reactive controller')
        for action in self.behavior.actions:
            if action.kind in {'cross','sudden_crossing'} and self.kind!='pedestrian':
                raise ValueError('Crossing behaviors require a pedestrian')
            if action.kind in {'lane_change','cut_in','merge','diverge','overtake','obstacle_bypass'} and self.kind!='vehicle':
                raise ValueError('Lateral road behaviors require a vehicle')
        return self

class ScenarioReactive(ScenarioCity):
    schema_version: Literal['scenario-v2/3'] = 'scenario-v2/3'
    actors: list[ReactiveActor] = Field(min_length=1, max_length=32)

    @model_validator(mode='after')
    def reactive_links(self):
        if self.timestep_s!=.05:
            raise ValueError('Reactive profile requires 20 Hz physics/control and 10 Hz planning')
        ids={actor.id for actor in self.actors}
        for actor in self.actors:
            if actor.speed_mps>12:raise ValueError('Reactive scenario supports speeds up to 12 m/s')
            for action in actor.behavior.actions:
                for target in (action.target_actor_id,action.trigger.target_actor_id):
                    if target is not None and (target not in ids or target==actor.id):
                        raise ValueError('Behavior target must reference another scenario actor')
        return self

ScenarioDefinition = Annotated[ScenarioV2 | ScenarioCity | ScenarioReactive, Field(discriminator='schema_version')]

def parse_scenario(value):
    """Keep the old schema valid without coercing native city contracts into it."""
    if isinstance(value, (ScenarioV2, ScenarioCity)):
        return value
    if isinstance(value, dict) and value.get('schema_version', 'scenario-v2/1') == 'scenario-v2/1':
        return ScenarioV2.model_validate(value)
    if isinstance(value, dict) and value.get('schema_version') == 'scenario-v2/3':
        return ScenarioReactive.model_validate(value)
    return ScenarioCity.model_validate(value)

class ScenarioV2Create(StrictModel):
    project_id: Identity = 'default'
    scenario: ScenarioDefinition
    source_yaml: str | None = Field(default=None, max_length=2000000)

class ScenarioV2Revise(StrictModel):
    expected_revision: int = Field(ge=1)
    scenario: ScenarioDefinition
    source_yaml: str | None = Field(default=None, max_length=2000000)

class ScenarioYamlInput(StrictModel):
    project_id: Identity = 'default'
    yaml: str = Field(min_length=1, max_length=2000000)

class ScenarioV2Import(StrictModel):
    project_id: Identity = 'default'
    xml: str = Field(min_length=1, max_length=2000000)

class SweepPreview(StrictModel):
    seeds: list[int] = Field(default_factory=lambda:[42], min_length=1, max_length=8)

    @model_validator(mode='after')
    def unique_seed(self):
        if len(set(self.seeds)) != len(self.seeds) or any(s < 0 or s > 2**32-1 for s in self.seeds):
            raise ValueError('Unique unsigned 32-bit seeds required')
        return self
