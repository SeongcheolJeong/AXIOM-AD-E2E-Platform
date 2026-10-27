import type { ObjectExecution } from "../object-scene/object-scene";

export type HilSide = "baseline" | "faulted";
export type HilParameter = { key: string; label: string; min: number; max: number; step: number; unit: string; default: number; baseline?: number };
export type HilFaultTiming = { schema_version: "hil-fault-timing/1"; start_min_s: number; start_max_s: number; end_max_s: number; duration_min_s: number; duration_max_s: number; step_s: number; default_start_s: number; default_duration_s: number };
export type HilPreset = { id: string; title: string; description: string; ready: boolean; status: string; reason: string | null; parameters: HilParameter[]; defaults: Record<string, number>; fault_timing?: HilFaultTiming; binding_revision: number | null; binding_sha256?: string | null; saved_execution_id: string | null; source?: { seed?: number; adapter_version?: string } | null };
export type HilPreview = { schema_version: string; parameters: Record<string, number>; experiment: Record<string, unknown>; preview: { preview_hash: string }; changes: { key: string; label: string; unit: string; baseline: number; value: number }[] };
export type HilExecution = ObjectExecution & { created_at: string; error?: string | null; outcome_uncertain?: boolean; cancellation_supported?: boolean; summary?: { fault?: { kind?: string } } | null };
export type HilAttempt = { key: string; signature: string; experiment?: { id: string; preview_hash: string }; execution?: { id: string } };
export type HilApi = <T>(path: string, init?: RequestInit) => Promise<T>;
export const hilTerminal = (status: string) => ["completed", "failed", "cancelled", "interrupted", "rejected"].includes(status);
export const hilStatus = (status: string) => ({ queued: "실행 대기", claimed: "엔진 준비", preparing: "엔진 준비", running: "Software DUT · CARLA A/B 실행 중", completed: "새 A/B 결과 준비 완료", failed: "실행 실패", cancelled: "취소됨", interrupted: "실행 중단", rejected: "실행 거절" } as Record<string, string>)[status] ?? status;
export const hilSignature = (project: string, preset: HilPreset, parameters: Record<string, number>) => JSON.stringify([project, preset.id, preset.binding_revision, preset.binding_sha256, parameters]);
export function hilParameterError(parameter: HilParameter, value: number) {
  if (!Number.isFinite(value) || value < parameter.min || value > parameter.max) return `${parameter.min}–${parameter.max} ${parameter.unit} 범위에서 선택하세요.`;
  if (Math.abs((value - parameter.min) / parameter.step - Math.round((value - parameter.min) / parameter.step)) > 1e-7) return `${parameter.step} ${parameter.unit} 단위로 선택하세요.`;
  return "";
}
export class HilPreviewChanged extends Error {}
export async function executeHilDemo(request: HilApi, preview: HilPreview, attempt: HilAttempt, progress: (text: string) => void): Promise<{ id: string }> {
  if (attempt.execution) return attempt.execution;
  progress("실험 입력 고정 중");
  attempt.experiment ??= await request<{ id: string; preview_hash: string }>("/experiments", { method: "POST", headers: { "Idempotency-Key": `${attempt.key}-experiment` }, body: JSON.stringify(preview.experiment) });
  if (attempt.experiment.preview_hash !== preview.preview.preview_hash) throw new HilPreviewChanged("확인한 설정과 현재 실행 입력이 달라졌습니다. 변경 내용을 다시 확인하세요.");
  progress("A/B 실행 제출 중");
  attempt.execution = await request<{ id: string }>(`/experiments/${encodeURIComponent(attempt.experiment.id)}/executions`, { method: "POST", headers: { "Idempotency-Key": `${attempt.key}-execution` }, body: JSON.stringify({ preview_hash: preview.preview.preview_hash }) });
  return attempt.execution;
}
export function isHilExecution(execution: HilExecution, project: string) {
  return execution.project_id === project && execution.experiment_kind === "hil_demo" && execution.adapter_id === "carla-software-dut";
}
export function hilInitialPreset(items: HilPreset[], explicit: string | null, result?: HilExecution | null) {
  const resultPreset = result?.summary?.fault?.kind === "repeat" ? "input_repeat" : result?.summary?.fault?.kind === "drop" ? "input_dropout" : "input_delay";
  return items.find(item => item.id === explicit) ?? items.find(item => item.id === resultPreset) ?? items[0];
}
export function hilErrorMessage(message: string) {
  if (message.includes("Batch state changed; reload")) return "순차 실행 상태가 변경되었습니다. 상태를 새로 확인한 뒤 다시 요청하세요.";
  if (message.includes("Batch preparation differs from the reviewed preview")) return "확인한 실행 준비 정보가 변경되었습니다. 고정 목록의 엔진 준비를 다시 확인하세요.";
  if (message.includes("The previous execution outcome is uncertain") || message.includes("Execution outcome is uncertain; no batch")) return "이전 실행 결과가 불확실합니다. 원본 실행 기록을 확인하세요. 이 순차 실행에서는 자동 재시도하거나 다음 조건을 제출하지 않습니다.";
  if (message.includes("The previous execution is still active")) return "이전 실행이 아직 끝나지 않았습니다. 실제 종료 기록이 확인되면 이어갈 수 있습니다.";
  if (message.includes("The pinned execution resource must be idle")) return "CARLA 실행 자원이 사용 중입니다. 현재 실행이 끝난 뒤 상태를 다시 확인하세요.";
  if (message.includes("Resolve the recorded evidence integrity issue")) return "보존된 입력·결과의 무결성 문제로 이어갈 수 없습니다. 원본 기록을 확인하고 문제를 해결한 뒤 새 목록을 만드세요.";
  if (message.includes("Unfinished batch input changed since acceptance")) return "접수 후 미완료 조건의 실행 입력이 변경되었습니다. 고정 입력을 확인해 새 목록을 만드세요.";
  if (/^Execution (failed|cancelled|interrupted); inspect its original receipt$/.test(message)) return "현재 조건의 실행이 실패하거나 중단되었습니다. 원본 실행 기록을 확인한 뒤 미완료 조건을 다시 실행할 수 있습니다.";
  if (message.includes("Dedicated native CARLA city transport is not configured")) return "새 실행을 위한 CARLA 연결 설정이 없습니다. 운영 설정을 적용한 뒤 연결 설정을 새로고침하세요.";
  if (message.includes("Native city endpoint probe failed")) return "CARLA 엔진 연결을 확인하지 못했습니다. 엔진 상태를 복구한 뒤 실행 준비를 다시 확인하세요.";
  if (message.includes("Execution inputs differ from the saved HIL test revision")) return "이 결과의 실행 입력이 선택한 시험 개정과 다릅니다. 같은 고장 값과 고정 조건으로 실행한 결과를 선택하세요.";
  if (message.includes("HIL test revision changed; reload before saving") || message.includes("Saved HIL test revision hash differs")) return "시험 개정이 변경되었습니다. 저장된 시험을 다시 연 뒤 저장하거나 실행을 확인하세요.";
  return message.includes("Dedicated native city endpoint contains foreign actors")
    ? "CARLA 환경에 사용 중인 객체가 있습니다. 진행 중인 실행이 끝난 후 다시 확인하세요."
    : message;
}
export function hilEngineStatus(status: string | undefined, loading: boolean, error: string, reviewed: boolean) {
  const retained = "시험 저장·복제와 기존 결과 조사·관찰을 계속 사용할 수 있습니다.";
  if (loading) return { title: "실행 연결 설정 확인 중…", description: retained };
  if (error) return { title: "실행 연결 설정을 읽지 못했습니다.", description: retained };
  if (status !== "available") return { title: "새 실행 연결 설정이 없습니다.", description: retained };
  if (reviewed) return { title: "실행 준비 확인 완료", description: "저장 개정을 확인한 시점의 엔진 상태입니다. 실행 접수 시 입력을 다시 확인합니다." };
  return { title: "실행 연결 설정됨 · 엔진 확인 필요", description: "‘저장 개정 실행 확인’으로 실제 CARLA 엔진 연결을 점검하세요." };
}
