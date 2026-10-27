import type { CityPose } from "./city-contract.ts";
import type { CityState } from "./city-scene.ts";
export type PlannerEvent = {
  time_s: number;
  actor_id: string;
  type: string;
  reason: string;
  action_id: string | null;
  route_revision: number;
  index: number;
  input_frame: number;
  provenance: string;
};
export type ReactiveDecision = {
  state: string;
  reason: string;
  route_revision: number;
  action_id: string | null;
  diagnostics: Record<string, unknown>;
  speed_mps: number;
  stop: boolean;
  planner_version?: string;
};
const labels: Record<string, string> = {
  route_follow: "경로 추종",
  decision_changed: "판단 전환",
  arrived: "목적지 도착",
  cruise: "경로 추종",
  gap_wait: "차로 변경 간격 대기",
  lead_headway: "선행차 간격 유지",
  lead_follow: "선행차 추종",
  signal_wait: "신호 대기",
  yield_conflict: "교차로 우선권 양보",
  yield_target: "대상 객체에 양보",
  crossing_conflict: "교차 이동 충돌 예측",
  predicted_crossing_conflict: "교차 이동 충돌 예측",
  footprint_emergency_hold: "차량 외곽 안전 간격 정지",
  deadlock_no_legal_progress: "교착 감지 · 합법적 진행 없음",
  blocked_route: "경로 폐쇄",
  obstacle_bypass: "장애물 우회",
  overtake: "추월",
  return_to_route: "원래 경로 복귀",
  route_replanned: "경로 재계획",
  action_started: "행동 시작",
  action_completed: "행동 완료",
  action_timeout: "행동 시간 초과",
  stop: "정지",
  wait: "대기",
  emergency_brake: "급제동",
  held_after_stop: "정지 유지",
  goal_reached: "목적지 도착",
  no_legal_route: "합법적 대체 경로 없음",
  lane_change: "차로 변경",
  selected_branch: "선택 분기로 재계획",
  automatic_bypass: "자동 장애물 우회",
  automatic_obstacle_bypass: "장애물을 피해 우회",
  automatic_bypass_rejoined: "우회 후 원래 경로 복귀",
  follow_gap: "선행차 간격에 맞춘 속도 조절",
  follow_lead_stop: "앞차 정지에 맞춰 대기",
  active_lane_gap_wait: "진입 공간이 열릴 때까지 대기",
  committed_merge_clearance: "확인한 후방 간격으로 천천히 진입",
  lane_changes_disabled: "차로 변경 제한으로 현재 차로 유지",
  closed_lane_reroute: "폐쇄 구간을 피해 경로 변경",
  stop_sign_wait: "정지 표지 앞 대기",
  yield_sign_wait: "양보 표지 앞 대기",
  conflict_clear: "교차 구간이 비어 진행",
  yield_target_clear: "양보 대상이 지나가 진행",
  action_duration_complete: "설정한 행동 시간 완료",
  no_legal_adjacent_lane: "이동할 수 있는 옆 차로 없음",
  candidate_hits_observed_obstacle: "우회 경로에 관측된 장애물",
  destination_reached: "목적지 도착",
};
export function decisionLabel(value: string): string {
  return labels[value] ?? value.replaceAll("_", " ");
}
export function reactiveDecision(
  state: CityState | undefined,
): ReactiveDecision | null {
  const raw = state?.plan?.reactive;
  if (!raw || typeof raw !== "object") return null;
  return raw as ReactiveDecision;
}

/** Highlight only the lead explicitly named by a current retained following/hold decision. */
export function followGapTarget(state: CityState, actors: CityState[]): {
  target: CityState; gapM: number; leadSpeedMps: number;
} | null {
  const decision = reactiveDecision(state);
  const raw = decision?.diagnostics?.lead;
  if (!decision || !["follow_gap", "follow_lead_stop"].includes(decision.reason) || decision.diagnostics.observation_basis !== "current_snapshot_ground_truth" || !raw || typeof raw !== "object") return null;
  const lead = raw as Record<string, unknown>;
  const target = actors.find(actor => actor.id === lead.actor_id && actor.id !== state.id);
  if (!target || typeof lead.gap_m !== "number" || !Number.isFinite(lead.gap_m) ||
      typeof lead.speed_mps !== "number" || !Number.isFinite(lead.speed_mps)) return null;
  return { target, gapM: lead.gap_m, leadSpeedMps: lead.speed_mps };
}
/** Reactive route revisions remain active until replaced; external model samples do not. */
export function currentPlan(
  series: { index: number; points: CityPose[]; route_revision?: number }[],
  index: number,
  reactive: boolean,
) {
  return reactive
    ? series.filter((s) => s.index <= index).at(-1)
    : series.find((s) => s.index === index);
}
export function decisionRelations(
  state: CityState,
  actors: CityState[],
): { from: CityState; to: CityState; label: string; gapM?: number; requiredGapM?: number }[] {
  const decision = reactiveDecision(state);
  if (!decision) return [];
  const d = decision.diagnostics,
    relations: { id: string; label: string; gapM?: number; requiredGapM?: number }[] = [];
  const add = (value: unknown, label: string) => {
    if (!value || typeof value !== "object") return;
    const r = value as Record<string, unknown>;
    if (typeof r.actor_id !== "string") return;
    const gap =
      typeof r.bumper_gap_m === "number"
        ? r.bumper_gap_m
        : typeof r.gap_m === "number"
          ? r.gap_m
          : undefined;
    relations.push({
      id: r.actor_id,
      label: gap === undefined ? label : `${label} ${gap.toFixed(1)}m`,
      gapM: gap,
      requiredGapM: typeof r.required_gap_m === "number" ? r.required_gap_m : undefined,
    });
  };
  add(d.lead, "선행 간격");
  for (const key of ["conflicts", "predicted_conflicts", "gap_checks"])
    if (Array.isArray(d[key]))
      for (const entry of d[key])
        add(entry, key === "gap_checks" ? "합류 간격" : "양보 대상");
  for (const key of ["active_lateral_gap", "automatic_bypass"])
    if (d[key] && typeof d[key] === "object") {
      const nested = d[key] as Record<string, unknown>;
      if (Array.isArray(nested.gap_checks)) for (const entry of nested.gap_checks) add(entry, "진입 간격");
    }
  for (const key of ["yield_actor_id", "emergency_actor_id"])
    if (typeof d[key] === "string")
      relations.push({ id: d[key], label: "대기 대상" });
  return relations
    .filter((r, i, a) => a.findIndex((v) => v.id === r.id) === i)
    .flatMap((r) => {
      const to = actors.find((a) => a.id === r.id);
      return to ? [{ from: state, to, label: r.label, gapM: r.gapM, requiredGapM: r.requiredGapM }] : [];
    });
}
