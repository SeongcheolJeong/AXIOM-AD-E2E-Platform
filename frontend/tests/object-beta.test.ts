import test from "node:test";
import assert from "node:assert/strict";
import { betaChangeParameter, betaDraftFromPreset, betaIssues, betaPreviewBody, betaSourceMatches, betaStorageKey, restoreBetaSession, nextBetaAttempt, saveBetaScenario } from "../src/components/object-demo/beta-contract.ts";
import type { BetaSession } from "../src/components/object-demo/beta-contract.ts";
import type { DemoApi, DemoPreset } from "../src/components/object-demo/demo-contract.ts";
import type { CityRecord, CityScenario } from "../src/components/city-sim/city-contract.ts";

const template = {
  id: "lead_stop_start", title: "앞차 정지·재출발", ready: true, binding_revision: 2,
  scenario_ref: { id: "source-1", revision: 3, configuration_hash: "a".repeat(64), manifest_hash: "b".repeat(64) },
  defaults: { ego_speed_mps: 4, lead_speed_mps: 3, lead_wait_s: 6, lead_start_s: 2 },
} as DemoPreset;

test("draft restoration is isolated by both authenticated principal and project", () => {
  const state: BetaSession = { version: 1, project: "project-a", identity: "principal-a", draft: betaDraftFromPreset(template), attempt: null, baselineId: "baseline-a" };
  const raw = JSON.stringify(state);
  assert.deepEqual(restoreBetaSession(raw, "project-a", "principal-a"), state);
  assert.equal(restoreBetaSession(raw, "project-b", "principal-a"), null);
  assert.equal(restoreBetaSession(raw, "project-a", "principal-b"), null);
  assert.notEqual(betaStorageKey("a:b", "c"), betaStorageKey("a", "b:c"));
  assert.notEqual(betaStorageKey("project-a", "principal-a"), betaStorageKey("project-a", "principal-b"));
  assert.equal(restoreBetaSession("bad JSON", "project-a", "principal-a"), null);
  assert.equal(restoreBetaSession(JSON.stringify({ ...state, executed: { id: "run" } }), "project-a", "principal-a"), null);
});

test("restored drafts refuse changed binding, immutable source revision, or source hash", () => {
  const draft = betaDraftFromPreset(template);
  assert.equal(betaSourceMatches(draft, template), true);
  assert.equal(betaSourceMatches(draft, { ...template, binding_revision: 3 }), false);
  assert.equal(betaSourceMatches(draft, { ...template, scenario_ref: { ...template.scenario_ref!, revision: 4 } }), false);
  assert.equal(betaSourceMatches(draft, { ...template, scenario_ref: { ...template.scenario_ref!, configuration_hash: "c".repeat(64) } }), false);
  assert.equal(betaSourceMatches(draft, { ...template, scenario_ref: null }), false);
});

test("selected actor speed is submitted once without conflicting with the template speed parameter", () => {
  const draft = betaDraftFromPreset(template);
  draft.roles = { ego: "vehicle_ego", lead: "vehicle_lead" };
  draft.actorEdits.vehicle_lead = { speed_mps: 5 };
  const body = betaPreviewBody("project-a", draft);
  assert.equal(body.parameters.lead_speed_mps, undefined);
  assert.equal(body.parameters.ego_speed_mps, 4);
  assert.equal(body.actor_edits.vehicle_lead.speed_mps, 5);
  assert.equal(draft.parameters.lead_speed_mps, 3, "serialization must not mutate the draft");
  assert.deepEqual(body.source_scenario_ref, template.scenario_ref);
});

test("changing a guided behavior condition clears its old action override but preserves route edits", () => {
  const draft = betaDraftFromPreset(template);
  draft.roles = { lead: "lead-car" };
  const destination = { x: 50, y: 5, z: 0, heading_rad: 0 };
  draft.actorEdits["lead-car"] = { destination, actions: [], speed_mps: 4 };
  const changed = betaChangeParameter(draft, "lead_wait_s", 0);
  assert.equal(changed.parameters.lead_wait_s, 0);
  assert.equal(changed.actorEdits["lead-car"].actions, undefined);
  assert.deepEqual(changed.actorEdits["lead-car"].destination, destination);
  assert.equal(changed.actorEdits["lead-car"].speed_mps, 4);
  assert.deepEqual(draft.actorEdits["lead-car"].actions, []);
});

test("structured server validation preserves the actor and field for actionable errors", () => {
  assert.deepEqual(betaIssues(JSON.stringify({ code: "object_sim_edit_invalid", issues: [{ actor_id: "ego", field: "destination", code: "invalid_route", message: "No connected lane route" }] })), [{ actor_id: "ego", field: "destination", code: "invalid_route", message: "No connected lane route" }]);
  assert.deepEqual(betaIssues("Network unavailable"), [{ field: "scenario", message: "Network unavailable" }]);
});

test("uncertain submission checkpoints survive refresh with the exact idempotency key", () => {
  const state: BetaSession = { version: 1, project: "project-a", identity: "principal-a", draft: betaDraftFromPreset(template), baselineId: "baseline", attempt: { key: "fixed-request", signature: "pinned-input", prepared: { preview_hash: "hash" }, experiment: { id: "experiment-a", preview_hash: "hash" } } };
  const restored = restoreBetaSession(JSON.stringify(state), state.project, state.identity)!;
  assert.equal(restored.attempt?.key, "fixed-request");
  assert.equal(restored.attempt?.experiment?.id, "experiment-a");
  assert.equal(restored.attempt?.execution, undefined);
});

test("editing an existing save appends a revision; rerunning unchanged input reuses it", async () => {
  const saved = { id: "my-scenario", revision: 2, configuration_hash: "a", scenario: { name: "Mine", seed: 42 } } as CityRecord;
  const previous = { key: "old", signature: "old-input", saved, execution: { id: "old-run" } };
  const rerun = nextBetaAttempt(previous, "old-input", "new-run");
  assert.equal(rerun.saved, saved);
  assert.equal(rerun.execution, undefined);
  const edited = nextBetaAttempt(previous, "new-input", "new-edit");
  assert.deepEqual(edited.revisionBase, { id: "my-scenario", revision: 2 });
  assert.equal(edited.saved, undefined);
  const calls: { path: string; body: Record<string, unknown>; key: string }[] = [];
  const request: DemoApi = async <T>(path: string, init?: RequestInit) => {
    calls.push({ path, body: JSON.parse(init!.body as string), key: (init!.headers as Record<string,string>)["Idempotency-Key"] });
    return { ...saved, revision: 3 } as T;
  };
  await saveBetaScenario(request, "project-a", { ...saved.scenario, name: "Edited" } as CityScenario, edited);
  await saveBetaScenario(request, "project-a", saved.scenario, edited);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, "/scenario-v2/my-scenario/revisions");
  assert.equal(calls[0].body.expected_revision, 2);
  assert.equal(calls[0].key, "new-edit-scenario");
  assert.equal(edited.saved?.revision, 3);
});
