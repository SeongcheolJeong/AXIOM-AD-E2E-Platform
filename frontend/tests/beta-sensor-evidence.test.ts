import test from "node:test";
import assert from "node:assert/strict";
import { betaObservationRequest, matchingBetaObservation, sameBetaRef, sensorGuides } from "../src/components/beta-workspace/beta-evidence.ts";
import type { BetaTask } from "../src/components/beta-workspace/beta-contract.ts";

const source = { id: "layout-1", revision: 2, sha256: "a".repeat(64) };
const task = { module: "sensor", project_id: "project-one", source_ref: source,
  execution_id: "run-1", duration_s: 19.9 } as BetaTask;
const observation = { title: "Direction", note: "Observed return points", kind: "observation", time_s: 19.9,
  side: "rig_b", sensor_key: "radar" as const };

test("Sensor observations use the placement endpoint and exact source/sensor/time", () => {
  const request = betaObservationRequest(task, observation);
  assert.equal(request.path, "/sensor-sim/layout-observations");
  assert.deepEqual(request.body, { project_id: "project-one", execution_id: "run-1", layout_ref: source,
    title: "Direction", note: "Observed return points", kind: "observation", time_s: 19.9, side: "rig_b", sensor_key: "radar" });
  assert.throws(() => betaObservationRequest(task, { ...observation, time_s: 20 }), /재생 범위/);
  assert.throws(() => betaObservationRequest(task, { ...observation, side: "faulted" }), /비교 대상/);
  assert.throws(() => betaObservationRequest({ ...task, execution_id: null }, observation), /먼저 연결/);
});

test("Observation selection does not borrow another layout hash or execution", () => {
  const record = { kind: "observation" as const, id: "note-1", label: "Observation", source_ref: source, execution_id: "run-1" };
  assert.ok(matchingBetaObservation(record, source, "run-1"));
  assert.equal(matchingBetaObservation(record, { ...source, sha256: "b".repeat(64) }, "run-1"), false);
  assert.equal(matchingBetaObservation(record, source, "run-2"), false);
  assert.equal(matchingBetaObservation(record, null, "run-1"), false);
  assert.equal(sameBetaRef(source, { ...source, revision: 3 }), false);
  assert.deepEqual(sensorGuides.map(item => item.id), ["camera_direction", "lidar_height", "radar_direction"]);
});

test("Object and HIL observation routes keep their domain references", () => {
  const object = betaObservationRequest({ ...task, module: "object" }, observation);
  assert.equal(object.path, "/object-sim/observations");
  assert.equal(object.body.scenario_ref, source);
  assert.equal("layout_ref" in object.body, false);
  const hil = betaObservationRequest({ ...task, module: "hil" }, { ...observation, side: "both" });
  assert.equal(hil.path, "/hil-sim/observations");
  assert.equal(hil.body.test_ref, source);
  assert.equal(hil.body.side, "both");
});
