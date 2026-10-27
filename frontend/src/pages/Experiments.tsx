import { useEffect, useMemo, useRef, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, authHeaders, formatBytes, isExternalViewer, useResource } from "../api";
import { Badge, PageHeading } from "../components/ui";
import CarlaFeedbackViewer, { CarlaFeedbackAuthor } from "./CarlaFeedbackViewer";
import { feedbackInputTemplate } from "./carla-feedback";
import { retainedExecutionSource } from "../retainedExecutionSources";
import ObjectSceneViewer from "../components/object-scene/ObjectSceneViewer";
import CitySceneViewer from "../components/city-sim/CitySceneViewer";
import CarlaMultisensorViewer from "../components/sensor-streams/CarlaMultisensorViewer";
import "../expansion.css";
import "./sensor-sim.css";

export type Kind = "object_sim" | "planning_replay" | "sensor_capture" | "isp_compare" | "driving_open_loop" | "neural_reconstruction" | "neural_render";
type Handler = { id: string; version: string; kinds: Kind[]; status: string; fidelity: string; description: string; resource_id: string | null };
type Project = { id: string; name: string };
type Preview = { preview_hash: string; job_count: number; resource_id: string | null; execution_fidelity: string; evaluation_status: string; prepared: { input: unknown } };
type Created = { id: string; project_id: string; preview_hash: string; prepared: { spec: { name: string; kind: Kind; seed?: number }; input: unknown }; executions?: Execution[] };
type ExecutionListing = Pick<Execution, "id" | "experiment_id" | "experiment_kind" | "status" | "fidelity" | "created_at" | "input_hash" | "manifest_hash"> & { name: string };
export type ExperimentsProps = { displayMode?: "author" | "results"; kindFilter?: Kind[]; title?: string; description?: string; executionId?: string };
type Artifact = { id: string; name: string; media_type: string; size_bytes: number; sha256: string; download_url: string };
type Metric = { status: string; value: number | boolean | null; unit: string; reason: string | null };
type Pose = { x_m: number; y_m: number; yaw_rad: number; speed_mps: number; length_m: number; width_m: number };
type ObjectPose = Pose & { id: string };
type Tick = { index: number; at_ns: string; ego: Pose; logged_ego: Pose; world_objects: ObjectPose[] | null; metrics: Record<string, Metric>; control: { acceleration_mps2: number; steering_rad: number; decision: string } | null; object_count?: number; objects_preview_truncated?: boolean };
type Execution = { id: string; experiment_id: string; project_id: string; status: string; experiment_kind: Kind; adapter_id: string; adapter_version: string; input_hash: string; error: string | null; outcome_uncertain: boolean; cancellation_supported: boolean; fidelity: string | null; artifacts: Artifact[]; summary: Record<string, unknown> | null; manifest_hash: string | null; download_url: string | null; created_at: string };
const labels: Record<Kind, string> = { object_sim: "Object simulation", planning_replay: "Log / planning replay", sensor_capture: "Sensor simulation / capture", isp_compare: "Retained RAW / ISP comparison", driving_open_loop: "Driving policy open loop", neural_reconstruction: "Neural 3D reconstruction", neural_render: "Neural novel view rendering" };
const terminal = new Set(["completed", "failed", "cancelled", "interrupted"]);
function fidelityLabel(fidelity: string | null | undefined) {
  if (fidelity === "native_carla_external_map_load_readback") return "CARLA · 외부 지도 로드 / 차로 읽기";
  if (fidelity === "neural_reconstruction_of_carla") return "CARLA 합성 로그 · Neural 재구성";
  if (fidelity === "native_carla_timed_position_replay") return "CARLA · 위치 재생 / RGB 진단";
  if (fidelity === "native_carla_mixed_physics_and_replay") return "CARLA · 물리 제어 + 위치 재생 혼합";
  if (fidelity === "native_carla_object_physics") return "CARLA · 객체 물리 / feedback 제어";
  if (fidelity?.includes("native_geometric_lens_db_estimate_sensor")) return "CameraE2E · 렌즈/추정 센서 계산";
  if (fidelity?.includes("carla") && fidelity?.includes("feedback")) return "CARLA · feedback 주행 / 연속 RGB";
  if (fidelity?.includes("carla")) return "CARLA · 시뮬레이터 출력";
  if (fidelity?.includes("authored_planar_reference")) return "작성한 평면 reference · 진단";
  if (fidelity?.includes("native") && fidelity?.includes("object")) return "Object engine · 객체 궤적";
  return fidelity ? fidelity.replaceAll("_", " ") : "Fidelity 미확인 · 실행 기록";
}
function resultDate(value: string) { return new Date(value).toLocaleString("ko-KR", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); }

function ObjectExecutionView({ execution }: { execution: Execution }) {
  if (execution.adapter_id === "carla-map-qualification") return <section className="expansion-panel"><h2>외부 지도 확인 작업</h2><p className="expansion-note">지도 로드·차로 읽기를 확인하는 작업입니다. 상태와 보존 산출물을 아래에서 확인하세요. 주행 시나리오의 영상·궤적과 안전 판정은 이 작업에 포함되지 않습니다.</p></section>;
  return execution.adapter_id === "carla-city-object" ? <CitySceneViewer execution={execution} /> : <ObjectSceneViewer execution={execution} />;
}


function validObject(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function jsonObject(text: string, name: string) { const value: unknown = JSON.parse(text); if (!validObject(value)) throw new Error(`${name} must be a JSON object.`); return value; }

type LensCatalog = { lenses: { revision_id: string; manufacturer: string; name: string; configuration: string; simulation_id: string }[]; sensors: { report_code: string; device_name: string; run_id: string }[]; fixed_isp_profile: unknown; scene_template: Record<string, unknown> };
function LensSceneAuthor({ catalog, text, onChange }: { catalog: LensCatalog | undefined; text: string; onChange: (value: string) => void }) {
  let value: Record<string, unknown> = {}; try { value = jsonObject(text, "Input"); } catch { /* explicit JSON correction remains available */ }
  const lenses = validObject(value.lenses) ? value.lenses : {}, sensors = validObject(value.sensors) ? value.sensors : {}, scene = validObject(value.scene) ? value.scene : {};
  function update(patch: Record<string, unknown>) { onChange(JSON.stringify({ ...value, ...patch }, null, 2)); }
  function load() {
    if (!catalog) return;
    const l1 = catalog.lenses.find((item) => item.revision_id === "4daeca8f-5d81-57b0-8ae3-928f23de2b36") ?? catalog.lenses[0];
    const l2 = catalog.lenses.find((item) => item.revision_id === "fd959d9d-8503-5fe4-a582-4cc9074e6bfd") ?? catalog.lenses.find((item) => item.revision_id !== l1?.revision_id);
    const s1 = catalog.sensors.find((item) => item.report_code === "DEF-2512-801") ?? catalog.sensors[0];
    const s2 = catalog.sensors.find((item) => item.report_code === "DEF-2412-804") ?? catalog.sensors.find((item) => item.run_id !== s1?.run_id);
    onChange(JSON.stringify({ schema_version: "camera-e2e-lens-scene-spec/1", lenses: { L1: l1?.revision_id ?? "", L2: l2?.revision_id ?? "" },
      sensors: { S1: { identifier: s1?.report_code ?? "", run_id: s1?.run_id ?? "" }, S2: { identifier: s2?.report_code ?? "", run_id: s2?.run_id ?? "" } },
      scene: catalog.scene_template, profile: catalog.fixed_isp_profile, acknowledge_simulation_limits: false }, null, 2));
  }
  return <section aria-label="Lens and sensor diagnostic authoring"><p className="expansion-note warning">Actual native geometric calculation on an authored central slanted edge. Saved sensors are DB estimates. A 550 nm point map supports a bounded angular diagnostic; physical pixel footprints, road labels and camera quality are unestablished.</p>
    <button className="button secondary" disabled={!catalog} onClick={load}>Load lens × sensor diagnostic template</button>
    {value.schema_version === "camera-e2e-lens-scene-spec/1" && <><div className="expansion-fields">
      {(["L1", "L2"] as const).map((slot) => <label className="expansion-field wide" key={slot}>{slot} · immutable Lens DB revision<select aria-label={`${slot} lens revision`} value={String(lenses[slot] ?? "")} onChange={(event) => update({ lenses: { ...lenses, [slot]: event.target.value } })}><option value="">Choose lens</option>{catalog?.lenses.map((item) => <option key={item.revision_id} value={item.revision_id}>{item.manufacturer} · {item.simulation_id} · {item.configuration}</option>)}</select></label>)}
      {(["S1", "S2"] as const).map((slot) => <label className="expansion-field wide" key={slot}>{slot} · saved sensor snapshot<select aria-label={`${slot} saved sensor`} value={validObject(sensors[slot]) ? String(sensors[slot].run_id ?? "") : ""} onChange={(event) => { const selected = catalog?.sensors.find((item) => item.run_id === event.target.value); update({ sensors: { ...sensors, [slot]: { identifier: selected?.report_code ?? "", run_id: selected?.run_id ?? "" } } }); }}><option value="">Choose saved sensor</option>{catalog?.sensors.map((item) => <option key={item.run_id} value={item.run_id}>{item.report_code} · {item.device_name} · {item.run_id.slice(0, 10)}</option>)}</select></label>)}
      {([['exposure_s', 'Exposure · seconds', 0.000001, 1, 0.001], ['luminance_cd_m2', 'Scene luminance · cd/m²', 0.01, 10000, 100], ['scene_horizontal_fov_deg', 'Common input horizontal FOV · degrees', 0.001, 30, 0.01]] as const).map(([key, label, min, max, step]) => <label className="expansion-field" key={key}>{label}<input aria-label={label} type="number" min={min} max={max} step={step} value={Number(scene[key] ?? 0)} onChange={(event) => update({ scene: { ...scene, [key]: Number(event.target.value) } })} /></label>)}
      <label className="expansion-field">Sensor noise<select aria-label="Native sensor noise" value={Number(scene.noise_flag ?? 0)} onChange={(event) => update({ scene: { ...scene, noise_flag: Number(event.target.value) } })}><option value="0">Disabled · deterministic diagnostic</option><option value="2">Native sensor noise enabled</option></select></label>
    </div><p className="expansion-clock">Four native RAW jobs then one fixed ISP v2 evaluation. Default crop: 32 × 32 pixels; common source: 256 × 256 samples. These are bounded native outputs, not enlarged full camera images. A missing valid common grid remains unevaluated.</p>
    <label className="expansion-note" style={{ display: "flex", gap: 10, alignItems: "flex-start" }}><input aria-label="Acknowledge lens sensor simulation limits" type="checkbox" checked={value.acknowledge_simulation_limits === true} style={{ width: "auto", marginTop: 5 }} onChange={(event) => update({ acknowledge_simulation_limits: event.target.checked })} />I acknowledge unity lens radiometry, geometric PSF and estimated sensor scope; this experiment cannot validate physical camera quality or road labels.</label>
    <details><summary>Fixed native ISP profile</summary><pre className="expansion-json">{JSON.stringify(value.profile, null, 2)}</pre></details></>}
  </section>;
}

type PointMap = { coordinates_deg_row_col_by_wavelength: number[][][][]; coordinate_convention: string; model: string };
type LensDiagnostic = { contract: { scope: string }; native_coordinates: Record<string, { array_sha256: string; contract: PointMap }>; common_angular_diagnostic: { available: boolean; grid: { row_angle_deg: number[]; column_angle_deg: number[]; shape_row_col: number[] } | null; per_slot: Record<string, { mtf50_cycles_per_degree_object_angle: number }>; pairs: { from: string; to: string; change: string; delta_mtf50_cycles_per_degree_object_angle: number; baseline_slot?: string; candidate_slot?: string; changed_factor?: string }[]; reasons: string[] } };
function LensSceneVisualization({ diagnostic }: { diagnostic: LensDiagnostic }) {
  const [slot, setSlot] = useState("L1S1"), [row, setRow] = useState(16), [col, setCol] = useState(16);
  const maps = diagnostic.native_coordinates, selected = maps[slot], coordinates = selected?.contract.coordinates_deg_row_col_by_wavelength[1] ?? [], point = coordinates[Math.min(row, coordinates.length - 1)]?.[Math.min(col, (coordinates[0]?.length ?? 1) - 1)];
  const all = Object.values(maps).flatMap((item) => item.contract.coordinates_deg_row_col_by_wavelength[1].flat()), bound = Math.max(0.001, ...all.flatMap((item) => item.map(Math.abs)));
  const x = (a: number) => 350 + a / bound * 130, y = (a: number) => 160 + a / bound * 130;
  const colors = ["#4b7844", "#5682ad", "#aa7841", "#8072ab"], common = diagnostic.common_angular_diagnostic;
  return <section className="expansion-panel" aria-label="Native lens sensor point correspondence"><h2>Native pixel-center correspondence · 550 nm</h2><p className="expansion-note warning">Each dot is an executed model pixel center before broadband PSF integration. The common grid compares a central angular region with antialiasing; it does not establish matched physical footprints or perception-label ground truth.</p>
    <svg className="expansion-trace" role="img" aria-label="Native lens sensor angular point maps" viewBox="0 0 700 320"><line x1="200" y1="160" x2="500" y2="160" stroke="#d4ded0" /><line x1="350" y1="10" x2="350" y2="310" stroke="#d4ded0" />
      {Object.entries(maps).map(([name, item], i) => <g key={name} opacity={name === slot ? 1 : .3}>{item.contract.coordinates_deg_row_col_by_wavelength[1].filter((_, r) => r % 4 === 0).flatMap((r, ri) => r.filter((_, c) => c % 4 === 0).map((p, ci) => <circle key={`${ri}-${ci}`} cx={x(p[1])} cy={y(p[0])} r="2" fill={colors[i]} />))}</g>)}
      {common.available && common.grid && <rect x={x(common.grid.column_angle_deg[0])} y={y(common.grid.row_angle_deg[0])} width={x(common.grid.column_angle_deg[1]) - x(common.grid.column_angle_deg[0])} height={y(common.grid.row_angle_deg[1]) - y(common.grid.row_angle_deg[0])} fill="none" stroke="#304e35" strokeWidth="2" strokeDasharray="4 3" />}
      {point && <circle cx={x(point[1])} cy={y(point[0])} r="5" fill="none" stroke="#c73535" strokeWidth="2" />}<text x="520" y="157" fontSize="10">column angle · degrees</text><text x="356" y="300" fontSize="10">row angle · degrees (down)</text></svg>
    <div className="expansion-legend">{Object.keys(maps).map((name, i) => <span key={name}><i style={{ background: colors[i] }} />{name}</span>)}</div><div className="expansion-fields"><label className="expansion-field">Inspect native slot<select aria-label="Native lens sensor slot" value={slot} onChange={(event) => setSlot(event.target.value)}>{Object.keys(maps).map((name) => <option key={name}>{name}</option>)}</select></label><label className="expansion-field">Pixel row<input aria-label="Native point row" type="range" min="0" max={Math.max(0, coordinates.length - 1)} value={Math.min(row, coordinates.length - 1)} onChange={(event) => setRow(Number(event.target.value))} /></label><label className="expansion-field">Pixel column<input aria-label="Native point column" type="range" min="0" max={Math.max(0, (coordinates[0]?.length ?? 1) - 1)} value={Math.min(col, (coordinates[0]?.length ?? 1) - 1)} onChange={(event) => setCol(Number(event.target.value))} /></label></div>
    <p className="expansion-clock">Selected point: {point?.map((v) => v.toFixed(9)).join(", ")} degrees (row, column)<br />Original native array SHA-256: {selected?.array_sha256}<br />Coordinates for all wavelengths, native RGB, common RGB and valid masks are preserved in the native downloads.</p>
    <h3>Common angular edge diagnostic · cycles/degree</h3>{common.available ? <><table className="expansion-table"><thead><tr><th>Slot</th><th>Central model MTF50</th><th>Evaluation scope</th></tr></thead><tbody>{Object.entries(common.per_slot).map(([name, item]) => <tr key={name}><td>{name}</td><td>{item.mtf50_cycles_per_degree_object_angle.toFixed(5)}</td><td>Simulated point-grid sensitivity</td></tr>)}</tbody></table><p className="expansion-clock">Common grid: {common.grid?.shape_row_col.join(" × ")} · source map wavelength 550 nm · physical validation not established</p><details><summary>Lens-only and sensor-only diagnostic deltas</summary><pre className="expansion-json">{JSON.stringify(common.pairs, null, 2)}</pre></details></> : <p className="expansion-note warning">Unevaluated common angular comparison: {common.reasons.join(" ")}</p>}
  </section>;
}

function LensSceneEvidence({ execution }: { execution: Execution }) {
  const [diagnostic, setDiagnostic] = useState<LensDiagnostic | null>(null), [error, setError] = useState("");
  const artifact = execution.artifacts.find((item) => item.name === execution.summary?.correspondence_artifact_name);
  useEffect(() => {
    setDiagnostic(null); setError(""); if (!artifact) return;
    let active = true; const controller = new AbortController();
    async function load() {
      const expected = `/api/v1/experiments/executions/${encodeURIComponent(execution.id)}/artifacts/${encodeURIComponent(artifact!.id)}`;
      if (artifact!.download_url !== expected || artifact!.media_type !== "application/json" || artifact!.size_bytes > 2 * 1024 * 1024) throw new Error("Native correspondence JSON exceeds the accepted identity/size contract.");
      const response = await fetch(expected, { headers: authHeaders(), signal: controller.signal });
      if (!response.ok) throw new Error(`Native correspondence read failed (${response.status}).`);
      const raw = await response.arrayBuffer();
      if (raw.byteLength !== artifact!.size_bytes || raw.byteLength > 2 * 1024 * 1024) throw new Error("Native coordinate byte size differs from its pinned artifact.");
      const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", raw))).map((v) => v.toString(16).padStart(2, "0")).join("");
      if (hash !== artifact!.sha256) throw new Error("Native coordinate SHA-256 mismatch.");
      const value = JSON.parse(new TextDecoder().decode(raw));
      if (value.schema !== "camera_lens_sensor_native_diagnostic_export_v1" || value.contract?.physical_validation !== "not_established" || value.contract?.product_ranking_allowed !== false) throw new Error("Native coordinate scope differs from the accepted model diagnostic.");
      if (active) setDiagnostic(value as LensDiagnostic);
    }
    load().catch((reason: Error) => { if (active) setError(reason.message); });
    return () => { active = false; controller.abort(); };
  }, [execution.id, artifact?.id, artifact?.sha256]);
  return error ? <p role="alert" className="expansion-error">{error}</p> : diagnostic ? <><p className="expansion-clock">Native correspondence JSON · original SHA-256 verified</p><LensSceneVisualization diagnostic={diagnostic} /></> : <p className="expansion-note">Loading retained native coordinate evidence…</p>;
}

function ObjectObserverEvidence({ summary }: { summary: Record<string, unknown> }) {
  const observers = Array.isArray(summary.observers) ? summary.observers.filter(validObject) : [];
  const verdict = typeof summary.verdict === "string" ? summary.verdict : "not_evaluated";
  return <section className="expansion-panel" aria-label="Object simulation observer evaluation"><h2>객체 시험 기준 판정</h2><div className="expansion-actions"><Badge status={verdict} /><span className="expansion-clock">평가 상태: {String(summary.evaluation_status ?? "미확인")} · 보존 표본 {String(summary.sample_count ?? "미확인")}개</span></div>
    <p className="expansion-note">이 판정은 저장된 엔진 관측과 고정 시험 기준의 결과입니다. 실행 완료와 기준 통과를 구분하며, schematic 재생만으로 센서 품질이나 도로 안전을 검증하지 않습니다.</p>
    {validObject(summary.observer_counts) && <div className="expansion-legend">{Object.entries(summary.observer_counts).map(([key, value]) => <span key={key}>{key} · {String(value)}</span>)}</div>}
    {observers.length > 0 ? <div className="sensor-observer-table"><table className="expansion-table"><thead><tr><th>시험 기준 · 객체</th><th>관측값</th><th>상태 · 사유</th></tr></thead><tbody>{observers.map((item, index) => {
      const observer = validObject(item.observer) ? item.observer : {};
      return <tr key={index}><td>{String(observer.kind ?? "미확인")} · {String(observer.actor_id ?? "미확인")}{observer.threshold !== null && observer.threshold !== undefined && <small className="sensor-observer-threshold">기준: {String(observer.threshold)}</small>}</td><td>{item.value === null || item.value === undefined ? "—" : typeof item.value === "object" ? JSON.stringify(item.value) : String(item.value)} {String(item.units ?? "")}</td><td>{String(item.status ?? "unevaluated")}{item.reason ? ` · ${String(item.reason)}` : ""}</td></tr>;
    })}</tbody></table></div> : <p className="expansion-note warning">보존된 기준별 관측이 없습니다. 일반 통과로 처리하지 않습니다.</p>}
    <p className="expansion-clock">시나리오: {String(summary.scenario_id ?? "미확인")} · 개정 {String(summary.scenario_revision ?? "미확인")}</p>
  </section>;
}
type DrivingOutput = { sample_token: string; timestamp_us: number; coordinate_frame: string; step_seconds: number; modes: { right: [number, number][]; left: [number, number][]; straight: [number, number][] } };
function DrivingVisualization({ outputs }: { outputs: DrivingOutput[] }) {
  const [index, setIndex] = useState(0);
  useEffect(() => setIndex(0), [outputs]);
  if (!outputs.length) return null;
  const output = outputs[Math.min(index, outputs.length - 1)], points = Object.values(output.modes).flat();
  const xmin = Math.min(0, ...points.map((point) => point[0])), xmax = Math.max(0, ...points.map((point) => point[0]));
  const ymin = Math.min(0, ...points.map((point) => point[1])), ymax = Math.max(0, ...points.map((point) => point[1]));
  const scale = Math.min(600 / Math.max(6, xmax - xmin + 2), 260 / Math.max(6, ymax - ymin + 2));
  const x = (value: number) => 350 + (value - (xmin + xmax) / 2) * scale;
  const y = (value: number) => 160 - (value - (ymin + ymax) / 2) * scale;
  const colors = { right: "#aa7841", left: "#8072ab", straight: "#3e7945" };
  return <section className="expansion-panel"><h2>Native learned policy · all trajectory modes</h2>
    <p className="expansion-note warning">Isolated noncommercial research. These native predictions are open loop in the current LiDAR XY frame; they are not global/map positions, a reactive camera view or a safety verdict. All modes are retained without future-ground-truth route selection.</p>
    <svg className="expansion-trace" role="img" aria-label="Native driving policy trajectory modes" viewBox="0 0 700 320"><line x1="20" y1={y(0)} x2="680" y2={y(0)} stroke="#d4ded0" /><line x1={x(0)} y1="10" x2={x(0)} y2="310" stroke="#d4ded0" /><circle cx={x(0)} cy={y(0)} r="4" fill="#304e35" />
      {Object.entries(output.modes).map(([name, trajectory]) => <g key={name}><polyline points={[[0, 0], ...trajectory].map((point) => `${x(point[0])},${y(point[1])}`).join(" ")} fill="none" stroke={colors[name as keyof typeof colors]} strokeWidth="2.5" />{trajectory.map((point, step) => <circle key={step} cx={x(point[0])} cy={y(point[1])} r="3" fill={colors[name as keyof typeof colors]} />)}</g>)}
      <text x="665" y={y(0) - 5} textAnchor="end" fontSize="10" fill="#556b4d">Native X · m</text><text x={x(0) + 8} y="20" fontSize="10" fill="#556b4d">Native Y · m</text>
    </svg><div className="expansion-legend">{Object.entries(colors).map(([name, color]) => <span key={name}><i style={{ background: color }} />{name} · 6 predicted points / {output.step_seconds}s</span>)}</div>
    <div className="expansion-scrubber"><input aria-label="Driving policy input timeline" type="range" min="0" max={outputs.length - 1} value={index} onChange={(event) => setIndex(Number(event.target.value))} /><span>Input {index + 1} / {outputs.length}</span></div>
    <p className="expansion-clock">Original timestamp: {output.timestamp_us} µs · sample: {output.sample_token}<br />Output frame: {output.coordinate_frame}</p>
    <table className="expansion-table"><thead><tr><th>Mode</th><th>Prediction horizon</th><th>Final native XY · m</th><th>Quality / safety evaluation</th></tr></thead><tbody>{Object.entries(output.modes).map(([name, trajectory]) => <tr key={name}><td>{name}</td><td>{trajectory.length * output.step_seconds}s</td><td>{trajectory.at(-1)?.map((value) => value.toFixed(3)).join(", ")}</td><td>Unevaluated open loop</td></tr>)}</tbody></table>
  </section>;
}


function NativeImagePreview({ executionId, artifact }: { executionId: string; artifact: Artifact }) {
  const [source, setSource] = useState(""), [error, setError] = useState(""), [shape, setShape] = useState<number[]>([]);
  useEffect(() => {
    if (artifact.media_type !== "image/png" && artifact.media_type !== "image/jpeg" || artifact.size_bytes > 16 * 1024 * 1024) return;
    let active = true, objectUrl = "";
    const controller = new AbortController();
    const expectedPath = `/api/v1/experiments/executions/${encodeURIComponent(executionId)}/artifacts/${encodeURIComponent(artifact.id)}`;
    setSource(""); setError(""); setShape([]);
    async function load() {
      if (artifact.download_url !== expectedPath) throw new Error("Image artifact uses an unexpected download route.");
      const response = await fetch(expectedPath, { headers: authHeaders(), signal: controller.signal });
      if (!response.ok) throw new Error(`Image preview failed (${response.status}).`);
      const raw = await response.arrayBuffer();
      if (raw.byteLength !== artifact.size_bytes || raw.byteLength > 16 * 1024 * 1024) throw new Error("Image artifact exceeds its pinned byte bound.");
      const actualHash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", raw))).map((value) => value.toString(16).padStart(2, "0")).join("");
      if (actualHash !== artifact.sha256) throw new Error("Image artifact SHA-256 mismatch.");
      if (active) { objectUrl = URL.createObjectURL(new Blob([raw], { type: artifact.media_type })); setSource(objectUrl); }
    }
    load().catch((reason: Error) => { if (active) setError(reason.message); });
    return () => { active = false; controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [executionId, artifact.id, artifact.download_url, artifact.media_type, artifact.sha256, artifact.size_bytes]);
  return source ? <figure style={{ margin: 0 }}><img src={source} alt={`Retained native artifact ${artifact.name}`} onLoad={(event) => setShape([event.currentTarget.naturalWidth, event.currentTarget.naturalHeight])} style={{ width: "100%", maxHeight: 280, objectFit: "contain", borderRadius: 8, imageRendering: "pixelated" }} /><figcaption className="expansion-clock">{artifact.name} · {shape.length ? `${shape.join(" × ")} 원본 픽셀 · ` : ""} SHA-256 verified</figcaption></figure> : error ? <p role="alert" className="expansion-error">{error}</p> : null;
}

function NativeImageGallery({ execution, executionId, artifacts, manifestHash, summary, onDownload }: { execution: Execution; executionId: string; artifacts: Artifact[]; manifestHash?: string | null; summary?: Record<string, unknown> | null; onDownload?: (artifact: Artifact) => void }) {
  const images = artifacts.filter((item) => item.media_type === "image/png" || item.media_type === "image/jpeg").sort((left, right) => left.name.localeCompare(right.name));
  const [selected, setSelected] = useState(""), [stage, setStage] = useState("native_fixed_isp");
  useEffect(() => { setSelected(""); setStage("native_fixed_isp"); }, [executionId]);
  if (summary?.schema_version === "carla-feedback-sensor-result/1") return <CarlaFeedbackViewer executionId={executionId} artifacts={artifacts} manifestHash={manifestHash} summary={summary} onDownload={onDownload} />;
  if (summary?.schema_version === "sensor-experiment-result/1" && validObject(summary.captures)) return <CarlaMultisensorViewer execution={execution} onDownload={onDownload} />;
  if (!images.length) return null;
  const artifact = images.find((item) => item.id === selected) ?? images.find((item) => /baseline.*rgb-000/.test(item.name)) ?? images[0];
  const lens = summary?.schema_version === "camera-e2e-lens-scene-result/1";
  const previews = Array.isArray(summary?.image_previews) ? summary.image_previews.filter(validObject).filter((item) => item.stage === stage).flatMap((item) => {
    const original = validObject(item.artifact) ? item.artifact : null;
    const saved = artifacts.find((candidate) => candidate.name === item.local_name && original?.sha256 === candidate.sha256);
    return saved ? [{ slot: String(item.slot), artifact: saved }] : [];
  }) : [];
  const raw = artifacts.filter((item) => /original-raw\.npy$/.test(item.name));
  return <section className="expansion-panel sensor-image-panel" aria-label="Retained native image viewer"><h2>{lens ? "렌즈 × 센서 · 저장된 ISP 영상 비교" : "저장된 센서 영상"}</h2>
    <p className="expansion-note">{lens ? "L1/L2 렌즈와 S1/S2 센서 조합을 실행한 원본 출력입니다. 작은 중앙 경사 에지 영상이며, 화면 확대는 해상도 증가나 실측 카메라 품질을 뜻하지 않습니다." : "저장된 원본 픽셀을 검사합니다. 시뮬레이션·추정·실측 여부는 실행 어댑터와 fidelity를 확인하세요."}</p>
    {lens && <><div className="sensor-stage-tabs" aria-label="Lens sensor image comparison stage"><button className={`button ${stage === "native_fixed_isp" ? "primary" : "secondary"}`} aria-pressed={stage === "native_fixed_isp"} onClick={() => setStage("native_fixed_isp")}>원래 격자 · 고정 ISP</button><button className={`button ${stage === "common_550nm_point_grid" ? "primary" : "secondary"}`} aria-pressed={stage === "common_550nm_point_grid"} onClick={() => setStage("common_550nm_point_grid")}>공통 각도 격자 · 550 nm</button></div>
      <div className="sensor-slot-images">{previews.map((item) => <div key={item.artifact.id} className="sensor-slot-card"><strong>{item.slot}</strong><NativeImagePreview executionId={executionId} artifact={item.artifact} /></div>)}</div>
      {!previews.length && <p className="expansion-note warning">이 단계의 보존된 영상이 없습니다. 실행 파일과 미평가 사유를 확인하세요.</p>}
      {raw.length > 0 && <div className="sensor-raw-downloads"><h3>처리 전 RAW 원본</h3><p className="expansion-clock">수치 배열(.npy)로 보존합니다. 아래 영상은 고정 ISP 처리 결과이며, RAW를 사진으로 복원했다고 표시하지 않습니다.</p><div className="expansion-actions">{raw.map((item) => <button key={item.id} className="button secondary" onClick={() => onDownload?.(item)} disabled={!onDownload}>{item.name.split("-")[0]} RAW 다운로드</button>)}</div></div>}
    </>}
    <details><summary>{lens ? "개별 영상 크게 보기 · 원본 파일 선택" : "개별 영상 파일 선택"}</summary><label className="expansion-field">Image artifact<select aria-label="Native image artifact" value={artifact.id} onChange={(event) => setSelected(event.target.value)}>{images.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><NativeImagePreview executionId={executionId} artifact={artifact} /></details>
    {!lens && <NativeImagePreview executionId={executionId} artifact={artifact} />}
  </section>;
}

function ReplayVisualization({ ticks, total, truncated }: { ticks: Tick[]; total: number; truncated: boolean }) {
  const [index, setIndex] = useState(0), [playing, setPlaying] = useState(false), [showLabels, setShowLabels] = useState(false);
  useEffect(() => { setIndex(0); setPlaying(false); }, [ticks]);
  useEffect(() => { if (!playing || ticks.length < 2) return; const timer = setInterval(() => setIndex((current) => { if (current + 1 >= ticks.length) { setPlaying(false); return current; } return current + 1; }), 300); return () => clearInterval(timer); }, [playing, ticks.length]);
  const bounds = useMemo(() => {
    const points = ticks.flatMap((tick) => [tick.ego, tick.logged_ego, ...(tick.world_objects ?? [])]);
    const xmin = Math.min(...points.map((point) => point.x_m)), xmax = Math.max(...points.map((point) => point.x_m));
    const ymin = Math.min(...points.map((point) => point.y_m)), ymax = Math.max(...points.map((point) => point.y_m));
    const width = Math.max(xmax - xmin + 12, 24), height = Math.max(ymax - ymin + 12, width * .48);
    return { cx: (xmin + xmax) / 2, cy: (ymin + ymax) / 2, width, height };
  }, [ticks]);
  if (!ticks.length) return null;
  const tick = ticks[Math.min(index, ticks.length - 1)], scale = Math.min(700 / bounds.width, 300 / bounds.height);
  const x = (value: number) => 350 + (value - bounds.cx) * scale, y = (value: number) => 160 - (value - bounds.cy) * scale;
  const vehicle = (pose: Pose, color: string, opacity = 1, id?: string) => <g key={id ?? color} transform={`translate(${x(pose.x_m)} ${y(pose.y_m)}) rotate(${-pose.yaw_rad * 180 / Math.PI})`} opacity={opacity}>{id && <title>{id}</title>}<rect x={-pose.length_m * scale / 2} y={-pose.width_m * scale / 2} width={pose.length_m * scale} height={pose.width_m * scale} rx="2" fill={color} /><path d={`M ${pose.length_m * scale / 2} 0 l -5 -3 v 6 Z`} fill="#fff" />{id && showLabels && <text x="0" y={-pose.width_m * scale / 2 - 5} textAnchor="middle" fontSize="10" fill="#364e41">{id.length > 8 ? `${id.slice(0, 8)}…` : id}</text>}</g>;
  const chart = ticks.map((item) => ({ step: item.index, speed: item.ego.speed_mps, error: typeof item.metrics.trajectory_error?.value === "number" ? item.metrics.trajectory_error.value : null }));
  return <section className="expansion-panel"><h2>Temporal evidence · reference vehicle feedback</h2>
    <svg className="expansion-trace" role="img" aria-label="Replay trajectory and object observations" viewBox="0 0 700 320">
      <defs><pattern id="replay-grid" width={5 * scale} height={5 * scale} patternUnits="userSpaceOnUse"><path d={`M ${5 * scale} 0 L 0 0 0 ${5 * scale}`} fill="none" stroke="#e1e6da" strokeWidth="1" /></pattern></defs>
      <rect width="700" height="320" fill="url(#replay-grid)" /><polyline points={ticks.map((item) => `${x(item.logged_ego.x_m)},${y(item.logged_ego.y_m)}`).join(" ")} fill="none" stroke="#adb7a3" strokeWidth="2" strokeDasharray="5 4" />
      <polyline points={ticks.slice(0, index + 1).map((item) => `${x(item.ego.x_m)},${y(item.ego.y_m)}`).join(" ")} fill="none" stroke="#4a7844" strokeWidth="2.5" />
      {vehicle(tick.logged_ego, "#aeb9a2", .45)}{tick.world_objects?.map((obj) => vehicle(obj, "#527eaa", 1, obj.id))}{vehicle(tick.ego, "#3d6e39")}
    </svg>
    <div className="expansion-legend"><span><i style={{ background: "#3d6e39" }} />Replay ego</span><span><i style={{ background: "#aeb9a2" }} />Original retained ego</span><span><i style={{ background: "#527eaa" }} />Nonreactive world objects</span></div>
    <div className="expansion-actions"><label className="expansion-note" style={{ display: "flex", alignItems: "center", gap: 8 }}><input aria-label="Show replay object labels" type="checkbox" checked={showLabels} onChange={(event) => setShowLabels(event.target.checked)} style={{ width: "auto" }} />Show short object labels</label><span className="expansion-clock">{tick.world_objects === null ? "Object observation unavailable; the world is not assumed empty." : `Objects shown: ${tick.world_objects.length} / ${tick.object_count ?? tick.world_objects.length} retained · hover for the full source ID`}</span></div>
    <div className="expansion-scrubber"><button className="button secondary" onClick={() => { if (index === ticks.length - 1) setIndex(0); setPlaying(!playing); }}>{playing ? "Pause" : "Play"}</button><input aria-label="Replay timeline" type="range" min="0" max={ticks.length - 1} value={index} onChange={(event) => { setPlaying(false); setIndex(Number(event.target.value)); }} /><span>Step {tick.index + 1} / {total}</span></div>
    <p className="expansion-clock">Original decision timestamp: {tick.at_ns} ns · control: {tick.control?.decision ?? "original replay"}</p>
    {(truncated || tick.objects_preview_truncated) && <p className="expansion-note">Preview is bounded. The full exact observations and trace are in the native artifact download.</p>}
    <div className="expansion-chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={chart}><CartesianGrid strokeDasharray="3 3" stroke="#e2e7dc" /><XAxis dataKey="step" fontSize="10" /><YAxis yAxisId="speed" fontSize="10" unit="m/s" /><YAxis yAxisId="error" orientation="right" fontSize="10" unit="m" /><Tooltip /><Line yAxisId="speed" dataKey="speed" name="Ego speed" stroke="#487a43" dot={false} isAnimationActive={false} /><Line yAxisId="error" dataKey="error" name="Error vs logged ego" stroke="#ad7d39" dot={false} isAnimationActive={false} /></LineChart></ResponsiveContainer></div>
    <table className="expansion-table"><thead><tr><th>Observer</th><th>Value</th><th>Evaluation state</th></tr></thead><tbody>{Object.entries(tick.metrics).map(([name, metric]) => <tr key={name}><td>{name.replaceAll("_", " ")}</td><td>{metric.value === null ? "—" : typeof metric.value === "number" ? metric.value.toFixed(3) : String(metric.value)} {metric.value === null ? "" : metric.unit}</td><td>{metric.status}{metric.reason ? ` · ${metric.reason}` : ""}</td></tr>)}</tbody></table>
  </section>;
}

export default function Experiments({ displayMode = "author", kindFilter, title, description, executionId }: ExperimentsProps = {}) {
  const initial = useRef(new URLSearchParams(location.hash.split("?")[1] ?? ""));
  const incomingKind = initial.current.get("kind");
  const [kind, setKind] = useState<Kind>(incomingKind && incomingKind in labels && (!kindFilter || kindFilter.includes(incomingKind as Kind)) ? incomingKind as Kind : kindFilter?.[0] ?? "planning_replay");
  const [project, setProject] = useState(initial.current.get("project") ?? "default"), [name, setName] = useState(initial.current.get("adapter") === "carla-feedback-sensor" ? "CARLA feedback · 주행 카메라" : "Development experiment"), [seed, setSeed] = useState(42);
  const [handlerId, setHandlerId] = useState(initial.current.get("adapter") ?? ""), [input, setInput] = useState(() => JSON.stringify(initial.current.get("adapter") === "carla-feedback-sensor" ? feedbackInputTemplate() : initial.current.get("replay") ? { replay_spec_id: initial.current.get("replay") } : initial.current.get("driving") ? { driving_sequence_id: initial.current.get("driving"), confirm_noncommercial_research: false } : {}, null, 2));
  const [comparison, setComparison] = useState("single"), [model, setModel] = useState(""), [sensor, setSensor] = useState(""), [evaluator, setEvaluator] = useState("");
  const projects = useResource<{ items: Project[] }>("/projects"), identity = useResource<{ permissions: { execute: boolean } }>("/team/whoami");
  const capabilities = useResource<{ handlers: Handler[] }>("/experiments/capabilities");
  const externalViewer = isExternalViewer();
  const lensCatalog = useResource<{ catalog: LensCatalog }>(!externalViewer && handlerId === "camera-e2e-lens-sensor-scene" && project === "default" ? "/camera-bridge/scene-catalog?project_id=default" : null);
  const [olderExecutions, setOlderExecutions] = useState<ExecutionListing[]>([]), [nextBefore, setNextBefore] = useState<number | null>(null);
  const executionHistory = useResource<{ items: ExecutionListing[]; next_before_seq: number | null }>(displayMode === "results" ? `/experiments/executions?project_id=${encodeURIComponent(project)}&limit=100` : null);
  const filterSignature = kindFilter?.join(",") ?? "all";
  const [resultKind, setResultKind] = useState<Kind | "all">("all"), [resultStatus, setResultStatus] = useState("all");
  const selectionIntent = useRef(0), automaticProject = useRef("");
  const history = useResource<{ items: { id: string; name: string; kind: Kind }[] }>(`/experiments?project_id=${encodeURIComponent(project)}`);
  const replays = useResource<{ items: { id: string; name: string; mode: string }[] }>(`/replay-specs?project_id=${encodeURIComponent(project)}`);
  const driving = useResource<{ items: { id: string; name: string; frame_count: number; camera_observations: number }[] }>(kind === "driving_open_loop" && project === "default" ? "/driving-sequences" : null);
  const [drivingAgree, setDrivingAgree] = useState(false), [drivingImportPath, setDrivingImportPath] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null), [created, setCreated] = useState<Created | null>(null), [execution, setExecution] = useState<Execution | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const createKey = useRef(crypto.randomUUID()), submitKey = useRef(crypto.randomUUID());
  const handlers = capabilities.data?.handlers.filter((item) => item.kinds.includes(kind)) ?? [], handler = handlers.find((item) => item.id === handlerId);
  const canExecute = identity.data?.permissions.execute ?? false;
  const resultRows = [...(executionHistory.data?.items ?? []), ...olderExecutions]
    .filter((item) => !kindFilter || kindFilter.includes(item.experiment_kind))
    .map((item) => execution?.id === item.id && execution.project_id === project ? {
      ...item, status: execution.status, fidelity: execution.fidelity,
      manifest_hash: execution.manifest_hash, input_hash: execution.input_hash,
    } : item);
  const shownRows = resultRows.filter((item) => (resultKind === "all" || resultKind === item.experiment_kind) && (resultStatus === "all" || resultStatus === item.status));
  const signature = JSON.stringify([project, kind, name, seed, handlerId, input, comparison, model, sensor, evaluator]);
  useEffect(() => { setPreview(null); if (displayMode !== "results") setCreated(null); createKey.current = crypto.randomUUID(); submitKey.current = crypto.randomUUID(); }, [signature]);
  useEffect(() => { if (projects.data?.items.length && !projects.data.items.some((item) => item.id === project)) setProject(projects.data.items[0].id); }, [project, projects.data]);
  useEffect(() => { const options = capabilities.data?.handlers.filter((item) => item.kinds.includes(kind)) ?? []; if (options.length && !options.some((item) => item.id === handlerId)) setHandlerId((options.find((item) => item.status === "available") ?? options[0]).id); }, [kind, handlerId, capabilities.data]);
  useEffect(() => { selectionIntent.current++; setExecution(null); setCreated(null); setOlderExecutions([]); setNextBefore(null); automaticProject.current = ""; }, [project, filterSignature]);
  useEffect(() => { if (executionHistory.data) setNextBefore(executionHistory.data.next_before_seq); }, [executionHistory.data]);
  useEffect(() => {
    const id = executionId ?? ((initial.current.get("project") ?? "default") === project ? initial.current.get("execution") : null); if (!id) return;
    let active = true; const intent = ++selectionIntent.current;
    api<Execution>(`/experiments/executions/${encodeURIComponent(id)}`).then(async (value) => {
      if (value.project_id !== project) throw new Error("이 실행은 선택한 프로젝트에 속하지 않습니다.");
      if (kindFilter && !kindFilter.includes(value.experiment_kind)) throw new Error("이 실행은 현재 Viewer의 결과 종류와 다릅니다. Experiments에서 확인해주세요.");
      const saved = await api<Created>(`/experiments/${encodeURIComponent(value.experiment_id)}`);
      if (saved.project_id !== project) throw new Error("고정 실험의 프로젝트가 선택한 프로젝트와 다릅니다.");
      if (active && intent === selectionIntent.current) { setExecution(value); setCreated(saved); setKind(value.experiment_kind); setHandlerId(value.adapter_id); }
    }).catch((reason: Error) => { if (active && intent === selectionIntent.current) setError(reason.message); });
    return () => { active = false; };
  }, [executionId, filterSignature, project]);
  useEffect(() => {
    if (displayMode !== "results" || executionId || ((initial.current.get("project") ?? "default") === project && initial.current.get("execution")) || !executionHistory.data || automaticProject.current === `${project}:${filterSignature}`) return;
    automaticProject.current = `${project}:${filterSignature}`;
    const rows = executionHistory.data.items.filter((item) => !kindFilter || kindFilter.includes(item.experiment_kind));
    const latest = rows.find((item) => item.status === "completed") ?? rows[0];
    if (latest) void openExecution(latest.id);
  }, [displayMode, executionHistory.data, project, filterSignature, executionId]);
  useEffect(() => {
    if (!execution || terminal.has(execution.status)) return;
    let active = true;
    const timer = setInterval(() => { api<Execution>(`/experiments/executions/${encodeURIComponent(execution.id)}`).then((value) => { if (active) setExecution(value); }).catch((reason: Error) => { if (active) setError(reason.message); }); }, 1500);
    return () => { active = false; clearInterval(timer); };
  }, [execution?.id, execution?.status]);

  function body() { if (!handler) throw new Error("Select a configured handler."); return { project_id: project, name, kind, adapter_id: handler.id, adapter_version: handler.version, input: jsonObject(input, "Input"), seed,
    comparison_kind: comparison, ...(model.trim() ? { model: jsonObject(model, "Model") } : {}), ...(sensor.trim() ? { sensor: jsonObject(sensor, "Sensor") } : {}), ...(evaluator.trim() ? { evaluator: jsonObject(evaluator, "Evaluator") } : {}) }; }
  async function perform(action: () => Promise<void>) { setBusy(true); setError(""); setNotice(""); try { await action(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Experiment request failed"); } finally { setBusy(false); } }
  async function save() { await perform(async () => { if (!preview) throw new Error("Preview the exact input first."); const value = await api<Created>("/experiments", { method: "POST", headers: { "Idempotency-Key": createKey.current }, body: JSON.stringify(body()) }); if (value.preview_hash !== preview.preview_hash) throw new Error("Inputs changed after preview. Refresh the preview before execution."); setCreated(value); history.reload(); setNotice("Immutable experiment saved. Explicitly submit the previewed job when ready."); }); }
  async function submit() { await perform(async () => { if (!created) throw new Error("Save the previewed experiment first."); setExecution(await api<Execution>(`/experiments/${encodeURIComponent(created.id)}/executions`, { method: "POST", headers: { "Idempotency-Key": submitKey.current }, body: JSON.stringify({ preview_hash: created.preview_hash }) })); setNotice("Submitted to the durable worker queue. Execution completion and evaluation verdict are separate."); }); }
  async function open(id: string) { await perform(async () => { const value = await api<Created>(`/experiments/${encodeURIComponent(id)}`); setCreated(value); const runs = [...(value.executions ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at)); setExecution(runs[0] ?? null); setNotice("Loaded the saved immutable experiment."); }); }
  async function openExecution(id: string) {
    const intent = ++selectionIntent.current; setBusy(true); setError(""); setNotice("");
    try {
      const value = await api<Execution>(`/experiments/executions/${encodeURIComponent(id)}`);
      if (value.project_id !== project) throw new Error("선택한 실행은 현재 프로젝트에 속하지 않습니다.");
      if (kindFilter && !kindFilter.includes(value.experiment_kind)) throw new Error("선택한 실행은 이 Viewer에서 지원하지 않는 결과 종류입니다.");
      const saved = await api<Created>(`/experiments/${encodeURIComponent(value.experiment_id)}`);
      if (saved.project_id !== project) throw new Error("고정 실험의 프로젝트가 현재 프로젝트와 다릅니다.");
      if (intent === selectionIntent.current) {
        setExecution(value); setCreated(saved);
        const query = new URLSearchParams(location.hash.split("?")[1] ?? ""); query.set("project", project); query.set("execution", value.id);
        window.history.replaceState(null, "", `${location.pathname}${location.search}${location.hash.split("?")[0]}?${query}`);
      }
    } catch (reason) { if (intent === selectionIntent.current) setError(reason instanceof Error ? reason.message : "결과를 불러올 수 없습니다."); }
    finally { if (intent === selectionIntent.current) setBusy(false); }
  }
  async function moreResults() {
    if (!nextBefore) return;
    await perform(async () => {
      const page = await api<{ items: ExecutionListing[]; next_before_seq: number | null }>(`/experiments/executions?project_id=${encodeURIComponent(project)}&limit=100&before_seq=${nextBefore}`);
      setOlderExecutions((items) => { const ids = new Set(items.map((item) => item.id)); return [...items, ...page.items.filter((item) => !ids.has(item.id))]; }); setNextBefore(page.next_before_seq);
    });
  }
  async function download(path: string, filename: string, expectedHash: string, size?: number) {
    await perform(async () => {
      if (!execution) throw new Error("Select an execution first.");
      const url = new URL(path, location.origin), allowed = `/api/v1/experiments/executions/${encodeURIComponent(execution.id)}/`;
      if (url.origin !== location.origin || !url.pathname.startsWith(allowed) || url.search || url.hash) throw new Error("Download must use the selected execution's native API.");
      if (size && size > 256 * 1024 * 1024) throw new Error("This file exceeds the 256 MiB browser download bound. Use the SDK's streamed artifact download for this native file.");
      const response = await fetch(url, { headers: authHeaders() });
      if (!response.ok) throw new Error(`Download failed (${response.status}).`);
      const declared = Number(response.headers.get("Content-Length") ?? 0);
      if (declared > 256 * 1024 * 1024) throw new Error("Download exceeds the bounded browser memory profile.");
      const bytes = await response.arrayBuffer();
      if (bytes.byteLength > 256 * 1024 * 1024 || size !== undefined && bytes.byteLength !== size) throw new Error("Artifact byte size differs from the pinned descriptor.");
      const actualHash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))).map((value) => value.toString(16).padStart(2, "0")).join("");
      if (actualHash !== expectedHash) throw new Error("Artifact SHA-256 differs from the pinned result.");
      const objectUrl = URL.createObjectURL(new Blob([bytes], { type: response.headers.get("Content-Type") ?? "application/octet-stream" }));
      const anchor = document.createElement("a"); anchor.href = objectUrl; anchor.download = filename; anchor.click(); setTimeout(() => URL.revokeObjectURL(objectUrl), 2000);
      setNotice("Native evidence downloaded and SHA-256 verified.");
    });
  }
  const visualization = execution?.summary?.visualization;
  const retainedSource = execution ? retainedExecutionSource(execution.id, execution.manifest_hash) : null;
  const ticks = validObject(visualization) && Array.isArray(visualization.ticks) ? visualization.ticks as Tick[] : [];
  if (displayMode === "results") {
    const scope = kindFilter ?? Object.keys(labels) as Kind[];
    const complete = resultRows.filter((item) => item.status === "completed").length, failed = resultRows.filter((item) => item.status === "failed").length;
    const rawCount = execution?.artifacts.filter((item) => /original-raw\.npy$/.test(item.name)).length ?? 0;
    const images = execution?.artifacts.filter((item) => item.media_type.startsWith("image/")) ?? [];
    const seedValue = execution?.summary?.seed ?? created?.prepared.spec.seed;
    const isLens = execution?.summary?.schema_version === "camera-e2e-lens-scene-result/1";
    const route = location.hash.split("?")[0] || "#viewers";
    return <div className="expansion-workspace sensor-results-workspace">
      <PageHeading eyebrow="VIEWERS · SAVED EXECUTION EVIDENCE" title={title ?? "저장 결과 Viewer"} description={description ?? "실행된 결과를 선택해 영상·객체 궤적·시간축을 재생하고, 원래 파일과 계산 범위를 확인합니다."} />
      <div className="sensor-result-stats" aria-label="Loaded execution counts">{executionHistory.loading ? <span role="status">실행 목록 조회 중…</span> : executionHistory.error ? <span>실행 목록 조회 실패 · 아래 오류를 확인하세요.</span> : <><span>읽어온 실행 <strong>{resultRows.length}</strong></span><span>실행 완료 <strong>{complete}</strong></span><span>실행 실패 <strong>{failed}</strong></span></>}<span>완료는 평가 통과를 뜻하지 않습니다.</span></div>
      {(error || executionHistory.error || projects.error) && <p role="alert" className="expansion-error">{error || executionHistory.error || projects.error}</p>}
      {notice && <p role="status" className="expansion-success">{notice}</p>}
      <div className="sensor-results-layout">
        <section className="expansion-panel sensor-result-browser" aria-label="Saved execution browser"><h2>저장된 실행 선택</h2>
          <label className="expansion-field">프로젝트<select aria-label="Result viewer project" value={project} onChange={(event) => {
            selectionIntent.current++; setExecution(null); setCreated(null); setProject(event.target.value);
            const query = new URLSearchParams(location.hash.split("?")[1] ?? ""); query.set("project", event.target.value); query.delete("execution");
            window.history.replaceState(null, "", `${location.pathname}${location.search}${location.hash.split("?")[0]}?${query}`);
          }}>{projects.data?.items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          {scope.length > 1 && <label className="expansion-field">결과 종류<select aria-label="Result viewer execution kind" value={resultKind} onChange={(event) => setResultKind(event.target.value as Kind | "all")}><option value="all">모든 종류</option>{scope.map((value) => <option key={value} value={value}>{labels[value]}</option>)}</select></label>}
          <label className="expansion-field">실행 상태<select aria-label="Result viewer execution status" value={resultStatus} onChange={(event) => setResultStatus(event.target.value)}><option value="all">완료·실패·진행 중 모두</option><option value="completed">실행 완료</option><option value="failed">실행 실패</option><option value="queued">대기 중</option><option value="running">실행 중</option><option value="interrupted">중단</option><option value="cancelled">취소</option></select></label>
          <button className="button secondary" disabled={busy || executionHistory.loading} onClick={() => { setOlderExecutions([]); executionHistory.reload(); }}>목록 새로고침</button>
          {executionHistory.loading && <p role="status" className="expansion-note">저장된 실행을 읽는 중…</p>}
          <div className="expansion-list sensor-result-list">{shownRows.map((item) => <button key={item.id} className={execution?.id === item.id ? "selected" : ""} aria-pressed={execution?.id === item.id} onClick={() => void openExecution(item.id)}>
            <strong>{fidelityLabel(item.fidelity)}</strong><span className="sensor-result-date">{resultDate(item.created_at)} · {item.status}</span><span className="sensor-result-name">{item.name}</span><small>{labels[item.experiment_kind]} · {item.id.slice(-8)}</small>
            {execution?.id === item.id && images[0] && <div className="sensor-result-thumbnail"><NativeImagePreview executionId={execution.id} artifact={images[0]} /></div>}
          </button>)}</div>
          {!executionHistory.loading && !shownRows.length && <p className="expansion-note">이 조건에 저장된 실행이 없습니다. 지원 설정을 작성해 시험을 제출하거나 다른 조건을 선택하세요.</p>}
          {nextBefore && <button className="button secondary" disabled={busy} onClick={() => void moreResults()}>이전 실행 더 보기</button>}
          <p className="expansion-clock">최근 100건부터 읽습니다. 실패·취소·미평가 결과도 보존합니다.</p>
        </section>
        <div className="sensor-result-focus" aria-label="Selected execution evidence">
          {busy && <p role="status" className="expansion-note">선택한 원본 결과를 읽는 중…</p>}
          {!execution && !busy && <section className="expansion-panel"><h2>결과를 선택하세요</h2><p className="expansion-note">완료된 실행이 있으면 가장 최근 결과를 자동으로 엽니다. 저장된 데이터가 없으면 영상이나 궤적을 만들어 표시하지 않습니다.</p></section>}
          {execution && <>
            {execution.experiment_kind === "object_sim" && <ObjectExecutionView execution={execution} />}
            <section className={`expansion-panel sensor-execution-heading ${execution.experiment_kind === "object_sim" ? "object-scene-status-heading" : ""}`}><div className="sensor-execution-title"><div><h2>{fidelityLabel(execution.fidelity)}</h2><p className="sensor-result-name">{created?.prepared.spec.name}</p></div><div className="sensor-execution-verdicts"><span>실행 <Badge status={execution.status} /></span>{execution.summary?.schema_version === "object-sim-evidence/1" && <span>시험 기준 <Badge status={typeof execution.summary.verdict === "string" ? execution.summary.verdict : "not_evaluated"} /></span>}</div></div>
              <div className="sensor-execution-meta"><div><span>실행 시각</span><strong>{resultDate(execution.created_at)}</strong></div><div><span>Seed</span><strong>{seedValue === undefined ? "기록 미확인" : String(seedValue)}</strong></div><div><span>어댑터</span><strong>{execution.adapter_id} · {execution.adapter_version}</strong></div><div><span>보존 산출물</span><strong>{execution.summary?.schema_version === "carla-city-result/1" ? `${execution.summary.camera_frame_count ?? 0} BGRA8 프레임 · ${execution.artifacts.length} 파일` : execution.summary?.schema_version === "sensor-experiment-result/1" ? `${images.length} PNG 표본 · RGB 시간축 포함 · ${execution.artifacts.length} 파일` : `${images.length} 영상 · ${rawCount} RAW · ${execution.artifacts.length} 파일`}</strong></div></div>
              {retainedSource && <p className="expansion-clock">{retainedSource}</p>}
              <p className="expansion-note warning">{isLens ? "실행된 렌즈·추정 센서의 중앙 경사 에지 모델 진단입니다. 물리 카메라 품질·도로 라벨·제품 우열은 검증하지 않았습니다." : "이 Viewer는 선택한 어댑터가 보존한 출력만 표시합니다. 실행 완료와 모델 기준·센서 물리 검증·주행 안전 판정은 별개입니다."}</p>
              {execution.error && <p role="alert" className="expansion-error">{execution.error}</p>}{execution.outcome_uncertain && <p className="expansion-note warning">실행 요청의 처리 결과가 불확실합니다. 반복 실행 전에 봉인된 기록을 확인하세요.</p>}
              {!terminal.has(execution.status) && <p className="expansion-note">{execution.status === "queued" ? "지원하는 worker를 기다리는 중입니다." : "실행 중입니다. 보존 결과가 갱신되면 표시합니다."}</p>}
              <div className="expansion-actions"><button className="button secondary" disabled={busy} onClick={() => void perform(async () => setExecution(await api<Execution>(`/experiments/executions/${encodeURIComponent(execution.id)}`)))}>결과 새로고침</button>{execution.download_url && execution.manifest_hash && <button className="button secondary" disabled={busy} onClick={() => void download(execution.download_url!, `${execution.id}.json`, execution.manifest_hash!)}>Download sealed receipt</button>}<a className="button secondary" href={`${route}?project=${encodeURIComponent(project)}&execution=${encodeURIComponent(execution.id)}`}>선택 결과 링크</a></div>
            </section>
            <NativeImageGallery execution={execution} executionId={execution.id} artifacts={execution.artifacts} manifestHash={execution.manifest_hash} summary={execution.summary} onDownload={(item) => void download(item.download_url, item.name, item.sha256, item.size_bytes)} />
            {isLens && <LensSceneEvidence execution={execution} />}
            {ticks.length > 0 && validObject(visualization) && <ReplayVisualization ticks={ticks} total={Number(visualization.total_ticks ?? ticks.length)} truncated={Boolean(visualization.preview_truncated)} />}
            
            {execution.summary?.schema_version === "object-sim-evidence/1" && <ObjectObserverEvidence summary={execution.summary} />}
            {Array.isArray(execution.summary?.driving_outputs) && <DrivingVisualization outputs={execution.summary.driving_outputs as DrivingOutput[]} />}
            {!execution.artifacts.length && terminal.has(execution.status) && <p className="expansion-note">이 실행에는 보존된 영상·궤적 파일이 없습니다. 오류와 실행 기록을 확인하세요.</p>}
            <section className="expansion-panel"><details><summary>원본 결과 파일 · SHA-256 · 다운로드 ({execution.artifacts.length})</summary><div className="sensor-download-files">{execution.artifacts.map((item) => <div className="expansion-artifact" key={item.id}><div><strong>{item.name}</strong><small>{formatBytes(item.size_bytes)} · {item.sha256}</small></div><button className="button secondary" disabled={busy} onClick={() => void download(item.download_url, item.name, item.sha256, item.size_bytes)}>Download</button></div>)}</div></details>
              <details><summary>실행 근거 · 고정 입력 · 계산 범위</summary><p className="expansion-clock">{execution.id}<br />Fidelity: {execution.fidelity ?? "미확인"}<br />Input SHA-256: {execution.input_hash}<br />Sealed receipt SHA-256: {execution.manifest_hash ?? "실행 결과 미봉인"}</p><pre className="expansion-json">{JSON.stringify({ input: created?.prepared.input, summary: { ...execution.summary, visualization: undefined } }, null, 2)}</pre></details>
            </section>
          </>}
        </div>
      </div>
    </div>;
  }
  return <div className="expansion-workspace">
    <PageHeading eyebrow="ADP · SHARED EXECUTION" title={title ?? "Experiments"} description={description ?? (externalViewer ? "Browse shared experiments, retained results and their original evidence." : "Choose a pinned input and available adapter, inspect the workload, then run it through the shared queue and retain native evidence.")} />
    <div className="expansion-note">Execution completed means the selected adapter returned evidence. Model criteria, sensor fidelity and driving safety require their own supported evaluation.</div>
    {error && <div className="expansion-error" role="alert">{error}</div>}{(projects.error || capabilities.error || history.error) && <div className="expansion-error" role="alert">{projects.error || capabilities.error || history.error}</div>}{notice && <div className="expansion-success" role="status">{notice}</div>}
    <div className="expansion-grid" style={externalViewer ? { gridTemplateColumns: "minmax(0, 1fr)" } : undefined}>{!externalViewer && <section className="expansion-panel"><h2>Author experiment</h2><div className="expansion-fields">
      <label className="expansion-field">Project<select aria-label="Experiment project" value={project} onChange={(event) => setProject(event.target.value)}>{projects.data?.items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label className="expansion-field">Execution type<select aria-label="Execution type" value={kind} onChange={(event) => { setKind(event.target.value as Kind); setComparison("single"); setModel(""); setSensor(""); setEvaluator(""); setInput("{}"); }}>{Object.entries(labels).filter(([value]) => !kindFilter || kindFilter.includes(value as Kind)).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="expansion-field">Name<input aria-label="Experiment name" value={name} onChange={(event) => setName(event.target.value)} /></label>
      <label className="expansion-field">Seed<input type="number" min="0" max="2147483647" value={seed} onChange={(event) => setSeed(Number(event.target.value))} /></label>
      <label className="expansion-field wide">Execution adapter<select aria-label="Execution adapter" value={handlerId} onChange={(event) => { setHandlerId(event.target.value); if (event.target.value === "carla-feedback-sensor") { setInput(JSON.stringify(feedbackInputTemplate(), null, 2)); setComparison("single"); setModel(""); setSensor(""); setEvaluator(""); } }}>{!handlers.length && <option value="">No configured handler for this type</option>}{handlers.map((item) => <option key={item.id} value={item.id}>{item.id} · {item.version} · {item.status}</option>)}</select></label>
      <label className="expansion-field wide">Comparison contract<select value={comparison} onChange={(event) => setComparison(event.target.value)}><option value="single">Single pinned execution</option>{(kind === "planning_replay" || kind === "driving_open_loop") && <option value="same_input_model_ab">Same-input model A/B · handler support required</option>}{kind === "sensor_capture" && handlerId !== "carla-feedback-sensor" && <option value="same_scene_sensor_ab">Same-scene sensor A/B</option>}{kind === "isp_compare" && <option value="same_raw_isp_ab">Same retained RAW / ISP A/B</option>}</select></label>
    </div>{handler && <div className={`expansion-note${handler.status !== "available" ? " warning" : ""}`}>{handler.description}<br />Fidelity: {handler.fidelity} · resource: {handler.resource_id ?? "CPU / no shared device reservation"}</div>}
      {kind === "planning_replay" && <label className="expansion-field">Saved replay input<select aria-label="Saved replay input" value={(() => { try { return String(jsonObject(input, "Input").replay_spec_id ?? ""); } catch { return ""; } })()} onChange={(event) => setInput(JSON.stringify({ replay_spec_id: event.target.value }, null, 2))}><option value="">Select a replay specification</option>{replays.data?.items.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.mode}</option>)}</select><a href="#replay-specs">Author a temporal sequence and replay intent →</a></label>}
      {kind === "driving_open_loop" && <><div className="expansion-note warning">The VAD profile uses isolated noncommercial research assets in the default workspace. A single trusted checkpoint is configured; model A/B and camera closed-loop execution are unsupported.</div>
        <label className="expansion-field">Saved driving input<select aria-label="Saved driving input" value={(() => { try { return String(jsonObject(input, "Input").driving_sequence_id ?? ""); } catch { return ""; } })()} onChange={(event) => setInput(JSON.stringify({ driving_sequence_id: event.target.value, confirm_noncommercial_research: drivingAgree }, null, 2))}><option value="">Select a retained driving-policy input</option>{driving.data?.items.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.frame_count} frames / {item.camera_observations} cameras</option>)}</select></label>
        <label className="expansion-note" style={{ display: "flex", gap: 10, alignItems: "flex-start" }}><input aria-label="Confirm isolated noncommercial research" type="checkbox" checked={drivingAgree} style={{ width: "auto", marginTop: 5 }} onChange={(event) => { setDrivingAgree(event.target.checked); try { const value = jsonObject(input, "Input"); setInput(JSON.stringify({ ...value, confirm_noncommercial_research: event.target.checked }, null, 2)); } catch { /* retain invalid input for explicit correction */ } }} />I confirm this execution remains isolated noncommercial research under the retained dataset and checkpoint rights.</label>
        <details><summary>Import a prepared input from the configured server workspace</summary><label className="expansion-field">Relative driving-input JSON path<input aria-label="Driving input relative path" value={drivingImportPath} placeholder="prepared-driving-input.json" onChange={(event) => setDrivingImportPath(event.target.value)} /></label><p className="expansion-note">The operator must configure the input root. Native image/CAN/metadata identities are checked at execution; importing the JSON does not run the model.</p>
          <button className="button secondary" disabled={busy || !canExecute || !drivingAgree || project !== "default" || !drivingImportPath.trim()} onClick={() => perform(async () => { const value = await api<{ id: string }>("/driving-sequences/import", { method: "POST", body: JSON.stringify({ name: drivingImportPath.split("/").at(-1), project_id: "default", research_isolated: true, rights_note: "Explicit isolated noncommercial research; original input dataset/checkpoint rights retained", relative_path: drivingImportPath }) }); driving.reload(); setInput(JSON.stringify({ driving_sequence_id: value.id, confirm_noncommercial_research: true }, null, 2)); setNotice("Original driving input JSON retained. Native files remain subject to execution-time checks."); })}>Import retained driving input</button></details>
        {driving.error && <p role="alert" className="expansion-error">{driving.error}</p>}</>}
      {handlerId === "camera-e2e-lens-sensor-scene" && <><LensSceneAuthor catalog={lensCatalog.data?.catalog} text={input} onChange={(value) => { setInput(value); setComparison("same_scene_sensor_ab"); setModel(""); setSensor(""); setEvaluator(""); }} />{lensCatalog.error && <p role="alert" className="expansion-error">{lensCatalog.error}</p>}</>}
      {handlerId === "carla-feedback-sensor" && <CarlaFeedbackAuthor text={input} onChange={(value) => { setInput(value); setComparison("single"); setModel(""); setSensor(""); setEvaluator(""); }} />}
      {kind === "sensor_capture" && !["camera-e2e-lens-sensor-scene", "carla-feedback-sensor"].includes(handlerId) && <button className="button secondary" disabled={busy} onClick={() => perform(async () => { const value = await api<{ template: unknown }>("/sensor-captures/capabilities"); setInput(JSON.stringify(comparison === "same_scene_sensor_ab" ? { rig_a: value.template, rig_b: value.template } : value.template, null, 2)); })}>Load supported capture template</button>}
      <label className="expansion-field">Immutable input JSON<textarea aria-label="Experiment input JSON" value={input} onChange={(event) => setInput(event.target.value)} spellCheck={false} rows={8} /></label>
      <details><summary>Optional model, sensor and evaluator references</summary><p className="expansion-note">Only the selected adapter's supported fields execute. Unsupported substitutions must be rejected by preflight.</p>
        <label className="expansion-field">Model JSON<textarea rows={3} value={model} onChange={(event) => setModel(event.target.value)} placeholder="Leave empty unless supported" /></label><label className="expansion-field">Sensor JSON<textarea rows={3} value={sensor} onChange={(event) => setSensor(event.target.value)} placeholder="Leave empty unless supported" /></label><label className="expansion-field">Evaluator JSON<textarea rows={3} value={evaluator} onChange={(event) => setEvaluator(event.target.value)} placeholder="Leave empty unless supported" /></label></details>
      <div className="expansion-actions"><button className="button secondary" disabled={busy || !canExecute || handler?.status !== "available"} onClick={() => perform(async () => setPreview(await api<Preview>("/experiments/preview", { method: "POST", body: JSON.stringify(body()) })))}>Preview workload</button><button className="button secondary" disabled={busy || !preview || !canExecute} onClick={save}>Save frozen experiment</button><button className="button primary" disabled={busy || !created || !canExecute} onClick={submit}>Submit execution</button></div>
      {!canExecute && <p className="expansion-clock">Viewer / Reviewer can inspect results; Engineer or Admin is required to author and execute.</p>}
      {preview && <div className="expansion-note"><strong>{preview.job_count} job</strong> · {preview.execution_fidelity} · evaluation: {preview.evaluation_status}<p className="expansion-clock">Preview SHA-256: {preview.preview_hash}</p><details><summary>Pinned execution inputs</summary><pre className="expansion-json">{JSON.stringify(preview.prepared.input, null, 2)}</pre></details></div>}
    </section>}<section className="expansion-panel"><h2>Project experiments</h2><div className="expansion-list">{history.data?.items.map((item) => <button key={item.id} className={created?.id === item.id ? "selected" : ""} onClick={() => open(item.id)}><strong>{item.name}</strong><small>{labels[item.kind]}<br />{item.id}</small></button>)}{!history.loading && !history.data?.items.length && <p>No saved experiments in this project.</p>}</div>
      {created && <><h2>Frozen intent</h2><p><strong>{created.prepared.spec.name}</strong> · {labels[created.prepared.spec.kind]}</p><p className="expansion-clock">{created.id}</p><details><summary>Immutable input definition</summary><pre className="expansion-json">{JSON.stringify(created.prepared.input, null, 2)}</pre></details>{created.executions?.map((item) => <button className="button secondary" key={item.id} onClick={() => setExecution(item)}>{item.status} · {item.id.slice(-8)}</button>)}</>}
      {execution && <><h2>Execution status</h2><div className="expansion-actions"><Badge status={execution.status} /><button className="button secondary" onClick={() => perform(async () => setExecution(await api<Execution>(`/experiments/executions/${encodeURIComponent(execution.id)}`)))}>Refresh</button>{!terminal.has(execution.status) && execution.cancellation_supported && <button className="button secondary" disabled={busy || !canExecute} onClick={() => perform(async () => setExecution(await api<Execution>(`/experiments/executions/${encodeURIComponent(execution.id)}/cancel`, { method: "POST" })))}>Cancel execution</button>}</div>
        <p className="expansion-clock">{execution.id}<br />Input SHA-256: {execution.input_hash}<br />{execution.adapter_id} · {execution.adapter_version}<br />{execution.fidelity ?? "Execution evidence pending"}</p>
        {execution.error && <div className="expansion-error" role="alert">{execution.error}</div>}{execution.outcome_uncertain && <div className="expansion-note warning">Dispatch outcome is uncertain. Inspect the retained receipt before repeating execution.</div>}
        {execution.status === "queued" && <p className="expansion-note">Waiting for a worker with the selected adapter. The API alone does not execute the job.</p>}
        {execution.download_url && execution.manifest_hash && <button className="button secondary" disabled={busy} onClick={() => download(execution.download_url!, `${execution.id}.json`, execution.manifest_hash!)}>Download sealed receipt</button>}
        {retainedSource && <p className="expansion-clock">{retainedSource}</p>}
        <NativeImageGallery execution={execution} executionId={execution.id} artifacts={execution.artifacts} manifestHash={execution.manifest_hash} summary={execution.summary} onDownload={(item) => void download(item.download_url, item.name, item.sha256, item.size_bytes)} />
        <details><summary>원본 결과 파일 · SHA-256 · 다운로드 ({execution.artifacts.length})</summary>{execution.artifacts.map((item) => <div className="expansion-artifact" key={item.id}><div><strong>{item.name}</strong><small>{formatBytes(item.size_bytes)} · {item.sha256}</small></div><button className="button secondary" disabled={busy} onClick={() => download(item.download_url, item.name, item.sha256, item.size_bytes)}>Download</button></div>)}</details>
        {execution.summary && <><h2>Evidence summary</h2><div className="expansion-note">{String(execution.summary.verification_verdict ?? "Adapter evidence returned; no generic safety verdict")}</div><details><summary>All metrics, provenance and limitations</summary><pre className="expansion-json">{JSON.stringify({ ...execution.summary, visualization: undefined }, null, 2)}</pre></details></>}
      </>}
    </section></div>
    {ticks.length > 0 && validObject(visualization) && <ReplayVisualization ticks={ticks} total={Number(visualization.total_ticks ?? ticks.length)} truncated={Boolean(visualization.preview_truncated)} />}
    {execution?.experiment_kind === "object_sim" && <ObjectExecutionView execution={execution} />}
    {execution?.summary?.schema_version === "object-sim-evidence/1" && <ObjectObserverEvidence summary={execution.summary} />}
    {Array.isArray(execution?.summary?.driving_outputs) && <DrivingVisualization outputs={execution.summary.driving_outputs as DrivingOutput[]} />}
    {execution?.summary?.schema_version === "camera-e2e-lens-scene-result/1" && <LensSceneEvidence execution={execution} />}
  </div>;
}
