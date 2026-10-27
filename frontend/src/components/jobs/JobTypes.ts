import type { Parameters, Scenario } from "../../types";
import type {
  SimulationControlSelection,
  PreparedSimulationContext,
} from "../carla/CarlaTypes";

export type JobStatus =
  | "queued"
  | "running"
  | "cancel_requested"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export interface JobRequest {
  scenario_id: string;
  adapter_id?: string;
  seed?: number | null;
  parameter_overrides?: Partial<Parameters>;
  control?: SimulationControlSelection | null;
}

export interface JobSummary {
  id: string;
  submission_seq: number;
  scenario_id: string;
  scenario_name: string;
  scenario_revision: number;
  adapter_id: string;
  adapter_version: string;
  status: JobStatus;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  dispatched_at?: string | null;
  run_id: string | null;
  error: string | null;
  input_hash: string;
  worker_id: string | null;
  lease_expires_at: string | null;
  cancel_requested_at: string | null;
  cancellation_supported: boolean;
  outcome_uncertain: boolean;
}

export interface JobDetail extends JobSummary {
  execution_context?: PreparedSimulationContext | null;
  scenario_snapshot: Scenario;
  request: JobRequest;
  events: { at: string; type: string; message: string }[];
  events_total?: number;
  failure_http_status?: number | null;
}

export interface JobsResponse {
  items: JobSummary[];
  next_before_seq: number | null;
}

export interface WorkerSummary {
  id: string;
  heartbeat_at: string;
  state: "idle" | "running" | "stopped";
  job_id: string | null;
  alive: boolean;
}

export interface ExecutionResource {
  adapter_id: string;
  state: "idle" | "reserved" | "reconcile_required";
  job_id: string | null;
  updated_at: string;
  note: string;
}

export const jobStatusLabels: Record<JobStatus, string> = {
  queued: "Queued",
  running: "Running",
  cancel_requested: "Cancel requested",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  interrupted: "Interrupted",
};

export const activeJobStatuses = new Set<JobStatus>([
  "queued",
  "running",
  "cancel_requested",
]);
