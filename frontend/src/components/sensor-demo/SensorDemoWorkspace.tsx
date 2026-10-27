import { useEffect, useRef, useState } from "react";
import { ArrowRight, Camera, Check, GitCompareArrows, LoaderCircle, Play, Radar, Radio, RefreshCw } from "lucide-react";
import { api, isExternalViewer, useResource } from "../../api";
import SensorRigDiagram from "./SensorRigDiagram";
import SensorPresenter from "./SensorPresenter";
import { executeSensorDemo, sensorDemoParameterError, sensorDemoSignature, sensorDemoStatus, sensorDemoTerminal, SensorDemoPreviewChanged, initialSensorPreset, isNativeSensorAdapter, isDrivingSensorAdapter, sensorPresetAdapter } from "./demo-contract";
import type { SensorDemoAttempt, SensorDemoExecution, SensorDemoPreset, SensorDemoPreview } from "./demo-contract";
import "./sensor-demo.css";

function setQuery(values: Record<string, string | null>) {
  const query = new URLSearchParams(location.hash.split("?")[1] ?? ""); query.set("mode", "demo");
  for (const [key, value] of Object.entries(values)) if (value === null) query.delete(key); else query.set(key, value);
  history.replaceState(null, "", `${location.pathname}${location.search}#sensor-sim?${query}`);
}
function errorText(error: unknown) { return error instanceof Error ? error.message : String(error); }

export default function SensorDemoWorkspace({ project }: { project: string }) {
  const initial = useRef(new URLSearchParams(location.hash.split("?")[1] ?? ""));
  const presets = useResource<{ items: SensorDemoPreset[] }>(`/sensor-sim/presets?project_id=${encodeURIComponent(project)}`);
  const identity = useResource<{ role: string; mode: string }>("/team/whoami");
  const [presetId, setPresetId] = useState(initial.current.get("preset") ?? "camera_fov"), [parameters, setParameters] = useState<Record<string, number> | null>(null);
  const [preview, setPreview] = useState<SensorDemoPreview | null>(null), [previewKey, setPreviewKey] = useState(""), [preparing, setPreparing] = useState(false), [busy, setBusy] = useState(false), [stage, setStage] = useState("");
  const [error, setError] = useState(""), [pollError, setPollError] = useState(""), [resultId, setResultId] = useState(initial.current.get("result")), [runId, setRunId] = useState(initial.current.get("run"));
  const [run, setRun] = useState<SensorDemoExecution | null>(null), [pollVersion, setPollVersion] = useState(0);
  const attempt = useRef<SensorDemoAttempt | null>(null), submitting = useRef(false), previewRequest = useRef(0), initialized = useRef(false);
  const result = useResource<SensorDemoExecution>(resultId ? `/experiments/executions/${encodeURIComponent(resultId)}` : null);
  const preset = presets.data?.items.find(item => item.id === presetId), parameter = preset?.parameters[0];
  const activeRun = !!runId && (!run || !sensorDemoTerminal(run.status));
  const locked = busy || activeRun;
  const writable = !isExternalViewer() && !!identity.data && (identity.data.mode !== "project" || ["Engineer", "Admin"].includes(identity.data.role));
  const value = parameter ? parameters?.[parameter.key] ?? parameter.default : 0;
  const driving = !!preset && isDrivingSensorAdapter(sensorPresetAdapter(preset));
  const parameterError = parameter ? sensorDemoParameterError(parameter, value) : "";
  const signature = preset && parameters ? sensorDemoSignature(project, preset, parameters) : "";
  const previewCurrent = !!preview && !!signature && previewKey === signature;

  useEffect(() => {
    if (initialized.current || !presets.data) return;
    initialized.current = true;
    const chosen = initialSensorPreset(presets.data.items, initial.current.get("preset"));
    if (!chosen) return;
    setPresetId(chosen.id); const parameter = chosen.parameters[0], requested = initial.current.get("value");
    const next = { ...chosen.defaults };
    if (parameter && requested !== null && !sensorDemoParameterError(parameter, Number(requested))) next[parameter.key] = Number(requested);
    setParameters(next);
    if (!initial.current.get("result")) setResultId(chosen.saved_execution_id);
    setQuery({ project, preset: chosen.id, value: String(next[parameter.key]), result: initial.current.get("result") ?? chosen.saved_execution_id });
  }, [presets.data, presetId, project]);

  useEffect(() => {
    if (!runId) return;
    let active = true, timer: ReturnType<typeof setTimeout> | undefined; const controller = new AbortController();
    async function poll() {
      try {
        const next = await api<SensorDemoExecution>(`/experiments/executions/${encodeURIComponent(runId!)}`, { signal: controller.signal });
        if (!active) return;
        if (next.project_id !== project || !isNativeSensorAdapter(next.adapter_id)) throw new Error("선택한 프로젝트의 센서 실행과 다릅니다.");
        setRun(next); setPollError("");
        if (!sensorDemoTerminal(next.status)) timer = setTimeout(() => void poll(), 2000);
      } catch (reason) { if (active) setPollError(errorText(reason)); }
    }
    void poll(); return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [runId, project, pollVersion]);

  function choose(item: SensorDemoPreset) {
    if (locked) return;
    ++previewRequest.current; setPreparing(false); setPresetId(item.id); setParameters({ ...item.defaults }); setPreview(null); setPreviewKey(""); setError(""); setPollError(""); setRun(null); setRunId(null); setResultId(item.saved_execution_id); attempt.current = null;
    setQuery({ project, preset: item.id, value: String(item.defaults[item.parameters[0].key]), result: item.saved_execution_id, run: null });
  }
  function change(next: number) {
    if (!preset || !parameter || locked) return;
    ++previewRequest.current; setPreparing(false); setParameters({ [parameter.key]: next }); setPreview(null); setPreviewKey(""); setError(""); attempt.current = null; setQuery({ value: String(next) });
  }
  async function prepare() {
    if (!preset || !parameters || !preset.ready || parameterError || preparing || locked) return;
    const request = ++previewRequest.current; setPreparing(true); setError(""); setPreview(null);
    try {
      const next = await api<SensorDemoPreview>(`/sensor-sim/presets/${encodeURIComponent(preset.id)}/preview`, { method: "POST", body: JSON.stringify({ project_id: project, expected_binding_revision: preset.binding_revision, parameters }) });
      if (request === previewRequest.current) { setPreview(next); setPreviewKey(signature); }
    } catch (reason) { if (request === previewRequest.current) setError(errorText(reason)); }
    finally { if (request === previewRequest.current) setPreparing(false); }
  }
  async function execute() {
    if (!previewCurrent || !preview || !writable || locked || submitting.current) return;
    submitting.current = true; setBusy(true); setError("");
    if (!attempt.current || attempt.current.signature !== signature || attempt.current.execution) attempt.current = { key: `sensor-demo-${crypto.randomUUID()}`, signature };
    try {
      const submitted = await executeSensorDemo(api, preview, attempt.current, setStage);
      setRun(null); setRunId(submitted.id); setPollError(""); setQuery({ run: submitted.id });
    } catch (reason) {
      if (reason instanceof SensorDemoPreviewChanged) { setPreview(null); setPreviewKey(""); attempt.current = null; }
      setError(errorText(reason));
    }
    finally { setBusy(false); submitting.current = false; setStage(""); }
  }
  function openResult(id: string) { setResultId(id); setQuery({ result: id }); }
  const nativeResult = result.data && result.data.id === resultId && result.data.project_id === project && result.data.status === "completed" && isNativeSensorAdapter(result.data.adapter_id) ? result.data : null;

  return <section className="sensor-demo" aria-label="Sensor Sim 빠른 시작">
    <header className="sensor-demo-hero"><div><span className="sensor-demo-eyebrow">CONFIGURE. SIMULATE. COMPARE.</span><h1>센서를 바꾸면, 관측이 달라집니다.</h1><p>한 가지 설정을 바꾸고 CARLA에서 다시 실행하세요. 카메라·LiDAR·radar의 실제 출력을 나란히 확인합니다.</p></div><span className="sensor-demo-native-badge"><i/>CARLA SENSOR SIM</span></header>
    {presets.loading && <p className="sensor-demo-message" role="status">프리셋을 불러오는 중…</p>}
    {presets.error && <p className="sensor-demo-error" role="alert">{presets.error}<button onClick={presets.reload}>다시 불러오기</button></p>}
    <div className="sensor-demo-presets">{presets.data?.items.map((item, i) => {
      const p = item.parameters[0], Icon = ["camera_fov", "driving_camera_fov"].includes(item.id) ? Camera : item.id === "lidar_density" ? Radio : Radar;
      return <button className={`sensor-demo-preset ${item.id === presetId ? "selected" : ""}`} key={item.id} onClick={() => choose(item)} disabled={locked} aria-pressed={item.id === presetId} aria-label={`${item.title} 프리셋 선택`}>
        <span className="sensor-demo-preset-icon"><Icon size={23}/></span><div><small>0{i + 1} / {item.id === "driving_camera_fov" ? "DRIVING · MULTISENSOR" : item.id === "camera_fov" ? "FIELD OF VIEW" : item.id === "lidar_density" ? "POINT DENSITY" : "DETECTION RANGE"}</small><h2>{item.title}</h2><span className="sensor-demo-card-values">{p.baseline.toLocaleString()} <ArrowRight size={13}/> {p.default.toLocaleString()} {p.unit}</span></div><span className={`sensor-demo-availability ${item.saved_execution_id ? "ready" : ""}`}>{item.saved_execution_id ? "저장 A/B" : "첫 실행 준비"}</span>
      </button>;
    })}</div>
    {preset && parameter && <div className="sensor-demo-layout"><aside className="sensor-demo-editor">
      <div className="sensor-demo-section-heading"><span>01</span><h2>다음 실행 · 센서 변경</h2></div><p className="sensor-demo-editor-description">{preset.description}</p>
      <SensorRigDiagram presetId={preset.id} value={Number.isFinite(value) ? value : parameter.default} baseline={parameter.baseline}/>
      <label className="sensor-demo-value-label" htmlFor="sensor-demo-value">변경 B · {parameter.label}<span>{parameter.unit}</span></label>
      <div className="sensor-demo-value-control"><input id="sensor-demo-value" type="number" aria-label={`변경 B ${parameter.label}`} min={parameter.min} max={parameter.max} step={parameter.step} value={Number.isFinite(value) ? value : ""} onChange={e => change(e.target.value === "" ? NaN : Number(e.target.value))} disabled={locked}/><button aria-label="프리셋 기본값으로 복원" onClick={() => change(parameter.default)} disabled={locked}><RefreshCw size={15}/></button></div>
      <input className="sensor-demo-value-slider" type="range" aria-label={`${parameter.label} 슬라이더`} min={parameter.min} max={parameter.max} step={parameter.step} value={Number.isFinite(value) ? value : parameter.default} onChange={e => change(Number(e.target.value))} disabled={locked}/>
      <div className="sensor-demo-range-labels"><span>{parameter.min.toLocaleString()} {parameter.unit}</span><span>{parameter.max.toLocaleString()} {parameter.unit}</span></div>
      <div className="sensor-demo-difference"><span>A · 기준</span><strong>{parameter.baseline.toLocaleString()} {parameter.unit}</strong><ArrowRight size={17}/><span>B · 변경</span><strong>{Number.isFinite(value) ? value.toLocaleString() : "—"} {parameter.unit}</strong></div>
      <p className="sensor-demo-fixed">Town10HD_Opt · {driving ? "주행 자차 · 20초 · 목표 3 m/s" : "정지 자차 · 10초"} · 10Hz<br/>전·후방 RGB 640×360 · LiDAR · radar</p>
      {parameterError && <p className="sensor-demo-input-error" role="status">{parameterError}</p>}
      {!preset.ready && <p className="sensor-demo-input-error">{preset.reason ?? "프로젝트의 실행 입력 연결이 필요합니다."}</p>}
      {previewCurrent ? <div className="sensor-demo-preview" aria-label="Sensor Sim 변경 내용 확인"><strong><Check size={15}/>변경 내용 확인됨</strong>{preview.changes.map(change => <p key={change.key}>{change.label}: {change.baseline.toLocaleString()} → {change.value.toLocaleString()} {change.unit}</p>)}<p>기준 A와 변경 B 모두 새로 캡처합니다.{driving && " 각 주행에서 RGB·LiDAR·radar를 함께 수집합니다."}</p><button className="sensor-demo-primary" disabled={!writable || locked} onClick={() => void execute()}>{busy ? <LoaderCircle size={16} className="sensor-demo-spin"/> : <Play size={16}/>} {busy ? stage : "확인하고 A/B 실행"}</button></div> : <button className="sensor-demo-primary" disabled={!preset.ready || !!parameterError || preparing || locked || !writable} onClick={() => void prepare()}>{preparing ? <LoaderCircle size={16} className="sensor-demo-spin"/> : <GitCompareArrows size={16}/>} {preparing ? "실행 가능성 확인 중…" : "변경 내용 확인"}</button>}
      {!writable && identity.data && <p className="sensor-demo-fixed">현재 권한으로 저장 결과를 재생할 수 있습니다. 새 실행은 Engineer 권한이 필요합니다.</p>}
      {identity.error && <p role="alert" className="sensor-demo-input-error">{identity.error}</p>}
      {error && <p role="alert" className="sensor-demo-error">{error}</p>}
      <details className="sensor-demo-source"><summary>고정 조건 · 원본 연결</summary><p>Seed {preset.source?.seed ?? "—"} · 프리셋 binding r{preset.binding_revision ?? "—"} · {preset.source?.adapter_version ?? "adapter 미연결"}</p><p>저장된 대표 결과는 기본 변경값입니다. 편집한 값은 새 실행 완료 후 결과에 반영됩니다.</p><code>{preset.saved_execution_id ?? "대표 실행 미등록"}</code></details>
    </aside><div className="sensor-demo-result">
      {runId && <div className={`sensor-demo-run-status ${run?.status === "failed" || run?.status === "interrupted" ? "failed" : ""}`} role="status"><div><strong>{run ? sensorDemoStatus(run.status) : "실행 상태 확인 중"}</strong><p>실행 {runId.slice(0, 12)} · 대기 중에도 저장 결과를 재생할 수 있습니다.</p>{run?.error && <p>{run.error}</p>}{run?.outcome_uncertain && <p>실행 결과가 불확실합니다. 상세 실행 기록을 확인하세요.</p>}{pollError && <p>{pollError}</p>}</div>{run?.status === "completed" ? <button onClick={() => openResult(run.id)}>새 A/B 결과 열기<ArrowRight size={15}/></button> : pollError ? <button onClick={() => setPollVersion(n => n + 1)}>같은 실행 다시 확인</button> : <LoaderCircle size={20} className={activeRun ? "sensor-demo-spin" : ""}/>}</div>}
      {resultId && <div className="sensor-demo-result-origin"><span>{resultId === preset.saved_execution_id ? "저장된 대표 A/B" : "선택한 실행 A/B"} · {result.data?.id === resultId && result.data?.created_at ? new Date(result.data.created_at).toLocaleString("ko-KR") : resultId.slice(0, 12)}</span>{preset.saved_execution_id && resultId !== preset.saved_execution_id && <button onClick={() => openResult(preset.saved_execution_id!)}>대표 결과로 돌아가기</button>}</div>}
      {result.loading && resultId && <div className="sensor-presenter-loading" role="status">저장된 A/B 실행을 여는 중…</div>}
      {result.error && <p role="alert" className="sensor-demo-error">{result.error}<button onClick={result.reload}>원본 다시 읽기</button></p>}
      {result.data && result.data.id === resultId && !nativeResult && <p role="alert" className="sensor-demo-error">이 결과는 현재 프로젝트의 완료된 CARLA 센서 A/B 실행이 아닙니다. 상세 실행 기록을 확인하세요.</p>}
      {nativeResult && <SensorPresenter key={nativeResult.id} execution={nativeResult} title={preset.title} presetId={preset.id} initialTab={preset.id === "radar_range" ? "radar" : "lidar"}/>}
      {!resultId && <div className="sensor-demo-empty"><div className="sensor-demo-empty-symbol"><Camera size={38}/><Radio size={38}/><Radar size={38}/></div><span className="sensor-demo-eyebrow">READY TO CAPTURE</span><h2>첫 A/B 결과를 만들어 보세요.</h2><p>왼쪽에서 변경 내용을 확인하고 실행하면<br/>전방 RGB와 LiDAR·radar를 이곳에서 비교합니다.</p><span>각 프리셋의 대표 결과는 명시적으로 연결한 완료 실행을 사용합니다.</span></div>}
    </div></div>}
  </section>;
}
