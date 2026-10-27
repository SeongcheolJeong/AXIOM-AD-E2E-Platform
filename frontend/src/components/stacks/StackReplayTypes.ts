import type { StackDetail, StackPacket, StackOutput } from "./StackTypes";
import type { JobStatus } from "../jobs/JobTypes";
import type { BindingDetail } from "../sensors/SensorTypes";
import type { RecordingSummary } from "../recordings/RecordingTypes";

export interface StackReplayCreate {
  label: string;
  stack_id: string;
  stack_revision: number;
  recording_id: string;
  binding_revision: number;
  lidar_channel_id: number;
  ego_channel_id: number;
  ego_decoder_id: "axiom-ego-json";
  ego_decoder_version: "1";
  ego_log_time_offset_ns: string;
  ego_ordinals: number[];
}
export interface StackReplayMessage {
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
export interface StackReplayInput {
  index: number;
  at_ns: string;
  ego: StackReplayMessage;
  lidar: StackReplayMessage | null;
  lidar_aligned_time_ns: string | null;
  lidar_age_ns: string | null;
  next_lidar_aligned_time_ns: string | null;
}
export interface StackReplayPoints {
  sha256: string;
  size_bytes: number;
  point_count: number;
  encoding: "xyz_float32_le";
  frame_id: string;
  download_url: string;
}
export interface StackReplayTick {
  index: number;
  status: "not_attempted" | "running" | "completed" | "failed";
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  input: StackReplayInput;
  packet: StackPacket | null;
  output: StackOutput | null;
  points: StackReplayPoints | null;
  output_hash: string | null;
  control_application: "not_applied_open_loop";
}
export interface StackReplaySummary {
  id: string;
  submission_seq: number;
  label: string;
  stack_id: string;
  stack_revision: number;
  stack_name: string;
  recording_id: string;
  binding_revision: number;
  mode: "recorded_open_loop";
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
  counts: {
    total: number;
    running: number;
    completed: number;
    failed: number;
    not_attempted: number;
  };
}
export interface StackReplayDetail extends StackReplaySummary {
  request: StackReplayCreate;
  stack_snapshot: StackDetail;
  recording_snapshot: Pick<
    RecordingSummary,
    "id" | "name" | "sha256" | "size_bytes" | "source_kind" | "source" | "index"
  > & { channels: unknown[]; schemas: unknown[] };
  binding_snapshot: BindingDetail;
  inputs: StackReplayInput[];
  ticks: StackReplayTick[];
  events: { at: string; type: string; message: string }[];
  events_total: number;
  manifest_hash: string | null;
  manifest_size_bytes: number | null;
  download_url: string | null;
}
export interface StackReplayPage {
  items: StackReplaySummary[];
  next_before_seq: number | null;
}
export interface StackReplayChannel {
  channel_id: number;
  topic: string;
  schema_name: string | null;
  schema_sha256: string | null;
  message_count: number;
  start_time_ns: string | null;
  end_time_ns: string | null;
  role: "lidar" | "ego";
  decoder_id: string;
  decoder_version: string;
}
export interface StackReplaySources {
  recording_id: string;
  recording_sha256: string;
  channels: StackReplayChannel[];
  warnings: string[];
}
