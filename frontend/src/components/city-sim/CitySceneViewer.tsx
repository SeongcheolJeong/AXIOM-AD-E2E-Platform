import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ObjectExecution } from "../object-scene/object-scene";
import { executionVerificationLink } from "../../experiment-verification-context";
import { CarlaCameraFrame } from "../../pages/CarlaCameraFrame";
import { loadCityScene } from "./load-city-scene";
import CityMapCanvas from "./CityMapCanvas";
import CityScene3D from "./CityScene3D";
import CityCaseCapture from "./CityCaseCapture";
import { presentationCameraKey } from "./city-camera";
import type { CityCameraMode } from "./city-camera";
import { isNativeTownMap, resolveCityPresentationLayout } from "./city-presentation-layout";
import type { CityPresentationLayout } from "./city-presentation-layout";
import { playbackBoundary, retainedFrameIndex } from "./city-playback";
import {
  currentPlan,
  decisionLabel,
  decisionRelations,
  reactiveDecision,
} from "./city-decisions";
import "./city-sim.css";
export type CityPlaybackSnapshot = {
  executionId: string;
  requestedTimeS: number;
  firstTimeS: number;
  lastTimeS: number;
  ready: boolean;
  error?: string;
};
export default function CitySceneViewer({
  execution,
  hilSide,
  playbackTimeS,
  onPlaybackSeek,
  onPlaybackSnapshot,
  presentation = false,
  presentationLayout = "auto",
  presentationSensor,
  onPresentationSensorChange,
  selectedActorId,
  decisionFocusTimeS,
  cameraMode,
  onCameraModeChange,
  onManualInteraction,
  playbackActive = false,
  initialView = "3d",
  initialMapFit = "map",
}: {
  execution: ObjectExecution;
  hilSide?: "baseline" | "faulted";
  playbackTimeS?: number;
  onPlaybackSeek?: (timeS: number) => void;
  onPlaybackSnapshot?: (snapshot: CityPlaybackSnapshot) => void;
  presentation?: boolean;
  presentationLayout?: CityPresentationLayout;
  presentationSensor?: "front" | "chase";
  onPresentationSensorChange?: (sensor: "front" | "chase") => void;
  selectedActorId?: string;
  decisionFocusTimeS?: number;
  cameraMode?: CityCameraMode;
  onCameraModeChange?: (mode: CityCameraMode) => void;
  onManualInteraction?: () => void;
  playbackActive?: boolean;
  initialView?: "2d" | "3d";
  initialMapFit?: "map" | "actor";
}) {
  const [loaded, setLoaded] = useState<Awaited<
      ReturnType<typeof loadCityScene>
    > | null>(null),
    [error, setError] = useState(""),
    [localIndex, setIndex] = useState(0),
    [playing, setPlaying] = useState(false),
    [speed, setSpeed] = useState(1),
    [selected, setSelected] = useState("ego"),
    [sensor, setSensor] = useState(""),
    [follow, setFollow] = useState(presentation),
    [view, setView] = useState<"2d" | "3d">(initialView),
    [fullscreen, setFullscreen] = useState(false),
    [expanded, setExpanded] = useState(false),
    [viewportNotice, setViewportNotice] = useState(""),
    [hidden, setHidden] = useState(new Set<string>()),
    [imageReady, setImageReady] = useState(""),
    [decisionFilter, setDecisionFilter] = useState<"selected" | "all">(
      "selected",
    );
  const cache = useRef(new Map<string, Uint8ClampedArray>()),
    workspace = useRef<HTMLElement>(null),
    signature = execution.artifacts
      .map((item) => `${item.id}:${item.sha256}`)
      .join(";");
  useEffect(() => {
    const changed = () =>
      setFullscreen(document.fullscreenElement === workspace.current);
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    document.addEventListener("fullscreenchange", changed);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("fullscreenchange", changed);
      document.removeEventListener("keydown", escape);
    };
  }, []);
  useEffect(() => {
    setLoaded(null);
    setError("");
    setIndex(0);
    setPlaying(false);
    setHidden(new Set());
    cache.current.clear();
    if (!execution.manifest_hash) return;
    const controller = new AbortController();
    let active = true;
    loadCityScene(execution, controller.signal, hilSide)
      .then((value) => {
        if (!active) return;
        setLoaded(value);
        setSelected(value.scene.actors.find((item) => item.role === "ego")!.id);
        setSensor(value.cameraRecords[0]?.key ?? "");
      })
      .catch((reason: unknown) => {
        if (active)
          setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [execution.id, execution.manifest_hash, signature, hilSide]);
  const controlled = playbackTimeS !== undefined,
    index =
      controlled && loaded
        ? retainedFrameIndex(loaded.scene.frames, playbackTimeS)
        : localIndex,
    frame = loaded?.scene.frames[index];
  useEffect(() => {
    if (selectedActorId && loaded?.scene.actors.some((actor) => actor.id === selectedActorId)) setSelected(selectedActorId);
  }, [selectedActorId, loaded]);
  const sensorKeys = useMemo(() => [...new Set(loaded?.cameraRecords.map((record) => record.key) ?? [])], [loaded]);
  const selectedSensor = presentationSensor ? presentationCameraKey(sensorKeys, presentationSensor) ?? "" : sensor;
  const camera = useMemo(
    () =>
      loaded?.cameraRecords
        .filter((item) => item.index <= index && item.key === selectedSensor)
        .at(-1),
    [loaded, index, selectedSensor],
  );
  const ready = useCallback(
    (key: string, value: boolean) => setImageReady(value ? key : ""),
    [],
  );
  useEffect(() => {
    if (!onPlaybackSnapshot || !controlled) return;
    onPlaybackSnapshot({
      executionId: execution.id,
      requestedTimeS: playbackTimeS,
      firstTimeS: loaded?.scene.frames[0]?.time_s ?? 0,
      lastTimeS: loaded?.scene.frames.at(-1)?.time_s ?? 0,
      ready: !!loaded && (!camera || imageReady === camera.cacheKey),
      ...(error ? { error } : {}),
    });
  }, [
    onPlaybackSnapshot,
    controlled,
    playbackTimeS,
    execution.id,
    loaded,
    camera,
    imageReady,
    error,
  ]);
  useEffect(() => {
    if (controlled || !playing || !loaded) return;
    if (index === loaded.scene.frames.length - 1) {
      setPlaying(false);
      return;
    }
    if (camera && imageReady !== camera.cacheKey) return;
    const interval =
        ((loaded.scene.frames[index + 1].time_s -
          loaded.scene.frames[index].time_s) *
          1000) /
        speed,
      timer = setTimeout(() => setIndex((value) => value + 1), interval);
    return () => clearTimeout(timer);
  }, [controlled, playing, loaded, index, speed, camera, imageReady]);
  const actual = useMemo(() => {
    const paths: Record<string, { x: number; y: number }[]> = {};
    if (loaded)
      for (const actor of loaded.scene.actors)
        if (!hidden.has(actor.id))
          paths[actor.id] = loaded.scene.frames
            .slice(0, index + 1)
            .map((item) => item.actors.find((state) => state.id === actor.id)!);
    return paths;
  }, [loaded, index, hidden]);
  if (error)
    return (
      <section className="city-panel">
        <h2>도시 실행 근거를 열 수 없습니다</h2>
        <p role="alert" className="expansion-error">
          {error}
        </p>
        <p>
          봉인된 개정·원본 지도·프레임 해시가 확인된 결과만 재생합니다. 원본
          산출물에서 실패 원인을 확인하세요.
        </p>
      </section>
    );
  if (!loaded || !frame)
    return (
      <section className="city-panel">
        <p role="status">
          {loaded && controlled && index < 0
            ? `관측 시작 전 · 첫 원본 ${loaded.scene.frames[0].time_s.toFixed(2)}s`
            : execution.manifest_hash
              ? "도시 시나리오·지도·네이티브 프레임의 SHA-256을 확인하는 중…"
              : "네이티브 도시 실행의 봉인된 결과를 기다리는 중…"}
        </p>
      </section>
    );
  const { scene } = loaded,
    resolvedLayout = resolveCityPresentationLayout(scene.map, presentationLayout),
    nativeCameraHero = resolvedLayout === "native_camera",
    nativeTown = isNativeTownMap(scene.map),
    actor = scene.actors.find((item) => item.id === selected)!,
    state = frame.actors.find((item) => item.id === selected)!,
    actors = scene.actors
      .filter((item) => !hidden.has(item.id))
      .map((item) => ({
        ...item,
        pose: frame.actors.find((current) => current.id === item.id)!,
        route: scene.routes[item.id],
        dimensions: item.dimensions_m,
      }));
  const planned = Object.fromEntries(
    Object.entries(scene.planned_paths)
      .filter(([id]) => !hidden.has(id))
      .map(([id, entries]) => [
        id,
        currentPlan(
          entries,
          index,
          scene.actors.find((a) => a.id === id)?.controller.kind === "reactive",
        )?.points ?? [],
      ]),
  );
  const decision = reactiveDecision(state),
    relations = decisionRelations(state, frame.actors),
    plannerEvents = (scene.planner_events ?? []).filter(
      (event) => decisionFilter === "all" || event.actor_id === selected,
    );
  const metrics = scene.metrics.find((item) => item.actor_id === selected),
    format = (value: unknown) =>
      typeof value === "number" ? value.toFixed(3) : "보존 값 없음";
  const holdReasons = Array.isArray(state.plan?.hold_reasons)
    ? state.plan.hold_reasons.map((entry: unknown) => {
        if (!entry || typeof entry !== "object") return "알 수 없는 보존 이유";
        const reason = entry as Record<string, unknown>;
        switch (reason.reason) {
          case "native_traffic_signal":
            return `신호 ${String(reason.signal_id ?? "미확인")} · ${String(reason.observed_state ?? "미보존")}`;
          case "lead_headway":
            return `선행 객체 간격 ${format(reason.origin_gap_m)}m · 제한 속도 ${format(reason.limited_speed_mps)}m/s`;
          case "authored_wait":
            return "작성한 출발 대기 시간";
          case "authored_stop_time":
            return "작성한 정지 시점";
          case "lane_change_gap":
            return "차로 변경 시작 간격 대기";
          default:
            return "기타 보존 이유";
        }
      })
    : null;
  function selectActor(id: string) { onManualInteraction?.(); setSelected(id); }
  function seek(value: number) {
    onManualInteraction?.();
    setPlaying(false);
    if (controlled) {
      onPlaybackSeek?.(
        scene.frames[Math.min(scene.frames.length - 1, Math.max(0, value))]
          .time_s,
      );
      return;
    }
    setIndex(Math.min(scene.frames.length - 1, Math.max(0, value)));
  }
  return (
    <section
      ref={workspace}
      className={`city-workspace city-presentation-${resolvedLayout.replace("_", "-")} ${presentation ? "city-comparison-view" : ""} ${expanded ? "city-expanded" : ""}`}
      data-presentation-layout={resolvedLayout}
      aria-label="CARLA city native scene viewer"
      onPointerDown={onManualInteraction}
    >
      <div className="city-intro">
        <div>
          <span className="city-eyebrow">NATIVE CARLA · RETAINED EVIDENCE</span>
          {execution.status === "completed" && (
            <a
              className="button secondary"
              href={executionVerificationLink(execution)}
            >
              검증 계획에 연결
            </a>
          )}
          <h2>
            {scene.map.name} · {scene.actors.length} actors ·{" "}
            {scene.frames.length} native frames
          </h2>
          <p>
            원하는 경로 · planner 출력 · 실제 차량 궤적과 RGB를 같은 보존
            시간축에서 조사합니다.{" "}
            {scene.fidelity === "native_carla_object_physics"
              ? "CARLA GT 피드백 / 객체 물리 실행"
              : scene.fidelity === "native_carla_mixed_physics_and_replay"
                ? "물리 제어와 시간 지정 위치 재생이 섞인 진단 실행"
                : "시간 지정 위치 재생 / 렌더 영상 진단 · 물리 경로 추종 판정 제외"}
            이며 물리 센서 또는 실차 안전성 검증이 아닙니다.
          </p>
        </div>
        <div className="city-evidence-statuses">
          <span
            className={`city-verdict ${loaded.evaluation.verdict}`}
            aria-label="City fixed criteria verdict"
          >
            {loaded.evaluation.verdict === "fail"
              ? "실행 완료 · 저장 기준 미달"
              : loaded.evaluation.verdict === "pass"
                ? "실행 완료 · 저장 기준 통과"
                : "실행 완료 · 기준 미평가"}
          </span>
          <span className="city-engine-status available">원본 해시 확인됨</span>
          {nativeTown && <span className="city-engine-status city-native-town-badge">CARLA 도시 지도 · {scene.map.native_name?.split("/").at(-1)}</span>}
        </div>
      </div>
      {controlled &&
        playbackBoundary(scene.frames, playbackTimeS) === "ended" && (
          <div className="city-playback-ended" role="status">
            관측 종료 · {scene.frames.at(-1)!.time_s.toFixed(2)}s의 마지막
            원본을 표시합니다.
          </div>
        )}
      <div
        className="city-mode-switch"
        role="group"
        aria-label="City native geometry view mode"
      >
        <button
          type="button"
          className={`button ${view === "3d" ? "" : "secondary"}`}
          aria-pressed={view === "3d"}
          onClick={() => { onManualInteraction?.(); setView("3d"); }}
        >
          3D 도시 · 객체
        </button>
        <button
          type="button"
          className={`button ${view === "2d" ? "" : "secondary"}`}
          aria-pressed={view === "2d"}
          onClick={() => { onManualInteraction?.(); setView("2d"); }}
        >
          2D 전체 지도
        </button>
        <span className="city-note">
          지도·3D 도식은 저장된 좌표를 사용합니다. RGB 카메라는 실제 CARLA
          렌더 영상입니다.
        </span>
        <button
          type="button"
          className="button secondary"
          aria-label="City scene fullscreen"
          onClick={() => {
            if (expanded) {
              setExpanded(false);
              setViewportNotice("");
              return;
            }
            const operation =
              document.fullscreenElement === workspace.current
                ? document.exitFullscreen()
                : workspace.current?.requestFullscreen();
            operation?.catch(() => {
              setExpanded(true);
              setViewportNotice(
                "브라우저 전체 화면 대신 넓은 집중 보기로 전환했습니다. Escape 또는 닫기로 돌아갑니다.",
              );
            });
          }}
        >
          {fullscreen || expanded ? "전체 화면 닫기" : "장면 전체 화면"}
        </button>
      </div>
      {viewportNotice && (
        <p role="status" className="city-note">
          {viewportNotice}
        </p>
      )}
      <div className="city-viewer-layout">
        <div className="city-scene-panel">
        {nativeCameraHero && <div className="city-visual-kind city-visual-kind-analysis"><strong>{view === "3d" ? "3D 분석 도식" : "2D 분석 지도"}</strong><span>보존 좌표 · 도시 배경 메시 제외</span></div>}
        {view === "3d" ? (
          <CityScene3D
            scene={scene}
            initialFit={presentation ? "actor" : "trace"}
            index={index}
            selected={selected}
            hidden={hidden}
            follow={follow}
            onSelect={selectActor}
            cameraMode={cameraMode}
            onCameraModeChange={onCameraModeChange}
            onManualInteraction={onManualInteraction}
            decisionFocusIndex={decisionFocusTimeS === undefined ? undefined : retainedFrameIndex(scene.frames, decisionFocusTimeS)}
            presentationActive={presentation && (controlled ? playbackActive : playing)}
          />
        ) : (
          <CityMapCanvas
            initialFit={initialMapFit}
            map={scene.map}
            actors={actors}
            selected={selected}
            onSelect={selectActor}
            actual={actual}
            planned={planned}
            relationships={relations}
            follow={follow}
          />
        )}
        </div>
        <aside className="city-panel city-native-camera-panel">
          {nativeCameraHero && <div className="city-visual-kind city-visual-kind-native"><strong>CARLA 원본 RGB</strong><span>{nativeTown ? `${scene.map.native_name?.split("/").at(-1)} · 네이티브 도시 렌더` : "네이티브 렌더 · 보존 프레임"}</span></div>}
          <label>
            보존 RGB 센서
            <select
              aria-label="City viewer camera"
              value={selectedSensor}
              onChange={(event) => {
                onManualInteraction?.();
                if (presentationSensor && onPresentationSensorChange) onPresentationSensorChange(presentationCameraKey([event.target.value], "chase") ? "chase" : "front");
                else setSensor(event.target.value);
              }}
              disabled={!loaded.cameraRecords.length || (!!presentationSensor && !onPresentationSensorChange)}
            >
              {!selectedSensor && <option value="">{presentationSensor === "chase" ? "추적 카메라 미보존" : "전방 카메라 미보존"}</option>}
              {sensorKeys.map(
                (key) => (
                  <option key={key}>{key}</option>
                ),
              )}
            </select>
          </label>
          {camera ? (
            <CarlaCameraFrame
              executionId={execution.id}
              record={camera}
              cache={cache.current}
              onReady={ready}
            />
          ) : (
            <div className="city-viewer-camera">
              {presentationSensor && !selectedSensor ? `${presentationSensor === "front" ? "전방" : "추적"} 시점은 이 실행에 보존되지 않았습니다.` : loaded.cameraRecords.length
                ? "현재 cursor 이전에 보존된 카메라 프레임이 없습니다."
                : "이 실행에는 RGB 카메라 기록이 없습니다."}
            </div>
          )}
          {camera && camera.index !== index && (
            <p className="city-note">
              카메라 보존 주기 사이:{" "}
              {scene.frames[camera.index].time_s.toFixed(2)}s의 마지막 원본을
              유지합니다 · 현재 cursor와{" "}
              {(frame.time_s - scene.frames[camera.index].time_s).toFixed(2)}s
              차이.
            </p>
          )}
          <p className="city-note">
            영상은 실제 renderer BGRA8 출력입니다. 표본 간 영상을 만들어 채우지
            않습니다. 카메라 timestamp는 보존 초 값을 ns로 환산해 표시합니다.
          </p>
          <label>
            조사할 객체
            <select
              aria-label="City viewer actor"
              value={selected}
              onChange={(event) => selectActor(event.target.value)}
            >
              {scene.actors.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.id} · {item.native_blueprint}
                </option>
              ))}
            </select>
          </label>
          <label className="city-checkbox">
            <input
              type="checkbox"
              checked={cameraMode ? cameraMode === "follow" : follow}
              onChange={(event) => { onManualInteraction?.(); setFollow(event.target.checked); onCameraModeChange?.(event.target.checked ? "follow" : "all"); }}
            />
            선택 객체 따라가기
          </label>
          {decision && (
            <div
              className="city-decision-reason"
              aria-label="Current reactive decision"
            >
              <strong>{decisionLabel(decision.reason)}</strong>
              <p>
                {decisionLabel(decision.state)} · 경로 r
                {decision.route_revision} · {decision.action_id ?? "기본 정책"}
              </p>
              <span>
                {decision.stop
                  ? "정지 요청"
                  : `요청 속도 ${format(decision.speed_mps)} m/s`}{" "}
                · 관측 GT 기반
              </span>
              {relations.length > 0 && <ul className="city-decision-targets">{relations.slice(0, 3).map((relation) => <li key={relation.to.id}><b>{relation.to.id}</b><span>{relation.gapM === undefined ? relation.label : `관측 간격 ${relation.gapM.toFixed(1)} m`}{relation.requiredGapM === undefined ? "" : ` / 요구 ${relation.requiredGapM.toFixed(1)} m`}</span></li>)}</ul>}
            </div>
          )}
          {presentation && (
            <div className="city-compare-telemetry">
              <span>
                관측 시각 <strong>{frame.time_s.toFixed(2)}s</strong>
              </span>
              <span>
                현재 속도 <strong>{state.speed_mps.toFixed(1)} m/s</strong>
              </span>
              <span>
                객체 <strong>{selected}</strong>
              </span>
            </div>
          )}
          <dl className="city-viewer-values">
            <dt>제어 방식</dt>
            <dd>
              {actor.controller.kind === "autoware"
                ? "Autoware · GT planning/control"
                : actor.controller.kind}
            </dd>
            <dt>Controller 상태</dt>
            <dd>{state.controller_state ?? "미보존"}</dd>
            <dt>정지·감속 이유</dt>
            <dd>
              {holdReasons === null
                ? "미보존"
                : holdReasons.length
                  ? holdReasons.join(" · ")
                  : state.plan?.goal_reached === true
                    ? "목표점 정지 조건 도달"
                    : "외부 정지·감속 조건 없음"}
            </dd>
            <dt>보존 planner 궤적</dt>
            <dd>
              {planned[selected]?.length
                ? `${planned[selected].length}점 · ${actor.controller.kind === "reactive" ? `현재 적용 경로 r${decision?.route_revision ?? "?"}` : `입력 frame ${frame.input_frame}의 계획`}`
                : "미보존 · 목표 경로와 구분"}
            </dd>
            <dt>현재 속도 · m/s</dt>
            <dd>{format(state.speed_mps)}</dd>
            <dt>목표 속도 · m/s</dt>
            <dd>{format(state.desired_speed_mps)}</dd>
            <dt>경로 오차 · m</dt>
            <dd>{format(state.route_error_m)}</dd>
            <dt>경로 진행 · m</dt>
            <dd>{format(state.route_progress_m)}</dd>
            <dt>Throttle / Brake / Steer</dt>
            <dd>
              {[
                state.control?.throttle,
                state.control?.brake,
                state.control?.steer,
              ]
                .map(format)
                .join(" / ")}
            </dd>
            <dt>Native actor ID</dt>
            <dd>{actor.native_actor_id}</dd>
            <dt>Motion fidelity</dt>
            <dd>{actor.motion_fidelity}</dd>
          </dl>
        </aside>
      </div>
      {!controlled && (
        <div className="city-panel city-playback">
          <button
            className="button"
            onClick={() => {
              if (index === scene.frames.length - 1) setIndex(0);
              setPlaying((value) => !value);
            }}
          >
            {playing ? "일시정지" : "재생"}
          </button>
          <button
            className="button secondary"
            aria-label="Previous city native frame"
            disabled={index === 0}
            onClick={() => seek(index - 1)}
          >
            ◀
          </button>
          <input
            type="range"
            aria-label="City native timeline"
            min={0}
            max={scene.frames.length - 1}
            step={1}
            value={index}
            onChange={(event) => seek(Number(event.target.value))}
          />
          <button
            className="button secondary"
            aria-label="Next city native frame"
            disabled={index === scene.frames.length - 1}
            onClick={() => seek(index + 1)}
          >
            ▶
          </button>
          <span>
            {frame.time_s.toFixed(2)}s /{" "}
            {scene.frames.at(-1)!.time_s.toFixed(2)}s · frame {frame.frame}
          </span>
          <select
            aria-label="City playback speed"
            value={speed}
            onChange={(event) => setSpeed(Number(event.target.value))}
          >
            {[0.25, 0.5, 1, 2, 4].map((value) => (
              <option key={value} value={value}>
                {value}×
              </option>
            ))}
          </select>
        </div>
      )}
      {scene.planner_events && (
        <section
          className="city-panel"
          aria-label="Reactive planner decision timeline"
        >
          <div className="city-toolbar">
            <div>
              <h3>행동·재계획 타임라인</h3>
              <p>
                사건을 선택하면 당시 지도·차량 상태·원본 RGB로 이동합니다. 경로
                개정은 다음 재계획까지 유지됩니다.
              </p>
            </div>
            <label className="city-event-filter">
              표시 객체
              <select
                aria-label="Planner event actor filter"
                value={decisionFilter}
                onChange={(e) =>
                  setDecisionFilter(e.target.value as "selected" | "all")
                }
              >
                <option value="selected">선택 객체 · {selected}</option>
                <option value="all">모든 객체</option>
              </select>
            </label>
          </div>
          <div className="city-event-list city-decision-events">
            {plannerEvents.length ? (
              plannerEvents.map((event, i) => (
                <button
                  key={i}
                  className={event.index === index ? "active" : ""}
                  onClick={() => {
                    seek(event.index);
                    selectActor(event.actor_id);
                  }}
                >
                  <strong>
                    {event.time_s.toFixed(2)}s · {event.actor_id} ·{" "}
                    {decisionLabel(event.type)}
                  </strong>
                  <span>{decisionLabel(event.reason)}</span>
                  <small>
                    {event.action_id ?? "기본 정책"} · 경로 r
                    {event.route_revision} · 관측 frame {event.input_frame}
                  </small>
                </button>
              ))
            ) : (
              <p>보존된 행동 전환 없음</p>
            )}
          </div>
          {decision && (
            <details>
              <summary>현재 판단의 관측 값 · 간격·우선권·대기</summary>
              <pre className="city-decision-diagnostics">
                {JSON.stringify(decision.diagnostics, null, 2)}
              </pre>
            </details>
          )}
          <p className="city-note">
            {scene.planner_scope} · 현재 상태 기반 추론이며 V2X 교환 또는 센서
            인지 결과가 아닙니다.
          </p>
        </section>
      )}
      <div className="city-execution-grid">
        <section className="city-panel">
          <h3>객체 표시 · 실행 지표</h3>
          <div className="city-button-row">
            {scene.actors.map((item) => (
              <label key={item.id} className="city-checkbox">
                <input
                  aria-label={`Show city actor ${item.id}`}
                  type="checkbox"
                  checked={!hidden.has(item.id)}
                  onChange={(event) =>
                    setHidden((current) => {
                      const next = new Set(current);
                      if (event.target.checked) next.delete(item.id);
                      else next.add(item.id);
                      return next;
                    })
                  }
                />
                {item.id}
              </label>
            ))}
          </div>
          <dl className="city-viewer-values">
            <dt>실제 이동 거리 · m</dt>
            <dd>{format(metrics?.distance_travelled_m)}</dd>
            <dt>경로 오차 P95 · m</dt>
            <dd>{format(metrics?.lateral_error_p95_m)}</dd>
            <dt>최종 목표 거리 · m</dt>
            <dd>{format(metrics?.goal_error_m)}</dd>
            <dt>마지막 속도 · m/s</dt>
            <dd>{format(metrics?.final_speed_mps)}</dd>
            <dt>Native 접촉</dt>
            <dd>
              {typeof metrics?.native_contact === "boolean"
                ? metrics.native_contact
                  ? "관측됨"
                  : "관측 없음"
                : "미평가"}
            </dd>
          </dl>
          <p className="city-note">
            실행 완료와 기준 통과는 별개입니다. Native 접촉은 엔진 collision
            sensor 관측이며 단순 bbox 겹침이나 차로 이탈과 합산하지 않습니다.
          </p>
        </section>
        <section className="city-panel">
          <h3>사건으로 이동 · 전체 객체</h3>
          <div className="city-event-list">
            {scene.events.length ? (
              scene.events.map((event, i) => (
                <button
                  key={i}
                  onClick={() => {
                    const next = scene.frames.findIndex(
                      (item) => item.time_s >= event.time_s,
                    );
                    seek(next < 0 ? scene.frames.length - 1 : next);
                    if (event.actor_ids[0]) setSelected(event.actor_ids[0]);
                  }}
                >
                  {event.time_s.toFixed(2)}s · {event.kind} ·{" "}
                  {event.actor_ids.join(" ↔ ")}
                  <br />
                  <small>{event.provenance}</small>
                </button>
              ))
            ) : (
              <p>보존된 사건 없음</p>
            )}
          </div>
        </section>
      </div>
      {!hilSide && !presentation && <CityCaseCapture key={execution.id} execution={execution} frame={frame} onOpenFrame={seek}/>}
      <section className="city-panel city-fixed-evaluation">
        <h3>고정 기준 평가 · {loaded.evaluation.verdict}</h3>
        <p>
          {loaded.evaluation.classification} ·{" "}
          {scene.fidelity === "native_carla_object_physics"
            ? "native simulator GT route control 범위"
            : scene.fidelity === "native_carla_mixed_physics_and_replay"
              ? "물리 제어 + 시간 지정 위치 재생 혼합 진단 범위 · 재생 객체 물리 판정 제외"
              : "시간 지정 위치 재생 · 렌더 영상 진단 범위 · 물리 경로 추종 판정 제외"}
        </p>
        <table className="expansion-table">
          <thead>
            <tr>
              <th>지표</th>
              <th>관측값</th>
              <th>저장 기준</th>
              <th>판정</th>
            </tr>
          </thead>
          <tbody>
            {loaded.evaluation.checks.map((check) => (
              <tr key={check.metric}>
                <td>{check.metric}</td>
                <td>{format(check.value)}</td>
                <td>
                  {check.operator} {check.threshold}
                </td>
                <td>{check.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <details className="city-panel">
        <summary>실행 출처 · 제한</summary>
        <p>
          시나리오 {String(scene.source.scenario_id)} · r
          {String(scene.source.scenario_revision)} · seed{" "}
          {String(scene.source.seed)} · {execution.adapter_id}{" "}
          {execution.adapter_version}
        </p>
        <p>
          Configuration <code>{String(scene.source.configuration_hash)}</code>
          <br />
          Map <code>{String(scene.source.map_sha256)}</code>
        </p>
        <ul>
          {scene.limitations.map((value) => (
            <li key={value}>{value}</li>
          ))}
        </ul>
      </details>
    </section>
  );
}
