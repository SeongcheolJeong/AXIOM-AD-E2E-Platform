import {test,expect} from '@playwright/test';

test('display contract: legacy 30-frame flag does not imply reduced resolution',async({page,request})=>{
 const execution=process.env.AXIOM_ADAS_DISPLAY_EXECUTION??'job_51a77f9b001d44a1bdedead51deb31dd';
 const run=await(await request.get(`/api/v1/experiments/executions/${execution}`)).json();
 expect(run.status).toBe('completed');
 const summaryUrl=run.artifacts.find((artifact:{name:string})=>artifact.name==='summary.json').download_url;
 const actualSummary=await(await request.get(summaryUrl)).json();expect(actualSummary.acceptance_1080p30).toBe(true);
 const url=`/#hil-sim?project=default&mode=fpga&target=engineering-lab&tab=module_rtl&execution=${execution}`;
 await page.goto(url);await expect(page.locator('.module-acceptance-note')).toContainText('4×1080p30 · 1초/30프레임 인수조건 구성');
 // Display-only 1080p/300-frame contract fixture; retained source artifacts remain unchanged.
 const longRun={...actualSummary,acceptance_1080p30:false,requested_frames:1200,source_window_ns:10000000000};
 await page.route('**'+summaryUrl,route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(longRun)}));
 await page.reload();const note=page.locator('.module-acceptance-note');
 await expect(note).toContainText('1초/30프레임 인수조건과 다른 구성');
 await expect(note).not.toContainText('축소 구성');await expect(note).not.toContainText('1080p30 처리 검증 아님');
});
