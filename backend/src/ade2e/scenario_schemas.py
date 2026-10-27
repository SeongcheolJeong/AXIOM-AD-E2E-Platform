"""Scenario contract, separated to keep simulation DTO imports acyclic."""
from typing import Annotated, Literal
from pydantic import Field, StringConstraints, model_validator
from .base_schemas import Name, StrictModel

Category = Literal["cut_in", "emergency_brake", "pedestrian_crossing", "lane_follow"]
Weather = Literal["clear", "rain", "fog"]
TimeOfDay = Literal["day", "night", "dusk"]
Operator = Literal["gte", "lte", "eq"]
MetricName = Literal["collision_count", "min_ttc_s", "max_deceleration_mps2", "max_jerk_mps3", "distance_m"]


class ScenarioParameters(StrictModel):
    ego_speed_mps: float = Field(default=15, ge=0, le=55)
    target_speed_mps: float = Field(default=12, ge=0, le=55)
    initial_gap_m: float = Field(default=40, ge=0, le=500)
    trigger_time_s: float = Field(default=2, ge=0, le=119)
    friction: float = Field(default=0.8, ge=0.1, le=1.2)
    response_delay_s: float = Field(default=0.4, ge=0, le=10)


class ParameterOverrides(StrictModel):
    ego_speed_mps: float | None = Field(default=None, ge=0, le=55)
    target_speed_mps: float | None = Field(default=None, ge=0, le=55)
    initial_gap_m: float | None = Field(default=None, ge=0, le=500)
    trigger_time_s: float | None = Field(default=None, ge=0, le=119)
    friction: float | None = Field(default=None, ge=0.1, le=1.2)
    response_delay_s: float | None = Field(default=None, ge=0, le=10)

    @model_validator(mode="after")
    def no_explicit_null(self):
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Parameter overrides cannot be null")
        return self


class ScenarioCreate(StrictModel):
    name: Name
    description: str = Field(default="", max_length=4000)
    category: Category = "cut_in"
    map_name: Name = "Reference Straight Road"
    weather: Weather = "clear"
    time_of_day: TimeOfDay = "day"
    tags: list[Annotated[str, StringConstraints(min_length=1, max_length=64)]] = Field(default_factory=list, max_length=32)
    parameters: ScenarioParameters = Field(default_factory=ScenarioParameters)
    duration_s: float = Field(default=12, ge=2, le=120)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)

    @model_validator(mode="after")
    def trigger_within_duration(self):
        if self.parameters.trigger_time_s >= self.duration_s:
            raise ValueError("trigger_time_s must be less than duration_s")
        return self


class ScenarioPatch(StrictModel):
    name: Name | None = None
    description: str | None = Field(default=None, max_length=4000)
    category: Category | None = None
    map_name: Name | None = None
    weather: Weather | None = None
    time_of_day: TimeOfDay | None = None
    tags: list[Annotated[str, StringConstraints(min_length=1, max_length=64)]] | None = Field(default=None, max_length=32)
    parameters: ParameterOverrides | None = None
    duration_s: float | None = Field(default=None, ge=2, le=120)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)

    @model_validator(mode="after")
    def no_explicit_null(self):
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Scenario authoring fields cannot be null")
        return self


class Scenario(ScenarioCreate):
    id: str
    revision: int = Field(ge=1)
    created_at: str
    updated_at: str
