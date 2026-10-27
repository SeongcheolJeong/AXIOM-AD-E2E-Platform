import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { DownloadLink } from "../components/ArtifactAccess";
import { PageHeading, ErrorState, Loading } from "../components/ui";
import { useSensorResource } from "../components/sensors/SensorData";
import { useDatasetImage } from "../components/datasets/useDatasetImage";
import type { DatasetImage } from "../components/datasets/DatasetTypes";
import "../cases.css";
import "../scene-discovery.css";

type Pin = { id: string; revision: number; manifest_hash: string };
type ProposalTag = { tag: string; metric: string; value: number; operator: string; threshold: number };
type Review = { source_key: string; accepted_feature_tags: string[]; lighting: string; weather: string; status: string; reviewer: string; note: string };
type Declaration = { review: Review; declared_at: string; reviewer_is_authenticated: false };
type SourceRow = { key: string; source: { recording_id: string; channel_id: number; ordinal: number; log_time_ns: string };
  image: DatasetImage | null; status: string; reason: string | null;
  features: { metrics: Record<string, number>; proposals: ProposalTag[]; pixel_identity: string } | null;
  label_declarations: { dataset_id: string; dataset_revision: number; review_status: string; label_counts: Record<string, number> }[] };
type ResultRow = SourceRow & { review: Declaration | null; review_status: string; distance: number | null;
  effective_conditions: { lighting: string; weather: string }; matched: boolean; filter_reasons: string[]; identical_pixels_to_query: boolean };
type IndexDetail = { id: string; manifest_hash: string; download_url: string; rows: SourceRow[]; counts: Record<string, number>;
  proposal: { collection: Pin; request: { name: string; note: string }; limitations: string[]; recipe: { id: string } } };
type QueryValue = { review_revision: number; anchor_key: string | null; feature_tag: string | null; feature_scope: string;
  lighting: string; weather: string; review_status: string; exclude_same_recording: boolean; exclude_exact_pixels: boolean };
type SearchResult = { index_id: string; manifest_hash: string; download_url: string; query: QueryValue; rows: ResultRow[];
  denominator: Record<string, number>; duplicate_pixel_groups: string[][]; review_snapshot: { revision: number } };
type Receipt = { id: string; index_id: string; download_url: string; collection: Pin; selected_keys: string[];
  not_selected_keys: string[]; search: { query: QueryValue; denominator: Record<string, number>; source_collection: Pin } };
type Preview = { preview_hash: string; workload: Record<string, number> };
const featureNames: Record<string, string> = { dark_image: "Dark image", bright_image: "Bright image", low_contrast: "Low contrast", high_contrast: "High contrast", highlight_rich: "Many bright pixels" };
const sceneHref = (id: string) => `#scene-discovery?index=${encodeURIComponent(id)}`;
const collectionHref = (p: Pin) => `#collections?${new URLSearchParams({ collection: p.id, revision: String(p.revision) })}`;
const originalHref = (row: SourceRow) => `#catalog?${new URLSearchParams({ recording: row.source.recording_id,
  channel: String(row.source.channel_id), ordinal: String(row.source.ordinal), at: row.source.log_time_ns })}`;

function SavedImage({ asset }: { asset: DatasetImage }) {
  const loaded = useDatasetImage(asset.image);
  return <div className="scene-preview">{loaded.url ? <img src={loaded.url} alt={`Original captured image, ordinal ${asset.input.ordinal}`} />
    : loaded.error ? <p role="alert">{loaded.error}</p> : <p>Loading verified image…</p>}</div>;
}

export function SceneSelectionOrigin({ collectionId }: { collectionId: string }) {
  const value = useSensorResource<{ items: Receipt[] }>(`/scene-selections?collection_id=${encodeURIComponent(collectionId)}`);
  if (value.error) return <ErrorState message={value.error} retry={value.reload} />;
  if (!value.data?.items.length) return null;
  return <section className="panel case-section"><h2>Scene search selection origin</h2>
    {value.data.items.map(r => <article key={r.id}><p>{r.selected_keys.length} chosen from {r.search.denominator.all_sources} original sources.
      All other sources, filters, ranking and tag-review r{r.search.query.review_revision} are retained in the receipt.</p>
      <p>No labels were copied or approved. Similarity does not establish independent scenes.</p>
      <div className="case-toolbar"><a href={sceneHref(r.index_id)}>Open source scene index</a>
        <a href={collectionHref(r.search.source_collection)}>Open original collection</a>
        <DownloadLink href={r.download_url} download>Download scene selection receipt</DownloadLink></div></article>)}
  </section>;
}

export default function SceneDiscovery() {
  const params = new URLSearchParams(location.hash.split("?")[1] ?? ""), indexId = params.get("index");
  const [offset, setOffset] = useState(0);
  const library = useSensorResource<{ items: { id: string; name: string; collection: Pin; counts: Record<string, number> }[]; next_offset: number | null }>(`/scene-indexes?limit=25&offset=${offset}`);
  const index = useSensorResource<IndexDetail>(indexId ? `/scene-indexes/${encodeURIComponent(indexId)}` : null);
  return <div className="cases-page scene-page"><PageHeading eyebrow="DISCOVER → REVIEW → REUSE" title="Scene discovery"
    description="Propose image-appearance tags, review scene declarations and find visually similar original images across a fixed collection." />
    <aside className="scene-boundary"><strong>Appearance search · not semantic AI</strong>
      <p>Automatic tags describe RGB pixel statistics. Darkness does not prove night; bright pixels do not prove sensor clipping. Lighting and weather are declared by a reviewer. No models or data are downloaded or sent outside this workspace.</p></aside>
    <div className="case-toolbar"><a className="button primary" href="#scene-discovery">New scene index</a><button onClick={() => { library.reload(); index.reload(); }}>Refresh indexes</button></div>
    {library.error && <ErrorState message={library.error} retry={library.reload} />}
    {library.data && <details className="panel case-section" open={!indexId}><summary>Saved scene indexes</summary>
      <div className="scene-index-library">{library.data.items.map(i => <a key={i.id} className="case-card" href={sceneHref(i.id)}><strong>{i.name}</strong><span>{i.counts.indexed ?? 0} indexed images · collection r{i.collection.revision}</span></a>)}</div>
      <button disabled={!offset} onClick={() => setOffset(Math.max(0, offset-25))}>Previous indexes</button>
      <button disabled={library.data.next_offset === null} onClick={() => setOffset(library.data!.next_offset!)}>More indexes</button></details>}
    {!indexId && <IndexForm initialId={params.get("source_collection") ?? ""} initialRevision={Number(params.get("source_revision") ?? 1)} />}
    {index.loading && indexId && <Loading />}{index.error && <ErrorState message={index.error} retry={index.reload} />}
    {index.data && <Explorer key={index.data.id} index={index.data} />}
  </div>;
}

function IndexForm({ initialId, initialRevision }: { initialId: string; initialRevision: number }) {
  const [collection, setCollection] = useState(initialId), [revision, setRevision] = useState(initialRevision);
  const [name, setName] = useState("Scene appearance index"), [note, setNote] = useState("");
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [consent, setConsent] = useState(false);
  const [preview, setPreview] = useState<{ signature: string; data: Preview } | null>(null);
  const pending = useRef({ body: "", key: "" });
  const library = useSensorResource<{ items: { id: string; revision: number; name: string }[] }>("/collections?limit=100");
  const definition = { collection_id: collection, collection_revision: revision, name, note }, signature = JSON.stringify(definition);
  const current = preview?.signature === signature ? preview.data : null;
  async function run(create: boolean) {
    setBusy(true); setError("");
    try {
      if (!create) { setPreview(null); setConsent(false); setPreview({ signature, data: await api("/scene-indexes/preview", { method: "POST", body: signature }) }); }
      else if (current) {
        const body = JSON.stringify({ ...definition, expected_preview_hash: current.preview_hash, confirm_local_analysis: consent });
        if (pending.current.body !== body) pending.current = { body, key: crypto.randomUUID() };
        const saved = await api<IndexDetail>("/scene-indexes", { method: "POST", body, headers: { "Idempotency-Key": pending.current.key } });
        location.hash = sceneHref(saved.id);
      }
    } catch (e) { setError(e instanceof Error ? e.message : "Scene analysis failed"); } finally { setBusy(false); }
  }
  return <section className="panel case-section"><h2>Index a fixed collection</h2><div className="case-form">
    <label>Collection ID<input aria-label="Scene source collection" list="scene-collections" value={collection} disabled={busy} onChange={e => { setCollection(e.target.value); setConsent(false); }} /></label>
    <datalist id="scene-collections">{library.data?.items.map(c => <option key={c.id} value={c.id}>{c.name} · r{c.revision}</option>)}</datalist>
    <label>Exact revision<input aria-label="Scene source revision" type="number" min={1} step={1} value={revision} disabled={busy} onChange={e => { setRevision(Number(e.target.value)); setConsent(false); }} /></label>
    <label>Index name<input aria-label="Scene index name" value={name} maxLength={180} disabled={busy} onChange={e => setName(e.target.value)} /></label>
    <label>Purpose<textarea aria-label="Scene index purpose" value={note} maxLength={1500} disabled={busy} onChange={e => setNote(e.target.value)} /></label></div>
    {library.error && <ErrorState message={library.error} retry={library.reload} />}
    <p>Uncaptured or ambiguous sources stay in the index as unindexed. Analysis checks original messages and saved PNGs; neither collection membership nor labels change.</p>
    <button className="button secondary" disabled={busy || !collection || !note.trim()} onClick={() => run(false)}>Preview scene workload</button>
    {current && <div data-testid="scene-workload"><p>{current.workload.source_messages} original messages · {current.workload.candidate_images} candidate images · {current.workload.unindexed_sources} unindexed sources.</p>
      <p>{current.workload.total_pixels.toLocaleString()} RGB pixels; bounded local CPU analysis, not a model inference job.</p>
      <label className="scene-check"><input aria-label="Confirm local scene analysis" type="checkbox" checked={consent} disabled={busy} onChange={e => setConsent(e.target.checked)} />Analyze these images and save a fixed appearance index.</label>
      <button className="button primary" disabled={busy || !consent} onClick={() => run(true)}>{busy ? "Analyzing images…" : "Build scene index"}</button></div>}
    {error && <p role="alert">{error}</p>}
  </section>;
}

function Explorer({ index }: { index: IndexDetail }) {
  const [anchor, setAnchor] = useState(""), [tag, setTag] = useState(""), [scope, setScope] = useState("proposed");
  const [lighting, setLighting] = useState("all"), [weather, setWeather] = useState("all"), [reviewState, setReviewState] = useState("all");
  const [sameRecording, setSameRecording] = useState(false), [samePixels, setSamePixels] = useState(false);
  const [result, setResult] = useState<SearchResult | null>(null), [error, setError] = useState(""), [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<string[]>([]), [editing, setEditing] = useState<string | null>(null), [showAll, setShowAll] = useState(false), [page, setPage] = useState(0);
  const reviews = useSensorResource<{ revision: number }>(`/scene-indexes/${index.id}/reviews`);
  const [refresh, setRefresh] = useState(0);
  const query: QueryValue = { review_revision: reviews.data?.revision ?? 0, anchor_key: anchor || null, feature_tag: tag || null,
    feature_scope: scope, lighting, weather, review_status: reviewState,
    exclude_same_recording: !!anchor && sameRecording, exclude_exact_pixels: !!anchor && samePixels };
  const signature = JSON.stringify(query), ready = !!reviews.data && !reviews.error;
  useEffect(() => {
    if (!ready) return;
    let active = true; setResult(null); setError(""); setLoading(true); setSelected([]); setPage(0);
    api<SearchResult>(`/scene-indexes/${index.id}/search`, { method: "POST", body: signature })
      .then(value => { if (active) setResult(value); }).catch(e => { if (active) setError(String(e.message ?? e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [index.id, signature, ready, refresh]);
  const rows = (result?.rows ?? []).filter(r => showAll || r.matched), visible = rows.slice(page*12, page*12+12);
  const editingRow = result?.rows.find(r => r.key === editing);
  return <div data-testid="scene-index-detail"><section className="panel case-section"><h2>{index.proposal.request.name}</h2>
    <code data-testid="scene-index-id">{index.id}</code><p>{index.proposal.request.note}</p>
    <div className="case-toolbar"><a href={collectionHref(index.proposal.collection)}>Open indexed collection revision</a>
      <DownloadLink href={index.download_url} download>Download original scene index</DownloadLink></div>
    <p>Recipe: {index.proposal.recipe.id}. No source image, label or earlier tag review is overwritten.</p></section>
    <section className="panel case-section"><h2>Find and review scenes</h2><div className="scene-filters">
      <label>Query image<select aria-label="Scene query image" value={anchor} onChange={e => setAnchor(e.target.value)}><option value="">Browse without similarity ranking</option>
        {index.rows.filter(r => r.status === "indexed").map(r => <option key={r.key} value={r.key}>#{r.source.ordinal} · {r.source.recording_id} · channel {r.source.channel_id}</option>)}</select></label>
      <label>Image feature<select aria-label="Scene feature filter" value={tag} onChange={e => setTag(e.target.value)}><option value="">All image features</option>{Object.entries(featureNames).map(([k,v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      <label>Feature evidence<select aria-label="Scene feature evidence" value={scope} onChange={e => setScope(e.target.value)}><option value="proposed">Automatic numeric proposals</option><option value="reviewed">Accepted in a completed review</option></select></label>
      <label>Lighting declaration<select aria-label="Scene lighting filter" value={lighting} onChange={e => setLighting(e.target.value)}>{["all","unknown","day","night","twilight"].map(v => <option key={v}>{v}</option>)}</select></label>
      <label>Weather declaration<select aria-label="Scene weather filter" value={weather} onChange={e => setWeather(e.target.value)}>{["all","unknown","dry","rain","snow","fog"].map(v => <option key={v}>{v}</option>)}</select></label>
      <label>Review state<select aria-label="Scene review filter" value={reviewState} onChange={e => setReviewState(e.target.value)}>{["all","pending","draft","reviewed"].map(v => <option key={v}>{v}</option>)}</select></label>
    </div><div className="case-toolbar">
      <label className="scene-check"><input type="checkbox" aria-label="Exclude identical pixels" disabled={!anchor} checked={samePixels} onChange={e => setSamePixels(e.target.checked)} />Exclude identical RGB pixels</label>
      <label className="scene-check"><input type="checkbox" aria-label="Exclude same recording" disabled={!anchor} checked={sameRecording} onChange={e => setSameRecording(e.target.checked)} />Other recordings only</label>
      <label className="scene-check"><input type="checkbox" aria-label="Show all scene sources" checked={showAll} onChange={e => { setShowAll(e.target.checked); setPage(0); }} />Show filtered and unavailable sources too</label></div>
    <p>Lighting/weather filters use reviewed human declarations only. Drafts remain unknown. Lower visual distance means closer color/layout, not the same object, condition or failure.</p>
    {reviews.error && <ErrorState message={reviews.error} retry={reviews.reload} />}
    {loading && <Loading label="Searching fixed visual features…" />}{error && <ErrorState message={error} retry={() => setRefresh(v => v+1)} />}
    {result && ready && <><div className="condition-stats" data-testid="scene-summary">{Object.entries(result.denominator).map(([k,v]) => <div key={k}><strong data-testid={`scene-count-${k}`}>{v}</strong><span>{k.replaceAll("_"," ")}</span></div>)}</div>
      <p>Identical-pixel groups: <strong data-testid="scene-duplicate-groups">{result.duplicate_pixel_groups.length}</strong> · Tag-review revision <strong data-testid="scene-review-revision">{result.query.review_revision}</strong>. Adjacent frames are not counted as independent scenes.</p>
      <DownloadLink href={result.download_url} download>Download full scene search JSON</DownloadLink>
      <p>The download retains all {result.denominator.all_sources} originals, including filtered, ambiguous and unavailable rows.</p>
      {editingRow && <ReviewEditor key={`${editingRow.key}/${result.query.review_revision}`} index={index} row={editingRow} revision={result.query.review_revision}
        onSaved={() => { setEditing(null); reviews.reload(); }} onClose={() => setEditing(null)} />}
      <div className="scene-results" data-testid="scene-results">{visible.map(row => <article className={`scene-card ${row.matched ? "" : "filtered"}`} key={row.key} data-testid="scene-card" data-source-key={row.key}>
        {row.image && row.status === "indexed" ? <SavedImage asset={row.image} /> : <div className="scene-preview"><p>{row.status.replaceAll("_"," ")}</p></div>}
        <h3>Channel {row.source.channel_id} · ordinal {row.source.ordinal}</h3><small>{row.source.recording_id}</small>
        <p>State: {row.status} · Review: {row.review_status}</p>{row.reason && <p>{row.reason}</p>}
        {row.distance !== null && <p>Visual distance: <strong data-testid="scene-distance">{row.distance.toFixed(6)}</strong>{row.identical_pixels_to_query && " · identical pixels"}</p>}
        <div className="scene-tags">{row.features?.proposals.map(p => <span key={p.tag} title={`${p.metric}: ${p.value} ${p.operator} ${p.threshold}`}>{featureNames[p.tag] ?? p.tag} · proposed</span>)}</div>
        <p>Reviewed conditions: {row.effective_conditions.lighting} / {row.effective_conditions.weather}</p>
        {!!row.filter_reasons.length && <p>Not a match: {row.filter_reasons.join(", ")}</p>}
        <div className="case-toolbar"><a href={originalHref(row)}>Inspect original</a><button disabled={row.status !== "indexed"} onClick={() => setEditing(row.key)}>Review scene tags</button>
          <button disabled={row.status !== "indexed"} onClick={() => setAnchor(row.key)}>Find similar images</button></div>
        <label className="scene-check"><input type="checkbox" aria-label={`Select scene result ${row.key}`} disabled={!row.matched || row.status !== "indexed" || (!selected.includes(row.key) && selected.length >= 32)} checked={selected.includes(row.key)}
          onChange={e => setSelected(old => e.target.checked ? [...old,row.key] : old.filter(k => k !== row.key))} />Add to selection</label>
      </article>)}</div>
      {!rows.length && <p>No matching sources. Broaden the filters; unknown or unindexed inputs are still retained in the full report.</p>}
      <div className="case-toolbar"><span>Showing {visible.length} of {rows.length} {showAll ? "total" : "matching"} sources</span><button disabled={!page} onClick={() => setPage(p => p-1)}>Previous images</button>
        <button disabled={(page+1)*12 >= rows.length} onClick={() => setPage(p => p+1)}>More images</button></div>
      <SelectionPublisher key={result.manifest_hash} indexId={index.id} result={result} selected={selected} />
    </>}</section></div>;
}

function ReviewEditor({ index, row, revision, onSaved, onClose }: { index: IndexDetail; row: ResultRow; revision: number; onSaved: () => void; onClose: () => void }) {
  const [form, setForm] = useState<Review>(row.review?.review ?? { source_key: row.key, accepted_feature_tags: [], lighting: "unknown", weather: "unknown", status: "draft", reviewer: "", note: "" });
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const pending = useRef({ body: "", key: "" });
  async function save() {
    setBusy(true); setError(""); const body = JSON.stringify({ ...form, expected_review_revision: revision });
    if (pending.current.body !== body) pending.current = { body, key: crypto.randomUUID() };
    try { await api(`/scene-indexes/${index.id}/reviews`, { method: "POST", body, headers: { "Idempotency-Key": pending.current.key } }); onSaved(); }
    catch (e) { setError(e instanceof Error ? e.message : "Review failed"); } finally { setBusy(false); }
  }
  return <section className="scene-review" data-testid="scene-review-editor"><h3>Review ordinal {row.source.ordinal}</h3>
    {row.image && <SavedImage asset={row.image} />}
    <p>Keep unknown when the image does not establish the condition. Review is a declared observation, not an authenticated approval or measurement.</p>
    <div className="scene-tags">{row.features?.proposals.map(p => <label className="scene-check" key={p.tag}>
      <input type="checkbox" aria-label={`Accept scene tag ${p.tag}`} disabled={busy} checked={form.accepted_feature_tags.includes(p.tag)} onChange={e => setForm(v => ({ ...v,
        accepted_feature_tags: e.target.checked ? [...v.accepted_feature_tags,p.tag] : v.accepted_feature_tags.filter(t => t !== p.tag) }))} />
      {featureNames[p.tag]}: {p.metric}={p.value.toFixed(4)} ({p.operator} {p.threshold})</label>)}</div>
    <div className="scene-filters"><label>Lighting<select aria-label="Declare scene lighting" disabled={busy} value={form.lighting} onChange={e => setForm(v => ({...v,lighting:e.target.value}))}>{["unknown","day","night","twilight"].map(v => <option key={v}>{v}</option>)}</select></label>
      <label>Weather<select aria-label="Declare scene weather" disabled={busy} value={form.weather} onChange={e => setForm(v => ({...v,weather:e.target.value}))}>{["unknown","dry","rain","snow","fog"].map(v => <option key={v}>{v}</option>)}</select></label>
      <label>Status<select aria-label="Scene declaration status" disabled={busy} value={form.status} onChange={e => setForm(v => ({...v,status:e.target.value}))}><option value="draft">draft</option><option value="reviewed">reviewed</option></select></label>
      <label>Reviewer<input aria-label="Scene reviewer" disabled={busy} maxLength={180} value={form.reviewer} onChange={e => setForm(v => ({...v,reviewer:e.target.value}))} /></label>
      <label>Evidence note<textarea aria-label="Scene review note" disabled={busy} maxLength={1500} value={form.note} onChange={e => setForm(v => ({...v,note:e.target.value}))} /></label></div>
    {!!row.label_declarations.length && <details><summary>Existing label context (not a new prediction)</summary>{row.label_declarations.map((d,i) => <p key={i}>{d.dataset_id} r{d.dataset_revision} · {d.review_status}: {JSON.stringify(d.label_counts)}</p>)}</details>}
    <div className="case-toolbar"><button className="button primary" disabled={busy || !form.reviewer.trim() || !form.note.trim()} onClick={save}>Save scene tag review</button><button disabled={busy} onClick={onClose}>Close tag review</button></div>
    {error && <p role="alert">{error}</p>}
  </section>;
}

function SelectionPublisher({ indexId, result, selected }: { indexId: string; result: SearchResult; selected: string[] }) {
  const [name, setName] = useState("Scene search selection"), [purpose, setPurpose] = useState("");
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [saved, setSaved] = useState<Receipt | null>(null);
  const pending = useRef({ body: "", key: "" });
  async function publish() {
    setBusy(true); setError("");const body = JSON.stringify({ query:result.query,expected_search_hash:result.manifest_hash,selected_keys:selected,name,purpose });
    if (pending.current.body !== body) pending.current = {body,key:crypto.randomUUID()};
    try { setSaved(await api(`/scene-indexes/${indexId}/collections`, {method:"POST",body,headers:{"Idempotency-Key":pending.current.key}})); }
    catch(e){setError(e instanceof Error ? e.message : "Collection creation failed");}finally{setBusy(false);}
  }
  return <section className="scene-selection"><h3>Reuse {selected.length} selected images</h3>
    <p>The collection receives selected original images only. The receipt keeps all {result.denominator.all_sources} sources, rejected matches, query, distance and tag-review snapshot. Annotation labels are not copied or approved.</p>
    <div className="case-form"><label>Collection name<input aria-label="Scene collection name" value={name} maxLength={180} disabled={busy} onChange={e => setName(e.target.value)} /></label>
      <label>Selection purpose<textarea aria-label="Scene collection purpose" value={purpose} maxLength={1500} disabled={busy} onChange={e => setPurpose(e.target.value)} /></label></div>
    <button className="button primary" disabled={busy || !selected.length || selected.length > 32 || !name.trim() || !purpose.trim()} onClick={publish}>Create collection from scene selection</button>
    {error && <p role="alert">{error}</p>}{saved && <div data-testid="scene-selection-saved"><p>Saved {saved.selected_keys.length} images. The full source denominator remains in the receipt.</p>
      <a href={collectionHref(saved.collection)}>Open selected collection</a>{" · "}<DownloadLink href={saved.download_url} download>Download selection receipt</DownloadLink></div>}
  </section>;
}
