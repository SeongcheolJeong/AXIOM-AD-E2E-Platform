export type RecordingSourceKind =
  "unknown" | "synthetic" | "simulation" | "measured";

export interface RecordingSummary {
  id: string;
  name: string;
  source_kind: RecordingSourceKind;
  source: string;
  sha256: string;
  size_bytes: number;
  created_at: string;
  format: "mcap";
  profile: string;
  message_count: number;
  channel_count: number;
  start_time_ns: string | null;
  end_time_ns: string | null;
  duration_s: number;
  download_url: string;
  warnings: string[];
  index?: {
    version: string;
    sha256: string;
    size_bytes: number;
    source_sha256: string;
  };
}

export interface RecordingChannel {
  id: number;
  topic: string;
  message_encoding: string;
  schema_id: number;
  message_count: number;
  start_time_ns: string | null;
  end_time_ns: string | null;
  metadata: Record<string, string>;
  decoder: "json" | "opaque";
}

export interface RecordingSchema {
  id: number;
  name: string;
  encoding: string;
  sha256: string;
  size_bytes: number;
  definition: string | null;
  definition_truncated: boolean;
}

export interface RecordingDetail extends RecordingSummary {
  channels: RecordingChannel[];
  schemas: RecordingSchema[];
  metadata: { name: string; metadata: Record<string, string> }[];
}

export interface RecordingSample {
  ordinal: number;
  log_time_ns: string;
  publish_time_ns: string;
  sequence: number;
  size_bytes: number;
  sha256: string;
  decoded: unknown;
  decoded_text?: string | null;
  decode_status: "decoded" | "opaque" | "invalid_json" | "too_large";
  preview_hex: string;
  truncated: boolean;
}

export interface RecordingSamples {
  recording_id: string;
  channel_id: number;
  requested_time_ns: string | null;
  samples: RecordingSample[];
  warnings: string[];
  next_after_ordinal?: number | null;
}

export const MAX_RECORDING_UPLOAD_BYTES = 128 * 1024 * 1024;
