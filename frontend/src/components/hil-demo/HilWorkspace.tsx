import { useEffect, useRef, useState } from "react";
import { ArrowRight, Check, Clock3, Cpu, GitCompareArrows, LoaderCircle, Play, RefreshCw, Unplug } from "lucide-react";
import { api, isExternalViewer, useResource } from "../../api";
import HilPresenter from "./HilPresenter";
import { executeHilDemo, hilErrorMessage, hilInitialPreset, hilParameterError, hilSignature, hilStatus, hilTerminal, HilPreviewChanged, isHilExecution } from "./demo-contract";
import type { HilAttempt, HilExecution, HilPreset, HilPreview } from "./demo-contract";

function queryUpdate(values: Record<string, string | null>) {
  const query = new URLSearchParams(location.hash.split("?")[1] ?? "");
  for (const [key, value] of Object.entries(values)) if (value === null) query.delete(key); else query.set(key, value);
  history.replaceState(null, "", `${location.pathname}${location.search}#hil-sim?${query}`);
}
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
function HilError({ message }: { message: string }) {
  const readable = hilErrorMessage(message);
  return <div className="hil-error" role="alert"><span>{readable}</span>{readable !== message && <details><summary>원본 오류 상세</summary><pre>{message}</pre></details>}</div>;
}

export default function HilWorkspace({ project }: { project: string }) {
  const initial = useRef(new URLSearchParams(location.hash.split("?")[1] ?? ""));
  const presets = useResource<{ items: HilPreset[] }>(`/hil-sim/presets?project_id=${encodeURIComponent(project)}`);
  const identity = useResource<{ role: string; mode: string }>("/team/whoami");
  const [presetId, setPresetId] = useState(initial.current.get("preset") ?? "input_delay"), [parameters, setParameters] = useState<Record<string, number> | null>(null);
  const [preview, setPreview] = useState<HilPreview | null>(null), [previewKey, setPreviewKey] = useState(""), [preparing, setPreparing] = useState(false), [busy, setBusy] = useState(false), [stage, setStage] = useState("");
  const [error, setError] = useState(""), [pollError, setPollError] = useState(""), [resultId, setResultId] = useState(initial.current.get("result") ?? initial.current.get("execution")), [runId, setRunId] = useState(initial.current.get("run"));
  const [run, setRun] = useState<HilExecution | null>(null), [pollVersion, setPollVersion] = useState(0), [cancelling, setCancelling] = useState(false);
  const attempt = useRef<HilAttempt | null>(null), submitting = useRef(false), previewRequest = useRef(0), initialized = useRef(false);
  const result = useResource<HilExecution>(resultId ? `/experiments/executions/${encodeURIComponent(resultId)}` : null);
  const preset = presets.data?.items.find(item => item.id === presetId), parameter = preset?.parameters[0];
  const activeRun = !!runId && (!run || !hilTerminal(run.status)), locked = busy || activeRun;
  const writable = !isExternalViewer() && !!identity.data && (identity.data.mode !== "project" || ["Engineer", "Admin"].includes(identity.data.role));
  const value = parameter ? parameters?.[parameter.key] ?? parameter.default : 0;
  const parameterError = parameter ? hilParameterError(parameter, value) : "";
  const signature = preset && parameters ? hilSignature(project, preset, parameters) : "";
  const previewCurrent = !!preview && !!signature && previewKey === signature;

  useEffect(() => {
    if (initialized.current || !presets.data || (initial.current.get("result") || initial.current.get("execution")) && !initial.current.get("preset") && result.loading) return;
    initialized.current = true;
    const chosen = hilInitialPreset(presets.data.items, initial.current.get("preset"), result.data);
    if (!chosen) return;
    const p = chosen.parameters[0], requested = initial.current.get("value"), next = { ...chosen.defaults };
    if (p && requested !== null && !hilParameterError(p, Number(requested))) next[p.key] = Number(requested);
    setPresetId(chosen.id); setParameters(next);
    const saved = initial.current.get("result") ?? initial.current.get("execution") ?? chosen.saved_execution_id;
    setResultId(saved); queryUpdate({ project, preset: chosen.id, value: p ? String(next[p.key]) : null, result: saved, execution: null });
  }, [presets.data, project, result.data, result.loading]);
  useEffect(() => {
    if (!runId) return;
    let active = true, timer: ReturnType<typeof setTimeout> | undefined; const controller = new AbortController();
    async function poll() {
      try {
        const next = await api<HilExecution>(`/experiments/executions/${encodeURIComponent(runId!)}`, { signal: controller.signal });
        if (!active) return;
        if (!isHilExecution(next, project)) throw new Error("선택한 프로젝트의 HIL 실행과 다릅니다.");
        setRun(next); setPollError("");
        if (!hilTerminal(next.status)) timer = setTimeout(() => void poll(), 2000);
      } catch (reason) { if (active) setPollError(errorText(reason)); }
    }
    void poll(); return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [runId, project, pollVersion]);
  function choose(item: HilPreset) {
    if (locked) return;
    ++previewRequest.current; setPreparing(false); setPresetId(item.id); setParameters({ ...item.defaults }); setPreview(null); setPreviewKey(""); setError(""); setPollError(""); setRun(null); setRunId(null); setResultId(item.saved_execution_id); attempt.current = null;
    queryUpdate({ project, preset: item.id, value: String(item.defaults[item.parameters[0].key]), result: item.saved_execution_id, run: null });
  }
  function change(next: number) {
    if (!preset || !parameter || locked) return;
    ++previewRequest.current; setPreparing(false); setParameters({ ...parameters, [parameter.key]: next }); setPreview(null); setPreviewKey(""); setError(""); attempt.current = null; queryUpdate({ value: Number.isFinite(next) ? String(next) : null });
  }
  async function prepare() {
    if (!preset || !parameters || !preset.ready || parameterError || preparing || locked) return;
    const request = ++previewRequest.current; setPreparing(true); setError(""); setPreview(null);
    try {
      const next = await api<HilPreview>(`/hil-sim/presets/${encodeURIComponent(preset.id)}/preview`, { method: "POST", body: JSON.stringify({ project_id: project, expected_binding_revision: preset.binding_revision, parameters }) });
      if (request === previewRequest.current) { setPreview(next); setPreviewKey(signature); }
    } catch (reason) { if (request === previewRequest.current) setError(errorText(reason)); }
    finally { if (request === previewRequest.current) setPreparing(false); }
  }
  async function execute() {
    if (!previewCurrent || !preview || !writable || locked || submitting.current) return;
    submitting.current = true; setBusy(true); setError("");
    if (!attempt.current || attempt.current.signature !== signature || attempt.current.execution) attempt.current = { key: `hil-demo-${crypto.randomUUID()}`, signature };
    try {
      const submitted = await executeHilDemo(api, preview, attempt.current, setStage);
      setRun(null); setRunId(submitted.id); setPollError(""); queryUpdate({ run: submitted.id });
    } catch (reason) {
      if (reason instanceof HilPreviewChanged) { setPreview(null); setPreviewKey(""); attempt.current = null; }
      setError(errorText(reason));
    } finally { setBusy(false); submitting.current = false; setStage(""); }
  }
  async function cancelRun() {
    if (!runId || !activeRun || cancelling || !writable) return;
    setCancelling(true); setError("");
    try { await api(`/experiments/executions/${encodeURIComponent(runId)}/cancel`, { method: "POST" }); setPollVersion(n => n + 1); }
    catch (reason) { setError(errorText(reason)); }
    finally { setCancelling(false); }
  }
  function openResult(id: string) { setResultId(id); queryUpdate({ result: id }); }
  const playable = result.data && result.data.id === resultId && isHilExecution(result.data, project) && result.data.status === "completed" ? result.data : null;
  return <section className="hil-demo" aria-label="HIL Workbench">
    <header className="hil-hero"><div><span className="hil-eyebrow">INJECT. OBSERVE. COMPARE.</span><h1>입력이 끊겼을 때,<br/><em>판단은 어떻게 달라질까요?</em></h1><p>객체 입력에 고장을 주입하고, 별도 프로세스 DUT의 판단과 차량 반응을 함께 보세요.</p></div><div className="hil-simulation-badge"><Cpu size={22}/><strong>Software DUT</strong><span>CARLA Simulation</span></div></header>
    <div className="hil-topology" aria-label="Software DUT 폐루프 연결"><span>CARLA<br/><small>차량·환경</small></span><ArrowRight/><span>객체 입력<br/><small>Simulator GT</small></span><ArrowRight/><span className="hil-topology-fault">고장 주입<br/><small>지연 · 단절</small></span><ArrowRight/><span className="hil-topology-dut">Software DUT<br/><small>별도 프로세스</small></span><ArrowRight/><span>차량 제어<br/><small>다음 CARLA 상태로 ↩</small></span></div>
    {presets.loading && <p role="status">프리셋을 불러오는 중…</p>}{presets.error && <div className="hil-error" role="alert">{presets.error}<button onClick={presets.reload}>다시 읽기</button></div>}
    <div className="hil-presets">{presets.data?.items.map((item, i) => { const Icon = item.id === "input_delay" ? Clock3 : Unplug; return <button key={item.id} className={item.id === presetId ? "selected" : ""} aria-pressed={item.id === presetId} disabled={locked} onClick={() => choose(item)}><Icon size={25}/><span><small>0{i + 1} · {item.id === "input_delay" ? "INPUT DELAY" : "INPUT DROPOUT"}</small><strong>{item.title}</strong></span><span className="hil-preset-state">{item.saved_execution_id ? "저장 A/B" : item.ready ? "실행 준비" : "입력 연결 필요"}</span></button>; })}</div>
    {preset && parameter && <div className="hil-workspace-grid"><aside className="hil-editor"><span className="hil-eyebrow">NEXT RUN</span><h2>다음 실행의 고장</h2><p>{preset.description}</p><label htmlFor="hil-value">B · {parameter.label}</label><div className="hil-input-row"><input id="hil-value" aria-label={`변경 B ${parameter.label}`} type="number" min={parameter.min} max={parameter.max} step={parameter.step} value={Number.isFinite(value) ? value : ""} disabled={locked} onChange={event => change(event.target.value === "" ? NaN : Number(event.target.value))}/><span>{parameter.unit}</span><button aria-label="고장 기본값으로 복원" disabled={locked} onClick={() => change(parameter.default)}><RefreshCw size={16}/></button></div><input aria-label={`${parameter.label} 슬라이더`} type="range" min={parameter.min} max={parameter.max} step={parameter.step} value={Number.isFinite(value) ? value : parameter.default} disabled={locked} onChange={event => change(Number(event.target.value))}/><div className="hil-range"><span>{parameter.min} {parameter.unit}</span><span>{parameter.max} {parameter.unit}</span></div><div className="hil-ab-key"><span><i/>A · 정상 입력</span><span><i/>B · {Number.isFinite(value) ? value : "—"} {parameter.unit} {preset.id === "input_delay" ? "지연" : "단절"}</span></div><p className="hil-fixed">Town10HD_Opt · 앞차 정지 장면<br/>18초 · seed {preset.source?.seed ?? 42}<br/>전방·추적 RGB · 640×360 · 10Hz</p><p className="hil-saved-note">위 값은 다음 실행에 적용됩니다.<br/>오른쪽은 해당 실행에 저장된 결과입니다.</p>
      {parameterError && <p className="hil-error" role="status">{parameterError}</p>}{!preset.ready && <p className="hil-error">{preset.reason ?? "실행 입력 연결이 필요합니다."}</p>}
      {previewCurrent ? <div className="hil-preview"><strong><Check size={16}/>변경 내용 확인됨</strong>{preview.changes.map(item => <p key={item.key}>{item.label}: {item.baseline} → {item.value} {item.unit}</p>)}<small>같은 초기 조건에서 A와 B를 각각 실행합니다.</small><button className="hil-primary" disabled={!writable || locked} onClick={() => void execute()}>{busy ? <LoaderCircle className="hil-spin" size={16}/> : <Play size={16}/>} {busy ? stage : "확인하고 A/B 실행"}</button></div> : <button className="hil-primary" disabled={!preset.ready || !!parameterError || preparing || locked || !writable} onClick={() => void prepare()}>{preparing ? <LoaderCircle className="hil-spin" size={16}/> : <GitCompareArrows size={16}/>} {preparing ? "실행 준비 확인 중…" : "변경 내용 확인"}</button>}
      {!writable && identity.data && <p className="hil-fixed">저장 결과를 재생할 수 있습니다. 새 실행은 Engineer 권한이 필요합니다.</p>}{identity.error && <p className="hil-error" role="alert">{identity.error}</p>}{error && <HilError message={error}/>}<details><summary>고정 입력 · 연결</summary><p>프리셋 binding r{preset.binding_revision ?? "—"}<br/>{preset.source?.adapter_version ?? "adapter 미연결"}</p><code>{preset.saved_execution_id ?? "대표 실행 미등록"}</code><p>객체 입력은 Simulator GT이며 RGB는 관측 영상입니다. 실제 ECU와 센서 검증은 후속 범위입니다.</p></details>
    </aside><div className="hil-result">{runId && <div className={`hil-run-status ${["failed", "interrupted"].includes(run?.status ?? "") ? "failed" : ""}`} role="status"><div><strong>{run ? hilStatus(run.status) : "실행 상태 확인 중"}</strong><small>{runId}</small>{run?.error && <HilError message={run.error}/>}{run?.outcome_uncertain && <p>실행 결과가 불확실합니다. 실행 기록을 확인하세요.</p>}{pollError && <HilError message={pollError}/>}</div><div>{run?.status === "completed" ? <button onClick={() => openResult(run.id)}>새 A/B 결과 열기<ArrowRight size={15}/></button> : pollError ? <button onClick={() => setPollVersion(n => n + 1)}>같은 실행 다시 확인</button> : activeRun && <LoaderCircle size={18} className="hil-spin"/>}{activeRun && writable && run?.cancellation_supported !== false && <button disabled={cancelling} onClick={() => void cancelRun()}>{cancelling ? "취소 요청 중…" : "실행 취소"}</button>}</div></div>}
    {resultId && <div className="hil-result-origin"><span>{resultId === preset.saved_execution_id ? "저장된 대표 A/B" : "선택한 실행 A/B"} · {result.data?.id === resultId && result.data.created_at ? new Date(result.data.created_at).toLocaleString("ko-KR") : resultId.slice(0, 12)}</span>{preset.saved_execution_id && resultId !== preset.saved_execution_id && <button onClick={() => openResult(preset.saved_execution_id!)}>대표 결과 열기</button>}</div>}
    {result.loading && resultId && <div className="hil-empty" role="status"><LoaderCircle className="hil-spin"/>저장된 A/B 실행을 여는 중…</div>}{result.error && <div className="hil-error" role="alert">{result.error}<button onClick={result.reload}>결과 다시 읽기</button></div>}{result.data && result.data.id === resultId && !playable && <p className="hil-error" role="alert">현재 프로젝트의 완료된 Software DUT · CARLA 실행이 아닙니다. 실행 상태와 원본을 확인하세요.</p>}
    {playable && <HilPresenter key={playable.id} execution={playable} presetId={preset.id}/>}{!resultId && <div className="hil-empty"><Cpu size={44}/><span className="hil-eyebrow">READY FOR THE LOOP</span><h2>입력에서 차량 반응까지,<br/>한 화면에서 비교합니다.</h2><p>고장 값을 확인하고 첫 A/B 실행을 시작하세요.<br/>완료된 실제 실행의 영상·신호·이벤트가 표시됩니다.</p></div>}</div></div>}
  </section>;
}
