import test from "node:test";
import assert from "node:assert/strict";
import {
  contextMatchesPlan,
  executionContextQuery,
  executionContextRows,
  executionVerificationLink,
  loadExecutionContext,
} from "../src/experiment-verification-context.ts";
const execution = {
  id: "job_verified",
  project_id: "project_a",
  experiment_id: "experiment_a",
  experiment_kind: "object_sim" as const,
  status: "completed",
  fidelity: "native_carla_object_physics",
  manifest_hash: "a".repeat(64),
  input_hash: "b".repeat(64),
  name: "Native result",
  created_at: "2026-10-09",
};
const projects = [{ id: "project_a" }];
test("result link resolves only the actual sealed execution with read-only access", async () => {
  const link = executionVerificationLink(execution);
  assert.match(link, /^#validation\?experiment-plan=connect&/);
  const query = executionContextQuery(link)!;
  const reads: string[] = [];
  const verified = await loadExecutionContext(query, projects, async (path) => {
    reads.push(path);
    return execution;
  });
  assert.deepEqual(reads, ["/experiments/executions/job_verified"]);
  assert.equal(verified.project_id, "project_a");
  assert.equal(verified.fidelity, execution.fidelity);
  const rows = executionContextRows(
    [{ ...execution, id: "job_other" }],
    verified,
    verified.project_id,
    verified.experiment_kind,
  );
  assert.deepEqual(
    rows.map((row) => row.id),
    ["job_verified", "job_other"],
  );
});
test("unknown, unavailable project, mismatched hints and unsealed executions never produce a selection", async () => {
  const query = executionContextQuery(executionVerificationLink(execution))!;
  await assert.rejects(
    loadExecutionContext(query, projects, async () => {
      throw new Error("Not found");
    }),
    /Not found/,
  );
  for (const candidate of [
    { ...execution, id: "other" },
    { ...execution, project_id: "unavailable" },
    { ...execution, status: "running" },
    { ...execution, manifest_hash: null },
  ])
    await assert.rejects(
      loadExecutionContext(query, projects, async () => candidate),
    );
  for (const candidate of [
    { ...query, project: "other" },
    { ...query, kind: "planning_replay" },
    { ...query, fidelity: "physical_camera" },
  ])
    await assert.rejects(
      loadExecutionContext(candidate, projects, async () => execution),
      /실제 실행과 다릅니다/,
    );
  await assert.rejects(
    loadExecutionContext(
      { ...query, execution: "../other" },
      projects,
      async () => {
        throw new Error("must not read invalid ID");
      },
    ),
    /ID/,
  );
});
test("only a same-project, same-kind saved plan with exact fidelity keeps the result selected", () => {
  const plan = {
    project_id: execution.project_id,
    experiment_kind: execution.experiment_kind,
    allowed_fidelities: [execution.fidelity],
  };
  assert.equal(contextMatchesPlan(execution, plan), true);
  assert.equal(
    contextMatchesPlan(execution, { ...plan, project_id: "other" }),
    false,
  );
  assert.equal(
    contextMatchesPlan(execution, {
      ...plan,
      experiment_kind: "sensor_capture",
    }),
    false,
  );
  assert.equal(
    contextMatchesPlan(execution, {
      ...plan,
      allowed_fidelities: ["authored_object_kinematics"],
    }),
    false,
  );
  assert.equal(contextMatchesPlan(null, plan), false);
});
test("pinned context stays visible across pages without duplication and never leaks into another project", () => {
  assert.deepEqual(
    executionContextRows([execution], execution, "project_a", "object_sim"),
    [execution],
  );
  assert.deepEqual(
    executionContextRows([], execution, "other", "object_sim"),
    [],
  );
  assert.deepEqual(
    executionContextRows([], execution, "project_a", "sensor_capture"),
    [],
  );
  assert.throws(
    () =>
      executionContextRows(
        [{ ...execution, input_hash: "c".repeat(64) }],
        execution,
        "project_a",
        "object_sim",
      ),
    /근거가 다릅니다/,
  );
  assert.equal(
    executionContextQuery("#validation?experiment-plan=connect"),
    null,
  );
});
