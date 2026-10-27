import test from "node:test";
import assert from "node:assert/strict";
import {
  playbackBoundary,
  retainedFrameIndex,
} from "../src/components/city-sim/city-playback.ts";
import {
  canShowCityExecution,
  comparisonNeedsPolling,
  comparisonFieldLabel,
  comparisonSettingValue,
  comparisonValue,
  executionStatusLabel,
} from "../src/components/object-demo/comparison-model.ts";
import type { ObjectExecution } from "../src/components/object-scene/object-scene.ts";
import type { ObjectComparisonData } from "../src/components/object-demo/comparison-model.ts";

test("shared time samples only past retained frames, including sparse and differing timelines", () => {
  const a = [{ time_s: 0.05 }, { time_s: 0.1 }, { time_s: 0.2 }];
  const b = [{ time_s: 0.1 }, { time_s: 0.3 }, { time_s: 0.5 }];
  assert.equal(retainedFrameIndex(a, 0), -1);
  assert.equal(retainedFrameIndex(a, 0.1), 1);
  assert.equal(retainedFrameIndex(a, 0.199), 1);
  assert.equal(retainedFrameIndex(b, 0.199), 0);
  assert.equal(retainedFrameIndex(a, 0.4), 2);
  assert.equal(retainedFrameIndex(b, 0.4), 1);
  assert.equal(playbackBoundary(a, 0.4), "ended");
  assert.equal(playbackBoundary(b, 0.4), "active");
  assert.equal(playbackBoundary(b, 0.05), "before");
  assert.equal(retainedFrameIndex([], 0), -1);
  assert.equal(retainedFrameIndex(a, NaN), -1);
});

test("retained playback treats subnanosecond timestamp noise as the same sample and boundary", () => {
  const frames = [{ time_s: 3.5 }, { time_s: 3.5500000000000003 }];
  assert.equal(retainedFrameIndex(frames, 3.55), 1);
  assert.equal(retainedFrameIndex(frames, 3.549), 0);
  assert.equal(retainedFrameIndex([{ time_s: 3.5500000000000003 }], 3.55), 0);
  assert.equal(
    playbackBoundary([{ time_s: 3.5500000000000003 }], 3.55),
    "active",
  );
  assert.equal(
    playbackBoundary([{ time_s: 3.55 }], 3.5500000000000003),
    "active",
  );
  assert.equal(playbackBoundary([{ time_s: 3.55 }], 3.549), "before");
  assert.equal(playbackBoundary(frames, 3.551), "ended");
});

test("unsealed or missing scene executions are unavailable, not rendered as motion", () => {
  const execution = {
    adapter_id: "carla-city-object",
    manifest_hash: "a".repeat(64),
    artifacts: [{ name: "city-scene.json" }],
  } as ObjectExecution;
  assert.equal(canShowCityExecution(execution), true);
  assert.equal(
    canShowCityExecution({ ...execution, manifest_hash: null }),
    false,
  );
  assert.equal(canShowCityExecution({ ...execution, artifacts: [] }), false);
  assert.equal(
    canShowCityExecution({ ...execution, adapter_id: "esmini" }),
    false,
  );
});

test("missing metrics stay missing and failed execution status stays failed", () => {
  assert.equal(comparisonValue(null), "기록 없음");
  assert.equal(comparisonValue(0), "0");
  assert.equal(comparisonValue(false), "아니요");
  assert.equal(executionStatusLabel("failed"), "실행 실패");
  const data = {
    left: { execution: { status: "completed" } },
    right: { execution: { status: "running" } },
  } as ObjectComparisonData;
  assert.equal(comparisonNeedsPolling(data), true);
  data.right.execution.status = "failed";
  assert.equal(comparisonNeedsPolling(data), false);
});

test("authored lead waits are presented as settings, not inferred reaction outcomes", () => {
  const field = "/actors/lead/behavior/actions";
  assert.equal(comparisonFieldLabel(field), "설정한 행동");
  assert.equal(
    comparisonFieldLabel(`${field}/0/duration_s`),
    "행동 유지 시간 (s)",
  );
  assert.equal(comparisonSettingValue(field, []), "추가 행동 없음");
  assert.equal(
    comparisonSettingValue(field, [
      { id: "pause", kind: "wait", duration_s: 6 },
    ]),
    "대기 6초",
  );
  assert.equal(comparisonSettingValue(`${field}/0`, null), "추가 행동 없음");
  assert.equal(comparisonFieldLabel(`${field}/lead_wait`), "설정한 행동");
  assert.equal(
    comparisonSettingValue(`${field}/lead_wait`, null),
    "추가 행동 없음",
  );
  assert.equal(
    comparisonSettingValue(`${field}/lead_wait`, {
      id: "lead_wait",
      kind: "wait",
      duration_s: 6,
    }),
    "대기 6초",
  );
  assert.equal(comparisonSettingValue("/duration_s", 24), "24");
});

test("long route diffs stay compact while original values remain unchanged", () => {
  const route = Array.from({ length: 200 }, (_, i) => ({
    x: -10 + i,
    y: 69.7,
    z: 1,
    heading_rad: 0,
  }));
  const before = JSON.stringify(route);
  assert.equal(comparisonFieldLabel("/actors/ego/route"), "경로");
  assert.equal(
    comparisonSettingValue("/actors/ego/route", route),
    "경유점 200개 · (-10.0, 69.7) → (189.0, 69.7) m",
  );
  assert.equal(JSON.stringify(route), before);
  assert.equal(comparisonSettingValue("/actors/ego/route", []), "경유점 없음");
  assert.equal(
    comparisonSettingValue("/actors/ego/route", [{ x: 1 }]),
    "경유점 1개",
  );
  assert.equal(
    comparisonSettingValue("/actors/ego/route/0", route[0]),
    "설정 4개",
  );
});
