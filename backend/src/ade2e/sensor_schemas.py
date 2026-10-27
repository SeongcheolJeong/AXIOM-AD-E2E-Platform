"""Source-declared sensor rigs, pinned recording bindings and native observations."""

import math
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, StringConstraints, model_validator

from .recording_schemas import SourceKind
from .sensor_decoder_schemas import SensorDecodeResult, SensorDecoderMetadata
from .base_schemas import Name, StrictModel


def _uint64(value: str) -> str:
    if int(value) > 2**64 - 1:
        raise ValueError("Timestamp or age exceeds uint64")
    return value


def _offset(value: str) -> str:
    if abs(int(value)) > 2**64 - 1:
        raise ValueError("Offset magnitude exceeds uint64")
    return value


UnsignedNs = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]{0,19})$"), AfterValidator(_uint64)]
SignedNs = Annotated[str, StringConstraints(pattern=r"^(0|-?[1-9][0-9]{0,19})$"), AfterValidator(_offset)]
SensorKey = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Convention = Literal["right_handed_z_up", "left_handed_z_up", "unknown"]
TranslationComponent = Annotated[float, Field(ge=-1_000_000, le=1_000_000)]


class SensorTransform(StrictModel):
    translation_m: tuple[TranslationComponent, TranslationComponent, TranslationComponent]
    rotation_xyzw: tuple[float, float, float, float]

    @model_validator(mode="after")
    def unit_quaternion(self):
        if not math.isclose(sum(value * value for value in self.rotation_xyzw), 1.0, abs_tol=1e-6):
            raise ValueError("rotation_xyzw must be a unit quaternion; values are not silently normalized")
        return self


class CameraIntrinsics(StrictModel):
    width: int = Field(ge=1, le=16384)
    height: int = Field(ge=1, le=16384)
    fx: float = Field(gt=0)
    fy: float = Field(gt=0)
    cx: float
    cy: float
    distortion_model: Literal["none", "plumb_bob", "rational_polynomial", "equidistant", "unknown"]
    distortion_coefficients: list[float] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def coefficient_count(self):
        expected = {"none": 0, "plumb_bob": 5, "rational_polynomial": 8, "equidistant": 4}
        if self.distortion_model in expected and len(self.distortion_coefficients) != expected[self.distortion_model]:
            raise ValueError(f"{self.distortion_model} requires {expected[self.distortion_model]} coefficients")
        return self


class RigSensor(StrictModel):
    key: SensorKey
    name: Name
    kind: Literal["camera", "lidar"]
    frame_id: Name
    transform: SensorTransform | None = None
    intrinsics: CameraIntrinsics | None = None

    @model_validator(mode="after")
    def camera_only_intrinsics(self):
        if self.kind != "camera" and self.intrinsics is not None:
            raise ValueError("Camera intrinsics cannot be assigned to a LiDAR sensor")
        return self


class RigCreate(StrictModel):
    name: Name
    description: str = Field(default="", max_length=4000)
    reference_frame: Name
    coordinate_convention: Convention = "unknown"
    source_kind: SourceKind = "unknown"
    source: str = Field(default="", max_length=1000)
    note: Note
    sensors: list[RigSensor] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique_sensors(self):
        if len({sensor.key for sensor in self.sensors}) != len(self.sensors):
            raise ValueError("Sensor keys must be unique within a rig revision")
        if len({sensor.frame_id for sensor in self.sensors}) != len(self.sensors):
            raise ValueError("Sensor frame IDs must be unique within a rig revision")
        return self


class RigRevisionCreate(RigCreate):
    expected_revision: int = Field(ge=1)


class RigSnapshot(RigCreate):
    schema_version: Literal["1"] = "1"
    id: str
    revision: int
    created_at: str
    definition_hash: str
    calibration_status: Literal["source_declared"] = "source_declared"


class RigDetail(RigSnapshot):
    manifest_hash: str
    manifest_size_bytes: int
    download_url: str


class ChannelBinding(StrictModel):
    channel_id: int = Field(ge=0, le=65535)
    sensor_key: SensorKey
    decoder_id: Name
    decoder_version: Name
    max_age_ns: UnsignedNs | None = None
    log_time_offset_ns: SignedNs = "0"


class BindingCreate(StrictModel):
    expected_revision: int = Field(ge=0)
    rig_id: Name
    rig_revision: int = Field(ge=1)
    note: Note
    channels: list[ChannelBinding] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique_channels_and_sensors(self):
        if len({channel.channel_id for channel in self.channels}) != len(self.channels):
            raise ValueError("Binding channel IDs must be unique")
        if len({channel.sensor_key for channel in self.channels}) != len(self.channels):
            raise ValueError("Each sensor may bind only one channel in a recording revision")
        return self


class BindingSnapshot(StrictModel):
    schema_version: Literal["1"] = "1"
    recording_id: str
    recording_sha256: str
    revision: int
    created_at: str
    rig_id: str
    rig_revision: int
    rig_manifest_hash: str
    rig_snapshot: RigSnapshot
    note: str
    channels: list[ChannelBinding]
    definition_hash: str


class BindingDetail(BindingSnapshot):
    manifest_hash: str
    manifest_size_bytes: int
    download_url: str


class SensorChannelSupport(StrictModel):
    channel_id: int
    topic: str
    message_encoding: str
    schema_id: int
    schema_name: str | None
    schema_sha256: str | None
    status: Literal["supported", "unsupported"]
    kind: Literal["camera", "lidar"] | None
    decoders: list[SensorDecoderMetadata]
    reason: str | None = None


class SensorChannels(StrictModel):
    recording_id: str
    channels: list[SensorChannelSupport]
    warnings: list[str]


class SensorMessage(StrictModel):
    ordinal: int
    log_time_ns: UnsignedNs
    publish_time_ns: UnsignedNs
    sequence: int
    size_bytes: int
    sha256: str
    preview_hex: str | None = None


class SensorFrame(StrictModel):
    recording_id: str
    channel_id: int
    status: Literal["decoded", "unsupported", "invalid_payload", "limit_exceeded", "decoder_unavailable", "decoder_error", "integrity_error"]
    message: SensorMessage
    decoded: SensorDecodeResult | None = None
    reason: str | None = None
    warnings: list[str] = Field(default_factory=list)


class SensorBundleEntry(StrictModel):
    channel_id: int
    sensor_key: str | None = None
    selection_status: Literal["selected", "missing"]
    time_relation: Literal["at_cursor", "past", "missing"]
    freshness: Literal["fresh", "stale", "unknown", "missing"]
    max_age_ns: UnsignedNs | None
    age_policy_source: Literal["binding", "query", "unknown"]
    log_time_offset_ns: SignedNs
    aligned_time_ns: str | None = None
    age_ns: str | None = None
    next_log_time_ns: UnsignedNs | None = None
    next_aligned_time_ns: str | None = None
    message: SensorMessage | None = None
    frame: SensorFrame | None = None
    declared_sensor: RigSensor | None = None
    transform_status: Literal["available", "unbound", "unknown_convention", "unknown_transform", "frame_mismatch", "kind_mismatch", "not_decoded", "out_of_range"]
    transform: SensorTransform | None = None
    reason: str | None = None


class SensorBundle(StrictModel):
    recording_id: str
    recording_sha256: str
    at_ns: UnsignedNs
    binding_revision: int | None
    binding_manifest_hash: str | None
    rig_snapshot: RigSnapshot | None
    alignment_basis: Literal["log_time_plus_declared_offset"] = "log_time_plus_declared_offset"
    entries: list[SensorBundleEntry]
    encoded_data_size_bytes: int
    warnings: list[str]
