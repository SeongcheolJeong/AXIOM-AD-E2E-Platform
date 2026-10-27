import { test, expect, type Page, type Locator } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const hash = (bytes: Buffer) =>
  createHash("sha256").update(bytes).digest("hex");
async function completed(page: Page, id: string) {
  await expect
    .poll(
      async () =>
        (await (await page.request.get(`/api/v1/stack-replays/${id}`)).json())
          .status,
      { timeout: 45000 },
    )
    .toBe("completed");
  return (await page.request.get(`/api/v1/stack-replays/${id}`)).json();
}
async function fillSource(
  form: Locator,
  recordingId: string,
  fixture: {
    channels: { lidar: number; ego: number };
    ego_ordinals: number[];
    ego_log_time_offset_ns: string;
  },
) {
  await form
    .getByLabel("Stack replay recording", { exact: true })
    .selectOption(recordingId);
  await form
    .getByLabel("Stack replay binding revision", { exact: true })
    .selectOption("1");
  await form
    .getByLabel("Stack replay LiDAR channel", { exact: true })
    .selectOption(String(fixture.channels.lidar));
  await form
    .getByLabel("Stack replay ego channel", { exact: true })
    .selectOption(String(fixture.channels.ego));
  await form
    .getByLabel("Stack replay ego offset", { exact: true })
    .fill(fixture.ego_log_time_offset_ns);
  await form
    .getByLabel("Stack replay ego ordinals", { exact: true })
    .fill(fixture.ego_ordinals.join(", "));
}
// Original MCAP bytes, shared worker and stack kernel. Responses are never fabricated;
// injected failures below exercise identity-preserving reads and idempotent submit only.
test("immutable modular stack → exact MCAP input decisions → headway revision → same-input control change without world feedback", async ({
  page,
}, testInfo) => {
  test.setTimeout(150000);
  page.setDefaultTimeout(15000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const fixturePath = testInfo.outputPath("authored-stack-input.mcap");
  const fixture = JSON.parse(
    execFileSync(
      process.env.AXIOM_TEST_PYTHON ?? path.join(root, ".venv/bin/python"),
      [path.join(root, "examples/generate_stack_recording.py"), fixturePath],
      { encoding: "utf8" },
    ),
  );
  const name = `Stack browser ${Date.now()}`;
  const imported = await page.request.post(
    `/api/v1/recordings?${new URLSearchParams({ name, source_kind: "synthetic", source: "Original authored sampled surfaces and independent ego telemetry; no sensor or dynamics validation" })}`,
    {
      headers: { "Content-Type": "application/octet-stream" },
      data: readFileSync(fixturePath),
    },
  );
  expect(imported.status()).toBe(201);
  const recording = await imported.json();
  expect(recording.sha256).toBe(fixture.sha256);
  const rigResponse = await page.request.post("/api/v1/sensor-rigs", {
    data: {
      name: name + " rig",
      reference_frame: "ego",
      coordinate_convention: "right_handed_z_up",
      source_kind: "synthetic",
      source: "Original fixture construction",
      note: "Nonzero roof mount declared by generator; not measured calibration.",
      sensors: [
        {
          key: "roof",
          name: "Roof LiDAR",
          kind: "lidar",
          frame_id: "lidar_roof",
          transform: {
            translation_m: [0, 0, 2.2],
            rotation_xyzw: [0, 0, 0, 1],
          },
        },
      ],
    },
  });
  expect(rigResponse.status()).toBe(201);
  const rig = await rigResponse.json();
  const bindingResponse = await page.request.post(
    `/api/v1/recordings/${recording.id}/sensor-bindings`,
    {
      data: {
        expected_revision: 0,
        rig_id: rig.id,
        rig_revision: 1,
        note: "Clock offsets from source construction, not measured synchronization.",
        channels: [
          {
            channel_id: fixture.channels.lidar,
            sensor_key: "roof",
            decoder_id: "foxglove-json",
            decoder_version: "1",
            max_age_ns: "100000000",
            log_time_offset_ns: fixture.lidar_log_time_offset_ns,
          },
        ],
      },
    },
  );
  expect(bindingResponse.status()).toBe(201);
  await page.goto("/#stacks");
  await page.getByRole("button", { name: "New stack", exact: true }).click();
  const editor = page.getByRole("dialog", {
    name: "Create component stack",
    exact: true,
  });
  await editor
    .getByLabel("Stack name", { exact: true })
    .fill(name + " unknown ground");
  await expect(
    editor.getByLabel("Stack perception ground_reference_z_m", { exact: true }),
  ).toHaveValue("");
  await editor
    .getByLabel("Stack revision note", { exact: true })
    .fill(
      "No ground geometry has been declared; preserve unknown rather than inferring zero.",
    );
  const unknownPublished = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/stacks",
  );
  await editor
    .getByRole("button", { name: "Publish stack", exact: true })
    .click();
  const unknownResponse = await unknownPublished;
  expect(unknownResponse.status()).toBe(201);
  const unknownStack = await unknownResponse.json();
  expect(
    unknownStack.definition.perception.parameters.ground_reference_z_m,
  ).toBeNull();
  expect(
    unknownStack.definition.perception.parameters.ground_reference_source,
  ).toBe("");
  await expect(editor).not.toBeVisible();
  await page.getByRole("button", { name: "New stack", exact: true }).click();
  await editor.getByLabel("Stack name", { exact: true }).fill(name);
  await editor
    .getByLabel("Stack description", { exact: true })
    .pressSequentially(
      "Original point geometry, modular decisions, no command feedback.",
      { delay: 3 },
    );
  await expect(
    editor.getByLabel("Stack description", { exact: true }),
  ).toBeFocused();
  await expect(
    editor.getByLabel("Stack perception ground_reference_z_m", { exact: true }),
  ).toHaveValue("");
  await editor
    .getByLabel("Stack perception ground_reference_z_m", { exact: true })
    .fill("0");
  await editor
    .getByLabel("Stack perception ground_reference_source", { exact: true })
    .fill(
      "Original fixture construction declares flat sampled ground at ego z=0; not measured road geometry.",
    );
  await expect(
    editor.getByLabel("Stack policy allow_no_obstacle_observed_motion", {
      exact: true,
    }),
  ).toHaveValue("false");
  await editor
    .getByLabel("Stack policy allow_no_obstacle_observed_motion", {
      exact: true,
    })
    .selectOption("true");
  await editor
    .getByLabel("Stack policy time_headway_s", { exact: true })
    .fill("1.5");
  await editor
    .getByLabel("Stack revision note", { exact: true })
    .fill(
      "Explicit pilot motion assumption over authored ground coverage. Not free-space or road safety evidence.",
    );
  const published = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/stacks",
  );
  await editor
    .getByRole("button", { name: "Publish stack", exact: true })
    .click();
  const stackResponse = await published;
  expect(stackResponse.status()).toBe(201);
  const stack = await stackResponse.json();
  await expect(editor).not.toBeVisible();
  await expect(page.getByTestId("stack-definition-detail")).toBeVisible();
  expect(stack.definition.policy.parameters.time_headway_s).toBe(1.5);
  expect(
    stack.definition.policy.parameters.allow_no_obstacle_observed_motion,
  ).toBe(true);
  const definitionBytes = await (
    await page.request.get(stack.download_url)
  ).body();
  expect(hash(definitionBytes)).toBe(stack.manifest_hash);
  await page
    .getByRole("button", { name: "Replay recording", exact: true })
    .click();
  const launch = page.getByRole("dialog", {
    name: "Replay stack on recorded inputs",
    exact: true,
  });
  await launch
    .getByLabel("Stack replay label", { exact: true })
    .fill(name + " r1");
  await fillSource(launch, recording.id, fixture);
  await launch
    .getByRole("button", { name: "Browse ego messages", exact: true })
    .click();
  await expect(
    launch.getByLabel(`Select ego ordinal ${fixture.ego_ordinals[0]}`, {
      exact: true,
    }),
  ).toBeChecked();
  await launch
    .getByRole("button", { name: "Hide ego message browser", exact: true })
    .click();
  let dropped = false;
  let lostId = "";
  await page.route("**/api/v1/stack-replays", async (route) => {
    if (route.request().method() === "POST" && !dropped) {
      dropped = true;
      const response = await route.fetch();
      expect(response.status()).toBe(202);
      lostId = (await response.json()).id;
      await route.abort("failed");
    } else await route.continue();
  });
  await launch
    .getByRole("button", { name: "Submit stack replay", exact: true })
    .click();
  await launch
    .getByRole("button", { name: "Retry stack replay submission", exact: true })
    .click();
  await expect(launch).not.toBeVisible();
  await page.unroute("**/api/v1/stack-replays");
  const first = await completed(page, lostId);
  expect(first.counts).toEqual({
    total: 8,
    running: 0,
    completed: 8,
    failed: 0,
    not_attempted: 0,
  });
  const list = await (
    await page.request.get("/api/v1/stack-replays?limit=100")
  ).json();
  expect(
    list.items.filter((item: { label: string }) => item.label === name + " r1"),
  ).toHaveLength(1);
  await expect(page.getByTestId("stack-replay-id")).toHaveText(first.id);
  await expect(page.getByTestId("stack-tick-time")).toHaveText(
    `${fixture.ticks[0].at_ns} ns`,
  );
  await expect(page.getByTestId("stack-observation-status")).toHaveText(
    "unknown",
  );
  await expect(page.getByTestId("stack-policy-mode")).toHaveText(
    "unknown stop",
  );
  await expect(page.getByTestId("stack-control-brake")).toHaveText("1");
  await expect(page.getByTestId("stack-consumed-points")).toHaveCount(0);
  expect(
    first.ticks.every(
      (tick: { control_application: string }) =>
        tick.control_application === "not_applied_open_loop",
    ),
  ).toBe(true);
  expect(
    first.ticks.every(
      (tick: { packet: { ego: { speed_mps: number } } }) =>
        tick.packet.ego.speed_mps === 4,
    ),
  ).toBe(true);
  await page.getByLabel("Select stack tick", { exact: true }).selectOption("1");
  await expect(page.getByTestId("stack-policy-mode")).toHaveText("approach");
  await expect(page.getByTestId("stack-observed-distance")).toHaveText(
    "19.5 m",
  );
  const consumed = page.getByTestId("stack-consumed-points");
  await expect(consumed).toHaveAttribute(
    "data-points-sha256",
    first.ticks[1].output.input_points_sha256,
  );
  await expect(consumed.getByTestId("sensor-point-coordinate")).toHaveText(
    "3, -6, 0",
  );
  await expect(
    consumed.getByTestId("sensor-point-coordinate-label"),
  ).toHaveText("Point 0 · consumed ego frame XYZ");
  const sourceFrameResponse = await page.request.get(
    `/api/v1/recordings/${recording.id}/sensor-frame?${new URLSearchParams({ channel_id: String(fixture.channels.lidar), ordinal: String(first.ticks[1].input.lidar.ordinal) })}`,
  );
  expect(sourceFrameResponse.status()).toBe(200);
  const sourceFrame = await sourceFrameResponse.json();
  const nativePoints = Buffer.from(
    sourceFrame.decoded.point_cloud.data_base64,
    "base64",
  );
  expect(nativePoints.readFloatLE(8)).toBeCloseTo(-2.2, 5);
  expect(
    first.binding_snapshot.rig_snapshot.sensors[0].transform.translation_m,
  ).toEqual([0, 0, 2.2]);
  const pointsDownload = page.waitForEvent("download");
  await page
    .getByRole("link", { name: "Download consumed points", exact: true })
    .click();
  const pointsBytes = readFileSync((await (await pointsDownload).path())!);
  expect(hash(pointsBytes)).toBe(first.ticks[1].points.sha256);
  expect(pointsBytes.length).toBe(first.ticks[1].points.point_count * 12);
  expect(pointsBytes.readFloatLE(8)).toBeCloseTo(
    nativePoints.readFloatLE(8) + 2.2,
    5,
  ); // The label identifies already transformed consumed coordinates; never applies the mount twice.
  await page.getByLabel("Select stack tick", { exact: true }).selectOption("2");
  await expect(page.getByTestId("stack-policy-mode")).toHaveText("approach");
  expect(first.ticks[2].input.lidar.ordinal).toBe(
    fixture.ticks[2].new_lidar_ordinal,
  ); // Last duplicate timestamp wins.
  await expect(page.getByTestId("stack-nearest-point")).toHaveText(
    "12, -0.8, 0.25 m",
  );
  for (const index of [3, 5]) {
    await page
      .getByLabel("Select stack tick", { exact: true })
      .selectOption(String(index));
    await expect(page.getByTestId("stack-policy-mode")).toHaveText(
      "unknown stop",
    );
    await expect(page.getByTestId("stack-control-brake")).toHaveText("1");
  }
  await expect(page.getByTestId("stack-input-unavailable")).toBeVisible();
  await expect(page.getByTestId("stack-consumed-points")).toHaveCount(0);
  await expect(page.getByTestId("stack-lidar-age")).toHaveText(
    "LiDAR age: 250000000 ns",
  );
  await page.getByLabel("Select stack tick", { exact: true }).selectOption("6");
  await expect(page.getByTestId("stack-policy-mode")).toHaveText(
    "obstacle stop",
  );
  await page.getByLabel("Select stack tick", { exact: true }).selectOption("7");
  await expect(page.getByTestId("stack-observation-status")).toHaveText(
    "no obstacle observed",
  );
  await expect(page.getByTestId("stack-policy-mode")).toHaveText(
    "pilot cruise",
  );
  await expect(page.getByTestId("stack-control-application")).toContainText(
    "NOT APPLIED",
  );
  const originalReplayBytes = await (
    await page.request.get(first.download_url)
  ).body();
  expect(hash(originalReplayBytes)).toBe(first.manifest_hash);

  await page.getByRole("tab", { name: "Definitions", exact: true }).click();
  await page
    .getByRole("button", { name: "Create stack revision", exact: true })
    .click();
  const revision = page.getByRole("dialog", {
    name: "Create stack revision",
    exact: true,
  });
  await revision
    .getByLabel("Stack policy time_headway_s", { exact: true })
    .fill("3");
  await revision
    .getByLabel("Stack revision note", { exact: true })
    .fill(
      "Increase authored time headway only; preserve source observations and all other modules.",
    );
  const revised = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname ===
        `/api/v1/stacks/${stack.id}/revisions`,
  );
  await revision
    .getByRole("button", { name: "Publish stack revision", exact: true })
    .click();
  expect((await revised).status()).toBe(201);
  await expect(revision).not.toBeVisible();
  await expect(
    page.getByLabel("Component stack revision", { exact: true }),
  ).toHaveValue("2");
  expect(await (await page.request.get(stack.download_url)).body()).toEqual(
    definitionBytes,
  );
  await page
    .getByRole("button", { name: "Replay recording", exact: true })
    .click();
  await launch
    .getByLabel("Stack replay label", { exact: true })
    .fill(name + " r2");
  await expect(
    launch.getByLabel("Stack replay revision", { exact: true }),
  ).toHaveValue("2");
  await fillSource(launch, recording.id, fixture);
  const submitted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/stack-replays",
  );
  await launch
    .getByRole("button", { name: "Submit stack replay", exact: true })
    .click();
  const second = await completed(page, (await (await submitted).json()).id);
  await expect(launch).not.toBeVisible();
  await expect(page.getByTestId("stack-replay-id")).toHaveText(second.id);
  expect(second.ticks[2].input).toEqual(first.ticks[2].input);
  expect(second.ticks[2].points.sha256).toBe(first.ticks[2].points.sha256);
  expect(second.ticks[2].output.decision.mode).toBe("obstacle_stop");
  expect(second.ticks[2].output.control.brake).toBe(1);
  expect(first.ticks[2].output.control.throttle).toBeGreaterThan(0);
  await page.getByLabel("Select stack tick", { exact: true }).selectOption("2");
  await expect(page.getByTestId("stack-policy-mode")).toHaveText(
    "obstacle stop",
  );
  await expect(page.getByTestId("stack-control-brake")).toHaveText("1");
  expect(await (await page.request.get(first.download_url)).body()).toEqual(
    originalReplayBytes,
  );
  const replayDownload = page.waitForEvent("download");
  await page
    .getByRole("link", { name: "Download stack replay manifest", exact: true })
    .click();
  expect(hash(readFileSync((await (await replayDownload).path())!))).toBe(
    second.manifest_hash,
  );

  // A selected point-file failure hides old geometry; the exact saved bytes can be retried.
  const targetPoints = second.ticks[1].points;
  await page.route(`**${targetPoints.download_url}`, (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Selected consumed XYZ read failure" },
    }),
  );
  await page.getByLabel("Select stack tick", { exact: true }).selectOption("1");
  await expect(
    page.getByRole("button", { name: "Retry consumed points", exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("sensor-point-view")).toHaveCount(0);
  await page.unroute(`**${targetPoints.download_url}`);
  await page
    .getByRole("button", { name: "Retry consumed points", exact: true })
    .click();
  await expect(page.getByTestId("stack-consumed-points")).toHaveAttribute(
    "data-points-sha256",
    targetPoints.sha256,
  );
  await page.route(`**/api/v1/stack-replays/${second.id}`, (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Same replay refresh failure" },
    }),
  );
  await page
    .getByRole("button", { name: "Refresh stacks workspace", exact: true })
    .click();
  await expect(page.getByText(/Replay refresh failed/)).toBeVisible();
  await expect(page.getByTestId("stack-replay-id")).toHaveText(second.id);
  await page.unroute(`**/api/v1/stack-replays/${second.id}`);
  await page.route(`**/api/v1/stack-replays/${first.id}`, (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Other replay read failure" },
    }),
  );
  await page
    .getByRole("button", { name: `Open stack replay ${name} r1`, exact: true })
    .click();
  await expect(
    page.getByText(/The selected stack replay could not be loaded/),
  ).toBeVisible();
  await expect(page.getByTestId("stack-trace-explorer")).toHaveCount(0);
  await page.unroute(`**/api/v1/stack-replays/${first.id}`);
  await page
    .getByRole("button", { name: `Open stack replay ${name} r2`, exact: true })
    .click();
  await expect(page.getByTestId("stack-replay-id")).toHaveText(second.id);
  await page.getByLabel("Select stack tick", { exact: true }).selectOption("2");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    )
    .toBeTruthy();
  await page.getByRole("tab", { name: "Decision trace", exact: true }).click();
  await expect(page.getByTestId("stack-policy-mode")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("stack-trace-mobile.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("tab", { name: "Definitions", exact: true }).click();
  await page
    .getByRole("button", { name: "Create stack revision", exact: true })
    .click();
  await expect(revision).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    )
    .toBeTruthy();
  await page.keyboard.press("Escape");
  await expect(revision).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "Create stack revision", exact: true }),
  ).toBeFocused();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page
    .getByRole("tab", { name: "Recorded replays", exact: true })
    .click();
  await page.getByTestId("stack-trace-explorer").scrollIntoViewIfNeeded();
  await page.getByLabel("Select stack tick", { exact: true }).selectOption("2");
  await expect(page.getByTestId("sensor-point-coordinate")).toBeVisible();
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await page.screenshot({
    path: testInfo.outputPath("stack-trace-desktop.png"),
    fullPage: true,
    animations: "disabled",
  });
  expect(errors).toEqual([]);
});
