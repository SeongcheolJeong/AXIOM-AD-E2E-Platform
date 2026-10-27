import { useEffect, useRef, useState } from "react";
import { api } from "../../api";
import { DownloadLink } from "../ArtifactAccess";
import { ErrorState, Loading } from "../ui";
import { useSensorResource } from "../sensors/SensorData";
import { useJobResource } from "../jobs/useJobResource";
import AnnotationEditor from "./AnnotationEditor";
import type { DatasetDetail, DatasetAnnotation } from "./DatasetTypes";
import "../../annotation-assist.css";

type Model = { id: string; revision: number; name: string; labels: string[]; profile: string };
type Pin = { id: string; revision: number; manifest_hash: string };
type Origin = { task_id: string; model_id: string; model_revision: number; model_sha256: string; publication_hash: string };
type Preview = { preview_hash: string; model: Model; workload: { total_images: number; target_images: number; preserved_images: number; inference_jobs: number; recordings: number } };
type Report = { id: string; terminal: boolean; dataset: Pin; model: Model; manifest_hash: string; download_url: string;
  workload: Preview["workload"]; counts: Record<string, number>;
  jobs: { id: string; status: string; error: string | null }[];
  rows: { image_id: string; status: string; reason: string | null; suggestions: DatasetAnnotation[];
    raw_predictions: { id: string; label: string; score: number; bbox_xyxy: number[] }[] }[];
  publication: { dataset: Pin; download_url: string } | null };
const datasetHref = (p: Pin) => `#datasets?${new URLSearchParams({ dataset: p.id, revision: String(p.revision) })}`;

export function AnnotationLineage({ dataset }: { dataset: DatasetDetail }) {
  const data = useSensorResource<{ images: Record<string, Origin[]> }>(`/datasets/${dataset.id}/annotation-lineage?revision=${dataset.revision}`);
  if (data.error) return <ErrorState message={data.error} retry={data.reload} />;
  const count = Object.keys(data.data?.images ?? {}).length;
  if (!count) return null;
  const origins = [...new Map(Object.values(data.data!.images).flat().map(o => [o.task_id, o])).values()];
  return <aside className="annotation-origin" data-testid="annotation-lineage">
    <strong>{count} image(s) have model-assisted annotation origins.</strong>
    <p>Review may edit the boxes, but does not make their origin independent. Known generator-model self-evaluation is blocked. Copying labels through Collections retains this lineage.</p>
    {origins.map(o => <p key={o.task_id}><code>{o.model_id} r{o.model_revision}</code>{" · "}
      <DownloadLink href={`/api/v1/annotation-tasks/${o.task_id}/publication`} download>Original draft receipt</DownloadLink></p>)}
  </aside>;
}

export default function AnnotationAssist({ dataset }: { dataset: DatasetDetail }) {
  const [open, setOpen] = useState(false), [taskId, setTaskId] = useState("");
  const library = useSensorResource<{ items: { id: string; dataset_revision: number }[]; next_offset: number | null }>(`/datasets/${dataset.id}/annotation-tasks`);
  const report = useJobResource<Report>(taskId ? `/annotation-tasks/${encodeURIComponent(taskId)}` : null, 2000);
  useEffect(() => { if (!taskId && library.data?.items[0]) setTaskId(library.data.items[0].id); }, [taskId, library.data]);
  const eligible = dataset.images.filter(m => m.review_status === "unreviewed" && !m.annotations.length).length;
  return <section className="panel annotation-assist" aria-label="Automatic label drafts">
    <h2>Automatic label drafts</h2>
    <p>Run a registered detector on empty, unreviewed images. Publish suggestions as a new <strong>unreviewed</strong> dataset revision, then inspect and correct them with the existing label editor.</p>
    <p>{eligible} eligible / {dataset.images.length} total images. Existing boxes and reviewed labels are preserved. Zero detections never approve a negative image.</p>
    <button className="button secondary" onClick={() => setOpen(!open)}>Configure automatic labels</button>
    {open && <DraftForm dataset={dataset} onStarted={id => { setTaskId(id); library.reload(); setOpen(false); }} />}
    {library.error && <ErrorState message={library.error} retry={library.reload} />}
    {!!library.data?.items.length && <label>Draft attempt<select aria-label="Annotation task" value={taskId} onChange={e => setTaskId(e.target.value)}>
      {library.data.items.map(t => <option key={t.id} value={t.id}>{t.id} · source r{t.dataset_revision}</option>)}</select></label>}
    {library.data?.next_offset != null && <p>Showing the newest 50 attempts. Older attempts remain available through the paginated API.</p>}
    {report.loading && <Loading label="Reading draft execution…" />}
    {report.error && <ErrorState message={report.error} retry={report.reload} />}
    {report.data && <DraftResult key={report.data.id} data={report.data} dataset={dataset} stale={!!report.error} refresh={report.reload} />}
    <AnnotationLineage dataset={dataset} />
  </section>;
}

function DraftForm({ dataset, onStarted }: { dataset: DatasetDetail; onStarted: (id: string) => void }) {
  const models = useSensorResource<Model[]>("/perception/models");
  const [id, setId] = useState(""), [revision, setRevision] = useState(1), [threshold, setThreshold] = useState(.35);
  const selected = useSensorResource<Model>(id ? `/perception/models/${encodeURIComponent(id)}?revision=${revision}` : null, 150);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [note, setNote] = useState(""), [consent, setConsent] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [preview, setPreview] = useState<{ signature: string; data: Preview } | null>(null);
  const pending = useRef({ signature: "", key: "" });
  useEffect(() => {
    if (selected.data) setMapping(Object.fromEntries(selected.data.labels.map(label => [label, dataset.labels.includes(label) ? label : "__choose__"])));
  }, [selected.data, dataset.labels]);
  const body = { expected_revision: dataset.revision, model_id: id, model_revision: revision, runtime_id: "onnx-cpu",
    class_mapping: (selected.data?.labels ?? []).map(label => ({ model_label: label, dataset_label: mapping[label] === "__exclude__" ? null : mapping[label] })),
    score_threshold: threshold, iou_threshold: .45, max_detections: 100, seed: 42, note };
  const signature = JSON.stringify(body);
  const valid = !!selected.data && !selected.error && selected.data.labels.every(l => mapping[l] && mapping[l] !== "__choose__") && !!note.trim();
  const current = preview?.signature === signature ? preview.data : null;
  async function act(submit: boolean) {
    setBusy(true); setError("");
    try {
      const path = `/datasets/${dataset.id}/annotation-tasks`;
      if (!submit) {
        const value = await api<Preview>(path+"/preview", { method: "POST", body: signature });
        setPreview({ signature, data: value }); setConsent(false);
      } else if (current && consent) {
        const payload = JSON.stringify({ ...body, expected_preview_hash: current.preview_hash, confirm_trusted_model: true });
        if (pending.current.signature !== payload) pending.current = { signature: payload, key: crypto.randomUUID() };
        const task = await api<{ id: string }>(path, { method: "POST", body: payload, headers: { "Idempotency-Key": pending.current.key } });
        onStarted(task.id);
      }
    } catch (e) { setError(e instanceof Error ? e.message : "Annotation action failed"); }
    finally { setBusy(false); }
  }
  return <fieldset disabled={busy} className="annotation-form">
    {models.error && <ErrorState message={models.error} retry={models.reload} />}
    <label>Registered model<select aria-label="Auto-label model" value={id} onChange={e => { setId(e.target.value); setRevision(models.data?.find(m => m.id === e.target.value)?.revision ?? 1); setConsent(false); }}>
      <option value="">Select a trusted model</option>{models.data?.map(m => <option key={m.id} value={m.id}>{m.name} · r{m.revision}</option>)}</select></label>
    <label>Exact revision<input type="number" min={1} aria-label="Auto-label model revision" value={revision} onChange={e => setRevision(Number(e.target.value))} /></label>
    {selected.error && <ErrorState message={selected.error} retry={selected.reload} />}
    {selected.data?.labels.map(label => <label key={label}>Map {label}<select aria-label={`Auto-label mapping ${label}`} value={mapping[label] ?? "__choose__"} onChange={e => setMapping({ ...mapping, [label]: e.target.value })}>
      <option value="__choose__">Choose dataset class</option><option value="__exclude__">Exclude this model class explicitly</option>
      {dataset.labels.map(name => <option key={name} value={name}>{name}</option>)}</select></label>)}
    <label>Score threshold<input aria-label="Auto-label score threshold" type="number" min={0} max={1} step={.05} value={threshold} onChange={e => setThreshold(Number(e.target.value))} /></label>
    <label>Purpose<textarea aria-label="Auto-label purpose" maxLength={1500} value={note} onChange={e => setNote(e.target.value)} /></label>
    <p>CPU inference · NMS IoU 0.45 · at most 100 predictions/image · seed 42. All mapped outputs remain suggestions.</p>
    <button className="button secondary" disabled={!valid || busy} onClick={() => act(false)}>Preview annotation workload</button>
    {current && <div data-testid="annotation-preview"><strong>{current.workload.target_images} images to infer · {current.workload.inference_jobs} jobs · {current.workload.recordings} recordings</strong>
      <p>{current.workload.preserved_images} existing image(s) preserved. Publishing does not approve any label.</p>
      <label className="annotation-check"><input type="checkbox" aria-label="Confirm trusted automatic labeling" checked={consent} onChange={e => setConsent(e.target.checked)} />I trust the model and approve this complete workload. Subprocess execution is not a security sandbox.</label>
      <button className="button primary" disabled={!consent || busy} onClick={() => act(true)}>Generate label drafts</button>
    </div>}
    {error && <p role="alert">{error}</p>}
  </fieldset>;
}

function DraftResult({ data, dataset, stale, refresh }: { data: Report; dataset: DatasetDetail; stale: boolean; refresh: () => void }) {
  const [imageId, setImageId] = useState(data.rows[0]?.image_id ?? ""), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const pending = useRef({ signature: "", key: "" });
  const row = data.rows.find(r => r.image_id === imageId), asset = dataset.image_assets.find(a => a.id === imageId);
  const current = dataset.revision === data.dataset.revision && dataset.manifest_hash === data.dataset.manifest_hash;
  async function publish() {
    setBusy(true); setError("");
    const body = JSON.stringify({ expected_revision: data.dataset.revision, expected_report_hash: data.manifest_hash,
      note: `Unreviewed model-assisted drafts from ${data.id}; inspect original pixels before approval.` });
    if (pending.current.signature !== body) pending.current = { signature: body, key: crypto.randomUUID() };
    try {
      const receipt = await api<{ dataset: Pin }>(`/annotation-tasks/${data.id}/publish`, { method: "POST", body, headers: { "Idempotency-Key": pending.current.key } });
      location.hash = datasetHref(receipt.dataset);
    } catch (e) { setError(e instanceof Error ? e.message : "Draft publication failed"); }
    finally { setBusy(false); }
  }
  async function cancel(id: string) {
    setBusy(true); setError("");
    try { await api(`/perception/jobs/${id}/cancel`, { method: "POST" }); refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : "Cancellation failed"); }
    finally { setBusy(false); }
  }
  return <div data-testid="annotation-task-detail">
    <p><code data-testid="annotation-task-id">{data.id}</code> · <strong data-testid="annotation-task-state">{data.terminal ? "finished" : "in progress"}</strong></p>
    <p>{data.model.name} · r{data.model.revision}. {Object.entries(data.counts).map(([k,v]) => `${k}: ${v}`).join(" · ")}</p>
    {data.jobs.map(j => <p key={j.id}><code>{j.id}</code> · {j.status} {j.error && <span role="alert">{j.error}</span>}
      {!["completed","failed","cancelled","interrupted"].includes(j.status) && <button disabled={busy || stale || j.status === "cancel_requested"} onClick={() => cancel(j.id)}>Cancel inference {j.id}</button>}</p>)}
    <div className="annotation-table-scroll"><table data-testid="annotation-rows"><thead><tr><th>Source image</th><th>Status</th><th>Suggestions</th><th>Reason</th></tr></thead><tbody>
      {data.rows.map(r => <tr key={r.image_id}><td><button onClick={() => setImageId(r.image_id)}>{r.image_id}</button></td><td>{r.status}</td><td>{r.suggestions.length}</td><td>{r.reason ?? "—"}</td></tr>)}
    </tbody></table></div>
    {row && asset && <details open><summary>Inspect proposed boxes on the original image</summary><AnnotationEditor key={imageId} image={asset.image} labels={dataset.labels} annotations={row.suggestions} reviewedNegative={false} readOnly onChange={() => {}} />
      <p>These are proposals. Corrections and per-image review are performed in Create dataset revision after publishing the drafts.</p>
      <details><summary>Raw model scores and excluded-class predictions</summary><pre>{JSON.stringify(row.raw_predictions,null,2)}</pre></details></details>}
    {!data.publication && <><p>No labels have been changed. Failed images remain unreviewed.</p>
      {!current && <p role="alert">Dataset changed since this task. Publication will not overwrite newer edits.</p>}
      <button className="button primary" disabled={busy || stale || !current || !data.terminal || !(data.counts.suggested+data.counts.empty_prediction)} onClick={publish}>Publish unreviewed draft revision</button></>}
    {data.publication && <p data-testid="annotation-published">Drafts published — human review is still required. <a href={datasetHref(data.publication.dataset)}>Open unreviewed dataset revision</a>{" · "}<DownloadLink href={data.publication.download_url} download>Download original draft receipt</DownloadLink></p>}
    <p><DownloadLink href={data.download_url} download>Download annotation report JSON</DownloadLink></p>
    {stale && <p role="alert">Previous observation only. Actions are disabled until refresh succeeds.</p>}{error && <p role="alert">{error}</p>}
  </div>;
}
