import { boundedDemoMoment, demoMoment } from "../demo-moment";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Check, ChevronRight, Clock3, FileSearch, Film, GitCompareArrows, LoaderCircle, Pause, Play, RefreshCw, Save, SlidersHorizontal } from "lucide-react";
import { api, isExternalViewer, useResource } from "../api";
import { ErrorState, Loading } from "../components/ui";
import CitySceneViewer from "../components/city-sim/CitySceneViewer";
import type { CityPlaybackSnapshot } from "../components/city-sim/CitySceneViewer";
import ObjectComparison from "../components/object-demo/ObjectComparison";
import { executionStatusLabel } from "../components/object-demo/comparison-model";
import { playbackDeadline, followingPlaybackDeadline } from "../components/object-demo/playback-cadence";
import type { PlaybackDeadline } from "../components/object-demo/playback-cadence";
import RoadLogViewer from "../components/log-sim/RoadLogViewer";
import EventExplorer from "../components/log-sim/EventExplorer";
import ReferenceReplay from "../components/log-sim/ReferenceReplay";
import LogCaseReview from "../components/log-sim/LogCaseReview";
import LogBatchLibrary from "../components/log-sim/LogBatchLibrary";
import { LogBetaProvider, LogBetaTaskBar, betaQuery, betaWriteKey, useLogBeta } from "../components/log-sim/LogGuidedBeta";
import { activeExecution, logSimHref, metricValue } from "../components/log-sim/log-sim-contract";
import type { DetectedLogEvent, LogCase, LogEvent, LogExecution, LogMetrics, LogSource, LogVariant, VariantPreview, VariantRequest } from "../components/log-sim/log-sim-contract";
import "../log-sim.css";

const errorText = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
function retainRoute(project: string, source?: string, variant?: string, compare = false) {
  const current = new URLSearchParams(location.hash.split("?")[1] ?? "");
  const route = logSimHref(project, source, variant, compare);
  const caseId = current.get("project") === project && current.get("source") === source ? current.get("case") : null;
  history.replaceState(null, "", `${location.pathname}${location.search}${route}${caseId ? `&case=${encodeURIComponent(caseId)}` : ""}${current.get("project") === project && current.get("task") ? `&task=${encodeURIComponent(current.get("task")!)}` : ""}`);
}

export default function LogSim() {
  const initial = useRef(new URLSearchParams(location.hash.split("?")[1] ?? "")).current;
  const [project, setProject] = useState(initial.get("project") ?? "");
  const [sourceId, setSourceId] = useState(initial.get("source") ?? "");
  const [detailParams, setDetailParams] = useState(initial);
  useEffect(() => {
    const restore = () => {
      if (!location.hash.startsWith("#log-sim")) return;
      const params = new URLSearchParams(location.hash.split("?")[1] ?? "");
      setProject(params.get("project") ?? ""); setSourceId(params.get("source") ?? ""); setDetailParams(params);
    };
    window.addEventListener("hashchange", restore);
    return () => window.removeEventListener("hashchange", restore);
  }, []);
  const projects = useResource<{ items: { id: string; name: string }[] }>("/projects");
  const sources = useResource<{ items: LogSource[] }>(project ? `/log-sim/sources?project_id=${encodeURIComponent(project)}` : null);
  useEffect(() => { if (!project && projects.data?.items[0]) setProject(projects.data.items[0].id); }, [project, projects.data]);
  const selected = sourceId ? sources.data?.items.find(source => source.id === sourceId) : undefined;
  const navigationSources = [sources.data?.items.find(source => source.kind === "carla"), selected && selected.kind !== "carla" ? selected : sources.data?.items.find(source => source.kind === "nuscenes")].filter((value): value is LogSource => !!value);
  function openSource(source: string, caseId?: string) {
    const query = betaQuery({ project, source });
    if (caseId) query.set("case", caseId);
    sources.reload(); setSourceId(source); setDetailParams(query);
    history.replaceState(null, "", `${location.pathname}${location.search}#log-sim?${query}`);
  }
  if (projects.loading) return <div className="log-sim"><Loading label="Log Sim 프로젝트 접근을 확인하는 중…" /></div>;
  if (projects.errorStatus === 401) return <section className="log-sim log-beta-guide" aria-label="Log Sim authentication required"><h1>프로젝트 연결이 필요합니다.</h1><p>Workspace 설정에 받은 프로젝트 토큰을 입력하면 대표 로그와 저장한 업무를 불러옵니다.</p><button className="button" onClick={() => (document.querySelector('[aria-label="Workspace settings"]') as HTMLButtonElement | null)?.click()}>Workspace 설정에서 토큰 입력</button></section>;
  return <LogBetaProvider key={project} project={project} initialTaskId={detailParams.get("task")}><div className="log-sim" data-testid="log-sim-workspace">
    <header className="log-sim-heading"><div><span className="log-sim-eyebrow">LOG SIM / DISCOVER · COMPARE · REPEAT</span><h1>기록에서, 다음 실험으로.</h1><p>주행 로그에서 사건을 찾고, 같은 입력의 제어 요청을 비교하고, 반복 시험으로 연결하세요.</p></div><label className="log-sim-project">Workspace<select aria-label="Log Sim project" value={project} onChange={event => { setProject(event.target.value); setSourceId(""); setDetailParams(new URLSearchParams({ project: event.target.value })); retainRoute(event.target.value); }}>{projects.data?.items.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label></header>
    {projects.error && <><ErrorState message={projects.error} retry={projects.reload} /><button className="button secondary" onClick={() => (document.querySelector('[aria-label="Workspace settings"]') as HTMLButtonElement | null)?.click()}>Workspace 설정에서 토큰 입력</button></>}
    {project && <LogBetaTaskBar project={project} onOpenSource={openSource} />}
    {sources.loading && <Loading label="Log Sim 소스를 불러오는 중…" />}
    {sources.error && <ErrorState message={sources.error} retry={sources.reload} />}
    {sourceId && <><button className="log-batch-back-library" onClick={() => { setSourceId(""); retainRoute(project); }}><ArrowLeft size={15} />장면 라이브러리로</button><div className="log-sim-sources" role="group" aria-label="Log Sim sources">{navigationSources.map(source => <button key={source.id} type="button" aria-label={`Log Sim source ${source.id}`} aria-pressed={selected?.id === source.id} onClick={() => openSource(source.id)} className={`log-sim-source ${selected?.id === source.id ? "selected" : ""}`}><span className="log-sim-source-icon">{source.kind === "carla" ? <SlidersHorizontal size={21} /> : <Film size={21} />}</span><span><small>{source.kind === "carla" ? "01 / LOG → VARIANT → COMPARE" : "02 / RECORDED LOG INVESTIGATION"}</small><strong>{source.title}</strong><span>{source.kind === "carla" ? "CARLA 원본 기록 · 조건 변경 · 재실행" : source.kind === "nuscenes" ? "nuScenes 원본 · 사건 검색 · 동일 로그 A/B" : "등록한 원본 · 사건 검색 · 동일 로그 A/B"}</span></span><span className={`log-sim-source-state ${source.ready ? "ready" : ""}`}>{source.ready ? <Check size={14} /> : <Clock3 size={14} />}{source.ready ? "준비됨" : "입력 준비 필요"}</span></button>)}</div></>}
    {!sourceId && project && <LogBatchLibrary key={`${project}/${detailParams.get("batch") ?? ""}/${detailParams.get("candidate") ?? ""}`} project={project} initialBatchId={detailParams.get("batch")} initialCandidateId={detailParams.get("candidate")} onOpenSource={openSource} />}
    {sourceId && sources.data && !selected && <p className="log-sim-notice">요청한 원본 구간을 찾을 수 없습니다. 장면 라이브러리에서 다른 원본을 선택하세요.</p>}
    {sourceId && sources.data && !sources.data.items.length && <div className="log-sim-notice">이 프로젝트에 등록된 Log Sim 소스가 없습니다. 원본 실행이나 연구용 선택 구간을 등록한 뒤 새로고침하세요.<button className="button secondary" onClick={sources.reload}>새로고침</button></div>}
    {selected && <LogWorkspace key={`${project}/${selected.id}/${detailParams.get("case") ?? ""}`} project={project} source={selected} initialCaseId={detailParams.get("source") === selected.id ? detailParams.get("case") : null} initialVariant={detailParams.get("source") === selected.id ? detailParams.get("variant") : null} initialCompare={detailParams.get("source") === selected.id && detailParams.get("view") === "compare"} />}
  </div></LogBetaProvider>;
}

function LogWorkspace({ project, source: summary, initialCaseId, initialVariant, initialCompare }: { project: string; source: LogSource; initialCaseId: string | null; initialVariant: string | null; initialCompare: boolean }) {
  const beta = useLogBeta();
  const sourceResource = useResource<LogSource>(`/log-sim/sources/${encodeURIComponent(summary.id)}?project_id=${encodeURIComponent(project)}`);
  const source = sourceResource.data ?? summary;
  const restoredCase = useResource<LogCase & { segment: { start_time_ns: string; end_time_ns: string } }>(initialCaseId ? `/log-sim/sources/${encodeURIComponent(summary.id)}/cases/${encodeURIComponent(initialCaseId)}?project_id=${encodeURIComponent(project)}` : null);
  const original = useResource<LogExecution>(source.baseline_execution_id ? `/experiments/executions/${encodeURIComponent(source.baseline_execution_id)}` : null);
  const variants = useResource<{ items: LogVariant[] }>(`/log-sim/variants?${new URLSearchParams({ project_id: project, source_id: source.id })}`);
  const access = useResource<{ role: string; mode: string; permissions: { execute: boolean } }>("/team/whoami");
  const [timeS, setTimeS] = useState(0), [playing, setPlaying] = useState(false), [sensor, setSensor] = useState<"front" | "chase">("chase");
  const [snapshot, setSnapshot] = useState<CityPlaybackSnapshot | null>(null), [roadReady, setRoadReady] = useState(false);
  const [focusedEvent, setFocusedEvent] = useState<LogEvent | null>(null);
  const [eventId, setEventId] = useState(""), [selection, setSelection] = useState<{ start: number; end: number } | null>(null);
  const [savedCase, setSavedCase] = useState<LogCase | null>(null), [caseBusy, setCaseBusy] = useState(false), [caseError, setCaseError] = useState("");
  const [caseChanged, setCaseChanged] = useState(false), [caseOpen, setCaseOpen] = useState(false);
  const [waitS, setWaitS] = useState(2), [preview, setPreview] = useState<VariantPreview | null>(null), [busy, setBusy] = useState(""), [error, setError] = useState("");
  const [localVariant, setLocalVariant] = useState<LogVariant | null>(null), [variantId, setVariantId] = useState(initialVariant ?? "");
  const [execution, setExecution] = useState<LogExecution | null>(null), [pollRevision, setPollRevision] = useState(0), [pollError, setPollError] = useState("");
  const [comparing, setComparing] = useState(initialCompare), [savedPair, setSavedPair] = useState(false);
  const [requestedNow, setRequestedNow] = useState(false);
  const caseRestored = useRef(false);
  const initialMoment = useRef(demoMoment(location.hash)), momentApplied = useRef(false);
  useEffect(() => {
    if (!restoredCase.data || caseRestored.current || caseChanged || !source.recording_ref) return;
    caseRestored.current = true; setSavedCase(restoredCase.data);
    const origin = BigInt(source.recording_ref.time_origin_ns);
    setSelection({ start: Number(BigInt(restoredCase.data.segment.start_time_ns) - origin) / 1e9, end: Number(BigInt(restoredCase.data.segment.end_time_ns) - origin) / 1e9 });
  }, [restoredCase.data, source.recording_ref, caseChanged]);
  const casePending = useRef({ body: "", key: "" }), variantPending = useRef({ body: "", key: "" });
  const deadlineRef = useRef<PlaybackDeadline | null>(null);
  const refreshedExecution = useRef("");
  const activeVariant = localVariant ?? variants.data?.items.find(item => item.id === variantId) ?? (!variantId ? variants.data?.items[0] : undefined);
  const candidateId = activeVariant?.execution_id;
  const actualCase = savedCase ?? (!caseChanged && activeVariant?.case_ref ? { ...activeVariant.case_ref, title: "이 실행에 연결된 Case" } : null);
  const writable = !isExternalViewer() && !!access.data?.permissions.execute;
  const caseDetail = useResource<{ id: string; revision: number; title: string; note: string; segment: { sample_count: number; start_time_ns: string; end_time_ns: string } }>(caseOpen && actualCase ? `/log-sim/sources/${encodeURIComponent(source.id)}/cases/${encodeURIComponent(actualCase.id)}?project_id=${encodeURIComponent(project)}` : null);
  const duration = source.duration_s || snapshot?.lastTimeS || source.road_frames?.at(-1)?.time_s || 0;
  const firstTime = source.kind === "carla" ? snapshot?.firstTimeS ?? 0 : source.road_frames?.[0]?.time_s ?? 0;
  const selectedEvent = focusedEvent ?? source.events?.find(event => event.id === eventId);
  const window = selection ?? { start: 0, end: Math.min(duration, 5) };
  const running = !!execution && activeExecution(execution.status);
  const candidateCompleted = execution?.status === "completed" && !!execution.manifest_hash;
  const compareRight = savedPair ? source.saved_comparison_execution_id : candidateCompleted ? execution?.id : undefined;
  const liveReady = source.kind === "carla" ? !!snapshot?.ready && snapshot.requestedTimeS === timeS && !snapshot.error : roadReady;
  const onSnapshot = useCallback((value: CityPlaybackSnapshot) => setSnapshot(previous => previous?.requestedTimeS === value.requestedTimeS && previous.ready === value.ready && previous.error === value.error && previous.lastTimeS === value.lastTimeS ? previous : value), []);
  const onRoadReady = useCallback((value: boolean) => setRoadReady(value), []);
  useEffect(() => { if (timeS < firstTime) setTimeS(firstTime); }, [firstTime, timeS]);
  useEffect(() => { if (!momentApplied.current && duration > 0 && (source.kind !== "carla" || snapshot?.lastTimeS)) { momentApplied.current = true; setTimeS(boundedDemoMoment(initialMoment.current, firstTime, duration)); } }, [duration, firstTime, source.kind, snapshot?.lastTimeS]);
  useEffect(() => {
    if (!playing || document.hidden) { deadlineRef.current = null; return; }
    if (!liveReady) return;
    if (timeS >= duration) { deadlineRef.current = null; setPlaying(false); return; }
    const deadline = playbackDeadline(deadlineRef.current, performance.now(), "log-sim");
    deadlineRef.current = deadline;
    const timer = setTimeout(() => {
      if (document.hidden) { deadlineRef.current = null; return; }
      deadlineRef.current = followingPlaybackDeadline(deadline, performance.now());
      setTimeS(value => Math.min(duration, Math.round((value + .05) * 100) / 100));
    }, Math.max(0, deadline.dueMs - performance.now()));
    return () => clearTimeout(timer);
  }, [playing, liveReady, timeS, duration]);
  useEffect(() => { const visibility = () => { if (document.hidden) setPlaying(false); }; document.addEventListener("visibilitychange", visibility); return () => document.removeEventListener("visibilitychange", visibility); }, []);
  useEffect(() => {
    setExecution(null); setPollError("");
    if (!candidateId) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function read() {
      try {
        const value = await api<LogExecution>(`/experiments/executions/${encodeURIComponent(candidateId!)}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setExecution(value); setPollError("");
        if (activeExecution(value.status)) timer = setTimeout(() => void read(), 2500);
        else if (refreshedExecution.current !== value.id) { refreshedExecution.current = value.id; variants.reload(); }
      } catch (reason) { if (!controller.signal.aborted) setPollError(errorText(reason)); }
    }
    void read(); return () => { controller.abort(); clearTimeout(timer); };
  }, [candidateId, pollRevision]);
  function seek(value: number) { setPlaying(false); setTimeS(Math.max(firstTime, Math.min(duration, value))); }
  function clearCaseRoute() {
    const query = new URLSearchParams(location.hash.split("?")[1] ?? "");
    query.delete("case"); history.replaceState(null, "", `${location.pathname}${location.search}#log-sim?${query}`);
  }
  function chooseEvent(event: LogEvent) {
    seek(event.time_s); setEventId(event.id); setFocusedEvent(event); setSelection({ start: Math.max(0, event.time_s - 1), end: Math.min(duration, Math.max(event.time_s, event.end_time_s) + 1) }); setSavedCase(null); setCaseChanged(true); clearCaseRoute(); setCaseOpen(false); setCaseError(""); setPreview(null);
  }
  async function saveCase() {
    setCaseBusy(true); setCaseError("");
    const detected = selectedEvent && "event_hash" in selectedEvent ? selectedEvent as DetectedLogEvent : null;
    const candidateNote = detected ? ` 규칙 조사 후보: ${detected.description} 규칙 ${detected.rule_id}@${detected.rule_version}; event=${detected.event_hash}; analysis=${detected.analysis_hash}; source=${detected.source_hash}. 미검토·안전 판정 미평가.` : "";
    const body = JSON.stringify({ project_id: project, title: `${source.title} · ${selectedEvent?.label ?? "선택 구간"}`, note: `${source.kind === "carla" ? "CARLA 원본 실행 조사" : source.kind === "nuscenes" ? "nuScenes 연구용 원본·객체 주석 조사" : "등록한 로그·객체 관측 조사"}. ${window.start.toFixed(2)}–${window.end.toFixed(2)}s.${candidateNote}`, start_time_s: window.start, end_time_s: window.end });
    if (casePending.current.body !== body) casePending.current = { body, key: crypto.randomUUID() };
    try { const value = await api<LogCase>(`/log-sim/sources/${encodeURIComponent(source.id)}/cases`, { method: "POST", body, headers: { "Idempotency-Key": beta?.task ? betaWriteKey(`manualCase.${beta.task.id}`, body) : casePending.current.key } }); setSavedCase(value); setPreview(null); if (beta?.task?.source_id === source.id && beta.task.batch_id) await beta.progress({ case_id: value.id }); const route = new URLSearchParams(location.hash.split("?")[1] ?? ""); route.set("project", project); route.set("source", source.id); route.set("case", value.id); history.replaceState(null, "", `${location.pathname}${location.search}#log-sim?${route}`); }
    catch (reason) { setCaseError(errorText(reason)); }
    finally { setCaseBusy(false); }
  }
  const request: VariantRequest = { project_id: project, source_id: source.id, ...(actualCase ? { case_id: actualCase.id, case_revision: actualCase.revision } : {}), lead_wait_s: waitS };
  async function prepare() {
    setBusy("preview"); setError(""); setPreview(null);
    try { setPreview(await api<VariantPreview>("/log-sim/variants/preview", { method: "POST", body: JSON.stringify(request) })); }
    catch (reason) { setError(errorText(reason)); }
    finally { setBusy(""); }
  }
  async function submit() {
    if (!preview) return;
    setBusy("submit"); setError("");
    const body = JSON.stringify({ ...request, preview_hash: preview.preview_hash });
    if (variantPending.current.body !== body) variantPending.current = { body, key: crypto.randomUUID() };
    try {
      const result = await api<LogVariant>("/log-sim/variants", { method: "POST", body, headers: { "Idempotency-Key": variantPending.current.key } });
      setLocalVariant(result); setVariantId(result.id); setRequestedNow(true); setSavedPair(false); setPreview(null); setPlaying(false); setComparing(false); retainRoute(project, source.id, result.id); variants.reload();
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(""); }
  }
  function openCompare(retained: boolean) { setPlaying(false); setSavedPair(retained); setComparing(true); retainRoute(project, source.id, retained ? "saved" : activeVariant?.id, true); }
  useEffect(() => { if (initialCompare && initialVariant === "saved") setSavedPair(true); }, [initialCompare, initialVariant]);
  const effectiveBaseline = source.defaults?.lead_wait_s ?? 6;
  return <>
    <nav className="log-sim-steps" aria-label="Log Sim workflow"><span className={!comparing ? "current" : ""}><b>1</b>로그·사건 조사</span><ChevronRight size={16} /><span className={actualCase ? "complete" : ""}><b>{actualCase ? <Check size={13} /> : "2"}</b>Case 저장</span><ChevronRight size={16} /><span className={execution ? "complete" : ""}><b>3</b>동일 로그 A/B</span><ChevronRight size={16} /><span className={comparing ? "current" : ""}><b>4</b>반복 시험</span></nav>
    {sourceResource.loading && <Loading label="원본 사건과 기록을 불러오는 중…" />}
    {restoredCase.error && <ErrorState message={`저장된 Case 복원: ${restoredCase.error}`} retry={restoredCase.reload} />}
    {sourceResource.error && <ErrorState message={sourceResource.error} retry={sourceResource.reload} />}
    {!source.ready && <div role="status" className="log-sim-notice"><strong>원본 입력을 준비해야 합니다.</strong><p>{source.reason ?? source.status}</p><button className="button secondary" onClick={sourceResource.reload}><RefreshCw size={14} />입력 다시 확인</button></div>}
    {comparing && source.baseline_execution_id && compareRight ? <section className="log-sim-comparison"><div className="log-sim-compare-label"><span>{savedPair || !requestedNow ? "저장된 실행 결과 비교" : "이번에 요청한 실행 결과 비교"} · 원본 A {effectiveBaseline}초 / 변경 B {savedPair ? "저장 조건" : `${activeVariant?.parameters.lead_wait_s}초`}</span><span>공통 시뮬레이션 시간축</span></div>{!savedPair && <MetricCards metrics={source.metrics} candidate={variants.data?.items.find(item => item.id === activeVariant?.id)?.metrics} comparing />}<ObjectComparison project={project} leftExecutionId={source.baseline_execution_id} rightExecutionId={compareRight} initialPresentationSensor="chase" initialGeometryView="2d" onBack={() => { setComparing(false); retainRoute(project, source.id, activeVariant?.id); }} /></section> : <>
      {comparing && !compareRight && <p className="log-sim-notice">비교할 실행 결과를 불러오는 중입니다.{pollError && ` ${pollError}`}</p>}
      <div className="log-sim-workbench"><section className="log-sim-investigation">
        <div className="log-sim-section-heading"><div><span className="log-sim-eyebrow">ORIGINAL RECORDING</span><h2>{source.title}</h2></div><div className="log-sim-original-controls">{source.kind === "carla" && <div role="group" aria-label="Log Sim RGB camera"><button aria-pressed={sensor === "chase"} onClick={() => setSensor("chase")}>추적 RGB</button><button aria-pressed={sensor === "front"} onClick={() => setSensor("front")}>전방 RGB</button></div>}<span className="log-sim-recorded"><span />{source.kind === "carla" ? "저장된 실제 CARLA 실행" : source.kind === "nuscenes" ? "보존된 실도로 원본" : "등록한 로그 원본"}</span></div></div>
        {source.kind === "carla" ? <div className="log-sim-native">{original.loading && <Loading label="원본 CARLA 영상과 좌표를 불러오는 중…" />}{original.error && <ErrorState message={original.error} retry={original.reload} />}{original.data?.manifest_hash && <CitySceneViewer execution={original.data} presentation initialView="2d" initialMapFit="actor" presentationSensor={sensor} onPresentationSensorChange={setSensor} playbackTimeS={timeS} onPlaybackSeek={seek} onPlaybackSnapshot={onSnapshot} playbackActive={playing} decisionFocusTimeS={selectedEvent?.time_s} />}{!source.baseline_execution_id && <p className="log-sim-notice">연결된 원본 실행이 없습니다.</p>}</div> : <RoadLogViewer frames={source.road_frames ?? []} timeS={timeS} onReady={onRoadReady} />}
        <div className="log-sim-transport"><button className="button" aria-label={playing ? "Log Sim pause" : "Log Sim play"} disabled={!source.ready || !duration || !!snapshot?.error} onClick={() => { if (timeS >= duration) setTimeS(firstTime); setPlaying(!playing); }}>{playing ? <Pause size={17} /> : <Play size={17} />}{playing ? "일시 정지" : "원본 재생"}</button><output>{timeS.toFixed(2)} <span>/ {duration.toFixed(2)} s</span></output><input aria-label="Log Sim playback time" type="range" min={firstTime} max={duration || 1} step={source.kind === "nuscenes" ? "any" : "0.05"} value={Math.min(timeS, duration)} onChange={event => seek(Number(event.target.value))} /><button aria-label="Log Sim rewind" className="log-sim-icon-button" onClick={() => seek(0)}><RefreshCw size={16} /></button></div>
        {!!source.events?.length && <div className="log-sim-events"><div><span className="log-sim-eyebrow">EVENTS</span><small>기록된 사건을 눌러 조사 구간 선택</small></div><div>{source.events?.map(event => <button type="button" aria-label={`Log Sim event ${event.id}`} key={event.id} aria-pressed={event.id === eventId} className={event.id === eventId ? "selected" : ""} onClick={() => chooseEvent(event)}><Clock3 size={14} /><strong>{event.label}</strong><span>{event.time_s.toFixed(2)}s</span></button>)}{!source.events?.length && <span className="log-sim-muted">사건 기록 없음 · 현재 시점에서 구간을 선택하세요.</span>}</div></div>}
        {source.ready && <EventExplorer project={project} sourceId={source.id} selectedId={eventId} onSelect={chooseEvent} />}
        {source.kind === "carla" && <MetricCards metrics={source.metrics} />}
      </section>
      <aside className="log-sim-actions">
        <section className="log-sim-case-panel"><span className="log-sim-eyebrow">01 / CAPTURE THE CASE</span><h3><FileSearch size={19} />조사 구간</h3><strong>{selectedEvent?.label ?? "관심 구간 선택"}</strong><div className="log-sim-range"><label>시작 (s)<input aria-label="Log Sim case start" type="number" min="0" max={duration} step="0.05" value={window.start} onChange={event => { setSelection({ ...window, start: Math.max(0, Math.min(window.end, Number(event.target.value))) }); setSavedCase(null); setCaseChanged(true); clearCaseRoute(); setCaseOpen(false); setPreview(null); }} /></label><ArrowRight size={14} /><label>끝 (s)<input aria-label="Log Sim case end" type="number" min={window.start} max={duration} step="0.05" value={window.end} onChange={event => { setSelection({ ...window, end: Math.max(window.start, Math.min(duration, Number(event.target.value))) }); setSavedCase(null); setCaseChanged(true); clearCaseRoute(); setCaseOpen(false); setPreview(null); }} /></label></div><button className="log-sim-text-button" onClick={() => { setSelection({ start: Math.max(0, timeS - 1), end: Math.min(duration, timeS + 2) }); setEventId(""); setFocusedEvent(null); setSavedCase(null); setCaseChanged(true); clearCaseRoute(); setCaseOpen(false); setPreview(null); }}>현재 시점 주변 선택</button><button className="button secondary" disabled={!source.ready || !source.capabilities?.case_save || caseBusy || !writable || window.end <= window.start} onClick={() => void saveCase()}>{caseBusy ? <LoaderCircle className="spin" size={16} /> : savedCase ? <Check size={16} /> : <Save size={16} />}{caseBusy ? "원본 구간 저장 중…" : "Case 저장"}</button>{actualCase && <button type="button" className="log-sim-case-saved" onClick={() => setCaseOpen(!caseOpen)}><Check size={14} />Case r{actualCase.revision} 연결됨 <ArrowRight size={13} /></button>}{caseError && <p className="log-sim-error" role="alert">{caseError}</p>}</section>
        {source.kind === "carla" ? <section className="log-sim-variant-panel"><span className="log-sim-eyebrow">02 / CHANGE ONE CONDITION</span><h3><SlidersHorizontal size={19} />앞차 정지 시간</h3><div className="log-sim-parameter"><span>원본 A<strong>{effectiveBaseline}<small>초</small></strong></span><ArrowRight size={23} /><span>변경 B<strong>{waitS}<small>초</small></strong></span></div><input aria-label="앞차 정지 시간" type="range" min="0" max="6" step="1" value={waitS} disabled={!!busy || running} onChange={event => { setWaitS(Number(event.target.value)); setPreview(null); setError(""); }} /><div className="log-sim-slider-labels"><span>계속 주행 · 0초</span><span>6초</span></div><p>같은 지도·자차 정책·seed에서 앞차의 정지 시간만 바꿉니다.</p><button className="button secondary" disabled={!source.ready || !source.capabilities?.variant || !actualCase || !!busy || running || !writable} onClick={() => void prepare()}>{busy === "preview" ? <LoaderCircle className="spin" size={16} /> : <SlidersHorizontal size={16} />}변경 내용 확인</button>{preview && <div className="log-sim-preview" role="status"><Check size={16} /><span>실행 준비 완료 · {effectiveBaseline}초 → {waitS}초<br /><small>아직 실행되지 않은 변경안</small></span></div>}<button className="button log-sim-run" disabled={!preview || !!busy || running || !writable} onClick={() => void submit()}>{busy === "submit" ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}{busy === "submit" ? "실행 요청 중…" : "CARLA로 변경안 실행"}</button>{!actualCase && <small className="log-sim-muted">먼저 조사할 구간을 Case로 저장하세요.</small>}<small className="log-sim-muted">전체 시나리오를 처음부터 재실행합니다. 선택 구간은 조사·비교의 기준입니다.</small>{error && <p className="log-sim-error" role="alert">{error}</p>}</section> : <section className="log-sim-road-context"><span className="log-sim-eyebrow">RECORDED LOG / INVESTIGATION</span><h3>원본에서 비교까지</h3><p>자동 조사 후보를 선택하고 Case로 저장하면, 아래에서 동일한 기록에 두 기준 제어기를 실행할 수 있습니다.</p><p>객체 상자는 기록된 입력이며 생성 출처는 원본 정보를 확인하세요. A/B는 기록된 객체·ego 입력의 제어 요청 비교이며 원본 영상은 그대로 유지됩니다.</p><span className="log-sim-recorded">{source.kind === "nuscenes" ? "연구용 데이터 시연" : "기록된 객체 입력 · 출처 확인 필요"}</span></section>}
        {source.saved_comparison_execution_id && <button className="log-sim-saved-pair" onClick={() => openCompare(true)}><GitCompareArrows size={18} /><span><strong>저장된 A/B 보기</strong><small>기존 실제 실행 결과 비교</small></span><ChevronRight size={16} /></button>}
        {!writable && <p className="log-sim-muted">실행과 저장에는 해당 프로젝트의 쓰기 권한이 필요합니다.</p>}
      </aside></div>
    </>}
    {!comparing && source.ready && !beta?.task && <ReferenceReplay project={project} sourceId={source.id} sourceKind={source.kind} savedCase={actualCase} writable={writable} timeS={timeS} onSeek={seek} />}
    {!comparing && actualCase && <LogCaseReview key={actualCase.id} project={project} source={source.id} caseId={actualCase.id} timeS={timeS} writable={writable} onSeek={seek} />}
    {caseOpen && <section className="log-sim-case-detail" aria-label="Log Sim saved Case detail"><div><span className="log-sim-eyebrow">PINNED ORIGINAL SEGMENT</span><button className="log-sim-text-button" onClick={() => setCaseOpen(false)}>닫기</button></div>{caseDetail.loading && <Loading label="저장된 Case 원본 구간 확인 중…" />}{caseDetail.error && <ErrorState message={caseDetail.error} retry={caseDetail.reload} />}{caseDetail.data && <><h3>{caseDetail.data.title}</h3><p>{caseDetail.data.note}</p><small>{caseDetail.data.id} · r{caseDetail.data.revision} · 원본 {caseDetail.data.segment.sample_count}개 표본</small><p className="log-sim-muted">{caseDetail.data.segment.start_time_ns} → {caseDetail.data.segment.end_time_ns} ns</p></>}</section>}
    {activeVariant && <section className="log-sim-execution" aria-label="Log Sim actual execution status"><div><span className="log-sim-eyebrow">{requestedNow ? "방금 요청한 실제 실행" : "저장된 실행 요청"}</span><h3>{running && <LoaderCircle className="spin" size={18} />}{execution ? executionStatusLabel(execution.status) : "실행 상태 확인 중"}<span>앞차 정지 {activeVariant.parameters.lead_wait_s}초</span></h3><small>{candidateId}</small></div><div className="log-sim-execution-actions"><button className="button secondary" onClick={() => setPollRevision(value => value + 1)}><RefreshCw size={14} />상태 새로고침</button><button className="button" disabled={!candidateCompleted} onClick={() => openCompare(false)}><GitCompareArrows size={16} />원본과 비교</button>{comparing && <button className="button secondary" onClick={() => { setComparing(false); retainRoute(project, source.id, activeVariant.id); }}><ArrowLeft size={14} />로그 조사로</button>}</div>{running && <p>CARLA가 변경 조건으로 실행 중입니다. 기다리는 동안 원본이나 실도로 소스를 조사할 수 있습니다.</p>}{(execution?.error || pollError) && <p role="alert" className="log-sim-error">{execution?.error ?? pollError}</p>}{execution?.outcome_uncertain && <p role="alert" className="log-sim-error">실행 결과를 확정할 수 없습니다. 상태를 다시 확인하세요.</p>}</section>}
    {(variants.data?.items.length ?? 0) > 1 && <div className="log-sim-history"><label>저장된 변경 실행<select aria-label="Log Sim saved variant" value={activeVariant?.id ?? ""} onChange={event => { setVariantId(event.target.value); setLocalVariant(null); setRequestedNow(false); setSavedPair(false); setComparing(false); retainRoute(project, source.id, event.target.value); }}>{variants.data?.items.map(item => <option key={item.id} value={item.id}>앞차 정지 {item.parameters.lead_wait_s}초 · {item.id}</option>)}</select></label></div>}
    {variants.error && <p role="alert" className="log-sim-error">저장된 변경 실행 조회: {variants.error}</p>}
    <footer className="log-sim-footnote">{source.kind === "carla" ? "CARLA GT 기반 시뮬레이션 기록 · 실차·물리 센서 검증과 구분" : source.kind === "nuscenes" ? "nuScenes 원본 영상·주석 · 연구용 시연" : "등록한 객체·ego 기록 · 생성 출처와 사용 범위는 원본 선언 확인"}<span>원본 → Case → 변경 조건 → 실행 결과의 출처 보존</span></footer>
  </>;
}

function MetricCards({ metrics, candidate, comparing = false }: { metrics?: LogMetrics; candidate?: LogMetrics; comparing?: boolean }) {
  const items: [string, number | null | undefined, string][] = [["앞차 최소 간격", metrics?.minimum_lead_gap_m, " m"], ["자차 누적 정지", metrics?.ego_stop_duration_s, " s"], ["목적지 도달", metrics?.arrival_time_s, " s"], ["Native 접촉 관측", metrics?.native_contact_count, "건"]];
  const candidateValues = [candidate?.minimum_lead_gap_m, candidate?.ego_stop_duration_s, candidate?.arrival_time_s, candidate?.native_contact_count];
  return <div className={`log-sim-metrics ${comparing ? "comparing" : ""}`} aria-label={comparing ? "원본 변경 비교 지표" : "원본 기록 지표"}>{items.map(([label, value, unit], index) => <article key={label} title={index === 1 ? "시작 직후를 포함해 목적지 도착 전 속도 0.2m/s 미만인 기록 구간의 누적 시간" : undefined}><small>{label}</small><strong>{metricValue(value, unit)}{comparing && <><ArrowRight size={14} />{metricValue(candidateValues[index], unit)}</>}</strong><span>{comparing ? "원본 A → 변경 B · 각 실행의 보존 기록" : "원본 실행 기록"}</span></article>)}</div>;
}
