"""Small, explicitly submitted sequences of immutable HIL test revisions."""
from pydantic import Field, field_validator
from .base_schemas import StrictModel
from .hil_beta_schemas import HilTestRef, safe_text
from .scenario_v2_schemas import Identity
from .simulation_catalog_schemas import Sha256


class HilBatchCreate(StrictModel):
    project_id: Identity = 'default'
    name: str = Field(min_length=1, max_length=180)
    note: str = Field(default='', max_length=4000)
    tests: list[HilTestRef] = Field(min_length=3, max_length=5)

    @field_validator('name', 'note')
    @classmethod
    def clean(cls, value, info):
        value = safe_text(value)
        if info.field_name == 'name' and not value:
            raise ValueError('A batch name is required')
        return value

    @field_validator('tests')
    @classmethod
    def unique(cls, value):
        if len({(item.id, item.revision, item.sha256) for item in value}) != len(value):
            raise ValueError('Select distinct exact test revisions')
        return value


class HilBatchAction(StrictModel):
    project_id: Identity = 'default'
    expected_revision: int = Field(strict=True, ge=1)


class HilBatchSubmit(HilBatchAction):
    preview_hash: Sha256
