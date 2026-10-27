import test from "node:test";
import assert from "node:assert/strict";
import { parseSensorJson } from "../src/components/sensor-streams/strict-json.ts";
import { nativeTimestampNs, parseCaptureManifest, parseCaptureReceipt, relativeRhMatrix, rigidMatrix } from "../src/components/sensor-streams/parser.ts";
import { decodeNativePoints } from "../src/components/sensor-streams/decode-points.ts";
import { contractFixture } from "./sensor-streams-fixture.ts";
import type { CaptureRecord } from "../src/components/sensor-streams/types.ts";

test("authored unit contract preserves all four original stream records and exact frame denominator", () => { const f = contractFixture(), pin = parseCaptureReceipt(f.sealed, f.execution, f.rigHashes), e = parseCaptureManifest(f.manifest, pin, "single"); assert.equal(e.ticks.length, 100); assert.deepEqual(e.ticks[20].records.map(r => r.kind), ["rgb", "rgb", "lidar", "radar"]); assert.equal(e.members.length, 404); assert.equal(e.ticks[99].timestampNs, "9900000000"); });
for (const [name, mutate] of [
  ["missing native tick", (m: any) => m.records.splice(4, 4)],
  ["duplicate sensor record", (m: any) => m.records[1] = m.records[0]],
  ["mixed world clock", (m: any) => m.records[2].simulation_timestamp_ns = "1"],
  ["world frame gap", (m: any) => m.records[4].frame += 1],
  ["wrong actor parent", (m: any) => m.records[0].sensor_parent_actor_id = 99],
  ["point count mismatch", (m: any) => m.records[2].point_count = 2],
  ["unknown tuple order", (m: any) => m.records[3].encoding = "depth_azimuth_altitude_velocity"],
  ["forged registered pose", (m: any) => m.records[3].mount_qualification.relative_rh_matrix[0][3] = 8],
  ["sheared measurement", (m: any) => m.records[0].sensor_world_transform.matrix[0][1] = .2],
  ["moving stationary ego", (m: any) => m.records[4].ego_world_transform.matrix[0][3] = 1],
  ["unreferenced original", (m: any) => { m.artifacts.push({ name: "extra.f32", media_type: "application/octet-stream", sha256: "a".repeat(64), size_bytes: 0 }); }],
  ["incomplete cleanup", (m: any) => m.actor_destroy_acknowledgements[0].acknowledged = false],
] as const) test(`capture rejects ${name}`, () => { const f = contractFixture(), pin = parseCaptureReceipt(f.sealed, f.execution, f.rigHashes); mutate(f.manifest); assert.throws(() => parseCaptureManifest(f.manifest, pin, "single"), /Sensor streams/); });
test("sealed execution refuses substituted source artifacts and calibration scope", () => { const f = contractFixture(); f.execution.artifacts[0].sha256 = "b".repeat(64); assert.throws(() => parseCaptureReceipt(f.sealed, f.execution, f.rigHashes), /metadata/); const g = contractFixture(); g.execution.fidelity = "measured_sensor"; assert.throws(() => parseCaptureReceipt(g.sealed, g.execution, g.rigHashes), /scope/); });
test("strict JSON rejects duplicate keys, nonfinite values and excessive depth", () => { assert.throws(() => parseSensorJson('{"records":[],"records":[]}'), /Sensor JSON/); assert.throws(() => parseSensorJson('{"value":1e999}'), /Sensor JSON/); assert.throws(() => parseSensorJson("[".repeat(70) + "0" + "]".repeat(70)), /Sensor JSON/); });
test("strict sealed slices preserve original Python float lexemes", () => { const d = parseSensorJson('{"prepared":{"input":{"rigs":{"single":{"rotation_deg":[0.0,-0.0,1.0]}}}}}'); assert.equal(d.rigSlices.get("single"), '{"rotation_deg":[0.0,-0.0,1.0]}'); assert.match(d.preparedSlice, /0\.0,-0\.0,1\.0/); });
test("native timestamp verification follows original Python ties-to-even", () => { assert.equal(nativeTimestampNs(944078.3430238306), "944078343023830"); assert.equal(nativeTimestampNs(.5e-9), "0"); assert.equal(nativeTimestampNs(1.5e-9), "2"); assert.equal(nativeTimestampNs(1.0000000015), "1000000001"); });
test("pose registration uses observed ego/sensor matrices with explicit LH reflection", () => { const ego = rigidMatrix([[0, -1, 0, 10], [1, 0, 0, 20], [0, 0, 1, 2], [0, 0, 0, 1]]), sensor = rigidMatrix([[0, -1, 0, 10], [1, 0, 0, 21.5], [0, 0, 1, 3.8], [0, 0, 0, 1]]); assert.deepEqual(relativeRhMatrix(ego, sensor).map(row => row.map(n => n === 0 ? 0 : n)), [[1, 0, 0, 1.5], [0, 1, 0, 0], [0, 0, 1, 1.7999999999999998], [0, 0, 0, 1]]); });
function record(kind: "lidar" | "radar", count = 1): CaptureRecord { return { index: 0, key: kind, kind, frame: 1, timestampNs: "0", timestampSeconds: 0, encoding: "unit", member: { name: `frames/0000-${kind}.f32`, size_bytes: count * 16, sha256: "a".repeat(64) }, width: 0, height: 0, pointCount: count, sensorWorld: [], egoWorld: [], relativeRh: [[1, 0, 0, 1.5], [0, 1, 0, 0], [0, 0, 1, 1.8], [0, 0, 0, 1]], cacheKey: "unit" }; }
function bytes(values: number[]) { const b = new Uint8Array(values.length * 4), v = new DataView(b.buffer); values.forEach((n, i) => v.setFloat32(i * 4, n, true)); return b; }
test("LiDAR little-endian tuple keeps raw values and registers all points", () => { const f = decodeNativePoints(bytes([10, 2, -1, .75]), record("lidar")); assert.deepEqual([...f.raw], [10, 2, -1, .75]); assert.deepEqual([...f.positions], [11.5, -2, Math.fround(.8)]); assert.equal(f.count, 1); });
test("radar native overshoot and outlier remain visible, never clipped or corrected", () => { const f = decodeNativePoints(bytes([609.6903, .1, 0, 81.60846]), record("radar")); assert.equal(f.count, 1); assert.ok(f.positions[0] > 80); assert.equal(f.raw[0], Math.fround(609.6903)); assert.equal(f.warnings.length, 1); });
test("valid empty native return is retained without manufactured points", () => { const f = decodeNativePoints(new Uint8Array(0), record("radar", 0)); assert.equal(f.count, 0); assert.equal(f.positions.length, 0); assert.equal(f.valueMin, null); });
test("point decoder refuses truncation, nonfinite tuple and unsupported radar angle", () => { assert.throws(() => decodeNativePoints(new Uint8Array(15), record("lidar")), /stride/); assert.throws(() => decodeNativePoints(bytes([1, NaN, 0, .5]), record("lidar")), /nonfinite/); assert.throws(() => decodeNativePoints(bytes([1, 7, 0, 10]), record("radar")), /angle/); });
