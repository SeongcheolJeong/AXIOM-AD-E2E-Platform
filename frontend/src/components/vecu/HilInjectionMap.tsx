import {
  Activity,
  ArrowUp,
  ArrowLeft,
  ArrowRight,
  Box,
  CheckCircle2,
  CircuitBoard,
  Info,
  Radio,
  Settings2,
  ShieldCheck,
} from "lucide-react";
import {
  capabilityLabel,
  draftInjections,
  faultLabel,
  hardwareState,
  injectionPoints,
  intervalState,
  otherBoundaries,
  sourceEditorUrl,
  savedSourceMatches,
} from "./hilInjectionGuide";
import type { GuideDoc as D } from "./hilInjectionGuide";
type Props = {
  project: string;
  profile?: D;
  catalog?: D;
  readiness: D | null;
  saved: D | null;
  source?: D;
  sourceKind: string;
  sourceKey: string;
  additional: D[];
  lateness: number;
  dirty: boolean;
  point: string;
  onPoint: (v: string) => void;
  scope: boolean;
  onScope: (v: boolean) => void;
  run: D | null;
  causes: D | null;
  row?: D;
  step: number;
  onStep: (v: number) => void;
  onConfigure: (where: "feedback" | "internal" | "validity" | "device") => void;
};
export default function HilInjectionMap(p: Props) {
  const faults = draftInjections(
    p.saved,
    p.source,
    p.sourceKind,
    p.sourceKey,
    p.additional,
    p.catalog?.boundaries,
  );
  const selected =
    injectionPoints.find((v) => v.id === p.point) ?? injectionPoints[0];
  const active = faults.filter((f) => selected.kinds.includes(f.kind));
  const hardware = hardwareState(p.profile, p.readiness),
    physical = p.profile?.target === "hardware";
  const savedMatch = savedSourceMatches(p.saved, p.sourceKind, p.sourceKey);
  const original = p.source ?? (savedMatch ? p.saved?.source_ref : undefined);
  const editor = sourceEditorUrl(p.project, p.sourceKind, original);
  const sameRun = !!(
    p.run?.status === "completed" &&
    p.saved &&
    !p.dirty &&
    p.run.summary?.binding_ref?.sha256 === p.saved.sha256
  );
  const cases = sameRun
    ? (p.causes?.cases ?? []).filter((c: D) =>
        selected.kinds.includes(c.fault.kind),
      )
    : [];
  const status = (point: typeof selected) => {
    if (!p.profile) return "프로필 확인 전";
    const states = point.kinds.map((k) =>
      capabilityLabel(p.profile?.fault_capabilities?.[k]),
    );
    return states.every((state) => state === states[0])
      ? states[0]
      : "고장별 지원 상태 다름";
  };
  const pin = (id: string) => {
    const point = injectionPoints.find((v) => v.id === id)!;
    const fs = faults.filter((f) => point.kinds.includes(f.kind));
    return (
      <button
        type="button"
        className={`hil-map-pin ${p.point === id ? "selected" : ""} ${fs.length ? "has-fault" : ""}`}
        aria-label={`주입 위치 ${point.number} ${point.title}`}
        aria-pressed={p.point === id}
        onClick={() => {
          p.onPoint(id);
          p.onScope(false);
        }}
      >
        <span>{point.number}</span>
        <b>{point.title}</b>
        <small>
          {fs.length
            ? fs
                .map((f) => `${f.start_ms / 1000}–${f.end_ms / 1000}s`)
                .join(" · ")
            : "주입 없음"}
        </small>
      </button>
    );
  };
  const node = (title: string, subtitle: string, ecu = false) => (
    <div className={`hil-map-node ${ecu ? hardware.tone : "software"}`}>
      <b>{title}</b>
      <small>{subtitle}</small>
      <span className={`hil-role ${ecu ? hardware.tone : "software"}`}>
        {ecu ? hardware.label : "소프트웨어 모델"}
      </span>
    </div>
  );
  const canId = (name: string) =>
    Number.isInteger(p.profile?.can?.[name])
      ? `0x${p.profile!.can[name].toString(16).toUpperCase()}`
      : "매핑 확인 필요";
  const signal =
    selected.id === "objects"
      ? "AXO1 · 객체 목록 / sequence / 생성 시각"
      : selected.id === "restbus"
        ? `${canId("vehicle_state_id")} 차량 상태 · ${canId("heartbeat_id")} heartbeat`
        : selected.id === "command"
          ? `${canId("command_id")} throttle / brake / steer / counter`
          : selected.id === "feedback"
            ? `${canId("status_id")} Brake 보고 출력`
            : selected.path;
  const detector = () => {
    if(selected.id.startsWith('model_'))return <><b>참조 ADAS의 모델 측정 감시</b><p>{selected.id==='model_power'?'출력 전압 구간 최솟값이 8V 미만':selected.id==='model_analog'?'출력 전압 구간이 2–3V 밖':'프레임 abort 관측 또는 미복구 상태'}이면 보호 제동합니다.</p><small>보호 유지 시간·복귀 표본 수는 아래 모델 연결에서 설정합니다. 실제 PMIC/ADC 또는 픽셀 인지 검증이 아닙니다.</small></>;
    if (selected.id === "feedback")
      return (
        <>
          <b>ECU 내부 감시 항목 아님</b>
          <p>
            게이트웨이가 피드백 신선도를 확인합니다. 피드백 상실을 ECU의 이상
            검출로 표시하지 않습니다.
          </p>
        </>
      );
    if (!["objects", "restbus", "command"].includes(selected.id))
      return (
        <>
          <b>장치 계약 필요</b>
          <p>
            진단·시험 훅이 등록되지 않은 상태에서는 검출 기준을 확정하지
            않습니다.
          </p>
        </>
      );
    if (physical || !p.profile)
      return (
        <>
          <b>실물 ECU 감시 조건 · 미확인</b>
          <p>
            장치별 설정·진단 근거가 필요합니다. 참조 ECU의 250ms / 100ms 값을
            실물 ECU 설정으로 사용하지 않습니다.
          </p>
        </>
      );
    return (
      <>
        <b>
          {selected.id === "objects"
            ? "입력 생성 후 경과 > 250ms"
            : selected.id === "restbus"
              ? "heartbeat 100ms 미수신"
              : "CAN 명령 100ms 미수신"}
        </b>
        <p>
          {selected.id === "command"
            ? "Brake ECU 자체 watchdog"
            : "ADAS ECU 내부 참조 로직"}{" "}
          → 보호 제동
        </p>
        <small>
          현재 예시 참조 구현의 고정 규칙 · 읽기 전용. 고장 지연값과 별개입니다.
        </small>
      </>
    );
  };
  return (
    <section
      className="hil-injection-workspace"
      aria-label="파이프라인과 고장 주입 안내"
    >
      <header className="hil-injection-header">
        <div>
          <span className="hil-eyebrow">PIPELINE & INJECTION</span>
          <h2>어디에 고장을 주입하나요?</h2>
          <p>번호를 눌러 변경할 신호·설정·검출 주체를 함께 확인하세요.</p>
        </div>
        <button
          type="button"
          aria-pressed={p.scope}
          onClick={() => p.onScope(!p.scope)}
        >
          <Info size={16} />
          {p.scope ? "주입 지도 보기" : "전체 지원 범위"}
        </button>
      </header>
      <div className="hil-map-context">
        <span>
          <Box size={15} />
          소프트웨어
        </span>
        <span>
          <CircuitBoard size={15} />
          참조 ECU
        </span>
        <span>
          <Radio size={15} />
          실물 장비는 연결 확인 별도
        </span>
        <strong>
          {!p.profile
            ? "실행 대상 확인 중"
            : physical
              ? "선택 대상 · 실물 ADAS + Brake"
              : "선택 대상 · 가상 CAN + 참조 ECU"}
        </strong>
      </div>
      {p.scope ? (
        <section className="hil-scope-panel" aria-label="전체 주입 지원 범위">
          <h3>전체 지원 범위 · 현재 선택 프로필</h3>
          <p>
            지원은 어댑터 기능입니다. 장비 연결과 시험 준비가 확인돼야 실행할 수
            있습니다.
          </p>
          <div className="hil-table-scroll">
            <table>
              <thead>
                <tr>
                  <th>경계</th>
                  <th>주입할 수 있는 고장</th>
                  <th>지원 상태</th>
                </tr>
              </thead>
              <tbody>
                {injectionPoints.map((point) => (
                  <tr key={point.id}>
                    <td>
                      <button
                        onClick={() => {
                          p.onPoint(point.id);
                          p.onScope(false);
                        }}
                      >
                        {point.number} · {point.title}
                      </button>
                    </td>
                    <td>
                      {point.kinds
                        .map(
                          (kind) =>
                            `${faultLabel({ kind })} (${capabilityLabel(p.profile?.fault_capabilities?.[kind])})`,
                        )
                        .join(" / ")}
                    </td>
                    <td>{status(point)}<small className="hil-scope-reason">{point.kinds.map(k=>p.profile?.fault_support_details?.[k]).filter(Boolean).join(" · ")}</small></td>
                  </tr>
                ))}
                {Object.entries(otherBoundaries).filter(()=>!p.profile?.model_loop).map(([id, title]) => (
                  <tr className="hil-scope-model" key={id}>
                    <td>{title}</td>
                    <td>{p.profile?.model_loop?'모델 출력 → 참조 ECU 감시 → CARLA 연결 가능':'실물 경계 어댑터 미연결'}{p.profile?.boundary_model_tests?.includes(id)&&<p><button onClick={()=>document.getElementById("hil-boundary-models")?.scrollIntoView({behavior:"smooth",block:"start"})}>RTL / 회로 주입·ECU 연결 설정 ↓</button></p>}</td>
                    <td>
                      {p.profile?.model_loop ? '참조 ECU 루프 지원 · 실물 미연결' : p.profile?.boundary_model_tests?.includes(id) ? '별도 모델 주입 구현됨 · 실물 미연결' : capabilityLabel(p.profile?.other_boundaries?.[id])}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p>
            현재 CARLA 영상은 차량 반응을 보는 관찰 영상입니다. 카메라 RAW·CSI
            픽셀 경로와 FPGA ISP는 이 객체 입력 시험에 연결되지 않았습니다.
          </p>
        </section>
      ) : (
        <div className="hil-injection-layout">
          <div className="hil-map-canvas" aria-label="고장 주입 위치 지도">
            <div className="hil-map-main">
              {node("CARLA / Perception", "객체 · 차량 자세 · 경로")}
              <ArrowRight className="hil-flow-arrow" />
              {node("ADAS ECU", "입력 감시 · 주행 판단", true)}
              <ArrowRight className="hil-flow-arrow" />
              {node("Brake ECU", "명령 감시 · 출력 보고", true)}
            </div>
            <div className="hil-map-pins">
              <div>
                {pin("objects")}
                <small className="hil-pin-path">Perception → ADAS</small>
              </div>
              <div className="hil-map-restbus">
                <ArrowUp size={18} />
                {pin("restbus")}
                <span>
                  ↑ Restbus · 소프트웨어
                  <br />
                  차량 상태 + heartbeat
                </span>
              </div>
              <div>
                {pin("command")}
                <small className="hil-pin-path">
                  CAN A → 게이트웨이 → CAN B
                </small>
              </div>
            </div>
            <div className="hil-map-return">
              <ArrowLeft size={22} />
              {pin("feedback")}
              <span>
                보고된 출력 → CARLA 차량 반응
                <br />
                <small>다음 차량 상태를 다시 입력에 반영</small>
              </span>
            </div>
            <div className="hil-model-map-pins">{['model_camera_raw_csi','model_fpga','model_power','model_analog'].map(id=><div key={id}>{pin(id)}</div>)}</div>
            <p className="hil-map-caption">
              <Info size={16} />
              Linux 게이트웨이가 UDP 송신·분리 CAN 구간 중계·피드백 전달에
              고장을 주입합니다. CARLA 영상은 차량 반응을 보는 관찰용입니다.
            </p>
            <div className="hil-hardware-status">
              <ShieldCheck size={18} />
              <div>
                <b>
                  {physical || !p.profile
                    ? hardware.label
                    : "실물 ECU 0대 · 두 ECU 모두 참조 프로세스"}
                </b>
                <small>
                  {p.readiness?.profile_id === p.profile?.id &&
                  p.readiness?.checked_at
                    ? `최근 확인 ${new Date(p.readiness?.checked_at).toLocaleTimeString()} · 현재 연결의 지속 보장은 아님`
                    : "프로필 선택만으로 장비 연결을 확인하지 않습니다."}
                </small>
                {hardware.confirmed && (
                  <small>
                    ADAS {String(p.readiness?.device.identities.adas)} · Brake{" "}
                    {String(p.readiness?.device.identities.brake)}
                  </small>
                )}
              </div>
              <button onClick={() => p.onConfigure("device")}>장비 설정</button>
            </div>
          </div>
          <aside
            className="hil-injection-inspector"
            aria-label="선택 주입 위치 설명"
          >
            <div className="hil-inspector-title">
              <span>{selected.number}</span>
              <div>
                <h3>{selected.title}</h3>
                <small>{selected.path}</small>
              </div>
              <em>{status(selected)}</em>
            </div>
            <p>{selected.kinds.map(k=>p.profile?.fault_support_details?.[k]).filter(Boolean).join(" · ")}</p>
            <p className="hil-selected-signal">
              <b>대상 신호</b> {signal}
            </p>
            <div className="hil-condition-grid">
              <article className="hil-condition fault">
                <label>① 주입할 고장 · 시험기가 변경</label>
                {active.length ? (
                  active.map((f: D, i: number) => (
                    <div key={i}>
                      <b>{faultLabel(f)}</b>
                      <p>
                        {f.start_ms / 1000}–{f.end_ms / 1000}초 ·{" "}
                        {(f.end_ms - f.start_ms) / 1000}초 동안
                      </p>
                      <small>
                        {f.origin === "source"
                          ? `원본 SIL 시험 r${original?.revision ?? "?"}에서 가져옴`
                          : "이 HIL Binding에 추가한 고장"}
                      </small>
                    </div>
                  ))
                ) : (
                  <>
                    <b>이 위치에 주입 일정 없음</b>
                    <p>
                      가능한 종류:{" "}
                      {selected.kinds
                        .map((k) => faultLabel({ kind: k }))
                        .join(" / ")}
                    </p>
                  </>
                )}
                {selected.id.startsWith('model_')?<button onClick={()=>document.getElementById('hil-model-loop')?.scrollIntoView({behavior:'smooth',block:'start'})}>모델 출력·ECU 연결 설정 ↓</button>:["feedback","reset","os"].includes(selected.id) ? (
                  <button onClick={() => p.onConfigure(selected.id === "feedback" ? "feedback" : "internal")}>
                    <Settings2 size={14} />
                    {selected.id === "feedback" ? "피드백 고장 설정" : "참조 ECU 내부 주입 설정"}
                  </button>
                ) : editor ? (
                  <>
                    <a href={editor}>원본 시험에서 고장 편집 →</a>
                    <small>
                      원본 저장 후 이곳에서 새 시험 개정을 선택하세요.
                    </small>
                  </>
                ) : (
                  <small>아래에서 원본 SIL 시험을 먼저 선택하세요.</small>
                )}
              </article>
              <article className="hil-condition ecu">
                <label>② ECU 감시 조건 · 판단 기준</label>
                {detector()}
              </article>
              <article className="hil-condition validity">
                <label>③ 시험 유효성 · 시험기가 지킬 조건</label>
                <b>스케줄 지연 ≤ {p.lateness}ms</b>
                <p>
                  실제 송신·태스크가 예정 시각을 지켰는지 검사합니다. 주입할
                  지연값이나 ECU 타임아웃이 아닙니다.
                </p>
                {selected.id === "feedback" && (
                  <small>별도 중단 기준: 출력 미갱신 100ms</small>
                )}
                <button onClick={() => p.onConfigure("validity")}>
                  시험 허용값 설정
                </button>
              </article>
            </div>
            <details className="hil-point-details">
              <summary>정상 동작 · 고장 영향 · 회복 처리</summary>
              <dl>
                <dt>정상</dt>
                <dd>{selected.normal}</dd>
                <dt>고장</dt>
                <dd>{selected.effect}</dd>
                <dt>회복</dt>
                <dd>
                  {physical && selected.id !== "feedback"
                    ? "실물 장치별 회복 계약을 확인해야 합니다."
                    : selected.recovery}
                </dd>
                <dt>확인할 근거</dt>
                <dd>{selected.observation}</dd>
              </dl>
            </details>
          </aside>
        </div>
      )}
      <div className="hil-injection-summary" aria-live="polite">
        <Activity size={18} />
        <div>
          <b>
            {p.dirty
              ? "편집 중인 조건 · 저장 후 실행"
              : p.saved
                ? `저장된 연결 r${p.saved.revision}`
                : "원본 시험 선택 전"}
          </b>
          <p>
            {status(selected) !== "주입 기능 구현됨"
              ? "현재 선택 프로필은 이 경계의 실행을 지원하지 않습니다. 전용 드라이버 또는 장비 내부 시험 훅을 등록해야 합니다."
              : active.length
                ? `${active.map((f) => `${f.start_ms / 1000}–${f.end_ms / 1000}초 ${faultLabel(f)}`).join(", ")} → ${selected.id === "feedback" ? "시험기 피드백 상실 감시" : selected.id === "command" ? "Brake 명령 감시" : physical ? "ECU 진단 근거 확인" : "참조 ADAS 입력 감시"} → ${selected.id === "feedback" ? "시험 유효성·중단 기록" : "Brake 출력·차량 반응 관찰"}`
                : "지도에서 주입 위치를 선택하면 현재 일정과 관찰할 반응을 연결해 보여줍니다."}
          </p>
        </div>
      </div>
      {p.run && (
        <section className="hil-map-observation">
          <h3>
            {p.run.status === "completed" ? (
              <CheckCircle2 size={16} />
            ) : (
              <Activity size={16} />
            )}
            저장 실행의 관측 · {p.run.id.slice(-8)}
          </h3>
          {!sameRun ? (
            <p>
              {p.run.status !== "completed"
                ? `실행 상태: ${p.run.status} · 완료된 관측 타임라인이 없습니다. 아래 실행과 비교에서 진행 상태 또는 중단 원인을 확인하세요.`
                : "현재 편집 조건과 결과가 연결되지 않았습니다. 아래 실행 결과는 보존된 별도 조건이며, 편집안을 실행한 결과로 표시하지 않습니다."}
            </p>
          ) : (
            <>
              <p>
                공통 입력 {((p.step ?? 0) * 0.05).toFixed(2)}초 · B:{" "}
                <b>{intervalState(active, p.step * 50)}</b> · ADAS 진단{" "}
                {p.row?.reply?.bridge?.diagnostic_observed
                  ? ((
                      {
                        tracking: "정상 추종",
                        degraded: "보호 제동",
                        recovering: "복구 확인 중",
                      } as Record<string, string>
                    )[p.row.reply.state] ?? p.row.reply.state)
                  : "미관측"}{" "}
                · Brake 보고 출력{" "}
                {p.row
                  ? `${(p.row.reply.control.brake * 100).toFixed(0)}%`
                  : "미관측"}
              </p>
              {cases.map((c: D, i: number) => (
                <div className="hil-observed-stages" key={i}>
                  {[
                    ["injection", "주입"],
                    ["detection", "ECU 검출"],
                    ["can_command", "ADAS CAN 출력"],
                    ["brake_output", "Brake 출력"],
                    ["vehicle", "차량 관측"],
                  ].map(([key, label]) => {
                    const e = c.stages[key];
                    return (
                      <button
                        key={key}
                        disabled={!e}
                        onClick={() => {
                          if (e) p.onStep(e.step);
                        }}
                      >
                        {label}
                        <b>
                          {e?.time_ms != null
                            ? `${(e.time_ms / 1000).toFixed(3)}초`
                            : e
                              ? "시계 관계 미확인"
                              : "미관측"}
                        </b>
                      </button>
                    );
                  })}
                </div>
              ))}
              <small>
                저장 결과 재생 · ECU 내부 검출은 진단 보고가 있을 때만 표시.
                시각을 누르면 아래 영상과 함께 이동합니다.
              </small>
            </>
          )}
        </section>
      )}
    </section>
  );
}
