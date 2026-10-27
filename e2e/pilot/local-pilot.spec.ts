import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

const checkout = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

test("built single-origin pilot → token login → actual ONNX/COCO evaluation → verified export", async ({ page }, testInfo) => {
  const work = process.env.AXIOM_PILOT_TEST_ROOT!;
  const bundle = path.join(work, "relocated application");
  const token = readFileSync(path.join(work, "access/api-token"), "utf8").trim();
  const base = "http://127.0.0.1:8191";
  const authorized = { Authorization: `Bearer ${token}` };
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  expect((await page.request.get("/api/v1/overview")).status()).toBe(401);
  expect((await page.request.get("/api/v1/overview", { headers: { Authorization: "Bearer incorrect" } })).status()).toBe(401);
  expect((await page.request.get("/api/v1/overview", { headers: { ...authorized, Origin: "https://outside.invalid" } })).status()).toBe(403);
  expect((await page.request.get("/", { headers: { Host: "outside.invalid" } })).status()).toBe(400);
  expect((await page.request.get("/backend/src/ade2e/main.py")).status()).toBe(404);
  const status = await (await page.request.get("/api/v1/pilot-status", { headers: authorized })).json();
  expect(status.frontend).toBe("built_static");
  expect(status.auth_required).toBe(true);
  expect(status.remote_engine_enabled).toBe(false);
  const adapters = await (await page.request.get("/api/v1/adapters", { headers: authorized })).json();
  expect(adapters.find((a: { id: string }) => a.id === "carla").status).toBe("not_configured");
  expect(await (await page.request.get("/api/v1/scenarios", { headers: authorized })).json()).toEqual([]);
  const ui = await page.request.get("/");
  expect(ui.headers()["content-security-policy"]).toContain("script-src 'self'");
  expect(await ui.text()).not.toContain(token);

  await page.goto("/#datasets");
  await page.getByRole("button", { name: "Workspace settings", exact: true }).first().click();
  const dialog = page.getByRole("dialog", { name: "Workspace settings", exact: true });
  await expect(dialog.getByText(base + "/api/v1", { exact: true })).toBeVisible();
  await dialog.locator('input[type="password"]').fill(token);
  await dialog.getByRole("button", { name: "Test & save connection", exact: true }).click();
  await expect(dialog).not.toBeVisible();

  // Both examples and SDK are taken from the frozen package, not the checkout.
  const output = path.join(work, "actual-workflow");
  const paths = ["backend/src", "sdk/python/src", "integrations/onnx/src", "integrations/coco/src"].map(p => path.join(bundle, p));
  execFileSync(process.env.AXIOM_TEST_PYTHON ?? path.join(checkout, ".venv/bin/python"),
    ["-B", path.join(bundle, "examples/dataset_evaluation_workflow.py"), "--url", base, "--output", output],
    { cwd: work, env: { ...process.env, PYTHONPATH: paths.join(path.delimiter), PYTHONDONTWRITEBYTECODE: "1", ADE2E_API_TOKEN: token },
      timeout: 100_000, maxBuffer: 2 * 1024 * 1024 });
  const summary = JSON.parse(readFileSync(path.join(output, "summary.json"), "utf8"));
  expect(summary.comparison_exports_verified).toBe(true);
  await page.reload();
  await page.getByRole("tab", { name: "Compare evaluations", exact: true }).click();
  const [, a, b] = summary.evaluation_ids;
  await page.getByLabel("Baseline evaluation ID", { exact: true }).fill(a);
  await page.getByLabel("Candidate evaluation ID", { exact: true }).fill(b);
  await expect(page.getByTestId("evaluation-comparison-status")).toHaveText("comparable");
  await expect(page.getByTestId("evaluation-comparison-images").locator("tbody tr")).toHaveCount(6);
  await page.getByLabel("Comparison image filter", { exact: true }).selectOption("more_errors");
  await expect(page.getByTestId("evaluation-comparison-images").locator("tbody tr")).toHaveCount(2);
  const descriptor = await (await page.request.get(`/api/v1/evaluation-comparisons?${new URLSearchParams({ baseline_id: a, candidate_id: b })}`, { headers: authorized })).json();
  const downloading = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download comparison HTML", exact: true }).click();
  const downloaded = readFileSync((await (await downloading).path())!);
  expect(createHash("sha256").update(downloaded).digest("hex")).toBe(descriptor.exports.html.sha256);
  expect(downloaded.toString()).toContain("All 6 selected images");
  // I1 is shipped in the same package/API, not a separate research viewer.
  const ev = await (await page.request.get(`/api/v1/detection-evaluations/${b}`, { headers: authorized })).json();
  const failed = ev.report.rows.find((r: { diagnostics: { fn: number } | null }) => r.diagnostics && r.diagnostics.fn > 0);
  await page.goto(`/#datasets?${new URLSearchParams({ evaluation: b, image: failed.image_id })}`);
  await page.getByRole("button", { name: "Create investigation case", exact: true }).click();
  const caseForm = page.getByRole("dialog", { name: "Save investigation case", exact: true });
  await caseForm.getByLabel("Case title", { exact: true }).fill("Packaged I1 investigation");
  await caseForm.getByLabel("Case note", { exact: true }).fill("Authored-source integration acceptance, not customer accuracy.");
  await caseForm.getByRole("button", { name: "Save case", exact: true }).click();
  await expect(page.getByTestId("case-detail")).toBeVisible();
  const caseId = await page.getByTestId("case-id").textContent();
  expect((await page.request.get(`/api/v1/cases/${caseId}`)).status()).toBe(401);
  await page.reload();
  await expect(page.getByTestId("case-id")).toHaveText(caseId!);
  await page.getByRole("button", { name: "Edit scene conditions", exact: true }).click();
  for (const [axis, choice] of Object.entries({ lighting: "low_light", target: "other", target_size: "medium" })) {
    await page.getByLabel(`Case condition ${axis}`, { exact: true }).selectOption(choice);
  }
  await page.getByLabel("Condition reviewer", { exact: true }).fill("Packaged workflow declaration");
  await page.getByLabel("Condition source note", { exact: true }).fill("Test-only scene labels on authored pixels; not measured lighting or road accuracy.");
  await page.getByLabel("Confirm condition review", { exact: true }).check();
  await page.getByRole("button", { name: "Publish condition revision", exact: true }).click();
  await expect(page.getByTestId("case-revision")).toHaveText("r2");
  await expect(page.getByTestId("case-condition-state")).toHaveText("reviewed");
  await expect(page.getByTestId("case-source-samples").locator("tbody tr")).toHaveCount(1);
  await page.getByRole("link", { name: "Open linked evaluation", exact: true }).click();
  await expect(page.getByLabel("Evaluation image row", { exact: true })).toHaveValue(failed.image_id);
  await page.getByRole("link", { name: /Packaged I1 investigation · r2/ }).click();
  await expect(page.getByTestId("case-id")).toHaveText(caseId!);
  await page.getByRole("tab", { name: "Condition coverage", exact: true }).click();
  // Other packaged workflows may have their own cases. Scope this assertion to
  // this test's recording instead of assuming the entire workspace has one case.
  await page.getByLabel("Condition recording scope", { exact: true }).fill(summary.recording_id);
  // Identical authored recordings are deduplicated across packaged workflows;
  // collection tests may legitimately add unclassified cases to this source.
  const scopedCoverage = await (await page.request.get(`/api/v1/case-condition-coverage?recording_id=${summary.recording_id}`, { headers: authorized })).json();
  expect(scopedCoverage.cases.find((c: { id: string }) => c.id === caseId).conditions.review_status).toBe("reviewed");
  await expect(page.getByTestId("coverage-total-cases")).toHaveText(String(scopedCoverage.denominator.total_cases));
  await expect(page.getByTestId("coverage-classified-cases")).toHaveText(String(scopedCoverage.denominator.classified_cases));
  await expect(page.getByTestId("condition-grid").locator("tbody tr")).toHaveCount(36);
  expect((await page.request.get("/api/v1/case-condition-coverage")).status()).toBe(401);
  const coverage = await (await page.request.get(`/api/v1/case-condition-coverage?recording_id=${summary.recording_id}`, { headers: authorized })).json();
  const exporting = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download condition coverage JSON", exact: true }).click();
  const coverageBytes = readFileSync((await (await exporting).path())!);
  expect(createHash("sha256").update(coverageBytes).digest("hex")).toBe(coverage.manifest_hash);
  // I3 uses the same packaged API/worker, never a CARLA or native-debugger job.
  await page.goto(`/#cases?case=${caseId}`);
  // The same hash may retain the coverage tab. Use the actual workspace control.
  await page.getByRole("tab", { name: "Cases", exact: true }).click();
  await expect(page.getByTestId("case-id")).toHaveText(caseId!);
  await page.getByRole("button", { name: "Define derived retest", exact: true }).click();
  await page.getByLabel("Retest assumptions", { exact: true }).fill("Manually authored reference-only follow test. Source labels are not simulated conditions.");
  await page.getByLabel("Acknowledge reference-only retest", { exact: true }).check();
  await page.getByRole("button", { name: "Publish retest definition", exact: true }).click();
  await expect(page.getByTestId("case-revision")).toHaveText("r3");
  await expect(page.getByTestId("retest-not-submitted")).toBeVisible();
  await page.getByRole("button", { name: "Submit 2 variants to queue", exact: true }).click();
  await expect(page.getByTestId("retest-execution-state")).toHaveText("finished", { timeout: 30_000 });
  await expect(page.getByTestId("retest-variants").locator("tbody tr")).toHaveCount(2);
  await page.getByRole("button", { name: "Capture finished outcome", exact: true }).click();
  await expect(page.getByTestId("case-revision")).toHaveText("r5");
  await expect(page.getByTestId("retest-evidence-mode")).toHaveText("captured");
  expect((await page.request.get(`/api/v1/cases/${caseId}/retests`)).status()).toBe(401);
  const retest = await (await page.request.get(`/api/v1/cases/${caseId}/retests`, { headers: authorized })).json();
  expect(retest.plans[0].attempts[0].report.counts.completed).toBe(2);
  const retestDownload = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download retest report JSON", exact: true }).click();
  expect(createHash("sha256").update(readFileSync((await (await retestDownload).path())!)).digest("hex")).toBe(retest.manifest_hash);
  await page.getByRole("tab", { name: "Retest activity", exact: true }).click();
  await expect(page.getByTestId("retest-activity-verified_sensor_condition_cells")).toHaveText("0");
  // Same pinned case also replays ACTUAL original images through registered ONNX/COCO.
  await page.goto(`/#cases?case=${caseId}`);
  await page.getByRole("tab", { name: "Cases", exact: true }).click();
  const candidateModel = ev.source_jobs[0].model_snapshot;
  await page.getByRole("button", { name: "Configure recorded model replay", exact: true }).click();
  await page.getByLabel("Model replay candidate ID", { exact: true }).fill(candidateModel.id);
  await page.getByLabel("Model replay candidate revision", { exact: true }).fill(String(candidateModel.revision));
  await page.getByLabel("Model replay purpose", { exact: true }).fill("Packaged full original split replay; same model repeatability, not a new accuracy claim.");
  await page.getByRole("button", { name: "Preview full replay workload", exact: true }).click();
  await expect(page.getByTestId("model-replay-preview")).toContainText("6 images");
  await page.getByLabel("Confirm trusted model replay", { exact: true }).check();
  await page.getByRole("button", { name: "Submit recorded inference", exact: true }).click();
  await expect(page.getByTestId("case-revision")).toHaveText("r6");
  await expect(page.getByTestId("model-replay-inference-state")).toHaveText("completed", { timeout: 40_000 });
  await page.getByRole("button", { name: "Evaluate stored replay predictions", exact: true }).click();
  await expect(page.getByTestId("case-revision")).toHaveText("r7");
  await expect(page.getByTestId("model-replay-evaluation-state")).toHaveText("completed", { timeout: 40_000 });
  await page.getByRole("button", { name: "Capture model replay outcome", exact: true }).click();
  await expect(page.getByTestId("case-revision")).toHaveText("r8");
  await expect(page.getByTestId("model-replay-captured")).toBeVisible();
  const replay = await (await page.request.get(`/api/v1/cases/${caseId}/model-replays`, { headers: authorized })).json();
  expect(replay.plans[0].comparison.status).toBe("comparable");
  expect(replay.plans[0].comparison.metrics.every((m: { delta: number | null }) => m.delta === 0 || m.delta === null)).toBe(true);
  expect((await page.request.get(`/api/v1/cases/${caseId}/model-replays`)).status()).toBe(401);
  await expect(page.getByTestId("retest-evidence-mode")).toHaveText("captured");
  await expect(page.getByTestId("case-condition-state")).toHaveText("reviewed");
  await page.screenshot({ path: testInfo.outputPath("pilot-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("pilot-mobile.png"), fullPage: true });
  expect(errors).toEqual([]);
});
