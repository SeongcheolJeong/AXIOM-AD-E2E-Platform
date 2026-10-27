"""Versioned sensor-driven stack declarations and per-tick evidence.

Steering and route heading error are positive left in the declared RH ego frame.
Component registration and authored parameters are not vehicle safety validation.
"""
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .model_schemas import Sha256
from .base_schemas import Name, StrictModel
from .sensor_schemas import UnsignedNs

KERNEL_VERSION = "stack-kernel/1"
MAX_STACK_POINTS = 20_000
Finite = Annotated[float, Field(allow_inf_nan=False, strict=True)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
ComponentRole = Literal["perception", "policy", "controller"]


class StackComponentMetadata(StrictModel):
    id: Name
    name: Name
    version: Name
    role: ComponentRole
    status: Literal["available", "unavailable"] = "available"
    parameter_schema: dict = Field(default_factory=dict)
    default_parameters: dict = Field(default_factory=dict)
    implementation: dict[str, str] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list, max_length=30)


class ModuleSelection(StrictModel):
    id: Name
    version: Name
    parameters: dict = Field(default_factory=dict)


class StackEgo(StrictModel):
    timestamp_ns: UnsignedNs = Field(description="Aligned selection clock used for age checks")
    capture_time_ns: UnsignedNs | None = Field(default=None, description="Original payload capture clock; no clock equivalence is inferred")
    frame_id: Name
    speed_mps: Finite = Field(ge=0, le=100)
    route_heading_error_rad: Finite = Field(ge=-3.141592653589793, le=3.141592653589793)
    pitch_rad: Finite = Field(ge=-3.141592653589793, le=3.141592653589793)
    roll_rad: Finite = Field(ge=-3.141592653589793, le=3.141592653589793)
    source: Name


class StackLidar(StrictModel):
    timestamp_ns: UnsignedNs = Field(description="Aligned selection clock used for age checks")
    capture_time_ns: UnsignedNs | None = Field(default=None, description="Original payload capture clock; no clock equivalence is inferred")
    frame_id: Name
    point_count: int = Field(ge=0, le=MAX_STACK_POINTS, strict=True)
    points_sha256: Sha256
    size_bytes: int = Field(ge=0, le=MAX_STACK_POINTS * 12, strict=True)
    source_message_sha256: Sha256
    complete_frame: bool = Field(strict=True)
    coordinate_convention: Literal["x_forward_y_left_z_up"] = "x_forward_y_left_z_up"
    encoding: Literal["xyz_float32_le"] = "xyz_float32_le"

    @model_validator(mode="after")
    def exact_size(self):
        if self.size_bytes != self.point_count * 12:
            raise ValueError("LiDAR XYZ size must equal point_count * 12")
        return self


class StackPacket(StrictModel):
    sequence: int = Field(ge=0, le=2**63 - 1, strict=True)
    at_ns: UnsignedNs
    ego: StackEgo | None = None
    lidar: StackLidar | None = None
    unavailable_reasons: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(default_factory=list, max_length=30)


class StackControl(StrictModel):
    throttle: Finite = Field(ge=0, le=1)
    brake: Finite = Field(ge=0, le=1)
    steer: Finite = Field(ge=-1, le=1)
    reverse: Literal[False] = False
    hand_brake: Literal[False] = False
    steering_convention: Literal["positive_left"] = "positive_left"

    @model_validator(mode="after")
    def no_opposed_actuation(self):
        if self.throttle > 0 and self.brake > 0:
            raise ValueError("Throttle and brake cannot both be nonzero")
        return self


