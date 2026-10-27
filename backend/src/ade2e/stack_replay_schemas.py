"""Pinned recorded stack inputs and open-loop command evidence."""

import copy
from typing import Annotated, Literal

from pydantic import Field, GetPydanticSchema, model_validator
from pydantic_core import core_schema

from .job_schemas import JobEvent, JobStatus
from .schemas import Name, StrictModel
from .sensor_schemas import SignedNs, UnsignedNs
from .stack_schemas import StackOutputLike, validate_stack_output


def _output_payload_schema(_source, handler):
    """Keep the existing dict API/bytes while exposing the true output union.

    Old v1 evidence may omit optional fields added later. Validate its meaning
    without inserting defaults into immutable, already-hashed dictionaries.
    """
    def validate_preserving_payload(value, validate):
        original = copy.deepcopy(value) if isinstance(value, dict) else None
        output = validate(validate_stack_output(value))
        return original if original is not None else output.model_dump(mode='json')
    return core_schema.no_info_wrap_validator_function(
        validate_preserving_payload, handler.generate_schema(StackOutputLike),
        serialization=core_schema.plain_serializer_function_ser_schema(
            lambda output: output))


StackOutputPayload = Annotated[dict, GetPydanticSchema(_output_payload_schema)]


class StackReplayCreate(StrictModel):
    label: Name
    stack_id: Name
    stack_revision: int = Field(ge=1, strict=True)
    recording_id: Name
    binding_revision: int = Field(ge=1, strict=True)
    lidar_channel_id: int = Field(ge=0, le=65535, strict=True)
    ego_channel_id: int = Field(ge=0, le=65535, strict=True)
    ego_decoder_id: Literal['axiom-ego-json'] = 'axiom-ego-json'
    ego_decoder_version: Literal['1'] = '1'
    ego_log_time_offset_ns: SignedNs = '0'
    ego_ordinals: list[Annotated[int, Field(ge=0, le=99999, strict=True)]] = Field(min_length=1, max_length=128)

    @model_validator(mode='after')
    def distinct_inputs(self):
        if len(set(self.ego_ordinals)) != len(self.ego_ordinals):
            raise ValueError('Ego ordinals must be unique and supplied in strictly increasing aligned time order')
        if self.lidar_channel_id == self.ego_channel_id:
            raise ValueError('LiDAR and ego roles require distinct recording channels')
        return self


class StackReplayMessage(StrictModel):
    recording_id: str
    recording_sha256: str
    channel_id: int
    ordinal: int
    log_time_ns: UnsignedNs
    publish_time_ns: UnsignedNs
    message_sha256: str
    message_size_bytes: int
    decoder_id: str
    decoder_version: str


class StackReplayInput(StrictModel):
    index: int
    at_ns: UnsignedNs
    ego: StackReplayMessage
    lidar: StackReplayMessage | None
    lidar_aligned_time_ns: str | None = None
    lidar_age_ns: UnsignedNs | None = None
    next_lidar_aligned_time_ns: str | None = None


class StackReplayPoints(StrictModel):
    sha256: str
    size_bytes: int
    point_count: int
    encoding: Literal['xyz_float32_le'] = 'xyz_float32_le'
    frame_id: str
    download_url: str


class StackReplayTick(StrictModel):
    index: int
    status: Literal['not_attempted', 'running', 'completed', 'failed']
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    input: StackReplayInput
    packet: dict | None = None
    output: StackOutputPayload | None = None
    points: StackReplayPoints | None = None
    output_hash: str | None = None
    control_application: Literal['not_applied_open_loop'] = 'not_applied_open_loop'


class StackReplayCounts(StrictModel):
    total: int
    running: int
    completed: int
    failed: int
    not_attempted: int


class StackReplaySummary(StrictModel):
    id: str
    submission_seq: int
    label: str
    stack_id: str
    stack_revision: int
    stack_name: str
    recording_id: str
    binding_revision: int
    mode: Literal['recorded_open_loop'] = 'recorded_open_loop'
    status: JobStatus
    created_at: str
    updated_at: str
    started_at: str | None = None
    dispatched_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    input_hash: str
    worker_id: str | None = None
    lease_expires_at: str | None = None
    cancel_requested_at: str | None = None
    cancellation_supported: bool
    outcome_uncertain: bool
    counts: StackReplayCounts


class StackReplayDetail(StackReplaySummary):
    request: StackReplayCreate
    stack_snapshot: dict
    recording_snapshot: dict
    binding_snapshot: dict
    inputs: list[StackReplayInput]
    ticks: list[StackReplayTick]
    events: list[JobEvent]
    events_total: int
    manifest_hash: str | None = None
    manifest_size_bytes: int | None = None
    download_url: str | None = None


class StackReplayPage(StrictModel):
    items: list[StackReplaySummary]
    next_before_seq: int | None = None


class StackReplayChannel(StrictModel):
    channel_id: int
    topic: str
    schema_name: str | None
    schema_sha256: str | None
    message_count: int
    start_time_ns: str | None
    end_time_ns: str | None
    role: Literal['lidar', 'ego']
    decoder_id: str
    decoder_version: str


class StackReplaySources(StrictModel):
    recording_id: str
    recording_sha256: str
    channels: list[StackReplayChannel]
    warnings: list[str]
