import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CarlaCameraFrame, type CarlaCameraRecord } from "../../pages/CarlaCameraFrame";
import type { CarlaZipArtifact } from "../../pages/carla-zip";
import { loadSensorCaptures } from "./load-capture";
import NativePointPanel from "./NativePointPanel";
import type { LoadedCapture, PointFrame, SensorStreamExecution } from "./types";
import "./sensor-streams.css";

export default function CarlaMultisensorViewer({ execution, onDownload }: { execution: SensorStreamExecution; onDownload?: (artifact: CarlaZipArtifact) => void }) {
  const [captures, setCaptures] = useState<LoadedCapture[]>([]), [error, setError] = useState(""), [stage, setStage] = useState(""), [position, setPosition] = useState(0), [playing, setPlaying] = useState(false), [speed, setSpeed] = useState(1), [ready, setReady] = useState<Record<string, boolean>>({}), [notice, setNotice] = useState("");
  const cameras = useRef(new Map<string, Uint8ClampedArray>()), points = useRef(new Map<string, PointFrame>()), root = useRef<HTMLElement>(null);
  const signature = execution.artifacts.map(a => `${a.id}:${a.sha256}:${a.size_bytes}`).join("|");
  const markReady = useCallback((key: string, value: boolean) => setReady(current => current[key] === value ? current : { ...current, [key]: value }), []);
  useEffect(() => {
    let active = true; const controller = new AbortController(); setCaptures([]); setError(""); setStage(""); setPosition(0); setPlaying(false); setReady({}); cameras.current.clear(); points.current.clear();
    loadSensorCaptures(execution, controller.signal).then(value => { if (active) { setCaptures(value); setStage(value[0]?.stage ?? ""); } }).catch((e: Error) => { if (active) setError(e.message); });
    return () => { active = false; controller.abort(); cameras.current.clear(); points.current.clear(); };
    // Source identity is the immutable selected receipt and original descriptors.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [execution.id, execution.manifest_hash, signature]);
  const selected = useMemo(() => captures.filter(c => stage === "compare" || c.stage === stage), [captures, stage]);
  const count = selected[0]?.ticks.length ?? 0, current = Math.min(position, Math.max(0, count - 1));
  const activeRecords = useMemo(() => selected.flatMap(c => c.ticks[current]?.records ?? []), [selected, current]);
  const cameraRecords = useMemo(() => selected.map(c => ({ capture: c, records: c.ticks[current].records.filter(r => r.kind === "rgb").map(r => ({ index: r.index, frame: r.frame, timestampNs: r.timestampNs, timestampSeconds: r.timestampSeconds, width: r.width, height: r.height, key: r.key, artifact: c.archiveArtifact, encoding: "bgra8" as const, archive: c.archive, member: r.member, cacheKey: r.cacheKey } satisfies CarlaCameraRecord)) })), [selected, current]);
  const driving = selected.length > 0 && selected.every(c => !!c.motion);
  const allReady = activeRecords.length > 0 && activeRecords.every(r => ready[r.cacheKey]);
  useEffect(() => {
    if (!playing || !allReady || !count) return;
    if (current >= count - 1) { setPlaying(false); return; }
    const tick = selected[0].ticks[current], next = selected[0].ticks[current + 1], interval = Math.max(16, Math.min(3000, (next.timestampSeconds - tick.timestampSeconds) * 1000 / speed));
    const timer = setTimeout(() => setPosition(n => n + 1), interval); return () => clearTimeout(timer);
  }, [playing, allReady, count, current, selected, speed]);
  function move(index: number) { setPlaying(false); setPosition(Math.min(Math.max(0, index), count - 1)); }
  async function fullscreen() { try { if (document.fullscreenElement) await document.exitFullscreen(); else await root.current?.requestFullscreen(); } catch { setNotice("이 브라우저에서는 전체 화면을 열 수 없습니다."); } }
  return <section ref={root} className="sensor-stream-workbench expansion-panel" aria-label="CARLA retained multisensor playback">
    <header className="sensor-stream-heading"><div><span className="sensor-stream-eyebrow">SENSOR SIM · RETAINED NATIVE STREAMS</span><h2>카메라 · LiDAR · Radar</h2><p>같은 native frame의 영상과 반환점을 함께 조사합니다.</p></div><button className="button secondary" aria-label="Fullscreen CARLA multisensor viewer" onClick={() => void fullscreen()}>전체 화면</button></header>
    <p className="sensor-stream-scope">{driving ? "주행 중 같은 native frame에서 수집한 CARLA RGB·LiDAR·radar입니다. 지도·자차 상태를 사용하는 기준 제어기이며 센서 입력 주행 모델이 아닙니다." : "정지 자차에서 보존한 CARLA 후처리 RGB·ray-cast LiDAR·radar입니다."} 실측 센서·물리 RAW / ISP·센서 품질은 평가하지 않았습니다.</p>
    {error && <p role="alert" className="expansion-error">{error}</p>}{!captures.length && !error && <p role="status">봉인된 입력·원본 파일 closure·clock·장착 좌표를 확인하는 중…</p>}
    {captures.length > 0 && <><div className="sensor-stream-toolbar"><div className="sensor-stream-stages" aria-label="CARLA multisensor rig selection">{captures.map(c => <button key={c.stage} aria-label={`Select multisensor ${c.stage}`} aria-pressed={stage === c.stage} onClick={() => { setStage(c.stage); setPosition(0); setPlaying(false); }}>{c.stage === "single" ? "단일 구성" : c.stage === "rig_a" ? "구성 A" : "구성 B"} · r{c.rigRevision}</button>)}{captures.length === 2 && <button aria-label="Compare multisensor A and B" aria-pressed={stage === "compare"} onClick={() => { setStage("compare"); setPosition(0); setPlaying(false); }}>A / B 나란히</button>}</div>
      <span>{selected[0]?.map} · Seed {selected[0]?.seed} · {count} native ticks</span></div>
      {stage === "compare" && <p className="sensor-stream-warning" role="status">Sequential A/B · 같은 capture index를 나란히 표시합니다. 원래 world clock과 교통 신호·renderer 이력은 서로 다르며 동시 측정이 아닙니다.</p>}
      <div className="sensor-stream-timeline"><div className="sensor-stream-play-controls"><button aria-label={playing ? "Pause CARLA multisensor playback" : "Play CARLA multisensor playback"} onClick={() => { if (current >= count - 1) setPosition(0); setPlaying(v => !v); }}>{playing ? "일시 정지" : "재생"}</button><button aria-label="Previous CARLA multisensor frame" disabled={current === 0} onClick={() => move(current - 1)}>←</button><button aria-label="Next CARLA multisensor frame" disabled={current >= count - 1} onClick={() => move(current + 1)}>→</button><select aria-label="CARLA multisensor playback speed" value={speed} onChange={e => setSpeed(Number(e.target.value))}>{[.25, .5, 1, 2].map(s => <option key={s} value={s}>{s}×</option>)}</select></div>
        <input type="range" aria-label="CARLA multisensor timeline" min="0" max={count - 1} value={current} onChange={e => move(Number(e.target.value))} /><span data-testid="multisensor-native-position">Index {current} / {count - 1} · {allReady ? "all active streams verified" : "waiting for original streams"}</span></div>
      <div className={`sensor-stream-rigs ${selected.length === 2 ? "compare" : ""}`}>{selected.map(c => {
        const tick = c.ticks[current], images = cameraRecords.find(r => r.capture === c)!.records;
        return <section className="sensor-stream-rig" key={c.stage} aria-label={`${c.stage} native sensor workspace`}>
          <header className="sensor-stream-rig-heading"><h3>{c.stage === "single" ? "Native capture" : c.stage === "rig_a" ? "Rig A" : "Rig B"} <span>r{c.rigRevision}</span></h3><div data-testid={`${c.stage}-native-clock`}>Native frame {tick.frame}<br /><strong>{tick.timestampNs} ns</strong> · +{(tick.timestampSeconds - c.ticks[0].timestampSeconds).toFixed(1)} s</div></header>
          {c.motion && <div className="sensor-stream-motion" aria-label={`${c.stage} 주행 관측`}>관측 속도 {c.motion.ticks[current].speedMps.toFixed(2)} m/s · 누적 이동 {c.motion.ticks[current].distanceM.toFixed(1)} m · 목표 {String(c.motion.controller.target_speed_mps)} m/s</div>}
          <div className="sensor-stream-cameras">{images.map(record => <article key={record.key} className="sensor-stream-camera"><header><h3>{record.key} · RGB</h3><span>{c.sensors.find(s => s.key === record.key)?.fov}° FOV</span></header><CarlaCameraFrame executionId={execution.id} record={record} cache={cameras.current} onReady={markReady} onDownload={onDownload} /></article>)}</div>
          {!images.length && <p>이 rig에는 보존된 RGB stream이 없습니다.</p>}
          <div className="sensor-stream-ranging">{tick.records.filter(r => r.kind !== "rgb").map(record => <NativePointPanel key={record.key} capture={c} record={record} cache={points.current} onReady={markReady} />)}</div>
          <details className="sensor-stream-source"><summary>Rig 개정 · engine readback / 장착 / 보정 범위</summary><pre>{JSON.stringify({ external_execution_id: c.externalExecutionId, configuration_sha256: c.configurationHash, rig_revision: c.rigRevision, seed: c.seed, motion: c.motion ? { controller: c.motion.controller, route: c.motion.route, selected_tick: c.motion.ticks[current], distance_m: c.motion.distanceM, maximum_speed_mps: c.motion.maximumSpeedMps } : undefined, sensors: c.sensors, manifest_sha256: c.manifest.sha256, retained_archive_sha256: c.archiveArtifact.sha256, registration: "Original sensor LH points → observed native sensor/ego transforms → ego RH", geometry_scope: "Metric grid/axes are reference guides; no road topology, tracked actors or calibrated physical response are reconstructed" }, null, 2)}</pre></details>
        </section>;
      })}</div>
      <footer className="sensor-stream-footer"><span>봉인 receipt·입력·manifest SHA-256 확인. ZIP의 현재 tick만 bounded Range로 읽고 각 member SHA-256을 검증합니다. 카메라 캐시 8장 / 반환점 캐시 16프레임. 영상·점·clock을 보간하지 않습니다.</span><details><summary>Immutable source identity</summary><pre>{JSON.stringify({ execution_id: execution.id, adapter_version: execution.adapter_version, input_hash: execution.input_hash, manifest_hash: execution.manifest_hash, fidelity: execution.fidelity, evaluation_status: "not_evaluated" }, null, 2)}</pre></details></footer>
    </>}{notice && <p role="status">{notice}</p>}
  </section>;
}
