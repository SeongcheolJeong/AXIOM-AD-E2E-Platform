import { test, expect, type Page, type Locator } from "@playwright/test";
import {
  authoredContext,
  installLive,
  liveFixture,
} from "./carla-live-fixture";
import { v2Fixture } from "./stack-v2-fixture";

// Authored browser/API contracts only. No simulation is dispatched by these tests.
async function launch(page: Page, version: string, kernel2 = false) {
  const fixture = kernel2
    ? await v2Fixture(page.request, "plan", "v2_launch")
    : null;
  const input = fixture
    ? {
        context: fixture.run.execution_context,
        scenario: fixture.run.scenario_snapshot,
      }
    : await authoredContext(page.request);
  await page.route("**/api/v1/adapters", async (route) => {
    const adapters = await (await route.fetch()).json();
    Object.assign(
      adapters.find((item: any) => item.id === "carla"),
      {
        version,
        status: "available",
        capabilities: ["lane_follow", "emergency_brake", "live_lidar_stack"],
        description: "Authored profile-selection contract; no CARLA dispatch",
      },
    );
    await route.fulfill({ json: adapters });
  });
  const submissions: { body: any; key: string | undefined }[] = [];
  await page.route("**/api/v1/jobs", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    submissions.push({
      body: route.request().postDataJSON(),
      key: route.request().headers()["idempotency-key"],
    });
    await route.abort();
  });
  await page.goto("/#runs");
  await page
    .getByRole("button", { name: "Run simulation", exact: true })
    .first()
    .click();
  const dialog = page.getByRole("dialog", { name: "Launch a simulation" });
  await dialog
    .getByLabel("Execution adapter", { exact: true })
    .selectOption("carla");
  await dialog
    .getByLabel("Scenario", { exact: true })
    .selectOption(input.scenario.id);
  await dialog.getByLabel("CARLA controller mode").selectOption("stack");
  await pinRevisions(dialog, input);
  return { input, dialog, submissions };
}
async function pinRevisions(
  dialog: Locator,
  input: Awaited<ReturnType<typeof authoredContext>>,
) {
  await dialog
    .getByLabel("Live CARLA stack", { exact: true })
    .selectOption(input.context.stack_snapshot.id);
  await dialog.getByLabel("Live CARLA stack revision").selectOption("1");
  await dialog
    .getByLabel("Live CARLA sensor rig")
    .selectOption(input.context.rig_snapshot.id);
  await dialog.getByLabel("Live CARLA rig revision").selectOption("1");
  await dialog.getByLabel("Live CARLA LiDAR sensor").selectOption("roof");
}

test("profile catalog keeps baseline default and gates dense by the selected engine version", async ({
  page,
}) => {
  const { dialog, submissions } = await launch(page, "0.5.0");
  const profile = dialog.getByLabel("Live CARLA sensor profile");
  await expect(profile).toHaveValue("carla-lidar-ego/1");
  await expect(
    profile.locator('option[value="carla-lidar-ego-dense/1"]'),
  ).toHaveJSProperty("disabled", true);
  await expect(
    profile.locator('option[value="carla-lidar-ego-dense/1"]'),
  ).toContainText("requires 0.6.0");
  await expect(dialog.getByTestId("carla-selected-profile")).toContainText(
    "1,600 nominal rays per frame",
  );
  await expect(dialog.getByTestId("carla-selected-profile")).toContainText(
    "Actual returned and consumed point counts",
  );
  await expect(
    dialog.getByRole("button", { name: "Run simulation", exact: true }),
  ).toBeEnabled();
  expect(submissions).toEqual([]);
});

test("0.6 dense is explicit, carries effective seed, and whole-profile retries keep exact request keys", async ({
  page,
}) => {
  const { dialog, submissions } = await launch(page, "0.6.0");
  const profile = dialog.getByLabel("Live CARLA sensor profile");
  await expect(profile).toHaveValue("carla-lidar-ego/1");
  await expect(
    profile.locator('option[value="carla-lidar-ego-dense/1"]'),
  ).toHaveJSProperty("disabled", false);
  await profile.selectOption("carla-lidar-ego-dense/1");
  await expect(dialog.getByTestId("carla-selected-profile")).toContainText(
    "6,400 nominal rays per frame",
  );
  await expect(dialog.getByTestId("carla-selected-profile")).toContainText(
    "Noise seed 41",
  );
  await expect(dialog.getByTestId("carla-selected-profile")).toContainText(
    "The seed records requested configuration. Sensor RNG application is unverified; changing the seed may produce identical points.",
  );
  await dialog.getByLabel(/Seed override/).fill("123");
  await expect(dialog.getByTestId("carla-selected-profile")).toContainText(
    "Noise seed 123",
  );
  await expect(dialog.getByTestId("launch-selection-summary")).toContainText(
    "carla-lidar-ego-dense/1",
  );
  const submit = dialog.getByRole("button", {
    name: "Run simulation",
    exact: true,
  });
  await expect(submit).toBeEnabled();
  await submit.click();
  const retry = dialog.getByRole("button", {
    name: "Retry submission",
    exact: true,
  });
  await expect(retry).toBeEnabled();
  await retry.click();
  await expect(retry).toBeEnabled();
  expect(submissions).toHaveLength(2);
  expect(submissions[0]).toEqual(submissions[1]);
  expect(submissions[0].key).toBeTruthy();
  expect(submissions[0].body.control.sensor_profile_id).toBe(
    "carla-lidar-ego-dense/1",
  );
  expect(submissions[0].body.seed).toBe(123);
  expect(submissions[0].body.control.stack_revision).toBe(1);
  expect(submissions[0].body.control.rig_revision).toBe(1);
  await profile.selectOption("carla-lidar-ego/1");
  await expect(dialog.getByTestId("launch-selection-summary")).toContainText(
    "carla-lidar-ego/1",
  );
  await dialog
    .getByRole("button", { name: "Run simulation", exact: true })
    .click();
  await expect(retry).toBeEnabled();
  expect(submissions).toHaveLength(3);
  expect(submissions[2].key).not.toBe(submissions[0].key);
  expect(submissions[2].body.control.sensor_profile_id).toBe(
    "carla-lidar-ego/1",
  );
});

test("catalog failure or malformed metadata blocks all live submission and retry preserves exact revisions", async ({
  page,
}) => {
  let mode = "failure";
  await page.route("**/api/v1/simulation-sensor-profiles", async (route) => {
    if (mode === "failure")
      return route.fulfill({
        status: 503,
        json: { detail: "Authored catalog outage" },
      });
    const profiles = await (await route.fetch()).json();
    if (mode === "malformed") profiles[0].nominal_points_per_frame = 6400;
    return route.fulfill({ json: profiles });
  });
  const { dialog, input, submissions } = await launch(page, "0.6.0");
  const submit = dialog.getByRole("button", {
    name: "Run simulation",
    exact: true,
  });
  await expect(dialog.getByRole("alert")).toContainText(
    "Sensor profile catalog unavailable",
  );
  await expect(submit).toBeDisabled();
  mode = "malformed";
  await dialog
    .getByRole("button", { name: "Retry live input definitions" })
    .click();
  await expect(dialog.getByRole("alert")).toContainText(
    "invalid or unsupported recipe",
  );
  await expect(submit).toBeDisabled();
  mode = "ready";
  await dialog
    .getByRole("button", { name: "Retry live input definitions" })
    .click();
  await expect(submit).toBeEnabled();
  await expect(dialog.getByLabel("Live CARLA sensor profile")).toHaveValue(
    "carla-lidar-ego/1",
  );
  await expect(
    dialog.getByLabel("Live CARLA stack", { exact: true }),
  ).toHaveValue(input.context.stack_snapshot.id);
  await expect(dialog.getByLabel("Live CARLA stack revision")).toHaveValue("1");
  await expect(dialog.getByLabel("Live CARLA rig revision")).toHaveValue("1");
  expect(submissions).toEqual([]);
});

test("an unlisted engine version cannot submit even the baseline profile", async ({
  page,
}) => {
  const { dialog, submissions } = await launch(page, "0.9.0");
  await expect(dialog.getByRole("alert")).toContainText(
    "unavailable for CARLA 0.9.0",
  );
  await expect(
    dialog.getByRole("button", { name: "Run simulation", exact: true }),
  ).toBeDisabled();
  await expect(dialog.getByLabel("Live CARLA sensor profile")).toHaveValue(
    "carla-lidar-ego/1",
  );
  expect(submissions).toEqual([]);
});

test("kernel 2 live requires adapter 0.7 and near coverage remains an explicit profile choice", async ({
  page,
}) => {
  const { dialog, submissions } = await launch(page, "0.6.0", true);
  await expect(dialog.getByTestId("carla-pinned-stack")).toContainText(
    "stack-kernel/2",
  );
  await expect(dialog.getByRole("alert")).toContainText("0.7.0");
  await expect(dialog.getByLabel("Live CARLA sensor profile")).toHaveValue(
    "carla-lidar-ego/1",
  );
  await expect(
    dialog
      .getByLabel("Live CARLA sensor profile")
      .locator('option[value="carla-lidar-ego-near/1"]'),
  ).toHaveJSProperty("disabled", true);
  await expect(
    dialog.getByRole("button", { name: "Run simulation", exact: true }),
  ).toBeDisabled();
  expect(submissions).toEqual([]);
});

test("kernel 2 and near recipe submit exact pinned revisions and block a missing component version", async ({
  page,
}) => {
  let removeExactVersion = false;
  await page.route("**/api/v1/stack-components", async (route) => {
    const catalog = await (await route.fetch()).json();
    await route.fulfill({
      json: removeExactVersion
        ? catalog.filter(
            (item: any) =>
              !(item.role === "controller" && item.version === "2"),
          )
        : catalog,
    });
  });
  const { dialog, input, submissions } = await launch(page, "0.7.0", true);
  const profile = dialog.getByLabel("Live CARLA sensor profile");
  await expect(profile).toHaveValue("carla-lidar-ego/1");
  await expect(
    profile.locator('option[value="carla-lidar-ego-near/1"]'),
  ).toHaveJSProperty("disabled", false);
  await profile.selectOption("carla-lidar-ego-near/1");
  await expect(dialog.getByTestId("carla-selected-profile")).toContainText(
    "12,800 nominal rays per frame",
  );
  const submit = dialog.getByRole("button", {
    name: "Run simulation",
    exact: true,
  });
  await expect(submit).toBeEnabled();
  await submit.click();
  await expect(
    dialog.getByRole("button", { name: "Retry submission", exact: true }),
  ).toBeEnabled();
  expect(submissions).toHaveLength(1);
  expect(submissions[0].body.control).toMatchObject({
    stack_id: input.context.stack_snapshot.id,
    stack_revision: 1,
    rig_id: input.context.rig_snapshot.id,
    rig_revision: 1,
    lidar_sensor_key: "roof",
    sensor_profile_id: "carla-lidar-ego-near/1",
  });
  removeExactVersion = true;
  await page.reload();
  await page
    .getByRole("button", { name: "Run simulation", exact: true })
    .first()
    .click();
  await dialog
    .getByLabel("Execution adapter", { exact: true })
    .selectOption("carla");
  await dialog
    .getByLabel("Scenario", { exact: true })
    .selectOption(input.scenario.id);
  await dialog.getByLabel("CARLA controller mode").selectOption("stack");
  await pinRevisions(dialog, input);
  await profile.selectOption("carla-lidar-ego-near/1");
  await expect(dialog.getByRole("alert")).toContainText(
    "exact saved kernel and component versions",
  );
  await expect(submit).toBeDisabled();
  await expect(profile).toHaveValue("carla-lidar-ego-near/1");
  await expect(
    dialog.getByLabel("Live CARLA stack", { exact: true }),
  ).toHaveValue(input.context.stack_snapshot.id);
  await expect(dialog.getByLabel("Live CARLA stack revision")).toHaveValue("1");
  expect(submissions).toHaveLength(1);
});

test("saved dense context is independent of current catalog and distinguishes nominal rays from retained points", async ({
  page,
}) => {
  const input = await authoredContext(page.request);
  const dense = liveFixture(input, "dense-profile");
  dense.run.provenance.adapter_version = "0.6.0";
  dense.manifest.worker_adapter_version = "0.6.0";
  Object.assign(dense.run.execution_context.sensor_profile, {
    id: "carla-lidar-ego-dense/1",
    attributes: {
      ...dense.run.execution_context.sensor_profile.attributes,
      points_per_second: "128000",
    },
  });
  dense.refresh();
  const oldVersion = liveFixture(input, "incompatible-dense");
  oldVersion.run.execution_context.sensor_profile.id =
    "carla-lidar-ego-dense/1";
  oldVersion.run.execution_context.sensor_profile.attributes.points_per_second =
    "128000";
  oldVersion.refresh();
  let catalogReads = 0;
  await page.route("**/api/v1/simulation-sensor-profiles", (route) => {
    catalogReads++;
    return route.fulfill({
      status: 503,
      json: { detail: "Current catalog is not historical evidence" },
    });
  });
  await installLive(page, [dense, oldVersion]);
  const saved = page.getByTestId("carla-saved-context");
  await expect(saved).toContainText("carla-lidar-ego-dense/1");
  await expect(saved).toContainText("6,400 nominal rays per frame");
  await expect(saved).toContainText("Saved noise seed 41");
  await expect(saved).toContainText(
    "The seed records requested configuration. Sensor RNG application is unverified; changing the seed may produce identical points.",
  );
  await expect(page.getByTestId("carla-live-points")).toContainText(
    "3 full points",
  );
  expect(catalogReads).toBe(0);
  await page
    .getByLabel("Select execution", { exact: true })
    .selectOption(oldVersion.run.id);
  await expect(
    page.getByTestId("carla-live-inspector").getByRole("alert"),
  ).toContainText("requires CARLA adapter 0.6.0");
  await expect(page.getByTestId("carla-live-verified")).not.toBeVisible();
});
