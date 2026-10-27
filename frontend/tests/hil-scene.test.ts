import test from "node:test";
import assert from "node:assert/strict";
import {
  hilSceneContext,
  hilSceneRef,
  hilSceneScheduleLabel,
  hilSceneTitle,
  parseHilSceneProfile,
  verifyHilSceneHash,
} from "../src/components/hil-demo/scene-profile.ts";
import type { HilSceneProfile } from "../src/components/hil-demo/scene-profile.ts";
import {
  hilDraftSignature,
  hilDuplicateDraft,
  hilTestDraft,
  hilTestSaveBody,
} from "../src/components/hil-demo/beta-contract.ts";
import type { HilTest } from "../src/components/hil-demo/beta-contract.ts";
import {
  hilEventLabel,
  hilVerifyTracePair,
  parseHilTrace,
} from "../src/components/hil-demo/hil-trace.ts";

// Fixed catalog descriptors plus authored trace fixtures; no native motion evidence is generated here.
const decel: HilSceneProfile = {
  schema_version: "hil-scene-profile/1",
  id: "lead_deceleration",
  revision: 1,
  title: "앞차 감속",
  description: "입력 시각 4초부터 앞차 목표 속도를 2m/s에서 0.5m/s로 낮춥니다.",
  clock: "simulation_input",
  duration_s: 18,
  lead_speed_schedule: [
    { at_s: 0, target_speed_mps: 2 },
    { at_s: 4, target_speed_mps: 0.5 },
  ],
  sha256: "4926394ca04838bb36f5303655725f1cd99c87ef723cd2c62465d4838fa27dc2",
};
const stopGo: HilSceneProfile = {
  schema_version: "hil-scene-profile/1",
  id: "lead_stop_go",
  revision: 1,
  title: "앞차 정지 후 재출발",
  description:
    "입력 시각 4초에 앞차 목표 속도를 0m/s로, 8초에 2m/s로 바꿉니다.",
  clock: "simulation_input",
  duration_s: 18,
  lead_speed_schedule: [
    { at_s: 0, target_speed_mps: 2 },
    { at_s: 4, target_speed_mps: 0 },
    { at_s: 8, target_speed_mps: 2 },
  ],
  sha256: "a061779a405b1a659f2f2d0eaec68d0b6fa969f9a848388f280e2dadf0b99c75",
};
const fault = { kind: "delay", start_s: 3, duration_s: 4, delay_ms: 400 };
const frame = (sequence: number) => ({
  time_s: 4 + (sequence + 1) * 0.05,
  input_time_s: 4 + sequence * 0.05,
  sequence,
  input_age_ms: 400,
  dut_state: "degraded",
  ego_speed_mps: 0.1,
  lead_gap_m: 9,
  command: { throttle: 0, brake: 1, steer: 0 },
  ipc_wall_latency_ms: 1,
  object_sequence: sequence,
  response_frame: sequence + 100,
});
const trace = () => ({
  schema_version: "hil-demo-trace/1",
  execution_target: "software_dut",
  side: "faulted",
  fault_time_basis: "simulation_time",
  ipc_time_basis: "parent_monotonic_wall_clock",
  max_object_age_s: 0.25,
  recovery_fresh_steps: 3,
  fault,
  scene_profile: stopGo,
  frames: [0, 1].map((index) => ({
    ...frame(index),
    lead_actor_id: "lead",
    lead_speed_mps: index ? 0.12 : 0.25,
    lead_target_speed_mps: 0,
    lead_phase: "stop",
  })),
  events: [
    {
      time_s: 4.05,
      input_time_s: 4,
      type: "lead_stop_commanded",
      label: "stop target",
      provenance: "acknowledged_lead_control",
    },
    {
      time_s: 4.1,
      input_time_s: 4.05,
      type: "lead_stopped",
      label: "stopped",
      provenance: "native_response_speed",
    },
  ],
});

test("fixed scene catalog canonical hashes match backend and altered content is rejected", async () => {
  for (const scene of [decel, stopGo]) {
    assert.deepEqual(parseHilSceneProfile(scene), scene);
    await verifyHilSceneHash(scene);
  }
  await assert.rejects(
    verifyHilSceneHash({ ...decel, title: "Changed title" }),
    /SHA-256/,
  );
  assert.throws(
    () => parseHilSceneProfile({ ...decel, clock: "response" }),
    /시간 기준/,
  );
  assert.throws(
    () =>
      parseHilSceneProfile({
        ...decel,
        lead_speed_schedule: [{ at_s: 0, target_speed_mps: 3 }],
      }),
    /목표 일정/,
  );
});
test("legacy omission remains clean while scene changes preserve fault edits and pinned DUT references", () => {
  const saved = {
    id: "test",
    revision: 3,
    sha256: "f".repeat(64),
    name: "Test",
    note: "",
    preset_id: "input_delay",
    binding_ref: { id: "input_delay", revision: 1, sha256: "a".repeat(64) },
    parameters: { delay_ms: 400, start_s: 2, duration_s: 3 },
  } as HilTest;
  const legacy = hilTestDraft(saved);
  assert.equal("scene_profile" in legacy, false);
  assert.equal(
    "scene_profile" in hilTestSaveBody("project", legacy, saved),
    false,
  );
  const changed = { ...legacy, scene_profile: hilSceneRef(decel) };
  assert.notEqual(hilDraftSignature(changed), hilDraftSignature(legacy));
  assert.deepEqual(
    hilTestSaveBody("project", changed, saved).scene_profile,
    hilSceneRef(decel),
  );
  assert.deepEqual(
    hilDuplicateDraft(saved, changed).parameters,
    saved.parameters,
  );
  assert.deepEqual(
    hilDuplicateDraft(saved, changed).scene_profile,
    hilSceneRef(decel),
  );
  assert.deepEqual(
    hilTestDraft({ ...saved, scene_profile: stopGo }).scene_profile,
    hilSceneRef(stopGo),
  );
});
test("scene labels describe authored schedules and preserve omitted legacy identity", () => {
  assert.match(hilSceneTitle(), /기존 장면/);
  assert.equal(hilSceneTitle(decel), "앞차 감속");
  assert.equal(
    hilSceneScheduleLabel(stopGo),
    "0 s → 2 m/s · 4 s → 0 m/s · 8 s → 2 m/s",
  );
});
test("scene identity is fixed across input, spec, summary and both side summaries", () => {
  const source = { scene_profile: decel },
    spec = { input: { scene_profile: decel } };
  const summary = {
    scene_profile: decel,
    sides: {
      baseline: { scene_profile: decel },
      faulted: { scene_profile: decel },
    },
  };
  assert.equal(hilSceneContext(summary, source, spec), decel);
  assert.equal(hilSceneContext({}, {}, {}), undefined);
  assert.throws(() => hilSceneContext(summary, {}, spec), /기존 HIL/);
  assert.throws(
    () =>
      hilSceneContext(summary, source, { input: { scene_profile: stopGo } }),
    /고정 장면/,
  );
  assert.throws(
    () =>
      hilSceneContext(
        {
          ...summary,
          sides: { ...summary.sides, baseline: { scene_profile: stopGo } },
        },
        source,
        spec,
      ),
    /고정 장면/,
  );
});
test("new traces preserve measured lead speed separately from target and distinguish command from observation clocks", () => {
  const parsed = parseHilTrace(trace(), "faulted");
  assert.equal(parsed.frames[1].lead_speed_mps, 0.12);
  assert.equal(parsed.frames[1].lead_target_speed_mps, 0);
  assert.match(hilEventLabel(parsed.events[0]), /목표 적용/);
  assert.match(hilEventLabel(parsed.events[1]), /≤0.2 m\/s/);
  const wrong = trace();
  wrong.events[1].provenance = "acknowledged_lead_control";
  assert.throws(() => parseHilTrace(wrong, "faulted"), /출처/);
  const invalid = trace();
  invalid.frames[0].lead_speed_mps = NaN;
  assert.throws(() => parseHilTrace(invalid, "faulted"), /앞차 응답/);
});
test("trace pair cannot adopt the draft scene and legacy traces remain without invented lead telemetry", () => {
  const faulted = parseHilTrace(trace(), "faulted");
  const baseline = parseHilTrace(
    { ...trace(), side: "baseline", fault: null },
    "baseline",
  );
  assert.doesNotThrow(() =>
    hilVerifyTracePair(
      { baseline, faulted },
      fault,
      "same_scene_fault_ab",
      undefined,
      stopGo,
    ),
  );
  assert.throws(
    () =>
      hilVerifyTracePair(
        { baseline, faulted },
        fault,
        "same_scene_fault_ab",
        undefined,
        decel,
      ),
    /장면/,
  );
  const { scene_profile: _, ...withoutScene } = trace();
  assert.throws(() => parseHilTrace(withoutScene, "faulted"), /출처 없는/);
  const legacy = parseHilTrace(
    { ...withoutScene, frames: [frame(0), frame(1)], events: [] },
    "faulted",
  );
  assert.equal(legacy.sceneProfile, undefined);
  assert.equal(legacy.frames[0].lead_speed_mps, undefined);
});
