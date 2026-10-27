import test from "node:test";
import assert from "node:assert/strict";
import {
  appendJunction,
  deleteRoad,
  emptyObjectMap,
  moveHistory,
  newRoad,
  previewObjectMap,
  sampleRoad,
} from "../src/components/city-sim/object-map-contract.ts";
import {
  currentPlan,
  decisionRelations,
} from "../src/components/city-sim/city-decisions.ts";
import {
  enableReactiveActor,
  newBehaviorAction,
  newCityScenario,
} from "../src/components/city-sim/city-contract.ts";
import type { CityState } from "../src/components/city-sim/city-scene.ts";
// Authored geometry/contract fixtures; no native simulator or physical-validation claims.
test("analytic circular preview maintains endpoints and signed CARLA lane direction", () => {
  const road = newRoad("1");
  road.geometries = [
    {
      kind: "arc",
      length_m: 6 * Math.PI,
      curvature_start: 1 / 12,
      curvature_end: 1 / 12,
    },
  ];
  const end = sampleRoad(road).at(-1)!;
  assert.ok(Math.abs(end.x - 12) < 1e-10);
  assert.ok(Math.abs(end.y - 12) < 1e-10);
  assert.ok(Math.abs(end.heading_rad - Math.PI / 2) < 1e-10);
  const source = emptyObjectMap();
  source.roads = [newRoad("1")];
  const lanes = previewObjectMap(source).lanes;
  const right = lanes.find((l) => l.id === "1:-1")!,
    left = lanes.find((l) => l.id === "1:1")!;
  assert.equal(right.points[0].y, 1.75);
  assert.equal(left.points[0].y, -1.75);
  assert.equal(left.points[0].x, 80);
  assert.equal(left.points.at(-1)!.x, 0);
});
test("T and cross junction templates have explicit connected lane endpoints and keep existing source untouched", () => {
  for (const kind of ["tee", "cross"] as const) {
    const original = emptyObjectMap(),
      source = appendJunction(original, kind),
      map = previewObjectMap(source);
    assert.equal(original.roads.length, 0);
    assert.equal(source.connections.length, kind === "cross" ? 24 : 12);
    for (const c of source.connections) {
      const a = map.lanes
          .find((l) => l.id === `${c.from_road}:${c.from_lane}`)!
          .points.at(-1)!,
        b = map.lanes.find((l) => l.id === `${c.to_road}:${c.to_lane}`)!
          .points[0];
      assert.ok(
        Math.hypot(a.x - b.x, a.y - b.y) < 1e-8,
        `${JSON.stringify(c)} endpoint disconnected`,
      );
      assert.ok(
        Math.abs(
          Math.atan2(
            Math.sin(a.heading_rad - b.heading_rad),
            Math.cos(a.heading_rad - b.heading_rad),
          ),
        ) < 1e-8,
      );
    }
  }
});
test("road deletion removes dangling source references without changing the previous snapshot", () => {
  const source = appendJunction(emptyObjectMap(), "tee"),
    removed = source.roads[0].id;
  source.signals = [
    {
      id: "stop",
      road_id: removed,
      kind: "stop",
      s_m: 5,
      t_m: 3,
      orientation: "+",
      lane_ids: [-1],
    },
  ];
  const next = deleteRoad(source, removed);
  assert.equal(source.signals.length, 1);
  assert.equal(next.signals.length, 0);
  assert.ok(
    next.connections.every(
      (c) => c.from_road !== removed && c.to_road !== removed,
    ),
  );
});
test("undo/redo preserves exact drafts and does not corrupt history when at boundary", () => {
  const initial = {
      past: [{ x: 1 }],
      present: { x: 2 },
      future: [] as { x: number }[],
    },
    undo = moveHistory(initial, "undo");
  assert.deepEqual(undo.present, { x: 1 });
  assert.deepEqual(moveHistory(undo, "redo"), initial);
  assert.equal(moveHistory(initial, "redo"), initial);
});
test("reactive conversion preserves old source and explicitly moves legacy control ownership", () => {
  const pose = { x: 0, y: 0, z: 0, heading_rad: 0 },
    sha = "a".repeat(64),
    scenario = newCityScenario(
      {
        id: "map",
        revision: 1,
        sha256: sha,
        project_id: "default",
        definition: { lanes: [{ id: "lane", points: [pose] }] },
      },
      {
        id: "car",
        revision: 1,
        sha256: sha,
        project_id: "default",
        definition: { actor_kind: "vehicle" },
      },
    );
  scenario.actors[0].controller = {
    kind: "traffic",
    ref: null,
    end_behavior: "repeat",
    headway_s: 3,
    obey_signals: false,
    wait_s: 2,
    stop_time_s: 15,
  };
  const reactive = enableReactiveActor(scenario, "ego");
  assert.equal(scenario.schema_version, "scenario-v2/2");
  assert.equal(scenario.actors[0].controller.kind, "traffic");
  assert.equal(reactive.schema_version, "scenario-v2/3");
  assert.equal(reactive.actors[0].controller.headway_s, 2);
  assert.equal(reactive.actors[0].controller.end_behavior, "stop");
  assert.equal(reactive.actors[0].planner?.headway_s, 3);
  assert.equal(reactive.actors[0].planner?.obey_signals, false);
  assert.deepEqual(
    reactive.actors[0].behavior?.actions.map((a) => [a.kind, a.trigger.value]),
    [
      ["wait", 0],
      ["stop", 15],
    ],
  );
});
test("behavior sequencing references the previous action and retains stable distinct identifiers", () => {
  const first = newBehaviorAction([], "wait"),
    next = newBehaviorAction([first], "target_speed");
  assert.notEqual(next.id, first.id);
  assert.equal(next.trigger.kind, "action_complete");
  assert.equal(next.trigger.action_id, first.id);
  assert.equal(next.speed_mps, 3);
});
test("reactive route stays active between replans while external-model samples remain frame-specific", () => {
  const samples = [
    {
      index: 0,
      route_revision: 0,
      points: [{ x: 0, y: 0, z: 0, heading_rad: 0 }],
    },
    {
      index: 4,
      route_revision: 1,
      points: [{ x: 5, y: 1, z: 0, heading_rad: 0 }],
    },
  ];
  assert.equal(currentPlan(samples, 3, true), samples[0]);
  assert.equal(currentPlan(samples, 4, true), samples[1]);
  assert.equal(currentPlan(samples, 3, false), undefined);
  assert.equal(currentPlan(samples, 0, false), samples[0]);
});
test("decision links use retained actor identities and omit unknown actors", () => {
  const state = {
    id: "ego",
    x: 0,
    y: 0,
    z: 0,
    heading_rad: 0,
    speed_mps: 0,
    desired_speed_mps: 0,
    controller_state: "wait",
    control: null,
    plan: {
      reactive: {
        reason: "gap_wait",
        state: "wait",
        speed_mps: 0,
        stop: true,
        route_revision: 1,
        action_id: null,
        diagnostics: {
          lead: { actor_id: "lead", bumper_gap_m: 3 },
          conflicts: [{ actor_id: "lead" }, { actor_id: "missing" }],
        },
      },
    },
  } satisfies CityState;
  const lead = { ...state, id: "lead", x: 8 };
  assert.equal(decisionRelations(state, [state, lead]).length, 1);
  assert.match(decisionRelations(state, [state, lead])[0].label, /3.0m/);
});
