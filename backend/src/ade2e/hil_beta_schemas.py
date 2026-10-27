"""Saved software-DUT tests and user observations, independent of engine uptime."""
from typing import Literal
from pydantic import Field, StrictFloat, StrictInt, field_validator
from .base_schemas import StrictModel
from .object_beta_observations import _CREDENTIAL
from .scenario_v2_schemas import Identity
from .simulation_catalog_schemas import Sha256


def safe_text(value):
    value = value.strip()
    if _CREDENTIAL.search(value):
        raise ValueError('Remove credentials from test names and observations')
    return value


class HilTestRef(StrictModel):
    id: Identity
    revision: int = Field(ge=1, strict=True)
    sha256: Sha256


class HilDutProfileParameters(StrictModel):
    max_object_age_ms: int = Field(strict=True, ge=50, le=1000, multiple_of=50)
    recovery_fresh_steps: int = Field(strict=True, ge=1, le=10)


class HilDutProfileSave(StrictModel):
    project_id: Identity = 'default'
    id: Identity | None = None
    expected_revision: int = Field(default=0, ge=0, strict=True)
    name: str = Field(min_length=1, max_length=180)
    note: str = Field(default='', max_length=4000)
    parameters: HilDutProfileParameters

    @field_validator('name', 'note')
    @classmethod
    def clean(cls, value, info):
        value = safe_text(value)
        if info.field_name == 'name' and not value:
            raise ValueError('A DUT profile name is required')
        return value


class HilDutProfileRecord(StrictModel):
    schema_version: Literal['hil-dut-profile/1']
    id: Identity
    project_id: Identity
    revision: int = Field(ge=1, strict=True)
    name: str = Field(min_length=1, max_length=180)
    note: str = Field(max_length=4000)
    parameters: HilDutProfileParameters
    created_at: str = Field(min_length=1, max_length=40)
    author: dict[str, str]
    sha256: Sha256


class HilDutProfileRefs(StrictModel):
    baseline: HilTestRef
    faulted: HilTestRef


class HilTestSave(StrictModel):
    project_id: Identity = 'default'
    id: Identity | None = None
    expected_revision: int = Field(default=0, ge=0, strict=True)
    name: str = Field(min_length=1, max_length=180)
    note: str = Field(default='', max_length=4000)
    preset_id: Literal['input_delay', 'input_dropout', 'input_repeat']
    binding_revision: int = Field(ge=1, strict=True)
    binding_sha256: Sha256
    parameters: dict[str, StrictInt | StrictFloat] = Field(default_factory=dict, max_length=3)
    copied_from: HilTestRef | None = None
    dut_profiles: HilDutProfileRefs | None = None
    scene_profile: HilTestRef | None = None

    @field_validator('name', 'note')
    @classmethod
    def clean(cls, value, info):
        value = safe_text(value)
        if info.field_name == 'name' and not value:
            raise ValueError('A test name is required')
        return value


class HilTestAction(StrictModel):
    project_id: Identity = 'default'
    test_ref: HilTestRef


class HilTestAttach(HilTestAction):
    execution_id: Identity


class HilObservationCreate(StrictModel):
    project_id: Identity = 'default'
    execution_id: Identity
    test_ref: HilTestRef | None = None
    side: Literal['baseline', 'faulted', 'both'] = 'both'
    time_s: float = Field(ge=0, le=18, strict=True, allow_inf_nan=False)
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
