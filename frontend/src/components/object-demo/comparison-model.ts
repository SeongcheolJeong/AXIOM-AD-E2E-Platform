import type { ObjectExecution } from "../object-scene/object-scene.ts";

export type ComparisonSide = {
  execution: ObjectExecution & {
    error?: string | null;
    summary?: unknown;
    outcome_uncertain?: boolean;
  };
  scenario: Record<string, unknown> | null;
  scenario_ref: {
    id: string;
    revision: number;
    configuration_hash: string;
    manifest_hash: string;
  } | null;
  metrics: unknown;
};
export type ObjectComparisonData = {
  schema_version: "object-sim-comparison/1";
  project_id: string;
  left: ComparisonSide;
  right: ComparisonSide;
  compatibility: { compatible: boolean; reasons: string[] };
  changes: { path: string; left: unknown; right: unknown }[];
  metrics: {
    path: string;
    left: number | boolean | null;
    right: number | boolean | null;
    delta: number | null;
  }[];
  evaluation_status: "existing_execution_verdicts_only";
};

export function canShowCityExecution(execution: ObjectExecution): boolean {
  return (
    execution.adapter_id === "carla-city-object" &&
    !!execution.manifest_hash &&
    execution.artifacts.some((artifact) => artifact.name === "city-scene.json")
  );
}

export function executionStatusLabel(status: string): string {
  return (
    (
      {
        completed: "실행 완료",
        failed: "실행 실패",
        cancelled: "실행 취소",
        canceled: "실행 취소",
        running: "실행 중",
        queued: "대기 중",
        pending: "준비 중",
      } as Record<string, string>
    )[status] ?? status
  );
}

export function comparisonValue(value: unknown): string {
  if (value === null || value === undefined) return "기록 없음";
  if (typeof value === "boolean") return value ? "예" : "아니요";
  if (typeof value === "number")
    return Number.isInteger(value) ? String(value) : value.toFixed(3);
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

const labels: Record<string, string> = {
  speed_mps: "설정 속도 (m/s)",
  target_speed_mps: "목표 속도 (m/s)",
  initial_speed_mps: "초기 속도 (m/s)",
  duration_s: "실행 시간 (s)",
  lane_change_allowed: "차로 변경 허용",
  allow_lane_change: "차로 변경 허용",
  allow_lane_changes: "차로 변경 허용",
  maximum_speed_mps: "최대 속도 (m/s)",
  lane_change_enabled: "차로 변경 허용",
  x: "위치 X (m)",
  y: "위치 Y (m)",
  z: "위치 Z (m)",
  heading_rad: "방향 (rad)",
  distance_travelled_m: "이동 거리 (m)",
  final_speed_mps: "최종 속도 (m/s)",
  goal_error_m: "최종 목표 거리 (m)",
  lateral_error_p95_m: "경로 오차 P95 (m)",
  native_contact: "Native 접촉 관측",
  native_contact_count: "Native 접촉 관측 수",
  seed: "Seed",
  route: "경로",
};
export function comparisonFieldLabel(path: string): string {
  if (/\/behavior\/actions\/[^/]+\/duration_s$/.test(path)) return "행동 유지 시간 (s)";
  if (/\/behavior\/actions(?:\/[^/]+)?$/.test(path)) return "설정한 행동";
  const last = path.split(/[./]/).at(-1) ?? path;
  return labels[last] ?? last.replaceAll("_", " ");
}

/** Summaries describe authored settings; exact values remain in the closed diff. */
export function comparisonSettingValue(path: string, value: unknown): string {
  if (/\/route$/.test(path) && Array.isArray(value)) {
    if (!value.length) return "경유점 없음";
    const point = (raw: unknown) => {
      if (!raw || typeof raw !== "object") return null;
      const item = raw as Record<string, unknown>;
      return typeof item.x === "number" && Number.isFinite(item.x) && typeof item.y === "number" && Number.isFinite(item.y)
        ? `(${item.x.toFixed(1)}, ${item.y.toFixed(1)})` : null;
    };
    const first = point(value[0]), last = point(value.at(-1));
    return `경유점 ${value.length}개${first && last ? ` · ${first} → ${last} m` : ""}`;
  }
  const compactValue = () => Array.isArray(value) ? `항목 ${value.length}개`
    : value && typeof value === "object" ? `설정 ${Object.keys(value).length}개` : comparisonValue(value);
  if (!/\/behavior\/actions(?:\/[^/]+)?$/.test(path)) return compactValue();
  const actions = Array.isArray(value) ? value : value === null ? [] : [value];
  if (!actions.length) return "추가 행동 없음";
  const waits = actions.map(action => {
    if (!action || typeof action !== "object") return null;
    const item = action as Record<string, unknown>;
    return item.kind === "wait" && typeof item.duration_s === "number" && Number.isFinite(item.duration_s)
      ? `대기 ${comparisonValue(item.duration_s)}초` : null;
  });
  return waits.every(Boolean) ? waits.slice(0, 3).join(" · ") + (waits.length > 3 ? ` · 외 ${waits.length - 3}개` : "") : compactValue();
}

/** Unavailable and failed sides remain visible; only active jobs are polled. */
export function comparisonNeedsPolling(data: ObjectComparisonData): boolean {
  return [data.left, data.right].some(({ execution }) =>
    ["queued", "pending", "running"].includes(execution.status),
  );
}
