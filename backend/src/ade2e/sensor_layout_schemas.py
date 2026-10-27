"""Placement beta edits mounted poses only; the native driving schema is unchanged."""
import math
from typing import Literal
from pydantic import Field, field_validator, model_validator
from .base_schemas import StrictModel
from .driving_sensor_capture_schemas import DrivingSensorCaptureSpec, driving_capture_template
from .hil_beta_schemas import safe_text
from .scenario_v2_schemas import Identity
from .simulation_catalog_schemas import Sha256

ADAPTER = 'carla-driving-multisensor'
VERSION = 'carla-driving-multisensor/1'
FIDELITY = 'actual_carla_reference_driving_multisensor'
SENSOR_KEYS = ('front', 'rear', 'lidar', 'radar')


def browser_numbers(value):
    if isinstance(value, dict):
        return {key: browser_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [browser_numbers(item) for item in value]
    return int(value) if type(value) is float and value.is_integer() else value


def baseline_rig():
    return browser_numbers(driving_capture_template())


def placement_rig(value):
    """Normalize typed mounts, then reject every non-placement deviation."""
    parsed = DrivingSensorCaptureSpec.model_validate(value).model_dump(mode='json')
    standard = baseline_rig()
    if {k: v for k, v in parsed.items() if k != 'sensors'} != {k: v for k, v in standard.items() if k != 'sensors'}:
        raise ValueError('Placement beta fixes the 20 second Town10HD spawn 0 driving profile and rig revision')
    if [item['key'] for item in parsed['sensors']] != list(SENSOR_KEYS):
        raise ValueError('Placement beta requires the four standard sensors in their original order')
    for actual, expected in zip(parsed['sensors'], standard['sensors']):
        if {k: v for k, v in actual.items() if k not in {'translation_m', 'rotation_deg'}} != {
                k: v for k, v in expected.items() if k not in {'translation_m', 'rotation_deg'}}:
            raise ValueError('Only sensor translation and rotation can change in placement beta')
        for field, step in (('translation_m', .05), ('rotation_deg', 1)):
            if any(not math.isclose(v / step, round(v / step), rel_tol=0, abs_tol=1e-8) for v in actual[field]):
                raise ValueError('Placement mounts use 0.05 meter and 1 degree steps')
    return browser_numbers(parsed)


class SensorLayoutRef(StrictModel):
    id: Identity
    revision: int = Field(ge=1, strict=True)
    sha256: Sha256


class SensorLayoutSave(StrictModel):
    project_id: Identity = 'default'
    id: Identity | None = None
    expected_revision: int = Field(default=0, ge=0, strict=True)
    name: str = Field(min_length=1, max_length=180)
    note: str = Field(default='', max_length=4000)
    rig_b: DrivingSensorCaptureSpec
    copied_from: SensorLayoutRef | None = None

    @field_validator('name', 'note')
    @classmethod
    def clean(cls, value, info):
        value = safe_text(value)
        if info.field_name == 'name' and not value:
            raise ValueError('A layout name is required')
        return value

    @model_validator(mode='after')
    def placement_only(self):
        placement_rig(self.rig_b)
        return self


class SensorLayoutAction(StrictModel):
    project_id: Identity = 'default'
    layout_ref: SensorLayoutRef


class SensorLayoutExecute(SensorLayoutAction):
    preview_hash: Sha256


class SensorLayoutObservationCreate(SensorLayoutAction):
    execution_id: Identity
    sensor_key: Literal['front', 'rear', 'lidar', 'radar']
    side: Literal['rig_a', 'rig_b', 'both'] = 'both'
    time_s: float = Field(ge=0, le=19.9, strict=True, allow_inf_nan=False)
    title: str = Field(min_length=1, max_length=180)
    note: str = Field(min_length=1, max_length=4000)
    kind: Literal['observation', 'issue'] = 'observation'

    @field_validator('title', 'note')
    @classmethod
    def clean(cls, value):
        value = safe_text(value)
        if not value:
            raise ValueError('An observation title and note are required')
        return value
