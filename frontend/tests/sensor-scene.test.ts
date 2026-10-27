import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { parseFeedbackEvidence } from "../src/pages/carla-feedback.ts";
import { boxWorldCenter, laneBoundary, multiplyMatrices, nativeToRenderMatrix, nativeToRenderPoint, parseSensorScene, rigidMatrix, validateMapMembership } from "../src/components/sensor-scene/sensor-scene.ts";

// Producer-authored mock contracts exercise rejection and geometry only, not native sensor acceptance.
function fixture() {
  const value = JSON.parse(readFileSync(new URL("../../integrations/carla/tests/fixtures/sensor-scene-mocked.json", import.meta.url), "utf8"));
  const artifacts = [value.manifest.map_artifact, value.manifest.scene_artifact, value.manifest.trace_artifact].map((a: Record<string, unknown>, index: number) => ({ ...a, id: `artifact_${index}`, download_url: `/api/v1/experiments/executions/mock/artifacts/artifact_${index}` }));
  return { ...value, artifacts };
}
function parse(value: ReturnType<typeof fixture>) { return parseSensorScene(value.scene, value.manifest, value.trace, parseFeedbackEvidence(value.manifest, value.trace), value.artifacts); }
test("new native scene binds local lanes, exact bbox, original camera matrices and all response clocks", () => { const v = fixture(), scene = parse(v); assert.equal(scene.frames.length, 20); assert.equal(scene.map.lanes.length, 1); assert.equal(scene.map.sampleCount, 41); assert.equal(scene.ego.actorId, v.manifest.ego_actor_id); assert.deepEqual(scene.frames[8].cameras[0].sensor_world_transform.matrix, v.manifest.records[8].sensor_world_transform.matrix); validateMapMembership(v.map_xodr, scene.map.lanes); });
for (const [name, change] of [
  ["foreign configuration", (v: ReturnType<typeof fixture>) => { v.scene.source.configuration_hash = "0".repeat(64); }],
  ["foreign scene seed", (v: ReturnType<typeof fixture>) => { v.scene.source.seed++; }],
  ["foreign external execution", (v: ReturnType<typeof fixture>) => { v.scene.source.external_execution_id = "another"; }],
  ["unsealed map hash", (v: ReturnType<typeof fixture>) => { v.scene.map.sha256 = "0".repeat(64); }],
  ["same index, different response frame", (v: ReturnType<typeof fixture>) => { v.scene.frames[4].frame--; }],
  ["same frame, different camera clock", (v: ReturnType<typeof fixture>) => { v.scene.frames[4].cameras[0].timestamp_seconds += .1; }],
  ["altered ego matrix", (v: ReturnType<typeof fixture>) => { v.scene.frames[4].ego_world_transform.matrix[0][3] += 1; }],
  ["altered camera matrix", (v: ReturnType<typeof fixture>) => { v.scene.frames[4].cameras[0].sensor_world_transform.matrix[1][3] += 1; }],
  ["invented native bbox", (v: ReturnType<typeof fixture>) => { v.scene.ego.native_bounding_box.extent_m[0] += .2; }],
  ["unsupported coordinates", (v: ReturnType<typeof fixture>) => { v.scene.coordinate_convention = "world_m_x_forward_y_left_z_up"; }],
  ["lane across missing source gap", (v: ReturnType<typeof fixture>) => { v.scene.map.lane_segments[0].points[4].s_m += 6; }],
  ["lane beyond original ROI", (v: ReturnType<typeof fixture>) => { v.scene.map.lane_segments[0].points[4].x_m += 1e4; }],
  ["infinite lane width", (v: ReturnType<typeof fixture>) => { v.scene.map.lane_segments[0].points[4].lane_width_m = Infinity; }],
  ["duplicate lane identity", (v: ReturnType<typeof fixture>) => { v.scene.map.lane_segments.push(v.scene.map.lane_segments[0]); }],
  ["missing original scene response", (v: ReturnType<typeof fixture>) => { v.scene.frames.pop(); }],
  ["foreign sensor mounting", (v: ReturnType<typeof fixture>) => { v.scene.cameras[0].mount_native.position_m[0]++; }],
] as const) test(`rejects ${name} before connected rendering`, () => { const v = fixture(); change(v); assert.throws(() => parse(v), /Sensor scene:/); });
test("map membership does not accept XML entities or a native lane absent from the original", () => { const v = fixture(), scene = parse(v); assert.throws(() => validateMapMembership(v.map_xodr.replace('id="-1"', 'id="-2"'), scene.map.lanes), /membership/); assert.throws(() => validateMapMembership('<!DOCTYPE OpenDRIVE [<!ENTITY x SYSTEM "file:///tmp/x">]>' + v.map_xodr, scene.map.lanes), /unsupported/); });
test("CARLA LH conjugation preserves nonzero pitch/yaw/roll and positive Y maps to render Z", () => {
  const yaw = Math.PI / 2, matrix = [[Math.cos(yaw), -Math.sin(yaw), 0, 10], [Math.sin(yaw), Math.cos(yaw), 0, 20], [0, 0, 1, 3], [0, 0, 0, 1]];
  const rendered = nativeToRenderMatrix(rigidMatrix(matrix)); assert.deepEqual(rendered.map(row => row[3]), [10, 3, 20, 1]); assert.ok(Math.abs(rendered[2][0] - 1) < 1e-10); assert.ok(Math.abs(rendered[0][2] + 1) < 1e-10);
  assert.deepEqual(nativeToRenderPoint({ x_m: 1, y_m: 2, z_m: 3 }), [1, 3, 2]);
  const pitchRoll = [[0, 0, 1, 4], [1, 0, 0, 5], [0, 1, 0, 6], [0, 0, 0, 1]];
  assert.deepEqual(nativeToRenderMatrix(nativeToRenderMatrix(pitchRoll)), pitchRoll);
  assert.deepEqual(nativeToRenderMatrix(multiplyMatrices(matrix, pitchRoll)), multiplyMatrices(nativeToRenderMatrix(matrix), nativeToRenderMatrix(pitchRoll)));
});
test("bbox world center uses native rotated offset, lane edges use each saved yaw and width", () => { const v = fixture(), scene = parse(v), center = boxWorldCenter(scene.frames[0], scene), m = scene.frames[0].ego_world_transform.matrix, offset = scene.ego.localBox.position_m; for (const [i, key] of ["x_m", "y_m", "z_m"].entries()) assert.ok(Math.abs(center[key as keyof typeof center] - (m[i][3] + m[i].slice(0, 3).reduce((sum, n, j) => sum + n * offset[j], 0))) < 1e-10); const p = { x_m: 10, y_m: 20, z_m: 2, yaw_rad: Math.PI / 2, lane_width_m: 4, s_m: 0, is_junction: false }; assert.deepEqual(laneBoundary(p, 1), { x_m: 8, y_m: 20, z_m: 2 }); assert.deepEqual(laneBoundary(p, -1), { x_m: 12, y_m: 20, z_m: 2 }); });
test("single native lane point remains a point and never becomes a made-up strip", () => { const v = fixture(); v.scene.map.lane_segments[0].points = [v.scene.map.lane_segments[0].points[0]]; v.scene.map.sample_count = 1; assert.equal(parse(v).map.lanes[0].points.length, 1); });
test("unwrapped native lane angles within the producer domain are preserved, not normalized or clipped", () => { const v = fixture(); v.scene.map.lane_segments[0].points[0].yaw_rad = -9.421998685855772; v.scene.map.lane_segments[0].points[1].yaw_rad = 7.860688006231521; const scene = parse(v); assert.equal(scene.map.lanes[0].points[0].yaw_rad, -9.421998685855772); assert.equal(scene.map.lanes[0].points[1].yaw_rad, 7.860688006231521); v.scene.map.lane_segments[0].points[0].yaw_rad = -4 * Math.PI; assert.equal(parse(v).map.lanes[0].points[0].yaw_rad, -4 * Math.PI); v.scene.map.lane_segments[0].points[0].yaw_rad = 4 * Math.PI + .001; assert.throws(() => parse(v), /lane yaw/); });
test("producer longitudinal tolerance and native dimensions remain executable at exact supported bounds", () => { const v = fixture(); v.scene.map.lane_segments[0].points[0].s_m = -.001; const box = v.scene.ego.native_bounding_box; box.extent_m = [30, .001, .001]; box.dimensions_m = [60, .002, .002]; v.manifest.ego_bounding_box = structuredClone(box); const scene = parse(v); assert.deepEqual(scene.ego.extent, [30, .001, .001]); validateMapMembership(v.map_xodr, scene.map.lanes); v.scene.map.lane_segments[0].points[0].s_m = -.00101; assert.throws(() => parse(v), /lane S/); });
test("native width, original road length and actor dimensions reject values outside producer bounds", () => { const v = fixture(); v.scene.map.lane_segments[0].points[0].lane_width_m = .0999; assert.throws(() => parse(v), /lane width/); v.scene.map.lane_segments[0].points[0].lane_width_m = .1; const scene = parse(v); scene.map.lanes[0].points[0].s_m = 1000.1001; assert.throws(() => validateMapMembership(v.map_xodr, scene.map.lanes), /longitudinal bounds/); v.scene.ego.native_bounding_box.extent_m[0] = 30.001; v.scene.ego.native_bounding_box.dimensions_m[0] = 60.002; v.manifest.ego_bounding_box = structuredClone(v.scene.ego.native_bounding_box); assert.throws(() => parse(v), /box extent/); });

const actualOriginals = process.env.AXIOM_SENSOR_SCENE_ACTUAL_ORIGINALS;
test("actual retained CARLA originals parse 78 lane segments / 1004 samples and preserve unwrapped native yaw", { skip: !actualOriginals }, () => {
  const load = (name: string) => JSON.parse(readFileSync(join(actualOriginals!, name), "utf8")), sceneOriginal = load("sensor-scene.json"), manifest = load("feedback-capture-manifest.json"), trace = load("feedback-trace.json"), execution = load("execution.json").execution;
  const scene = parseSensorScene(sceneOriginal, manifest, trace, parseFeedbackEvidence(manifest, trace), execution.artifacts);
  validateMapMembership(readFileSync(join(actualOriginals!, "map.xodr"), "utf8"), scene.map.lanes);
  assert.equal(scene.map.lanes.length, 78); assert.equal(scene.map.sampleCount, 1004); assert.equal(scene.frames.length, 40);
  const originalPoints = sceneOriginal.map.lane_segments.flatMap((lane: { points: unknown[] }) => lane.points), parsedPoints = scene.map.lanes.flatMap(lane => lane.points);
  assert.deepEqual(parsedPoints, originalPoints);
  assert.equal(Math.min(...parsedPoints.map(p => p.yaw_rad)), -9.421998685855772); assert.equal(Math.max(...parsedPoints.map(p => p.yaw_rad)), 7.860688006231521);
  const altered = structuredClone(sceneOriginal); altered.map.lane_segments[0].points[0].yaw_rad = 4 * Math.PI + .001; assert.throws(() => parseSensorScene(altered, manifest, trace, parseFeedbackEvidence(manifest, trace), execution.artifacts), /lane yaw/);
  const foreign = structuredClone(sceneOriginal); foreign.frames[1].cameras[0].frame++; assert.throws(() => parseSensorScene(foreign, manifest, trace, parseFeedbackEvidence(manifest, trace), execution.artifacts), /response camera frame/);
});
