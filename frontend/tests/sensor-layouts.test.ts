import test from "node:test";
import assert from "node:assert/strict";
import { layoutMountError, layoutQuery, layoutRef, layoutResultMatches, layoutSensor, mountChanges, mountMatrix, mountPoint, sameLayoutRef } from "../src/components/sensor-layouts/layout-contract.ts";
import type { LayoutExecution, LayoutRig, LayoutSensor, SensorLayout } from "../src/components/sensor-layouts/layout-contract.ts";
import type { CaptureEvidence } from "../src/components/sensor-streams/types.ts";
import { sensorDemoObservedChange } from "../src/components/sensor-demo/demo-contract.ts";

function fixture() {
  const a: LayoutRig = { sensors: ["front", "rear", "lidar", "radar"].map((key, index) => ({ key, kind: index < 2 ? "rgb" : key, translation_m: [1.5, 0, 1.8], rotation_deg: [0, key === "rear" ? 180 : 0, 0], rotation_order: "pitch_yaw_roll_right_hand_rule", parameters: {} })) as LayoutSensor[] };
  const b = structuredClone(a); b.sensors[0].rotation_deg[1] = -15;
  const layout = { id: "layout-one", revision: 2, sha256: "a".repeat(64), project_id: "project-one", rig_a: a, rig_b: b, seed: 42, adapter_id: "carla-driving-multisensor" } as SensorLayout;
  const link = { layout_ref: layoutRef(layout), execution_id: "run-one", input_hash: "b".repeat(64), execution: { id: "run-one", input_hash: "b".repeat(64), status: "completed", project_id: "project-one", adapter_id: layout.adapter_id } } as LayoutExecution;
  const captures = [a, b].map((rig, index) => ({ stage: index ? "rig_b" : "rig_a", seed: 42, motion: {}, sensors: rig.sensors.map(sensor => ({ key: sensor.key, kind: sensor.kind, mountRh: mountMatrix(sensor), parameters: sensor.kind === "rgb" ? { fov: "90" } : { range: "80" } })) })) as CaptureEvidence[];
  return { a, b, layout, link, captures };
}
function near(actual: number[], expected: number[]) { actual.forEach((value, index) => assert.ok(Math.abs(value - expected[index]) < 1e-10, `${actual} != ${expected}`)); }

test("placement coordinates use right-handed X forward, Y left, Z up", () => {
  const sensor = fixture().a.sensors[0];
  near(mountPoint(sensor, [1, 0, 0]), [2.5, 0, 1.8]);
  near(mountPoint({ ...sensor, rotation_deg: [0, 90, 0] }, [1, 0, 0]), [1.5, 1, 1.8]);
  near(mountPoint({ ...sensor, rotation_deg: [90, 0, 0] }, [1, 0, 0]), [1.5, 0, .8]);
  near(mountPoint({ ...sensor, rotation_deg: [0, 0, 90] }, [0, 1, 0]), [1.5, 0, 2.8]);
});

test("rear yaw turns optical forward backwards; coupled rotations stay proper", () => {
  near(mountPoint(fixture().a.sensors[1], [1, 0, 0]), [.5, 0, 1.8]);
  const sensor = { ...fixture().a.sensors[0], rotation_deg: [25, -70, 40] as [number, number, number] };
  const matrix = mountMatrix(sensor);
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) assert.ok(Math.abs(matrix[i].slice(0, 3).reduce((sum, value, k) => sum + value * matrix[j][k], 0) - (i === j ? 1 : 0)) < 1e-10);
});

test("numeric mounts accept .05m and whole degrees, reject partial or out-of-range edits", () => {
  const { b } = fixture(); b.sensors[0].translation_m[1] = -.05; assert.equal(layoutMountError(b), null);
  for (const value of [NaN, Infinity, -10.05, 10.05, .03]) { const rig = structuredClone(b); rig.sensors[0].translation_m[1] = value; assert.ok(layoutMountError(rig)); }
  for (const value of [NaN, .5, -181, 181]) { const rig = structuredClone(b); rig.sensors[0].rotation_deg[0] = value; assert.ok(layoutMountError(rig)); }
  const duplicate = structuredClone(b); duplicate.sensors[3].key = "front"; assert.ok(layoutMountError(duplicate));
});

test("mount delta lists only edited components while retaining the source baseline", () => {
  const { a, b } = fixture(); b.sensors[2].translation_m[2] = 2.5;
  assert.deepEqual(mountChanges(a, b), [{ sensor_key: "front", field: "rotation_deg", axis: 1, baseline: 0, value: -15 }, { sensor_key: "lidar", field: "translation_m", axis: 2, baseline: 1.8, value: 2.5 }]);
  assert.equal(a.sensors[2].translation_m[2], 1.8);
});

test("placement presenter accepts native mount changes without weakening the legacy parameter-only guard", () => {
  const f = fixture(); assert.doesNotThrow(() => layoutResultMatches(f.captures, f.layout, f.link));
  assert.throws(() => sensorDemoObservedChange(f.captures, "driving_camera_fov"), /저장된 rig 구성이 다릅니다/);
});

for (const [label, mutate] of [
  ["different layout revision", (f: ReturnType<typeof fixture>) => f.link.layout_ref.revision++],
  ["different layout digest", (f: ReturnType<typeof fixture>) => f.link.layout_ref.sha256 = "c".repeat(64)],
  ["different execution input digest", (f: ReturnType<typeof fixture>) => f.link.execution.input_hash = "c".repeat(64)],
  ["another project", (f: ReturnType<typeof fixture>) => f.link.execution.project_id = "project-two"],
  ["actual mount differs from saved input", (f: ReturnType<typeof fixture>) => f.captures[1].sensors[0].mountRh[0][3] += .05],
  ["parameter changes mixed into placement", (f: ReturnType<typeof fixture>) => f.captures[1].sensors[0].parameters.fov = "70"],
  ["stationary data labelled driving", (f: ReturnType<typeof fixture>) => delete f.captures[1].motion],
] as const) test(`placement presenter refuses ${label}`, () => { const f = fixture(); mutate(f); assert.throws(() => layoutResultMatches(f.captures, f.layout, f.link)); });

test("saved layout identity includes hash and revision; edited revisions invalidate a reviewed source", () => {
  const ref = layoutRef(fixture().layout); assert.equal(sameLayoutRef(ref, { ...ref }), true);
  assert.equal(sameLayoutRef(ref, { ...ref, revision: 3 }), false); assert.equal(sameLayoutRef(ref, { ...ref, sha256: "changed" }), false);
});

test("layout URLs preserve task and guide while pinning exact source, sensor, time and observation", () => {
  const href = layoutQuery("#sensor-sim?project=p&mode=demo&beta_task=t&guide=lidar_height", { layout: "l", revision: "2", result: "e", time_s: "9.9", sensor: "lidar", observation: "o" });
  const query = new URLSearchParams(href.split("?")[1]);
  assert.deepEqual(Object.fromEntries(query), { project: "p", mode: "layout", beta_task: "t", guide: "lidar_height", layout: "l", revision: "2", result: "e", time_s: "9.9", sensor: "lidar", observation: "o" });
  const next = new URLSearchParams(layoutQuery(href, { observation: null, sensor: "radar" }).split("?")[1]); assert.equal(next.has("observation"), false); assert.equal(next.get("result"), "e"); assert.equal(next.get("beta_task"), "t");
  assert.equal(layoutSensor("wrong"), "front"); assert.equal(layoutSensor("rear"), "rear");
  assert.equal(layoutSensor(null, "lidar_height"), "lidar"); assert.equal(layoutSensor(null, "radar_direction"), "radar");
  assert.equal(layoutSensor(null, "camera_direction"), "front"); assert.equal(layoutSensor("rear", "lidar_height"), "rear");
});
