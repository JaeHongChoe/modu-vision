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
  await api('/api/product-delivery/operator/inputs',{mode:'folder',folder:fixture.source},'PUT');
  if(url)await page.goto(url);else await page.reload();
  await page.getByRole('button',{name:'운영자 검사',exact:true}).click();
  const panel=page.getByRole('region',{name:'운영자 검사 작업 공간'});
  await panel.getByRole('button',{name:'검사 시작',exact:true}).click();
  await expect(panel).toContainText('승인 적용 기록과 실행 버전 일치',{timeout:60_000});
  await expect.poll(async()=>{const q=await api('/api/product-delivery/operator/queue');return q.jobs.filter((j:any)=>j.state==='completed').length;},{timeout:60_000}).toBe(16);
  // New arrivals only in this test's owned inbox; do not modify original bytes.
  const bad=path.join(fixture.source,'controlled-corrupt-input.png');fs.writeFileSync(bad,'controlled invalid png');
  const original=Object.keys(fixture.images)[0];
  for(let index=0;index<40;index++)fs.copyFileSync(original,path.join(fixture.source,`controlled-arrival-${index}.png`));
  await expect.poll(async()=>{const q=await api('/api/product-delivery/operator/queue?limit=100');return q.total===57&&q.outstanding===0;},{timeout:60_000}).toBe(true);
  await panel.getByText('입력 대기열·실패 재처리',{exact:true}).click();
  const queue=panel.getByRole('region',{name:'검사 입력 대기열'});
  await expect(queue).toContainText('/57');
  await queue.getByRole('button',{name:'다음 페이지',exact:true}).click();await expect(queue).toContainText('51–57/57');
  await queue.getByRole('button',{name:'이전 페이지',exact:true}).click();
  await queue.getByLabel('대기열 상태 필터',{exact:true}).selectOption('error');
  await expect(queue.locator('article')).toHaveCount(1);
  const failed=(await api('/api/product-delivery/operator/queue?state=error')).jobs[0];
  expect(failed.dead_letter_reason).toBe('CORRUPT_INPUT');expect(failed.attempts).toBe(0);
  await queue.getByRole('button',{name:'동일 검사 재시도',exact:true}).click();
  await expect.poll(async()=>{const q=await api('/api/product-delivery/operator/queue?state=error');return q.jobs.find((j:any)=>j.job_id===failed.job_id)?.retry_count;},{timeout:30_000}).toBe(1);
  await queue.getByRole('button',{name:'대기열 새로고침',exact:true}).click();
  await queue.getByRole('button',{name:'새 검사로 재처리',exact:true}).click();
  await expect(queue.getByRole('button',{name:'사유를 기록하고 재처리',exact:true})).toBeDisabled();
  await queue.getByLabel('재처리 작업자',{exact:true}).fill('synthetic-queue-reviewer');
  await queue.getByLabel('재처리 사유',{exact:true}).fill('Controlled explicit replay preserves the admitted recipe');
  const response=page.waitForResponse(r=>r.url().endsWith(`/${failed.job_id}/replay`)&&r.request().method()==='POST');
  await queue.getByRole('button',{name:'사유를 기록하고 재처리',exact:true}).click();const replayResponse=await response;expect(replayResponse.status()).toBe(202);const replay=await replayResponse.json();
  await expect.poll(async()=>{const q=await api('/api/product-delivery/operator/queue?state=error');return q.jobs.some((j:any)=>j.job_id===replay.job_id);},{timeout:30_000}).toBe(true);
  await queue.getByRole('button',{name:'대기열 새로고침',exact:true}).click();
  const replayCard=queue.locator(`article[data-job-id="${replay.job_id}"]`);
  await replayCard.getByText('접수 버전·입력 해시',{exact:true}).click();await expect(replayCard).toContainText(failed.image_sha256);
  await expect(replayCard).toContainText(failed.runtime_binding_sha256);
  await replayCard.getByRole('button',{name:'처리 이력',exact:true}).click();
  await expect(queue.locator('fieldset pre')).toContainText('replay_of');
  await queue.locator('fieldset').scrollIntoViewIfNeeded();
  await evidence.screenshot(page,`${native?'native':'browser'}-input-replay-lineage`);
  const jsonPath=await download(()=>queue.getByRole('button',{name:'JSON 결과 내보내기',exact:true}).click(),'json');
  const exported=JSON.parse(fs.readFileSync(jsonPath,'utf8'));expect(exported.total).toBe(58);expect(exported.count).toBe(58);expect(exported.truncated).toBe(false);
  const replayRow=exported.jobs.find((j:any)=>j.job_id===replay.job_id);expect(replayRow.replay_of).toBe(failed.job_id);expect(replayRow.runtime_binding).toEqual(failed.runtime_binding);expect(replayRow.image_sha256).toBe(failed.image_sha256);
  const csvPath=await download(()=>queue.getByRole('button',{name:'CSV 결과 내보내기',exact:true}).click(),'csv');expect(fs.readFileSync(csvPath,'utf8')).toContain(replay.job_id);
  await queue.getByLabel('대기열 상태 필터',{exact:true}).selectOption('');await queue.getByRole('button',{name:'대기열 새로고침',exact:true}).click();
  await expect(queue).toContainText('/58');
  await page.reload();await page.getByRole('button',{name:'운영자 검사',exact:true}).click();await page.getByText('입력 대기열·실패 재처리',{exact:true}).click();
  await expect(page.getByRole('region',{name:'검사 입력 대기열'})).toContainText('/58');
  await page.getByRole('button',{name:'검사 중지',exact:true}).click();
  await expect(page.getByRole('region',{name:'검사 입력 대기열'})).toContainText('프로젝트 검사 서비스를 시작하면');
  evidence.note('queue_closure',{fixture_project:fixture.project,failed,replay,replayRow,jsonPath,csvPath,actual_folder_arrivals:57,actual_cpu_execution:true,exact_original_hashes:fixture.images,actual_ui_reload:true,synthetic_review_only:true,model_quality_accepted:false,windows_excluded:true});
 }finally{
  const stopped=JSON.parse(execFileSync(harness.resolvePython(),[tool,'stop',fixture.project.project_dir],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}).trim());evidence.note('owned_service_cleanup',stopped);expect(stopped.status).toBe('stopped');
 }
}
test('folder inputs persist a paged queue, retry and explicit replay lineage with exports',async({page,renderer,workspace,evidence})=>{
 const api:Api=async(route,body,method)=>{const response=await page.request.fetch(renderer.origin+route,{method:method||(body?'POST':'GET'),data:body});expect(response.ok(),await response.text()).toBe(true);return response.json();};
 const download:Download=async(action,format)=>{const event=page.waitForEvent('download');await action();const item=await event,target=path.join(workspace.root,'queue-results.'+format);await item.saveAs(target);expect(await item.failure()).toBeNull();return target;};
 await installDesktopHostShim(page,renderer.port);await exercise(page,workspace,evidence,api,download,false,renderer.url);
});
test('native folder queue retry replay and persisted exports',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const status=await electronSession.waitForBackend();const page=electronSession.window;
 const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!r.ok)throw Error(`Owned queue fixture HTTP ${r.status}: ${await r.text()}`);return r.json();},{port:status.port,route,body,method});
 const download:Download=async(action,format)=>{const target=path.join(workspace.root,'queue-results.'+format);await electronSession.app.evaluate(({session},file)=>{(globalThis as any).__queueDownload=null;session.defaultSession.once('will-download',(_event,item)=>{item.setSavePath(file);item.once('done',(_ev,state)=>{(globalThis as any).__queueDownload=state;});});},target);await action();await expect.poll(()=>electronSession.app.evaluate(()=> (globalThis as any).__queueDownload)).toBe('completed');return target;};
 await exercise(page,workspace,evidence,api,download,true);
});
