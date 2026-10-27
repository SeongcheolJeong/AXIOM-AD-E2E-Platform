import { useEffect, useState } from "react";
import { ArrowRight, ArrowUpRight, CheckCircle2, Clock3, ClipboardList, HelpCircle, Plus, RefreshCw } from "lucide-react";
import { useResource } from "../../api";
import BetaFeedbackForm from "./BetaFeedbackForm";
import BetaTaskProgress from "./BetaTaskProgress";
import BetaOperatorOverview from "./BetaOperatorOverview";
import { betaDate, betaError, betaLink, betaQuery, betaWrite, moduleNames, rememberBetaTask, sensorGuides, taskLink, taskStatus, writableBeta } from "./beta-contract";
import type { BetaAccess, BetaEvidence, BetaMode, BetaModule, BetaReadiness, BetaTask, SensorGuide } from "./beta-contract";
import "./beta-workspace.css";

export default function BetaWorkspace({ project }: { project: string }) {
  const access = useResource<BetaAccess>("/team/whoami");
  const readiness = useResource<BetaReadiness>(`/beta/readiness?${betaQuery(project)}`);
  const tasks = useResource<{ items: BetaTask[] }>(`/beta/tasks?${betaQuery(project)}`);
  const [selected, setSelected] = useState(() => new URLSearchParams(location.hash.split("?")[1] ?? "").get("beta_task") ?? "");
  const [createOpen, setCreateOpen] = useState(false), [feedback, setFeedback] = useState(false);
  const [module, setModule] = useState<BetaModule>("object"), [mode, setMode] = useState<BetaMode>("new_execution");
  const [title, setTitle] = useState(""), [pack, setPack] = useState("");
  const [sensorGuide, setSensorGuide] = useState<SensorGuide>("camera_direction");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const packs = useResource<{ items: BetaEvidence[] }>(createOpen && module === "log" ? `/beta/evidence?${betaQuery(project)}&module=log` : null);
  const writable = writableBeta(access.data);
  const task = tasks.data?.items.find(item => item.id === selected) ?? null;
  const items = tasks.data?.items ?? [];
  const active = items.filter(item => item.status !== "completed");
  const operator = !!access.data && access.data.mode !== "project";
  const failure = readiness.error || tasks.error || access.error;
  useEffect(() => { if (!pack && packs.data?.items.some(item => item.kind === "pack")) setPack(packs.data.items.find(item => item.kind === "pack")!.id); }, [packs.data, pack]);
  function selectTask(value: BetaTask) {
    setSelected(value.id); setFeedback(false);
    const query = new URLSearchParams(location.hash.split("?")[1] ?? "");
    query.set("project", project); query.set("beta_task", value.id);
    history.replaceState(null, "", `#workspace?${query}`);
    rememberBetaTask(value, access.data?.principal_id ?? "");
  }
  function savedTask(value: BetaTask) {
    tasks.setData(previous => ({ items: (previous?.items ?? []).map(item => item.id === value.id ? value : item) }));
  }
  async function create(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const value = await betaWrite<BetaTask>("/beta/tasks", `create:${project}:${module}`, {
        project_id: project, module, mode: module === "log" ? "new_execution" : mode,
        title: title.trim() || (module === "sensor" ? `Sensor Sim · ${sensorGuides.find(item => item.id === sensorGuide)!.title}` : `${moduleNames[module]} · 내 첫 베타 과제`),
        ...(module === "log" ? { pack_id: pack } : {}),
        ...(module === "sensor" ? { guide_id: sensorGuide } : {}),
      });
      tasks.setData(previous => ({ items: [value, ...(previous?.items ?? []).filter(item => item.id !== value.id)] }));
      selectTask(value); setCreateOpen(false); setTitle("");
    } catch (reason) { setError(betaError(reason)); }
    finally { setBusy(false); }
  }
  const resume = task ? taskLink(task) : null;
  return <section className="beta-workspace" aria-label="ADP 베타 작업공간">
    <header className="beta-hero"><div><span className="eyebrow">ADP · INTERNAL BETA</span><h2>혼자 시작하고, 함께 개선하는 작업공간</h2><p>{access.data ? `${access.data.user_label}님, ` : ""}과제 하나를 선택해 조건을 바꾸고 결과를 확인하세요.<br />저장한 작업은 이어서 열고, 막힌 지점은 운영자에게 남길 수 있습니다.</p></div><div className="beta-hero-stats"><strong>{tasks.data ? active.length : "—"}</strong><span>진행 중인 과제</span><small>{items.filter(item => item.status === "completed").length}개 완료 · 실제 기록 기준</small></div></header>
    <div className="beta-start-steps"><span><b>01</b> 과제 선택</span><ArrowRight size={14} /><span><b>02</b> 저장·실행·관찰</span><ArrowRight size={14} /><span><b>03</b> 근거 연결·의견</span></div>
    <div className="beta-section-heading"><div><h3>지금 할 수 있는 작업</h3><p>작성·저장 결과 조회·새 실행 상태를 각각 확인하세요.</p></div><button className="button secondary" aria-label="베타 작업공간 새로고침" disabled={readiness.loading || tasks.loading} onClick={() => { readiness.reload(); tasks.reload(); }}><RefreshCw size={14} />새로고침</button></div>
    {readiness.loading && <p role="status" className="beta-hint">현재 프로젝트의 기능과 실행 준비 상태를 확인하고 있습니다…</p>}
    {readiness.data && <div className="beta-readiness-grid">{readiness.data.items.map(item => {
      const entry = betaLink(item.entry_url, project);
      return <article className="beta-readiness-card" key={item.module} aria-label={`${item.title} 베타 준비 상태`}><div><h4>{item.title}</h4><span className="beta-pill">{item.guided ? "안내 과제" : "기존 기능·의견"}</span></div><dl>{([
        ["authoring", "작성·저장"], ["retained_playback", "저장 결과"], ["new_execution", "새 실행"],
      ] as const).map(([key, label]) => <div key={key}><dt>{label}</dt><dd><span className={`beta-readiness-state ${item[key].status}`}>{item[key].status === "available" ? <CheckCircle2 size={12} /> : <Clock3 size={12} />}{({ available: "가능", blocked: "불가", empty: "결과 없음", unknown: "확인 필요" } as Record<string, string>)[item[key].status] ?? "확인 필요"}</span><small>{item[key].reason}</small></dd></div>)}</dl>{entry && <a href={entry} className="beta-text-link">모듈 열기<ArrowUpRight size={13} /></a>}</article>;
    })}</div>}
    {readiness.data && <p className="beta-hint">확인 시각 {betaDate(readiness.data.checked_at)} · 새 실행은 해당 모듈에서 입력과 실행 환경을 확인한 뒤 제출합니다.</p>}
    {failure && <div className="beta-error" role="alert"><strong>베타 작업공간을 확인하지 못했습니다.</strong><p>{failure}</p><button className="button secondary" onClick={() => { readiness.reload(); tasks.reload(); access.reload(); }}>다시 확인</button></div>}
    <div className="beta-section-heading"><div><h3>내 과제 · 이어하기</h3><p>저장 결과 체험과 새 실행 과제를 구분해서 기록합니다.</p></div><button className="button" aria-label="새 베타 과제" disabled={!writable || tasks.loading} onClick={() => setCreateOpen(value => !value)}><Plus size={15} />새 과제</button></div>
    {createOpen && <form className="beta-create-form" aria-label="새 베타 과제 만들기" onSubmit={event => void create(event)}><div className="beta-form-grid"><label>모듈<select aria-label="베타 과제 모듈" disabled={busy} value={module} onChange={event => { setModule(event.target.value as BetaModule); setPack(""); }}><option value="object">Object Sim</option><option value="log">Log Sim</option><option value="hil">Software-DUT HIL</option><option value="sensor">Sensor Sim</option></select></label><label>과제 유형<select aria-label="베타 과제 유형" disabled={busy || module === "log"} value={module === "log" ? "new_execution" : mode} onChange={event => setMode(event.target.value as BetaMode)}><option value="new_execution">조건을 바꾸고 새로 실행</option><option value="retained_playback">저장 결과를 열어 체험</option></select></label>{module === "sensor" && <label>센서 배치 과제<select aria-label="베타 Sensor 안내 과제" disabled={busy} value={sensorGuide} onChange={event => setSensorGuide(event.target.value as SensorGuide)}>{sensorGuides.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>}{module === "log" ? <label>대표 로그<select aria-label="베타 Log 대표 팩" disabled={busy || packs.loading} value={pack} onChange={event => setPack(event.target.value)}><option value="">대표 팩 선택</option>{packs.data?.items.filter(item => item.kind === "pack").map(item => <option value={item.id} key={item.id}>{item.label}</option>)}</select></label> : <label className="beta-full-width">과제 이름<input aria-label="베타 과제 이름" value={title} maxLength={180} disabled={busy} onChange={event => setTitle(event.target.value)} placeholder={`${moduleNames[module]} · 내 첫 베타 과제`} /></label>}</div>{module === "sensor" && <p className="beta-hint">{sensorGuides.find(item => item.id === sensorGuide)?.description} 전방·후방 카메라, LiDAR, Radar의 고정 4개 구성을 사용합니다.</p>}<p className="beta-hint">과제 만들기는 실행을 제출하지 않습니다. {module === "log" ? "기존 Log 안내 과제와 연결됩니다." : mode === "retained_playback" ? "저장 결과 체험 완료는 새 실행 완료에 포함되지 않습니다." : "작성 화면에서 변경한 개정을 저장한 뒤 실행하세요."}</p><div className="beta-actions"><button className="button" type="submit" disabled={busy || module === "log" && !pack}>{busy ? "과제 만드는 중…" : "과제 만들기"}</button><button className="button secondary" type="button" disabled={busy} onClick={() => setCreateOpen(false)}>닫기</button></div>{packs.error && <p className="beta-error" role="alert">{packs.error}</p>}</form>}
    {error && <p className="beta-error" role="alert">{error}</p>}
    {tasks.loading && <p className="beta-hint" role="status">저장된 과제를 불러오고 있습니다…</p>}
    {!tasks.loading && !tasks.error && !items.length && <div className="beta-empty"><ClipboardList size={26} /><div><h4>첫 과제를 시작하세요</h4><p>Object·Log·HIL·Sensor 중 하나를 골라 저장부터 관찰까지 연결합니다. 이미 있는 기능과 결과는 아래 사례 목록에서도 열 수 있습니다.</p></div></div>}
    {items.length > 0 && <div className="beta-task-layout"><div className="beta-task-list" role="group" aria-label="베타 과제 목록">{items.map(item => <button key={item.id} className={`beta-task-card ${selected === item.id ? "selected" : ""}`} aria-pressed={selected === item.id} onClick={() => selectTask(item)}><div><span>{moduleNames[item.module]}</span><span className={`beta-pill ${item.status}`}>{taskStatus[item.status] ?? item.status}</span></div><strong>{item.title}</strong><small>{item.mode === "retained_playback" ? "저장 결과 체험" : "새 실행 과제"} · {item.participant}</small><small>{item.steps.find(step => !step.complete)?.label ?? "연결된 단계 완료"} · {betaDate(item.updated_at)}</small></button>)}</div><div className="beta-task-detail">{task ? <><div className="beta-section-heading"><div><span className="eyebrow">{moduleNames[task.module]} · {task.mode === "retained_playback" ? "저장 결과 체험" : "새 실행 과제"}</span><h3>{task.title}</h3></div>{resume ? <a className="button" aria-label="선택한 베타 과제 이어하기" href={resume} onClick={() => rememberBetaTask(task, access.data?.principal_id ?? "")}>과제 이어하기<ArrowRight size={14} /></a> : <span className="beta-error">이어하기 링크를 확인할 수 없습니다.</span>}</div><p className="beta-hint">{task.status === "completed" ? task.mode === "retained_playback" ? "저장 결과 체험과 관찰 기록을 완료했습니다." : "새 실행과 결과 확인·관찰 기록을 완료했습니다." : "단계 완료는 연결한 저장 기록과 실행 결과로 확인합니다."}{task.execution_status && ` · 실행 상태 ${task.execution_status}`}</p><BetaTaskProgress key={task.id} task={task} onSaved={savedTask} writable={writable} /><button className="beta-text-button beta-help-toggle" aria-expanded={feedback} onClick={() => setFeedback(value => !value)}><HelpCircle size={15} />{feedback ? "의견 작성 닫기" : "이 과제에서 막혔어요 · 의견 남기기"}</button>{feedback && <BetaFeedbackForm key={`${task.id}:${task.revision}`} project={project} module={task.module} task={task} initialTime={task.time_s} writable={writable} />}</> : <div className="beta-empty"><ClipboardList size={24} /><p>과제를 선택하면 진행 단계와 이어하기가 표시됩니다.</p></div>}</div></div>}
    {operator && <BetaOperatorOverview />}
  </section>;
}
