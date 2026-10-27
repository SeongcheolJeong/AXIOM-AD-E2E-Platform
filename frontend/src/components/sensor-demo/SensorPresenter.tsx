import { demoMoment, demoMomentIndex } from "../../demo-moment";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Expand, Pause, Play, SkipBack, SkipForward } from "lucide-react";
import { CarlaCameraFrame, type CarlaCameraRecord } from "../../pages/CarlaCameraFrame";
import { loadSensorCaptures } from "../sensor-streams/load-capture";
import NativePointPanel from "../sensor-streams/NativePointPanel";
import type { CaptureRecord, LoadedCapture, PointFrame, SensorStreamExecution } from "../sensor-streams/types";
import { sensorDemoObservedChange } from "./demo-contract";
import "../sensor-streams/sensor-streams.css";
import "./sensor-demo.css";

function cameraRecord(capture: LoadedCapture, record: CaptureRecord): CarlaCameraRecord {
  return { index: record.index, frame: record.frame, timestampNs: record.timestampNs, timestampSeconds: record.timestampSeconds, width: record.width, height: record.height, key: `${capture.stage} · ${record.key}`, artifact: capture.archiveArtifact, encoding: "bgra8", archive: capture.archive, member: record.member, cacheKey: record.cacheKey };
}
function frontRecord(records: CaptureRecord[]) { return records.find(r => r.kind === "rgb" && /front/i.test(r.key)) ?? records.find(r => r.kind === "rgb"); }

export default function SensorPresenter({ execution, title, presetId, initialTab = "lidar" }: { execution: SensorStreamExecution; title: string; presetId: string; initialTab?: "lidar" | "radar" }) {
  const [captures, setCaptures] = useState<LoadedCapture[]>([]), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [position, setPosition] = useState(0), [playing, setPlaying] = useState(false), [speed, setSpeed] = useState(1), [tab, setTab] = useState(initialTab), [rear, setRear] = useState(false);
  const [verifiedPosition, setVerifiedPosition] = useState<number | null>(null);
  const [ready, setReady] = useState<Record<string, boolean>>({});
  const cameraCache = useRef(new Map<string, Uint8ClampedArray>()), pointCache = useRef(new Map<string, PointFrame>()), root = useRef<HTMLElement>(null);
  const initialMoment = useRef(demoMoment(location.hash));
  const signature = execution.artifacts.map(a => `${a.id}:${a.sha256}:${a.size_bytes}`).join("|");
  const markReady = useCallback((key: string, value: boolean) => setReady(previous => previous[key] === value ? previous : { ...previous, [key]: value }), []);
  useEffect(() => {
    const controller = new AbortController(); let active = true;
    setCaptures([]); setVerifiedPosition(null); setError(""); setPosition(0); setPlaying(false); setReady({}); cameraCache.current.clear(); pointCache.current.clear();
    loadSensorCaptures(execution, controller.signal).then(value => { if (active) { if (value.length !== 2 || !value.some(c => c.stage === "rig_a") || !value.some(c => c.stage === "rig_b")) throw new Error("A/B 비교에는 같은 실행의 rig_a · rig_b 원본이 필요합니다."); sensorDemoObservedChange(value, presetId); const ordered = value.sort((a, b) => a.stage.localeCompare(b.stage)); const times = ordered[0].ticks.slice(0, Math.min(...ordered.map(c => c.ticks.length))).map(t => t.timestampSeconds - ordered[0].ticks[0].timestampSeconds); setPosition(demoMomentIndex(times, initialMoment.current)); setCaptures(ordered); } }).catch((e: Error) => { if (active) setError(e.message); });
    return () => { active = false; controller.abort(); cameraCache.current.clear(); pointCache.current.clear(); };
  }, [execution.id, execution.manifest_hash, signature, presetId]);
  const count = captures.length ? Math.min(...captures.map(c => c.ticks.length)) : 0, current = Math.min(position, Math.max(0, count - 1));
  const streams = useMemo(() => captures.map(capture => {
    const tick = capture.ticks[current], front = frontRecord(tick.records);
    return { capture, tick, front: front ? cameraRecord(capture, front) : null, rear: tick.records.filter(r => r.kind === "rgb" && r !== front).map(r => cameraRecord(capture, r)), points: tick.records.filter(r => r.kind === tab) };
  }), [captures, current, tab]);
  const activeKeys = streams.flatMap(s => [s.front?.cacheKey, ...(rear ? s.rear.map(r => r.cacheKey) : []), ...s.points.map(r => r.cacheKey)]).filter((key): key is string => !!key);
  const allReady = activeKeys.length > 0 && activeKeys.every(key => ready[key]);
  const driving = captures.length > 0 && captures.every(c => !!c.motion);
  useEffect(() => { if (allReady) setVerifiedPosition(current); }, [allReady, current]);
  const seconds = captures[0] ? captures[0].ticks[current].timestampSeconds - captures[0].ticks[0].timestampSeconds : 0;
  const duration = captures[0] ? captures[0].ticks[count - 1].timestampSeconds - captures[0].ticks[0].timestampSeconds : 0;
  useEffect(() => {
    if (!playing || !allReady || !count) return;
    if (current >= count - 1) { setPlaying(false); return; }
    const interval = Math.max(16, (captures[0].ticks[current + 1].timestampSeconds - captures[0].ticks[current].timestampSeconds) * 1000 / speed);
    const timer = setTimeout(() => setPosition(n => n + 1), interval); return () => clearTimeout(timer);
  }, [playing, allReady, count, current, captures, speed]);
  const sourceDifference = useMemo(() => {
    if (!captures.length) return null;
    const difference = sensorDemoObservedChange(captures, presetId);
    return `${difference.baseline.toLocaleString()} → ${difference.value.toLocaleString()} ${difference.unit}`;
  }, [captures, presetId]);
  const seek = (value: number) => { setPlaying(false); setPosition(Math.max(0, Math.min(value, count - 1))); };
  async function fullscreen() { try { if (document.fullscreenElement) await document.exitFullscreen(); else await root.current?.requestFullscreen(); } catch { setNotice("이 브라우저에서는 전체 화면을 열 수 없습니다."); } }
  return <section className={`sensor-presenter ${driving ? "sensor-presenter-driving" : ""}`} ref={root} aria-label="Sensor Sim A/B 발표 화면">
    <header className="sensor-presenter-heading"><div><span className="sensor-demo-eyebrow">RETAINED CARLA OUTPUT · A / B</span><h2>{title}</h2></div><button type="button" onClick={() => void fullscreen()} aria-label="Sensor Sim 전체 화면"><Expand size={16}/>전체 화면</button></header>
    <div className="sensor-presenter-context"><span>{driving ? "주행 중 동시 수집" : "정지 장면"} · {captures[0]?.map ?? "원본 확인 중"}</span><span>순차 실행 A/B · 상대 시간 비교</span><span>CARLA RGB · ray-cast LiDAR · radar</span>{driving && <span>지도·자차 기준 제어 · 센서 입력 주행 모델 아님</span>}</div>
    {sourceDifference && <div className="sensor-presenter-observed-difference"><strong>{sourceDifference}</strong><span>이 결과에 적용된 설정 · native readback</span>{driving && <div className="sensor-driving-current" aria-label="동일 프레임 주행 관측">{captures.map(capture => { const motion = verifiedPosition === null ? null : capture.motion?.ticks[verifiedPosition]; return <span key={capture.stage}><b>{capture.stage === "rig_a" ? "A" : "B"}</b>{motion ? `${motion.speedMps.toFixed(2)} m/s · ${motion.distanceM.toFixed(1)} m` : "원본 준비 중"}</span>; })}</div>}</div>}
    {error ? <p role="alert" className="sensor-demo-error">{error}</p> : !captures.length ? <div className="sensor-presenter-loading" role="status">보존된 실행과 원본 센서 스트림을 여는 중…</div> : <>
      <div className="sensor-presenter-cameras">{streams.map(({ capture, front }) => <article className="sensor-presenter-camera" key={capture.stage}>
        <header><span className={`sensor-rig-letter ${capture.stage}`}>{capture.stage === "rig_a" ? "A" : "B"}</span><strong>{capture.stage === "rig_a" ? "기준 센서" : "변경 센서"}</strong><span>{capture.sensors.find(s => /front/i.test(s.key))?.fov ?? capture.sensors.find(s => s.kind === "rgb")?.fov}° · 전방 RGB</span></header>
        {front ? <CarlaCameraFrame executionId={execution.id} record={front} cache={cameraCache.current} onReady={markReady}/> : <p>전방 카메라 원본이 없습니다.</p>}
      </article>)}</div>
      <div className="sensor-presenter-playback">
        <div className="sensor-presenter-controls"><button className="sensor-presenter-play" aria-label={playing ? "Sensor Sim 일시 정지" : "Sensor Sim 재생"} onClick={() => { if (current >= count - 1) setPosition(0); setPlaying(v => !v); }}>{playing ? <Pause size={16}/> : <Play size={16}/>} {playing ? "일시 정지" : "재생"}</button>
          <button onClick={() => seek(0)} aria-label="Sensor Sim 시작"><SkipBack size={14}/>시작</button><button onClick={() => seek(Math.floor((count - 1) / 2))}>중간</button><button onClick={() => seek(count - 1)} aria-label="Sensor Sim 마지막">마지막<SkipForward size={14}/></button>
          <select aria-label="Sensor Sim 재생 속도" value={speed} onChange={e => setSpeed(Number(e.target.value))}>{[.5, 1, 2].map(s => <option key={s} value={s}>{s}×</option>)}</select><output>{seconds.toFixed(1)} / {duration.toFixed(1)} s</output>
        </div><input aria-label="Sensor Sim 공통 상대 시간축" type="range" min="0" max={count - 1} value={current} onChange={e => seek(Number(e.target.value))}/><div className="sensor-presenter-stream-status" role="status">{allReady ? `표본 ${current + 1} / ${count} · 선택 스트림 원본 확인됨` : "다음 원본 준비 중 · 이전 확인 프레임 유지"}</div>
      </div>
      <div className="sensor-presenter-ranging-tabs" role="tablist" aria-label="거리 센서 비교"><button role="tab" aria-selected={tab === "lidar"} onClick={() => setTab("lidar")}>LiDAR · 공간 밀도</button><button role="tab" aria-selected={tab === "radar"} onClick={() => setTab("radar")}>Radar · 반환점</button><span>원본 반환점 · 물리 품질 점수 아님</span></div>
      <div className="sensor-presenter-ranging">{streams.map(({ capture, points }) => <div key={capture.stage}><strong className="sensor-presenter-point-label">{capture.stage === "rig_a" ? "A · 기준 센서" : "B · 변경 센서"}</strong>{points.map(record => <NativePointPanel key={record.key} capture={capture} record={record} cache={pointCache.current} onReady={markReady} compact/>)}{!points.length && <p>이 구성에 해당 센서 원본이 없습니다.</p>}</div>)}</div>
      <details className="sensor-presenter-details" onToggle={e => setRear(e.currentTarget.open)}><summary>후방 RGB 보기</summary>{rear && <div className="sensor-presenter-cameras">{streams.map(({ capture, rear: records }) => <div key={capture.stage}>{records.map(record => <CarlaCameraFrame key={record.key} executionId={execution.id} record={record} cache={cameraCache.current} onReady={markReady}/>)}</div>)}</div>}</details>
      <details className="sensor-presenter-details"><summary>실행 출처 · 원래 clock · 센서 설정 · 다운로드</summary>
        {driving && <p>각 주행의 RGB·LiDAR·radar는 같은 native frame·timestamp·자차 pose를 사용합니다. 표시 이동거리는 원본 자차 pose 간 XY 이동의 누적값입니다. 센서는 기준 제어기의 입력이 아닙니다.</p>}
        <p>순차 A/B는 서로 다른 world clock·renderer 이력을 가집니다. 상대 시간과 capture index를 맞춰 재생합니다. 실측 센서 상관성·물리 RAW·센서 품질은 평가하지 않았습니다.</p>
        {streams.map(({ capture, tick }) => <div key={capture.stage}><h3>{capture.stage === "rig_a" ? "Rig A" : "Rig B"} · native frame {tick.frame}</h3><p>{tick.timestampNs} ns · Seed {capture.seed} · Rig r{capture.rigRevision}</p><pre>{JSON.stringify({ external_execution_id: capture.externalExecutionId, configuration_sha256: capture.configurationHash, sensors: capture.sensors, motion: capture.motion ? { controller: capture.motion.controller, distance_m: capture.motion.distanceM, maximum_speed_mps: capture.motion.maximumSpeedMps, selected_tick: capture.motion.ticks[current] } : undefined, manifest_sha256: capture.manifest.sha256, archive_sha256: capture.archiveArtifact.sha256 }, null, 2)}</pre></div>)}
        <pre>{JSON.stringify({ execution_id: execution.id, adapter: `${execution.adapter_id}@${execution.adapter_version}`, input_hash: execution.input_hash, manifest_hash: execution.manifest_hash, fidelity: execution.fidelity }, null, 2)}</pre>
        <a href={`#sensor-sim?project=${encodeURIComponent(execution.project_id)}&mode=results&execution=${encodeURIComponent(execution.id)}`}>상세 원본 Viewer와 다운로드 열기 →</a>
      </details>
    </>}{notice && <p role="status">{notice}</p>}
  </section>;
}
