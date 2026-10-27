import test from "node:test";
import assert from "node:assert/strict";
import { executeHilDemo, hilEngineStatus, hilErrorMessage, hilInitialPreset, hilParameterError, hilSignature, HilPreviewChanged } from "../src/components/hil-demo/demo-contract.ts";
import type { HilApi, HilAttempt, HilExecution, HilParameter, HilPreset, HilPreview } from "../src/components/hil-demo/demo-contract.ts";
import { hilObservedFaultWindow, hilPresetMatches, hilSampleAt, hilSealedSummary, parseHilTrace } from "../src/components/hil-demo/hil-trace.ts";

// Authored contract fixtures only. No native execution or driving validation claim.
const frame = (sequence: number) => ({ time_s: (sequence + 1) * .05, input_time_s: sequence * .05, sequence, input_age_ms: 0, dut_state: "tracking", ego_speed_mps: 2.7, lead_gap_m: 9, command: { throttle: .1, brake: 0, steer: 0 }, ipc_wall_latency_ms: 1, object_sequence: sequence, response_frame: sequence + 100 });
const rawTrace = () => ({ schema_version: "hil-demo-trace/1", execution_target: "software_dut", side: "faulted", max_object_age_s:.25,recovery_fresh_steps:3, fault_time_basis: "simulation_time", ipc_time_basis: "parent_monotonic_wall_clock", fault: { kind: "delay", start_s: 3, duration_s: 4, delay_ms: 300 }, frames: [frame(0), frame(1), frame(2)], events: [{ time_s: 0, type: "dut_tracking", label: "dut_tracking" }] });
const preview = { experiment: { project_id: "demo", kind: "hil_demo", comparison_kind: "same_scene_fault_ab", input: { fault: { kind: "delay", delay_ms: 300 } } }, preview: { preview_hash: "a".repeat(64) } } as HilPreview;

test("HIL submit fixes the reviewed A/B body and deduplicates an uncertain execution response", async () => {
  const calls: { path: string; key: string; body: unknown }[] = []; let executionCalls = 0;
  const request: HilApi = async <T>(path: string, init?: RequestInit) => {
    calls.push({ path, key: new Headers(init?.headers).get("Idempotency-Key")!, body: JSON.parse(String(init?.body)) });
    if (path === "/experiments") return { id: "exp", preview_hash: preview.preview.preview_hash } as T;
    if (++executionCalls === 1) throw new Error("response lost");
    return { id: "run" } as T;
  };
  const attempt: HilAttempt = { key: "hil-attempt", signature: "fixed" };
  await assert.rejects(executeHilDemo(request, preview, attempt, () => {}), /response lost/);
  assert.equal((await executeHilDemo(request, preview, attempt, () => {})).id, "run");
  await executeHilDemo(request, preview, attempt, () => {});
  assert.equal(calls.length, 3);
  assert.deepEqual(calls[0].body, preview.experiment);
  assert.deepEqual(calls[1], calls[2]);
});
test("changed preview hash blocks execution submission", async () => {
  const calls: string[] = [];
  const request: HilApi = async <T>(path: string) => { calls.push(path); return { id: "exp", preview_hash: "changed" } as T; };
  await assert.rejects(executeHilDemo(request, preview, { key: "one", signature: "fixed" }, () => {}), HilPreviewChanged);
  assert.deepEqual(calls, ["/experiments"]);
});
test("input edits, project changes and binding revisions invalidate the preview", () => {
  const preset = { id: "input_delay", binding_revision: 1 } as HilPreset;
  const initial = hilSignature("demo", preset, { delay_ms: 300 });
  assert.notEqual(initial, hilSignature("demo", preset, { delay_ms: 350 }));
  assert.notEqual(initial, hilSignature("other", preset, { delay_ms: 300 }));
  assert.notEqual(initial, hilSignature("demo", { ...preset, binding_revision: 2 }, { delay_ms: 300 }));
  const parameter = { min: 50, max: 1000, step: 50, unit: "ms" } as HilParameter;
  assert.equal(hilParameterError(parameter, 300), "");
  for (const value of [NaN, Infinity, 0, 51, 1001]) assert.ok(hilParameterError(parameter, value));
});
test("generic execution deep links select the retained fault preset; explicit selection stays explicit", () => {
  const items = [{ id: "input_delay" }, { id: "input_dropout" }] as HilPreset[];
  const result = { summary: { fault: { kind: "drop" } } } as HilExecution;
  assert.equal(hilInitialPreset(items, null, result).id, "input_dropout");
  assert.equal(hilInitialPreset(items, "input_delay", result).id, "input_delay");
});
test("trace cursor uses only a retained sample at or before the requested time", () => {
  const trace = parseHilTrace(rawTrace(), "faulted");
  assert.equal(hilSampleAt(trace, -.01), null);
  assert.equal(hilSampleAt(trace, .049), null);
  assert.equal(hilSampleAt(trace, .05)?.sequence, 0);
  assert.equal(hilSampleAt(trace, .099)?.sequence, 0);
  assert.equal(hilSampleAt(trace, .1)?.sequence, 1);
  assert.equal(hilSampleAt(trace, .15)?.sequence, 2);
  assert.equal(hilSampleAt(trace, trace.frames[2].time_s)?.sequence, 2);
  assert.equal(hilSampleAt(trace, 100)?.sequence, 2);
  assert.equal(hilPresetMatches(trace.fault!, "input_delay"), true);
  assert.equal(hilPresetMatches(trace.fault!, "input_dropout"), false);
});
test("HIL cursor tolerates timestamp float noise without selecting a genuinely later response", () => {
  const raw = rawTrace(); raw.frames = [frame(69), frame(70)];
  const trace = parseHilTrace(raw, "faulted");
  assert.equal(trace.frames[1].time_s, 3.5500000000000003);
  assert.equal(hilSampleAt(trace, 3.55)?.sequence, 70);
  assert.equal(hilSampleAt(trace, 3.549)?.sequence, 69);
});
test("trace rejects wrong side, clock basis, unordered sequence and invalid command", () => {
  assert.throws(() => parseHilTrace(rawTrace(), "baseline"), /계약/);
  const clock = rawTrace(); clock.fault_time_basis = "wall_time"; assert.throws(() => parseHilTrace(clock, "faulted"), /시간 기준/);
  const unordered = rawTrace(); unordered.frames[1].sequence = 0; assert.throws(() => parseHilTrace(unordered, "faulted"), /sequence/);
  const command = rawTrace(); command.frames[1].command.brake = 2; assert.throws(() => parseHilTrace(command, "faulted"), /명령/);
  const baseline = { ...rawTrace(), side: "baseline" }; assert.throws(() => parseHilTrace(baseline, "baseline"), /고장 조건/);
});
test("sealed HIL result identity and artifacts must match the selected execution", () => {
  const execution = { id: "job", experiment_id: "exp", project_id: "demo", status: "completed", experiment_kind: "hil_demo", adapter_id: "carla-software-dut", adapter_version: "v1", input_hash: "a", fidelity: "actual_carla_software_dut", artifacts: [] } as unknown as HilExecution;
  const receipt = { schema_version: 1, execution: { ...execution, summary: { schema_version: "hil-demo-result/1", execution_target: "software_dut" } }, prepared: { input: {} } };
  assert.equal(hilSealedSummary(receipt, execution).summary.execution_target, "software_dut");
  assert.throws(() => hilSealedSummary(receipt, { ...execution, project_id: "other" }), /project_id/);
  assert.throws(() => hilSealedSummary(receipt, { ...execution, fidelity: "mock" }), /완료된/);
  assert.throws(() => hilSealedSummary({ ...receipt, execution: { ...receipt.execution, artifacts: [{ name: "changed" }] } }, execution), /산출물 목록/);
});


test("busy CARLA error is actionable while original diagnostic remains available", () => {
  const raw = "Preflight failed: Dedicated native city endpoint contains foreign actors: [123]";
  assert.equal(hilErrorMessage(raw), "CARLA 환경에 사용 중인 객체가 있습니다. 진행 중인 실행이 끝난 후 다시 확인하세요.");
  assert.equal(hilErrorMessage("Unrecognized error"), "Unrecognized error");
});
test("configured adapter metadata does not claim a live CARLA engine before native preview", () => {
  assert.match(hilEngineStatus("available", false, "", false).title, /엔진 확인 필요/);
  assert.match(hilEngineStatus("available", false, "", true).description, /확인한 시점/);
  assert.match(hilEngineStatus("not_configured", false, "", false).title, /설정이 없습니다/);
  assert.match(hilEngineStatus("available", false, "connection error", true).title, /읽지 못/);
  assert.match(hilEngineStatus(undefined, true, "", false).title, /확인 중/);
});
test("native connection errors remain explicit without inventing a GPU diagnosis", () => {
  assert.match(hilErrorMessage("Dedicated native CARLA city transport is not configured"), /연결 설정/);
  const probe = hilErrorMessage("Native city endpoint probe failed: timeout");
  assert.match(probe, /연결을 확인하지 못/);
  assert.doesNotMatch(probe, /GPU|드라이버/);
});
test("HIL revision and retained-result conflicts explain the corrective action", () => {
  assert.match(hilErrorMessage("Execution inputs differ from the saved HIL test revision"), /같은 고장 값과 고정 조건/);
  assert.match(hilErrorMessage("HIL test revision changed; reload before saving"), /저장된 시험을 다시/);
  assert.match(hilErrorMessage("Saved HIL test revision hash differs"), /저장된 시험을 다시/);
});

test("fault overlay uses response events on the same axis as camera and vehicle response", () => {
  const raw = rawTrace();
  raw.events = [{time_s:.05,type:"fault_started",label:"start"},{time_s:.15,type:"fault_ended",label:"end"}];
  const trace = parseHilTrace(raw, "faulted");
  assert.deepEqual(hilObservedFaultWindow(trace), {start:.05,end:.15});
  const invalid = rawTrace(); invalid.frames[1].input_time_s = .2;
  assert.throws(() => parseHilTrace(invalid, "faulted"), /입력과 응답 시간/);
});
