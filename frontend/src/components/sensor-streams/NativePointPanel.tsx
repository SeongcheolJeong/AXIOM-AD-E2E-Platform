import { Component, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { BufferAttribute, BufferGeometry, Color, Vector3 } from "three";
import { decodeNativePoints } from "./decode-points";
import type { CaptureRecord, LoadedCapture, PointFrame } from "./types";

class ViewBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <p role="status">3D rendering is unavailable; original point counts and raw inspector remain available.</p> : this.props.children; }
}
function NativeCloud({ frame, mode, size, selected, pick }: { frame: PointFrame; mode: string; size: number; selected: number; pick: (index: number) => void }) {
  const geometry = useMemo(() => {
    const g = new BufferGeometry(), colors = new Float32Array(frame.count * 3), color = new Color();
    g.setAttribute("position", new BufferAttribute(frame.positions, 3));
    for (let index = 0; index < frame.count; index++) {
      if (frame.kind === "radar" && Math.abs(frame.values[index]) > 100) color.set("#ff4c9d");
      else if (mode === "height") color.setHSL(.57 - Math.min(1, Math.max(0, (frame.positions[index * 3 + 2] + 3) / 20)) * .46, .75, .62);
      else if (frame.kind === "lidar") color.setHSL(.62 - frame.values[index] * .4, .74, .64);
      else { const v = frame.values[index]; color.set(v < 0 ? "#59aaff" : v > 0 ? "#f8b96a" : "#6ef0c4"); }
      color.toArray(colors, index * 3);
    }
    g.setAttribute("color", new BufferAttribute(colors, 3)); g.computeBoundingSphere(); return g;
  }, [frame, mode]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  return <><points geometry={geometry} onClick={event => { if (event.index !== undefined) { event.stopPropagation(); pick(event.index); } }}><pointsMaterial size={size} sizeAttenuation={false} vertexColors /></points>
    {frame.count > 0 && <mesh position={[frame.positions[selected * 3], frame.positions[selected * 3 + 1], frame.positions[selected * 3 + 2]]}><sphereGeometry args={[.45, 12, 8]} /><meshBasicMaterial color="#fff6aa" /></mesh>}</>;
}
function ViewCamera({ frame, view, reset }: { frame: PointFrame; view: string; reset: number }) {
  const { camera, controls } = useThree(), latest = useRef(frame); latest.current = frame;
  useEffect(() => {
    const positions = latest.current.positions, min = new Vector3(-5, -5, -2), max = new Vector3(5, 5, 3);
    for (let i = 0; i < positions.length; i += 3) { const p = new Vector3(positions[i], positions[i + 1], positions[i + 2]); min.min(p); max.max(p); }
    const center = min.clone().add(max).multiplyScalar(.5), distance = Math.max(20, min.distanceTo(max)) * .82;
    camera.up.set(0, view === "xy" ? 1 : 0, view === "xy" ? 0 : 1);
    const direction = view === "xy" ? new Vector3(0, 0, 1) : new Vector3(-1, -1.25, .9).normalize();
    camera.position.copy(center).addScaledVector(direction, distance); camera.near = .01; camera.far = Math.max(1000, distance * 10); camera.updateProjectionMatrix(); camera.lookAt(center);
    const orbit = controls as unknown as { target: Vector3; update: () => void } | undefined; if (orbit) { orbit.target.copy(center); orbit.update(); }
  }, [camera, controls, view, reset]);
  return null;
}

export default function NativePointPanel({ capture, record, cache, onReady, compact = false }: { capture: LoadedCapture; record: CaptureRecord; cache: Map<string, PointFrame>; onReady: (id: string, ready: boolean) => void; compact?: boolean }) {
  const [state, setState] = useState<{ key: string; frame: PointFrame | null; record: CaptureRecord | null; error: string }>({ key: "", frame: null, record: null, error: "" });
  const [view, setView] = useState("orbit"), [mode, setMode] = useState("native"), [size, setSize] = useState(record.kind === "radar" ? 6 : 3), [picked, setPicked] = useState(0), [reset, setReset] = useState(0), [downloading, setDownloading] = useState(false);
  const downloadController = useRef<AbortController | null>(null), label = `${capture.stage} ${record.key}`;
  useEffect(() => () => downloadController.current?.abort(), []);
  useEffect(() => {
    let active = true; const controller = new AbortController(); setState(previous => compact && previous.record?.key === record.key ? { ...previous, error: "" } : { key: record.cacheKey, frame: null, record: null, error: "" }); setPicked(0); onReady(record.cacheKey, false);
    async function load() {
      let frame = cache.get(record.cacheKey);
      if (!frame) { frame = decodeNativePoints(await capture.archive.read(record.member, controller.signal), record); if (!active) return; cache.set(record.cacheKey, frame); while (cache.size > 16) cache.delete(cache.keys().next().value!); }
      else { cache.delete(record.cacheKey); cache.set(record.cacheKey, frame); }
      if (active) { setState({ key: record.cacheKey, frame, record, error: "" }); onReady(record.cacheKey, true); }
    }
    load().catch((error: Error) => { if (active) setState({ key: record.cacheKey, frame: null, record: null, error: error.message }); });
    return () => { active = false; controller.abort(); };
  }, [capture.archive, record, cache, onReady, compact]);
  const frame = state.key === record.cacheKey || compact ? state.frame : null, shown = frame && state.record ? state.record : record, currentReady = !!frame && state.key === record.cacheKey, selected = frame && frame.count ? Math.min(picked, frame.count - 1) : null;
  async function download() {
    const controller = new AbortController(); downloadController.current?.abort(); downloadController.current = controller; setDownloading(true);
    try { const bytes = await capture.archive.read(shown.member, controller.signal); if (controller.signal.aborted) return; const url = URL.createObjectURL(new Blob([new Uint8Array(bytes)], { type: "application/octet-stream" })), anchor = document.createElement("a"); anchor.href = url; anchor.download = `${capture.stage}-${shown.member.name.split("/").at(-1)}`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 2000); }
    catch (error) { if (!controller.signal.aborted) setState(s => ({ ...s, error: (error as Error).message })); }
    finally { if (!controller.signal.aborted) setDownloading(false); }
  }
  return <article className={`native-point-panel ${compact ? "native-point-compact" : ""}`} aria-label={`${label} native ranging viewer`} data-native-kind={record.kind} data-native-count={frame?.count ?? ""} data-native-index={shown.index} data-requested-index={record.index}>
    <header><div><h3>{record.kind === "lidar" ? "LiDAR" : "Radar"} <span>{record.key}</span></h3><small>{frame ? `${frame.count.toLocaleString()} 원본 ${record.kind === "lidar" ? "points" : "returns"} · ${currentReady ? compact ? "원본 확인됨" : "SHA-256 verified" : "이전 확인 표본 유지"}` : "원본 표본 확인 중"}</small></div><button type="button" aria-label={`Fit ${label} points`} onClick={() => setReset(n => n + 1)}>Fit</button></header>
    {frame?.warnings.map(w => <p key={w} role="status" className="sensor-stream-warning native-radar-anomaly">{w}</p>)}
    <div className="native-point-controls"><button aria-label={`${label} 3D view`} aria-pressed={view === "orbit"} onClick={() => setView("orbit")}>3D</button><button aria-label={`${label} XY view`} aria-pressed={view === "xy"} onClick={() => setView("xy")}>XY</button>
      <label>색상<select aria-label={`${label} point color`} value={mode} onChange={e => setMode(e.target.value)}><option value="native">{record.kind === "lidar" ? "Native intensity" : "Native velocity sign"}</option><option value="height">Ego RH height</option></select></label>
      <label>크기<input type="range" aria-label={`${label} point size`} min="1" max="10" value={size} onChange={e => setSize(Number(e.target.value))} /></label></div>
    <div className="native-point-canvas" role="img" aria-label={`${label} registered native point cloud`} data-native-ready={currentReady}>
      {state.error ? <p role="alert" className="expansion-error">{state.error}</p> : !frame ? <p role="status">현재 tick의 원본 Range·SHA-256 확인 중…</p> : !frame.count ? <p>이 native tick에는 반환점이 없습니다. 빈 원본 표본을 보존합니다.</p> : <ViewBoundary><Canvas dpr={[1, 1.5]} camera={{ fov: 48, position: [-80, -95, 65], up: [0, 0, 1] }} raycaster={{ params: { Points: { threshold: 1 }, Line: { threshold: 1 }, Mesh: {}, LOD: {}, Sprite: {} } }}><color attach="background" args={["#10232c"]} /><gridHelper args={[200, 20, "#65878e", "#28434f"]} rotation={[Math.PI / 2, 0, 0]} /><axesHelper args={[8]} />
        <NativeCloud frame={frame} mode={mode} size={size} selected={selected ?? 0} pick={setPicked} /><OrbitControls makeDefault enableDamping={false} /><ViewCamera frame={frame} view={view} reset={reset} /></Canvas></ViewBoundary>}
    </div>
    {compact && <p className="native-point-hold" role="status">{currentReady ? `표본 ${shown.index + 1} · ${shown.timestampNs} ns` : state.error ? "원본 오류 · 재생 대기" : "다음 반환점 확인 중 · 이전 표본 유지"}</p>}
    <p className="native-point-axis">Ego RH · +X 전방 / +Y 좌측 / +Z 위 · m · 10 m reference grid</p>
    {frame && <div className="native-point-inspector" aria-label={`${label} raw point inspector`}>
      <label>원본 표본 index<input aria-label={`${label} raw point index`} type="number" min="0" max={Math.max(0, frame.count - 1)} disabled={!frame.count} value={selected ?? 0} onChange={e => setPicked(Math.min(Math.max(0, Number(e.target.value) || 0), Math.max(0, frame.count - 1)))} /></label>
      {selected !== null ? <><dl><dt>{record.kind === "lidar" ? "Native sensor LH (x, y, z, intensity)" : "Native (velocity m/s, azimuth rad, altitude rad, depth m)"}</dt><dd data-testid={`${capture.stage}-${record.key}-raw-tuple`}>{Array.from(frame.raw.slice(selected * 4, selected * 4 + 4)).map(v => v.toFixed(6)).join(" · ")}</dd><dt>Derived ego RH (x, y, z) · m</dt><dd>{Array.from(frame.positions.slice(selected * 3, selected * 3 + 3)).map(v => v.toFixed(4)).join(" · ")}</dd></dl></> : <span>0 original returns</span>}
      <small>표본 index는 현재 프레임 안에서만 유효합니다. actor 추적 ID가 아닙니다. 원본 값을 보정·클리핑하지 않습니다.</small>
    </div>}
    <details className="native-point-evidence"><summary>현재 native 표본 · clock / pose / hash</summary><pre>{JSON.stringify({ native_frame: shown.frame, timestamp_ns: shown.timestampNs, member: shown.member, native_encoding: shown.encoding, sensor_world_lh: shown.sensorWorld, ego_world_lh: shown.egoWorld, derived_sensor_to_ego_rh: shown.relativeRh, registration: "S × inverse(M_ego_LH) × M_sensor_LH × p_sensor_LH" }, null, 2)}</pre></details>
    <button className="button secondary" aria-label={`Download ${label} original points`} disabled={downloading} onClick={() => void download()}>{downloading ? "원본 확인 중…" : "현재 float32 원본 다운로드"}</button>
  </article>;
}
