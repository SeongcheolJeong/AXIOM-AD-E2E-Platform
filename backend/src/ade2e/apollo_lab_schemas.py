"""Engine-neutral Apollo reference model contracts; no vendor ABI or chip claims."""
from typing import Literal
from pydantic import Field, model_validator
from .ecu_lab_schemas import StrictModel, RevisionRef, RevisionSave
from .fpga_lab_schemas import TimingConfig
ADAPTER = 'apollo-camera-reference'
FIDELITY = 'architecture_timing_cpu_reference'

class RawMetadata(StrictModel):
    width: int = Field(default=1920, ge=8, le=1920)
    height: int = Field(default=1080, ge=8, le=1080)
    bits: Literal[10,12] = 12
    packing: Literal['uint16le','mipi'] = 'uint16le'
    bayer: Literal['RGGB','BGGR','GRBG','GBRG'] = 'RGGB'
    stride_bytes: int = Field(default=0, ge=0, le=8192)
    black_level: int = Field(default=64, ge=0, le=4094)
    white_level: int = Field(default=4095, ge=1, le=4095)
    fps: int = Field(default=30, ge=1, le=60)
    @property
    def row_bytes(self): return self.width*2 if self.packing=='uint16le' else self.width*self.bits//8
    @property
    def frame_bytes(self): return (self.stride_bytes or self.row_bytes)*self.height
    @model_validator(mode='after')
    def valid(self):
        if self.width%4 or self.height%2: raise ValueError('RAW width must align to 4 and height to 2')
        if not self.black_level < self.white_level < 2**self.bits: raise ValueError('Invalid black/white levels for RAW bits')
        if self.stride_bytes and self.stride_bytes < self.row_bytes: raise ValueError('Stride is shorter than packed row')
        return self

class RawInput(StrictModel):
    asset_ref: RevisionRef | None = None
    metadata: RawMetadata = Field(default_factory=RawMetadata)
    repeat: bool = False

class ApolloConfig(StrictModel):
    csi_lane_Mbps: int = Field(default=1000, ge=50, le=10000)
    memory_MBps: int = Field(default=8000, ge=100, le=50000)
    memory_bytes: int = Field(default=268435456, ge=1048576, le=1073741824)
    tfe_us: int = Field(default=2000, ge=1, le=100000)
    ofe_us: int = Field(default=4000, ge=1, le=100000)
    ipe_us: int = Field(default=2000, ge=1, le=100000)
    queue_frames: int = Field(default=8, ge=1, le=120)
    zsl_frames: int = Field(default=4, ge=1, le=8)
    deadline_us: int = Field(default=100000, ge=1, le=2000000)
    wb: list[float] = Field(default_factory=lambda:[1.6,1.,1.3], min_length=3,max_length=3)
    shading: float = Field(default=0.,ge=0.,le=1.)
    exposure: float = Field(default=1.,ge=.1,le=8.)
    gamma: float = Field(default=2.2,ge=.5,le=4.)
    denoise: float = Field(default=.25,ge=0.,le=1.)
    sharpen: float = Field(default=.2,ge=0.,le=2.)
    @model_validator(mode='after')
    def gains(self):
        if any(not .1<=x<=8 for x in self.wb): raise ValueError('WB gains must be 0.1–8')
        return self

class ApolloChannel(StrictModel):
    channel: int = Field(ge=0,le=3)
    vc: int = Field(ge=0,le=3)
    tfe: int = Field(default=0,ge=0,le=2)
    design_ref: RevisionRef
    raw: RawInput = Field(default_factory=RawInput)
    source_offset_us: int = Field(default=120000,ge=120000,le=1000000)

class Capture(StrictModel):
    channel: int = Field(ge=0,le=3)
    at_us: int = Field(ge=0,le=3000000)

class ApolloProfileSave(RevisionSave):
    config: ApolloConfig
class ApolloTestSave(RevisionSave):
    profile_ref: RevisionRef
    channels: list[ApolloChannel] = Field(min_length=1,max_length=4)
    captures: list[Capture] = Field(default_factory=list,max_length=12)
    seed: int = Field(default=42,ge=0,le=2147483647)
    @model_validator(mode='after')
    def mapping(self):
        ids=[c.channel for c in self.channels]
        if len(set(ids))!=len(ids) or len({c.vc for c in self.channels})!=len(ids):raise ValueError('Channels and VCs must be unique')
        if any(c.channel not in ids for c in self.captures):raise ValueError('Capture channel inactive')
        if len({(c.channel,c.at_us) for c in self.captures})!=len(self.captures):raise ValueError('Duplicate capture request')
        return self

def validate_snapshot(profile, channels, captures):
    p=ApolloConfig.model_validate(profile)
    total=0
    for ch in channels:
        c=TimingConfig.model_validate(ch['design']['config']); m=RawMetadata.model_validate(ch['raw']['metadata'])
        if (c.width,c.height,c.bits,c.fps)!=(m.width,m.height,m.bits,m.fps):raise ValueError('RAW metadata must match sender dimensions/bits/FPS')
        if c.frames>30:raise ValueError('At most 30 frames per channel')
        total+=c.frames
        if ch.get('asset'):
            if ch['asset']['metadata']!=m.model_dump():raise ValueError('RAW asset metadata differs from test')
            if not ch['raw']['repeat'] and c.frames>ch['asset']['frames']:raise ValueError('RAW file too short; explicitly enable repeat')
    if total>120:raise ValueError('At most 120 source frames')
    shapes={(c['design']['config']['width'],c['design']['config']['height'],c['design']['config']['bits'],c['design']['config']['fps']) for c in channels}
    if len(shapes)!=1:raise ValueError('First reference model requires matching channel shapes and FPS')
    # Full-frame tokens, ZSL retention and in-flight snapshots are bounded at runtime too.
    pixels=max(c['design']['config']['width']*c['design']['config']['height'] for c in channels)
    if pixels*2>p.memory_bytes:raise ValueError('Memory cannot hold one RAW frame')
    horizon=total*(p.tfe_us+p.ofe_us+p.ipe_us+pixels*20/p.memory_MBps)+len(captures)*(p.ofe_us+p.ipe_us+pixels*16/p.memory_MBps)
    if horizon>50000000:raise ValueError('Conservative execution exceeds 60 virtual seconds')
    return p
