import { useRef, useState } from "react";
import { api } from "../api";
import { PageHeading, ErrorState, Loading } from "../components/ui";
import { DownloadLink } from "../components/ArtifactAccess";
import { useSensorResource } from "../components/sensors/SensorData";
import { caseHref, type CaseDetail, type CasePage } from "../components/cases/CaseTypes";
import type { RecordingSample } from "../components/recordings/RecordingTypes";
import { CaseConditionCoverage, CaseConditionEditor, CaseConditionFilters, diagnosticLabels, emptyFilters,
  type ConditionFilters, type ConditionTaxonomy } from "../components/cases/CaseConditions";
import "../cases.css";
import { CaseRetests, CaseRetestActivity } from "../components/cases/CaseRetests";
import CaseModelReplays from "../components/cases/CaseModelReplays";
import CaseStackReplays from "../components/cases/CaseStackReplays";
import CaseNeuralEvidence from "../components/cases/CaseNeuralEvidence";

export default function Cases() {
  const params = new URLSearchParams(location.hash.split("?")[1] ?? ""), id = params.get("case") ?? "";
  const revision = params.get("revision");
  const [state, setState] = useState(""), [offset, setOffset] = useState(0);
  const [tab, setTab] = useState(params.get("view") === "coverage" ? "coverage" : params.get("view") === "retests" ? "retests" : "cases");
  const [filters, setFilters] = useState<ConditionFilters>({ ...emptyFilters(), recording: params.get("recording") ?? "" });
  const taxonomy = useSensorResource<ConditionTaxonomy>("/case-condition-taxonomy");
  const query = new URLSearchParams({ limit: "50", offset: String(offset), review: filters.review,
    ...(state ? { state } : {}), ...(filters.q ? { q: filters.q } : {}),
    ...(filters.recording.trim() ? { recording_id: filters.recording.trim() } : {}),
    ...(filters.diagnostic ? { diagnostic: filters.diagnostic } : {}) });
  Object.entries(filters.values).forEach(([k, v]) => query.append("condition", `${k}:${v}`));
  const library = useSensorResource<CasePage>(`/cases?${query}`, 180);
  const detail = useSensorResource<CaseDetail>(id ? `/cases/${encodeURIComponent(id)}${revision ? `?revision=${encodeURIComponent(revision)}` : ""}` : null);
  return <div className="cases-page">
    <PageHeading eyebrow="ORIGINAL SEGMENT ↔ INVESTIGATION ↔ EVALUATION" title="Investigation cases" description="Keep one traceable case across log investigation and model evaluation. Case status is not a validation verdict." />
    <div className="case-toolbar" role="tablist" aria-label="Investigation workspace views">
      <button role="tab" aria-selected={tab === "cases"} onClick={() => setTab("cases")}>Cases</button>
      <button role="tab" aria-selected={tab === "coverage"} onClick={() => setTab("coverage")}>Condition coverage</button>
      <button role="tab" aria-selected={tab === "retests"} onClick={() => setTab("retests")}>Retest activity</button>
    </div>
    {taxonomy.error && <ErrorState message={taxonomy.error} retry={taxonomy.reload} />}
    {taxonomy.data && <CaseConditionFilters taxonomy={taxonomy.data} value={filters} onChange={(next) => { setFilters(next); setOffset(0); }} />}
    {tab === "coverage" ? <CaseConditionCoverage recording={filters.recording} onSelectCell={(values) => {
      setFilters({ ...emptyFilters(), recording: filters.recording, values, review: "reviewed" }); setState(""); setOffset(0); setTab("cases");
    }} /> : tab === "retests" ? <CaseRetestActivity recording={filters.recording} /> : <>
    <div className="case-toolbar"><label>Workflow state<select aria-label="Filter case state" value={state} onChange={(e) => { setState(e.target.value); setOffset(0); }}>{["", "open", "investigating", "resolved", "dismissed"].map((v) => <option key={v} value={v}>{v || "All states"}</option>)}</select></label><button className="button secondary" onClick={() => { library.reload(); detail.reload(); }}>Refresh cases</button></div>
    <section className="panel case-library">
      <h2>Cases</h2>{library.loading && <Loading />}{library.error && <ErrorState message={library.error} retry={library.reload} />}
      {library.data?.items.length === 0 && <p>No cases in this selection. Create one in Data catalog → Recordings or a detection evaluation image.</p>}
      {library.data?.items.map((c) => <a className="case-card" key={c.id} href={caseHref(c.id, undefined, params.get("project") ?? undefined)} aria-current={id === c.id ? "page" : undefined}><strong>{c.title}</strong><span>{c.state} · r{c.revision} · {c.assignee || "Unassigned"}</span><span className="condition-chips">{c.conditions?.review_status ?? "unclassified"} · {Object.values(c.conditions?.values ?? {}).join(" / ")}{c.diagnostics?.tags.map((t) => <span key={t}>{diagnosticLabels[t] ?? t}</span>)}</span></a>)}
      <div className="case-toolbar"><button disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 50))}>Newer cases</button><button disabled={library.data?.next_offset == null} onClick={() => setOffset(library.data!.next_offset!)}>Older cases</button></div>
    </section>
    {detail.loading && <Loading label="Loading exact case revision…" />}{detail.error && <ErrorState message={detail.error} retry={detail.reload} />}
    {detail.data && <CaseView key={`${detail.data.id}/${detail.data.revision}`} value={detail.data} />}
    </>}
  </div>;
}

function CaseView({ value }: { value: CaseDetail }) {
  const [sampleOffset, setSampleOffset] = useState(0), [editing, setEditing] = useState(false);
  const samples = useSensorResource<{ total: number; samples: RecordingSample[]; next_offset: number | null }>(`/cases/${encodeURIComponent(value.id)}/samples?offset=${sampleOffset}&limit=25`);
  const s = value.segment;
  const recordingLink = (ordinal: number, time: string) => `#catalog?${new URLSearchParams({ recording: s.recording_id, channel: String(s.channel_id), at: time, ordinal: String(ordinal) })}`;
  return <div data-testid="case-detail">
    <section className="panel case-section"><h2>{value.title}</h2><p><code data-testid="case-id">{value.id}</code> · <strong data-testid="case-revision">r{value.revision}</strong> · {value.state}</p><p className="case-note">{value.note}</p><p>Assignee: {value.assignee || "Unassigned"} (declared label, not an authenticated user)</p>{value.disposition_note && <p>Disposition: {value.disposition_note}</p>}
      <div className="case-toolbar"><button className="button secondary" onClick={() => setEditing(!editing)}>Edit investigation</button><DownloadLink className="button secondary" href={value.download_url} download>Download case revision</DownloadLink><a href={caseHref(value.id, undefined, value.neural_sensitivity?.project_id)}>Latest revision</a>{value.revision > 1 && <a href={caseHref(value.id, value.revision - 1, value.neural_sensitivity?.project_id)}>Previous revision</a>}</div>
      <p><a href={`#collections?${new URLSearchParams({source_case:value.id,source_revision:String(value.revision)})}`}>Start collection from this case</a></p>
      <p>Source selection and previous revisions are immutable. Resolving a case does not turn any evaluation into a pass.</p>{editing && <CaseEditor value={value} />}
    </section>
    <CaseNeuralEvidence value={value} />
    <CaseConditionEditor value={value} />
    <CaseModelReplays value={value} />
    <CaseStackReplays value={value} />
    <CaseRetests value={value} />
    <section className="panel case-section"><h2>Pinned original segment</h2><p>{s.recording_name} · channel {s.channel_id} · {s.sample_count} original messages</p><p>Clock: {s.clock_domain}; capture-clock alignment is not validated.</p><code>{s.start_time_ns} → {s.end_time_ns} ns</code><p><a href={recordingLink(s.samples[0].ordinal, s.samples[0].log_time_ns)}>Open original recording at anchor</a></p>
      {samples.loading && <Loading />}{samples.error && <ErrorState message={samples.error} retry={samples.reload} />}
      {samples.data && <><div className="case-table-scroll"><table data-testid="case-source-samples"><thead><tr><th>Ordinal</th><th>Original log time ns</th><th>SHA-256</th><th>Source</th></tr></thead><tbody>{samples.data.samples.map((m) => <tr key={m.ordinal}><td>{m.ordinal}</td><td>{m.log_time_ns}</td><td><code>{m.sha256}</code></td><td><a href={recordingLink(m.ordinal, m.log_time_ns)}>Inspect original message {m.ordinal}</a></td></tr>)}</tbody></table></div><p>Showing {samples.data.samples.length} of {samples.data.total} pinned messages. Payload integrity checked on this read.</p><div className="case-toolbar"><button disabled={!sampleOffset} onClick={() => setSampleOffset(Math.max(0, sampleOffset-25))}>Previous samples</button><button disabled={samples.data.next_offset == null} onClick={() => setSampleOffset(samples.data!.next_offset!)}>Next samples</button></div></>}
    </section>
    <section className="panel case-section"><h2>Linked evaluation evidence</h2>{!value.evaluations.length && <p>No evaluation linked. Open the source image in a detection evaluation, choose Create investigation case, and enter this case ID.</p>}
      <div className="case-table-scroll"><table data-testid="case-evaluations"><thead><tr><th>Evaluation</th><th>Image</th><th>State</th><th>TP / FP / FN</th><th>Inspect</th></tr></thead><tbody>{value.evaluations.map((e) => <tr key={`${e.evaluation_id}/${e.image_id}`}><td>{e.name} · dataset r{e.dataset_revision}</td><td><code>{e.image_id}</code></td><td>{e.row_status}</td><td>{e.diagnostics ? `${e.diagnostics.tp} / ${e.diagnostics.fp} / ${e.diagnostics.fn}` : "Not evaluated"}</td><td><a href={`#datasets?${new URLSearchParams({ evaluation: e.evaluation_id, image: e.image_id })}`}>Open linked evaluation</a></td></tr>)}</tbody></table></div>
      <details><summary>Exact source and revision identities</summary><pre>{JSON.stringify({ segment_hash: value.segment_hash, recording_sha256: s.recording_sha256, manifest_hash: value.manifest_hash, previous_manifest_hash: value.previous_manifest_hash, evaluations: value.evaluations }, null, 2)}</pre></details>
    </section>
  </div>;
}

function CaseEditor({ value }: { value: CaseDetail }) {
  const [title, setTitle] = useState(value.title), [note, setNote] = useState(value.note), [assignee, setAssignee] = useState(value.assignee);
  const [state, setState] = useState(value.state), [disposition, setDisposition] = useState(value.disposition_note);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const pending = useRef({ body: "", key: "" });
  async function save() {
    setBusy(true); setError("");
    try {
      const body = JSON.stringify({ expected_revision: value.revision, title, note, assignee, state, disposition_note: disposition });
      if (pending.current.body !== body) pending.current = { body, key: crypto.randomUUID() };
      const result = await api<CaseDetail>(`/cases/${encodeURIComponent(value.id)}/revisions`, { method: "POST", headers: { "Idempotency-Key": pending.current.key }, body });
      location.hash = caseHref(result.id, result.revision, result.neural_sensitivity?.project_id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Revision failed"); }
    finally { setBusy(false); }
  }
  return <div className="case-form"><label>Case title<input aria-label="Edit case title" value={title} onChange={(e) => setTitle(e.target.value)} /></label><label>Investigation note<textarea aria-label="Edit case note" value={note} onChange={(e) => setNote(e.target.value)} /></label><label>Assignee (declared)<input aria-label="Edit case assignee" value={assignee} onChange={(e) => setAssignee(e.target.value)} /></label><label>Workflow state<select aria-label="Edit case state" value={state} onChange={(e) => setState(e.target.value)}>{["open", "investigating", "resolved", "dismissed"].map((s) => <option key={s} value={s}>{s}</option>)}</select></label><label>Disposition note (required for resolved/dismissed)<textarea aria-label="Case disposition note" value={disposition} onChange={(e) => setDisposition(e.target.value)} /></label>{error && <p role="alert">{error}</p>}<button className="button primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Publish case revision"}</button></div>;
}
