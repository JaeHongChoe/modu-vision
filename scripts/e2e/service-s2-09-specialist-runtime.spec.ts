import {test,expect} from './fixtures/test';
import {png} from './qa/appFlow';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import fs from 'node:fs';import path from 'node:path';import crypto from 'node:crypto';
test.use({actionTimeout:10_000});
const families=[
 {family:'rotation',card:'정방향 보정',button:'정방향 모델 후보 학습'},
 {family:'ocr',card:'문자 인식',button:'OCR 후보 학습'},
 {family:'defect-gan',card:'결함 이미지 생성',button:'생성 모델 학습'},
 {family:'enhancement',card:'이미지 개선',button:'이미지 개선 후보 학습'},
 {family:'rotated-detection',card:'회전 객체 검출',button:'후보 학습'},
];
for(const entry of families)for(const mode of ['local','remote'] as const)test(`${entry.family} ${mode} runtime is validated and sent with its prepared input`,async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);
 const source=path.join(workspace.root,'runtime-source');fs.mkdirSync(source);const rows:any[]=[];const files:string[]=[];
 for(let i=0;i<4;i++){
  const image=`part-${i}.png`,file=path.join(source,image);fs.writeFileSync(file,png(32,3,(x,y)=>[x+i*10,y,120]));files.push(file);
  const row:any={image,split:['train','train','val','test'][i]};
  if(entry.family==='rotation')row.correction_deg=i*30;
  else if(entry.family==='ocr')row.text='A';
  else if(entry.family==='defect-gan')row.bbox=[0,0,32,32];
  else if(entry.family==='rotated-detection'){row.label='part';row.box={cx:16,cy:16,width:12,height:8,angle_deg:0};}
  rows.push(row);
 }
 const hashes=()=>Object.fromEntries(files.map(file=>[file,crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')]));const before=hashes();
 expect((await request.post(`${renderer.origin}/api/project/create`,{data:{name:`Runtime ${entry.family} ${mode}`,task:'classification'}})).ok()).toBe(true);
 expect((await request.put(`${renderer.origin}/api/project/update`,{data:{source_dataset_dir:source}})).ok()).toBe(true);
 const reply=await request.post(`${renderer.origin}/api/${entry.family}/prepare`,{data:{source_dataset_path:source,...(entry.family==='enhancement'?{}:{samples:rows})}});
 expect(reply.ok(),await reply.text()).toBe(true);const prepared=await reply.json();
 if(mode==='remote'){
  await page.route('**/api/compute/profiles',route=>route.fulfill({json:{profiles:[{id:'controlled-runtime-server',name:'Controlled server',gpu_selector:null}]}}));
  await page.route('**/api/compute/selection',route=>route.fulfill({json:{compute_profile_id:'controlled-runtime-server'}}));
 }
 const submissions:Array<{path:string;body:any}>=[];
 for(const endpoint of [`/api/${entry.family}/train`,'/api/compute/jobs'])await page.route(`**${endpoint}`,route=>{
  if(route.request().method()!=='POST')return route.continue();submissions.push({path:new URL(route.request().url()).pathname,body:route.request().postDataJSON()});
  return route.fulfill({status:503,json:{detail:'Controlled runtime admission refusal; no worker launched'}});
 });
 await page.goto(renderer.url);await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(`Runtime ${entry.family}`);
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(2).click();
 await page.getByRole('region',{name:'모델 학습 허브'}).getByRole('button',{name:new RegExp(`^${entry.card}`)}).click();
 if(entry.family==='defect-gan')await page.locator('summary',{hasText:'결함 이미지 생성 실험'}).click();
 const settings=page.locator('summary',{hasText:'학습 시간 제한'});await expect(settings).toBeVisible({timeout:3000});await settings.click();
 const minutes=page.getByLabel('학습 시간 제한 (분)',{exact:true});const start=page.getByRole('button',{name:entry.button,exact:true});
 await minutes.fill('-1');await expect(start).toBeDisabled();expect(submissions).toEqual([]);
 await page.getByLabel('학습 대기열 우선순위',{exact:true}).fill('8');
 await page.getByLabel('장치가 사용 중이면 대기열에 넣기',{exact:true}).uncheck();
 await minutes.fill('1.5');await expect(start).toBeEnabled();await start.click();await expect.poll(()=>submissions.length).toBe(1);
 const body=submissions[0].body;expect(body.max_runtime_s).toBe(90);
 expect(submissions[0].path).toBe(mode==='remote'?'/api/compute/jobs':`/api/${entry.family}/train`);
 if(mode==='remote'){expect(body.family_dataset_path).toBe(prepared.dataset_path);expect(body.dataset_path).toBe(source);expect(body.config_overrides).not.toHaveProperty('max_runtime_s');}
 else expect(body.dataset_path).toBe(prepared.dataset_path);
 expect(body.queue).toBe(false);expect(body.priority).toBe(8);
 if(mode==='remote'){expect(body.config_overrides).not.toHaveProperty('queue');expect(body.config_overrides).not.toHaveProperty('priority');}
 await expect(page.getByRole('alert').filter({hasText:'Controlled runtime admission refusal'})).toBeVisible();await expect(start).toBeEnabled();
 expect(hashes()).toEqual(before);await settings.scrollIntoViewIfNeeded();await evidence.screenshot(page,`runtime-${entry.family}-${mode}`);
 evidence.note('scope',{actual_renderer:true,actual_owned_backend:true,actual_preparation:true,training:'controlled refusal only',server:false,submissions,source_unchanged:true,source_images:Object.entries(before).map(([path,sha256])=>({path,sha256}))});
});

test('real owned CPU rotation training reaches its runtime limit from the app without manual cancellation',async({page,request,renderer,workspace,evidence})=>{
 const source=path.join(workspace.root,'actual-rotation-source');fs.mkdirSync(source);const rows:any[]=[];const originals:Record<string,string>={};
 for(let i=0;i<4;i++){const image=`part-${i}.png`,file=path.join(source,image);fs.writeFileSync(file,png(64,3,(x,y)=>[x+i*5,y,120]));originals[file]=crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');rows.push({image,split:['train','train','val','test'][i],correction_deg:i*30});}
 await request.post(`${renderer.origin}/api/project/create`,{data:{name:'Actual specialist runtime',task:'classification'}});
 await request.put(`${renderer.origin}/api/project/update`,{data:{source_dataset_dir:source}});
 const prepared=await request.post(`${renderer.origin}/api/rotation/prepare`,{data:{source_dataset_path:source,samples:rows}});expect(prepared.ok(),await prepared.text()).toBe(true);
 let jobId='',posted:any;page.on('response',async response=>{if(new URL(response.url()).pathname==='/api/rotation/train'&&response.request().method()==='POST'){posted=response.request().postDataJSON();jobId=(await response.json()).job_id;}});
 await page.goto(renderer.url);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(2).click();await page.getByRole('region',{name:'모델 학습 허브'}).getByRole('button',{name:/^정방향 보정/}).click();
 await page.getByLabel('Epoch',{exact:true}).fill('500');await page.getByLabel('배치 크기',{exact:true}).fill('1');await page.getByLabel('모델 입력 크기',{exact:true}).fill('128');await page.getByLabel(/CNN 채널 폭/).selectOption('32');
 await page.locator('summary',{hasText:'학습 시간 제한'}).click();await page.getByLabel('학습 시간 제한 (분)',{exact:true}).fill('0.02');
 await page.getByRole('button',{name:'정방향 모델 후보 학습',exact:true}).click();await expect.poll(()=>jobId).not.toBe('');
 await expect(page.getByRole('status',{name:'학습 작업 상태'})).toContainText('Training runtime limit exceeded',{timeout:15000});
 await expect(page.getByRole('button',{name:'정방향 모델 후보 학습',exact:true})).toBeEnabled();
 const row=await(await request.get(`${renderer.origin}/api/rotation/jobs/${jobId}`)).json();expect(row.status).toBe('stopped');expect(row.stop_reason).toBe('time_limit');expect(row.batch).toBeGreaterThan(0);expect(posted.max_runtime_s).toBe(1.2);
 const project=await(await request.get(`${renderer.origin}/api/project/current`)).json();for(const name of ['best_model.pt','job_receipt.json'])expect(fs.existsSync(path.join(project.models_dir,'rotation',jobId,name))).toBe(false);
 expect((await(await request.get(`${renderer.origin}/api/rotation/models`)).json()).models).toEqual([]);
 for(const [file,sha]of Object.entries(originals))expect(crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')).toBe(sha);
 await evidence.screenshot(page,'actual-specialist-runtime-stopped');evidence.note('scope',{actual_renderer:true,actual_owned_backend:true,actual_cpu_training:true,server:false,manual_cancellation:false,posted,terminal:row,source_unchanged:true,source_images:Object.entries(originals).map(([path,sha256])=>({path,sha256}))});
});
