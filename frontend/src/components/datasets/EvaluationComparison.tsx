import { useState } from "react";
import { DownloadLink } from "../ArtifactAccess";
import { Empty, ErrorState, Loading } from "../ui";
import { useSensorResource } from "../sensors/SensorData";
import type { EvaluationPage } from "./EvaluationTypes";

type Counts = Record<"gt" | "crowd_gt" | "tp" | "fp" | "fn" | "crowd_ignored", number>;
interface Review {
  status: "comparable" | "not_comparable" | "not_evaluated";
  reasons: string[];
  baseline: { id: string; name: string; manifest_hash: string | null; dataset_revision: number; coverage: { total: number; evaluated: number } | null };
  candidate: Review["baseline"];
  metrics: { name: string; baseline: number | null; candidate: number | null; delta: number | null }[];
  rows: { image_id: string; baseline: { status: string; counts: Counts | null }; candidate: { status: string; counts: Counts | null }; delta: Record<string, number> | null; outcome: string }[];
  row_count: number;
  limitations: string[];
  differences: { field: string; baseline: unknown; candidate: unknown }[];
  exports: Record<string, { url: string; sha256: string; size_bytes: number }>;
}
const value = (n: number | null) => n === null ? "—" : n.toFixed(5);

export default function EvaluationComparison({ onOpenEvaluation }: { onOpenEvaluation: (id: string) => void }) {
  const target = new URLSearchParams(location.hash.split("?")[1] ?? "");
  const [baseline, setBaseline] = useState(target.get("baseline") ?? "");
  const [candidate, setCandidate] = useState(target.get("candidate") ?? "");
  const [filter, setFilter] = useState("all");
  const library = useSensorResource<EvaluationPage>("/detection-evaluations?limit=100");
  const query = new URLSearchParams({ baseline_id: baseline.trim(), candidate_id: candidate.trim() });
  const selected = baseline.trim() && candidate.trim() && baseline.trim() !== candidate.trim();
  const review = useSensorResource<Review>(selected ? `/evaluation-comparisons?${query}` : null, 180);
  const data = review.data;
  const rows = data?.rows.filter((row) => filter === "all" || row.outcome === filter) ?? [];
  const counts = (c: Counts | null) => c ? `${c.tp} / ${c.fp} / ${c.fn}` : "Not evaluated";
  return <section className="panel evaluation-comparison" aria-label="Evaluation A/B review">
    <h2>Compare evaluations</h2>
    <p>1. Choose baseline A and candidate B. 2. Check identical evaluation conditions. 3. Inspect every image, then export a pinned review.</p>
    <p className="dataset-scope">Different model versions are allowed. Changed labels, scoring, runtime policy or incomplete input coverage withhold deltas. A score change is not release approval.</p>
    {library.error && <ErrorState message={library.error} retry={library.reload} />}
    <div className="dataset-library-controls">
      <label>Baseline A evaluation ID<input aria-label="Baseline evaluation ID" list="evaluation-review-options" value={baseline} onChange={(e) => setBaseline(e.target.value)} placeholder="Choose or paste an evaluation ID" /></label>
      <label>Candidate B evaluation ID<input aria-label="Candidate evaluation ID" list="evaluation-review-options" value={candidate} onChange={(e) => setCandidate(e.target.value)} placeholder="Choose or paste an evaluation ID" /></label>
    </div>
    <datalist id="evaluation-review-options">{library.data?.items.map((item) => <option key={item.id} value={item.id}>{item.name} · r{item.dataset_revision} · {item.status}</option>)}</datalist>
    <p className="muted">Suggestions contain the latest {library.data?.items.length ?? 0} evaluations. Older IDs can be pasted directly.</p>
    {baseline.trim() && baseline.trim() === candidate.trim() && <p role="alert">Choose two different evaluations.</p>}
    {!selected && <Empty title="Select two stored evaluations" description="No simulation or inference is started by this review." />}
    {review.loading && <Loading label="Verifying pinned comparison inputs…" />}
    {review.error && <ErrorState message={review.error} retry={review.reload} />}
    {data && <div data-testid="evaluation-comparison-report">
      <h3 data-testid="evaluation-comparison-status">{data.status}</h3>
      {data.reasons.map((reason) => <p key={reason} role="note">{reason}</p>)}
      <div className="dataset-actions">{(["baseline", "candidate"] as const).map((side) => <button key={side} className="button secondary" onClick={() => onOpenEvaluation(data[side].id)}>Inspect {side === "baseline" ? "A" : "B"}: {data[side].name}</button>)}</div>
      <p>A coverage: {data.baseline.coverage ? `${data.baseline.coverage.evaluated}/${data.baseline.coverage.total}` : "not evaluated"} · B coverage: {data.candidate.coverage ? `${data.candidate.coverage.evaluated}/${data.candidate.coverage.total}` : "not evaluated"}</p>
      <div className="dataset-table-scroll"><table data-testid="evaluation-comparison-metrics"><thead><tr><th>Metric · 0–1</th><th>Baseline A</th><th>Candidate B</th><th>Delta B−A</th></tr></thead><tbody>{data.metrics.map((m) => <tr key={m.name}><th>{m.name}</th><td>{value(m.baseline)}</td><td>{value(m.candidate)}</td><td data-testid={`comparison-delta-${m.name}`}>{value(m.delta)}</td></tr>)}</tbody></table></div>
      <label>Image diagnostics <select aria-label="Comparison image filter" value={filter} onChange={(e) => setFilter(e.target.value)}>{["all", "more_errors", "fewer_errors", "tradeoff", "unchanged", "not_evaluated"].map((item) => <option key={item} value={item}>{item.replaceAll("_", " ")}</option>)}</select></label>
      <p data-testid="comparison-denominator">Showing {rows.length} of {data.row_count} selected images. Filtering never changes scores or the export denominator.</p>
      <div className="dataset-table-scroll"><table data-testid="evaluation-comparison-images"><thead><tr><th>Image ID</th><th>A state</th><th>B state</th><th>A TP / FP / FN</th><th>B TP / FP / FN</th><th>Diagnostic</th></tr></thead><tbody>{rows.map((row) => <tr key={row.image_id}><td><code>{row.image_id}</code></td><td>{row.baseline.status}</td><td>{row.candidate.status}</td><td>{counts(row.baseline.counts)}</td><td>{counts(row.candidate.counts)}</td><td>{row.outcome}</td></tr>)}</tbody></table></div>
      {data.differences.length > 0 && <details><summary>Why these inputs are not comparable</summary><pre>{JSON.stringify(data.differences, null, 2)}</pre></details>}
      <div className="dataset-actions">{Object.entries(data.exports).map(([kind, entry]) => <DownloadLink key={kind} href={entry.url} download className="button secondary">Download comparison {kind.toUpperCase()}</DownloadLink>)}</div>
      <p className="dataset-scope">Exports contain all selected rows and source manifest hashes. They do not include source PNGs, model weights or a reviewer approval.</p>
      <details><summary>Source hashes and limitations</summary><pre>{JSON.stringify({ baseline: data.baseline, candidate: data.candidate, limitations: data.limitations, exports: data.exports }, null, 2)}</pre></details>
    </div>}
  </section>;
}
