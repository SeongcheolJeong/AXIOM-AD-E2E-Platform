import type { CarlaZipArtifact } from "../../pages/carla-zip";

export type NeuralParameters = { lateral_m: number; height_m: number };
export type NeuralMode = "frame" | "sequence" | "camera_sweep";
export type NeuralPreset = { id: string; title: string; description: string; ready: boolean; status: string; reason: string | null; defaults: NeuralParameters; default_mode?: NeuralMode; default_frame_index?: number; binding_revision: number | null; binding_sha256?: string | null; saved_execution_id: string | null; source: { source_execution_id: string; reconstruction_execution_id: string | null; saved_execution_id: string | null; seed: number; adapter_version: string } | null };
export type NeuralSource = { id: string; name: string; experiment_kind: string; adapter_id: string; created_at: string; source_kind?: string; frame_count?: number; fps?: number; default_frame_index?: number; source_execution_id?: string; reconstruction_execution_id?: string; ready?: boolean; reason?: string | null };
export type NeuralSources = { sources: NeuralSource[]; reconstructions: NeuralSource[]; renders: NeuralSource[] };
export type NeuralExecution = { id: string; experiment_id: string; project_id: string; experiment_kind: string; status: string; adapter_id: string; adapter_version: string; input_hash: string; manifest_hash: string | null; download_url: string | null; fidelity: string | null; summary: Record<string, unknown> | null; artifacts: CarlaZipArtifact[]; created_at: string; error?: string | null; outcome_uncertain?: boolean; cancellation_supported?: boolean };
export type NeuralPreview = { experiment: Record<string, unknown>; preview: { preview_hash: string; resource_id?: string | null; job_count?: number; execution_fidelity?: string }; parameters?: NeuralParameters };
export type NeuralAttempt = { key: string; signature: string; experiment?: { id: string; preview_hash: string }; execution?: { id: string } };
export type NeuralApi = <T>(path: string, init?: RequestInit) => Promise<T>;
export const neuralSourceLabel = (kind: string | undefined) => kind === "simulation" ? "CARLA 합성 로그의 Neural 재구성" : kind === "real_road" ? "실도로 로그의 Neural 재구성 · 연구용" : "로그의 Neural 재구성";
export const neuralOriginalLabel = (kind: string | undefined) => kind === "simulation" ? "원본 CARLA 영상" : kind === "real_road" ? "원본 실도로 영상" : "원본 로그 영상";
export function neuralPresetForSource(preset: NeuralPreset, source?: NeuralSource): NeuralPreset {
  if (preset.source || source?.source_kind !== "real_road") return preset;
  const sweep = preset.default_mode === "camera_sweep";
  return { ...preset, defaults: { ...preset.defaults, ...(sweep ? { lateral_m: .5 } : {}) },
    default_frame_index: sweep ? source.default_frame_index ?? Math.floor((source.frame_count ?? 1) / 2) : 0,
    reason: "선택한 실도로 로그와 학습 장면을 프리셋에 연결하세요." };
}
export const neuralTerminal = (status: string) => ["completed", "failed", "cancelled", "interrupted", "rejected"].includes(status);
export const neuralStatus = (status: string) => ({ queued: "GPU 실행 대기", claimed: "GPU 준비", preparing: "GPU 준비", running: "Neural 엔진 실행 중", completed: "결과 준비 완료", failed: "실행 실패", cancelled: "취소됨", interrupted: "실행 중단", rejected: "실행 거절" } as Record<string, string>)[status] ?? status;
export const isNeuralExecution = (run: NeuralExecution, project: string) => run.project_id === project && run.adapter_id === "neural-splatfacto" && ["neural_reconstruction", "neural_render"].includes(run.experiment_kind);
export const neuralSignature = (project: string, preset: NeuralPreset, parameters: NeuralParameters, mode: NeuralMode, frameIndex: number) => JSON.stringify([project, preset.id, preset.binding_revision, preset.binding_sha256, parameters.lateral_m, parameters.height_m, mode, mode === "sequence" ? 0 : frameIndex]);
export function neuralParameterError(parameters: NeuralParameters, mode: NeuralMode = "sequence") {
  if (mode === "camera_sweep") {
    if (!Number.isFinite(parameters.lateral_m) || parameters.lateral_m <= 0 || parameters.lateral_m > 2) return "카메라 이동 반경은 0보다 크고 2 m 이하로 선택하세요.";
  } else if (!Number.isFinite(parameters.lateral_m) || Math.abs(parameters.lateral_m) > .5) return "좌우 이동은 −0.5 ~ +0.5 m 범위에서 선택하세요.";
  if (!Number.isFinite(parameters.height_m) || Math.abs(parameters.height_m) > .3) return "높이 이동은 −0.3 ~ +0.3 m 범위에서 선택하세요.";
  return "";
}
export function neuralPlaybackStep(index: number, count: number, direction: 1 | -1, loop: boolean) {
  if (count < 2) return null;
  if (index + direction >= count || index + direction < 0) {
    if (!loop) return null;
    direction = direction === 1 ? -1 : 1;
  }
  return { index: index + direction, direction };
}
export class NeuralPreviewChanged extends Error {}
export async function executeNeural(request: NeuralApi, preview: NeuralPreview, attempt: NeuralAttempt, progress: (text: string) => void) {
  if (attempt.execution) return attempt.execution;
  progress("입력과 장면 개정 저장 중");
  attempt.experiment ??= await request<{ id: string; preview_hash: string }>("/experiments", { method: "POST", headers: { "Idempotency-Key": `${attempt.key}-experiment` }, body: JSON.stringify(preview.experiment) });
  if (attempt.experiment.preview_hash !== preview.preview.preview_hash) throw new NeuralPreviewChanged("장면 또는 실행 환경이 바뀌었습니다. 실행 내용을 다시 확인하세요.");
  progress("GPU 작업 제출 중");
  attempt.execution = await request<{ id: string }>(`/experiments/${encodeURIComponent(attempt.experiment.id)}/executions`, { method: "POST", headers: { "Idempotency-Key": `${attempt.key}-execution` }, body: JSON.stringify({ preview_hash: preview.preview.preview_hash }) });
  return attempt.execution;
}
