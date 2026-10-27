import { useEffect, useRef, useState } from "react";
import { ArrowRight, BookmarkPlus, Box, Check, LoaderCircle, Play, RefreshCw } from "lucide-react";
import { api, useResource } from "../../api";
import type { ModelDetail } from "../perception/PerceptionTypes";
import { caseHref } from "../cases/CaseTypes";
import { executeNeural, neuralTerminal } from "./demo-contract";
import type { NeuralAttempt, NeuralExecution, NeuralPreview } from "./demo-contract";
import type { LoadedNeuralResult } from "./load-neural-result";
import { neuralPerceptionIndices, parseNeuralPerception } from "./neural-perception";
import type { NeuralPerceptionResult, NeuralPerceptionRun } from "./neural-perception";

const message = (error: unknown) => error instanceof Error ? error.message : String(error);
const signed = (value: number) => `${value > 0 ? "+" : ""}${Math.abs(value) < 1e-6 ? "0.00" : value.toFixed(2)}`;
function updatePerceptionQuery(id: string) {
  const query = new URLSearchParams(location.hash.split("?")[1] ?? ""); query.set("perception", id);
  history.replaceState(null, "", `${location.pathname}${location.search}#neural-sim?${query}`);
}
export default function NeuralPerceptionPanel({ execution, loaded, writable, displayedFrame, onResult, onInspect }: { execution: NeuralExecution; loaded: LoadedNeuralResult; writable: boolean; displayedFrame: number; onResult: (value: NeuralPerceptionResult) => void; onInspect: (frame: number) => void }) {
  const models = useResource<ModelDetail[]>("/perception/models"), saved = useResource<{ perceptions?: NeuralPerceptionRun[] }>(`/neural-sim/sources?project_id=${encodeURIComponent(execution.project_id)}`);
  const [modelId, setModelId] = useState(""), [threshold, setThreshold] = useState(.35), [busy, setBusy] = useState(false), [stage, setStage] = useState(""), [error, setError] = useState("");
  const [runId, setRunId] = useState<string | null>(null), [run, setRun] = useState<NeuralExecution | null>(null), [pollVersion, setPollVersion] = useState(0);
  const [report, setReport] = useState<{ id: string; result: NeuralPerceptionResult } | null>(null), [loadingReport, setLoadingReport] = useState(false);
  const [note, setNote] = useState(""), [caseBusy, setCaseBusy] = useState(false), [caseResult, setCaseResult] = useState<{ id: string; revision: number } | null>(null);
  const attempted = useRef<NeuralAttempt | null>(null), caseAttempt = useRef({ signature: "", key: "" }), initialized = useRef(false), requested = useRef<string | null>(null), mounted = useRef(true);
  const availableModels = (models.data ?? []).filter(item => ["opencv_yolox_rgb_v1", "boxes_xyxy_v1"].includes(item.profile)), model = availableModels.find(item => item.id === modelId), runs = saved.data?.perceptions?.filter(item => item.render_execution_id === execution.id) ?? [];
  const active = !!runId && (!run || !neuralTerminal(run.status)), indices = neuralPerceptionIndices(loaded.result);
  const currentFrame = report?.result.frames.find(frame => frame.frame_index === displayedFrame), canSaveCase = loaded.result.source_kind === "real_road" && report?.result.case_supported === true;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { if (!modelId && availableModels.length === 1) setModelId(availableModels[0].id); }, [modelId, availableModels]);
  async function openReport(id: string, inspect = false) {
    requested.current = id; setLoadingReport(true); setError("");
    try {
      const raw = await api<unknown>(`/neural-sim/perception/${encodeURIComponent(id)}?project_id=${encodeURIComponent(execution.project_id)}`);
      const parsed = parseNeuralPerception(raw, execution, loaded.result);
      if (!mounted.current || requested.current !== id) return;
      setReport({ id, result: parsed }); onResult(parsed); setCaseResult(null); updatePerceptionQuery(id);
      if (inspect) onInspect(parsed.frames[Math.floor(parsed.frames.length / 2)].frame_index);
    } catch (reason) { if (mounted.current && requested.current === id) setError(message(reason)); }
    finally { if (mounted.current && requested.current === id) setLoadingReport(false); }
  }
  useEffect(() => {
    if (initialized.current || !saved.data) return;
    initialized.current = true;
    const explicit = new URLSearchParams(location.hash.split("?")[1] ?? "").get("perception"), selected = runs.find(item => item.id === explicit) ?? runs[0];
    if (selected) void openReport(selected.id);
    else if (explicit) { setRunId(explicit); setRun(null); }
  }, [saved.data]);
  useEffect(() => {
    if (!runId) return;
    let active = true, timer: ReturnType<typeof setTimeout> | undefined; const controller = new AbortController();
    async function poll() {
      try {
        const next = await api<NeuralExecution>(`/experiments/executions/${encodeURIComponent(runId!)}`, { signal: controller.signal });
        if (!active) return;
        if (next.project_id !== execution.project_id || next.experiment_kind !== "neural_perception" || next.adapter_id !== "neural-onnx-sensitivity") throw new Error("현재 프로젝트의 Neural 모델 비교 실행이 아닙니다.");
        setRun(next);
        if (!neuralTerminal(next.status)) timer = setTimeout(() => void poll(), 1800);
        else if (next.status === "completed") { saved.reload(); void openReport(next.id, true); }
      } catch (reason) { if (active) setError(message(reason)); }
    }
    void poll(); return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [runId, pollVersion, execution.id]);
  async function execute() {
    if (!model || !writable || busy || active || indices.length < 3 || !Number.isFinite(threshold) || threshold < 0 || threshold > 1) return;
    const body = { project_id: execution.project_id, render_execution_id: execution.id, model_id: model.id, model_revision: model.revision, frame_indices: indices, score_threshold: threshold, iou_threshold: .45, max_detections: 100, seed: 42 }, signature = JSON.stringify(body);
    if (!attempted.current || attempted.current.signature !== signature || attempted.current.execution) attempted.current = { signature, key: `neural-model-${crypto.randomUUID()}` };
    setBusy(true); setError(""); setStage("모델 입력 확인 중");
    try {
      const preview = await api<NeuralPreview>("/neural-sim/perception/preview", { method: "POST", body: signature });
      const submitted = await executeNeural(api, preview, attempted.current, text => setStage(text.replace("GPU 작업", "모델 작업")));
      setRun(null); setRunId(submitted.id); updatePerceptionQuery(submitted.id);
    } catch (reason) { setError(message(reason)); } finally { setBusy(false); setStage(""); }
  }
  async function saveCase() {
    if (!report || !currentFrame || !writable || caseBusy || !canSaveCase) return;
    const body = JSON.stringify({ project_id: execution.project_id, frame_index: currentFrame.frame_index, selected_variant: "novel", title: `${report.result.model_snapshot.name} · 시점 ${signed(currentFrame.applied_lateral_m)} m 모델 출력 변화`.slice(0, 180), note: note.trim() || "원본, 같은 시점의 Neural 재구성, 변경 시점에서 관측한 모델 출력의 차이를 조사합니다. 정답 라벨과 비교한 정확도 판정은 포함하지 않습니다." });
    const signature = report.id + body;
    if (caseAttempt.current.signature !== signature) caseAttempt.current = { signature, key: `neural-case-${crypto.randomUUID()}` };
    setCaseBusy(true); setError("");
    try { setCaseResult(await api<{ id: string; revision: number }>(`/neural-sim/perception/${encodeURIComponent(report.id)}/case`, { method: "POST", body, headers: { "Idempotency-Key": caseAttempt.current.key } })); }
    catch (reason) { setError(message(reason)); } finally { setCaseBusy(false); }
  }
  return <section className="neural-perception" aria-label="Neural 모델 출력 비교">
    <header><div><span className="neural-eyebrow">MODEL OUTPUT SENSITIVITY</span><h2>시점이 바뀌면, 모델은 무엇을 볼까요?</h2><p>원본 → 같은 시점 재구성 → 새 시점에 동일한 모델을 실행합니다.</p></div><Box size={26}/></header>
    <div className="neural-model-controls"><label>인지 모델<select aria-label="Neural 인지 모델" value={modelId} disabled={busy || active || !writable} onChange={event => setModelId(event.target.value)}><option value="">등록 모델 선택</option>{availableModels.map(item => <option key={item.id} value={item.id}>{item.name} · r{item.revision}</option>)}</select></label><label>추론 score 기준<input aria-label="Neural 모델 score 기준" type="number" min={0} max={1} step={.05} value={threshold} disabled={busy || active} onChange={event => setThreshold(Number(event.target.value))}/></label><button className="neural-primary" disabled={!writable || !model || busy || active || indices.length < 3 || !Number.isFinite(threshold) || threshold < 0 || threshold > 1} onClick={() => void execute()}>{busy || active ? <LoaderCircle size={16} className="neural-spin"/> : <Play size={16}/>} {busy ? stage : active ? "모델 실행 중" : `${indices.length}개 시점 · 모델 비교 실행`}</button></div>
    {indices.length < 3 && <p className="neural-note">모델 비교는 3개 이상의 시점이 있는 카메라 이동 또는 전체 구간 결과에서 실행할 수 있습니다.</p>}
    {models.error && <p className="neural-error" role="alert">{models.error}<button onClick={models.reload}>모델 다시 읽기</button></p>}{!models.loading && !availableModels.length && <p className="neural-note">등록된 인지 모델이 없습니다. 모델 등록 후 비교를 실행할 수 있습니다.</p>}
    {runId && <div className="neural-perception-status" role="status"><strong>{run?.status === "completed" ? "모델 출력 준비 완료" : run && neuralTerminal(run.status) ? `모델 실행 ${run.status}` : "모델이 각 시점을 읽고 있습니다"}</strong><span>{runId}</span>{run?.error && <p>{run.error}</p>}{error && active && <button onClick={() => setPollVersion(value => value + 1)}><RefreshCw size={14}/>상태 다시 확인</button>}{report && active && <small>화면에는 이전 비교 결과가 유지됩니다.</small>}</div>}
    {!!runs.length && <label className="neural-perception-saved">저장된 모델 비교<select aria-label="Neural 저장 모델 비교" value={report?.id ?? ""} onChange={event => void openReport(event.target.value, true)}><option value="">비교 선택</option>{runs.map(item => <option key={item.id} value={item.id}>{item.name} · r{item.model_revision}</option>)}</select></label>}
    {loadingReport && <p role="status">고정된 모델 출력과 영상 연결을 확인하는 중…</p>}{error && <p className="neural-error" role="alert">{error}</p>}
    {report && <><div className="neural-perception-summary"><strong>{report.result.model_snapshot.name} · r{report.result.model_snapshot.revision}</strong><span>검출 개수 · 원본 / 재구성 / 새 시점</span><small>score는 모델 출력값입니다.</small></div><div className="neural-variant-grid">{report.result.frames.map(frame => <button key={frame.frame_index} aria-label={`모델 비교 시점 ${frame.frame_index}`} aria-pressed={displayedFrame === frame.frame_index} onClick={() => onInspect(frame.frame_index)}><span>좌우 {signed(frame.applied_lateral_m)} m</span><strong>{frame.summary.original_count}<ArrowRight size={12}/>{frame.summary.baseline_count}<ArrowRight size={12}/>{frame.summary.novel_count}</strong><small>새 시점 − 재구성 {frame.summary.novel_count_delta > 0 ? "+" : ""}{frame.summary.novel_count_delta}개</small></button>)}</div><p className="neural-perception-scope">선택한 시점의 검출 박스와 score를 영상에서 비교하세요. 출력 변화는 인식 실패나 정확도 판정과 구분합니다.</p>
      <div className="neural-case-save"><label>조사 메모<input aria-label="Neural Case 메모" maxLength={1500} placeholder="조사할 객체나 관측한 변화" value={note} onChange={event => { setNote(event.target.value); setCaseResult(null); }}/></label><button disabled={!writable || !currentFrame || caseBusy || !canSaveCase} onClick={() => void saveCase()}>{caseBusy ? <LoaderCircle size={16} className="neural-spin"/> : <BookmarkPlus size={16}/>}선택 시점을 Case로 저장</button>{caseResult && <a href={caseHref(caseResult.id, caseResult.revision, execution.project_id)}><Check size={15}/>저장한 Case 열기 →</a>}{!currentFrame && <small>위에서 모델을 실행한 시점을 선택하세요.</small>}{!canSaveCase && <small>원본 도로 recording이 연결된 결과에서 Case로 저장할 수 있습니다.</small>}</div>
      <details className="neural-perception-provenance"><summary>모델 개정과 비교 실행 근거</summary><dl><dt>모델 비교 실행</dt><dd>{report.id}</dd><dt>렌더 실행</dt><dd>{report.result.render_pin.execution_id}</dd><dt>모델 개정 SHA-256</dt><dd>{report.result.model_snapshot.manifest_hash}</dd><dt>가중치 SHA-256</dt><dd>{report.result.model_snapshot.artifact.sha256}</dd></dl><p>{loaded.result.source_kind === "real_road" ? "공개 도로 로그를 이용한 로컬 연구 범위입니다. " : ""}생성 영상의 score 변화에는 재구성 품질과 시점 변경이 함께 영향을 줍니다.</p></details>
    </>}
  </section>;
}
