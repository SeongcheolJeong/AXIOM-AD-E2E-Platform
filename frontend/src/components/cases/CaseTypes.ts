export type CaseSource = { kind: "recording"; recording_id: string; channel_id: number; start_time_ns: string; end_time_ns: string } | { kind: "evaluation"; evaluation_id: string; image_id: string };
export interface CaseSummary { id: string; revision: number; title: string; state: string; assignee: string; created_at: string; manifest_hash: string }
export interface CaseListItem extends CaseSummary {
  conditions?: { values: Record<string, string>; review_status: string };
  diagnostics?: { state: string; tags: string[] };
}
export interface NeuralCaseLink {
  schema_version: "neural-sensitivity-case-link/1"; project_id: string;
  render_pin: { execution_id: string }; sensitivity_pin: { execution_id: string };
  model_id: string; model_revision: number; frame_index: number; source_frame_index: number; source_time_s: number;
  selected_variant: "original" | "baseline" | "novel";
  summary: { original_count: number; baseline_count: number; novel_count: number };
}
export interface CaseDetail extends CaseSummary {
  neural_sensitivity?: NeuralCaseLink;
  conditions?: import("./CaseConditions").ConditionAnnotation;
  note: string; disposition_note: string; previous_manifest_hash: string | null; download_url: string;
  segment_hash: string;
  segment: { id: string; recording_id: string; recording_sha256: string; recording_name: string; channel_id: number; clock_domain: string; sample_count: number; start_time_ns: string; end_time_ns: string; samples: { ordinal: number; log_time_ns: string; publish_time_ns: string; sha256: string; size_bytes: number }[] };
  evaluations: { evaluation_id: string; name: string; image_id: string; row_status: string; manifest_hash: string; dataset_revision: number; diagnostics: { tp: number; fp: number; fn: number } | null }[];
}
export interface CasePage { items: CaseListItem[]; next_offset: number | null; total_matches?: number }
export function caseHref(id: string, revision?: number, project?: string) { return `#cases?${new URLSearchParams({ case: id, ...(revision ? { revision: String(revision) } : {}), ...(project ? { project } : {}) })}`; }
