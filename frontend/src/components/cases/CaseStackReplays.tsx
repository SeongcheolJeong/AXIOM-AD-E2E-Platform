import { useRef, useState } from "react";
import { api } from "../../api";
import { DownloadLink } from "../ArtifactAccess";
import { ErrorState, Loading } from "../ui";
import { useSensorResource } from "../sensors/SensorData";
import { useJobResource } from "../jobs/useJobResource";
import { caseHref, type CaseDetail } from "./CaseTypes";

interface Preview {
  preview_hash: string;
  prepared: { stack_snapshot: { name: string; revision: number; manifest_hash: string }; binding_snapshot: { revision: number; manifest_hash: string } };
  workload: { total_ticks: number; case_tick_indices: number[]; unique_source_messages: number; source_bytes: number; state_policy: string };
}
interface Side { status: string; mode: string | null; control: Record<string, number> | null; unavailable_reasons: string[]; error: string | null }
interface Comparison {
  status: string; reasons: string[]; total_ticks: number; changed_request_ticks: number | null;
  rows: { index: number; at_ns: string; baseline: Side; candidate: Side; control_delta: Record<string, number> | null; request_changed: boolean | null }[];
}
interface Plan {
  id: string; definition_hash: string; job_id: string; status: string; error: string | null;
  manifest_hash: string | null; capture: { comparison_status: string } | null;
  definition: Preview & { baseline_id: string; note: string };
  counts: Record<string, number>; comparison: Comparison;
}
interface Report { case_revision: number; plans: Plan[]; download_url: string }
const terminal = (s: string) => ["completed", "cancelled", "failed", "interrupted"].includes(s);
const controls = (c: Record<string, number> | null) => c ? ["throttle", "brake", "steer"].map(k => `${k} ${Number(c[k]).toFixed(4)}`).join(" · ") : "No output";

export default function CaseStackReplays({ value }: { value: CaseDetail }) {
  const [open, setOpen] = useState(false);
  const report = useJobResource<Report>(`/cases/${encodeURIComponent(value.id)}/stack-replays?revision=${value.revision}`);
  return <section className="panel case-section" aria-label="Recorded temporal stack replay">
    <h2>Recorded temporal stack replay</h2>
    <p>Compare saved stack revisions on the same ego/LiDAR sequence, including the full recorded prefix. This case must reference an exact ego or LiDAR input of a completed baseline replay.</p>
    <p><strong>Open loop:</strong> throttle, brake and steering are requests only. They never change the vehicle or the next recorded observation. Clock offsets and rig calibration remain source declarations.</p>
    <button className="button secondary" onClick={() => setOpen(!open)}>Configure temporal stack replay</button>
    {open && <ReplayForm value={value} />}
    {report.loading && <Loading label="Reading recorded stack attempts…" />}
    {report.error && <ErrorState message={report.error} retry={report.reload} />}
    {report.data && <div data-testid="case-stack-report">
      {report.error && <p role="alert">Previous observation only; refresh must succeed before actions.</p>}
      {!report.data.plans.length && <p>No recorded stack replay submitted for this case revision.</p>}
      {report.data.plans.map(plan => <Result key={plan.id} value={value} plan={plan} stale={!!report.error} reload={report.reload} />)}
      <DownloadLink href={report.data.download_url} download className="button secondary">Download case stack comparison JSON</DownloadLink>
    </div>}
  </section>;
}

function ReplayForm({ value }: { value: CaseDetail }) {
  const [baseline, setBaseline] = useState(""), [stack, setStack] = useState(""), [revision, setRevision] = useState(1);
  const [note, setNote] = useState(""), [consent, setConsent] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [preview, setPreview] = useState<{ signature: string; value: Preview } | null>(null);
  const pending = useRef({ signature: "", key: "" });
  const replays = useSensorResource<{ items: { id: string; label: string; recording_id: string; stack_revision: number }[] }>("/stack-replays?status=completed&limit=100");
  const stacks = useSensorResource<{ id: string; name: string; revision: number }[]>("/stacks");
  const body = { expected_revision: value.revision, baseline_replay_id: baseline.trim(), stack_id: stack.trim(), stack_revision: revision, note };
  const signature = JSON.stringify(body), current = preview?.signature === signature ? preview.value : null;
  async function inspect() {
    setBusy(true); setError(""); setPreview(null); setConsent(false);
    try { setPreview({ signature, value: await api<Preview>(`/cases/${value.id}/stack-replays/preview`, { method: "POST", body: signature }) }); }
    catch (e) { setError(e instanceof Error ? e.message : "Stack preview failed"); }
    finally { setBusy(false); }
  }
  async function start() {
    if (!current || !consent) return;
    setBusy(true); setError("");
    const payload = JSON.stringify({ ...body, expected_preview_hash: current.preview_hash, confirm_open_loop: true });
    if (pending.current.signature !== payload) pending.current = { signature: payload, key: crypto.randomUUID() };
    try {
      const result = await api<CaseDetail>(`/cases/${value.id}/stack-replays`, { method: "POST", body: payload, headers: { "Idempotency-Key": pending.current.key } });
      location.hash = caseHref(result.id, result.revision);
    } catch (e) { setError(e instanceof Error ? e.message : "Queue submission failed"); }
    finally { setBusy(false); }
  }
  return <div className="case-form">
    {replays.error && <ErrorState message={replays.error} retry={replays.reload} />}
    <label>Completed baseline replay<input aria-label="Temporal baseline replay ID" list="case-stack-baselines" value={baseline} disabled={busy} onChange={e => { setBaseline(e.target.value); setConsent(false); }} /></label>
    <datalist id="case-stack-baselines">{replays.data?.items.filter(r => r.recording_id === value.segment.recording_id).map(r => <option key={r.id} value={r.id}>{r.label} · r{r.stack_revision}</option>)}</datalist>
    <small>Suggestions cover the newest 100 completed replays. An exact older replay ID can be entered; source identity is checked by the API.</small>
    {stacks.error && <ErrorState message={stacks.error} retry={stacks.reload} />}
    <label>Candidate stack<input aria-label="Temporal candidate stack ID" list="case-stack-definitions" value={stack} disabled={busy} onChange={e => {
      setStack(e.target.value); setConsent(false); const item = stacks.data?.find(s => s.id === e.target.value); if (item) setRevision(item.revision);
    }} /></label>
    <datalist id="case-stack-definitions">{stacks.data?.map(s => <option key={s.id} value={s.id}>{s.name} · r{s.revision}</option>)}</datalist>
    <label>Exact candidate revision<input aria-label="Temporal candidate revision" type="number" min={1} step={1} value={revision} disabled={busy} onChange={e => { setRevision(Number(e.target.value)); setConsent(false); }} /></label>
    <label>Comparison purpose<textarea aria-label="Temporal replay purpose" maxLength={1500} value={note} disabled={busy} onChange={e => { setNote(e.target.value); setConsent(false); }} /></label>
    <button className="button secondary" disabled={busy || !baseline.trim() || !stack.trim() || !note.trim()} onClick={inspect}>Preview full temporal sequence</button>
    {current && <div data-testid="case-stack-preview">
      <p><strong>{current.workload.total_ticks} recorded ticks</strong>, {current.workload.case_tick_indices.length} touching this case · {current.workload.unique_source_messages} unique source messages · {current.workload.source_bytes} bytes.</p>
      <p>{current.workload.state_policy}</p>
      <p>{current.prepared.stack_snapshot.name} · r{current.prepared.stack_snapshot.revision} · binding r{current.prepared.binding_snapshot.revision}</p>
      <code>{current.preview_hash}</code>
      <label className="condition-check"><input type="checkbox" aria-label="Confirm recorded stack execution" checked={consent} disabled={busy} onChange={e => setConsent(e.target.checked)} />Run the entire original sequence. I understand requests are not applied to a vehicle.</label>
      <button className="button primary" disabled={busy || !consent} onClick={start}>Submit recorded stack replay</button>
    </div>}
    {error && <p role="alert">{error}</p>}
  </div>;
}

function Result({ value, plan, stale, reload }: { value: CaseDetail; plan: Plan; stale: boolean; reload: () => void }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const pending = useRef({ body: "", key: "" });
  async function act(cancel: boolean) {
    setBusy(true); setError("");
    try {
      if (cancel) { await api(`/stack-replays/${plan.job_id}/cancel`, { method: "POST" }); reload(); }
      else {
        const body = JSON.stringify({ expected_revision: value.revision, plan_hash: plan.definition_hash });
        if (pending.current.body !== body) pending.current = { body, key: crypto.randomUUID() };
        const result = await api<CaseDetail>(`/cases/${value.id}/stack-replays/${plan.id}/capture`, { method: "POST", body, headers: { "Idempotency-Key": pending.current.key } });
        location.hash = caseHref(result.id, result.revision);
      }
    } catch (e) { setError(e instanceof Error ? e.message : "Stack action failed"); }
    finally { setBusy(false); }
  }
  const c = plan.comparison;
  return <article className="case-section" data-testid="case-stack-attempt">
    <h3>{plan.definition.prepared.stack_snapshot.name} · r{plan.definition.prepared.stack_snapshot.revision}</h3>
    <p>Execution <strong data-testid="case-stack-status">{plan.status}</strong> · {Object.entries(plan.counts).map(([k,v]) => `${k}: ${v}`).join(" · ")}</p>
    <p>{plan.definition.note}</p>{plan.error && <p role="alert">{plan.error}</p>}
    <div className="case-toolbar"><a href={`#stacks?replay=${plan.definition.baseline_id}`}>Inspect baseline stack trace</a><a href={`#stacks?replay=${plan.job_id}`}>Inspect candidate stack trace</a>
      {!terminal(plan.status) && <button disabled={busy || stale || plan.status === "cancel_requested"} onClick={() => act(true)}>Request stack replay cancellation</button>}
      {terminal(plan.status) && !plan.capture && <button disabled={busy || stale} onClick={() => act(false)}>Capture stack replay outcome</button>}
      {plan.manifest_hash && <DownloadLink href={`/api/v1/stack-replays/${plan.job_id}/export`} download>Download stack execution evidence</DownloadLink>}
    </div>
    {plan.capture && <p data-testid="case-stack-captured">Captured. Original evidence and case status are unchanged.</p>}
    <h4 data-testid="case-stack-comparison-status">{c.status}</h4>
    <p data-testid="case-stack-denominator">All {c.total_ticks} baseline ticks · changed request ticks: {c.changed_request_ticks ?? "not evaluated"}. Different is not better; no driving score is assigned.</p>
    {c.reasons.map(r => <p key={r}>{r}</p>)}
    <div className="case-table-scroll"><table data-testid="case-stack-comparison"><thead><tr><th>Tick / exact time ns</th><th>Baseline mode / request</th><th>Candidate mode / request</th><th>Delta: candidate − baseline</th><th>Unavailable inputs / errors</th></tr></thead>
      <tbody>{c.rows.map(r => <tr key={r.index}><th>{r.index}<br /><code>{r.at_ns}</code></th><td>{r.baseline.status} · {r.baseline.mode ?? "—"}<br />{controls(r.baseline.control)}</td><td>{r.candidate.status} · {r.candidate.mode ?? "—"}<br />{controls(r.candidate.control)}</td><td>{r.control_delta ? controls(r.control_delta) : "Withheld"}</td><td>{[...r.candidate.unavailable_reasons, r.candidate.error].filter(Boolean).join(" · ") || "None recorded"}</td></tr>)}</tbody>
    </table></div>
    {error && <p role="alert">{error}</p>}
  </article>;
}
