import {
  brakeReason,
  networkObservation,
  numberText as value,
  stateName,
} from "./network-observation";
type Doc = Record<string, any>;

export default function VecuNetworkFlow({
  rows,
  closed,
  test,
}: {
  rows: Doc[];
  closed: boolean;
  test: Doc | null;
}) {
  return (
    <section
      className="vecu-network-flow"
      aria-label="Native ECU network signal flow"
    >
      <header>
        <h3>
          선택 시점 {value(rows[0]?.request?.time_s, 2)}초 · 고장 → ECU → 차량
        </h3>
        <p>
          원본 객체 → Ethernet/UDP → ADAS OS·BSW → CAN 0x100 → Brake ECU →{" "}
          {closed ? "CARLA 차량" : "출력 기록"}
        </p>
      </header>
      <div className="vecu-network-overview">
        {rows.map((row, index) => {
          const observation = networkObservation(
            row,
            index,
            Array.isArray(test?.faults) ? test.faults : null,
            closed,
          );
          return (
            <div
              className={`vecu-network-summary-side ${index ? "side-b" : ""}`}
              key={index}
            >
              <div className="vecu-network-flow-title">
                <b>{index ? "B · 고장 시험" : "A · 정상 기준"}</b>
                <span>
                  {observation.phase} · {stateName(row.reply?.state)}
                </span>
              </div>
              <ol
                className="vecu-network-cause"
                aria-label={`${index ? "B" : "A"} 선택 시점 요약`}
              >
                <li>
                  <b>① 고장 위치 / 현재 구간</b>
                  <span>{observation.location}</span>
                </li>
                <li>
                  <b>② ECU 보호·제어 동작</b>
                  <span>{observation.protection}</span>
                </li>
                <li>
                  <b>③ 차량 반응</b>
                  <span>{observation.response}</span>
                </li>
              </ol>
            </div>
          );
        })}
      </div>
      <details className="vecu-network-detail">
        <summary>수신·태스크·제동 상세 경로 보기</summary>
        {rows.map((row, index) => {
          const net = row.reply?.network ?? {},
            brake = net.brake ?? {},
            stats = net.stats ?? {},
            packet = row.request?.object_packet;
          const commandAge =
            typeof net.time_ms === "number" &&
            typeof brake.last_command_ms === "number"
              ? net.time_ms - brake.last_command_ms
              : null;
          const state = row.reply?.state,
            received =
              typeof row.reply?.object_sequence === "number" &&
              row.reply.object_sequence >= 0;
          const controller = net.controller_steps?.at(-1)?.reply,
            adasFault =
              !received ||
              net.heartbeat_override ||
              controller?.state === "degraded" ||
              (net.time_ms > 0 && !controller);
          const observation = networkObservation(
            row,
            index,
            Array.isArray(test?.faults) ? test.faults : null,
            closed,
          );
          return (
            <article
              className={`vecu-network-flow-side ${index ? "side-b" : ""}`}
              key={index}
            >
              <div className="vecu-network-flow-title">
                <b>{index ? "B · 고장 시험" : "A · 정상 기준"}</b>
                <span>
                  {observation.phase} · {stateName(state)} ({state ?? "미보존"})
                </span>
              </div>
              <div className="vecu-network-chain">
                <div className="vecu-network-node">
                  <small>PERCEPTION ECU</small>
                  <strong>생성 seq {packet?.sequence ?? "—"}</strong>
                  <span>생성 시각 {value(packet?.time_s, 2)} s</span>
                  <span>객체 {packet?.objects?.length ?? "—"}개</span>
                  <i>Ethernet / UDP →</i>
                </div>
                <div
                  className={`vecu-network-node ${adasFault ? "fault-active" : ""}`}
                >
                  <small>ADAS ECU · 실제 OS / BSW</small>
                  <strong>
                    {received
                      ? `수신 seq ${row.reply.object_sequence}`
                      : "미수신"}
                  </strong>
                  <span>
                    입력 age{" "}
                    {received
                      ? `${value(row.reply?.object_age_s * 1000)} ms`
                      : "—"}
                  </span>
                  <span>
                    OS ControlTask 누적 {stats.os_control_tasks ?? "—"}회
                  </span>
                  <span>
                    ADAS epoch {net.epoch ?? "—"} · reset {stats.resets ?? "—"}
                    회
                  </span>
                  <span>
                    객체 정책{" "}
                    {controller
                      ? `${stateName(controller.state)} (${controller.state})`
                      : "이번 구간 실행 없음"}
                    {net.heartbeat_override
                      ? " · 생존 신호 보호 제동 적용"
                      : ""}
                  </span>
                  <span className="vecu-network-stack">Com → PduR → CanIf</span>
                  <i>CAN 0x100 →</i>
                </div>
                <div
                  className={`vecu-network-node ${brake.reason !== "command" || net.heartbeat_override ? "fault-active" : ""}`}
                >
                  <small>BRAKE ECU</small>
                  <strong>
                    적용 제동 {value(row.reply?.control?.brake * 100)}%
                  </strong>
                  <span>
                    최근 CAN seq {brake.command_sequence ?? "—"} · 수신 후{" "}
                    {value(commandAge)} ms
                  </span>
                  <span>{brakeReason(brake.reason)}</span>
                  <i>{closed ? "차량 제어 →" : "명령 기록 →"}</i>
                </div>
                <div className="vecu-network-node">
                  <small>{closed ? "CARLA 차량" : "고정 재생 출력"}</small>
                  <strong>
                    {closed
                      ? `${value(row.next_ego?.speed_mps, 3)} m/s`
                      : "차량 반응 없음"}
                  </strong>
                  <span>
                    {closed
                      ? "제어 적용 후 다음 관측"
                      : "출력 명령은 입력 ego를 바꾸지 않음"}
                  </span>
                  <span>
                    Throttle {value(row.reply?.control?.throttle * 100)}% ·
                    Steer {value(row.reply?.control?.steer, 3)}
                  </span>
                </div>
              </div>
              <div
                className={`vecu-network-restbus ${net.heartbeat_override ? "fault-active" : ""}`}
              >
                <small>RESTBUS ECU</small>
                <b>차량 상태 / 생존 신호</b>
                <span>CAN 0x200 / 0x201 → ADAS</span>
                <span>
                  {net.heartbeat_override === true
                    ? (stats.heartbeat_timeouts ?? 0) > 0
                      ? "생존 신호 시간 초과 · 보호 제동 적용"
                      : "생존 신호 확인 전 · 보호 제동 적용"
                    : net.heartbeat_override === false
                      ? "생존 신호 수신 정상"
                      : "생존 신호 상태 미보존"}{" "}
                  · 시간 초과 누적 {stats.heartbeat_timeouts ?? "—"}회
                </span>
              </div>
            </article>
          );
        })}
      </details>
      <p className="vecu-measure-scope">
        ‘수신 CAN 명령 적용 중’은 명령의 유효성을 뜻하며, 그 명령이 100% 제동일
        수도 있습니다. 색은 보호·대기 상태를 강조하며 제동량은 숫자로
        확인하세요. seq는 메시지 번호, 입력 age는 생성 후 경과 시간, 수신 후
        시간은 마지막 CAN 명령 수신 이후 경과 시간입니다. 값은 선택 실행의
        trace에서 읽으며 노드 배치는 논리 연결입니다.
      </p>
    </section>
  );
}
