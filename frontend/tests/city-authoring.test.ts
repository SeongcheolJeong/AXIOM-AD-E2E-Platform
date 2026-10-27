import test from "node:test";
import assert from "node:assert/strict";
import {
  catalogRef,
  changedFields,
  closestLanePoint,
  newCityActor,
  newCityScenario,
  nextActorId,
  sameCityConfiguration,
  newCityRgbSensor,
  savedCityScenarioRoute,
} from "../src/components/city-sim/city-contract.ts";
import type {
  CatalogItem,
  CityAsset,
  CityMap,
} from "../src/components/city-sim/city-contract.ts";
// Authored metadata fixtures only: no claim of native simulator execution.
const pose = { x: 10, y: 30, z: 1, heading_rad: 0.4 };
const map: CatalogItem<CityMap> = {
  id: "town01",
  revision: 4,
  sha256: "a".repeat(64),
  project_id: "default",
  definition: {
    name: "Town01",
    lanes: [{ id: "lane1", width_m: 3.5, points: [pose, { ...pose, x: 20 }] }],
    spawn_points: [pose],
  },
};
const asset: CatalogItem<CityAsset> = {
  id: "prius",
  revision: 2,
  sha256: "b".repeat(64),
  project_id: "default",
  definition: { actor_kind: "vehicle", blueprint_id: "vehicle.toyota.prius" },
};
test("saved resume URLs pin the new scenario revision and its exact map", () => {
  const scenario = newCityScenario(map, asset);
  const route = savedCityScenarioRoute({ id: "scenario-a", project_id: "default", revision: 2, scenario, manifest_hash: "c".repeat(64) });
  const query = new URLSearchParams(route.split("?")[1]);
  assert.equal(query.get("mode"), "city"); assert.equal(query.get("scenario"), "scenario-a"); assert.equal(query.get("revision"), "2");
  assert.equal(query.get("map"), map.id); assert.equal(query.get("map_revision"), String(map.revision)); assert.equal(query.get("map_hash"), map.sha256);
  assert.equal(query.has("execution"), false); assert.equal(query.has("source"), false);
});
test("authoring copies immutable catalog references and native CARLA coordinate identity", () => {
  const scenario = newCityScenario(map, asset);
  assert.equal(scenario.schema_version, "scenario-v2/2");
  assert.equal(
    scenario.coordinate_system,
    "carla_world_m_x_forward_y_right_z_up",
  );
  assert.deepEqual(scenario.map, catalogRef(map));
  assert.deepEqual(scenario.actors[0].asset, catalogRef(asset));
  assert.equal(scenario.actors[0].controller.end_behavior, "stop");
  assert.equal(scenario.actors[0].controller.kind, "route_follow");
  assert.deepEqual(
    scenario.actors[0].route,
    [],
    "editor does not invent a native reachable route",
  );
  scenario.actors[0].pose.x = 100;
  assert.equal(map.definition.spawn_points![0].x, 10);
});
test("lane picking retains recorded heading and altitude and refuses distant clicks", () => {
  assert.deepEqual(closestLanePoint(map.definition, { x: 10.5, y: 30 }), pose);
  assert.equal(closestLanePoint(map.definition, { x: 100, y: 100 }), null);
  assert.equal(closestLanePoint(map.definition, { x: 15, y: 30 }, 5), null);
});
test("catalogs without native lane or spawn geometry cannot fabricate a city draft", () => {
  assert.throws(
    () => newCityScenario({ ...map, definition: { lanes: [] } }, asset),
    /차로 좌표/,
  );
});
test("new actor identities remain unique after deletion and static assets stay stationary", () => {
  const ego = newCityScenario(map, asset).actors[0],
    vehicle = newCityActor(asset, pose, [ego]);
  assert.equal(vehicle.id, "vehicle_1");
  assert.equal(nextActorId([vehicle], "vehicle"), "vehicle_2");
  const item = newCityActor(
    { ...asset, definition: { actor_kind: "static" } },
    pose,
    [ego],
  );
  assert.equal(item.controller.kind, "static");
  assert.equal(item.speed_mps, 0);
  assert.equal(item.role, "other");
});
test("revision change summary identifies nested configuration changes without hiding removed fields", () => {
  assert.deepEqual(
    changedFields(
      { seed: 1, environment: { lighting: "day" }, extra: true },
      { seed: 2, environment: { lighting: "day" } },
    ),
    ["scenario.seed", "scenario.extra"],
  );
});
test("validation-only object-key normalization keeps saved actors and sensors clean while route order remains meaningful", () => {
  const saved = newCityScenario(map, asset);
  saved.actors[0].route = [pose, { ...pose, x: 20 }];
  saved.sensors = [
    {
      id: "front",
      actor_id: "ego",
      kind: "camera_rgb",
      width: 320,
      height: 180,
      period_s: 0.2,
      fov_deg: 90,
      pose,
    },
  ];
  function reverseKeys(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(reverseKeys);
    if (value && typeof value === "object")
      return Object.fromEntries(
        Object.entries(value)
          .reverse()
          .map(([key, item]) => [key, reverseKeys(item)]),
      );
    return value;
  }
  const normalized = reverseKeys(saved);
  assert.notEqual(
    JSON.stringify(normalized),
    JSON.stringify(saved),
    "reproduce saved sorted JSON versus API model key order",
  );
  assert.equal(sameCityConfiguration(saved, normalized), true);
  assert.deepEqual(changedFields(saved, normalized), []);
  const changed = structuredClone(saved);
  changed.actors[0].route.reverse();
  assert.equal(
    sameCityConfiguration(saved, changed),
    false,
    "route array order changes intent",
  );
  assert.deepEqual(changedFields(saved, changed), ["scenario.actors"]);
  changed.actors[0].route.reverse();
  changed.sensors[0].period_s = 0.1;
  assert.equal(
    sameCityConfiguration(saved, changed),
    false,
    "capture period change remains dirty",
  );
  assert.deepEqual(changedFields(saved, changed), ["scenario.sensors"]);
  assert.equal(sameCityConfiguration(["sensor"], { 0: "sensor" }), false);
});
test("adding an RGB preset preserves saved sensor IDs, sampling, mounting and custom preset configurations", () => {
  const scenario = newCityScenario(map, asset);
  scenario.sensors = [
    {
      id: "front",
      actor_id: "ego",
      kind: "camera_rgb",
      width: 320,
      height: 180,
      period_s: 0.2,
      pose,
    },
    {
      id: "ego_front_rgb",
      actor_id: "ego",
      kind: "camera_rgb",
      width: 96,
      height: 64,
      period_s: 0.4,
      pose,
    },
  ];
  const original = structuredClone(scenario.sensors),
    added = newCityRgbSensor(scenario);
  assert.equal(added.id, "ego_front_rgb_2");
  assert.deepEqual(
    scenario.sensors,
    original,
    "helper never replaces or removes existing sensor configs",
  );
  scenario.sensors.push(added);
  assert.deepEqual(scenario.sensors.slice(0, 2), original);
  assert.deepEqual(
    [added.width, added.height, added.period_s, added.actor_id],
    [640, 360, 0.1, "ego"],
  );
});
