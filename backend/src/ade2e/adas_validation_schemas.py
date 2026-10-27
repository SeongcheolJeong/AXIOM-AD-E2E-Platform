"""Versioned, deterministic pre-board verification plans; no board binding is inferred."""
import math
import random
from typing import Annotated, Literal
from pydantic import Field, model_validator
from .ecu_lab_schemas import StrictModel
from .adas_module_schemas import AdasModuleConfig

Seed = Annotated[int, Field(ge=0, le=2147483647, strict=True)]


class AdasPreboardConfig(StrictModel):
    engine: Literal['adas_preboard_checks']
    core_config: AdasModuleConfig = Field(default_factory=lambda: AdasModuleConfig(engine='adas_module_rtl'))
    boundary_words: int = Field(default=256, ge=16, le=4096, strict=True)
    source_period_ns: int = Field(default=14, ge=4, le=40, strict=True)
    sink_period_ns: int = Field(default=10, ge=4, le=40, strict=True)
    reset_hold_cycles: int = Field(default=4, ge=2, le=16, strict=True)
    lock_delay_cycles: int = Field(default=8, ge=2, le=32, strict=True)

    @model_validator(mode='after')
    def clocks(self):
        if self.source_period_ns % 2 or self.sink_period_ns % 2:
            raise ValueError('Reference digital clock periods must be even integer ns')
        if any(c.source_kind != 'synthetic' for c in self.core_config.channels):
            raise ValueError('Pre-board static/core checks do not bind external RAW assets')
        return self


class AdasCampaignConfig(StrictModel):
    engine: Literal['adas_validation_campaign']
    campaign_version: Literal['adas-preboard-campaign/1'] = 'adas-preboard-campaign/1'
    mode: Literal['full1080p', 'extended_smoke'] = 'full1080p'
    long_frames: int = Field(default=300, ge=30, le=1800, strict=True)
    seeds: list[Seed] = Field(default_factory=lambda: [42, 7, 2026], min_length=1, max_length=8)
    reset_count: int = Field(default=8, ge=2, le=32, strict=True)
    simultaneous_channels: list[Annotated[int, Field(ge=0, le=3, strict=True)]] = Field(default_factory=lambda: [0, 1, 2], min_length=2, max_length=4)
    stall_cycles: list[Annotated[int, Field(ge=1, le=20000, strict=True)]] = Field(default_factory=lambda: [5800, 6000, 6200, 6400, 6600], min_length=2, max_length=16)
    include_preboard: bool = True
    preview_frames: list[Annotated[int, Field(ge=0, le=1799, strict=True)]] = Field(default_factory=lambda: [0, 3, 29], max_length=8)

    @model_validator(mode='after')
    def unique(self):
        for name in ('seeds', 'simultaneous_channels', 'stall_cycles', 'preview_frames'):
            value = getattr(self, name)
            if len(set(value)) != len(value):
                raise ValueError(name + ' must not contain duplicates')
        if self.stall_cycles != sorted(self.stall_cycles):
            raise ValueError('Stall sweep must be sorted in increasing duration')
        if any(f >= self.long_frames for f in self.preview_frames):
            raise ValueError('Preview selection exceeds long run frame count')
        return self


def campaign_plan(config):
    """Freeze actual stimulus schedules before dispatch, not after observing DUT results."""
    c = AdasCampaignConfig.model_validate(config).model_dump()
    smoke = c['mode'] == 'extended_smoke'
    base = AdasModuleConfig(engine='adas_module_rtl').model_dump()
    # Exercise changing RAW data across seeds/frames as well as color bars.
    for channel,pattern in zip(base['channels'],('gradient','color_bars','impulse','gradient')):
        channel['source_pattern']=pattern
    if smoke:
        # Functional accelerator, explicitly excluded from full-HD duration gates.
        base.update(width=128, height=96, raster_width=144, raster_height=100, pixel_rate_hz=74250000)
    period = base['raster_width'] * base['raster_height'] * 100000000 / base['pixel_rate_hz']
    active_offset = 500 if smoke else 500000
    items = []

    def add(identity, name, seed, frames, category, faults=None, init='zero', updates=None, duration=None):
        # Retain bounded waveform evidence around injected transitions as well as reset.
        transitions=sorted({p for f in faults or [] for p in (f['at_cycle'],f['at_cycle']+f['duration_cycles'])})
        if len(transitions)>15:
            transitions=transitions[:14]+transitions[-1:]
        windows=[]
        for start,end in sorted([(0,1024)]+[(max(0,p-32),p+160) for p in transitions]):
            if windows and start<=windows[-1]['end_cycle']:
                windows[-1]['end_cycle']=max(end,windows[-1]['end_cycle'])
            else:
                windows.append(dict(start_cycle=start,end_cycle=end))
        cfg = dict(base, frames=frames, max_cycles=math.ceil(frames * period) + 100000,
                   faults=faults or [], csr_updates=updates or [], init_mode=init, init_seed=max(1,seed),
                   preview_frames=sorted({f for f in c['preview_frames'] + [frames - 1] if f < frames}),
                   trace_windows=windows)
        items.append(dict(id=identity, name=name, seed=seed, category=category,
                          stall_cycles=duration, config=AdasModuleConfig.model_validate(cfg).model_dump()))

    add('long-continuous', 'Continuous input', c['seeds'][0], c['long_frames'], 'continuous')
    for seed in c['seeds']:
        add('init-zero-' + str(seed), 'Zero-initialized baseline', seed, 3, 'initialization')
        add('init-random-' + str(seed), 'Randomized two-state initialization', seed, 3, 'initialization', init='randomized')
        rng = random.Random(seed)
        faults = []
        for n in range(c['reset_count']):
            at = 256 + round((n + 1) * period) + active_offset + rng.randrange(0, 64)
            faults.append(dict(channel=n % 4, kind='reset', at_cycle=at, duration_cycles=1))
        add('repeated-reset-' + str(seed), 'Repeated resets / isolated recovery', seed,
            c['reset_count'] + 3, 'repeated_reset', faults, init='randomized')
        at = 256 + round(period) + active_offset
        faults = []
        for index, ch in enumerate(c['simultaneous_channels']):
            kind = ['output_stall', 'reset', 'input_gap', 'output_stall'][index]
            duration = 10000 if kind == 'output_stall' else (32 if smoke else 3000) if kind == 'input_gap' else 1
            faults.append(dict(channel=ch, kind=kind, at_cycle=at, duration_cycles=duration))
        add('simultaneous-' + str(seed), 'Simultaneous channel faults', seed, 4, 'simultaneous', faults)
    for duration in c['stall_cycles']:
        add('fifo-' + str(duration), 'FIFO duration sweep', c['seeds'][0], 3, 'fifo_sweep',
            [dict(channel=0, kind='output_stall', at_cycle=active_offset, duration_cycles=duration)], duration=duration)
    return items


def validation_templates():
    return [dict(id='adas-preboard-campaign', name='ADAS · Pre-board validation campaign', seed=42,
                 config=AdasCampaignConfig(engine='adas_validation_campaign').model_dump()),
            dict(id='adas-preboard-checks', name='ADAS · Initialization / clock boundary checks', seed=42,
                 config=AdasPreboardConfig(engine='adas_preboard_checks').model_dump())]
