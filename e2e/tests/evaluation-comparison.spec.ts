import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

test("actual ONNX and COCO A/B → full-denominator review → portable export and selection failure", async ({ page, context }, testInfo) => {
  test.setTimeout(180_000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const output = testInfo.outputPath("actual-evaluation-workflow");
  // The model and labels are authored pixels, not real-world accuracy data.
  // Native ONNX and COCO execute through the real API/queue; no result mocks.
  execFileSync(process.env.AXIOM_TEST_PYTHON ?? path.join(root, ".venv/bin/python"),
    [path.join(root, "examples/dataset_evaluation_workflow.py"), "--url", "http://127.0.0.1:8010", "--output", output],
    { env: { ...process.env, ADE2E_API_TOKEN: "" }, timeout: 100_000, maxBuffer: 2 * 1024 * 1024 });
  const summary = JSON.parse(readFileSync(path.join(output, "summary.json"), "utf8"));
  const [partial, a, b] = summary.evaluation_ids;
  expect(summary.comparison_exports_verified).toBe(true);
  await page.goto("/#datasets");
  await page.getByRole("tab", { name: "Compare evaluations", exact: true }).click();
  await page.getByLabel("Baseline evaluation ID", { exact: true }).fill(a);
  await page.getByLabel("Candidate evaluation ID", { exact: true }).fill(b);
  await expect(page.getByTestId("evaluation-comparison-status")).toHaveText("comparable");
  await expect(page.getByTestId("evaluation-comparison-images").locator("tbody tr")).toHaveCount(6);
  expect(Number(await page.getByTestId("comparison-delta-ap").textContent())).toBeLessThan(0);
  await page.getByLabel("Comparison image filter", { exact: true }).selectOption("more_errors");
  await expect(page.getByTestId("evaluation-comparison-images").locator("tbody tr")).toHaveCount(2);
  await expect(page.getByTestId("comparison-denominator")).toContainText("2 of 6");
  const descriptor = await (await page.request.get(`/api/v1/evaluation-comparisons?${new URLSearchParams({ baseline_id: a, candidate_id: b })}`)).json();
  const downloading = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download comparison HTML", exact: true }).click();
  const saved = (await (await downloading).path())!;
  expect(createHash("sha256").update(readFileSync(saved)).digest("hex")).toBe(descriptor.exports.html.sha256);
  expect(readFileSync(saved, "utf8")).toContain("All 6 selected images");
  const reportPage = await context.newPage();
  await reportPage.goto(pathToFileURL(path.join(output, "comparison.html")).toString());
  await expect(reportPage.getByRole("heading", { name: "All 6 selected images" })).toBeVisible();
  await reportPage.screenshot({ path: testInfo.outputPath("export-report.png"), fullPage: true });
  await reportPage.close();
  await page.getByLabel("Comparison image filter", { exact: true }).selectOption("all");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("comparison-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1080 });
  await page.screenshot({ path: testInfo.outputPath("comparison-desktop.png"), fullPage: true });
  await page.getByLabel("Baseline evaluation ID", { exact: true }).fill(partial);
  await expect(page.getByTestId("evaluation-comparison-status")).toHaveText("not_comparable");
  await expect(page.getByTestId("comparison-delta-ap")).toHaveText("—");
  await expect(page.getByTestId("evaluation-comparison-images").locator("tbody tr")).toHaveCount(6);
  // A failed new selection must hide the previous pair immediately.
  await page.route("**/api/v1/evaluation-comparisons?**", (route) => route.fulfill({ status: 503, json: { detail: "Selected comparison unavailable" } }));
  await page.getByLabel("Baseline evaluation ID", { exact: true }).fill(a);
  await expect(page.getByText("Selected comparison unavailable", { exact: true })).toBeVisible();
  await expect(page.getByTestId("evaluation-comparison-report")).toHaveCount(0);
  await page.unroute("**/api/v1/evaluation-comparisons?**");
  expect(errors).toEqual([]);
});
