import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { decisionCameraStates, presentationCameraKey } from "../src/components/city-sim/city-camera.ts";
import { decisionLabel, decisionRelations, followGapTarget } from "../src/components/city-sim/city-decisions.ts";
import type { CityScene, CityState } from "../src/components/city-sim/city-scene.ts";

function state(id: string, x: number, diagnostics: Record<string, unknown> = {}): CityState {
  return { id, x, y: 0, z: 1, heading_rad: 0, speed_mps: 2, desired_speed_mps: 2, controller_state: "reactive", control: null,
    plan: { reactive: { state: "active_lane_gap_wait", reason: "active_lane_gap_wait", route_revision: 1, action_id: null, diagnostics, speed_mps: 0, stop: true } } };
}

test("camera focuses only observed decision relationships and preserves current native poses", () => {
  const ego = state("ego", 0, { active_lateral_gap: { gap_checks: [{ actor_id: "rear", gap_m: 2.5, required_gap_m: 8 }] }, yield_actor_id: "remote" });
  const rear = state("rear", -12), nearby = state("uninvolved", 3), remote = state("remote", 180);
  const scene = { actors: [{ id: "ego", role: "ego" }], frames: [{ actors: [ego, rear, nearby, remote] }] } as unknown as CityScene;
  const source = JSON.stringify(scene);
  const focus = decisionCameraStates(scene, 0, "ego");
  assert.deepEqual(focus.map((actor) => actor.id), ["ego", "rear"]);
  assert.equal(focus[1], rear);
  assert.equal(JSON.stringify(scene), source);
});

test("story anchor chooses target identity but camera always uses the current retained target pose", () => {
  const anchor = state("ego", 0, { yield_actor_id: "rear" }), before = state("rear", -20), now = state("rear", 10);
  const scene = { actors: [{ id: "ego", role: "ego" }], frames: [{ actors: [anchor, before] }, { actors: [state("ego", 3), now] }] } as unknown as CityScene;
  assert.equal(decisionCameraStates(scene, 1, "ego", 0).find((actor) => actor.id === "rear"), now);
  assert.equal(decisionRelations(scene.frames[1].actors[0], scene.frames[1].actors).length, 0);
});

test("without diagnostic targets the camera includes bounded nearby static scene context without inventing a relationship", () => {
  const ego = state("ego", 0), parked = { ...state("parked", 38), speed_mps: 0 };
  const moving = [1, 2, 3, 4, 5, 80].map((x) => state(`moving-${x}`, x));
  const scene = { actors: [{ id: "ego", role: "ego" }, { id: "parked", role: "other", controller: { kind: "static" } }], frames: [{ actors: [ego, ...moving, parked] }] } as unknown as CityScene;
  const focus = decisionCameraStates(scene, 0, "ego");
  assert.equal(focus.length, 5);
  assert.equal(focus.find((actor) => actor.id === "parked"), parked);
  assert.equal(focus.some((actor) => actor.id === "moving-80"), false);
  assert.equal(focus.some((actor) => actor.id === "moving-5"), false);
  assert.deepEqual(decisionRelations(ego, scene.frames[0].actors), []);
});

test("native gap diagnostics show observed and required gaps without deriving safety metrics", () => {
  const ego = state("ego", 0, { active_lateral_gap: { gap_checks: [{ actor_id: "rear", gap_m: 2.5, required_gap_m: 8 }] } }), rear = state("rear", -10);
  const relation = decisionRelations(ego, [ego, rear])[0];
  assert.equal(relation.to.id, "rear"); assert.equal(relation.gapM, 2.5); assert.equal(relation.requiredGapM, 8);
  assert.equal(Object.hasOwn(relation, "ttc"), false);
  assert.equal(decisionLabel("active_lane_gap_wait"), "진입 공간이 열릴 때까지 대기");
});

test("follow-gap highlight names only the current diagnostic lead and clears after the reason changes", () => {
  const ego = state("ego", 0, { observation_basis: "current_snapshot_ground_truth", lead: { actor_id: "lead", gap_m: 5.4, speed_mps: 0 } });
  (ego.plan!.reactive as { reason: string }).reason = "follow_gap";
  const lead = state("lead", 10), nearer = state("nearer", 2);
  const actors = [ego, nearer, lead], source = JSON.stringify(actors);
  assert.deepEqual(followGapTarget(ego, actors), { target: lead, gapM: 5.4, leadSpeedMps: 0 });
  assert.equal(JSON.stringify(actors), source);
  assert.equal(followGapTarget(ego, [ego, nearer]), null);
  (ego.plan!.reactive as { reason: string }).reason = "follow_lead_stop";
  assert.deepEqual(followGapTarget(ego, actors), { target: lead, gapM: 5.4, leadSpeedMps: 0 });
  assert.equal(decisionLabel("follow_lead_stop"), "앞차 정지에 맞춰 대기");
  (ego.plan!.reactive as { reason: string }).reason = "route_follow";
  assert.equal(followGapTarget(ego, actors), null);
});

test("missing or nonfinite lead diagnostics never create a decision target", () => {
  const lead = state("lead", 10);
  for (const diagnostics of [{}, { lead: { actor_id: "lead", gap_m: 5, speed_mps: 0 } }, { observation_basis: "current_snapshot_ground_truth", lead: { actor_id: "lead", gap_m: NaN, speed_mps: 0 } }, { observation_basis: "current_snapshot_ground_truth", lead: { actor_id: "ego", gap_m: 0, speed_mps: 0 } }]) {
    const ego = state("ego", 0, diagnostics);
    for (const reason of ["follow_gap", "follow_lead_stop"]) {
      (ego.plan!.reactive as { reason: string }).reason = reason;
      assert.equal(followGapTarget(ego, [ego, lead]), null);
    }
  }
});

test("requested front or chase camera never silently falls back to a different recording", () => {
  assert.equal(presentationCameraKey(["front_rgb", "chase_rgb"], "front"), "front_rgb");
  assert.equal(presentationCameraKey(["front_rgb", "chase_rgb"], "chase"), "chase_rgb");
  assert.equal(presentationCameraKey(["front_rgb"], "chase"), undefined);
  assert.equal(presentationCameraKey(["unknown-camera"], "front"), undefined);
});

test("vendored passenger visual bytes, linked texture and CC0 license match the source manifest", () => {
  const directory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../public/assets/kenney-car-kit");
  const manifest = JSON.parse(fs.readFileSync(path.join(directory, "manifest.json"), "utf8"));
  assert.equal(manifest.license, "CC0-1.0");
  for (const file of manifest.files) {
    const bytes = fs.readFileSync(path.join(directory, file.path));
    assert.equal(bytes.length, file.size_bytes);
    assert.equal(createHash("sha256").update(bytes).digest("hex"), file.sha256);
  }
  assert.match(fs.readFileSync(path.join(directory, "License.txt"), "utf8"), /Creative Commons Zero, CC0/);
  const glb = fs.readFileSync(path.join(directory, "sedan.glb"));
  assert.equal(glb.toString("utf8", 0, 4), "glTF");
  const model = JSON.parse(glb.toString("utf8", 20, 20 + glb.readUInt32LE(12)));
  assert.equal((model.extensionsUsed ?? []).includes("KHR_draco_mesh_compression"), false);
  assert.equal((model.extensionsUsed ?? []).includes("EXT_meshopt_compression"), false);
  assert.ok(model.nodes.filter((node: { name: string }) => node.name.startsWith("wheel-front")).every((node: { translation: number[] }) => node.translation[2] > 0));
  for (const texture of model.images) assert.ok(fs.existsSync(path.join(directory, texture.uri)));
  assert.equal(model.animations, undefined);
});
