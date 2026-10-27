import type { JobStatus } from "../jobs/JobTypes";
import type { InferenceDetail } from "../perception/PerceptionTypes";
import type {
  DatasetAnnotation,
  DatasetDetail,
  DatasetImage,
  DatasetSplit,
} from "./DatasetTypes";

export interface EvaluatorMetadata {
  id: string;
  name: string;
  version: string;
  policy_version: string;
  status: "available" | "not_installed" | "unavailable";
  implementation: Record<string, string>;
  limits: Record<string, number>;
  limitations: string[];
}
export interface EvaluationCreate {
  name: string;
  note: string;
  dataset_id: string;
  dataset_revision: number;
  split: DatasetSplit;
  inference_job_ids: string[];
  evaluator_id: string;
  class_mapping: { model_label: string; dataset_label: string | null }[];
  score_threshold: number;
  diagnostic_iou: number;
}
export interface EvaluationSummary {
  id: string;
  submission_seq: number;
  status: JobStatus;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  dispatched_at: string | null;
  finished_at: string | null;
  error: string | null;
  worker_id: string | null;
  lease_expires_at: string | null;
  cancel_requested_at: string | null;
  cancellation_supported: boolean;
  outcome_uncertain: boolean;
  input_hash: string;
  name: string;
  dataset_id: string;
  dataset_revision: number;
  dataset_name: string;
  evaluator_id: string;
  evaluator_version: string;
}
export type EvaluationRowStatus =
  | "evaluated"
  | "unreviewed"
  | "missing_prediction"
  | "failed_prediction"
  | "input_changed";
export interface EvaluationPrediction {
  id: string;
  label: string;
  score: number;
  bbox_xyxy: [number, number, number, number];
}
export interface EvaluationInputRow {
  image_id: string;
  status: EvaluationRowStatus;
  width: number;
  height: number;
  annotations: DatasetAnnotation[];
  predictions: EvaluationPrediction[];
}
export interface EvaluationMatch {
  outcome:
    "true_positive" | "false_positive" | "crowd_ignored" | "false_negative";
  prediction_id: string | null;
  annotation_id: string | null;
  iou: number | null;
}
export interface EvaluationDiagnostics {
  gt: number;
  crowd_gt: number;
  predictions: number;
  score_filtered: number;
  tp: number;
  fp: number;
  fn: number;
  crowd_ignored: number;
  matches: EvaluationMatch[];
}
export interface EvaluationReportRow {
  image_id: string;
  status: EvaluationRowStatus;
  diagnostics: EvaluationDiagnostics | null;
}
export interface EvaluationClass {
  label: string;
  gt: number;
  crowd_gt: number;
  predictions: number;
  tp: number;
  fp: number;
  fn: number;
  crowd_ignored: number;
  ap: number | null;
  pr_curve: { recall: number[]; precision: number[] } | null;
}
export interface EvaluationMetrics {
  ap: number | null;
  ap50: number | null;
  ap75: number | null;
  ap_small: number | null;
  ap_medium: number | null;
  ap_large: number | null;
  ar1: number | null;
  ar10: number | null;
  ar100: number | null;
  ar_small: number | null;
  ar_medium: number | null;
  ar_large: number | null;
  precision: number | null;
  recall: number | null;
  f1: number | null;
}
export interface EvaluationReport {
  policy_version: string;
  implementation: Record<string, string>;
  parameters: Record<string, unknown>;
  coverage: {
    total: number;
    evaluated: number;
    unreviewed: number;
    missing_prediction: number;
    failed_prediction: number;
    input_changed: number;
    complete: boolean;
  };
  metrics: EvaluationMetrics | null;
  diagnostic_scope: "evaluated_rows";
  classes: EvaluationClass[];
  rows: EvaluationReportRow[];
  warnings: string[];
  elapsed_ms: number;
}
export interface EvaluationDetail extends EvaluationSummary {
  request: EvaluationCreate;
  dataset_snapshot: DatasetDetail;
  source_jobs: InferenceDetail[];
  evaluator_snapshot: EvaluatorMetadata;
  evaluation_input: {
    labels: string[];
    score_threshold: number;
    diagnostic_iou: number;
    rows: EvaluationInputRow[];
  };
  source_context: {
    inference_configuration: Record<string, unknown>;
    observed_runtime_context: unknown;
    rows: {
      image_id: string;
      status: EvaluationRowStatus;
      reason: string | null;
      inference_job_id: string | null;
      selection_index: number | null;
      image_sha256: string;
      excluded_class_predictions: number;
      input: DatasetImage["input"];
    }[];
    outside_split_sources: unknown[];
    excluded_class_predictions: number;
  };
  report: EvaluationReport | null;
  events: { at: string; type: string; message: string }[];
  events_total: number;
  manifest_hash: string | null;
  manifest_size_bytes: number | null;
  download_url: string | null;
}
export interface EvaluationPage {
  items: EvaluationSummary[];
  next_before_seq: number | null;
}
