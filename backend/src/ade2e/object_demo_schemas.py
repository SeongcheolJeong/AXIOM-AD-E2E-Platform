"""Small demo authoring surface over the existing immutable scenario contracts."""
from typing import Literal
from pydantic import Field, StrictBool, StrictFloat, model_validator
from .base_schemas import StrictModel
from .object_behavior_schemas import BehaviorAction
from .scenario_v2_schemas import CityPose, Identity
from .simulation_catalog_schemas import Sha256


class DemoScenarioReference(StrictModel):
    id: Identity
    revision: int = Field(ge=1, strict=True)
    configuration_hash: Sha256
    manifest_hash: Sha256


class DemoBinding(StrictModel):
    project_id: Identity = 'default'
    expected_revision: int = Field(default=0, ge=0, strict=True)
    scenario_ref: DemoScenarioReference
    actor_roles: dict[Literal['ego', 'obstacle', 'rear', 'lead', 'intruder', 'crossing'], Identity]
    saved_execution_id: Identity | None = None
    saved_comparison_execution_id: Identity | None = None


class DemoActorEdit(StrictModel):
    speed_mps: StrictFloat | None = Field(default=None, ge=0, le=12)
    pose: CityPose | None = None
    destination: CityPose | None = None
    actions: list[BehaviorAction] | None = Field(default=None, max_length=16)

    @model_validator(mode='after')
    def no_explicit_null(self):
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError('Omit unchanged actor fields instead of sending null')
        return self


class DemoPreview(StrictModel):
    project_id: Identity = 'default'
    parameters: dict[str, StrictFloat | StrictBool] = Field(default_factory=dict, max_length=5)
    actor_poses: dict[Identity, CityPose] = Field(default_factory=dict, max_length=32)
    actor_edits: dict[Identity, DemoActorEdit] = Field(default_factory=dict, max_length=32)
    source_scenario_ref: DemoScenarioReference | None = None
