"""Versioned, engine-neutral inputs for an assumed rigid-body stunt vehicle."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .service import digest

ADAPTER = 'carla-stunt'
FIDELITY = 'assumed_vehicle_rigid_body'
PHYSICAL_VALIDATION = 'not_established'
RAMP_BLOCKED_REASON = 'Ramp collision asset and compatible native map have not been verified'


class StuntConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    schema_version: Literal['stunt-spec/1'] = 'stunt-spec/1'
    preset: Literal['kickflip', 'ramp'] = 'kickflip'
    vehicle_blueprint: str = Field(default='vehicle.audi.tt', pattern=r'^vehicle\.[a-zA-Z0-9_.-]{1,100}$')
    map_name: str = Field(default='Town10HD_Opt', pattern=r'^[a-zA-Z0-9_-]{1,100}$')
    mass_kg: float = Field(default=1500.0, ge=500, le=4000)
    initial_speed_mps: float = Field(default=3.0, ge=0, le=15)
    max_thrust_n: float = Field(default=40000.0, gt=0, le=150000)
    impulse_budget_ns: float = Field(default=120000.0, ge=0, le=1000000)
    thrust_scale: float = Field(default=1.0, ge=0, le=1)
    target_height_m: float = Field(default=5.0, ge=1, le=15)
    duration_s: float = Field(default=10.0, ge=3, le=20)
    dt_s: Literal[0.01, 0.005] = 0.01
    camera_fps: Literal[20] = 20
    suction_force_n: float = Field(default=25000.0, ge=0, le=100000)

    @model_validator(mode='after')
    def aligned_capture_duration(self):
        frames = self.duration_s*self.camera_fps
        if abs(frames-round(frames)) > 1e-8:
            raise ValueError('Duration must align to the 20fps camera period (0.05s)')
        return self


class DesignSave(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: str = Field(default='default', min_length=1, max_length=64)
    id: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    expected_revision: int = Field(default=0, ge=0, strict=True)
    name: str = Field(min_length=1, max_length=180)
    config: StuntConfig = Field(default_factory=StuntConfig)
    seed: int = Field(default=42, ge=0, le=2147483647, strict=True)


class DesignRef(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    revision: int = Field(ge=1, strict=True)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class DesignAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: str = Field(default='default', min_length=1, max_length=64)
    revision: int = Field(ge=1, strict=True)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


def model_definition():
    """A frozen assumption, not Tesla geometry or a measured vehicle model."""
    value = dict(version='stunt-thrusters/1', body_axes=dict(x='forward', y='right', z='up'),
        position_unit='m', force_unit='N', impulse_unit='N s',
        thrusters=[dict(id=identity, position_m=[x, y, 0.0], direction=[0.0, 0.0, 1.0],
                        bidirectional=True, max_thrust_fraction=0.25)
                   for identity, x, y in [('front-left', 1.5, -0.75), ('front-right', 1.5, 0.75),
                                          ('rear-left', -1.5, -0.75), ('rear-right', -1.5, 0.75)]],
        budget='integral_of_sum_absolute_thruster_forces',
        controller_version='kickflip-controller/1',
        landing_criteria=dict(max_abs_roll_deg=10, max_abs_pitch_deg=10,
                              max_precontact_vertical_speed_mps=2, max_settled_speed_mps=0.5,
                              settled_duration_s=1),
        physical_validation=PHYSICAL_VALIDATION)
    return value | {'sha256': digest(value)}


PRESETS = [
    ('kickflip', 'Kickflip · 기준 추력', {}),
    ('kickflip-low-thrust', 'Kickflip · 추력 부족', {'thrust_scale': 0.3}),
    ('ramp', 'Ramp · 충돌 에셋 준비 필요', {'preset': 'ramp'}),
]
