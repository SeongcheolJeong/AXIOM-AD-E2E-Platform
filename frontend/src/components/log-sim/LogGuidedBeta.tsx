import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, useResource } from "../../api";
import { downloadLog, type ComparisonRef } from "./LogBetaTools";
import "./log-beta.css";

export type BetaTask = {
  id: string; project_id: string; pack_id: string; revision: number;
  source_id: string | null; source_hash: string | null; comparison_ref: ComparisonRef | null;
  batch_id: string | null; case_id: string | null; result_opened: boolean;
  status: string; stage: string; created_at: string; updated_at: string; participant: string;
  execution_status?: string | null; execution_reason?: string | null; review_status?: string | null;
};
type Progress = { comparison_ref?: ComparisonRef; batch_id?: string; case_id?: string; result_opened?: boolean; source_id?: string };
type GuidedContext = { task: BetaTask | null; tasks: BetaTask[]; loading: boolean; error: string; busy: boolean; linkPending: boolean; recover: () => Promise<void>; start: (pack: string) => Promise<void>; resume: (task: BetaTask) => void; progress: (change: Progress) => Promise<void>; refresh: () => Promise<void>; leave: () => void };
const Context = createContext<GuidedContext | null>(null);
export const useLogBeta = () => useContext(Context);
const message = (error: unknown) => error instanceof Error ? error.message : String(error);
export function betaQuery(values: Record<string, string>) {
  const q = new URLSearchParams(values), current = new URLSearchParams(location.hash.split("?")[1] ?? "");
  if (current.get("project") === values.project && current.get("task")) q.set("task", current.get("task")!);
  return q;
}
function retainTask(id: string | null) {
  const q = new URLSearchParams(location.hash.split("?")[1] ?? "");
  if (id) q.set("task", id); else q.delete("task");
  history.replaceState(null, "", `${location.pathname}${location.search}#log-sim?${q}`);
}
export function betaWriteKey(scope: string, body: string) {
  const storage = `axiom.logBeta.pending.${scope}`;
  let pending: { body: string; key: string } | null = null;
  try { pending = JSON.parse(sessionStorage.getItem(storage) ?? "null"); } catch { /* invalid local cache */ }
  if (pending?.body === body) return pending.key;
  const key = crypto.randomUUID(); sessionStorage.setItem(storage, JSON.stringify({ body, key })); return key;
}
export function LogBetaProvider({ project, initialTaskId, children }: { project: string; initialTaskId: string | null; children: ReactNode }) {
  const list = useResource<{ items: BetaTask[] }>(project ? `/log-sim/beta-tasks?project_id=${encodeURIComponent(project)}` : null);
  const [task, setTask] = useState<BetaTask | null>(null), [loading, setLoading] = useState(!!initialTaskId), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const [linkPending, setLinkPending] = useState(!!(initialTaskId && sessionStorage.getItem(`axiom.logBeta.link.${initialTaskId}`)));
  const current = useRef<BetaTask | null>(null), queue = useRef(Promise.resolve()), requested = useRef(initialTaskId);
  const assign = useCallback((value: BetaTask | null) => { if (value && current.current?.id === value.id && current.current.revision > value.revision) return; current.current = value; setTask(value); }, []);
  const read = useCallback(async (id: string) => api<BetaTask>(`/log-sim/beta-tasks/${encodeURIComponent(id)}?project_id=${encodeURIComponent(project)}`), [project]);
  const refresh = useCallback(async () => {
    const id = current.current?.id ?? requested.current; if (!id) return;
    try { const value = await read(id); if (current.current?.id === id || !current.current) assign(value); setError(""); }
    catch (e) { setError(message(e)); }
  }, [read, assign]);
  useEffect(() => {
    requested.current = initialTaskId;
    if (!initialTaskId) { setLoading(false); return; }
    let active = true; setLoading(true);
    read(initialTaskId).then(value => { if (active) { assign(value); setError(""); } }).catch(e => { if (active) setError(message(e)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [initialTaskId, read, assign]);
  useEffect(() => {
    if (!task?.batch_id || task.status === "completed") return;
    const timer = setInterval(() => void refresh(), 3000); return () => clearInterval(timer);
  }, [task?.batch_id, task?.status, refresh]);
  useEffect(() => { setLinkPending(!!(task && sessionStorage.getItem(`axiom.logBeta.link.${task.id}`))); }, [task?.id]);
  const progress = useCallback((change: Progress): Promise<void> => {
    const requestedTask = task?.id;
    if (!requestedTask) return Promise.resolve();
    const operation = queue.current.then(async () => {
      const value = current.current; if (!value || value.id !== requestedTask) return;
      setBusy(true); setError("");
      const storageKey = `axiom.logBeta.link.${value.id}`;
      let prior: Progress = {}; try { prior = JSON.parse(sessionStorage.getItem(storageKey) ?? "{}"); } catch { /* invalid local state */ }
      const combined = { ...prior, ...change };
      sessionStorage.setItem(storageKey, JSON.stringify(combined)); setLinkPending(true);
      const body = JSON.stringify({ project_id: project, expected_revision: value.revision, ...combined });
      try {
        const saved = await api<BetaTask>(`/log-sim/beta-tasks/${encodeURIComponent(value.id)}/progress`, { method: "POST", body, headers: { "Idempotency-Key": betaWriteKey(`progress.${value.id}`, body) } });
        sessionStorage.removeItem(storageKey);
        if (current.current?.id === value.id) { assign(saved); setLinkPending(false); }
        list.reload();
      } catch (e) { setError(`업무 연결 저장: ${message(e)}`); throw e; }
      finally { setBusy(false); }
    });
    queue.current = operation.catch(() => {}); return operation;
  }, [project, task?.id, assign, list.reload]);
  const recover = useCallback(() => progress({}), [progress]);
  const resume = (value: BetaTask) => { assign(value); retainTask(value.id); setError(""); };
  async function start(pack: string) {
    setBusy(true); setError("");
    try {
      const existing = list.data?.items.find(value => value.pack_id === pack && value.status !== "completed");
      if (existing) resume(await read(existing.id));
      else {
        const body = JSON.stringify({ project_id: project, pack_id: pack });
        const value = await api<BetaTask>("/log-sim/beta-tasks", { method: "POST", body, headers: { "Idempotency-Key": betaWriteKey(`start.${project}.${pack}`, body) } });
        resume(value); list.reload();
        sessionStorage.removeItem(`axiom.logBeta.pending.start.${project}.${pack}`);
      }
      requestAnimationFrame(() => document.getElementById(pack === "input-errors" ? "log-beta-upload" : "log-beta-settings")?.scrollIntoView({ behavior: "smooth", block: "center" }));
    } catch (e) { setError(message(e)); }
    finally { setBusy(false); }
  }
  const leave = () => { assign(null); requested.current = null; retainTask(null); setError(""); setLinkPending(false); };
  return <Context.Provider value={{ task, tasks: list.data?.items ?? [], loading, error: error || list.error, busy, linkPending, recover, start, resume, progress, refresh, leave }}>{children}</Context.Provider>;
}
const steps = [["source", "원본 확인"], ["settings", "조건 저장"], ["execution", "A/B 실행"], ["investigation", "결과 조사"], ["review", "Case 검토"]];
const titles: Record<string, string> = { "following-gap": "차간 거리", "stop-resume": "정지·재출발", "lead-brake": "앞차 감속", "cut-in": "끼어들기", intersection: "교차로", "input-errors": "입력 오류 확인" };
export function LogBetaTaskBar({ project, onOpenSource }: { project: string; onOpenSource: (id: string, caseId?: string) => void }) {
  const beta = useLogBeta();
  const [feedbackOpen, setFeedbackOpen] = useState(false), [note, setNote] = useState(""), [help, setHelp] = useState(""), [busy, setBusy] = useState(false), [error, setError] = useState(""), [saved, setSaved] = useState("");
  const task = beta?.task;
  useEffect(() => { setFeedbackOpen(false); setNote(""); setHelp(""); setSaved(""); setError(""); }, [task?.id]);
  if (!beta) return null;
  const completed = task?.stage === "completed";
  async function sendFeedback() {
    if (!task) return; setBusy(true); setError("");
    const body = JSON.stringify({ project_id: project, task_id: task.id, stage: task.stage, kind: completed ? "completion" : "blocked", message: note, needs_help: completed ? help === "yes" : true });
    try {
      await api("/log-sim/beta-feedback", { method: "POST", body, headers: { "Idempotency-Key": betaWriteKey(`feedback.${task.id}`, body) } });
      setSaved(completed ? "완료 의견을 저장했습니다. 운영자 현황에서 확인할 수 있습니다." : "막힌 단계와 의견을 저장했습니다."); setFeedbackOpen(false); setNote("");
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  return <section className="log-beta-guide" aria-label="Log Sim beta task progress">
    <header><div><small>GUIDED INTERNAL BETA</small><h2>{task ? `${titles[task.pack_id] ?? task.pack_id} · ${completed ? "업무 완료" : "이어서 조사하기"}` : "대표 로그로 시작하세요"}</h2></div>{task && <button className="button secondary" aria-label="Log Sim beta blocked" onClick={() => setFeedbackOpen(value => !value)}>막혔어요</button>}</header>
    {!task && !beta.loading && <p>팩 하나에서 조건 변경 → 실행 → 조사 → 검토를 완료합니다. 시작 버튼은 실행을 예약하지 않습니다.</p>}
    {beta.loading && <p role="status">저장된 업무를 복원하고 있습니다…</p>}
    {task && <>
      <ol className="log-beta-steps">{steps.map(([id, title], i) => <li key={id} className={completed || steps.findIndex(([s]) => s === task.stage) > i ? "done" : task.stage === id ? "current" : ""}><span>{i + 1}</span>{title}</li>)}</ol>
      <p>{({ source: "정상·누락 예제를 내려받아 입력 오류를 확인하고, 정상 입력을 등록하세요.", settings: "아래에서 조건 하나를 바꾸고 비교 설정을 저장하세요.", execution: task.batch_id ? "실행 상태를 확인하세요. 완료한 결과와 실행 기록을 유지합니다." : "저장된 조건을 확인하고 분석 준비 → A/B 실행을 누르세요.", investigation: "후보를 열어 차이를 조사하세요. 후보가 없으면 원본의 구간을 직접 Case로 저장할 수 있습니다.", review: "Case에 관찰을 남기고 상태를 ‘검토 완료’로 저장하세요.", completed: "비교 실행과 Case 검토가 연결됐습니다. 도움 필요 여부와 사용 의견을 남겨 주세요." } as Record<string, string>)[task.stage]}</p>
      <small>원본 {task.source_id ?? "등록 대기"} · 설정 {task.comparison_ref ? `r${task.comparison_ref.revision}` : "저장 전"} · {task.id}</small>
      <div className="log-beta-actions">
        {task.batch_id && <a className="button secondary" href={`#log-sim?${new URLSearchParams({ project, task: task.id, batch: task.batch_id })}`}>이 업무의 A/B 결과</a>}
        {task.source_id && <button className="button secondary" onClick={() => onOpenSource(task.source_id!, task.case_id ?? undefined)}>{task.case_id ? "연결된 Case 검토" : "원본에서 구간 선택"}</button>}
        {completed && <button className="button" aria-label="Log Sim beta completion feedback" onClick={() => setFeedbackOpen(true)}>완료 의견 남기기</button>}
        <button className="button secondary" onClick={() => void beta.refresh()}>업무 상태 새로고침</button>
        <button className="log-sim-text-button" onClick={beta.leave}>일반 조사로 돌아가기</button>
      </div>
      {task.execution_reason && <p role="alert">실행 확인: {task.execution_reason} · A/B 결과에서 실패 실행을 다시 시도할 수 있습니다.</p>}
      {feedbackOpen && <div className="log-beta-feedback"><label>막힌 내용 또는 사용 의견<textarea aria-label="Log Sim beta feedback message" maxLength={4000} value={note} onChange={e => setNote(e.target.value)} placeholder="기대했던 동작과 실제로 막힌 부분을 적어 주세요. 토큰이나 원본 파일은 넣지 마세요." /></label>{completed && <label>완료하는 데 도움이 필요했나요?<select aria-label="Log Sim beta feedback needs help" value={help} onChange={e => setHelp(e.target.value)}><option value="">선택</option><option value="no">혼자 완료했어요</option><option value="yes">도움이 필요했어요</option></select></label>}<button className="button" aria-label="Send Log Sim beta feedback" disabled={busy || !note.trim() || (completed && !help)} onClick={() => void sendFeedback()}>의견 저장</button></div>}
    </>}
    {!task && beta.tasks.length > 0 && <label>이전 업무 이어하기<select aria-label="Resume Log Sim beta task" disabled={beta.loading || beta.busy} value="" onChange={e => { const found = beta.tasks.find(t => t.id === e.target.value); if (found) beta.resume(found); }}><option value="">업무 선택</option>{beta.tasks.map(t => <option key={t.id} value={t.id}>{titles[t.pack_id] ?? t.pack_id} · {t.stage} · {new Date(t.updated_at).toLocaleString("ko-KR")}</option>)}</select></label>}
    {beta.linkPending && !beta.busy && <div className="log-beta-no-candidates"><p>저장된 조건·실행·Case의 업무 연결을 확인해야 합니다. 기존 자료는 그대로 보존돼 있습니다.</p><button className="button" aria-label="Recover Log Sim beta task link" onClick={() => void beta.recover().catch(() => {})}>저장한 업무 연결 복구</button></div>}
    {(beta.error || error) && <div role="alert" className="log-sim-error"><p>{error || beta.error}</p><div className="log-beta-actions"><button className="button secondary" onClick={() => void beta.refresh()}>업무 연결 다시 확인</button><button className="button secondary" onClick={() => (document.querySelector('[aria-label="Workspace settings"]') as HTMLButtonElement | null)?.click()}>Workspace 설정 열기</button></div></div>}
    {saved && <p role="status">{saved}</p>}
  </section>;
}
export function LogBetaOverview({ operator }: { operator: boolean }) {
  const data = useResource<{ tasks: BetaTask[]; feedback: { task_id: string; kind: string; message: string; needs_help: boolean | null }[]; counts: Record<string, number> }>(operator ? "/log-sim/beta-overview?format=json" : null);
  const [error, setError] = useState(""); if (!operator) return null;
  return <details className="log-beta-guide" aria-label="Log Sim beta overview"><summary>운영자 · 참가자 진행 현황</summary><div className="log-beta-actions"><button className="button secondary" onClick={data.reload}>현황 새로고침</button>{["json", "csv"].map(format => <button key={format} className="button secondary" onClick={() => void downloadLog(`/log-sim/beta-overview?format=${format}`, `log-beta-overview.${format}`).catch(e => setError(message(e)))}>{format.toUpperCase()} 내보내기</button>)}</div><p>자동 검사 통과와 실제 참가자의 도움 없는 완료를 구분합니다. 목표: 5명 중 4명.</p>{data.data && <><div className="log-beta-overview-counts">{Object.entries(data.data.counts).map(([key, value]) => <span key={key}>{key}: <strong>{value}</strong></span>)}</div><div className="log-batch-table-scroll"><table className="log-batch-candidates"><thead><tr><th>참가자</th><th>업무</th><th>진행</th><th>의견</th></tr></thead><tbody>{data.data.tasks.map(task => <tr key={task.id}><td>{task.participant}</td><td>{titles[task.pack_id] ?? task.pack_id}</td><td>{task.stage} · {task.status}</td><td>{data.data!.feedback.filter(f => f.task_id === task.id).map((f, index) => <p key={index}>{f.kind === "blocked" ? "막힘" : f.needs_help ? "도움 필요" : "도움 없이 완료"}: {f.message}</p>)}</td></tr>)}</tbody></table></div></>}{(error || data.error) && <p role="alert">{error || data.error}</p>}</details>;
}
