import { authHeaders } from "../../api";
import { verifiedArtifactBytes } from "../../pages/CarlaCameraFrame";
import { createArtifactRangeReader, openCarlaStoredZip } from "../../pages/carla-zip";
import { parseCaptureManifest, parseCaptureReceipt } from "./parser";
import { parseSensorJson } from "./strict-json";
import type { LoadedCapture, SensorStreamExecution } from "./types";

export async function sha256(bytes: Uint8Array) { return [...new Uint8Array(await crypto.subtle.digest("SHA-256", new Uint8Array(bytes)))].map(v => v.toString(16).padStart(2, "0")).join(""); }
export async function loadSensorCaptures(execution: SensorStreamExecution, signal: AbortSignal): Promise<LoadedCapture[]> {
  const driving = execution.adapter_id === "carla-driving-multisensor", jsonLimit = (driving ? 8 : 4) * 1024 * 1024;
  const route = `/api/v1/experiments/executions/${encodeURIComponent(execution.id)}/export`;
  if (execution.download_url !== route || !execution.manifest_hash || !/^[a-f0-9]{64}$/.test(execution.manifest_hash)) throw new Error("Sensor viewer needs the selected immutable execution receipt.");
  const response = await fetch(route, { headers: authHeaders(), signal, redirect: "error" });
  if (!response.ok || !response.body) throw new Error(`Sealed sensor receipt read failed (${response.status}).`);
  const reader = response.body.getReader(), chunks: Uint8Array[] = []; let count = 0;
  try { while (true) { const { value, done } = await reader.read(); if (done) break; count += value.length; if (count > jsonLimit) throw new Error(`Sealed sensor receipt exceeds ${driving ? 8 : 4} MiB.`); chunks.push(value); } }
  catch (error) { await reader.cancel(); throw error; }
  const raw = new Uint8Array(count); let offset = 0; for (const chunk of chunks) { raw.set(chunk, offset); offset += chunk.length; }
  if (await sha256(raw) !== execution.manifest_hash) throw new Error("Sealed sensor receipt SHA-256 mismatch.");
  const decoder = new TextDecoder("utf-8", { fatal: true }), document = parseSensorJson(decoder.decode(raw), driving);
  if (!document.preparedSlice || await sha256(new TextEncoder().encode(document.preparedSlice)) !== execution.input_hash) throw new Error("Frozen sensor input hash differs from the sealed original.");
  const rigHashes: Record<string, string> = {};
  await Promise.all([...document.rigSlices].map(async ([stage, slice]) => { rigHashes[stage] = await sha256(new TextEncoder().encode(slice)); }));
  const pin = parseCaptureReceipt(document.value, execution, rigHashes);
  return await Promise.all(pin.stages.map(async stage => {
    const artifacts = execution.artifacts.filter(a => a.name === `${stage}-capture-manifest.json`);
    if (artifacts.length !== 1 || artifacts[0].media_type !== "application/json") throw new Error("Capture manifest is missing or ambiguous.");
    const bytes = await verifiedArtifactBytes(execution.id, artifacts[0], jsonLimit, signal), evidence = parseCaptureManifest(parseSensorJson(decoder.decode(bytes), driving).value, pin, stage);
    const archive = await openCarlaStoredZip(evidence.archiveArtifact.size_bytes, createArtifactRangeReader(execution.id, evidence.archiveArtifact, authHeaders()), signal);
    if (!archive.assertClosure) throw new Error("Original capture ZIP has no bounded closure verifier.");
    archive.assertClosure([...evidence.members, { name: "capture-manifest.json", size_bytes: bytes.length, sha256: artifacts[0].sha256 }]);
    for (const tick of evidence.ticks) for (const record of tick.records) archive.has(record.member);
    return { ...evidence, archive };
  }));
}
