import { useEffect, useRef, useState } from "react";
import { ArrowRight, Check, ChevronDown, Film, FolderCheck, Layers3, LoaderCircle, Play, RefreshCw, Save, ScanSearch } from "lucide-react";
import { api, authHeaders, isExternalViewer, useResource } from "../../api";
import { ErrorState, Loading } from "../ui";
import LogBetaTools, { LogBatchExport, type ComparisonRef } from "./LogBetaTools";
import LogBatchBoard from "./LogBatchBoard";
import { betaQuery, betaWriteKey, LogBetaOverview, useLogBeta } from "./LogGuidedBeta";
import LogBatchInspector from "./LogBatchInspector";
import { batchIsActive, batchStatusLabel } from "./log-batch-contract";
import type { LogBatch, LogBatchCandidate, LogBatchPreview, LogCaseBundle, LogLibrary, LogLibraryScene } from "./log-batch-contract";
import "../../log-batch.css";

const errorText = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);

export default function LogBatchLibrary({ project, initialBatchId, initialCandidateId, onOpenSource }: {
  project: string; initialBatchId: string | null; initialCandidateId: string | null; onOpenSource: (source: string, caseId?: string) => void;
}) {
  const beta = useLogBeta();
  const task = beta?.task;
  const [comparisonRef,setComparisonRef] = useState<ComparisonRef|null>(null), [settingsDirty,setSettingsDirty] = useState(false);
  const library = useResource<LogLibrary>(`/log-sim/library?project_id=${encodeURIComponent(project)}`);
  const access = useResource<{ mode: string; permissions: { execute: boolean } }>("/team/whoami");
  const history = useResource<{ items: LogBatch[] }>(`/log-sim/batches?project_id=${encodeURIComponent(project)}`);
  const bundles = useResource<{ items: LogCaseBundle[] }>(`/log-sim/case-bundles?project_id=${encodeURIComponent(project)}`);
  const [selectedScenes, setSelectedScenes] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState<{ signature: string; value: LogBatchPreview } | null>(null);
  const [batchId, setBatchId] = useState(initialBatchId ?? ""), [batch, setBatch] = useState<LogBatch | null>(null);
  const [batchRevision, setBatchRevision] = useState(0), [batchError, setBatchError] = useState("");
  const [busy, setBusy] = useState(""), [error, setError] = useState("");
  const [selectedCandidates, setSelectedCandidates] = useState<Set<string>>(new Set());
  const [inspecting, setInspecting] = useState<{ id: string; ids: string[] } | null>(initialCandidateId ? { id: initialCandidateId, ids: [] } : null);
  const [bundleName, setBundleName] = useState("동일 로그 A/B · 검토할 장면");
  const [savedBundle, setSavedBundle] = useState<LogCaseBundle | null>(null);
  const [selectionMessage, setSelectionMessage] = useState("");
  const initialized = useRef(false), submitPending = useRef({ body: "", key: "" }), bundlePending = useRef({ body: "", key: "" });
  const retryPending = useRef<Record<string, string>>({});
  const [pendingPayload, setPendingPayload] = useState("");
  const submissionKey = task ? `axiom.logBeta.request.${task.id}` : "";
  const firstTaskRestore = useRef(true);
  const writable = !isExternalViewer() && !!access.data?.permissions.execute;
  const scenes = library.data?.items ?? [];
  const sourceIds = task ? (task.source_id ? [task.source_id] : []) : [...new Set(scenes.filter(scene => selectedScenes.has(scene.id)).flatMap(scene => scene.segments.map(segment => segment.source_id)))].sort();
  const originalOnly = !task && scenes.filter(scene=>selectedScenes.has(scene.id)).every(scene=>scene.source_kind==='nuscenes') && library.data?.library_id;
  const guidedLocked = !!task?.batch_id || !!beta?.busy || !!beta?.loading || !!beta?.linkPending || !!pendingPayload || (!!task && !task.comparison_ref);
  const body = { project_id: project, ...(originalOnly ? {library_id: library.data?.library_id, selected_scene_ids: [...selectedScenes].sort()} : {}), source_ids: sourceIds, ...(comparisonRef ? {comparison_ref:comparisonRef} : {}) };
  const signature = JSON.stringify(body), currentPreview = preview?.signature === signature ? preview.value : null;
  const active = !!batch && batchIsActive(batch);
  const selectedFrameCount = scenes.filter(scene => selectedScenes.has(scene.id)).reduce((sum, scene) => sum + scene.available_frame_count, 0);
  const candidate = batch?.candidates.find(item => item.id === inspecting?.id);
  const candidateIds = inspecting?.ids.length ? inspecting.ids : batch?.candidates.map(item => item.id) ?? [];
  const candidateIndex = candidate ? candidateIds.indexOf(candidate.id) : -1;
  const candidateScene = candidate ? scenes.find(scene => scene.segments.some(segment => segment.source_id === candidate.source_id)) : undefined;

  useEffect(() => { if (library.data && !task && !beta?.loading && !initialized.current) { initialized.current = true; setSelectedScenes(new Set(library.data.items.map(scene => scene.id))); } }, [library.data, task?.id, beta?.loading]);
  useEffect(() => { if (!task && !beta?.loading && !batchId && history.data?.items[0]) setBatchId(history.data.items[0].id); }, [batchId, history.data, task?.id, beta?.loading]);
  useEffect(() => {
    if (!task) return;
    setComparisonRef(task.comparison_ref); setPreview(null); setBatchId(task.batch_id ?? ""); setBatch(null); setBatchRevision(value => value + 1);
    // The initial deep link already seeded local state. Keep the latest user intent
    // (including a close or another candidate) when that same task arrives late.
    const keepInspectorIntent = firstTaskRestore.current && !!initialBatchId && initialBatchId === task.batch_id;
    setInspecting(current => keepInspectorIntent ? current : null);
    firstTaskRestore.current = false;
    setPendingPayload(!task.batch_id ? sessionStorage.getItem(`axiom.logBeta.request.${task.id}`) ?? "" : "");
  }, [task?.id]);
  useEffect(() => { if (task?.batch_id) { setBatchId(task.batch_id); sessionStorage.removeItem(`axiom.logBeta.request.${task.id}`); setPendingPayload(""); } }, [task?.batch_id]);
  useEffect(() => {
    if (task) setSelectedScenes(new Set(scenes.filter(scene => scene.segments.some(segment => segment.source_id === task.source_id)).map(scene => scene.id)));
  }, [task?.id, task?.source_id, library.data]);
  useEffect(() => {
    if (!batchId) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function read() {
      try {
        const value = await api<LogBatch>(`/log-sim/batches/${encodeURIComponent(batchId)}?project_id=${encodeURIComponent(project)}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setBatch(value); setBatchError("");
        if (batchIsActive(value)) timer = setTimeout(() => void read(), 2000);
      } catch (reason) { if (!controller.signal.aborted) setBatchError(errorText(reason)); }
    }
    void read(); return () => { controller.abort(); clearTimeout(timer); };
  }, [batchId, batchRevision, project]);
  useEffect(() => { setSelectedCandidates(new Set()); setSavedBundle(null); }, [batchId]);

  const openedCandidate = useRef("");
  useEffect(() => {
    if (!candidate || !task || task.batch_id !== batch?.id || batch.status !== "completed" || task.result_opened) return;
    const key = `${task.id}/${candidate.id}`; if (openedCandidate.current === key) return;
    openedCandidate.current = key; void beta!.progress({ result_opened: true }).catch(() => {});
  }, [candidate?.id, task?.id, task?.batch_id, task?.result_opened, batch?.status]);
  function retainBatch(id?: string, candidateId?: string) {
    const query = betaQuery({ project });
    if (id) query.set("batch", id);
    if (candidateId) query.set("candidate", candidateId);
    window.history.replaceState(null, "", `${location.pathname}${location.search}#log-sim?${query}`);
  }
  function toggleScene(id: string) { if (task) return; setSelectedScenes(current => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; }); setError(""); }
  function selectCandidates(ids: string[], checked: boolean) {
    setSelectedCandidates(current => {
      const next = new Set(current);
      if (!checked) ids.forEach(id => next.delete(id));
      else ids.forEach(id => { if (next.size < 100) next.add(id); });
      if (checked && ids.some(id => !next.has(id))) setSelectionMessage("한 묶음에 100개까지 선택할 수 있습니다. 나머지는 다음 묶음으로 저장하세요.");
      else setSelectionMessage("");
      return next;
    });
  }
  function inspect(value: LogBatchCandidate, ids: string[]) { setInspecting({ id: value.id, ids }); retainBatch(batch?.id, value.id); }
  async function prepare() {
    setBusy("preview"); setError(""); setPreview(null);
    try { setPreview({ signature, value: await api<LogBatchPreview>("/log-sim/batches/preview", { method: "POST", body: signature }) }); }
    catch (reason) { setError(errorText(reason)); }
    finally { setBusy(""); }
  }
  async function execute(recover = false) {
    if (!recover && !currentPreview) return;
    setBusy("execute"); setError("");
    const payload = recover ? pendingPayload : JSON.stringify({ ...body, preview_hash: currentPreview!.preview_hash });
    if (!payload) { setBusy(""); return; }
    if (task) { sessionStorage.setItem(submissionKey, payload); setPendingPayload(payload); }
    if (submitPending.current.body !== payload) submitPending.current = { body: payload, key: crypto.randomUUID() };
    try {
      const value = await api<LogBatch>("/log-sim/batches", { method: "POST", body: payload, headers: { "Idempotency-Key": task ? betaWriteKey(`batch.${task.id}`, payload) : submitPending.current.key } });
      setBatch(value); setBatchId(value.id); setBatchRevision(value => value + 1); retainBatch(value.id); setPreview(null); history.reload(); if (task) { await beta!.progress({ batch_id: value.id }); sessionStorage.removeItem(submissionKey); setPendingPayload(""); } submitPending.current = { body: "", key: "" };
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(""); }
  }
  async function retry() {
    if (!batch) return;
    setBusy("retry"); setError("");
    const key = retryPending.current[batch.id] ??= crypto.randomUUID();
    try { await api(`/log-sim/batches/${encodeURIComponent(batch.id)}/retry`, { method: "POST", body: JSON.stringify({ project_id: project }), headers: { "Idempotency-Key": key } }); delete retryPending.current[batch.id]; setBatchRevision(value => value + 1); }
    catch (reason) { setError(errorText(reason)); }
    finally { setBusy(""); }
  }
  async function saveBundle() {
    if (!batch || !selectedCandidates.size) return;
    setBusy("bundle"); setError("");
    const payload = JSON.stringify({ project_id: project, name: bundleName.trim(), candidate_ids: [...selectedCandidates].sort(), report_hash: batch.report_hash });
    const bundleSignature = `${batch.id}/${payload}`;
    if (bundlePending.current.body !== bundleSignature) bundlePending.current = { body: bundleSignature, key: crypto.randomUUID() };
    try {
      const value = await api<LogCaseBundle>(`/log-sim/batches/${encodeURIComponent(batch.id)}/case-bundles`, { method: "POST", body: payload, headers: { "Idempotency-Key": task ? betaWriteKey(`bundle.${task.id}.${batch.id}`, payload) : bundlePending.current.key } });
      setSavedBundle(value); bundles.reload();
      const first = value.items.find(item => item.source_id === task?.source_id);
      if (task?.batch_id === batch.id && first) await beta!.progress({ case_id: first.case_id });
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(""); }
  }

  return <div className="log-batch-library" data-testid="log-batch-library">
    <LogBetaTools key={task?.id ?? "unguided"} pendingExecution={!!pendingPayload} project={project} writable={writable} onRegistered={() => library.reload()} onRecipe={setComparisonRef} onDirty={setSettingsDirty} />
    {pendingPayload && task && !task.batch_id && <section className="log-beta-no-candidates"><h3>실행 요청 확인이 필요합니다.</h3><p>이전에 보낸 원본·조건·요청 키를 유지했습니다. 같은 요청을 확인해 이미 접수된 실행을 연결합니다.</p><button className="button" aria-label="Recover Log Sim beta batch" disabled={!!busy || !!beta?.busy} onClick={() => void execute(true)}>이전 실행 요청 다시 확인</button></section>}
    <LogBetaOverview operator={access.data?.mode === "operator"} />
    <div className="log-batch-intro"><div><span className="log-sim-eyebrow">PROJECT LOG LIBRARY</span><h2>여러 주행에서, 검토할 장면만.</h2><p>원본 장면 선택 → 사건 탐색과 동일 로그 A/B → 차이가 큰 구간 조사 → Case 묶음 저장</p></div><span className="log-batch-research"><Film size={15} />프로젝트 원본 · 출처별 사용 범위</span></div>
    {library.loading && <Loading label="원본 장면 라이브러리를 불러오는 중…" />}
    {library.error && <ErrorState message={library.error} retry={library.reload} />}
    {library.data && <>
      <div className="log-batch-library-stats"><article><small>원본 장면·로그</small><strong>{library.data.counts.scene_count}<span>scenes</span></strong></article><article><small>분석 가능한 원본 구간</small><strong>{library.data.counts.segment_count}<span>segments</span></strong></article><article><small>보존된 입력 표본</small><strong>{library.data.counts.available_frame_count}<span>frames</span></strong></article><article className={library.data.counts.excluded_frame_count ? "has-exclusions" : ""}><small>제외·미선택</small><strong>{library.data.counts.excluded_frame_count}<span>/ {library.data.counts.original_frame_count} frames</span></strong></article></div>
      <div className="log-batch-library-controls">{task && <p>이 업무는 선택한 팩의 정확한 원본 구간 1개로 진행합니다.</p>}<label><input type="checkbox" aria-label="Select all Log Sim scenes" disabled={!!task} checked={scenes.length > 0 && scenes.every(scene => selectedScenes.has(scene.id))} onChange={event => setSelectedScenes(new Set(event.target.checked ? scenes.map(scene => scene.id) : []))} />모든 장면 선택</label><span>{selectedScenes.size}개 장면 · {sourceIds.length}개 구간 · {selectedFrameCount}개 입력 표본 선택</span><small>원본 데이터셋 설명과 사용자 등록 로그를 구분합니다.</small></div>
      <div className="log-batch-scene-grid">{scenes.filter(scene => !task || selectedScenes.has(scene.id)).map(scene => <SceneCard key={scene.id} scene={scene} checked={selectedScenes.has(scene.id)} onToggle={() => toggleScene(scene.id)} locked={!!task} />)}</div>
      <div className="log-batch-launch"><div><ScanSearch size={23} /><div><strong>선택한 주행 전체에서 차이를 찾습니다.</strong><span>동일한 객체·ego 입력 · 저장한 A/B 설정 적용 · 원본 구간별 실행</span></div></div><button className="button" aria-label="Preview Log Sim batch" disabled={!sourceIds.length || !writable || !!busy || active || settingsDirty || guidedLocked} onClick={() => void prepare()}>{busy === "preview" ? <LoaderCircle size={16} className="spin" /> : <Layers3 size={16} />}{selectedScenes.size}개 장면 분석 준비<ArrowRight size={16} /></button></div>
      {currentPreview && <section className="log-batch-preview" aria-label="Log Sim batch preview"><div><span className="log-sim-eyebrow">WORKLOAD PREVIEW</span><h3>{currentPreview.counts.distinct_scenes}개 장면 · {currentPreview.counts.supported_sources}개 지원 구간</h3><p>원본 {currentPreview.counts.frame_count}개 표본에 기준 제어기 {currentPreview.planned_execution_count}회 실행 · 미지원 {currentPreview.counts.unsupported_sources}개 구간</p><small>원본·규칙·제어기 설정을 고정합니다. 원본 영상은 변경되지 않습니다.</small></div><button className="button" aria-label="Run Log Sim batch" disabled={!writable || !!busy || !currentPreview.counts.supported_sources || settingsDirty || guidedLocked} onClick={() => void execute()}>{busy === "execute" ? <LoaderCircle size={16} className="spin" /> : <Play size={16} />}사건 탐색 + A/B 실행</button></section>}
    </>}
    {error && <div className="log-sim-error" role="alert"><p>{error}</p><button className="button secondary" onClick={() => { library.reload(); setBatchRevision(value => value + 1); }}>입력·실행 상태 다시 확인</button></div>}
    {history.error && <ErrorState message={history.error} retry={history.reload} />}
    {batchError && <ErrorState message={batchError} retry={() => setBatchRevision(value => value + 1)} />}
    {(history.data?.items.length ?? 0) > 0 && <div className="log-batch-history"><label>분석 기록<select aria-label="Log Sim saved batch" disabled={!!task} value={batchId} onChange={event => { setBatchId(event.target.value); setBatch(null); setInspecting(null); retainBatch(event.target.value); }}>{history.data?.items.map(item => <option key={item.id} value={item.id}>{new Date(item.created_at).toLocaleString("ko-KR", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })} · {item.counts.distinct_scenes}개 장면 · {batchStatusLabel(item.status)}</option>)}</select></label><button className="log-sim-icon-button" aria-label="Refresh Log Sim batch" onClick={() => { setBatchRevision(value => value + 1); history.reload(); }}><RefreshCw size={15} /></button></div>}
    {batch && <>
      <section className="log-batch-progress" aria-label="Log Sim actual batch progress"><div><div><span className="log-sim-eyebrow">{active ? "ACTUAL EXECUTIONS IN PROGRESS" : "RETAINED BATCH RESULT"}</span><h3>{active && <LoaderCircle className="spin" size={18} />}{batchStatusLabel(batch.status)}<span>{batch.counts.completed_sources} / {batch.counts.source_count}개 구간 완료</span></h3></div><div className="log-batch-progress-counts"><span><strong>{batch.counts.distinct_scenes}</strong>원본 장면</span><span><strong>{batch.counts.source_count}</strong>원본 구간</span><span><strong>{batch.counts.candidate_count}</strong>조사 후보</span></div></div><div className="log-batch-progress-track"><span style={{ width: `${batch.counts.source_count ? batch.counts.completed_sources / batch.counts.source_count * 100 : 0}%` }} /></div><details><summary>구간별 실행 상태 · 실패 {batch.counts.failed_sources} / 미지원 {batch.counts.unsupported_sources}<ChevronDown size={13} /></summary><div className="log-batch-source-status">{batch.sources.map(source => <div key={source.source_id}><strong>{source.title}</strong><span>{source.frame_count}개 표본</span><span className={source.status}>{batchStatusLabel(source.status)}</span>{source.reason && <small>{source.reason}</small>}</div>)}</div></details>{batch.counts.failed_sources > 0 && !active && <button className="button secondary" aria-label="Retry Log Sim batch" disabled={!writable || !!busy} onClick={() => void retry()}><RefreshCw size={14} />실패한 실행 다시 시도</button>}</section>
      <LogBatchExport project={project} batch={batch.id} />
      {task?.batch_id === batch.id && <p className="log-sim-notice">보존 결과 · 이 업무의 설정 r{task.comparison_ref?.revision}과 원본 hash에 연결됐습니다.</p>}
      {!active && batch.status === "completed" && batch.candidates.length === 0 && <section className="log-beta-no-candidates"><h3>분석 완료 · 규칙 조사 후보 없음</h3><p>이 조건에서 후보 규칙에 해당하는 구간이 없었습니다. 안전 합격을 뜻하지 않습니다. 원본 구간을 직접 선택하고 ‘차이 없음’ 관찰을 Case로 남길 수 있습니다.</p><button className="button" aria-label="Log Sim beta no candidates Case" onClick={() => { if (task?.batch_id === batch.id) void beta!.progress({ result_opened: true }).then(() => onOpenSource(task.source_id!)).catch(() => {}); else if (batch.sources[0]) onOpenSource(batch.sources[0].source_id); }}>원본에서 Case 기록</button></section>}
      <LogBatchBoard batch={batch} scenes={scenes} selected={selectedCandidates} onToggle={id => selectCandidates([id], !selectedCandidates.has(id))} onSelectMany={selectCandidates} onInspect={inspect} />
      <section className="log-batch-bundle-save" aria-label="Log Sim candidate Case bundle"><div><FolderCheck size={23} /><div><span className="log-sim-eyebrow">TURN REVIEW INTO REUSABLE CASES</span><h3>선택한 장면을 Case 묶음으로</h3><p>{selectedCandidates.size}개 후보 · 원본 구간과 A/B 실행 근거를 함께 보존합니다.</p></div></div><label>묶음 이름<input aria-label="Log Sim Case bundle name" value={bundleName} maxLength={180} onChange={event => setBundleName(event.target.value)} /></label><button className="button" aria-label="Save Log Sim Case bundle" disabled={!writable || !!busy || active || !selectedCandidates.size || !bundleName.trim() || !!batchError} onClick={() => void saveBundle()}>{busy === "bundle" ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}선택한 {selectedCandidates.size}개 Case 저장</button>{selectionMessage && <small className="log-batch-selection-message">{selectionMessage}</small>}{savedBundle && <div className="log-batch-bundle-saved" role="status"><Check size={15} />{savedBundle.items.length}개 Case를 “{savedBundle.name}” 묶음에 저장했습니다.{savedBundle.items[0] && <button className="button secondary" aria-label="Review saved Log Sim beta Case" onClick={() => onOpenSource(savedBundle.items[0].source_id, savedBundle.items[0].case_id)}>저장한 Case 검토</button>}</div>}</section>
    </>}
    {bundles.error && <ErrorState message={bundles.error} retry={bundles.reload} />}
    {!!bundles.data?.items.length && <section className="log-batch-bundles" aria-label="Log Sim saved Case bundles"><div><span className="log-sim-eyebrow">SAVED CASE BUNDLES</span><h2>다시 꺼내볼 조사 묶음</h2></div>{bundles.data.items.map(bundle => <details key={bundle.id} data-testid="log-batch-saved-bundle"><summary><FolderCheck size={18} /><strong>{bundle.name}</strong><span>{bundle.items.length}개 Case</span><ChevronDown size={15} /></summary><div>{bundle.items.map(item => <button key={item.candidate_id} onClick={() => onOpenSource(item.source_id, item.case_id)}><span>{item.title ?? item.case_id}<small>Case r{item.case_revision}</small></span><ArrowRight size={14} /></button>)}</div></details>)}</section>}
    {candidate && <LogBatchInspector key={candidate.source_id} project={project} candidate={candidate} sceneName={candidateScene?.name ?? candidate.source_id} selected={selectedCandidates.has(candidate.id)} onToggle={() => selectCandidates([candidate.id], !selectedCandidates.has(candidate.id))} onClose={() => { setInspecting(null); retainBatch(batch?.id); }} canPrevious={candidateIndex > 0} canNext={candidateIndex >= 0 && candidateIndex < candidateIds.length - 1} onNavigate={delta => { const id = candidateIds[candidateIndex + delta]; if (id) { setInspecting({ id, ids: candidateIds }); retainBatch(batch?.id, id); } }} />}
    <footer className="log-batch-footnote">원본 영상(선택) · 기록된 객체와 ego · 기준 제어기 요청 비교<span>원본 장면 수, 분석 구간 수, 조사 후보 수는 서로 다른 분모입니다.</span></footer>
  </div>;
}

function SceneCard({ scene, checked, onToggle, locked = false }: { scene: LogLibraryScene; checked: boolean; onToggle: () => void; locked?: boolean }) {
  return <article className={`log-batch-scene ${checked ? "selected" : ""} ${scene.status}`} data-testid="log-library-scene"><button aria-label={`Select Log Sim scene ${scene.name}`} aria-pressed={checked} disabled={locked} onClick={onToggle}><div className="log-batch-scene-image"><OriginalThumbnail url={scene.thumbnail_url} label={scene.description || scene.name} /><span className="log-batch-scene-check">{checked && <Check size={13} />}</span><span className="log-batch-scene-name">{scene.name}</span></div><div className="log-batch-scene-copy"><p title={scene.description}>{scene.description || "원본 장면 설명 없음"}</p><span><strong>{scene.segments.length}개 구간</strong><small>{scene.available_frame_count} frames · {scene.duration_s.toFixed(1)}s</small></span></div></button>{scene.excluded_frame_count > 0 && <details className="log-batch-scene-excluded"><summary>원본 {scene.original_frame_count}개 중 {scene.excluded_frame_count}개 제외</summary>{scene.exclusions.map((exclusion, index) => <p key={index}>{exclusion.frame_count}개 · {exclusion.reason}</p>)}</details>}</article>;
}

function OriginalThumbnail({ url, label }: { url: string | null; label: string }) {
  const [image, setImage] = useState<string | null>(null), [error, setError] = useState(false);
  useEffect(() => {
    if (!url) return;
    const controller = new AbortController(); let retained: string | undefined;
    setImage(null); setError(false);
    void (async () => {
      try {
        const response = await fetch(url, { headers: authHeaders(), signal: controller.signal });
        if (!response.ok) throw new Error(`Original thumbnail unavailable (${response.status})`);
        const bytes = await response.arrayBuffer(), expected = response.headers.get("X-Content-SHA256");
        if (expected) { const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))).map(value => value.toString(16).padStart(2, "0")).join(""); if (hash !== expected) throw new Error("Original thumbnail integrity mismatch"); }
        if (controller.signal.aborted) return;
        retained = URL.createObjectURL(new Blob([bytes], { type: response.headers.get("Content-Type") ?? "image/jpeg" })); setImage(retained);
      } catch { if (!controller.signal.aborted) setError(true); }
    })();
    return () => { controller.abort(); if (retained) URL.revokeObjectURL(retained); };
  }, [url]);
  return image ? <img src={image} alt={`nuScenes 원본 장면: ${label}`} /> : <span className="log-batch-thumbnail-empty"><Film size={23} />{!url ? "원본 영상 없음" : error ? "원본 영상 조회 실패" : "원본 불러오는 중"}</span>;
}
