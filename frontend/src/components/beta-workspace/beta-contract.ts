import { api, authHeaders } from "../../api";

export type BetaModule = "object" | "log" | "hil" | "sensor" | "neural";
export const moduleNames: Record<BetaModule, string> = {
  object: "Object Sim", log: "Log Sim", hil: "Software-DUT HIL",
  sensor: "Sensor Sim", neural: "Neural Sim",
};
export type BetaMode = "new_execution" | "retained_playback";
export { sensorGuides, betaSensorNames, sameBetaRef, matchingBetaObservation, betaObservationRequest } from "./beta-evidence";
import type { BetaSensorKey, SensorGuide } from "./beta-evidence";
export type { BetaSensorKey, SensorGuide } from "./beta-evidence";
export type BetaRef = { id: string; revision: number; [key: string]: unknown };
export type BetaTask = {
  id: string; revision: number; project_id: string; module: BetaModule; title: string;
  mode: BetaMode; participant: string; participant_id: string; status: string; stage: string;
  source_ref: BetaRef | null; execution_id: string | null; observation_id: string | null;
  result_opened: boolean; time_s: number | null; resume_url: string; created_at: string;
  updated_at: string; steps: { id: string; label: string; complete: boolean; basis: string }[];
  guide_id?: SensorGuide | null; sensor_key?: BetaSensorKey | null; duration_s?: number | null;
  execution_status: string | null; execution_reason?: string | null; completion_basis: string | null; origin: "common" | "log_guided";
};
export type BetaEvidence = {
  kind: "saved_revision" | "execution" | "observation" | "pack";
  id: string; label: string; source_ref?: BetaRef; execution_id?: string;
  observation_id?: string; duration_s?: number; status?: string; created_at?: string;
  sensor_key?: BetaSensorKey; time_s?: number;
};
export type BetaReadiness = {
  project_id: string; checked_at: string; items: {
    module: BetaModule; title: string; guided: boolean; entry_url: string;
    authoring: { status: string; reason: string };
    retained_playback: { status: string; reason: string };
    new_execution: { status: string; reason: string };
  }[];
};
export type BetaFeedback = {
  id: string; revision: number; project_id: string; module: BetaModule;
  task_id: string | null; participant: string; kind: "issue" | "help" | "completion";
  message: string; stage: string; error: string | null; time_s: number | null;
  status: "received" | "investigating" | "resolved"; triage_note: string;
  created_at: string; updated_at: string; origin?: string;
  context: { task_revision?: number; source_ref?: BetaRef; execution_id?: string; observation_id?: string; resume_url?: string; execution_status?: string };
};
export type BetaAccess = {
  mode: string; role: string; user_label: string; principal_id: string;
  project_id?: string; permissions?: { execute?: boolean; admin?: boolean };
};
export const betaQuery = (project: string) => new URLSearchParams({ project_id: project });
export const betaError = (error: unknown) => error instanceof Error ? error.message : String(error);
export const betaDate = (value: string) => new Date(value).toLocaleString("ko-KR", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
export const taskStatus: Record<string, string> = { active: "진행 중", blocked: "확인 필요", completed: "완료" };
export const feedbackStatus = { received: "접수", investigating: "조사 중", resolved: "해결" };
export const writableBeta = (access: BetaAccess | null) => !!access && (access.mode !== "project" || ["Engineer", "Admin"].includes(access.role));

/** Only open the API's project-scoped product links, never an arbitrary URL. */
export function betaLink(value: string | null | undefined, project: string): string | null {
  if (!value || !/^#(?:workspace|object-sim|log-sim|hil-sim|sensor-sim|neural-sim|scenario-v2|viewers)\?/.test(value)) return null;
  const query = new URLSearchParams(value.split("?")[1]);
  return query.get("project") === project ? value : null;
}
export function taskLink(task: BetaTask): string | null {
  const link = betaLink(task.resume_url, task.project_id);
  if (!link) return null;
  const [page, raw] = link.split("?");
  const query = new URLSearchParams(raw); query.set("beta_task", task.id);
  return `${page}?${query}`;
}
export function rememberBetaTask(task: BetaTask, principal: string) {
  try { sessionStorage.setItem(`axiom.beta.active:${principal}:${task.project_id}`, task.id); } catch { /* links remain usable without browser storage */ }
}
export function rememberedBetaTask(project: string, principal: string) {
  try { return sessionStorage.getItem(`axiom.beta.active:${principal}:${project}`); } catch { return null; }
}
/** Reuse the same key after a lost response; a changed payload is a new write. */
export function betaWriteKey(scope: string, body: string) {
  const storage = `axiom.beta.write:${scope}`;
  try {
    const prior = JSON.parse(sessionStorage.getItem(storage) ?? "null");
    if (prior?.body === body && typeof prior.key === "string") return prior.key;
  } catch { /* no valid pending attempt */ }
  const key = crypto.randomUUID();
  try { sessionStorage.setItem(storage, JSON.stringify({ body, key })); } catch { /* optional recovery cache */ }
  return key;
}
export async function betaWrite<T>(path: string, scope: string, value: unknown) {
  const body = JSON.stringify(value);
  const result = await api<T>(path, { method: "POST", body, headers: { "Idempotency-Key": betaWriteKey(scope, body) } });
  try { sessionStorage.removeItem(`axiom.beta.write:${scope}`); } catch { /* optional recovery cache */ }
  return result;
}
export async function downloadBetaOverview(format: "json" | "csv") {
  const response = await fetch(`/api/v1/beta/overview?format=${format}`, { headers: authHeaders() });
  if (!response.ok) {
    let detail = `내보내기 실패 (${response.status})`;
    try { detail = (await response.json()).detail ?? detail; } catch { /* HTTP fallback */ }
    throw new Error(detail);
  }
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a"); link.href = url; link.download = `adp-beta-overview.${format}`;
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
