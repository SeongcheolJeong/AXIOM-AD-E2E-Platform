import { boundedDemoMoment, demoMoment } from "../../demo-moment";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  Maximize2,
  Pause,
  Play,
  RotateCcw,
  Sparkles,
  X,
} from "lucide-react";
import CitySceneViewer from "../city-sim/CitySceneViewer";
import type { CityPlaybackSnapshot } from "../city-sim/CitySceneViewer";
import {
  followingPlaybackDeadline,
  playbackDeadline,
} from "../object-demo/playback-cadence";
import type { PlaybackDeadline } from "../object-demo/playback-cadence";
import { loadHilTrace } from "./load-hil-trace";
import {
  hilDutLabel,
  hilEventLabel,
  hilFaultIntervalLabel,
  hilFaultLabel,
  hilObservedFaultWindow,
  hilSampleAt,
  hilSideLabel,
} from "./hil-trace";
import type { HilEvidence, HilFrame, HilTrace } from "./hil-trace";
import type { HilExecution, HilSide } from "./demo-contract";
import { hilSceneScheduleLabel, hilSceneTitle } from "./scene-profile";

const sides: HilSide[] = ["baseline", "faulted"];
const show = (value: number | null | undefined, precision = 1) =>
  typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(precision)
    : "—";
type SignalKey = "input_age_ms" | "ego_speed_mps" | "lead_gap_m" | "brake";
const signalValue = (frame: HilFrame, key: SignalKey) =>
  key === "brake" ? frame.command.brake * 100 : frame[key];

function SignalPlot({
  data,
  timeS,
  duration,
  onSeek,
}: {
  data: HilEvidence;
  timeS: number;
  duration: number;
  onSeek: (timeS: number) => void;
}) {
  const signals: {
    key: SignalKey;
    label: string;
    unit: string;
    minMax: number;
  }[] = [
    { key: "input_age_ms", label: "입력 age", unit: "ms", minMax: 300 },
    { key: "ego_speed_mps", label: "자차 속도", unit: "m/s", minMax: 3 },
    { key: "brake", label: "제동 명령", unit: "%", minMax: 100 },
    {
      key: "lead_gap_m",
      label: "입력 앞차 간격",
      unit: "m · 중심 간",
      minMax: 12,
    },
  ];
  const faultWindow = hilObservedFaultWindow(data.traces.faulted);
  function path(trace: HilTrace, key: SignalKey, max: number) {
    let open = false;
    return trace.frames
      .map((frame) => {
        const value = signalValue(frame, key);
        if (value === null) {
          open = false;
          return "";
        }
        const command = open ? "L" : "M";
        open = true;
        return `${command}${((frame.time_s / duration) * 800).toFixed(2)},${(39 - (Math.min(max, Math.max(0, value)) / max) * 33).toFixed(2)}`;
      })
      .join(" ");
  }
  return (
    <div className="hil-signal-plots" aria-label="A/B 동기 신호">
      <div className="hil-signal-heading">
        <span>INPUT → RESPONSE</span>
        <span>
          <i className="baseline" />
          {data.comparisonKind === "same_fault_dut_ab"
            ? "프로필 A"
            : "정상 A"}{" "}
          <i className="faulted" />
          {data.comparisonKind === "same_fault_dut_ab" ? "프로필 B" : "고장 B"}
        </span>
      </div>
      {signals.map((signal) => {
        const max = Math.max(
          signal.minMax,
          ...(signal.key === "input_age_ms"
            ? sides.map((side) => data.traces[side].policy.maxObjectAgeMs)
            : []),
          ...sides.flatMap((side) =>
            data.traces[side].frames.map(
              (frame) => signalValue(frame, signal.key) ?? 0,
            ),
          ),
        );
        return (
          <div className="hil-signal-row" key={signal.key}>
            <div>
              <strong>{signal.label}</strong>
              <small>{signal.unit}</small>
            </div>
            <svg
              viewBox="0 0 800 46"
              preserveAspectRatio="none"
              role="img"
              aria-label={`${signal.label} A/B 시간 그래프`}
              onClick={(event) => {
                const bounds = event.currentTarget.getBoundingClientRect();
                onSeek(
                  Math.max(
                    0,
                    Math.min(
                      duration,
                      ((event.clientX - bounds.left) / bounds.width) * duration,
                    ),
                  ),
                );
              }}
            >
              {faultWindow && (
                <rect
                  className="hil-fault-window"
                  x={(faultWindow.start / duration) * 800}
                  y="0"
                  width={
                    ((faultWindow.end - faultWindow.start) / duration) * 800
                  }
                  height="46"
                />
              )}
              {signal.key === "input_age_ms" &&
                sides.map((side, index) => (
                  <line
                    key={side}
                    className={`hil-age-threshold ${side}`}
                    aria-label={`${index === 0 ? "A" : "B"} 입력 age 임계값 ${data.traces[side].policy.maxObjectAgeMs} ms`}
                    x1="0"
                    x2="800"
                    y1={
                      39 - (data.traces[side].policy.maxObjectAgeMs / max) * 33
                    }
                    y2={
                      39 - (data.traces[side].policy.maxObjectAgeMs / max) * 33
                    }
                  />
                ))}
              <line className="hil-plot-axis" x1="0" x2="800" y1="40" y2="40" />
              {sides.map((side) => (
                <path
                  className={side}
                  d={path(data.traces[side], signal.key, max)}
                  key={side}
                />
              ))}
              <line
                className="hil-plot-cursor"
                x1={(timeS / duration) * 800}
                x2={(timeS / duration) * 800}
                y1="0"
                y2="46"
              />
            </svg>
            <div className="hil-signal-values">
              {sides.map((side) => {
                const frame = hilSampleAt(data.traces[side], timeS);
                return (
                  <span className={side} key={side}>
                    {show(
                      frame ? signalValue(frame, signal.key) : null,
                      signal.key === "input_age_ms" || signal.key === "brake"
                        ? 0
                        : 1,
                    )}
                  </span>
                );
              })}
            </div>
          </div>
        );
      })}
      <small className="hil-signal-footnote">
        공통 시간축은 제어 적용 후 응답 시점 · 고장 음영은 보존된 응답 이벤트
        기준 · 앞차 간격은 DUT에 전달된 객체 중심 간 거리 · 입력 age 기준선: A{" "}
        {data.traces.baseline.policy.maxObjectAgeMs} ms / B{" "}
        {data.traces.faulted.policy.maxObjectAgeMs} ms
      </small>
    </div>
  );
}

export default function HilPresenter({
  execution,
  presetId,
  onCursorChange,
  seekRequest,
}: {
  execution: HilExecution;
  presetId: string;
  onCursorChange?: (timeS: number) => void;
  seekRequest?: { timeS: number; nonce: number };
}) {
  const [data, setData] = useState<HilEvidence | null>(null),
    [error, setError] = useState(""),
    [timeS, setTimeS] = useState(0),
    [playing, setPlaying] = useState(false),
    [speed, setSpeed] = useState(1),
    [guided, setGuided] = useState(false);
  const [sensor, setSensor] = useState<"chase" | "front">("chase"),
    [expanded, setExpanded] = useState(false),
    [snapshots, setSnapshots] = useState<
      Partial<Record<HilSide, CityPlaybackSnapshot>>
    >({}),
    [visible, setVisible] = useState(!document.hidden);
  const initialMoment = useRef(demoMoment(location.hash));
  const root = useRef<HTMLElement>(null),
    deadline = useRef<PlaybackDeadline | null>(null),
    initialized = useRef(false);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    void loadHilTrace(execution, presetId, controller.signal)
      .then((value) => {
        if (active) setData(value);
      })
      .catch((reason) => {
        if (active)
          setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [execution, presetId]);
  useEffect(() => {
    const visibility = () => {
      setVisible(!document.hidden);
      deadline.current = null;
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    document.addEventListener("visibilitychange", visibility);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("visibilitychange", visibility);
      document.removeEventListener("keydown", escape);
    };
  }, []);
  useEffect(() => {
    if (expanded) root.current?.scrollTo({ top: 0 });
  }, [expanded]);
  const snapshot = useCallback(
    (side: HilSide, next: CityPlaybackSnapshot) =>
      setSnapshots((previous) => {
        const old = previous[side];
        return old &&
          old.requestedTimeS === next.requestedTimeS &&
          old.firstTimeS === next.firstTimeS &&
          old.lastTimeS === next.lastTimeS &&
          old.ready === next.ready &&
          old.error === next.error
          ? previous
          : { ...previous, [side]: next };
      }),
    [],
  );
  const baselineSnapshot = useCallback(
    (value: CityPlaybackSnapshot) => snapshot("baseline", value),
    [snapshot],
  );
  const faultedSnapshot = useCallback(
    (value: CityPlaybackSnapshot) => snapshot("faulted", value),
    [snapshot],
  );
  const duration = data
    ? Math.min(
        ...sides.map((side) => data.traces[side].frames.at(-1)!.time_s),
        ...sides.map((side) => snapshots[side]?.lastTimeS || Infinity),
      )
    : 0;
  const firstTime = data
    ? Math.max(
        ...sides.map((side) => data.traces[side].frames[0].time_s),
        ...sides.map((side) => snapshots[side]?.firstTimeS ?? 0),
      )
    : 0;
  const cameraError = sides.map((side) => snapshots[side]?.error).find(Boolean);
  const ready =
    !!data &&
    sides.every(
      (side) =>
        snapshots[side]?.ready &&
        snapshots[side]?.requestedTimeS === timeS &&
        !snapshots[side]?.error,
    );
  const loaded =
    !!data &&
    sides.every(
      (side) =>
        (snapshots[side]?.lastTimeS ?? 0) > 0 && !snapshots[side]?.error,
    );
  useEffect(() => {
    if (loaded && !initialized.current) {
      initialized.current = true;
      setTimeS(boundedDemoMoment(initialMoment.current, firstTime, duration));
    }
  }, [loaded, firstTime, duration]);
  useEffect(() => {
    if (!playing || !visible) {
      deadline.current = null;
      return;
    }
    if (!ready || cameraError) return;
    if (timeS >= duration) {
      setPlaying(false);
      deadline.current = null;
      return;
    }
    const next = playbackDeadline(
      deadline.current,
      performance.now(),
      `hil:${speed}`,
    );
    deadline.current = next;
    const timer = setTimeout(
      () => {
        if (document.hidden) return;
        deadline.current = followingPlaybackDeadline(next, performance.now());
        setTimeS((value) =>
          Math.min(duration, Math.round((value + 0.05 * speed) * 1e6) / 1e6),
        );
      },
      Math.max(0, next.dueMs - performance.now()),
    );
    return () => clearTimeout(timer);
  }, [playing, visible, ready, cameraError, timeS, duration, speed]);
  const seek = useCallback(
    (value: number) => {
      setPlaying(false);
      setGuided(false);
      deadline.current = null;
      setTimeS(Math.max(firstTime, Math.min(duration, value)));
    },
    [duration, firstTime],
  );
  useEffect(() => {
    onCursorChange?.(timeS);
  }, [timeS, onCursorChange]);
  useEffect(() => {
    if (seekRequest && loaded) seek(seekRequest.timeS);
  }, [seekRequest, loaded, seek]);
  const pause = useCallback(() => {
    setPlaying(false);
    setGuided(false);
  }, []);
  const changeSensor = useCallback(
    (value: "front" | "chase") => {
      pause();
      setSensor(value);
    },
    [pause],
  );
  const events = useMemo(
    () =>
      data
        ? (data.comparisonKind === "same_fault_dut_ab" || data.sceneProfile
            ? sides
            : ["faulted" as const]
          )
            .flatMap((side) =>
              data.traces[side].events
                .filter(
                  (event) =>
                    event.type === "fault_started" ||
                    event.type === "fault_ended" ||
                    event.type.includes("dut") ||
                    event.type.startsWith("lead_") ||
                    event.type.includes("state"),
                )
                .map((event) => ({ ...event, side })),
            )
            .sort((a, b) => a.time_s - b.time_s)
            .slice(0, 24)
        : [],
    [data],
  );
  async function fullscreen() {
    if (expanded) {
      setExpanded(false);
      if (document.fullscreenElement === root.current)
        await document.exitFullscreen();
      return;
    }
    setExpanded(true);
    try {
      await root.current?.requestFullscreen();
    } catch {
      /* In-app browsers retain the expanded presentation. */
    }
  }
  const faulted = data ? hilSampleAt(data.traces.faulted, timeS) : null;
  const faultWindow = data ? hilObservedFaultWindow(data.traces.faulted) : null;
  const responseOneTickLater =
    !!data &&
    !!faultWindow &&
    Math.abs(faultWindow.start - data.fault.start_s - 0.05) < 1e-7 &&
    Math.abs(
      faultWindow.end - data.fault.start_s - data.fault.duration_s - 0.05,
    ) < 1e-7;
  const inFault =
    !!faultWindow && timeS >= faultWindow.start && timeS < faultWindow.end;
  const profileMode = data?.comparisonKind === "same_fault_dut_ab";
  const guideTitle = !data
    ? "원본 결과를 준비합니다"
    : !faultWindow || timeS < faultWindow.start
      ? "01 · 같은 장면, 두 번의 실행"
      : inFault
        ? profileMode
          ? "02 · 같은 고장, 다른 DUT 정책"
          : "02 · B에만 객체 입력 고장 주입"
        : "03 · 입력 복구 이후의 DUT 반응";
  const guideBody = !data
    ? ""
    : !faultWindow || timeS < faultWindow.start
      ? profileMode
        ? "같은 초기 장면과 고장 일정을 사용합니다. 정책에 따른 차량 반응 이후 객체 입력은 달라질 수 있습니다."
        : "A와 B는 같은 초기 설정으로 시작합니다. 영상 아래 신호는 각 실행의 실제 저장값입니다."
      : inFault
        ? profileMode
          ? "A와 B에 같은 고장을 주입 중입니다. 각 프로필의 임계값과 제동 반응을 비교하세요."
          : `현재 입력 age ${show(faulted?.input_age_ms, 0)} ms · DUT ${faulted ? hilDutLabel(faulted.dut_state) : "관측 전"}. 영상과 제동 신호에서 반응을 비교하세요.`
        : `고장 주입 구간이 끝났습니다. 현재 DUT 상태 ${faulted ? hilDutLabel(faulted.dut_state) : "관측 전"}와 두 차량의 속도를 확인하세요.`;
  if (error)
    return (
      <div className="hil-error" role="alert">
        <strong>저장 결과를 열 수 없습니다.</strong>
        <p>{error}</p>
        <p>이 실행의 원본 연결과 프리셋을 확인하세요.</p>
      </div>
    );
  return (
    <section
      className={`hil-presenter ${expanded ? "hil-expanded" : ""}`}
      ref={root}
      aria-label="HIL A/B 발표 화면"
    >
      <header className="hil-presenter-heading">
        <div>
          <span className="hil-eyebrow">SOFTWARE DUT · CARLA SIMULATION</span>
          {data && (
            <p className="hil-retained-scene-title">
              실행 장면 · Town10HD_Opt · {hilSceneTitle(data.sceneProfile)}
              {data.sceneProfile ? ` · r${data.sceneProfile.revision}` : ""}
            </p>
          )}
          <h2>
            {profileMode ? "DUT 프로필 A" : "정상 입력"} <span>vs.</span>{" "}
            {profileMode
              ? `B · ${hilFaultLabel(data!.fault)}`
              : data
                ? hilFaultLabel(data.fault)
                : "고장 입력"}
            {data && (
              <strong className="hil-executed-range">
                입력 {hilFaultIntervalLabel(data.fault)} s
              </strong>
            )}
          </h2>
          {data && (
            <p className="hil-executed-clock">
              실행 결과 기준 · 응답 고장 이벤트{" "}
              {faultWindow
                ? `${faultWindow.start.toFixed(2)}–${faultWindow.end.toFixed(2)} s${responseOneTickLater ? " · 입력 +0.05 s" : ""}`
                : "보존 없음"}
            </p>
          )}
        </div>
        <button
          onClick={() => void fullscreen()}
          aria-label="HIL 발표 전체 화면"
        >
          {expanded ? <X size={17} /> : <Maximize2 size={17} />}{" "}
          {expanded ? "발표 닫기" : "발표 모드"}
        </button>
      </header>
      {data?.sceneProfile && (
        <div className="hil-lead-evidence">
          <div>
            <strong>작성한 앞차 목표 · 입력 시각</strong>
            <span>{hilSceneScheduleLabel(data.sceneProfile)}</span>
            <small>기존 장면의 앞차 속도·정지 행동만 대체</small>
          </div>
          <div>
            <strong>현재 보존된 앞차 응답</strong>
            <span>
              {sides.map((side, index) => {
                const frame = hilSampleAt(data.traces[side], timeS);
                return (
                  <b key={side} className={side}>
                    {index === 0 ? "A" : "B"} {show(frame?.lead_speed_mps, 2)}{" "}
                    <small>m/s</small>
                    <em>목표 {show(frame?.lead_target_speed_mps, 1)}</em>
                  </b>
                );
              })}
            </span>
            <small>
              실제 CARLA 응답 속도 · 정지·재출발 이벤트는 아래에서 탐색
            </small>
          </div>
        </div>
      )}
      {!data && (
        <p className="hil-loading" role="status">
          입력·DUT 응답 원본의 SHA-256을 확인하는 중…
        </p>
      )}
      <div className="hil-video-grid">
        {sides.map((side, index) => {
          const frame = data ? hilSampleAt(data.traces[side], timeS) : null;
          return (
            <article
              className={`hil-video-side ${side}`}
              key={side}
              aria-label={`${index === 0 ? "A" : "B"} ${data ? hilSideLabel(data, side) : "원본 준비"} 실행`}
            >
              <header>
                <span className="hil-side-letter">
                  {index === 0 ? "A" : "B"}
                </span>
                <div>
                  <strong>
                    {data ? hilSideLabel(data, side) : "원본 준비"}
                  </strong>
                  <small>
                    {index === 0 && !profileMode
                      ? "Baseline"
                      : inFault
                        ? "고장 주입 구간"
                        : faultWindow && timeS >= faultWindow.end
                          ? "주입 종료 이후"
                          : "고장 주입 전"}
                  </small>
                  {data && (
                    <small className="hil-retained-policy">
                      age {data.traces[side].policy.maxObjectAgeMs} ms · 회복{" "}
                      {data.traces[side].policy.recoveryFreshSteps}개
                    </small>
                  )}
                </div>
                <span className={`hil-dut-status ${frame?.dut_state ?? ""}`}>
                  {frame ? hilDutLabel(frame.dut_state) : "원본 준비"}
                </span>
              </header>
              <div className="hil-native-view">
                <CitySceneViewer
                  execution={execution}
                  hilSide={side}
                  initialView="2d"
                  playbackTimeS={timeS}
                  onPlaybackSeek={seek}
                  onPlaybackSnapshot={
                    index === 0 ? baselineSnapshot : faultedSnapshot
                  }
                  playbackActive={playing}
                  presentationSensor={sensor}
                  onPresentationSensorChange={changeSensor}
                  onManualInteraction={pause}
                  presentationLayout="native_camera"
                  presentation
                />
              </div>
              <div className="hil-frame-values">
                <div>
                  <small>자차 속도</small>
                  <strong>
                    {show(frame?.ego_speed_mps)}
                    <span>m/s</span>
                  </strong>
                </div>
                <div
                  className={
                    (frame?.input_age_ms ?? 0) >
                    (data?.traces[side].policy.maxObjectAgeMs ?? Infinity) +
                      1e-5
                      ? "warning"
                      : ""
                  }
                >
                  <small>DUT 입력 age</small>
                  <strong>
                    {show(frame?.input_age_ms, 0)}
                    <span>ms</span>
                  </strong>
                </div>
                <div>
                  <small>제동 명령</small>
                  <strong>
                    {show(frame ? frame.command.brake * 100 : null, 0)}
                    <span>%</span>
                  </strong>
                </div>
              </div>
            </article>
          );
        })}
      </div>
      <div className="hil-transport">
        <button
          className="hil-play"
          disabled={!loaded || !!cameraError}
          aria-label={playing ? "HIL 재생 일시정지" : "HIL A/B 재생"}
          onClick={() => {
            setGuided(false);
            if (timeS >= duration) setTimeS(firstTime);
            setPlaying((value) => !value);
          }}
        >
          {playing ? <Pause size={19} /> : <Play size={19} />}
        </button>
        <button
          aria-label="HIL 처음으로"
          disabled={!loaded}
          onClick={() => seek(firstTime)}
        >
          <RotateCcw size={16} />
        </button>
        <time>
          {timeS.toFixed(2)} <span>/ {duration.toFixed(2)} s</span>
        </time>
        <input
          aria-label="HIL 공통 시간축"
          type="range"
          min={firstTime}
          max={duration || 1}
          step="0.05"
          value={timeS}
          disabled={!loaded}
          onChange={(event) => seek(Number(event.target.value))}
        />
        <select
          aria-label="HIL 재생 속도"
          value={speed}
          onChange={(event) => {
            setSpeed(Number(event.target.value));
            deadline.current = null;
          }}
        >
          <option value={0.5}>0.5×</option>
          <option value={1}>1×</option>
          <option value={2}>2×</option>
        </select>
        <button
          className={sensor === "chase" ? "selected" : ""}
          onClick={() => changeSensor("chase")}
        >
          추적
        </button>
        <button
          className={sensor === "front" ? "selected" : ""}
          onClick={() => changeSensor("front")}
        >
          전방
        </button>
      </div>
      <div className="hil-buffering" role="status">
        {cameraError
          ? `원본 영상 오류 · ${cameraError}`
          : playing && !ready
            ? "다음 원본 프레임을 준비하고 있습니다…"
            : "같은 시뮬레이션 시점의 A/B 원본을 재생합니다."}
      </div>
      <div className={`hil-guide ${guided ? "active" : ""}`}>
        <div>
          <strong>{guideTitle}</strong>
          <p>{guideBody}</p>
        </div>
        <button
          disabled={!loaded || !!cameraError}
          onClick={() => {
            setTimeS(firstTime);
            setSpeed(1);
            setPlaying(true);
            setGuided(true);
            deadline.current = null;
          }}
        >
          <Sparkles size={16} />
          {guided && playing ? "자동 시연 다시 시작" : "자동 시연"}
          <ArrowRight size={14} />
        </button>
      </div>
      {data && (
        <>
          <div className="hil-events" aria-label="고장·DUT·앞차 응답 이벤트">
            {events.map((event, index) => (
              <button
                key={`${event.time_s}-${index}`}
                onClick={() => seek(event.time_s)}
                className={
                  Math.abs(timeS - event.time_s) < 0.06 ? "selected" : ""
                }
              >
                <span>{event.time_s.toFixed(2)} s</span>
                {profileMode || data.sceneProfile
                  ? `${event.side === "baseline" ? "A" : "B"} · `
                  : ""}
                {hilEventLabel(event)}
              </button>
            ))}
            {!events.length && (
              <span>보존된 고장·DUT 상태 전환 이벤트 없음</span>
            )}
          </div>
          <SignalPlot
            data={data}
            timeS={timeS}
            duration={duration || 18}
            onSeek={seek}
          />
          <details className="hil-provenance">
            <summary>실행 원본 · 시간 기준 · DUT 정책</summary>
            {data.sceneProfile && (
              <p>
                실행 장면: {data.sceneProfile.title} · r
                {data.sceneProfile.revision}. 기본 시나리오에 고정된 앞차 목표
                일정만 적용했습니다. 목표 일정은 입력 시각이며 앞차 명령
                이벤트와 관측 속도 이벤트는 보존된 응답 시각입니다. 장면
                SHA-256: <code>{data.sceneProfile.sha256}</code>
              </p>
            )}
            {sides.map((side, index) => (
              <p key={side}>
                {index === 0 ? "A" : "B"} · {hilSideLabel(data, side)}: 입력 age{" "}
                {data.traces[side].policy.maxObjectAgeMs} ms 초과 시 정지 명령,
                연속 {data.traces[side].policy.recoveryFreshSteps}개 정상 입력
                이후 주행 판단 재개.
                {data.traces[side].profile && (
                  <>
                    {" "}
                    프로필 SHA-256:{" "}
                    <code>{data.traces[side].profile!.sha256}</code>
                  </>
                )}
              </p>
            ))}
            {profileMode && (
              <p>
                A/B는 같은 고장 일정과 초기 시나리오를 사용합니다. 제어에 따른
                차량 반응 이후 생성·전달되는 객체 값은 달라질 수 있습니다.
              </p>
            )}
            <p>
              입력 age는 DUT 판단 시점의 값이며 영상·속도·제동 명령은 그 명령이
              적용된 응답 시점에 정렬합니다. 객체 입력은 CARLA GT, RGB는 관측
              영상입니다.
            </p>
            <p>
              고장 설정(입력 시점): {data.fault.start_s}–
              {(data.fault.start_s + data.fault.duration_s).toFixed(2)} s ·{" "}
              {hilFaultLabel(data.fault)}. 지연은 시뮬레이션 시간 기준이며 실제
              시간 마감시간 보장은 평가하지 않습니다.
            </p>
            <p>
              프로세스 왕복 시간(실제 wall clock): A{" "}
              {show(
                hilSampleAt(data.traces.baseline, timeS)?.ipc_wall_latency_ms,
                2,
              )}{" "}
              ms · B {show(faulted?.ipc_wall_latency_ms, 2)} ms
            </p>
            <dl>
              <dt>실행</dt>
              <dd>{execution.id}</dd>
              <dt>Adapter</dt>
              <dd>
                {execution.adapter_id} · {execution.adapter_version}
              </dd>
              <dt>Fidelity</dt>
              <dd>{execution.fidelity}</dd>
              <dt>입력 SHA-256</dt>
              <dd>{execution.input_hash}</dd>
              <dt>Manifest SHA-256</dt>
              <dd>{execution.manifest_hash}</dd>
            </dl>
            <p>
              실제 ECU·광학·RF 주입 및 물리 센서 검증은 포함하지 않습니다. 각
              실행의 native 차량 반응은 보존된 원본대로 표시합니다.
            </p>
            <a
              className="hil-provenance-link"
              href={`#viewers?project=${encodeURIComponent(execution.project_id)}&execution=${encodeURIComponent(execution.id)}`}
            >
              전체 실행 기록 · 원본 다운로드
              <ArrowRight size={12} />
            </a>
          </details>
        </>
      )}
    </section>
  );
}
