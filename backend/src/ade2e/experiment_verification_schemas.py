"""Fixed metric types, units and evidence scope; no generic cross-type pass."""
import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .experiment_schemas import ExperimentKind
from .city_motion import FIDELITIES as CITY_FIDELITIES

Metric = Literal['collision', 'minimum_distance', 'minimum_ttc', 'lane_departure', 'maximum_speed',
                 'maximum_acceleration', 'maximum_jerk', 'goal_arrival', 'maximum_trajectory_error',
                 'native_contact', 'lateral_error_p95', 'goal_error', 'final_speed', 'distance_travelled']
UNITS = {'collision': 'boolean', 'minimum_distance': 'm', 'minimum_ttc': 's', 'lane_departure': 'boolean',
         'maximum_speed': 'm/s', 'maximum_acceleration': 'm/s2', 'maximum_jerk': 'm/s3',
         'maximum_trajectory_error': 'm', 'native_contact':'boolean', 'lateral_error_p95':'m',
         'goal_error':'m', 'final_speed':'m/s','distance_travelled':'m'}

CITY_METRICS = {'native_contact','lateral_error_p95','goal_error','final_speed','distance_travelled'}

class ExperimentCriterion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    metric: Metric
    unit: str = Field(min_length=1, max_length=32)
    operator: Literal['eq', 'lt', 'lte', 'gt', 'gte']
    threshold: bool | float
    actor_id: str = Field(default='ego', min_length=1, max_length=80)
    required_evaluated_fraction: float = Field(default=1.0, gt=0, le=1)

    @model_validator(mode='after')
    def exact_value_type(self):
        if self.unit == 'boolean':
            if type(self.threshold) is not bool or self.operator != 'eq':
                raise ValueError('Boolean metrics require eq and a boolean threshold')
        elif type(self.threshold) is bool or not math.isfinite(self.threshold):
            raise ValueError('Numeric metric thresholds must be finite numbers')
        return self

class ExperimentVerificationCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: str = Field(default='default', min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=180)
    purpose: str = Field(min_length=1, max_length=2000)
    experiment_kind: ExperimentKind
    allowed_fidelities: list[str] = Field(min_length=1, max_length=8)
    criteria: list[ExperimentCriterion] = Field(default_factory=list, max_length=32)
    change_reason: str = Field(default='Initial plan', min_length=1, max_length=2000)

    @model_validator(mode='after')
    def supported_evidence(self):
        if len(set(self.allowed_fidelities)) != len(self.allowed_fidelities) or any(not item or len(item)>180 for item in self.allowed_fidelities):
            raise ValueError('Fidelity scopes must be unique explicit identifiers')
        if self.experiment_kind not in {'object_sim', 'planning_replay'} and self.criteria:
            raise ValueError('Sensor/ISP/driving/HIL demos are context evidence only; no physical/safety evaluator is accepted')
        seen = set()
        for criterion in self.criteria:
            identity = (criterion.metric, criterion.actor_id)
            if identity in seen:
                raise ValueError('Duplicate metric/actor criterion')
            seen.add(identity)
            expected = ('m' if self.experiment_kind == 'object_sim' else 'boolean') if criterion.metric == 'goal_arrival' else UNITS[criterion.metric]
            if criterion.unit != expected:
                raise ValueError('Metric unit differs from its typed evaluator contract')
            if criterion.metric == 'maximum_trajectory_error' and self.experiment_kind != 'planning_replay':
                raise ValueError('Trajectory error requires retained planning replay')
            if criterion.metric in CITY_METRICS and (self.experiment_kind != 'object_sim' or
                    not CITY_FIDELITIES.intersection(self.allowed_fidelities)):
                raise ValueError('Native city metrics require an object_sim plan explicitly accepting a native city motion fidelity')
            if self.experiment_kind == 'planning_replay' and criterion.actor_id != 'ego':
                raise ValueError('Planning observer evaluator currently supports the ego actor only')
        return self

class ExperimentVerificationEvaluate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    plan_revision: int = Field(ge=1)
    execution_ids: list[str] = Field(min_length=1, max_length=128)

    @model_validator(mode='after')
    def distinct_trials(self):
        if len(set(self.execution_ids)) != len(self.execution_ids):
            raise ValueError('Duplicate executions do not add independent evidence')
        return self
