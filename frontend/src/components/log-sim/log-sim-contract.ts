import type { ObjectExecution } from "../object-scene/object-scene";

export type LogEvent = { id: string; label: string; kind: string; time_s: number; end_time_s: number };
export type DetectedLogEvent = LogEvent & {
  event_hash: string;
  description: string;
  rule_id: string;
  rule_version: string;
  source_hash: string;
  analysis_hash: string;
  severity: "review";
  evidence: {
    start_sample_index: number; end_sample_index: number;
    start_timestamp_ns: string; end_timestamp_ns: string; sample_count: number;
    minimum_speed_mps?: number; minimum_acceleration_mps2?: number;
    object_id?: string; minimum_longitudinal_center_distance_m?: number;
    absolute_lateral_center_distance_m?: number;
  };
};
export type LogEventSearch = { schema_version: string; items: DetectedLogEvent[]; total: number; rule_version: string; analysis_hash: string; scope: string };
export type LogMetrics = {
  minimum_lead_gap_m: number | null;
  ego_stop_duration_s: number | null;
  arrival_time_s: number | null;
  native_contact_count: number | null;
};
export type RoadActor = { id?: string; x: number; y: number; yaw_rad: number; length_m: number; width_m: number; speed_mps?: number; category?: string };
export type RoadFrame = { time_s: number; timestamp_ns: string; ego: RoadActor; objects: RoadActor[]; image_url?: string; image_timestamp_ns?: string; image_age_ns?: string };
export type LogSource = {
  id: string;
  title: string;
  kind: "carla" | "nuscenes" | "recorded";
  ready: boolean;
  status: string;
  reason?: string;
  source_scope?: string;
  baseline_execution_id?: string;
  saved_comparison_execution_id?: string;
  duration_s: number;
  defaults: { lead_wait_s?: number };
  capabilities: { variant: boolean; case_save: boolean };
  events?: LogEvent[];
  metrics?: LogMetrics;
  road_frames?: RoadFrame[];
  recording_ref?: { time_origin_ns: string };
};
export type LogCase = { id: string; revision: number; title: string };
export type VariantRequest = { project_id: string; source_id: string; case_id?: string; case_revision?: number; lead_wait_s: number };
export type VariantPreview = {
  schema_version: string;
  preview_hash: string;
  request: VariantRequest;
  parameters: { lead_wait_s: number };
  baseline_execution_id: string;
  execution_status: "not_executed";
};
export type LogExecution = ObjectExecution & { error?: string | null; outcome_uncertain?: boolean };
export type LogVariant = { id: string; execution_id: string; experiment_id: string; baseline_execution_id: string; parameters: { lead_wait_s: number }; case_ref?: { id: string; revision: number } | null; execution?: LogExecution; created_at?: string; metrics?: LogMetrics };
export type ReplayProfile = { id: string; label: string; controller: { cruise_speed_mps: number; time_headway_s: number; stop_buffer_m: number } };
export type ReplayRequest = { project_id: string; source_id: string; case_id: string; case_revision: number; baseline_profile: string; candidate_profile: string };
export type ReplayPreview = { preview_hash: string; frame_count: number; case_frame_count: number; source_hash: string; sequence_sha256: string; mode: "open_loop"; profiles: { baseline: ReplayProfile; candidate: ReplayProfile }; limitations: string[] };
export type ReplayControl = { acceleration_mps2: number; steering_rad: number; target_speed_mps: number; decision: string };
export type ReplaySample = { index: number; time_s: number; in_case: boolean; logged_speed_mps: number; baseline: ReplayControl; candidate: ReplayControl };
export type ReplayComparison = {
  id: string; project_id: string; source_id: string; created_at: string;
  case_ref: { id: string; revision: number; manifest_hash: string };
  status: string; profiles: { baseline: ReplayProfile; candidate: ReplayProfile };
  frame_count: number; case_frame_count: number; source_hash: string; sequence_sha256: string;
  execution_ids: { baseline: string; candidate: string }; limitations: string[];
  result: null | { status: string; frame_count: number; changed_request_count: number | null; case_changed_request_count: number | null; first_changed_time_s: number | null; reason?: string; metrics: { name: string; label: string; unit: string; baseline: number; candidate: number; delta: number }[]; samples: ReplaySample[] };
};
export type RegressionRun = { id: string; comparison_id: string; status: string; verdict: "pass" | "fail" | "not_evaluated"; checks: { name: string; value: number | null; limit: number; passed: boolean | null }[]; matches_saved_outputs: boolean | null };
export type ReplayRegression = { id: string; name: string; comparison_id: string; case_ref: { id: string; revision: number }; criteria: { max_deceleration_request_mps2: number; max_target_speed_mps: number }; runs: RegressionRun[] };
export const activeExecution = (status: string) => ["queued", "pending", "running"].includes(status);
export const metricValue = (value: number | null | undefined, unit: string) => typeof value === "number" && Number.isFinite(value) ? `${Number.isInteger(value) ? value : value.toFixed(2)}${unit}` : "기록 없음";

export function logSimHref(project: string, source?: string, variant?: string, compare = false) {
  const query = new URLSearchParams({ project });
  if (source) query.set("source", source);
  if (variant) query.set("variant", variant);
  if (compare) query.set("view", "compare");
  return `#log-sim?${query}`;
}
