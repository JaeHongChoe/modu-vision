import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {expect,test} from './fixtures/test';
import {png} from './qa/appFlow';

test.use({actionTimeout:10_000});
for(const family of ['core','ocr'] as const)test(`${family} parent selection displays saved context without inventing missing completion`,async({page,request,renderer,workspace,evidence})=>{
 const source=path.join(workspace.root,'parent-summary-source');const files:string[]=[];
 for(const split of ['train','val'])for(const label of ['OK','NG'])for(let i=0;i<2;i++){
  const folder=path.join(source,split,label);fs.mkdirSync(folder,{recursive:true});const file=path.join(folder,`${label}-${i}.png`);fs.writeFileSync(file,png(32,3,(x,y)=>[x+i*3,y,label==='OK'?200:30]));files.push(file);
 }
 const hashes=()=>Object.fromEntries(files.map(file=>[file,crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')]));const before=hashes();
 expect((await request.post(`${renderer.origin}/api/project/create`,{data:{name:`S209 parent summary ${family}`,task:'classification'}})).ok()).toBe(true);
 expect((await request.put(`${renderer.origin}/api/project/update`,{data:{source_dataset_dir:source}})).ok()).toBe(true);
 const complete={job_id:'job_summary_parent',checkpoint_sha256:'a'.repeat(64),summary:{model_recorded_at:'2026-09-30T00:00:00Z',completed_at:null,training_metrics:{best_metric:.91,val_loss:.25}}};
 const legacy={job_id:'job_legacy_parent',checkpoint_sha256:'b'.repeat(64)};const submits:string[]=[];
 await page.route('**/api/training/warm-start-parents?**',r=>r.fulfill({json:{parents:[complete,legacy],total:2}}));
 await page.route('**/api/ocr/warm-start-parents?**',r=>r.fulfill({json:{parents:[complete,legacy],total:2}}));
 if(family==='ocr')await page.route('**/api/ocr/datasets',r=>r.fulfill({json:{datasets:[{dataset_path:source,sample_count:8,provenance:{source_dataset_path:source}}]}}));
 page.on('request',r=>{if(r.method()==='POST'&&['/api/training/start','/api/ocr/train','/api/compute/jobs'].includes(new URL(r.url()).pathname))submits.push(r.url());});
 await page.goto(renderer.url);await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(`S209 parent summary ${family}`);
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(2).click();
 if(family==='ocr')await page.getByRole('region',{name:'모델 학습 허브'}).getByRole('button',{name:/^문자 인식/}).click();
 const select=page.getByRole('combobox',{name:family==='core'?'재학습 시작 모델':'ocr 재학습 시작 모델',exact:true});
 const option=select.locator('option[value="job_summary_parent"]');await expect(option).toContainText('검증 손실=0.2500',{timeout:10_000});await expect(option).toContainText('모델 기록 2026-09-30 00:00 UTC');await expect(option).toContainText('완료일 미확인');
 await expect(select.locator('option[value="job_legacy_parent"]')).toContainText('학습 지표 미확인');await expect(select).toBeEnabled();await select.selectOption(complete.job_id);await expect(select).toHaveValue(complete.job_id);
 await expect(page.getByText('지표는 저장된 학습 기록입니다.',{exact:false})).toBeVisible();expect(submits).toEqual([]);expect(hashes()).toEqual(before);
 await select.scrollIntoViewIfNeeded();await evidence.screenshot(page,`s209-parent-summary-${family}`);evidence.note('scope',{actual_renderer:true,actual_backend:true,parent_api:'controlled',compatibility_qualification:'separate backend suites',training:false,server:false,submits,selected_parent:complete.job_id,source_images:Object.entries(before).map(([path,sha256])=>({path,sha256,bytes:fs.statSync(path).size})),source_unchanged:true});
});
