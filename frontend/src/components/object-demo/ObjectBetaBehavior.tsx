import { useResource } from "../../api";

type BehaviorReport = {
  status: "available" | "pending" | "unavailable";
  reason?: string;
  actors: {
    actor_id: string;
    role: string;
    mode: "authored_actions" | "route_only";
    actions: {
      action_id: string;
      kind: string;
      status: "pending" | "not_exercised" | "started" | "completed";
      started_at_s: number | null;
      completed_at_s: number | null;
      events?: { type: string; reason: string; time_s: number }[];
    }[];
    planner_arrival_time_s: number | null;
  }[];
  ego_outcome: {
    status: "arrived" | "stopped" | "moving" | "unavailable";
    distance_travelled_m?: number | null;
    goal_error_m?: number | null;
    final_speed_mps?: number | null;
    native_contact?: boolean | null;
  };
};
const actionLabel = {
  pending: "기록 준비 중",
  not_exercised: "행동 시작 기록 없음",
  started: "행동 시작 기록됨",
  completed: "planner 기록상 완료",
};
const outcomeLabel = {
  arrived: "목적지 근처 도착",
  stopped: "최종 정지",
  moving: "종료 시 이동 중",
  unavailable: "관측 정보 없음",
};
const timeoutPolicyLabel: Record<string, string> = {
  stop: "정지 유지",
  skip: "다음 행동으로 건너뜀",
  fail: "실패 처리",
};
const measurement = (value: number | null | undefined, unit: string) =>
  typeof value === "number" ? `${value.toFixed(2)} ${unit}` : "기록 없음";

export default function ObjectBetaBehavior({
  project,
  executionId,
}: {
  project: string;
  executionId: string;
}) {
  const report = useResource<BehaviorReport>(
    `/object-sim/executions/${encodeURIComponent(executionId)}/behavior?project_id=${encodeURIComponent(project)}`,
  );
  return (
    <section
      className="demo-editor beta-behavior"
      aria-label="기록된 행동과 자차 대응"
    >
      <div className="demo-editor-heading">
        <div>
          <span className="demo-eyebrow">RETAINED EXECUTION EVIDENCE</span>
          <h3>행동이 발생했나요? 자차는 어떻게 움직였나요?</h3>
        </div>
      </div>
      {report.loading && (
        <p className="demo-message" role="status">
          원본 실행 기록을 읽고 있습니다…
        </p>
      )}
      {report.error && (
        <div className="demo-message error" role="alert">
          {report.error}
          <button className="button secondary" onClick={report.reload}>
            행동 기록 다시 확인
          </button>
        </div>
      )}
      {report.data?.status !== "available" && report.data && (
        <p className="demo-message">
          {report.data.reason ??
            "이 실행의 행동 기록을 아직 사용할 수 없습니다."}
        </p>
      )}
      {report.data?.status === "available" && (
        <div className="beta-evidence-grid">
          <div>
            <h4>작성 행동의 실행 기록</h4>
            <ul>
              {report.data.actors.map((actor) => (
                <li key={actor.actor_id}>
                  <strong>
                    {actor.actor_id}
                    {actor.role === "ego" ? " · 자차" : ""}
                  </strong>
                  {actor.mode === "route_only" && (
                    <span>경로 주행 · 별도 행동 없음</span>
                  )}
                  {actor.actions.map((action) => (
                    <span key={action.action_id}>
                      {action.kind} · {actionLabel[action.status]}
                      {action.started_at_s != null
                        ? ` · 시작 ${action.started_at_s.toFixed(2)}초`
                        : ""}
                      {action.completed_at_s != null
                        ? ` · 완료 ${action.completed_at_s.toFixed(2)}초`
                        : ""}
                      {action.events
                        ?.filter((event) => event.type === "action_timeout")
                        .map((event, index) => (
                          <span className="beta-action-timeout" key={index}>
                            시간 초과 · {timeoutPolicyLabel[event.reason] ?? event.reason}
                            {` (${event.reason}) · ${event.time_s.toFixed(2)}초`}
                          </span>
                        ))}
                    </span>
                  ))}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h4>자차의 실제 이동</h4>
            <strong className="beta-outcome">
              {outcomeLabel[report.data.ego_outcome.status]}
            </strong>
            <dl>
              <dt>이동 거리</dt>
              <dd>
                {measurement(report.data.ego_outcome.distance_travelled_m, "m")}
              </dd>
              <dt>최종 목적지 거리</dt>
              <dd>{measurement(report.data.ego_outcome.goal_error_m, "m")}</dd>
              <dt>최종 속도</dt>
              <dd>
                {measurement(report.data.ego_outcome.final_speed_mps, "m/s")}
              </dd>
              <dt>Native 접촉 관측</dt>
              <dd>
                {report.data.ego_outcome.native_contact === true
                  ? "있음"
                  : report.data.ego_outcome.native_contact === false
                    ? "없음"
                    : "기록 없음"}
              </dd>
            </dl>
          </div>
        </div>
      )}
      <p className="demo-control-note">
        행동은 planner 이벤트, 자차 이동은 보존된 CARLA 관측에서 확인합니다.
        행동 완료가 목적지 도달이나 안전 통과를 뜻하지는 않습니다.
      </p>
    </section>
  );
}
