import type { CarlaZipArtifact, CarlaZipMember } from "../../pages/carla-zip";
import type { NeuralExecution, NeuralMode, NeuralParameters } from "./demo-contract";

export type NeuralFrame = { frame_index: number; source_frame: number; time_s: number; source_frame_index?: number; source_time_s?: number; applied_lateral_m?: number; applied_height_m?: number; original: CarlaZipMember; baseline: CarlaZipMember; novel: CarlaZipMember };
export type NeuralResult = NeuralParameters & { schema_version: "neural-render-result/1" | "neural-render-result/2"; source_execution_id: string; reconstruction_execution_id: string; source_manifest_sha256: string; checkpoint_sha256: string; frames_archive_sha256: string; fidelity: string; source_kind: string; source_frame_count?: number; source_fps?: number; recording_id?: string; channel_id?: number; license_scope?: string; external_publication_cleared?: boolean; width: number; height: number; fps: number; mode: NeuralMode; frame_index: number; source_frame_index?: number; source_time_s?: number; frames: NeuralFrame[] };
const fail = (message: string): never => { throw new Error(`Neural Sim: ${message}`); };
function object(value: unknown): Record<string, unknown> { if (!value || typeof value !== "object" || Array.isArray(value)) fail("저장 결과 구조가 올바르지 않습니다."); return value as Record<string, unknown>; }
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const hash = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const id = (value: unknown) => typeof value === "string" && value.length > 0 && value.length <= 180;
export function parseNeuralResult(raw: unknown, execution: NeuralExecution, archive: CarlaZipArtifact): NeuralResult {
  const result = object(raw);
  const sweep = result.mode === "camera_sweep", realRoad = result.source_kind === "real_road";
  const expectedFidelity = realRoad ? "neural_reconstruction_of_real_road" : "neural_reconstruction_of_carla";
  if (execution.experiment_kind !== "neural_render" || execution.adapter_id !== "neural-splatfacto" || execution.status !== "completed" || execution.fidelity !== expectedFidelity || result.schema_version !== (sweep ? "neural-render-result/2" : "neural-render-result/1") || (!realRoad && result.source_kind !== "simulation") || result.fidelity !== execution.fidelity) fail("완료된 원본 로그의 Neural 렌더 결과가 아닙니다.");
  if (!id(result.source_execution_id) || !id(result.reconstruction_execution_id) || !hash(result.source_manifest_sha256) || !hash(result.checkpoint_sha256) || result.frames_archive_sha256 !== archive.sha256) fail("원본 로그·checkpoint·영상 묶음 연결이 유효하지 않습니다.");
  if (!finite(result.lateral_m) || (sweep ? result.lateral_m <= 0 || result.lateral_m > 2 : Math.abs(result.lateral_m) > .5) || !finite(result.height_m) || Math.abs(result.height_m) > .3 || !Number.isSafeInteger(result.width) || !Number.isSafeInteger(result.height) || Number(result.width) < 1 || Number(result.height) < 1 || Number(result.width) * Number(result.height) > 1920 * 1080 || !finite(result.fps) || result.fps <= 0 || result.fps > 60 || !["frame", "sequence", "camera_sweep"].includes(String(result.mode))) fail("영상 크기·시간·카메라 변경값이 지원 범위를 벗어났습니다.");
  if (!Array.isArray(result.frames) || !result.frames.length || result.frames.length > 2000 || result.mode === "frame" && result.frames.length !== 1 || sweep && result.frames.length !== 61) fail("재생 가능한 프레임 구간이 올바르지 않습니다.");
  if (realRoad && (!Number.isSafeInteger(result.source_frame_count) || Number(result.source_frame_count) < 1 || Number(result.source_frame_count) > 2000 || !finite(result.source_fps) || result.source_fps <= 0 || !id(result.recording_id))) fail("실도로 원본 로그의 구간과 recording 연결이 유효하지 않습니다.");
  if (sweep && (!Number.isSafeInteger(result.frame_index) || Number(result.frame_index) < 0 || Number(result.frame_index) >= (realRoad ? Number(result.source_frame_count) : 200) || result.source_frame_index !== result.frame_index || (!finite(result.source_time_s) || result.source_time_s < 0 || !realRoad && result.source_time_s !== Number(result.frame_index) / 10) || result.fps !== 10)) fail("고정할 원본 시각이 유효하지 않습니다.");
  let previous = -1, previousTime = -1;
  const frames = (result.frames as unknown[]).map((value, ordinal) => {
    const frame = object(value), index = frame.frame_index;
    if (!Number.isSafeInteger(index) || Number(index) < 0 || Number(index) <= previous || !Number.isSafeInteger(frame.source_frame) || Number(frame.source_frame) < 0 || !finite(frame.time_s) || frame.time_s < 0 || frame.time_s <= previousTime) fail("원본 프레임과 시간 순서가 보존되지 않았습니다.");
    previous = Number(index); previousTime = Number(frame.time_s);
    if (sweep && (index !== ordinal || frame.time_s !== ordinal / 10 || frame.source_frame_index !== result.frame_index || frame.source_time_s !== result.source_time_s || frame.source_frame !== object((result.frames as unknown[])[0]).source_frame || !finite(frame.applied_lateral_m) || Math.abs(frame.applied_lateral_m + Number(result.lateral_m) * Math.cos(Math.PI * ordinal / 60)) > 1e-6 || frame.applied_height_m !== result.height_m)) fail("카메라 이동 결과의 고정 원본 시각 또는 실제 시점이 다릅니다.");
    for (const side of ["original", "baseline", "novel"] as const) {
      const member = object(frame[side]);
      if (member.name !== `${side}/${String(index).padStart(6, "0")}.png` || !Number.isSafeInteger(member.size_bytes) || Number(member.size_bytes) < 8 || Number(member.size_bytes) > 16 * 1024 * 1024 || !hash(member.sha256)) fail("프레임 파일 경로·크기·hash가 유효하지 않습니다.");
    }
    return frame as unknown as NeuralFrame;
  });
  if (result.mode === "frame" && result.frame_index !== frames[0].frame_index) fail("현재 프레임 요청과 보존 결과가 다릅니다.");
  for (const key of ["source_execution_id", "reconstruction_execution_id", "source_manifest_sha256", "checkpoint_sha256", "frames_archive_sha256", "lateral_m", "height_m", "mode", "source_kind"] as const) if (execution.summary?.[key] !== result[key]) fail(`실행 기록과 렌더 결과의 ${key}가 다릅니다.`);
  if (sweep) for (const key of ["frame_index", "source_frame_index", "source_time_s"] as const) if (execution.summary?.[key] !== result[key]) fail(`실행 기록과 렌더 결과의 ${key}가 다릅니다.`);
  return { ...result, frames } as NeuralResult;
}
export function assertNeuralReceipt(raw: unknown, execution: NeuralExecution) {
  const receipt = object(raw), stored = object(receipt.execution);
  if (receipt.schema_version !== 1) fail("실행 개정 형식을 지원하지 않습니다.");
  for (const key of ["id", "experiment_id", "project_id", "status", "experiment_kind", "adapter_id", "adapter_version", "input_hash", "fidelity"] as const) if (stored[key] !== execution[key]) fail(`선택한 실행의 ${key}가 고정 원본과 다릅니다.`);
  if (!Array.isArray(stored.artifacts) || stored.artifacts.length !== execution.artifacts.length || stored.artifacts.some((value, index) => { const artifact = object(value); return (["id", "name", "media_type", "size_bytes", "sha256", "download_url"] as const).some(key => artifact[key] !== execution.artifacts[index][key]); })) fail("영상 산출물이 고정 원본과 다릅니다.");
  return stored.summary;
}
