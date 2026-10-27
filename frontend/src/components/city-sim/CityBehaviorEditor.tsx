import { useState } from "react";
import { useResource } from "../../api";
import { newBehaviorAction } from "./city-contract";
import type {
  BehaviorAction,
  BehaviorCatalog,
  BehaviorTrigger,
  CityActor,
  CityMap,
  PlannerSettings,
} from "./city-contract";
const triggerLabels: Record<BehaviorTrigger["kind"], string> = {
  time: "실행 시간",
  distance: "상대 거리",
  relative_speed: "상대 속도",
  ttc: "충돌 예상 시간",
  region: "영역 진입",
  signal: "신호 상태",
  action_complete: "앞 행동 완료",
};
function Numeric({
  label,
  value,
  onChange,
  min = 0,
  max = 120,
  step = 0.1,
  disabled,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled: boolean;
}) {
  return (
    <label>
      {label}
      <input
        aria-label={label}
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onChange={(e) => {
          if (Number.isFinite(e.target.valueAsNumber))
            onChange(e.target.valueAsNumber);
        }}
      />
    </label>
  );
}
export default function CityBehaviorEditor({
  actor,
  actors,
  map,
  disabled,
  onChange,
  onPickLane,
}: {
  actor: CityActor;
  actors: CityActor[];
  map: CityMap | null;
  disabled: boolean;
  onChange: (fn: (a: CityActor) => void) => void;
  onPickLane?: (actionId: string) => void;
}) {
  const catalog = useResource<BehaviorCatalog>("/object-behaviors"),
    [kind, setKind] = useState("route_follow"),
    [nodeSearch, setNodeSearch] = useState("");
  const behavior = actor.behavior ?? {
      schema_version: "object-behavior/1" as const,
      risk_mode: false,
      actions: [],
    },
    planner = actor.planner ?? { schema_version: "object-planner/1" as const };
  const items =
      catalog.data?.behaviors.filter((item) =>
        item.actor_kinds.includes(actor.kind),
      ) ?? [],
    available = items.filter((item) => behavior.risk_mode || !item.risk),
    selected = available.find((item) => item.id === kind) ?? available[0];
  const candidates = (map?.nodes ?? [])
    .filter(
      (n) =>
        !nodeSearch || `${n.id} ${n.road_id} ${n.lane_id}`.includes(nodeSearch),
    )
    .slice(0, 300);
  const update = (index: number, fn: (action: BehaviorAction) => void) =>
    onChange((a) => {
      if (a.behavior) fn(a.behavior.actions[index]);
    });
  const updatePlanner = <K extends keyof PlannerSettings>(
    key: K,
    value: PlannerSettings[K],
  ) =>
    onChange((a) => {
      a.planner = { ...planner, [key]: value };
    });
  return (
    <section
      className="city-behavior-editor"
      aria-label="Reactive actor behavior editor"
    >
      <h4>반응형 행동 · GT 기본 planner</h4>
      <p className="city-note">
        상대 객체의 관측 상태와 고정 지도에 반응합니다. 센서 인지나 V2X 통신이
        아닙니다. 한 번에 하나의 행동이 차량 제어를 소유합니다.
      </p>
      {catalog.error && (
        <p className="expansion-error" role="alert">
          행동 카탈로그를 읽을 수 없습니다: {catalog.error}
        </p>
      )}
      <details>
        <summary>기본 planner 설정 · 양보·우회</summary>
        <div className="city-field-grid">
          <Numeric
            label="차량 외곽 최소 간격 · m"
            value={planner.min_gap_m ?? 3}
            min={0.5}
            max={20}
            disabled={disabled}
            onChange={(n) => updatePlanner("min_gap_m", n)}
          />
          <Numeric
            label="반응형 선행 시간 · s"
            value={planner.headway_s ?? 2}
            min={0.5}
            max={10}
            disabled={disabled}
            onChange={(n) => updatePlanner("headway_s", n)}
          />
          <Numeric
            label="교착 감지 대기 · s"
            value={planner.deadlock_timeout_s ?? 10}
            min={2}
            max={120}
            disabled={disabled}
            onChange={(n) => updatePlanner("deadlock_timeout_s", n)}
          />
          <Numeric
            label="최대 감속 · m/s²"
            value={planner.max_deceleration_mps2 ?? 3}
            min={0.1}
            max={8}
            disabled={disabled}
            onChange={(n) => updatePlanner("max_deceleration_mps2", n)}
          />
        </div>
        {(
          [
            ["allow_lane_changes", "차선 변경 허용"],
            ["allow_reroute", "대체 경로 허용"],
            ["obey_signals", "신호 준수"],
          ] as const
        ).map(([key, label]) => (
          <label key={key} className="city-checkbox">
            <input
              type="checkbox"
              checked={planner[key] ?? true}
              disabled={disabled}
              onChange={(e) => updatePlanner(key, e.target.checked)}
            />
            {label}
          </label>
        ))}
        <label>
          폐쇄 노드 ID · 쉼표 구분
          <input
            aria-label="Closed planner lane nodes"
            value={planner.closed_node_ids?.join(", ") ?? ""}
            disabled={disabled}
            onChange={(e) =>
              updatePlanner(
                "closed_node_ids",
                e.target.value
                  .split(",")
                  .map((v) => v.trim())
                  .filter(Boolean),
              )
            }
          />
        </label>
      </details>
      <label className="city-checkbox city-risk-option">
        <input
          aria-label="Enable adversarial behaviors"
          type="checkbox"
          checked={behavior.risk_mode}
          disabled={
            disabled ||
            behavior.actions.some(
              (a) => items.find((item) => item.id === a.kind)?.risk,
            )
          }
          onChange={(e) =>
            onChange((a) => {
              a.behavior = { ...behavior, risk_mode: e.target.checked };
            })
          }
        />
        위험 상황 생성 명시적 허용
      </label>
      {behavior.risk_mode && (
        <p className="city-note">
          위험 행동의 발생 여부와 ego의 대응 성공은 별도 결과입니다.
        </p>
      )}
      <div className="city-field-grid">
        <label>
          행동 라이브러리
          <select
            aria-label="Behavior library"
            value={selected?.id ?? ""}
            disabled={disabled || !available.length}
            onChange={(e) => setKind(e.target.value)}
          >
            {available.map((item) => (
              <option key={item.id} value={item.id}>
                {item.risk ? "⚠ " : ""}
                {item.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {selected && <p className="city-note">{selected.description}</p>}
      <button
        className="button secondary"
        disabled={disabled || !selected || behavior.actions.length >= 64}
        onClick={() =>
          onChange((a) => {
            a.behavior ??= {
              schema_version: "object-behavior/1",
              risk_mode: false,
              actions: [],
            };
            a.behavior.actions.push(
              newBehaviorAction(a.behavior.actions, selected.id),
            );
          })
        }
      >
        행동 순서에 추가
      </button>
      <div className="city-behavior-sequence">
        {behavior.actions.map((action, index) => {
          const item = items.find((i) => i.id === action.kind),
            trigger = action.trigger,
            others = actors.filter((a) => a.id !== actor.id);
          return (
            <article
              key={action.id}
              className={`object-map-subcard ${item?.risk ? "risk" : ""}`}
            >
              <div className="city-behavior-heading">
                <strong>
                  {index + 1}. {item?.name ?? action.kind}
                </strong>
                <span>{action.id}</span>
                <button
                  disabled={
                    disabled ||
                    behavior.actions.some(
                      (a) => a.trigger.action_id === action.id,
                    )
                  }
                  title={
                    behavior.actions.some(
                      (a) => a.trigger.action_id === action.id,
                    )
                      ? "이 행동을 참조한 완료 조건을 먼저 변경하세요."
                      : "행동 삭제"
                  }
                  onClick={() =>
                    onChange((a) => {
                      a.behavior!.actions.splice(index, 1);
                    })
                  }
                >
                  삭제
                </button>
              </div>
              <label>
                시작 조건
                <select
                  aria-label={`${action.id} trigger`}
                  value={trigger.kind}
                  disabled={disabled}
                  onChange={(e) =>
                    update(index, (a) => {
                      const next = e.target.value as BehaviorTrigger["kind"];
                      a.trigger = {
                        kind: next,
                        value: 0,
                        operator:
                          next === "ttc" || next === "distance" ? "lte" : "gte",
                        ...(next === "region"
                          ? {
                              region: {
                                x: actor.pose.x,
                                y: actor.pose.y,
                                radius_m: 3,
                              },
                            }
                          : {}),
                        ...(next === "signal"
                          ? {
                              signal_id: map?.signals?.[0]?.id ?? "",
                              signal_state: "green" as const,
                            }
                          : {}),
                        ...(["distance", "relative_speed", "ttc"].includes(next)
                          ? { target_actor_id: others[0]?.id ?? "" }
                          : {}),
                        ...(next === "action_complete"
                          ? { action_id: behavior.actions[index - 1]?.id ?? "" }
                          : {}),
                      };
                    })
                  }
                >
                  {Object.entries(triggerLabels)
                    .filter(([key]) => key !== "action_complete" || index > 0)
                    .map(([key, label]) => (
                      <option key={key} value={key}>
                        {label}
                      </option>
                    ))}
                </select>
              </label>
              {["time", "distance", "relative_speed", "ttc"].includes(
                trigger.kind,
              ) && (
                <div className="city-field-grid">
                  <Numeric
                    label={`${action.id} 조건 값 · ${trigger.kind === "distance" ? "m" : trigger.kind === "relative_speed" ? "m/s" : "s"}`}
                    value={trigger.value}
                    max={100000}
                    disabled={disabled}
                    onChange={(n) =>
                      update(index, (a) => {
                        a.trigger.value = n;
                      })
                    }
                  />
                  <label>
                    조건
                    <select
                      disabled={disabled}
                      value={trigger.operator}
                      onChange={(e) =>
                        update(index, (a) => {
                          a.trigger.operator = e.target.value as "lte" | "gte";
                        })
                      }
                    >
                      <option value="gte">이상 ≥</option>
                      <option value="lte">이하 ≤</option>
                    </select>
                  </label>
                </div>
              )}
              {["distance", "relative_speed", "ttc"].includes(trigger.kind) && (
                <label>
                  조건 대상 객체
                  <select
                    aria-label={`${action.id} trigger actor`}
                    value={trigger.target_actor_id ?? ""}
                    disabled={disabled}
                    onChange={(e) =>
                      update(index, (a) => {
                        a.trigger.target_actor_id = e.target.value;
                      })
                    }
                  >
                    <option value="">대상 선택</option>
                    {others.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.id}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {trigger.kind === "region" && trigger.region && (
                <div className="city-field-grid">
                  {(["x", "y", "radius_m"] as const).map((key) => (
                    <Numeric
                      key={key}
                      label={`${action.id} 영역 ${key}`}
                      value={trigger.region![key]}
                      min={key === "radius_m" ? 0.1 : -100000}
                      max={key === "radius_m" ? 1000 : 100000}
                      disabled={disabled}
                      onChange={(n) =>
                        update(index, (a) => {
                          a.trigger.region![key] = n;
                        })
                      }
                    />
                  ))}
                </div>
              )}
              {trigger.kind === "signal" && (
                <div className="city-field-grid">
                  <label>
                    신호
                    <select
                      value={trigger.signal_id ?? ""}
                      disabled={disabled}
                      onChange={(e) =>
                        update(index, (a) => {
                          a.trigger.signal_id = e.target.value;
                        })
                      }
                    >
                      <option value="">신호 선택</option>
                      {map?.signals?.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.id}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    상태
                    <select
                      value={trigger.signal_state ?? "green"}
                      disabled={disabled}
                      onChange={(e) =>
                        update(index, (a) => {
                          a.trigger.signal_state = e.target.value as
                            "red" | "yellow" | "green";
                        })
                      }
                    >
                      <option value="green">녹색</option>
                      <option value="yellow">황색</option>
                      <option value="red">적색</option>
                    </select>
                  </label>
                </div>
              )}
              {trigger.kind === "action_complete" && (
                <label>
                  완료할 앞 행동
                  <select
                    aria-label={`${action.id} preceding action`}
                    value={trigger.action_id ?? ""}
                    disabled={disabled}
                    onChange={(e) =>
                      update(index, (a) => {
                        a.trigger.action_id = e.target.value;
                      })
                    }
                  >
                    {behavior.actions.slice(0, index).map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.id} ·{" "}
                        {items.find((i) => i.id === a.kind)?.name ?? a.kind}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <div className="city-field-grid">
                <label>
                  목표 속도 · m/s (선택)
                  <input
                    type="number"
                    min={0}
                    max={12}
                    step={0.1}
                    value={action.speed_mps ?? ""}
                    disabled={disabled}
                    onChange={(e) =>
                      update(index, (a) => {
                        a.speed_mps =
                          e.target.value === "" ? null : Number(e.target.value);
                      })
                    }
                  />
                </label>
                <Numeric
                  label={`${action.id} 유지 시간 · s`}
                  value={action.duration_s}
                  max={120}
                  disabled={disabled}
                  onChange={(n) =>
                    update(index, (a) => {
                      a.duration_s = n;
                    })
                  }
                />
                <Numeric
                  label={`${action.id} 시간 초과 · s`}
                  value={action.timeout_s}
                  min={0.1}
                  max={120}
                  disabled={disabled}
                  onChange={(n) =>
                    update(index, (a) => {
                      a.timeout_s = n;
                    })
                  }
                />
                <label>
                  시간 초과 정책
                  <select
                    value={action.on_timeout}
                    disabled={disabled}
                    onChange={(e) =>
                      update(index, (a) => {
                        a.on_timeout = e.target
                          .value as BehaviorAction["on_timeout"];
                      })
                    }
                  >
                    <option value="stop">정지 · 원인 표시</option>
                    <option value="skip">건너뛰기 · 기록</option>
                    <option value="fail">실패 처리</option>
                  </select>
                </label>
              </div>
              {[
                "follow",
                "yield",
                "overtake",
                "obstacle_bypass",
                "cut_in",
                "merge",
              ].includes(action.kind) && (
                <label>
                  행동 대상 객체 (선택)
                  <select
                    value={action.target_actor_id ?? ""}
                    disabled={disabled}
                    onChange={(e) =>
                      update(index, (a) => {
                        a.target_actor_id = e.target.value || null;
                      })
                    }
                  >
                    <option value="">주변 관측에서 선택</option>
                    {others.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.id}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {item?.requires_target_lane && (
                <label>
                  목표 차로 노드 · 지도에 등록된 ID
                  <input
                    aria-label={`${action.id} target lane node`}
                    list={`lane-nodes-${actor.id}`}
                    placeholder="노드 ID 검색"
                    value={action.target_lane_node_id ?? ""}
                    disabled={disabled}
                    onChange={(e) => {
                      setNodeSearch(e.target.value);
                      update(index, (a) => {
                        a.target_lane_node_id = e.target.value || null;
                      });
                    }}
                  />
                  {onPickLane && (
                    <button
                      className="button secondary"
                      disabled={disabled}
                      onClick={() => onPickLane(action.id)}
                    >
                      목표 차로 지도에서 선택
                    </button>
                  )}
                  <small>
                    지도 카탈로그의 실제 노드를 선택합니다. 연결·허용
                    차로·곡률은 실행 준비에서 검사합니다.
                  </small>
                </label>
              )}
            </article>
          );
        })}
      </div>
      <datalist id={`lane-nodes-${actor.id}`}>
        {candidates.map((n) => (
          <option key={n.id} value={n.id}>
            도로 {n.road_id} / 차로 {n.lane_id} · {n.x.toFixed(1)},{" "}
            {n.y.toFixed(1)}
          </option>
        ))}
      </datalist>
      {!behavior.actions.length && (
        <p className="city-note">
          명시적 행동이 없으면 기본 planner가 경로·간격·신호에 반응합니다.
        </p>
      )}
      <p className="city-note">
        {catalog.data?.planner_version} · {catalog.data?.fidelity}
      </p>
    </section>
  );
}
