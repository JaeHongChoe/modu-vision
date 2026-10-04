import fs from 'node:fs';
import crypto from 'node:crypto';
import path from 'node:path';
import {expect,test,type Workspace,type RendererServer} from './fixtures/test';
import type {Page} from '@playwright/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {createProject,pickDataset,segmentationDataset,stage,openTaskCenter} from './qa/appFlow';

test.use({actionTimeout:10_000});
async function prepare(page:Page,renderer:RendererServer,workspace:Workspace){
 const dataset=segmentationDataset(path.join(workspace.root,'budget-source'),{train:40,val:8},{cleanEvery:3});
 await installDesktopHostShim(page,renderer.port);
 await page.addInitScript(folder=>{(window as unknown as {api:{selectFolder:()=>Promise<string>}}).api.selectFolder=async()=>folder;},dataset);
 await page.goto(renderer.url);await createProject(page,'S209 CPU runtime budget','영역 분할');await pickDataset(page);
 await expect(page.getByText(/Train:\s*40/)).toBeVisible();await stage(page,'03','오토딥러닝').click();
 await page.getByLabel('학습 모델 구조').selectOption({label:'UNet · 기존 구조'});
 await page.getByText('다음 학습 배치·로컬 장치 설정',{exact:true}).click();await page.getByLabel('다음 학습 로컬 장치',{exact:true}).selectOption('cpu');
 await page.locator('summary',{hasText:'학습 실행 예산·대기열'}).click();return dataset;
}

test('invalid runtime budget blocks the real core start action without submitting a job',async({page,renderer,workspace,evidence})=>{
 await prepare(page,renderer,workspace);let submissions=0;page.on('request',request=>{if(new URL(request.url()).pathname==='/api/training/start'&&request.method()==='POST')submissions++;});
 const minutes=page.getByLabel('학습 시간 제한 (분)',{exact:true});await expect(minutes).toBeVisible({timeout:2000});await minutes.fill('-1');
 await expect(page.getByRole('alert').filter({hasText:'시간 제한'})).toBeVisible();await expect(page.getByRole('button',{name:'선택 설정으로 학습 시작',exact:true})).toBeDisabled();expect(submissions).toBe(0);
 await minutes.fill('1.5');await page.getByLabel('장치가 사용 중이면 대기열에 넣기',{exact:true}).uncheck();await page.getByLabel('학습 대기열 우선순위',{exact:true}).fill('-10');
 await expect(page.getByRole('button',{name:'선택 설정으로 학습 시작',exact:true})).toBeEnabled();expect(submissions).toBe(0);
 await evidence.screenshot(page,'s209-budget-ready');evidence.note('scheduling_validation',{actual_renderer:true,actual_backend:true,submissions,training:false});
});

test('the app submits runtime seconds and the owned CPU job reaches budget cancellation with its reservation returned',async({page,renderer,workspace,evidence})=>{
 test.setTimeout(240_000);const source=await prepare(page,renderer,workspace);
 const image=path.join(source,'images','train','part_train_0.png');const digest=()=>crypto.createHash('sha256').update(fs.readFileSync(image)).digest('hex');const before=digest();
 const minutes=page.getByLabel('학습 시간 제한 (분)',{exact:true});await expect(minutes).toBeVisible({timeout:2000});await minutes.fill('0.1');
 await page.getByLabel('학습 대기열 우선순위',{exact:true}).fill('2');
 const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/training/start'&&r.request().method()==='POST');await page.getByRole('button',{name:'선택 설정으로 학습 시작',exact:true}).click();
 const started=await response;expect(started.ok(),await started.text()).toBe(true);const submitted=started.request().postDataJSON();expect(submitted.max_runtime_s).toBe(6);expect(submitted.queue).toBe(true);expect(submitted.priority).toBe(2);
 const job=await started.json();let observed:any=null;
 await expect.poll(async()=>{const rows=await(await page.request.get(`${renderer.origin}/api/training/jobs`)).json();observed=rows.jobs.find((row:any)=>row.job_id===job.job_id);return observed?.status;},{timeout:150_000,intervals:[1000,2000]}).toBe('aborted');
 expect(observed.observation.cause).toBe('time_limit');expect(digest()).toBe(before);
 const center=await openTaskCenter(page);const row=center.getByRole('status').filter({hasText:job.job_id});await expect(row).toContainText('예약 반환',{timeout:15_000});
 await expect(page.getByRole('button',{name:'선택 설정으로 학습 시작',exact:true})).toBeEnabled();
 await evidence.screenshot(page,'s209-budget-cancelled');evidence.note('runtime_budget',{submitted,job_id:job.job_id,observation:observed.observation,actual_cpu_training:true,server:false,manual_stop:false,source_unchanged:true});
});
