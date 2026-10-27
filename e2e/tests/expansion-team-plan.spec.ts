import {test,expect} from '@playwright/test';

test('legacy workspace still opens when an older server has no team API',async({page})=>{
  await page.route('**/api/v1/team/whoami',route=>route.fulfill({status:404,contentType:'application/json',body:JSON.stringify({detail:'Not Found'})}));
  await page.goto('/#scenarios');
  await expect(page.getByRole('heading',{name:'Scenario library',exact:true})).toBeVisible();
  await expect(page.getByLabel('Workspace settings',{exact:true})).toBeVisible();
});

test('engineer authors and runs; reviewer assesses; viewer reads the same project evidence',async({browser,request})=>{
  const project=await (await request.post('/api/v1/projects',{data:{name:'Browser role-separated verification'}})).json();
  const tokens:Record<string,string>={};
  for(const role of ['Engineer','Reviewer','Viewer']){
    const r=await request.post(`/api/v1/projects/${project.id}/tokens`,{data:{user_label:`Browser ${role}`,role}});
    expect(r.status()).toBe(201);tokens[role]=(await r.json()).token;
  }
  async function session(role:string){
    const context=await browser.newContext({baseURL:'http://127.0.0.1:5174'});await context.addInitScript(token=>sessionStorage.setItem('axiom.apiToken',token),tokens[role]);
    return {context,page:await context.newPage()};
  }
  const engineer=await session('Engineer');
  await engineer.page.goto('/#replay-specs');
  await expect(engineer.page.getByLabel('Replay project')).toHaveValue(project.id);
  await expect(engineer.page.getByRole('button',{name:'Freeze sequence',exact:true})).toBeEnabled();
  await engineer.page.getByRole('button',{name:'Freeze sequence',exact:true}).click();
  await engineer.page.getByRole('button',{name:'Save replay specification',exact:true}).click();
  await engineer.page.getByRole('button',{name:'Check retained inputs',exact:true}).click();
  await engineer.page.getByRole('button',{name:'Open planning experiment',exact:true}).click();
  await engineer.page.getByLabel('Experiment name').fill('Role-separated planning evidence');
  await engineer.page.getByRole('button',{name:'Preview workload',exact:true}).click();
  await engineer.page.getByRole('button',{name:'Save frozen experiment',exact:true}).click();
  const submission=engineer.page.waitForResponse(r=>/\/experiments\/[^/]+\/executions$/.test(r.url())&&r.request().method()==='POST');
  await engineer.page.getByRole('button',{name:'Submit execution',exact:true}).click();const execution=await(await submission).json();
  await expect(engineer.page.getByRole('img',{name:'Replay trajectory and object observations'})).toBeVisible();
  await engineer.page.goto('/#validation?experiment-plan=new');
  await expect(engineer.page.getByRole('heading',{name:'Experiment verification',exact:true})).toBeVisible();
  await engineer.page.getByLabel('Experiment plan name').fill('Planning speed bounded on reference vehicle');
  await engineer.page.getByLabel('Experiment plan purpose').fill('Assess maximum ego speed on the authored reference; no safety certification.');
  await engineer.page.getByLabel('Experiment plan evidence type').selectOption('planning_replay');
  const creation=engineer.page.waitForResponse(r=>r.url().endsWith('/experiment-verification-plans')&&r.request().method()==='POST');
  await engineer.page.getByRole('button',{name:'Save verification plan',exact:true}).click();const plan=await(await creation).json();
  await engineer.page.getByRole('button',{name:'Select visible executions',exact:true}).click();
  await expect(engineer.page.getByRole('button',{name:'Evaluate selected evidence',exact:true})).toBeDisabled();
  const reviewer=await session('Reviewer');await reviewer.page.goto('/#validation?experiment-plan=review');
  await reviewer.page.getByRole('button',{name:'Planning speed bounded on reference vehicle'}).click();
  await reviewer.page.getByRole('button',{name:'Select visible executions',exact:true}).click();
  await expect(reviewer.page.getByRole('button',{name:'Evaluate selected evidence',exact:true})).toBeEnabled();
  const assessment=reviewer.page.waitForResponse(r=>r.url().endsWith(`/experiment-verification-plans/${plan.id}/evaluations`)&&r.request().method()==='POST');
  await reviewer.page.getByRole('button',{name:'Evaluate selected evidence',exact:true}).click();const evaluated=await(await assessment).json();
  expect(evaluated.execution_denominator).toBe(1);expect(evaluated.entries[0].execution_id).toBe(execution.id);expect(evaluated.verdict).toBe('passed');
  await expect(reviewer.page.getByRole('button',{name:'Save new plan revision',exact:true})).toBeDisabled();
  const viewer=await session('Viewer');await viewer.page.goto('/#validation?experiment-plan=read');
  await viewer.page.getByRole('button',{name:'Planning speed bounded on reference vehicle'}).click();
  await expect(viewer.page.getByRole('button',{name:'Evaluate selected evidence',exact:true})).toBeDisabled();
  const download=viewer.page.waitForEvent('download');await viewer.page.getByRole('button',{name:'Download assessment receipt',exact:true}).click();
  expect((await download).suggestedFilename()).toBe(`${evaluated.id}.json`);
  const forbidden=await viewer.page.request.get('/api/v1/experiments?project_id=default',{headers:{Authorization:`Bearer ${tokens.Viewer}`}});expect(forbidden.status()).toBe(404);
  await viewer.page.screenshot({path:test.info().outputPath('team-verification.png'),fullPage:true});
  await engineer.context.close();await reviewer.context.close();await viewer.context.close();
});
