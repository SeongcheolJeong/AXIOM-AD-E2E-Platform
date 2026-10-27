"""Guided software-DUT demo inputs; no physical HIL claim is implied."""
from pydantic import Field, StrictFloat, StrictInt
from .base_schemas import StrictModel
from .object_demo_schemas import DemoScenarioReference
from .scenario_v2_schemas import Identity
from .simulation_catalog_schemas import Sha256


class HilSceneReference(StrictModel):
    id: Identity
    revision: int = Field(strict=True, ge=1)
    sha256: Sha256


class HilDemoBinding(StrictModel):
    project_id: Identity = 'default'
    expected_revision: int = Field(default=0, strict=True, ge=0)
    scenario_ref: DemoScenarioReference
    seed: int = Field(default=42, strict=True, ge=0, le=2147483647)
    saved_execution_id: Identity | None = None


class HilDemoDefinition(StrictModel):
    scenario_ref: DemoScenarioReference
    adapter_id: str = Field(default='carla-software-dut', min_length=1, max_length=180)
    adapter_version: str = Field(min_length=1, max_length=180)
    seed: int = Field(strict=True, ge=0, le=2147483647)
    saved_execution_id: Identity | None = None


class HilDemoPreview(StrictModel):
    project_id: Identity = 'default'
    expected_binding_revision: int = Field(strict=True, ge=1)
    parameters: dict[str, StrictInt | StrictFloat] = Field(default_factory=dict, max_length=3)
    scene_profile: HilSceneReference | None = None
