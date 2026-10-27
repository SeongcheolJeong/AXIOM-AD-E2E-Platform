import type { JobStatus } from "../jobs/JobTypes";

export type ModelProfile = string;
export interface ModelArtifact {
  id: string;
  name: string;
  sha256: string;
  size_bytes: number;
  created_at: string;
  download_url: string;
}
export interface ModelCreate {
  name: string;
  description: string;
  artifact_id: string;
  profile: ModelProfile;
  input_width: number;
  input_height: number;
  labels: string[];
  source: string;
  source_revision: string;
  weights_license: string;
  weights_license_url: string;
  code_license: string;
  training_data_note: string;
  note: string;
}
export interface ModelDetail extends ModelCreate {
  id: string;
  revision: number;
  created_at: string;
  artifact: ModelArtifact;
  label_map_hash: string;
  manifest_hash: string;
  manifest_size_bytes: number;
  download_url: string;
}
export interface RuntimeMetadata {
  id: string;
  name: string;
  version: string;
  status: string;
  profiles: ModelProfile[];
  limits: Record<string, unknown>;
  limitations: string[];
}
export interface InferenceSource {
  recording_id: string;
  channel_id: number;
  ordinal: number;
  decoder_id: string;
  decoder_version: string;
}
export interface InferenceRequest {
  label: string;
  model_id: string;
  model_revision: number;
  runtime_id: string;
  recording_id: string;
  channel_id: number;
  ordinals: number[];
  decoder_id: string;
  decoder_version: string;
  seed: number;
  score_threshold: number;
  iou_threshold: number;
  max_detections: number;
}
export interface InferenceInput {
  selection_index: number;
  recording_id: string;
  recording_sha256: string;
  channel_id: number;
  ordinal: number;
  log_time_ns: string;
  publish_time_ns: string;
  message_sha256: string;
  message_size_bytes: number;
  decoder_id: string;
  decoder_version: string;
}
export interface InferenceImage {
  sha256: string;
  size_bytes: number;
  width: number;
  height: number;
  download_url: string;
  capture_time_ns: string | null;
  frame_id: string | null;
}
export interface Detection {
  id: string;
  class_id: number;
  label: string;
  score: number;
  bbox_xyxy: [number, number, number, number];
}
export interface InferenceFrame {
  selection_index: number;
  status: "not_attempted" | "running" | "completed" | "failed";
  error: string | null;
  input: InferenceInput;
  image: InferenceImage | null;
  detections: Detection[];
  runtime: Record<string, unknown> | null;
  output_hash: string | null;
  created_at: string | null;
  started_at: string | null;
}
export interface InferenceCounts {
  total: number;
  running: number;
  completed: number;
  failed: number;
  not_attempted: number;
}
export interface InferenceJob {
  id: string;
  submission_seq: number;
  label: string;
  model_id: string;
  model_revision: number;
  model_name: string;
  runtime_id: string;
  runtime_version: string;
  status: JobStatus;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  dispatched_at: string | null;
  finished_at: string | null;
  error: string | null;
  input_hash: string;
  worker_id: string | null;
  lease_expires_at: string | null;
  cancel_requested_at: string | null;
  cancellation_supported: boolean;
  outcome_uncertain: boolean;
  counts: InferenceCounts;
}
export interface InferenceDetail extends InferenceJob {
  request: InferenceRequest;
  model_snapshot: ModelDetail;
  recording_snapshot: Record<string, unknown>;
  inputs: InferenceInput[];
  frames: InferenceFrame[];
  events: { at: string; type: string; message: string }[];
  events_total: number;
  manifest_hash: string | null;
  manifest_size_bytes: number | null;
  download_url: string | null;
}
export interface InferenceJobs {
  items: InferenceJob[];
  next_before_seq: number | null;
}
export interface PredictionComparisonRow {
  key: string;
  status:
    | "same_input"
    | "input_changed"
    | "left_only"
    | "right_only"
    | "not_evaluated";
  left: InferenceFrame | null;
  right: InferenceFrame | null;
  detection_count_delta: number | null;
  predictions_identical: boolean | null;
}
export interface PredictionComparison {
  id: string;
  name: string;
  note: string;
  created_at: string;
  left_job_id: string;
  right_job_id: string;
  left_manifest_hash: string;
  right_manifest_hash: string;
  policy_version: string;
  configuration_differences: { field: string; left: unknown; right: unknown }[];
  rows: PredictionComparisonRow[];
  counts: Record<string, number>;
  manifest_hash: string;
  manifest_size_bytes: number;
  download_url: string;
}
export type PredictionComparisonSummary = Omit<
  PredictionComparison,
  "configuration_differences" | "rows"
>;
export const terminalInference = (status: JobStatus) =>
  ["completed", "failed", "cancelled", "interrupted"].includes(status);
