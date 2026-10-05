import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness=require('./fixtures/harness.cjs');
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 await api('/api/project/create',{name:'Provenance trace fixture',task:'classification'});
 const project=await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
 await api('/api/team-data/settings',{expected_revision:1,actor:'fixture-owner',changes:{review_enabled:true,approved_only_training:true}},'PUT');
 await api('/api/team-data/books',{expected_version:0,actor:'fixture-owner',title:'Controlled guidance',categories:[{id:0,name:'OK',color:'#10b981'},{id:1,name:'NG',color:'#ef4444'}]});
 const image=path.join(workspace.dataset,'ok','sample-ok.png');
 const saved=await api('/api/annotations/save',{image_id:'sample-ok',image_path:image,image_width:32,image_height:32,actor:'fixture-labeler',annotations:[{type:'tag',label:'OK',category_id:0,is_normal:true}]});
 await api(`/api/team-data/images/${saved.metadata.image_uuid}/review`,{expected_revision:saved.metadata.revision,actor:'fixture-reviewer',decision:'approve'});
 await api('/api/dataset/split',{folder_path:workspace.dataset,task:'classification',train_ratio:0.5,val_ratio:0.5,seed:17});
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/provenance_binding.py'),workspace.root,project.project_dir],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:45_000}).trim());evidence.note('fixture',fixture);
 if(url)await page.goto(url);else await page.reload();
 await page.getByRole('button',{name:'데이터·판정 이력',exact:true}).click();
 let dialog=page.getByRole('dialog',{name:'데이터·모델·판정 이력',exact:true});
 await dialog.getByLabel('검사 이미지',{exact:true}).selectOption(image);
 const traceRoute='/api/provenance?image_path='+encodeURIComponent(image);
 const before=await api(traceRoute);expect(before.review_binding.image_eligible).toBe(true);expect(before.models[0].data_state).toBe('current');expect(before.split.sha256).toMatch(/^[a-f0-9]{64}$/);expect(before.split.sha256).toBe(fixture.binding.split_sha256);expect(before.split.assignment).toBe('val');
 await expect(dialog.getByRole('region',{name:'현재 검수·학습 적격성'})).toContainText('전체 적격 1장');
 await dialog.getByText('classification · job_trace',{exact:false}).click();
 await expect(dialog).toContainText(before.split.sha256);
 await expect(dialog).toContainText(fixture.checkpoint_sha256);await expect(dialog).toContainText(fixture.binding.dataset_version_id);
 await dialog.getByText('REVIEW · '+fixture.run_id,{exact:true}).click();await expect(dialog).toContainText(fixture.graph_sha256);
 await dialog.getByText('Controlled trace flow · '+fixture.flow_version_id,{exact:true}).click();
 await expect(dialog).toContainText('job_trace');
 const versionSummary=dialog.locator('summary').filter({hasText:fixture.binding.dataset_version_id});await versionSummary.click();
 await expect(dialog).toContainText(before.review_binding.eligibility_sha256);
 await evidence.screenshot(page,`${native?'native':'browser'}-provenance-bound-before`);
 // Revoke review without altering source or saved label bytes.
 await api('/api/dataset/metadata/'+saved.metadata.image_uuid,{expected_revision:before.label.revision,actor:'fixture-reviewer',changes:{workflow_state:'needs_review'}},'PATCH');
 await dialog.getByRole('button',{name:'이력 새로고침',exact:true}).click();
 await expect(dialog.getByRole('region',{name:'현재 검수·학습 적격성'})).toContainText('전체 적격 0장');await expect(dialog).toContainText('학습 입력 변경됨 · 재검토 필요');
 const revoked=await api(traceRoute);expect(revoked.label.sha256).toBe(before.label.sha256);expect(revoked.models[0].data_state).toBe('changed');
 const original=fs.readFileSync(image);try{
  // Valid alternate bytes within the isolated source; originals outside this fixture are never touched.
  fs.copyFileSync(path.join(workspace.dataset,'ng','sample-ng.png'),image);
  await dialog.getByRole('button',{name:'이력 새로고침',exact:true}).click();await expect(dialog.getByText('원본 변경됨',{exact:true})).toHaveCount(2);
  const changed=await api(traceRoute);expect(changed.inspections[0].image_sha256).toBe(fixture.image_sha256);expect(changed.inspections[0].image_matches).toBe(false);
  await evidence.screenshot(page,`${native?'native':'browser'}-provenance-stale`);evidence.note('changed',changed);
 }finally{fs.writeFileSync(image,original);}
 await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);await page.reload();
 await page.getByRole('button',{name:'데이터·판정 이력',exact:true}).click();dialog=page.getByRole('dialog',{name:'데이터·모델·판정 이력',exact:true});await dialog.getByLabel('검사 이미지',{exact:true}).selectOption(image);
 await expect(dialog.getByRole('region',{name:'현재 검수·학습 적격성'})).toContainText('전체 적격 0장');
 const reopened=await api(traceRoute);expect(reopened.models[0].data_state).toBe('changed');expect(reopened.inspections[0].flow_version_id).toBe(fixture.flow_version_id);expect(reopened.inspections[0].image_sha256).toBe(fixture.image_sha256);expect(reopened.image.content_hash).toBe(fixture.image_sha256);
 await evidence.screenshot(page,`${native?'native':'browser'}-provenance-reopened`);
 evidence.note('provenance_closure',{before,revoked,reopened,actual_app_read_only_trace:true,source_restored:true,quality_approved:false,new_model_execution:false});
}
test('source label split model flow and historical result trace survives revocation and source changes',async({page,renderer,workspace,evidence})=>{
 const api:Api=async(route,body,method)=>{const r=await page.request.fetch(renderer.origin+route,{method:method||(body?'POST':'GET'),data:body});expect(r.ok(),await r.text()).toBe(true);return r.json();};await installDesktopHostShim(page,renderer.port);await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native provenance displays frozen lineage separately from current review eligibility',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const status=await electronSession.waitForBackend(),page=electronSession.window;
 const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!r.ok)throw Error(`Owned trace fixture HTTP ${r.status}`);return r.json();},{port:status.port,route,body,method});await exercise(page,workspace,evidence,api,true);
});
