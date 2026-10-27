import { useCallback, useEffect, useRef, useState } from "react";
import { Copy, Download, Plus, RefreshCw, Save } from "lucide-react";
import { api, ApiError, authHeaders, useResource } from "../../api";
import { betaWrite, writableBeta } from "../beta-workspace/beta-contract";
import type { BetaAccess, BetaReadiness } from "../beta-workspace/beta-contract";
import LayoutEditor from "./LayoutEditor";
import LayoutPresenter from "./LayoutPresenter";
import { layoutMountError, layoutRef, layoutSensor, layoutTerminal, mountChanges, sameLayoutRef, sensorLabels, setLayoutQuery } from "./layout-contract";
import type { LayoutExecution, LayoutMoment, LayoutObservation, LayoutPreview, LayoutRef, LayoutRig, LayoutTemplates, SensorLayout } from "./layout-contract";
import "./sensor-layouts.css";

const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const date = (value: string) => new Date(value).toLocaleString("ko-KR");
type Draft = { name: string; note: string; rig: LayoutRig };
const signature = (draft: Draft) => JSON.stringify([draft.name.trim(), draft.note.trim(), draft.rig]);
const draftFrom = (layout: SensorLayout): Draft => ({ name: layout.name, note: layout.note, rig: structuredClone(layout.rig_b) });

export default function SensorLayoutWorkspace({ project }: { project: string }) {
  const route = useRef(new URLSearchParams(location.hash.split("?")[1] ?? ""));
  const query = new URLSearchParams({ project_id: project }).toString();
  const templates = useResource<LayoutTemplates>(`/sensor-sim/layouts/templates?${query}`);
  const catalogue = useResource<{ items: SensorLayout[] }>(`/sensor-sim/layouts?${query}`);
  const access = useResource<BetaAccess>("/team/whoami");
  const readiness = useResource<BetaReadiness>(`/beta/readiness?${query}`);
  const [layoutId, setLayoutId] = useState(route.current.get("layout")), [revision, setRevision] = useState(route.current.get("revision"));
  const savedResource = useResource<SensorLayout>(layoutId ? `/sensor-sim/layouts/${encodeURIComponent(layoutId)}?${query}${revision ? `&revision=${encodeURIComponent(revision)}` : ""}` : null);
  const revisions = useResource<{ items: SensorLayout[] }>(layoutId ? `/sensor-sim/layouts/${encodeURIComponent(layoutId)}/revisions?${query}` : null);
  const [saved, setSaved] = useState<SensorLayout | null>(null), [draft, setDraft] = useState<Draft | null>(null), [copiedFrom, setCopiedFrom] = useState<LayoutRef | null>(null);
  const [sensor, setSensor] = useState(layoutSensor(route.current.get("sensor"), route.current.get("guide")));
  const [preview, setPreview] = useState<LayoutPreview | null>(null), [busy, setBusy] = useState(""), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [links, setLinks] = useState<LayoutExecution[]>([]), [linksLoading, setLinksLoading] = useState(false), [linksError, setLinksError] = useState(""), [linksVersion, setLinksVersion] = useState(0);
  const [resultId, setResultId] = useState(route.current.get("result")), [runId, setRunId] = useState(route.current.get("run"));
  const [moment, setMoment] = useState<LayoutMoment | null>(null), [seekRequest, setSeekRequest] = useState({ time_s: Math.max(0, Number(route.current.get("time_s")) || 0), sequence: 0 });
  const [observationId, setObservationId] = useState(route.current.get("observation")), [observationTitle, setObservationTitle] = useState(""), [observationNote, setObservationNote] = useState(""), [side, setSide] = useState<"both" | "rig_a" | "rig_b">("both"), [kind, setKind] = useState("observation");
  const guide = templates.data?.tasks.find(task => task.id === route.current.get("guide"));
  const writable = writableBeta(access.data);
  const ready = readiness.data?.items.find(item => item.module === "sensor");
  const actualLink = links.find(link => link.execution_id === resultId) ?? null;
  const resultLayoutResource = useResource<SensorLayout>(actualLink ? `/sensor-sim/layouts/${encodeURIComponent(actualLink.layout_ref.id)}?${query}&revision=${actualLink.layout_ref.revision}` : null);
  const resultLayout = resultLayoutResource.data;
  const observations = useResource<{ items: LayoutObservation[] }>(actualLink ? `/sensor-sim/layout-observations?${query}&execution_id=${encodeURIComponent(actualLink.execution_id)}` : null);
  const shownObservation = observations.data?.items.find(item => item.id === observationId);
  const run = links.find(link => link.execution_id === runId);
  const activeRuns = links.filter(link => !layoutTerminal(link.execution.status));
  const dirty = !!draft && (!saved || signature(draft) !== signature(draftFrom(saved)));
  const mountError = draft ? layoutMountError(draft.rig) : null;
  const previewCurrent = !!preview && !!saved && !dirty && sameLayoutRef(preview.layout_ref, saved);
  const baseline = saved?.rig_a ?? templates.data?.template.rig_a;
  const changes = baseline && draft ? mountChanges(baseline, draft.rig) : [];
  const canExecute = writable && !!ready && ready.new_execution.status !== "blocked";

  useEffect(() => {
    if (savedResource.data) { setSaved(savedResource.data); setDraft(draftFrom(savedResource.data)); setCopiedFrom(null); setPreview(null); setLayoutQuery({ layout: savedResource.data.id, revision: String(savedResource.data.revision) }); }
  }, [savedResource.data]);
  useEffect(() => {
    if (!layoutId && templates.data) { setSaved(null); setDraft(previous => previous ?? { name: "나의 센서 배치", note: "", rig: structuredClone(templates.data!.template.rig_b) }); }
  }, [layoutId, templates.data]);
  useEffect(() => {
    setLinks([]); setMoment(null); setLinksError("");
  }, [layoutId]);
  useEffect(() => {
    if (!layoutId) return;
    let active = true; setLinksLoading(true);
    api<{ items: LayoutExecution[] }>(`/sensor-sim/layouts/${encodeURIComponent(layoutId)}/executions?${query}`).then(value => { if (active) { setLinks(value.items); setLinksError(""); } }).catch(reason => { if (active) setLinksError(errorText(reason)); }).finally(() => { if (active) setLinksLoading(false); });
    return () => { active = false; };
  }, [layoutId, query, linksVersion]);
  useEffect(() => { if (!activeRuns.length || linksError) return; const timer = setTimeout(() => setLinksVersion(value => value + 1), 3000); return () => clearTimeout(timer); }, [links, linksError]);
  useEffect(() => { if (!shownObservation || shownObservation.execution_id !== resultId) return; setSensor(shownObservation.sensor_key); setSide(shownObservation.side); setSeekRequest(previous => ({ time_s: shownObservation.time_s, sequence: previous.sequence + 1 })); }, [shownObservation?.id, resultId]);
  const verifiedMoment = useCallback((value: LayoutMoment | null) => { setMoment(value); if (value) setLayoutQuery({ time_s: String(value.time_s), sensor: value.sensor_key }); }, []);
  function selectSensor(value: typeof sensor) { setMoment(null); setSensor(value); setLayoutQuery({ sensor: value, observation: null }); setObservationId(null); }
  function openSaved(id: string, rev: number | null = null) {
    setSaved(null); setDraft(null); setPreview(null); setError(""); setNotice(""); setCopiedFrom(null); setLayoutId(id); setRevision(rev ? String(rev) : null);
    if (id !== layoutId) { setResultId(null); setRunId(null); setObservationId(null); setLayoutQuery({ result: null, run: null, time_s: null, observation: null }); }
    setLayoutQuery({ layout: id, revision: rev ? String(rev) : null });
    if (id === layoutId && (rev ? String(rev) : null) === revision) savedResource.reload();
  }
  function newDraft(clone: boolean) {
    const source = clone && saved && draft ? { ...draft, name: `${draft.name} · 복사`, rig: structuredClone(draft.rig) } : templates.data ? { name: "나의 센서 배치", note: "", rig: structuredClone(templates.data.template.rig_b) } : null;
    setCopiedFrom(clone && saved ? layoutRef(saved) : null); setDraft(source); setSaved(null); setLayoutId(null); setRevision(null); setPreview(null); setResultId(null); setRunId(null); setError(""); setNotice(""); setObservationId(null);
    setLayoutQuery({ layout: null, revision: null, result: null, run: null, observation: null, time_s: null });
  }
  async function save() {
    if (!draft || mountError || !draft.name.trim()) return;
    setBusy("save"); setError(""); setNotice("");
    try {
      const latest = Math.max(saved?.revision ?? 0, ...((revisions.data?.items ?? []).map(item => item.revision)));
      const value = await betaWrite<SensorLayout>("/sensor-sim/layouts", `sensor-layout:${project}:${saved?.id ?? "new"}`, { project_id: project, ...(saved ? { id: saved.id } : {}), expected_revision: saved ? latest : 0, name: draft.name.trim(), note: draft.note.trim(), rig_b: draft.rig, ...(copiedFrom ? { copied_from: copiedFrom } : {}) });
      setSaved(value); setDraft(draftFrom(value)); setLayoutId(value.id); setRevision(String(value.revision)); setPreview(null); setCopiedFrom(null); setLayoutQuery({ layout: value.id, revision: String(value.revision) }); catalogue.reload(); revisions.reload(); setNotice(`배치 r${value.revision}을 저장했습니다.`);
    } catch (reason) { setError(errorText(reason)); } finally { setBusy(""); }
  }
  async function prepare() {
    if (!saved || dirty) return; setBusy("preview"); setPreview(null); setError("");
    try { const value = await api<LayoutPreview>(`/sensor-sim/layouts/${encodeURIComponent(saved.id)}/preview`, { method: "POST", body: JSON.stringify({ project_id: project, layout_ref: layoutRef(saved) }) }); if (!sameLayoutRef(value.layout_ref, saved)) throw new Error("Preview의 저장 배치 개정이 다릅니다."); setPreview(value); }
    catch (reason) { setError(errorText(reason)); } finally { setBusy(""); readiness.reload(); }
  }
  async function execute() {
    if (!saved || !preview || !previewCurrent) return; setBusy("execute"); setError("");
    try {
      const value = await betaWrite<LayoutExecution>(`/sensor-sim/layouts/${encodeURIComponent(saved.id)}/executions`, `sensor-layout-execute:${project}:${saved.id}`, { project_id: project, layout_ref: layoutRef(saved), preview_hash: preview.preview.preview_hash });
      setLinks(previous => [value, ...previous.filter(item => item.execution_id !== value.execution_id)]); setRunId(value.execution_id); setLayoutQuery({ run: value.execution_id }); setPreview(null); setNotice("A/B 주행을 제출했습니다. 완료 뒤 새 결과를 열어주세요.");
    } catch (reason) { const stale = reason instanceof ApiError && [409, 422].includes(reason.status); if (stale) setPreview(null); setError(`${errorText(reason)} · ${stale ? "변경·실행 가능 여부를 다시 확인하세요." : "응답이 불확실하면 같은 실행 버튼으로 재시도하세요."}`); } finally { setBusy(""); }
  }
  function openResult(id: string, time_s = 0, observation?: LayoutObservation) { setResultId(id); setMoment(null); setSeekRequest(previous => ({ time_s, sequence: previous.sequence + 1 })); setObservationId(observation?.id ?? null); if (observation) { setSensor(observation.sensor_key); setSide(observation.side); } setLayoutQuery({ result: id, time_s: String(time_s), observation: observation?.id ?? null, ...(observation ? { sensor: observation.sensor_key } : {}) }); }
  async function saveObservation() {
    if (!moment || !actualLink || !resultLayout || moment.execution_id !== actualLink.execution_id) return;
    setBusy("observation"); setError("");
    try { const value = await betaWrite<LayoutObservation>("/sensor-sim/layout-observations", `sensor-layout-note:${project}:${actualLink.execution_id}`, { project_id: project, execution_id: actualLink.execution_id, layout_ref: layoutRef(resultLayout), sensor_key: moment.sensor_key, side, time_s: moment.time_s, title: observationTitle.trim(), note: observationNote.trim(), kind }); setObservationId(value.id); setLayoutQuery({ observation: value.id }); observations.reload(); setObservationTitle(""); setObservationNote(""); setNotice("현재 확인된 프레임에 관찰을 저장했습니다。"); }
    catch (reason) { setError(errorText(reason)); } finally { setBusy(""); }
  }
  async function download() {
    if (!saved) return; setBusy("export"); setError("");
    try { const response = await fetch(`/api/v1/sensor-sim/layouts/${encodeURIComponent(saved.id)}/export?${query}&revision=${saved.revision}`, { headers: authHeaders() }); if (!response.ok) throw new Error(`내보내기 실패 (${response.status})`); const blob = await response.blob(); const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = `sensor-layout-${saved.id}-r${saved.revision}.json`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
    catch (reason) { setError(errorText(reason)); } finally { setBusy(""); }
  }

  return <section className="sensor-layout-workspace" aria-label="Sensor Sim 배치 베타">
    <header className="layout-workspace-heading"><div><span className="sensor-demo-eyebrow">SENSOR PLACEMENT · INTERNAL BETA</span><h1>센서 위치를 바꾸고, 같은 주행을 비교하세요.</h1><p>A는 고정 기준 · B는 위치와 회전만 변경 · Town10HD_Opt · 20초 · 10 Hz · RGB 2 + LiDAR + radar</p></div><div className="layout-actions"><button onClick={() => newDraft(false)} disabled={!writable || !!busy || !templates.data}><Plus size={15}/>새 배치</button><button onClick={() => newDraft(true)} disabled={!writable || !!busy || !saved}><Copy size={15}/>복제</button><button onClick={() => void download()} disabled={!saved || !!busy}><Download size={15}/>내보내기</button></div></header>
    <div className="layout-readiness" aria-label="센서 배치 준비 상태"><span><b>배치 작성</b> {ready?.authoring.status === "available" ? "사용 가능" : ready?.authoring.reason ?? "공통 베타 준비 상태 확인 중"}</span><span><b>저장 결과</b> {ready?.retained_playback.reason ?? "보존된 실행이 있으면 재생 가능"}</span><span><b>새 실행</b> {ready?.new_execution.reason ?? "worker 준비 여부 미확인"}</span><button onClick={readiness.reload} aria-label="센서 준비 상태 새로고침"><RefreshCw size={14}/></button></div>
    {guide && <div className="layout-guide"><strong>{guide.title}</strong><span>{guide.description}</span><button disabled={!draft || !writable || !!busy} onClick={() => { if (!draft) return; const next = structuredClone(draft.rig), target = next.sensors.find(item => item.key === guide.sensor_key)!; if (guide.translation_m) target.translation_m = [...guide.translation_m]; if (guide.rotation_deg) target.rotation_deg = [...guide.rotation_deg]; setDraft({ ...draft, rig: next }); setPreview(null); selectSensor(guide.sensor_key); }}>과제 예시값 적용</button></div>}
    <div className="layout-library"><label>저장 배치<select aria-label="저장 센서 배치" value={layoutId ?? ""} disabled={!!busy} onChange={event => event.target.value ? openSaved(event.target.value) : newDraft(false)}><option value="">새 배치</option>{layoutId && !catalogue.data?.items.some(item => item.id === layoutId) && <option value={layoutId}>{layoutId}</option>}{catalogue.data?.items.map(item => <option key={item.id} value={item.id}>{item.name} · r{item.revision}</option>)}</select></label><label>저장 개정<select aria-label="센서 배치 저장 개정" value={saved?.revision ?? revision ?? ""} disabled={!layoutId || !!busy} onChange={event => openSaved(layoutId!, Number(event.target.value))}><option value="" disabled>개정 선택</option>{revisions.data?.items.map(item => <option key={item.revision} value={item.revision}>r{item.revision} · {date(item.created_at)}</option>)}</select></label>{saved && <span className="layout-hint">{saved.id} · {saved.sha256.slice(0, 12)}{dirty ? " · 편집 중" : " · 저장됨"}</span>}</div>
    {(templates.error || savedResource.error || catalogue.error || revisions.error || readiness.error) && <p role="alert" className="sensor-demo-error">{templates.error || savedResource.error || catalogue.error || revisions.error || readiness.error}<button onClick={() => { templates.reload(); savedResource.reload(); catalogue.reload(); revisions.reload(); readiness.reload(); }}>다시 읽기</button></p>}
    {error && <p role="alert" className="sensor-demo-error">{error}</p>}{notice && <p role="status" className="layout-notice">{notice}</p>}
    <div className="layout-workspace-grid"><div className="layout-authoring">{draft && baseline ? <>
      <label>배치 이름<input aria-label="센서 배치 이름" maxLength={180} value={draft.name} disabled={!writable || !!busy} onChange={event => { setDraft({ ...draft, name: event.target.value }); setPreview(null); }}/></label><label>설명<input aria-label="센서 배치 설명" maxLength={4000} value={draft.note} disabled={!writable || !!busy} onChange={event => { setDraft({ ...draft, note: event.target.value }); setPreview(null); }}/></label>
      <LayoutEditor baseline={baseline} rig={draft.rig} selected={sensor} onSelect={selectSensor} onChange={rig => { setDraft({ ...draft, rig }); setPreview(null); }} disabled={!writable || !!busy}/>
      {mountError && <p role="alert" className="sensor-demo-error">{mountError}</p>}
      <div className="layout-save-actions"><button className="button" disabled={!writable || !!busy || !dirty || !!mountError || !draft.name.trim()} onClick={() => void save()}><Save size={15}/>{busy === "save" ? "저장 중…" : saved ? "새 개정 저장" : "배치 저장"}</button><button disabled={!canExecute || !!busy || dirty || !saved || !changes.length} onClick={() => void prepare()}>{busy === "preview" ? "엔진·변경 확인 중…" : "변경·실행 가능 여부 확인"}</button></div>
      {!writable && <p className="layout-hint">읽기 권한입니다. 저장·실행에는 Engineer 권한이 필요합니다.</p>}{dirty && <p className="layout-hint">현재 편집값을 저장하면 실행 변경을 확인할 수 있습니다.</p>}
      {previewCurrent && <div className="layout-preview"><strong>저장된 r{saved!.revision} · A/B 모두 새로 수집</strong><p>변경 {preview!.changes.length}개 · {preview!.preview.job_count}개 작업 · 20초 × 2회</p><p>기존 결과는 실행 중에도 그대로 재생합니다.</p><button className="button" onClick={() => void execute()} disabled={!canExecute || !!busy}>{busy === "execute" ? "제출 중…" : "확인한 배치로 A/B 실행"}</button></div>}
      <details className="layout-history"><summary>저장 이력 · 정확한 개정 열기</summary>{revisions.data?.items.map(item => <button key={item.revision} onClick={() => openSaved(item.id, item.revision)} disabled={!!busy}>r{item.revision} · {item.name}<small>{date(item.created_at)} · {item.sha256.slice(0, 12)}</small></button>)}</details>
    </> : <div className="sensor-presenter-loading">배치 입력 불러오는 중…</div>}</div>
    <div className="layout-evidence">
      {run && <div className="layout-run-status" role="status"><strong>{run.execution.status === "completed" ? "새 A/B 실행 완료" : `실행 ${run.execution.status}`}</strong><span>{run.execution.error ?? run.execution_id}</span>{run.execution.status === "completed" && <button onClick={() => openResult(run.execution_id)}>새 결과 열기</button>}</div>}
      <div className="layout-results-select"><label>실행 결과<select aria-label="센서 배치 실행 결과" value={resultId ?? ""} onChange={event => openResult(event.target.value)}><option value="">저장된 실행 선택</option>{links.map(link => <option key={link.execution_id} value={link.execution_id}>r{link.layout_ref.revision} · {link.execution.status} · {date(link.created_at)}</option>)}</select></label><button onClick={() => setLinksVersion(value => value + 1)} disabled={!layoutId || linksLoading} aria-label="센서 실행 새로고침"><RefreshCw size={15}/></button></div>
      {linksError && <p role="alert" className="sensor-demo-error">{linksError}</p>}
      {resultId && !actualLink && !linksLoading && !linksError && <p role="alert" className="sensor-demo-error">이 배치에 연결된 실행을 찾지 못했습니다. 저장 목록에서 실행을 선택하세요.</p>}
      {actualLink && actualLink.execution.status !== "completed" && <p className="layout-notice">선택 실행: {actualLink.execution.status} {actualLink.execution.error} · 완료된 결과를 선택하면 원본을 재생할 수 있습니다.</p>}
      {resultLayoutResource.error && <p role="alert" className="sensor-demo-error">{resultLayoutResource.error}</p>}
      {actualLink && resultLayout && actualLink.execution.status === "completed" && !actualLink.execution.outcome_uncertain && <LayoutPresenter key={actualLink.execution_id} link={actualLink} layout={resultLayout} sensor={sensor} onSensor={selectSensor} onMoment={verifiedMoment} seekRequest={seekRequest}/>}
      {!resultId && <div className="layout-empty"><span>EDIT → SAVE → CAPTURE → COMPARE</span><h2>배치를 저장하고 결과를 연결하세요.</h2><p>엔진이 꺼져 있어도 배치 작성·복제·이력 확인은 가능합니다.<br/>완료된 실행을 선택하면 같은 시점의 A/B 센서를 비교합니다.</p><p>렌즈·감도·채널·출력 수는 고정합니다. 이 단계는 장착 배치 비교입니다.</p></div>}
      {actualLink && resultLayout && <section className="layout-observations" aria-label="배치 결과 관찰"><h3>현재 프레임에 관찰 남기기</h3><p>{moment ? `${sensorLabels[moment.sensor_key]} · ${moment.time_s.toFixed(1)} s · 표본 ${moment.index + 1} · 원본 확인됨` : "재생을 멈추고 원본 프레임이 준비되면 관찰을 저장할 수 있습니다."}</p><form onSubmit={event => { event.preventDefault(); void saveObservation(); }}><div className="layout-observation-fields"><label>관찰 제목<input aria-label="센서 배치 관찰 제목" maxLength={180} required value={observationTitle} onChange={event => setObservationTitle(event.target.value)} disabled={!writable || !!busy}/></label><label>비교 대상<select aria-label="센서 관찰 비교 대상" value={side} onChange={event => setSide(event.target.value as typeof side)}><option value="both">A·B 둘 다</option><option value="rig_a">A 기준</option><option value="rig_b">B 변경</option></select></label><label>종류<select value={kind} onChange={event => setKind(event.target.value)}><option value="observation">관찰</option><option value="issue">문제 발견</option></select></label></div><label>관찰 내용<textarea aria-label="센서 배치 관찰 내용" maxLength={4000} required value={observationNote} onChange={event => setObservationNote(event.target.value)} disabled={!writable || !!busy}/></label><button className="button secondary" type="submit" disabled={!writable || !!busy || !moment || !observationTitle.trim() || !observationNote.trim()}>{busy === "observation" ? "관찰 저장 중…" : "현재 원본 시점에 관찰 저장"}</button><span className="layout-hint">사용자 관찰 · 자동 판정 또는 물리 검증 아님</span></form>{shownObservation && <div className="layout-selected-observation"><strong>{shownObservation.title}</strong><p>{shownObservation.note}</p><span>{sensorLabels[shownObservation.sensor_key]} · {shownObservation.time_s} s · {shownObservation.side}</span></div>}{observations.error && <p role="alert" className="sensor-demo-error">{observations.error}</p>}<div className="layout-observation-list">{observations.data?.items.map(item => <button key={item.id} onClick={() => openResult(item.execution_id, item.time_s, item)}><strong>{item.title}</strong><span>{sensorLabels[item.sensor_key]} · {item.time_s.toFixed(1)} s · {item.side}</span></button>)}</div></section>}
    </div></div>
  </section>;
}
