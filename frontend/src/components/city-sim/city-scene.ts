import { record, sameValue } from "../object-scene/object-scene.ts";
import { hilProfileComparison } from "../hil-demo/dut-profile.ts";
import { hilSceneContext } from "../hil-demo/scene-profile.ts";
import type { ObjectExecution } from "../object-scene/object-scene.ts";
import type { CityActor, CityMap, CityPose } from "./city-contract.ts";
import type { PlannerEvent } from "./city-decisions.ts";
export type CityState = CityPose & {
  id: string;
  speed_mps: number;
  desired_speed_mps: number | null;
  route_error_m?: number;
  route_progress_m?: number;
  goal_distance_m?: number;
  controller_state: string | null;
  plan?: Record<string, unknown>;
  control: {
    throttle?: number;
    brake?: number;
    steer?: number;
    speed_mps?: number;
  } | null;
};
export type CityCamera = {
  sensor_id: string;
  index: number;
  frame: number;
  time_s: number;
  simulation_time_s: number;
  archive: string;
  member: string;
  sha256: string;
  size_bytes: number;
  width: number;
  height: number;
  encoding: "bgra8";
};
export type CityFrame = {
  index: number;
  frame: number;
  input_frame: number;
  time_s: number;
  simulation_time_s: number;
  actors: CityState[];
  cameras: CityCamera[];
  signals?: {
    id: string;
    state: "red" | "yellow" | "green" | "off" | "unknown";
  }[];
};
export type CityScene = {
  schema_version: "carla-city-scene/1";
  fidelity:
    | "native_carla_object_physics"
    | "native_carla_mixed_physics_and_replay"
    | "native_carla_timed_position_replay";
  source: Record<string, unknown>;
  map: CityMap;
  actors: (CityActor & {
    native_actor_id: number;
    native_blueprint: string;
    dimensions_m: { length_m: number; width_m: number; height_m: number };
    bbox_center: { x: number; y: number; z: number };
    motion_fidelity: string;
  })[];
  frames: CityFrame[];
  events: {
    kind: string;
    actor_ids: string[];
    time_s: number;
    frame: number;
    provenance: string;
    details: Record<string, unknown>;
  }[];
  planner_events?: PlannerEvent[];
  planner_scope?: string;
  routes: Record<string, CityPose[]>;
  planned_paths: Record<
    string,
    {
      index: number;
      points: CityPose[];
      route_revision?: number;
      reason?: string;
    }[]
  >;
  camera_records: CityCamera[];
  metrics: Record<string, unknown>[];
  limitations: string[];
  verification_verdict?: string;
};
function finite(value: unknown, label: string, min = -1e6, max = 1e6): number {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < min ||
    value > max
  )
    throw new Error(`City scene ${label}: finite bounded number required.`);
  return value;
}
function rows(
  value: unknown,
  label: string,
  min: number,
  max: number,
): unknown[] {
  if (!Array.isArray(value) || value.length < min || value.length > max)
    throw new Error(`City scene ${label}: unsupported array bounds.`);
  return value;
}
function pose(value: unknown) {
  const item = record(value, "city pose");
  for (const key of ["x", "y", "z"]) finite(item[key], key);
  return item;
}
export type HilSide = "baseline" | "faulted";
/** Select a nested native capture only after authenticating its outer execution. */
export function cityEvidenceContext(sealed: unknown, execution: ObjectExecution, hilSide?: HilSide) {
  const receipt = record(sealed, "city sealed receipt"), stored = record(receipt.execution, "city execution"),
    prepared = record(receipt.prepared, "city prepared input"), spec = record(prepared.spec, "city spec"),
    outerInput = record(prepared.input, "city input");
  if (receipt.schema_version !== 1 || prepared.schema_version !== 1) throw new Error("Unsupported city receipt contract.");
  for (const key of ["id", "experiment_id", "project_id", "status", "experiment_kind", "adapter_id", "adapter_version", "input_hash", "fidelity"] as const)
    if (stored[key] !== execution[key]) throw new Error(`City sealed ${key} differs from selected execution.`);
  if (!sameValue(stored.artifacts, execution.artifacts) || spec.project_id !== execution.project_id ||
      spec.adapter_id !== execution.adapter_id || spec.adapter_version !== execution.adapter_version)
    throw new Error("City sealed artifact/spec identity differs from selected execution.");
  if (hilSide) {
    const summary = record(stored.summary, "HIL summary"), sides = record(summary.sides, "HIL sides");
    if (execution.experiment_kind !== "hil_demo" || execution.adapter_id !== "carla-software-dut" ||
        execution.fidelity !== "actual_carla_software_dut" || outerInput.schema_version !== "carla-software-dut-experiment/1" ||
        summary.schema_version !== "hil-demo-result/1" || summary.execution_target !== "software_dut" ||
        !sameValue(summary.fault, outerInput.fault) || typeof outerInput.city_adapter_version !== "string")
      throw new Error("Unsupported HIL native city execution contract.");
    const input = record(outerInput.city_snapshot, "HIL pinned city input"), sideSummary = record(sides[hilSide], "HIL side summary");
    hilProfileComparison(summary, outerInput, execution.project_id, spec);
    const sceneProfile = hilSceneContext(summary, outerInput, spec);
    if (input.schema_version !== "carla-city-input/1" || input.execution_fidelity !== "native_carla_object_physics" ||
        !sameValue(sideSummary.source, input.source)) throw new Error("HIL side differs from pinned city source.");
    return { input, spec, summary: sideSummary, prefix: `${hilSide}-`, fidelity: input.execution_fidelity,
      adapterVersion: outerInput.city_adapter_version, sceneProfile };
  }
  if (outerInput.schema_version !== "carla-city-input/1" || execution.adapter_id !== "carla-city-object" ||
      execution.experiment_kind !== "object_sim" || !["native_carla_object_physics", "native_carla_mixed_physics_and_replay", "native_carla_timed_position_replay"].includes(execution.fidelity ?? ""))
    throw new Error("Unsupported CARLA city execution contract.");
  return { input: outerInput, spec, summary: stored.summary, prefix: "", fidelity: execution.fidelity, adapterVersion: execution.adapter_version, sceneProfile: undefined };
}
export function parseCityScene(
  sealed: unknown,
  raw: unknown,
  execution: ObjectExecution,
  hilSide?: HilSide,
): CityScene {
  const { input, spec, prefix, fidelity, adapterVersion, sceneProfile } = cityEvidenceContext(sealed, execution, hilSide),
    scene = record(raw, "city scene");
  if (hilSide && !sameValue(scene.hil === undefined ? undefined : record(scene.hil, "HIL city scene").scene_profile, sceneProfile))
    throw new Error("HIL camera scene profile differs from its sealed execution.");
  if (
    scene.schema_version !== "carla-city-scene/1" ||
    scene.coordinate_system !== "carla_world_m_x_forward_y_right_z_up" ||
    scene.fidelity !== fidelity ||
    scene.physical_sensor_validation !== false
  )
    throw new Error(
      "City scene coordinate/fidelity boundary differs from the native contract.",
    );
  const scenario = record(input.scenario, "city scenario"),
    source = record(scene.source, "city source"),
    pinnedSource = record(input.source, "city pinned source"),
    mapRef = record(scenario.map, "city map reference");
  if (
    !["scenario-v2/2", "scenario-v2/3"].includes(
      String(scenario.schema_version),
    ) ||
    scenario.coordinate_system !== scene.coordinate_system ||
    spec.seed !== scenario.seed ||
    source.seed !== spec.seed ||
    source.adapter_version !== adapterVersion
  )
    throw new Error("City native source differs from the pinned scenario.");
  for (const [key, value] of Object.entries(pinnedSource))
    if (!sameValue(source[key], value))
      throw new Error(`City source ${key} differs from the sealed input.`);
  if (
    source.runner_sha256 !== input.native_script_sha256 ||
    source.control_sha256 !== input.control_script_sha256
  )
    throw new Error(
      "City native runner/controller hashes differ from pinned execution.",
    );
  if (
    (input.maneuver_script_sha256 !== undefined &&
      source.maneuver_sha256 !== input.maneuver_script_sha256) ||
    (source.maneuver_sha256 !== undefined &&
      (typeof source.maneuver_sha256 !== "string" ||
        !/^[a-f0-9]{64}$/.test(source.maneuver_sha256)))
  )
    throw new Error("City maneuver source differs from the pinned execution.");
  if (scene.signal_control !== undefined) {
    const control = record(scene.signal_control, "native signal control");
    if (
      !sameValue(control.schedules, scenario.signals ?? []) ||
      ![
        "explicit_states_with_all_native_light_cycles_frozen",
        "native_uncontrolled_cycles",
      ].includes(String(control.scope)) ||
      control.phase_clock !== "command_index_times_timestep"
    )
      throw new Error(
        "City signal schedule differs from the frozen authored input.",
      );
  }
  for (const key of [
    "configuration_hash",
    "scenario_manifest_hash",
    "map_sha256",
    "runner_sha256",
    "control_sha256",
    "xodr_sha256",
  ])
    if (
      typeof source[key] !== "string" ||
      !/^[a-f0-9]{64}$/.test(source[key] as string)
    )
      throw new Error(`City ${key} missing.`);
  const xodr = execution.artifacts.filter((item) => item.name === `${prefix}map.xodr`);
  if (
    xodr.length !== 1 ||
    xodr[0].sha256 !== source.xodr_sha256 ||
    source.map_sha256 !== mapRef.sha256
  )
    throw new Error("City map/OpenDRIVE source differs from retained content.");
  const frozenActors = rows(scenario.actors, "scenario actors", 1, 32).map(
      (value) => record(value, "actor"),
    ),
    actors = rows(
      scene.actors,
      "actors",
      frozenActors.length,
      frozenActors.length,
    ).map((value) => record(value, "native actor"));
  if (frozenActors.filter((item) => item.role === "ego").length !== 1)
    throw new Error("City frozen scenario must contain exactly one ego.");
  const modes = frozenActors.map(
    (actor) => record(actor.controller, "city actor controller").kind,
  );
  if (modes.includes("reactive")) {
    if (
      scenario.schema_version !== "scenario-v2/3" ||
      typeof source.planner_sha256 !== "string" ||
      !/^[a-f0-9]{64}$/.test(source.planner_sha256) ||
      source.planner_sha256 !== input.planner_script_sha256 ||
      scene.planner_scope !== "simulator_ground_truth_low_speed_reactive_no_v2x"
    )
      throw new Error(
        "City reactive planner source/scope differs from pinned execution.",
      );
  }
  const expectedFidelity = !modes.includes("timed_replay")
    ? "native_carla_object_physics"
    : modes.some((mode) =>
          ["route_follow", "traffic", "autoware", "reactive"].includes(
            String(mode),
          ),
        )
      ? "native_carla_mixed_physics_and_replay"
      : "native_carla_timed_position_replay";
  if (
    scene.fidelity !== expectedFidelity ||
    (input.execution_fidelity !== undefined &&
      input.execution_fidelity !== expectedFidelity)
  )
    throw new Error(
      "City motion fidelity differs from frozen actor controllers.",
    );
  const ids = new Set<string>();
  for (const actor of actors) {
    if (typeof actor.id !== "string" || ids.has(actor.id))
      throw new Error("City actor identity missing/duplicated.");
    ids.add(actor.id);
    const frozen = frozenActors.find((value) => value.id === actor.id);
    if (!frozen) throw new Error("City actor is absent from frozen scenario.");
    for (const [key, value] of Object.entries(frozen))
      if (!sameValue(actor[key], value))
        throw new Error(
          `City actor ${actor.id} ${key} differs from authored source.`,
        );
    const dimensions = record(actor.dimensions_m, "native dimensions");
    for (const key of ["length_m", "width_m", "height_m"])
      finite(dimensions[key], key, 0.01, 100);
    pose(actor.bbox_center);
    finite(actor.native_actor_id, "native actor id", 0);
    const motion =
      record(frozen.controller, "city actor controller").kind === "timed_replay"
        ? "timed_position_replay_no_physics"
        : "native_carla_physics";
    if (actor.motion_fidelity !== motion)
      throw new Error(
        "City actor motion fidelity differs from its controller.",
      );
  }
  const duration = finite(scenario.duration_s, "duration", 1, 120),
    dt = finite(scenario.timestep_s, "timestep", 0.02, 0.1),
    expected = Math.round(duration / dt),
    frames = rows(scene.frames, "frames", expected, expected).map((value) =>
      record(value, "city frame"),
    );
  let previous = -1;
  for (const [index, frame] of frames.entries()) {
    if (
      frame.index !== index ||
      Math.abs(finite(frame.time_s, "frame time", 0, 121) - (index + 1) * dt) >
        1e-5 ||
      !Number.isSafeInteger(frame.frame) ||
      (index > 0 && frame.frame !== previous + 1) ||
      frame.input_frame !== Number(frame.frame) - 1
    )
      throw new Error("City native frame/clock continuity failed.");
    previous = Number(frame.frame);
    finite(frame.simulation_time_s, "world time", 0, 1e9);
    const states = rows(frame.actors, "frame actors", ids.size, ids.size).map(
      (value) => record(value, "state"),
    );
    if (new Set(states.map((state) => state.id)).size !== ids.size)
      throw new Error("City frame repeats actors.");
    for (const state of states) {
      if (!ids.has(String(state.id)))
        throw new Error("City frame contains an unknown actor.");
      pose(state);
      finite(state.heading_rad, "heading", -7, 7);
      finite(state.speed_mps, "speed", 0, 150);
      if (state.plan !== undefined) {
        const plan = record(state.plan, "city retained plan");
        if (plan.reactive !== undefined) {
          const decision = record(plan.reactive, "reactive decision");
          if (
            record(
              frozenActors.find((actor) => actor.id === state.id)?.controller,
              "reactive actor owner",
            ).kind !== "reactive" ||
            typeof decision.reason !== "string" ||
            typeof decision.state !== "string" ||
            typeof decision.stop !== "boolean" ||
            !Number.isSafeInteger(decision.route_revision)
          )
            throw new Error("City reactive decision identity invalid.");
          finite(decision.route_revision, "route revision", 0, 10000);
          finite(decision.speed_mps, "reactive requested speed", 0, 12);
          record(decision.diagnostics, "reactive diagnostics");
        }
      }
      for (const field of [
        "route_error_m",
        "route_progress_m",
        "goal_distance_m",
      ])
        if (state[field] !== undefined) finite(state[field], field, 0, 1e6);
    }
  }
  const map = record(scene.map, "native city map");
  if (
    map.opendrive_sha256 !== source.xodr_sha256 ||
    map.coordinate_system !== scene.coordinate_system
  )
    throw new Error("City lane map provenance mismatch.");
  if (!sameValue(map.external ?? null, input.external_map_provenance ?? null))
    throw new Error("City external map lineage differs from the sealed qualified source.");
  for (const lane of rows(map.lanes, "map lanes", 1, 50000)) {
    const value = record(lane, "lane");
    rows(value.points, "lane points", 1, 50000).forEach((point) => {
      const p = pose(point);
      finite(p.heading_rad, "lane heading", -7, 7);
    });
    finite(value.width_m, "lane width", 0.1, 30);
  }
  const signals = rows(map.signals ?? [], "map signals", 0, 5000).map((value) =>
    record(value, "native signal"),
  );
  if (new Set(signals.map((signal) => signal.id)).size !== signals.length)
    throw new Error("City native signal identity duplicated.");
  for (const signal of signals) {
    if (
      typeof signal.id !== "string" ||
      !["traffic_light", "stop", "other"].includes(String(signal.kind))
    )
      throw new Error("City signal identity/kind invalid.");
    pose(signal.pose);
  }
  for (const frame of frames) {
    const observations = rows(
      frame.signals ?? [],
      "signal observations",
      0,
      5000,
    ).map((value) => record(value, "signal observation"));
    if (
      new Set(observations.map((item) => item.id)).size !==
        observations.length ||
      observations.some(
        (item) =>
          !signals.some((signal) => signal.id === item.id) ||
          !["red", "yellow", "green", "off", "unknown"].includes(
            String(item.state),
          ),
      )
    )
      throw new Error(
        "City observed signal state differs from native identities.",
      );
  }
  const routes = record(scene.routes, "desired routes");
  for (const actor of frozenActors)
    if (!sameValue(routes[String(actor.id)], actor.route))
      throw new Error("City desired routes differ from frozen input.");
  const cameras = rows(scene.camera_records, "camera records", 0, 19200).map(
      (value) => record(value, "camera record"),
    ),
    names = new Set<string>();
  for (const camera of cameras) {
    const index = finite(camera.index, "camera index", 0, frames.length - 1),
      frame = frames[index];
    if (
      !frame ||
      frame.frame !== camera.frame ||
      Math.abs(Number(camera.time_s) - Number(frame.time_s)) > 1e-5 ||
      Math.abs(
        Number(camera.simulation_time_s) - Number(frame.simulation_time_s),
      ) > 1e-5 ||
      camera.encoding !== "bgra8" ||
      typeof camera.archive !== "string" ||
      !/^city-camera-\d{4}\.zip$/.test(camera.archive) ||
      typeof camera.member !== "string" ||
      !/^[A-Za-z0-9_-]{1,64}\/\d{6}\.bgra$/.test(camera.member) ||
      typeof camera.sha256 !== "string" ||
      !/^[a-f0-9]{64}$/.test(camera.sha256)
    )
      throw new Error("City camera frame identity/clock is invalid.");
    const width = finite(camera.width, "camera width", 64, 1920),
      height = finite(camera.height, "camera height", 64, 1080);
    if (camera.size_bytes !== width * height * 4)
      throw new Error("City camera byte/grid mismatch.");
    const name = `${camera.archive}/${camera.member}`;
    if (names.has(name)) throw new Error("City camera member duplicated.");
    names.add(name);
  }
  if (
    !sameValue(
      frames.flatMap((frame) => rows(frame.cameras, "frame cameras", 0, 8)),
      cameras,
    )
  )
    throw new Error("City frame and camera inventories differ.");
  for (const rawEvent of rows(scene.events, "events", 0, 50000)) {
    const event = record(rawEvent, "event");
    finite(event.time_s, "event time", 0, duration + dt);
    if (
      typeof event.kind !== "string" ||
      typeof event.provenance !== "string" ||
      rows(event.actor_ids, "event actors", 0, 32).some(
        (id) => !ids.has(String(id)),
      )
    )
      throw new Error("City event lacks known identity/provenance.");
  }
  if (scene.planner_events !== undefined) {
    for (const raw of rows(scene.planner_events, "planner events", 0, 200000)) {
      const event = record(raw, "planner event");
      const i = finite(
        event.index,
        "planner event index",
        0,
        frames.length - 1,
      );
      finite(event.time_s, "planner event time", 0, duration);
      if (
        !Number.isSafeInteger(i) ||
        !ids.has(String(event.actor_id)) ||
        record(
          frozenActors.find((actor) => actor.id === event.actor_id)?.controller,
          "planner event actor owner",
        ).kind !== "reactive" ||
        Math.abs(Number(event.time_s) - i * dt) > 1e-5 ||
        event.input_frame !== frames[i].input_frame ||
        typeof event.reason !== "string" ||
        typeof event.type !== "string" ||
        event.provenance !== "observed_state_reactive_planner" ||
        !Number.isSafeInteger(event.route_revision)
      )
        throw new Error("City planner event provenance/clock invalid.");
    }
  }
  for (const [id, series] of Object.entries(
    record(scene.planned_paths, "planned paths"),
  )) {
    if (!ids.has(id)) throw new Error("Planner output has unknown actor.");
    let previousIndex = -1;
    for (const item of rows(series, "planned series", 0, frames.length)) {
      const entry = record(item, "planned entry");
      const index = finite(entry.index, "planned index", 0, frames.length - 1);
      if (!Number.isSafeInteger(index) || index <= previousIndex)
        throw new Error(
          "City planned samples must have unique ordered frame indices.",
        );
      previousIndex = index;
      rows(entry.points, "planned points", 1, 10000).forEach(pose);
    }
  }
  rows(scene.metrics, "metrics", 0, 100);
  if (
    rows(scene.limitations, "limitations", 1, 100).some(
      (value) => typeof value !== "string",
    )
  )
    throw new Error("City limitations missing.");
  return scene as unknown as CityScene;
}
