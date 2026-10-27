import { boundedDemoMoment, demoMoment } from "../../demo-moment";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Maximize2,
  Pause,
  Play,
  RotateCcw,
} from "lucide-react";
import { api } from "../../api";
import CitySceneViewer from "../city-sim/CitySceneViewer";
import type { CityPlaybackSnapshot } from "../city-sim/CitySceneViewer";
import {
  canShowCityExecution,
  comparisonFieldLabel,
  comparisonNeedsPolling,
  comparisonValue,
  comparisonSettingValue,
  executionStatusLabel,
} from "./comparison-model";
import type { ObjectComparisonData, ComparisonSide } from "./comparison-model";
import StoryPanel from "./StoryPanel";
import {
  advanceStory,
  initialStoryController,
  interruptStory,
  startStory as beginStory,
  storyProblem,
} from "./story-model";
import type { ObjectStory, StoryController } from "./story-model";
import {
  followingPlaybackDeadline,
  playbackDeadline,
} from "./playback-cadence";
import type { PlaybackDeadline } from "./playback-cadence";
import type { DemoPresentationLayout } from "./demo-contract";
import "./object-comparison.css";
type DisplayData = Omit<ObjectComparisonData, "right"> & {
  right: ComparisonSide | null;
};
function singleStoryData(story: ObjectStory): DisplayData {
  return {
    schema_version: "object-sim-comparison/1",
    project_id: story.project_id,
    left: story.left,
    right: null,
    compatibility: { compatible: true, reasons: [] },
    changes: [],
    metrics: [],
    evaluation_status: "existing_execution_verdicts_only",
  };
}

export default function ObjectComparison({
  project,
  leftExecutionId,
  rightExecutionId,
  onBack,
  autoStartStory = false,
  initialPresentationSensor = "front",
  initialGeometryView = "3d",
}: {
  project: string;
  leftExecutionId: string;
  rightExecutionId?: string;
  autoStartStory?: boolean;
  initialPresentationSensor?: "front" | "chase";
  initialGeometryView?: "2d" | "3d";
  onBack?: () => void;
}) {
  const [data, setData] = useState<DisplayData | null>(null),
    [error, setError] = useState(""),
    [reload, setReload] = useState(0),
    [timeS, setTimeS] = useState(0),
    [playing, setPlaying] = useState(false),
    [speed, setSpeed] = useState(1),
    [snapshots, setSnapshots] = useState<Record<string, CityPlaybackSnapshot>>(
      {},
    ),
    [expanded, setExpanded] = useState(false),
    [fullscreen, setFullscreen] = useState(false);
  const [story, setStory] = useState<ObjectStory | null>(null),
    [storyError, setStoryError] = useState(""),
    [storyLoading, setStoryLoading] = useState(true),
    [guide, setGuide] = useState<StoryController>({
      ...initialStoryController,
    }),
    [presentationSensor, setPresentationSensor] = useState<"front" | "chase">(
      initialPresentationSensor,
    ),
    [cameraMode, setCameraMode] = useState<"follow" | "overhead" | "all">(
      "follow",
    );
  const autoStarted = useRef(false);
  const [presentationLayout, setPresentationLayout] =
    useState<DemoPresentationLayout>("auto");
  const manualDeadline = useRef<PlaybackDeadline | null>(null),
    guidedDeadline = useRef<PlaybackDeadline | null>(null);
  const [pageVisible, setPageVisible] = useState(() => !document.hidden);
  const [bufferingVisible, setBufferingVisible] = useState(false);
  const bufferingShownAt = useRef(0);
  const workspace = useRef<HTMLElement>(null);
  const initializedCursor = useRef(false);
  const initialMoment = useRef(demoMoment(location.hash));
  const params = new URLSearchParams({
    project_id: project,
    left_execution_id: leftExecutionId,
  });
  if (rightExecutionId) params.set("right_execution_id", rightExecutionId);
  const path = `/object-sim/comparison?${params}`;
  const storyPath = `/object-sim/story?${params}`;

  useEffect(() => {
    const visibilityChanged = () => {
      manualDeadline.current = null;
      guidedDeadline.current = null;
      setPageVisible(!document.hidden);
    };
    document.addEventListener("visibilitychange", visibilityChanged);
    return () =>
      document.removeEventListener("visibilitychange", visibilityChanged);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    setData(null);
    setError("");
    setSnapshots({});
    setTimeS(0);
    setPlaying(false);
    initializedCursor.current = false;
    setStory(null);
    setStoryError("");
    setStoryLoading(true);
    setGuide({ ...initialStoryController });
    autoStarted.current = false;
    async function read() {
      const [comparisonResult, storyResult] = await Promise.allSettled([
        rightExecutionId
          ? api<ObjectComparisonData>(path, { signal: controller.signal })
          : Promise.resolve(null),
        api<ObjectStory>(storyPath, { signal: controller.signal }),
      ]);
      if (controller.signal.aborted) return;
      setStoryLoading(false);
      if (storyResult.status === "fulfilled") {
        setStory(storyResult.value);
        setStoryError("");
      } else
        setStoryError(
          storyResult.reason instanceof Error
            ? storyResult.reason.message
            : String(storyResult.reason),
        );
      const value =
        comparisonResult.status === "fulfilled" ? comparisonResult.value : null;
      if (value) {
        setData(value);
        setError("");
        if (comparisonNeedsPolling(value))
          timer = setTimeout(() => void read(), 3000);
      } else if (!rightExecutionId && storyResult.status === "fulfilled") {
        setData(singleStoryData(storyResult.value));
        setError("");
      } else {
        const reason =
          comparisonResult.status === "rejected"
            ? comparisonResult.reason
            : storyResult.status === "rejected"
              ? storyResult.reason
              : new Error("실행을 불러올 수 없습니다.");
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    }
    void read();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [path, storyPath, rightExecutionId, reload]);

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

  const onSnapshot = useCallback((value: CityPlaybackSnapshot) => {
    setSnapshots((previous) => {
      const old = previous[value.executionId];
      if (
        old &&
        old.requestedTimeS === value.requestedTimeS &&
        old.firstTimeS === value.firstTimeS &&
        old.lastTimeS === value.lastTimeS &&
        old.ready === value.ready &&
        old.error === value.error
      )
        return previous;
      return { ...previous, [value.executionId]: value };
    });
  }, []);
  const seek = useCallback((value: number) => {
    autoStarted.current = true;
    setPlaying(false);
    setTimeS(value);
    setGuide((state) => interruptStory(state, value));
  }, []);
  const manualInteraction = useCallback(() => {
    autoStarted.current = true;
    setPlaying(false);
    setGuide((state) => interruptStory(state, state.timeS));
  }, []);
  const visibleIds = useMemo(
    () =>
      data
        ? [data.left, ...(data.right ? [data.right] : [])]
            .filter(({ execution }) => canShowCityExecution(execution))
            .map(({ execution }) => execution.id)
        : [],
    [data],
  );
  const duration = Math.max(
    0,
    ...visibleIds.map((id) => snapshots[id]?.lastTimeS ?? 0),
  );
  const ready =
    visibleIds.length > 0 &&
    visibleIds.every((id) => {
      const snapshot = snapshots[id];
      return (
        snapshot &&
        (snapshot.error ||
          (snapshot.ready && snapshot.requestedTimeS === timeS))
      );
    });
  const hasPlayable = visibleIds.some(
    (id) =>
      snapshots[id] && !snapshots[id].error && snapshots[id].lastTimeS > 0,
  );
  const firstTime = Math.max(
    0,
    ...visibleIds.map((id) => snapshots[id]?.firstTimeS ?? 0),
  );
  const playbackActive = playing || guide.mode === "guided";
  const waitingForFrames = playbackActive && !ready;

  // This affects only the notice. The original-frame gate and playback clock
  // continue to use `ready` immediately, including every short acknowledgement.
  useEffect(() => {
    if (!playbackActive || !pageVisible) {
      setBufferingVisible(false);
      bufferingShownAt.current = 0;
      return;
    }
    let timer: ReturnType<typeof setTimeout> | undefined;
    if (waitingForFrames && !bufferingVisible) {
      timer = setTimeout(() => {
        bufferingShownAt.current = performance.now();
        setBufferingVisible(true);
      }, 400);
    } else if (!waitingForFrames && bufferingVisible) {
      // A sustained wait should not produce a one-frame flash as it finishes.
      timer = setTimeout(() => {
        setBufferingVisible(false);
        bufferingShownAt.current = 0;
      }, Math.max(0, 600 - (performance.now() - bufferingShownAt.current)));
    }
    return () => clearTimeout(timer);
  }, [waitingForFrames, bufferingVisible, playbackActive, pageVisible]);

  useEffect(() => {
    if (
      initializedCursor.current ||
      !hasPlayable ||
      !visibleIds.every(
        (id) => snapshots[id]?.lastTimeS > 0 || snapshots[id]?.error,
      )
    )
      return;
    initializedCursor.current = true;
    setTimeS(boundedDemoMoment(initialMoment.current, firstTime, duration));
  }, [visibleIds, snapshots, hasPlayable, firstTime, duration]);

  useEffect(() => {
    if (guide.mode === "guided" || !playing || !pageVisible) {
      manualDeadline.current = null;
      return;
    }
    if (!ready) return;
    if (timeS >= duration) {
      manualDeadline.current = null;
      setPlaying(false);
      return;
    }
    // Preserve the deadline across normal React/RGB acknowledgements, while
    // requiring both original views to be ready before granting one fixed step.
    const deadline = playbackDeadline(
      manualDeadline.current,
      performance.now(),
      `manual:${speed}`,
    );
    manualDeadline.current = deadline;
    const timer = setTimeout(
      () => {
        if (document.hidden) {
          manualDeadline.current = null;
          return;
        }
        manualDeadline.current = followingPlaybackDeadline(
          deadline,
          performance.now(),
        );
        setTimeS((value) =>
          Math.min(duration, Math.round((value + 0.05 * speed) * 1e6) / 1e6),
        );
      },
      Math.max(0, deadline.dueMs - performance.now()),
    );
    return () => clearTimeout(timer);
  }, [guide.mode, playing, ready, timeS, duration, speed, pageVisible]);

  const guideProblem = storyError || (story ? storyProblem(story) : null);
  const activeSegment = story?.segments[guide.segmentIndex];
  const startGuide = useCallback(
    (index = 0) => {
      if (!story || storyProblem(story) || !hasPlayable) return;
      const next = beginStory(story.segments, index);
      autoStarted.current = true;
      initializedCursor.current = true;
      setGuide(next);
      setTimeS(next.timeS);
      setPlaying(false);
      if (document.fullscreenElement !== workspace.current) setExpanded(true);
    },
    [story, hasPlayable],
  );
  useEffect(() => {
    if (
      !autoStartStory ||
      autoStarted.current ||
      !story ||
      !ready ||
      !hasPlayable ||
      guideProblem
    )
      return;
    autoStarted.current = true;
    startGuide();
  }, [autoStartStory, story, ready, hasPlayable, guideProblem, startGuide]);
  useEffect(() => {
    if (guide.mode !== "guided" || !story || !pageVisible) {
      guidedDeadline.current = null;
      return;
    }
    if (!ready) return;
    // Phase keys reset at the first ready anchor frame, so the explanation hold
    // starts after that original frame is visible and lasts 40 fixed steps.
    const deadline = playbackDeadline(
      guidedDeadline.current,
      performance.now(),
      `guide:${guide.segmentIndex}:${guide.phase}`,
    );
    guidedDeadline.current = deadline;
    const timer = setTimeout(
      () => {
        if (document.hidden) {
          guidedDeadline.current = null;
          return;
        }
        guidedDeadline.current = followingPlaybackDeadline(
          deadline,
          performance.now(),
        );
        const next = advanceStory(guide, story.segments, 0.05, true);
        setGuide(next);
        setTimeS(next.timeS);
      },
      Math.max(0, deadline.dueMs - performance.now()),
    );
    return () => clearTimeout(timer);
  }, [guide, story, ready, pageVisible]);
  const changeSensor = useCallback(
    (sensor: "front" | "chase") => {
      manualInteraction();
      setPresentationSensor(sensor);
    },
    [manualInteraction],
  );

  async function toggleFullscreen() {
    if (expanded) {
      setExpanded(false);
      if (document.fullscreenElement !== workspace.current) return;
    }
    if (document.fullscreenElement === workspace.current) {
      await document.exitFullscreen();
      return;
    }
    // Expand immediately: embedded browsers may leave fullscreen requests pending.
    setExpanded(true);
    try {
      await workspace.current?.requestFullscreen();
    } catch {
      setExpanded(true);
    }
  }

  function sidePanel(side: ComparisonSide, letter: "A" | "B") {
    const { execution } = side;
    const eventSide = letter === "A" ? "left" : "right";
    const focusedEvent =
      guide.mode === "guided"
        ? activeSegment?.events
            .filter((event) => event.side === eventSide)
            .sort(
              (left, right) =>
                Math.abs(
                  left.observation_time_s - activeSegment.anchor_time_s,
                ) -
                Math.abs(
                  right.observation_time_s - activeSegment.anchor_time_s,
                ),
            )[0]
        : undefined;
    const actorCount = Array.isArray(side.scenario?.actors)
      ? side.scenario.actors.length
      : 0;
    const snapshot = snapshots[execution.id];
    const ended =
      snapshot &&
      snapshot.lastTimeS > 0 &&
      timeS > snapshot.lastTimeS &&
      !snapshot.error;
    return (
      <article
        className={`object-compare-side side-${letter.toLowerCase()}`}
        aria-label={`실행 ${letter}`}
        key={letter}
      >
        <header className="object-compare-side-heading">
          <span className="object-compare-letter">{letter}</span>
          <div>
            <strong>
              {!rightExecutionId
                ? "저장된 실행"
                : letter === "A"
                  ? "기준 실행"
                  : "변경 실행"}
            </strong>
            <span>{String(side.scenario?.name ?? execution.id)}</span>
          </div>
          <span className={`object-compare-status ${execution.status}`}>
            {execution.outcome_uncertain
              ? "실행 결과 불확실"
              : executionStatusLabel(execution.status)}
          </span>
          {ended && <span className="object-compare-status">관측 종료</span>}
        </header>
        {canShowCityExecution(execution) ? (
          <CitySceneViewer
            execution={execution}
            initialView={initialGeometryView}
            initialMapFit={initialGeometryView === "2d" ? "actor" : "map"}
            playbackTimeS={timeS}
            onPlaybackSeek={seek}
            onPlaybackSnapshot={onSnapshot}
            playbackActive={
              playing || (guide.mode === "guided" && guide.phase !== "hold")
            }
            presentationSensor={presentationSensor}
            presentationLayout={presentationLayout}
            onPresentationSensorChange={changeSensor}
            onManualInteraction={manualInteraction}
            selectedActorId={focusedEvent?.actor_id}
            decisionFocusTimeS={
              guide.mode === "guided" ? activeSegment?.anchor_time_s : undefined
            }
            cameraMode={
              guide.mode === "guided"
                ? actorCount >= 8
                  ? "all"
                  : "follow"
                : cameraMode
            }
            onCameraModeChange={(mode: "follow" | "overhead" | "all") => {
              manualInteraction();
              setCameraMode(mode);
            }}
            presentation
          />
        ) : (
          <div className="object-compare-unavailable" role="status">
            <strong>{executionStatusLabel(execution.status)}</strong>
            <p>
              {["queued", "pending", "running"].includes(execution.status)
                ? "원본 결과가 봉인되면 이 자리에서 재생할 수 있습니다."
                : "이 실행에는 재생 가능한 CARLA 도시 원본이 없습니다. 실행 상태와 남아 있는 기록을 확인하세요."}
            </p>
            {execution.error && (
              <p role="alert">
                {typeof execution.error === "string"
                  ? execution.error
                  : JSON.stringify(execution.error)}
              </p>
            )}
          </div>
        )}
        <details className="object-compare-source">
          <summary>실행 정보 · 개정 · 원본</summary>
          <dl>
            <dt>실행</dt>
            <dd>{execution.id}</dd>
            <dt>시나리오 개정</dt>
            <dd>
              {side.scenario_ref
                ? `${side.scenario_ref.id} · r${side.scenario_ref.revision}`
                : "기록 없음"}
            </dd>
            <dt>Adapter</dt>
            <dd>
              {execution.adapter_id} · {execution.adapter_version}
            </dd>
            <dt>Fidelity</dt>
            <dd>{execution.fidelity ?? "기록 없음"}</dd>
            <dt>입력 hash</dt>
            <dd>{execution.input_hash}</dd>
            <dt>Manifest hash</dt>
            <dd>{execution.manifest_hash ?? "미봉인"}</dd>
          </dl>
          <a
            className="button secondary"
            href={`#object-sim?${new URLSearchParams({ project, execution: execution.id })}`}
          >
            전체 실행 기록 열기
          </a>
        </details>
      </article>
    );
  }

  return (
    <section
      ref={workspace}
      className={`object-comparison ${!rightExecutionId ? "object-comparison-single" : ""} ${guide.mode === "guided" ? "object-story-active" : ""} ${expanded ? "object-comparison-expanded" : ""}`}
      aria-label="Object Sim A/B 비교"
    >
      <header className="object-compare-heading">
        <div>
          {onBack && (
            <button className="object-compare-back" onClick={onBack}>
              <ArrowLeft size={15} />
              빠른 시작
            </button>
          )}
          <span className="object-compare-eyebrow">
            OBJECT SIM / {rightExecutionId ? "A · B" : "NATIVE STORY"}
          </span>
          <h2>
            {rightExecutionId
              ? "설정의 변화가 행동의 차이로."
              : "차량의 판단을 한 장면씩."}
          </h2>
          <p>
            {rightExecutionId
              ? "두 실행을 같은 시간으로 재생하며, 회피·양보·복귀가 어떻게 달라졌는지 확인하세요."
              : "실제 보존된 사건을 따라가며 다중 차량의 판단과 움직임을 확인하세요."}
          </p>
        </div>
        <button
          className="button secondary"
          onClick={() => void toggleFullscreen()}
        >
          <Maximize2 size={15} />
          {fullscreen || expanded ? "발표 화면 닫기" : "발표 화면"}
        </button>
      </header>
      {error && (
        <div className="object-compare-error" role="alert">
          {error}
          <button
            className="button secondary"
            onClick={() => setReload((v) => v + 1)}
          >
            다시 불러오기
          </button>
        </div>
      )}
      {!data && !error && (
        <div className="object-compare-unavailable" role="status">
          고정된 실행과 설정 차이를 불러오는 중…
        </div>
      )}
      {data && (
        <>
          <StoryPanel
            story={story}
            problem={guideProblem}
            loading={storyLoading}
            canStart={hasPlayable}
            state={guide}
            onStart={() => startGuide()}
            onResume={() => startGuide(guide.segmentIndex)}
            onSegment={(index) => {
              const segment = story?.segments[index];
              if (segment) {
                seek(segment.anchor_time_s);
                setGuide((state) => ({ ...state, segmentIndex: index }));
              }
            }}
          />
          {data.right && (
            <section
              className="object-compare-changes"
              aria-label="변경한 설정"
            >
              <div className="object-compare-section-title">
                <div>
                  <span>01 / CONFIGURATION</span>
                  <h3>무엇을 바꿨나요?</h3>
                </div>
                <strong>{data.changes.length}개 변경</strong>
              </div>
              {data.changes.length ? (
                <div className="object-compare-change-grid">
                  {data.changes.slice(0, 6).map((change) => (
                    <article key={change.path}>
                      <strong>{comparisonFieldLabel(change.path)}</strong>
                      <small>{change.path}</small>
                      <div>
                        <span aria-label="A 기준값">
                          A · {comparisonSettingValue(change.path, change.left)}
                        </span>
                        <ArrowRight size={14} />
                        <b aria-label="B 변경값">
                          B · {comparisonSettingValue(change.path, change.right)}
                        </b>
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <p>
                  두 실행의 비교 설정이 같습니다. 반복 실행 결과를 같은
                  시간축에서 확인할 수 있습니다.
                </p>
              )}
              {data.changes.length > 6 && (
                <details>
                  <summary>모든 변경 {data.changes.length}개 보기</summary>
                  <div className="object-compare-table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>설정</th>
                          <th>A · 기준</th>
                          <th>B · 변경</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.changes.map((change) => (
                          <tr key={change.path}>
                            <th>{change.path}</th>
                            <td>{comparisonSettingValue(change.path, change.left)}</td>
                            <td>{comparisonSettingValue(change.path, change.right)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>
              )}
              {data.changes.some((change) => [change.left, change.right].some((value) => value && typeof value === "object")) && (
                <details className="object-compare-exact-diff">
                  <summary>원본 설정 차이 보기</summary>
                  <pre>{JSON.stringify(data.changes, null, 2)}</pre>
                </details>
              )}
              {!data.compatibility.compatible && (
                <div className="object-compare-compatibility" role="status">
                  <strong>비교 조건을 확인하세요</strong>
                  <p>
                    고정 조건 또는 실행 결과가 충분히 일치하지 않아 수치 증감은
                    제공하지 않습니다. 각 실행의 원본은 개별적으로 조사할 수
                    있습니다.
                  </p>
                  <ul>
                    {data.compatibility.reasons.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                </div>
              )}
            </section>
          )}
          <div
            className="object-compare-transport"
            aria-label="공통 재생 컨트롤"
          >
            <button
              className="button"
              disabled={!hasPlayable}
              onClick={() => {
                autoStarted.current = true;
                const wasGuided = guide.mode === "guided";
                setGuide((state) => interruptStory(state, timeS));
                if (timeS >= duration) setTimeS(firstTime);
                setPlaying((v) => (wasGuided ? false : !v));
              }}
            >
              {playing || guide.mode === "guided" ? (
                <Pause size={16} />
              ) : (
                <Play size={16} />
              )}
              {playing || guide.mode === "guided"
                ? "일시정지"
                : rightExecutionId
                  ? "함께 재생"
                  : "재생"}
            </button>
            <button
              className="button secondary"
              aria-label="비교 처음으로"
              onClick={() => seek(firstTime)}
              disabled={!hasPlayable}
            >
              <RotateCcw size={15} />
            </button>
            <input
              aria-label="비교 공통 시간"
              type="range"
              min={0}
              max={duration || 1}
              step={0.01}
              value={Math.min(timeS, duration)}
              disabled={!hasPlayable}
              onChange={(event) => seek(Number(event.target.value))}
            />
            <output>
              {timeS.toFixed(2)} <small>/ {duration.toFixed(2)} s</small>
            </output>
            <select
              aria-label="비교 재생 배속"
              value={speed}
              onChange={(event) => {
                manualInteraction();
                setSpeed(Number(event.target.value));
              }}
            >
              {[0.25, 0.5, 1, 2, 4].map((value) => (
                <option key={value} value={value}>
                  {value}×
                </option>
              ))}
            </select>
            <div
              className="object-story-camera-switch"
              role="group"
              aria-label="공통 카메라"
            >
              <button
                className={`button ${presentationSensor === "front" ? "" : "secondary"}`}
                aria-pressed={presentationSensor === "front"}
                onClick={() => changeSensor("front")}
              >
                전방
              </button>
              <button
                className={`button ${presentationSensor === "chase" ? "" : "secondary"}`}
                aria-pressed={presentationSensor === "chase"}
                onClick={() => changeSensor("chase")}
              >
                추적
              </button>
            </div>
            <div
              className="object-story-camera-switch object-scene-layout-switch"
              role="group"
              aria-label="공통 장면 구성"
            >
              {(
                [
                  ["auto", "자동"],
                  ["native_camera", "카메라 중심"],
                  ["analysis", "분석 중심"],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  className={`button ${presentationLayout === value ? "" : "secondary"}`}
                  aria-pressed={presentationLayout === value}
                  onClick={() => {
                    manualInteraction();
                    setPresentationLayout(value);
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="object-compare-buffer-status" role="status" aria-live="polite" aria-atomic="true">
              {bufferingVisible ? rightExecutionId ? "양쪽 원본 프레임을 기다리는 중…" : "원본 프레임을 기다리는 중…" : ""}
            </div>
          </div>
          <div className="object-compare-panels">
            {sidePanel(data.left, "A")}
            {data.right && sidePanel(data.right, "B")}
          </div>
          {data.right && (
            <details className="object-compare-metrics-details">
              <summary>기존 실행 지표 비교</summary>
              <section
                className="object-compare-metrics"
                aria-label="기존 실행 지표 비교"
              >
                <div className="object-compare-section-title">
                  <div>
                    <span>02 / RETAINED RESULTS</span>
                    <h3>실행 결과 나란히 보기</h3>
                  </div>
                  <span>각 실행의 기존 평가 · 관측값</span>
                </div>
                <p>
                  수치 증감은 B − A입니다. 설정 차이의 효과나 승패를 자동
                  판정하지 않습니다.
                </p>
                {data.metrics.length ? (
                  <div className="object-compare-table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>기존 지표</th>
                          <th>A · 기준</th>
                          <th>B · 변경</th>
                          <th>증감</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.metrics.map((metric) => (
                          <tr key={metric.path}>
                            <th>
                              {comparisonFieldLabel(metric.path)}
                              <small>{metric.path}</small>
                            </th>
                            <td>{comparisonValue(metric.left)}</td>
                            <td>{comparisonValue(metric.right)}</td>
                            <td>
                              {data.compatibility.compatible &&
                              metric.delta !== null
                                ? `${metric.delta > 0 ? "+" : ""}${comparisonValue(metric.delta)}`
                                : "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p>아직 비교할 수 있는 기존 실행 지표가 없습니다.</p>
                )}
              </section>
            </details>
          )}
          <p className="object-compare-footnote">
            3D는 저장 좌표의 도식이며 RGB는 CARLA 원본 렌더 영상입니다. 각
            화면은 공통 시각 이전의 마지막 관측을 표시하고, 실행이 끝나면 마지막
            원본을 유지합니다. 실제 센서·실차 검증 결과가 아닙니다.
          </p>
        </>
      )}
    </section>
  );
}
