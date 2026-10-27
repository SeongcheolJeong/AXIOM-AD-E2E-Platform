"""Imported-container metadata is provenance, not sensor validation."""

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints

from .base_schemas import Name, StrictModel

SourceKind = Literal["unknown", "synthetic", "simulation", "measured"]
NanoTimestamp = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]{0,19})$")]


class RecordingImportMetadata(StrictModel):
    name: Name
    source_kind: SourceKind = "unknown"
    source: str = Field(default="", max_length=1000)


class RecordingIndex(StrictModel):
    version: str
    sha256: str
    size_bytes: int
    source_sha256: str


class RecordingSummary(StrictModel):
    id: str
    name: str
    source_kind: SourceKind
    source: str
    sha256: str
    size_bytes: int
    created_at: str
    format: Literal["mcap"] = "mcap"
    profile: str
    message_count: int
    channel_count: int
    start_time_ns: NanoTimestamp | None
    end_time_ns: NanoTimestamp | None
    duration_s: float
    download_url: str
    warnings: list[str]
    index: RecordingIndex


class RecordingChannel(StrictModel):
    id: int
    topic: str
    message_encoding: str
    schema_id: int
    message_count: int
    start_time_ns: NanoTimestamp | None
    end_time_ns: NanoTimestamp | None
    metadata: dict[str, str]
    decoder: Literal["json", "opaque"]


class RecordingSchema(StrictModel):
    id: int
    name: str
    encoding: str
    sha256: str
    size_bytes: int
    definition: str | None
    definition_truncated: bool


class RecordingMetadata(StrictModel):
    name: str
    metadata: dict[str, str]


class RecordingDetail(RecordingSummary):
    channels: list[RecordingChannel]
    schemas: list[RecordingSchema]
    metadata: list[RecordingMetadata]


class RecordingSample(StrictModel):
    ordinal: int
    log_time_ns: NanoTimestamp
    publish_time_ns: NanoTimestamp
    sequence: int
    size_bytes: int
    sha256: str
    decoded: Any = None
    decoded_text: str | None = None
    decode_status: Literal["decoded", "opaque", "invalid_json", "too_large"]
    preview_hex: str
    truncated: bool


class RecordingSamples(StrictModel):
    recording_id: str
    channel_id: int
    requested_time_ns: NanoTimestamp | None
    samples: list[RecordingSample]
    warnings: list[str]
    next_after_ordinal: int | None = None
