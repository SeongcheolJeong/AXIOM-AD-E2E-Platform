"""Bounded moving RGB/LiDAR/radar rig; reference controller is not sensor-driven."""
from typing import Literal
from pydantic import Field, model_validator
from .base_schemas import StrictModel
from .sensor_capture_schemas import SensorCaptureSpec, capture_template


class DrivingReferenceController(StrictModel):
    id: Literal['native-map-lane-follow/1'] = 'native-map-lane-follow/1'
    target_speed_mps: float = Field(default=3, strict=True, ge=1, le=4)
    lookahead_m: float = Field(default=6, strict=True, ge=3, le=12)
    route_length_m: float = Field(default=150, strict=True, ge=30, le=150)


class DrivingSensorCaptureSpec(SensorCaptureSpec):
    schema_version: Literal['carla-driving-multisensor-spec/1'] = 'carla-driving-multisensor-spec/1'
    duration_seconds: int = Field(default=20, strict=True, ge=20, le=30)
    controller: DrivingReferenceController = Field(default_factory=DrivingReferenceController)

    @model_validator(mode='after')
    def route_horizon(self):
        if self.duration_seconds * self.controller.target_speed_mps + self.controller.lookahead_m + 10 > self.controller.route_length_m:
            raise ValueError('Declared native route cannot cover the driving capture horizon')
        return self


def driving_capture_template():
    template = capture_template()
    template.update(schema_version='carla-driving-multisensor-spec/1', duration_seconds=20,
                    controller=DrivingReferenceController().model_dump(mode='json'))
    return DrivingSensorCaptureSpec.model_validate(template).model_dump(mode='json')
