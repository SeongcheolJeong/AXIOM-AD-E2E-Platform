type Doc = Record<string, any>;
export const numberText = (n: unknown, digits = 0) =>
  typeof n === "number" && Number.isFinite(n) ? n.toFixed(digits) : "—";

const faults: Record<string, [string, string]> = {
  udp_delay: ["UDP 입력 지연", "Perception → ADAS 객체 입력 지연"],
  udp_drop: ["UDP 입력 단절", "Perception → ADAS 객체 입력 단절"],
  udp_repeat: ["UDP 패킷 반복", "Perception → ADAS 이전 객체 패킷 반복 전달"],
  can_command_drop: [
    "CAN 제동 명령 단절",
    "ADAS → Brake ECU CAN 명령 전달 단절",
  ],
  restbus_drop: [
    "Restbus 통신 단절",
    "Restbus → ADAS 차량 상태·생존 신호 단절",
  ],
  ecu_reset: ["ADAS ECU 리셋", "ADAS OS·BSW·제어 프로세스 재시작 구간"],
  os_event_drop: [
    "OS 제어 이벤트 누락",
    "ADAS OS → 제어 태스크 이벤트 전달 누락",
  ],
};
export const faultName = (kind: string) =>
  faults[kind]?.[0] ?? `미해석 고장 (${kind})`;
export const faultCondition = (fault: Doc) =>
  `${faultName(fault.kind)}${fault.kind === "udp_delay" ? ` ${numberText(fault.delay_ms)} ms` : ""} · ${numberText(fault.start_ms / 1000, 2)}–${numberText(fault.end_ms / 1000, 2)}초`;
export const stateName = (state: string) =>
  ({
    tracking: "정상 추종",
    degraded: "보호 상태",
    recovering: "복귀 확인 중",
  })[state] ?? "상태 미해석";
export const brakeReason = (reason: string) =>
  ({
    initial: "첫 CAN 명령 대기 · 보호 제동",
    watchdog: "CAN 명령 수신 시간 초과 · 보호 제동",
    recovery: "새 CAN 명령 회복 확인 중 · 제동 유지",
    command: "수신 CAN 명령 적용 중",
  })[reason] ?? "CAN 명령 상태 미보존";

// Describe retained observations only. The schedule comes from the verified
// execution receipt, never the editable authoring form or another execution.
export function networkObservation(
  row: Doc,
  side: number,
  schedule: Doc[] | null,
  closed: boolean,
) {
  const net = row.reply?.network ?? {},
    brake = net.brake ?? {};
  const controller = net.controller_steps?.at(-1)?.reply;
  const active: string[] = Array.isArray(row.injection?.faults)
    ? row.injection.faults.map((f: Doc) => f.kind)
    : row.injection?.active
      ? String(row.injection.kind).split(" + ")
      : [];
  const time = net.time_ms ?? row.request?.time_s * 1000;
  const ended = schedule?.some((f) => time >= f.end_ms);
  const allEnded =
    !!schedule?.length && schedule.every((f) => time >= f.end_ms);
  const protectedState =
    row.reply?.state === "degraded" || row.reply?.state === "recovering";
  const phase = !side
    ? "고장 주입 없음"
    : active.length
      ? "고장 주입 중"
      : schedule === null
        ? "고장 일정 미보존"
        : !schedule.length
          ? "고장 주입 없음"
          : allEnded
            ? protectedState
              ? "고장 주입 종료 · 보호/회복 확인 중"
              : "고장 주입 종료"
            : ended
              ? "고장 주입 사이 구간"
              : "고장 주입 전";

  let location = active.length
    ? active.map((k) => faults[k]?.[1] ?? `미해석 고장 (${k})`).join(" + ")
    : side && allEnded
      ? "고장 주입은 종료됨. 아래는 현재 남아 있는 보호·회복 상태"
      : side && schedule === null
        ? "고장 일정 근거가 없어 주입 위치를 확정할 수 없음"
        : side && schedule?.length
          ? "현재 주입 중인 고장 없음"
          : "고장 주입 없음";
  // A snapshot can establish a protection state, not a new physical failure.
  if (
    !active.length &&
    net.heartbeat_override === true &&
    (net.stats?.heartbeat_timeouts ?? 0) > 0
  )
    location += " · ADAS 생존 신호 보호가 아직 적용됨";

  const actions: string[] = [];
  if (active.includes("ecu_reset") && !controller)
    actions.push("ADAS 재시작 중으로 이번 구간 제어 태스크 실행 없음");
  else if (!controller) actions.push("이번 구간 ADAS 제어 실행 기록 없음");
  if (net.heartbeat_override === true)
    actions.push(
      (net.stats?.heartbeat_timeouts ?? 0) > 0
        ? "생존 신호 시간 초과에 따른 ADAS 보호 제동 적용"
        : "생존 신호 확인 전 ADAS 보호 제동 적용",
    );
  if (controller?.state === "tracking")
    actions.push("ADAS 객체 입력 정상 추종 처리");
  if (controller?.duplicate_packet_detected)
    actions.push("이전 객체 패킷의 중복을 검출");
  if (controller?.state === "degraded")
    actions.push("객체 입력 정책이 보호 상태");
  if (controller?.state === "recovering")
    actions.push("객체 입력 정책이 새 패킷 회복을 확인 중");
  actions.push(
    `Brake ECU: ${brakeReason(brake.reason)} · 적용 제동 ${numberText(row.reply?.control?.brake * 100)}%`,
  );

  const speed = row.next_ego?.speed_mps;
  const response = !closed
    ? "고정 입력 재생이므로 차량 반응은 계산하지 않음"
    : typeof speed !== "number" || !Number.isFinite(speed)
      ? "차량 응답 속도 미보존"
      : `제어 적용 후 다음 관측 ${numberText(speed, 3)} m/s · ${speed < 0.1 ? "정지 수준 (<0.1 m/s)" : "이동 중"}`;
  return { phase, location, protection: actions.join(" · "), response, active };
}
