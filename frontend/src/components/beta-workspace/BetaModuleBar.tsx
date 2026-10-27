import { useEffect, useState } from "react";
import { ArrowLeft, ClipboardList, HelpCircle, X } from "lucide-react";
import { useResource } from "../../api";
import BetaFeedbackForm from "./BetaFeedbackForm";
import BetaTaskProgress from "./BetaTaskProgress";
import { betaQuery, moduleNames, rememberedBetaTask, rememberBetaTask, writableBeta } from "./beta-contract";
import type { BetaAccess, BetaModule, BetaTask } from "./beta-contract";
import "./beta-workspace.css";

const modulePages: Record<string, BetaModule> = { "object-sim": "object", "log-sim": "log", "hil-sim": "hil", "sensor-sim": "sensor", "neural-sim": "neural" };
function contextFromRoute(fallback: string) {
  const query = new URLSearchParams(location.hash.split("?")[1] ?? "");
  const time = query.get("time_s");
  return { project: query.get("project") ?? fallback, task: query.get("beta_task") ?? query.get("task"), execution: query.get("result") ?? query.get("execution") ?? query.get("right") ?? query.get("left") ?? query.get("run"), time: time !== null && Number.isFinite(Number(time)) && Number(time) >= 0 ? Number(time) : null };
}

export default function BetaModuleBar({ page, route, defaultProject }: { page: string; route: string; defaultProject: string }) {
  const module = modulePages[page];
  const [context, setContext] = useState(() => contextFromRoute(defaultProject));
  const [open, setOpen] = useState(false);
  // Module editors keep their URL current using replaceState without a hashchange.
  // Observe only the scope; no API polling or player seeking is performed here.
  useEffect(() => {
    const update = () => setContext(previous => { const next = contextFromRoute(defaultProject); return previous.project === next.project && previous.task === next.task && previous.execution === next.execution ? previous : next; });
    update(); const timer = window.setInterval(update, 1000);
    window.addEventListener("hashchange", update);
    return () => { window.clearInterval(timer); window.removeEventListener("hashchange", update); };
  }, [route, defaultProject]);
  if (!module || !context.project) return null;
  const home = `#workspace?${new URLSearchParams({ project: context.project, ...(context.task ? { beta_task: context.task } : {}) })}`;
  return <aside className="beta-module-bar" aria-label="ADP 베타 과제와 도움"><div className="beta-module-bar-heading"><a href={home} className="beta-text-link"><ArrowLeft size={14} />베타 작업공간</a><span>{moduleNames[module]} · {context.project}</span><button className="button secondary" aria-label="ADP 베타 도움 열기" aria-expanded={open} onClick={() => { setContext(contextFromRoute(defaultProject)); setOpen(value => !value); }}>{open ? <X size={14} /> : <HelpCircle size={14} />}{open ? "닫기" : "과제 연결·도움 요청"}</button></div>{open && <BetaModulePanel key={`${context.project}:${module}:${context.task ?? "none"}`} project={context.project} module={module} routeTask={context.task} executionId={context.execution} initialTime={context.time} />}</aside>;
}

function BetaModulePanel({ project, module, routeTask, executionId, initialTime }: { project: string; module: BetaModule; routeTask: string | null; executionId: string | null; initialTime: number | null }) {
  const identity = useResource<BetaAccess>("/team/whoami");
  const tasks = useResource<{ items: BetaTask[] }>(`/beta/tasks?${betaQuery(project)}`);
  const [chosen, setChosen] = useState<string | null>(routeTask), [progressOpen, setProgressOpen] = useState(false);
  const [feedbackVersion, setFeedbackVersion] = useState(0);
  const available = tasks.data?.items.filter(item => item.module === module) ?? [];
  useEffect(() => {
    if (chosen !== null || !identity.data) return;
    setChosen(rememberedBetaTask(project, identity.data.principal_id) ?? "");
  }, [identity.data, chosen, project]);
  const task = available.find(item => item.id === chosen) ?? null;
  const writable = writableBeta(identity.data);
  function saved(value: BetaTask) {
    tasks.setData(previous => ({ items: (previous?.items ?? []).map(item => item.id === value.id ? value : item) }));
    setFeedbackVersion(value => value + 1);
  }
  return <div className="beta-module-panel"><div className="beta-form-grid"><label>현재 화면의 과제<select aria-label="현재 모듈 베타 과제" value={task?.id ?? ""} onChange={event => { setChosen(event.target.value); const selected = available.find(item => item.id === event.target.value); if (selected) rememberBetaTask(selected, identity.data?.principal_id ?? ""); setFeedbackVersion(value => value + 1); }}><option value="">과제 연결 없이 의견 남기기</option>{available.map(item => <option value={item.id} key={item.id}>{item.title}</option>)}</select></label>{task && <button className="button secondary beta-align-end" aria-expanded={progressOpen} onClick={() => setProgressOpen(value => !value)}><ClipboardList size={14} />{progressOpen ? "진행 연결 닫기" : "저장 근거·진행 연결"}</button>}</div>{chosen && !task && !tasks.loading && !tasks.error && <p className="beta-hint">현재 모듈과 프로젝트에서 연결된 과제를 찾지 못했습니다. 과제를 선택하거나 의견만 남길 수 있습니다.</p>}{(tasks.error || identity.error) && <p className="beta-error" role="alert">{tasks.error || identity.error}<button className="beta-text-button" onClick={() => { tasks.reload(); identity.reload(); }}>다시 확인</button></p>}{task && progressOpen && <BetaTaskProgress key={task.id} task={task} onSaved={saved} writable={writable} />}<BetaFeedbackForm key={`${task?.id ?? "none"}:${feedbackVersion}`} project={project} module={module} task={task} executionId={executionId} initialTime={initialTime ?? task?.time_s} writable={writable} /></div>;
}
