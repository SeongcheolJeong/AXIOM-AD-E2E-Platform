"""Engine-neutral contracts for the bounded camera timing laboratory."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

ADAPTER = 'simpy-camera-timing'
FIDELITY = 'behavioral_timing'

class TimingConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    width: int = Field(default=1920, ge=4, le=4096)
    height: int = Field(default=1080, ge=1, le=2160)
    bits: Literal[8, 10, 12, 16] = 12
    fps: int = Field(default=30, ge=1, le=120)
    frames: int = Field(default=60, ge=1, le=120)
    supply_delay_us: int = Field(default=0, ge=0, le=1000000)
    ddr_bytes: int = Field(default=12582912, ge=1, le=1073741824)
    fifo_bytes: int = Field(default=65536, ge=1, le=16777216)
    write_MBps: int = Field(default=800, ge=1, le=10000)
    read_MBps: int = Field(default=800, ge=1, le=10000)
    preload_frames: int = Field(default=1, ge=0, le=120)
    start_delay_us: int = Field(default=10000, ge=0, le=1000000)
    jitter_us: int = Field(default=0, ge=0, le=1000000)
    drop_every: int = Field(default=0, ge=0, le=120)
    overhead_bytes: int = Field(default=6, ge=0, le=4096)
    link_Mbps: int = Field(default=1200, ge=1, le=100000)
    propagation_us: int = Field(default=100, ge=0, le=1000000)
    expected_fps: int = Field(default=30, ge=1, le=120)
    deadline_us: int = Field(default=40000, ge=1, le=10000000)
    timeout_us: int = Field(default=100000, ge=1, le=10000000)

    @property
    def line_bytes(self):
        return self.width * self.bits // 8

    @property
    def frame_bytes(self):
        return self.line_bytes * self.height

    @model_validator(mode='after')
    def bounded(self):
        alignment = {8: 1, 10: 4, 12: 2, 16: 1}[self.bits]
        if self.width % alignment:
            raise ValueError(f'RAW{self.bits} requires width aligned to {alignment} pixels')
        if self.frames > self.fps * 10:
            raise ValueError('At most 10 seconds of source frames are supported')
        if self.frames * (self.height * 6 + 10) > 1000000:
            raise ValueError('Conservative event budget exceeds 1,000,000')
        if self.fifo_bytes < self.line_bytes:
            raise ValueError('FIFO must hold at least one payload line')
        if self.preload_frames > self.frames or self.preload_frames * self.frame_bytes > self.ddr_bytes:
            raise ValueError('Initial frames exceed source length or DDR capacity')
        if self.deadline_us > self.timeout_us:
            raise ValueError('Deadline must not exceed timeout')
        return self

class DesignSave(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: str = Field(default='default', min_length=1, max_length=64)
    id: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    expected_revision: int = Field(default=0, ge=0, strict=True)
    name: str = Field(min_length=1, max_length=180)
    config: TimingConfig = Field(default_factory=TimingConfig)
    seed: int = Field(default=42, ge=0, le=2147483647, strict=True)

class DesignAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: str = Field(default='default', min_length=1, max_length=64)
    revision: int = Field(ge=1, strict=True)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')

PRESETS = [
    ('normal', '정상 전송', {}),
    ('bandwidth', '대역폭 부족', {'fps': 60, 'frames': 120, 'expected_fps': 60}),
    ('supply', '공급 부족', {'preload_frames': 0, 'supply_delay_us': 45000}),
    ('fault', '타이밍 고장', {'start_delay_us': 30000, 'jitter_us': 20000, 'drop_every': 10}),
]
