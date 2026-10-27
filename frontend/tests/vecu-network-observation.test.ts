import test from "node:test";
import assert from "node:assert/strict";
import {
  brakeReason,
  faultCondition,
  networkObservation,
} from "../src/components/vecu/network-observation.ts";

// Authored observation fixtures test UI interpretation, not native execution.
const schedule = [
  { kind: "restbus_drop", start_ms: 3000, end_ms: 7000 },
  { kind: "ecu_reset", start_ms: 6900, end_ms: 7100 },
];
function sample(
  time: number,
  reason = "command",
  state = "tracking",
  heartbeat = false,
) {
  return {
    request: { time_s: time / 1000 },
    injection: {
      faults: schedule.filter((f) => time >= f.start_ms && time < f.end_ms),
    },
    reply: {
      state,
      control: { brake: reason === "command" ? 0 : 1 },
      network: {
        time_ms: time,
        brake: { reason },
        heartbeat_override: heartbeat,
        stats: { heartbeat_timeouts: time >= 3100 ? 1 : 0 },
        controller_steps: [{ reply: { state: "tracking" } }],
      },
    },
    next_ego: { speed_mps: 0 },
  };
}
test("valid CAN delivery does not imply released brake or global health", () => {
  const row = sample(6900, "command", "degraded", true);
  row.reply.control.brake = 1;
  row.reply.network.controller_steps = [];
  const text = networkObservation(row, 1, schedule, true);
  assert.equal(brakeReason("command"), "수신 CAN 명령 적용 중");
  assert.match(text.location, /Restbus → ADAS.*ADAS OS/);
  assert.match(text.protection, /ADAS 재시작 중.*생존 신호 시간 초과/);
  assert.match(text.protection, /적용 제동 100%/);
  assert.match(text.response, /0.000 m\/s.*정지 수준/);
});
test("after injection ends, healthy heartbeat/controller cannot hide Brake recovery", () => {
  const text = networkObservation(
    sample(7150, "recovery", "degraded"),
    1,
    schedule,
    true,
  );
  assert.equal(text.phase, "고장 주입 종료 · 보호/회복 확인 중");
  assert.match(text.location, /고장 주입은 종료됨/);
  assert.match(text.protection, /새 CAN 명령 회복 확인 중 · 제동 유지/);
  assert.doesNotMatch(text.protection, /생존 신호 시간 초과/);
});
test("released brake does not establish resumed vehicle motion", () => {
  const text = networkObservation(sample(7250), 1, schedule, true);
  assert.equal(text.phase, "고장 주입 종료");
  assert.match(text.protection, /적용 제동 0%/);
  assert.match(text.response, /정지 수준/);
});
test("normal baseline can protect during initialization without an injected failure", () => {
  const row = sample(0, "initial", "degraded", true);
  row.reply.network.controller_steps = [];
  const text = networkObservation(row, 0, schedule, true);
  assert.equal(text.phase, "고장 주입 없음");
  assert.equal(text.location, "고장 주입 없음");
  assert.match(text.protection, /생존 신호 확인 전/);
  assert.doesNotMatch(text.protection, /시간 초과/);
});
test("schedule uses half-open intervals and distinguishes before, during and after", () => {
  assert.equal(
    networkObservation(sample(2950), 1, schedule, true).phase,
    "고장 주입 전",
  );
  assert.equal(
    networkObservation(sample(6900), 1, schedule, true).active.length,
    2,
  );
  assert.deepEqual(networkObservation(sample(7000), 1, schedule, true).active, [
    "ecu_reset",
  ]);
  assert.deepEqual(
    networkObservation(sample(7100), 1, schedule, true).active,
    [],
  );
});
test("no-fault B, missing evidence and open loop do not invent faults or vehicle response", () => {
  assert.equal(
    networkObservation(sample(0), 1, [], false).phase,
    "고장 주입 없음",
  );
  const unknown = networkObservation({}, 1, null, true);
  assert.equal(unknown.phase, "고장 일정 미보존");
  assert.match(unknown.response, /미보존/);
  assert.match(unknown.protection, /상태 미보존/);
  assert.match(
    networkObservation(sample(7150), 1, schedule, false).response,
    /반응은 계산하지 않음/,
  );
});
test("all supported fault locations remain distinct and unfamiliar faults stay explicit", () => {
  for (const [kind, location] of Object.entries({
    udp_delay: "입력 지연",
    udp_drop: "입력 단절",
    udp_repeat: "반복 전달",
    can_command_drop: "CAN 명령 전달 단절",
    restbus_drop: "생존 신호 단절",
    ecu_reset: "재시작 구간",
    os_event_drop: "이벤트 전달 누락",
  })) {
    const row = { ...sample(4000), injection: { faults: [{ kind }] } };
    assert.ok(
      networkObservation(row, 1, schedule, true).location.includes(location),
    );
  }
  assert.match(
    networkObservation(
      { ...sample(4000), injection: { faults: [{ kind: "unsupported" }] } },
      1,
      schedule,
      true,
    ).location,
    /미해석 고장/,
  );
  assert.match(
    faultCondition({
      kind: "udp_delay",
      delay_ms: 400,
      start_ms: 3000,
      end_ms: 7000,
    }),
    /400 ms · 3.00–7.00초/,
  );
});
