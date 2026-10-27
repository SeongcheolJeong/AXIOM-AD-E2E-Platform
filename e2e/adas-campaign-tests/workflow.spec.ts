import {test,expect,type Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const url='/#hil-sim?project=default&mode=fpga&target=engineering-lab&tab=module_rtl&moduleView=campaign';
async function author(page:Page){
 await page.goto(url);await expect(page.getByRole('heading',{name:'보드 전 검증 캠페인',exact:true})).toBeVisible();
 await page.getByLabel('캠페인 모드',{exact:true}).selectOption('extended_smoke');
 await page.getByLabel('캠페인 이름',{exact:true}).fill('Native preboard browser '+Date.now());
 await page.getByLabel('캠페인 장기 프레임',{exact:true}).fill('30');
 await page.getByLabel('캠페인 seeds',{exact:true}).fill('42, 7');
 await page.getByLabel('캠페인 reset 횟수',{exact:true}).fill('2');
 await page.getByLabel('캠페인 stall cycles',{exact:true}).fill('5000, 8000');
 await page.getByLabel('캠페인 미리보기',{exact:true}).fill('0, 1, 29');
}
test('native preboard campaign: plan, immutable save, execute, gates, bounded frames, resume, export and cancel',async({page,request})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await author(page);
 await page.getByRole('button',{name:'시험 계획 확인',exact:true}).click();await expect(page.locator('.campaign-plan')).toContainText('12개 검사');await expect(page.locator('.campaign-plan')).toContainText('아직 실행하지 않은 입력 일정');
 await page.getByRole('button',{name:'캠페인 개정 저장',exact:true}).click();await expect(page.getByRole('button',{name:'새 검증 캠페인 실행',exact:true})).toBeEnabled();
 const savedQuery=new URLSearchParams(page.url().split('?')[1]);expect(savedQuery.get('campaign')).toBeTruthy();expect(savedQuery.get('campaignRevision')).toBe('1');
 await page.getByRole('button',{name:'새 검증 캠페인 실행',exact:true}).click();await expect(page.locator('.campaign-run-identity')).toContainText('이번 세션에서 제출한 새 실행');await expect(page.getByLabel('캠페인 실행 진행')).toContainText('최종 판정 전');await expect(page.locator('.campaign-status')).toContainText('캠페인 완료',{timeout:480000});
 await expect(page.getByRole('heading',{name:'캠페인 실행 결과',exact:true})).toBeVisible();
 const execution=await page.getByLabel('보존 캠페인 실행',{exact:true}).inputValue();const run=await(await request.get(`/api/v1/experiments/executions/${execution}`)).json();
 const summary=await(await request.get(run.artifacts.find((a:any)=>a.name==='summary.json').download_url)).json();
 expect(summary.engine).toBe('adas_validation_campaign');expect(summary.verdict).toBe('passed');expect(summary.mode).toBe('extended_smoke');expect(summary.gates.full1080p_continuous).toBe('not_evaluated');expect(summary.cases).toHaveLength(12);expect(summary.preboard.board_ready).toBe(false);expect(summary.cases.filter((c:any)=>c.frame_manifest_artifact).every((c:any)=>c.validation_verdict==='passed')).toBe(true);expect(summary.source_seed_diversity.unique_inputs).toBe(2);await expect(page.locator('.campaign-seed-diversity')).toContainText('서로 다른 입력 2종');expect(summary.fifo_sweep.some((x:any)=>x.overflow)).toBe(true);expect(summary.fifo_sweep.some((x:any)=>!x.overflow)).toBe(true);
 await expect(page.locator('.campaign-results')).toContainText('1080p 통과 주장 없음');await expect(page.locator('.campaign-board-checklist')).toContainText('설정되지 않음');await expect(page.getByLabel('보드 참고 프로필')).toContainText('ULX3S');await expect(page.getByLabel('보드 참고 프로필')).toContainText('3 Mbit/s');await expect(page.getByLabel('보드 참고 프로필')).toContainText('보드 예제 · 실제 연결 안 됨');await expect(page.getByLabel('보드 참고 프로필')).toContainText('실시간 4채널 RAW 전송을 지원하지 않습니다');
 await page.getByLabel('캠페인 관찰 프레임',{exact:true}).selectOption('29');await expect(page.locator('.campaign-selected-case .module-preview-grid img')).toHaveCount(4);
 const restored=page.url();await page.reload();await expect(page.locator('.campaign-run-identity')).toContainText('저장 실행 다시 보기');await expect(page.getByLabel('캠페인 관찰 프레임',{exact:true})).toHaveValue('29');expect(page.url()).toBe(restored);
 await page.locator('.campaign-case-table tr').filter({hasText:'repeated-reset-42'}).getByRole('button').click();await page.getByLabel('캠페인 관찰 프레임',{exact:true}).selectOption('1');const image=page.locator('.campaign-selected-case .module-image').first();await expect(image).toContainText('정상 출력 이미지 없음');await expect(image.locator('img')).toHaveCount(0);await page.getByLabel('캠페인 영상 단계',{exact:true}).selectOption('input');await expect(image.locator('img')).toHaveCount(1);await expect(image).toContainText('입력 원본 · 출력 무효');
 const download=page.waitForEvent('download');await page.getByRole('button',{name:'캠페인 실행 근거 내보내기',exact:true}).click();const evidence=await download;expect(evidence.suggestedFilename()).toBe(`${execution}-campaign-evidence.json`);await evidence.saveAs('test-results/adas-campaign-evidence.json');const receipt=await readFile('test-results/adas-campaign-evidence.json');expect(createHash('sha256').update(receipt).digest('hex')).toBe(run.manifest_hash);const receiptData=JSON.parse(receipt.toString());expect(receiptData.execution.id).toBe(execution);expect(receiptData.prepared.input.test.sha256).toBe(summary.test_ref.sha256);
 await expect(page.getByLabel('캠페인 모드',{exact:true})).toBeVisible();await page.getByLabel('보드 참고 프로필').screenshot({path:'test-results/adas-campaign-board.png'});await page.locator('.campaign-fifo-sweep').screenshot({path:'test-results/adas-campaign-fifo.png'});await page.locator('.adas-campaign').screenshot({path:'test-results/adas-campaign-desktop.png'});await page.setViewportSize({width:760,height:1000});await expect.poll(()=>page.locator('.sidebar').evaluate(e=>e.getBoundingClientRect().right)).toBeLessThanOrEqual(0);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.locator('.adas-campaign').screenshot({path:'test-results/adas-campaign-narrow.png'});
 await page.getByRole('button',{name:'새 검증 캠페인 실행',exact:true}).click();await expect(page.getByLabel('보존 캠페인 실행',{exact:true})).not.toHaveValue(execution);await page.getByRole('button',{name:'캠페인 취소',exact:true}).click();await expect(page.locator('.campaign-status')).toContainText('캠페인 취소됨',{timeout:30000});await expect(page.getByRole('heading',{name:'캠페인 실행 결과',exact:true})).toHaveCount(0);
 await page.getByLabel('보존 캠페인 실행',{exact:true}).selectOption(execution);await expect(page.getByRole('heading',{name:'캠페인 실행 결과',exact:true})).toBeVisible();
 await test.step('UI contract fixture: downstream validation failure overrides a passed native verdict',async()=>{
  // Display-only negative fixture. Native runs, saved artifacts and the evidence above remain unchanged.
  const fixture=structuredClone(summary);fixture.verdict='failed';fixture.cases[0].validation_verdict='failed';fixture.cases[0].validation_errors=['contract fixture: downstream readback mismatch'];expect(fixture.cases[0].summary.verdict).toBe('passed');
  await page.route('**'+run.artifacts.find((a:any)=>a.name==='summary.json').download_url,route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(fixture)}));
  await page.getByLabel('보존 캠페인 실행',{exact:true}).selectOption('');await page.getByLabel('보존 캠페인 실행',{exact:true}).selectOption(execution);
  await expect(page.locator('.campaign-results .eng-metrics>div').filter({hasText:'실패 case'}).locator('strong')).toHaveText('1');await expect(page.locator('.campaign-results .eng-metrics>div').filter({hasText:'통과 case'}).locator('strong')).toHaveText('11');
  const row=page.locator('.campaign-case-table tr').filter({hasText:'long-continuous'});await expect(row).toContainText('실패');await row.getByRole('button').click();await expect(page.locator('.campaign-validation-errors')).toContainText('downstream readback mismatch');
 });expect(errors).toEqual([]);
});
