import type { Scenario } from "../../types";
import type { PreparedSimulationContext } from "../carla/CarlaTypes";
import type { JobStatus } from "./JobTypes";

/** Mirrored from ade2e.execution_attempt and ade2e.recovery_schemas. */
export interface RemoteExecutionStatus {
  schema_version: "remote-execution-status/1";
  store_id: string;
  execution_id: string;
  request_hash: string;
  input_hash: string;
  context_hash: string | null;
  adapter_id: string;
  adapter_version: string;
  state:
    | "not_found"
    | "accepted"
    | "running"
    | "completed"
    | "failed"
    | "interrupted";
  observed_at: string;
  archive_kind: "complete" | "partial" | null;
  archive_sha256: string | null;
  archive_size_bytes: number | null;
  mutations_started: boolean;
  cleanup_status: "not_started" | "confirmed" | "failed" | "unknown";
  error: string | null;
}

export interface RemoteExecutionBinding {
  store_id: string;
  request_hash: string;
  attempt: {
    schema_version: "execution-attempt/1";
    execution_id: string;
    input_hash: string;
    adapter_id: string;
    adapter_version: string;
    scenario: Scenario;
    context: PreparedSimulationContext | null;
  };
}
export interface RemoteExecutionInspection {
  source_job_id: string;
  source_run_id: string | null;
  source_execution_id: string | null;
  supported: boolean;
  can_inspect: boolean;
  can_retrieve: boolean;
  reason: string | null;
  binding: RemoteExecutionBinding | null;
  status: RemoteExecutionStatus | null;
  recovered_run_id: string | null;
}
export interface RecoverySummary {
  id: string;
  submission_seq: number;
  source_job_id: string;
  source_run_id: string | null;
  source_execution_id: string;
  status: JobStatus;
  outcome:
    "pending" | "recovered" | "diagnostics_only" | "not_available" | "failed";
  created_at: string;
  updated_at: string;
  finished_at: string | null;
  note: string;
  run_id: string | null;
  error: string | null;
  remote_status: RemoteExecutionStatus | null;
  manifest_hash: string | null;
  manifest_size_bytes: number | null;
  download_url: string | null;
  archive_hash: string | null;
  archive_size_bytes: number | null;
  archive_download_url: string | null;
}
export interface RecoveryDetail extends RecoverySummary {
  binding: RemoteExecutionBinding;
  events: { at: string; type: string; message: string }[];
  events_total: number;
  cancellation_supported: boolean;
  worker_id: string | null;
  lease_expires_at: string | null;
  cancel_requested_at: string | null;
}
export interface RecoveryPage {
  items: RecoverySummary[];
  next_before_seq: number | null;
}
