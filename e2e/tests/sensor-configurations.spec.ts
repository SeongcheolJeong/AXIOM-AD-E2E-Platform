import {
  test,
  expect,
  type Page,
  type APIRequestContext,
} from "@playwright/test";
import { randomUUID } from "node:crypto";
import { authoredContext, installLive } from "./carla-live-fixture";

// Real local registry/API authoring; all CARLA execution/readback responses below
// are authored browser contracts. No simulator execution is dispatched.
async function createConfiguration(request: APIRequestContext) {
  const capability = (
    await (
      await request.get(
        "/api/v1/simulation-sensor-capabilities?adapter_id=carla",
      )
    ).json()
  )[0];
  const body = {
    name: `Authored configuration ${randomUUID()}`,
    description: "Browser protocol fixture, not a qualified physical placement",
    note: "Authored test",
    adapter_id: "carla",
    capability_id: capability.id,
    capability_version: capability.version,
    parameters: {
      ...capability.default_parameters,
      horizontal_fov: 175,
      points_per_second: 64000,
    },
  };
  const response = await request.post(
    "/api/v1/simulation-sensor-configurations",
    { data: body, headers: { "Idempotency-Key": randomUUID() } },
  );
  expect(response.status(), await response.text()).toBe(201);
  return { configuration: await response.json(), capability, body };
}
async function selectLibrary(page: Page, id: string, revision: string) {
  await page
    .getByLabel("Saved sensor configuration", { exact: true })
    .selectOption(id);
  await page
    .getByLabel("Sensor configuration revision", { exact: true })
    .selectOption(revision);
}

test("configuration authoring retries lost responses, preserves revisions, exports verified bytes and reuses the rig editor", async ({
  page,
}) => {
  await page.goto("/#simulation-sensors");
  const attempts: any[] = [];
  let lose = true;
  await page.route(
    "**/api/v1/simulation-sensor-configurations",
    async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      attempts.push({
        body: route.request().postDataJSON(),
        key: route.request().headers()["idempotency-key"],
      });
      const response = await route.fetch();
      if (lose) {
        lose = false;
        return route.abort();
      }
      await route.fulfill({ response });
    },
  );
  await page
    .getByRole("button", { name: "New sensor configuration", exact: true })
    .click();
  const modal = page.getByRole("dialog", {
    name: "Create sensor configuration",
    exact: true,
  });
  const name = `Browser nonpreset ${randomUUID()}`;
  await modal.getByLabel("Configuration name", { exact: true }).fill(name);
  await modal
    .getByLabel("Horizontal field of view (°)", { exact: true })
    .fill("175");
  await modal.getByLabel("Rays per second", { exact: true }).fill("64000");
  await modal
    .getByLabel("Revision note", { exact: true })
    .fill("Authored nonpreset recipe; no simulation dispatched");
  await modal
    .getByRole("button", { name: "Save sensor configuration", exact: true })
    .click();
  await expect(modal.getByRole("alert")).toContainText("Uncertain responses");
  await modal
    .getByRole("button", { name: "Save sensor configuration", exact: true })
    .click();
  await expect(modal).toHaveCount(0);
  expect(attempts).toHaveLength(2);
  expect(attempts[0]).toEqual(attempts[1]);
  const detail = page.getByTestId("sensor-configuration-detail");
  await expect(detail).toContainText(`${name} · fixed r1`);
  const id = await page
    .getByLabel("Saved sensor configuration", { exact: true })
    .inputValue();
  const revisionAttempts: any[] = [];
  let loseRevision = true;
  await page.route(
    `**/api/v1/simulation-sensor-configurations/${id}/revisions`,
    async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      revisionAttempts.push({
        body: route.request().postDataJSON(),
        key: route.request().headers()["idempotency-key"],
      });
      const response = await route.fetch();
      if (loseRevision) {
        loseRevision = false;
        return route.abort();
      }
      await route.fulfill({ response });
    },
  );
  await page
    .getByRole("button", { name: "Create configuration revision", exact: true })
    .click();
  const revisionModal = page.getByRole("dialog", {
    name: "Create sensor configuration revision",
    exact: true,
  });
  await revisionModal
    .getByLabel("Horizontal field of view (°)", { exact: true })
    .fill("170");
  await revisionModal
    .getByLabel("Revision note", { exact: true })
    .fill("Second authored recipe");
  await revisionModal
    .getByRole("button", { name: "Save configuration revision", exact: true })
    .click();
  await expect(revisionModal.getByRole("alert")).toContainText(
    "Uncertain responses",
  );
  await revisionModal
    .getByRole("button", { name: "Save configuration revision", exact: true })
    .click();
  expect(revisionAttempts).toHaveLength(2);
  expect(revisionAttempts[0]).toEqual(revisionAttempts[1]);
  await expect(detail).toContainText("fixed r2");
  await selectLibrary(page, id, "1");
  await expect(detail).toContainText('"horizontal_fov": 175');
  await page.getByRole("button", { name: "Refresh sensor library" }).click();
  await expect(
    page.getByLabel("Sensor configuration revision", { exact: true }),
  ).toHaveValue("1");
  const download = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Download verified configuration" })
    .click();
  expect((await download).suggestedFilename()).toBe(`${id}-r1.json`);
  await page
    .getByRole("button", { name: "Create configuration revision", exact: true })
    .click();
  await revisionModal
    .getByLabel("Revision note", { exact: true })
    .fill("Conflicting edit of archived r1");
  await revisionModal
    .getByRole("button", { name: "Save configuration revision", exact: true })
    .click();
  await expect(revisionModal.getByRole("alert")).toContainText(/revision/i);
  await expect(
    revisionModal.getByLabel("Horizontal field of view (°)", { exact: true }),
  ).toHaveValue("175");
  await revisionModal
    .getByRole("button", { name: "Cancel", exact: true })
    .click();

  await page
    .getByRole("button", { name: "New sensor rig", exact: true })
    .click();
  const rig = page.getByRole("dialog", {
    name: "Create sensor rig",
    exact: true,
  });
  await rig
    .getByLabel("Rig name", { exact: true })
    .fill(`Authored front rig ${randomUUID()}`);
  await rig.getByLabel("Reference frame", { exact: true }).fill("ego");
  await rig
    .getByRole("combobox", { name: "Coordinate convention", exact: true })
    .selectOption("right_handed_z_up");
  await rig
    .getByRole("combobox", { name: "Rig source kind", exact: true })
    .selectOption("synthetic");
  await rig
    .getByLabel("Rig source", { exact: true })
    .fill("Authored browser geometry; no physical qualification");
  await rig
    .getByLabel("Rig note", { exact: true })
    .fill("Explicit nonroof placement");
  await rig.getByLabel("Sensor key", { exact: true }).fill("front");
  await rig.getByLabel("Sensor name", { exact: true }).fill("Front LiDAR");
  await rig
    .getByRole("combobox", { name: "Sensor kind", exact: true })
    .selectOption("lidar");
  await rig.getByLabel("Sensor frame ID", { exact: true }).fill("lidar_front");
  await rig.getByLabel("Declare static transform", { exact: true }).check();
  await rig.getByLabel("Translation X", { exact: true }).fill("2.3");
  await rig.getByLabel("Translation Z", { exact: true }).fill("1.6");
  await rig
    .getByRole("button", { name: "Save sensor rig", exact: true })
    .click();
  await expect(page.getByTestId("sensor-library-rig-detail")).toContainText(
    "lidar_front",
  );
});

async function launcher(page: Page, version = "0.8.0") {
  const input = await authoredContext(page.request);
  const saved = await createConfiguration(page.request);
  const body = { ...input.context.rig_snapshot };
  delete (body as any).id;
  for (const k of [
    "schema_version",
    "revision",
    "created_at",
    "definition_hash",
    "calibration_status",
    "manifest_hash",
    "manifest_size_bytes",
    "download_url",
  ])
    delete (body as any)[k];
  body.name = "Authored nonroof launcher rig";
  body.sensors = [
    {
      key: "front",
      name: "Front",
      kind: "lidar",
      frame_id: "lidar_front",
      transform: { translation_m: [2.3, 0, 1.6], rotation_xyzw: [0, 0, 0, 1] },
      intrinsics: null,
    },
  ];
  const r = await page.request.post("/api/v1/sensor-rigs", { data: body });
  expect(r.status()).toBe(201);
  const rig = await r.json();
  await page.route("**/api/v1/adapters", async (route) => {
    const data = await (await route.fetch()).json();
    Object.assign(
      data.find((a: any) => a.id === "carla"),
      {
        version,
        status: "available",
        capabilities: ["lane_follow", "emergency_brake", "live_lidar_stack"],
      },
    );
    await route.fulfill({ json: data });
  });
  const submissions: any[] = [];
  await page.route("**/api/v1/jobs", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    submissions.push({
      body: route.request().postDataJSON(),
      key: route.request().headers()["idempotency-key"],
    });
    await route.abort();
  });
  await page.goto("/#runs");
  await page.reload();
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
  await expect(dialog.getByLabel("Live CARLA sensor setup")).toHaveValue(
    "profile",
  );
  await dialog
    .getByLabel("Live CARLA sensor setup")
    .selectOption("configuration");
  await dialog
    .getByLabel("Live CARLA stack", { exact: true })
    .selectOption(input.context.stack_snapshot.id);
  await dialog.getByLabel("Live CARLA stack revision").selectOption("1");
  await dialog.getByLabel("Live CARLA sensor rig").selectOption(rig.id);
  await dialog.getByLabel("Live CARLA rig revision").selectOption("1");
  await dialog.getByLabel("Live CARLA LiDAR sensor").selectOption("front");
  await dialog
    .getByLabel("Live CARLA sensor configuration", { exact: true })
    .selectOption(saved.configuration.id);
  await dialog
    .getByLabel("Live CARLA sensor configuration revision")
    .selectOption("1");
  return { dialog, input, ...saved, rig, submissions };
}
test("configured launcher pins exact revisions and body across uncertain submission; unsupported engine is blocked", async ({
  page,
}) => {
  const { dialog, configuration, body, submissions } = await launcher(page);
  await expect(
    dialog.getByTestId("carla-selected-configuration"),
  ).toContainText("lidar_front");
  const r = await page.request.post(
    `/api/v1/simulation-sensor-configurations/${configuration.id}/revisions`,
    {
      data: {
        ...body,
        expected_revision: 1,
        note: "New head while original is selected",
        parameters: { ...body.parameters, horizontal_fov: 160 },
      },
      headers: { "Idempotency-Key": randomUUID() },
    },
  );
  expect(r.status()).toBe(201);
  const submit = dialog.getByRole("button", {
    name: "Run simulation",
    exact: true,
  });
  await expect(submit).toBeEnabled();
  await submit.click();
  await dialog
    .getByRole("button", { name: "Retry submission", exact: true })
    .click();
  expect(submissions).toHaveLength(2);
  expect(submissions[0]).toEqual(submissions[1]);
  expect(submissions[0].body.control).toMatchObject({
    schema_version: "simulation-control-selection/2",
    sensor_configuration_id: configuration.id,
    sensor_configuration_revision: 1,
    rig_revision: 1,
    lidar_sensor_key: "front",
  });
  expect(submissions[0].body.control).not.toHaveProperty("sensor_profile_id");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.unroute("**/api/v1/adapters");
  const legacy = await launcher(page, "0.7.0");
  await expect(
    legacy.dialog.getByRole("button", { name: "Run simulation", exact: true }),
  ).toBeDisabled();
  await expect(legacy.dialog.getByRole("alert")).toContainText(
    "unavailable for CARLA 0.7.0",
  );
  expect(legacy.submissions).toEqual([]);
});

test("failed configuration or capability fetch hides previous selection and blocks submission", async ({
  page,
}) => {
  const { dialog, configuration } = await launcher(page);
  await expect(
    dialog.getByRole("button", { name: "Run simulation", exact: true }),
  ).toBeEnabled();
  await page.route(
    `**/api/v1/simulation-sensor-configurations/${configuration.id}?revision=2`,
    (route) =>
      route.fulfill({
        status: 409,
        json: { detail: "Authored failed exact revision read" },
      }),
  );
  await page.route(
    `**/api/v1/simulation-sensor-configurations/${configuration.id}/revisions`,
    async (route) => {
      const old = await (await route.fetch()).json();
      await route.fulfill({ json: [{ ...old[0], revision: 2 }, ...old] });
    },
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
    dialog.getByRole("button", { name: "Run simulation", exact: true }),
  ).toBeDisabled();
  await expect(
    dialog
      .getByRole("alert")
      .filter({ hasText: "Authored failed exact revision read" }),
  ).toBeVisible();
});

import { configuredSensorFixture } from "./configurable-sensor-fixture";
test("authored mounted evidence displays exact physical readbacks and hides old evidence on failed run selection", async ({
  page,
}) => {
  test.setTimeout(90000);
  const complete = configuredSensorFixture(),
    partial = configuredSensorFixture("partial");
  await installLive(page, [complete, partial]);
  await page
    .getByLabel("Select execution", { exact: true })
    .selectOption(complete.run.id);
  const inspector = page.getByRole("region", {
    name: "Mounted sensor configuration evidence",
  });
  await expect(
    inspector.getByRole("heading", {
      name: "Mounted sensor configuration evidence",
    }),
  ).toHaveCount(1);
  await expect(inspector).toContainText("81 / 81 measurement poses retained");
  await expect(inspector.getByTestId("sensor-mount-readback")).toContainText(
    "Declared pose matched",
  );
  await expect(inspector).toContainText("Matched typed request");
  await expect(page.getByTestId("carla-saved-context")).toContainText(
    "lidar_front_oblique",
  );
  await inspector.getByLabel("Sensor mount observation").selectOption("80");
  await expect(inspector.getByTestId("sensor-mount-readback")).toContainText(
    "Measurement pose: frame",
  );
  await expect(
    page.getByRole("alert").filter({ hasText: "evidence rejected" }),
  ).toHaveCount(0);
  const artifact = partial.run.artifacts.find(
    (a: any) => a.name === "carla-sensor-configuration.json",
  );
  await page.route(`**${artifact.download_url}`, (route) =>
    route.fulfill({ status: 503, body: "Authored unavailable readback" }),
  );
  await page
    .getByLabel("Select execution", { exact: true })
    .selectOption(partial.run.id);
  await expect(inspector.getByTestId("sensor-mount-readback")).toHaveCount(0);
  await expect(inspector.getByRole("alert")).toContainText("503");
  await page.unroute(`**${artifact.download_url}`);
  await page.route(`**${artifact.download_url}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: partial.files.get(artifact.name)!,
    }),
  );
  await inspector
    .getByRole("button", { name: "Retry sensor readback" })
    .click();
  await expect(inspector).toContainText("Mismatch retained");
  await expect(
    inspector.getByTestId("sensor-mount-not-observed"),
  ).toBeVisible();
  await expect(inspector).toContainText("0 / 81 measurement poses retained");
});

test("mounted readback rejects digest failures and coherent-looking parent claims before visualizing poses", async ({
  page,
}) => {
  const bad = configuredSensorFixture("forged-parent");
  const value = JSON.parse(
    bad.files.get("carla-sensor-configuration.json")!.toString(),
  );
  value.observations[0].parent_actor_id += 100;
  bad.files.set(
    "carla-sensor-configuration.json",
    Buffer.from(JSON.stringify(value)),
  );
  bad.refresh();
  await installLive(page, [bad]);
  const inspector = page.getByRole("region", {
    name: "Mounted sensor configuration evidence",
  });
  await expect(inspector.getByRole("alert")).toContainText(
    "contradicts the original frame/parent/matrix",
  );
  await expect(inspector.getByTestId("sensor-mount-readback")).toHaveCount(0);
  const artifact = bad.run.artifacts.find(
    (a: any) => a.name === "carla-sensor-configuration.json",
  );
  await page.route(`**${artifact.download_url}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: Buffer.alloc(artifact.size_bytes, 32),
    }),
  );
  await inspector
    .getByRole("button", { name: "Retry sensor readback" })
    .click();
  await expect(inspector.getByRole("alert")).toContainText("checksum mismatch");
});

test("sensor capability discovery failure blocks configurable execution without substituting the roof recipe", async ({
  page,
}) => {
  const { dialog, submissions } = await launcher(page);
  await expect(
    dialog.getByRole("button", { name: "Run simulation", exact: true }),
  ).toBeEnabled();
  await page.route(
    "**/api/v1/simulation-sensor-capabilities?adapter_id=carla",
    (route) =>
      route.fulfill({
        status: 503,
        json: { detail: "Authored capability catalog outage" },
      }),
  );
  await dialog.getByLabel("Live CARLA sensor setup").selectOption("profile");
  await dialog
    .getByLabel("Live CARLA sensor setup")
    .selectOption("configuration");
  await expect(
    dialog
      .getByRole("alert")
      .filter({ hasText: "Authored capability catalog outage" }),
  ).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Run simulation", exact: true }),
  ).toBeDisabled();
  await expect(dialog.getByLabel("Live CARLA sensor setup")).toHaveValue(
    "configuration",
  );
  expect(submissions).toEqual([]);
});
