import { useEffect, useState } from "react";
import { ArrowUpRight, Check, Link2, RefreshCw } from "lucide-react";
import { useResource } from "../../api";
import BetaObservationForm from "./BetaObservationForm";
import { betaError, betaQuery, betaSensorNames, betaWrite, matchingBetaObservation, sameBetaRef, taskLink } from "./beta-contract";
import type { BetaEvidence, BetaSensorKey, BetaTask } from "./beta-contract";

export default function BetaTaskProgress({ task, onSaved, writable = true }: { task: BetaTask; onSaved: (task: BetaTask) => void; writable?: boolean }) {
  const evidence = useResource<{ items: BetaEvidence[] }>(task.module !== "log" ? `/beta/evidence?${betaQuery(task.project_id)}&module=${task.module}` : null);
  const [source, setSource] = useState(""), [execution, setExecution] = useState(task.execution_id ?? ""), [observation, setObservation] = useState(task.observation_id ?? "");
  const [opened, setOpened] = useState(task.result_opened), [time, setTime] = useState(task.time_s === null ? "" : String(task.time_s));
  const [sensor, setSensor] = useState<BetaSensorKey>(task.sensor_key ?? "front");
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const items = evidence.data?.items ?? [];
  const sources = items.filter(item => item.kind === "saved_revision");
  const chosenSource = sources.find(item => item.id === source);
  const executions = items.filter(item => item.kind === "execution" && (!chosenSource?.source_ref || sameBetaRef(chosenSource.source_ref, item.source_ref)) && (task.mode !== "new_execution" || !!item.created_at && item.created_at >= task.created_at));
  const observations = items.filter(item => matchingBetaObservation(item, chosenSource?.source_ref ?? task.source_ref, execution));
  useEffect(() => {
    setSource(evidence.data?.items.find(item => item.kind === "saved_revision" && sameBetaRef(item.source_ref, task.source_ref))?.id ?? "");
    setExecution(task.execution_id ?? ""); setObservation(task.observation_id ?? "");
    setOpened(task.result_opened); setTime(task.time_s === null ? "" : String(task.time_s));
    setSensor(task.sensor_key ?? (task.guide_id === "lidar_height" ? "lidar" : task.guide_id === "radar_direction" ? "radar" : "front"));
  }, [task.id, task.revision, evidence.data]);
  async function save() {
    setBusy(true); setError(""); setNotice("");
    try {
      const currentProject = new URLSearchParams(location.hash.split("?")[1] ?? "").get("project");
      if (currentProject && currentProject !== task.project_id) throw new Error("프로젝트가 변경되었습니다. 현재 프로젝트의 과제를 다시 선택해 주세요.");
      const updated = await betaWrite<BetaTask>(`/beta/tasks/${encodeURIComponent(task.id)}/progress`, `progress:${task.id}`, {
        project_id: task.project_id, expected_revision: task.revision,
        ...(chosenSource?.source_ref ? { source_ref: chosenSource.source_ref } : {}),
        ...(execution ? { execution_id: execution } : {}),
        ...(observation ? { observation_id: observation } : {}),
        ...(execution ? { result_opened: opened } : {}),
        ...(time.trim() ? { time_s: Number(time) } : {}),
        ...(task.module === "sensor" && execution ? { sensor_key: sensor } : {}),
      });
      onSaved(updated); setNotice("저장된 근거를 연결했습니다. 진행 단계는 실제 기록을 기준으로 갱신됩니다.");
    } catch (reason) { setError(betaError(reason)); }
    finally { setBusy(false); }
  }
  const resume = taskLink(task);
  const chosenDuration = executions.find(item => item.execution_id === execution)?.duration_s ?? task.duration_s;
  const invalidTime = time.trim() !== "" && (!Number.isFinite(Number(time)) || Number(time) < 0 || chosenDuration != null && Number(time) > chosenDuration);
  return <div className="beta-progress" aria-label="베타 과제 진행 연결">
    <ol className="beta-workflow-steps">{task.steps.map((step, index) => <li className={step.complete ? "done" : ""} key={step.id}><span>{step.complete ? <Check size={13} /> : index + 1}</span><div>{step.label}{step.complete && <small>{step.basis === "participant_report" ? "참가자 확인" : "저장 기록 확인됨"}</small>}</div></li>)}</ol>
    {task.module === "log" ? <div className="beta-notice"><p>Log Sim의 안내 과제가 조건·실행·Case 검토를 자동으로 연결합니다. 기존 업무 화면에서 이어가세요.</p>{resume && <a className="button secondary" href={resume}>Log 과제 이어하기<ArrowUpRight size={14} /></a>}</div> : <>
      <p className="beta-hint">모듈에서 저장·실행한 기록을 선택하세요. 선택한 개정과 실행이 일치하는지 서버에서 확인합니다.</p>
      <div className="beta-form-grid">
        <label>저장한 시험 개정<select aria-label="베타 과제 저장 개정" value={source} disabled={!writable || busy || evidence.loading || !!task.source_ref} onChange={event => { setSource(event.target.value); setExecution(""); setObservation(""); setOpened(false); setTime(""); }}><option value="">{evidence.loading ? "개정 확인 중…" : "저장 개정 선택"}</option>{sources.map(item => <option value={item.id} key={item.id}>{item.label}</option>)}</select></label>
        <label>실행 기록<select aria-label="베타 과제 실행 기록" value={execution} disabled={!writable || busy || evidence.loading || !!task.execution_id} onChange={event => { setExecution(event.target.value); setObservation(""); setOpened(false); setTime(""); }}><option value="">실행 기록 선택</option>{task.execution_id && !executions.some(item => item.execution_id === task.execution_id) && <option value={task.execution_id}>{task.execution_id} · 현재 연결</option>}{executions.map(item => <option value={item.execution_id ?? item.id} key={item.id}>{item.label}{item.status ? ` · ${item.status}` : ""}</option>)}</select></label>
        <label>저장한 관찰<select aria-label="베타 과제 관찰 기록" value={observation} disabled={!writable || busy || !execution || !!task.observation_id} onChange={event => { const selected = observations.find(item => (item.observation_id ?? item.id) === event.target.value); setObservation(event.target.value); if (task.module === "sensor" && selected) { if (selected.sensor_key) setSensor(selected.sensor_key); if (selected.time_s != null) setTime(String(selected.time_s)); } }}><option value="">관찰 기록 선택</option>{task.observation_id && !observations.some(item => item.observation_id === task.observation_id) && <option value={task.observation_id}>{task.observation_id} · 현재 연결</option>}{observations.map(item => <option value={item.observation_id ?? item.id} key={item.id}>{item.label}</option>)}</select></label>
        <label>이어서 볼 시점 (초)<input aria-label="베타 과제 재생 시점" type="number" min="0" max={chosenDuration ?? undefined} step="0.1" value={time} disabled={!writable || busy || !execution || task.module === "sensor" && !!observation} onChange={event => setTime(event.target.value)} placeholder="선택 사항" /></label>{task.module === "sensor" && <label>조사 센서<select aria-label="베타 과제 조사 센서" value={sensor} disabled={!writable || busy || !execution || !!observation} onChange={event => setSensor(event.target.value as BetaSensorKey)}>{Object.entries(betaSensorNames).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>}
      </div>
      <label className="beta-checkbox"><input type="checkbox" checked={opened} disabled={!writable || busy || !execution || task.result_opened} onChange={event => setOpened(event.target.checked)} />선택한 실행의 결과를 열어 확인했습니다</label>
      {(task.source_ref || task.execution_id) && <p className="beta-hint">연결한 개정·실행·관찰은 이 과제에 고정됩니다. 다른 조건을 시험하려면 새 과제를 만드세요.</p>}
      <div className="beta-actions"><button className="button" onClick={() => void save()} disabled={!writable || busy || invalidTime || (!source && !execution)}><Link2 size={14} />{busy ? "연결 저장 중…" : "근거 연결·진행 저장"}</button><button className="button secondary" disabled={busy || evidence.loading} onClick={evidence.reload}><RefreshCw size={14} />저장 기록 새로고침</button></div>
      {!evidence.loading && !evidence.error && !items.length && <p className="beta-hint">아직 연결할 저장 기록이 없습니다. 과제 이어하기에서 조건을 저장한 뒤 돌아오세요.</p>}
      {evidence.error && <p role="alert" className="beta-error">저장 기록 조회: {evidence.error}</p>}
      {invalidTime && <p role="alert" className="beta-error">재생 시점은 원본 실행의 재생 범위 안에 있어야 합니다.</p>}
      {writable && task.source_ref && task.execution_id && !task.observation_id && (task.module !== "sensor" || task.execution_status === "completed") && ["completed", "failed", "cancelled", "interrupted"].includes(task.execution_status ?? "") && <details className="beta-observation-entry"><summary>새 관찰 기록 작성</summary><BetaObservationForm key={`${task.id}:${task.execution_id}`} task={task} onSaved={updated => { onSaved(updated); evidence.reload(); setNotice("관찰을 저장하고 과제에 연결했습니다."); }} /></details>}
    </>}
    {error && <p role="alert" className="beta-error">{error} <button className="beta-text-button" onClick={evidence.reload}>저장 기록 다시 확인</button></p>}
    {notice && <p role="status" className="beta-success">{notice}</p>}
  </div>;
}
