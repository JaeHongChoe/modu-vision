import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness=require('./fixtures/harness.cjs');
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
type Download=(action:()=>Promise<void>,format:string)=>Promise<string>;

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,download:Download,native:boolean,url?:string){
 const tool=path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/operator_runtime.py');
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[tool,workspace.root],{cwd:harness.REPO_ROOT,env:{...process.env,VISION_AI_STUDIO_USER_DATA_DIR:workspace.userData},encoding:'utf8',timeout:90_000}).trim());
 evidence.note('fixture',fixture);
 try{
  await api('/api/project/open',{project_dir:fixture.project.project_dir});
  if(url)await page.goto(url);else await page.reload();
  await page.getByRole('button',{name:'운영자 검사',exact:true}).click();
  const panel=page.getByRole('region',{name:'운영자 검사 작업 공간'});
  await panel.getByRole('button',{name:'검사 시작',exact:true}).click();
  await expect(panel).toContainText('승인 적용 기록과 실행 버전 일치',{timeout:60_000});
  await panel.getByText('부품·Lot·재검사 정보',{exact:true}).click();
  const sources=Object.keys(fixture.images);
  const submit=async(source:string,part:string,operator:string)=>{
   await panel.getByLabel('운영자 검사 이미지',{exact:true}).selectOption(source);
   await panel.getByLabel('부품·바코드 ID',{exact:true}).fill(part);
   await panel.getByLabel('제품 ID',{exact:true}).fill('Product-A');
   await panel.getByLabel('Lot ID',{exact:true}).fill('Lot-1');
   await panel.getByLabel('검사 작업자',{exact:true}).fill(operator);
   const response=page.waitForResponse(r=>r.url().endsWith('/operator/inspect')&&r.request().method()==='POST');
   await panel.getByRole('button',{name:'검사 입력',exact:true}).click();
   const result=await response;expect(result.status(),await result.text()).toBe(202);const {job_id}=await result.json();
   await expect.poll(async()=>{const q=await api('/api/product-delivery/operator/queue');return q.jobs.find((j:any)=>j.job_id===job_id)?.state;},{timeout:30_000}).toBe('completed');
   return job_id as string;
  };
  const first=await submit(sources[0],'PART-001','operator-a');
  const before=(await api('/api/product-delivery/operator/queue?part_id=PART-001')).jobs[0];
  await panel.getByText('입력 대기열·실패 재처리',{exact:true}).click();
  const queue=panel.getByRole('region',{name:'검사 입력 대기열'});
  await queue.getByRole('button',{name:'대기열 새로고침',exact:true}).click();
  const firstCard=queue.locator(`article[data-job-id="${first}"]`);
  await firstCard.getByRole('button',{name:'재검사 정보 가져오기',exact:true}).click();
  await expect(panel.getByLabel('이전 검사 연결',{exact:true})).toHaveValue(first);
  // Taking the identity never submits an image automatically.
  expect((await api('/api/product-delivery/operator/queue')).total).toBe(1);
  await panel.getByLabel('운영자 검사 이미지',{exact:true}).selectOption(sources[1]);
  await panel.getByLabel('검사 작업자',{exact:true}).fill('operator-b');
  const response=page.waitForResponse(r=>r.url().endsWith('/operator/inspect')&&r.request().method()==='POST');
  await panel.getByRole('button',{name:'검사 입력',exact:true}).click();
  const submitted=await response;expect(submitted.status(),await submitted.text()).toBe(202);const second=(await submitted.json()).job_id;
  await expect.poll(async()=>{const q=await api('/api/product-delivery/operator/queue');return q.jobs.find((j:any)=>j.job_id===second)?.state;},{timeout:30_000}).toBe('completed');
  const third=await submit(sources[2],'PART-002','operator-c');
  await queue.getByLabel('조회할 부품 ID',{exact:true}).fill('PART-001');
  await queue.getByRole('button',{name:'부품 이력 조회',exact:true}).click();
  await expect(queue.locator('article')).toHaveCount(2);
  const secondCard=queue.locator(`article[data-job-id="${second}"]`);
  await expect(secondCard).toContainText('재검사 원본 '+first);
  await secondCard.getByText('접수 버전·입력 해시',{exact:true}).click();
  await expect(secondCard).toContainText(fixture.active.release.manifest_sha256);
  await expect(secondCard).toContainText(fixture.images[sources[1]]);
  await secondCard.getByText('모델 원본 결과 JSON',{exact:true}).click();
  await expect(secondCard).toContainText('execution_steps');
  const recent=panel.getByRole('heading',{name:'최근 검사와 REVIEW',exact:true}).locator('..');
  await recent.locator('article').filter({hasText:path.basename(sources[0])}).getByRole('button',{name:'검수 열기',exact:true}).click();
  await panel.getByLabel('운영자 검수 판정',{exact:true}).selectOption('REVIEW');
  await panel.getByLabel('운영자 검토자',{exact:true}).fill('synthetic-trace-reviewer');
  await panel.getByLabel('운영자 검수 근거',{exact:true}).fill('Controlled traceability review separate from model output');
  await panel.getByRole('button',{name:'검수 기록 저장',exact:true}).click();
  await expect(panel).toContainText('운영자 검수 기록 저장됨');
  const reviewed=(await api('/api/product-delivery/operator')).results.find((j:any)=>j.job_id===first);
  expect(reviewed.model_verdict).toBe(before.model_verdict);expect(reviewed.operator_review.verdict).toBe('REVIEW');
  const rows=(await api('/api/product-delivery/operator/queue?part_id=PART-001')).jobs;
  expect(rows.find((j:any)=>j.job_id===first)).toEqual(before);
  const reinspection=rows.find((j:any)=>j.job_id===second);
  expect(reinspection.reinspection_of).toBe(first);expect(reinspection.input_operator).toBe('operator-b');
  expect(reinspection.runtime_binding.product_id).toBe('Product-A');expect(reinspection.runtime_binding.lot_id).toBe('Lot-1');
  await queue.scrollIntoViewIfNeeded();
  await evidence.screenshot(page,`${native?'native':'browser'}-part-reinspection-history`);
  const jsonPath=await download(()=>queue.getByRole('button',{name:'JSON 결과 내보내기',exact:true}).click(),'json');
  const exported=JSON.parse(fs.readFileSync(jsonPath,'utf8'));
  expect(exported.count).toBe(2);expect(exported.total).toBe(2);expect(exported.truncated).toBe(false);
  expect(exported.jobs.map((j:any)=>j.job_id).sort()).toEqual([first,second].sort());
  const csvPath=await download(()=>queue.getByRole('button',{name:'CSV 결과 내보내기',exact:true}).click(),'csv');
  expect(fs.readFileSync(csvPath,'utf8')).toContain(first);expect(fs.readFileSync(csvPath,'utf8')).toContain('reinspection_of');
  await page.reload();await page.getByRole('button',{name:'운영자 검사',exact:true}).click();
  await page.getByText('입력 대기열·실패 재처리',{exact:true}).click();
  const reopened=page.getByRole('region',{name:'검사 입력 대기열'});
  await reopened.getByLabel('조회할 부품 ID',{exact:true}).fill('PART-001');await reopened.getByRole('button',{name:'부품 이력 조회',exact:true}).click();
  await expect(reopened.locator('article')).toHaveCount(2);await expect(reopened).toContainText(first);
  await expect(page.getByRole('region',{name:'운영자 검사 작업 공간'})).toContainText('synthetic-trace-reviewer');
  await page.getByRole('button',{name:'검사 중지',exact:true}).click();
  await expect(reopened).toContainText('프로젝트 검사 서비스를 시작하면');
  evidence.note('traceability_closure',{fixture_project:fixture.project,first,second,third,before,reinspection,reviewed,jsonPath,csvPath,actual_cpu_execution:true,actual_ui_reload:true,model_quality_accepted:false,windows_excluded:true});
 }finally{
  const stopped=JSON.parse(execFileSync(harness.resolvePython(),[tool,'stop',fixture.project.project_dir],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}).trim());
  evidence.note('owned_service_cleanup',stopped);expect(stopped.status).toBe('stopped');
 }
}
test('part identity, reinspection lineage, separate review and filtered exports persist',async({page,renderer,workspace,evidence})=>{
 const api:Api=async(route,body,method)=>{const response=await page.request.fetch(renderer.origin+route,{method:method||(body?'POST':'GET'),data:body});expect(response.ok(),await response.text()).toBe(true);return response.json();};
 const download:Download=async(action,format)=>{const event=page.waitForEvent('download');await action();const item=await event,target=path.join(workspace.root,'part-results.'+format);await item.saveAs(target);expect(await item.failure()).toBeNull();return target;};
 await installDesktopHostShim(page,renderer.port);await exercise(page,workspace,evidence,api,download,false,renderer.url);
});
test('native part reinspection history and actual filtered downloads',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const status=await electronSession.waitForBackend(),page=electronSession.window;
 const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!r.ok)throw Error(`Owned trace fixture HTTP ${r.status}: ${await r.text()}`);return r.json();},{port:status.port,route,body,method});
 const download:Download=async(action,format)=>{const target=path.join(workspace.root,'part-results.'+format);await electronSession.app.evaluate(({session},file)=>{(globalThis as any).__traceDownload=null;session.defaultSession.once('will-download',(_event,item)=>{item.setSavePath(file);item.once('done',(_ev,state)=>{(globalThis as any).__traceDownload=state;});});},target);await action();await expect.poll(()=>electronSession.app.evaluate(()=> (globalThis as any).__traceDownload)).toBe('completed');return target;};
 await exercise(page,workspace,evidence,api,download,true);
});
