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

async function waitCompleted(page: Page, id: string) {
  await expect
    .poll(
      async () =>
        (await (await page.request.get(`/api/v1/perception/jobs/${id}`)).json())
          .status,
      { timeout: 45_000 },
    )
    .toBe("completed");
  return (await page.request.get(`/api/v1/perception/jobs/${id}`)).json();
}

async function svgImageBytes(svg: Locator) {
  return svg.locator("image").evaluate(async (node) => {
    const response = await fetch(node.getAttribute("href")!);
    const bytes = await response.arrayBuffer();
    return Array.from(
      new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
      (value) => value.toString(16).padStart(2, "0"),
    ).join("");
  });
}

// The original ONNX graph reads RGB pixels. No inference response is fabricated.
test("recorded image → uploaded ONNX revision → input-dependent boxes and empty output → immutable prediction comparison", async ({
  page,
}, testInfo) => {
  test.setTimeout(150_000);
  page.setDefaultTimeout(15_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const fixture = testInfo.outputPath("perception-fixture");
  execFileSync(
    process.env.AXIOM_TEST_PYTHON ?? path.join(root, ".venv/bin/python"),
    [path.join(root, "examples/generate_perception_fixture.py"), fixture],
  );
  const fixtureInfo = JSON.parse(
    readFileSync(path.join(fixture, "summary.json"), "utf8"),
  );
  const name = `Pixel inference ${Date.now()}`;
  const runtime = await (
    await page.request.get("/api/v1/perception/runtimes")
  ).json();
  expect(
    runtime.find((item: { id: string }) => item.id === "onnx-cpu")?.status,
  ).toBe("available");

  await page.goto("/#perception");
  await page.getByRole("tab", { name: "Models", exact: true }).click();
  await page
    .getByRole("button", { name: "Register model", exact: true })
    .click();
  const modelForm = page.getByRole("dialog", {
    name: "Register perception model",
  });
  await modelForm.getByLabel("Model name", { exact: true }).fill(name);
  await modelForm
    .getByLabel("Inference profile", { exact: true })
    .fill("boxes_xyxy_v1");
  await modelForm.getByLabel("Model input width", { exact: true }).fill("64");
  await modelForm.getByLabel("Model input height", { exact: true }).fill("64");
  await modelForm
    .getByLabel("Ordered class labels", { exact: true })
    .fill("red region (authored pixel rule)");
  await modelForm
    .getByLabel("Model revision note", { exact: true })
    .pressSequentially(
      "Original input-dependent pixel rule; not trained perception.",
      { delay: 5 },
    );
  await expect(
    modelForm.getByLabel("Model revision note", { exact: true }),
  ).toBeFocused();
  await modelForm
    .getByLabel("ONNX model file", { exact: true })
    .setInputFiles(path.join(fixture, "red-rule.onnx"));
  await modelForm
    .getByLabel("Ordered class labels", { exact: true })
    .fill("red region (authored pixel rule)\n\nsecond class");
  await modelForm
    .getByRole("button", { name: "Register model", exact: true })
    .click();
  await expect(modelForm.getByRole("alert")).toContainText(
    "without empty lines",
  );
  await modelForm
    .getByLabel("Ordered class labels", { exact: true })
    .fill("red region (authored pixel rule)");
  const createdModel = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/perception/models",
  );
  await modelForm
    .getByRole("button", { name: "Register model", exact: true })
    .click();
  const modelResponse = await createdModel;
  expect(modelResponse.status()).toBe(201);
  const model = await modelResponse.json();
  expect(model.artifact.sha256).toBe(fixtureInfo.model_a_sha256);
  await expect(modelForm).not.toBeVisible();
  const oldManifest = await (await page.request.get(model.download_url)).body();
  expect(hash(oldManifest)).toBe(model.manifest_hash);

  // The submission originates from the displayed source image, not a guessed ordinal.
  await page.goto("/#catalog");
  await page.getByRole("tab", { name: "Recordings", exact: true }).click();
  await page
    .getByRole("button", { name: "Import MCAP", exact: true })
    .first()
    .click();
  const upload = page.getByRole("dialog", { name: "Import an MCAP recording" });
  await upload
    .getByLabel("MCAP file", { exact: true })
    .setInputFiles(path.join(fixture, "images.mcap"));
  await upload
    .getByLabel("Recording name", { exact: true })
    .fill(name + " images");
  await upload
    .getByRole("combobox", { name: "Source kind", exact: true })
    .selectOption("synthetic");
  await upload
    .getByRole("textbox", { name: /^Source label/ })
    .fill("Original RGB pixel-rule fixture, not sensor validation");
  const imported = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/recordings",
  );
  await upload
    .getByRole("button", { name: "Import recording", exact: true })
    .click();
  expect((await imported).status()).toBe(201);
  const records = await (
    await page.request.get(
      "/api/v1/recordings?q=" + encodeURIComponent(name + " images"),
    )
  ).json();
  const recording = records.find(
    (item: { name: string }) => item.name === name + " images",
  );
  expect(recording).toBeDefined();
  await expect(upload).not.toBeVisible();
  await page.getByRole("tab", { name: "Sensor replay", exact: true }).click();
  const camera = page.getByTestId("sensor-camera-panel");
  await expect(camera.getByTestId("decoded-camera-image")).toBeVisible();
  await camera
    .getByRole("button", { name: "Run inference on this image", exact: true })
    .click();
  const launch = page.getByRole("dialog", {
    name: "Create perception experiment",
  });
  await expect(
    launch.getByLabel("Inference recording", { exact: true }),
  ).toHaveValue(recording.id);
  await expect(
    launch.getByLabel("Inference camera channel", { exact: true }),
  ).toHaveValue("1");
  await expect(
    launch.getByLabel("Inference file ordinals", { exact: true }),
  ).toHaveValue("0");
  await launch
    .getByLabel("Inference model", { exact: true })
    .selectOption(model.id);
  await launch
    .getByLabel("Inference model revision", { exact: true })
    .selectOption("1");
  await launch
    .getByLabel("Experiment label", { exact: true })
    .fill(name + " A");
  await launch
    .getByLabel("Inference file ordinals", { exact: true })
    .fill("0, 1, 2");
  let lostJob: { id: string } | null = null;
  let dropped = false;
  await page.route("**/api/v1/perception/jobs", async (route) => {
    if (route.request().method() === "POST" && !dropped) {
      dropped = true;
      const response = await route.fetch();
      expect(response.status()).toBe(202);
      lostJob = await response.json();
      await route.abort("failed");
    } else await route.continue();
  });
  await launch
    .getByRole("button", { name: "Submit inference", exact: true })
    .click();
  await expect(
    launch.getByRole("button", {
      name: "Retry inference submission",
      exact: true,
    }),
  ).toBeVisible();
  await launch
    .getByRole("button", { name: "Retry inference submission", exact: true })
    .click();
  await expect(launch).not.toBeVisible();
  await page.unroute("**/api/v1/perception/jobs");
  expect(lostJob).not.toBeNull();
  const jobA = await waitCompleted(page, lostJob!.id);
  await expect(page.getByTestId("perception-job-id")).toHaveText(jobA.id);
  const listA = await (
    await page.request.get("/api/v1/perception/jobs?limit=100")
  ).json();
  expect(
    listA.items.filter((job: { label: string }) => job.label === name + " A"),
  ).toHaveLength(1);
  expect(jobA.counts).toMatchObject({
    total: 3,
    completed: 3,
    failed: 0,
    running: 0,
    not_attempted: 0,
  });
  expect(
    jobA.inputs.map((input: { ordinal: number }) => input.ordinal),
  ).toEqual([0, 1, 2]);
  expect(jobA.inputs[0].log_time_ns).toBe("1700000010003000123");
  const overlay = page.getByTestId("prediction-overlay");
  await expect(overlay).toHaveAttribute(
    "data-image-sha256",
    jobA.frames[0].image.sha256,
  );
  expect(await svgImageBytes(overlay)).toBe(jobA.frames[0].image.sha256);
  const firstBox = page.getByTestId("detection-box");
  await expect(firstBox).toHaveAttribute("x", "25");
  await expect(firstBox).toHaveAttribute("y", "47.5");
  await expect(firstBox).toHaveAttribute("width", "70");
  await expect(firstBox).toHaveAttribute("height", "72.5");
  const firstScore = jobA.frames[0].detections[0].score;
  await page
    .getByRole("button", {
      name: "Inspect inference input 2, ordinal 1",
      exact: true,
    })
    .click();
  await expect(overlay).toHaveAttribute(
    "data-image-sha256",
    jobA.frames[1].image.sha256,
  );
  await expect(firstBox).toHaveAttribute("x", "50");
  expect(jobA.frames[1].detections[0].score).toBeLessThan(firstScore);
  expect(jobA.frames[1].image.sha256).not.toBe(jobA.frames[0].image.sha256);
  await page
    .getByRole("button", {
      name: "Inspect inference input 3, ordinal 2",
      exact: true,
    })
    .click();
  await expect(overlay).toBeVisible();
  await expect(page.getByTestId("detection-box")).toHaveCount(0);
  await expect(
    page.getByText(
      "Inference completed with zero detections at the captured threshold.",
    ),
  ).toBeVisible();

  // A polling failure retains only the same identified job, with visible stale state.
  await page.route(`**/api/v1/perception/jobs/${jobA.id}`, (route) =>
    route.fulfill({ status: 503, json: { detail: "Injected read failure" } }),
  );
  await page
    .getByRole("button", { name: "Refresh perception workspace", exact: true })
    .click();
  await expect(page.getByText(/State refresh failed/)).toBeVisible();
  await expect(page.getByTestId("perception-job-id")).toHaveText(jobA.id);
  await expect(overlay).toHaveAttribute(
    "data-image-sha256",
    jobA.frames[2].image.sha256,
  );
  await page.unroute(`**/api/v1/perception/jobs/${jobA.id}`);
  await page
    .getByRole("button", { name: "Refresh perception workspace", exact: true })
    .click();

  // A new weight artifact and explicit immutable model revision, authored through the UI.
  await page.getByRole("tab", { name: "Models", exact: true }).click();
  await page
    .getByLabel("Select perception model", { exact: true })
    .selectOption(model.id);
  await page
    .getByRole("button", { name: "Create model revision", exact: true })
    .click();
  const revision = page.getByRole("dialog", {
    name: "Create model revision",
    exact: true,
  });
  await revision
    .getByLabel("ONNX model file", { exact: true })
    .setInputFiles(path.join(fixture, "red-rule-lower-score.onnx"));
  await revision
    .getByLabel("Model revision note", { exact: true })
    .fill("Multiply the authored RGB score by 0.65; same source image inputs.");
  await revision
    .getByRole("button", { name: "Publish model revision", exact: true })
    .click();
  await expect(revision).not.toBeVisible();
  await expect(
    page.getByLabel("Perception model revision", { exact: true }),
  ).toHaveValue("2");
  expect(await (await page.request.get(model.download_url)).body()).toEqual(
    oldManifest,
  );
  const modelB = await (
    await page.request.get(`/api/v1/perception/models/${model.id}?revision=2`)
  ).json();
  expect(modelB.artifact.sha256).toBe(fixtureInfo.model_b_sha256);
  await page
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await launch
    .getByLabel("Experiment label", { exact: true })
    .fill(name + " B");
  await launch
    .getByLabel("Inference model", { exact: true })
    .selectOption(model.id);
  await launch
    .getByLabel("Inference model revision", { exact: true })
    .selectOption("2");
  await launch
    .getByLabel("Inference recording", { exact: true })
    .selectOption(recording.id);
  await launch
    .getByLabel("Inference camera channel", { exact: true })
    .selectOption("1");
  await launch
    .getByLabel("Inference file ordinals", { exact: true })
    .fill("0, 1, 2");
  const submittedB = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/perception/jobs",
  );
  await launch
    .getByRole("button", { name: "Submit inference", exact: true })
    .click();
  const jobB = await waitCompleted(page, (await (await submittedB).json()).id);
  await expect(launch).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "Compare results", exact: true }),
  ).toBeVisible();
  expect(jobB.frames[0].image.sha256).toBe(jobA.frames[0].image.sha256);
  expect(jobB.frames[0].detections[0].score).toBeCloseTo(firstScore * 0.65, 5);
  await page
    .getByRole("button", { name: "Compare results", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Capture comparison", exact: true })
    .click();
  const compare = page.getByRole("dialog", {
    name: "Capture prediction comparison",
  });
  await compare
    .getByLabel("Prediction comparison name", { exact: true })
    .fill(name + " comparison");
  await compare
    .getByLabel("Prediction comparison note", { exact: true })
    .fill(
      "Same image identities, changed score gain. Prediction difference, not accuracy evidence.",
    );
  await compare
    .getByLabel("Left inference experiment", { exact: true })
    .selectOption(jobA.id);
  await compare
    .getByLabel("Right inference experiment", { exact: true })
    .selectOption(jobB.id);
  const captured = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/perception/comparisons",
  );
  await compare
    .getByRole("button", { name: "Save prediction comparison", exact: true })
    .click();
  const comparison = await (await captured).json();
  await expect(compare).not.toBeVisible();
  await expect(page.getByTestId("prediction-comparison-detail")).toBeVisible();
  expect(comparison.counts).toMatchObject({
    total: 3,
    same_input: 3,
    not_evaluated: 0,
  });
  expect(comparison.rows[0].predictions_identical).toBe(false);
  expect(comparison.rows[2].predictions_identical).toBe(true);
  await expect(page.getByTestId("prediction-overlay")).toHaveCount(2);
  const downloaded = page.waitForEvent("download");
  await page
    .getByRole("link", { name: "Download prediction comparison", exact: true })
    .click();
  const bytes = readFileSync((await (await downloaded).path())!);
  expect(hash(bytes)).toBe(comparison.manifest_hash);
  expect(JSON.parse(bytes).comparison.left_manifest_hash).toBe(
    jobA.manifest_hash,
  );

  // A failed request for another job must never relabel the previous prediction as its result.
  await page.getByRole("tab", { name: "Experiments", exact: true }).click();
  await expect(page.getByTestId("perception-job-id")).toHaveText(jobB.id);
  await page.route(`**/api/v1/perception/jobs/${jobA.id}`, (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Other selected job unavailable" },
    }),
  );
  await page
    .getByRole("button", {
      name: `Open inference experiment ${name} A`,
      exact: true,
    })
    .click();
  await expect(
    page.getByText(/This experiment could not be loaded/),
  ).toBeVisible();
  await expect(page.getByTestId("perception-job-detail")).toHaveCount(0);
  await expect(page.getByTestId("prediction-overlay")).toHaveCount(0);
  await page.unroute(`**/api/v1/perception/jobs/${jobA.id}`);
  await page
    .getByRole("button", { name: "Retry selected experiment", exact: true })
    .click();
  await expect(page.getByTestId("perception-job-id")).toHaveText(jobA.id);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    )
    .toBeTruthy();
  await page.getByRole("tab", { name: "Compare", exact: true }).click();
  await expect(page.getByTestId("prediction-comparison-detail")).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    )
    .toBeTruthy();
  await page.screenshot({
    path: testInfo.outputPath("perception-mobile.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page
    .getByTestId("prediction-comparison-report")
    .scrollIntoViewIfNeeded();
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await page.screenshot({
    path: testInfo.outputPath("perception-comparison.png"),
    animations: "disabled",
  });
  expect(errors).toEqual([]);
});
