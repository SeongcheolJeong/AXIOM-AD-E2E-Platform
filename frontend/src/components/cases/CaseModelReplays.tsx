import { useRef, useState } from "react";
import { api } from "../../api";
import { DownloadLink } from "../ArtifactAccess";
import { ErrorState, Loading } from "../ui";
import { useSensorResource } from "../sensors/SensorData";
import { useJobResource } from "../jobs/useJobResource";
import { caseHref, type CaseDetail } from "./CaseTypes";

interface Model { id: string; revision: number; name: string; profile: string; manifest_hash: string }
interface Proposal {
  preview_hash: string; baseline_id: string; model: Model; split: string;
  workload: { full_split_images: number; case_images: number; source_bytes: number };
  dataset_manifest_hash: string; scope: string;
}
interface ReplayPlan {
  id: string; definition_hash: string;
  definition: { baseline_id: string; model: Model; scope: string; workload: Proposal["workload"] };
  inference: { job_id: string }; evaluation: { job_id: string } | null;
  capture: { comparison_status: string } | null;
  inference_status: string; inference_counts: Record<string, number>; inference_error: string | null;
  inference_manifest_hash: string | null; evaluation_status: string | null; evaluation_error: string | null;
  comparison: { status: string; row_count: number; reasons: string[];
    metrics: { name: string; baseline: number | null; candidate: number | null; delta: number | null }[];
    rows: { image_id: string; outcome: string }[] } | null;
}
interface ReplayReport { plans: ReplayPlan[]; case_revision: number; download_url: string; manifest_hash: string }
const terminal = (status: string | null) => !!status && ["completed", "failed", "cancelled", "interrupted"].includes(status);
const number = (n: number | null) => n === null ? "—" : n.toFixed(5);

export default function CaseModelReplays({ value }: { value: CaseDetail }) {
  const [open, setOpen] = useState(false);
  const report = useJobResource<ReplayReport>(`/cases/${encodeURIComponent(value.id)}/model-replays?revision=${value.revision}`);
  return <section className="panel case-section" aria-label="Recorded model replay">
    <h2>Recorded model replay</h2>
    <p>Run a registered detector on the original images, then evaluate with the baseline labels and policy. The full baseline split is replayed, including images outside this case.</p>
    <p>No new sensor conditions, training or closed-loop driving are simulated. Model files must be trusted.</p>
    <button className="button secondary" onClick={() => setOpen(!open)}>Configure recorded model replay</button>
    {open && <ReplayForm value={value} />}
    {report.loading && <Loading label="Reading model replay jobs…" />}
    {report.error && <ErrorState message={report.error} retry={report.reload} />}
    {report.data && <div data-testid="model-replay-report">
      {report.error && <p role="alert">Previous observation only. Actions are disabled until the report refresh succeeds.</p>}
      {!report.data.plans.length && <p>No model replay has been submitted for this case revision.</p>}
      {report.data.plans.map((plan) => <ReplayResult key={plan.id} value={value} plan={plan} stale={!!report.error} />)}
      <DownloadLink href={report.data.download_url} download className="button secondary">Download model replay JSON</DownloadLink>
      <p>Export retains the full split and all attempts. Active job state changes invalidate an earlier export snapshot.</p>
    </div>}
  </section>;
}

function ReplayForm({ value }: { value: CaseDetail }) {
  const models = useSensorResource<Model[]>("/perception/models");
  const [baseline, setBaseline] = useState(value.evaluations[0]?.evaluation_id ?? "");
  const [model, setModel] = useState(""), [revision, setRevision] = useState(1), [note, setNote] = useState("");
  const [preview, setPreview] = useState<{ signature: string; data: Proposal } | null>(null);
  const [consent, setConsent] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const pending = useRef({ body: "", key: "" });
  const body = { expected_revision: value.revision, baseline_evaluation_id: baseline, model_id: model.trim(), model_revision: revision, note };
  const signature = JSON.stringify(body);
  const current = preview?.signature === signature ? preview.data : null;
  async function inspect() {
    setBusy(true); setError(""); setPreview(null); setConsent(false);
    try {
      const data = await api<Proposal>(`/cases/${encodeURIComponent(value.id)}/model-replays/preview`, { method: "POST", body: signature });
      setPreview({ signature, data });
    } catch (e) { setError(e instanceof Error ? e.message : "Preview failed"); }
    finally { setBusy(false); }
  }
  async function start() {
    if (!current) return;
    setBusy(true); setError("");
    const request = JSON.stringify({ ...body, expected_preview_hash: current.preview_hash, confirm_trusted_model: consent });
    if (pending.current.body !== request) pending.current = { body: request, key: crypto.randomUUID() };
    try {
      const next = await api<CaseDetail>(`/cases/${encodeURIComponent(value.id)}/model-replays`, {
        method: "POST", headers: { "Idempotency-Key": pending.current.key }, body: request,
      });
      location.hash = caseHref(next.id, next.revision);
    } catch (e) { setError(e instanceof Error ? e.message : "Queue submission failed"); }
    finally { setBusy(false); }
  }
  const options = [...new Map(value.evaluations.map(e => [e.evaluation_id, e])).values()];
  return <div className="case-form">
    {!options.length && <p>Link a completed, fully reviewed detection evaluation to this case first. No labels or evaluation scope will be guessed.</p>}
    <label>Baseline evaluation<select aria-label="Model replay baseline" value={baseline} disabled={busy} onChange={e => { setBaseline(e.target.value); setConsent(false); }}>
      <option value="">Select linked evaluation</option>{options.map(e => <option key={e.evaluation_id} value={e.evaluation_id}>{e.name} · {e.evaluation_id}</option>)}
    </select></label>
    {models.error && <ErrorState message={models.error} retry={models.reload} />}
    <label>Registered candidate model<input aria-label="Model replay candidate ID" list="replay-model-options" value={model} disabled={busy} onChange={e => {
      setModel(e.target.value); setConsent(false); const found = models.data?.find(m => m.id === e.target.value); if (found) setRevision(found.revision);
    }} /></label>
    <datalist id="replay-model-options">{models.data?.map(m => <option key={m.id} value={m.id}>{m.name} · r{m.revision} · {m.profile}</option>)}</datalist>
    <label>Exact model revision<input aria-label="Model replay candidate revision" type="number" min={1} step={1} value={revision} disabled={busy} onChange={e => { setRevision(Number(e.target.value)); setConsent(false); }} /></label>
    <label>Replay purpose<textarea aria-label="Model replay purpose" maxLength={1500} value={note} disabled={busy} onChange={e => { setNote(e.target.value); setConsent(false); }} /></label>
    <button className="button secondary" disabled={busy || !baseline || !model.trim() || !note.trim()} onClick={inspect}>Preview full replay workload</button>
    {current && <div data-testid="model-replay-preview">
      <p><strong>{current.workload.full_split_images} images</strong> in the full <strong>{current.split}</strong> split; {current.workload.case_images} image(s) belong to this case. No failed or negative image is excluded.</p>
      <p>{current.model.name} · r{current.model.revision}</p><code>{current.model.manifest_hash}</code>
      <p>Dataset: <code>{current.dataset_manifest_hash}</code></p>
      <label className="condition-check"><input type="checkbox" aria-label="Confirm trusted model replay" checked={consent} disabled={busy} onChange={e => setConsent(e.target.checked)} />I trust these model files and approve inference over the entire split. This is not a sandbox.</label>
      <button className="button primary" disabled={busy || !consent} onClick={start}>{busy ? "Submitting…" : "Submit recorded inference"}</button>
    </div>}
    {error && <p role="alert">{error}</p>}
  </div>;
}

function ReplayResult({ value, plan, stale }: { value: CaseDetail; plan: ReplayPlan; stale: boolean }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const pending = useRef({ signature: "", key: "" });
  async function act(stage: "evaluate" | "capture" | "cancel-inference" | "cancel-evaluation") {
    setBusy(true); setError("");
    if (stage.startsWith("cancel-")) {
      const path = stage === "cancel-inference" ? `/perception/jobs/${encodeURIComponent(plan.inference.job_id)}/cancel`
        : `/detection-evaluations/${encodeURIComponent(plan.evaluation!.job_id)}/cancel`;
      try { await api(path, { method: "POST" }); }
      catch (e) { setError(e instanceof Error ? e.message : "Cancellation request failed"); }
      finally { setBusy(false); }
      return;
    }
    const body = JSON.stringify({ expected_revision: value.revision, plan_hash: plan.definition_hash });
    const signature = stage + body;
    if (pending.current.signature !== signature) pending.current = { signature, key: crypto.randomUUID() };
    try {
      const result = await api<CaseDetail>(`/cases/${encodeURIComponent(value.id)}/model-replays/${encodeURIComponent(plan.id)}/${stage}`, {
        method: "POST", body, headers: { "Idempotency-Key": pending.current.key },
      });
      location.hash = caseHref(result.id, result.revision);
    } catch (e) { setError(e instanceof Error ? e.message : "Action failed"); }
    finally { setBusy(false); }
  }
  const review = plan.comparison;
  return <article className="case-section" data-testid="model-replay-attempt">
    <h3>{plan.definition.model.name} · r{plan.definition.model.revision}</h3>
    <p><code>{plan.id}</code> · full split: {plan.definition.workload.full_split_images} images</p>
    <p>Inference: <strong data-testid="model-replay-inference-state">{plan.inference_status}</strong> · {Object.entries(plan.inference_counts).map(([k,v]) => `${k}: ${v}`).join(" · ")}</p>
    {plan.inference_error && <p role="alert">{plan.inference_error}</p>}
    {!terminal(plan.inference_status) && <button disabled={busy || stale || plan.inference_status === "cancel_requested"} onClick={() => act("cancel-inference")}>Request inference cancellation</button>}
    {plan.inference_manifest_hash && <DownloadLink href={`/api/v1/perception/jobs/${plan.inference.job_id}/export`} download>Download inference evidence</DownloadLink>}
    {!plan.evaluation && <button className="button secondary" disabled={busy || stale || !terminal(plan.inference_status)} onClick={() => act("evaluate")}>Evaluate stored replay predictions</button>}
    {plan.evaluation && <><p>Evaluation: <strong data-testid="model-replay-evaluation-state">{plan.evaluation_status}</strong></p>
      <a href={`#datasets?${new URLSearchParams({ evaluation: plan.evaluation.job_id })}`}>Inspect replay evaluation</a>
      {plan.evaluation_error && <p role="alert">{plan.evaluation_error}</p>}
      {!terminal(plan.evaluation_status) && <button disabled={busy || stale || plan.evaluation_status === "cancel_requested"} onClick={() => act("cancel-evaluation")}>Request replay evaluation cancellation</button>}
      {!plan.capture && <button className="button primary" disabled={busy || stale || !terminal(plan.evaluation_status)} onClick={() => act("capture")}>Capture model replay outcome</button>}
    </>}
    {plan.capture && <p data-testid="model-replay-captured">Captured in this case. Original failures and workflow state are unchanged.</p>}
    {review && <><h4 data-testid="model-replay-comparison-state">{review.status}</h4>
      <p>All {review.row_count} selected images; a score change is not release approval.</p>
      {review.reasons.map(r => <p key={r}>{r}</p>)}
      <div className="case-table-scroll"><table data-testid="model-replay-metrics"><thead><tr><th>Metric (0–1)</th><th>Baseline</th><th>Replay</th><th>Delta</th></tr></thead><tbody>{review.metrics.map(m => <tr key={m.name}><th>{m.name}</th><td>{number(m.baseline)}</td><td>{number(m.candidate)}</td><td>{number(m.delta)}</td></tr>)}</tbody></table></div>
      <a href={`#datasets?${new URLSearchParams({ baseline: plan.definition.baseline_id, candidate: plan.evaluation!.job_id })}`}>Inspect full replay A/B comparison</a>
    </>}
    {error && <p role="alert">{error}</p>}
  </article>;
}
