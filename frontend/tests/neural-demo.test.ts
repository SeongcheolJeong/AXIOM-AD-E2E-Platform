import test from "node:test";
import assert from "node:assert/strict";
import { executeNeural, neuralParameterError, neuralSignature, neuralPlaybackStep, neuralSourceLabel, neuralOriginalLabel, neuralPresetForSource, NeuralPreviewChanged, neuralTerminal } from "../src/components/neural-demo/demo-contract.ts";
import { assertNeuralReceipt, parseNeuralResult } from "../src/components/neural-demo/neural-result.ts";
import { neuralPerceptionIndices, parseNeuralPerception, predictionForFrame, neuralCaseReviewHref } from "../src/components/neural-demo/neural-perception.ts";
import { caseHref } from "../src/components/cases/CaseTypes.ts";
import type { NeuralApi, NeuralAttempt, NeuralExecution, NeuralPreset, NeuralPreview, NeuralSource } from "../src/components/neural-demo/demo-contract.ts";

const hash = "a".repeat(64), archive = { id: "archive", name: "neural-render-frames.zip", media_type: "application/zip", size_bytes: 500, sha256: hash, download_url: "/api/v1/experiments/executions/run/artifacts/archive" };
const result = { schema_version: "neural-render-result/1", source_execution_id: "source", reconstruction_execution_id: "scene", source_manifest_sha256: hash, checkpoint_sha256: hash, frames_archive_sha256: hash, fidelity: "neural_reconstruction_of_carla", source_kind: "simulation", lateral_m: -.3, height_m: 0, mode: "sequence", frame_index: 0, width: 640, height: 360, fps: 10, frames: [0, 1].map(index => ({ frame_index: index, source_frame: 100 + index, time_s: index / 10, ...Object.fromEntries(["original", "baseline", "novel"].map(side => [side, { name: `${side}/${String(index).padStart(6, "0")}.png`, size_bytes: 50, sha256: hash }])) })) };
const execution = { id: "run", experiment_id: "exp", project_id: "demo", status: "completed", experiment_kind: "neural_render", adapter_id: "neural-splatfacto", adapter_version: "1.0.0", input_hash: hash, manifest_hash: hash, download_url: "/api/v1/experiments/executions/run/export", fidelity: "neural_reconstruction_of_carla", artifacts: [archive], created_at: "2026-10-10", summary: result } as NeuralExecution;
const preview: NeuralPreview = { experiment: { kind: "neural_render", input: { reconstruction_execution_id: "scene", lateral_m: -.3, height_m: 0 } }, preview: { preview_hash: hash } };

test("Neural submission preserves reviewed input and retries the same uncertain execution key", async () => {
  const calls: { path: string; body: unknown; key: string | null }[] = []; let submits = 0;
  const request: NeuralApi = async <T>(path: string, init?: RequestInit) => {
    calls.push({ path, body: JSON.parse(String(init?.body)), key: new Headers(init?.headers).get("Idempotency-Key") });
    if (path === "/experiments") return { id: "exp", preview_hash: hash } as T;
    if (++submits === 1) throw new Error("response lost");
    return { id: "run" } as T;
  };
  const attempt: NeuralAttempt = { key: "attempt", signature: "input" };
  await assert.rejects(executeNeural(request, preview, attempt, () => {}), /response lost/);
  assert.deepEqual(await executeNeural(request, preview, attempt, () => {}), { id: "run" });
  await executeNeural(request, preview, attempt, () => {});
  assert.equal(calls.length, 3); assert.deepEqual(calls[0].body, preview.experiment);
  assert.equal(calls[1].key, calls[2].key); assert.deepEqual(calls[2].body, { preview_hash: hash });
});
test("changed frozen preview never submits a different result", async () => {
  let calls = 0;
  const request: NeuralApi = async <T>() => { calls++; return { id: "exp", preview_hash: "different" } as T; };
  await assert.rejects(executeNeural(request, preview, { key: "k", signature: "s" }, () => {}), NeuralPreviewChanged);
  assert.equal(calls, 1);
});
test("source revision, editable offsets, frame index and mode distinguish submission intents", () => {
  const preset = { id: "left", binding_revision: 1 } as NeuralPreset, params = { lateral_m: -.3, height_m: 0 };
  const initial = neuralSignature("demo", preset, params, "frame", 3);
  for (const changed of [neuralSignature("other", preset, params, "frame", 3), neuralSignature("demo", { ...preset, binding_revision: 2 }, params, "frame", 3), neuralSignature("demo", preset, { ...params, height_m: .1 }, "frame", 3), neuralSignature("demo", preset, params, "frame", 4), neuralSignature("demo", preset, params, "sequence", 3)]) assert.notEqual(initial, changed);
  assert.equal(neuralSignature("demo", preset, params, "sequence", 3), neuralSignature("demo", preset, params, "sequence", 4));
});
test("camera editing rejects nonfinite and out-of-demo bounds; terminal failure unlocks next action", () => {
  assert.equal(neuralParameterError({ lateral_m: -.5, height_m: .3 }), "");
  for (const lateral_m of [NaN, Infinity, .51, -.51]) assert.ok(neuralParameterError({ lateral_m, height_m: 0 }));
  for (const height_m of [NaN, -.31, .31]) assert.ok(neuralParameterError({ lateral_m: 0, height_m }));
  assert.equal(neuralTerminal("running"), false); assert.equal(neuralTerminal("failed"), true);
});
test("Neural results preserve original frame identity, actual saved offsets and sealed source", () => {
  const parsed = parseNeuralResult(result, execution, archive);
  assert.equal(parsed.frames[1].source_frame, 101); assert.equal(parsed.lateral_m, -.3);
  const reversedArtifactKeys = Object.fromEntries(Object.entries(archive).reverse());
  assert.deepEqual(assertNeuralReceipt({ schema_version: 1, execution: { ...execution, artifacts: [reversedArtifactKeys] } }, execution), result);
  assert.throws(() => assertNeuralReceipt({ schema_version: 1, execution: { ...execution, project_id: "other" } }, execution), /project_id/);
});
test("wrong source, fake fidelity, unsafe image paths, reordered frames and unsaved metadata are rejected", () => {
  assert.throws(() => parseNeuralResult({ ...result, source_kind: "real_world" }, execution, archive));
  assert.throws(() => parseNeuralResult({ ...result, checkpoint_sha256: "bad" }, execution, archive));
  assert.throws(() => parseNeuralResult({ ...result, lateral_m: .3 }, execution, archive), /lateral_m/);
  assert.throws(() => parseNeuralResult({ ...result, source_execution_id: "other" }, execution, archive), /source_execution_id/);
  assert.throws(() => parseNeuralResult({ ...result, frames: [...result.frames].reverse() }, execution, archive), /시간 순서/);
  const edited = structuredClone(result); (edited.frames[0] as any).novel.name = "../outside.png";
  assert.throws(() => parseNeuralResult(edited, execution, archive), /파일 경로/);
  assert.throws(() => parseNeuralResult({ ...result, mode: "frame" }, execution, archive), /프레임 구간/);
});

const sweepResult = {
  ...result, schema_version: "neural-render-result/2", lateral_m: 1.5, height_m: .1, mode: "camera_sweep", frame_index: 50, source_frame_index: 50, source_time_s: 5,
  frames: Array.from({ length: 61 }, (_, index) => ({
    frame_index: index, time_s: index / 10, source_frame: 150, source_frame_index: 50, source_time_s: 5,
    applied_lateral_m: -1.5 * Math.cos(Math.PI * index / 60), applied_height_m: .1,
    ...Object.fromEntries(["original", "baseline", "novel"].map(side => [side, { name: `${side}/${String(index).padStart(6, "0")}.png`, size_bytes: 50, sha256: hash }]))
  }))
};
const sweepExecution = { ...execution, summary: sweepResult };
test("camera sweep supports bounded amplitude and preserves the frozen-source submission intent", () => {
  const preset = { id: "sweep", binding_revision: 1 } as NeuralPreset, parameters = { lateral_m: 1.5, height_m: .1 };
  assert.equal(neuralParameterError(parameters, "camera_sweep"), "");
  assert.ok(neuralParameterError(parameters, "sequence"));
  for (const lateral_m of [0, -1.5, 2.01, NaN, Infinity]) assert.ok(neuralParameterError({ ...parameters, lateral_m }, "camera_sweep"));
  assert.notEqual(neuralSignature("demo", preset, parameters, "camera_sweep", 50), neuralSignature("demo", preset, parameters, "camera_sweep", 51));
});
test("camera sweep v2 binds every rendered pose to one source instant and the sealed execution", () => {
  const parsed = parseNeuralResult(sweepResult, sweepExecution, archive);
  assert.equal(parsed.frames[60].source_frame_index, 50);
  assert.equal(parsed.frames[60].source_time_s, 5);
  assert.equal(parsed.frames[60].applied_lateral_m, 1.5);
  assert.ok(Math.abs(parsed.frames[30].applied_lateral_m!) < 1e-6);
  assert.throws(() => parseNeuralResult({ ...sweepResult, schema_version: "neural-render-result/1" }, sweepExecution, archive));
  assert.throws(() => parseNeuralResult(sweepResult, { ...sweepExecution, summary: { ...sweepResult, source_time_s: 6 } }, archive), /source_time_s/);
  for (const mutation of [{ source_frame_index: 51 }, { source_frame: 151 }, { source_time_s: 5.1 }, { applied_lateral_m: .4 }, { applied_height_m: .2 }]) {
    const changed = structuredClone(sweepResult); Object.assign(changed.frames[30], mutation);
    assert.throws(() => parseNeuralResult(changed, sweepExecution, archive), /고정 원본 시각 또는 실제 시점/);
  }
});
test("native camera frames ping-pong continuously without jumping from one endpoint to the other", () => {
  assert.deepEqual(neuralPlaybackStep(59, 61, 1, true), { index: 60, direction: 1 });
  assert.deepEqual(neuralPlaybackStep(60, 61, 1, true), { index: 59, direction: -1 });
  assert.deepEqual(neuralPlaybackStep(0, 61, -1, true), { index: 1, direction: 1 });
  assert.equal(neuralPlaybackStep(60, 61, 1, false), null);
  assert.equal(neuralPlaybackStep(0, 1, 1, true), null);
});

const roadResult = { ...sweepResult, source_kind: "real_road", fidelity: "neural_reconstruction_of_real_road", source_frame_count: 96, source_fps: 12, recording_id: "recording-road", channel_id: 7, source_time_s: 4.1672,
  frames: sweepResult.frames.map(frame => ({ ...frame, source_time_s: 4.1672 })) };
const roadExecution = { ...execution, fidelity: roadResult.fidelity, summary: roadResult };
test("recorded-road renders preserve actual irregular source time and explicit research labels", () => {
  const parsed = parseNeuralResult(roadResult, roadExecution, archive);
  assert.equal(parsed.frames[30].source_time_s, 4.1672);
  assert.equal(parsed.source_frame_count, 96); assert.equal(parsed.source_fps, 12);
  assert.match(neuralSourceLabel(parsed.source_kind), /실도로.*연구용/);
  assert.equal(neuralOriginalLabel(parsed.source_kind), "원본 실도로 영상");
  assert.match(neuralSourceLabel("simulation"), /CARLA/);
  assert.throws(() => parseNeuralResult(roadResult, { ...roadExecution, fidelity: "neural_reconstruction_of_carla" }, archive));
  assert.throws(() => parseNeuralResult({ ...roadResult, source_frame_count: 40 }, roadExecution, archive), /원본 시각/);
});
const render = parseNeuralResult(roadResult, roadExecution, archive);
const sensitivity = {
  schema_version: "neural-perception-result/1", scope: "model_output_sensitivity", ground_truth_used: false, accuracy_evaluated: false, case_supported: true,
  width: 640, height: 360, model_snapshot: { id: "detector", revision: 1, name: "Actual registered detector", profile: "opencv_yolox_rgb_v1", manifest_hash: hash, artifact: { sha256: hash } },
  render_pin: { execution_id: roadExecution.id, manifest_hash: roadExecution.manifest_hash },
  frames: [0, 15, 30, 45, 60].map(index => {
    const frame = render.frames[index];
    return { frame_index: index, source_frame_index: frame.source_frame_index, source_time_s: frame.source_time_s, applied_lateral_m: frame.applied_lateral_m, applied_height_m: frame.applied_height_m,
      ...Object.fromEntries(["original", "baseline", "novel"].map(side => [side, { image: { ...frame[side as "original"], width: 640, height: 360 }, detections: [{ id: "d0", class_id: 2, label: "car", score: .83, bbox_xyxy: [10.5, 50, 150.2, 199] }] }])),
      summary: { original_count: 1, baseline_count: 1, novel_count: 1, baseline_count_delta: 0, novel_count_delta: 0, novel_vs_original_count_delta: 0 } };
  })
};
test("model comparison pins each displayed image and reports model outputs only", () => {
  const parsed = parseNeuralPerception(sensitivity, roadExecution, render);
  assert.deepEqual(neuralPerceptionIndices(render), [0, 15, 30, 45, 60]);
  assert.equal(predictionForFrame(parsed, render.frames[30])?.novel.detections[0].score, .83);
  assert.equal(predictionForFrame(parsed, render.frames[31]), null, "Never borrow a neighboring frame's detections");
  assert.deepEqual(neuralPerceptionIndices({ ...render, frames: [render.frames[50]] }), [50]);
  assert.throws(() => parseNeuralPerception({ ...sensitivity, render_pin: { ...sensitivity.render_pin, execution_id: "other" } }, roadExecution, render), /렌더 실행/);
  assert.throws(() => parseNeuralPerception({ ...sensitivity, render_pin: { ...sensitivity.render_pin, manifest_hash: "b".repeat(64) } }, roadExecution, render), /렌더 실행/);
  assert.throws(() => parseNeuralPerception({ ...sensitivity, accuracy_evaluated: true }, roadExecution, render), /렌더 실행/);
});
test("incorrect PNG identity, pose, out-of-image boxes and fabricated count summaries are rejected", () => {
  const edited = () => structuredClone(sensitivity) as any;
  const wrongImage = edited(); wrongImage.frames[0].novel.image.sha256 = "b".repeat(64);
  assert.throws(() => parseNeuralPerception(wrongImage, roadExecution, render), /읽은 이미지/);
  const wrongPose = edited(); wrongPose.frames[0].applied_lateral_m = .1;
  assert.throws(() => parseNeuralPerception(wrongPose, roadExecution, render), /카메라 위치/);
  for (const values of [{ score: 1.1 }, { bbox_xyxy: [-1, 2, 10, 20] }, { bbox_xyxy: [1, 2, 641, 20] }, { bbox_xyxy: [10, 2, 5, 20] }]) {
    const bad = edited(); Object.assign(bad.frames[0].novel.detections[0], values);
    assert.throws(() => parseNeuralPerception(bad, roadExecution, render), /검출 박스/);
  }
  const wrongCount = edited(); wrongCount.frames[0].summary.novel_count = 8;
  assert.throws(() => parseNeuralPerception(wrongCount, roadExecution, render), /실제 검출 개수/);
});

test("Case links preserve project and the exact render and model comparison identities", () => {
  const href = neuralCaseReviewHref({ project_id: "project&road", render_pin: { execution_id: "render+one" }, sensitivity_pin: { execution_id: "model/run" } });
  const query = new URLSearchParams(href.split("?")[1]);
  assert.equal(query.get("project"), "project&road"); assert.equal(query.get("result"), "render+one");
  assert.equal(query.get("run"), "render+one"); assert.equal(query.get("perception"), "model/run");
  const caseQuery = new URLSearchParams(caseHref("case", 2, "project&road").split("?")[1]);
  assert.equal(caseQuery.get("case"), "case"); assert.equal(caseQuery.get("revision"), "2"); assert.equal(caseQuery.get("project"), "project&road");
});

test("unbound preset setup follows the selected real-road source without relabeling a saved CARLA binding", () => {
  const unbound = { id: "sweep", default_mode: "camera_sweep", default_frame_index: 50, defaults: { lateral_m: 1.5, height_m: 0 }, source: null, reason: "CARLA source needed" } as NeuralPreset;
  const road = { id: "road", source_kind: "real_road", frame_count: 96, fps: 12, default_frame_index: 48 } as NeuralSource;
  const adapted = neuralPresetForSource(unbound, road);
  assert.equal(adapted.defaults.lateral_m, .5); assert.equal(adapted.default_frame_index, 48);
  assert.match(adapted.reason!, /실도로/); assert.doesNotMatch(adapted.reason!, /CARLA/);
  const bound = { ...unbound, source: { source_execution_id: "carla" } } as NeuralPreset;
  assert.equal(neuralPresetForSource(bound, road), bound);
  assert.equal(neuralPresetForSource(unbound, { ...road, source_kind: "simulation" }), unbound);
});
