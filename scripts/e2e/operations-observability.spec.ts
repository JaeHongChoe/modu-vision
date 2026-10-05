import path from 'node:path';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness=require('./fixtures/harness.cjs');
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const tool=path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/operator_runtime.py');
 const env={...process.env,VISION_AI_STUDIO_USER_DATA_DIR:workspace.userData};
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[tool,workspace.root],{cwd:harness.REPO_ROOT,env,encoding:'utf8',timeout:90_000}).trim());
 evidence.note('fixture',fixture);
 try{
  await api('/api/project/open',{project_dir:fixture.project.project_dir});
  const context=(await api('/api/context')).project_context;
  const generate=(mode:string)=>JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/observability_events.py'),workspace.root,JSON.stringify(context),mode],{cwd:harness.REPO_ROOT,env,encoding:'utf8',timeout:30_000}).trim());
  const training=generate('seed');
  if(url)await page.goto(url);else await page.reload();
  await page.getByRole('button',{name:'운영자 검사',exact:true}).click();
  const operator=page.getByRole('region',{name:'운영자 검사 작업 공간'});
  await operator.getByRole('button',{name:'검사 시작',exact:true}).click();
  await expect(operator).toContainText('승인 적용 기록과 실행 버전 일치',{timeout:60_000});
  await operator.getByLabel('운영자 검사 이미지',{exact:true}).selectOption(Object.keys(fixture.images)[0]);
  const pending=page.waitForResponse(r=>r.url().endsWith('/operator/inspect')&&r.request().method()==='POST');
  await operator.getByRole('button',{name:'검사 입력',exact:true}).click();const submitted=await pending;
  expect(submitted.status(),await submitted.text()).toBe(202);const inspection=(await submitted.json()).job_id;
  await expect.poll(async()=>{const rows=await api('/api/product-delivery/operator/queue');return rows.jobs.find((j:any)=>j.job_id===inspection)?.state;},{timeout:30_000}).toBe('completed');
  await operator.getByText('운영 상태·영속 로그·내부 알림',{exact:true}).click();
  const panel=operator.getByRole('region',{name:'운영 관측과 알림'});
  await expect(panel).toContainText('서비스 ready');await expect(panel).toContainText('외부 전송 꺼짐');
  await expect(panel.getByLabel('내부 실패 알림',{exact:true})).not.toBeChecked();
  await expect(panel.locator('article')).toHaveCount(50);
  await panel.getByRole('button',{name:'다음 로그 페이지',exact:true}).click();
  await expect(panel).toContainText('영속 로그 51');
  const before=await api('/api/product-delivery/operations/observability?limit=500');
  expect(before.notifications.total).toBe(0);expect(before.logs.total).toBeGreaterThan(65);
  expect(JSON.stringify(before)).not.toContain('private-e2e-log-fixture');
  expect(JSON.stringify(before)).not.toContain('/private/fixture');
  await panel.getByLabel('운영 로그 작업 ID',{exact:true}).fill(training.job_id);
  await panel.getByRole('button',{name:'작업 이력 조회',exact:true}).click();
  await expect(panel.locator('article')).toHaveCount(50);await expect(panel).toContainText(training.job_id);
  await panel.getByRole('button',{name:'다음 로그 페이지',exact:true}).click();await expect(panel.locator('article')).toHaveCount(15);
  await panel.getByLabel('내부 실패 알림',{exact:true}).check();
  await panel.getByLabel('운영 알림 작업자',{exact:true}).fill('synthetic-operations-reviewer');
  await panel.getByLabel('운영 알림 변경 사유',{exact:true}).fill('Controlled local notification verification');
  await panel.getByRole('button',{name:'알림 정책 저장',exact:true}).click();await expect(panel).toContainText('내부 알림 정책 저장됨');
  const failure=generate('failure');
  await panel.getByRole('button',{name:'운영 기록 새로고침',exact:true}).click();
  await expect(panel).toContainText('저장된 내부 알림 1건');
  const after=await api('/api/product-delivery/operations/observability?limit=500');
  const again=await api('/api/product-delivery/operations/observability?limit=500');
  expect(after.notifications.total).toBe(1);expect(again.notifications).toEqual(after.notifications);
  expect(after.notifications.items[0].job_id).toBe(failure.job_id);
  const completed=after.logs.items.find((row:any)=>row.job_id===inspection&&row.state==='completed');
  expect(completed.payload.execution_steps.length).toBeGreaterThan(0);expect(completed.trace_id).toBe(inspection);
  await panel.scrollIntoViewIfNeeded();await evidence.screenshot(page,`${native?'native':'browser'}-persistent-operations-logs`);
  await page.reload();await page.getByRole('button',{name:'운영자 검사',exact:true}).click();
  await page.getByText('운영 상태·영속 로그·내부 알림',{exact:true}).click();
  const reopened=page.getByRole('region',{name:'운영 관측과 알림'});
  await expect(reopened.getByLabel('내부 실패 알림',{exact:true})).toBeChecked();await expect(reopened).toContainText('저장된 내부 알림 1건');
  const persisted=await api('/api/product-delivery/operations/observability?limit=500');expect(persisted.logs).toEqual(after.logs);
  await page.getByRole('button',{name:'검사 중지',exact:true}).click();
  evidence.note('operations_closure',{fixture_project:fixture.project,inspection,training,failure,before,after,persisted,
   actual_cpu_inspection:true,controlled_training_callbacks:true,representative_training:false,actual_ui_reload:true,native,external_transmission:false,windows_excluded:true});
 }finally{
  const stopped=JSON.parse(execFileSync(harness.resolvePython(),[tool,'stop',fixture.project.project_dir],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}).trim());
  evidence.note('owned_service_cleanup',stopped);expect(stopped.status).toBe('stopped');
 }
}
test('persistent operations pages, scoped traces and opt-in failures',async({page,renderer,workspace,evidence})=>{
 const api:Api=async(route,body,method)=>{const r=await page.request.fetch(renderer.origin+route,{method:method||(body?'POST':'GET'),data:body});expect(r.ok(),await r.text()).toBe(true);return r.json();};
 await installDesktopHostShim(page,renderer.port);await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native persisted operations pages and opt-in failure history',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const status=await electronSession.waitForBackend(),page=electronSession.window;
 const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!r.ok)throw Error(`Owned operations fixture HTTP ${r.status}: ${await r.text()}`);return r.json();},{port:status.port,route,body,method});
 await exercise(page,workspace,evidence,api,true);
});
