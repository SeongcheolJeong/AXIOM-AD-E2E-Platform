export type Category =
  "cut_in" | "emergency_brake" | "pedestrian_crossing" | "lane_follow";
export interface Parameters {
  ego_speed_mps: number;
  target_speed_mps: number;
  initial_gap_m: number;
  trigger_time_s: number;
  friction: number;
  response_delay_s: number;
}
export interface Scenario {
  id: string;
  name: string;
  description: string;
  category: Category;
  map_name: string;
  weather: "clear" | "rain" | "fog";
  time_of_day: "day" | "night" | "dusk";
  tags: string[];
  parameters: Parameters;
  duration_s: number;
  seed: number;
  revision: number;
  created_at: string;
  updated_at: string;
}
export type ScenarioCreate = Omit<
  Scenario,
  "id" | "revision" | "created_at" | "updated_at"
>;
export interface Metric {
  name: string;
  value: number | null;
  unit: string;
  threshold: number;
  operator: "gte" | "lte" | "eq";
  passed: boolean;
}
export interface Pose {
  x: number;
  y: number;
  yaw: number;
  speed_mps: number;
}
export interface Frame {
  time_s: number;
  ego: Pose;
  actors: (Pose & { id: string; type: string })[];
  ttc_s: number | null;
  acceleration_mps2: number;
}
export interface RunEvent {
  time_s: number;
  type: string;
  message: string;
}
export interface Provenance {
  input_hash: string;
  output_hash: string;
  seed: number;
  adapter_version: string;
  model_fidelity: string;
}
export interface RunSummary {
  id: string;
  scenario_id: string;
  scenario_name: string;
  scenario_revision: number;
  adapter_id: string;
  status: "completed" | "error";
  verdict: "passed" | "failed" | "error";
  created_at: string;
  duration_s: number;
  execution_ms: number;
  metrics: Metric[];
  provenance: Provenance;
  recovery?: RecoveryLineage | null;
}
export interface RecoveryLineage {
  verification_version: "remote-recovery/1";
  recovery_id: string;
  source_job_id: string;
  source_run_id: string | null;
  source_execution_id: string;
  source_submission_seq: number;
  store_id: string;
  request_hash: string;
  archive_sha256: string;
  archive_kind: "complete" | "partial";
  recovered_at: string;
}
export interface RunDetail extends RunSummary {
  execution_context?:
    import("./components/carla/CarlaTypes").PreparedSimulationContext | null;
  scenario_snapshot: Scenario;
  trajectory: Frame[];
  events: RunEvent[];
  artifacts: Artifact[];
}
export interface Artifact {
  id: string;
  run_id: string;
  name: string;
  kind: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  created_at: string;
  download_url: string;
  source: string;
}
export interface Requirement {
  id: string;
  name: string;
  description: string;
  metric_name: string;
  operator: "gte" | "lte" | "eq";
  threshold: number;
  category: string | null;
  scenario_ids: string[];
  status: "passed" | "failed" | "untested";
  evidence_count: number;
  last_run_id: string | null;
}
export interface Adapter {
  id: string;
  name: string;
  version: string;
  kind: string;
  status: "available" | "not_configured" | "planned";
  description: string;
  capabilities: string[];
  limitations: string[];
  license: string;
  source_url: string;
}
export interface Overview {
  scenario_count: number;
  run_count: number;
  pass_rate: number;
  artifact_count: number;
  requirement_count: number;
  covered_requirement_count: number;
  recent_runs: RunSummary[];
  category_distribution: { category: string; count: number }[];
  coverage: {
    category: string;
    weather: string;
    total: number;
    passed: number;
    tested: number;
    failed: number;
    untested: number;
  }[];
}
export interface AgentTool {
  name: string;
  description: string;
  method: string;
  path: string;
  input_schema: Record<string, unknown>;
  read_only: boolean;
  required_headers?: string[];
  idempotency_scope?: string | null;
}
export const categoryLabels: Record<string, string> = {
  cut_in: "Cut-in",
  emergency_brake: "Emergency braking",
  pedestrian_crossing: "Pedestrian crossing",
  lane_follow: "Lane following",
};
export const metricLabels: Record<string, string> = {
  collision_count: "Collisions",
  min_ttc_s: "Minimum TTC",
  max_deceleration_mps2: "Peak deceleration",
  max_jerk_mps3: "Peak jerk",
  distance_m: "Distance travelled",
};
export const categories = Object.keys(categoryLabels) as Category[];
