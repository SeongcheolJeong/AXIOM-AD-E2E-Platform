import { useRef, useState } from "react";
import { api } from "../../api";
import { DownloadLink } from "../ArtifactAccess";
import { ErrorState, Loading } from "../ui";
import { useSensorResource } from "../sensors/SensorData";
import { caseHref, type CaseDetail, type CaseSummary } from "./CaseTypes";

export interface ConditionTaxonomy {
  id: string; revision: number; manifest_hash: string;
  axes: { key: string; name: string; definition: string; values: { id: string; name: string }[] }[];
  grid_policy: string; unknown_policy: string;
}
export interface ConditionAnnotation {
  taxonomy: ConditionTaxonomy; values: Record<string, string>; review_status: "draft" | "reviewed";
  author: string; note: string; declared_at: string; source: "manual";
}
export interface ConditionFilters {
  q: string; values: Record<string, string>; review: string; diagnostic: string; recording: string;
}
export const emptyFilters = (): ConditionFilters => ({ q: "", values: {}, review: "all", diagnostic: "", recording: "" });
export const diagnosticLabels: Record<string, string> = {
  has_false_negative: "Linked false negatives", has_false_positive: "Linked false positives",
  incomplete_evidence: "Incomplete linked evidence", no_linked_evaluation: "No linked evaluation",
};

export function CaseConditionFilters({ taxonomy, value, onChange }: {
  taxonomy: ConditionTaxonomy; value: ConditionFilters; onChange: (next: ConditionFilters) => void;
}) {
  return <div className="condition-controls" aria-label="Case condition search">
    <label>Find in title, note or assignee<input aria-label="Search investigation text" value={value.q} maxLength={300} onChange={(e) => onChange({ ...value, q: e.target.value })} /></label>
    <label>Recording scope (optional ID)<input aria-label="Condition recording scope" value={value.recording} maxLength={100} onChange={(e) => onChange({ ...value, recording: e.target.value })} placeholder="All recordings" /></label>
    {taxonomy.axes.map((axis) => <label key={axis.key}>{axis.name}<select aria-label={`Filter condition ${axis.key}`} value={value.values[axis.key] ?? ""} onChange={(e) => {
      const next = { ...value.values }; if (e.target.value) next[axis.key] = e.target.value; else delete next[axis.key];
      onChange({ ...value, values: next });
    }}><option value="">All values</option>{axis.values.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}</select></label>)}
    <label>Condition review<select aria-label="Filter condition review" value={value.review} onChange={(e) => onChange({ ...value, review: e.target.value })}>
      <option value="all">All review states</option><option value="reviewed">Reviewed declarations</option><option value="draft">Draft declarations</option><option value="unclassified">No declared values</option>
    </select></label>
    <label>Evidence-derived tag<select aria-label="Filter diagnostic tag" value={value.diagnostic} onChange={(e) => onChange({ ...value, diagnostic: e.target.value })}>
      <option value="">All diagnostics</option>{Object.entries(diagnosticLabels).map(([k, name]) => <option key={k} value={k}>{name}</option>)}
    </select></label>
    <button className="button secondary" onClick={() => onChange(emptyFilters())}>Clear condition filters</button>
  </div>;
}

export function CaseConditionEditor({ value }: { value: CaseDetail }) {
  const catalog = useSensorResource<ConditionTaxonomy>("/case-condition-taxonomy");
  const prior = value.conditions;
  const [values, setValues] = useState<Record<string, string>>({ ...prior?.values });
  const [author, setAuthor] = useState(prior?.author ?? ""), [note, setNote] = useState(prior?.note ?? "");
  const [reviewed, setReviewed] = useState(false), [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const pending = useRef({ body: "", key: "" });
  async function save() {
    if (!catalog.data) return;
    setBusy(true); setError("");
    const t = catalog.data;
    const body = JSON.stringify({ expected_revision: value.revision, taxonomy_id: t.id, taxonomy_revision: t.revision,
      taxonomy_hash: t.manifest_hash, values, author, note, review_status: reviewed ? "reviewed" : "draft" });
    if (pending.current.body !== body) pending.current = { body, key: crypto.randomUUID() };
    try {
      const result = await api<CaseDetail>(`/cases/${encodeURIComponent(value.id)}/conditions`, {
        method: "POST", headers: { "Idempotency-Key": pending.current.key }, body,
      });
      location.hash = caseHref(result.id, result.revision);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Condition save failed"); }
    finally { setBusy(false); }
  }
  return <section className="panel case-section" aria-label="Case scene conditions">
    <h2>Scene conditions</h2><p data-testid="case-condition-state">{prior?.values && Object.keys(prior.values).length ? prior.review_status : "unclassified"}</p>
    {prior && <><div className="condition-chips">{Object.entries(prior.values).map(([k, v]) => <span key={k}>{k}: {v}</span>)}</div>
      <p>Declared by {prior.author} · taxonomy r{prior.taxonomy.revision} · {prior.declared_at}</p><p className="case-note">{prior.note}</p></>}
    <p>Manual source declarations, not image recognition or authenticated approval. Unknown or mixed conditions remain unclassified. Only reviewed, fully classified cases enter the condition grid.</p>
    <button className="button secondary" onClick={() => setEditing(!editing)}>Edit scene conditions</button>
    {editing && <div className="case-form">
      {catalog.loading && <Loading />}{catalog.error && <ErrorState message={catalog.error} retry={catalog.reload} />}
      {catalog.data && <>{catalog.data.axes.map((axis) => <label key={axis.key}>{axis.name}
        <select aria-label={`Case condition ${axis.key}`} value={values[axis.key] ?? ""} disabled={busy} onChange={(e) => {
          const next = { ...values }; if (e.target.value) next[axis.key] = e.target.value; else delete next[axis.key];
          setValues(next); setReviewed(false);
        }}><option value="">Unclassified / unknown</option>{axis.values.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}</select>
        <small>{axis.definition}</small></label>)}
        <label>Declared reviewer<input aria-label="Condition reviewer" maxLength={180} disabled={busy} value={author} onChange={(e) => { setAuthor(e.target.value); setReviewed(false); }} /></label>
        <label>Source and review note<textarea aria-label="Condition source note" maxLength={4000} disabled={busy} value={note} onChange={(e) => { setNote(e.target.value); setReviewed(false); }} /></label>
        <label className="condition-check"><input type="checkbox" aria-label="Confirm condition review" disabled={busy} checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} />I reviewed these declarations against the pinned source. This is not a test pass.</label>
        {error && <p role="alert">{error}</p>}
        <button className="button primary" disabled={busy || !author.trim() || !note.trim()} onClick={save}>{busy ? "Saving…" : error ? "Retry condition save" : "Publish condition revision"}</button>
      </>}
    </div>}
  </section>;
}

interface CoverageCase extends CaseSummary {
  source_selection_hash: string;
  conditions: { values: Record<string, string>; review_status: string };
  diagnostics: { state: string; tags: string[]; rule_version: string; best_single_evaluation_samples: number; source_samples: number };
}
interface CoverageReport {
  taxonomy: ConditionTaxonomy;
  denominator: { total_cases: number; classified_cases: number; unclassified_cases: number; exact_source_selections: number;
    repeated_selection_cases: number; grid_cells: number; cells_with_cases: number; cells_with_complete_linked_rows: number };
  evaluation_rows: Record<string, number>;
  cells: { conditions: Record<string, string>; cases: number; with_complete_linked_rows: number; with_observed_errors: number }[];
  cases: CoverageCase[]; limitations: string[]; manifest_hash: string; download_url: string;
}

export function CaseConditionCoverage({ recording, onSelectCell }: {
  recording: string; onSelectCell: (values: Record<string, string>) => void;
}) {
  const query = new URLSearchParams(recording.trim() ? { recording_id: recording.trim() } : {});
  const report = useSensorResource<CoverageReport>(`/case-condition-coverage?${query}`, 180);
  const [filter, setFilter] = useState("all");
  const data = report.data;
  const rows = data?.cases.filter((c) => {
    if (filter === "all") return true;
    if (filter === "unclassified") return c.conditions.review_status !== "reviewed" || Object.keys(c.conditions.values).length !== data.taxonomy.axes.length;
    return c.diagnostics.state !== "complete";
  }) ?? [];
  return <section className="panel case-section" aria-label="Condition coverage">
    <div className="case-toolbar"><h2>Condition coverage</h2><button className="button secondary" onClick={report.reload}>Refresh condition coverage</button></div>
    <p>All current cases {recording ? "in the selected recording" : "in this workspace"}, including resolved and dismissed. Library text, condition and diagnostic filters do not change this denominator.</p>
    {report.loading && <Loading label="Reading a consistent case snapshot…" />}{report.error && <ErrorState message={report.error} retry={report.reload} />}
    {data && <div data-testid="condition-coverage-report">
      <div className="condition-stats">
        <div><strong data-testid="coverage-total-cases">{data.denominator.total_cases}</strong><span>Current cases</span></div>
        <div><strong data-testid="coverage-classified-cases">{data.denominator.classified_cases}</strong><span>Reviewed and fully classified</span></div>
        <div><strong data-testid="coverage-unclassified-cases">{data.denominator.unclassified_cases}</strong><span>Missing / partial / draft</span></div>
        <div><strong>{data.denominator.cells_with_cases} / {data.denominator.grid_cells}</strong><span>Review-grid cells with cases</span></div>
      </div>
      <p data-testid="coverage-source-duplicates">{data.denominator.exact_source_selections} exact source selections · {data.denominator.repeated_selection_cases} repeated-selection cases. Overlapping intervals are not independent scenes.</p>
      <p>Linked-row evidence: {Object.entries(data.evaluation_rows).map(([k, n]) => `${k}: ${n}`).join(" · ")}. Complete means every pinned sample is linked to usable rows from ONE evaluation. It does not mean a pass or complete dataset coverage.</p>
      <p>{data.taxonomy.grid_policy} Select a cell to investigate its cases; no simulation is started.</p>
      <div className="case-table-scroll"><table data-testid="condition-grid"><thead><tr>{data.taxonomy.axes.map((a) => <th key={a.key}>{a.name}</th>)}<th>Cases</th><th>Complete linked rows</th><th>Observed errors</th><th>Investigate</th></tr></thead>
        <tbody>{data.cells.map((cell) => <tr key={JSON.stringify(cell.conditions)}>{data.taxonomy.axes.map((a) => <td key={a.key}>{cell.conditions[a.key]}</td>)}<td>{cell.cases}</td><td>{cell.with_complete_linked_rows}</td><td>{cell.with_observed_errors}</td><td><button onClick={() => onSelectCell(cell.conditions)} aria-label={`Inspect condition ${data.taxonomy.axes.map(a => cell.conditions[a.key]).join(" ")}`}>View cases</button></td></tr>)}</tbody></table></div>
      <h3>Every case in this snapshot</h3>
      <label className="case-toolbar">Inspect subset<select aria-label="Coverage case filter" value={filter} onChange={(e) => setFilter(e.target.value)}><option value="all">All cases</option><option value="unclassified">Unclassified for grid</option><option value="incomplete">Without complete linked rows</option></select></label>
      <p>Showing {rows.length} of {data.denominator.total_cases} cases. Export always contains all cases and all 36 grid cells.</p>
      <div className="case-table-scroll"><table data-testid="coverage-case-table"><thead><tr><th>Case / revision</th><th>Declared conditions</th><th>Evidence state</th><th>Rule-derived diagnostics</th></tr></thead><tbody>{rows.map((c) => <tr key={c.id}><td><a href={caseHref(c.id, c.revision)}>{c.title} · r{c.revision}</a></td><td>{c.conditions.review_status} · {Object.values(c.conditions.values).join(" / ") || "Unknown"}</td><td>{c.diagnostics.state} · {c.diagnostics.best_single_evaluation_samples}/{c.diagnostics.source_samples}</td><td>{c.diagnostics.tags.map((tag) => diagnosticLabels[tag] ?? tag).join(" · ") || "No observed FP/FN in linked rows (not a pass)"}</td></tr>)}</tbody></table></div>
      <div className="case-toolbar"><DownloadLink href={data.download_url} download className="button secondary">Download condition coverage JSON</DownloadLink><code>{data.manifest_hash}</code></div>
      <p>Export fails if the case inventory changes. Refresh and review the new snapshot rather than silently downloading different evidence.</p>
      <details><summary>Definitions, denominators and limitations</summary>{data.limitations.map((s) => <p key={s}>{s}</p>)}<pre>{JSON.stringify(data.taxonomy, null, 2)}</pre></details>
    </div>}
  </section>;
}
