import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const sha = (b: Buffer) => createHash("sha256").update(b).digest("hex");

test("actual evaluation case → authored retest → durable campaign → failed outcome captured without false sensor coverage", async ({ page }, info) => {
  test.setTimeout(180_000);
  const errors: string[] = []; page.on("pageerror", e => errors.push(e.message));
  const work = info.outputPath("actual-workflow");
  execFileSync(process.env.AXIOM_TEST_PYTHON ?? path.join(root, ".venv/bin/python"),
    [path.join(root, "examples/dataset_evaluation_workflow.py"), "--url", "http://127.0.0.1:8010", "--output", work],
    { env: { ...process.env, ADE2E_API_TOKEN: "" }, timeout: 100_000, maxBuffer: 2*1024*1024 });
  const source = JSON.parse(readFileSync(path.join(work, "summary.json"), "utf8"));
  const evaluation = await (await page.request.get(`/api/v1/detection-evaluations/${source.evaluation_ids[2]}`)).json();
  const failed = evaluation.report.rows.find((r: {image_id:string; diagnostics: {fn:number}|null}) => r.diagnostics && r.diagnostics.fn > 0);
  const originalEval = await (await page.request.get(evaluation.download_url)).body();
  await page.goto(`/#datasets?${new URLSearchParams({evaluation:evaluation.id,image:failed.image_id})}`);
  await page.getByRole("button",{name:"Create investigation case",exact:true}).click();
  const dialog = page.getByRole("dialog",{name:"Save investigation case",exact:true});
  await dialog.getByLabel("Case title",{exact:true}).fill("I3 reference investigation");
  await dialog.getByLabel("Case note",{exact:true}).fill("Original authored images, not measured data. Retest is only a manually inspired object test.");
  await dialog.getByRole("button",{name:"Save case",exact:true}).click();
  await expect(page.getByTestId("case-id")).toBeVisible();
  const cid = (await page.getByTestId("case-id").textContent())!;
  const originalCase = await (await page.request.get(`/api/v1/cases/${cid}/export?revision=1`)).body();
  await page.getByRole("button",{name:"Define derived retest",exact:true}).click();
  const form = page.getByRole("group",{name:"Derived retest definition",exact:true});
  await form.getByLabel("Retest name",{exact:true}).fill("I3 complete execution, failed criterion");
  await form.getByLabel("Retest assumptions",{exact:true}).fill("Gaps 20m and 10m are manually assumed. Source camera data is not used by this object-reference engine.");
  await form.getByLabel("Retest criterion 1 name",{exact:true}).fill("Deliberately impossible zero travel");
  await form.getByLabel("Retest criterion 1 metric",{exact:true}).selectOption("distance_m");
  await form.getByLabel("Retest criterion 1 operator",{exact:true}).selectOption("lte");
  await form.getByLabel("Retest criterion 1 threshold",{exact:true}).fill("0");
  await expect(form.getByRole("button",{name:"Publish retest definition",exact:true})).toBeDisabled();
  await form.getByLabel("Acknowledge reference-only retest",{exact:true}).check();
  await form.getByRole("button",{name:"Publish retest definition",exact:true}).click();
  await expect(page.getByTestId("case-revision")).toHaveText("r2");
  await expect(page.getByTestId("retest-not-submitted")).toBeVisible();
  const defined = await (await page.request.get(`/api/v1/cases/${cid}`)).json();
  const plan = defined.retests[0];
  expect(plan.attempts).toHaveLength(0);
  let dropped = false;
  await page.route("**/api/v1/cases/*/retests/*/campaigns",async route=>{
    if(route.request().method()==="POST"&&!dropped){dropped=true;const response=await route.fetch();expect(response.status()).toBe(202);await route.abort("failed");}
    else await route.continue();
  });
  await page.getByRole("button",{name:"Submit 2 variants to queue",exact:true}).click();
  await expect(page.getByRole("link",{name:"Reload latest case before changing the request",exact:true})).toBeVisible();
  await page.getByRole("button",{name:"Submit 2 variants to queue",exact:true}).click();
  await page.unroute("**/api/v1/cases/*/retests/*/campaigns");
  await expect(page.getByTestId("case-revision")).toHaveText("r3");
  await expect(page.getByTestId("retest-execution-state")).toHaveText("finished",{timeout:30_000});
  await expect(page.getByTestId("retest-verdict")).toHaveText("failed");
  await expect(page.getByTestId("retest-variants").locator("tbody tr")).toHaveCount(2);
  const queued = await (await page.request.get(`/api/v1/cases/${cid}`)).json();
  expect(queued.retests[0].attempts).toHaveLength(1);
  const live=await (await page.request.get(`/api/v1/cases/${cid}/retests`)).json();
  const campaign=live.plans[0].attempts[0].report;
  expect(campaign.counts).toMatchObject({total:2,completed:2,checks_failed:2,passed_cases:0,failed_cases:2});
  await page.getByRole("button",{name:"Capture finished outcome",exact:true}).click();
  await expect(page.getByTestId("case-revision")).toHaveText("r4");
  await expect(page.getByTestId("retest-evidence-mode")).toHaveText("captured");
  const captured=await (await page.request.get(`/api/v1/cases/${cid}/retests`)).json();
  const download=page.waitForEvent("download");
  await page.getByRole("link",{name:"Download retest report JSON",exact:true}).click();
  const bytes=readFileSync((await (await download).path())!);
  expect(sha(bytes)).toBe(captured.manifest_hash);
  await page.reload();await expect(page.getByTestId("retest-evidence-mode")).toHaveText("captured");
  expect(await (await page.request.get(`/api/v1/cases/${cid}/export?revision=1`)).body()).toEqual(originalCase);
  expect(await (await page.request.get(evaluation.download_url)).body()).toEqual(originalEval);
  expect((await (await page.request.get(`/api/v1/cases/${cid}`)).json()).state).toBe("open");
  // Existing result and queue pages receive exact IDs, not the most recent item.
  await page.getByRole("link",{name:"Inspect result base",exact:true}).click();
  expect(new URLSearchParams(page.url().split("?")[1]).get("run")).toBe(campaign.cases[0].run_id);
  await page.goto(`/#cases?case=${cid}`);
  await page.getByRole("link",{name:"Inspect queue job base",exact:true}).click();
  await expect(page.getByTestId("job-id")).toHaveText(campaign.cases[0].job_id);
  await page.goto(`/#cases?case=${cid}`);
  await page.getByRole("tab",{name:"Retest activity",exact:true}).click();
  await expect(page.getByTestId("retest-activity-verified_sensor_condition_cells")).toHaveText("0");
  await expect(page.getByTestId("retest-activity-table")).toContainText("failed: 2");
  await page.screenshot({path:info.outputPath("retest-activity.png"),fullPage:true});
  await page.getByRole("link",{name:"I3 complete execution, failed criterion",exact:true}).click();
  await page.getByRole("region",{name:"Case retests",exact:true}).screenshot({path:info.outputPath("retest-result.png")});
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
  await page.screenshot({path:info.outputPath("retest-mobile.png"),fullPage:true});
  expect(errors).toEqual([]);
  writeFileSync(info.outputPath("acceptance.json"),JSON.stringify({case_id:cid,recording_id:defined.segment.recording_id,
    source_evaluation:evaluation.id,plan_id:plan.id,campaign_id:campaign.id,case_revision:4,variants:2,completed:2,failed:2,
    original_case_sha256:sha(originalCase),original_evaluation_sha256:sha(originalEval),report_hash:captured.manifest_hash,
    lost_response_deduplicated:true,sensor_condition_coverage_claimed:false,browser_errors:errors},null,2));
});
