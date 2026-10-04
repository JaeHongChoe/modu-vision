import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {expect,test} from './fixtures/test';
import {png} from './qa/appFlow';

// Actual mounted renderer with owned backend; saved evaluation/approval reads and
// approval submission are controlled transport fixtures. No quality approval is created.
test.use({actionTimeout:10_000});
for(const mode of ['matching','failed read and missing comparison'] as const)test(`specialist replacement selects saved incumbent evidence: ${mode}`,async({page,request,renderer,workspace,evidence})=>{
 const source=path.join(workspace.root,'incumbent-source');fs.mkdirSync(source,{recursive:true});
 const files=Array.from({length:8},(_,i)=>{const file=path.join(source,`${i}.png`);fs.writeFileSync(file,png(32,3,(x,y)=>[x+i,y,80]));return file;});
 const hashes=()=>Object.fromEntries(files.map(file=>[file,crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')]));const before=hashes();
 expect((await request.post(`${renderer.origin}/api/project/create`,{data:{name:`S209 incumbent ${mode}`,task:'classification'}})).ok()).toBe(true);
 expect((await request.put(`${renderer.origin}/api/project/update`,{data:{source_dataset_dir:source}})).ok()).toBe(true);
 const record=(id:string,job:string,cohort='test-cohort',split='test')=>({evaluation_id:id,created_at:1791150000,result:{job_id:job,task:'ocr',split,sample_count:8,exact_match_accuracy:1,character_error_rate:0},binding:{source_dataset_path:source,checkpoint_sha256:(job==='incumbent'?'a':'b').repeat(64),dataset_fingerprint:'frozen-source',family_dataset_sha256:cohort}});
 let failActive=mode!=='matching',includeMatching=mode==='matching',reads=0;const submits:Record<string,unknown>[]=[],training:string[]=[];
 await page.route('**/api/evaluation/history?**',r=>r.fulfill({json:{items:[record('candidate-eval','candidate'),record('other-candidate-eval','candidate-two'),record('wrong-cohort','incumbent','other'),record('validation-eval','incumbent','test-cohort','val'),...(includeMatching?[record('incumbent-eval','incumbent')]:[])]}}));
 await page.route('**/api/model-deployments/active?**',r=>{reads++;return failActive?r.fulfill({status:503,json:{detail:'Controlled current approval unavailable'}}):r.fulfill({json:{active:{job_id:'incumbent',checkpoint_sha256:'a'.repeat(64),valid:true}}});});
 await page.route('**/api/model-deployments/specialized-approve',r=>{submits.push(r.request().postDataJSON());return r.fulfill({json:{revision:{revision_id:'transport-fixture-only'}}});});
 page.on('request',r=>{if(r.method()==='POST'&&['/api/training/start','/api/ocr/train','/api/compute/jobs'].includes(new URL(r.url()).pathname))training.push(r.url());});
 await page.goto(renderer.url);await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(`S209 incumbent ${mode}`);
 await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();const dialog=page.getByRole('dialog',{name:'패키지·장치·설치·진단',exact:true});
 await dialog.getByText('OCR · 회전 검출 · 이미지 개선 승인',{exact:true}).click();
 const candidate=dialog.getByRole('combobox',{name:'특수 모델 평가 승인',exact:true});const incumbent=dialog.getByLabel('특수 모델 기준 평가',{exact:true});
 const approve=dialog.getByRole('button',{name:'품질 기준 검증 후 승인',exact:true});const refresh=dialog.getByRole('button',{name:'특수 모델 평가 기록 새로 고침',exact:true});
 await expect(incumbent).toHaveJSProperty('tagName','SELECT',{timeout:5000});
 if(mode!=='matching'){
  await expect(dialog.getByRole('status').filter({hasText:'Controlled current approval unavailable'})).toBeVisible();await expect(candidate).toBeDisabled();await expect(approve).toBeDisabled();
  failActive=false;await refresh.click();await expect(candidate).toBeEnabled();await candidate.selectOption('candidate-eval');
  await expect(dialog.getByRole('alert').filter({hasText:'같은 test 데이터'})).toBeVisible();await expect(incumbent.locator('option[value="incumbent-eval"]')).toHaveCount(0);await expect(approve).toBeDisabled();
  includeMatching=true;await refresh.click();await expect(candidate).toHaveValue('');await expect(candidate).toBeEnabled();await candidate.selectOption('candidate-eval');await expect(incumbent.locator('option[value="incumbent-eval"]')).toHaveCount(1);expect(submits).toEqual([]);
 }else{
  await expect(candidate).toBeEnabled();await candidate.selectOption('candidate-eval');await expect(incumbent.locator('option')).toHaveCount(2);await expect(incumbent).toHaveValue('');
  await incumbent.selectOption('incumbent-eval');await dialog.getByLabel('특수 모델 검토자',{exact:true}).fill('Fixture reviewer');await dialog.getByLabel('특수 모델 승인 근거',{exact:true}).fill('Synthetic selection handler test only');const reviewed=dialog.getByRole('checkbox',{name:'독립 test 정답과 평가 결과를 검토했습니다.',exact:true});await reviewed.check();await expect(approve).toBeEnabled();
  await candidate.selectOption('other-candidate-eval');await expect(incumbent).toHaveValue('');await expect(reviewed).not.toBeChecked();await expect(approve).toBeDisabled();
  await incumbent.selectOption('incumbent-eval');await reviewed.check();await approve.click();await expect(dialog.getByRole('status').filter({hasText:'transport-fixture-only'})).toBeVisible();expect(submits).toHaveLength(1);expect(submits[0]).toMatchObject({source_dataset_path:source,task:'ocr',evaluation_id:'other-candidate-eval',incumbent_evaluation_id:'incumbent-eval'});
 }
 expect(training).toEqual([]);expect(hashes()).toEqual(before);await incumbent.scrollIntoViewIfNeeded();await evidence.screenshot(page,`s209-incumbent-${mode==='matching'?'selected':'recovered'}`);
 evidence.note('scope',{actual_renderer:true,actual_backend:true,evaluation_and_active_reads:'controlled',approval_post:'controlled',quality_approval_created:false,training:false,server:false,active_reads:reads,submissions:submits.length,source_unchanged:true,source_images:Object.entries(before).map(([path,sha256])=>({path,sha256,bytes:fs.statSync(path).size}))});
});
