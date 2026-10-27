"""Host-process vECU authoring. No executable paths cross the public API."""
from typing import Literal
from pydantic import Field, model_validator
from .base_schemas import StrictModel
from .hil_beta_schemas import HilDutProfileParameters, HilTestRef

class SaveBase(StrictModel):
    project_id: str = Field(default='default', min_length=1, max_length=64)
    id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,64}$')
    expected_revision: int = Field(default=0, ge=0, strict=True)
    name: str = Field(min_length=1, max_length=180)

class PackageSave(SaveBase):
    runtime_id: str | None = Field(default=None,min_length=1,max_length=80)
    build_execution_id: str | None = Field(default=None,pattern=r'^job_[A-Za-z0-9_-]{1,80}$')
    @model_validator(mode='after')
    def executable_source(self):
        if (self.runtime_id is None)==(self.build_execution_id is None):
            raise ValueError('Package requires exactly one registered runtime or completed build execution')
        return self

class BuildCreate(StrictModel):
    project_id: str = Field(default='default',min_length=1,max_length=64)
    name: str = Field(min_length=1,max_length=180)
    source_id: Literal['cpp-immediate','cpp-ramp']

class Actor(StrictModel):
    id: str = Field(min_length=1,max_length=64)
    x: float = Field(allow_inf_nan=False,ge=-100000,le=100000)
    y: float = Field(default=0,allow_inf_nan=False,ge=-100000,le=100000)
    z: float = Field(default=0,allow_inf_nan=False,ge=-10000,le=10000)
    heading_rad: float = Field(default=0,allow_inf_nan=False,ge=-7,le=7)
    speed_mps: float = Field(ge=0,le=100,allow_inf_nan=False)

class RoutePoint(StrictModel):
    x: float = Field(allow_inf_nan=False,ge=-100000,le=100000)
    y: float = Field(default=0,allow_inf_nan=False,ge=-100000,le=100000)
    z: float = Field(default=0,allow_inf_nan=False,ge=-10000,le=10000)

class Frame(StrictModel):
    ego: Actor
    objects: list[Actor] = Field(max_length=32)
    @model_validator(mode='after')
    def unique(self):
        ids=[self.ego.id]+[o.id for o in self.objects]
        if len(ids)!=len(set(ids)):raise ValueError('Actor IDs must be unique per frame')
        return self

class InputSave(SaveBase):
    source_kind: Literal['synthetic_reference','provided_unvalidated'] = 'synthetic_reference'
    seed: int = Field(default=42,ge=0,le=2147483647,strict=True)
    period_ns: Literal[50000000] = 50000000
    route: list[RoutePoint] = Field(min_length=2,max_length=1000)
    frames: list[Frame] = Field(min_length=20,max_length=1200)
    target_speed_mps: float = Field(default=3,ge=0,le=4,allow_inf_nan=False)
    wheelbase_m: float = Field(default=2.7,gt=0,le=10,allow_inf_nan=False)
    max_steer_angle_rad: float = Field(default=.6,gt=0,le=1.5,allow_inf_nan=False)
    @model_validator(mode='after')
    def route_length(self):
        if not any((a.x,a.y)!=(b.x,b.y) for a,b in zip(self.route,self.route[1:])):
            raise ValueError('Route must have nonzero length')
        if len({f.ego.id for f in self.frames})!=1:raise ValueError('Ego identity must remain constant')
        return self

class Fault(StrictModel):
    kind: Literal['delay','dropout','repeat']
    start_step: int = Field(ge=1,strict=True)
    end_step: int = Field(gt=1,strict=True)
    delay_steps: int = Field(default=8,ge=1,le=40,strict=True)
    @model_validator(mode='after')
    def interval(self):
        if self.end_step<=self.start_step:raise ValueError('Fault requires start < end')
        return self

class TestSave(SaveBase):
    mode: Literal['fixed_replay','carla_closed_loop'] = 'fixed_replay'
    package_ref: HilTestRef
    variant_package_ref: HilTestRef | None = None
    input_ref: HilTestRef | None = None
    hil_test_ref: HilTestRef | None = None
    comparison: Literal['fault_ab','settings_ab','package_ab'] = 'fault_ab'
    fault_enabled: bool = Field(default=True,strict=True)
    baseline: HilDutProfileParameters = Field(default_factory=lambda:HilDutProfileParameters(max_object_age_ms=250,recovery_fresh_steps=3))
    variant: HilDutProfileParameters = Field(default_factory=lambda:HilDutProfileParameters(max_object_age_ms=250,recovery_fresh_steps=3))
    faults: list[Fault] = Field(default_factory=list,max_length=16)
    seed: int = Field(default=42,ge=0,le=2147483647,strict=True)
    @model_validator(mode='after')
    def mode_inputs(self):
        if (self.comparison=='package_ab')!=(self.variant_package_ref is not None):
            raise ValueError('Package A/B requires variant_package_ref; other comparisons use one package')
        if not self.fault_enabled and self.comparison!='package_ab':
            raise ValueError('fault_enabled is configurable only for package A/B')
        if self.mode=='fixed_replay':
            if self.comparison=='fault_ab' and self.baseline!=self.variant:raise ValueError('Fault A/B requires identical DUT settings; use settings_ab for parameter comparison')
            if self.input_ref is None or self.hil_test_ref is not None:raise ValueError('Replay requires only input_ref')
            ordered=sorted(self.faults,key=lambda f:f.start_step)
            if self.faults!=ordered or any(a.end_step>b.start_step for a,b in zip(ordered,ordered[1:])):
                raise ValueError('Fault intervals must be ordered and nonoverlapping')
        else:
            if self.hil_test_ref is None or self.input_ref is not None:raise ValueError('Closed loop requires only saved HIL test')
            defaults=HilDutProfileParameters(max_object_age_ms=250,recovery_fresh_steps=3)
            if self.faults or (self.comparison!='package_ab' and (self.baseline!=defaults or self.variant!=defaults or self.comparison!='fault_ab')):
                raise ValueError('Closed loop inherits fault and DUT settings from its saved HIL revision')
        return self

class Action(StrictModel):
    project_id: str = 'default'
    revision: int = Field(ge=1,strict=True)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
