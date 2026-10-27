/** Authored straight-road editor geometry. These helpers never predict native motion. */
export type Pose = { x: number; y: number; heading_rad: number };
export type TimedPose = Pose & { time_s: number };
export type Trigger = {
  kind: "time" | "distance" | "object_state";
  value: number;
  actor_id: string | null;
  target_actor_id: string | null;
  rule: "greaterThan" | "lessThan";
  state: "speed";
};
export type Action = {
  kind: "speed" | "stop" | "lane_change" | "follow_path";
  trigger: Trigger;
  target_speed_mps: number | null;
  target_lane: number | null;
  transition_s: number;
  path: TimedPose[];
};
export type Actor = {
  id: string;
  kind: "vehicle" | "pedestrian";
  role: "ego" | "other";
  pose: Pose;
  dimensions: { length_m: number; width_m: number; height_m: number };
  speed_mps: number;
  route: Pose[];
  actions: Action[];
};
export type Scenario = {
  schema_version: "scenario-v2/1";
  name: string;
  template:
    | "lane_follow"
    | "cut_in"
    | "emergency_brake"
    | "pedestrian_crossing"
    | "merge"
    | "lead_stop"
    | "traffic_interaction"
    | null;
  map: {
    kind: "authored_straight_road";
    id: string;
    length_m: number;
    lane_width_m: number;
  };
  coordinate_system: string;
  duration_s: number;
  timestep_s: number;
  actors: Actor[];
  environment: { lighting: "day"; weather: "dry" };
  observers: {
    kind: string;
    actor_id: string;
    threshold: number | null;
    goal: Pose | null;
  }[];
  sweep: { actor_id: string; field: string; values: number[] }[];
};

export const laneCenter = (lane: -1 | -2, width: number) =>
  (lane + 0.5) * width;
export function startingLane(y: number, width: number): -1 | -2 | null {
  if (
    !Number.isFinite(y) ||
    !Number.isFinite(width) ||
    width <= 0 ||
    y > 0 ||
    y < -2 * width
  )
    return null;
  return y > -width ? -1 : -2;
}
export function actorColor(id: string): string {
  let hash = 2166136261;
  for (let index = 0; index < id.length; index++)
    hash = Math.imul(hash ^ id.charCodeAt(index), 16777619);
  const hue = (hash >>> 0) % 360,
    lightness = 0.43,
    saturation = 0.5 + ((hash >>> 16) % 20) / 100;
  const amplitude = saturation * Math.min(lightness, 1 - lightness);
  const channel = (n: number) => {
    const k = (n + hue / 30) % 12;
    return Math.round(
      255 * (lightness - amplitude * Math.max(-1, Math.min(k - 3, 9 - k, 1))),
    )
      .toString(16)
      .padStart(2, "0");
  };
  return `#${channel(0)}${channel(8)}${channel(4)}`;
}
export function createActor(
  kind: Actor["kind"],
  actors: Actor[],
  map: Scenario["map"],
  duration: number,
): Actor {
  let ordinal = 1;
  while (actors.some((actor) => actor.id === `${kind}_${ordinal}`)) ordinal++;
  const x = Math.min(map.length_m - 5, 20 + actors.length * 8),
    y =
      kind === "vehicle"
        ? laneCenter(-2, map.lane_width_m)
        : -2 * map.lane_width_m - 0.8;
  const actor: Actor = {
    id: `${kind}_${ordinal}`,
    kind,
    role: "other",
    pose: { x, y, heading_rad: kind === "vehicle" ? 0 : Math.PI / 2 },
    dimensions:
      kind === "vehicle"
        ? { length_m: 4.5, width_m: 1.8, height_m: 1.5 }
        : { length_m: 0.5, width_m: 0.5, height_m: 1.75 },
    speed_mps: kind === "vehicle" ? 8 : 0,
    route: [],
    actions: [],
  };
  if (kind === "pedestrian")
    actor.actions.push(defaultAction("follow_path", actor, map, duration));
  return actor;
}
export function defaultAction(
  kind: Action["kind"],
  actor: Actor,
  map: Scenario["map"],
  duration: number,
): Action {
  const start = Math.min(2, duration / 3),
    end = Math.min(duration, start + Math.max(0.1, duration / 2));
  const destination =
    actor.kind === "pedestrian"
      ? {
          ...actor.pose,
          y: actor.pose.y + map.lane_width_m * 2 + 1.6,
          heading_rad: Math.PI / 2,
        }
      : {
          ...actor.pose,
          x: actor.pose.x + Math.max(5, actor.speed_mps * (end - start)),
        };
  return {
    kind,
    trigger: {
      kind: "time",
      value: start,
      actor_id: null,
      target_actor_id: null,
      rule: "greaterThan",
      state: "speed",
    },
    transition_s: 2,
    target_speed_mps: kind === "speed" ? 5 : null,
    target_lane:
      kind === "lane_change"
        ? startingLane(actor.pose.y, map.lane_width_m) === -1
          ? -2
          : -1
        : null,
    path:
      kind === "follow_path"
        ? [
            { ...actor.pose, time_s: start },
            { ...destination, time_s: end },
          ]
        : [],
  };
}
export function appendWaypoint(
  points: Pose[] | TimedPose[],
  pose: Pose,
  duration?: number,
): Pose | TimedPose {
  const prior = points.at(-1),
    point = { ...pose };
  if (prior && (prior.x !== pose.x || prior.y !== pose.y))
    point.heading_rad = Math.atan2(pose.y - prior.y, pose.x - prior.x);
  if (duration === undefined) return point;
  const previousTime =
    prior && "time_s" in prior && typeof prior.time_s === "number"
      ? prior.time_s
      : -1;
  if (previousTime >= duration)
    throw new Error(
      "마지막 waypoint가 종료 시각입니다. 시간을 앞당긴 뒤 점을 추가하세요.",
    );
  return {
    ...point,
    time_s: previousTime < 0 ? 0 : Math.min(duration, previousTime + 1),
  };
}
export function actorReferences(scenario: Scenario, id: string): number {
  return (
    scenario.actors
      .filter((actor) => actor.id !== id)
      .flatMap((actor) => actor.actions)
      .filter(
        (action) =>
          action.trigger.actor_id === id ||
          action.trigger.target_actor_id === id,
      ).length +
    scenario.observers.filter((observer) => observer.actor_id === id).length +
    scenario.sweep.filter((axis) => axis.actor_id === id).length
  );
}
export function laneChangeWarnings(scenario: Scenario): string[] {
  const warnings: string[] = [];
  for (const actor of scenario.actors) {
    const lane = startingLane(actor.pose.y, scenario.map.lane_width_m);
    if (lane === null || actor.route.length) continue;
    const lateral = actor.actions.filter(
      (action) =>
        action.kind === "lane_change" || action.kind === "follow_path",
    );
    for (const action of lateral) {
      if (action.kind !== "lane_change" || action.target_lane !== lane)
        continue;
      // An earlier or uncertain lateral trigger may legitimately change the starting lane.
      const possiblyEarlier = lateral.some(
        (other) =>
          other !== action &&
          !(
            other.trigger.kind === "time" &&
            other.trigger.rule === "greaterThan" &&
            action.trigger.kind === "time" &&
            action.trigger.rule === "greaterThan" &&
            other.trigger.value > action.trigger.value
          ),
      );
      if (!possiblyEarlier)
        warnings.push(
          `${actor.id}: 차로 변경 목표 ${lane}이 시작 차로와 같습니다. 횡방향 이동이 발생하지 않을 수 있습니다. 시작 위치 또는 목표 차로를 확인하세요.`,
        );
    }
  }
  return warnings;
}
export function waypointIssue(
  points: Pose[] | TimedPose[],
  duration?: number,
): string | null {
  if (
    points.some(
      (point) =>
        ![point.x, point.y, point.heading_rad].every(Number.isFinite) ||
        Math.abs(point.x) > 10000 ||
        Math.abs(point.y) > 10000 ||
        Math.abs(point.heading_rad) > 6.284,
    )
  )
    return "좌표·heading은 유한한 계약 범위 값이어야 합니다.";
  if (duration !== undefined) {
    const timed = points as TimedPose[];
    if (timed.length < 2) return "시간 지정 경로는 점이 2개 이상 필요합니다.";
    if (
      timed.some(
        (point, index) =>
          !Number.isFinite(point.time_s) ||
          point.time_s < 0 ||
          point.time_s > duration ||
          (index > 0 && point.time_s <= timed[index - 1].time_s),
      )
    )
      return "절대 시각은 0부터 장면 종료 시각 안에서 엄격히 증가해야 합니다.";
  }
  return null;
}
