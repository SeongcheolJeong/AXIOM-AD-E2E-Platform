/** Versioned city authoring data. The server is the validation authority. */
export type CityPose = { x: number; y: number; z: number; heading_rad: number };
export type CatalogRef = { id: string; revision: number; sha256: string };
export type CatalogItem<T = Record<string, unknown>> = CatalogRef & {
  project_id: string;
  definition: T;
};
export type CityLane = {
  id: string;
  points: CityPose[];
  width_m?: number;
  successors?: string[];
};
export type CityMap = Record<string, unknown> & {
  name?: string;
  native_name?: string;
  lanes: CityLane[];
  spawn_points?: CityPose[];
  signals?: { id: string; pose: CityPose; kind: string }[];
  nodes?: (CityPose & {
    id: string;
    road_id: number;
    lane_id: number;
    closed?: boolean;
  })[];
};
export type CityMapRecord = CatalogItem<CityMap> & { lanes: CityLane[] };
export type CityAsset = Record<string, unknown> & {
  name?: string;
  actor_kind: "vehicle" | "pedestrian" | "static";
  blueprint_id?: string;
};
export type CityControllerKind =
  | "route_follow"
  | "traffic"
  | "autoware"
  | "timed_replay"
  | "static"
  | "reactive";
export type BehaviorTrigger = {
  kind:
    | "time"
    | "distance"
    | "relative_speed"
    | "ttc"
    | "region"
    | "signal"
    | "action_complete";
  value: number;
  operator: "lte" | "gte";
  target_actor_id?: string | null;
  region?: { x: number; y: number; radius_m: number } | null;
  signal_id?: string | null;
  signal_state?: "red" | "yellow" | "green" | null;
  action_id?: string | null;
};
export type BehaviorAction = {
  id: string;
  kind: string;
  trigger: BehaviorTrigger;
  speed_mps?: number | null;
  duration_s: number;
  target_actor_id?: string | null;
  target_lane_node_id?: string | null;
  timeout_s: number;
  on_timeout: "stop" | "skip" | "fail";
};
export type BehaviorPlan = {
  schema_version: "object-behavior/1";
  risk_mode: boolean;
  actions: BehaviorAction[];
};
export type PlannerSettings = {
  schema_version: "object-planner/1";
  headway_s?: number;
  min_gap_m?: number;
  max_deceleration_mps2?: number;
  max_lateral_acceleration_mps2?: number;
  max_curvature_inv_m?: number;
  allow_lane_changes?: boolean;
  allow_reroute?: boolean;
  obey_signals?: boolean;
  deadlock_timeout_s?: number;
  closed_node_ids?: string[];
};
export type BehaviorCatalog = {
  schema_version: string;
  planner_version: string;
  fidelity: string;
  behaviors: {
    id: string;
    name: string;
    description: string;
    risk: boolean;
    requires_target_lane: boolean;
    actor_kinds: string[];
  }[];
  trigger_kinds: string[];
  limits: Record<string, unknown>;
};
export type CityActor = {
  id: string;
  kind: CityAsset["actor_kind"];
  role: "ego" | "other";
  asset: CatalogRef;
  pose: CityPose;
  route: CityPose[];
  destination?: CityPose | null;
  route_mode?: "native_lane_graph" | "authored_lane_change";
  speed_mps: number;
  controller: {
    kind: CityControllerKind;
    ref: CatalogRef | null;
    end_behavior: "stop" | "repeat";
    headway_s: number;
    obey_signals: boolean;
    wait_s?: number;
    stop_time_s?: number | null;
    required_gap_m?: number;
  };
  trajectory: (CityPose & { time_s: number })[];
  behavior?: BehaviorPlan;
  planner?: PlannerSettings;
};
export type CityScenario = {
  schema_version: "scenario-v2/2" | "scenario-v2/3";
  name: string;
  description: string;
  seed: number;
  duration_s: number;
  timestep_s: number;
  coordinate_system: "carla_world_m_x_forward_y_right_z_up";
  map: CatalogRef;
  actors: CityActor[];
  sensors: Record<string, unknown>[];
  signals?: {
    id: string;
    initial_state: "red" | "yellow" | "green";
    changes: { time_s: number; state: "red" | "yellow" | "green" }[];
  }[];
  acceptance?: {
    max_lateral_error_p95_m: number;
    max_goal_error_m: number;
    max_final_speed_mps: number;
    require_goal: boolean;
    require_no_contacts: boolean;
  };
  observers: Record<string, unknown>[];
  environment: { lighting: "day"; weather: "dry" };
};
export type CityRecord = {
  id: string;
  project_id: string;
  revision: number;
  scenario: CityScenario;
  manifest_hash: string;
  configuration_hash?: string;
};
export type CityValidation = {
  scenario: CityScenario;
  configuration_hash: string;
  canonical_yaml: string;
  catalog_validation: unknown;
};
export function catalogRef(item: CatalogRef): CatalogRef {
  return { id: item.id, revision: item.revision, sha256: item.sha256 };
}
/** Saved scenario and map are the complete resume identity; stale run/source params are omitted. */
export function savedCityScenarioRoute(record: CityRecord): string {
  return `#object-sim?${new URLSearchParams({ project: record.project_id, mode: "city", scenario: record.id,
    revision: String(record.revision), map: record.scenario.map.id,
    map_revision: String(record.scenario.map.revision), map_hash: record.scenario.map.sha256 })}`;
}
export function lanePoints(map: CityMap): CityPose[] {
  return map.lanes.flatMap((lane) => lane.points);
}
export function closestLanePoint(
  map: CityMap,
  point: Pick<CityPose, "x" | "y">,
  maxDistance = 5,
): CityPose | null {
  let best: CityPose | null = null,
    distance = maxDistance;
  for (const candidate of lanePoints(map)) {
    const d = Math.hypot(candidate.x - point.x, candidate.y - point.y);
    if (d < distance) {
      distance = d;
      best = candidate;
    }
  }
  return best ? { ...best } : null;
}
export function nextActorId(
  actors: CityActor[],
  kind: CityAsset["actor_kind"],
): string {
  let n = 1;
  while (actors.some((actor) => actor.id === `${kind}_${n}`)) n++;
  return `${kind}_${n}`;
}
export function newCityActor(
  asset: CatalogItem<CityAsset>,
  pose: CityPose,
  actors: CityActor[],
): CityActor {
  return {
    id: actors.length
      ? nextActorId(actors, asset.definition.actor_kind)
      : "ego",
    kind: asset.definition.actor_kind,
    role: actors.length ? "other" : "ego",
    asset: catalogRef(asset),
    pose: { ...pose },
    route: [],
    speed_mps:
      asset.definition.actor_kind === "static"
        ? 0
        : asset.definition.actor_kind === "pedestrian"
          ? 1.2
          : 3,
    controller: {
      kind:
        asset.definition.actor_kind === "static" ? "static" : "route_follow",
      ref: null,
      end_behavior: "stop",
      headway_s: 2,
      obey_signals: true,
    },
    trajectory: [],
  };
}
export function newCityScenario(
  map: CatalogItem<CityMap>,
  asset: CatalogItem<CityAsset>,
): CityScenario {
  const first =
    map.definition.spawn_points?.[0] ?? lanePoints(map.definition)[0];
  if (!first) throw new Error("등록 지도에 배치할 차로 좌표가 없습니다.");
  return {
    schema_version: "scenario-v2/2",
    name: `${map.definition.name ?? map.id} · 경로 추종`,
    description: "",
    seed: 42,
    duration_s: 20,
    timestep_s: 0.05,
    coordinate_system: "carla_world_m_x_forward_y_right_z_up",
    map: catalogRef(map),
    actors: [newCityActor(asset, first, [])],
    sensors: [],
    observers: [],
    environment: { lighting: "day", weather: "dry" },
  };
}
export function changedFields(
  previous: unknown,
  next: unknown,
  path = "scenario",
): string[] {
  if (sameCityConfiguration(previous, next)) return [];
  if (
    previous &&
    next &&
    typeof previous === "object" &&
    typeof next === "object" &&
    !Array.isArray(previous) &&
    !Array.isArray(next)
  ) {
    const left = previous as Record<string, unknown>,
      right = next as Record<string, unknown>;
    return [...new Set([...Object.keys(left), ...Object.keys(right)])].flatMap(
      (key) => changedFields(left[key], right[key], `${path}.${key}`),
    );
  }
  return [path];
}

/** UI equality only. Server canonicalization remains the configuration hash authority. */
export function sameCityConfiguration(
  previous: unknown,
  next: unknown,
): boolean {
  if (Object.is(previous, next)) return true;
  if (Array.isArray(previous) || Array.isArray(next))
    return (
      Array.isArray(previous) &&
      Array.isArray(next) &&
      previous.length === next.length &&
      previous.every((value, index) =>
        sameCityConfiguration(value, next[index]),
      )
    );
  if (
    previous &&
    next &&
    typeof previous === "object" &&
    typeof next === "object"
  ) {
    const left = previous as Record<string, unknown>,
      right = next as Record<string, unknown>,
      leftKeys = Object.keys(left).sort(),
      rightKeys = Object.keys(right).sort();
    return (
      leftKeys.length === rightKeys.length &&
      leftKeys.every(
        (key, index) =>
          key === rightKeys[index] &&
          sameCityConfiguration(left[key], right[key]),
      )
    );
  }
  return false;
}

/** Adds a distinct sensor; existing IDs/configurations are never repurposed. */
export function newCityRgbSensor(
  scenario: CityScenario,
): Record<string, unknown> {
  const ego = scenario.actors.find((actor) => actor.role === "ego");
  if (!ego) throw new Error("Ego 객체가 있어야 카메라를 추가할 수 있습니다.");
  let id = "ego_front_rgb",
    suffix = 2;
  while (scenario.sensors.some((sensor) => sensor.id === id))
    id = `ego_front_rgb_${suffix++}`;
  return {
    id,
    actor_id: ego.id,
    kind: "camera_rgb",
    pose: { x: 1.5, y: 0, z: 1.7, heading_rad: 0 },
    width: 640,
    height: 360,
    fov_deg: 90,
    period_s: 0.1,
  };
}

/** Explicit draft upgrade: does not mutate a saved /2 revision or its controller mode. */
export function enableReactiveActor(
  scenario: CityScenario,
  actorId: string,
): CityScenario {
  const next = structuredClone(scenario);
  next.schema_version = "scenario-v2/3";
  for (const actor of next.actors) {
    actor.behavior ??= {
      schema_version: "object-behavior/1",
      risk_mode: false,
      actions: [],
    };
    actor.planner ??= { schema_version: "object-planner/1" };
  }
  const actor = next.actors.find((a) => a.id === actorId);
  if (!actor || actor.kind === "static")
    throw new Error("동적 객체를 선택하세요.");
  const prior = actor.controller;
  actor.planner = {
    schema_version: "object-planner/1",
    ...actor.planner,
    headway_s: prior.headway_s,
    obey_signals: prior.obey_signals,
  };
  if (prior.wait_s && prior.wait_s > 0)
    actor.behavior!.actions.push({
      ...newBehaviorAction(actor.behavior!.actions, "wait"),
      trigger: { kind: "time", value: 0, operator: "gte" },
      duration_s: prior.wait_s,
      timeout_s: Math.max(30, prior.wait_s),
    });
  if (prior.stop_time_s !== undefined && prior.stop_time_s !== null)
    actor.behavior!.actions.push({
      ...newBehaviorAction(actor.behavior!.actions, "stop"),
      trigger: { kind: "time", value: prior.stop_time_s, operator: "gte" },
    });
  actor.controller = {
    kind: "reactive",
    ref: null,
    end_behavior: "stop",
    headway_s: 2,
    obey_signals: true,
  };
  return next;
}
export function newBehaviorAction(
  actions: BehaviorAction[],
  kind: string,
): BehaviorAction {
  let n = 1;
  while (actions.some((a) => a.id === `action_${n}`)) n++;
  return {
    id: `action_${n}`,
    kind,
    trigger: actions.length
      ? {
          kind: "action_complete",
          value: 0,
          operator: "gte",
          action_id: actions.at(-1)!.id,
        }
      : { kind: "time", value: 0, operator: "gte" },
    duration_s: 0,
    timeout_s: 30,
    on_timeout: "stop",
    ...(kind === "target_speed" ? { speed_mps: 3 } : {}),
  };
}
