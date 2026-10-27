import type { ReplayProfile, ReplaySample } from "./log-sim-contract";

export type LogLibrarySegment = { source_id: string; title: string; frame_count: number; duration_s: number; start_time_s: number; end_time_s: number; source_hash: string };
export type LogLibraryScene = {
  id: string; name: string; title: string; description: string; description_source: "original_dataset_metadata" | "user_upload";
  source_kind: "nuscenes" | "recorded"; original_frame_count: number; available_frame_count: number; excluded_frame_count: number;
  duration_s: number; status: "ready" | "partial" | "unavailable"; exclusions: { reason: string; frame_count: number }[];
  thumbnail_url: string | null; segments: LogLibrarySegment[];
};
export type LogLibrary = { schema_version: string; project_id: string; library_id: string; manifest_hash: string; items: LogLibraryScene[]; counts: { scene_count: number; segment_count: number; original_frame_count: number; available_frame_count: number; excluded_frame_count: number }; scope: string };
export type LogBatchSource = { source_id: string; title: string; status: string; reason?: string | null; comparison_id?: string; execution_ids?: { baseline?: string; candidate?: string }; frame_count: number; duration_s: number; source_hash: string; scene_id: string };
export type LogBatchCounts = { distinct_scenes: number; source_count: number; supported_sources: number; completed_sources: number; failed_sources: number; unsupported_sources: number; candidate_count: number; frame_count: number; duration_s: number };
export type LogBatchCandidate = {
  id: string; source_id: string; label: string; kind: string; time_s: number; end_time_s: number;
  start_index: number; end_index: number; rank: number; comparison_id: string; status: string; event_ids: string[];
  metrics: { sample_count: number; decision_difference_count: number; decision_difference_ratio: number; opposite_acceleration_count: number; opposite_acceleration_ratio: number; max_abs_acceleration_delta_mps2: number; baseline_max_deceleration_mps2: number; candidate_max_deceleration_mps2: number };
};
export type LogBatch = { id: string; status: string; report_hash: string; created_at: string; sources: LogBatchSource[]; counts: LogBatchCounts; candidates: LogBatchCandidate[]; limitations?: string[] };
export type LogBatchPreview = { preview_hash: string; sources: LogBatchSource[]; counts: LogBatchCounts; planned_execution_count: number; profiles: { baseline: ReplayProfile; candidate: ReplayProfile }; limitations?: string[] };
export type LogBatchComparison = { id: string; profiles: { baseline: ReplayProfile; candidate: ReplayProfile }; status: string; frame_count: number; result: null | { status: string; samples: ReplaySample[]; reason?: string } };
export type LogCaseBundleItem = { candidate_id: string; source_id: string; case_id: string; case_revision: number; title?: string };
export type LogCaseBundle = { id: string; name: string; status: string; created_at: string; batch_id: string; items: LogCaseBundleItem[]; candidate_count?: number };
export const batchIsActive = (value: LogBatch) => ["queued", "running", "pending", "preparing", "submitted"].includes(value.status);
export const batchKindLabel = (kind: string) => ({ low_speed: "저속·정지", deceleration: "감속", nearby_ahead: "전방 근접", decision_divergence: "판단 차이", opposite_acceleration: "가감속 방향 반대" }[kind] ?? kind);
export const batchStatusLabel = (status: string) => ({ ready: "준비됨", queued: "실행 대기", pending: "실행 대기", running: "실행 중", submitted: "실행 대기", completed: "완료", evaluated: "평가됨", failed: "실패", unavailable: "입력 없음", unsupported: "미지원", partial: "일부 제외", partially_completed: "부분 완료", completed_with_errors: "일부 실행 실패", not_evaluated: "미평가", submission_failed: "제출 실패", cancelled: "취소됨" }[status] ?? status);
