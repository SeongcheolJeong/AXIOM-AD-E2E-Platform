import { test, expect, type Page, type Locator } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const hash = (bytes: Buffer) =>
  createHash("sha256").update(bytes).digest("hex");
const modelLabel = "red region (authored pixel rule)";

async function completed(page: Page, route: string, id: string) {
  await expect
    .poll(
      async () =>
        (await (await page.request.get(`${route}/${id}`)).json()).status,
      { timeout: 45_000 },
    )
    .toBe("completed");
  return (await page.request.get(`${route}/${id}`)).json();
}
async function coordinates(form: Locator, box: number[]) {
  for (const [index, label] of [
    "X minimum",
    "Y minimum",
    "X maximum",
    "Y maximum",
  ].entries()) {
    await form.getByLabel(label, { exact: true }).fill(String(box[index]));
  }
  await form
    .getByRole("button", { name: "Apply box coordinates", exact: true })
    .click();
  await expect(form.getByTestId("annotation-coordinates")).toHaveText(
    `Saved xyxy: ${box.join(", ")} px`,
  );
}
async function review(form: Locator, note: string) {
  await form
    .getByLabel("Image annotation source", { exact: true })
    .selectOption("synthetic");
  await form
    .getByLabel("Image reviewer", { exact: true })
    .fill("Original fixture author declaration");
  await form.getByLabel("Image review note", { exact: true }).fill(note);
  await form.getByLabel("Mark image reviewed", { exact: true }).check();
}
async function capture(
  page: Page,
  form: Locator,
  recordingId: string,
  ordinal: number,
) {
  await form.getByRole("button", { name: "Add image", exact: true }).click();
  await form
    .getByLabel("Dataset image recording", { exact: true })
    .selectOption(recordingId);
  await form
    .getByLabel("Dataset image channel", { exact: true })
    .selectOption("1");
  await form
    .getByLabel("Dataset image ordinal", { exact: true })
    .fill(String(ordinal));
  const saved = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/dataset-images",
  );
  await form
    .getByRole("button", { name: "Capture dataset image", exact: true })
    .click();
  const response = await saved;
  expect([200, 201]).toContain(response.status());
  const image = await response.json();
  await expect(form.getByTestId("annotation-canvas")).toHaveAttribute(
    "data-image-sha256",
    image.image.sha256,
  );
  await form
    .getByLabel("Image dataset split", { exact: true })
    .selectOption("test");
  return image;
}
async function launchEvaluation(page: Page, name: string, jobLabel: string) {
  await page
    .getByRole("button", { name: "Evaluate detections", exact: true })
    .click();
  const launch = page.getByRole("dialog", {
    name: "Evaluate recorded detections",
    exact: true,
  });
  await launch.getByLabel("Evaluation name", { exact: true }).fill(name);
  await launch
    .getByLabel(`Use inference job ${jobLabel}`, { exact: true })
    .check();
  const mapping = launch.getByLabel(`Map model class ${modelLabel}`, {
    exact: true,
  });
  await expect(mapping).toHaveValue(""); // Matching label names are not silently mapped.
  await mapping.selectOption({ label: "region" });
  await launch
    .getByLabel("Evaluation note", { exact: true })
    .fill(
      "Original authored pixel geometry and actual ONNX predictions; arithmetic exercise, not road accuracy.",
    );
  return launch;
}

// Ground truth is authored from generate_perception_fixture.py:image_bytes before
// any inference job exists. No prediction or evaluator response is fabricated.
test("original-image annotation → draft denominator → reviewed negative and crowd → actual COCO diagnostics and immutable evidence", async ({
  page,
}, testInfo) => {
  test.setTimeout(180_000);
  page.setDefaultTimeout(15_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const fixture = testInfo.outputPath("dataset-fixture");
  execFileSync(
    process.env.AXIOM_TEST_PYTHON ?? path.join(root, ".venv/bin/python"),
    [path.join(root, "examples/generate_perception_fixture.py"), fixture],
  );
  const name = `Dataset labels ${Date.now()}`;
  const evaluators = await (
    await page.request.get("/api/v1/detection-evaluators")
  ).json();
  expect(
    evaluators.find((item: { id: string }) => item.id === "coco-bbox")?.status,
  ).toBe("available");
  const imported = await page.request.post(
    `/api/v1/recordings?${new URLSearchParams({ name, source_kind: "synthetic", source: "Original authored RGB fixture; no sensor validation" })}`,
    {
      headers: { "Content-Type": "application/octet-stream" },
      data: readFileSync(path.join(fixture, "images.mcap")),
    },
  );
  expect(imported.status()).toBe(201);
  const recording = await imported.json();
  const support = await (
    await page.request.get(`/api/v1/recordings/${recording.id}/sensor-channels`)
  ).json();
  const decoder = support.channels.find(
    (item: { channel_id: number }) => item.channel_id === 1,
  ).decoders[0];

  await page.goto("/#datasets");
  await page.getByRole("button", { name: "New dataset", exact: true }).click();
  const form = page.getByRole("dialog", {
    name: "Create detection dataset",
    exact: true,
  });
  await form.getByLabel("Dataset name", { exact: true }).fill(name);
  await form.getByLabel("Dataset class labels", { exact: true }).fill("region");
  await form
    .getByLabel("Dataset revision note", { exact: true })
    .pressSequentially(
      "Two declared regions; third image deliberately unreviewed.",
      { delay: 2 },
    );
  await expect(
    form.getByLabel("Dataset revision note", { exact: true }),
  ).toBeFocused();
  await form
    .getByLabel("Dataset source", { exact: true })
    .fill("examples/generate_perception_fixture.py:image_bytes");
  await form.getByLabel("Dataset source revision", { exact: true }).fill("1");
  const image0 = await capture(page, form, recording.id, 0);
  expect(image0.input.log_time_ns).toBe("1700000010003000123");
  // Pointer-to-image transform stays in original pixel coordinates, despite CSS fitting.
  await form
    .getByRole("button", { name: "Draw annotation box", exact: true })
    .click();
  const canvas = form.getByTestId("annotation-canvas");
  await canvas.scrollIntoViewIfNeeded();
  const points = await canvas.evaluate((node) => {
    const matrix = (node as unknown as SVGSVGElement).getScreenCTM()!;
    return [
      [24, 48],
      [97, 120],
    ].map(([x, y]) => {
      const point = new DOMPoint(x, y).matrixTransform(matrix);
      return { x: point.x, y: point.y };
    });
  });
  await page.mouse.move(points[0].x, points[0].y);
  await page.mouse.down();
  await page.mouse.move(points[1].x, points[1].y, { steps: 8 });
  await page.mouse.up();
  await expect(form.getByTestId("annotation-box")).toHaveCount(1);
  expect(
    Number(await form.getByTestId("annotation-box").getAttribute("x")),
  ).toBeCloseTo(24, 1);
  await coordinates(form, [24, 48, 97, 120]);
  await review(
    form,
    "Original rectangle pixels x24…96 inclusive, y48…119 inclusive; continuous box [24,48,97,120].",
  );
  // Box changes invalidate review; split-only changes preserve the declaration.
  await coordinates(form, [24, 48, 96, 120]);
  await expect(
    form.getByLabel("Mark image reviewed", { exact: true }),
  ).not.toBeChecked();
  await expect(form.getByLabel("Image reviewer", { exact: true })).toHaveValue(
    "",
  );
  await coordinates(form, [24, 48, 97, 120]);
  await review(
    form,
    "Reviewed original rectangle construction again after coordinate edit: x24…96, y48…119.",
  );
  await form
    .getByLabel("Image dataset split", { exact: true })
    .selectOption("validation");
  await expect(
    form.getByLabel("Mark image reviewed", { exact: true }),
  ).toBeChecked();
  await form
    .getByLabel("Image dataset split", { exact: true })
    .selectOption("test");

  const image1 = await capture(page, form, recording.id, 1);
  await form
    .getByRole("button", { name: "Add box by coordinates", exact: true })
    .click();
  await coordinates(form, [50, 48, 123, 120]);
  await form.getByLabel("Crowd annotation", { exact: true }).check();
  await review(
    form,
    "Original second red rectangle; deliberately declared crowd for COCO crowd-contract exercise, not crowd perception.",
  );
  const image2 = await capture(page, form, recording.id, 2);
  await expect(form.getByTestId("annotation-box")).toHaveCount(0);
  await expect(
    form.getByLabel("Mark image reviewed", { exact: true }),
  ).not.toBeChecked();
  await expect(
    form.getByText(
      "No annotations. A blank draft is not a reviewed negative image.",
    ),
  ).toBeVisible();
  const publishing = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/datasets",
  );
  await form
    .getByRole("button", { name: "Publish dataset", exact: true })
    .click();
  const published = await publishing;
  expect(published.status()).toBe(201);
  const dataset1 = await published.json();
  expect(dataset1.stats).toMatchObject({
    images: 3,
    reviewed: 2,
    unreviewed: 1,
    annotations: 2,
    negative_images: 0,
  });
  expect(dataset1.images[1].annotations[0].iscrowd).toBe(true);
  await expect(form).not.toBeVisible();
  const oldManifest = await (
    await page.request.get(dataset1.download_url)
  ).body();
  expect(hash(oldManifest)).toBe(dataset1.manifest_hash);

  // Only now register and run the actual pixel-dependent ONNX model.
  const artifactResponse = await page.request.post(
    "/api/v1/perception/model-artifacts?name=red-rule.onnx",
    {
      headers: { "Content-Type": "application/octet-stream" },
      data: readFileSync(path.join(fixture, "red-rule.onnx")),
    },
  );
  expect([200, 201]).toContain(artifactResponse.status());
  const artifact = await artifactResponse.json();
  const modelResponse = await page.request.post("/api/v1/perception/models", {
    data: {
      name,
      artifact_id: artifact.id,
      profile: "boxes_xyxy_v1",
      input_width: 64,
      input_height: 64,
      labels: [modelLabel],
      note: "Original authored RGB pixel rule, not trained perception.",
    },
  });
  expect(modelResponse.status()).toBe(201);
  const model = await modelResponse.json();
  const jobLabel = name + " actual ONNX";
  const submission = await page.request.post("/api/v1/perception/jobs", {
    headers: { "Idempotency-Key": randomUUID() },
    data: {
      label: jobLabel,
      model_id: model.id,
      model_revision: 1,
      recording_id: recording.id,
      channel_id: 1,
      ordinals: [0, 1, 2],
      decoder_id: decoder.id,
      decoder_version: decoder.version,
      score_threshold: 0.6,
    },
  });
  expect(submission.status()).toBe(202);
  const source = await completed(
    page,
    "/api/v1/perception/jobs",
    (await submission.json()).id,
  );
  expect(source.frames[0].detections[0].bbox_xyxy).toEqual([25, 47.5, 95, 120]);
  expect(source.frames[1].detections[0].score).toBeLessThan(
    source.frames[0].detections[0].score,
  );
  expect(source.frames[2].detections).toEqual([]);

  const launch = await launchEvaluation(page, name + " partial", jobLabel);
  let lostId = "";
  let dropped = false;
  await page.route("**/api/v1/detection-evaluations", async (route) => {
    if (route.request().method() === "POST" && !dropped) {
      dropped = true;
      const response = await route.fetch();
      expect(response.status()).toBe(202);
      lostId = (await response.json()).id;
      await route.abort("failed");
    } else await route.continue();
  });
  await launch
    .getByRole("button", { name: "Submit evaluation", exact: true })
    .click();
  await launch
    .getByRole("button", { name: "Retry evaluation submission", exact: true })
    .click();
  await expect(launch).not.toBeVisible();
  await page.unroute("**/api/v1/detection-evaluations");
  const partial = await completed(
    page,
    "/api/v1/detection-evaluations",
    lostId,
  );
  expect(partial.report.coverage).toMatchObject({
    total: 3,
    evaluated: 2,
    unreviewed: 1,
    complete: false,
  });
  expect(partial.report.metrics).toBeNull();
  expect(partial.report.classes[0]).toMatchObject({
    ap: null,
    pr_curve: null,
    tp: 1,
    crowd_ignored: 1,
  });
  const listed = await (
    await page.request.get("/api/v1/detection-evaluations?limit=100")
  ).json();
  expect(
    listed.items.filter(
      (job: { name: string }) => job.name === name + " partial",
    ),
  ).toHaveLength(1);
  await expect(page.getByTestId("detection-evaluation-id")).toHaveText(
    partial.id,
  );
  await expect(page.getByTestId("evaluation-metrics-withheld")).toBeVisible();
  await expect(page.getByTestId("evaluation-pr-chart")).toHaveCount(0);
  await page
    .getByLabel("Evaluation image row", { exact: true })
    .selectOption(image2.id);
  await expect(page.getByTestId("evaluation-overlay")).toHaveAttribute(
    "data-image-sha256",
    image2.image.sha256,
  );
  const partialBytes = await (
    await page.request.get(partial.download_url)
  ).body();

  await page.getByRole("tab", { name: "Datasets", exact: true }).click();
  await page
    .getByRole("button", { name: "Create dataset revision", exact: true })
    .click();
  const revision = page.getByRole("dialog", {
    name: "Create dataset revision",
    exact: true,
  });
  await revision
    .getByLabel("Dataset revision note", { exact: true })
    .fill(
      "Declare the empty original third image reviewed, preserving prior labels.",
    );
  await revision
    .getByRole("button", {
      name: "Select dataset image 3, ordinal 2",
      exact: true,
    })
    .click();
  await review(
    revision,
    "Original image construction paints no red rectangle in frame 2. Explicit synthetic negative declaration.",
  );
  await expect(
    revision.getByText(
      "No target instances are declared in this reviewed negative image.",
    ),
  ).toBeVisible();
  const revising = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname ===
        `/api/v1/datasets/${dataset1.id}/revisions`,
  );
  await revision
    .getByRole("button", { name: "Publish dataset revision", exact: true })
    .click();
  expect((await revising).status()).toBe(201);
  const dataset2 = await (await revising).json();
  await expect(revision).not.toBeVisible();
  expect(dataset2.stats).toMatchObject({
    reviewed: 3,
    negative_images: 1,
    unreviewed: 0,
  });
  expect(dataset2.images[0].annotations).toEqual(
    dataset1.images[0].annotations,
  );
  expect(await (await page.request.get(dataset1.download_url)).body()).toEqual(
    oldManifest,
  );
  await expect(
    page.getByLabel("Dataset revision", { exact: true }),
  ).toHaveValue("2");
  await page
    .getByLabel("Published dataset image", { exact: true })
    .selectOption(image2.id);
  await expect(
    page.getByText(
      "No target instances are declared in this reviewed negative image.",
    ),
  ).toBeVisible();

  const secondLaunch = await launchEvaluation(
    page,
    name + " complete",
    jobLabel,
  );
  await expect(
    secondLaunch.getByLabel("Evaluation dataset revision", { exact: true }),
  ).toHaveValue("2");
  const launched = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/detection-evaluations",
  );
  await secondLaunch
    .getByRole("button", { name: "Submit evaluation", exact: true })
    .click();
  const full = await completed(
    page,
    "/api/v1/detection-evaluations",
    (await (await launched).json()).id,
  );
  await expect(secondLaunch).not.toBeVisible();
  expect(full.report.coverage).toMatchObject({
    total: 3,
    evaluated: 3,
    complete: true,
  });
  expect(full.report.classes[0]).toMatchObject({
    gt: 1,
    crowd_gt: 1,
    tp: 1,
    fp: 0,
    fn: 0,
    crowd_ignored: 1,
  });
  expect(full.report.metrics.ap).toBeCloseTo(1);
  expect(full.report.classes[0].pr_curve.recall).toHaveLength(101);
  await expect(page.getByTestId("detection-evaluation-id")).toHaveText(full.id);
  await expect(page.getByTestId("evaluation-metric-ap")).toHaveText("100.00%");
  await expect(page.getByTestId("evaluation-pr-chart")).toBeVisible();
  await expect(page.getByTestId("evaluation-label-box")).toHaveAttribute(
    "x",
    "24",
  );
  await expect(page.getByTestId("evaluation-prediction-box")).toHaveAttribute(
    "x",
    "25",
  );
  await page.route(`**${image1.image.download_url}`, (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Selected PNG unavailable" },
    }),
  );
  await page
    .getByLabel("Evaluation image row", { exact: true })
    .selectOption(image1.id);
  await expect(
    page.getByRole("button", {
      name: "Retry evaluation source image",
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByTestId("evaluation-overlay")).toHaveCount(0);
  await page.unroute(`**${image1.image.download_url}`);
  await page
    .getByRole("button", { name: "Retry evaluation source image", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Crowd ignored", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Crowd ignored", exact: true })
    .click();
  await expect(page.getByTestId("evaluation-overlay")).toHaveAttribute(
    "data-image-sha256",
    image1.image.sha256,
  );
  const imageHash = await page
    .getByTestId("evaluation-overlay")
    .locator("image")
    .evaluate(async (node) => {
      const bytes = await (
        await fetch(node.getAttribute("href")!)
      ).arrayBuffer();
      return Array.from(
        new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
        (x) => x.toString(16).padStart(2, "0"),
      ).join("");
    });
  expect(imageHash).toBe(image1.image.sha256);
  const download = page.waitForEvent("download");
  await page
    .getByRole("link", { name: "Download evaluation manifest", exact: true })
    .click();
  expect(hash(readFileSync((await (await download).path())!))).toBe(
    full.manifest_hash,
  );
  expect(await (await page.request.get(partial.download_url)).body()).toEqual(
    partialBytes,
  );

  // Inject read failures only: actual computed data stays identified or is hidden.
  await page.route(`**/api/v1/detection-evaluations/${full.id}`, (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Injected same-job read failure" },
    }),
  );
  await page
    .getByRole("button", { name: "Refresh datasets workspace", exact: true })
    .click();
  await expect(page.getByText(/Evaluation refresh failed/)).toBeVisible();
  await expect(page.getByTestId("detection-evaluation-id")).toHaveText(full.id);
  await page.unroute(`**/api/v1/detection-evaluations/${full.id}`);
  await page.route(`**/api/v1/detection-evaluations/${partial.id}`, (route) =>
    route.fulfill({ status: 503, json: { detail: "Other job read failure" } }),
  );
  await page
    .getByRole("button", {
      name: `Open detection evaluation ${name} partial`,
      exact: true,
    })
    .click();
  await expect(
    page.getByText(/The selected evaluation could not be loaded/),
  ).toBeVisible();
  await expect(page.getByTestId("detection-evaluation-report")).toHaveCount(0);
  await expect(page.getByTestId("evaluation-overlay")).toHaveCount(0);
  await page.unroute(`**/api/v1/detection-evaluations/${partial.id}`);
  await page
    .getByRole("button", {
      name: `Open detection evaluation ${name} complete`,
      exact: true,
    })
    .click();
  await expect(page.getByTestId("evaluation-metric-ap")).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    )
    .toBeTruthy();
  await page.screenshot({
    path: testInfo.outputPath("dataset-evaluation-mobile.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("tab", { name: "Datasets", exact: true }).click();
  await page
    .getByRole("button", { name: "Create dataset revision", exact: true })
    .click();
  await expect(revision).toBeVisible();
  await revision.getByTestId("annotation-canvas").scrollIntoViewIfNeeded();
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    )
    .toBeTruthy();
  await page.screenshot({
    path: testInfo.outputPath("dataset-annotation-mobile.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page.keyboard.press("Escape");
  await expect(revision).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "Create dataset revision", exact: true }),
  ).toBeFocused();
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.getByRole("tab", { name: "Evaluations", exact: true }).click();
  await page
    .getByTestId("detection-evaluation-report")
    .scrollIntoViewIfNeeded();
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await page.screenshot({
    path: testInfo.outputPath("dataset-evaluation-desktop.png"),
    fullPage: true,
    animations: "disabled",
  });
  expect(errors).toEqual([]);
});
