"""Local operator curation pins; never an API write or an execution request."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ExecutionPin(StrictModel):
    role: Literal['left', 'right', 'result']
    id: str = Field(min_length=1, max_length=64)
    input_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    manifest_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class KeyMoment(StrictModel):
    label: str = Field(min_length=1, max_length=160)
    time_s: float = Field(ge=0, le=3600, allow_inf_nan=False)


class CasePin(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    executions: list[ExecutionPin] = Field(min_length=1, max_length=2)
    key_moments: list[KeyMoment] = Field(default_factory=list, max_length=5)
    log_variant_id: str | None = Field(default=None, min_length=1, max_length=64)
    observation: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode='after')
    def unique_roles(self):
        roles = [item.role for item in self.executions]
        if sorted(roles) not in [['result'], ['left', 'right']]:
            raise ValueError('Use one result or an explicit left/right pair')
        return self


class ProjectCuration(StrictModel):
    project_id: str = Field(min_length=1, max_length=64)
    cases: list[CasePin] = Field(max_length=10)

    @model_validator(mode='after')
    def unique_cases(self):
        if len({item.id for item in self.cases}) != len(self.cases):
            raise ValueError('Duplicate curated case')
        return self


class DemoCaseCuration(StrictModel):
    schema_version: Literal['demo-case-curation/1']
    projects: list[ProjectCuration] = Field(max_length=100)

    @model_validator(mode='after')
    def unique_projects(self):
        if len({item.project_id for item in self.projects}) != len(self.projects):
            raise ValueError('Duplicate project curation')
        return self
