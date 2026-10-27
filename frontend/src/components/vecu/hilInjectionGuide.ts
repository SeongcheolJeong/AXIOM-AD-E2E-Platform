// Presentation of the existing hil-bridge API; this does not define executable capabilities.
export type GuideDoc = Record<string, any>;
export type InjectionPoint = {
  id: string;
  number: string;
  title: string;
  path: string;
  kinds: string[];
  normal: string;
  effect: string;
  recovery: string;
  observation: string;
};
export const injectionPoints: InjectionPoint[] = [
  {
    id: "objects",
    number: "01",
    title: "객체 입력",
    path: "Perception → ADAS · UDP",
    kinds: ["udp_delay", "udp_drop", "udp_repeat"],
    normal: "50ms마다 새 객체 목록과 sequence를 전달합니다.",
    effect:
      "전달을 늦추거나 끊고, 반복 시험에서는 직전 패킷을 그대로 다시 보냅니다. 영상 픽셀을 바꾸는 고장이 아닙니다.",
    recovery:
      "새 입력 전달을 재개합니다. 지연 종료 시 큐에 남은 이전 패킷은 폐기하며, 참조 ADAS는 신선한 입력 3회를 확인합니다.",
    observation:
      "송수신 sequence·payload hash → ADAS 진단 → CAN 명령 → Brake 출력",
  },
  {
    id: "restbus",
    number: "02",
    title: "차량 상태 · heartbeat",
    path: "Restbus → ADAS · CAN 구간 A",
    kinds: ["restbus_drop"],
    normal: "10ms마다 가상 차량 상태와 heartbeat를 보냅니다.",
    effect:
      "Restbus의 두 메시지 송신을 중단합니다. 객체 UDP 입력은 유지됩니다.",
    recovery:
      "Restbus 송신을 재개합니다. 참조 ADAS는 새 heartbeat 3회를 확인한 뒤 통신 보호 조건을 해제합니다.",
    observation: "Restbus 송신 억제 → ADAS heartbeat 진단 → CAN 제동 명령",
  },
  {
    id: "command",
    number: "03",
    title: "CAN 명령 전달",
    path: "ADAS → 게이트웨이 → Brake · CAN A/B",
    kinds: ["can_command_drop"],
    normal: "ADAS 명령을 구간 A에서 받아 분리된 구간 B로 중계합니다.",
    effect:
      "지정 명령의 중계만 차단합니다. ADAS의 상류 송신은 계속되며 Brake에는 도착하지 않습니다.",
    recovery:
      "중계를 재개합니다. 참조 Brake는 유효한 새 명령 3회를 확인합니다.",
    observation:
      "상류 수신 / 하류 송신 대조 → Brake watchdog → Brake 보호 제동",
  },
  {
    id: "feedback",
    number: "04",
    title: "출력 피드백",
    path: "Brake 보고 출력 → CARLA 차량",
    kinds: ["brake_feedback_drop"],
    normal: "수신한 Brake 출력으로 차량 모델을 제어합니다.",
    effect:
      "Brake 출력의 차량 모델 전달을 끊습니다. ECU가 제동을 명령한 사건과 구분하는 시험기 고장입니다.",
    recovery:
      "피드백이 100ms 이상 갱신되지 않으면 시험을 중단합니다. 중단된 실행은 자동 재개하지 않습니다.",
    observation: "피드백 신선도 → 시험 중단 로그 · 대체 제어값 생성 없음",
  },
  {
    id: "reset",
    number: "05",
    title: "ECU 리셋",
    path: "ADAS 진단 / 리셋 인터페이스",
    kinds: ["ecu_reset"],
    normal: "등록된 ECU가 계속 실행됩니다.",
    effect: "가상 CAN에서는 참조 ADAS 상태를 초기화하고 명령 송신을 억제합니다. 실물은 장치별 리셋 드라이버가 필요합니다.",
    recovery: "참조 ADAS 재초기화 후 새 입력을 확인하고 Brake가 새 epoch 명령 3회를 확인합니다.",
    observation: "참조 리셋 적용·해제 → Brake watchdog → 새 epoch와 출력 회복",
  },
  {
    id: "os",
    number: "06",
    title: "OS 이벤트",
    path: "ECU 내부 실행 훅",
    kinds: ["os_event_drop"],
    normal: "ECU 내부 태스크가 예정대로 실행됩니다.",
    effect:
      "가상 CAN에서는 참조 ADAS의 제어 tick을 생략합니다. 실물은 내부 시험 훅이 필요합니다.",
    recovery: "참조 제어 tick을 재개하고 Brake가 새 명령 3회를 확인합니다.",
    observation: "참조 태스크 억제 로그 → CAN 송신 공백 → Brake watchdog · 실제 OS 커널 시간 검증 제외",
  },
  ...[['camera_raw_csi','RAW 프레임 모델'],['fpga','FPGA 출력 모델'],['power','전원 회로 모델'],['analog','아날로그 회로 모델']].map(([id,title],i)=>({
    id:'model_'+id,number:String(7+i).padStart(2,'0'),title,path:'native 모델 출력 → 측정 UDP → 참조 ADAS',kinds:['model_'+id],
    normal:'정상 A 모델의 봉인된 출력 기록을 같은 차량 시나리오에 재생합니다.',
    effect:'고장 B 모델의 프레임 commit/abort 또는 전압 구간 관측값을 전달합니다. 참조 ECU가 이상 여부를 판단합니다.',
    recovery:'마지막 이상 뒤 최소 보호 시간과 정상 표본 수를 확인합니다. 모델 종료 후 마지막 관측 상태 유지는 명시적인 재생 가정입니다.',
    observation:'모델 artifact → 측정 송수신 → ADAS 검출 → CAN 명령 → Brake 보고 출력 → CARLA'})),
];
export const otherBoundaries: Record<string, string> = {
  camera_raw_csi: "카메라 RAW / CSI",
  fpga: "FPGA 내부",
  power: "전원",
  analog: "아날로그 신호",
};
export const faultLabels: Record<string, string> = {
  udp_delay: "객체 UDP 지연",
  udp_drop: "객체 입력 단절",
  udp_repeat: "동일 패킷 반복",
  restbus_drop: "Restbus 송신 중단",
  can_command_drop: "CAN 명령 중계 차단",
  brake_feedback_drop: "출력 피드백 단절",
  ecu_reset: "ECU 리셋",
  os_event_drop: "OS 이벤트 누락",
  model_camera_raw_csi:"RAW 프레임 입력 이상",model_fpga:"FPGA 출력 정체",model_power:"모델 전원 강하",model_analog:"모델 아날로그 오프셋",
};
export function faultLabel(f: GuideDoc) {
  return f.kind === "udp_delay" && Number.isFinite(f.delay_ms)
    ? `${f.delay_ms}ms 객체 지연`
    : (faultLabels[f.kind] ?? f.kind);
}
export function capabilityLabel(value?: string) {
  return value === "supported"
    ? "주입 기능 구현됨"
    : value === "driver_required"
      ? "장치별 연동 필요"
      : "주입 경로 미구현";
}
export function savedSourceMatches(
  saved: GuideDoc | null,
  kind: string,
  key: string,
) {
  return (
    !!saved &&
    saved.source_kind === kind &&
    `${saved.source_ref.id}:${saved.source_ref.revision}` === key
  );
}
export function draftInjections(
  saved: GuideDoc | null,
  source: GuideDoc | undefined,
  kind: string,
  key: string,
  additional: GuideDoc[],
  boundaries: Record<string, string> = {},
): GuideDoc[] {
  let faults: GuideDoc[] = [];
  if (savedSourceMatches(saved, kind, key)) faults = saved!.model_link ? [...saved!.source.faults,...(saved!.model_link.injections??[saved!.model_link.injection])] : saved!.source.faults;
  else if (source) {
    if (kind === "vecu-network-test") faults = source.faults ?? [];
    else if (kind === "vecu-test" && source.mode === "fixed_replay")
      faults = (source.faults ?? []).map((f: GuideDoc) => ({
        ...f,
        kind: (
          {
            delay: "udp_delay",
            dropout: "udp_drop",
            repeat: "udp_repeat",
          } as Record<string, string>
        )[f.kind],
        start_ms: f.start_step * 50,
        end_ms: f.end_step * 50,
        delay_ms: f.delay_steps * 50,
      }));
    else {
      const f = (source.hil_experiment ?? source.experiment)?.input?.fault;
      if (f)
        faults = [
          {
            kind: (
              {
                delay: "udp_delay",
                drop: "udp_drop",
                repeat: "udp_repeat",
              } as Record<string, string>
            )[f.kind],
            start_ms: Math.round(f.start_s * 1000),
            end_ms: Math.round((f.start_s + f.duration_s) * 1000),
            delay_ms: f.delay_ms,
          },
        ];
    }
  }
  return [
    ...faults.map((f) => ({ ...f, origin: f.kind.startsWith('model_')?'model':"source" })),
    ...additional.map((f) => ({ ...f, origin: "binding" })),
  ].map((f: GuideDoc) => ({
    ...f,
    ...(f.kind === 'ecu_reset' && f.reset_restart_ms ? {source_end_ms:f.end_ms,end_ms:f.start_ms+f.reset_restart_ms}:{}),
    boundary: boundaries[f.kind] ?? f.boundary,
  }));
}
export function hardwareState(
  profile: GuideDoc | undefined,
  readiness: GuideDoc | null,
) {
  if (!profile)
    return { label: "대상 미선택", tone: "unknown", confirmed: false };
  if (profile.target !== "hardware")
    return { label: "참조 ECU 프로세스", tone: "reference", confirmed: false };
  const matching = readiness?.profile_id === profile.id;
  const identities = readiness?.device?.identities;
  const confirmed = !!(
    matching &&
    readiness?.available === true &&
    readiness?.physical_hardware_connected === true &&
    identities?.adas &&
    identities?.brake
  );
  return {
    label: confirmed
      ? "실물 장비 · 연결 확인됨"
      : matching && readiness?.available === false
        ? "실물 장비 · 미연결"
        : "실물 장비 · 연결 미확인",
    tone: confirmed ? "hardware" : "unknown",
    confirmed,
  };
}
export function sourceEditorUrl(
  project: string,
  kind: string,
  source: GuideDoc | undefined,
) {
  if (!source) return null;
  const q = new URLSearchParams({
    project,
    mode: kind === "hil-test" ? "beta" : "vecu",
    test: source.id,
    revision: String(source.revision),
  });
  if (kind === "vecu-network-test") q.set("tab", "network");
  return `#hil-sim?${q}`;
}
export function intervalState(faults: GuideDoc[], ms: number) {
  if (!faults.length) return "주입 없음";
  if (faults.some((f) => f.start_ms <= ms && ms < f.end_ms))
    return "고장 주입 구간";
  return faults.every((f) => ms < f.start_ms)
    ? "주입 전"
    : "주입 종료 · 회복 관찰";
}
