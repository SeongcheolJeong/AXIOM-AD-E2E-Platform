import test from "node:test";
import assert from "node:assert/strict";
import { parseCityScene } from "../src/components/city-sim/city-scene.ts";
import type { ObjectExecution } from "../src/components/object-scene/object-scene.ts";
import { newCityScenario } from "../src/components/city-sim/city-contract.ts";
const sha = "a".repeat(64),
  coordinates = "carla_world_m_x_forward_y_right_z_up";
// Authored parser fixtures exercise provenance rejection, not native driving.
function fixture() {
  const point = { x: 10, y: 20, z: 1, heading_rad: 0 },
    map = {
      id: "town01",
      project_id: "default",
      revision: 1,
      sha256: sha,
      definition: {
        name: "Town01",
        lanes: [
          { id: "lane", width_m: 3.5, points: [point, { ...point, x: 20 }] },
        ],
      },
    };
  const scenario = newCityScenario(map, {
    id: "prius",
    revision: 1,
    project_id: "default",
    sha256: sha,
    definition: { actor_kind: "vehicle", blueprint_id: "vehicle.toyota.prius" },
  });
  scenario.duration_s = 1;
  scenario.actors[0].route = [point, { ...point, x: 20 }];
  const source = {
    scenario_id: "city1",
    scenario_revision: 1,
    scenario_manifest_hash: sha,
    configuration_hash: sha,
    map_sha256: sha,
    seed: 42,
  };
  const execution: ObjectExecution = {
    id: "job_city",
    experiment_id: "exp_city",
    project_id: "default",
    experiment_kind: "object_sim",
    status: "completed",
    adapter_id: "carla-city-object",
    adapter_version: "carla-city-object/1.0.0",
    input_hash: sha,
    manifest_hash: sha,
    download_url: "/api/v1/experiments/executions/job_city/export",
    fidelity: "native_carla_object_physics",
    artifacts: [
      {
        id: "road",
        name: "map.xodr",
        media_type: "application/xml",
        sha256: sha,
        size_bytes: 25,
        download_url: "/api/v1/experiments/executions/job_city/artifacts/road",
      },
    ],
  };
  const sealed = {
    schema_version: 1,
    execution: structuredClone(execution),
    prepared: {
      schema_version: 1,
      input: {
        schema_version: "carla-city-input/1",
        scenario,
        source,
        native_script_sha256: sha,
        control_script_sha256: sha,
      },
      spec: {
        project_id: "default",
        adapter_id: execution.adapter_id,
        adapter_version: execution.adapter_version,
        seed: 42,
      },
    },
  };
  const scene = {
    schema_version: "carla-city-scene/1",
    coordinate_system: coordinates,
    fidelity: execution.fidelity,
    physical_sensor_validation: false,
    source: {
      ...source,
      adapter_version: execution.adapter_version,
      runner_sha256: sha,
      control_sha256: sha,
      xodr_sha256: sha,
    },
    map: {
      ...map.definition,
      coordinate_system: coordinates,
      opendrive_sha256: sha,
    },
    actors: scenario.actors.map((actor) => ({
      ...structuredClone(actor),
      native_actor_id: 55,
      bbox_center: { x: 0.1, y: 0, z: 0.7 },
      native_blueprint: "vehicle.toyota.prius",
      dimensions_m: { length_m: 4.5, width_m: 1.8, height_m: 1.5 },
      motion_fidelity: "native_carla_physics",
    })),
    frames: Array.from({ length: 20 }, (_, index) => ({
      index,
      frame: 101 + index,
      input_frame: 100 + index,
      time_s: (index + 1) * 0.05,
      simulation_time_s: 10 + (index + 1) * 0.05,
      actors: [
        {
          ...point,
          id: "ego",
          x: point.x + index * 0.1,
          speed_mps: 2,
          desired_speed_mps: 3,
          route_error_m: 0.1,
          controller_state: "following",
          control: { throttle: 0.2, brake: 0, steer: 0 },
        },
      ],
      cameras: [] as unknown[],
    })),
    events: [] as unknown[],
    routes: { ego: scenario.actors[0].route },
    planned_paths: {},
    camera_records: [] as unknown[],
    metrics: [],
    limitations: ["Fixture is not native evidence."],
  };
  return { scene, sealed, execution };
}
test("city parser preserves native samples and pinned source with explicit physics limits", () => {
  const value = fixture(),
    parsed = parseCityScene(value.sealed, value.scene, value.execution);
  assert.equal(parsed.frames.length, 20);
  assert.equal(parsed.source.seed, 42);
  assert.equal(parsed.actors[0].native_actor_id, 55);
});

test("external map playback requires the exact source, conversion and qualification lineage sealed in the input", () => {
  const value = fixture();
  const provenance = { source: { id: "source-a", revision: 2, sha256: sha }, conversion: { id: "conversion-a", revision: 1, sha256: sha }, qualification_execution_id: "job-qualified", qualification_manifest_hash: sha };
  const sealed = { ...value.sealed, prepared: { ...value.sealed.prepared, input: { ...value.sealed.prepared.input, external_map_provenance: provenance } } };
  const scene = { ...value.scene, map: { ...value.scene.map, external: structuredClone(provenance) } };
  assert.deepEqual(parseCityScene(sealed, scene, value.execution).map.external, provenance);
  assert.throws(() => parseCityScene(sealed, value.scene, value.execution), /external map lineage/);
  assert.throws(() => parseCityScene(value.sealed, scene, value.execution), /external map lineage/);
  scene.map.external.conversion.sha256 = "b".repeat(64);
  assert.throws(() => parseCityScene(sealed, scene, value.execution), /external map lineage/);
});

test("city parser classifies pure timed replay and rejects a physical motion label", () => {
  const value = fixture();
  const actor = value.sealed.prepared.input.scenario.actors[0];
  actor.controller.kind = "timed_replay";
  actor.trajectory = [
    { ...actor.pose, time_s: 0 },
    { ...actor.pose, x: actor.pose.x + 2, time_s: 1 },
  ];
  actor.route = [];
  Object.assign(value.scene.actors[0], structuredClone(actor), {
    motion_fidelity: "timed_position_replay_no_physics",
  });
  value.scene.routes.ego = [];
  assert.throws(
    () => parseCityScene(value.sealed, value.scene, value.execution),
    /motion fidelity/,
  );
  value.execution.fidelity =
    value.sealed.execution.fidelity =
    value.scene.fidelity =
      "native_carla_timed_position_replay";
  assert.equal(
    parseCityScene(value.sealed, value.scene, value.execution).fidelity,
    "native_carla_timed_position_replay",
  );
  value.scene.actors[0].motion_fidelity = "native_carla_physics";
  assert.throws(
    () => parseCityScene(value.sealed, value.scene, value.execution),
    /motion fidelity/,
  );
});

test("city parser keeps mixed replay visible with distinct scene and actor fidelities", () => {
  const value = fixture();
  const other = structuredClone(value.sealed.prepared.input.scenario.actors[0]);
  other.id = "replay";
  other.role = "other";
  other.controller.kind = "timed_replay";
  other.trajectory = [
    { ...other.pose, time_s: 0 },
    { ...other.pose, x: other.pose.x + 2, time_s: 1 },
  ];
  other.route = [];
  value.sealed.prepared.input.scenario.actors.push(other);
  value.scene.actors.push({
    ...structuredClone(value.scene.actors[0]),
    ...structuredClone(other),
    native_actor_id: 56,
    motion_fidelity: "timed_position_replay_no_physics",
  });
  for (const frame of value.scene.frames)
    frame.actors.push({ ...frame.actors[0], id: "replay" });
  Object.assign(value.scene.routes, { replay: [] });
  value.execution.fidelity =
    value.sealed.execution.fidelity =
    value.scene.fidelity =
      "native_carla_mixed_physics_and_replay";
  const parsed = parseCityScene(value.sealed, value.scene, value.execution);
  assert.equal(parsed.actors.length, 2);
  assert.equal(parsed.fidelity, "native_carla_mixed_physics_and_replay");
  value.execution.fidelity =
    value.sealed.execution.fidelity =
    value.scene.fidelity =
      "native_carla_timed_position_replay";
  assert.throws(
    () => parseCityScene(value.sealed, value.scene, value.execution),
    /motion fidelity/,
  );
});
test("city parser preserves observed signal state and rejects invented states or identities", () => {
  const value = fixture();
  const scene = value.scene as unknown as {
    map: Record<string, unknown>;
    frames: Record<string, unknown>[];
  };
  scene.map.signals = [
    {
      id: "native-signal",
      kind: "traffic_light",
      pose: { x: 12, y: 20, z: 1, heading_rad: 0 },
    },
  ];
  scene.frames[0].signals = [{ id: "native-signal", state: "red" }];
  assert.equal(
    parseCityScene(value.sealed, value.scene, value.execution).frames[0]
      .signals?.[0].state,
    "red",
  );
  scene.frames[0].signals = [{ id: "native-signal", state: "inferred_green" }];
  assert.throws(() =>
    parseCityScene(value.sealed, value.scene, value.execution),
  );
  scene.frames[0].signals = [{ id: "unknown-signal", state: "green" }];
  assert.throws(() =>
    parseCityScene(value.sealed, value.scene, value.execution),
  );
});
test("city parser retains legacy scenes and pins additive maneuver and signal source", () => {
  const value = fixture();
  assert.doesNotThrow(() =>
    parseCityScene(value.sealed, value.scene, value.execution),
  );
  const input = value.sealed.prepared.input as Record<string, unknown>,
    scene = value.scene as unknown as Record<string, unknown>,
    source = value.scene.source as Record<string, unknown>;
  input.maneuver_script_sha256 = sha;
  source.maneuver_sha256 = sha;
  scene.signal_control = {
    schedules: [],
    scope: "native_uncontrolled_cycles",
    phase_clock: "command_index_times_timestep",
    prior_elapsed_cycle_phase_restorable: false,
  };
  assert.doesNotThrow(() =>
    parseCityScene(value.sealed, value.scene, value.execution),
  );
  source.maneuver_sha256 = "b".repeat(64);
  assert.throws(() =>
    parseCityScene(value.sealed, value.scene, value.execution),
  );
  source.maneuver_sha256 = sha;
  (scene.signal_control as Record<string, unknown>).schedules = [
    { id: "substituted", initial_state: "green", changes: [] },
  ];
  assert.throws(() =>
    parseCityScene(value.sealed, value.scene, value.execution),
  );
});
test("city parser rejects ambiguous planner frame ownership", () => {
  const value = fixture(),
    point = value.scene.routes.ego[0];
  value.scene.planned_paths = {
    ego: [
      { index: 1, points: [point] },
      { index: 1, points: [point] },
    ],
  };
  assert.throws(() =>
    parseCityScene(value.sealed, value.scene, value.execution),
  );
  value.scene.planned_paths = { ego: [{ index: 1.5, points: [point] }] };
  assert.throws(() =>
    parseCityScene(value.sealed, value.scene, value.execution),
  );
});
for (const [name, mutate] of [
  [
    "foreign execution",
    (v: ReturnType<typeof fixture>) => {
      v.sealed.execution.id = "another";
    },
  ],
  [
    "wrong coordinate frame",
    (v: ReturnType<typeof fixture>) => {
      v.scene.coordinate_system = "y_left";
    },
  ],
  [
    "unproven physical claim",
    (v: ReturnType<typeof fixture>) => {
      v.scene.physical_sensor_validation = true;
    },
  ],
  [
    "map hash substitution",
    (v: ReturnType<typeof fixture>) => {
      v.scene.source.map_sha256 = "b".repeat(64);
    },
  ],
  [
    "changed route",
    (v: ReturnType<typeof fixture>) => {
      v.scene.routes = { ego: [{ x: 0, y: 0, z: 0, heading_rad: 0 }] };
    },
  ],
  [
    "changed actor controller",
    (v: ReturnType<typeof fixture>) => {
      v.scene.actors[0].controller.kind = "timed_replay";
    },
  ],
  [
    "missing native frame",
    (v: ReturnType<typeof fixture>) => {
      v.scene.frames.pop();
    },
  ],
  [
    "discontinuous world clock",
    (v: ReturnType<typeof fixture>) => {
      v.scene.frames[3].frame += 1;
    },
  ],
  [
    "wrong input frame owner",
    (v: ReturnType<typeof fixture>) => {
      v.scene.frames[3].input_frame -= 1;
    },
  ],
  [
    "unknown frame actor",
    (v: ReturnType<typeof fixture>) => {
      v.scene.frames[3].actors[0].id = "invented";
    },
  ],
  [
    "nonfinite world coordinate",
    (v: ReturnType<typeof fixture>) => {
      v.scene.frames[3].actors[0].x = NaN;
    },
  ],
  [
    "nonfinite native box center",
    (v: ReturnType<typeof fixture>) => {
      v.scene.actors[0].bbox_center.z = NaN;
    },
  ],
  [
    "missing camera closure",
    (v: ReturnType<typeof fixture>) => {
      v.scene.frames[0].cameras.push({ frame: 101 });
    },
  ],
] as const)
  test(`city parser fails closed for ${name}`, () => {
    const v = fixture();
    mutate(v);
    assert.throws(() => parseCityScene(v.sealed, v.scene, v.execution));
  });

test("reactive /3 parser binds planner code, event actor and input clock without upgrading legacy evidence", () => {
  const value = fixture(),
    scenario = value.sealed.prepared.input.scenario;
  scenario.schema_version = "scenario-v2/3";
  scenario.actors[0].controller.kind = "reactive";
  scenario.actors[0].behavior = {
    schema_version: "object-behavior/1",
    risk_mode: false,
    actions: [],
  };
  scenario.actors[0].planner = { schema_version: "object-planner/1" };
  Object.assign(value.scene.actors[0], structuredClone(scenario.actors[0]));
  Object.assign(value.sealed.prepared.input, { planner_script_sha256: sha });
  Object.assign(value.scene.source, { planner_sha256: sha });
  const scene = Object.assign(value.scene, {
    planner_scope: "simulator_ground_truth_low_speed_reactive_no_v2x",
    planner_events: [
      {
        time_s: 0,
        actor_id: "ego",
        type: "decision_changed",
        reason: "route_follow",
        action_id: null,
        route_revision: 0,
        index: 0,
        input_frame: 100,
        provenance: "observed_state_reactive_planner",
      },
    ],
  });
  assert.equal(
    parseCityScene(value.sealed, scene, value.execution).planner_events?.length,
    1,
  );
  const foreign = structuredClone(scene);
  foreign.planner_events[0].actor_id = "foreign";
  assert.throws(
    () => parseCityScene(value.sealed, foreign, value.execution),
    /planner event/,
  );
  const wrongClock = structuredClone(scene);
  wrongClock.planner_events[0].time_s = 0.2;
  assert.throws(
    () => parseCityScene(value.sealed, wrongClock, value.execution),
    /planner event/,
  );
  const alteredCode = structuredClone(scene);
  Object.assign(alteredCode.source, { planner_sha256: "b".repeat(64) });
  assert.throws(
    () => parseCityScene(value.sealed, alteredCode, value.execution),
    /planner source/,
  );
});

test("HIL side parser authenticates the outer receipt and uses the pinned native source", () => {
  const value = fixture(), nativeInput = structuredClone(value.sealed.prepared.input),
    version = value.execution.adapter_version, fault = { kind: "delay", start_s: 3, duration_s: 4, delay_ms: 300 };
  const execution: ObjectExecution = { ...value.execution, adapter_id: "carla-software-dut", adapter_version: "1.0.0",
    experiment_kind: "hil_demo", fidelity: "actual_carla_software_dut",
    artifacts: value.execution.artifacts.map(item => ({ ...item, name: "baseline-" + item.name })) };
  const sealed = { ...value.sealed, execution: { ...structuredClone(execution), summary: {
      schema_version: "hil-demo-result/1", execution_target: "software_dut", fault,
      sides: { baseline: { source: nativeInput.source } } } },
    prepared: { ...value.sealed.prepared, spec: { ...value.sealed.prepared.spec, adapter_id: execution.adapter_id, adapter_version: execution.adapter_version },
      input: { schema_version: "carla-software-dut-experiment/1", city_snapshot: { ...nativeInput, execution_fidelity: "native_carla_object_physics" }, city_adapter_version: version, fault } } };
  assert.equal(parseCityScene(sealed, value.scene, execution, "baseline").frames.length, value.scene.frames.length);
  assert.throws(() => parseCityScene(sealed, value.scene, execution), /Unsupported/);
  assert.throws(() => parseCityScene(sealed, value.scene, execution, "faulted"), /object required/);
  assert.throws(() => parseCityScene(sealed, value.scene, { ...execution, input_hash: "b".repeat(64) }, "baseline"), /input_hash/);
  const changed = structuredClone(sealed); changed.prepared.input.city_adapter_version = "other";
  assert.throws(() => parseCityScene(changed, value.scene, execution, "baseline"), /native source/);
  const exchanged = structuredClone(value.scene); exchanged.source.configuration_hash = "b".repeat(64);
  assert.throws(() => parseCityScene(sealed, exchanged, execution, "baseline"), /configuration_hash/);
  assert.throws(() => parseCityScene(sealed, { ...value.scene, hil: { scene_profile: { id: "foreign-scene" } } }, execution, "baseline"), /scene profile/);
});
