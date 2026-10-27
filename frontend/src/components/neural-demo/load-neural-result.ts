import { verifiedArtifactBytes } from "../../pages/CarlaCameraFrame";
import { verifiedReceipt } from "../object-scene/load-object-scene";
import { locateZipDirectory, parseZipDirectory, readStoredMember } from "../../pages/carla-zip";
import type { CarlaZipMember } from "../../pages/carla-zip";
import type { NeuralExecution } from "./demo-contract";
import { assertNeuralReceipt, parseNeuralResult } from "./neural-result";

export async function loadNeuralResult(execution: NeuralExecution, signal: AbortSignal) {
  function artifact(name: string, type: string) {
    const matches = execution.artifacts.filter(item => item.name === name);
    if (matches.length !== 1 || matches[0].media_type !== type) throw new Error(`Neural Sim: ${name} 원본이 없거나 중복되었습니다.`);
    return matches[0];
  }
  const resultArtifact = artifact("neural-render-result.json", "application/json"), archive = artifact("neural-render-frames.zip", "application/zip");
  const [rawReceipt, bytes] = await Promise.all([verifiedReceipt(execution, signal), verifiedArtifactBytes(execution.id, resultArtifact, 8 * 1024 * 1024, signal)]);
  const sealedSummary = assertNeuralReceipt(rawReceipt, execution);
  const result = parseNeuralResult(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)), { ...execution, summary: sealedSummary as Record<string, unknown> }, archive);
  // The artifact server verifies a full file hash for each request. Load the bounded
  // archive once so 10 Hz playback does not rehash hundreds of MB per PNG range.
  const archiveBytes = await verifiedArtifactBytes(execution.id, archive, 512 * 1024 * 1024, signal);
  const location = locateZipDirectory(archiveBytes.subarray(Math.max(0, archive.size_bytes - 65557)), archive.size_bytes);
  const entries = parseZipDirectory(archiveBytes.subarray(location.offset, location.offset + location.size), location);
  const members = result.frames.flatMap(frame => [frame.original, frame.baseline, frame.novel]);
  if (entries.size !== members.length || members.some(member => entries.get(member.name)?.size !== member.size_bytes)) throw new Error("Neural Sim: 영상 묶음의 파일 목록이 렌더 결과와 다릅니다.");
  const allowed = new Map(members.map(member => [member.name, member]));
  async function read(member: CarlaZipMember, frameSignal: AbortSignal) {
    const entry = entries.get(member.name);
    if (!entry || allowed.get(member.name)?.sha256 !== member.sha256) throw new Error("Neural Sim: 등록되지 않은 영상 프레임입니다.");
    if (frameSignal.aborted) throw new DOMException("Aborted", "AbortError");
    const count = Math.min(entry.size + 4096, location.offset - entry.offset), bytes = readStoredMember(archiveBytes.subarray(entry.offset, entry.offset + count), entry);
    const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new Uint8Array(bytes)))).map(value => value.toString(16).padStart(2, "0")).join("");
    if (hash !== member.sha256) throw new Error("Neural Sim: 영상 프레임 SHA-256이 저장 결과와 다릅니다.");
    return bytes;
  }
  return { result, read };
}
export type LoadedNeuralResult = Awaited<ReturnType<typeof loadNeuralResult>>;
