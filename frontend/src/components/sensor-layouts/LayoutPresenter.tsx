import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Expand, Minimize2, Pause, Play } from "lucide-react";
import { CarlaCameraFrame, type CarlaCameraRecord } from "../../pages/CarlaCameraFrame";
import { demoMomentIndex } from "../../demo-moment";
import { loadSensorCaptures } from "../sensor-streams/load-capture";
import NativePointPanel from "../sensor-streams/NativePointPanel";
import type { LoadedCapture, PointFrame } from "../sensor-streams/types";
import { layoutResultMatches, mountChanges, sensorKeys, sensorLabels } from "./layout-contract";
import type { LayoutExecution, LayoutMoment, LayoutSensorKey, SensorLayout } from "./layout-contract";
import "../sensor-streams/sensor-streams.css";
import "../sensor-demo/sensor-demo.css";

export default function LayoutPresenter({ link, layout, sensor, onSensor, onMoment, seekRequest }: { link: LayoutExecution; layout: SensorLayout; sensor: LayoutSensorKey; onSensor: (sensor: LayoutSensorKey) => void; onMoment: (moment: LayoutMoment | null) => void; seekRequest: { time_s: number; sequence: number } }) {
  const execution = link.execution;
  const [captures, setCaptures] = useState<LoadedCapture[]>([]), [error, setError] = useState("");
  const [position, setPosition] = useState(0), [playing, setPlaying] = useState(false), [speed, setSpeed] = useState(1);
  const [expanded, setExpanded] = useState(false), [inlineHeight, setInlineHeight] = useState(0);
  const [ready, setReady] = useState<{ cursor: string; keys: Record<string, boolean> }>({ cursor: "", keys: {} });
  const cameraCache = useRef(new Map<string, Uint8ClampedArray>()), pointCache = useRef(new Map<string, PointFrame>()), root = useRef<HTMLElement>(null);
  const expandButton = useRef<HTMLButtonElement>(null);
  const initialTime = useRef(seekRequest.time_s);
  useEffect(() => {
    if (!expanded) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    expandButton.current?.focus({ preventScroll: true });
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setExpanded(false); }
      if (event.key !== "Tab") return;
      const controls = Array.from(root.current?.querySelectorAll<HTMLElement>("button, a[href], input, select, textarea, summary, [tabindex]") ?? []).filter(element => !element.hasAttribute("disabled") && element.tabIndex >= 0 && element.getClientRects().length > 0);
      const first = controls[0], last = controls.at(-1);
      if (event.shiftKey && (document.activeElement === first || !root.current?.contains(document.activeElement))) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !root.current?.contains(document.activeElement))) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", keydown, true);
    return () => { document.body.style.overflow = previousOverflow; document.removeEventListener("keydown", keydown, true); expandButton.current?.focus({ preventScroll: true }); };
  }, [expanded]);
  useEffect(() => {
    let active = true; const controller = new AbortController(); onMoment(null);
    setCaptures([]); setError(""); setPlaying(false); cameraCache.current.clear(); pointCache.current.clear();
    loadSensorCaptures(execution, controller.signal).then(value => {
      if (!active) return; layoutResultMatches(value, layout, link);
      const ordered = value.sort((a, b) => a.stage.localeCompare(b.stage));
      setPosition(demoMomentIndex(ordered[0].ticks.map(t => t.timestampSeconds - ordered[0].ticks[0].timestampSeconds), initialTime.current)); setCaptures(ordered);
    }).catch(reason => { if (active) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => { active = false; controller.abort(); onMoment(null); };
  }, [execution.id, execution.manifest_hash, layout.sha256, onMoment]);
  const count = captures.length ? Math.min(...captures.map(capture => capture.ticks.length)) : 0;
  const current = Math.min(position, Math.max(0, count - 1)), cursor = `${execution.id}:${current}:${sensor}`;
  const markReady = useCallback((key: string, value: boolean) => setReady(previous => previous.cursor === cursor && previous.keys[key] === value ? previous : { cursor, keys: { ...(previous.cursor === cursor ? previous.keys : {}), [key]: value } }), [cursor]);
  const streams = useMemo(() => captures.map(capture => {
    const record = capture.ticks[current].records.find(record => record.key === sensor)!;
    const camera: CarlaCameraRecord = { index: record.index, frame: record.frame, timestampNs: record.timestampNs, timestampSeconds: record.timestampSeconds, width: record.width, height: record.height, key: `${capture.stage} · ${record.key}`, artifact: capture.archiveArtifact, encoding: "bgra8", archive: capture.archive, member: record.member, cacheKey: record.cacheKey };
    return { capture, record, camera };
  }), [captures, current, sensor]);
  const allReady = streams.length === 2 && ready.cursor === cursor && streams.every(stream => ready.keys[stream.record.cacheKey]);
  const time = captures[0] ? captures[0].ticks[current].timestampSeconds - captures[0].ticks[0].timestampSeconds : 0;
  const duration = captures[0] ? captures[0].ticks[count - 1].timestampSeconds - captures[0].ticks[0].timestampSeconds : 0;
  useEffect(() => { onMoment(allReady && !playing ? { execution_id: execution.id, index: current, time_s: Math.round(time * 1e6) / 1e6, sensor_key: sensor } : null); }, [allReady, playing, execution.id, current, time, sensor, onMoment]);
  useEffect(() => { if (!captures.length) return; setPlaying(false); setPosition(demoMomentIndex(captures[0].ticks.map(t => t.timestampSeconds - captures[0].ticks[0].timestampSeconds), seekRequest.time_s)); }, [seekRequest.sequence, captures]);
  useEffect(() => {
    if (!playing || !allReady || !count) return;
    if (current >= count - 1) { setPlaying(false); return; }
    const timer = setTimeout(() => setPosition(index => index + 1), Math.max(16, (captures[0].ticks[current + 1].timestampSeconds - captures[0].ticks[current].timestampSeconds) * 1000 / speed)); return () => clearTimeout(timer);
  }, [playing, allReady, count, current, captures, speed]);
  const changes = mountChanges(layout.rig_a, layout.rig_b);
  const seek = (index: number) => { setPlaying(false); setPosition(Math.max(0, Math.min(count - 1, index))); };
  function toggleExpanded() { if (!expanded) setInlineHeight(root.current?.getBoundingClientRect().height ?? 0); setExpanded(value => !value); }
  return <div className="layout-presenter-slot" style={expanded ? { height: inlineHeight } : undefined}><section ref={root} className={`sensor-presenter layout-presenter${expanded ? " layout-presenter-expanded" : ""}`} role={expanded ? "dialog" : undefined} aria-modal={expanded || undefined} aria-label="센서 배치 저장 결과 비교">
    <header className="sensor-presenter-heading"><div><span className="sensor-demo-eyebrow">RETAINED PLACEMENT · A / B</span><h2>{layout.name} · r{layout.revision}</h2></div><button ref={expandButton} aria-expanded={expanded} onClick={toggleExpanded}>{expanded ? <Minimize2 size={16}/> : <Expand size={16}/>}{expanded ? "큰 화면 닫기 · Esc" : "크게 보기"}</button></header>
    <div className="sensor-presenter-context"><span>주행 RGB · LiDAR · radar 동시 수집</span><span>순차 A/B · 공통 상대 시간</span><span>CARLA 시뮬레이션 출력</span></div>
    <div className="layout-sensor-picker" role="group" aria-label="저장 결과 센서 선택">{sensorKeys.map(key => <button key={key} aria-pressed={sensor === key} onClick={() => { setPlaying(false); onSensor(key); }}>{sensorLabels[key]}</button>)}</div>
    <div className="layout-source-mounts" aria-label="실행에 적용된 장착 위치">{(["rig_a", "rig_b"] as const).map(stage => { const mount = layout[stage].sensors.find(item => item.key === sensor)!; return <div key={stage}><strong>{stage === "rig_a" ? "A · 고정 기준" : "B · 실행된 배치"}</strong><span>XYZ {mount.translation_m.join(" / ")} m</span><span>Pitch / Yaw / Roll {mount.rotation_deg.join(" / ")}°</span></div>; })}<p>{captures.length ? "검증된 실행 원본과 일치하는 저장 배치" : "원본 일치 확인 중"} · 변경 {changes.length}개 · 편집값과 별개</p></div>
    {error ? <p className="sensor-demo-error" role="alert">{error}</p> : !captures.length ? <div className="sensor-presenter-loading" role="status">실행 receipt · 센서 manifest · 원본 해시 확인 중…</div> : <>
      <div className={`layout-result-pair ${sensor === "front" || sensor === "rear" ? "layout-result-cameras" : "layout-result-points"}`}>{streams.map(({ capture, record, camera }) => {
        return <article key={capture.stage}><header><span className={`sensor-rig-letter ${capture.stage}`}>{capture.stage === "rig_a" ? "A" : "B"}</span><strong>{sensorLabels[sensor]}</strong>{allReady && <span>{capture.motion?.ticks[current].speedMps.toFixed(2)} m/s · {capture.motion?.ticks[current].distanceM.toFixed(1)} m</span>}</header>{record.kind === "rgb" ? <CarlaCameraFrame key={sensor} executionId={execution.id} record={camera} cache={cameraCache.current} onReady={markReady}/> : <NativePointPanel key={sensor} capture={capture} record={record} cache={pointCache.current} onReady={markReady} compact/>}</article>;
      })}</div>
      <div className="sensor-presenter-playback"><div className="sensor-presenter-controls"><button className="sensor-presenter-play" aria-label={playing ? "배치 결과 일시 정지" : "배치 결과 재생"} onClick={() => { if (current >= count - 1) setPosition(0); setPlaying(value => !value); }}>{playing ? <Pause size={15}/> : <Play size={15}/>}{playing ? "일시 정지" : "재생"}</button><button onClick={() => seek(0)}>시작</button><button onClick={() => seek(Math.floor((count - 1) / 2))}>중간</button><button onClick={() => seek(count - 1)}>마지막</button><select aria-label="배치 재생 속도" value={speed} onChange={event => setSpeed(Number(event.target.value))}>{[.5, 1, 2].map(value => <option key={value} value={value}>{value}×</option>)}</select><output>{time.toFixed(1)} / {duration.toFixed(1)} s</output></div><input aria-label="배치 결과 공통 시간축" type="range" min={0} max={count - 1} value={current} onChange={event => seek(Number(event.target.value))}/><p className="sensor-presenter-stream-status" role="status">{allReady ? `원본 표본 ${current + 1} / ${count} 확인됨${playing ? " · 관찰하려면 일시 정지" : " · 현재 시점에 관찰 작성 가능"}` : "다음 원본 준비 중 · 이전 확인 프레임 유지"}</p></div>
      <details className="sensor-presenter-details"><summary>실행 출처 · 실제 적용된 전체 배치 · 원본 Viewer</summary><p>센서는 지도 기반 주행 제어기의 입력이 아닙니다. 배치 도식은 실제 가시성·물리 센서 검증을 대신하지 않습니다.</p><pre>{JSON.stringify({ layout_ref: link.layout_ref, execution_id: execution.id, input_hash: execution.input_hash, manifest_hash: execution.manifest_hash, changes, captures: captures.map(capture => ({ stage: capture.stage, hash: capture.configurationHash, sensors: capture.sensors, distance_m: capture.motion?.distanceM, manifest_sha256: capture.manifest.sha256 })) }, null, 2)}</pre><a href={`#sensor-sim?project=${encodeURIComponent(layout.project_id)}&mode=results&execution=${encodeURIComponent(execution.id)}`}>상세 원본 Viewer 열기 →</a></details>
    </>}
  </section></div>;
}
