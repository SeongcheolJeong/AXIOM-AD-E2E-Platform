"""Versioned, engine-neutral contracts for the SA8650 reference behavior model."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .fpga_lab_schemas import TimingConfig

ADAPTER = 'sa8650-ecu-behavioral'
FIDELITY = 'behavioral_timing'

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class RevisionRef(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    revision: int = Field(ge=1)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')

class EcuProfileConfig(StrictModel):
    width: int = Field(default=1920, ge=4, le=4096)
    height: int = Field(default=1080, ge=1, le=2160)
    bits: Literal[8, 10, 12, 16] = 12
    fps: int = Field(default=30, ge=1, le=120)
    csi_lanes: int = Field(default=4, ge=1, le=4)
    csi_lane_Mbps: int = Field(default=1000, ge=1, le=10000)
    csi_efficiency_percent: int = Field(default=80, ge=1, le=100)
    des_buffer_bytes: int = Field(default=67108864, ge=1, le=1073741824)
    receive_queue_frames: int = Field(default=3, ge=1, le=120)
    processing_us: int = Field(default=4000, ge=1, le=1000000)
    boot_us: int = Field(default=100000, ge=0, le=1000000)
    link_lock_us: int = Field(default=10000, ge=0, le=1000000)
    input_timeout_us: int = Field(default=100000, ge=1, le=10000000)
    processing_deadline_us: int = Field(default=100000, ge=1, le=10000000)
    heartbeat_period_us: int = Field(default=10000, ge=1, le=1000000)
    heartbeat_timeout_us: int = Field(default=30000, ge=1, le=10000000)
    recovery_good_frames: int = Field(default=3, ge=1, le=20)

    @model_validator(mode='after')
    def valid(self):
        if self.width % (4 if self.bits == 10 else 2 if self.bits == 12 else 1):
            raise ValueError('RAW packing requires aligned width')
        if self.heartbeat_timeout_us < self.heartbeat_period_us:
            raise ValueError('Heartbeat timeout must cover at least one period')
        return self

class EcuChannel(StrictModel):
    channel: int = Field(ge=0, le=3)
    vc: int = Field(ge=0, le=3)
    design_ref: RevisionRef
    source_offset_us: int = Field(default=120000, ge=0, le=1000000)
    duplicate_every: int = Field(default=0, ge=0, le=120)

class EcuFault(StrictModel):
    kind: Literal['link_down', 'processor_stall', 'heartbeat_loss']
    at_us: int = Field(ge=0, le=10000000)
    duration_us: int = Field(ge=1, le=1000000)
    channel: int | None = Field(default=None, ge=0, le=3)

    @model_validator(mode='after')
    def scope(self):
        if (self.kind == 'link_down') != (self.channel is not None):
            raise ValueError('Only link_down requires a channel; processor and heartbeat faults are shared')
        return self

class EcuCommand(StrictModel):
    command: Literal['configure', 'start', 'stop', 'reset']
    at_us: int = Field(ge=0, le=10000000)

DEFAULT_COMMANDS = [dict(command='configure', at_us=100000), dict(command='start', at_us=110000)]

class RevisionSave(StrictModel):
    project_id: str = Field(default='default', min_length=1, max_length=64)
    id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,64}$')
    expected_revision: int = Field(default=0, ge=0)
    name: str = Field(min_length=1, max_length=180)

class EcuProfileSave(RevisionSave):
    config: EcuProfileConfig

class EcuTestSave(RevisionSave):
    profile_ref: RevisionRef
    channels: list[EcuChannel] = Field(min_length=1, max_length=4)
    commands: list[EcuCommand] = Field(default_factory=lambda: [EcuCommand(**c) for c in DEFAULT_COMMANDS], max_length=32)
    faults: list[EcuFault] = Field(default_factory=list, max_length=32)
    seed: int = Field(default=42, ge=0, le=2147483647)

    @model_validator(mode='after')
    def mapping(self):
        channels = [c.channel for c in self.channels]
        if len(channels) != len(set(channels)) or len({c.vc for c in self.channels}) != len(channels):
            raise ValueError('Active channels and virtual channels must each be unique')
        if any(f.channel is not None and f.channel not in channels for f in self.faults):
            raise ValueError('Fault references an inactive channel')
        if len({c.at_us for c in self.commands}) != len(self.commands):
            raise ValueError('Commands require distinct timestamps')
        for index, a in enumerate(self.faults):
            for b in self.faults[index + 1:]:
                if a.kind == b.kind and a.channel == b.channel and max(a.at_us, b.at_us) < min(a.at_us+a.duration_us, b.at_us+b.duration_us):
                    raise ValueError('Overlapping faults of the same kind and scope are unsupported')
        return self

def validate_inputs(profile, channels):
    """Channels are hydrated with immutable FPGA design documents."""
    p = EcuProfileConfig.model_validate(profile)
    total_frames = 0
    event_budget = 0
    for channel in channels:
        c = TimingConfig.model_validate(channel['design']['config'])
        if (c.width, c.height, c.bits, c.fps) != (p.width, p.height, p.bits, p.fps):
            raise ValueError('RAW dimensions, packing and FPS must match the ECU profile')
        total_frames += c.frames
        event_budget += c.frames * (c.height * 7 + 40)
    if total_frames > 120 or event_budget > 1000000:
        raise ValueError('Combined channels exceed 120 frames or the 1000000 event budget')
    if p.des_buffer_bytes < p.width * p.bits // 8:
        raise ValueError('Deserializer buffer must hold at least one RAW line')
    return p

def validate_runtime(profile, channels, commands, faults):
    """Conservative bound for finite draining and watchdog work, before dispatch."""
    p = validate_inputs(profile, channels)
    frame_count = sum(c['design']['config']['frames'] for c in channels)
    source_end = sender_drain = total_wire_bytes = base_events = 0
    for ch in channels:
        c = TimingConfig.model_validate(ch['design']['config'])
        source_end = max(source_end, ch['source_offset_us'] + c.frames * 1000000 // c.fps
                         + c.supply_delay_us + c.start_delay_us + c.jitter_us)
        wire_bytes = (c.line_bytes + c.overhead_bytes) * c.height * c.frames
        total_wire_bytes += wire_bytes
        # Independent read/write/link ports actually overlap; the sum bounds all tails.
        sender_drain = max(sender_drain, c.frames*c.frame_bytes*(1/c.write_MBps+1/c.read_MBps)
                           + wire_bytes*8/c.link_Mbps + c.propagation_us)
        base_events += c.frames*(c.height*7+40)
    csi_Mbps = p.csi_lanes*p.csi_lane_Mbps*p.csi_efficiency_percent/100
    control_end = max([0]+[c['at_us'] for c in commands]+[f['at_us']+f['duration_us'] for f in faults])
    horizon = max(source_end, control_end) + sender_drain + total_wire_bytes*8/csi_Mbps
    horizon += frame_count*p.processing_us + sum(f['duration_us'] for f in faults)
    horizon += p.boot_us+p.link_lock_us+p.input_timeout_us+p.processing_deadline_us+p.heartbeat_timeout_us
    if horizon > 60000000:
        raise ValueError('Conservative full execution horizon exceeds 60 virtual seconds; reduce frames or increase service rates')
    # Heartbeat outages are modeled by boundary events, not periodic ticks.
    if base_events + len(commands)*20 + len(faults)*20 > 1000000:
        raise ValueError('Combined source and control event budget exceeds 1000000; reduce frames')
    return p
