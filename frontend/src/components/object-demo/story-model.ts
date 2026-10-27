import type { ComparisonSide } from "./comparison-model.ts";

export type StoryEvent = {
  side: "left" | "right";
  category: "wait" | "bypass" | "return" | "arrived" | "blocked" | "resume";
  actor_id: string;
  time_s: number;
  observation_time_s: number;
  type: string;
  reason: string;
  index: number;
  input_frame: number;
  provenance: string;
  decision_context?: {
    source: "retained_planner_diagnostics";
    frame_index: number;
    input_frame: number;
    target_actor_id: string;
    gap_m: number;
    lead_speed_mps: number;
    requested_speed_mps: number;
    stop: boolean;
  };
  previous_decision?: { reason: "follow_gap" | "follow_lead_stop"; index: number; time_s: number; input_frame: number };
};

/** This is an event's retained diagnostic, never a claim about the current RGB. */
export function storyDecisionSummary(event: StoryEvent): string | null {
  const context = event.decision_context;
  if (!context || context.source !== "retained_planner_diagnostics" ||
      context.frame_index !== event.index || context.input_frame !== event.input_frame ||
      typeof context.target_actor_id !== "string" || !context.target_actor_id ||
      context.target_actor_id === event.actor_id || typeof context.stop !== "boolean" ||
      ![context.gap_m, context.lead_speed_mps, context.requested_speed_mps, event.observation_time_s].every(Number.isFinite)) return null;
  return `${event.actor_id} → ${context.target_actor_id} · 간격 ${context.gap_m.toFixed(1)} m · 선행차 GT ${context.lead_speed_mps.toFixed(1)} m/s · ${context.stop ? "정지 요청" : `${context.requested_speed_mps.toFixed(1)} m/s 요청`} · 관측 ${event.observation_time_s.toFixed(2)}s`;
}
export type StorySegment = {
  id: string;
  start_time_s: number;
  end_time_s: number;
  anchor_time_s: number;
  hold_s: number;
  playback_rate: number;
  title: string;
  description: string;
  actor_ids: string[];
  events: StoryEvent[];
};
export type ObjectStory = {
  schema_version: "object-sim-story/1";
  rule_version: string;
  project_id: string;
  status: "available" | "unavailable";
  reason: string | null;
  manual_replay_available: boolean;
  clock: "shared_simulation_time_s";
  event_clock?: string;
  observation_clock?: string;
  left: ComparisonSide;
  right: ComparisonSide | null;
  sources: Record<
    string,
    {
      execution_id: string;
      manifest_hash: string;
      artifact_id: string;
      artifact_sha256: string;
      artifact_name: string;
      scenario_ref: unknown;
    } | null
  >;
  segments: StorySegment[];
  total_presentation_s: number;
  limits: {
    max_segments: number;
    max_total_s: number;
    pre_event_s: number;
    post_event_s: number;
    hold_s: number;
  };
  limitations: string[];
};

/** A malformed guide must not seek to invented time or run an unbounded presentation. */
export function storyProblem(story: ObjectStory): string | null {
  if (
    story.schema_version !== "object-sim-story/1" ||
    story.clock !== "shared_simulation_time_s"
  )
    return "지원하지 않는 시연 시간 계약입니다.";
  if (story.status !== "available") {
    const reason = story.reason ?? "";
    if (reason.startsWith("comparison_incompatible"))
      return "두 실행의 고정 조건이 일치하지 않아 자동 시연을 구성할 수 없습니다.";
    if (reason === "completed_sealed_execution_required")
      return "완료되고 봉인된 실행에서만 자동 시연을 구성할 수 있습니다.";
    if (
      reason.startsWith("original_") ||
      reason === "unsupported_original_city_scene"
    )
      return "원본 장면·사건의 근거를 확인할 수 없어 자동 시연을 사용할 수 없습니다.";
    if (!reason || reason === "no_representative_planner_events")
      return "관측된 대표 사건으로 구성할 시연 구간이 없습니다.";
    return reason;
  }
  if (!story.segments.length || story.segments.length > 5)
    return "시연 구간 수가 지원 범위를 벗어났습니다.";
  let total = 0;
  for (const segment of story.segments) {
    if (
      ![
        segment.start_time_s,
        segment.anchor_time_s,
        segment.end_time_s,
        segment.hold_s,
        segment.playback_rate,
      ].every(Number.isFinite) ||
      segment.start_time_s < 0 ||
      segment.anchor_time_s < segment.start_time_s ||
      segment.end_time_s < segment.anchor_time_s ||
      segment.hold_s !== 2 ||
      segment.playback_rate !== 1 ||
      !segment.events.length ||
      !segment.events.some(
        (event) => event.observation_time_s === segment.anchor_time_s,
      )
    )
      return "시연 구간이 보존된 사건 시각과 일치하지 않습니다.";
    total +=
      (segment.end_time_s - segment.start_time_s) / segment.playback_rate +
      segment.hold_s;
  }
  if (
    !Number.isFinite(story.total_presentation_s) ||
    total > 60.001 ||
    Math.abs(total - story.total_presentation_s) > 0.01
  )
    return "시연 재생 시간은 최대 60초입니다.";
  return null;
}

export type StoryController = {
  mode: "idle" | "guided" | "manual" | "completed";
  phase: "before" | "hold" | "after";
  segmentIndex: number;
  timeS: number;
  holdRemainingS: number;
};
export const initialStoryController: StoryController = {
  mode: "idle",
  phase: "before",
  segmentIndex: 0,
  timeS: 0,
  holdRemainingS: 0,
};

export function startStory(
  segments: StorySegment[],
  index = 0,
): StoryController {
  const segment = segments[index];
  if (!segment) return { ...initialStoryController };
  const holding = segment.start_time_s === segment.anchor_time_s;
  return {
    mode: "guided",
    phase: holding ? "hold" : "before",
    segmentIndex: index,
    timeS: segment.start_time_s,
    holdRemainingS: holding ? segment.hold_s : 0,
  };
}

export function interruptStory(
  state: StoryController,
  timeS: number,
): StoryController {
  return { ...state, mode: "manual", timeS, holdRemainingS: 0 };
}

/** Both native viewers report readiness for this exact shared time before advancing. */
export function advanceStory(
  state: StoryController,
  segments: StorySegment[],
  elapsedS: number,
  ready: boolean,
): StoryController {
  if (
    state.mode !== "guided" ||
    !ready ||
    !Number.isFinite(elapsedS) ||
    elapsedS <= 0
  )
    return state;
  const segment = segments[state.segmentIndex];
  if (!segment) return { ...state, mode: "completed" };
  if (state.phase === "hold") {
    const remaining = Math.max(0, state.holdRemainingS - elapsedS);
    return {
      ...state,
      holdRemainingS: remaining,
      phase: remaining <= 1e-6 ? "after" : "hold",
    };
  }
  const target =
    state.phase === "before" ? segment.anchor_time_s : segment.end_time_s;
  const timeS = Math.min(
    target,
    Math.round((state.timeS + elapsedS * segment.playback_rate) * 1e6) / 1e6,
  );
  if (timeS < target) return { ...state, timeS };
  if (state.phase === "before")
    return {
      ...state,
      timeS: target,
      phase: "hold",
      holdRemainingS: segment.hold_s,
    };
  return state.segmentIndex + 1 < segments.length
    ? startStory(segments, state.segmentIndex + 1)
    : { ...state, timeS: target, mode: "completed" };
}
