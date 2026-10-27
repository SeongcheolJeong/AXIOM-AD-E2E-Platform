import test from "node:test";
import assert from "node:assert/strict";
import { executeSensorDemo, sensorDemoParameterError, sensorDemoSignature, sensorDemoTerminal, sensorDemoObservedChange, SensorDemoPreviewChanged } from "../src/components/sensor-demo/demo-contract.ts";
import type { LoadedCapture } from "../src/components/sensor-streams/types.ts";
import type { SensorDemoApi, SensorDemoAttempt, SensorDemoParameter, SensorDemoPreset, SensorDemoPreview } from "../src/components/sensor-demo/demo-contract.ts";

const preview = { preview: { preview_hash: "a".repeat(64) }, experiment: { project_id: "demo", kind: "sensor_capture", comparison_kind: "same_scene_sensor_ab", input: { rig_a: { fov: 90 }, rig_b: { fov: 70 } } } } as SensorDemoPreview;

test("new demo execution preserves exact reviewed A/B create body and checks its preview hash", async () => {
  const bodies: unknown[] = [];
  const request: SensorDemoApi = async <T>(path: string, init?: RequestInit) => { bodies.push(JSON.parse(String(init?.body))); return (path === "/experiments" ? { id: "one", preview_hash: "a".repeat(64) } : { id: "run-one" }) as T; };
  const result = await executeSensorDemo(request, preview, { key: "attempt", signature: "one" }, () => {});
  assert.deepEqual(bodies, [preview.experiment, { preview_hash: "a".repeat(64) }]);
  assert.equal(result.id, "run-one");
});

test("uncertain submission retries the same saved experiment and idempotency key", async () => {
  const calls: { path: string; key: string | null }[] = []; let submissions = 0;
  const request: SensorDemoApi = async <T>(path: string, init?: RequestInit) => {
    calls.push({ path, key: new Headers(init?.headers).get("Idempotency-Key") });
    if (path === "/experiments") return { id: "one", preview_hash: "a".repeat(64) } as T;
    if (++submissions === 1) throw new Error("response lost");
    return { id: "run-one" } as T;
  };
  const attempt: SensorDemoAttempt = { key: "attempt", signature: "one" };
  await assert.rejects(executeSensorDemo(request, preview, attempt, () => {}), /response lost/);
  await executeSensorDemo(request, preview, attempt, () => {});
  await executeSensorDemo(request, preview, attempt, () => {});
  assert.deepEqual(calls, [{ path: "/experiments", key: "attempt-experiment" }, { path: "/experiments/one/executions", key: "attempt-execution" }, { path: "/experiments/one/executions", key: "attempt-execution" }]);
});

test("changed runtime preview never executes a different create result", async () => {
  const calls: string[] = [];
  const request: SensorDemoApi = async <T>(path: string) => { calls.push(path); return { id: "one", preview_hash: "changed" } as T; };
  await assert.rejects(executeSensorDemo(request, preview, { key: "attempt", signature: "one" }, () => {}), SensorDemoPreviewChanged);
  assert.deepEqual(calls, ["/experiments"]);
});

test("edited parameter and binding revision invalidate a reviewed preview signature", () => {
  const preset = { id: "camera_fov", binding_revision: 1, binding_sha256: "a" } as SensorDemoPreset;
  const initial = sensorDemoSignature("project", preset, { front_fov_deg: 70 });
  assert.notEqual(initial, sensorDemoSignature("project", preset, { front_fov_deg: 80 }));
  assert.notEqual(initial, sensorDemoSignature("project", { ...preset, binding_revision: 2 }, { front_fov_deg: 70 }));
  assert.notEqual(initial, sensorDemoSignature("another-project", preset, { front_fov_deg: 70 }));
});

test("only supported values and a changed B can enter preview; terminal failure unlocks authoring", () => {
  const p = { key: "lidar_points_per_second", min: 8000, max: 32000, step: 1000, baseline: 32000, unit: "points/s" } as SensorDemoParameter;
  assert.equal(sensorDemoParameterError(p, 8000), "");
  for (const value of [NaN, Infinity, 0, 8100, 32000, 33000]) assert.ok(sensorDemoParameterError(p, value));
  for (const status of ["failed", "interrupted", "cancelled", "completed"]) assert.equal(sensorDemoTerminal(status), true);
  for (const status of ["running", "claimed", "queued"]) assert.equal(sensorDemoTerminal(status), false);
});


test("presentation names the manifest-derived change and refuses another preset deep link", () => {
  const sensor = { key: "front", kind: "rgb", blueprint: "sensor.camera.rgb", mountRh: [[1]], parameters: { fov: "90", image_size_x: "640" } };
  const captures = [{ stage: "rig_a", sensors: [sensor] }, { stage: "rig_b", sensors: [{ ...sensor, parameters: { ...sensor.parameters, fov: "70.0" } }] }] as Pick<LoadedCapture, "stage" | "sensors">[];
  assert.deepEqual(sensorDemoObservedChange(captures, "camera_fov"), { sensor: "front", parameter: "fov", baseline: 90, value: 70, unit: "° FoV" });
  assert.throws(() => sensorDemoObservedChange(captures, "radar_range"), /실제 변경 항목/);
  captures[1].sensors[0].parameters.image_size_x = "320";
  assert.throws(() => sensorDemoObservedChange(captures, "camera_fov"), /실제 변경 항목/);
});
