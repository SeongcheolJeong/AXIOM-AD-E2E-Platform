import test from "node:test";
import assert from "node:assert/strict";
import {
  hilBatchAcceptSnapshot,
  hilBatchActionNotice,
  hilBatchActive,
  hilBatchCounts,
  hilBatchDraftError,
  hilBatchMove,
  hilBatchMutationKey,
  hilBatchPreviewMatches,
  hilBatchRequestRejected,
  hilBatchRowState,
  hilBatchSaveBody,
  hilBatchTestDescription,
  restoreHilBatchMutation,
} from "../src/components/hil-demo/batch-contract.ts";
import type {
  HilBatch,
  HilBatchMutation,
  HilBatchPreview,
} from "../src/components/hil-demo/batch-contract.ts";
import type { HilTest } from "../src/components/hil-demo/beta-contract.ts";
import { hilTestRef } from "../src/components/hil-demo/beta-contract.ts";

// Authored contract records only. No simulator, DUT, or measured trace is exercised by these tests.
const saved = (id: string, revision = 1): HilTest =>
  ({
    id,
    revision,
    sha256: String(revision).repeat(64),
    project_id: "project-one",
    name: id,
    note: "Authored test",
    preset_id: "input_delay",
    parameters: { delay_ms: 400 },
  }) as HilTest;
const tests = [saved("delay", 1), saved("delay", 2), saved("drop")];
const draft = () => ({
  name: "  Three conditions  ",
  note: "  Purpose  ",
  tests,
});
const batch = (): HilBatch => ({
  schema_version: "hil-sim-batch/1",
  id: "batch-one",
  project_id: "project-one",
  revision: 3,
  sha256: "a".repeat(64),
  definition_sha256: "b".repeat(64),
  name: "Batch",
  note: "",
  status: "draft",
  created_at: "2026-10-11",
  updated_at: "2026-10-11",
  author: {},
  error: null,
  can_start: true,
  can_cancel: true,
  can_resume: false,
  action_reason: null,
  items: tests.map((value, index) => ({
    index,
    test_ref: hilTestRef(value),
    test: value,
    status: "pending",
    attempts: [],
  })),
});
const preview = (value: HilBatch): HilBatchPreview => ({
  schema_version: "hil-sim-batch-preview/1",
  batch_ref: { id: value.id, revision: value.revision, sha256: value.sha256 },
  preview_hash: "c".repeat(64),
  requires_explicit_submit: true,
  items: value.items
    .filter((item) => item.status !== "completed")
    .map((item) => ({
      index: item.index,
      test_ref: item.test_ref,
      preview_hash: "d".repeat(64),
      execution_fidelity: "actual_carla_software_dut",
      resource_id: "carla",
    })),
  job_count: value.items.filter((item) => item.status !== "completed").length,
});

test("batch authoring accepts different revisions of one test but rejects exact duplicates and invalid sizes", () => {
  assert.equal(hilBatchDraftError(draft()), "");
  assert.match(
    hilBatchDraftError({ ...draft(), tests: [tests[0], tests[0], tests[2]] }),
    /한 번/,
  );
  assert.match(
    hilBatchDraftError({ ...draft(), tests: tests.slice(0, 2) }),
    /3–5/,
  );
  assert.match(
    hilBatchDraftError({
      ...draft(),
      tests: [...tests, saved("4"), saved("5"), saved("6")],
    }),
    /3–5/,
  );
  assert.match(hilBatchDraftError({ ...draft(), name: " " }), /이름/);
  assert.match(
    hilBatchDraftError({
      ...draft(),
      tests: [tests[0], tests[1], { ...tests[2], project_id: "other" }],
    }),
    /프로젝트/,
  );
});
test("batch ordering preserves exact refs and never mutates saved test definitions", () => {
  const moved = hilBatchMove(tests, 2, -1);
  assert.deepEqual(
    moved.map((item) => item.id),
    ["delay", "drop", "delay"],
  );
  assert.equal(tests[2].id, "drop");
  assert.equal(hilBatchMove(tests, 0, -1), tests);
  assert.deepEqual(
    hilBatchSaveBody("project-one", { ...draft(), tests: moved }),
    {
      project_id: "project-one",
      name: "Three conditions",
      note: "Purpose",
      tests: moved.map(hilTestRef),
    },
  );
});
test("batch labels use frozen timing and policies rather than the current catalog", () => {
  assert.equal(hilBatchTestDescription(tests[0]).interval, "입력 3–7 s");
  const value = {
    ...tests[0],
    parameters: { delay_ms: 400, start_s: 2, duration_s: 3 },
    dut_profiles: {
      baseline: { name: "기준", revision: 1 },
      faulted: { name: "허용", revision: 2 },
    },
  } as HilTest;
  assert.deepEqual(hilBatchTestDescription(value), {
    fault: "400 ms 지연",
    interval: "입력 2–5 s",
    profiles: "A 기준 r1 / B 허용 r2",
  });
  assert.equal(
    hilBatchTestDescription({
      ...tests[0],
      preset_id: "input_dropout",
      parameters: { duration_s: 1.5 },
    }).interval,
    "입력 3.5–5 s",
  );
});
test("review binds exact current batch state and all remaining ordered test revisions", () => {
  const value = batch(),
    review = preview(value);
  assert.equal(hilBatchPreviewMatches(review, value), true);
  assert.equal(
    hilBatchPreviewMatches(review, { ...value, revision: 4 }),
    false,
  );
  assert.equal(
    hilBatchPreviewMatches(
      { ...review, batch_ref: { ...review.batch_ref, sha256: "x" } },
      value,
    ),
    false,
  );
  assert.equal(
    hilBatchPreviewMatches(
      { ...review, items: [...review.items].reverse() },
      value,
    ),
    false,
  );
  assert.equal(
    hilBatchPreviewMatches(
      { ...review, items: [review.items[0], review.items[0], review.items[2]] },
      value,
    ),
    false,
  );
  assert.equal(
    hilBatchPreviewMatches(
      { ...review, items: review.items.slice(1), job_count: 2 },
      value,
    ),
    false,
  );
  value.items[0].status = "completed";
  assert.equal(hilBatchPreviewMatches(review, value), false);
  const remaining = preview(value);
  assert.equal(remaining.items[0].index, 1);
  assert.equal(hilBatchPreviewMatches(remaining, value), true);
});
test("out of order polling cannot roll back progress, and source mismatches fail closed", () => {
  const current = batch();
  assert.equal(
    hilBatchAcceptSnapshot(current, { ...current, revision: 2 }, "project-one"),
    current,
  );
  assert.throws(
    () =>
      hilBatchAcceptSnapshot(
        null,
        { ...current, project_id: "other" },
        "project-one",
      ),
    /프로젝트/,
  );
  const invalid = batch();
  invalid.items[0].test_ref = { ...invalid.items[0].test_ref, revision: 9 };
  assert.throws(
    () => hilBatchAcceptSnapshot(null, invalid, "project-one"),
    /개정/,
  );
  const reordered = batch();
  reordered.items.reverse();
  assert.throws(
    () => hilBatchAcceptSnapshot(null, reordered, "project-one"),
    /개정/,
  );
});
test("unknown submission outcomes recover the exact key and body only for the same principal and project", () => {
  const mutation: HilBatchMutation = {
    version: 1,
    project: "project-one",
    principal: "engineer-one",
    action: "start",
    path: "/hil-sim/batches/batch-one/start",
    body: JSON.stringify({
      project_id: "project-one",
      expected_revision: 3,
      preview_hash: "a".repeat(64),
    }),
    key: "same-start-key",
  };
  const raw = JSON.stringify(mutation);
  assert.deepEqual(
    restoreHilBatchMutation(raw, "project-one", "engineer-one"),
    mutation,
  );
  assert.equal(
    restoreHilBatchMutation(raw, "other-project", "engineer-one"),
    null,
  );
  assert.equal(
    restoreHilBatchMutation(raw, "project-one", "other-engineer"),
    null,
  );
  assert.equal(
    restoreHilBatchMutation(
      JSON.stringify({
        ...mutation,
        path: "/experiments/different/executions",
      }),
      "project-one",
      "engineer-one",
    ),
    null,
  );
  assert.equal(
    restoreHilBatchMutation("broken", "project-one", "engineer-one"),
    null,
  );
  assert.notEqual(
    hilBatchMutationKey("project-one", "engineer-one"),
    hilBatchMutationKey("project-one", "engineer-two"),
  );
});
test("explicit client rejection permits new review, but timeout/server uncertainty retains the submission key", () => {
  assert.equal(hilBatchRequestRejected(409), true);
  assert.equal(hilBatchRequestRejected(422), true);
  assert.equal(hilBatchRequestRejected(403), true);
  assert.equal(hilBatchRequestRejected(408), false);
  assert.equal(hilBatchRequestRejected(500), false);
  assert.equal(hilBatchRequestRejected(504), false);
});
test("progress counts completed conditions separately from failed attempts and uncertainty", () => {
  const value = batch();
  value.status = "stopped";
  value.items[0].status = "completed";
  value.items[1].status = "failed";
  value.items[1].attempts.push({
    attempt: 1,
    status: "failed",
    execution_id: "run-one",
    created_at: "now",
    finished_at: "later",
    error: "error",
    execution: { id: "run-one", status: "failed" },
    outcome_uncertain: true,
    result: null,
  });
  assert.deepEqual(hilBatchCounts(value), {
    total: 3,
    completed: 1,
    failed: 1,
    uncertain: 1,
    attempts: 1,
  });
  assert.equal(hilBatchActive(value), false);
  assert.equal(hilBatchActive({ ...value, status: "stopping" }), true);
});
test("sealed result summaries cannot be relabeled as another execution or a safety verdict", () => {
  const value = batch();
  const attempt = {
    attempt: 1,
    status: "completed",
    execution_id: "one",
    created_at: "now",
    finished_at: "later",
    error: null,
    execution: {
      id: "one",
      status: "completed",
      manifest_hash: "e".repeat(64),
    },
    outcome_uncertain: false,
    result: {
      schema_version: "hil-sim-batch-result/1",
      execution_id: "one",
      manifest_hash: "e".repeat(64),
      verification_status: "not_a_test_verdict",
    },
  } as HilBatch["items"][number]["attempts"][number];
  value.items[0].attempts.push(attempt);
  assert.equal(hilBatchAcceptSnapshot(null, value, "project-one"), value);
  attempt.result!.execution_id = "another";
  assert.throws(
    () => hilBatchAcceptSnapshot(null, value, "project-one"),
    /원본 결과/,
  );
  attempt.result!.execution_id = "one";
  attempt.result!.manifest_hash = "f".repeat(64);
  assert.throws(
    () => hilBatchAcceptSnapshot(null, value, "project-one"),
    /원본 결과/,
  );
  attempt.result!.manifest_hash = "";
  attempt.execution!.manifest_hash = "";
  assert.throws(
    () => hilBatchAcceptSnapshot(null, value, "project-one"),
    /원본 결과/,
  );
  attempt.result!.manifest_hash = "e".repeat(64);
  attempt.execution!.manifest_hash = "e".repeat(64);
  attempt.status = "queued";
  assert.throws(
    () => hilBatchAcceptSnapshot(null, value, "project-one"),
    /확정 상태/,
  );
  attempt.status = "completed";
  attempt.execution!.status = "running";
  assert.throws(
    () => hilBatchAcceptSnapshot(null, value, "project-one"),
    /확정 상태/,
  );
  attempt.execution!.status = "completed";
  attempt.outcome_uncertain = true;
  assert.throws(
    () => hilBatchAcceptSnapshot(null, value, "project-one"),
    /확정 상태/,
  );
});

test("read-only live receipt status updates the active row without changing pinned batch progress or source", () => {
  const value = batch();
  value.status = "running";
  value.items[0].status = "queued";
  value.items[0].attempts.push({
    attempt: 1,
    status: "queued",
    execution_id: "active-one",
    created_at: "now",
    finished_at: null,
    error: null,
    execution: null,
    outcome_uncertain: false,
    result: null,
  });
  value.live_executions = {
    "active-one": {
      id: "active-one",
      status: "running",
      outcome_uncertain: false,
    },
  };
  assert.equal(hilBatchRowState(value, value.items[0]).status, "running");
  assert.equal(value.items[0].status, "queued");
  value.live_executions["active-one"].status = "completed";
  assert.equal(hilBatchRowState(value, value.items[0]).summaryPending, true);
  assert.equal(hilBatchCounts(value).completed, 0);
  value.live_executions["active-one"].outcome_uncertain = true;
  assert.equal(hilBatchCounts(value).uncertain, 1);
  value.live_executions["active-one"].id = "unrelated";
  assert.throws(
    () => hilBatchAcceptSnapshot(null, value, "project-one"),
    /실시간 상태/,
  );
});

test("action notices advance with terminal polling rather than retaining in-progress cancellation wording", () => {
  assert.match(
    hilBatchActionNotice("cancel", "stopping"),
    /종료 상태를 확인하는 중/,
  );
  assert.match(hilBatchActionNotice("cancel", "cancelled"), /취소되었습니다/);
  assert.doesNotMatch(
    hilBatchActionNotice("cancel", "cancelled"),
    /확인하는 중/,
  );
  assert.match(hilBatchActionNotice("start", "completed"), /완료되었습니다/);
  assert.match(hilBatchActionNotice("resume", "stopped"), /중단되었습니다/);
  assert.match(hilBatchActionNotice("save", "draft"), /엔진 준비를 확인한 뒤/);
});
