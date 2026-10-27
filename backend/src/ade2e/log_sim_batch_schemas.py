"""Bounded, project-owned Log Sim batch requests."""
from pydantic import Field, model_validator
from .base_schemas import StrictModel
from .log_sim_beta import SettingsRef
from .scenario_v2_schemas import Identity
from .simulation_catalog_schemas import Sha256


class BatchSelection(StrictModel):
    project_id: Identity = 'default'
    source_ids: list[Identity] = Field(min_length=1, max_length=64)
    library_id: Identity | None = None
    comparison_ref: SettingsRef | None = None
    selected_scene_ids: list[Identity] | None = Field(default=None, min_length=1, max_length=20)

    @model_validator(mode='after')
    def unique_sources(self):
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError('Select each original source interval only once')
        self.source_ids = sorted(self.source_ids)
        if self.selected_scene_ids is not None:
            if self.library_id is None or len(set(self.selected_scene_ids)) != len(self.selected_scene_ids):
                raise ValueError('Scene selection requires a library and unique scene IDs')
            self.selected_scene_ids = sorted(self.selected_scene_ids)
        return self


class BatchSubmit(BatchSelection):
    preview_hash: Sha256


class BatchAction(StrictModel):
    project_id: Identity = 'default'


class BundleCreate(BatchAction):
    name: str = Field(min_length=1, max_length=180)
    candidate_ids: list[Identity] = Field(min_length=1, max_length=100)
    report_hash: Sha256

    @model_validator(mode='after')
    def unique_candidates(self):
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError('Select each candidate once')
        self.candidate_ids = sorted(self.candidate_ids)
        return self
