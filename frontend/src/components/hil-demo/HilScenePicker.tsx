import { useEffect, useState } from "react";
import { api } from "../../api";
import {
  hilSceneRef,
  hilSceneScheduleLabel,
  parseHilSceneProfile,
  verifyHilSceneHash,
} from "./scene-profile";
import type {
  HilSceneCatalog,
  HilSceneProfile,
  HilSceneRef,
} from "./scene-profile";

export default function HilScenePicker({
  project,
  selected,
  retained,
  disabled,
  onChange,
}: {
  project: string;
  selected?: HilSceneRef;
  retained?: HilSceneProfile;
  disabled: boolean;
  onChange: (value: HilSceneRef | undefined) => void;
}) {
  const [items, setItems] = useState<HilSceneProfile[]>([]),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    void api<HilSceneCatalog>(
      `/hil-sim/scenes?${new URLSearchParams({ project_id: project })}`,
    )
      .then(async (value) => {
        if (
          value.schema_version !== "hil-sim-scenes/1" ||
          value.project_id !== project ||
          value.default_scene_id !== "lead_stop" ||
          value.source_scope !== "existing_preset_bound_base_scene"
        )
          throw new Error("프로젝트 장면 목록의 계약이 다릅니다.");
        const profiles = value.items.map(parseHilSceneProfile);
        await Promise.all(profiles.map(verifyHilSceneHash));
        if (active) setItems(profiles);
      })
      .catch((reason) => {
        if (active)
          setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [project]);
  const matches = (item: HilSceneProfile) =>
    item.id === selected?.id &&
    item.revision === selected.revision &&
    item.sha256 === selected.sha256;
  const scene =
    items.find(matches) ??
    (retained && matches(retained) ? retained : undefined);
  return (
    <div className="hil-scene-picker">
      <label>
        주행 장면
        <select
          aria-label="HIL 주행 장면"
          value={selected?.id ?? "lead_stop"}
          disabled={disabled || loading}
          onChange={(event) => {
            const item = items.find((value) => value.id === event.target.value);
            onChange(item ? hilSceneRef(item) : undefined);
          }}
        >
          <option value="lead_stop">앞차 정지 · 기존 장면</option>
          {items.map((item) => (
            <option key={item.id} value={item.id}>
              {item.title} · r{item.revision}
            </option>
          ))}
          {selected && !items.some(matches) && (
            <option value={selected.id}>
              {scene?.title ?? selected.id} · 보존 r{selected.revision}
            </option>
          )}
        </select>
      </label>
      <small>Town10HD_Opt · 18초 · 기존 고정 장면 위의 앞차 행동</small>
      {scene ? (
        <>
          <p>{scene.description}</p>
          <div
            className="hil-scene-schedule"
            aria-label="작성한 앞차 목표 속도 일정"
          >
            {scene.lead_speed_schedule.map((step) => (
              <span key={step.at_s}>
                <b>{step.at_s} s</b>
                {step.target_speed_mps} m/s
              </span>
            ))}
          </div>
          <small>
            작성 목표 · 입력 시각: {hilSceneScheduleLabel(scene)}. 실제
            속도·정지·재출발은 실행 결과에서 확인합니다.
          </small>
        </>
      ) : (
        <p>
          {selected
            ? "선택한 장면의 고정 개정을 유지합니다. 상세 일정은 저장된 장면 정보를 확인한 뒤 표시합니다."
            : "기존에 고정한 정지 앞차 장면을 유지합니다."}
        </p>
      )}
      {loading && <small>장면 목록 확인 중…</small>}
      {error && (
        <p className="hil-error" role="alert">
          {error} 기존 저장 장면은 유지됩니다.
        </p>
      )}
    </div>
  );
}
