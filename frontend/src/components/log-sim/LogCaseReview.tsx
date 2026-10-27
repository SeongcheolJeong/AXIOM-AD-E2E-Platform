import { useEffect, useRef, useState } from "react";
import { api, useResource } from "../../api";
import "./log-beta.css";
import { useLogBeta, betaQuery } from "./LogGuidedBeta";
type Review = {
  revision: number;
  status: string;
  note: string;
  tags: string[];
  time_s: number;
  author: string;
};
export default function LogCaseReview({
  project,
  source,
  caseId,
  timeS,
  writable,
  onSeek,
}: {
  project: string;
  source: string;
  caseId: string;
  timeS: number;
  writable: boolean;
  onSeek: (time: number) => void;
}) {
  const beta = useLogBeta();
  const resource = useResource<{ items: Review[] }>(
    `/log-sim/reviews?${new URLSearchParams({ project_id: project, source_id: source, case_id: caseId })}`,
  );
  const bundles = useResource<{
    items: {
      batch_id: string;
      items: { case_id: string; candidate_id: string }[];
    }[];
  }>(`/log-sim/case-bundles?project_id=${encodeURIComponent(project)}`);
  const bundle = bundles.data?.items.find((b) =>
    b.items.some((i) => i.case_id === caseId),
  );
  const candidate = bundle?.items.find((i) => i.case_id === caseId);
  const [revision, setRevision] = useState(0),
    [status, setStatus] = useState("unreviewed"),
    [note, setNote] = useState(""),
    [tags, setTags] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState("");
  const pending = useRef({ body: "", key: "" });
  useEffect(() => {
    const r = resource.data?.items[0];
    if (r) {
      setRevision(r.revision);
      setStatus(r.status);
      setNote(r.note);
      setTags(r.tags.join(", "));
    }
  }, [resource.data]);
  async function save() {
    setBusy(true);
    setError("");
    const payload = JSON.stringify({
      project_id: project,
      source_id: source,
      case_id: caseId,
      expected_revision: revision,
      status,
      note,
      tags: tags
        .split(",")
        .map((x) => x.trim())
        .filter(Boolean),
      time_s: timeS,
    });
    if (pending.current.body !== payload)
      pending.current = { body: payload, key: crypto.randomUUID() };
    try {
      const r = await api<Review>("/log-sim/reviews", {
        method: "POST",
        body: payload,
        headers: { "Idempotency-Key": pending.current.key },
      });
      setRevision(r.revision);
      setMessage(`검토 기록 r${r.revision} 저장됨`);
      resource.reload();
      if (beta?.task?.case_id === caseId) await beta.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  const share = () => {
    const query = betaQuery({
      project,
      source,
      case: caseId,
      time_s: timeS.toFixed(6),
    });
    const url = `${location.origin}${location.pathname}#log-sim?${query}`;
    void navigator.clipboard
      .writeText(url)
      .then(() =>
        setMessage(
          "현재 원본·Case·시점 링크를 복사했습니다. 같은 프로젝트 권한이 필요합니다.",
        ),
      )
      .catch(() => setMessage(url));
  };
  return (
    <section className="log-beta-review" aria-label="Log Sim Case review">
      <h3>Case 검토 기록</h3>
      {bundle && candidate && (
        <a
          className="button secondary"
          href={`#log-sim?${betaQuery({ project, batch: bundle.batch_id, candidate: candidate.candidate_id })}`}
        >
          이 Case를 만든 A/B 결과 열기
        </a>
      )}
      <p>
        원본 시점 {timeS.toFixed(2)}s · r{revision} · 검토 상태는 안전 합격
        판정이 아닙니다.
      </p>
      <label>
        상태
        <select
          aria-label="Log Sim review status"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        >
          <option value="unreviewed">미검토</option>
          <option value="investigate">추가 조사</option>
          <option value="reviewed">검토 완료</option>
        </select>
      </label>
      <label>
        태그 · 쉼표로 구분
        <input
          aria-label="Log Sim review tags"
          value={tags}
          onChange={(e) => setTags(e.target.value)}
        />
      </label>
      <label>
        관찰·이유·다음 행동
        <textarea
          aria-label="Log Sim review note"
          rows={3}
          maxLength={4000}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
      <div className="log-beta-actions">
        <button
          className="button"
          disabled={!writable || busy || resource.loading}
          onClick={() => void save()}
        >
          검토 기록 저장
        </button>
        <button className="button secondary" onClick={share}>
          현재 시점 링크 복사
        </button>
        {resource.data?.items[0] && (
          <button
            className="button secondary"
            onClick={() => onSeek(resource.data!.items[0].time_s)}
          >
            저장한 조사 시점
          </button>
        )}
        <button className="button secondary" onClick={resource.reload}>
          저장 기록 새로고침
        </button>
      </div>
      {message && <p role="status">{message}</p>}
      {(error || resource.error) && (
        <p role="alert">{error || resource.error}</p>
      )}
    </section>
  );
}
