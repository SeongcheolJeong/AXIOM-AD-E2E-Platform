"""Bounded four-camera module contracts; no physical target binding is implied."""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from .ecu_lab_schemas import StrictModel, RevisionRef

Gain = Annotated[int, Field(ge=0, le=65535, strict=True)]


class AdasChannelConfig(StrictModel):
    channel: int = Field(ge=0, le=3, strict=True)
    bayer_pattern: Literal['RGGB', 'BGGR', 'GRBG', 'GBRG'] = 'RGGB'
    black_level: int = Field(default=64, ge=0, le=4095, strict=True)
    gains: list[Gain] = Field(default_factory=lambda: [128]*4, min_length=4, max_length=4)
    source_kind: Literal['synthetic', 'raw_asset'] = 'synthetic'
    source_pattern: Literal['gradient', 'color_bars', 'impulse'] = 'color_bars'
    raw_asset_ref: RevisionRef | None = None
    raw_repeat: bool = False
    raw_frame_index: int = Field(default=0, ge=0, le=29, strict=True)

    @model_validator(mode='after')
    def source(self):
        if (self.source_kind == 'raw_asset') != (self.raw_asset_ref is not None):
            raise ValueError('RAW source requires an immutable asset reference; synthetic source forbids one')
        if self.source_kind == 'synthetic' and (self.raw_frame_index or self.raw_repeat):
            raise ValueError('Synthetic source has no RAW frame index')
        return self


class AdasFault(StrictModel):
    channel: int = Field(ge=0, le=3, strict=True)
    kind: Literal['output_stall', 'input_gap', 'reset']
    at_cycle: int = Field(ge=2, le=6099999999, strict=True)
    duration_cycles: int = Field(ge=1, le=6100000000, strict=True)


class AdasCsrUpdate(StrictModel):
    channel: int = Field(ge=0, le=3, strict=True)
    at_cycle: int = Field(ge=2, le=6099999999, strict=True)
    black_level: int | None = Field(default=None, ge=0, le=4095, strict=True)
    gains: list[Gain] | None = Field(default=None, min_length=4, max_length=4)

    @model_validator(mode='after')
    def nonempty(self):
        if self.black_level is None and self.gains is None:
            raise ValueError('CSR update must change black_level or gains')
        return self


class AdasTraceWindow(StrictModel):
    start_cycle: int = Field(ge=0, le=6099999999, strict=True)
    end_cycle: int = Field(ge=1, le=6100000000, strict=True)


class AdasModuleConfig(StrictModel):
    engine: Literal['adas_module_rtl']
    pipeline_id: Literal['adas-camera-4ch/1'] = 'adas-camera-4ch/1'
    width: int = Field(default=1920, ge=8, le=1920, strict=True)
    height: int = Field(default=1080, ge=8, le=1080, strict=True)
    frames: int = Field(default=30, ge=1, le=1800, strict=True)
    fps: Literal[30] = 30
    clock_hz: Literal[100000000] = 100000000
    fifo_depth: Literal[4096] = 4096
    pixel_rate_hz: int = Field(default=74250000, ge=1, le=100000000, strict=True)
    raster_width: int = Field(default=2200, ge=8, le=2200, strict=True)
    raster_height: int = Field(default=1125, ge=8, le=1125, strict=True)
    channels: list[AdasChannelConfig] = Field(default_factory=lambda: [AdasChannelConfig(channel=i) for i in range(4)], min_length=4, max_length=4)
    faults: list[AdasFault] = Field(default_factory=list, max_length=4096)
    csr_updates: list[AdasCsrUpdate] = Field(default_factory=list, max_length=4096)
    trace_windows: list[AdasTraceWindow] = Field(default_factory=lambda: [AdasTraceWindow(start_cycle=0, end_cycle=1024)], max_length=16)
    max_cycles: int = Field(default=110000000, ge=16, le=6100000000, strict=True)
    preview_frames: list[Annotated[int, Field(ge=0, le=1799, strict=True)]] | None = Field(default=None, max_length=32)
    init_mode: Literal['zero', 'randomized'] = 'zero'
    init_seed: int = Field(default=42, ge=1, le=2147483647, strict=True)
    hardware_target: str | None = Field(default=None, max_length=128)

    @model_validator(mode='after')
    def bounded(self):
        if self.width % 2 or self.height % 2:
            raise ValueError('ADAS Bayer dimensions must be even')
        if [c.channel for c in self.channels] != list(range(4)):
            raise ValueError('ADAS requires exactly four channels ordered 0, 1, 2, 3')
        if self.raster_width < self.width or self.raster_height < self.height:
            raise ValueError('Raster dimensions must contain the active image')
        if (self.width, self.height) == (1920, 1080) and (self.pixel_rate_hz, self.raster_width, self.raster_height) != (74250000, 2200, 1125):
            raise ValueError('1080p30 uses the fixed 74.25 MHz, 2200 x 1125 raster')
        if self.hardware_target is not None:
            raise ValueError('Physical hardware target binding is unavailable; no board or ECU execution is configured')
        if self.preview_frames is not None and (len(set(self.preview_frames)) != len(self.preview_frames) or any(f >= self.frames for f in self.preview_frames)):
            raise ValueError('Preview frame indices must be unique and inside the requested frame range')
        intervals = {}
        for fault in self.faults:
            if fault.kind == 'reset' and fault.duration_cycles != 1:
                raise ValueError('Reset is one CSR write-one pulse; duration_cycles must be 1. at_cycle is the request cycle; application follows bus latency')
            if fault.at_cycle + fault.duration_cycles > self.max_cycles:
                raise ValueError('Fault interval must lie inside the cycle budget')
            key = (fault.channel, fault.kind)
            previous = intervals.setdefault(key, [])
            if any(fault.at_cycle < end and start < fault.at_cycle + fault.duration_cycles for start, end in previous):
                raise ValueError('Fault intervals of the same channel and kind must not overlap')
            previous.append((fault.at_cycle, fault.at_cycle + fault.duration_cycles))
        seen = set()
        for update in self.csr_updates:
            key = (update.channel, update.at_cycle)
            if update.at_cycle >= self.max_cycles or key in seen:
                raise ValueError('CSR updates must have unique channel/cycle pairs inside the cycle budget')
            seen.add(key)
        end, samples = 0, 0
        for window in self.trace_windows:
            if window.start_cycle < end or window.end_cycle <= window.start_cycle or window.end_cycle > self.max_cycles:
                raise ValueError('Trace windows must be sorted, disjoint, nonempty and inside the cycle budget')
            samples += window.end_cycle - window.start_cycle
            end = window.end_cycle
        if samples > 16384:
            raise ValueError('Detailed trace windows are limited to 16384 total cycles')
        return self


def adas_templates():
    def preset(identity, name, **changes):
        value = AdasModuleConfig(engine='adas_module_rtl', **changes).model_dump()
        return dict(id=identity, name=name, seed=42, config=value)
    return [
        preset('adas-normal', 'ADAS · 4 × 1080p30 normal'),
        preset('adas-wb', 'ADAS · CH0 white balance at frame boundary', csr_updates=[dict(channel=0, at_cycle=10000000, gains=[192,128,128,80])]),
        preset('adas-stall-30us', 'ADAS · CH0 30 µs output stall', faults=[dict(channel=0, kind='output_stall', at_cycle=500000, duration_cycles=3000)]),
        preset('adas-overflow-100us', 'ADAS · CH0 100 µs overflow', faults=[dict(channel=0, kind='output_stall', at_cycle=500000, duration_cycles=10000)]),
        preset('adas-reset', 'ADAS · CH0 reset / recovery', faults=[dict(channel=0, kind='reset', at_cycle=500000, duration_cycles=1)]),
        preset('adas-input-gap', 'ADAS · CH0 input gap', faults=[dict(channel=0, kind='input_gap', at_cycle=500000, duration_cycles=3000)]),
    ]
