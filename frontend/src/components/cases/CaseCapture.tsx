import { useRef, useState } from "react";
import { api } from "../../api";
import { Modal } from "../ui";
import { useSensorResource } from "../sensors/SensorData";
import { caseHref, type CaseDetail, type CasePage, type CaseSource } from "./CaseTypes";
import "../../cases.css";

/** A lost response retries the frozen request/key, including the expected revision. */
export default function CaseCapture({ source, label = "Create investigation case" }: { source: CaseSource; label?: string }) {
  const [open, setOpen] = useState(false), [title, setTitle] = useState(""), [note, setNote] = useState("");
  const [assignee, setAssignee] = useState(""), [existing, setExisting] = useState("");
  const [start, setStart] = useState(source.kind === "recording" ? source.start_time_ns : "");
  const [end, setEnd] = useState(source.kind === "recording" ? source.end_time_ns : "");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const request = useRef<{ signature: string; path: string; body: object; key: string } | null>(null);
  const params = new URLSearchParams(source.kind === "recording" ? { recording_id: source.recording_id } : { evaluation_id: source.evaluation_id, image_id: source.image_id });
  const related = useSensorResource<CasePage>(`/cases?${params}`);
  async function save() {
    setBusy(true); setError("");
    try {
      const selected = source.kind === "recording" ? { ...source, start_time_ns: start, end_time_ns: end } : source;
      const signature = JSON.stringify({ title, note, assignee, existing, selected });
      if (request.current?.signature !== signature) {
        let body: object = { title, note, assignee, source: selected }, path = "/cases";
        if (existing.trim() && source.kind === "evaluation") {
          const prior = await api<CaseDetail>(`/cases/${encodeURIComponent(existing.trim())}`);
          path = `/cases/${encodeURIComponent(prior.id)}/revisions`;
          body = { title: prior.title, note, assignee: prior.assignee, state: prior.state, disposition_note: prior.disposition_note,
            expected_revision: prior.revision, add_evaluation: source };
        }
        request.current = { signature, path, body, key: crypto.randomUUID() };
      }
      const pending = request.current!;
      const result = await api<CaseDetail>(pending.path, { method: "POST", headers: { "Idempotency-Key": pending.key }, body: JSON.stringify(pending.body) });
      setOpen(false); related.reload(); location.hash = caseHref(result.id, result.revision);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Case could not be saved"); }
    finally { setBusy(false); }
  }
  return <div className="case-capture">
    <button className="button secondary" onClick={() => { setStart(source.kind === "recording" ? source.start_time_ns : ""); setEnd(source.kind === "recording" ? source.end_time_ns : ""); setOpen(true); }}>{label}</button>
    <span className="muted">{source.kind === "recording" ? "Cases in this recording" : "Cases for this evaluation image"}: </span>
    {related.loading ? <span>Loading…</span> : related.data?.items.map((c) => <a key={c.id} href={caseHref(c.id)}>{c.title} · r{c.revision}</a>)}
    {related.error && <span role="alert">Case links unavailable. <button onClick={related.reload}>Retry case links</button></span>}
    {open && <Modal title="Save investigation case" subtitle="Pin original evidence. This does not change labels, model results or test verdicts." onClose={() => { if (!busy) setOpen(false); }}>
      <div className="modal-body case-form">
        {source.kind === "evaluation" && <label>Existing case ID (optional)<input aria-label="Existing case ID" value={existing} onChange={(e) => setExisting(e.target.value)} placeholder="Leave blank to create a new case" /></label>}
        {!existing.trim() && <><label>Case title<input aria-label="Case title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={180} /></label><label>Assignee (declared, not authenticated)<input aria-label="Case assignee" value={assignee} onChange={(e) => setAssignee(e.target.value)} maxLength={180} /></label></>}
        <label>Observation / investigation note<textarea aria-label="Case note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={4000} /></label>
        {source.kind === "recording" && <><p>Inclusive log-time range, one selected channel. Every duplicate timestamp is retained; maximum 2000 messages / 64 MiB.</p><label>Start log time (ns)<input aria-label="Case start time ns" value={start} onChange={(e) => setStart(e.target.value)} inputMode="numeric" /></label><label>End log time (ns)<input aria-label="Case end time ns" value={end} onChange={(e) => setEnd(e.target.value)} inputMode="numeric" /></label></>}
        <p>No simulation, automatic root-cause decision or approval is performed.</p>
        {error && <p role="alert" className="inline-error">{error}</p>}
      </div><footer><button className="button secondary" disabled={busy} onClick={() => setOpen(false)}>Cancel</button><button className="button primary" disabled={busy || !note.trim() || (!existing.trim() && !title.trim())} onClick={save}>{busy ? "Saving…" : error ? "Retry case save" : existing.trim() ? "Link evaluation to case" : "Save case"}</button></footer>
    </Modal>}
  </div>;
}
