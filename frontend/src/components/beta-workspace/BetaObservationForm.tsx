import { useState } from "react";
import { NotebookPen } from "lucide-react";
import { api } from "../../api";
import { betaError, betaObservationRequest, betaQuery, betaSensorNames, betaWrite } from "./beta-contract";
import type { BetaSensorKey, BetaTask } from "./beta-contract";

type PendingObservation = { id: string; execution_id: string; time_s: number; sensor_key?: BetaSensorKey };

/** Reuse the module's immutable observation API, then link the saved record. */
export default function BetaObservationForm({ task, onSaved }: { task: BetaTask; onSaved: (task: BetaTask) => void }) {
  const storage = `axiom.beta.observation:${task.project_id}:${task.id}`;
  const [pending, setPending] = useState<PendingObservation | null>(() => {
    try {
      const item = JSON.parse(sessionStorage.getItem(storage) ?? "null");
      return item?.execution_id === task.execution_id && typeof item?.id === "string" && Number.isFinite(item.time_s) ? item : null;
    } catch { return null; }
  });
  const [title, setTitle] = useState(""), [note, setNote] = useState("");
  const [time, setTime] = useState(String(task.time_s ?? 0)), [side, setSide] = useState("both");
  const [sensor, setSensor] = useState<BetaSensorKey>(task.sensor_key ?? (task.guide_id === "lidar_height" ? "lidar" : task.guide_id === "radar_direction" ? "radar" : "front"));
  const [kind, setKind] = useState("observation"), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const invalidTime = !time.trim() || !Number.isFinite(Number(time)) || Number(time) < 0 || task.duration_s != null && Number(time) > task.duration_s;
  async function save(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    let retained = pending;
    try {
      const currentProject = new URLSearchParams(location.hash.split("?")[1] ?? "").get("project");
      if (currentProject && currentProject !== task.project_id) throw new Error("프로젝트가 변경되었습니다. 현재 프로젝트의 과제를 다시 선택해 주세요.");
      if (!retained) {
        const request = betaObservationRequest(task, { title, note, kind, time_s: Number(time), side, sensor_key: sensor });
        const saved = await betaWrite<{ id: string }>(request.path, `observation:${task.id}`, request.body);
        retained = { id: saved.id, execution_id: task.execution_id!, time_s: Number(time), ...(task.module === "sensor" ? { sensor_key: sensor } : {}) };
        setPending(retained);
        try { sessionStorage.setItem(storage, JSON.stringify(retained)); } catch { /* the saved record also remains in module evidence */ }
      }
      const current = await api<BetaTask>(`/beta/tasks/${encodeURIComponent(task.id)}?${betaQuery(task.project_id)}`);
      if (current.execution_id !== retained.execution_id) throw new Error("과제의 연결 실행이 바뀌었습니다. 저장된 관찰을 확인해 주세요.");
      if (current.observation_id && current.observation_id !== retained.id) throw new Error("과제에 다른 관찰이 이미 연결되어 있습니다. 새 관찰은 모듈의 저장 기록에 보존했습니다.");
      const updated = current.observation_id === retained.id ? current : await betaWrite<BetaTask>(`/beta/tasks/${encodeURIComponent(task.id)}/progress`, `progress:${task.id}`, {
        project_id: task.project_id, expected_revision: current.revision,
        observation_id: retained.id, time_s: retained.time_s,
        ...(retained.sensor_key ? { sensor_key: retained.sensor_key } : {}),
      });
      try { sessionStorage.removeItem(storage); } catch { /* recovery cache only */ }
      setPending(null); onSaved(updated);
    } catch (reason) { setError(`${retained ? "관찰은 저장되었습니다. 과제 연결을 다시 확인해 주세요. " : ""}${betaError(reason)}`); }
    finally { setBusy(false); }
  }
  return <form className="beta-observation-form" aria-label="베타 과제 새 관찰" onSubmit={event => void save(event)}><div className="beta-subheading"><NotebookPen size={17} /><div><h3>이 결과에 관찰 남기기</h3><p>확인한 내용을 선택한 실행과 저장 개정에 연결합니다.</p></div></div>{pending ? <p className="beta-hint">관찰 {pending.id} 저장됨 · 과제 연결 대기</p> : <><div className="beta-form-grid"><label>관찰 제목<input aria-label="베타 관찰 제목" value={title} disabled={busy} maxLength={180} required onChange={event => setTitle(event.target.value)} placeholder="예: 감속 구간의 차량 간격" /></label><label>관찰 시점 (초)<input aria-label="베타 관찰 시점" type="number" min="0" max={task.duration_s ?? undefined} step="0.1" value={time} disabled={busy} required onChange={event => setTime(event.target.value)} /></label><label>기록 종류<select aria-label="베타 관찰 종류" value={kind} disabled={busy} onChange={event => setKind(event.target.value)}><option value="observation">관찰</option><option value="issue">문제 발견</option></select></label>{task.module === "sensor" && <><label>조사 센서<select aria-label="베타 Sensor 관찰 센서" value={sensor} disabled={busy} onChange={event => setSensor(event.target.value as BetaSensorKey)}>{Object.entries(betaSensorNames).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label>비교 대상<select aria-label="베타 Sensor 관찰 대상" value={side} disabled={busy} onChange={event => setSide(event.target.value)}><option value="both">배치 A·B 둘 다</option><option value="rig_a">배치 A</option><option value="rig_b">배치 B</option></select></label></>}{task.module === "hil" && <label>비교 대상<select aria-label="베타 HIL 관찰 대상" value={side} disabled={busy} onChange={event => setSide(event.target.value)}><option value="both">기준·고장 둘 다</option><option value="baseline">기준</option><option value="faulted">고장</option></select></label>}</div><label>관찰 내용<textarea aria-label="베타 관찰 내용" value={note} disabled={busy} maxLength={4000} required onChange={event => setNote(event.target.value)} placeholder="영상·신호에서 확인한 사실과 더 확인할 점을 적어 주세요." /></label><p className="beta-hint">사용자 관찰로 저장합니다. 자동 통과 판정이나 실차 검증 결과로 표시되지 않습니다.</p></>}<button type="submit" className="button secondary" disabled={busy || !pending && (!title.trim() || !note.trim() || invalidTime)}>{busy ? "관찰 저장·연결 중…" : pending ? "저장된 관찰 과제 연결 재시도" : "관찰 저장·과제 연결"}</button>{error && <p className="beta-error" role="alert">{error}</p>}</form>;
}
