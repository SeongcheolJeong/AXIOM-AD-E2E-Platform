"""Engine-neutral contracts for the bounded, single-clock reference FIFO lab."""
from pydantic import Field, model_validator
from .ecu_lab_schemas import StrictModel, RevisionRef, RevisionSave

ADAPTER = 'verilator-fifo-ab'
FIDELITY = 'rtl_reference_fifo'
CLOCK_HZ = 100_000_000
DATA_WIDTH = 32


class StallWindow(StrictModel):
    start_cycle: int = Field(ge=0, le=99_999)
    end_cycle: int = Field(ge=1, le=100_000)

    @model_validator(mode='after')
    def nonempty(self):
        if self.end_cycle <= self.start_cycle:
            raise ValueError('Stall end_cycle must be greater than start_cycle (end exclusive)')
        return self


class RtlConfig(StrictModel):
    depth: int = Field(default=64, ge=2, le=1024)
    words: int = Field(default=4096, ge=1, le=16384)
    max_cycles: int = Field(default=20000, ge=16, le=100000)
    stalls: list[StallWindow] = Field(default_factory=list, max_length=128)
    resets: list[int] = Field(default_factory=lambda: [0, 1], min_length=1, max_length=128)

    @model_validator(mode='after')
    def bounded_schedule(self):
        if self.resets != sorted(set(self.resets)) or self.resets[0] != 0:
            raise ValueError('Reset cycles must be sorted, unique and include cycle 0 for explicit initialization')
        if any(c < 0 or c >= self.max_cycles for c in self.resets):
            raise ValueError('Reset cycles must lie inside the execution cycle budget')
        end = 0
        for stall in self.stalls:
            if stall.start_cycle < end:
                raise ValueError('Stall windows must be sorted and must not overlap')
            if stall.end_cycle > self.max_cycles:
                raise ValueError('Stall windows must lie inside the execution cycle budget')
            end = stall.end_cycle
        return self


class RtlTestSave(RevisionSave):
    config: RtlConfig = Field(default_factory=RtlConfig)
    seed: int = Field(default=42, ge=0, le=2147483647)
