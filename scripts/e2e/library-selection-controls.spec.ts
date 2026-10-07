import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
test.use({actionTimeout:10_000});
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(bytes:Buffer)=>crypto.createHash('sha256').update(bytes).digest('hex');

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const prefix=native?'native':'browser',name='Resolved library selection fixture';
 const project=await api('/api/project/create',{name,task:'classification'});
 await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
 const started=await api('/api/dataset/imports',{task:'classification',verify:true});let view:any;
 for(let i=0;i<200;i++){view=await api(`/api/dataset/imports/${started.job_id}`);if(['completed','failed','aborted','interrupted'].includes(view.state))break;await new Promise(r=>setTimeout(r,50));}
 expect(view.state).toBe('completed');await api(`/api/dataset/imports/${started.job_id}/accept`,{revision_id:view.result.revision.revision_id,expected_active:null});
 const rows=await api('/api/dataset/library/images?limit=120');expect(rows.items.length).toBeGreaterThan(1);const first=rows.items[0],second=rows.items[1];
 const metadata=await api('/api/dataset/metadata/image?image_path='+encodeURIComponent(first.file_path));
 await api('/api/dataset/metadata/'+metadata.image_uuid,{expected_revision:metadata.revision,actor:'fixture',changes:{tags:['selected-cohort']}},'PATCH');
 const storage=()=>page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.startsWith('modu.inspectionImage.v2:')).map(([key,value])=>({key,value:JSON.parse(value)})));
 const requests:any[]=[],prohibited:string[]=[];
 page.on('request',r=>{const u=new URL(r.url());if(u.pathname==='/api/dataset/library/images')requests.push(Object.fromEntries(u.searchParams));if(r.method()==='POST'&&/\/(training\/start|compute\/jobs|train)$/.test(u.pathname))prohibited.push(u.pathname);});
 if(url)await page.goto(url);else await page.reload();
 await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);
 await page.getByRole('button',{name:/05.*플로우차트/}).click();
 const open=()=>page.getByRole('button',{name:'이미지 변경...',exact:true}).click();
 const picker=page.getByRole('dialog',{name:'검사 대상 이미지 선택'}),grid=picker.getByRole('list',{name:'데이터 버전 이미지'});
 const confirm=()=>picker.getByRole('button',{name:'선택 확정',exact:true});
 const cancel=()=>picker.getByRole('button',{name:'취소',exact:true}).click();
 const selected=()=>expect(picker).toContainText('선택: '+first.relative_path);
 await open();await expect(grid.getByRole('listitem').first()).toBeVisible();await expect(confirm()).toBeDisabled();expect(await storage()).toEqual([]);
 await grid.getByRole('listitem').filter({hasText:first.file_name}).first().click();await selected();await confirm().click();await expect(picker).toBeHidden();
 const remembered=await storage();expect(remembered).toHaveLength(1);expect(remembered[0].value.imageUuid).toBe(first.image_uuid);expect(remembered[0].value.sha256).toBe(first.sha256);
 let release!:()=>void,arrived!:()=>void;const hold=new Promise<void>(r=>release=r),ready=new Promise<void>(r=>arrived=r);let resolveBody:any;
 await page.route('**/api/dataset/library/resolve',async r=>{resolveBody=r.request().postDataJSON();arrived();await hold;await r.continue();});
 const resolved=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/dataset/library/resolve'&&r.request().method()==='POST');
 await open();await ready;
 try{await expect(confirm()).toBeDisabled();expect(await storage()).toEqual(remembered);await evidence.screenshot(page,`${prefix}-saved-identity-waits-for-original-resolution`);}finally{release();}
 const resolvedReply=await resolved;expect(resolvedReply.status()).toBe(200);const resolution=await resolvedReply.json();expect(resolution.results[0].status).toBe('found');
 await page.unroute('**/api/dataset/library/resolve');await selected();await expect(confirm()).toBeEnabled();
 await expect(grid.getByRole('listitem').first()).toBeVisible();await grid.getByRole('listitem').filter({hasText:second.file_name}).first().click();
 await expect(picker).toContainText('선택: '+second.relative_path);await cancel();expect(await storage()).toEqual(remembered);
 await open();await selected();await expect(confirm()).toBeEnabled();await cancel();
 await page.route('**/api/dataset/library/resolve',r=>r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Owned saved identity unavailable'})}));
 await open();await expect(picker.getByRole('alert')).toContainText('Owned saved identity unavailable');await expect(confirm()).toBeDisabled();expect(await storage()).toEqual(remembered);
 await evidence.screenshot(page,`${prefix}-unresolved-saved-selection-is-not-offered`);await cancel();await page.unroute('**/api/dataset/library/resolve');
 await page.route('**/api/dataset/library/images*',r=>r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Owned filtered library unavailable'})}));
 await open();await expect(picker.getByRole('alert')).toContainText('Owned filtered library unavailable');
 await picker.getByLabel('태그',{exact:true}).fill('selected-cohort');await expect(picker.getByRole('alert')).toContainText('Owned filtered library unavailable');
 expect(await storage()).toEqual(remembered);await page.unroute('**/api/dataset/library/images*');
 await picker.getByRole('button',{name:'다시 불러오기',exact:true}).click();await expect(grid.getByRole('listitem')).toHaveCount(1);
 await expect(picker.getByLabel('태그',{exact:true})).toHaveValue('selected-cohort');await expect(grid).toContainText(first.file_name);await selected();
 expect(requests.some(q=>q.tag==='selected-cohort')).toBe(true);await evidence.screenshot(page,`${prefix}-exact-filter-retry-preserves-confirmed-identity`);
 await picker.getByLabel('Lot',{exact:true}).fill('abandoned-transient-cohort');await expect(grid.getByRole('listitem')).toHaveCount(0);await cancel();expect(await storage()).toEqual(remembered);
 await open();await expect(picker.getByLabel('태그',{exact:true})).toHaveValue('');await expect(picker.getByLabel('Lot',{exact:true})).toHaveValue('');await selected();await expect(grid.getByRole('listitem').first()).toBeVisible();
 await picker.getByLabel('태그',{exact:true}).fill('selected-cohort');await expect(grid.getByRole('listitem')).toHaveCount(1);
 await grid.getByRole('listitem').click();await confirm().click();await expect(picker).toBeHidden();expect(await storage()).toEqual(remembered);
 await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);await page.getByRole('button',{name:/05.*플로우차트/}).click();await open();await selected();await expect(confirm()).toBeEnabled();
 expect(await storage()).toEqual(remembered);expect(prohibited).toEqual([]);
 for(const image of workspace.images){expect(sha(fs.readFileSync(image.path))).toBe(image.sha256);evidence.addFile(image.path);}
 await evidence.screenshot(page,`${prefix}-resolved-filter-selection-handoff-reopened`);
 evidence.note('library_selection_controls',{project,first,second,remembered,requests,resolveBody,resolution,prohibited,source_images:workspace.images,
  actual_ui_and_backend:true,empty_confirmation_disabled:true,pending_saved_resolution_disabled:true,actual_found_resolution:true,controlled_saved_resolution_503:true,unresolved_confirmation_disabled:true,
  unsubmitted_selection_cancel_preserved:true,controlled_filtered_listing_503:true,exact_query_explicit_retry:true,transient_filter_cancel_preserved:true,reopened_transient_filters_cleared:true,
  filtered_identity_handoff_exact:true,reopened_confirmed_identity:true,source_hashes_preserved:true,no_training_submitted:true,human_quality_approval:false,independent_acceptance:false});
}
test('library selection waits for saved identity and preserves cancelled failed and retried filters',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);
 const api:Api=async(route,body,method)=>{const r=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(r.ok(),`Owned library fixture HTTP ${r.status()}`).toBe(true);return r.json();};
 await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native library selection refuses unresolved saved bytes and reopens exact confirmed identity',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,status=await electronSession.waitForBackend();
 const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!r.ok)throw Error(`Owned library fixture HTTP ${r.status}`);return r.json();},{port:status.port,route,body,method});
 await exercise(window,workspace,evidence,api,true);
});

async function exerciseChangedOriginal(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const prefix=native?'native':'browser',name='Changed original identity fixture';
 const project=await api('/api/project/create',{name,task:'classification'});
 await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
 const importAndAccept=async(expectedActive:string|null)=>{
  const started=await api('/api/dataset/imports',{task:'classification',verify:true});let view:any;
  for(let i=0;i<200;i++){view=await api(`/api/dataset/imports/${started.job_id}`);if(['completed','failed','aborted','interrupted'].includes(view.state))break;await new Promise(r=>setTimeout(r,50));}
  expect(view.state).toBe('completed');const revision=view.result.revision.revision_id;
  const accepted=await api(`/api/dataset/imports/${started.job_id}/accept`,{revision_id:revision,expected_active:expectedActive});
  expect(accepted.active_revision).toBe(revision);return {job_id:started.job_id,revision};
 };
 const originalRevision=await importAndAccept(null);
 const rows=await api('/api/dataset/library/images?limit=120'),first=rows.items[0];
 expect(rows.items.length).toBeGreaterThan(1);
 const original=workspace.images.find(i=>path.resolve(i.path)===path.resolve(first.file_path));expect(original).toBeDefined();
 expect(fs.lstatSync(first.file_path).isSymbolicLink()).toBe(false);expect(sha(fs.readFileSync(first.file_path))).toBe(original!.sha256);
 const storage=()=>page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.startsWith('modu.inspectionImage.v2:')).map(([key,value])=>({key,value:JSON.parse(value)})));
 const prohibited:string[]=[];page.on('request',r=>{if(r.method()==='POST'&&/\/(training\/start|compute\/jobs|train)$/.test(new URL(r.url()).pathname))prohibited.push(r.url());});
 if(url)await page.goto(url);else await page.reload();
 const enter=async()=>{await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);await page.getByRole('button',{name:/05.*플로우차트/}).click();};
 await enter();const open=()=>page.getByRole('button',{name:'이미지 변경...',exact:true}).click();
 const picker=page.getByRole('dialog',{name:'검사 대상 이미지 선택'}),confirm=()=>picker.getByRole('button',{name:'선택 확정',exact:true});
 await open();await picker.getByRole('listitem').filter({hasText:first.file_name}).first().click();await confirm().click();await expect(picker).toBeHidden();
 const remembered=await storage();expect(remembered).toHaveLength(1);expect(remembered[0].value.imageUuid).toBe(first.image_uuid);expect(remembered[0].value.sha256).toBe(original!.sha256);
 const preserved=path.join(workspace.root,'preserved-original.png'),replacement=path.join(workspace.root,'preserved-replacement.png');
 expect(fs.existsSync(preserved)).toBe(false);expect(fs.existsSync(replacement)).toBe(false);
 const replacementBytes=png(32,3,(x,y)=>[x*7%256,y*5%256,97]),replacementSha=sha(replacementBytes);
 expect(replacementSha).not.toBe(original!.sha256);fs.renameSync(first.file_path,preserved);let changedRevision:any,changedResolution:any;
 try{
  fs.writeFileSync(first.file_path,replacementBytes,{flag:'wx'});changedRevision=await importAndAccept(originalRevision.revision);
  const changedRows=await api('/api/dataset/library/images?limit=120'),changedRow=changedRows.items.find((i:any)=>i.image_uuid===first.image_uuid);
  expect(changedRow.sha256).toBe(replacementSha);expect(sha(fs.readFileSync(preserved))).toBe(original!.sha256);
  await page.reload();await enter();const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/dataset/library/resolve'&&r.request().method()==='POST');
  await open();const reply=await response;expect(reply.status()).toBe(200);changedResolution=await reply.json();
  expect(changedResolution.results[0].status).toBe('changed');expect(changedResolution.results[0].current.sha256).toBe(replacementSha);
  await expect(picker).toContainText('다른 내용의 파일');await expect(confirm()).toBeDisabled();expect(await storage()).toEqual(remembered);
  await evidence.screenshot(page,`${prefix}-changed-original-is-not-confirmable`);await picker.getByRole('button',{name:'취소',exact:true}).click();
 }finally{
  if(fs.existsSync(first.file_path))fs.renameSync(first.file_path,replacement);
  fs.renameSync(preserved,first.file_path);expect(sha(fs.readFileSync(first.file_path))).toBe(original!.sha256);
 }
 const restoredRevision=await importAndAccept(changedRevision.revision);
 await page.reload();await enter();const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/dataset/library/resolve'&&r.request().method()==='POST');
 await open();const reply=await response;expect(reply.status()).toBe(200);const restoredResolution=await reply.json();
 expect(restoredResolution.results[0].status).toBe('found');expect(restoredResolution.results[0].current.sha256).toBe(original!.sha256);
 await expect(picker).toContainText('선택: '+first.relative_path);await expect(confirm()).toBeEnabled();expect(await storage()).toEqual(remembered);expect(prohibited).toEqual([]);
 for(const image of workspace.images){expect(sha(fs.readFileSync(image.path))).toBe(image.sha256);evidence.addFile(image.path);}
 expect(sha(fs.readFileSync(replacement))).toBe(replacementSha);evidence.addFile(replacement);
 await evidence.screenshot(page,`${prefix}-restored-original-resolves-after-revalidation`);
 evidence.note('library_changed_original',{project,first,originalRevision,changedRevision,restoredRevision,remembered,replacement_sha256:replacementSha,changedResolution,restoredResolution,
  actual_ui_and_backend:true,actual_changed_resolution:true,invalid_confirmation_disabled:true,remembered_uuid_sha_unchanged:true,owned_original_temporarily_replaced:true,
  exact_original_restored_and_revalidated:true,actual_restored_found_resolution:true,replacement_evidence_preserved:true,no_training_submitted:true,human_quality_approval:false,independent_acceptance:false});
}
test('library selection refuses an actual changed original and recovers its exact restored revision',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);
 const api:Api=async(route,body,method)=>{const r=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(r.ok(),`Owned library fixture HTTP ${r.status()}`).toBe(true);return r.json();};
 await exerciseChangedOriginal(page,workspace,evidence,api,false,renderer.url);
});
test('native library selection refuses an actual changed original and recovers its exact restored revision',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,status=await electronSession.waitForBackend();
 const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!r.ok)throw Error(`Owned library fixture HTTP ${r.status}`);return r.json();},{port:status.port,route,body,method});
 await exerciseChangedOriginal(window,workspace,evidence,api,true);
});
