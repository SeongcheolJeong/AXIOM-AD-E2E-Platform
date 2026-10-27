import test from "node:test";
import assert from "node:assert/strict";
import {
  draftInjections,
  hardwareState,
  sourceEditorUrl,
  intervalState,
  faultLabel,
  capabilityLabel,
  injectionPoints,
} from "../src/components/vecu/hilInjectionGuide.ts";

// Authored presentation fixtures; no hardware or native execution is claimed.
const original = {
  id: "source-a",
  revision: 2,
  faults: [{ kind: "udp_delay", start_ms: 3000, end_ms: 7000, delay_ms: 400 }],
};
const saved = {
  source_kind: "vecu-network-test",
  source_ref: { id: original.id, revision: original.revision },
  source: { faults: original.faults },
  additional_faults: [
    { kind: "brake_feedback_drop", start_ms: 3000, end_ms: 7000 },
  ],
};
const hardware = { id: "axiom-two-ecu-hardware", target: "hardware" };
const ready = {
  profile_id: hardware.id,
  available: true,
  physical_hardware_connected: true,
  device: { identities: { adas: "ADAS-1", brake: "Brake-1" } },
};

test("virtual ECU readiness never establishes physical hardware identity", () => {
  const state = hardwareState(
    { id: "axiom-two-ecu-vcan", target: "vcan" },
    ready,
  );
  assert.equal(state.confirmed, false);
  assert.equal(state.label, "참조 ECU 프로세스");
});
test("physical identity requires both ECU identities and matching successful physical probe", () => {
  assert.equal(hardwareState(hardware, ready).confirmed, true);
  for (const probe of [
    null,
    { ...ready, profile_id: "axiom-two-ecu-vcan" },
    { ...ready, available: false },
    { ...ready, physical_hardware_connected: false },
    { ...ready, device: { identities: { adas: "ADAS-1" } } },
  ]) {
    assert.equal(hardwareState(hardware, probe).confirmed, false);
  }
  assert.equal(hardwareState(hardware, null).label, "실물 장비 · 연결 미확인");
  assert.equal(
    hardwareState(hardware, { ...ready, available: false }).label,
    "실물 장비 · 미연결",
  );
  assert.equal(hardwareState(undefined, ready).label, "대상 미선택");
});
test("changing source revision replaces saved fault explanation instead of using stale injection", () => {
  const newer = {
    ...original,
    revision: 3,
    faults: [{ kind: "restbus_drop", start_ms: 4000, end_ms: 6000 }],
  };
  assert.deepEqual(
    draftInjections(saved, newer, "vecu-network-test", "source-a:3", []),
    [{ ...newer.faults[0], origin: "source", boundary: undefined }],
  );
  assert.deepEqual(
    draftInjections(saved, undefined, "vecu-network-test", "", []),
    [],
  );
});
test("saved source remains available while loading and only current feedback draft is added", () => {
  const draft = { kind: "brake_feedback_drop", end_ms: 6500, start_ms: 4500 };
  const items = draftInjections(
    saved,
    undefined,
    "vecu-network-test",
    "source-a:2",
    [draft],
    { brake_feedback_drop: "brake.plant.feedback" },
  );
  assert.equal(items[0].delay_ms, 400);
  assert.equal(items[0].origin, "source");
  assert.equal(items[1].start_ms, 4500);
  assert.equal(items[1].origin, "binding");
  assert.equal(items[1].boundary, "brake.plant.feedback");
  assert.equal(
    draftInjections(saved, original, "vecu-network-test", "source-a:2", [])
      .length,
    1,
  );
});
test("fixed replay step schedules normalize to the same milliseconds as the HIL source contract", () => {
  const source = {
    mode: "fixed_replay",
    faults: [
      { kind: "delay", start_step: 60, end_step: 140, delay_steps: 8 },
      { kind: "dropout", start_step: 150, end_step: 160 },
      { kind: "repeat", start_step: 170, end_step: 180 },
    ],
  };
  const items = draftInjections(null, source, "vecu-test", "v:1", []);
  assert.deepEqual(
    items.map((f) => [f.kind, f.start_ms, f.end_ms]),
    [
      ["udp_delay", 3000, 7000],
      ["udp_drop", 7500, 8000],
      ["udp_repeat", 8500, 9000],
    ],
  );
  assert.equal(items[0].delay_ms, 400);
});
test("CARLA vECU and Software-DUT source schedules keep second-based duration and revision", () => {
  for (const [kind, source] of [
    [
      "vecu-test",
      {
        hil_experiment: {
          input: { fault: { kind: "repeat", start_s: 3, duration_s: 4 } },
        },
      },
    ],
    [
      "hil-test",
      {
        experiment: {
          input: {
            fault: { kind: "delay", start_s: 3, duration_s: 4, delay_ms: 400 },
          },
        },
      },
    ],
  ] as const) {
    const [f] = draftInjections(null, source, kind, "source:1", []);
    assert.equal(f.start_ms, 3000);
    assert.equal(f.end_ms, 7000);
    assert.equal(f.kind, kind === "vecu-test" ? "udp_repeat" : "udp_delay");
  }
});
test("source editor returns to exact source revision without a retained execution", () => {
  for (const kind of ["vecu-network-test", "vecu-test", "hil-test"]) {
    const url = sourceEditorUrl("project-1", kind, original)!;
    const q = new URLSearchParams(url.split("?")[1]);
    assert.equal(q.get("test"), "source-a");
    assert.equal(q.get("revision"), "2");
    assert.equal(q.get("mode"), kind === "hil-test" ? "beta" : "vecu");
    assert.equal(q.get("tab"), kind === "vecu-network-test" ? "network" : null);
    assert.equal(q.has("execution"), false);
  }
  assert.equal(sourceEditorUrl("p", "vecu-test", undefined), null);
});
test("ending injection at 7 seconds means recovery observation, not proven recovery", () => {
  assert.equal(intervalState(original.faults, 2999), "주입 전");
  assert.equal(intervalState(original.faults, 3000), "고장 주입 구간");
  assert.equal(intervalState(original.faults, 6999), "고장 주입 구간");
  assert.equal(intervalState(original.faults, 7000), "주입 종료 · 회복 관찰");
  assert.equal(intervalState([], 5000), "주입 없음");
});
test("labels distinguish injected delay from capability and do not fabricate unspecified values", () => {
  assert.equal(
    faultLabel({ kind: "udp_delay", delay_ms: 400 }),
    "400ms 객체 지연",
  );
  assert.equal(faultLabel({ kind: "udp_delay" }), "객체 UDP 지연");
  assert.equal(capabilityLabel("supported"), "주입 기능 구현됨");
  assert.equal(capabilityLabel("driver_required"), "장치별 연동 필요");
  assert.equal(capabilityLabel(undefined), "주입 경로 미구현");
});

test("saved model link restores its fractional native fault interval and measurement origin", () => {
  const model = { ...saved, source: { faults: [] }, model_link: {
    injection: { kind: "model_camera_raw_csi", start_ms: 3005, end_ms: 3005.03 },
  } };
  const [f] = draftInjections(model, undefined, "vecu-network-test", "source-a:2", [], {
    model_camera_raw_csi: "model.camera_raw_csi.measurement",
  });
  assert.equal(f.origin, "model");
  assert.equal(f.end_ms, 3005.03);
  assert.equal(f.boundary, "model.camera_raw_csi.measurement");
  assert.equal(intervalState([f], 3050), "주입 종료 · 회복 관찰");
  assert.deepEqual(draftInjections(model, { faults: [] }, "vecu-network-test", "source-a:3", []), []);
});

test("four model map locations describe measurement replay without physical ECU readiness", () => {
  const points = injectionPoints.filter(p => p.id.startsWith("model_"));
  assert.equal(points.length, 4);
  assert.equal(new Set(points.map(p => p.id)).size, 4);
  for (const point of points) {
    assert.match(point.path, /측정 UDP.*참조 ADAS/);
    assert.match(point.recovery, /재생 가정/);
    assert.notEqual(faultLabel({ kind: point.kinds[0] }), point.kinds[0]);
  }
});

test("compound model binding keeps both map schedules and independent model origins", () => {
  const injections=[{kind:'model_power',start_ms:3003,end_ms:3007},{kind:'model_fpga',start_ms:3005,end_ms:3305}];
  const compound={...saved,source:{faults:[]},model_link:{boundary:'multiple',injections}};
  const items=draftInjections(compound,undefined,'vecu-network-test','source-a:2',[]);
  assert.deepEqual(items.map(f=>f.kind),['model_power','model_fpga']);
  assert.ok(items.every(f=>f.origin==='model'));
  assert.equal(intervalState(items,3100),'고장 주입 구간');
  assert.equal(intervalState(items,3310),'주입 종료 · 회복 관찰');
});


test("recurrent model faults restore every interval without hiding the first injection", () => {
  const intervals = [
    {kind: 'model_fpga', start_ms: 3005, end_ms: 3005.1},
    {kind: 'model_fpga', start_ms: 3800, end_ms: 3800.1},
  ];
  const model = {...saved, source: {faults: []}, model_link: {injection: intervals[0], injections: intervals}};
  const items = draftInjections(model, undefined, 'vecu-network-test', 'source-a:2', [], {model_fpga: 'model.fpga.measurement'});
  assert.deepEqual(items.map(f => [f.start_ms, f.end_ms]), [[3005, 3005.1], [3800, 3800.1]]);
  assert.ok(items.every(f => f.boundary === 'model.fpga.measurement'));
});

test('power recurrence beyond one native second restores its own interval alongside FPGA recurrences',()=>{
 const injections=[{kind:'model_power',start_ms:3003,end_ms:3303},{kind:'model_power',start_ms:4350,end_ms:4650},
  {kind:'model_fpga',start_ms:3005,end_ms:3005.1},{kind:'model_fpga',start_ms:3800,end_ms:3800.2}];
 const model={...saved,source:{faults:[]},model_link:{boundary:'multiple',injections}};
 const values=draftInjections(model,undefined,'vecu-network-test','source-a:2',[],{model_power:'model.power.measurement',model_fpga:'model.fpga.measurement'});
 assert.equal(values.length,4);
 assert.deepEqual(values.filter(f=>f.kind==='model_power').map(f=>[f.start_ms,f.end_ms]),[[3003,3303],[4350,4650]]);
 assert.equal(intervalState(values.filter(f=>f.kind==='model_power'),4350),'고장 주입 구간');
});
