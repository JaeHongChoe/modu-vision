import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {expect,test} from './fixtures/test';
import {png} from './qa/appFlow';

test('native patch scheduling validates the budget and retains the prepared input on admission refusal',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession;const status=await electronSession.waitForBackend();expect(new URL(window.url()).protocol).toBe('file:');
 const source=path.join(workspace.root,'native-patch-source');fs.mkdirSync(source);const inputs:string[]=[];
 for(let i=0;i<3;i++){
  const file=path.join(source,`part-${i}.png`);fs.writeFileSync(file,png(32,3,(x,y)=>[x+i*5,y,120]));inputs.push(file);
  const label=file.replace(/\.png$/,'.json');fs.writeFileSync(label,JSON.stringify({imagePath:path.basename(file),imageWidth:32,imageHeight:32,shapes:[{label:'chip',shape_type:'rectangle',points:[[0,0],[8,8]]}]}));inputs.push(label);
 }
 const hashes=()=>Object.fromEntries(inputs.map(file=>[file,crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')]));const before=hashes();
 // Main supplies the owned process token; it never enters the spec or evidence.
 const api=async(route:string,body:unknown)=>window.evaluate(async({port,route,body})=>{
  const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:route.endsWith('/update')?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!response.ok)throw new Error(`Owned fixture API: HTTP ${response.status}`);return response.json();
 },{port:status.port,route,body});
 await api('/api/project/create',{name:'Native patch scheduling',task:'classification'});await api('/api/project/update',{source_dataset_dir:source});
 const prepared=await api('/api/patch-classification/prepare',{patch_size:16,stride:16});expect(prepared.patch_count).toBe(12);
 await window.reload();await expect(window.getByTitle('프로젝트 관리',{exact:true})).toContainText('Native patch scheduling');
 await window.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(2).click();await window.getByRole('region',{name:'모델 학습 허브'}).getByRole('button',{name:/^패치 분류/}).click();
 const settings=window.locator('summary',{hasText:'학습 실행 예산·대기열'});await settings.click();const minutes=window.getByLabel('학습 시간 제한 (분)',{exact:true});const start=window.getByRole('button',{name:'패치 분류 후보 학습',exact:true});
 const submissions:any[]=[];await window.route('**/api/patch-classification/train',route=>{
  submissions.push(route.request().postDataJSON());return route.fulfill({status:503,json:{detail:'Controlled native patch admission outage; no worker was launched'}});
 });
 await minutes.fill('-1');await expect(start).toBeDisabled();expect(submissions).toEqual([]);
 await minutes.fill('1.5');await window.getByLabel('장치가 사용 중이면 대기열에 넣기',{exact:true}).uncheck();await window.getByLabel('학습 대기열 우선순위',{exact:true}).fill('2');await start.click();
 await expect(window.getByRole('alert').filter({hasText:'Controlled native patch admission outage'})).toBeVisible();await expect(start).toBeEnabled();
 expect(submissions).toHaveLength(1);expect(submissions[0]).toMatchObject({dataset_path:prepared.dataset_path,queue:false,priority:2,max_runtime_s:90});
 expect(hashes()).toEqual(before);await settings.scrollIntoViewIfNeeded();await evidence.screenshot(window,'s209-native-patch-budget-refusal');
 evidence.note('scope',{actual_electron_main_preload:true,actual_owned_backend:true,patch_preparation:'actual source-bound 12 patches',training:false,server:false,training_submission:'controlled refusal only',submissions,source_images:Object.entries(before).map(([path,sha256])=>({path,sha256,bytes:fs.statSync(path).size})),source_unchanged:true});
});
