"""Versioned authoring surface for stationary and driving multisensor demos."""
from pydantic import Field, StrictFloat, StrictInt
from .base_schemas import StrictModel
from .scenario_v2_schemas import Identity
from .sensor_capture_schemas import SensorCaptureSpec, capture_template
from .driving_sensor_capture_schemas import DrivingSensorCaptureSpec


class SensorDemoBinding(StrictModel):
    project_id: Identity = 'default'
    expected_revision: int = Field(default=0, strict=True, ge=0)
    capture_spec: SensorCaptureSpec | DrivingSensorCaptureSpec | None = None
    seed: int = Field(default=42, strict=True, ge=0, le=2147483647)
    saved_execution_id: Identity | None = None


class SensorDemoDefinition(StrictModel):
    capture_spec: SensorCaptureSpec | DrivingSensorCaptureSpec
    adapter_id: str = Field(default='carla-streaming-capture', min_length=1, max_length=180)
    seed: int = Field(strict=True, ge=0, le=2147483647)
    adapter_version: str = Field(min_length=1, max_length=180)
    saved_execution_id: Identity | None = None


class SensorDemoPreview(StrictModel):
    project_id: Identity = 'default'
    expected_binding_revision: int = Field(strict=True, ge=1)
    parameters: dict[str, StrictInt | StrictFloat] = Field(default_factory=dict, max_length=1)
