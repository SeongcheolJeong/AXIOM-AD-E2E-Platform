"""Versioned, engine-neutral Object Sim behavior authoring contract."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import Field, StringConstraints, model_validator
from .base_schemas import StrictModel

BehaviorId = Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')]
BehaviorKind = Literal['route_follow','target_speed','follow','stop','resume','wait',
    'lane_change','cut_in','merge','diverge','overtake','yield','cross','obstacle_bypass',
    'reroute','arrive','emergency_brake','signal_violation','sudden_crossing']
RISK_BEHAVIORS = frozenset({'cut_in','emergency_brake','signal_violation','sudden_crossing'})


class BehaviorRegion(StrictModel):
    x: float
    y: float
    radius_m: float = Field(default=3, gt=0, le=1000)


class BehaviorTrigger(StrictModel):
    kind: Literal['time','distance','relative_speed','ttc','region','signal','action_complete'] = 'time'
    value: float = Field(default=0, ge=0, le=100000)
    operator: Literal['lte','gte'] = 'gte'
    target_actor_id: BehaviorId | None = None
    region: BehaviorRegion | None = None
    signal_id: str | None = Field(default=None, max_length=160)
    signal_state: Literal['red','yellow','green'] | None = None
    action_id: BehaviorId | None = None

    @model_validator(mode='after')
    def required_arguments(self):
        if self.kind in {'distance','relative_speed','ttc'} and self.target_actor_id is None:
            raise ValueError('Relative triggers require target_actor_id')
        if self.kind == 'region' and self.region is None:
            raise ValueError('Region trigger requires its center and radius')
        if self.kind == 'signal' and (self.signal_id is None or self.signal_state is None):
            raise ValueError('Signal trigger requires signal_id and signal_state')
        if self.kind == 'action_complete' and self.action_id is None:
            raise ValueError('Action completion trigger requires action_id')
        return self


class BehaviorAction(StrictModel):
    id: BehaviorId
    kind: BehaviorKind
    trigger: BehaviorTrigger = Field(default_factory=BehaviorTrigger)
    speed_mps: float | None = Field(default=None, ge=0, le=12)
    duration_s: float = Field(default=0, ge=0, le=120)
    target_actor_id: BehaviorId | None = None
    target_lane_node_id: str | None = Field(default=None, min_length=1, max_length=160)
    timeout_s: float = Field(default=30, gt=0, le=120)
    on_timeout: Literal['stop','skip','fail'] = 'stop'

    @model_validator(mode='after')
    def required_arguments(self):
        if self.kind == 'target_speed' and self.speed_mps is None:
            raise ValueError('target_speed requires speed_mps')
        if self.kind in {'lane_change','cut_in','merge','diverge'} and self.target_lane_node_id is None:
            raise ValueError('Lane transition requires a target_lane_node_id')
        if self.duration_s > self.timeout_s:
            raise ValueError('Action duration cannot exceed timeout')
        return self


class ObjectBehaviorPlan(StrictModel):
    schema_version: Literal['object-behavior/1'] = 'object-behavior/1'
    risk_mode: bool = False
    actions: list[BehaviorAction] = Field(default_factory=list, max_length=64)

    @model_validator(mode='after')
    def ordered_actions(self):
        seen = set()
        for action in self.actions:
            if action.id in seen:
                raise ValueError('Behavior action IDs must be unique per actor')
            if action.kind in RISK_BEHAVIORS and not self.risk_mode:
                raise ValueError('Risk behavior requires explicit risk_mode=true')
            if action.trigger.kind == 'action_complete' and action.trigger.action_id not in seen:
                raise ValueError('Completion trigger must reference an earlier action')
            seen.add(action.id)
        return self


class ObjectPlannerSettings(StrictModel):
    schema_version: Literal['object-planner/1'] = 'object-planner/1'
    headway_s: float = Field(default=2, ge=.5, le=10)
    min_gap_m: float = Field(default=3, ge=.5, le=20)
    max_deceleration_mps2: float = Field(default=3, gt=0, le=8)
    max_lateral_acceleration_mps2: float = Field(default=2, gt=0, le=5)
    max_curvature_inv_m: float = Field(default=.12, gt=0, le=.3)
    allow_lane_changes: bool = True
    allow_reroute: bool = True
    obey_signals: bool = True
    deadlock_timeout_s: float = Field(default=10, ge=2, le=120)
    closed_node_ids: list[str] = Field(default_factory=list, max_length=2000)

