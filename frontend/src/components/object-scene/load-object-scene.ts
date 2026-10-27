import { authHeaders } from "../../api";
import { verifiedArtifactBytes } from "../../pages/CarlaCameraFrame";
import { parseSceneEvidence, record, retainedLogActionEvents, type ObjectArtifact, type ObjectExecution } from "./object-scene";

export async function verifiedReceipt(execution: ObjectExecution, signal: AbortSignal, limit = 4 * 1024 * 1024) {
  const route = `/api/v1/experiments/executions/${encodeURIComponent(execution.id)}/export`;
  if (execution.download_url !== route || !execution.manifest_hash || !/^[a-f0-9]{64}$/.test(execution.manifest_hash)) throw new Error("Object scene needs the selected execution's sealed SHA-256 receipt.");
  const response = await fetch(route, { headers: authHeaders(), signal });
  if (!response.ok || !response.body) throw new Error(`Sealed Object Sim receipt read failed (${response.status}).`);
  const reader = response.body.getReader(), chunks: Uint8Array[] = []; let count = 0;
  try {
    while (true) { const { value, done } = await reader.read(); if (done) break; count += value.length; if (count > limit) throw new Error("Sealed receipt exceeds the bounded viewer profile."); chunks.push(value); }
  } catch (error) { await reader.cancel(); throw error; }
  const raw = new Uint8Array(count); let offset = 0; for (const chunk of chunks) { raw.set(chunk, offset); offset += chunk.length; }
  const sha = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", raw))).map((item) => item.toString(16).padStart(2, "0")).join("");
  if (sha !== execution.manifest_hash) throw new Error("Sealed Object Sim receipt SHA-256 mismatch.");
  return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw)) as unknown;
}
function artifact(execution: ObjectExecution, name: string, required = true): ObjectArtifact | undefined {
  const values = execution.artifacts.filter((item) => item.name === name);
  if (values.length > 1 || required && values.length !== 1) throw new Error(`${name}: retained artifact is missing or ambiguous.`);
  return values[0];
}
export async function loadObjectScene(execution: ObjectExecution, signal: AbortSignal) {
  const trace = artifact(execution, "object-trace.json")!, road = artifact(execution, "road.xodr")!, scene = artifact(execution, "object-scene.json", false), log = artifact(execution, "engine.log", false);
  if (trace.media_type !== "application/json" || scene && scene.media_type !== "application/json" || road.media_type !== "application/xml") throw new Error("Object scene artifacts have an unsupported media type.");
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const loaded = await Promise.all([
    verifiedReceipt(execution, signal),
    verifiedArtifactBytes(execution.id, trace, 16 * 1024 * 1024, signal),
    verifiedArtifactBytes(execution.id, road, 200000, signal),
    scene ? verifiedArtifactBytes(execution.id, scene, 16 * 1024 * 1024, signal) : Promise.resolve(null),
    log ? verifiedArtifactBytes(execution.id, log, 1024 * 1024, signal) : Promise.resolve(null),
  ]);
  const result = parseSceneEvidence(loaded[0], JSON.parse(decoder.decode(loaded[1])), decoder.decode(loaded[2]), execution, loaded[3] ? JSON.parse(decoder.decode(loaded[3])) : undefined);
  const receipt = record(loaded[0], "receipt"), summary = record(record(receipt.execution, "execution").summary, "summary"), engine = record(summary.engine_receipt, "engine receipt");
  if (engine.road_xml_sha256 !== road.sha256 || summary.sample_count !== result.frames.length) throw new Error("Native engine receipt road hash/sample count differs from saved source.");
  const scenarioArtifact = artifact(execution, "scenario.xosc", false), csv = artifact(execution, "native-trace.csv", false);
  if (scenarioArtifact && engine.scenario_xml_sha256 !== scenarioArtifact.sha256 || csv && engine.native_csv_sha256 !== csv.sha256) throw new Error("Native engine receipt source hashes differ from retained files.");
  if (loaded[4]) {
    const lines = decoder.decode(loaded[4]).split(/\r?\n/);
    for (const actor of result.actors) {
      const matches = lines.flatMap((line, index) => { const match = /Add and deactivate new object "([A-Za-z0-9_-]+)" \(id (\d+)\)/.exec(line); return match?.[1] === actor.id ? [{ id: Number(match[2]), line: index + 1 }] : []; });
      if (matches.length === 1) {
        const existing = actor.metadata?.native_entity_id;
        if (existing != null && existing !== matches[0].id) throw new Error("Scene native entity ID differs from retained engine log.");
        actor.metadata = { ...actor.metadata, native_entity_id: matches[0].id, native_entity_id_provenance: { source: "engine.log", sha256: log!.sha256, line_number: matches[0].line } };
      } else if (actor.metadata?.native_entity_id != null) throw new Error("Scene native entity ID has no unique retained engine log source.");
    }
    if (result.provenance === "legacy_verified_derivation") result.events.push(...retainedLogActionEvents(result, decoder.decode(loaded[4]), log!.sha256));
  }
  return result;
}
