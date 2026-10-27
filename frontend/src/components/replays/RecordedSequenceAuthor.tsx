import { useEffect, useMemo, useRef, useState } from "react";
import { api, authHeaders, formatBytes, useResource } from "../../api";

type Sequence = { id: string; project_id: string; name: string; sha256: string; source_type: string; source_kind: string; frame_count: number };
type Source = { id: string; name: string; sha256: string; size_bytes: number; download_url: string; source_kind: string; message_count: number; channel_count: number; access_scope: string; source_use_scope: Scope };
type Scope = { use_scope: string; commercial_use: boolean | string; status?: string; source_dataset?: string; limitations?: string[] };
type Channel = { id: number; topic: string; message_encoding: string; schema_id: number; message_count: number };
type Discovery = { recording_sha256: string; channels: { channel_id: number; topic: string; role: string }[] };
type Sample = { ordinal: number; log_time_ns: string; publish_time_ns: string; sha256: string; decoded?: Record<string, unknown> | null };
type Page = { samples: Sample[]; next_after_ordinal: number | null; warnings: string[] };
type Preview = { executable: boolean; snapshot_sha256?: string; frame_count: number; source_kind: string; blocking_reasons: string[]; warnings: string[];
  supported_modes?: Record<string, unknown> | string[]; source_use_scope?: Scope; input_summary?: Record<string, unknown>;
  checks: { id?: string; name?: string; key?: string; status: string; reason?: string; detail?: string }[];
  frames: { index: number; at_ns: string; object_drop?: string | null; object_age_ns?: string | null }[]; next_actions?: unknown[] };

export default function RecordedSequenceAuthor({ project, canWrite, onFrozen }: { project: string; canWrite: boolean; onFrozen: (sequence: Sequence) => void }) {
  const [query, setQuery] = useState(""), [recording, setRecording] = useState("");
  const sources = useResource<{ items: Source[]; has_more: boolean }>(`/project-recordings?project_id=${encodeURIComponent(project)}&limit=100&q=${encodeURIComponent(query)}`);
  const detail = useResource<{ id: string; sha256: string; size_bytes: number; download_url: string; channels: Channel[]; schemas: { id: number; name: string }[]; source_use_scope: Scope; channels_truncated: boolean }>(recording ? `/project-recordings/${encodeURIComponent(recording)}?project_id=${encodeURIComponent(project)}` : null);
  const discovery = useResource<Discovery>(recording ? `/scene-sequences/sources/${encodeURIComponent(recording)}?project_id=${encodeURIComponent(project)}` : null);
  const [egoChannel, setEgoChannel] = useState(""), [objectChannel, setObjectChannel] = useState("");
  const [samples, setSamples] = useState<Sample[]>([]), [selected, setSelected] = useState<number[]>([]), [nextOrdinal, setNextOrdinal] = useState<number | null>(null);
  const [sampleWarnings, setSampleWarnings] = useState<string[]>([]), [name, setName] = useState("Retained road-log interval");
  const [world, setWorld] = useState("map"), [clock, setClock] = useState("original-log-clock"), [egoOffset, setEgoOffset] = useState("0"), [objectOffset, setObjectOffset] = useState("0");
  const [maxAge, setMaxAge] = useState("100000000"), [maxStep, setMaxStep] = useState("1000000000");
  const [note, setNote] = useState("Original retained ego/object observations; explicit clock offsets and geometry provenance.");
  const [preview, setPreview] = useState<Preview | null>(null), [previewSignature, setPreviewSignature] = useState("");
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [busy, setBusy] = useState(false);
  const chosenSource = sources.data?.items.find((item) => item.id === recording);
  const orderedOrdinals = useMemo(() => [...selected].sort((a,b) => {
    const sa = samples.find((sample) => sample.ordinal === a), sb = samples.find((sample) => sample.ordinal === b);
    if (!sa || !sb) return a-b;
    const difference = BigInt(sa.log_time_ns) - BigInt(sb.log_time_ns);
    return difference === 0n ? a-b : difference < 0n ? -1 : 1;
  }), [selected, samples]);
  const definition = { name, project_id: project, source_type: "recording", world_frame: world, coordinate_convention: "right_handed_z_up",
    clock_domain: clock, note, recording_id: recording, ego_channel_id: Number(egoChannel), object_channel_id: Number(objectChannel),
    ego_ordinals: orderedOrdinals, ego_log_time_offset_ns: egoOffset, object_log_time_offset_ns: objectOffset,
    object_max_age_ns: maxAge, max_step_ns: maxStep, sensor_channels: [] };
  const signature = JSON.stringify(definition);
  const sampleContext = useRef(""); sampleContext.current = JSON.stringify([project, recording, egoChannel]);
  const freshPreview = preview && previewSignature === signature;
  useEffect(() => { setRecording(""); setSelected([]); setSamples([]); setPreview(null); }, [project]);
  useEffect(() => { setEgoChannel(""); setObjectChannel(""); setSelected([]); setSamples([]); setNextOrdinal(null); setPreview(null); setError(""); }, [recording]);
  useEffect(() => { setSelected([]); setSamples([]); setNextOrdinal(null); setSampleWarnings([]); }, [egoChannel]);
  useEffect(() => { setPreview(null); setPreviewSignature(""); }, [signature]);
  async function perform(action: () => Promise<void>) { setBusy(true); setError(""); setNotice(""); try { await action(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Recorded input request failed"); } finally { setBusy(false); } }
  async function readSamples(after?: number) { await perform(async () => {
    const context = sampleContext.current;
    const params = new URLSearchParams({ project_id: project, channel_id: egoChannel, limit: "50", ...(after === undefined ? { from_start: "true" } : { after_ordinal: String(after) }) });
    const value = await api<Page>(`/project-recordings/${encodeURIComponent(recording)}/samples?${params}`);
    if (sampleContext.current !== context) return;
    setSamples((current) => after === undefined ? value.samples : [...current, ...value.samples.filter((sample) => !current.some((existing) => existing.ordinal === sample.ordinal))]);
    setNextOrdinal(value.next_after_ordinal); setSampleWarnings(value.warnings);
  }); }
  async function downloadSource() { await perform(async () => {
    const source = detail.data;
    if (!source || source.id !== recording) throw new Error("Select an accessible original source first.");
    const endpoint = `/api/v1/project-recordings/${encodeURIComponent(recording)}/download?project_id=${encodeURIComponent(project)}`;
    if (source.download_url !== endpoint) throw new Error("Original source download must use this project's authenticated API.");
    const maximum = 128 * 1024 * 1024;
    if (!Number.isSafeInteger(source.size_bytes) || source.size_bytes < 1 || source.size_bytes > maximum) throw new Error("This source exceeds the 128 MiB browser profile. Use the SDK's streamed source download.");
    const response = await fetch(endpoint, { headers: authHeaders(), redirect: "error", cache: "no-store" });
    if (!response.ok) throw new Error(`Original source download failed (${response.status}).`);
    if (response.headers.get("X-Content-SHA256") !== source.sha256 || response.headers.get("Content-Length") !== String(source.size_bytes)) throw new Error("Source download headers differ from the pinned SHA-256 or byte size.");
    const reader = response.body?.getReader();
    if (!reader) throw new Error("Original source response has no readable bytes.");
    const chunks: Uint8Array[] = []; let count = 0;
    try {
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        count += value.byteLength;
        if (count > source.size_bytes || count > maximum) throw new Error("Original source grew beyond its pinned byte bound.");
        chunks.push(value);
      }
    } catch (reason) { await reader.cancel(); throw reason; } finally { reader.releaseLock(); }
    if (count !== source.size_bytes) throw new Error("Original source download byte size differs from its pin.");
    const bytes = new Uint8Array(count); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    const actualHash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))).map((value) => value.toString(16).padStart(2, "0")).join("");
    if (actualHash !== source.sha256) throw new Error("Original source download SHA-256 differs from its immutable grant.");
    const objectUrl = URL.createObjectURL(new Blob([bytes], { type: "application/octet-stream" }));
    const anchor = document.createElement("a"); anchor.href = objectUrl; anchor.download = `${source.id}.mcap`; anchor.click(); setTimeout(() => URL.revokeObjectURL(objectUrl), 2000);
    setNotice("Original MCAP downloaded; SHA-256 and byte size verified. Source-use restrictions remain unchanged.");
  }); }
  function toggle(ordinal: number) { setSelected((current) => current.includes(ordinal) ? current.filter((item) => item !== ordinal) : current.length < 512 ? [...current, ordinal] : current); }
  const roles = discovery.data?.channels ?? [];
  const scope = detail.data?.source_use_scope ?? chosenSource?.source_use_scope;
  return <div>
    <div className="expansion-note">Select an operator-granted original source and exact ego samples. Inspection creates no sequence, replay or engine job. Pose and dimensions must exist in the source; this workflow does not infer them.</div>
    {(sources.error || detail.error || discovery.error || error) && <div role="alert" className="expansion-error">{sources.error || detail.error || discovery.error || error}</div>}
    {notice && <div role="status" className="expansion-success">{notice}</div>}
    <label className="expansion-field">Find an approved source<input aria-label="Find granted recording" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Recording name or ID" /></label>
    <label className="expansion-field">Original recording<select aria-label="Granted recording source" value={recording} disabled={busy} onChange={(event) => setRecording(event.target.value)}><option value="">Select an accessible source</option>{sources.data?.items.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.source_kind} · {item.channel_count} channels</option>)}</select></label>
    {!sources.loading && !sources.data?.items.length && <p className="expansion-note">No source grants in this project. The operator must grant a recording and its exact SHA-256. Existing global recordings are not automatically shared with team tokens.</p>}
    {sources.data?.has_more && <p className="expansion-note warning">The catalog is bounded to 100 sources. Refine the search to find another source.</p>}
    {chosenSource && <p className="expansion-clock">Original SHA-256: {chosenSource.sha256} · {chosenSource.access_scope}</p>}
    {detail.data && <div className="expansion-actions"><button className="button secondary" disabled={busy} onClick={downloadSource}>Download original MCAP</button><span>{formatBytes(detail.data.size_bytes)} · authenticated project source</span></div>}
    {scope && <div className={`expansion-note${scope.use_scope === "isolated_noncommercial_research" ? " warning" : ""}`}>Source use: {scope.use_scope}. Commercial use: {String(scope.commercial_use)}.{scope.use_scope === "isolated_noncommercial_research" && " Annotation-derived geometry is research ground truth, not sensor detections. Source grants preserve this restriction."}</div>}
    {detail.data && <><div className="expansion-fields">
      <label className="expansion-field">Ego world-pose channel<select aria-label="Recorded ego channel" value={egoChannel} onChange={(event) => setEgoChannel(event.target.value)}><option value="">Select supported ego schema</option>{roles.filter((item) => item.role === "ego").map((item) => <option key={item.channel_id} value={item.channel_id}>{item.topic} · channel {item.channel_id}</option>)}</select></label>
      <label className="expansion-field">Object geometry channel<select aria-label="Recorded object channel" value={objectChannel} onChange={(event) => setObjectChannel(event.target.value)}><option value="">Select supported object schema</option>{roles.filter((item) => item.role === "objects").map((item) => <option key={item.channel_id} value={item.channel_id}>{item.topic} · channel {item.channel_id}</option>)}</select></label>
    </div><details><summary>Channel contract inventory</summary><table className="expansion-table"><thead><tr><th>Channel</th><th>Schema / encoding</th><th>Current planning support</th></tr></thead><tbody>{detail.data.channels.map((channel) => <tr key={channel.id}><td>{channel.topic} · {channel.id}</td><td>{detail.data?.schemas.find((schema) => schema.id === channel.schema_id)?.name ?? "No declared schema"} / {channel.message_encoding}</td><td>{roles.find((role) => role.channel_id === channel.id)?.role ?? "Unsupported; explicit converter required"}</td></tr>)}</tbody></table></details>
    {detail.data.channels_truncated && <p className="expansion-note warning">The inventory preview shows the first 128 channels. The exact supported-role selector includes all matched channels; the API supports bounded channel pages.</p>}
    <div className="expansion-actions"><button className="button secondary" disabled={busy || !egoChannel} onClick={() => readSamples()}>Read exact ego samples</button><button className="button secondary" disabled={busy || nextOrdinal === null || samples.length >= 512} onClick={() => nextOrdinal !== null && readSamples(nextOrdinal)}>Read next samples</button>
      <button className="button secondary" disabled={busy || !samples.length} onClick={() => setSelected(samples.slice(0,512).map((sample) => sample.ordinal))}>Select loaded samples</button><button className="button secondary" disabled={busy || !selected.length} onClick={() => setSelected([])}>Clear selection</button></div>
    {sampleWarnings.map((warning) => <p key={warning} className="expansion-note warning">{warning}</p>)}
    <p className="expansion-clock">Selected {selected.length}/512 exact observations · loaded {samples.length}{nextOrdinal !== null && " · more source samples remain"}</p>
    {!!samples.length && <div style={{ maxHeight: 260, overflow: "auto" }}><table className="expansion-table"><thead><tr><th>Select</th><th>Original ordinal / clocks</th><th>Source ego geometry</th></tr></thead><tbody>{samples.map((sample) => <tr key={sample.ordinal}><td><input type="checkbox" aria-label={`Select ego ordinal ${sample.ordinal}`} checked={selected.includes(sample.ordinal)} disabled={busy || !selected.includes(sample.ordinal) && selected.length >= 512} onChange={() => toggle(sample.ordinal)} /></td><td>#{sample.ordinal}<br /><small>log {sample.log_time_ns}<br />publish {sample.publish_time_ns}<br />capture {String(sample.decoded?.timestamp_ns ?? "unknown")}</small></td><td>{String(sample.decoded?.frame_id ?? "unknown")} · {String(sample.decoded?.length_m ?? "missing")} × {String(sample.decoded?.width_m ?? "missing")} m<br /><small>x {String(sample.decoded?.x_m ?? "missing")} / y {String(sample.decoded?.y_m ?? "missing")} / yaw {String(sample.decoded?.yaw_rad ?? "missing")}<br />{String(sample.decoded?.source ?? "No source declaration")}</small></td></tr>)}</tbody></table></div>}
    <div className="expansion-fields"><label className="expansion-field wide">Sequence name<input aria-label="Recorded sequence name" value={name} onChange={(event) => setName(event.target.value)} /></label>
      <label className="expansion-field">Declared world frame<input value={world} onChange={(event) => setWorld(event.target.value)} /></label><label className="expansion-field">Declared clock domain<input value={clock} onChange={(event) => setClock(event.target.value)} /></label>
      <label className="expansion-field">Ego log offset · ns<input aria-label="Ego log offset" value={egoOffset} onChange={(event) => setEgoOffset(event.target.value)} /></label><label className="expansion-field">Object log offset · ns<input aria-label="Object log offset" value={objectOffset} onChange={(event) => setObjectOffset(event.target.value)} /></label>
      <label className="expansion-field">Object max age · ns<input value={maxAge} onChange={(event) => setMaxAge(event.target.value)} /></label><label className="expansion-field">Maximum step gap · ns<input value={maxStep} onChange={(event) => setMaxStep(event.target.value)} /></label>
      <label className="expansion-field wide">Source and interpretation note<textarea rows={2} value={note} onChange={(event) => setNote(event.target.value)} /></label></div>
    <div className="expansion-note">This reference planner uses ego/object geometry. Camera calibration and six-camera learned model inputs are separate requirements; retained pixels stay nonreactive. Unsupported formats remain unsupported until a versioned converter is installed.</div>
    <div className="expansion-actions"><button className="button secondary" disabled={busy || !egoChannel || !objectChannel || !selected.length} onClick={() => perform(async () => { const value = await api<Preview>("/scene-sequences/preview", { method: "POST", body: JSON.stringify(definition) }); setPreview(value); setPreviewSignature(signature); })}>Inspect selected inputs</button>
      <button className="button primary" disabled={busy || !canWrite || !freshPreview || !preview?.executable || !preview.snapshot_sha256} onClick={() => perform(async () => { if (!freshPreview || !preview.snapshot_sha256) throw new Error("Inspect the current input selection first."); const sequence = await api<Sequence>("/scene-sequences/freeze", { method: "POST", body: JSON.stringify({ definition, preview_sha256: preview.snapshot_sha256 }) }); onFrozen(sequence); setNotice(`Frozen ${sequence.frame_count} original observations. Select the supported replay mode and continue.`); })}>Freeze inspected sequence</button></div>
    </>}
    {preview && <div aria-label="Recorded source inspection"><div className={`expansion-note${preview.executable ? "" : " warning"}`}>{preview.executable ? "Selected observations are structurally executable in the supported reference scope." : "Execution blocked: " + preview.blocking_reasons.join("; ")}</div>
      <p className="expansion-clock">{preview.frame_count} steps · source {preview.source_kind} · snapshot {preview.snapshot_sha256 ?? "not available"}</p>
      {Array.isArray(preview.supported_modes) && <p className="expansion-note">Supported replay modes: {preview.supported_modes.join(", ") || "none"}. {!preview.supported_modes.includes("planning_closed_loop") && "Planning closed loop is unavailable for this selection."}</p>}
      <table className="expansion-table"><thead><tr><th>Input requirement</th><th>Status</th><th>Evidence / next action</th></tr></thead><tbody>{preview.checks.map((check,index) => <tr key={index}><td>{check.name ?? check.key ?? check.id?.replaceAll("_", " ") ?? `Check ${index+1}`}</td><td>{check.status}</td><td>{check.reason ?? check.detail ?? ""}</td></tr>)}</tbody></table>
      {preview.warnings.map((warning) => <p key={warning} className="expansion-note warning">{warning}</p>)}
      <details><summary>Exact causal object coverage and supported modes</summary><pre>{JSON.stringify({ supported_modes: preview.supported_modes, input_summary: preview.input_summary, frames: preview.frames, next_actions: preview.next_actions },null,2)}</pre></details>
    </div>}
  </div>;
}
