import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness=require('./fixtures/harness.cjs');
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const tool=path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/operator_runtime.py');
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[tool,workspace.root],{cwd:harness.REPO_ROOT,env:{...process.env,VISION_AI_STUDIO_USER_DATA_DIR:workspace.userData},encoding:'utf8',timeout:90_000}).trim());
 evidence.note('fixture',fixture);
 try{
  await api('/api/project/open',{project_dir:fixture.project.project_dir});
  if(url)await page.goto(url);else await page.reload();
  await page.getByRole('button',{name:'운영자 검사',exact:true}).click();
  let panel=page.getByRole('region',{name:'운영자 검사 작업 공간',exact:true});
  await expect(panel).toContainText('중지됨');
  await panel.getByRole('button',{name:'검사 시작',exact:true}).click();
  await expect(panel).toContainText('승인 적용 기록과 실행 버전 일치',{timeout:60_000});
  await panel.getByText('적용 버전·해시',{exact:true}).click();
  await expect(panel).toContainText(fixture.active.release.manifest_sha256);
  const image=Object.keys(fixture.images).find(p=>p.includes('/OK/'))!;
  await expect(panel.getByLabel('운영자 검사 이미지',{exact:true}).locator('option',{hasText:'ok_00.png'})).toHaveCount(1);
  await panel.getByLabel('운영자 검사 이미지',{exact:true}).selectOption(image);
  const submitted=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/operator/inspect')&&r.request().method()==='POST');
  await panel.getByRole('button',{name:'검사 입력',exact:true}).click();const response=await submitted;expect(response.status(),await response.text()).toBe(202);const job=await response.json();
  await expect.poll(async()=>{const s=await api('/api/product-delivery/operator');return s.results.find((r:any)=>r.job_id===job.job_id)?.state;},{timeout:60_000}).toBe('completed');
  await panel.getByRole('button',{name:'상태 확인',exact:true}).click();
  await expect(panel).toContainText('모델 OK · 운영 OK');
  await panel.getByRole('button',{name:'검수 열기',exact:true}).click();
  await panel.getByLabel('운영자 검토자',{exact:true}).fill('synthetic-ui-reviewer');
  await panel.getByLabel('운영자 검수 근거',{exact:true}).fill('Controlled operator workflow review');
  await panel.getByLabel('운영자 검수 판정',{exact:true}).selectOption('REVIEW');
  await panel.getByRole('button',{name:'검수 기록 저장',exact:true}).click();
  await expect(panel).toContainText('모델 OK · 운영 REVIEW');
  await page.keyboard.press('F1');await expect(panel.getByRole('complementary',{name:'작업자 도움말'})).toBeVisible();
  await page.getByTitle('Toggle Language (KR / EN)').click();
  panel=page.getByRole('region',{name:'Operator inspection workspace',exact:true});
  await expect(panel.getByRole('complementary',{name:'Operator help'})).toContainText('Keyboard actions use the same permissions.');
  await expect(panel).toContainText('Model OK · Operator REVIEW');
  await expect(panel.getByLabel('Operator inspection image',{exact:true})).toHaveValue(image);
  await page.keyboard.press('Escape');await expect(panel.getByRole('complementary')).toHaveCount(0);
  await page.reload();
  await expect(page.getByTitle('Toggle Language (KR / EN)')).toContainText('EN');
  await page.getByRole('button',{name:'Operator inspection',exact:true}).click();
  panel=page.getByRole('region',{name:'Operator inspection workspace',exact:true});
  await expect(panel).toContainText('Model OK · Operator REVIEW');
  await expect(panel.getByLabel('Operator inspection image',{exact:true})).toHaveValue(image);
  await panel.getByText('Input connection settings',{exact:true}).click();
  await panel.getByLabel('Operator input mode',{exact:true}).selectOption('folder');
  await panel.getByLabel('Operator watched folder',{exact:true}).fill(workspace.root);
  await panel.getByRole('button',{name:'Validate and save input settings',exact:true}).click();
  await expect(panel.getByRole('alert')).toBeVisible();
  await panel.getByLabel('Operator watched folder',{exact:true}).fill(fixture.source);
  await panel.getByRole('button',{name:'Validate and save input settings',exact:true}).click();
  await expect(panel).toContainText('Input settings saved; explicitly stop and start the service to apply them');
  // Preserve a pending configuration; do not start a folder watcher that would
  // consume the same original inputs a second time.
  const inputs=await api('/api/product-delivery/operator');expect(inputs.input_health.configuration.mode).toBe('folder');
  await evidence.screenshot(page,`${native?'native':'browser'}-operator-review-language-settings`);
  await panel.getByRole('button',{name:'Stop inspection',exact:true}).click();
  await expect(panel).toContainText('Stopped');
  const stopped=await api('/api/product-delivery/operator');
  const row=stopped.results.find((r:any)=>r.job_id===job.job_id);expect(row.model_verdict).toBe('OK');expect(row.operator_review.verdict).toBe('REVIEW');expect(stopped.service.active.release.manifest_sha256).toBe(fixture.active.release.manifest_sha256);
  // Controlled display permissions exercise the actual DOM keyboard refusal.
  // Backend role refusal is separately qualified with real scoped API tests.
  let mutations=0;
  await page.route('**/api/product-delivery/operator',route=>route.fulfill({json:{...stopped,permissions:{can_control:false,can_inspect:false,can_review:false,can_configure:false}}}));
  page.on('request',r=>{if(r.method()!=='GET'&&new URL(r.url()).pathname.startsWith('/api/')&& !new URL(r.url()).pathname.includes('/team/'))mutations++;});
  await panel.getByRole('button',{name:'Refresh status',exact:true}).click();
  await expect(panel.getByRole('button',{name:'Validate and save input settings',exact:true})).toBeDisabled();
  await expect(panel.getByLabel('Operator input mode',{exact:true})).toBeDisabled();
  await panel.locator('h2').click();await page.keyboard.press('Tab');await page.keyboard.press('Enter');
  await expect(panel.getByRole('button',{name:'Start inspection',exact:true})).toBeDisabled();
  await expect(panel.getByRole('button',{name:'Open review',exact:true})).toBeDisabled();
  expect(mutations).toBe(0);
  await evidence.screenshot(page,`${native?'native':'browser'}-operator-permission-refusal`);
  evidence.note('operator_closure',{job,row,inputs:inputs.input_health.configuration,stopped,language_restart:'en',image,source_sha256:fixture.images[image],actual_cpu_service:true,actual_review_save_reopen:true,display_viewer_permissions_controlled:true,keyboard_mutations:mutations,windows_tests_excluded:true,model_quality_accepted:false});
 }finally{
  const stopped=JSON.parse(execFileSync(harness.resolvePython(),[tool,'stop',fixture.project.project_dir],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}).trim());evidence.note('owned_service_cleanup',stopped);expect(stopped.status).toBe('stopped');
 }
}
test('operator executes owned CPU service, keeps review and locale on restart and refuses keyboard edits',async({page,renderer,workspace,evidence})=>{
 const api:Api=async(route,body,method)=>{const response=await page.request.fetch(renderer.origin+route,{method:method||(body?'POST':'GET'),data:body});expect(response.ok(),await response.text()).toBe(true);return response.json();};
 await installDesktopHostShim(page,renderer.port);await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native operator CPU service review locale restart and keyboard permission refusal',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const status=await electronSession.waitForBackend();const page=electronSession.window;
 const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!r.ok)throw Error(`Owned operator fixture HTTP ${r.status}`);return r.json();},{port:status.port,route,body,method});
 await exercise(page,workspace,evidence,api,true);
});
