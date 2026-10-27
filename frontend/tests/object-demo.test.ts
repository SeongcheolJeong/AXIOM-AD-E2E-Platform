import test from "node:test";
import assert from "node:assert/strict";
import { demoChanges, demoParameterUnit, demoSavedPair, demoTerminal, executeDemo, snapDemoPose } from "../src/components/object-demo/demo-contract.ts";
import type { DemoApi, DemoPreset, DemoRunAttempt } from "../src/components/object-demo/demo-contract.ts";
import type { CityMap, CityScenario } from "../src/components/city-sim/city-contract.ts";

const scenario = { name: "demo", seed: 42 } as CityScenario;
const adapter = { id: "carla-city-object", version: "1" };
const saved = { id: "scenario-1", revision: 1, configuration_hash: "a".repeat(64), scenario };

test("lost submit response retries the same execution key without recreating immutable inputs", async () => {
  const calls: { path: string; key?: string }[] = [];
  let submitCount = 0;
  const request: DemoApi = async <T>(path: string, init?: RequestInit) => {
    calls.push({ path, key: (init?.headers as Record<string, string> | undefined)?.["Idempotency-Key"] });
    if (path === "/scenario-v2") return saved as T;
    if (path === "/experiments/preview") return { preview_hash: "b".repeat(64) } as T;
    if (path === "/experiments") return { id: "experiment-1", preview_hash: "b".repeat(64) } as T;
    if (++submitCount === 1) throw new Error("response lost after server acceptance");
    return { id: "execution-1" } as T;
  };
  const attempt: DemoRunAttempt = { key: "demo-fixed", signature: "frozen" };
  await assert.rejects(executeDemo(request, "demo", scenario, adapter, attempt, () => {}), /response lost/);
  assert.deepEqual(await executeDemo(request, "demo", scenario, adapter, attempt, () => {}), { id: "execution-1" });
  assert.equal(calls.filter((call) => call.path === "/scenario-v2").length, 1);
  assert.equal(calls.filter((call) => call.path === "/experiments").length, 1);
  assert.deepEqual(calls.filter((call) => call.path.endsWith("/executions")).map((call) => call.key), ["demo-fixed-execution", "demo-fixed-execution"]);
  const count = calls.length;
  await executeDemo(request, "demo", scenario, adapter, attempt, () => {});
  assert.equal(calls.length, count);
});

test("preview and experiment hashes must agree before any actual execution", async () => {
  const calls: string[] = [];
  const request: DemoApi = async <T>(path: string) => {
    calls.push(path);
    return (path === "/scenario-v2" ? saved : path === "/experiments/preview" ? { preview_hash: "a" } : { id: "experiment", preview_hash: "b" }) as T;
  };
  await assert.rejects(executeDemo(request, "demo", scenario, adapter, { key: "demo-key", signature: "one" }, () => {}), /설정이 바뀌/);
  assert.equal(calls.some((path) => path.endsWith("/executions")), false);
});

test("lane snapping projects onto native segments and refuses unsupported off-road positions", () => {
  const map: CityMap = { lanes: [{ id: "lane", points: [{ x: 0, y: 10, z: 1, heading_rad: 0 }, { x: 20, y: 10, z: 3, heading_rad: 0 }] }] };
  assert.deepEqual(snapDemoPose(map, { x: 7, y: 12 }), { x: 7, y: 10, z: 1.7, heading_rad: 0 });
  assert.equal(snapDemoPose(map, { x: 7, y: 25 }), null);
  assert.deepEqual(map.lanes[0].points[0], { x: 0, y: 10, z: 1, heading_rad: 0 });
});

test("interrupted runs remain terminal and cannot keep the editor locked forever", () => {
  for (const state of ["completed", "failed", "cancelled", "interrupted"]) assert.equal(demoTerminal(state), true);
  for (const state of ["queued", "claimed", "running"]) assert.equal(demoTerminal(state), false);
});

test("lead wait controls and changed settings use seconds, preserving numeric zero", () => {
  const preset = { parameters: [{ key: "lead_wait_s", label: "선행차 대기 시간", type: "number", default: 6 }], defaults: { lead_wait_s: 6 } } as DemoPreset;
  assert.equal(demoParameterUnit("lead_wait_s"), "초");
  assert.equal(demoParameterUnit("ego_speed_mps"), "m/s");
  assert.equal(demoParameterUnit("rear_gap_m"), "m");
  assert.deepEqual(demoChanges(preset, { lead_wait_s: 0 }, {}), ["선행차 대기 시간: 0 초"]);
});

test("retained comparison orders explicit comparison A before saved baseline B and requires a ready pair", () => {
  const preset = { ready: true, saved_execution_id: "wait-B", saved_comparison_execution_id: "continuous-A" } as DemoPreset;
  assert.deepEqual(demoSavedPair(preset), { left: "continuous-A", right: "wait-B" });
  assert.equal(demoSavedPair({ ...preset, ready: false }), null);
  assert.equal(demoSavedPair({ ...preset, saved_comparison_execution_id: null }), null);
  assert.equal(demoSavedPair({ ...preset, saved_comparison_execution_id: "wait-B" }), null);
});
