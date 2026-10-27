import { expect, type Page, type TestInfo } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import path from "node:path";

export async function annotationWorkflow(page: Page, info: TestInfo, source: string, python: string, apiUrl: string, token = "") {
  info.setTimeout(180_000);
  const errors: string[]=[]; page.on("pageerror",e=>errors.push(e.message));
  const headers=token?{Authorization:`Bearer ${token}`}:{}, directory=info.outputPath("actual-annotation-data");
  execFileSync(python,["-B",path.join(source,"examples/dataset_evaluation_workflow.py"),"--url",apiUrl,"--output",directory],
    {cwd:source,env:{...process.env,ADE2E_API_TOKEN:token,PYTHONDONTWRITEBYTECODE:"1",
      PYTHONPATH:["backend/src","sdk/python/src","integrations/onnx/src","integrations/coco/src"].map(p=>path.join(source,p)).join(path.delimiter)},
      timeout:100_000,maxBuffer:2*1024*1024});
  const fixture=JSON.parse(readFileSync(path.join(directory,"summary.json"),"utf8"));
  const get=async(url:string)=>{const r=await page.request.get(url,{headers});expect(r.status()).toBe(200);return r.json();};
  const baseline=await get(`/api/v1/detection-evaluations/${fixture.evaluation_ids[1]}`);
  const original=await get(`/api/v1/datasets/${fixture.dataset_id}?revision=2`), model=baseline.source_jobs[0].model_snapshot;
  const create=await page.request.post('/api/v1/datasets',{headers,data:{name:'Automatic labels · human-review workflow',labels:original.labels,
    note:'Authored pixels and rule model; no real-road or semantic accuracy claim.',images:original.images.map((m:any,i:number)=>
      i===5?m:{image_id:m.image_id,split:m.split,review_status:'unreviewed',annotation_source:'unknown',annotations:[]})}});
  expect(create.status()).toBe(201);const dataset=await create.json();
  const beforeBytes=await (await page.request.get(dataset.download_url,{headers})).body();
  await page.goto(`/#datasets?dataset=${dataset.id}&revision=1`);
  await page.getByRole('button',{name:'Configure automatic labels',exact:true}).click();
  await page.getByLabel('Auto-label model',{exact:true}).selectOption(model.id);
  await page.getByLabel('Auto-label purpose',{exact:true}).fill('Generate proposals for empty unreviewed images; preserve existing negative labels.');
  await page.getByRole('button',{name:'Preview annotation workload',exact:true}).click();
  await expect(page.getByTestId('annotation-preview')).toContainText('5 images to infer');
  expect((await get(`/api/v1/datasets/${dataset.id}/annotation-tasks`)).items).toHaveLength(0);
  await page.getByLabel('Confirm trusted automatic labeling',{exact:true}).check();
  let lost=false,taskId='';
  await page.route(`**/api/v1/datasets/${dataset.id}/annotation-tasks`,async route=>{
    if(route.request().method()==='POST'&&!lost){lost=true;const r=await route.fetch();expect(r.status()).toBe(202);taskId=(await r.json()).id;await route.abort('failed');}
    else await route.continue();
  });
  await page.getByRole('button',{name:'Generate label drafts',exact:true}).click();
  await expect(page.getByRole('button',{name:'Generate label drafts',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'Generate label drafts',exact:true}).click();
  await page.unroute(`**/api/v1/datasets/${dataset.id}/annotation-tasks`);
  await expect(page.getByTestId('annotation-task-state')).toHaveText('finished',{timeout:30_000});
  await expect(page.getByTestId('annotation-task-id')).toHaveText(taskId);
  await expect(page.getByTestId('annotation-rows').locator('tbody tr')).toHaveCount(5);
  const ready=await get('/api/v1/annotation-tasks/'+taskId);
  expect(ready.counts.suggested).toBe(4);expect(ready.counts.empty_prediction).toBe(1);
  expect((await get(`/api/v1/datasets/${dataset.id}/annotation-tasks`)).items).toHaveLength(1);
  expect((await get('/api/v1/datasets/'+dataset.id)).revision).toBe(1);
  await page.getByRole('button',{name:'Publish unreviewed draft revision',exact:true}).click();
  await expect(page).toHaveURL(new RegExp(`dataset=${dataset.id}&revision=2`));
  await expect(page.getByTestId('annotation-published')).toBeVisible();
  const draft=await get(`/api/v1/datasets/${dataset.id}?revision=2`);
  expect(draft.stats.reviewed).toBe(1);expect(draft.stats.unreviewed).toBe(5);expect(draft.stats.negative_images).toBe(1);
  expect(draft.images[5]).toEqual(dataset.images[5]);
  const emptyId=ready.rows.find((r:any)=>r.status==='empty_prediction').image_id;
  const emptyMember=draft.images.find((m:any)=>m.image_id===emptyId);
  expect(emptyMember.review_status).toBe('unreviewed');expect(emptyMember.annotations).toEqual([]);
  expect(await (await page.request.get(dataset.download_url,{headers})).body()).toEqual(beforeBytes);
  const report=await get('/api/v1/annotation-tasks/'+taskId);
  const downloading=page.waitForEvent('download');
  await page.getByRole('link',{name:'Download annotation report JSON',exact:true}).click();
  const bytes=readFileSync((await (await downloading).path())!);
  expect(createHash('sha256').update(bytes).digest('hex')).toBe(report.manifest_hash);
  await page.getByRole('button',{name:'Create dataset revision',exact:true}).click();
  const editor=page.getByRole('dialog',{name:'Create dataset revision',exact:true});
  await expect(editor.getByTestId('annotation-lineage')).toContainText('5 image(s)');
  await editor.getByLabel('Select dataset annotation',{exact:true}).selectOption('auto-0');
  const corrected=String(draft.images[0].annotations[0].bbox_xyxy[0]+1);
  await editor.getByLabel('X minimum',{exact:true}).fill(corrected);
  await editor.getByRole('button',{name:'Apply box coordinates',exact:true}).click();
  await editor.getByLabel('Image reviewer',{exact:true}).fill('Browser-declared reviewer');
  await editor.getByLabel('Image review note',{exact:true}).fill('Inspected authored original pixels and corrected one box; still model-assisted, not independent benchmark.');
  await editor.getByLabel('Mark image reviewed',{exact:true}).check();
  await editor.getByLabel('Dataset revision note',{exact:true}).fill('One explicit image review; other suggestions remain unreviewed.');
  await editor.getByRole('button',{name:'Publish dataset revision',exact:true}).click();
  await expect(editor).not.toBeVisible();
  const reviewed=await get('/api/v1/datasets/'+dataset.id);
  expect(reviewed.revision).toBe(3);expect(reviewed.stats.reviewed).toBe(2);expect(reviewed.stats.unreviewed).toBe(4);
  expect(reviewed.images[0].annotations[0].bbox_xyxy[0]).toBe(Number(corrected));
  const lineage=await get(`/api/v1/datasets/${dataset.id}/annotation-lineage?revision=3`);
  expect(Object.keys(lineage.images)).toHaveLength(5);
  const selfEvaluation=await page.request.post('/api/v1/detection-evaluations',{headers:{...headers,'Idempotency-Key':randomUUID()},data:{
    ...baseline.request,dataset_id:dataset.id,dataset_revision:3,inference_job_ids:report.jobs.map((j:any)=>j.id),name:'Must not self-score'}});
  expect(selfEvaluation.status()).toBe(422);expect(await selfEvaluation.text()).toContain('Self-evaluation blocked');
  expect((await get('/api/v1/annotation-tasks/'+taskId)).manifest_hash).toBe(report.manifest_hash);
  await page.goto(`/#datasets?dataset=${dataset.id}&revision=3`);
  await expect(page.getByTestId('annotation-lineage')).toContainText('5 image(s)');
  if(token)expect((await page.request.get('/api/v1/annotation-tasks/'+taskId)).status()).toBe(401);
  await page.screenshot({path:info.outputPath('annotations-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
  await page.screenshot({path:info.outputPath('annotations-mobile.png'),fullPage:true});
  expect(errors).toEqual([]);
  writeFileSync(info.outputPath('acceptance.json'),JSON.stringify({dataset_id:dataset.id,task_id:taskId,model_id:model.id,
    source_revision:1,draft_revision:2,reviewed_revision:3,total_images:6,inferred_images:5,preserved_images:1,
    generated_boxes:4,empty_unreviewed:1,reviewed_after_draft:1,reviewed_after_manual_edit:2,
    known_self_evaluation_http:422,no_duplicate_after_response_loss:true,original_sha256:createHash('sha256').update(beforeBytes).digest('hex'),
    draft_hash:draft.manifest_hash,reviewed_hash:reviewed.manifest_hash,report_hash:report.manifest_hash,browser_errors:errors,
    scope:'Actual ONNX on authored pixels and pixel-rule model, human-review UI. Not a trained semantic model or real-road acceptance.'},null,2));
}
