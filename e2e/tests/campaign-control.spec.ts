import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import {
  campaignFixture,
  chooseControl,
  authoredCampaign,
} from "./campaign-control-fixture";

test.afterEach(async ({ page }) => {
  // Finish catalog reads started by a final tab transition before this test's
  // authored HTTP routes and browser context are torn down.
  await page.unrouteAll({ behavior: "wait" });
});

test("campaign pins configured revisions and distinct member fingerprints across a lost response", async ({
  page,
}) => {
  const input = await campaignFixture(page);
  const { dialog, suite, configuration, configBody } = input;
  await chooseControl(dialog, input);
  await expect(
    dialog.getByRole("button", { name: "Submit campaign", exact: true }),
  ).toBeEnabled();
  await expect(dialog.getByTestId("campaign-execution-review")).toContainText(
    "seed 41",
  );
  await expect(dialog.getByTestId("campaign-execution-review")).toContainText(
    "seed 42",
  );
  await expect(dialog.getByTestId("carla-pinned-stack")).toContainText(
    "Ground is unknown",
  );
  const revision = await page.request.post(
    `/api/v1/simulation-sensor-configurations/${configuration.id}/revisions`,
    {
      data: {
        ...configBody,
        expected_revision: 1,
        note: "Head changed while campaign remains pinned",
        parameters: { ...configBody.parameters, horizontal_fov: 160 },
      },
      headers: { "Idempotency-Key": randomUUID() },
    },
  );
  expect(revision.status()).toBe(201);
  const submissions: any[] = [];
  let report: any;
  await page.route("**/api/v1/campaigns?*", (route) =>
    route.fulfill({
      json: { items: report ? [report] : [], next_before_seq: null },
    }),
  );
  await page.route("**/api/v1/campaigns/campaign_authored_*", (route) =>
    route.fulfill({ json: report }),
  );
  await page.route("**/api/v1/campaigns", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    submissions.push({
      body: route.request().postDataJSON(),
      key: route.request().headers()["idempotency-key"],
    });
    report ??= authoredCampaign(input, submissions[0].body.control);
    if (submissions.length === 1) return route.abort();
    await route.fulfill({ status: 202, json: report });
  });
  await dialog
    .getByRole("button", { name: "Submit campaign", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toBeVisible();
  await dialog
    .getByRole("button", { name: "Retry campaign submission" })
    .click();
  await expect(dialog).toHaveCount(0);
  expect(submissions).toHaveLength(2);
  expect(submissions[0]).toEqual(submissions[1]);
  expect(submissions[0].body).toMatchObject({
    suite_id: suite.id,
    suite_revision: 1,
    adapter_id: "carla",
    control: {
      schema_version: "simulation-control-selection/2",
      sensor_configuration_id: configuration.id,
      sensor_configuration_revision: 1,
      stack_revision: 1,
      rig_revision: 1,
      lidar_sensor_key: "roof",
    },
  });
  expect(submissions[0].body.control).not.toHaveProperty("sensor_profile_id");
  const pinned = page.getByTestId("campaign-pinned-plan");
  await expect(pinned).toContainText("first case");
  await expect(pinned).toContainText(`${configuration.name} · r1`);
  await expect(pinned).toContainText("Saved noise seed 41");
  await expect(
    page.getByRole("region", { name: "Execution fingerprints for case-1" }),
  ).toContainText("fingerprints match");
  await expect(
    page.getByRole("region", { name: "Execution fingerprints for case-2" }),
  ).toContainText("missing or pending");
  await expect(page.getByTestId("campaign-case-report-case-2")).toContainText(
    "e".repeat(64),
  );
  await expect(page.getByTestId("campaign-detail")).toContainText(
    "1 / 2 checks not evaluated",
  );
  const contactCase = page.getByTestId("campaign-case-report-case-1");
  await expect(contactCase).toContainText("observed contacts");
  await expect(contactCase).toContainText("Observed contact count criterion");
  await expect(contactCase).toContainText(
    "does not prove collision-stream completeness or absence of collisions",
  );
  await expect(
    page.getByTestId("campaign-case-report-case-2"),
  ).not.toContainText("Observed contact count criterion");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    )
    .toBeTruthy();
});

test("campaign retains context1 selection and error-key retries; changing controller clears it and sends no control for default GT", async ({
  page,
}) => {
  const input = await campaignFixture(page);
  const { dialog } = input;
  const submissions: any[] = [];
  await page.route("**/api/v1/campaigns", (route) => {
    if (route.request().method() !== "POST") return route.continue();
    submissions.push({
      body: route.request().postDataJSON(),
      key: route.request().headers()["idempotency-key"],
    });
    return route.fulfill({
      status: 422,
      json: {
        detail:
          "Case case-2: authored remote preflight rejected; no jobs created",
      },
    });
  });
  await chooseControl(dialog, input, false);
  await dialog
    .getByRole("button", { name: "Submit campaign", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toContainText("Case case-2");
  await dialog
    .getByRole("button", { name: "Retry campaign submission" })
    .click();
  expect(submissions[0]).toEqual(submissions[1]);
  expect(submissions[0].body.control).toMatchObject({
    sensor_profile_id: "carla-lidar-ego/1",
    stack_revision: 1,
    rig_revision: 1,
  });
  expect(submissions[0].body.control).not.toHaveProperty("schema_version");
  await dialog
    .getByLabel("CARLA campaign controller mode")
    .selectOption("legacy");
  await dialog
    .getByRole("button", { name: "Submit campaign", exact: true })
    .click();
  expect(submissions[2].body).not.toHaveProperty("control");
  expect(submissions[2].key).not.toBe(submissions[0].key);
  await dialog
    .getByLabel("CARLA campaign controller mode")
    .selectOption("stack");
  await expect(
    dialog.getByLabel("Live CARLA stack", { exact: true }),
  ).toHaveValue("");
  await expect(
    dialog.getByRole("button", { name: "Submit campaign", exact: true }),
  ).toBeDisabled();
});

test("all 100 pinned campaign cases remain visible and an unsupported final case blocks submission", async ({
  page,
}) => {
  const input = await campaignFixture(page, 100, true);
  const { dialog } = input;
  let posts = 0;
  await page.route("**/api/v1/campaigns", (route) => {
    if (route.request().method() === "POST") posts++;
    return route.abort();
  });
  await chooseControl(dialog, input);
  await expect(dialog.locator('[data-testid^="campaign-input-"]')).toHaveCount(
    100,
  );
  await expect(dialog.getByTestId("campaign-execution-review")).toContainText(
    "100 / 100 pinned cases",
  );
  const last = dialog.getByTestId("campaign-input-case-100");
  await expect(last).toContainText("CARLA does not support cut_in");
  await expect(last).toContainText("requires a duration of 2–4 seconds");
  await expect(
    dialog.getByRole("button", { name: "Submit campaign", exact: true }),
  ).toBeDisabled();
  expect(posts).toBe(0);
});

test("exact configuration lookup failure hides old details and blocks a campaign despite available metadata", async ({
  page,
}) => {
  const input = await campaignFixture(page);
  const { dialog, configuration } = input;
  await chooseControl(dialog, input);
  await expect(
    dialog.getByRole("button", { name: "Submit campaign", exact: true }),
  ).toBeEnabled();
  await page.route(
    `**/api/v1/simulation-sensor-configurations/${configuration.id}/revisions`,
    async (route) => {
      const old = await (await route.fetch()).json();
      await route.fulfill({ json: [{ ...old[0], revision: 2 }, ...old] });
    },
  );
  await page.route(
    `**/api/v1/simulation-sensor-configurations/${configuration.id}?revision=2`,
    (route) =>
      route.fulfill({
        status: 503,
        json: { detail: "Authored exact revision unavailable" },
      }),
  );
  await dialog
    .getByLabel("Live CARLA sensor configuration", { exact: true })
    .selectOption("");
  await dialog
    .getByLabel("Live CARLA sensor configuration", { exact: true })
    .selectOption(configuration.id);
  await dialog
    .getByLabel("Live CARLA sensor configuration revision")
    .selectOption("2");
  await expect(dialog.getByTestId("carla-selected-configuration")).toHaveCount(
    0,
  );
  await expect(
    dialog.getByText("Authored exact revision unavailable", { exact: true }),
  ).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Submit campaign", exact: true }),
  ).toBeDisabled();
});

test("missing member preserves the submitted plan while a substituted nonmissing job identity is rejected", async ({
  page,
}) => {
  const input = await campaignFixture(page);
  await chooseControl(input.dialog, input, false);
  let report: any;
  await page.route("**/api/v1/campaigns?*", (route) =>
    route.fulfill({
      json: { items: report ? [report] : [], next_before_seq: null },
    }),
  );
  await page.route("**/api/v1/campaigns/campaign_authored_*", (route) =>
    route.fulfill({ json: report }),
  );
  await page.route("**/api/v1/campaigns", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    report = authoredCampaign(input, route.request().postDataJSON().control);
    report.cases[1].job_id = null;
    report.cases[1].job_status = "missing";
    report.counts.failed = 0;
    report.counts.missing = 1;
    await route.fulfill({ status: 202, json: report });
  });
  await input.dialog
    .getByRole("button", { name: "Submit campaign", exact: true })
    .click();
  await expect(input.dialog).toHaveCount(0);
  await expect(page.getByTestId("campaign-pinned-plan")).toBeVisible();
  const missing = page.getByTestId("campaign-case-report-case-2");
  await expect(missing).toContainText("The original member job is missing");
  await expect(missing).toContainText("job_authored_campaign_1");
  await expect(missing).toContainText("e".repeat(64));
  await expect(
    missing.getByRole("button", { name: "Open job for case-2" }),
  ).toHaveCount(0);
  await expect(page.getByTestId("campaign-detail")).toContainText("1 missing");
  // A different present job is not the known-missing exception.
  report.cases[1].job_id = "job_authored_wrong_identity";
  report.cases[1].job_status = "failed";
  await page.getByRole("tab", { name: "Pinned cases", exact: true }).click();
  await page.getByRole("tab", { name: "Campaigns", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "does not match its case identities",
  );
  await expect(page.getByTestId("campaign-pinned-plan")).toHaveCount(0);
});
