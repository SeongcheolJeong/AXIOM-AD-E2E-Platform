import { useEffect, useRef, useState } from "react";
import { ArrowRight, Check, ChevronRight, GitCompareArrows, LoaderCircle, Play, RefreshCw, SlidersHorizontal } from "lucide-react";
import { api, useResource } from "../../api";
import type { CityPose } from "../city-sim/city-contract";
import type { ObjectExecution } from "../object-scene/object-scene";
import CitySceneViewer from "../city-sim/CitySceneViewer";
import DemoMapCanvas from "./DemoMapCanvas";
import { demoChanges, demoParameterUnit, demoSavedPair, demoStatus, demoTerminal, executeDemo } from "./demo-contract";
import type { DemoParameters, DemoPreset, DemoPreview, DemoRunAttempt, DemoSelection } from "./demo-contract";
import "./object-demo.css";

type Execution = ObjectExecution & { error?: string | null; outcome_uncertain?: boolean; cancellation_supported?: boolean; cancel_requested_at?: string | null };
type Baseline = { id: string; selection: DemoSelection; retained: boolean };
const accents: Record<string, string> = { obstacle_bypass: "mint", gap_release: "blue", junction_eight: "amber", urban_city: "blue", urban_right_turn: "amber" };

function SceneGlyph({ id }: { id: string }) {
  return <svg viewBox="0 0 280 96" aria-hidden="true">
    {id === "urban_right_turn" ? <>
      <path className="demo-glyph-road" d="M0 48H280M174 0V96" /><path className="demo-glyph-lane" d="M0 48H280M174 0V96" />
      <path className="demo-glyph-path" d="M25 61H150Q188 61 188 90" />
      <rect className="ego" x="52" y="56" width="22" height="11" rx="3" /><rect className="traffic" x="112" y="56" width="22" height="11" rx="3" />
      <path d="M79 61H106" stroke="#b6772a" strokeWidth="2" strokeDasharray="4 3" />
      <path d="M117 37V45M125 37V45" stroke="#b6772a" strokeWidth="3" />
    </> : id === "urban_city" ? <>
      {[[12, 5, 38, 20], [60, 5, 38, 20], [181, 5, 37, 20], [230, 5, 37, 20], [15, 71, 60, 18], [202, 71, 61, 18]].map(([x, y, width, height], index) => <rect key={index} x={x} y={y} width={width} height={height} rx="3" fill="#dbe8e3" stroke="#a4bfb4" />)}
      <path className="demo-glyph-road" d="M0 48H280M140 0V96" /><path className="demo-glyph-lane" d="M0 48H280M140 0V96" />
      <path className="demo-glyph-path" d="M40 60H107Q152 60 152 22" />
      <rect className="ego" x="70" y="55" width="22" height="11" rx="3" /><rect className="traffic" x="149" y="14" width="8" height="16" rx="2" />
    </> : id === "junction_eight" ? <>
      <path className="demo-glyph-road" d="M0 48H280M140 0V96" /><path className="demo-glyph-lane" d="M0 48H280M140 0V96" />
      {[[42, 36], [73, 36], [184, 57], [216, 57], [123, 8], [123, 76], [146, 17], [146, 73]].map(([x, y], i) => <rect key={i} className={i ? "traffic" : "ego"} x={x} y={y} width={i > 3 ? 8 : 15} height={i > 3 ? 15 : 8} rx="2" />)}
    </> : <>
      <path className="demo-glyph-road" d="M0 48H280" /><path className="demo-glyph-lane" d="M0 48H280" />
      <path className="demo-glyph-path" d={id === "gap_release" ? "M42 62H82C107 62 107 34 133 34H235" : "M42 62H85C112 62 109 34 137 34H173C200 34 204 62 235 62"} />
      <rect className="ego" x="38" y="57" width="22" height="11" rx="3" /><rect className="obstacle" x="135" y="55" width="14" height="15" rx="2" />
      {id === "gap_release" && <rect className="traffic" x="88" y="29" width="22" height="11" rx="3" />}
    </>}
  </svg>;
}

export default function ObjectDemoWorkspace({ project, onAdvanced, onCompare, onStory, notify }: {
  project: string; onAdvanced: () => void; onCompare: (left: string, right: string) => void; onStory: (left: string, right?: string) => void; notify?: (message: string) => void;
}) {
  const presets = useResource<{ items: DemoPreset[] }>(`/object-sim/presets?project_id=${encodeURIComponent(project)}`);
  const demoPresets = presets.data?.items.filter((item) => !["lead_stop_start", "cut_in", "urban_left_turn"].includes(item.id));
  const capabilities = useResource<{ handlers: { id: string; version: string; status: string; description?: string }[] }>("/experiments/capabilities");
  const identity = useResource<{ role: string; mode: string }>("/team/whoami");
  const [selection, setSelection] = useState<DemoSelection | null>(null), [preview, setPreview] = useState<DemoPreview | null>(null);
  const [previewing, setPreviewing] = useState(false), [previewError, setPreviewError] = useState("");
  const [previewKey, setPreviewKey] = useState("");
  const [selectedActor, setSelectedActor] = useState("ego"), [baseline, setBaseline] = useState<Baseline | null>(null);
  const [executionId, setExecutionId] = useState<string | null>(null), [execution, setExecution] = useState<Execution | null>(null);
  const [busy, setBusy] = useState(false), [stage, setStage] = useState(""), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [shownExecution, setShownExecution] = useState<{ id: string; retained: boolean } | null>(null);
  const attempt = useRef<DemoRunAttempt | null>(null), submitting = useRef(false), runSelection = useRef<DemoSelection | null>(null);
  const [pollVersion, setPollVersion] = useState(0);
  const result = useResource<Execution>(shownExecution ? `/experiments/executions/${encodeURIComponent(shownExecution.id)}` : null);
  const preset = presets.data?.items.find((item) => item.id === selection?.presetId);
  const native = capabilities.data?.handlers.find((item) => item.id === "carla-city-object");
  const writable = !!identity.data && (identity.data.mode !== "project" || ["Engineer", "Admin"].includes(identity.data.role));
  const activeRun = !!executionId && (!execution || !demoTerminal(execution.status));
  const locked = busy || activeRun;
  const selectionKey = JSON.stringify(selection);

  useEffect(() => {
    if (!selection || !preset?.ready) return;
    let active = true;
    const controller = new AbortController();
    setPreviewing(true); setPreviewError("");
    const timer = setTimeout(() => {
      api<DemoPreview>(`/object-sim/presets/${encodeURIComponent(selection.presetId)}/preview`, {
        method: "POST", signal: controller.signal,
        body: JSON.stringify({ project_id: project, parameters: selection.parameters, actor_poses: selection.actorPoses }),
      }).then((value) => {
        if (!active) return;
        setPreview(value);
        setPreviewKey(selectionKey);
        setSelectedActor((id) => value.scenario.actors.some((actor) => actor.id === id) ? id : value.actor_roles.ego);
      }).catch((reason: unknown) => {
        if (active) { setPreview(null); setPreviewError(reason instanceof Error ? reason.message : String(reason)); }
      }).finally(() => { if (active) setPreviewing(false); });
    }, 200);
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [project, selectionKey, preset?.ready]);

  useEffect(() => {
    if (!executionId) return;
    let active = true, timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    async function poll() {
      try {
        const value = await api<Execution>(`/experiments/executions/${encodeURIComponent(executionId!)}`, { signal: controller.signal });
        if (!active) return;
        setExecution(value);
        if (value.status === "completed") {
          setBaseline((current) => current ?? (runSelection.current ? { id: value.id, selection: structuredClone(runSelection.current), retained: false } : null));
          setNotice("실행 결과가 준비되었습니다. 원본 재생 또는 A/B 비교를 열 수 있습니다.");
        }
        if (!demoTerminal(value.status)) timer = setTimeout(() => void poll(), 1800);
      } catch (reason) {
        if (active) { setError(`실행 상태를 확인할 수 없습니다. 같은 실행의 상태를 다시 확인하세요. ${reason instanceof Error ? reason.message : String(reason)}`); }
      }
    }
    void poll();
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [executionId, pollVersion]);

  function choose(item: DemoPreset) {
    if (locked || !item.ready) return;
    const next = { presetId: item.id, parameters: { ...item.defaults }, actorPoses: {} };
    setSelection(next); setPreview(null); setPreviewError(""); setSelectedActor("ego");
    setBaseline(item.saved_execution_id ? { id: item.saved_execution_id, selection: structuredClone(next), retained: true } : null);
    setExecutionId(null); setExecution(null); setShownExecution(null); setError(""); setNotice(""); attempt.current = null;
  }
  function change(parameters: DemoParameters, actorPoses = selection?.actorPoses ?? {}) {
    if (!selection || locked) return;
    setSelection({ ...selection, parameters, actorPoses }); setError(""); setNotice("");
  }
  function changeParameter(key: string, value: number | boolean) {
    if (!selection) return;
    const poses = { ...selection.actorPoses };
    const id = key === "obstacle_offset_m" ? preview?.actor_roles.obstacle : key === "rear_gap_m" ? preview?.actor_roles.rear : undefined;
    if (id) delete poses[id];
    change({ ...selection.parameters, [key]: value }, poses);
  }
  function moveActor(id: string, pose: CityPose) {
    if (!selection || !preset) return;
    const parameters = { ...selection.parameters };
    if (id === preview?.actor_roles.obstacle && "obstacle_offset_m" in parameters) parameters.obstacle_offset_m = preset.defaults.obstacle_offset_m;
    if (id === preview?.actor_roles.rear && "rear_gap_m" in parameters) parameters.rear_gap_m = preset.defaults.rear_gap_m;
    change(parameters, { ...selection.actorPoses, [id]: pose });
  }
  async function run() {
    if (submitting.current || !selection || !preview || !native || !writable || previewing || previewKey !== selectionKey || activeRun || previewError) return;
    submitting.current = true; setBusy(true); setError(""); setNotice("");
    const frozen = structuredClone(preview.scenario), frozenSelection = structuredClone(selection);
    const signature = JSON.stringify({ project, scenario: frozen, adapter: native.id, version: native.version });
    if (attempt.current?.signature !== signature || attempt.current.execution) attempt.current = { key: `demo-${crypto.randomUUID()}`, signature };
    try {
      const value = await executeDemo(api, project, frozen, native, attempt.current, setStage);
      runSelection.current = frozenSelection;
      setExecution(null); setExecutionId(value.id); setShownExecution(null); setStage("");
      notify?.("Object Sim 시험을 큐에 제출했습니다.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally { submitting.current = false; setBusy(false); }
  }
  async function cancel() {
    if (!executionId || submitting.current) return;
    submitting.current = true; setBusy(true); setError("");
    try {
      const value = await api<Execution>(`/experiments/executions/${encodeURIComponent(executionId)}/cancel`, { method: "POST" });
      setExecution(value); setPollVersion((v) => v + 1); setNotice("취소 요청을 보냈습니다. 실행 상태에서 처리 결과를 확인합니다.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { submitting.current = false; setBusy(false); }
  }
  const map = preview ? { ...preview.map.definition, lanes: preview.map.lanes ?? preview.map.definition.lanes } : null;
  const actor = preview?.scenario.actors.find((item) => item.id === selectedActor);
  const comparisonPreset = preset && baseline ? { ...preset, defaults: baseline.selection.parameters } : preset;
  const changes = selection && comparisonPreset ? demoChanges(comparisonPreset, selection.parameters, selection.actorPoses, baseline?.selection.actorPoses) : [];
  const canCompare = !!baseline && !!execution && demoTerminal(execution.status) && baseline.id !== execution.id;

  return <section className="object-demo" aria-label="Object Sim 빠른 시작">
    <div className="demo-hero"><div><span className="demo-eyebrow">BUILD A SCENE. CHANGE THE OUTCOME.</span><h2>한 가지 변경, 다른 움직임.</h2><p>장면을 고르고 직접 바꿔 보세요. CARLA에서 다시 실행하고, 회피와 양보의 차이를 나란히 확인합니다.</p></div><div className="demo-hero-flow"><span>장면 선택</span><ChevronRight size={15} /><span>설정 변경</span><ChevronRight size={15} /><span>A/B 비교</span></div></div>
    {presets.loading && <p role="status" className="demo-message"><LoaderCircle className="demo-spin" size={16} /> 데모 장면을 불러오는 중…</p>}
    {presets.error && <div className="demo-message error" role="alert">{presets.error}<button type="button" className="button secondary" onClick={presets.reload}>다시 불러오기</button></div>}
    <div className="demo-presets" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 250px), 1fr))" }}>{demoPresets?.map((item, index) => <article key={item.id} className={`demo-card ${accents[item.id] ?? "mint"} ${selection?.presetId === item.id ? "selected" : ""}`}>
      <div className="demo-card-top"><span>0{index + 1} / {item.id === "urban_right_turn" ? "LEAD INTERACTION" : item.id === "urban_city" ? "URBAN DRIVING" : item.id === "junction_eight" ? "MULTI ACTOR" : "INTERACTIVE"}</span><span className={`demo-readiness ${item.ready ? "ready" : ""}`}>{item.ready ? "편집 준비됨" : "장면 준비 필요"}</span></div>
      <SceneGlyph id={item.id} /><h3>{item.title}</h3><p>{item.description}</p>
      {["urban_city", "urban_right_turn"].includes(item.id) && <p className="demo-control-note">{!item.ready ? "도시 지도와 원본 실행 연결 후 시연할 수 있습니다." : item.saved_execution_id ? "CARLA 도시 배경은 저장된 원본 RGB에서 확인합니다." : "도시 지도 등록됨 · 원본 RGB는 실행 후 확인합니다."}</p>}
      {!item.ready && <p className="demo-binding-reason">{item.reason ?? "이 프로젝트에 지도·시나리오를 먼저 연결해 주세요."}</p>}
      <div className="demo-card-actions"><button type="button" className="button" disabled={!item.ready || locked} onClick={() => choose(item)}>{selection?.presetId === item.id ? <Check size={15} /> : <SlidersHorizontal size={15} />}{selection?.presetId === item.id ? "선택한 장면" : "직접 수정하기"}</button>
        {item.ready && item.saved_execution_id && <button type="button" className="button secondary" onClick={() => onStory(item.saved_execution_id!)}><Play size={14} /> 시연 시작</button>}{item.saved_execution_id && <button type="button" className="demo-text-button" onClick={() => setShownExecution({ id: item.saved_execution_id!, retained: true })}><Play size={13} /> 저장 결과</button>}</div>
      {demoSavedPair(item) && <button type="button" className="button secondary" onClick={() => { const pair = demoSavedPair(item); if (pair) onCompare(pair.left, pair.right); }}><GitCompareArrows size={14} /> 저장 A/B 비교</button>}
    </article>)}</div>
    {!!demoPresets && !demoPresets.some((item) => item.ready) && <div className="demo-message"><SlidersHorizontal size={20} /><div><strong>이 프로젝트의 데모 장면을 준비해 주세요.</strong><p>프리셋에는 등록된 지도·객체·시나리오 개정이 필요합니다. 준비되지 않은 장면은 실행되지 않습니다.</p></div><button className="button secondary" onClick={onAdvanced}>고급 편집 열기</button></div>}
    {selection && preset && <div className="demo-editor" aria-label="데모 시나리오 빠른 편집">
      <div className="demo-editor-heading"><div><span className="demo-eyebrow">EDIT & RUN</span><h3>{preset.title}</h3></div><button type="button" className="demo-text-button" onClick={onAdvanced}>고급 편집 <ArrowRight size={15} /></button></div>
      <div className="demo-edit-grid"><div className="demo-map-shell">
        {map && preview ? <DemoMapCanvas key={`${preset.id}:${preview.binding_revision}`} map={map} actors={preview.scenario.actors} selected={selectedActor} disabled={locked || !writable || previewing} onSelect={setSelectedActor} onError={setError} onMove={moveActor} /> : <div className="demo-map-empty"><LoaderCircle size={28} className={previewing ? "demo-spin" : ""} /><p>{previewError ? "장면을 준비할 수 없습니다." : "등록 지도와 객체를 준비하는 중…"}</p></div>}
        {previewing && <span className="demo-map-loading" role="status"><LoaderCircle size={14} className="demo-spin" /> 변경안 준비 중</span>}
      </div><aside className="demo-controls"><div className="demo-controls-heading"><h4>변경안 B</h4><button type="button" className="demo-text-button" disabled={locked} onClick={() => change({ ...(baseline?.selection.parameters ?? preset.defaults) }, structuredClone(baseline?.selection.actorPoses ?? {}))}><RefreshCw size={13} /> 기준 복원</button></div>
        <label className="demo-control">선택 객체<select value={selectedActor} aria-label="데모 선택 객체" onChange={(event) => setSelectedActor(event.target.value)}>{preview?.scenario.actors.map((item) => <option key={item.id} value={item.id}>{item.id} · {item.role === "ego" ? "자차" : (item.kind === "static" || item.controller.kind === "static") ? "장애물" : "주변 객체"}</option>)}</select></label>
        {actor && <div className="demo-actor-info"><strong>{actor.role === "ego" ? "자차" : (actor.kind === "static" || actor.controller.kind === "static") ? "고정 장애물" : "주변 차량"}</strong><span>x {actor.pose.x.toFixed(1)} · y {actor.pose.y.toFixed(1)} m</span><small>지도에서 객체를 끌어 시작 위치를 바꿉니다.</small></div>}
        {preset.parameters.map((parameter) => parameter.type === "boolean" ? <label className="demo-toggle" key={parameter.key}><span>{parameter.label}</span><input type="checkbox" aria-label={parameter.label} checked={Boolean(selection.parameters[parameter.key])} disabled={locked || !writable} onChange={(event) => changeParameter(parameter.key, event.target.checked)} /></label> : <label className="demo-control" key={parameter.key}><span>{parameter.label}<output>{selection.parameters[parameter.key]} {demoParameterUnit(parameter.key)}</output></span><input type="range" aria-label={parameter.label} min={parameter.min} max={parameter.max} step={parameter.step ?? .5} value={Number(selection.parameters[parameter.key])} disabled={locked || !writable} onChange={(event) => changeParameter(parameter.key, Number(event.target.value))} /><div className="demo-range-limits"><small>{parameter.min}</small><small>{parameter.max}</small></div></label>)}
        {preset.id === "urban_right_turn" && <p className="demo-control-note">선행차 대기 0초는 계속 주행, 1–6초는 설정한 시간 동안 정지 후 재출발합니다. 자차의 정책과 설정 속도는 그대로 두고 선행차의 행동만 바꿉니다.</p>}
        {preset.parameters.length === 0 && <p className="demo-control-note">8대의 객체를 선택해 경로와 시작 위치를 살펴보세요. 행동별 세부 설정은 고급 편집에서 제공합니다.</p>}
        <div className="demo-changes"><span>기준 A에서 바뀐 설정</span>{changes.length ? <ul>{changes.map((change) => <li key={change}>{change}</li>)}</ul> : <p>기준과 같은 설정</p>}</div>
      </aside></div>
      {previewError && <p role="alert" className="demo-message error">{previewError}</p>}
      <div className="demo-run-bar"><div><strong>{baseline ? "기준 A 준비됨" : "첫 실행을 기준 A로 저장"}</strong><span>{baseline ? `${baseline.retained ? "저장된 실제 실행" : "이 세션의 실제 실행"} · 같은 지도와 seed로 변경안 작성` : "기준 결과가 없습니다. 먼저 현재 설정을 실행하세요."}</span></div><button type="button" className="button demo-run-button" disabled={!preview || previewing || previewKey !== selectionKey || !!previewError || locked || !writable || native?.status !== "available"} onClick={() => void run()}>{busy ? <LoaderCircle className="demo-spin" size={17} /> : <Play size={17} />}{busy ? stage : baseline ? "변경안 실행" : "기준 실행"}</button></div>
      {!writable && <p className="demo-control-note">시나리오 실행과 편집에는 Engineer 권한이 필요합니다.</p>}
      {native?.status !== "available" && <p className="demo-control-note">CARLA 실행 환경: {capabilities.loading ? "확인 중" : native?.description ?? capabilities.error ?? "연결되지 않음"}. 저장된 결과는 재생할 수 있습니다.</p>}
      <details className="demo-details"><summary>실행 설정 · 출처{preview?.warnings.length ? ` · 작성 정보 참고 (${preview.warnings.length})` : ""}</summary><p>프리셋 r{preset.version} · binding r{preview?.binding_revision ?? "—"} · seed {preview?.scenario.seed ?? "—"} · {preview?.scenario.duration_s ?? "—"}초 · {native?.id ?? "adapter 미연결"} {native?.version}</p><p>객체 GT 기반 CARLA 시뮬레이션. 작성 지도는 도식이며 원본 RGB는 완료된 실행에서 확인합니다.</p>{preview?.warnings.map((warning) => <p key={warning}>{warning}</p>)}{baseline && <code>기준 A: {baseline.id}</code>}</details>
    </div>}
    {error && <div className="demo-message error" role="alert">{error}{executionId && <button type="button" className="button secondary" onClick={() => { setError(""); setPollVersion((v) => v + 1); }}>실행 상태 다시 확인</button>}</div>}
    {notice && <p className="demo-message" role="status">{notice}</p>}
    {executionId && <div className="demo-execution" aria-label="요청한 실제 실행 상태"><div className="demo-execution-title"><span className={`demo-live-dot ${activeRun ? "active" : ""}`} /><div><span className="demo-eyebrow">방금 요청한 실제 실행</span><h3>{execution ? demoStatus(execution.status) : "실행 상태 확인 중"}</h3></div><code>{executionId}</code></div>
      <div className="demo-execution-actions">{activeRun && writable && (execution?.status === "queued" || execution?.cancellation_supported) && <button type="button" className="button secondary" disabled={busy || !!execution?.cancel_requested_at} onClick={() => void cancel()}>{execution?.cancel_requested_at ? "취소 처리 중" : "실행 취소"}</button>}
        {execution?.manifest_hash && <button type="button" className="button secondary" onClick={() => setShownExecution({ id: execution.id, retained: false })}><Play size={15} /> 원본 결과 재생</button>}
        {execution?.status === "completed" && execution.manifest_hash && <button type="button" className="button secondary" onClick={() => onStory(canCompare ? baseline!.id : execution.id, canCompare ? execution.id : undefined)}><Play size={15} /> 시연 시작</button>}{canCompare && <button type="button" className="button" onClick={() => onCompare(baseline!.id, execution!.id)}><GitCompareArrows size={16} /> A/B 나란히 비교</button>}
        {execution?.status === "completed" && execution.id !== baseline?.id && <button className="demo-text-button" onClick={() => { if (runSelection.current) { setBaseline({ id: execution.id, selection: structuredClone(runSelection.current), retained: false }); setSelection(structuredClone(runSelection.current)); setNotice("이 실행을 기준 A로 고정했습니다. 설정을 바꿔 다음 변경안을 실행하세요."); } }}>이 결과를 기준 A로</button>}
        {baseline && activeRun && <button type="button" className="demo-text-button" onClick={() => setShownExecution({ id: baseline.id, retained: baseline.retained })}>기다리는 동안 기준 A 재생 <ArrowRight size={14} /></button>}</div>
      {execution?.error && <p className="demo-message error">{execution.error}</p>}{execution?.outcome_uncertain && <p className="demo-message error">실행 결과가 불확실합니다. 성공한 결과로 간주하지 않습니다.</p>}
    </div>}
    {shownExecution && <section className="demo-recording" aria-label="선택한 실제 실행 재생"><div className="demo-editor-heading"><div><span className="demo-eyebrow">{shownExecution.retained ? "저장된 실제 실행 · 현재 변경안의 결과가 아닙니다" : "이 세션에서 요청한 실제 실행"}</span><h3>원본 결과 재생</h3></div><button type="button" className="button secondary" onClick={() => setShownExecution(null)}>재생 닫기</button></div>{result.loading && <p role="status">원본 실행 결과를 불러오는 중…</p>}{result.error && <p role="alert" className="demo-message error">{result.error}</p>}{result.data && <CitySceneViewer execution={result.data} />}</section>}
  </section>;
}
