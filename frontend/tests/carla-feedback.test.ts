import test from "node:test";
import assert from "node:assert/strict";
import { feedbackInputTemplate, parseFeedbackEvidence } from "../src/pages/carla-feedback.ts";

// Authored fixtures exercise evidence rejection only; they are never simulator acceptance.
function fixture(rear = false) {
  const cameraKeys = rear ? ["front", "rear"] : ["front"], checksum = "a".repeat(64);
  const state = (index: number) => ({ x_m: index * .1, y_m: 3, z_m: .2, yaw_rad: 0, speed_mps: index * .05 });
  const command = { throttle: .3, brake: 0, steer: -.02 };
  const ticks = Array.from({ length: 20 }, (_, index) => ({ index, input_frame: 100 + index, input_timestamp_seconds: (1000 + index * 100) / 1000, input_ego: state(index), planned_path: [{ x_m: index * .1, y_m: 3, z_m: .2 }, { x_m: index * .1 + 6, y_m: 3, z_m: .2 }], requested_control: command, acknowledged_control: command, actuator_acknowledged: true, frame: 101 + index, timestamp_seconds: (1100 + index * 100) / 1000, simulation_timestamp_ns: String(1100000000 + index * 100000000), ego: state(index + 1), observed_control: command, rgb_keys: cameraKeys }));
  const records = ticks.flatMap(tick => cameraKeys.map(key => ({ index: tick.index, key, frame: tick.frame, timestamp_seconds: tick.timestamp_seconds, simulation_timestamp_ns: tick.simulation_timestamp_ns, sensor_blueprint: "sensor.camera.rgb", width: 640, height: 360, raw_artifact: { name: `frames/${String(tick.index).padStart(4, "0")}-${key}.bgra8`, size_bytes: 640 * 360 * 4, sha256: checksum, media_type: "application/octet-stream" } })));
  return { manifest: { schema_version: "carla-feedback-native-result/1", status: "completed", cleanup_status: "confirmed", world_settings_restored: true, camera_is_policy_input: false, frame_count: 20, distance_travelled_m: 2, configuration_hash: checksum, configuration_revision: 1, seed: 42, probe: { map: "Town10HD_Opt" }, sensors: cameraKeys.map(key => ({ key })), trace_artifact: { name: "feedback-trace.json", size_bytes: 5000, sha256: checksum, media_type: "application/json" }, records }, trace: { schema_version: "carla-feedback-trace/1", coordinate_convention: "left_handed_x_forward_y_right_z_up", camera_is_policy_input: false, configuration_hash: checksum, configuration_revision: 1, seed: 42, controller: { id: "native-map-lane-follow/1", target_speed_mps: 4 }, ticks } };
}

test("coherent response/camera evidence preserves native coordinates, controls, clock and motion", () => {
  const input = fixture(), actual = parseFeedbackEvidence(input.manifest, input.trace);
  assert.equal(actual.ticks.length, 20); assert.equal(actual.cameras.length, 20); assert.deepEqual(actual.cameraKeys, ["front"]);
  assert.equal(actual.ticks[4].input_frame + 1, actual.cameras[4].frame);
  assert.equal(actual.ticks[4].ego.y_m, 3); assert.equal(actual.ticks[4].requested_control.steer, -.02);
  assert.ok(Math.abs(actual.distance - 2) < 1e-10);
});
test("two cameras bind every output frame to the same physics response", () => {
  const input = fixture(true), actual = parseFeedbackEvidence(input.manifest, input.trace);
  assert.equal(actual.cameras.length, 40); assert.deepEqual(actual.cameraKeys, ["front", "rear"]);
  assert.equal(actual.cameras[9].frame, actual.ticks[4].frame);
});

for (const [name, corrupt] of [
  ["different configuration revision", (v: ReturnType<typeof fixture>) => { v.trace.configuration_revision = 2; }],
  ["different configuration digest", (v: ReturnType<typeof fixture>) => { v.trace.configuration_hash = "b".repeat(64); }],
  ["camera-input policy claim", (v: ReturnType<typeof fixture>) => { v.trace.camera_is_policy_input = true; }],
  ["right-handed trace relabel", (v: ReturnType<typeof fixture>) => { v.trace.coordinate_convention = "right_handed_x_forward_y_left_z_up"; }],
  ["unconfirmed cleanup", (v: ReturnType<typeof fixture>) => { v.manifest.cleanup_status = "failed"; }],
  ["unrestored world", (v: ReturnType<typeof fixture>) => { v.manifest.world_settings_restored = false; }],
  ["missing command acknowledgement", (v: ReturnType<typeof fixture>) => { v.trace.ticks[4].actuator_acknowledged = false; }],
  ["camera from input instead of response", (v: ReturnType<typeof fixture>) => { v.manifest.records[4].frame--; }],
  ["camera from another time at same index", (v: ReturnType<typeof fixture>) => { v.manifest.records[4].timestamp_seconds += .1; }],
  ["duplicate camera replacing a missing frame", (v: ReturnType<typeof fixture>) => { v.manifest.records[4] = v.manifest.records[3]; }],
  ["missing continuous frame", (v: ReturnType<typeof fixture>) => { v.manifest.records.splice(4, 1); }],
  ["noncausal next input despite plausible frame", (v: ReturnType<typeof fixture>) => { v.trace.ticks[5].input_ego.x_m += 1; }],
  ["wrong physical tick period", (v: ReturnType<typeof fixture>) => { v.trace.ticks[4].input_timestamp_seconds -= .05; }],
  ["bad native file index", (v: ReturnType<typeof fixture>) => { v.manifest.records[4].raw_artifact.name = "frames/0005-front.bgra8"; }],
  ["invalid source hash", (v: ReturnType<typeof fixture>) => { v.manifest.records[4].raw_artifact.sha256 = "unverified"; }],
  ["incomplete original pixels", (v: ReturnType<typeof fixture>) => { v.manifest.records[4].raw_artifact.size_bytes--; }],
  ["distance metric contradicting actual physics", (v: ReturnType<typeof fixture>) => { v.manifest.distance_travelled_m = 7; }],
  ["nonfinite plan", (v: ReturnType<typeof fixture>) => { v.trace.ticks[4].planned_path[0].x_m = NaN; }],
  ["excessive actuator output", (v: ReturnType<typeof fixture>) => { v.trace.ticks[4].observed_control = { throttle: 1.01, brake: 0, steer: 0 }; }],
] as const) test(`rejects ${name} before a synchronized viewer can display it`, () => {
  const input = fixture(); corrupt(input); assert.throws(() => parseFeedbackEvidence(input.manifest, input.trace), /CARLA feedback:/);
});

test("guided defaults use explicit reference control and standard user sensor declarations", () => {
  const input = feedbackInputTemplate();
  assert.equal(input.duration_seconds, 4); assert.equal(input.sample_hz, 10); assert.equal(input.controller.id, "native-map-lane-follow/1");
  assert.equal(input.sensors[0].key, "front"); assert.equal(input.expected_server_version, "1ae5356");
  assert.equal("seed" in input, false); assert.equal("native_parameters" in input.sensors[0], false);
});
