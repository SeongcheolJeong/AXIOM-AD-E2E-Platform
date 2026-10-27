import { test, expect, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

// Authored diagnostic fixtures for browser/protocol checks. No CARLA execution
// or vehicle-dynamics acceptance is performed by this test file.
const POLICY = {
  version: "carla-startup-stability/1", min_ticks: 8, max_ticks: 100,
  consecutive_stable_samples: 5, max_linear_speed_mps: 0.1,
  max_vertical_speed_mps: 0.05, max_angular_speed_deg_s: 1,
  max_position_step_m: 0.005, max_rotation_step_deg: 0.05,
  max_acceleration_mps2: 0.5, max_wall_time_s: 60,
  max_tick_rpc_time_s: 10, clock_tolerance_s: 0.000001,
};
const vector = (z = 0) => ({ x: 0, y: 0, z });
function report(suffix = "a") {
  return {
    schema_version: "1", policy: { ...POLICY }, fixed_delta_seconds: 0.05,
    max_simulated_duration_s: 5, status: "passed", reason_code: "stable_rest_observed",
    reason: "Authored diagnostic fixture: both actors satisfy the recorded rest criteria.",
    actor_ids: { ego: 11, lead: 12 }, initial_world_frame: 100,
    initial_elapsed_seconds: 5, completed_ticks: 8, stable_streak: 5,
    elapsed_simulated_seconds: 0.4, elapsed_wall_seconds: 0.8,
    limitations: ["Authored diagnostic fixture for browser checks; not an actual CARLA run."],
    context: {
      adapter_version: "0.3.0", scenario_id: `startup_scenario_${suffix}`,
      scenario_revision: 1, seed: 41, server_version: "authored-fixture",
      map_name: "/Game/Carla/Maps/Town10HD_Opt", stage: "rest_before_initial_target_velocity",
      requested_initial_speeds_mps: { ego: 8, lead: 6 },
      spawn_z_offset_m: 0.3, spawn_transforms: {},
    },
    trace: Array.from({ length: 8 }, (_, index) => ({
      tick: index + 1, world_frame: 101 + index, tick_returned_frame: 101 + index,
      elapsed_seconds: 5 + (index + 1) * 0.05, delta_seconds: 0.05,
      invalid_fields: [] as { field: string; observed: string }[],
      stable: index >= 3, stable_streak: Math.max(0, index - 2),
      actors: Object.fromEntries((["ego", "lead"] as const).map((role, actorIndex) => {
        const vz = index < 3 ? -(3 - index) * (actorIndex ? 0.2 : 0.3) : 0.01;
        return [role, {
          actor_id: 11 + actorIndex, stable: index >= 3,
          position_m: vector(0.1), rotation_deg: { pitch: 0, yaw: 0, roll: 0 },
          velocity_mps: vector(vz), angular_velocity_deg_s: vector(), acceleration_mps2: vector(),
          invalid_fields: [] as { field: string; observed: string }[],
          derived: { linear_speed_mps: Math.abs(vz), absolute_vertical_speed_mps: Math.abs(vz),
            angular_speed_deg_s: 0, acceleration_norm_mps2: 0,
            position_step_m: index ? 0 : null, rotation_step_deg: index ? 0 : null },
          criteria: { linear_speed: index >= 3, vertical_speed: index >= 3,
            angular_speed: true, acceleration: true, position_step: index > 0, rotation_step: index > 0 },
        }];
      })),
    })),
  };
}
type Fixture = ReturnType<typeof fixture>;
function fixture(suffix = "a", document = report(suffix)) {
  const bytes = Buffer.from(JSON.stringify(document));
  const artifact = { id: `artifact_startup_${suffix}`, run_id: `run_startup_${suffix}`,
    name: "carla-startup.json", kind: "startup_manifest", media_type: "application/json",
    size_bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex"),
    created_at: "2026-09-13T12:00:00Z", download_url: `/api/v1/artifacts/artifact_startup_${suffix}/download`, source: "authored browser diagnostic fixture" };
  const scenario = { id: `startup_scenario_${suffix}`, name: `Authored startup fixture ${suffix}`,
    revision: 1, seed: 41, category: "lane_follow", description: "Authored fixture, not actual CARLA evidence",
    map_name: "Town10HD_Opt", weather: "clear", time_of_day: "day", tags: [], duration_s: 4,
    parameters: { ego_speed_mps: 8, target_speed_mps: 6, initial_gap_m: 35, trigger_time_s: 3, friction: 0.9, response_delay_s: 0.5 },
    created_at: artifact.created_at, updated_at: artifact.created_at };
  const run = { id: artifact.run_id, scenario_id: scenario.id, scenario_name: scenario.name,
    scenario_revision: scenario.revision, adapter_id: "carla", status: "error", verdict: "error",
    created_at: artifact.created_at, duration_s: 4, execution_ms: 10, metrics: [], error: "Authored browser fixture",
    provenance: { input_hash: "1".repeat(64), output_hash: "2".repeat(64), seed: 41, adapter_version: "0.3.0", model_fidelity: "Authored diagnostic fixture; not CARLA execution" },
    scenario_snapshot: scenario, trajectory: [], events: [], artifacts: [artifact] };
  return { document, bytes, artifact, run };
}

async function install(page: Page, fixtures: Fixture[]) {
  await page.route("**/api/v1/overview", async (route) => {
    const original = await (await route.fetch()).json();
    await route.fulfill({ json: { ...original, recent_runs: fixtures.map((item) => item.run) } });
  });
  await page.route("**/api/v1/runs", (route) => route.fulfill({ json: fixtures.map((item) => item.run) }));
  for (const item of fixtures) {
    await page.route(`**/api/v1/runs/${item.run.id}`, (route) => route.fulfill({ json: item.run }));
    await page.route(`**${item.artifact.download_url}`, (route) => route.fulfill({ contentType: "application/json", body: item.bytes }));
  }
  await page.goto("/#runs");
}

test("authored startup evidence renders verified plots and downloads exact bytes", async ({ page }) => {
  const item = fixture(), errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await install(page, [item]);
  const panel = page.getByTestId("carla-startup-verified");
  await expect(panel).toContainText("Rest criteria met");
  await expect(panel).toContainText("Stable streak 5 / 5");
  await expect(page.getByText(/Settlement is not a measured-driving pass/)).toBeVisible();
  await expect(panel.getByRole("img", { name: "Vertical velocity by startup tick", exact: true })).toBeVisible();
  await expect(panel.getByRole("img", { name: "Linear speed by startup tick", exact: true })).toBeVisible();
  await expect(panel.getByRole("img", { name: "Consecutive stable snapshots by startup tick", exact: true })).toBeVisible();
  await panel.locator("summary", { hasText: "Inspect 8 original sample rows" }).click();
  await expect(panel.locator("tbody tr")).toHaveCount(8);
  await expect(panel.locator("tbody tr").first()).toContainText("-0.900");
  const downloaded = page.waitForEvent("download");
  await panel.getByRole("link", { name: "Download verified startup JSON" }).click();
  const bytes = await readFile((await (await downloaded).path())!);
  expect(bytes).toEqual(item.bytes);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  expect(errors).toEqual([]);
});

test("failed authored startup retains partial/null samples as plot gaps and supports empty reports", async ({ page }) => {
  const failed = report("partial");
  failed.status = "failed"; failed.reason_code = "invalid_actor_sample"; failed.reason = "Authored invalid sample";
  failed.trace = failed.trace.slice(0, 3); failed.completed_ticks = 3; failed.stable_streak = 0;
  const actor = failed.trace[2].actors.ego;
  actor.invalid_fields = [{ field: "ego.velocity_mps.z", observed: "nan" }];
  actor.velocity_mps.z = null as unknown as number;
  delete (actor as { derived?: unknown }).derived;
  const empty = report("empty");
  empty.status = "failed"; empty.reason_code = "snapshot_error"; empty.reason = "Authored early RPC error";
  empty.trace = []; empty.completed_ticks = 0; empty.stable_streak = 0; empty.elapsed_simulated_seconds = 0;
  await install(page, [fixture("partial", failed), fixture("empty", empty)]);
  const panel = page.getByTestId("carla-startup-verified");
  await expect(panel).toContainText("Rest criteria not established");
  const vertical = panel.getByRole("img", { name: "Vertical velocity by startup tick", exact: true });
  await expect(vertical.locator('[data-series="Ego"]')).toHaveAttribute("data-valid-points", "2");
  await expect(vertical.locator('[data-series="Lead"]')).toHaveAttribute("data-valid-points", "3");
  await panel.locator("summary", { hasText: "Inspect 3 original sample rows" }).click();
  await expect(panel.locator("tbody tr").last()).toContainText("ego.velocity_mps.z: nan");
  await expect(panel.locator("tbody tr").last().locator("td").nth(2)).toHaveText("—");
  await expect(panel.locator("tbody tr").last().locator("td").nth(6)).toHaveText("—");
  await page.getByRole("combobox", { name: "Select execution", exact: true }).selectOption("run_startup_empty");
  await expect(panel).toContainText("No completed startup samples");
  await expect(panel.locator(".startup-chart svg")).toHaveCount(0);
});

test("SHA, context, schema, policy and oversized startup evidence fail closed", async ({ page }) => {
  const sha = fixture("sha"); sha.artifact.sha256 = "a".repeat(64);
  const contextDoc = report("context"); contextDoc.context.seed = 999;
  const schemaDoc = report("schema"); schemaDoc.schema_version = "2";
  const policyDoc = report("policy"); policyDoc.policy.version = "unknown/2";
  const large = fixture("large"); large.artifact.size_bytes = 3 * 1024 * 1024;
  const wrongRun = fixture("wrongrun"); wrongRun.artifact.run_id = "other_run";
  const wrongSize = fixture("size"); wrongSize.artifact.size_bytes += 1;
  const foreignUrl = fixture("foreign"); foreignUrl.artifact.download_url = "https://invalid.example/secret";
  const tooMany = report("toomany"); tooMany.trace = Array(101).fill(tooMany.trace[0]); tooMany.completed_ticks = 101;
  const cases = [sha, fixture("context", contextDoc), fixture("schema", schemaDoc), fixture("policy", policyDoc), large, wrongRun, wrongSize, foreignUrl, fixture("toomany", tooMany)];
  await install(page, cases);
  const selection = page.getByRole("combobox", { name: "Select execution", exact: true });
  for (const [index, item] of cases.entries()) {
    if (index) await selection.selectOption(item.run.id);
    await expect(page.getByTestId("carla-startup-inspector").getByRole("alert")).toContainText("Startup evidence rejected");
    await expect(page.getByTestId("carla-startup-verified")).not.toBeVisible();
  }
});

test("switching runs immediately hides old startup evidence and ignores a late or failed lookup", async ({ page }) => {
  const first = fixture("first"), slow = fixture("slow"), failed = fixture("failed"), absent = fixture("absent");
  absent.run.artifacts = [];
  await install(page, [first, slow, failed, absent]);
  const panel = page.getByTestId("carla-startup-verified"), selection = page.getByRole("combobox", { name: "Select execution", exact: true });
  await expect(panel).toBeVisible();
  let release: () => void = () => undefined;
  const blocked = new Promise<void>((resolve) => { release = resolve; });
  await page.route(`**${slow.artifact.download_url}`, async (route) => {
    await blocked;
    await route.fulfill({ contentType: "application/json", body: slow.bytes }).catch(() => undefined);
  });
  await selection.selectOption(slow.run.id);
  await expect(page.getByRole("status").filter({ hasText: "Verifying startup" })).toBeVisible();
  await expect(panel).not.toBeVisible();
  await page.route(`**${failed.artifact.download_url}`, (route) => route.fulfill({ status: 503, json: { detail: "Authored artifact failure" } }));
  await selection.selectOption(failed.run.id);
  await expect(page.getByTestId("carla-startup-inspector").getByRole("alert")).toContainText("503");
  release();
  await expect(panel).not.toBeVisible();
  await selection.selectOption(absent.run.id);
  await expect(page.getByTestId("carla-startup-inspector")).not.toBeVisible();
});
