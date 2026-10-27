import { expect, type Page, type TestInfo } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";

export async function caseStackWorkflow(page: Page, info: TestInfo, source: string, python: string,
  apiUrl: string, token = "") {
  info.setTimeout(180_000);
  const errors: string[] = []; page.on("pageerror", e => errors.push(e.message));
  const headers = token ? { Authorization: `Bearer ${token}` } : {};
  const directory = info.outputPath("actual-recorded-stack");
  execFileSync(python, ["-B", path.join(source, "examples/stack_replay_workflow.py"), "--url", apiUrl, "--output", directory],
    { cwd: source, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", ADE2E_API_TOKEN: token,
        PYTHONPATH: ["backend/src", "sdk/python/src"].map(p => path.join(source,p)).join(path.delimiter) },
      timeout: 100_000, maxBuffer: 2*1024*1024 });
  const fixture = JSON.parse(readFileSync(path.join(directory,"summary.json"),"utf8"));
  const baselineId = fixture.replays[0].id, independentId = fixture.replays[1].id;
  const get = async (url: string) => {
    const response = await page.request.get(url, { headers }); expect(response.status()).toBe(200); return response.json();
  };
  const baseline = await get(`/api/v1/stack-replays/${baselineId}`), input = baseline.inputs[2].ego;
  const originalBaseline = await (await page.request.get(baseline.download_url,{headers})).body();
  await page.goto(`/#catalog?${new URLSearchParams({recording:fixture.recording_id,channel:String(input.channel_id),at:input.log_time_ns,ordinal:String(input.ordinal)})}`);
  await expect(page.getByTestId("recording-detail")).toBeVisible();
  await page.getByRole("button",{name:"Create case from log interval",exact:true}).click();
  const modal=page.getByRole("dialog",{name:"Save investigation case",exact:true});
  await modal.getByLabel("Case title",{exact:true}).fill("Temporal ego/LiDAR investigation");
  await modal.getByLabel("Case note",{exact:true}).fill("Authored MCAP stack comparison; requests do not affect recorded vehicle motion.");
  await modal.getByLabel("Case start time ns",{exact:true}).fill(input.log_time_ns);
  await modal.getByLabel("Case end time ns",{exact:true}).fill(input.log_time_ns);
  await modal.getByRole("button",{name:"Save case",exact:true}).click();
  await expect(page.getByTestId("case-revision")).toHaveText("r1");
  const caseId=(await page.getByTestId("case-id").textContent())!;
  const originalCase=await get(`/api/v1/cases/${caseId}`);
  const originalBytes=await (await page.request.get(originalCase.download_url,{headers})).body();
  await page.getByRole("button",{name:"Configure temporal stack replay",exact:true}).click();
  await page.getByLabel("Temporal baseline replay ID",{exact:true}).fill(baselineId);
  await page.getByLabel("Temporal candidate stack ID",{exact:true}).fill(fixture.stack_id);
  await page.getByLabel("Temporal candidate revision",{exact:true}).fill("2");
  await page.getByLabel("Temporal replay purpose",{exact:true}).fill("Only headway revision changes; use all eight original ticks, not the single case tick.");
  await page.getByRole("button",{name:"Preview full temporal sequence",exact:true}).click();
  await expect(page.getByTestId("case-stack-preview")).toContainText("8 recorded ticks");
  await expect(page.getByTestId("case-stack-preview")).toContainText("1 touching this case");
  await page.getByLabel("Confirm recorded stack execution",{exact:true}).check();
  let lost=false, submitted="";
  await page.route(`**/api/v1/cases/${caseId}/stack-replays`,async route => {
    if(route.request().method()==="POST" && !lost) {
      lost=true;const response=await route.fetch();expect(response.status()).toBe(202);
      submitted=(await response.json()).stack_replays[0].job_id;await route.abort("failed");
    } else await route.continue();
  });
  await page.getByRole("button",{name:"Submit recorded stack replay",exact:true}).click();
  await expect(page.getByRole("button",{name:"Submit recorded stack replay",exact:true})).toBeEnabled();
  await page.getByRole("button",{name:"Submit recorded stack replay",exact:true}).click();
  await page.unroute(`**/api/v1/cases/${caseId}/stack-replays`);
  await expect(page.getByTestId("case-revision")).toHaveText("r2");
  await expect(page.getByTestId("case-stack-status")).toHaveText("completed",{timeout:30_000});
  await expect(page.getByTestId("case-stack-comparison-status")).toHaveText("comparable");
  await expect(page.getByTestId("case-stack-comparison").locator("tbody tr")).toHaveCount(8);
  const task=await get(`/api/v1/stack-replays/${submitted}`), independent=await get(`/api/v1/stack-replays/${independentId}`);
  expect(task.inputs).toEqual(baseline.inputs);
  expect(task.ticks.map((t:any)=>t.output.control)).toEqual(independent.ticks.map((t:any)=>t.output.control));
  expect(task.ticks.map((t:any)=>t.output.decision)).toEqual(independent.ticks.map((t:any)=>t.output.decision));
  expect(baseline.ticks[2].output.decision.mode).toBe("approach");
  expect(task.ticks[2].output.decision.mode).toBe("obstacle_stop");
  for(const i of [0,3,5])expect(task.ticks[i].output.decision.mode).toBe("unknown_stop");
  expect(task.ticks.every((t:any)=>t.control_application==="not_applied_open_loop")).toBe(true);
  await page.getByRole("button",{name:"Capture stack replay outcome",exact:true}).click();
  await expect(page.getByTestId("case-revision")).toHaveText("r3");
  await expect(page.getByTestId("case-stack-captured")).toBeVisible();
  const report=await get(`/api/v1/cases/${caseId}/stack-replays`);
  expect(report.plans).toHaveLength(1);expect(report.case_state).toBe("open");
  const c=report.plans[0].comparison;
  expect(c.total_ticks).toBe(8);expect(c.changed_request_ticks).toBeGreaterThan(0);
  expect(c.driving_verdict).toBeNull();
  const exporting=page.waitForEvent("download");
  await page.getByRole("link",{name:"Download case stack comparison JSON",exact:true}).click();
  const raw=readFileSync((await (await exporting).path())!);
  expect(createHash("sha256").update(raw).digest("hex")).toBe(report.manifest_hash);
  await page.reload();await expect(page.getByTestId("case-stack-captured")).toBeVisible();
  await page.getByRole("link",{name:"Inspect candidate stack trace",exact:true}).click();
  await expect(page.getByTestId("stack-replay-id")).toHaveText(submitted);
  await page.goto(`/#cases?case=${caseId}`);
  await expect(page.getByTestId("case-stack-comparison").locator("tbody tr")).toHaveCount(8);
  expect(await (await page.request.get(originalCase.download_url,{headers})).body()).toEqual(originalBytes);
  expect(await (await page.request.get(baseline.download_url,{headers})).body()).toEqual(originalBaseline);
  if(token)expect((await page.request.get(`/api/v1/cases/${caseId}/stack-replays`)).status()).toBe(401);
  await page.screenshot({path:info.outputPath("case-stack-desktop.png"),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
  await page.screenshot({path:info.outputPath("case-stack-mobile.png"),fullPage:true});
  expect(errors).toEqual([]);
  writeFileSync(info.outputPath("acceptance.json"),JSON.stringify({case_id:caseId,case_revision:3,
    baseline_id:baselineId,independent_candidate_id:independentId,candidate_id:submitted,
    recording_id:fixture.recording_id,stack_id:fixture.stack_id,ticks:8,case_ticks:1,
    changed_request_ticks:c.changed_request_ticks,comparison_status:c.status,unknown_ticks_preserved:[0,3,5],
    matches_independent_candidate:true,report_hash:report.manifest_hash,
    original_case_sha256:createHash("sha256").update(originalBytes).digest("hex"),
    original_baseline_sha256:createHash("sha256").update(originalBaseline).digest("hex"),
    original_bytes_preserved:true,duplicate_after_lost_response:false,browser_errors:errors},null,2));
}
