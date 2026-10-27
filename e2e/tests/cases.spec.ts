import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
test("actual evaluation failure → persistent case → exact log → append evidence and revision", async ({ page }, info) => {
  test.setTimeout(180_000);
  const errors: string[] = []; page.on("pageerror", (e) => errors.push(e.message));
  const work = info.outputPath("case-workflow");
  execFileSync(process.env.AXIOM_TEST_PYTHON ?? path.join(root, ".venv/bin/python"),
    [path.join(root, "examples/dataset_evaluation_workflow.py"), "--url", "http://127.0.0.1:8010", "--output", work],
    { env: { ...process.env, ADE2E_API_TOKEN: "" }, timeout: 100_000, maxBuffer: 2*1024*1024 });
  const summary = JSON.parse(readFileSync(path.join(work, "summary.json"), "utf8"));
  const [, baselineId, candidateId] = summary.evaluation_ids;
  const evaluation = await (await page.request.get(`/api/v1/detection-evaluations/${candidateId}`)).json();
  const originalEvaluation = await (await page.request.get(evaluation.download_url)).body();
  const failure = evaluation.report.rows.find((r: { diagnostics: { fn: number } | null }) => r.diagnostics && r.diagnostics.fn > 0);
  const asset = evaluation.dataset_snapshot.image_assets.find((a: { id: string }) => a.id === failure.image_id);
  const evaluationUrl = (id: string) => `/#datasets?${new URLSearchParams({ evaluation: id, image: failure.image_id })}`;
  await page.goto(evaluationUrl(candidateId));
  await expect(page.getByLabel("Evaluation image row", { exact: true })).toHaveValue(failure.image_id);
  await page.getByRole("button", { name: "Create investigation case", exact: true }).click();
  const modal = page.getByRole("dialog", { name: "Save investigation case", exact: true });
  await modal.getByLabel("Case title", { exact: true }).fill("Investigate candidate false negative");
  await modal.getByLabel("Case note", { exact: true }).fill("Authored ONNX/COCO workflow; source mismatch must never be hidden.");
  let dropped=false, createdId="";
  await page.route('**/api/v1/cases', async (route) => {
    if (route.request().method() === 'POST' && !dropped) {
      dropped=true; const response=await route.fetch(); expect(response.status()).toBe(201);
      createdId=(await response.json()).id; await route.abort('failed');
    } else await route.continue();
  });
  await modal.getByRole('button',{name:'Save case',exact:true}).click();
  await modal.getByRole('button',{name:'Retry case save',exact:true}).click();
  await page.unroute('**/api/v1/cases');
  await expect(page.getByTestId('case-id')).toHaveText(createdId);
  await expect(page.getByTestId('case-source-samples').locator('tbody tr')).toHaveCount(1);
  const first = await (await page.request.get(`/api/v1/cases/${createdId}`)).json();
  expect(first.segment.samples[0].ordinal).toBe(asset.input.ordinal);
  expect(first.segment.samples[0].sha256).toBe(asset.input.message_sha256);
  const original = await (await page.request.get(first.download_url)).body();
  expect(createHash('sha256').update(original).digest('hex')).toBe(first.manifest_hash);
  const list = await (await page.request.get(`/api/v1/cases?evaluation_id=${candidateId}`)).json();
  expect(list.items.filter((v:{id:string})=>v.id===createdId)).toHaveLength(1);
  await page.reload(); await expect(page.getByTestId('case-id')).toHaveText(createdId);
  await page.getByRole('link',{name:'Open original recording at anchor',exact:true}).click();
  await expect(page.getByTestId('recording-detail')).toBeVisible();
  await expect(page.getByRole('region',{name:'Recorded sample inspector'})).toHaveAttribute('data-selected-ordinal', String(asset.input.ordinal));
  await expect(page.getByRole('region',{name:'Recorded sample inspector'})).toHaveAttribute('data-selected-sha256', asset.input.message_sha256);
  const sampleReply = await (await page.request.get(`/api/v1/cases/${createdId}/samples`)).json();
  expect(sampleReply.samples[0].ordinal).toBe(asset.input.ordinal);
  await expect(page.getByRole('link',{name:/Investigate candidate false negative · r1/})).toBeVisible();
  // A log-origin interval pins all channel messages, not just the current image.
  await page.getByRole('button',{name:'Create case from log interval',exact:true}).click();
  await modal.getByLabel('Case title',{exact:true}).fill('Original interval investigation');
  await modal.getByLabel('Case note',{exact:true}).fill('All messages in an inclusive original log-time interval.');
  await modal.getByLabel('Case start time ns',{exact:true}).fill('0');
  await modal.getByLabel('Case end time ns',{exact:true}).fill('18446744073709551615');
  await modal.getByRole('button',{name:'Save case',exact:true}).click();
  await expect(page.getByTestId('case-source-samples').locator('tbody tr')).toHaveCount(6);
  const intervalId=await page.getByTestId('case-id').textContent();
  // Link the other model's evaluation of the SAME original image to the SAME case.
  await page.goto(evaluationUrl(baselineId));
  await page.getByRole('button',{name:'Create investigation case',exact:true}).click();
  await modal.getByLabel('Existing case ID',{exact:true}).fill(createdId);
  await modal.getByLabel('Case note',{exact:true}).fill('Compare the baseline evaluation for this exact image.');
  await modal.getByRole('button',{name:'Link evaluation to case',exact:true}).click();
  await expect(page.getByTestId('case-revision')).toHaveText('r2');
  await expect(page.getByTestId('case-evaluations').locator('tbody tr')).toHaveCount(2);
  await page.getByRole('button',{name:'Edit investigation',exact:true}).click();
  await page.getByLabel('Edit case state',{exact:true}).selectOption('investigating');
  await page.getByLabel('Edit case assignee',{exact:true}).fill('Internal engineer (declared)');
  await page.getByRole('button',{name:'Publish case revision',exact:true}).click();
  await expect(page.getByTestId('case-revision')).toHaveText('r3');
  expect(await (await page.request.get(first.download_url)).body()).toEqual(original);
  expect(await (await page.request.get(evaluation.download_url)).body()).toEqual(originalEvaluation);
  await page.getByRole('link',{name:'Open linked evaluation',exact:true}).first().click();
  await expect(page.getByLabel('Evaluation image row',{exact:true})).toHaveValue(failure.image_id);
  await page.getByRole('link',{name:/Investigate candidate false negative · r3/}).click();
  await expect(page.getByTestId('case-revision')).toHaveText('r3');
  await page.screenshot({path:info.outputPath('case-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
  await page.screenshot({path:info.outputPath('case-mobile.png'),fullPage:true});
  // Fail a NEW selection: do not retain the previous investigation as its data.
  await page.route('**/api/v1/cases/case_unavailable',route=>route.fulfill({status:503,json:{detail:'Selected case unavailable'}}));
  await page.goto('/#cases?case=case_unavailable');
  await expect(page.getByText('Selected case unavailable',{exact:true})).toBeVisible();
  await expect(page.getByTestId('case-detail')).toHaveCount(0);
  expect(errors).toEqual([]);
  writeFileSync(info.outputPath('case-acceptance.json'),JSON.stringify({case_id:createdId,interval_case_id:intervalId,
    dataset_id:evaluation.dataset_id,candidate_id:candidateId,baseline_id:baselineId,image_id:failure.image_id,
    exact_ordinal:asset.input.ordinal,case_revisions:3,linked_evaluations:2,interval_messages:6,
    original_case_and_evaluation_unchanged:true,lost_response_deduplicated:true,browser_errors:errors},null,2));
});
