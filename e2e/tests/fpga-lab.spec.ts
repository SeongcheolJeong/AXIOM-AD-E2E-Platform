import {test,expect} from '@playwright/test';

test('actual SimPy: author, execute, inspect, compare, resume and export',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/#hil-sim?project=default&mode=fpga');
  await expect(page.getByRole('heading',{name:'FPGA Lab 동작·타이밍'})).toBeVisible();
  await page.getByLabel('설계 이름',{exact:true}).fill('Browser FPGA '+Date.now());
  await page.getByRole('button',{name:'개정 저장',exact:true}).click();
  await expect(page.getByRole('button',{name:'계산 실행',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'계산 실행',exact:true}).click();
  await expect(page.getByRole('heading',{name:/저장 실행 A/})).toBeVisible({timeout:90000});
  await expect(page.getByText('실행 상태: completed')).toBeVisible();
  const first=await page.getByLabel('실행 A',{exact:true}).inputValue();
  await page.getByRole('button',{name:'다음 사건',exact:true}).click();
  await page.getByRole('button',{name:'다음 프레임',exact:true}).click();
  await expect(page.getByRole('img',{name:'선택 프레임 타이밍 파형'})).toBeVisible();
  await page.getByLabel('사건 위치').fill('120');
  const savedURL=page.url();
  await page.reload();
  await expect(page.getByRole('heading',{name:/저장 실행 A/})).toBeVisible({timeout:30000});
  await expect(page.getByLabel('사건 위치')).toHaveValue('120');
  expect(page.url()).toBe(savedURL);
  // Change the source and save a new revision through the normal editor.
  await page.getByLabel('공급 FPS',{exact:true}).fill('60');
  await expect(page.getByRole('button',{name:'계산 실행',exact:true})).toBeDisabled();
  await expect(page.getByText('위 편집안과 다른 저장 결과입니다.',{exact:false})).toBeVisible();
  await page.getByRole('button',{name:'개정 저장',exact:true}).click();
  await page.getByRole('button',{name:'계산 실행',exact:true}).click();
  await expect(page.getByRole('heading',{name:/저장 실행 A · r2/})).toBeVisible({timeout:90000});
  await page.getByLabel('비교 B',{exact:true}).selectOption(first);
  await expect(page.getByRole('heading',{name:/A\/B 설정 차이/})).toBeVisible();
  await expect(page.getByRole('cell',{name:'fps',exact:true})).toBeVisible();
  const downloadEvent=page.waitForEvent('download');
  await page.getByRole('button',{name:'실행 근거 JSON',exact:true}).click();
  const download=await downloadEvent;expect(download.suggestedFilename()).toMatch(/\.json$/);
  await page.screenshot({path:'test-results/fpga-lab-desktop.png',fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:760,height:1000});
  await page.reload();
  await expect(page.getByRole('heading',{name:/A\/B 설정 차이/})).toBeVisible();
  await page.mouse.move(0,0);
  await page.screenshot({path:'test-results/fpga-lab-narrow.png',fullPage:true,animations:'disabled'});
  expect(errors).toEqual([]);
});

test('invalid packing and lost submission response recover without another execution',async({page,request})=>{
  await page.goto('/#hil-sim?project=default&mode=fpga');
  await page.getByLabel('설계 이름',{exact:true}).fill('Recovery '+Date.now());
  await page.getByLabel('가로 px',{exact:true}).fill('1919');
  await page.getByRole('button',{name:'개정 저장',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('aligned');
  await page.getByLabel('가로 px',{exact:true}).fill('32');
  await page.getByLabel('세로 px',{exact:true}).fill('16');
  await page.getByLabel('프레임 수',{exact:true}).fill('5');
  await page.getByRole('button',{name:'개정 저장',exact:true}).click();
  await expect(page.getByRole('button',{name:'계산 실행',exact:true})).toBeEnabled();
  const design=new URLSearchParams(page.url().split('?')[1]).get('design');
  await page.route('**/api/v1/experiments/*/executions',async route=>{
    if(route.request().method()==='POST') {await route.fetch();await route.abort('failed');}
    else await route.continue();
  },{times:1});
  await page.getByRole('button',{name:'계산 실행',exact:true}).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await page.reload();
  await expect(page.getByRole('button',{name:'계산 실행',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'계산 실행',exact:true}).click();
  await expect(page.getByRole('heading',{name:/저장 실행 A/})).toBeVisible();
  const list=await (await request.get('/api/v1/hil-sim/fpga-lab/executions?project_id=default')).json();
  expect(list.items.filter((r:{design_ref:{id:string}})=>r.design_ref.id===design)).toHaveLength(1);
});
