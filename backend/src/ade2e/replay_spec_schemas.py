"""Three distinct replay modes; only planning_closed_loop changes next ego state."""
import math
from typing import Literal

from pydantic import Field, model_validator

from .base_schemas import Name, StrictModel
from .scene_sequence_schemas import Hash


class ReferenceController(StrictModel):
    id: Literal['geometric-headway'] = 'geometric-headway'
    version: Literal['1'] = '1'
    cruise_speed_mps: float = Field(default=8.0, strict=True, ge=0, le=50)
    time_headway_s: float = Field(default=1.5, strict=True, ge=0.1, le=10)
    stop_buffer_m: float = Field(default=2.0, strict=True, ge=0, le=30)
    max_acceleration_mps2: float = Field(default=2.0, strict=True, gt=0, le=10)
    max_deceleration_mps2: float = Field(default=5.0, strict=True, gt=0, le=15)
    heading_gain: float = Field(default=0.5, strict=True, ge=0, le=3)
    steering_bias_rad: float = Field(default=0.0, strict=True, ge=-0.5, le=0.5)


class ReferenceDynamics(StrictModel):
    id: Literal['planar-kinematic-bicycle'] = 'planar-kinematic-bicycle'
    version: Literal['1'] = '1'
    wheelbase_m: float = Field(default=2.7, strict=True, gt=0.1, le=15)
    max_steering_rad: float = Field(default=0.6, strict=True, gt=0, le=1.2)


class ReplayGoal(StrictModel):
    x_m: float = Field(strict=True, ge=-1_000_000, le=1_000_000)
    y_m: float = Field(strict=True, ge=-1_000_000, le=1_000_000)
    tolerance_m: float = Field(default=2.0, strict=True, gt=0, le=100)


class ReplaySpecCreate(StrictModel):
    name: Name
    project_id: Name = 'default'
    sequence_id: Name
    sequence_sha256: Hash
    mode: Literal['original_replay', 'open_loop', 'planning_closed_loop']
    controller: ReferenceController = Field(default_factory=ReferenceController)
    dynamics: ReferenceDynamics = Field(default_factory=ReferenceDynamics)
    goal: ReplayGoal | None = None
    observers: list[Literal['collision', 'distance', 'ttc', 'speed', 'acceleration', 'jerk', 'trajectory_error', 'goal_arrival', 'lane_departure']] = Field(
        default_factory=lambda: ['collision', 'distance', 'ttc', 'speed', 'acceleration', 'jerk', 'trajectory_error'], max_length=9)
    camera_reactive: Literal[False] = False
    note: str = Field(min_length=1, max_length=4000)

    @model_validator(mode='after')
    def unique_observers(self):
        if len(set(self.observers)) != len(self.observers):
            raise ValueError('Observer names must be unique')
        return self


class ReplaySpecDetail(StrictModel):
    id: Name
    project_id: Name
    name: Name
    created_at: str
    sha256: Hash
    request: ReplaySpecCreate
    sequence_snapshot: dict
    fidelity: str
    limitations: list[str]

