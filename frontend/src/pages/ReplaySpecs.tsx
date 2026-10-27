import { useEffect, useState } from "react";
import { api, useResource } from "../api";
import { PageHeading } from "../components/ui";
import "../expansion.css";
import RecordedSequenceAuthor from "../components/replays/RecordedSequenceAuthor";

type Project = { id: string; name: string };
type Sequence = { id: string; project_id: string; name: string; sha256: string; source_type: string; source_kind: string; frame_count: number };
type Replay = { id: string; name: string; project_id: string; mode: string; fidelity: string; sha256: string; sequence_id: string; request?: Record<string, unknown> };
type Preflight = { executable: boolean; blocking_reasons: string[]; warnings: string[]; frame_count: number; object_steps_available: number; object_steps_unevaluated: number; source_type: string; source_kind: string; execution_fidelity: string };

function referenceExample(project: string) {
  return { name: "Authored lead-vehicle reference", project_id: project, source_type: "authored_reference", world_frame: "map",
    clock_domain: "authored-reference-clock", note: "Authored synthetic reference. Not a measured road log.",
    authored_frames: Array.from({ length: 21 }, (_, index) => {
      const at = String(1_000_000_000 + index * 100_000_000);
      return { at_ns: at, ego: { timestamp_ns: at, frame_id: "map", x_m: index * .8, y_m: 0, yaw_rad: 0,
        speed_mps: 8, length_m: 4.5, width_m: 1.9, source: "authored ego reference" },
        objects: { timestamp_ns: at, frame_id: "map", coordinate_frame: "world", velocity_semantics: "absolute_ground_velocity",
          source: "authored stationary object reference", objects: [{ id: "lead", x_m: 22, y_m: 0, yaw_rad: 0,
            length_m: 4.5, width_m: 1.9, vx_mps: 0, vy_mps: 0 }] } };
    }) };
}

export default function ReplaySpecs() {
  const projects = useResource<{ items: Project[] }>("/projects");
  const identity = useResource<{ permissions: { execute: boolean } }>("/team/whoami");
  const [project, setProject] = useState("default");
  const sequences = useResource<{ items: Sequence[] }>(`/scene-sequences?project_id=${encodeURIComponent(project)}`);
  const replays = useResource<{ items: Replay[] }>(`/replay-specs?project_id=${encodeURIComponent(project)}`);
  const [source, setSource] = useState("authored_reference"), [draft, setDraft] = useState(() => JSON.stringify(referenceExample("default"), null, 2));
  const [sequenceId, setSequenceId] = useState(""), [mode, setMode] = useState("planning_closed_loop"), [name, setName] = useState("Planning replay");
  const [cruise, setCruise] = useState(8), [steering, setSteering] = useState(0), [selected, setSelected] = useState<Replay | null>(null);
  const [preflight, setPreflight] = useState<Preflight | null>(null), [error, setError] = useState(""), [notice, setNotice] = useState(""), [busy, setBusy] = useState(false);
  const canWrite = identity.data?.permissions.execute ?? false;
  const chosenSequence = sequences.data?.items.find((item) => item.id === sequenceId);
  useEffect(() => { if (projects.data?.items.length && !projects.data.items.some((item) => item.id === project)) setProject(projects.data.items[0].id); }, [project, projects.data]);
  useEffect(() => { setSequenceId(""); setSelected(null); setPreflight(null); setDraft(JSON.stringify(referenceExample(project), null, 2)); }, [project]);

  async function perform(action: () => Promise<void>) { setBusy(true); setError(""); setNotice(""); try { await action(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Replay request failed"); } finally { setBusy(false); } }
  async function createSequence() {
    await perform(async () => {
      const body: Record<string, unknown> = JSON.parse(draft);
      if (body.source_type !== source) throw new Error("The selected source type and JSON source_type must match.");
      const result = await api<Sequence>("/scene-sequences", { method: "POST", body: JSON.stringify({ ...body, project_id: project }) });
      sequences.setData((current) => ({ items: [result, ...(current?.items ?? [])] })); setSequenceId(result.id);
      setNotice(`Frozen ${result.frame_count} temporal observations (${result.source_kind}).`);
    });
  }
  async function createReplay() {
    await perform(async () => {
      if (!chosenSequence) throw new Error("Select a frozen temporal sequence first.");
      const result = await api<Replay>("/replay-specs", { method: "POST", body: JSON.stringify({ name, project_id: project,
        sequence_id: chosenSequence.id, sequence_sha256: chosenSequence.sha256, mode,
        controller: { cruise_speed_mps: cruise, steering_bias_rad: steering }, note: "Explicit reference replay; recorded world actors remain nonreactive." }) });
      replays.reload(); setSelected(result); setPreflight(null); setNotice("Replay specification saved. Check retained inputs before creating an experiment.");
    });
  }
  async function inspect(item: Replay) { await perform(async () => { setSelected(await api<Replay>(`/replay-specs/${encodeURIComponent(item.id)}`)); setPreflight(null); }); }

  return <div className="expansion-workspace">
    <PageHeading eyebrow="LOG SIM · TEMPORAL INPUTS" title="Replay specifications" description="Freeze source clocks and exact observations, then choose original replay, open-loop planning or reference vehicle feedback." />
    <div className="expansion-note warning">Planning closed loop updates a reference bicycle ego. Surrounding actors follow retained world tracks; camera pixels stay nonreactive. Authored examples and this reference controller do not establish road-driving safety.</div>
    {error && <div role="alert" className="expansion-error">{error}</div>}
    {(projects.error || sequences.error || replays.error) && <div role="alert" className="expansion-error">{projects.error || sequences.error || replays.error}</div>}
    {notice && <div role="status" className="expansion-success">{notice}</div>}
    <div className="expansion-grid">
      <section className="expansion-panel"><h2>1. Freeze a temporal sequence</h2>
        <label className="expansion-field">Project<select aria-label="Replay project" value={project} onChange={(event) => setProject(event.target.value)}>{projects.data?.items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label className="expansion-field">Source type<select aria-label="Replay source type" value={source} onChange={(event) => { const type = event.target.value; setSource(type); setDraft(JSON.stringify(type === "authored_reference" ? referenceExample(project) : {
          name: "Retained recording interval", project_id: project, source_type: "recording", world_frame: "map", clock_domain: "original-log-clock", note: "Original declared ego/object channels; no inferred pose.",
          recording_id: "", ego_channel_id: 1, object_channel_id: 2, ego_ordinals: [], object_max_age_ns: "100000000" }, null, 2)); }}>
          <option value="authored_reference">Authored synthetic reference</option><option value="recording">Imported MCAP ego/object channels</option></select></label>
        {source === "recording" ? <RecordedSequenceAuthor project={project} canWrite={canWrite} onFrozen={(result) => { sequences.setData((current) => ({ items: [result, ...(current?.items ?? [])] })); setSequenceId(result.id); setNotice(`Frozen ${result.frame_count} inspected original observations (${result.source_kind}).`); }} /> : <>
        <div className="expansion-note">Recorded replay requires explicit <code>axiom.PlanningEgo.v1</code> world poses and <code>axiom.ObjectTracks.v1</code> geometry. Missing source poses, unsupported schemas and stale observations remain visible.</div>
        <label className="expansion-field">Sequence definition JSON<textarea aria-label="Sequence definition JSON" value={draft} onChange={(event) => setDraft(event.target.value)} rows={13} spellCheck={false} /></label>
        <div className="expansion-actions"><button className="button primary" disabled={busy || !canWrite} onClick={createSequence}>Freeze sequence</button>
          <button className="button secondary" onClick={() => { setSource("authored_reference"); setDraft(JSON.stringify(referenceExample(project), null, 2)); }}>Load authored reference</button></div>
        {!canWrite && <p className="expansion-clock">Engineer or Admin access is required to author inputs and submit execution.</p>}
        </>}
      </section>
      <section className="expansion-panel"><h2>2. Pin replay intent</h2><div className="expansion-fields">
        <label className="expansion-field wide">Frozen sequence<select aria-label="Frozen sequence" value={sequenceId} onChange={(event) => setSequenceId(event.target.value)}><option value="">Select a sequence</option>{sequences.data?.items.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.frame_count} frames · {item.source_kind}</option>)}</select></label>
        <label className="expansion-field">Replay name<input value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label className="expansion-field">Mode<select aria-label="Replay mode" value={mode} onChange={(event) => setMode(event.target.value)}><option value="original_replay">Original retained observations</option><option value="open_loop">Open-loop reference planning</option><option value="planning_closed_loop">Planning / control closed loop</option></select></label>
        <label className="expansion-field">Cruise speed · m/s<input type="number" min="0" max="50" step="0.5" value={cruise} onChange={(event) => setCruise(Number(event.target.value))} /></label>
        <label className="expansion-field">Steering bias · rad<input type="number" min="-0.5" max="0.5" step="0.01" value={steering} onChange={(event) => setSteering(Number(event.target.value))} /></label>
      </div>{chosenSequence && <p className="expansion-clock">Source SHA-256: {chosenSequence.sha256}</p>}
        <button className="button primary" disabled={busy || !canWrite || !chosenSequence} onClick={createReplay}>Save replay specification</button>
        <h2>Saved specifications</h2><div className="expansion-list">{replays.data?.items.map((item) => <button key={item.id} className={selected?.id === item.id ? "selected" : ""} onClick={() => inspect(item)}><strong>{item.name}</strong><small>{item.mode} · {item.fidelity}<br />{item.id}</small></button>)}{!replays.loading && !replays.data?.items.length && <p>No replay specifications in this project.</p>}</div>
      </section>
    </div>
    {selected && <section className="expansion-panel"><h2>3. Check inputs and open an experiment</h2><p><strong>{selected.name}</strong> · {selected.mode || String(selected.request?.mode ?? "")} · {selected.fidelity}</p><p className="expansion-clock">Replay SHA-256: {selected.sha256}</p>
      <div className="expansion-actions"><button className="button secondary" disabled={busy} onClick={() => perform(async () => setPreflight(await api<Preflight>(`/replay-specs/${encodeURIComponent(selected.id)}/preflight`, { method: "POST" })))}>Check retained inputs</button>
        <button className="button primary" disabled={!preflight?.executable || !canWrite} onClick={() => { location.hash = `experiments?replay=${encodeURIComponent(selected.id)}&project=${encodeURIComponent(project)}`; }}>Open planning experiment</button></div>
      {preflight && <><div className={`expansion-note${preflight.executable ? "" : " warning"}`}>{preflight.executable ? "Inputs are executable within the declared reference scope." : "Execution blocked: " + preflight.blocking_reasons.join("; ")}</div>
        <div className="expansion-stat-grid"><div className="expansion-stat"><strong>{preflight.frame_count}</strong><span>Selected temporal steps</span></div><div className="expansion-stat"><strong>{preflight.object_steps_available}</strong><span>Causal object observations</span></div><div className="expansion-stat"><strong>{preflight.object_steps_unevaluated}</strong><span>Missing / stale observations</span></div></div>
        {preflight.warnings.map((warning) => <p className="expansion-note warning" key={warning}>{warning}</p>)}</>}
    </section>}
  </div>;
}
