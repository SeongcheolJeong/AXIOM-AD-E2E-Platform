import test from "node:test";
import assert from "node:assert/strict";
import {
  executeHilTest,
  hilDraftFromPreset,
  hilDraftSignature,
  hilObservationsPath,
  hilPendingKey,
  hilResultQuery,
  hilTestDraft,
  hilTestPreviewMatches,
  hilTestRef,
  hilTestSaveBody,
  hilTrackedResultQuery,
  restoreHilPending,
} from "../src/components/hil-demo/beta-contract.ts";
import type {
  HilPending,
  HilTest,
  HilTestPreview,
} from "../src/components/hil-demo/beta-contract.ts";
import { HilPreviewChanged } from "../src/components/hil-demo/demo-contract.ts";
import { boundedDemoMoment, demoMoment } from "../src/demo-moment.ts";
import type {
  HilApi,
  HilPreset,
} from "../src/components/hil-demo/demo-contract.ts";

// Authored API contract fixtures. These checks do not exercise CARLA or a DUT.
test("observation bookmark uses the presenter URL clock and opening another result clears it", () => {
  const query = new URLSearchParams({
    project: "demo",
    mode: "beta",
    moment: "3.3",
  });
  const apply = (values: Record<string, string | null>) => {
    for (const [key, value] of Object.entries(values))
      if (value === null) query.delete(key);
      else query.set(key, value);
  };
  apply(hilResultQuery("retained-result", 4.55));
  assert.equal(
    boundedDemoMoment(demoMoment(`#hil-sim?${query}`), 0.05, 18),
    4.55,
  );
  assert.equal(query.get("result"), "retained-result");
  assert.equal(query.has("moment"), false);
  apply(hilResultQuery("another-result"));
  assert.equal(demoMoment(`#hil-sim?${query}`), null);
  assert.equal(query.get("result"), "another-result");
  assert.equal(query.get("mode"), "beta");
});
test("opening a batch result replaces previous run tracking and restores the same bookmarked response after reload", () => {
  const query = new URLSearchParams({
    run: "previous-run",
    result: "previous-result",
    time_s: "7",
    moment: "9",
    batch: "selected-batch",
  });
  for (const [key, value] of Object.entries(
    hilTrackedResultQuery("batch-result", 2.3),
  )) {
    if (value === null) query.delete(key);
    else query.set(key, value);
  }
  assert.equal(query.get("run"), "batch-result");
  assert.equal(query.get("result"), "batch-result");
  assert.equal(query.get("batch"), "selected-batch");
  assert.equal(query.has("moment"), false);
  assert.equal(demoMoment(`#hil-sim?${query}`), 2.3);
});
const saved = {
  id: "test-one",
  revision: 2,
  sha256: "b".repeat(64),
  project_id: "demo",
  name: "Delay",
  note: "Investigate response",
  preset_id: "input_delay",
  parameters: { delay_ms: 300 },
  binding_ref: { id: "input_delay", revision: 1, sha256: "a".repeat(64) },
} as HilTest;
const preview = {
  schema_version: "hil-sim-test-preview/1",
  test_ref: hilTestRef(saved),
  experiment: { project_id: "demo", name: "Delay", kind: "hil_demo" },
  preview: { preview_hash: "c".repeat(64) },
} as HilTestPreview;
const pending = (): HilPending => ({
  version: 1,
  project: "demo",
  principal: "engineer",
  test_ref: hilTestRef(saved),
  preview,
  attempt: { key: "stable-submit", signature: "fixed-test" },
  attachment_key: "stable-attach",
});

test("observations are scoped to the displayed result and exact saved revision, matching export", () => {
  const path = hilObservationsPath("demo", "retained-result", saved)!;
  const query = new URL(path, "https://example.test").searchParams;
  assert.equal(query.get("project_id"), "demo");
  assert.equal(query.get("execution_id"), "retained-result");
  assert.equal(query.get("test_id"), saved.id);
  assert.equal(query.get("revision"), "2");
  assert.equal(hilObservationsPath("demo", "retained-result", null), null);
  assert.equal(hilObservationsPath("demo", null, saved), null);
});

test("offline preset source pins allow test authoring without claiming native readiness", () => {
  const preset = {
    id: "input_delay",
    title: "Delay",
    defaults: { delay_ms: 300 },
    ready: false,
    status: "not_configured",
    binding_revision: 1,
    binding_sha256: "a".repeat(64),
  } as HilPreset;
  assert.deepEqual(hilDraftFromPreset(preset).binding_ref, saved.binding_ref);
  assert.throws(
    () => hilDraftFromPreset({ ...preset, binding_sha256: null }),
    /고정 시나리오/,
  );
});

test("revision save and clone keep source pins while edits invalidate saved-preview state", () => {
  const original = hilTestDraft(saved),
    edited = { ...original, parameters: { delay_ms: 400 } };
  assert.notEqual(hilDraftSignature(original), hilDraftSignature(edited));
  assert.notEqual(
    hilDraftSignature(original),
    hilDraftSignature({ ...original, name: "New purpose" }),
  );
  assert.equal(
    hilDraftSignature(original),
    hilDraftSignature({ ...original, name: " Delay " }),
  );
  const body = hilTestSaveBody("demo", edited, saved);
  assert.equal(body.expected_revision, 2);
  assert.equal(body.id, saved.id);
  assert.equal(body.binding_revision, 1);
  assert.equal(body.binding_sha256, saved.binding_ref.sha256);
  assert.deepEqual(body.parameters, { delay_ms: 400 });
  const clone = hilTestSaveBody(
    "demo",
    { ...original, copied_from: hilTestRef(saved) },
    null,
  );
  assert.equal(clone.id, undefined);
  assert.equal(clone.expected_revision, 0);
  assert.deepEqual(clone.copied_from, hilTestRef(saved));
  assert.equal(clone.binding_sha256, saved.binding_ref.sha256);
  assert.equal(hilTestPreviewMatches(preview, saved), true);
  for (const changed of [
    { ...saved, revision: 3 },
    { ...saved, sha256: "changed" },
    { ...saved, id: "other" },
  ])
    assert.equal(hilTestPreviewMatches(preview, changed), false);
});

test("restored pending request is scoped to both principal and project and exact test ref", () => {
  const raw = JSON.stringify(pending());
  assert.deepEqual(restoreHilPending(raw, "demo", "engineer"), pending());
  assert.equal(restoreHilPending(raw, "other", "engineer"), null);
  assert.equal(restoreHilPending(raw, "demo", "viewer"), null);
  assert.notEqual(
    hilPendingKey("demo", "engineer"),
    hilPendingKey("demo", "viewer"),
  );
  const mismatch = pending();
  mismatch.test_ref = { ...mismatch.test_ref, revision: 3 };
  assert.equal(
    restoreHilPending(JSON.stringify(mismatch), "demo", "engineer"),
    null,
  );
});

test("lost attachment response retains submitted execution and retries the same link without another job", async () => {
  const calls: { path: string; key: string; body: unknown }[] = [];
  let links = 0,
    checkpoint = "";
  const work = pending();
  const request: HilApi = async <T>(path: string, init?: RequestInit) => {
    calls.push({
      path,
      key: new Headers(init?.headers).get("Idempotency-Key")!,
      body: JSON.parse(String(init?.body)),
    });
    if (path === "/experiments")
      return {
        id: "experiment-one",
        preview_hash: preview.preview.preview_hash,
      } as T;
    if (path === "/experiments/experiment-one/executions")
      return { id: "execution-one" } as T;
    assert.equal(JSON.parse(checkpoint).attempt.execution.id, "execution-one");
    if (++links === 1) throw new Error("attachment response lost");
    return {} as T;
  };
  await assert.rejects(
    executeHilTest(
      request,
      work,
      () => {},
      (next) => {
        checkpoint = JSON.stringify(next);
      },
    ),
    /response lost/,
  );
  const recovered = restoreHilPending(checkpoint, "demo", "engineer")!;
  assert.equal(
    (
      await executeHilTest(
        request,
        recovered,
        () => {},
        () => {},
      )
    ).id,
    "execution-one",
  );
  assert.equal(calls.length, 4);
  assert.deepEqual(calls[2], calls[3]);
  assert.deepEqual(calls[2].body, {
    project_id: "demo",
    test_ref: hilTestRef(saved),
    execution_id: "execution-one",
  });
});

test("changed execution preview signals a safe re-preview before any submission or attachment", async () => {
  const work = pending(),
    paths: string[] = [];
  let checkpoint = false;
  const request: HilApi = async <T>(path: string) => {
    paths.push(path);
    return { id: "changed-experiment", preview_hash: "different" } as T;
  };
  await assert.rejects(
    executeHilTest(
      request,
      work,
      () => {},
      () => {
        checkpoint = true;
      },
    ),
    HilPreviewChanged,
  );
  assert.deepEqual(paths, ["/experiments"]);
  assert.equal(work.attempt.execution, undefined);
  assert.equal(checkpoint, false);
});
