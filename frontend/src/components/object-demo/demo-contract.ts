import type { CityMapRecord, CityPose, CityRecord, CityScenario } from "../city-sim/city-contract.ts";

export type DemoParameters = Record<string, number | boolean>;
export type DemoPresentationLayout = "auto" | "native_camera" | "analysis";
export type DemoPreset = {
  id: string; version: number; title: string; description: string;
  ready: boolean; status: string; reason: string | null;
  parameters: { key: string; type: "number" | "boolean"; label: string; min?: number; max?: number; step?: number; default: number | boolean }[];
  defaults: DemoParameters; binding_revision: number | null;
  scenario_ref: { id: string; revision: number; configuration_hash: string; manifest_hash: string } | null;
  saved_execution_id: string | null;
  saved_comparison_execution_id?: string | null;
};
export type DemoPreview = {
  preset_id: string; project_id: string; binding_revision: number;
  scenario: CityScenario; parameters: DemoParameters; map: CityMapRecord;
  actor_roles: { ego: string; obstacle?: string; rear?: string; lead?: string };
  saved_execution_id: string | null; warnings: string[];
};
export type DemoSelection = { presetId: string; parameters: DemoParameters; actorPoses: Record<string, CityPose> };
export type DemoApi = <T>(path: string, init?: RequestInit) => Promise<T>;
export type DemoRunAttempt = {
  key: string; signature: string; saved?: CityRecord;
  prepared?: { preview_hash: string }; experiment?: { id: string; preview_hash: string };
  execution?: { id: string };
};
export const demoTerminal = (status: string) => ["completed", "failed", "cancelled", "interrupted", "rejected"].includes(status);
export const demoStatus = (status: string) => ({ queued: "실행 대기", claimed: "실행 준비", preparing: "실행 준비", running: "실행 중", completed: "결과 준비 완료", failed: "실행 실패", cancelled: "취소됨", cancelling: "취소 요청 중", interrupted: "실행 중단 · 결과 확인 필요", rejected: "실행 거절" } as Record<string, string>)[status] ?? status;

export function demoParameterUnit(key: string): string {
  if (key.endsWith("_mps")) return "m/s";
  if (key.endsWith("_s")) return "초";
  if (key.endsWith("_m")) return "m";
  return "";
}

/** The binding explicitly identifies both retained runs; never substitute another result. */
export function demoSavedPair(preset: DemoPreset): { left: string; right: string } | null {
  const left = preset.saved_comparison_execution_id, right = preset.saved_execution_id;
  return preset.ready && left && right && left !== right ? { left, right } : null;
}

/** Preserve the attempt and endpoint keys on uncertain responses. Retries cannot create a second run. */
export async function executeDemo(
  request: DemoApi, project: string, scenario: CityScenario,
  adapter: { id: string; version: string }, attempt: DemoRunAttempt,
  progress: (value: string) => void,
): Promise<{ id: string }> {
  const post = <T>(path: string, body: unknown, suffix?: string) => request<T>(path, {
    method: "POST", body: JSON.stringify(body),
    ...(suffix ? { headers: { "Idempotency-Key": `${attempt.key}-${suffix}` } } : {}),
  });
  if (attempt.execution) return attempt.execution;
  progress("시나리오 개정 저장");
  attempt.saved ??= await post<CityRecord>("/scenario-v2", { project_id: project, scenario }, "scenario");
  const saved = attempt.saved;
  if (!saved.configuration_hash) throw new Error("저장된 시나리오의 configuration hash가 없습니다.");
  const body = { project_id: project, name: saved.scenario.name, kind: "object_sim", adapter_id: adapter.id, adapter_version: adapter.version, seed: saved.scenario.seed, input: { scenario_id: saved.id, scenario_revision: saved.revision, configuration_hash: saved.configuration_hash } };
  progress("CARLA 실행 가능성 확인");
  attempt.prepared ??= await post<{ preview_hash: string }>("/experiments/preview", body);
  attempt.experiment ??= await post<{ id: string; preview_hash: string }>("/experiments", body, "experiment");
  if (attempt.prepared.preview_hash !== attempt.experiment.preview_hash) throw new Error("실행 설정이 바뀌었습니다. 새 변경안으로 다시 준비하세요.");
  progress("실행 큐에 제출");
  attempt.execution = await post<{ id: string }>(`/experiments/${attempt.experiment.id}/executions`, { preview_hash: attempt.prepared.preview_hash }, "execution");
  return attempt.execution;
}

/** Project onto an actual lane segment, retaining lane direction and native elevation. */
export function snapDemoPose(map: CityMapRecord["definition"], point: { x: number; y: number }, maxDistance = 6): CityPose | null {
  let best: CityPose | null = null, distance = maxDistance;
  for (const lane of map.lanes) {
    for (let i = 0; i < lane.points.length; i++) {
      const a = lane.points[i], b = lane.points[i + 1] ?? a;
      const dx = b.x - a.x, dy = b.y - a.y, length = dx * dx + dy * dy;
      const t = length ? Math.max(0, Math.min(1, ((point.x - a.x) * dx + (point.y - a.y) * dy) / length)) : 0;
      const pose = { x: a.x + dx * t, y: a.y + dy * t, z: a.z + (b.z - a.z) * t, heading_rad: length ? Math.atan2(dy, dx) : a.heading_rad };
      const candidateDistance = Math.hypot(point.x - pose.x, point.y - pose.y);
      if (candidateDistance <= distance) { distance = candidateDistance; best = pose; }
    }
  }
  return best;
}

export function demoChanges(preset: DemoPreset, parameters: DemoParameters, poses: Record<string, CityPose>, baselinePoses: Record<string, CityPose> = {}): string[] {
  return [
    ...preset.parameters.filter((item) => parameters[item.key] !== preset.defaults[item.key]).map((item) => `${item.label}: ${typeof parameters[item.key] === "boolean" ? parameters[item.key] ? "허용" : "제한" : `${parameters[item.key]} ${demoParameterUnit(item.key)}`.trim()}`),
    ...[...new Set([...Object.keys(poses), ...Object.keys(baselinePoses)])].filter((id) => JSON.stringify(poses[id]) !== JSON.stringify(baselinePoses[id])).map((id) => `${id} 시작 위치 ${poses[id] ? "변경" : "프리셋 위치로 복원"}`),
  ];
}
