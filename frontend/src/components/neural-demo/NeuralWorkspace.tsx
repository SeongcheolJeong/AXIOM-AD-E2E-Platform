import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowDownToLine, ArrowLeft, ArrowLeftRight, ArrowRight, ArrowUp, Box, Camera, Check, LoaderCircle, Play, RefreshCw } from "lucide-react";
import { api, isExternalViewer, useResource } from "../../api";
import NeuralReview from "./NeuralReview";
import { executeNeural, isNeuralExecution, neuralParameterError, neuralSignature, neuralSourceLabel, neuralPresetForSource, neuralStatus, neuralTerminal, NeuralPreviewChanged } from "./demo-contract";
import type { NeuralAttempt, NeuralExecution, NeuralMode, NeuralParameters, NeuralPreset, NeuralPreview, NeuralSources } from "./demo-contract";

const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
function queryUpdate(values: Record<string, string | null>) {
  const params = new URLSearchParams(location.hash.split("?")[1] ?? "");
  for (const [key, value] of Object.entries(values)) if (value === null) params.delete(key); else params.set(key, value);
  history.replaceState(null, "", `${location.pathname}${location.search}#neural-sim?${params}`);
}
type Attempt = NeuralAttempt & { preview?: NeuralPreview };
export default function NeuralWorkspace({ project }: { project: string }) {
  const initial = useRef(new URLSearchParams(location.hash.split("?")[1] ?? "")), initialized = useRef(false), submitting = useRef(false), attempt = useRef<Attempt | null>(null);
  const presets = useResource<{ items: NeuralPreset[] }>(`/neural-sim/presets?project_id=${encodeURIComponent(project)}`);
  const sources = useResource<NeuralSources>(`/neural-sim/sources?project_id=${encodeURIComponent(project)}`);
  const identity = useResource<{ role: string; mode: string }>("/team/whoami");
  const [presetId, setPresetId] = useState(initial.current.get("preset") ?? "left"), [parameters, setParameters] = useState<NeuralParameters>({ lateral_m: -.3, height_m: 0 });
  const [sourceId, setSourceId] = useState(""), [reconstructionId, setReconstructionId] = useState(""), [frameIndex, setFrameIndex] = useState(0);
  const [resultId, setResultId] = useState(initial.current.get("result") ?? initial.current.get("execution")), [runId, setRunId] = useState(initial.current.get("run"));
  const [run, setRun] = useState<NeuralExecution | null>(null), [pollVersion, setPollVersion] = useState(0), [error, setError] = useState(""), [pollError, setPollError] = useState("");
  const [busy, setBusy] = useState(false), [binding, setBinding] = useState(false), [stage, setStage] = useState(""), [notice, setNotice] = useState("");
  const result = useResource<NeuralExecution>(resultId ? `/experiments/executions/${encodeURIComponent(resultId)}` : null);
  const selectedSource = sources.data?.sources.find(item => item.id === sourceId);
  const sourcePresets = presets.data?.items.map(item => neuralPresetForSource(item, selectedSource));
  const preset = sourcePresets?.find(item => item.id === presetId), activeRun = !!runId && (!run || !neuralTerminal(run.status)), locked = busy || activeRun || binding;
  const writable = !isExternalViewer() && !!identity.data && (identity.data.mode !== "project" || ["Engineer", "Admin"].includes(identity.data.role));
  const defaultMode = preset?.default_mode ?? "sequence", isSweep = defaultMode === "camera_sweep";
  const parameterError = neuralParameterError(parameters, defaultMode);
  const maxFrameIndex = Math.max(0, (selectedSource?.frame_count ?? 200) - 1), frameError = !Number.isSafeInteger(frameIndex) || frameIndex < 0 || frameIndex > maxFrameIndex;
  const reconstructions = sources.data?.reconstructions.filter(item => item.source_execution_id === sourceId) ?? [];
  const sceneReady = !!preset?.ready && preset.source?.source_execution_id === sourceId && preset.source?.reconstruction_execution_id === reconstructionId;
  const onFrameChange = useCallback((index: number) => { if (!isSweep) setFrameIndex(index); }, [isSweep]);
  useEffect(() => {
    if (initialized.current || !presets.data || !sources.data) return;
    initialized.current = true;
    const rawChosen = presets.data.items.find(item => item.id === initial.current.get("preset")) ?? presets.data.items.find(item => item.id === "sweep" && item.saved_execution_id) ?? presets.data.items.find(item => item.saved_execution_id) ?? presets.data.items[0];
    if (!rawChosen) return;
    const source = sources.data.sources.find(item => item.id === rawChosen.source?.source_execution_id) ?? sources.data.sources.find(item => item.ready !== false);
    const chosen = neuralPresetForSource(rawChosen, source);
    setPresetId(chosen.id); setParameters({ ...chosen.defaults }); setFrameIndex(chosen.default_frame_index ?? 0);
    setSourceId(chosen.source?.source_execution_id ?? source?.id ?? "");
    setReconstructionId(chosen.source?.reconstruction_execution_id ?? "");
    const saved = initial.current.get("result") ?? initial.current.get("execution") ?? chosen.saved_execution_id;
    setResultId(saved); queryUpdate({ project, preset: chosen.id, result: saved, execution: null });
  }, [presets.data, sources.data, project]);
  useEffect(() => {
    if (!runId) return;
    let active = true, timer: ReturnType<typeof setTimeout> | undefined; const controller = new AbortController();
    async function poll() {
      try {
        const next = await api<NeuralExecution>(`/experiments/executions/${encodeURIComponent(runId!)}`, { signal: controller.signal });
        if (!active) return;
        if (!isNeuralExecution(next, project)) throw new Error("현재 프로젝트의 Neural Sim 실행과 다릅니다.");
        setRun(next); setPollError("");
        if (!neuralTerminal(next.status)) timer = setTimeout(() => void poll(), 2000);
        else sources.reload();
      } catch (reason) { if (active) setPollError(errorText(reason)); }
    }
    void poll(); return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [runId, project, pollVersion, sources.reload]);
  function choose(item: NeuralPreset) {
    setPresetId(item.id); setParameters({ ...item.defaults }); setFrameIndex(item.default_frame_index ?? 0); setError("");
    if (item.source) { setSourceId(item.source.source_execution_id); setReconstructionId(item.source.reconstruction_execution_id ?? ""); }
    if (item.saved_execution_id) setResultId(item.saved_execution_id);
    queryUpdate({ preset: item.id, ...(item.saved_execution_id ? { result: item.saved_execution_id, perception: null } : {}) });
  }
  async function connectScene() {
    if (!sourceId || !writable || locked || !presets.data) return;
    setBinding(true); setError(""); setNotice("");
    try {
      for (const item of presets.data.items) {
        const same = item.source?.source_execution_id === sourceId && item.source?.reconstruction_execution_id === (reconstructionId || null);
        await api(`/neural-sim/presets/${encodeURIComponent(item.id)}/bindings`, { method: "POST", body: JSON.stringify({ project_id: project, expected_revision: item.binding_revision ?? 0, source_execution_id: sourceId, reconstruction_execution_id: reconstructionId || null, saved_execution_id: same ? item.saved_execution_id : null, seed: 42 }) });
      }
      setNotice(reconstructionId ? "프리셋에 학습 장면을 연결했습니다. 새 시점을 생성할 수 있습니다." : "원본 로그를 연결했습니다. 학습 실행으로 3D 장면을 준비하세요.");
    } catch (reason) { setError(errorText(reason)); }
    finally { setBinding(false); presets.reload(); }
  }
  async function execute(kind: "neural_reconstruction" | "neural_render", mode: NeuralMode = "sequence") {
    if (!writable || locked || submitting.current || !sourceId || kind === "neural_render" && (!preset || !sceneReady || parameterError || mode !== "sequence" && frameError)) return;
    const signature = kind === "neural_reconstruction" ? JSON.stringify([project, sourceId, kind]) : neuralSignature(project, preset!, parameters, mode, frameIndex);
    if (!attempt.current || attempt.current.signature !== signature || attempt.current.execution) attempt.current = { key: `neural-${crypto.randomUUID()}`, signature };
    const current = attempt.current;
    submitting.current = true; setBusy(true); setError(""); setNotice(""); setStage("실행 입력 확인 중");
    try {
      if (!current.preview) {
        if (kind === "neural_render") current.preview = await api<NeuralPreview>(`/neural-sim/presets/${encodeURIComponent(preset!.id)}/preview`, { method: "POST", body: JSON.stringify({ project_id: project, expected_binding_revision: preset!.binding_revision, parameters, mode, frame_index: mode === "sequence" ? 0 : frameIndex }) });
        else {
          const experiment = { project_id: project, name: `Neural Scene · ${selectedSource?.name ?? sourceId}`.slice(0, 180), kind, adapter_id: "neural-splatfacto", adapter_version: "1.0.0", comparison_kind: "single", seed: 42, input: { source_execution_id: sourceId, rig: "rig_a", camera: "front" } };
          const preview = await api<NeuralPreview["preview"]>("/experiments/preview", { method: "POST", body: JSON.stringify(experiment) });
          current.preview = { experiment, preview };
        }
      }
      const submitted = await executeNeural(api, current.preview, current, setStage);
      setRun(null); setRunId(submitted.id); setPollError(""); queryUpdate({ run: submitted.id });
    } catch (reason) { if (reason instanceof NeuralPreviewChanged) attempt.current = null; setError(errorText(reason)); }
    finally { submitting.current = false; setBusy(false); setStage(""); }
  }
  async function cancelRun() {
    if (!runId || !activeRun || !writable || busy) return;
    setBusy(true); setError("");
    try { await api(`/experiments/executions/${encodeURIComponent(runId)}/cancel`, { method: "POST" }); setPollVersion(value => value + 1); }
    catch (reason) { setError(errorText(reason)); } finally { setBusy(false); }
  }
  async function saveRepresentative() {
    if (!preset || !result.data || !writable || locked) return;
    setBinding(true); setError("");
    try {
      await api(`/neural-sim/presets/${encodeURIComponent(preset.id)}/bindings`, { method: "POST", body: JSON.stringify({ project_id: project, expected_revision: preset.binding_revision ?? 0, source_execution_id: result.data.summary?.source_execution_id, reconstruction_execution_id: result.data.summary?.reconstruction_execution_id, saved_execution_id: result.data.id, seed: 42 }) });
      setNotice(`${preset.title}의 대표 영상을 저장했습니다.`); presets.reload();
    } catch (reason) { setError(errorText(reason)); } finally { setBinding(false); }
  }
  function openResult(id: string) { setResultId(id); queryUpdate({ result: id, perception: null }); }
  const playable = result.data && result.data.id === resultId && isNeuralExecution(result.data, project) && result.data.experiment_kind === "neural_render" && result.data.status === "completed" ? result.data : null;
  const representativeReady = !!playable && !!preset && playable.summary?.lateral_m === preset.defaults.lateral_m && playable.summary?.height_m === preset.defaults.height_m && playable.summary?.mode === defaultMode && playable.summary?.frame_index === (preset.default_frame_index ?? 0) && playable.summary?.source_execution_id === sourceId && playable.summary?.reconstruction_execution_id === reconstructionId;
  return <section className="neural-demo" aria-label="Neural Sim Workbench">
    <header className="neural-hero"><div><span className="neural-eyebrow">NEURAL SIM · FROM LOG TO NEW VIEW</span><h1>기록한 길을, <em>새로운 시점으로.</em></h1><p>장면의 시간은 고정하고, 카메라를 움직여 기록에 없던 시점을 봅니다.</p></div><span className="neural-hero-tag">{neuralSourceLabel(String(playable?.summary?.source_kind ?? selectedSource?.source_kind ?? ""))}</span></header>
    {(presets.error || sources.error) && <div className="neural-error" role="alert">{presets.error || sources.error}<button onClick={() => { presets.reload(); sources.reload(); }}>다시 읽기</button></div>}
    {!initialized.current && (presets.loading || sources.loading) && <p role="status">장면과 프리셋을 불러오는 중…</p>}
    <details className="neural-setup" open={!sourceId || !reconstructionId}><summary><Box size={16}/>장면 준비 · 원본 로그와 학습 실행<span>{reconstructionId ? "학습 장면 선택됨" : "원본 선택 → GPU 학습 → 장면 연결"}</span></summary><div className="neural-setup-grid"><label>원본 로그 실행<select aria-label="Neural 원본 실행" value={sourceId} disabled={locked || !writable} onChange={event => { const selected = sources.data?.sources.find(item => item.id === event.target.value); const rawPreset = presets.data?.items.find(item => item.id === presetId); setSourceId(event.target.value); setReconstructionId(""); if (rawPreset && !rawPreset.source) { const next = neuralPresetForSource(rawPreset, selected); setParameters({ ...next.defaults }); setFrameIndex(next.default_frame_index ?? 0); } }}><option value="">원본 실행 선택</option>{sources.data?.sources.map(item => <option key={item.id} value={item.id} disabled={item.ready === false}>{item.name} · {item.id.slice(0, 8)}{item.ready === false ? " · 준비 필요" : ""}</option>)}</select></label><label>학습된 장면<select aria-label="Neural 학습 장면" value={reconstructionId} disabled={locked || !writable} onChange={event => setReconstructionId(event.target.value)}><option value="">새 학습이 필요합니다</option>{reconstructionId && !reconstructions.some(item => item.id === reconstructionId) && <option value={reconstructionId}>{reconstructionId}</option>}{reconstructions.map(item => <option key={item.id} value={item.id}>{item.name} · {item.id.slice(0, 8)}</option>)}</select></label><div className="neural-setup-actions"><button disabled={!writable || locked || !sourceId || selectedSource?.ready === false} onClick={() => void execute("neural_reconstruction")}><Play size={15}/>새 3D 장면 학습</button><button disabled={!writable || locked || !sourceId} onClick={() => void connectScene()}><Check size={15}/>{binding ? "연결 중…" : "프리셋에 장면 연결"}</button><button aria-label="Neural 장면 새로고침" disabled={locked} onClick={() => { sources.reload(); presets.reload(); }}><RefreshCw size={15}/></button></div></div>{!sources.loading && !sources.data?.sources.length && <p>사용 가능한 원본 로그가 없습니다. 전방 영상과 카메라 pose가 포함된 도로 로그 또는 CARLA 주행 실행을 먼저 준비하세요.</p>}{selectedSource?.reason && <p>{selectedSource.reason}</p>}<p>{selectedSource && <strong>{neuralSourceLabel(selectedSource.source_kind)} · {selectedSource.frame_count ?? "—"} frames<br/></strong>}학습은 사전에 준비하고 발표 중에는 저장 영상과 새 렌더를 이용합니다. 전방 RGB · 알려진 카메라 pose · Splatfacto.</p></details>
    <div className="neural-presets">{sourcePresets?.map((item, i) => { const Icon = item.id === "sweep" ? ArrowLeftRight : item.id === "left" ? ArrowLeft : item.id === "right" ? ArrowRight : ArrowUp; return <button key={item.id} aria-label={`${item.title} Neural 프리셋`} aria-pressed={presetId === item.id} onClick={() => choose(item)}><span className="neural-preset-icon"><Icon size={25}/></span><div><small>{item.id === "sweep" ? "시간 고정 · 카메라 이동" : `0${i + 1} · NEW PERSPECTIVE`}</small><strong>{item.title}</strong><span>{item.default_mode === "camera_sweep" ? `좌우 ±${item.defaults.lateral_m} m · 원본 frame ${item.default_frame_index ?? 0} 고정` : `좌우 ${item.defaults.lateral_m > 0 ? "+" : ""}${item.defaults.lateral_m} m · 높이 ${item.defaults.height_m > 0 ? "+" : ""}${item.defaults.height_m} m`}</span></div><i className={item.saved_execution_id ? "saved" : ""}>{item.saved_execution_id ? "저장 영상" : item.ready ? "생성 가능" : "장면 준비"}</i></button>; })}</div>
    <div className={`neural-workspace-grid ${isSweep ? "neural-showcase-workspace" : ""}`}><details className="neural-editor-drawer" open={!isSweep}><summary><Camera size={16}/>다음 카메라 이동 생성<span>이동 범위 · 고정할 원본 시각</span></summary><aside className="neural-editor"><span className="neural-eyebrow">NEXT VIEW</span><h2>카메라를 옮겨보세요.</h2><p>변경한 위치에서 학습된 장면을 다시 렌더합니다.</p>{!isSweep && <div className="neural-camera-diagram" aria-label="카메라 위치 편집 안내"><div className="neural-road-mark"/><span className="neural-base-camera"><Camera size={22}/><small>기준</small></span><span className="neural-next-camera" style={{ transform: `translate(${parameters.lateral_m * 110}px, ${-parameters.height_m * 90}px)` }}><Camera size={24}/><small>다음 시점</small></span><span className="neural-diagram-caption">위치 편집 도식</span></div>}
      {([{ key: "lateral_m", label: isSweep ? "좌우 이동 반경" : "좌우 이동", min: isSweep ? .1 : -.5, max: isSweep ? 2 : .5 }, { key: "height_m", label: "높이 이동", min: -.3, max: .3 }] as const).map(field => <div className="neural-control" key={field.key}><label htmlFor={`neural-${field.key}`}>{field.label}<span><input id={`neural-${field.key}`} aria-label={`Neural ${field.label}`} type="number" min={field.min} max={field.max} step={.05} value={Number.isFinite(parameters[field.key]) ? parameters[field.key] : ""} onChange={event => setParameters(previous => ({ ...previous, [field.key]: event.target.value === "" ? NaN : Number(event.target.value) }))}/> m</span></label><input aria-label={`Neural ${field.label} 슬라이더`} type="range" min={field.min} max={field.max} step={.05} value={Number.isFinite(parameters[field.key]) ? parameters[field.key] : 0} onChange={event => setParameters(previous => ({ ...previous, [field.key]: Number(event.target.value) }))}/><div className="neural-range"><span>{field.min} m</span><span>+{field.max} m</span></div></div>)}
      <p className="neural-editor-note">입력값은 <strong>다음 생성</strong>에 적용됩니다.<br/>영상의 카메라 위치는 저장 결과를 따릅니다.</p>{parameterError && <p className="neural-error" role="alert">{parameterError}</p>}{!sceneReady && <p className="neural-note">{preset?.reason ?? "원본 로그와 학습 장면을 연결하면 새 시점을 생성할 수 있습니다."}</p>}
      <label className="neural-frame-input">{isSweep ? "고정할 원본 frame" : "미리보기 원본 frame"}<input aria-label="Neural 미리보기 프레임" type="number" min={0} max={maxFrameIndex} step={1} value={Number.isFinite(frameIndex) ? frameIndex : ""} onChange={event => setFrameIndex(event.target.value === "" ? NaN : Number(event.target.value))}/></label>
      {frameError && <p className="neural-error" role="alert">원본 frame은 0–{maxFrameIndex}의 정수로 선택하세요.</p>}{!isSweep && <button className="neural-secondary" disabled={!writable || locked || !sceneReady || !!parameterError || frameError} onClick={() => void execute("neural_render", "frame")}><Camera size={17}/>현재 프레임 생성</button>}<button className="neural-primary" disabled={!writable || locked || !sceneReady || !!parameterError || isSweep && frameError} onClick={() => void execute("neural_render", defaultMode)}>{busy ? <LoaderCircle size={17} className="neural-spin"/> : <Play size={17}/>} {busy ? stage : isSweep ? "고정 시간 · 카메라 이동 생성" : "전체 구간 새 시점 생성"}</button>
      {!writable && identity.data && <p className="neural-note">저장 영상을 재생할 수 있습니다. 새 학습·생성은 Engineer 권한이 필요합니다.</p>}{identity.error && <p className="neural-error" role="alert">{identity.error}</p>}{error && <div className="neural-error" role="alert">{error}</div>}{notice && <p className="neural-notice" role="status">{notice}</p>}
      <a className="neural-details-link" href={`#experiments?project=${encodeURIComponent(project)}&kind=neural_reconstruction&adapter=neural-splatfacto`}>상세 실험 설정 →</a>
    </aside></details><div className="neural-results">
      {runId && <div className={`neural-run ${run?.status === "failed" ? "failed" : ""}`} role="status"><div><strong>{run ? neuralStatus(run.status) : "실행 상태 확인 중"}</strong><small>{runId}</small>{run?.error && <p>{run.error}</p>}{run?.outcome_uncertain && <p>원격 실행 결과가 불확실합니다. 실행 기록을 확인하세요.</p>}{pollError && <p>{pollError}</p>}</div><div>{run?.status === "completed" ? run.experiment_kind === "neural_render" ? <button onClick={() => openResult(run.id)}>새 결과 열기<ArrowRight size={15}/></button> : <button onClick={() => { setReconstructionId(run.id); setSourceId(String(run.summary?.source_execution_id ?? sourceId)); setNotice("학습이 완료됐습니다. 위의 ‘프리셋에 장면 연결’을 눌러 렌더할 장면을 지정하세요."); }}>학습 장면 선택<Check size={15}/></button> : pollError ? <button onClick={() => setPollVersion(value => value + 1)}>상태 다시 확인</button> : activeRun ? <LoaderCircle className="neural-spin" size={18}/> : null}{activeRun && writable && run?.cancellation_supported !== false && <button disabled={busy} onClick={() => void cancelRun()}>실행 취소</button>}</div></div>}
      <div className="neural-result-picker"><label><ArrowDownToLine size={15}/>저장 결과<select aria-label="Neural 저장 결과" value={resultId ?? ""} onChange={event => openResult(event.target.value)}><option value="">결과 선택</option>{resultId && !sources.data?.renders.some(item => item.id === resultId) && <option value={resultId}>{resultId.slice(0, 16)}</option>}{sources.data?.renders.map(item => <option key={item.id} value={item.id}>{item.name} · {item.id.slice(0, 8)}</option>)}</select></label>{preset?.saved_execution_id && <button onClick={() => openResult(preset.saved_execution_id!)}>프리셋 대표 영상</button>}</div>
      {result.loading && resultId && <div className="neural-empty" role="status"><LoaderCircle className="neural-spin"/>저장된 렌더 결과를 읽는 중…</div>}{result.error && <div className="neural-error" role="alert">{result.error}<button onClick={result.reload}>다시 읽기</button></div>}{result.data && result.data.id === resultId && !playable && <p className="neural-error" role="alert">현재 프로젝트의 완료된 Neural 렌더 결과가 아닙니다.</p>}
      {playable && <NeuralReview key={playable.id} execution={playable} writable={writable} onFrameChange={onFrameChange}/>}{representativeReady && writable && playable?.id !== preset?.saved_execution_id && <button className="neural-save-result" disabled={locked} onClick={() => void saveRepresentative()}><Check size={15}/>이 결과를 {preset?.title} 대표 영상으로 저장</button>}{!resultId && <div className="neural-empty"><Box size={54}/><span className="neural-eyebrow">A SCENE. MORE PERSPECTIVES.</span><h2>장면은 하나,<br/>시점은 새롭게.</h2><p>원본 로그를 학습하고 첫 시점을 생성하세요.<br/>실제 렌더가 완료되면 원본과 함께 비교할 수 있습니다.</p><div className="neural-empty-path"><span>원본</span><ArrowRight size={16}/><span>재구성</span><ArrowRight size={16}/><span>새 시점</span></div></div>}
    </div></div>
  </section>;
}
