"""Versioned native sensor previews; successful decode does not certify calibration."""

from typing import Literal

from pydantic import Field

from .base_schemas import StrictModel


class SensorDecoderMetadata(StrictModel):
    id: str
    version: str
    name: str
    message_encoding: str
    schema_names: list[str]
    kinds: list[Literal["camera", "lidar"]]
    max_input_bytes: int = 16 * 1024 * 1024
    max_pixels: int = 2_000_000
    max_source_points: int = 1_000_000
    default_max_points: int = 20_000


class SensorPreviewBytes(StrictModel):
    data_base64: str
    sha256: str
    size_bytes: int


class CameraPreview(SensorPreviewBytes):
    mime_type: Literal["image/png"] = "image/png"
    encoding: Literal["rgb8"] = "rgb8"
    width: int
    height: int


class PointCloudPreview(SensorPreviewBytes):
    encoding: Literal["xyz_float32_le"] = "xyz_float32_le"
    point_stride: Literal[12] = 12
    point_count: int
    original_point_count: int
    invalid_points: int
    skipped_points: int
    sampling_stride: int
    bounds_min: tuple[float, float, float] | None
    bounds_max: tuple[float, float, float] | None
    intensity: SensorPreviewBytes | None = None
    intensity_encoding: Literal["float32_le"] | None = None
    intensity_invalid_points: int = 0


class SensorDecodeResult(StrictModel):
    kind: Literal["camera", "lidar"]
    decoder_id: str
    decoder_version: str
    frame_id: str | None
    capture_time_ns: str | None
    original_encoding: str
    source_sha256: str
    source_size_bytes: int
    transform_policy: Literal["preserve_native_coordinates", "message_pose_to_frame"] = "preserve_native_coordinates"
    source_pose: dict[str, dict[str, float]] | None = None
    coordinate_convention: Literal["camera_optical", "ros_right_handed_z_up", "source_declared"]
    units: Literal["pixels", "m"]
    camera: CameraPreview | None = None
    point_cloud: PointCloudPreview | None = None
    warnings: list[str] = Field(default_factory=list)
