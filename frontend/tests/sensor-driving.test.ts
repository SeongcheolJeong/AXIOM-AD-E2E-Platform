import test from "node:test";
import assert from "node:assert/strict";
import { parseCaptureManifest, parseCaptureReceipt } from "../src/components/sensor-streams/parser.ts";
import { initialSensorPreset, sensorDemoObservedChange, sensorPresetAdapter } from "../src/components/sensor-demo/demo-contract.ts";
import type { SensorDemoPreset } from "../src/components/sensor-demo/demo-contract.ts";
import { contractFixture } from "./sensor-streams-fixture.ts";

const clone = <T>(value: T): T => structuredClone(value);
function drivingFixture() {
  const fixture = contractFixture(200), manifest = fixture.manifest, prepared = fixture.sealed.prepared as Record<string, any>, rig = prepared.input.rigs.single;
  const version = "carla-driving-multisensor/1", fidelity = "actual_carla_reference_driving_multisensor";
  const controller = { id: "native-map-lane-follow/1", target_speed_mps: 3, lookahead_m: 6, route_length_m: 150 };
  rig.schema_version = "carla-driving-multisensor-spec/1"; rig.controller = controller;
  fixture.execution.adapter_id = "carla-driving-multisensor"; fixture.execution.adapter_version = version; fixture.execution.fidelity = fidelity;
  Object.assign(prepared.spec, { adapter_id: fixture.execution.adapter_id, adapter_version: version });
  Object.assign(prepared.handler, { id: fixture.execution.adapter_id, version, fidelity });
  prepared.input.schema_version = "carla-driving-multisensor-experiment/1";
  const route = { schema_version: "carla-native-map-route/1", coordinate_convention: manifest.native_coordinate_convention, map_opendrive_sha256: "a".repeat(64), spawn_index: 0, configuration_revision: 1, sha256: "b".repeat(64), points: Array.from({ length: 76 }, (_, i) => ({ x_m: i * 2, y_m: 0, z_m: 0 })) };
  Object.assign(manifest.probe, { route, native_support_script_sha256: "c".repeat(64), native_feedback_script_sha256: "d".repeat(64), native_scene_script_sha256: "e".repeat(64) });
  prepared.input.native_capability_snapshots = { single: clone(manifest.probe) }; delete prepared.input.native_capability_snapshot;
  const ego = (x: number, speed: number) => ({ x_m: x, y_m: 0, z_m: 0, yaw_rad: 0, speed_mps: speed, velocity_mps: [speed, 0, 0], world_transform: { matrix: [[1, 0, 0, x], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]] } });
  manifest.warmup = Array.from({ length: 10 }, (_, i) => ({ frame: 90 + i, timestamp_seconds: (i - 10) / 10, ego: ego(0, 0) }));
  Object.assign(manifest, { schema_version: "carla-driving-multisensor-result/1", adapter_version: version, fidelity, controller, ego_physics_enabled: true, camera_is_policy_input: false, sensors_are_policy_input: false, warmup_policy: "10_native_braked_settling_frames_all_sensor_queues_drained/1", ego_initial_world_frame: 99, ego_initial_timestamp_seconds: -.1 });
  for (const row of manifest.records) {
    const x = (row.index + 1) / 10;
    row.ego_world_transform = ego(x, 1).world_transform;
    for (const key of ["sensor_world_transform", "sensor_snapshot_world_transform", "sensor_api_world_transform"]) row[key].matrix[0][3] += x;
    row.mount_qualification.translation_tolerance_m = 2e-5 + 16 * 2 ** -23 * Math.max(1, x, x + 3.3);
  }
  const control = { throttle: .2, brake: 0, steer: 0 };
  manifest.motion = { schema_version: "carla-driving-motion-trace/1", adapter_version: version, configuration_hash: manifest.configuration_hash, rig_revision: 1, seed: 42, coordinate_convention: manifest.native_coordinate_convention, controller, route, camera_is_policy_input: false, sensors_are_policy_input: false,
    ticks: Array.from({ length: 200 }, (_, i) => ({ index: i, input_frame: 99 + i, input_timestamp_seconds: (i - 1) / 10, input_ego: ego(i / 10, i ? 1 : 0), frame: 100 + i, timestamp_seconds: i / 10, simulation_timestamp_ns: String(i * 100000000), ego: ego((i + 1) / 10, 1), requested_control: { ...control }, acknowledged_control: { ...control }, observed_control: { ...control }, actuator_acknowledged: true, sensor_keys: ["front", "rear", "lidar", "radar"] })) };
  manifest.distance_travelled_m = 20; manifest.maximum_speed_mps = 1;
  manifest.callback_drain.started = manifest.callback_drain.finished = 840;
  manifest.engine_interlock = { lock_acquired: true, reservation_cleared_after_confirmed_cleanup: true };
  manifest.cleanup_observation = { status: "confirmed", controlled_cleanup_tick_count: 1, observed_dynamic_actor_ids: [], owned_snapshot_absent_actor_ids: [1, 2, 3, 4, 5], input_frame: 299, frame: 300 };
  fixture.sealed.execution = { ...clone(fixture.execution), manifest_hash: null, download_url: null };
  return fixture;
}

test("driving manifest ties all four streams to same moving ego response and preserves measured velocity", () => {
  const f = drivingFixture(), pin = parseCaptureReceipt(f.sealed, f.execution, f.rigHashes), capture = parseCaptureManifest(f.manifest, pin, "single");
  assert.equal(pin.driving, true); assert.equal(capture.ticks.length, 200);
  assert.deepEqual(capture.ticks[199].records.map(r => r.kind), ["rgb", "rgb", "lidar", "radar"]);
  assert.equal(capture.motion?.ticks[99].speedMps, 1); assert.equal(capture.motion?.distanceM, 20);
  assert.equal(capture.ticks[199].records[0].egoWorld[0][3], 20);
});
for (const [name, mutate] of [
  ["sensor world pose differs from corresponding moving response", (m: any) => m.motion.ticks[20].ego.world_transform.matrix[0][3] += 1],
  ["future input frame", (m: any) => m.motion.ticks[20].input_frame += 1],
  ["altered native velocity", (m: any) => m.motion.ticks[20].ego.speed_mps = 20],
  ["missing sensor in motion denominator", (m: any) => m.motion.ticks[20].sensor_keys.pop()],
  ["unacknowledged control", (m: any) => m.motion.ticks[20].actuator_acknowledged = false],
  ["control readback changed", (m: any) => m.motion.ticks[20].observed_control.throttle = .9],
  ["altered aggregate distance", (m: any) => m.distance_travelled_m = 200],
  ["warmup replaced by moving sample", (m: any) => m.ego_initial_transform = m.records[0].ego_world_transform],
] as const) test(`driving capture rejects ${name}`, () => { const f = drivingFixture(), pin = parseCaptureReceipt(f.sealed, f.execution, f.rigHashes); mutate(f.manifest); assert.throws(() => parseCaptureManifest(f.manifest, pin, "single"), /Sensor streams/); });

test("only ready driving preset becomes default; explicit legacy selection is retained", () => {
  const old = { id: "camera_fov", ready: true } as SensorDemoPreset, driving = { id: "driving_camera_fov", ready: true, source: { adapter_id: "carla-driving-multisensor" } } as SensorDemoPreset;
  assert.equal(initialSensorPreset([old, driving], null), driving); assert.equal(initialSensorPreset([old, driving], "camera_fov"), old);
  assert.equal(initialSensorPreset([old, { ...driving, ready: false }], null), old); assert.equal(sensorPresetAdapter(old), "carla-streaming-capture");
});

test("driving preset result uses same visible native camera delta with separate driving fidelity", () => {
  const f = drivingFixture(), pin = parseCaptureReceipt(f.sealed, f.execution, f.rigHashes), a = parseCaptureManifest(f.manifest, pin, "single"), b = clone(a);
  a.stage = "rig_a"; b.stage = "rig_b"; b.sensors[0].parameters.fov = "70";
  assert.equal(sensorDemoObservedChange([a, b], "driving_camera_fov").value, 70);
});
