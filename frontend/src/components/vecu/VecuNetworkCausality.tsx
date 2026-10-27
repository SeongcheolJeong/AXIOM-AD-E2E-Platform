import { useEffect, useState } from "react";
import { api } from "../../api";
import { faultCondition, numberText } from "./network-observation";

type Doc = Record<string, any>;
const stageNames: Record<string, string> = {
  injection: "고장 주입",
  detection: "ECU 이상 검출",
  can_tx: "CAN 제동 명령 송신",
  can_rx: "Brake ECU 명령 수신",
  application: "차량 제동 적용",
  vehicle: "제어 반영 후 첫 차량 관측",
  slowdown: "이후 최초 속도 감소 관측",
  stop: "정지 수준 관측",
};
function useCausality(id: string, project: string) {
  const [report, setReport] = useState<Doc | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setReport(null);
    setError("");
    if (id)
      void api<Doc>(
        `/virtual-ecu-network/executions/${encodeURIComponent(id)}/causality?project_id=${encodeURIComponent(project)}`,
      )
        .then((value) => {
          if (controller.signal.aborted) return;
          if (
            value.schema_version !== "vecu-network-causality/1" ||
            value.execution_id !== id ||
            value.project_id !== project ||
            value.side !== "faulted"
          )
            throw Error("인과 분석의 실행·프로젝트·버전이 일치하지 않습니다.");
          setReport(value);
        })
        .catch((e) => {
          if (!controller.signal.aborted) setError(String(e));
        });
    return () => controller.abort();
  }, [id, project]);
  return { report, error };
}
function basis(record: Doc | undefined) {
  const evidence = record?.stages?.detection?.evidence;
  if (!evidence) return "검출 근거 미관측";
  return record?.fault.kind === "udp_delay"
    ? `객체 생성 후 ${numberText(evidence.object_age_ms)} ms 경과 > 허용 ${numberText(evidence.threshold_ms)} ms · 마지막 객체 생성 ${numberText(evidence.source_time_s, 3)}초`
    : `생존 신호 미수신 시간 초과 · 마지막 수신 ${numberText(evidence.last_heartbeat_ms / 1000, 3)}초 · 검출 시 ${numberText(evidence.observed_silence_ms)} ms 미수신`;
}
const time = (point: Doc | undefined | null) =>
  point ? `${numberText(point.time_ms / 1000, 3)}초` : "미관측";

export default function VecuNetworkCausality({
  project,
  run,
  history,
  cursor,
  faultIndex,
  onFaultIndex,
  onStep,
  delayId,
  restbusId,
  onDelayId,
  onRestbusId,
  onNavigate,
}: {
  project: string;
  run: Doc;
  history: Doc[];
  cursor: number;
  faultIndex: number;
  onFaultIndex: (value: number) => void;
  onStep: (value: number) => void;
  delayId: string;
  restbusId: string;
  onDelayId: (value: string) => void;
  onRestbusId: (value: string) => void;
  onNavigate: (id: string, step: number) => void;
}) {
  const current = useCausality(run.id, project),
    delay = useCausality(delayId, project),
    restbus = useCausality(restbusId, project);
  const report = current.report,
    selected =
      report?.cases?.[
        Math.min(faultIndex, Math.max(0, (report?.cases?.length ?? 1) - 1))
      ];
  const exact =
    report?.manifest_sha256 === run.manifest_hash &&
    run.artifacts?.some(
      (a: Doc) =>
        a.name === "vecu-trace.json" &&
        a.sha256 === report?.source_artifact?.sha256,
    );
  const options = (kind: string) =>
    history.filter(
      (item) =>
        item.status === "completed" &&
        item.mode === "carla_closed_loop" &&
        item.faults?.length === 1 &&
        item.faults[0].kind === kind &&
        (kind !== "udp_delay" || item.faults[0].delay_ms === 400),
    );
  const pair = [delay.report, restbus.report],
    cases = pair.map((r) => r?.cases?.[0]);
  const compatible =
    pair.every(Boolean) &&
    pair[0]?.adapter_version === pair[1]?.adapter_version &&
    pair[0]?.fidelity === pair[1]?.fidelity &&
    pair[0]?.comparison_identity_sha256 ===
      pair[1]?.comparison_identity_sha256 &&
    cases[0]?.fault.kind === "udp_delay" &&
    cases[0]?.fault.delay_ms === 400 &&
    cases[1]?.fault.kind === "restbus_drop" &&
    cases[0]?.fault.start_ms === cases[1]?.fault.start_ms &&
    cases[0]?.fault.end_ms === cases[1]?.fault.end_ms;
  return (
    <section className="vecu-causality" aria-label="고장 검출·제동 시간 추적">
      <header>
        <div>
          <h3>고장 주입 → ECU 검출 → CAN 명령 → 차량 반응</h3>
          <p>
            원본 이벤트·CAN 바이트·차량 제어 readback을 연결한 보존 결과입니다.
            시간은 공통 가상시간입니다.
          </p>
        </div>
        {report?.cases?.length > 1 && (
          <label>
            분석할 고장
            <select
              aria-label="인과 분석 고장"
              value={faultIndex}
              onChange={(e) => onFaultIndex(Number(e.target.value))}
            >
              {report?.cases.map((c: Doc, i: number) => (
                <option value={i} key={i}>
                  {faultCondition(c.fault)}
                </option>
              ))}
            </select>
          </label>
        )}
      </header>
      {current.error ? (
        <p className="vecu-error">{current.error}</p>
      ) : !report ? (
        <p>원본 trace 인과 관계 확인 중…</p>
      ) : !exact ? (
        <p role="alert" className="vecu-error">
          선택 실행의 manifest·trace 해시가 달라 분석을 표시하지 않습니다.
        </p>
      ) : selected ? (
        <>
          <p className="vecu-causality-basis">
            <b>B측 · {faultCondition(selected.fault)}</b>
            <br />
            {basis(selected)}
          </p>
          {selected.note && <p>{selected.note}</p>}
          <div className="vecu-causality-stages">
            {Object.entries(stageNames)
              .slice(0, 6)
              .map(([key, label]) => {
                const point = selected.stages[key];
                return (
                  <button
                    key={key}
                    disabled={!point}
                    className={
                      point?.step === cursor
                        ? "selected"
                        : point?.step < cursor
                          ? "observed"
                          : ""
                    }
                    onClick={() => point && onStep(point.step)}
                  >
                    <small>{label}</small>
                    <strong>{time(point)}</strong>
                    {point &&
                      ["can_tx", "can_rx", "application"].includes(key) && (
                        <span>
                          제동 {numberText(point.evidence.brake * 100)}%
                        </span>
                      )}
                    {point && key === "vehicle" && (
                      <span>
                        속도 {numberText(point.evidence.speed_mps, 3)} m/s ·
                        제동 {numberText(point.evidence.brake * 100)}%
                      </span>
                    )}
                    <span>
                      {point
                        ? point.step <= cursor
                          ? "선택 구간까지 관측됨"
                          : "선택 구간 이후"
                        : "근거 미관측"}
                    </span>
                    {key !== "injection" && point && (
                      <span>
                        주입 후{" "}
                        {numberText(
                          selected.latency_ms?.[`injection_to_${key}`],
                        )}{" "}
                        ms
                      </span>
                    )}
                  </button>
                );
              })}
          </div>
          <p>
            검출 → CAN 송신{" "}
            <b>{numberText(selected.latency_ms?.detection_to_can_tx)} ms</b> ·
            주입 → 제동 적용{" "}
            <b>
              {numberText(selected.latency_ms?.injection_to_application)} ms
            </b>{" "}
            · 이후 최초 속도 감소 <b>{time(selected.stages.slowdown)}</b>
          </p>
          <details>
            <summary>검출 근거·단계별 원본 위치</summary>
            <pre>
              {JSON.stringify(
                {
                  execution_id: report.execution_id,
                  test_ref: report.test_ref,
                  manifest_sha256: report.manifest_sha256,
                  source_artifact: report.source_artifact,
                  stages: selected.stages,
                  analysis_sha256: report.sha256,
                },
                null,
                2,
              )}
            </pre>
          </details>
        </>
      ) : (
        <p>주입한 고장이 없는 실행입니다.</p>
      )}
      <p className="vecu-measure-scope">
        이벤트 버튼은 해당 이벤트를 포함하는 50 ms 입력 구간으로 이동합니다.
        네트워크 이벤트는 1 ms, 객체 정책 검출은 50 ms 제어 실행 기준이며 차량
        관측은 제어 적용 후 시각입니다. 영상은 가장 가까운 보존 프레임이므로
        검출 시각과 다를 수 있습니다. ‘첫 차량 관측’은 감속 시작 판정이 아니며,
        속도 감소는 한 차량 단계에서 0.01 m/s 이상 감소한 첫 관측입니다.
      </p>
      <div
        className="vecu-causality-comparison"
        aria-label="400ms 지연과 Restbus 단절 비교"
      >
        <h3>고장 유형 비교 · 서로 다른 실행의 B측</h3>
        <p>
          현재 영상의 정상 A / 고장 B와 구분해서, 아래 두 고장 실행을
          비교합니다. 시각 버튼을 누르면 해당 실행의 영상·ECU 상태로 이동합니다.
        </p>
        <div className="vecu-fields">
          {[
            ["udp_delay", "400ms 지연 실행", delayId, onDelayId],
            ["restbus_drop", "Restbus 단절 실행", restbusId, onRestbusId],
          ].map(([kind, label, id, set]) => (
            <label key={String(kind)}>
              {String(label)}
              <select
                aria-label={String(label)}
                value={String(id)}
                onChange={(e) => (set as (id: string) => void)(e.target.value)}
              >
                <option value="">보존 실행 선택</option>
                {options(String(kind)).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · {item.id.slice(-6)}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </div>
        {(delay.error || restbus.error) && (
          <p className="vecu-error">{delay.error || restbus.error}</p>
        )}
        {!pair.every(Boolean) ? (
          <p>
            완료된 두 고장 실행을 선택하면 검출 근거와 소요 시간을 비교합니다.
          </p>
        ) : (
          <>
            <p className={compatible ? "vecu-policy-note" : "vecu-error"}>
              {compatible
                ? "같은 시나리오·seed·네트워크·런타임·고장 구간 확인됨"
                : "실행 조건이 다릅니다. 차이를 고장 종류만의 영향으로 해석할 수 없습니다."}
            </p>
            <div className="vecu-measured-table">
              <table>
                <thead>
                  <tr>
                    <th>원본 시각 · +ms는 주입 후 경과</th>
                    <th>UDP 400ms 지연</th>
                    <th>Restbus 단절</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <th>ECU 검출 근거</th>
                    {cases.map((c, i) => (
                      <td key={i}>{basis(c)}</td>
                    ))}
                  </tr>
                  {Object.entries(stageNames).map(([key, label]) => (
                    <tr key={key}>
                      <th>{label}</th>
                      {cases.map((c, i) => {
                        const point = c?.stages[key];
                        return (
                          <td key={i}>
                            {point ? (
                              <button
                                onClick={() =>
                                  onNavigate(pair[i]!.execution_id, point.step)
                                }
                              >
                                {time(point)}
                                {key !== "injection" && (
                                  <small>
                                    · +{" "}
                                    {numberText(
                                      c?.latency_ms?.[`injection_to_${key}`],
                                    )}{" "}
                                    ms
                                  </small>
                                )}
                              </button>
                            ) : (
                              "미관측"
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                  <tr>
                    <th>검출 → CAN 송신</th>
                    {cases.map((c, i) => (
                      <td key={i}>
                        {numberText(c?.latency_ms?.detection_to_can_tx)} ms
                      </td>
                    ))}
                  </tr>
                </tbody>
              </table>
            </div>
            {compatible && cases.every((c) => c?.status === "observed") && (
              <p className="vecu-causality-basis">
                이 두 실행에서는 Restbus 단절의 검출이{" "}
                {numberText(
                  cases[0].latency_ms.injection_to_detection -
                    cases[1].latency_ms.injection_to_detection,
                )}{" "}
                ms, 제동 적용이{" "}
                {numberText(
                  cases[0].latency_ms.injection_to_application -
                    cases[1].latency_ms.injection_to_application,
                )}{" "}
                ms 빨랐습니다. 400ms는 주입한 전송 지연이며 ECU 검출 대기시간이
                아닙니다. 객체 신선도와 생존 신호 시간 초과라는 서로 다른 검출
                기준의 결과입니다.
              </p>
            )}
            {pair.map((r, i) => (
              <details key={i}>
                <summary>
                  {i ? "Restbus" : "400ms 지연"} 실행·시험·원본 해시
                </summary>
                <pre>
                  {JSON.stringify(
                    {
                      execution_id: r!.execution_id,
                      test_ref: r!.test_ref,
                      seed: r!.seed,
                      adapter_version: r!.adapter_version,
                      manifest_sha256: r!.manifest_sha256,
                      source_artifact: r!.source_artifact,
                      analysis_sha256: r!.sha256,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
            ))}
          </>
        )}
      </div>
    </section>
  );
}
