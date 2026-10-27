import { useRef, useState } from "react";
import { api } from "../api";
import { PageHeading, ErrorState, Loading } from "../components/ui";
import { DownloadLink } from "../components/ArtifactAccess";
import { useSensorResource } from "../components/sensors/SensorData";
import "../cases.css";
import "../collections.css";
import { SceneSelectionOrigin } from "./SceneDiscovery";

type Source = { kind: "case" | "dataset" | "image"; case_id?: string; dataset_id?: string; image_id?: string; revision?: number; split?: string; reason: string };
type Definition = { name: string; purpose: string; sources: Source[] };
type ImageAsset = { id: string; image: { sha256: string; width: number; height: number }; input: { decoder_id: string; decoder_version: string } };
type LabelOption = { id: string; dataset_id: string; dataset_revision: number; dataset_name: string; labels: string[];
  member: { image_id: string; review_status: string; annotations: unknown[]; reviewer: string } };
type Row = { key: string; source: { recording_id: string; channel_id: number; ordinal: number; log_time_ns: string };
  source_indices: number[]; image_state: string; images: ImageAsset[]; label_options: LabelOption[] };
type Selection = { definition: Definition; sources: { index: number; name: string; selection: Source; conditions?: unknown }[];
  rows: Row[]; summary: Record<string, number>; limitations: string[];
  duplicate_png_groups: { png_sha256: string; source_keys: string[] }[] };
type Collection = { id: string; revision: number; manifest_hash: string; download_url: string; selection: Selection };
type Decision = { key: string; action: "include" | "exclude"; image_id?: string; label_option_id?: string | null; split?: string; reason?: string };
type Publication = { id: string; download_url: string; dataset: { id: string; revision: number; manifest_hash: string };
  proposal: { collection_id: string; collection_revision: number; denominator: Record<string, number>; stats: Record<string, number>; dataset_definition: { name: string } } };
type DatasetPreview = { preview_hash: string; stats: Record<string, number>; denominator: Record<string, number>; limitations: string[] };

const href = (id: string, revision?: number) => `#collections?${new URLSearchParams({ collection: id, ...(revision ? { revision: String(revision) } : {}) })}`;
const datasetHref = (id: string, revision: number) => `#datasets?${new URLSearchParams({ dataset: id, revision: String(revision) })}`;
const sourceHref = (s: Source) => s.kind === "case" ? `#cases?${new URLSearchParams({ case: s.case_id!, revision: String(s.revision) })}`
  : s.kind === "dataset" ? datasetHref(s.dataset_id!, s.revision!) : null;
const originalHref = (r: Row) => `#catalog?${new URLSearchParams({ recording: r.source.recording_id,
  channel: String(r.source.channel_id), ordinal: String(r.source.ordinal), at: r.source.log_time_ns })}`;

export default function Collections() {
  const params = new URLSearchParams(location.hash.split("?")[1] ?? "");
  const id = params.get("collection"), revision = params.get("revision");
  const [search, setSearch] = useState(""), [offset, setOffset] = useState(0);
  const library = useSensorResource<{ items: { id: string; revision: number; name: string; purpose: string; summary: Record<string, number> }[]; total: number; next_offset: number | null }>(
    `/collections?${new URLSearchParams({ q: search, limit: "50", offset: String(offset) })}`, 180);
  const detail = useSensorResource<Collection>(id ? `/collections/${encodeURIComponent(id)}${revision ? `?revision=${encodeURIComponent(revision)}` : ""}` : null);
  const sourceCase = params.get("source_case"), sourceDataset = params.get("source_dataset");
  const initial: Definition = { name: "", purpose: "", sources: sourceCase ? [{ kind: "case", case_id: sourceCase, revision: Number(params.get("source_revision") ?? 1), reason: "" }]
    : sourceDataset ? [{ kind: "dataset", dataset_id: sourceDataset, revision: Number(params.get("source_revision") ?? 1), split: "all", reason: "" }] : [] };
  return <div className="collections-page cases-page">
    <PageHeading eyebrow="SELECT → REVIEW → FREEZE A DATASET" title="Data collections" description="Reuse normal, unreviewed and investigation data across recordings. Originals and previous revisions stay unchanged." />
    <div className="case-toolbar"><a href="#collections" className="button primary">New collection</a>
      <label>Find a saved collection<input aria-label="Find collection" value={search} onChange={e => { setSearch(e.target.value); setOffset(0); }} /></label>
      <button onClick={() => { library.reload(); detail.reload(); }} className="button secondary">Refresh collections</button></div>
    {library.error && <ErrorState message={library.error} retry={library.reload} />}
    {library.data && <section className="panel case-section collection-library"><h2>Saved collections · {library.data.total}</h2>
      {!library.data.items.length && <p>No collection in this selection. Start with cases, captured images or a dataset revision below.</p>}
      {library.data.items.map(c => <a className="case-card" key={c.id} href={href(c.id, c.revision)}><strong>{c.name} · r{c.revision}</strong><span>{c.summary.unique_messages} messages · {c.summary.recordings} recording(s)</span></a>)}
      <div className="case-toolbar"><button disabled={!offset} onClick={() => setOffset(Math.max(0, offset-50))}>Previous collections</button>
        <button disabled={library.data.next_offset === null} onClick={() => setOffset(library.data!.next_offset!)}>More collections</button></div>
    </section>}
    {!id && <CollectionForm initial={initial} />}
    {detail.loading && <Loading />}{detail.error && <ErrorState message={detail.error} retry={detail.reload} />}
    {detail.data && <CollectionDetail value={detail.data} key={`${detail.data.id}/${detail.data.revision}`} />}
  </div>;
}

function CollectionForm({ initial, prior }: { initial: Definition; prior?: Collection }) {
  const [name, setName] = useState(initial.name), [purpose, setPurpose] = useState(initial.purpose), [sources, setSources] = useState<Source[]>(initial.sources);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [preview, setPreview] = useState<{ signature: string; data: Selection & { preview_hash: string } } | null>(null);
  const pending = useRef({ body: "", key: "" });
  const cases = useSensorResource<{ items: { id: string; title: string; revision: number }[] }>("/cases?limit=100");
  const datasets = useSensorResource<{ id: string; name: string; revision: number }[]>("/datasets");
  const definition = { name, purpose, sources }, signature = JSON.stringify(definition);
  const current = preview?.signature === signature ? preview.data : null;
  function setSource(index: number, next: Source) { setSources(old => old.map((s, i) => i === index ? next : s)); }
  async function inspect() {
    setBusy(true); setError(""); setPreview(null);
    try { setPreview({ signature, data: await api("/collections/preview", { method: "POST", body: signature }) }); }
    catch (e) { setError(e instanceof Error ? e.message : "Preview failed"); } finally { setBusy(false); }
  }
  async function save() {
    if (!current) return;
    setBusy(true); setError("");
    const body = JSON.stringify({ ...definition, expected_preview_hash: current.preview_hash, ...(prior ? { expected_revision: prior.revision } : {}) });
    if (pending.current.body !== body) pending.current = { body, key: crypto.randomUUID() };
    try {
      const result = await api<Collection>(prior ? `/collections/${prior.id}/revisions` : "/collections", { method: "POST", body, headers: { "Idempotency-Key": pending.current.key } });
      location.hash = href(result.id, result.revision);
    } catch (e) { setError(e instanceof Error ? e.message : "Collection save failed"); } finally { setBusy(false); }
  }
  return <section className="panel case-section"><h2>{prior ? "Publish a new selection revision" : "1 · Select original data"}</h2>
    <p>Select exact source revisions, not a changing search result. A Case includes all its pinned messages; dataset sources may include normal and reviewed-negative images without a Case.</p>
    <div className="case-form"><label>Collection name<input aria-label="Collection name" maxLength={180} disabled={busy} value={name} onChange={e => setName(e.target.value)} /></label>
      <label>Selection purpose<textarea aria-label="Collection purpose" maxLength={1500} disabled={busy} value={purpose} onChange={e => setPurpose(e.target.value)} /></label></div>
    {cases.error && <ErrorState message={cases.error} retry={cases.reload} />}{datasets.error && <ErrorState message={datasets.error} retry={datasets.reload} />}
    <datalist id="collection-case-options">{cases.data?.items.map(c => <option key={c.id} value={c.id}>{c.title} · r{c.revision}</option>)}</datalist>
    <datalist id="collection-dataset-options">{datasets.data?.map(d => <option key={d.id} value={d.id}>{d.name} · r{d.revision}</option>)}</datalist>
    <div className="collection-sources">{sources.map((s, i) => <fieldset key={i} data-testid="collection-source"><legend>Source {i+1}</legend>
      <label>Type<select aria-label={`Source ${i+1} type`} disabled={busy} value={s.kind} onChange={e => setSource(i, { kind: e.target.value as Source["kind"], revision: 1, reason: s.reason })}>
        <option value="case">Case interval</option><option value="dataset">Dataset revision</option><option value="image">Captured image</option></select></label>
      <label>Exact source ID<input aria-label={`Source ${i+1} ID`} disabled={busy} list={s.kind === "case" ? "collection-case-options" : s.kind === "dataset" ? "collection-dataset-options" : undefined}
        value={s.case_id ?? s.dataset_id ?? s.image_id ?? ""} onChange={e => {
          const identity = e.target.value.trim();
          const rev = s.kind === "case" ? cases.data?.items.find(c => c.id === identity)?.revision : datasets.data?.find(d => d.id === identity)?.revision;
          setSource(i, s.kind === "case" ? { kind: s.kind, case_id: identity, revision: rev ?? s.revision ?? 1, reason: s.reason }
            : s.kind === "dataset" ? { kind: s.kind, dataset_id: identity, revision: rev ?? s.revision ?? 1, split: s.split ?? "all", reason: s.reason }
            : { kind: s.kind, image_id: identity, reason: s.reason });
        }} /></label>
      {s.kind !== "image" && <label>Revision<input aria-label={`Source ${i+1} revision`} type="number" min={1} step={1} disabled={busy} value={s.revision ?? 1} onChange={e => setSource(i, { ...s, revision: Number(e.target.value) })} /></label>}
      {s.kind === "dataset" && <label>Source split<select aria-label={`Source ${i+1} split`} value={s.split ?? "all"} disabled={busy} onChange={e => setSource(i, { ...s, split: e.target.value })}>
        {["all","train","validation","test","unspecified"].map(k => <option key={k}>{k}</option>)}</select></label>}
      <label>Why selected<textarea aria-label={`Source ${i+1} reason`} maxLength={1500} disabled={busy} value={s.reason} onChange={e => setSource(i, { ...s, reason: e.target.value })} /></label>
      <button disabled={busy} onClick={() => setSources(old => old.filter((_, n) => n !== i))}>Remove source {i+1}</button>
    </fieldset>)}</div>
    <div className="case-toolbar"><button disabled={busy || sources.length >= 32} onClick={() => setSources(old => [...old, { kind: "case", case_id: "", revision: 1, reason: "" }])}>Add collection source</button>
      <button className="button secondary" disabled={busy || !name.trim() || !purpose.trim() || !sources.length} onClick={inspect}>Preview complete collection</button></div>
    {current && <div data-testid="collection-preview"><SelectionView selection={current} /><p>No files or jobs were created. Saving freezes these source identities and annotation options.</p>
      <button className="button primary" disabled={busy} onClick={save}>{prior ? "Publish collection revision" : "Save collection snapshot"}</button></div>}
    {error && <p role="alert">{error}</p>}
  </section>;
}

function SelectionView({ selection }: { selection: Selection }) {
  const s = selection.summary;
  return <><div className="condition-stats">
    <div><strong data-testid="collection-unique">{s.unique_messages}</strong><span>Unique original messages</span></div>
    <div><strong data-testid="collection-recordings">{s.recordings}</strong><span>Recordings</span></div>
    <div><strong data-testid="collection-overlap">{s.repeated_message_references}</strong><span>Overlapping references merged</span></div>
    <div><strong data-testid="collection-uncaptured">{s.without_captured_image}</strong><span>Messages without a captured image</span></div></div>
    <p>{s.expanded_references} source references → {s.unique_messages} original messages. {s.with_reviewed_label_option} have a reviewed-label option; labels are not selected automatically.</p>
    <p>Equal-PNG groups across different messages: {s.duplicate_png_groups}. This does not detect all near-duplicates or prove scene independence.</p>
    <div className="case-table-scroll"><table data-testid="collection-messages"><thead><tr><th>Source message</th><th>Selection reasons</th><th>Captured image</th><th>Label options</th></tr></thead>
      <tbody>{selection.rows.map(r => <tr key={r.key}><td><a href={originalHref(r)}>Channel {r.source.channel_id} · ordinal {r.source.ordinal}</a><small>{r.source.recording_id}</small><small>{r.source.log_time_ns} ns</small></td>
        <td>{r.source_indices.map((n, i) => <div key={`${n}/${i}`}>#{n+1}: {selection.sources[n].selection.reason}</div>)}</td>
        <td>{r.image_state.replaceAll("_", " ")}<small>{r.images.length} interpretation(s)</small></td>
        <td>{r.label_options.length ? r.label_options.map(o => <div key={o.id}>{o.dataset_name} r{o.dataset_revision} · {o.member.review_status} · {o.member.annotations.length} boxes</div>) : "None — keep unreviewed"}</td></tr>)}</tbody></table></div>
    <details><summary>Fixed source revisions, definitions and limitations</summary>{selection.sources.map(ref => <p key={ref.index}>
      {sourceHref(ref.selection) ? <a href={sourceHref(ref.selection)!}>{ref.name}</a> : ref.name} · {ref.selection.reason}</p>)}
      {selection.limitations.map(t => <p key={t}>{t}</p>)}</details>
  </>;
}

function CollectionDetail({ value }: { value: Collection }) {
  const [editing, setEditing] = useState(false), [curating, setCurating] = useState(false);
  const publications = useSensorResource<{ items: Publication[] }>(`/collection-datasets?collection_id=${value.id}`);
  return <div data-testid="collection-detail"><section className="panel case-section"><h2>{value.selection.definition.name}</h2>
    <p><code data-testid="collection-id">{value.id}</code> · <strong data-testid="collection-revision">r{value.revision}</strong></p>
    <p>{value.selection.definition.purpose}</p><div className="case-toolbar">
      <a href={href(value.id)}>Latest collection revision</a>{value.revision > 1 && <a href={href(value.id, value.revision-1)}>Previous collection revision</a>}
      <DownloadLink href={value.download_url} download>Download collection snapshot JSON</DownloadLink>
      <a className="button secondary" href={`#scene-discovery?${new URLSearchParams({source_collection:value.id,source_revision:String(value.revision)})}`}>Discover scenes in collection</a>
      <button onClick={() => setEditing(!editing)}>Edit selection as new revision</button></div>
    <SelectionView selection={value.selection} />
    </section>
    {editing && <CollectionForm initial={value.selection.definition} prior={value} />}
    <SceneSelectionOrigin collectionId={value.id} />
    <section className="panel case-section"><h2>2 · Review membership and labels</h2>
      <p>Freeze an ordinary detection dataset from captured images. No new inference is performed. A source without a captured image must be explicitly excluded or captured through the existing data workflow and added in a new collection revision.</p>
      <button className="button primary" onClick={() => setCurating(!curating)}>Prepare dataset from collection</button>
      {curating && <DatasetCuration collection={value} onSaved={publications.reload} />}</section>
    <section className="panel case-section"><h2>Published datasets and selection receipts</h2>
      {publications.loading && <Loading />}{publications.error && <ErrorState message={publications.error} retry={publications.reload} />}
      {publications.data?.items.map(p => <article key={p.id} className="collection-publication"><a href={datasetHref(p.dataset.id, p.dataset.revision)}>
        Open published dataset · {p.proposal.dataset_definition.name} r{p.dataset.revision}</a>
        <p>Collection r{p.proposal.collection_revision} · {p.proposal.denominator.included} included / {p.proposal.denominator.collection_messages} selected · {p.proposal.denominator.excluded} explicitly excluded</p>
        <DownloadLink href={p.download_url} download>Download curation receipt JSON</DownloadLink></article>)}
    </section></div>;
}

function DatasetCuration({ collection, onSaved }: { collection: Collection; onSaved: () => void }) {
  const rows = collection.selection.rows;
  const [name, setName] = useState(collection.selection.definition.name + " · curated"), [note, setNote] = useState("");
  const [labels, setLabels] = useState([...new Set(rows.flatMap(r => r.label_options.flatMap(o => o.labels)))].join(", "));
  const [decisions, setDecisions] = useState<Decision[]>(rows.map(r => r.images.length === 1 ? { key: r.key, action: "include", image_id: r.images[0].id, label_option_id: null, split: "unspecified" }
    : { key: r.key, action: "exclude", reason: "" }));
  const [bulkSplit, setBulkSplit] = useState("test"), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [preview, setPreview] = useState<{ signature: string; data: DatasetPreview } | null>(null), [saved, setSaved] = useState<Publication | null>(null);
  const pending = useRef({ body: "", key: "" });
  const body = { collection_revision: collection.revision, name, note, labels: labels.split(",").map(v => v.trim()).filter(Boolean), decisions };
  const signature = JSON.stringify(body), current = preview?.signature === signature ? preview.data : null;
  const patch = (i: number, d: Decision) => setDecisions(old => old.map((v,n) => n === i ? d : v));
  async function inspect() {
    setBusy(true); setError(""); setPreview(null);
    try { setPreview({ signature, data: await api(`/collections/${collection.id}/datasets/preview`, { method: "POST", body: signature }) }); }
    catch (e) { setError(e instanceof Error ? e.message : "Dataset preview failed"); } finally { setBusy(false); }
  }
  async function publish() {
    if (!current) return; setBusy(true); setError("");
    const request = JSON.stringify({ ...body, expected_preview_hash: current.preview_hash });
    if (pending.current.body !== request) pending.current = { body: request, key: crypto.randomUUID() };
    try { setSaved(await api(`/collections/${collection.id}/datasets`, { method: "POST", body: request, headers: { "Idempotency-Key": pending.current.key } })); onSaved(); }
    catch (e) { setError(e instanceof Error ? e.message : "Dataset publication failed"); } finally { setBusy(false); }
  }
  return <div className="collection-curation"><div className="case-form">
    <label>Dataset name<input aria-label="Collection dataset name" disabled={busy} value={name} maxLength={180} onChange={e => setName(e.target.value)} /></label>
    <label>Dataset labels (comma separated)<input aria-label="Collection dataset labels" disabled={busy} value={labels} onChange={e => setLabels(e.target.value)} /></label>
    <label>Curation purpose<textarea aria-label="Collection dataset note" disabled={busy} value={note} maxLength={1500} onChange={e => setNote(e.target.value)} /></label></div>
    <p>Every source requires a decision. Selecting no label option leaves the image unreviewed, not a reviewed negative. Historical review declarations are copied only when explicitly selected.</p>
    <div className="case-toolbar"><label>Bulk split<select aria-label="Assign collection split" disabled={busy} value={bulkSplit} onChange={e => setBulkSplit(e.target.value)}>
      {["unspecified","train","validation","test"].map(k => <option key={k}>{k}</option>)}</select></label>
      <button disabled={busy} onClick={() => setDecisions(old => old.map(d => d.action === "include" ? { ...d, split: bulkSplit } : d))}>Apply split to included images</button>
      <button disabled={busy} onClick={() => setDecisions(old => old.map((d,i) => {
        const options = rows[i].label_options.filter(o => o.member.image_id === d.image_id);
        return d.action === "include" ? { ...d, label_option_id: options.length === 1 ? options[0].id : null } : d;
      }))}>Use unambiguous dataset labels</button></div>
    <p>All images from one recording must stay in one assigned split. Identical PNGs cannot cross assigned splits even across recordings. Near-duplicate leakage is not automatically solved.</p>
    <div className="case-table-scroll"><table data-testid="collection-decisions"><thead><tr><th>Original message</th><th>Include</th><th>Image interpretation</th><th>Annotation source</th><th>Split</th><th>Exclusion / selection note</th></tr></thead>
      <tbody>{rows.map((r,i) => { const d = decisions[i]; return <tr key={r.key}>
        <td><a href={originalHref(r)}>Channel {r.source.channel_id} · ordinal {r.source.ordinal}</a><small>{r.source.recording_id}</small></td>
        <td><input type="checkbox" aria-label={`Include source ${i+1}`} disabled={busy} checked={d.action === "include"} onChange={e => patch(i,e.target.checked ? { key:r.key, action:"include", image_id:r.images[0]?.id, label_option_id:null, split:"unspecified" } : { key:r.key, action:"exclude", reason:"" })} /></td>
        <td>{d.action === "include" ? <select aria-label={`Source ${i+1} captured image`} value={d.image_id ?? ""} disabled={busy} onChange={e => patch(i,{ ...d, image_id:e.target.value || undefined, label_option_id:null })}>
          <option value="">Choose captured image</option>{r.images.map(a => <option value={a.id} key={a.id}>{a.input.decoder_id}/{a.input.decoder_version} · {a.image.width}×{a.image.height} · {a.id}</option>)}</select> : r.image_state.replaceAll("_"," ")}</td>
        <td>{d.action === "include" && <select aria-label={`Source ${i+1} labels`} disabled={busy} value={d.label_option_id ?? ""} onChange={e => patch(i,{...d,label_option_id:e.target.value || null})}>
          <option value="">No labels · unreviewed</option>{r.label_options.filter(o => o.member.image_id === d.image_id).map(o => <option key={o.id} value={o.id}>{o.dataset_name} r{o.dataset_revision} · {o.member.review_status} · {o.member.annotations.length} boxes</option>)}</select>}</td>
        <td>{d.action === "include" && <select aria-label={`Source ${i+1} target split`} disabled={busy} value={d.split} onChange={e => patch(i,{...d,split:e.target.value})}>{["unspecified","train","validation","test"].map(k => <option key={k}>{k}</option>)}</select>}</td>
        <td><input aria-label={`Source ${i+1} decision note`} disabled={busy} value={d.reason ?? ""} maxLength={1500} onChange={e => patch(i,{...d,reason:e.target.value})} placeholder={d.action === "exclude" ? "Required exclusion reason" : "Optional selection note"} /></td>
      </tr>; })}</tbody></table></div>
    <button className="button secondary" disabled={busy || !name.trim() || !note.trim() || !labels.trim()} onClick={inspect}>Preview frozen dataset</button>
    {current && <div data-testid="collection-dataset-preview"><h3>3 · Publish the reviewed selection</h3>
      <p>{current.denominator.included} included + {current.denominator.excluded} excluded = {current.denominator.collection_messages} original messages</p>
      <p>{current.stats.reviewed} reviewed · {current.stats.unreviewed} unreviewed · {current.stats.negative_images} reviewed negatives · {current.stats.annotations} annotations</p>
      <p>Selected original messages and PNG files were checked. No inference, training or evaluation is started.</p>
      <button className="button primary" disabled={busy} onClick={publish}>Publish frozen dataset</button></div>}
    {error && <p role="alert">{error}</p>}
    {saved && <p data-testid="collection-published">Dataset published with a complete curation receipt. <a href={datasetHref(saved.dataset.id,saved.dataset.revision)}>Open newly published dataset</a></p>}
  </div>;
}
