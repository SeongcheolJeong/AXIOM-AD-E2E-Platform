import test from "node:test";
import assert from "node:assert/strict";
import {
  betaDraftFromPreset,
  betaPreviewMatchesBinding,
  betaSubmissionDefinitelyRejected,
  betaUnresolvedSubmission,
  nextBetaAttempt,
  restoreBetaSession,
} from "../src/components/object-demo/beta-contract.ts";
import type {
  BetaAttempt,
  BetaPreview,
  BetaSession,
} from "../src/components/object-demo/beta-contract.ts";
import {
  executeDemo,
  type DemoApi,
  type DemoPreset,
} from "../src/components/object-demo/demo-contract.ts";
import type {
  CityRecord,
  CityScenario,
} from "../src/components/city-sim/city-contract.ts";

const template = {
  id: "lead_stop_start",
  title: "앞차 정지·재출발",
  ready: true,
  binding_revision: 2,
  scenario_ref: {
    id: "source-a",
    revision: 3,
    configuration_hash: "a".repeat(64),
    manifest_hash: "b".repeat(64),
  },
  defaults: { ego_speed_mps: 4 },
} as DemoPreset;
const saved = {
  id: "scenario-a",
  project_id: "project-a",
  revision: 4,
  configuration_hash: "c".repeat(64),
  manifest_hash: "d".repeat(64),
  scenario: { name: "Pinned scenario", seed: 42 },
} as CityRecord;
const experiment = { id: "experiment-a", preview_hash: "preview-a" };

test("a restored unresolved submission cannot be replaced by edits or a new request key", () => {
  for (const pending of [undefined, true]) {
    const prior: BetaAttempt = {
      key: "accepted-request",
      signature: "original-input",
      saved,
      experiment,
      ...(pending === undefined ? {} : { submissionPending: pending }),
    };
    assert.equal(betaUnresolvedSubmission(prior), true);
    assert.equal(nextBetaAttempt(prior, "edited-input", "different-key"), prior);
    assert.equal(nextBetaAttempt(prior, "original-input", "different-key"), prior);
    assert.equal(prior.key, "accepted-request");
  }
});

test("pre-submission edits and an acknowledged execution still permit deliberate new runs", () => {
  const prepared: BetaAttempt = {
    key: "prepared-request",
    signature: "original-input",
    saved,
    experiment,
    submissionPending: false,
  };
  assert.equal(betaUnresolvedSubmission(prepared), false);
  assert.equal(nextBetaAttempt(prepared, "original-input", "unused-key"), prepared);
  const edited = nextBetaAttempt(prepared, "edited-input", "new-request");
  assert.equal(edited.key, "new-request");
  assert.deepEqual(edited.revisionBase, { id: saved.id, revision: saved.revision });
  assert.equal(edited.experiment, undefined);
  const acknowledged = { ...prepared, submissionPending: true, execution: { id: "run-a" } };
  assert.equal(betaUnresolvedSubmission(acknowledged), false);
  const rerun = nextBetaAttempt(acknowledged, "original-input", "rerun-request");
  assert.equal(rerun.saved, saved);
  assert.equal(rerun.key, "rerun-request");
  assert.equal(rerun.execution, undefined);
  assert.equal(rerun.submissionPending, false);
});

test("only definite first-submission admission rejections unlock the attempt", () => {
  assert.equal(betaSubmissionDefinitelyRejected(422, false), true);
  assert.equal(betaSubmissionDefinitelyRejected(429, false), true);
  for (const status of [undefined, 400, 401, 403, 404, 408, 409, 500, 502, 503, 504]) {
    assert.equal(betaSubmissionDefinitelyRejected(status, false), false);
  }
  for (const status of [undefined, 401, 403, 409, 422, 429, 500]) {
    assert.equal(betaSubmissionDefinitelyRejected(status, true), false,
      "a recovery error cannot prove that an earlier ambiguous submission was not accepted");
  }
});

test("an accepted execution with a lost response is restored and recovered with the original key", async () => {
  const signature = JSON.stringify({
    project: "project-a",
    scenario: saved.scenario,
    adapter: "carla-city-object",
    version: "pinned-adapter-version",
  });
  const attempt: BetaAttempt = {
    key: "accepted-request",
    signature,
    saved,
    submissionPending: false,
  };
  let checkpoint = "";
  const persist = (current: BetaAttempt) => {
    checkpoint = JSON.stringify({
      version: 1,
      project: "project-a",
      identity: "principal-a",
      draft: betaDraftFromPreset(template),
      attempt: current,
      baselineId: null,
    } satisfies BetaSession);
  };
  const jobsByKey = new Map<string, { id: string }>();
  const calls: { path: string; key?: string; body: Record<string, unknown> }[] = [];
  let loseResponse = true;
  const request: DemoApi = async <T>(path: string, init?: RequestInit) => {
    const key = (init?.headers as Record<string, string> | undefined)?.["Idempotency-Key"];
    const body = JSON.parse(init!.body as string);
    calls.push({ path, key, body });
    if (path === "/experiments/preview") return { preview_hash: experiment.preview_hash } as T;
    if (path === "/experiments") return experiment as T;
    assert.equal(path, "/experiments/experiment-a/executions");
    assert.equal(key, "accepted-request-execution");
    assert.deepEqual(body, { preview_hash: experiment.preview_hash });
    if (!jobsByKey.has(key!)) jobsByKey.set(key!, { id: "accepted-run-a" });
    if (loseResponse) {
      loseResponse = false;
      throw new Error("Response lost after acceptance");
    }
    return jobsByKey.get(key!) as T;
  };
  const progress = (current: BetaAttempt) => (stage: string) => {
    if (stage === "실행 큐에 제출") current.submissionPending = true;
    persist(current);
  };
  const adapter = { id: "carla-city-object", version: "pinned-adapter-version" };
  await assert.rejects(
    executeDemo(request, "project-a", saved.scenario, adapter, attempt, progress(attempt)),
    /Response lost after acceptance/,
  );
  assert.equal(jobsByKey.size, 1);
  const restored = restoreBetaSession(checkpoint, "project-a", "principal-a")!.attempt!;
  assert.equal(betaUnresolvedSubmission(restored), true);
  assert.equal(restored.execution, undefined);
  assert.deepEqual(restored.saved, saved);
  assert.equal(nextBetaAttempt(restored, "user-edited-input", "would-duplicate"), restored);
  const recovered = await executeDemo(
    request,
    "project-a",
    restored.saved!.scenario,
    adapter,
    restored,
    progress(restored),
  );
  assert.deepEqual(recovered, { id: "accepted-run-a" });
  assert.equal(betaUnresolvedSubmission(restored), false);
  assert.equal(jobsByKey.size, 1);
  assert.deepEqual(calls.map((call) => call.path), [
    "/experiments/preview",
    "/experiments",
    "/experiments/experiment-a/executions",
    "/experiments/experiment-a/executions",
  ]);
  assert.deepEqual(calls[0].body.input, {
    scenario_id: saved.id,
    scenario_revision: saved.revision,
    configuration_hash: saved.configuration_hash,
  });
});

test("preview acceptance requires the exact project, template, binding and both immutable source pins", () => {
  const draft = betaDraftFromPreset(template);
  const preview = {
    preset_id: draft.templateId,
    project_id: "project-a",
    binding_revision: draft.bindingRevision,
    scenario_ref: { ...draft.source },
    source_scenario_ref: { ...draft.source },
    scenario: saved.scenario as CityScenario,
  } as BetaPreview;
  assert.equal(betaPreviewMatchesBinding(draft, preview, "project-a"), true);
  assert.equal(betaPreviewMatchesBinding(draft, { ...preview, binding_revision: 3 }, "project-a"), false);
  assert.equal(betaPreviewMatchesBinding(draft, { ...preview, project_id: "project-b" }, "project-a"), false);
  assert.equal(betaPreviewMatchesBinding(draft, { ...preview, preset_id: "cut_in" }, "project-a"), false);
  for (const field of ["scenario_ref", "source_scenario_ref"] as const) {
    for (const changed of [
      { id: "another-source" },
      { revision: 4 },
      { configuration_hash: "e".repeat(64) },
      { manifest_hash: "f".repeat(64) },
    ]) {
      assert.equal(betaPreviewMatchesBinding(draft, {
        ...preview,
        [field]: { ...preview[field], ...changed },
      }, "project-a"), false, `${field} change must invalidate the preview`);
    }
    assert.equal(betaPreviewMatchesBinding(draft, {
      ...preview,
      [field]: undefined,
    } as unknown as BetaPreview, "project-a"), false);
  }
});
