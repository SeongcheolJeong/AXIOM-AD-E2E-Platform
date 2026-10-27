"""Versioned, engine-neutral experiment authoring and durable receipts."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

ExperimentKind = Literal['virtual_ecu', 'fpga_build', 'engineering_lab', 'fpga_workbench', 'fpga_rtl', 'apollo_camera', 'ecu_timing', 'fpga_timing', 'object_sim', 'planning_replay', 'sensor_capture', 'isp_compare', 'driving_open_loop', 'hil_demo', 'neural_reconstruction', 'neural_render', 'neural_source_import', 'neural_perception']
ComparisonKind = Literal['single', 'same_input_model_ab', 'same_raw_isp_ab', 'same_scene_sensor_ab', 'same_scene_fault_ab', 'same_fault_dut_ab']

class ExperimentCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: str = Field(default='default', min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=180)
    kind: ExperimentKind
    adapter_id: str = Field(min_length=1, max_length=180)
    adapter_version: str | None = Field(default=None, max_length=180)
    input: dict[str, Any]
    model: dict[str, Any] | None = None
    sensor: dict[str, Any] | None = None
    evaluator: dict[str, Any] | None = None
    comparison_kind: ComparisonKind = 'single'
    seed: int = Field(default=42, ge=0, le=2147483647, strict=True)

    @model_validator(mode='after')
    def coherent_comparison(self):
        if (self.comparison_kind in {'same_scene_fault_ab', 'same_fault_dut_ab'}) != (self.kind == 'hil_demo'):
            raise ValueError('Software-DUT HIL demos require same_scene_fault_ab or same_fault_dut_ab comparison')
        if self.comparison_kind == 'same_raw_isp_ab' and self.kind != 'isp_compare':
            raise ValueError('RAW/ISP comparison requires the isp_compare experiment kind')
        if self.comparison_kind == 'same_scene_sensor_ab' and self.kind != 'sensor_capture':
            raise ValueError('Scene sensor comparison requires the sensor_capture experiment kind')
        if self.comparison_kind == 'same_input_model_ab' and self.kind not in {'driving_open_loop', 'planning_replay'}:
            raise ValueError('Same-input model comparison requires a supported replay kind')
        return self

class ExperimentSubmit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$')

class ExperimentJobDetail(BaseModel):
    id: str
    experiment_id: str
    project_id: str
    status: str
    kind: Literal['experiment'] = 'experiment'
    experiment_kind: ExperimentKind
    adapter_id: str
    adapter_version: str
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    dispatched_at: str | None = None
    worker_id: str | None = None
    input_hash: str
    error: str | None = None
    outcome_uncertain: bool = False
    cancellation_supported: bool
    cancel_requested_at: str | None = None
    failure_http_status: int | None = None
    summary: dict | None = None
    fidelity: str | None = None
    artifacts: list[dict] = Field(default_factory=list)
    events: list[dict] = Field(default_factory=list)
    manifest_hash: str | None = None
    download_url: str | None = None
