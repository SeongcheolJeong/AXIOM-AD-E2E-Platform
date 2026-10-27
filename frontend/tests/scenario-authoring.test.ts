import test from "node:test";
import assert from "node:assert/strict";
import {
  actorColor,
  actorReferences,
  appendWaypoint,
  createActor,
  defaultAction,
  laneCenter,
  laneChangeWarnings,
  startingLane,
  waypointIssue,
} from "../src/scenario-authoring.ts";
import type { Actor, Scenario, TimedPose } from "../src/scenario-authoring.ts";

// Authored editor fixtures verify contracts only; these are not native simulator results.
const map: Scenario["map"] = {
  kind: "authored_straight_road",
  id: "axiom-straight-road-r1",
  length_m: 500,
  lane_width_m: 3.5,
};
function fixture(): Scenario {
  const ego = createActor("vehicle", [], map, 8);
  ego.id = "ego";
  ego.role = "ego";
  ego.pose = { x: 5, y: laneCenter(-1, map.lane_width_m), heading_rad: 0 };
  return {
    schema_version: "scenario-v2/1",
    name: "Editor contract fixture",
    template: "cut_in",
    map: { ...map },
    coordinate_system: "world_m_x_forward_y_left_z_up",
    duration_s: 8,
    timestep_s: 0.1,
    actors: [ego],
    environment: { lighting: "day", weather: "dry" },
    observers: [],
    sweep: [],
  };
}
function actorWithChange(target = -1): { scenario: Scenario; actor: Actor } {
  const scenario = fixture(),
    actor = createActor("vehicle", scenario.actors, map, 8);
  actor.pose.y = laneCenter(-1, map.lane_width_m);
  const action = defaultAction("lane_change", actor, map, 8);
  action.target_lane = target;
  actor.actions = [action];
  scenario.actors.push(actor);
  return { scenario, actor };
}

test("lane centers and classification use authored widths, preserve outside-road status", () => {
  assert.ok(Math.abs(laneCenter(-1, 4.6) + 2.3) < 1e-10);
  assert.ok(Math.abs(laneCenter(-2, 4.6) + 6.9) < 1e-10);
  assert.equal(startingLane(-2.3, 4.6), -1);
  assert.equal(startingLane(-6.9, 4.6), -2);
  assert.equal(startingLane(0.01, 4.6), null);
  assert.equal(startingLane(-9.21, 4.6), null);
  assert.equal(startingLane(0, 0), null);
});
test("new IDs remain unique after deleting an actor and changing actor order", () => {
  const scenario = fixture(),
    a = createActor("vehicle", scenario.actors, map, 8),
    b = createActor("vehicle", [...scenario.actors, a], map, 8);
  assert.notEqual(a.id, b.id);
  const next = createActor("vehicle", [b, ...scenario.actors], map, 8);
  assert.notEqual(next.id, b.id);
  assert.equal(actorColor(b.id), actorColor(b.id));
  assert.match(actorColor(b.id), /^#[0-9a-f]{6}$/);
  assert.equal(
    new Set(
      ["ego", "lane_changer", "lead", "pedestrian", "trailing"].map(actorColor),
    ).size,
    5,
  );
});
test("new pedestrian uses explicit timed motion within even a one-second scenario", () => {
  const pedestrian = createActor(
    "pedestrian",
    fixture().actors,
    { ...map, lane_width_m: 5 },
    1,
  );
  assert.equal(pedestrian.speed_mps, 0);
  assert.deepEqual(pedestrian.route, []);
  assert.equal(pedestrian.actions[0].kind, "follow_path");
  assert.equal(pedestrian.pose.y, -10.8);
  assert.equal(waypointIssue(pedestrian.actions[0].path, 1), null);
  assert.ok(pedestrian.actions[0].path.at(-1)!.y > 0);
});
test("new lane changes default to the other lane, while vehicle paths keep their lane", () => {
  const scenario = fixture(),
    ego = scenario.actors[0];
  assert.equal(defaultAction("lane_change", ego, map, 8).target_lane, -2);
  const path = defaultAction("follow_path", ego, map, 8).path;
  assert.equal(path[0].y, path[1].y);
  assert.ok(path[1].x > path[0].x);
});
test("first same-starting-lane change is warned without altering the authored scenario", () => {
  const { scenario, actor } = actorWithChange(),
    original = structuredClone(scenario);
  assert.match(laneChangeWarnings(scenario)[0], new RegExp(actor.id));
  assert.deepEqual(scenario, original);
  actor.actions[0].target_lane = -2;
  assert.deepEqual(laneChangeWarnings(scenario), []);
});
test("a legitimate later return to the initial lane is not called a no-op", () => {
  const { scenario, actor } = actorWithChange();
  actor.actions[0].trigger.value = 5;
  const earlier = defaultAction("lane_change", actor, map, 8);
  earlier.target_lane = -2;
  earlier.trigger.value = 1;
  actor.actions.unshift(earlier);
  assert.deepEqual(laneChangeWarnings(scenario), []);
});
test("uncertain prior lateral triggers or authored route do not cause a global no-op rejection", () => {
  const { scenario, actor } = actorWithChange();
  const conditional = defaultAction("lane_change", actor, map, 8);
  conditional.target_lane = -2;
  conditional.trigger = {
    kind: "distance",
    value: 5,
    actor_id: actor.id,
    target_actor_id: "ego",
    rule: "lessThan",
    state: "speed",
  };
  actor.actions.push(conditional);
  assert.deepEqual(laneChangeWarnings(scenario), []);
  actor.actions.pop();
  actor.route.push({ x: 20, y: -5.25, heading_rad: 0 });
  assert.deepEqual(laneChangeWarnings(scenario), []);
});
test("a later known lateral change does not hide an earlier same-lane warning", () => {
  const { scenario, actor } = actorWithChange(),
    later = defaultAction("lane_change", actor, map, 8);
  later.target_lane = -2;
  later.trigger.value = 6;
  actor.actions.push(later);
  assert.equal(laneChangeWarnings(scenario).length, 1);
});
test("graphical waypoints derive segment heading and monotonically append absolute times", () => {
  const first: TimedPose = { x: 5, y: -4, heading_rad: 0, time_s: 1 };
  const next = appendWaypoint(
    [first],
    { x: 5, y: 0, heading_rad: 0 },
    8,
  ) as TimedPose;
  assert.equal(next.heading_rad, Math.PI / 2);
  assert.equal(next.time_s, 2);
  assert.throws(
    () =>
      appendWaypoint(
        [{ ...first, time_s: 8 }],
        { x: 7, y: 0, heading_rad: 0 },
        8,
      ),
    /종료 시각/,
  );
  assert.equal(
    "time_s" in appendWaypoint([first], { x: 7, y: 0, heading_rad: 0 }),
    false,
  );
});
test("timed path validation catches duplicate/out-of-duration timestamps and nonfinite poses", () => {
  const poses: TimedPose[] = [
    { x: 1, y: 2, heading_rad: 0, time_s: 1 },
    { x: 2, y: 3, heading_rad: 0, time_s: 2 },
  ];
  assert.equal(waypointIssue(poses, 8), null);
  assert.match(waypointIssue([poses[0]], 8)!, /2개/);
  assert.match(
    waypointIssue([poses[0], { ...poses[1], time_s: 1 }], 8)!,
    /엄격히 증가/,
  );
  assert.match(
    waypointIssue([poses[0], { ...poses[1], time_s: 9 }], 8)!,
    /종료 시각/,
  );
  assert.match(waypointIssue([{ ...poses[0], x: NaN }])!, /유한/);
});
test("actor removal discovers other actor triggers, observers and sweep references", () => {
  const { scenario, actor } = actorWithChange();
  scenario.actors[0].actions.push({
    ...defaultAction("stop", scenario.actors[0], map, 8),
    trigger: {
      kind: "distance",
      actor_id: "ego",
      target_actor_id: actor.id,
      value: 2,
      rule: "lessThan",
      state: "speed",
    },
  });
  scenario.observers.push({
    kind: "maximum_speed",
    actor_id: actor.id,
    threshold: 20,
    goal: null,
  });
  scenario.sweep.push({
    actor_id: actor.id,
    field: "speed_mps",
    values: [5, 6],
  });
  assert.equal(actorReferences(scenario, actor.id), 3);
});
