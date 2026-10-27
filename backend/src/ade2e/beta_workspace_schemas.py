"""Small common beta contracts; domain records remain owned by their modules."""
from typing import Literal
from pydantic import Field, field_validator
from .base_schemas import StrictModel
from .object_beta_observations import _CREDENTIAL

Module = Literal['object', 'log', 'hil', 'sensor', 'neural']
SensorGuide = Literal['camera_direction', 'lidar_height', 'radar_direction']


class ProjectBody(StrictModel):
    project_id: str = Field(default='default', min_length=1, max_length=64)


class TaskCreate(ProjectBody):
    module: Literal['object', 'log', 'hil', 'sensor']
    title: str = Field(min_length=1, max_length=180)
    mode: Literal['new_execution', 'retained_playback']
    pack_id: str | None = Field(default=None, min_length=1, max_length=100)
    guide_id: SensorGuide | None = None

    @field_validator('title')
    @classmethod
    def title_present(cls, value):
        if not value.strip():
            raise ValueError('A task title is required')
        return value.strip()


class TaskProgress(ProjectBody):
    expected_revision: int = Field(ge=1, strict=True)
    source_ref: dict | None = None
    execution_id: str | None = Field(default=None, min_length=1, max_length=100)
    observation_id: str | None = Field(default=None, min_length=1, max_length=100)
    result_opened: bool | None = None
    time_s: float | None = Field(default=None, ge=0, le=86400, allow_inf_nan=False)
    sensor_key: Literal['front', 'rear', 'lidar', 'radar'] | None = None


class FeedbackCreate(ProjectBody):
    module: Module
    task_id: str | None = Field(default=None, min_length=1, max_length=100)
    execution_id: str | None = Field(default=None, min_length=1, max_length=100)
    kind: Literal['issue', 'help', 'completion']
    message: str = Field(min_length=1, max_length=4000)
    stage: str | None = Field(default=None, max_length=80)
    error: str | None = Field(default=None, max_length=1000)
    time_s: float | None = Field(default=None, ge=0, le=86400, allow_inf_nan=False)

    @field_validator('message', 'error', 'stage')
    @classmethod
    def safe_text(cls, value):
        if value is not None and (not value.strip() or _CREDENTIAL.search(value)):
            raise ValueError('Describe the issue without credentials or blank text')
        return value.strip() if value is not None else None


class FeedbackTriage(ProjectBody):
    expected_revision: int = Field(ge=1, strict=True)
    status: Literal['received', 'investigating', 'resolved']
    note: str | None = Field(default=None, max_length=4000)

    @field_validator('note')
    @classmethod
    def safe_note(cls, value):
        if value and _CREDENTIAL.search(value):
            raise ValueError('Remove credentials from the note')
        return value
