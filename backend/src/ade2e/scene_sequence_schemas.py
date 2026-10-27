"""Temporal driving inputs, separate from detection-image datasets.

All poses are planar right-handed X/Y metres, yaw radians and Z-up. These
declarations and authored examples are not measured calibration or road truth.
"""
import math
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .base_schemas import Name, StrictModel
from .sensor_schemas import SignedNs, UnsignedNs

Metres = Annotated[float, Field(strict=True, ge=-1_000_000, le=1_000_000)]
Radians = Annotated[float, Field(strict=True, ge=-math.pi, le=math.pi)]
Hash = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
Ordinal = Annotated[int, Field(strict=True, ge=0, le=99_999)]


class PlanningEgo(StrictModel):
    timestamp_ns: UnsignedNs
    frame_id: Name
    x_m: Metres
    y_m: Metres
    yaw_rad: Radians
    speed_mps: float = Field(strict=True, ge=0, le=100)
    length_m: float = Field(strict=True, gt=0, le=30)
    width_m: float = Field(strict=True, gt=0, le=10)
    source: Name


class TrackedObject(StrictModel):
    id: Name
    x_m: Metres
    y_m: Metres
    yaw_rad: Radians
    length_m: float = Field(strict=True, gt=0, le=100)
    width_m: float = Field(strict=True, gt=0, le=30)
    vx_mps: float | None = Field(default=None, strict=True, ge=-100, le=100)
    vy_mps: float | None = Field(default=None, strict=True, ge=-100, le=100)

    @model_validator(mode='after')
    def complete_velocity(self):
        if (self.vx_mps is None) != (self.vy_mps is None):
            raise ValueError('Object velocity requires both X and Y components')
        return self


class ObjectTracks(StrictModel):
    timestamp_ns: UnsignedNs
    frame_id: Name
    coordinate_frame: Literal['world', 'ego']
    velocity_semantics: Literal['absolute_ground_velocity']
    objects: list[TrackedObject] = Field(max_length=128)
    source: Name

    @model_validator(mode='after')
    def unique_tracks(self):
        if len({obj.id for obj in self.objects}) != len(self.objects):
            raise ValueError('Object IDs must be unique within each observation')
        return self


class AuthoredSequenceFrame(StrictModel):
    at_ns: UnsignedNs
    ego: PlanningEgo
    objects: ObjectTracks


class SequenceSensorSelection(StrictModel):
    channel_id: int = Field(strict=True, ge=0, le=65_535)
    role: Literal['camera', 'lidar', 'radar', 'other']
    log_time_offset_ns: SignedNs = '0'
    max_age_ns: UnsignedNs
    decoder_id: Name
    decoder_version: Name


class SceneSequenceCreate(StrictModel):
    name: Name
    project_id: Name = 'default'
    source_type: Literal['recording', 'authored_reference']
    world_frame: Name = 'map'
    coordinate_convention: Literal['right_handed_z_up'] = 'right_handed_z_up'
    clock_domain: Name
    note: str = Field(min_length=1, max_length=4000)
    recording_id: Name | None = None
    ego_channel_id: int | None = Field(default=None, strict=True, ge=0, le=65_535)
    object_channel_id: int | None = Field(default=None, strict=True, ge=0, le=65_535)
    ego_ordinals: list[Ordinal] = Field(default_factory=list, max_length=512)
    ego_log_time_offset_ns: SignedNs = '0'
    object_log_time_offset_ns: SignedNs = '0'
    object_max_age_ns: UnsignedNs = '100000000'
    max_step_ns: UnsignedNs = '1000000000'
    binding_revision: int | None = Field(default=None, strict=True, ge=1)
    sensor_channels: list[SequenceSensorSelection] = Field(default_factory=list, max_length=16)
    authored_frames: list[AuthoredSequenceFrame] = Field(default_factory=list, max_length=512)

    @model_validator(mode='after')
    def input_variant(self):
        if int(self.max_step_ns) == 0 or int(self.max_step_ns) > 1_000_000_000:
            raise ValueError('max_step_ns must be between 1 ns and 1 s')
        if int(self.object_max_age_ns) > 1_000_000_000:
            raise ValueError('Object maximum age must not exceed 1 s')
        if self.source_type == 'recording':
            if self.recording_id is None or self.ego_channel_id is None or self.object_channel_id is None or not self.ego_ordinals:
                raise ValueError('Recording sequences require recording, ego/object channels and exact ego ordinals')
            if self.ego_channel_id == self.object_channel_id or self.authored_frames:
                raise ValueError('Recorded channels must be distinct and cannot include authored frames')
            if len(set(self.ego_ordinals)) != len(self.ego_ordinals):
                raise ValueError('Exact ego ordinals must be unique')
            if self.sensor_channels and self.binding_revision is None:
                raise ValueError('Additional sensor references require a pinned rig binding revision')
            if any(item.channel_id in {self.ego_channel_id,self.object_channel_id} for item in self.sensor_channels):
                raise ValueError('Additional sensor roles cannot reuse the selected ego/object channels')
        else:
            if not self.authored_frames or self.recording_id is not None or self.ego_ordinals or self.sensor_channels or self.binding_revision:
                raise ValueError('Authored reference requires explicit frames without recording/sensor bindings')
            if self.ego_channel_id is not None or self.object_channel_id is not None:
                raise ValueError('Authored reference cannot declare recording channels')
        if len({item.channel_id for item in self.sensor_channels}) != len(self.sensor_channels):
            raise ValueError('Additional sensor channels must be unique')
        return self


class SceneSequenceDetail(StrictModel):
    id: Name
    project_id: Name
    name: Name
    created_at: str
    sha256: Hash
    source_type: Literal['recording', 'authored_reference']
    source_kind: Literal['unknown', 'synthetic', 'simulation', 'measured']
    frame_count: int
    snapshot: dict
    limitations: list[str]


class SceneSequenceFreeze(StrictModel):
    definition: SceneSequenceCreate
    preview_sha256: Hash
