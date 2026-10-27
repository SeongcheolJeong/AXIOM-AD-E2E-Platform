import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { v2Fixture } from "./stack-v2-fixture";
import { digest } from "./carla-live-fixture";
const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);

// Actual local import/binding/API/worker/kernel replay over original authored MCAP.
// The source has independent ego samples; requests never change recorded motion.
test("kernel 2 recorded replay retains immutable source hashes, state sequence and unknown geometry", async ({
  page,
}, testInfo) => {
  test.setTimeout(90000);
  const filename = testInfo.outputPath("kernel2-authored-input.mcap");
  const source = JSON.parse(
    execFileSync(
      process.env.AXIOM_TEST_PYTHON ?? path.join(root, ".venv/bin/python"),
      [path.join(root, "examples/generate_stack_recording.py"), filename],
      { encoding: "utf8" },
    ),
  );
  const label = `Kernel 2 recorded browser ${Date.now()}`;
  const imported = await page.request.post(
    `/api/v1/recordings?${new URLSearchParams({
      name: label,
      source_kind: "synthetic",
      source: "Original authored MCAP samples; no engine or vehicle physics",
    })}`,
    {
      headers: { "Content-Type": "application/octet-stream" },
      data: readFileSync(filename),
    },
  );
  expect(imported.status()).toBe(201);
  const recording = await imported.json();
  expect(recording.sha256).toBe(source.sha256);
  const fixture = await v2Fixture(page.request, "gap", "v2_recorded_stack");
  const { stack_snapshot: stack, rig_snapshot: rig } =
    fixture.run.execution_context;
  const bindingResponse = await page.request.post(
    `/api/v1/recordings/${recording.id}/sensor-bindings`,
    {
      data: {
        expected_revision: 0,
        rig_id: rig.id,
        rig_revision: 1,
        note: "Declared source construction, not measured calibration or synchronization",
        channels: [
          {
            channel_id: source.channels.lidar,
            sensor_key: "roof",
            decoder_id: "foxglove-json",
            decoder_version: "1",
            max_age_ns: "100000000",
            log_time_offset_ns: source.lidar_log_time_offset_ns,
          },
        ],
      },
    },
  );
  expect(bindingResponse.status()).toBe(201);
  await page.goto("/#stacks");
  await page
    .getByLabel("Select component stack", { exact: true })
    .selectOption(stack.id);
  await page
    .getByRole("button", { name: "Replay recording", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Replay stack on recorded inputs",
    exact: true,
  });
  await expect(
    dialog.getByLabel("Stack replay definition", { exact: true }),
  ).toHaveValue(stack.id);
  await expect(
    dialog
      .getByLabel("Stack replay revision", { exact: true })
      .locator("option:checked"),
  ).toContainText("stack-kernel/2");
  await dialog.getByLabel("Stack replay label", { exact: true }).fill(label);
  await dialog
    .getByLabel("Stack replay recording", { exact: true })
    .selectOption(recording.id);
  await dialog
    .getByLabel("Stack replay binding revision", { exact: true })
    .selectOption("1");
  await dialog
    .getByLabel("Stack replay LiDAR channel", { exact: true })
    .selectOption(String(source.channels.lidar));
  await dialog
    .getByLabel("Stack replay ego channel", { exact: true })
    .selectOption(String(source.channels.ego));
  await dialog
    .getByLabel("Stack replay ego offset", { exact: true })
    .fill(source.ego_log_time_offset_ns);
  await dialog
    .getByLabel("Stack replay ego ordinals", { exact: true })
    .fill(source.ego_ordinals.join(", "));
  const submitted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/stack-replays",
  );
  await dialog
    .getByRole("button", { name: "Submit stack replay", exact: true })
    .click();
  const response = await submitted;
  expect(response.status()).toBe(202);
  expect(response.request().postDataJSON()).toMatchObject({
    stack_id: stack.id,
    stack_revision: 1,
    recording_id: recording.id,
    binding_revision: 1,
    ego_ordinals: source.ego_ordinals,
  });
  const queued = await response.json();
  await expect
    .poll(
      async () =>
        (
          await (
            await page.request.get(`/api/v1/stack-replays/${queued.id}`)
          ).json()
        ).status,
      { timeout: 45000 },
    )
    .toBe("completed");
  const detail = await (
    await page.request.get(`/api/v1/stack-replays/${queued.id}`)
  ).json();
  expect(detail.counts).toEqual({
    total: 8,
    running: 0,
    completed: 8,
    failed: 0,
    not_attempted: 0,
  });
  expect(detail.stack_snapshot.kernel_version).toBe("stack-kernel/2");
  expect(detail.stack_snapshot.manifest_hash).toBe(stack.manifest_hash);
  expect(detail.recording_snapshot.sha256).toBe(source.sha256);
  for (const [index, tick] of detail.ticks.entries()) {
    expect(tick.output.kernel_version).toBe("stack-kernel/2");
    expect(tick.control_application).toBe("not_applied_open_loop");
    if (index > 0)
      expect(tick.output.state_before).toEqual(
        detail.ticks[index - 1].output.state_after,
      );
  }
  const originalManifest = await (
    await page.request.get(detail.download_url)
  ).body();
  expect(digest(originalManifest)).toBe(detail.manifest_hash);
  await expect(page.getByTestId("stack-replay-id")).toHaveText(detail.id);
  await expect(page.getByTestId("stack-v2-stages")).toBeVisible();
  await expect(page.getByTestId("stack-control-application")).toContainText(
    "NOT APPLIED · OPEN-LOOP",
  );
  await expect(page.getByTestId("stack-v2-qualified-prefix")).toHaveText(
    "Unavailable",
  );
  await page
    .getByRole("button", {
      name: "Inspect plan state at sequence 1",
      exact: true,
    })
    .click();
  await expect(page.getByTestId("stack-plan-state")).toContainText(
    "unknown stop → unknown stop",
  );
  await expect(page.getByTestId("stack-observation-status")).toHaveText(
    "sampled · valid input",
  );
  await expect(page.getByTestId("stack-coverage-grid")).toContainText(
    "39 / 39 cells qualified",
  );
  await expect(page.getByTestId("stack-v2-qualified-prefix")).toHaveText(
    "12.5 m",
  );
  // A changed installed catalog may block a new replay, but cannot reinterpret
  // this immutable trace. Keep the original replay manifest exactly intact.
  await page.route("**/api/v1/stack-components", async (route) => {
    const catalog = await (await route.fetch()).json();
    await route.fulfill({
      json: catalog.filter(
        (item: any) => !(item.role === "controller" && item.version === "2"),
      ),
    });
  });
  await page.getByRole("button", { name: "Refresh stacks workspace" }).click();
  await page.getByRole("tab", { name: "Definitions", exact: true }).click();
  await page
    .getByLabel("Select component stack", { exact: true })
    .selectOption(stack.id);
  await page
    .getByRole("button", { name: "Replay recording", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toContainText(
    "exact saved kernel and component versions are unavailable",
  );
  await expect(
    dialog.getByRole("button", { name: "Submit stack replay", exact: true }),
  ).toBeDisabled();
  expect(await (await page.request.get(detail.download_url)).body()).toEqual(
    originalManifest,
  );
});
