import { expect, type Page, type Locator } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { authoredContext } from "./carla-live-fixture";

// Real isolated asset/suite authoring; CARLA metadata and campaign results are
// explicitly authored HTTP fixtures. This helper never dispatches CARLA jobs.
export async function campaignFixture(
  page: Page,
  count = 2,
  invalidLast = false,
) {
  const { context, scenario } = await authoredContext(page.request);
  let invalid = scenario;
  if (invalidLast) {
    const response = await page.request.post("/api/v1/scenarios", {
      data: {
        name: `Authored unsupported case ${randomUUID()}`,
        category: "cut_in",
        duration_s: 6,
        parameters: { trigger_time_s: 1 },
      },
    });
    expect(response.status()).toBe(201);
    invalid = await response.json();
  }
  const criterionResponse = await page.request.post("/api/v1/requirements", {
    data: {
      name: `Authored criterion ${randomUUID()}`,
      metric_name: "collision_count",
      operator: "eq",
      threshold: 0,
      scenario_ids: [...new Set([scenario.id, invalid.id])],
    },
  });
  expect(criterionResponse.status()).toBe(201);
  const criterion = await criterionResponse.json();
  const suiteResponse = await page.request.post("/api/v1/suites", {
    data: {
      name: `Authored campaign control ${randomUUID()}`,
      description: "Browser contract; no CARLA execution",
      cases: Array.from({ length: count }, (_, i) => ({
        key: `case-${i + 1}`,
        name: `Pinned member ${i + 1}`,
        scenario_id: i === count - 1 ? invalid.id : scenario.id,
        scenario_revision: 1,
        seed: 41 + i,
        parameter_overrides: { initial_gap_m: 30 + i },
        requirement_ids: [criterion.id],
      })),
    },
  });
  expect(suiteResponse.status(), await suiteResponse.text()).toBe(201);
  const suite = await suiteResponse.json();
  const capability = (
    await (
      await page.request.get(
        "/api/v1/simulation-sensor-capabilities?adapter_id=carla",
      )
    ).json()
  )[0];
  const configBody = {
    name: `Campaign recipe ${randomUUID()}`,
    description: "Authored browser recipe",
    note: "No actual execution",
    adapter_id: "carla",
    capability_id: capability.id,
    capability_version: capability.version,
    parameters: {
      ...capability.default_parameters,
      horizontal_fov: 175,
      points_per_second: 64000,
    },
  };
  const configResponse = await page.request.post(
    "/api/v1/simulation-sensor-configurations",
    { data: configBody, headers: { "Idempotency-Key": randomUUID() } },
  );
  expect(configResponse.status()).toBe(201);
  const configuration = await configResponse.json();
  let adapter: any;
  await page.route("**/api/v1/adapters", async (route) => {
    const data = await (await route.fetch()).json();
    adapter = data.find((item: any) => item.id === "carla");
    Object.assign(adapter, {
      version: "0.8.0",
      status: "available",
      capabilities: ["lane_follow", "emergency_brake", "live_lidar_stack"],
      description:
        "Authored available CARLA metadata; no actual engine used by this test",
    });
    await route.fulfill({ json: data });
  });
  await page.goto("/#suites");
  await page
    .getByLabel("Select test suite", { exact: true })
    .selectOption(suite.id);
  await page.getByRole("button", { name: "Run suite", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "Run a test suite",
    exact: true,
  });
  await dialog
    .getByLabel("Campaign label", { exact: true })
    .fill("Authored CARLA campaign contract");
  await dialog
    .getByRole("combobox", { name: "Campaign adapter", exact: true })
    .selectOption("carla");
  return {
    context,
    scenario,
    suite,
    configuration,
    capability,
    configBody,
    dialog,
    getAdapter: () => adapter,
  };
}
export async function chooseControl(
  dialog: Locator,
  input: Awaited<ReturnType<typeof campaignFixture>>,
  configured = true,
) {
  await dialog
    .getByLabel("CARLA campaign controller mode")
    .selectOption("stack");
  await expect(dialog.getByLabel("Live CARLA sensor setup")).toHaveValue(
    "profile",
  );
  if (configured)
    await dialog
      .getByLabel("Live CARLA sensor setup")
      .selectOption("configuration");
  await dialog
    .getByLabel("Live CARLA stack", { exact: true })
    .selectOption(input.context.stack_snapshot.id);
  await dialog.getByLabel("Live CARLA stack revision").selectOption("1");
  await dialog
    .getByLabel("Live CARLA sensor rig")
    .selectOption(input.context.rig_snapshot.id);
  await dialog.getByLabel("Live CARLA rig revision").selectOption("1");
  await dialog.getByLabel("Live CARLA LiDAR sensor").selectOption("roof");
  if (configured) {
    await dialog
      .getByLabel("Live CARLA sensor configuration", { exact: true })
      .selectOption(input.configuration.id);
    await dialog
      .getByLabel("Live CARLA sensor configuration revision")
      .selectOption("1");
  }
}
export function authoredCampaign(
  input: Awaited<ReturnType<typeof campaignFixture>>,
  control: any,
) {
  const { suite } = input;
  const context: any = structuredClone(input.context);
  if (control.schema_version) {
    context.schema_version = "simulation-control-context/2";
    context.physical_mount_policy = "carla_rh_rig_to_lh_native_matrix/1";
    context.source_serialization_policy = "carla_configured_lidar_json/1";
    context.sensor_configuration_snapshot = input.configuration;
    context.sensor_capability_snapshot = input.capability;
    context.sensor_profile.id = "carla-raycast-lidar/1";
    context.sensor_profile.attributes = Object.fromEntries(
      Object.entries(input.configuration.parameters).map(([k, v]) => [
        k,
        String(v),
      ]),
    );
    context.sensor_profile.attributes.noise_seed = "41";
    context.sensor_profile.max_points_per_frame = 20000;
    delete context.sensor_profile.max_points;
  }
  const members = suite.cases.map((c: any, i: number) => ({
    key: c.key,
    job_id: `job_authored_campaign_${i}`,
    input_hash: String(i + 1).repeat(64),
    context_hash: i === 0 ? context.context_hash : "e".repeat(64),
    request_hash: "f".repeat(64),
  }));
  return {
    id: `campaign_authored_${randomUUID()}`,
    submission_seq: 1,
    label: "Authored CARLA campaign contract",
    suite_id: suite.id,
    suite_revision: suite.revision,
    suite_name: suite.name,
    adapter_id: "carla",
    adapter_version: "0.8.0",
    created_at: new Date().toISOString(),
    evaluated_at: new Date().toISOString(),
    status: "finished",
    verdict: "incomplete",
    evaluation_complete: false,
    evaluator_version: suite.evaluator_version,
    suite_snapshot: suite,
    adapter_snapshot: input.getAdapter(),
    counts: {
      total: members.length,
      pending: 0,
      completed: 1,
      failed: members.length - 1,
      cancelled: 0,
      interrupted: 0,
      missing: 0,
      passed_cases: 1,
      failed_cases: 0,
      not_evaluated_cases: members.length - 1,
      checks_total: members.length,
      checks_passed: 1,
      checks_failed: 0,
      checks_not_evaluated: members.length - 1,
    },
    execution_plan: {
      schema_version: "campaign-execution-plan/1",
      metric_contract_version: "carla-observed-contact-count/1",
      control,
      context_template: context,
      members,
      plan_hash: "b".repeat(64),
    },
    cases: suite.cases.map((c: any, i: number) => ({
      key: c.key,
      name: c.name,
      scenario_id: c.scenario_id,
      scenario_revision: c.scenario_revision,
      effective_input_hash: c.effective_input_hash,
      criteria_hash: c.criteria_hash,
      evaluator_version: suite.evaluator_version,
      job_id: members[i].job_id,
      run_id: i ? null : "run_authored_observed_contacts",
      job_status: i ? "failed" : "completed",
      verdict: i ? "not_evaluated" : "passed",
      evaluation_complete: i === 0,
      reason: i
        ? "Authored HTTP report: observation evidence missing; no native CARLA execution"
        : null,
      metrics: i
        ? []
        : [
            {
              name: "collision_count",
              value: 0,
              unit: "observed contacts",
              threshold: 0,
              operator: "eq",
              passed: true,
            },
          ],
      expected_execution: members[i],
      execution_context: {
        adapter_id: "carla",
        adapter_version: "0.8.0",
        model_fidelity: "Authored HTTP contract; not actual CARLA",
        result_schema_version: "1",
        input_hash: i ? null : members[i].input_hash,
        output_hash: i ? null : "a".repeat(64),
        control_context_hash: i ? null : members[i].context_hash,
      },
      checks: c.requirements.map((r: any) => ({
        requirement_id: r.id,
        name: r.definition.name,
        metric_name: r.definition.metric_name,
        operator: r.definition.operator,
        threshold: r.definition.threshold,
        expected_unit: r.unit,
        status: i ? "not_evaluated" : "passed",
        value: i ? null : 0,
        unit: i ? null : "observed contacts",
        ...(i
          ? {}
          : { measurement_contract: "carla-observed-contact-count/1" }),
        unbounded: false,
        reason: i ? "Authored missing result evidence" : null,
      })),
    })),
  };
}
