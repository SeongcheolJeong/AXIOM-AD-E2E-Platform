"""Versioned sensor-driven stack declarations and per-tick evidence.

Steering and route heading error are positive left in the declared RH ego frame.
Component registration and authored parameters are not vehicle safety validation.
"""
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from .model_schemas import Sha256
from .base_schemas import Name, StrictModel
from .sensor_schemas import UnsignedNs

# Common models remain public here for existing adapters and saved v1 schemas.
from .stack_common_schemas import (KERNEL_VERSION, MAX_STACK_POINTS, Finite, Note,
    ComponentRole, StackComponentMetadata, ModuleSelection, StackEgo, StackLidar,
    StackPacket, StackControl)


class StackDefinition(StrictModel):
    schema_version: Literal["1"] = "1"
    reference_frame: Name = "ego"
    perception: ModuleSelection
    policy: ModuleSelection
    controller: ModuleSelection


class CorridorObservation(StrictModel):
    status: Literal["obstacle_observed", "no_obstacle_observed", "unknown"]
    distance_m: Finite | None = Field(default=None, ge=0)
    nearest_point_xyz_m: tuple[Finite, Finite, Finite] | None = None
    input_points: int = Field(ge=0, le=MAX_STACK_POINTS, strict=True)
    forward_points: int = Field(default=0, ge=0, le=MAX_STACK_POINTS, strict=True)
    obstacle_points: int = Field(default=0, ge=0, le=MAX_STACK_POINTS, strict=True)
    ground_points: int = Field(default=0, ge=0, le=MAX_STACK_POINTS, strict=True)
    ground_bins_observed: int = Field(default=0, ge=0, le=20, strict=True)
    supported_distance_m: Finite | None = Field(default=None, ge=0,
        description="Farthest observed forward ground x minus ego front; sample extent, not guaranteed free space")
    coverage_qualified: bool = Field(default=False, strict=True)
    reasons: list[str] = Field(default_factory=list, max_length=30)
    semantics: Literal["observed_surface_distance_not_free_space_or_ttc"] = "observed_surface_distance_not_free_space_or_ttc"


class StackDecision(StrictModel):
    mode: Literal["unknown_stop", "obstacle_stop", "approach", "pilot_cruise", "hold"]
    target_speed_mps: Finite = Field(ge=0, le=20)
    stopping_distance_m: Finite = Field(ge=0)
    force_brake: bool = Field(strict=True)
    reasons: list[str] = Field(default_factory=list, max_length=30)


class StackOutput(StrictModel):
    kernel_version: Literal["stack-kernel/1"] = KERNEL_VERSION
    sequence: int
    at_ns: UnsignedNs
    observation: CorridorObservation
    decision: StackDecision
    control: StackControl
    input_points_sha256: Sha256 | None
    components: list[StackComponentMetadata]
    warnings: list[str] = Field(default_factory=list)
    elapsed_ms: Finite = Field(ge=0)

# v2 schemas depend only on stack_common_schemas, so either public module may be
# imported first without a circular initialization dependency.
from .stack_v2_schemas import StackDefinitionV2, StackOutputV2

StackDefinitionLike = StackDefinition | StackDefinitionV2
StackOutputLike = StackOutput | StackOutputV2


def _model_value(value):
    return value.model_dump(mode="python") if hasattr(value, "model_dump") else value


def validate_stack_definition(value) -> StackDefinitionLike:
    value = _model_value(value)
    if not isinstance(value, dict):
        raise ValueError("A stack definition must be an object")
    version = value.get("schema_version", "1")
    if version == "1":
        return StackDefinition.model_validate(value)
    if version == "2":
        return StackDefinitionV2.model_validate(value)
    raise ValueError("Unsupported explicit stack definition schema_version")


def stack_kernel_version(definition) -> str:
    definition = validate_stack_definition(definition)
    return KERNEL_VERSION if isinstance(definition, StackDefinition) else definition.kernel_version


def validate_stack_output(value) -> StackOutputLike:
    value = _model_value(value)
    if not isinstance(value, dict):
        raise ValueError("A stack output must be an object")
    # Persisted v1 StackOutput always included its default kernel identity. Unlike
    # authoring inputs, engine evidence must not infer identity from a missing key.
    version = value.get("kernel_version")
    if version == KERNEL_VERSION:
        return StackOutput.model_validate(value)
    if version == "stack-kernel/2":
        return StackOutputV2.model_validate(value)
    raise ValueError("Missing or unsupported explicit stack output kernel_version")


class StackCreate(StrictModel):
    name: Name
    description: str = Field(default="", max_length=4000)
    note: Note
    definition: StackDefinitionLike

    @field_validator("definition", mode="before")
    @classmethod
    def explicit_definition(cls, value):
        return validate_stack_definition(value)


class StackRevisionCreate(StackCreate):
    expected_revision: int = Field(ge=1, le=2**31 - 1, strict=True)


class StackSnapshot(StackCreate):
    schema_version: Literal["1"] = "1"
    id: Name
    revision: int = Field(ge=1, le=2**31 - 1, strict=True)
    created_at: str
    kernel_version: Literal["stack-kernel/1", "stack-kernel/2"] = KERNEL_VERSION
    component_metadata: list[StackComponentMetadata] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def exact_kernel(self):
        if self.kernel_version != stack_kernel_version(self.definition):
            raise ValueError("Stack snapshot kernel differs from its explicit definition")
        return self


class StackDetail(StackSnapshot):
    manifest_hash: Sha256
    manifest_size_bytes: int = Field(ge=1, strict=True)
    download_url: str
