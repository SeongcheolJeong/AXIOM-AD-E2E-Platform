"""Bounded Log Sim demo authoring over existing source and execution contracts."""
from pydantic import Field, model_validator
from .base_schemas import StrictModel
from .scenario_v2_schemas import Identity
from .simulation_catalog_schemas import Sha256


class LogVariantPreview(StrictModel):
    project_id: Identity = 'default'
    source_id: Identity
    case_id: Identity | None = None
    case_revision: int | None = Field(default=None, ge=1, strict=True)
    lead_wait_s: float = Field(ge=0, le=6, strict=True, allow_inf_nan=False)

    @model_validator(mode='after')
    def case_pin(self):
        if (self.case_id is None) != (self.case_revision is None):
            raise ValueError('Case identity and revision must be supplied together')
        return self


class LogVariantSubmit(LogVariantPreview):
    preview_hash: Sha256


class LogCaseCreate(StrictModel):
    project_id: Identity = 'default'
    title: str = Field(min_length=1, max_length=180)
    note: str = Field(min_length=1, max_length=4000)
    start_time_s: float = Field(ge=0, strict=True, allow_inf_nan=False)
    end_time_s: float = Field(ge=0, strict=True, allow_inf_nan=False)

    @model_validator(mode='after')
    def interval(self):
        if self.end_time_s < self.start_time_s:
            raise ValueError('Select an ordered source interval')
        return self
