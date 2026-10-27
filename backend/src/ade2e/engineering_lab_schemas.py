"""Bounded source contracts; engines and external runtimes remain optional adapters."""
import re
from typing import Annotated, Literal, Union
from pydantic import Field, model_validator
from .ecu_lab_schemas import StrictModel, RevisionRef, RevisionSave
from .rtl_lab_schemas import StallWindow, RtlConfig
from .adas_module_schemas import AdasModuleConfig
from .adas_validation_schemas import AdasCampaignConfig, AdasPreboardConfig

ADAPTER = 'native-engineering-lab'
FIDELITY = 'native_engineering_reference'
MAX_SOURCE_BYTES = 256 * 1024

def ascii_text(value):
    if not value.isascii() or any(ord(c) < 32 and c not in '\n\r\t' for c in value) or '\x7f' in value:
        raise ValueError('Source text must be ASCII without control characters')
    return value

class RtlSource(StrictModel):
    name: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_-]{0,79}\.(?:sv|v)$')
    content: str = Field(min_length=1, max_length=MAX_SOURCE_BYTES)
    @model_validator(mode='after')
    def source_subset(self):
        text = ascii_text(self.content)
        if re.search(r'verilator|\bDPI\b|\b(?:import|export)\s*"|#\s*(?:include|pragma|define|line)', text, re.I):
            raise ValueError('Foreign code, DPI, compiler directives and Verilator metacomments are unsupported')
        if set(re.findall(r'\$([A-Za-z_][A-Za-z0-9_$]*)', text)) - {'clog2', 'signed', 'unsigned', 'bits'}:
            raise ValueError('Only $clog2/$signed/$unsigned/$bits system functions are supported')
        # Disallow macros/token-pasting so a forbidden system task cannot be assembled later.
        if re.search(r'\b(?:timeunit|timeprecision)\b', text):
            raise ValueError('Timeunit/timeprecision declarations are unsupported; the Stream32 clock uses fixed 1ns units')
        reduced = re.sub(r'^\s*`(?:timescale\s+1\s*ns\s*/\s*1\s*ns|default_nettype\s+(?:none|wire))\s*$', '', text, flags=re.M)
        if '`' in reduced:
            raise ValueError('Only `timescale 1ns/1ns and default_nettype directives are supported; includes/macros/other scales are forbidden')
        return self

class RtlEngineeringConfig(StrictModel):
    engine: Literal['rtl']
    top: Literal['user_top'] = 'user_top'
    sources: list[RtlSource] = Field(min_length=1, max_length=8)
    words: int = Field(default=256, ge=1, le=16384)
    max_cycles: int = Field(default=2048, ge=16, le=100000)
    stalls: list[StallWindow] = Field(default_factory=list, max_length=128)
    resets: list[int] = Field(default_factory=lambda: [0, 1], min_length=1, max_length=128)
    expectation: Literal['identity', 'xor', 'observe'] = 'identity'
    xor_mask: int = Field(default=0, ge=0, le=4294967295)
    @model_validator(mode='after')
    def bounded(self):
        if len({s.name.lower() for s in self.sources}) != len(self.sources):
            raise ValueError('RTL source basenames must be unique, ignoring case')
        if sum(len(s.content.encode('ascii')) for s in self.sources) > MAX_SOURCE_BYTES:
            raise ValueError('RTL source bundle exceeds 256 KiB')
        RtlConfig(words=self.words, max_cycles=self.max_cycles, stalls=[s.model_dump() for s in self.stalls], resets=self.resets)
        return self

class SpiceEngineeringConfig(StrictModel):
    engine: Literal['spice']
    netlist: str = Field(min_length=1, max_length=MAX_SOURCE_BYTES)
    step_s: float = Field(default=1e-6, ge=1e-12, le=1., allow_inf_nan=False)
    stop_s: float = Field(default=.005, gt=0., le=10., allow_inf_nan=False)
    probes: list[Annotated[str, Field(pattern=r'^(?:v\([A-Za-z0-9_]+\)|i\([Vv][A-Za-z0-9_]*\))$')]] = Field(default_factory=lambda: ['v(out)'], min_length=1, max_length=8)
    @model_validator(mode='after')
    def bounded_netlist(self):
        text = ascii_text(self.netlist)
        if self.stop_s < self.step_s or self.stop_s / self.step_s > 250000:
            raise ValueError('SPICE transient requires step <= stop and at most 250000 requested steps')
        if len(set(self.probes)) != len(self.probes):
            raise ValueError('SPICE probes must be unique')
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines or not lines[0].startswith('*'):
            raise ValueError('First netlist line must be an explicit * title/comment')
        active = [line for line in lines[1:] if not line.startswith('*')]
        if not active:
            raise ValueError('SPICE requires at least one circuit element')
        count = 0
        for i, line in enumerate(active):
            if any(c in line for c in ['"', "'", ';', '`', '\\', '/', ':', '{', '}']) or re.search(r'\bfile\s*=', line, re.I):
                raise ValueError('SPICE paths, expressions, quoted strings and external files are unsupported')
            if line.lower() == '.end':
                if i != len(active) - 1: raise ValueError('.end is allowed only as the final statement')
            elif line.startswith('.'):
                if not re.fullmatch(r'\.model\s+[A-Za-z_][A-Za-z0-9_]*\s+[dD]\s*\([^\n]*\)', line, re.I):
                    raise ValueError('Only inline .model diode definitions and final .end are supported')
            elif not re.match(r'^[RCLVIDrclvid][A-Za-z0-9_]+\s+\S+\s+\S+\s+.+$', line):
                raise ValueError('Only self-contained R/C/L/V/I/D elements are supported')
            else:
                count += 1
        if not 1 <= count <= 128:
            raise ValueError('SPICE supports 1..128 circuit elements')
        return self

class QualcommEngineeringConfig(StrictModel):
    engine: Literal['qualcomm']
    target: Literal['SA8650', 'SM8750', 'SM8850'] = 'SA8650'

class IspStallWindow(StrictModel):
    start_cycle: int = Field(ge=0, le=999999)
    end_cycle: int = Field(ge=1, le=1000000)


class IspRtlConfig(StrictModel):
    """Pinned bundled ISP only; arbitrary user RTL keeps its separate restrictions."""
    engine: Literal['isp_rtl']
    pipeline_id: Literal['mini-isp-blc-wb-mhc/1'] = 'mini-isp-blc-wb-mhc/1'
    width: int = Field(default=128, ge=8, le=320)
    height: int = Field(default=96, ge=8, le=240)
    bayer_pattern: Literal['RGGB', 'BGGR', 'GBRG', 'GRBG'] = 'RGGB'
    black_level: int = Field(default=64, ge=0, le=4095)
    gains: list[Annotated[int, Field(ge=0, le=65535, strict=True)]] = Field(default_factory=lambda:[128]*4, min_length=4, max_length=4)
    frames: int = Field(default=1, ge=1, le=2)
    source_kind: Literal['synthetic', 'raw_asset'] = 'synthetic'
    source_pattern: Literal['gradient', 'color_bars', 'impulse'] = 'color_bars'
    raw_asset_ref: RevisionRef | None = None
    raw_frame_index: int = Field(default=0, ge=0, le=29)
    stalls: list[IspStallWindow] = Field(default_factory=list, max_length=32)
    reset_at_cycle: int | None = Field(default=None, ge=2, le=999999)
    max_cycles: int = Field(default=200000, ge=16, le=1000000)

    @model_validator(mode='after')
    def bounded(self):
        if self.width % 2 or self.height % 2:
            raise ValueError('ISP Bayer dimensions must be even')
        if (self.source_kind == 'raw_asset') != (self.raw_asset_ref is not None):
            raise ValueError('RAW asset source requires an immutable asset reference; synthetic source forbids one')
        if self.source_kind == 'synthetic' and self.raw_frame_index:
            raise ValueError('Synthetic source has no RAW asset frame index')
        if self.reset_at_cycle is not None and self.reset_at_cycle >= self.max_cycles:
            raise ValueError('Reset cycle must lie inside the cycle budget')
        end = 0
        for stall in self.stalls:
            if stall.start_cycle < end or stall.end_cycle <= stall.start_cycle or stall.end_cycle > self.max_cycles:
                raise ValueError('Stalls must be sorted, disjoint nonempty intervals inside the cycle budget')
            end = stall.end_cycle
        return self


class EngineeringBuildAction(StrictModel):
    project_id: str = Field(default='default', min_length=1, max_length=64)
    revision: int = Field(ge=1)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    build_profile_id: str = Field(default='ecp5-85k-cabga381-6-ooc/1', min_length=1, max_length=128)


EngineeringConfig = Annotated[Union[RtlEngineeringConfig, SpiceEngineeringConfig, QualcommEngineeringConfig, IspRtlConfig, AdasModuleConfig, AdasCampaignConfig, AdasPreboardConfig], Field(discriminator='engine')]

class EngineeringTestSave(RevisionSave):
    config: EngineeringConfig
    seed: int = Field(default=42, ge=0, le=2147483647)
