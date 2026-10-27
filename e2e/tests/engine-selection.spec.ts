import { expect, test, type Page } from "@playwright/test";
import {
  authoredContext,
  installLive,
  liveFixture,
} from "./carla-live-fixture";

test("adapter lookup recovery preserves the selected identity until an explicit engine choice", async ({
  page,
}) => {
  const input = await authoredContext(page.request);
  const adapters = await (await page.request.get("/api/v1/adapters")).json();
  const carla = {
    ...adapters.find((item: any) => item.id === "carla"),
    status: "available",
    version: "0.5.0",
    capabilities: ["lane_follow", "emergency_brake"],
  };
  let unavailable = true;
  await page.route("**/api/v1/adapters", (route) =>
    unavailable
      ? route.fulfill({
          status: 503,
          json: { detail: "Authored adapter catalog unavailable" },
        })
      : route.fulfill({ json: [carla] }),
  );
  const dispatches: string[] = [];
  await page.route("**/api/v1/jobs", (route) => {
    if (route.request().method() === "POST") {
      dispatches.push(route.request().postData() ?? "");
      return route.abort();
    }
    return route.continue();
  });
  await page.goto("/#runs");
  await expect(
    page.getByText("Adapter list unavailable", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Run simulation", exact: true })
    .first()
    .click();
  const dialog = page.getByRole("dialog", { name: "Launch a simulation" });
  await dialog
    .getByLabel("Scenario", { exact: true })
    .selectOption(input.scenario.id);
  const engine = dialog.getByLabel("Execution adapter", { exact: true });
  await expect(engine).toHaveValue("reference");
  await expect(dialog.getByRole("alert")).toContainText(
    "Authored adapter catalog unavailable",
  );
  await expect(
    dialog.getByRole("button", { name: "Run simulation", exact: true }),
  ).toBeDisabled();
  unavailable = false;
  await dialog.getByRole("button", { name: "Retry adapter list" }).click();
  await expect(engine.locator('option[value="carla"]')).toBeEnabled();
  await expect(engine).toHaveValue("reference");
  await expect(engine.locator('option[value="reference"]')).toHaveText(
    "reference · unavailable in current adapter list",
  );
  await expect(
    dialog.getByRole("button", { name: "Run simulation", exact: true }),
  ).toBeDisabled();
  await engine.selectOption("carla");
  await expect(dialog.getByTestId("launch-selection-summary")).toContainText(
    "Surrounding actor ground-truth controller",
  );
  await expect(
    dialog.getByRole("button", { name: "Run simulation", exact: true }),
  ).toBeEnabled();
  await expect(
    dialog.getByLabel("CARLA controller mode").locator('option[value="stack"]'),
  ).toHaveJSProperty("disabled", true);
  await page.keyboard.press("Escape");
  expect(dispatches).toEqual([]);
});

// Deliberately authored API/dispatch fixtures. This workflow never runs CARLA.
async function engineCatalog(page: Page) {
  await page.route("**/api/v1/adapters", async (route) => {
    const items = await (await route.fetch()).json();
    Object.assign(
      items.find((item: any) => item.id === "carla"),
      {
        status: "available",
        version: "0.5.0",
        capabilities: ["lane_follow", "emergency_brake", "live_lidar_stack"],
        description:
          "Authored browser engine-selection fixture; no native dispatch",
      },
    );
    await route.fulfill({ json: items });
  });
}

test("a CARLA-named scenario retains recorded adapter identity and requires explicit engine/controller selection", async ({
  page,
}) => {
  const input = await authoredContext(page.request);
  const renamed = await page.request.patch(
    `/api/v1/scenarios/${input.scenario.id}`,
    { data: { name: "CARLA wet-road pilot · authored selection fixture" } },
  );
  expect(renamed.ok()).toBeTruthy();
  input.scenario = await renamed.json();
  const cloned = await page.request.post(
    `/api/v1/scenarios/${input.scenario.id}/clone`,
  );
  expect(cloned.status()).toBe(201);
  const duplicateId = (await cloned.json()).id;
  const duplicateResponse = await page.request.patch(
    `/api/v1/scenarios/${duplicateId}`,
    { data: { name: input.scenario.name } },
  );
  expect(duplicateResponse.ok()).toBeTruthy();
  const duplicate = await duplicateResponse.json();
  expect(duplicate.revision).toBe(input.scenario.revision);
  const reference = liveFixture(input, "reference-label"),
    carla = liveFixture(input, "carla-label");
  for (const item of [reference, carla]) {
    item.run.scenario_name = input.scenario.name;
    delete item.run.execution_context;
    item.run.artifacts = [];
  }
  reference.run.adapter_id = "reference";
  reference.run.verdict = "passed";
  reference.run.provenance.adapter_version = "1.0.0";
  reference.run.provenance.model_fidelity =
    "Authored reference record; synthetic object model with no sensor or perception simulation";
  carla.run.provenance.adapter_version = "0.5.0";
  carla.run.provenance.model_fidelity =
    "Authored CARLA record; experimental ground-truth controller; no native execution in this test";
  await engineCatalog(page);
  const posts: { body: any; key: string | undefined }[] = [];
  const forbidden: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      /simulate|reconcile|preflight/.test(request.url())
    )
      forbidden.push(request.url());
  });
  await page.route("**/api/v1/jobs", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    posts.push({
      body: route.request().postDataJSON(),
      key: route.request().headers()["idempotency-key"],
    });
    await route.fulfill({
      status: 503,
      json: { detail: "Authored submission failure; no engine dispatched" },
    });
  });
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await installLive(page, [reference, carla]);
  await expect(
    page.getByText("Execution engines", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Reference engine", { exact: true })).toHaveCount(
    0,
  );
  const selector = page.getByLabel("Select execution", { exact: true });
  await expect(
    selector.locator(`option[value="${reference.run.id}"]`),
  ).toHaveText(/^Reference 1\.0\.0 · CARLA wet-road pilot/);
  await expect(
    selector.locator(`option[value="${reference.run.id}"]`),
  ).toContainText("passed");
  await expect(selector.locator(`option[value="${carla.run.id}"]`)).toHaveText(
    /^CARLA 0\.5\.0 · CARLA wet-road pilot/,
  );
  const summary = page.getByTestId("selected-run-engine");
  await expect(summary).toContainText(reference.run.provenance.model_fidelity);
  await selector.selectOption(carla.run.id);
  await expect(
    summary.getByTestId("recorded-engine-badge"),
  ).toHaveAccessibleName("Recorded adapter: CARLA, version 0.5.0");
  await expect(summary).toContainText(carla.run.provenance.model_fidelity);
  await page.getByRole("button", { name: "Browse 2 runs" }).click();
  const rows = page.locator(".run-browser tbody tr");
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0).getByTestId("recorded-engine-badge")).toContainText(
    "Reference",
  );
  await expect(rows.nth(1).getByTestId("recorded-engine-badge")).toContainText(
    "CARLA",
  );

  await page
    .getByRole("button", { name: "Run simulation", exact: true })
    .first()
    .click();
  const dialog = page.getByRole("dialog", { name: "Launch a simulation" });
  await dialog
    .getByLabel("Scenario", { exact: true })
    .selectOption(input.scenario.id);
  const engine = dialog.getByLabel("Execution adapter", { exact: true });
  await expect(engine).toHaveValue("reference");
  const selection = dialog.getByTestId("launch-selection-summary");
  const scenarios = dialog.getByLabel("Scenario", { exact: true });
  await expect(
    scenarios.locator(`option[value="${input.scenario.id}"]`),
  ).toHaveText(
    `${input.scenario.name} · r${input.scenario.revision} · ${input.scenario.id.slice(0, 12)}`,
  );
  await expect(scenarios.locator(`option[value="${duplicate.id}"]`)).toHaveText(
    `${duplicate.name} · r${duplicate.revision} · ${duplicate.id.slice(0, 12)}`,
  );
  await expect(selection).toContainText(input.scenario.id);
  await scenarios.selectOption(duplicate.id);
  await expect(selection).toContainText(duplicate.id);
  await expect(selection).not.toContainText(input.scenario.id);
  await expect(engine).toHaveValue("reference");
  await scenarios.selectOption(input.scenario.id);
  await expect(selection).toContainText(input.scenario.id);
  await expect(selection).toContainText("Reference object model");
  await expect(selection).toContainText("No camera, LiDAR or perception model");
  await dialog
    .getByRole("button", { name: "Run simulation", exact: true })
    .click();
  await expect(
    dialog.getByRole("button", { name: "Retry submission" }),
  ).toBeEnabled();
  expect(posts[0].body).toEqual({
    scenario_id: input.scenario.id,
    adapter_id: "reference",
  });
  await engine.selectOption("carla");
  await expect(dialog.getByLabel("CARLA controller mode")).toHaveValue(
    "legacy",
  );
  await expect(selection).toContainText(
    "Surrounding actor ground-truth controller",
  );
  await expect(selection).toContainText(
    "Captured RGB/LiDAR are evidence and do not drive this controller",
  );
  await expect(
    dialog.getByText("Authored submission failure; no engine dispatched", {
      exact: true,
    }),
  ).toHaveCount(0);
  await dialog
    .getByRole("button", { name: "Run simulation", exact: true })
    .click();
  await expect(
    dialog.getByRole("button", { name: "Retry submission" }),
  ).toBeEnabled();
  expect(posts[1].body).toEqual({
    scenario_id: input.scenario.id,
    adapter_id: "carla",
  });
  expect(posts[1].key).not.toBe(posts[0].key);
  await dialog.getByRole("button", { name: "Retry submission" }).click();
  await expect.poll(() => posts.length).toBe(3);
  expect(posts[2]).toEqual(posts[1]);

  await dialog.getByLabel("CARLA controller mode").selectOption("stack");
  await dialog
    .getByLabel("Live CARLA stack", { exact: true })
    .selectOption(input.context.stack_snapshot.id);
  await dialog
    .getByLabel("Live CARLA stack revision", { exact: true })
    .selectOption("1");
  await dialog
    .getByLabel("Live CARLA sensor rig", { exact: true })
    .selectOption(input.context.rig_snapshot.id);
  await dialog
    .getByLabel("Live CARLA rig revision", { exact: true })
    .selectOption("1");
  await dialog
    .getByLabel("Live CARLA LiDAR sensor", { exact: true })
    .selectOption("roof");
  await expect(
    dialog.getByRole("button", { name: "Run simulation", exact: true }),
  ).toBeEnabled();
  await expect(selection).toContainText(input.context.stack_snapshot.id);
  await expect(selection).toContainText("Exact revisions loaded");
  await engine.selectOption("reference");
  await engine.selectOption("carla");
  await expect(dialog.getByLabel("CARLA controller mode")).toHaveValue(
    "legacy",
  );
  await dialog.getByLabel("CARLA controller mode").selectOption("stack");
  await expect(
    dialog.getByLabel("Live CARLA stack", { exact: true }),
  ).toHaveValue("");
  await expect(
    dialog.getByLabel("Live CARLA sensor rig", { exact: true }),
  ).toHaveValue("");
  await expect(
    dialog.getByRole("button", { name: "Run simulation", exact: true }),
  ).toBeDisabled();
  await expect(selection).not.toContainText(input.context.stack_snapshot.id);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(selection).toBeVisible();
  expect(
    await dialog.evaluate(
      (element) => element.scrollWidth <= element.clientWidth + 1,
    ),
  ).toBeTruthy();
  expect(posts).toHaveLength(3);
  expect(forbidden).toEqual([]);
  expect(pageErrors).toEqual([]);
});

test("failed and late definition lookups cannot validate a different engine selection", async ({
  page,
}) => {
  const input = await authoredContext(page.request),
    stack = input.context.stack_snapshot;
  await engineCatalog(page);
  let release = () => {};
  const barrier = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(`**/api/v1/stacks/${stack.id}?revision=1`, async (route) => {
    await barrier;
    await route.fulfill({ json: stack }).catch(() => {});
  });
  const dispatches: string[] = [];
  await page.route("**/api/v1/jobs", (route) => {
    if (route.request().method() === "POST") {
      dispatches.push(route.request().postData() ?? "");
      return route.abort();
    }
    return route.continue();
  });
  await page.goto("/#runs");
  await page
    .getByRole("button", { name: "Run simulation", exact: true })
    .first()
    .click();
  const dialog = page.getByRole("dialog", { name: "Launch a simulation" });
  await dialog
    .getByLabel("Scenario", { exact: true })
    .selectOption(input.scenario.id);
  const engine = dialog.getByLabel("Execution adapter", { exact: true });
  await engine.selectOption("carla");
  await dialog.getByLabel("CARLA controller mode").selectOption("stack");
  await dialog
    .getByLabel("Live CARLA stack", { exact: true })
    .selectOption(stack.id);
  await dialog
    .getByLabel("Live CARLA stack revision", { exact: true })
    .selectOption("1");
  await engine.selectOption("reference");
  release();
  await expect(dialog.getByTestId("launch-selection-summary")).toContainText(
    "Reference object model",
  );
  await expect(
    dialog.getByTestId("launch-selection-summary"),
  ).not.toContainText(stack.id);
  await page.route(`**/api/v1/stacks/${stack.id}?revision=1`, (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Authored exact revision lookup unavailable" },
    }),
  );
  await engine.selectOption("carla");
  await dialog.getByLabel("CARLA controller mode").selectOption("stack");
  await dialog
    .getByLabel("Live CARLA stack", { exact: true })
    .selectOption(stack.id);
  await dialog
    .getByLabel("Live CARLA stack revision", { exact: true })
    .selectOption("1");
  await dialog
    .getByLabel("Live CARLA sensor rig", { exact: true })
    .selectOption(input.context.rig_snapshot.id);
  await dialog
    .getByLabel("Live CARLA rig revision", { exact: true })
    .selectOption("1");
  await dialog
    .getByLabel("Live CARLA LiDAR sensor", { exact: true })
    .selectOption("roof");
  await expect(dialog.getByRole("alert")).toContainText(
    "Authored exact revision lookup unavailable",
  );
  await expect(
    dialog.getByRole("button", { name: "Run simulation", exact: true }),
  ).toBeDisabled();
  await expect(dialog.getByTestId("launch-selection-summary")).toContainText(
    "could not be verified",
  );
  expect(dispatches).toEqual([]);
});
