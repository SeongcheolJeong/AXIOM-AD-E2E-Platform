"""Engine-neutral declarations for the bounded CARLA LiDAR capability.

These types contain no CARLA imports. Calibration is explicitly unverified.
"""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .base_schemas import Name, StrictModel
from .model_schemas import Sha256
from .sensor_schemas import Note

Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class CarlaLidarParameters(StrictModel):
    channels: int = Field(default=16, strict=True, ge=1, le=128)
    range: Finite = Field(default=80.0, ge=0.1, le=200)
    points_per_second: int = Field(default=32000, strict=True, ge=20, le=400000)
    rotation_frequency: Finite = Field(default=20.0, ge=20, le=20)
    sensor_tick: Finite = Field(default=0.0, ge=0, le=0)
    upper_fov: Finite = Field(default=10.0, ge=-89, le=89)
    lower_fov: Finite = Field(default=-30.0, ge=-89, le=89)
    horizontal_fov: Finite = Field(default=360.0, ge=1, le=360)
    atmosphere_attenuation_rate: Finite = Field(default=0.004, ge=0, le=1)
    dropoff_general_rate: Finite = Field(default=0.45, ge=0, le=1)
    dropoff_intensity_limit: Finite = Field(default=0.8, gt=0, le=1)
    dropoff_zero_intensity: Finite = Field(default=0.4, ge=0, le=1)
    noise_stddev: Finite = Field(default=0.0, ge=0, le=1)

    @model_validator(mode='after')
    def consistent_sampling(self):
        if self.lower_fov >= self.upper_fov:
            raise ValueError('lower_fov must be less than upper_fov')
        if self.points_per_second < 20*self.channels:
            raise ValueError('points_per_second must provide at least one ray per channel at 20 Hz')
        if self.channels*math.ceil(self.points_per_second/(20*self.channels)) > 20000:
            raise ValueError('Conservative rays per frame exceed 20000')
        return self


class SensorConfigurationCreate(StrictModel):
    name: Name
    description: str = Field(default='', max_length=4000)
    note: Note
    adapter_id: Literal['carla'] = 'carla'
    capability_id: Literal['carla-raycast-lidar/1'] = 'carla-raycast-lidar/1'
    capability_version: Literal['1'] = '1'
    parameters: CarlaLidarParameters


class SensorConfigurationRevisionCreate(SensorConfigurationCreate):
    expected_revision: int = Field(strict=True, ge=1, le=2**31-1)


class SensorConfigurationSnapshot(SensorConfigurationCreate):
    schema_version: Literal['simulation-sensor-configuration/1'] = 'simulation-sensor-configuration/1'
    id: Name
    revision: int = Field(strict=True, ge=1, le=2**31-1)
    created_at: str
    definition_hash: Sha256
    capability_schema_hash: Sha256


class SensorConfigurationDetail(SensorConfigurationSnapshot):
    manifest_hash: Sha256
    manifest_size_bytes: int = Field(strict=True, gt=0, le=65536)
    download_url: str


class SensorExecutionCapability(StrictModel):
    id: Literal['carla-raycast-lidar/1'] = 'carla-raycast-lidar/1'
    version: Literal['1'] = '1'
    adapter_id: Literal['carla'] = 'carla'
    name: str
    status: Literal['supported'] = 'supported'
    supported_adapter_versions: list[str]
    supported_context_versions: list[str]
    supported_kernel_versions: list[str]
    parameter_schema: dict
    default_parameters: CarlaLidarParameters
    schema_hash: Sha256
    mount_rules: dict
    limits: dict
    budget_policy_version: Literal['carla-full-execution-evidence-budget/1'] = 'carla-full-execution-evidence-budget/1'
    calibration_status: Literal['uncalibrated_engine_model'] = 'uncalibrated_engine_model'
    limitations: list[str]


class ExecutionEvidenceBudget(StrictModel):
    policy_version: Literal['carla-full-execution-evidence-budget/1'] = 'carla-full-execution-evidence-budget/1'
    measurement_intervals: int
    observation_frames: int
    conservative_points_per_frame: int
    raw_points_bytes: int
    consumed_points_bytes: int
    recording_bytes: int
    metadata_bytes: int
    total_payload_bytes: int
    wire_bytes: int
    per_artifact_limit_bytes: int
    total_payload_limit_bytes: int
    wire_limit_bytes: int
    admissible: bool
    reasons: list[str]
    assumptions: list[str]
