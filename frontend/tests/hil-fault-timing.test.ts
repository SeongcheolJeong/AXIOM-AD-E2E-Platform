import test from "node:test";
import assert from "node:assert/strict";
import type { HilFaultTiming } from "../src/components/hil-demo/demo-contract.ts";
import {
  hilEditTiming,
  hilTimingError,
  hilTimingTargetLabel,
  hilTimingWindow,
} from "../src/components/hil-demo/fault-timing.ts";
import {
  hilDraftSignature,
  hilDuplicateDraft,
  hilTestDraft,
  hilTestRef,
  hilTestSaveBody,
} from "../src/components/hil-demo/beta-contract.ts";
import type { HilTest } from "../src/components/hil-demo/beta-contract.ts";
import {
  hilFaultIntervalLabel,
  hilObservedFaultWindow,
} from "../src/components/hil-demo/hil-trace.ts";
import type { HilTrace } from "../src/components/hil-demo/hil-trace.ts";

// Authored timing fixtures exercise authoring/provenance contracts, not native execution.
test("fault timing target follows selected comparison mode", () => {
  assert.equal(hilTimingTargetLabel(false), "입력 시각 · 18초 중 B에만 적용");
  assert.equal(hilTimingTargetLabel(true), "입력 시각 · 18초 중 A/B에 동일 적용");
});
const delay: HilFaultTiming = {
  schema_version: "hil-fault-timing/1",
  start_min_s: 1,
  start_max_s: 17,
  end_max_s: 17.5,
  duration_min_s: 0.5,
  duration_max_s: 4,
  step_s: 0.5,
  default_start_s: 3,
  default_duration_s: 4,
};
const drop = { ...delay, default_start_s: 3.5, default_duration_s: 1 };
const oldTest = {
  id: "saved-delay",
  revision: 2,
  sha256: "a".repeat(64),
  name: "400ms delay",
  note: "",
  preset_id: "input_delay",
  parameters: { delay_ms: 400 },
  binding_ref: { id: "input_delay", revision: 1, sha256: "b".repeat(64) },
} as HilTest;

test("viewing old implicit timing does not modify parameters, saved hash or dirty signature", () => {
  const draft = hilTestDraft(oldTest),
    before = hilDraftSignature(draft);
  assert.deepEqual(hilTimingWindow(delay, draft.parameters), {
    start: 3,
    duration: 4,
    end: 7,
  });
  assert.equal(hilTimingError(delay, draft.parameters), "");
  assert.equal(hilDraftSignature(draft), before);
  assert.deepEqual(hilTestSaveBody("project", draft, oldTest).parameters, {
    delay_ms: 400,
  });
  assert.equal(oldTest.sha256, "a".repeat(64));
  assert.deepEqual(hilTimingWindow(drop, { duration_s: 2 }), {
    start: 3.5,
    duration: 2,
    end: 5.5,
  });
});

test("moving start preserves implicit or edited duration and moving end changes only duration", () => {
  const first = hilEditTiming(delay, { delay_ms: 400 }, "start", 2);
  assert.deepEqual(first, { delay_ms: 400, start_s: 2 });
  assert.deepEqual(hilTimingWindow(delay, first), {
    start: 2,
    duration: 4,
    end: 6,
  });
  const second = hilEditTiming(delay, first, "end", 5);
  assert.deepEqual(second, { delay_ms: 400, start_s: 2, duration_s: 3 });
  assert.deepEqual(
    hilTimingWindow(delay, hilEditTiming(delay, second, "start", 4)),
    { start: 4, duration: 3, end: 7 },
  );
  assert.deepEqual(hilEditTiming(drop, { duration_s: 1 }, "end", 6), {
    duration_s: 2.5,
  });
  assert.deepEqual(hilTimingWindow(drop, { duration_s: 2.5 }), {
    start: 3.5,
    duration: 2.5,
    end: 6,
  });
});

test("invalid time edits are preserved and rejected rather than clamped or snapped", () => {
  const edited = hilEditTiming(delay, { delay_ms: 400 }, "start", 2.1);
  assert.equal(edited.start_s, 2.1);
  assert.match(hilTimingError(delay, edited), /0.5초 단위/);
  for (const parameters of [
    { start_s: NaN },
    { duration_s: Infinity },
    { start_s: 0.5 },
    { start_s: 17.5, duration_s: 0.5 },
    { duration_s: 0 },
    { duration_s: 4.5 },
    { start_s: 14, duration_s: 4 },
    { start_s: 2, duration_s: -1 },
  ])
    assert.ok(hilTimingError(delay, parameters));
  assert.equal(hilTimingError(delay, { start_s: 17, duration_s: 0.5 }), "");
  assert.equal(hilTimingError(delay, { start_s: 13.5, duration_s: 4 }), "");
  assert.ok(
    Number.isNaN(
      hilEditTiming(delay, { delay_ms: 400 }, "end", NaN).duration_s,
    ),
  );
});

test("timing grid rejects near-half-second values exactly as native input validation", () => {
  for (const parameters of [
    { start_s: 16.00000001, duration_s: 0.5 },
    { start_s: 15.99999999, duration_s: 0.5 },
    { start_s: 2, duration_s: 3.00000001 },
    { start_s: 2, duration_s: 2.99999999 },
    hilEditTiming(delay, { start_s: 2 }, "end", 5.00000001),
  ])
    assert.match(hilTimingError(delay, parameters), /0.5초 단위/);
  assert.equal(hilTimingError(delay, { start_s: 16, duration_s: 0.5 }), "");
  assert.equal(
    hilTimingError(delay, hilEditTiming(delay, { start_s: 2 }, "end", 5)),
    "",
  );
});

test("duplicate keeps unsaved timing edits and exact source lineage without expanding legacy defaults", () => {
  const edited = {
    ...hilTestDraft(oldTest),
    parameters: { delay_ms: 400, start_s: 2, duration_s: 3 },
    note: "Edited purpose",
  };
  const duplicate = hilDuplicateDraft(oldTest, edited);
  assert.deepEqual(duplicate.parameters, edited.parameters);
  assert.equal(duplicate.note, edited.note);
  assert.deepEqual(duplicate.copied_from, hilTestRef(oldTest));
  assert.deepEqual(duplicate.binding_ref, oldTest.binding_ref);
  assert.deepEqual(
    hilDuplicateDraft(oldTest, hilTestDraft(oldTest)).parameters,
    { delay_ms: 400 },
  );
  assert.notEqual(
    hilDraftSignature(edited),
    hilDraftSignature(hilTestDraft(oldTest)),
  );
});

test("executed interval label comes from retained fault while response events retain their own clock", () => {
  const oldFault = {
    kind: "delay" as const,
    delay_ms: 400,
    start_s: 3,
    duration_s: 4,
  };
  assert.equal(hilFaultIntervalLabel(oldFault), "3.00–7.00");
  const edited = hilEditTiming(
    delay,
    hilEditTiming(delay, { delay_ms: 400 }, "start", 2),
    "end",
    5,
  );
  assert.deepEqual(hilTimingWindow(delay, edited), {
    start: 2,
    duration: 3,
    end: 5,
  });
  assert.equal(hilFaultIntervalLabel(oldFault), "3.00–7.00");
  assert.equal(
    hilFaultIntervalLabel({ ...oldFault, start_s: 2, duration_s: 3 }),
    "2.00–5.00",
  );
  const trace = {
    events: [
      { type: "fault_started", time_s: 2.05 },
      { type: "fault_ended", time_s: 5.05 },
    ],
  } as HilTrace;
  assert.deepEqual(hilObservedFaultWindow(trace), { start: 2.05, end: 5.05 });
});
