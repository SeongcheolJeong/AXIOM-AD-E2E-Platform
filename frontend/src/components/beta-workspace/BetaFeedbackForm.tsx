import { useEffect, useState } from "react";
import { MessageSquare, Send } from "lucide-react";
import { betaError, betaWrite, moduleNames } from "./beta-contract";
import type { BetaFeedback, BetaModule, BetaTask } from "./beta-contract";

export default function BetaFeedbackForm({ project, module, task, executionId, initialTime, onSaved, writable = true }: {
  project: string; module: BetaModule; task?: BetaTask | null; executionId?: string | null;
  initialTime?: number | null; onSaved?: (feedback: BetaFeedback) => void; writable?: boolean;
}) {
  const [kind, setKind] = useState<"help" | "issue" | "completion">("help");
  const [message, setMessage] = useState(""), [errorText, setErrorText] = useState("");
  const [time, setTime] = useState(initialTime === null || initialTime === undefined ? "" : String(initialTime));
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [saved, setSaved] = useState("");
  const attachedExecution = executionId ?? task?.execution_id;
  const mismatchedExecution = !!task && module !== "log" && !!executionId && task.execution_id !== executionId;
  const invalidTime = time !== "" && (!Number.isFinite(Number(time)) || Number(time) < 0);
  useEffect(() => { setTime(initialTime === null || initialTime === undefined ? "" : String(initialTime)); }, [executionId, task?.id]);
  async function send(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setSaved("");
    try {
      const [page, queryText] = location.hash.split("?");
      const query = new URLSearchParams(queryText ?? "");
      if (query.get("project") && query.get("project") !== project) throw new Error("프로젝트가 변경되었습니다. 현재 프로젝트에서 의견 창을 다시 열어 주세요.");
      const displayed = page === `#${module}-sim` && module !== "log"
        ? query.get("result") ?? query.get("execution") ?? query.get("right") ?? query.get("left") ?? query.get("run")
        : executionId ?? null;
      if (displayed !== (executionId ?? null)) throw new Error("보고 있는 실행이 변경되었습니다. 현재 실행의 의견 창을 다시 열어 주세요.");
      if (task && module !== "log" && displayed && task.execution_id !== displayed) throw new Error("현재 실행과 선택한 과제가 다릅니다. 일치하는 과제를 선택해 주세요.");
      const value = await betaWrite<BetaFeedback>("/beta/feedback", `feedback:${project}:${task?.id ?? module}`, {
        project_id: project, module, kind, message: message.trim(),
        ...(task ? { task_id: task.id, stage: task.stage } : {}),
        ...(executionId && module !== "log" ? { execution_id: executionId } : {}),
        ...(errorText.trim() ? { error: errorText.trim() } : {}),
        ...(time.trim() ? { time_s: Number(time) } : {}),
      });
      setSaved(`의견을 저장했습니다. 운영자가 재현 정보를 확인할 수 있습니다. · ${value.id}`);
      setMessage(""); setErrorText(""); onSaved?.(value);
    } catch (reason) { setError(betaError(reason)); }
    finally { setBusy(false); }
  }
  return <form className="beta-feedback-form" onSubmit={event => void send(event)} aria-label="ADP 베타 문제 신고">
    <div className="beta-subheading"><MessageSquare size={18} /><div><h3>도움 요청·문제 신고</h3><p>기대했던 동작과 실제로 막힌 부분을 적어 주세요.</p></div></div>
    <div className="beta-feedback-context"><strong>{moduleNames[module]}</strong><span>{task ? task.title : "과제 연결 없이 의견 남기기"}</span>{task?.source_ref && <small>저장 개정 {task.source_ref.id} · r{task.source_ref.revision}</small>}{attachedExecution && <small>실행 {attachedExecution}</small>}<small>현재 프로젝트와 계정, 연결된 기록의 식별 정보를 첨부합니다. 인증정보·원본 파일은 첨부하지 않습니다.</small></div>
    <div className="beta-form-grid"><label>의견 종류<select aria-label="베타 의견 종류" value={kind} disabled={busy || !writable} onChange={event => setKind(event.target.value as typeof kind)}><option value="help">도움이 필요해요</option><option value="issue">문제가 있어요</option><option value="completion" disabled={task?.status !== "completed"}>완료 의견</option></select></label><label>재생 시점 (초, 선택)<input aria-label="베타 신고 재생 시점" type="number" min="0" step="0.1" placeholder="시점 없이 신고 가능" value={time} disabled={busy || !writable || !attachedExecution} onChange={event => setTime(event.target.value)} /></label></div>
    <label>설명<textarea aria-label="베타 신고 내용" value={message} onChange={event => setMessage(event.target.value)} disabled={busy || !writable} maxLength={4000} required placeholder="예: 조건을 저장한 뒤 결과를 열었는데 어떤 개정의 결과인지 찾기 어려웠어요." /></label>
    <details><summary>화면에 표시된 오류 첨부 (선택)</summary><label>오류 메시지<textarea aria-label="베타 신고 오류" maxLength={1000} value={errorText} disabled={busy || !writable} onChange={event => setErrorText(event.target.value)} placeholder="토큰이나 개인정보를 제외한 오류 메시지" /></label></details>
    {mismatchedExecution && <p className="beta-error" role="alert">현재 보고 있는 실행이 선택한 과제에 연결되어 있지 않습니다. 위에서 일치하는 과제를 선택하거나 ‘저장 근거·진행 연결’로 이 실행을 연결하세요. 과제와 분리하려면 ‘과제 연결 없이 의견 남기기’를 선택하세요.</p>}
    <button className="button" type="submit" disabled={busy || !writable || !message.trim() || invalidTime || mismatchedExecution}><Send size={14} />{busy ? "의견 저장 중…" : "의견 보내기"}</button>
    {!writable && <p className="beta-hint">현재 계정은 조회 권한입니다. 운영자에게 작성 권한을 요청하세요.</p>}
    {invalidTime && <p className="beta-error" role="alert">재생 시점은 0 이상의 숫자여야 합니다.</p>}
    {error && <p className="beta-error" role="alert">{error}</p>}
    {saved && <p className="beta-success" role="status">{saved}</p>}
  </form>;
}
