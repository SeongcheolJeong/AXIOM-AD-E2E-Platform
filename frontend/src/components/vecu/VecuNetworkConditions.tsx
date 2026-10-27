import { faultCondition, networkObservation } from "./network-observation";
type Doc = Record<string, any>;

export default function VecuNetworkConditions({
  test,
  rows,
}: {
  test: Doc | null;
  rows: Doc[];
}) {
  const schedule = Array.isArray(test?.faults) ? test.faults : null;
  return (
    <section className="vecu-network-conditions" aria-label="A/B 실행 조건">
      <h3>A/B 비교 조건 · 선택 실행에 고정된 시험</h3>
      <p>
        선택한 시험 1건 안에서 고장을 넣지 않은 A와 고장을 넣은 B를 같은
        가상시각으로 비교합니다. 시나리오·seed는 같고, 고장 이후의 차량 움직임은
        달라질 수 있습니다.
      </p>
      <div className="vecu-network-condition-pair">
        <div>
          <b>A · 정상 기준</b>
          <strong>고장 주입 없음</strong>
          <span>
            정상 기준도 앞차 추종·초기화 상황에 따라 제동할 수 있습니다.
          </span>
        </div>
        <div>
          <b>B · 고장 시험</b>
          {schedule === null ? (
            <strong>실행 고장 일정 미보존</strong>
          ) : schedule.length ? (
            <ul>
              {schedule.map((fault: Doc, index: number) => (
                <li key={index}>{faultCondition(fault)}</li>
              ))}
            </ul>
          ) : (
            <strong>고장 주입 없음 · A와 같은 조건</strong>
          )}
          <span>
            선택 시점:{" "}
            {networkObservation(rows[1] ?? {}, 1, schedule, false).phase}
          </span>
        </div>
      </div>
      <p className="vecu-measure-scope">
        시험 {test?.id ?? "미확인"} · r{test?.revision ?? "—"} · seed{" "}
        {test?.seed ?? "—"} · 구간은 시작 포함·종료 제외. 편집 중인 설정은 이
        결과에 반영되지 않습니다.
      </p>
    </section>
  );
}
