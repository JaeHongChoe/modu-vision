import fs from 'node:fs';import path from 'node:path';import crypto from 'node:crypto';import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {openTaskCenter} from './qa/appFlow';
const harness=require('./fixtures/harness.cjs');
test.use({actionTimeout:10_000});
const labels:Record<string,string>={classification:'이미지 분류',segmentation:'영역 분할',detection:'객체 검출',anomaly:'이상탐지',patch_classification:'패치 분류',ocr:'문자 인식',rotated_detection:'회전 객체 검출',rotation:'정방향 보정',defect_gan:'결함 이미지 생성',enhancement:'이미지 개선'};
type Api=(route:string,body?:any)=>Promise<any>;
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,url?:string){
 const project=await api('/api/project/create',{name:'Ten family restart qualification',task:'classification'});
 const updated=await api('/api/project/update',{source_dataset_dir:workspace.dataset});
 const current=await api('/api/project/current');const context=await api('/api/context');
 const before=Object.fromEntries(workspace.images.map(row=>[row.path,crypto.createHash('sha256').update(fs.readFileSync(row.path)).digest('hex')]));
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/training_center_records.py'),workspace.root,JSON.stringify({...current,project_context:context.project_context})],{cwd:harness.REPO_ROOT,env:{...process.env,VISION_AI_STUDIO_USER_DATA_DIR:workspace.userData},encoding:'utf8',timeout:30_000}).trim());
 const submissions:string[]=[];page.on('request',r=>{if(r.method()==='POST'&&(/\/train$/.test(new URL(r.url()).pathname)||['/api/training/start','/api/compute/jobs'].includes(new URL(r.url()).pathname)))submissions.push(new URL(r.url()).pathname);});
 if(url)await page.goto(url);else await page.reload();
 await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(current.name);
 const qualifications:any[]=[];
 for(const row of fixture.records){
  const center=await openTaskCenter(page);
  const selected=center.getByLabel('저장 작업 다시 열기');await expect(selected.locator(`option[value="${row.kind}:local:${row.job_id}"]`)).toHaveCount(1,{timeout:15_000});
  await selected.selectOption(`${row.kind}:local:${row.job_id}`);
  const status=center.getByRole('status').filter({hasText:'실행 주체 없음 · 재개 확인 필요'});await expect(status).toBeVisible();await expect(status).toContainText(/다시 (학습|실행)하세요/);
  await expect(status.getByRole('button',{name:'취소 요청',exact:true})).toHaveCount(0);await expect(status.getByRole('button',{name:/재연결/})).toHaveCount(0);
  await status.getByRole('button',{name:'학습 화면으로 이동',exact:true}).click();await expect(center).not.toBeVisible();
  if(row.family==='defect_gan')await page.locator('summary').filter({hasText:'결함 이미지 생성 실험'}).click();
  const preparation=page.getByRole('region',{name:'공통 모델 준비'});await expect(preparation).toBeVisible();
  await expect(preparation.getByRole('heading')).toContainText(labels[row.family]);const actualTitle=await preparation.getByRole('heading').textContent();
  await expect(preparation).toContainText('1. 프로젝트 이미지');await expect(preparation).toContainText('2. 정답·시험 분리');await expect(preparation).toContainText('3. 모델·실행 환경');
  await preparation.getByRole('button',{name:'로컬 준비 검사',exact:true}).click();await expect(preparation.getByRole('status')).toContainText('실제 학습·추론 실행과 모델 품질 승인은 아직 확인하지 않았습니다.',{timeout:15_000});
  const summary=page.locator('summary').filter({hasText:/^학습 (실행 예산|시간 제한)/});await expect(summary).toHaveCount(1);if(!(await summary.locator('..').getAttribute('open')))await summary.click();
  const minutes=page.getByLabel('학습 시간 제한 (분)',{exact:true});await minutes.fill('1.5');await expect(minutes).toHaveValue('1.5');
  const priority=page.getByLabel('학습 대기열 우선순위',{exact:true});await priority.fill('3');await expect(priority).toHaveValue('3');
  const queue=page.getByLabel('장치가 사용 중이면 대기열에 넣기',{exact:true});await queue.uncheck();await expect(queue).not.toBeChecked();
  const workbench=page.getByRole('status',{name:'학습 작업 상태',exact:true});await expect(workbench).toContainText('실행 주체 없음 · 재개 확인 필요');await expect(workbench).toContainText(/다시 (학습|실행)하세요/);
  await expect(workbench.getByRole('button',{name:'취소 요청',exact:true})).toHaveCount(0);
  await workbench.scrollIntoViewIfNeeded();await evidence.screenshot(page,`training-center-${row.family}`);
  qualifications.push({family:row.family,job_id:row.job_id,preparation_title:actualTitle,shared_runtime_minutes:1.5,priority:3,queue:false,record_only:true});
 }
 await page.reload();const reopened=await openTaskCenter(page);await expect(reopened.getByLabel('저장 작업 다시 열기').locator('option')).toHaveCount(11,{timeout:15_000});
 const records=await api('/api/training-workspace/tasks');expect(records.tasks.filter((r:any)=>fixture.records.some((f:any)=>f.job_id===r.job_id))).toHaveLength(10);
 expect(submissions).toEqual([]);for(const [file,sha]of Object.entries(before))expect(crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')).toBe(sha);
 evidence.note('ten_family_qualification',{qualifications,actual_renderer:true,actual_backend:true,actual_training:false,completed_models:false,recorded_execution:false,submissions,source_unchanged:true,fixture,records});
}
test('all ten families reopen the same persisted interruption and use preparation and budget controls',async({page,request,renderer,workspace,evidence})=>{test.setTimeout(180_000);const api:Api=async(route,body)=>{const r=body===undefined?await request.get(renderer.origin+route):route.endsWith('/update')?await request.put(renderer.origin+route,{data:body}):await request.post(renderer.origin+route,{data:body});expect(r.ok(),await r.text()).toBe(true);return r.json();};await exercise(page,workspace,evidence,api,renderer.url);});
test('native all ten family persisted interruptions survive task-center navigation and reload',{tag:'@electron'},async({electronSession,workspace,evidence})=>{test.setTimeout(180_000);const {window}=electronSession;const backend=await electronSession.waitForBackend();const api:Api=(route,body)=>window.evaluate(async({port,route,body})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{...(body===undefined?{}:{method:route.endsWith('/update')?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!response.ok)throw new Error(`Owned API ${response.status}: ${await response.text()}`);return response.json();},{port:backend.port,route,body});await exercise(window,workspace,evidence,api);});
