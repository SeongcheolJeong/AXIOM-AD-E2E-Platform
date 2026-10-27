import { authHeaders } from "../../api";
import { verifiedArtifactBytes } from "../../pages/CarlaCameraFrame";
import type { FeedbackEvidence } from "../../pages/carla-feedback";
import type { CarlaZipArtifact } from "../../pages/carla-zip";
import { object, parseSensorScene, sameValue, validateMapMembership } from "./sensor-scene";

export async function loadSensorScene(executionId: string, artifacts: CarlaZipArtifact[], manifestHash: string | null | undefined, summary: Record<string, unknown> | null | undefined, manifest: unknown, trace: unknown, evidence: FeedbackEvidence, signal: AbortSignal) {
  const sceneArtifacts = artifacts.filter(a => a.name === "sensor-scene.json"), roads = artifacts.filter(a => a.name === "map.xodr");
  const capture = object(manifest, "capture manifest");
  if (!sceneArtifacts.length && !roads.length && !capture.scene_artifact && !capture.map_artifact && !summary?.scene_artifact && !summary?.map_artifact) return null;
  if (sceneArtifacts.length !== 1 || roads.length !== 1 || sceneArtifacts[0].media_type !== "application/json" || roads[0].media_type !== "application/xml" || !manifestHash || !/^[a-f0-9]{64}$/.test(manifestHash)) throw new Error("Sensor scene: original scene/map or sealed receipt is missing or ambiguous.");
  const sceneArtifact = sceneArtifacts[0], road = roads[0];
  for (const [pointer, artifact] of [[capture.scene_artifact, sceneArtifact], [capture.map_artifact, road]] as const) { const descriptor = object(pointer, "native scene/map original pointer"); if (descriptor.name !== artifact.name || descriptor.sha256 !== artifact.sha256 || descriptor.size_bytes !== artifact.size_bytes || descriptor.media_type !== artifact.media_type) throw new Error("Sensor scene: native scene/map original pointer differs from managed artifact."); }
  if (summary?.scene_artifact !== sceneArtifact.name || summary.scene_sha256 !== sceneArtifact.sha256 || summary.map_artifact !== road.name || summary.map_sha256 !== road.sha256 || summary.scene_geometry_fidelity !== "sampled_native_driving_lane_geometry") throw new Error("Sensor scene: selected execution summary differs from scene originals.");
  const exportRoute = `/api/v1/experiments/executions/${encodeURIComponent(executionId)}/export`;
  const response = await fetch(exportRoute, { headers: authHeaders(), signal });
  if (!response.ok || !response.body) throw new Error(`Sensor scene: sealed receipt read failed (${response.status}).`);
  const reader = response.body.getReader(), chunks: Uint8Array[] = []; let count = 0;
  try { while (true) { const { value, done } = await reader.read(); if (done) break; count += value.length; if (count > 4 * 1024 * 1024) throw new Error("Sensor scene: sealed receipt exceeds 4 MiB bound."); chunks.push(value); } } catch (error) { await reader.cancel(); throw error; }
  const receiptBytes = new Uint8Array(count); let offset = 0; for (const chunk of chunks) { receiptBytes.set(chunk, offset); offset += chunk.length; }
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", receiptBytes))).map(n => n.toString(16).padStart(2, "0")).join("");
  if (digest !== manifestHash) throw new Error("Sensor scene: sealed execution receipt SHA-256 differs.");
  const decoder = new TextDecoder("utf-8", { fatal: true }), receipt = object(JSON.parse(decoder.decode(receiptBytes)), "sealed receipt"), execution = object(receipt.execution, "sealed execution");
  if (receipt.schema_version !== 1 || execution.id !== executionId || execution.experiment_kind !== "sensor_capture" || execution.adapter_id !== "carla-feedback-sensor" || execution.status !== "completed" || execution.fidelity !== "actual_carla_reference_feedback_rgb" || execution.adapter_version !== capture.adapter_version || !sameValue(execution.summary, summary) || !Array.isArray(execution.artifacts)) throw new Error("Sensor scene: sealed execution scope or summary differs.");
  const prepared = object(receipt.prepared, "sealed prepared input"), input = object(prepared.input, "native prepared input"), spec = object(input.spec, "frozen native specification");
  if (input.schema_version !== "carla-feedback-experiment/1" || !sameValue(input.native_capability_snapshot, capture.probe) || !sameValue(spec.controller, capture.controller) || spec.configuration_revision !== evidence.configurationRevision || spec.seed !== evidence.seed) throw new Error("Sensor scene: original frozen input differs from captured source.");
  for (const original of artifacts) { const sealed = execution.artifacts.filter(a => { const row = object(a, "sealed artifact"); return row.id === original.id; }); if (sealed.length !== 1 || !sameValue(sealed[0], original)) throw new Error("Sensor scene: selected artifact differs from sealed source."); }
  if (execution.artifacts.length !== artifacts.length) throw new Error("Sensor scene: selected artifacts are incomplete.");
  const [sceneBytes, mapBytes] = await Promise.all([verifiedArtifactBytes(executionId, sceneArtifact, 4 * 1024 * 1024, signal), verifiedArtifactBytes(executionId, road, 8 * 1024 * 1024, signal)]);
  const parsed = parseSensorScene(JSON.parse(decoder.decode(sceneBytes)), manifest, trace, evidence, artifacts);
  validateMapMembership(decoder.decode(mapBytes), parsed.map.lanes);
  return parsed;
}
