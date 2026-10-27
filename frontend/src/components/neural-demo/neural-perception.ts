import type { NeuralExecution } from "./demo-contract";
import type { NeuralFrame, NeuralResult } from "./neural-result";

export const neuralSides = ["original", "baseline", "novel"] as const;
export type NeuralSide = typeof neuralSides[number];
export type NeuralDetection = { id: string | number; class_id: number; label: string; score: number; bbox_xyxy: [number, number, number, number] };
export type NeuralPrediction = { image: { name: string; sha256: string; size_bytes: number; width: number; height: number }; detections: NeuralDetection[] };
export type NeuralPerceptionFrame = { frame_index: number; source_frame_index: number; source_time_s: number; applied_lateral_m: number; applied_height_m: number; original: NeuralPrediction; baseline: NeuralPrediction; novel: NeuralPrediction; summary: { original_count: number; baseline_count: number; novel_count: number; baseline_count_delta: number; novel_count_delta: number; novel_vs_original_count_delta: number } };
export type NeuralPerceptionResult = { schema_version: "neural-perception-result/1"; scope: "model_output_sensitivity"; case_supported?: boolean; width: number; height: number; model_snapshot: { id: string; revision: number; name: string; manifest_hash: string; profile: string; artifact: { sha256: string } }; render_pin: { execution_id: string; manifest_hash: string }; frames: NeuralPerceptionFrame[] };
export type NeuralPerceptionRun = { id: string; name: string; render_execution_id: string; model_id: string; model_revision: number; status: string };
function fail(message: string): never { throw new Error(`Neural 모델 비교: ${message}`); }
function object(value: unknown): Record<string, unknown> { if (!value || typeof value !== "object" || Array.isArray(value)) fail("결과 구조가 올바르지 않습니다."); return value as Record<string, unknown>; }
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const hash = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
export function neuralPerceptionIndices(result: NeuralResult): number[] {
  const last = result.frames.length - 1;
  return [...new Set([0, .25, .5, .75, 1].map(fraction => result.frames[Math.round(last * fraction)].frame_index))];
}
export function parseNeuralPerception(raw: unknown, execution: NeuralExecution, render: NeuralResult): NeuralPerceptionResult {
  const result = object(raw), pin = object(result.render_pin), model = object(result.model_snapshot);
  if (result.schema_version !== "neural-perception-result/1" || result.scope !== "model_output_sensitivity" || result.ground_truth_used !== false || result.accuracy_evaluated !== false || pin.execution_id !== execution.id || pin.manifest_hash !== execution.manifest_hash || result.width !== render.width || result.height !== render.height) fail("선택한 렌더 실행과 모델 비교 결과가 다릅니다.");
  if (typeof model.id !== "string" || !model.id || typeof model.name !== "string" || !Number.isSafeInteger(model.revision) || Number(model.revision) < 1 || !hash(model.manifest_hash) || !hash(object(model.artifact).sha256)) fail("고정 모델 개정과 가중치 hash가 유효하지 않습니다.");
  if (!Array.isArray(result.frames) || !result.frames.length || result.frames.length > 61) fail("비교할 시점이 없습니다.");
  const seen = new Set<number>();
  for (const item of result.frames) {
    const frame = object(item), source = render.frames.find(candidate => candidate.frame_index === frame.frame_index);
    if (!source || seen.has(source.frame_index)) fail("모델 출력의 시점이 렌더 영상과 다릅니다.");
    seen.add(source.frame_index);
    const sourceIndex = source.source_frame_index ?? source.frame_index, sourceTime = source.source_time_s ?? source.time_s;
    if (frame.source_frame_index !== sourceIndex || frame.source_time_s !== sourceTime || !finite(frame.applied_lateral_m) || Math.abs(frame.applied_lateral_m - (source.applied_lateral_m ?? render.lateral_m)) > 1e-6 || frame.applied_height_m !== (source.applied_height_m ?? render.height_m)) fail("모델 출력의 원본 시각과 카메라 위치가 다릅니다.");
    for (const side of neuralSides) {
      const output = object(frame[side]), image = object(output.image), member = source[side];
      if (image.name !== member.name || image.sha256 !== member.sha256 || image.size_bytes !== member.size_bytes || image.width !== render.width || image.height !== render.height) fail("모델이 읽은 이미지와 표시할 렌더 프레임이 다릅니다.");
      if (!Array.isArray(output.detections) || output.detections.length > 1000) fail("검출 결과 형식이 올바르지 않습니다.");
      for (const value of output.detections) {
        const detection = object(value), box = detection.bbox_xyxy;
        if (typeof detection.label !== "string" || !detection.label || !Number.isSafeInteger(detection.class_id) || Number(detection.class_id) < 0 || !finite(detection.score) || detection.score < 0 || detection.score > 1 || !Array.isArray(box) || box.length !== 4 || !box.every(finite) || box[0] < 0 || box[1] < 0 || box[2] > render.width || box[3] > render.height || box[2] <= box[0] || box[3] <= box[1]) fail("검출 박스 또는 confidence가 영상 범위를 벗어났습니다.");
      }
    }
    const summary = object(frame.summary), counts = neuralSides.map(side => (object(frame[side]).detections as unknown[]).length);
    if (summary.original_count !== counts[0] || summary.baseline_count !== counts[1] || summary.novel_count !== counts[2] || summary.baseline_count_delta !== counts[1] - counts[0] || summary.novel_count_delta !== counts[2] - counts[1] || summary.novel_vs_original_count_delta !== counts[2] - counts[0]) fail("비교 요약과 실제 검출 개수가 다릅니다.");
  }
  return result as unknown as NeuralPerceptionResult;
}
export function predictionForFrame(result: NeuralPerceptionResult | null, frame: NeuralFrame | null) {
  return frame ? result?.frames.find(item => item.frame_index === frame.frame_index) ?? null : null;
}

export function neuralCaseReviewHref(link: { project_id: string; render_pin: { execution_id: string }; sensitivity_pin: { execution_id: string } }) {
  return `#neural-sim?${new URLSearchParams({ project: link.project_id, result: link.render_pin.execution_id, run: link.render_pin.execution_id, perception: link.sensitivity_pin.execution_id })}`;
}
