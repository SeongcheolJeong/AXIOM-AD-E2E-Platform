"""Bounded CARLA-to-neural demo authoring, independent of the renderer runtime."""
from typing import Literal
from pydantic import Field, StrictFloat, StrictInt, model_validator
from .base_schemas import StrictModel
from .scenario_v2_schemas import Identity


class NeuralReconstructionInput(StrictModel):
    source_execution_id: Identity
    rig: Literal['rig_a'] = 'rig_a'
    camera: Literal['front'] = 'front'


class NeuralRenderInput(StrictModel):
    reconstruction_execution_id: Identity
    lateral_m: float = Field(default=0, strict=True, ge=-.5, le=2, allow_inf_nan=False)
    height_m: float = Field(default=0, strict=True, ge=-.3, le=.3, allow_inf_nan=False)
    mode: Literal['frame', 'sequence', 'camera_sweep'] = 'sequence'
    frame_index: int = Field(default=0, strict=True, ge=0, le=199)

    @model_validator(mode='after')
    def camera_edit_bounds(self):
        if self.mode == 'camera_sweep':
            if not 0 < self.lateral_m <= 2:
                raise ValueError('Camera sweep requires a positive lateral amplitude within 2 m')
        elif abs(self.lateral_m) > .5:
            raise ValueError('Frame/sequence camera edits must remain within +/-0.5 m')
        return self


class NeuralDemoBinding(StrictModel):
    project_id: Identity = 'default'
    expected_revision: int = Field(default=0, strict=True, ge=0)
    source_execution_id: Identity
    reconstruction_execution_id: Identity | None = None
    saved_execution_id: Identity | None = None
    seed: int = Field(default=42, strict=True, ge=0, le=2147483647)


class NeuralDemoPreview(StrictModel):
    project_id: Identity = 'default'
    expected_binding_revision: int = Field(strict=True, ge=1)
    parameters: dict[str, StrictInt | StrictFloat] = Field(default_factory=dict, max_length=2)
    mode: Literal['frame', 'sequence', 'camera_sweep'] | None = None
    frame_index: int | None = Field(default=None, strict=True, ge=0, le=199)
