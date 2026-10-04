import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {expect,test} from './fixtures/test';
import {png} from './qa/appFlow';

test.use({actionTimeout:10_000});
for(const mode of ['local','remote','refused'] as const)test(`${mode} scheduling keeps prepared patch input and forwards a bounded runtime without launching training`,async({page,request,renderer,workspace,evidence})=>{
 const source=path.join(workspace.root,'patch-budget-source');fs.mkdirSync(source);const files:string[]=[];
 for(let i=0;i<3;i++){
  const file=path.join(source,`part-${i}.png`);fs.writeFileSync(file,png(32,3,(x,y)=>[x+i*5,y,120]));files.push(file);
  const label=file.replace(/\.png$/,'.json');fs.writeFileSync(label,JSON.stringify({imagePath:path.basename(file),imageWidth:32,imageHeight:32,shapes:[{label:'chip',shape_type:'rectangle',points:[[0,0],[8,8]]}]}));files.push(label);
 }
 const hashes=()=>Object.fromEntries(files.map(file=>[file,crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')]));const before=hashes();
 expect((await request.post(`${renderer.origin}/api/project/create`,{data:{name:`S209 patch scheduling ${mode}`,task:'classification'}})).ok()).toBe(true);
 expect((await request.put(`${renderer.origin}/api/project/update`,{data:{source_dataset_dir:source}})).ok()).toBe(true);
 const preparedReply=await request.post(`${renderer.origin}/api/patch-classification/prepare`,{data:{patch_size:16,stride:16}});expect(preparedReply.ok(),await preparedReply.text()).toBe(true);const prepared=await preparedReply.json();expect(prepared.patch_count).toBe(12);
 const project=await(await request.get(`${renderer.origin}/api/project/current`)).json();
 if(mode==='remote'){
  await page.route('**/api/compute/profiles',route=>route.fulfill({json:{profiles:[{id:'controlled-server',name:'Controlled server',gpu_selector:null}]}}));
  await page.route('**/api/compute/selection',route=>route.fulfill({json:{compute_profile_id:'controlled-server'}}));
 }
 const submissions:Array<{path:string;body:any}>=[];const job={job_id:'job_patch_budget_controlled',task:'patch_classification',status:'queued',dataset_path:prepared.dataset_path,source_dataset_path:source,output_dir:path.join(project.models_dir,'job_patch_budget_controlled')};
 for(const endpoint of ['/api/patch-classification/train','/api/compute/jobs'])await page.route(`**${endpoint}`,route=>{
  if(route.request().method()!=='POST')return route.continue();
  submissions.push({path:new URL(route.request().url()).pathname,body:route.request().postDataJSON()});
  return mode==='refused'?route.fulfill({status:503,json:{detail:'Controlled patch admission outage; no worker was launched'}}):route.fulfill({json:mode==='remote'?{...job,job_id:'exec_patch_budget_controlled',model_id:job.job_id,compute_profile_id:'controlled-server'}:job});
 });
 await page.route('**/api/training/status?**',route=>route.fulfill({json:job}));
 await page.route('**/api/compute/jobs/exec_patch_budget_controlled',route=>route.fulfill({json:{...job,job_id:'exec_patch_budget_controlled',model_id:job.job_id,compute_profile_id:'controlled-server'}}));
 await page.goto(renderer.url);await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(`S209 patch scheduling ${mode}`);
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(2).click();
 await page.getByRole('region',{name:'모델 학습 허브'}).getByRole('button',{name:/^패치 분류/}).click();
 const settings=page.locator('summary',{hasText:'학습 실행 예산·대기열'});await expect(settings).toBeVisible({timeout:3000});await settings.click();
 const minutes=page.getByLabel('학습 시간 제한 (분)',{exact:true});const priority=page.getByLabel('학습 대기열 우선순위',{exact:true});const queue=page.getByLabel('장치가 사용 중이면 대기열에 넣기',{exact:true});const start=page.getByRole('button',{name:'패치 분류 후보 학습',exact:true});
 await minutes.fill('-1');await expect(start).toBeDisabled();await expect(page.getByRole('alert').filter({hasText:'시간 제한'})).toBeVisible();expect(submissions).toEqual([]);
 await minutes.fill('1.5');
 if(mode==='remote'){await expect(priority).toBeDisabled();await expect(priority).toHaveValue('0');await expect(queue).toBeDisabled();await expect(queue).toBeChecked();}
 else{await queue.uncheck();await priority.fill('0');}
 await expect(start).toBeEnabled();await start.click();await expect.poll(()=>submissions.length).toBe(1);
 const submitted=submissions[0];expect(submitted.body.max_runtime_s).toBe(90);expect(submitted.body.queue).toBe(mode==='remote');expect(submitted.body.priority).toBe(0);
 if(mode==='remote'){
  expect(submitted.path).toBe('/api/compute/jobs');expect(submitted.body.family_dataset_path).toBe(prepared.dataset_path);expect(submitted.body.dataset_path).toBe(source);
  for(const key of ['queue','priority','max_runtime_s'])expect(submitted.body.config_overrides).not.toHaveProperty(key);
 }else{expect(submitted.path).toBe('/api/patch-classification/train');expect(submitted.body.dataset_path).toBe(prepared.dataset_path);}
 if(mode==='refused'){await expect(page.getByRole('alert').filter({hasText:'Controlled patch admission outage'})).toBeVisible();await expect(start).toBeEnabled();}
 else{await expect(page.getByRole('status',{name:'학습 작업 상태'})).toContainText('대기');await expect(start).toBeDisabled();}
 expect(hashes()).toEqual(before);await settings.scrollIntoViewIfNeeded();await evidence.screenshot(page,`s209-patch-scheduling-${mode}`);
 evidence.note('scope',{actual_renderer:true,actual_owned_backend:true,patch_preparation:'actual source-bound 12 patches',training_submission:'controlled API only',training:false,server:false,submissions,source_images:Object.entries(before).map(([path,sha256])=>({path,sha256,bytes:fs.statSync(path).size})),source_unchanged:true});
});
