import type {
  PreparedSimulationContext,
  SimulationControlSelection,
} from "../carla/CarlaTypes";
import type {
  Adapter,
  Metric,
  Parameters,
  Requirement,
  Scenario,
} from "../../types";
import type { JobStatus } from "../jobs/JobTypes";

export type CaseVerdict = "pending" | "passed" | "failed" | "not_evaluated";
export type CampaignVerdict = "pending" | "passed" | "failed" | "incomplete";
export type ComparisonStatus =
  | "added"
  | "removed"
  | "not_evaluated"
  | "not_comparable"
  | "regressed"
  | "improved"
  | "unchanged_pass"
  | "unchanged_fail";
export interface SuiteCaseCreate {
  key: string;
  name: string;
  scenario_id: string;
  scenario_revision: number;
  seed?: number | null;
  parameter_overrides: Partial<Parameters>;
  requirement_ids: string[];
}
export interface SuiteCreate {
  name: string;
  description: string;
  cases: SuiteCaseCreate[];
}
export interface RequirementSnapshot {
  id: string;
  definition: Pick<
    Requirement,
    | "name"
    | "description"
    | "metric_name"
    | "operator"
    | "threshold"
    | "category"
    | "scenario_ids"
  >;
  definition_hash: string;
  unit: string;
}
export interface SuiteCaseSnapshot extends SuiteCaseCreate {
  scenario_snapshot: Scenario;
  effective_input_hash: string;
  requirements: RequirementSnapshot[];
  criteria_hash: string;
}
export interface SuiteSummary {
  id: string;
  name: string;
  description: string;
  revision: number;
  case_count: number;
  created_at: string;
  updated_at: string;
  definition_hash: string;
  evaluator_version: string;
}
export interface SuiteDetail extends SuiteSummary {
  cases: SuiteCaseSnapshot[];
}
export interface CampaignCounts {
  total: number;
  pending: number;
  completed: number;
  failed: number;
  cancelled: number;
  interrupted: number;
  missing: number;
  passed_cases: number;
  failed_cases: number;
  not_evaluated_cases: number;
  checks_total: number;
  checks_passed: number;
  checks_failed: number;
  checks_not_evaluated: number;
}
export interface CriterionCheck {
  measurement_contract?: "carla-observed-contact-count/1" | null;
  requirement_id: string;
  name: string;
  metric_name: string;
  operator: "gte" | "lte" | "eq";
  threshold: number;
  expected_unit: string;
  status: "passed" | "failed" | "not_evaluated";
  value: number | null;
  unit: string | null;
  unbounded: boolean;
  reason: string | null;
}
export interface ExecutionContext {
  adapter_id: string;
  adapter_version: string;
  model_fidelity: string | null;
  result_schema_version: string | null;
  input_hash: string | null;
  output_hash: string | null;
  control_context_hash?: string | null;
}
export interface CampaignExecutionMember {
  key: string;
  job_id: string;
  input_hash: string;
  context_hash: string;
  request_hash: string;
}
export interface CampaignExecutionPlan {
  schema_version: "campaign-execution-plan/1";
  metric_contract_version: "carla-observed-contact-count/1";
  control: SimulationControlSelection;
  context_template: PreparedSimulationContext;
  members: CampaignExecutionMember[];
  plan_hash: string;
}
export interface CampaignCaseReport {
  key: string;
  name: string;
  scenario_id: string;
  scenario_revision: number;
  effective_input_hash: string;
  criteria_hash: string;
  evaluator_version: string;
  job_id: string | null;
  run_id: string | null;
  job_status: JobStatus | "missing";
  verdict: CaseVerdict;
  evaluation_complete: boolean;
  checks: CriterionCheck[];
  metrics: Metric[];
  execution_context: ExecutionContext;
  expected_execution?: CampaignExecutionMember | null;
  reason: string | null;
}
export interface CampaignSummary {
  id: string;
  submission_seq: number;
  label: string;
  suite_id: string;
  suite_revision: number;
  suite_name: string;
  adapter_id: string;
  adapter_version: string;
  created_at: string;
  status: "queued" | "running" | "finished";
  verdict: CampaignVerdict;
  evaluation_complete: boolean;
  counts: CampaignCounts;
}
export interface CampaignDetail extends CampaignSummary {
  execution_plan?: CampaignExecutionPlan | null;
  evaluated_at: string;
  evaluator_version: string;
  suite_snapshot: SuiteDetail;
  adapter_snapshot: Adapter;
  cases: CampaignCaseReport[];
}
export interface CampaignPage {
  items: CampaignSummary[];
  next_before_seq: number | null;
}
export interface BaselineSummary {
  id: string;
  name: string;
  note: string;
  campaign_id: string;
  suite_id: string;
  suite_revision: number;
  created_at: string;
  manifest_hash: string;
  manifest_size_bytes: number;
  total_cases: number;
  verdict: CampaignVerdict;
  download_url: string;
}
export interface BaselineDetail extends BaselineSummary {
  manifest: {
    schema_version: string;
    baseline_id: string;
    name: string;
    note: string;
    captured_at: string;
    report: CampaignDetail;
  };
}
export interface MetricDelta {
  metric_name: string;
  unit: string | null;
  baseline_value: number | null;
  current_value: number | null;
  delta: number | null;
}
export interface ComparisonRow {
  key: string;
  name: string;
  status: ComparisonStatus;
  reasons: string[];
  baseline_verdict: CaseVerdict | null;
  current_verdict: CaseVerdict | null;
  baseline_job_id: string | null;
  current_job_id: string | null;
  baseline_run_id: string | null;
  current_run_id: string | null;
  baseline_adapter_version: string | null;
  current_adapter_version: string | null;
  adapter_version_changed: boolean;
  metrics: MetricDelta[];
}
export interface CampaignComparison {
  campaign_id: string;
  baseline_id: string;
  baseline_manifest_hash: string;
  suite_id: string;
  compared_at: string;
  counts: Record<ComparisonStatus, number> & { total: number };
  rows: ComparisonRow[];
}
export interface ComparisonSummary {
  id: string;
  name: string;
  note: string;
  campaign_id: string;
  campaign_label: string;
  baseline_id: string;
  baseline_name: string;
  suite_id: string;
  suite_revision: number;
  created_at: string;
  policy_version: string;
  candidate_verdict: CampaignVerdict;
  candidate_evaluation_complete: boolean;
  manifest_hash: string;
  manifest_size_bytes: number;
  counts: CampaignComparison["counts"];
  download_url: string;
}
export interface ComparisonDetail extends ComparisonSummary {
  manifest: {
    schema_version: string;
    comparison_id: string;
    name: string;
    note: string;
    captured_at: string;
    policy_version: string;
    baseline_manifest_hash: string;
    baseline_manifest_size_bytes: number;
    baseline: BaselineDetail["manifest"];
    candidate: CampaignDetail;
    report: CampaignComparison;
  };
}
export const comparisonLabels: Record<ComparisonStatus, string> = {
  added: "Added",
  removed: "Removed",
  not_evaluated: "Not evaluated",
  not_comparable: "Conditions differ",
  regressed: "Regressed",
  improved: "Improved",
  unchanged_pass: "Unchanged pass",
  unchanged_fail: "Unchanged fail",
};
