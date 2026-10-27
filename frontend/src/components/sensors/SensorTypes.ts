import type { RecordingSourceKind } from "../recordings/RecordingTypes";
import type { DeclaredTransform } from "./SensorPointCloudView";

export type CoordinateConvention =
  "right_handed_z_up" | "left_handed_z_up" | "unknown";
export type SensorKind = "camera" | "lidar";
export interface CameraIntrinsics {
  width: number;
  height: number;
  fx: number;
  fy: number;
  cx: number;
  cy: number;
  distortion_model:
    "none" | "plumb_bob" | "rational_polynomial" | "equidistant" | "unknown";
  distortion_coefficients: number[];
}
export interface RigSensor {
  key: string;
  name: string;
  kind: SensorKind;
  frame_id: string;
  transform: DeclaredTransform | null;
  intrinsics: CameraIntrinsics | null;
}
export interface RigCreate {
  name: string;
  description: string;
  reference_frame: string;
  coordinate_convention: CoordinateConvention;
  source_kind: RecordingSourceKind;
  source: string;
  note: string;
  sensors: RigSensor[];
}
export interface RigSnapshot extends RigCreate {
  schema_version: "1";
  id: string;
  revision: number;
  created_at: string;
  definition_hash: string;
  calibration_status: "source_declared";
}
export interface RigDetail extends RigSnapshot {
  manifest_hash: string;
  manifest_size_bytes: number;
  download_url: string;
}
export interface ChannelBinding {
  channel_id: number;
  sensor_key: string;
  decoder_id: string;
  decoder_version: string;
  max_age_ns: string | null;
  log_time_offset_ns: string;
}
export interface BindingCreate {
  expected_revision: number;
  rig_id: string;
  rig_revision: number;
  note: string;
  channels: ChannelBinding[];
}
export interface BindingSnapshot {
  schema_version: "1";
  recording_id: string;
  recording_sha256: string;
  revision: number;
  created_at: string;
  rig_id: string;
  rig_revision: number;
  rig_manifest_hash: string;
  rig_snapshot: RigSnapshot;
  note: string;
  channels: ChannelBinding[];
  definition_hash: string;
}
export interface BindingDetail extends BindingSnapshot {
  manifest_hash: string;
  manifest_size_bytes: number;
  download_url: string;
}

export interface SensorDecoderMetadata {
  id: string;
  version: string;
  name: string;
  message_encoding: string;
  schema_names: string[];
  kinds: SensorKind[];
  max_input_bytes: number;
  max_pixels: number;
  max_source_points: number;
  default_max_points: number;
}
export interface SensorPreviewBytes {
  data_base64: string;
  sha256: string;
  size_bytes: number;
}
export interface CameraPreview extends SensorPreviewBytes {
  mime_type: "image/png";
  encoding: "rgb8";
  width: number;
  height: number;
}
export interface PointCloudPreview extends SensorPreviewBytes {
  encoding: "xyz_float32_le";
  point_stride: 12;
  point_count: number;
  original_point_count: number;
  invalid_points: number;
  skipped_points: number;
  sampling_stride: number;
  bounds_min: [number, number, number] | null;
  bounds_max: [number, number, number] | null;
  intensity: SensorPreviewBytes | null;
  intensity_encoding: "float32_le" | null;
  intensity_invalid_points: number;
}
export interface SensorDecodeResult {
  kind: SensorKind;
  decoder_id: string;
  decoder_version: string;
  frame_id: string | null;
  capture_time_ns: string | null;
  original_encoding: string;
  source_sha256: string;
  source_size_bytes: number;
  transform_policy: "preserve_native_coordinates" | "message_pose_to_frame";
  source_pose: Record<string, Record<string, number>> | null;
  coordinate_convention:
    "camera_optical" | "ros_right_handed_z_up" | "source_declared";
  units: "pixels" | "m";
  camera: CameraPreview | null;
  point_cloud: PointCloudPreview | null;
  warnings: string[];
}
export interface SensorChannelSupport {
  channel_id: number;
  topic: string;
  message_encoding: string;
  schema_id: number;
  schema_name: string | null;
  schema_sha256: string | null;
  status: "supported" | "unsupported";
  kind: SensorKind | null;
  decoders: SensorDecoderMetadata[];
  reason: string | null;
}
export interface SensorChannels {
  recording_id: string;
  channels: SensorChannelSupport[];
  warnings: string[];
}
export interface SensorMessage {
  ordinal: number;
  log_time_ns: string;
  publish_time_ns: string;
  sequence: number;
  size_bytes: number;
  sha256: string;
  preview_hex: string | null;
}
export interface SensorFrame {
  recording_id: string;
  channel_id: number;
  status:
    | "decoded"
    | "unsupported"
    | "invalid_payload"
    | "limit_exceeded"
    | "decoder_unavailable"
    | "decoder_error"
    | "integrity_error";
  message: SensorMessage;
  decoded: SensorDecodeResult | null;
  reason: string | null;
  warnings: string[];
}
export interface SensorBundleEntry {
  channel_id: number;
  sensor_key: string | null;
  selection_status: "selected" | "missing";
  time_relation: "at_cursor" | "past" | "missing";
  freshness: "fresh" | "stale" | "unknown" | "missing";
  max_age_ns: string | null;
  age_policy_source: "binding" | "query" | "unknown";
  log_time_offset_ns: string;
  aligned_time_ns: string | null;
  age_ns: string | null;
  next_log_time_ns: string | null;
  next_aligned_time_ns: string | null;
  message: SensorMessage | null;
  frame: SensorFrame | null;
  declared_sensor: RigSensor | null;
  transform_status:
    | "available"
    | "unbound"
    | "unknown_convention"
    | "unknown_transform"
    | "frame_mismatch"
    | "kind_mismatch"
    | "out_of_range"
    | "not_decoded";
  transform: DeclaredTransform | null;
  reason: string | null;
}
export interface SensorBundle {
  recording_id: string;
  recording_sha256: string;
  at_ns: string;
  binding_revision: number | null;
  binding_manifest_hash: string | null;
  rig_snapshot: RigSnapshot | null;
  alignment_basis: "log_time_plus_declared_offset";
  entries: SensorBundleEntry[];
  encoded_data_size_bytes: number;
  warnings: string[];
}
