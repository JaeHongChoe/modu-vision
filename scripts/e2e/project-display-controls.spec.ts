import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {Page,Route} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

type Api=(route:string,body?:any,method?:string)=>Promise<any>;
const sha=(file:string)=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
function files(root:string):Record<string,string>{
 const found:Record<string,string>={};
 const walk=(dir:string)=>{for(const item of fs.readdirSync(dir,{withFileTypes:true})){const file=path.join(dir,item.name);expect(item.isSymbolicLink()).toBe(false);if(item.isDirectory())walk(file);else if(item.isFile()&&!item.name.endsWith('.lock'))found[path.relative(root,file)]=sha(file);}};
 if(fs.existsSync(root))walk(root);return found;
}
test.use({actionTimeout:10_000});

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,url?:string){
 const name='Project display controls',created=await api('/api/project/create',{name,task:'classification'});
 await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');await api('/api/dataset/import',{folder_path:workspace.dataset,task:'classification'});
 const image=workspace.images.find(row=>row.label==='ng')!,query='/api/annotations/'+path.basename(image.path,'.png')+'?file_path='+encodeURIComponent(image.path);
 await api('/api/annotations/save',{image_id:path.basename(image.path,'.png'),image_path:image.path,image_width:32,image_height:32,actor:'fixture-label-author',annotations:[{id:'fixture-label',type:'tag',label:'ng',category_id:1,is_normal:false}]});
 // Normal UI reads register the other image and default team settings. Stabilize those owned records before freezing all annotation bytes.
 await api('/api/dataset/metadata?limit=10');await api('/api/team-data');
 const saved=await api(query),defaultRoot=path.join(created.project_dir,'annotations'),originalLabels=files(defaultRoot);expect(Object.keys(originalLabels).length).toBeGreaterThan(0);
 const prefFile=path.join(created.project_dir,'preferences.json');let patches=0,copies=0;
 const observe=(req:any)=>{const u=new URL(req.url());if(u.pathname==='/api/project/preferences'&&req.method()==='PATCH')patches++;if(u.pathname==='/api/project/labelsets'&&req.method()==='POST')copies++;};page.on('request',observe);
 const stage=async(index:number)=>{await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(index).click();};
 const reload=async(index:number)=>{if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);await stage(index);};
 const toggle=()=>page.getByRole('button',{name:'태그 색상·플래그',exact:true}).click();const panel=page.getByRole('region',{name:'프로젝트 태그 색상과 모델 플래그'});
 const tag=panel.getByLabel('색상을 지정할 태그'),color=panel.getByLabel('태그 색상',{exact:true}),flags=panel.getByLabel('라벨 세트 플래그'),tagSave=panel.getByRole('button',{name:'저장',exact:true}).nth(0),flagSave=panel.getByRole('button',{name:'저장',exact:true}).nth(1);
 const response=(method:string,route:string)=>page.waitForResponse(r=>r.request().method()===method&&new URL(r.url()).pathname===route);
 const savePreference=async(action:()=>Promise<unknown>,status=200)=>{const event=response('PATCH','/api/project/preferences');await action();const reply=await event;expect(reply.status(),await reply.text()).toBe(status);return reply.json();};

 await reload(0);await toggle();await expect(tag).toBeEnabled();await expect(tagSave).toBeDisabled();await tag.fill('   ');await expect(tagSave).toBeDisabled();
 await tag.fill('inspection-ready');await color.fill('#24a7cb');await tagSave.click();await expect(panel.getByRole('alert')).toContainText('작업자 이름');expect(patches).toBe(0);expect(fs.existsSync(prefFile)).toBe(false);
 await stage(1);await page.getByRole('button',{name:'이미지 정보·검토',exact:true}).click();await page.getByRole('region',{name:'이미지 정보와 검토 기록'}).getByLabel('작업자·검토자 이름').fill('fixture-display-editor');
 await stage(0);await toggle();await expect(tag).toBeEnabled();await tag.fill('inspection-ready');await color.fill('#24a7cb');await savePreference(()=>tagSave.click());await expect(panel.getByRole('button',{name:'inspection-ready',exact:true})).toBeVisible();
 await flags.fill('baseline');await savePreference(()=>flagSave.click());const baseline=await api('/api/project/preferences'),baselineHash=sha(prefFile);expect(baseline.labelset_flags.default).toEqual(['baseline']);
 const baselinePatches=patches;await tag.fill('unsubmitted-tag');await color.fill('#ef4444');await flags.fill('unsubmitted-flag');await stage(1);expect(patches).toBe(baselinePatches);expect(sha(prefFile)).toBe(baselineHash);
 await stage(0);await toggle();await expect(flags).toHaveValue('baseline');await expect(panel.getByRole('button',{name:'unsubmitted-tag',exact:true})).toHaveCount(0);expect(sha(prefFile)).toBe(baselineHash);
 await flags.fill('x'.repeat(41));await savePreference(()=>flagSave.click(),422);await expect(panel.getByRole('alert')).toContainText('at most twenty');expect(sha(prefFile)).toBe(baselineHash);
 await flags.fill('');const cleared=await savePreference(()=>flagSave.click());expect(cleared.labelset_flags.default).toEqual([]);expect(JSON.parse(fs.readFileSync(prefFile,'utf8')).labelset_flags.default).toEqual([]);
 await flags.fill('baseline, baseline, second');const normalized=await savePreference(()=>flagSave.click());expect(normalized.labelset_flags.default).toEqual(['baseline','second']);
 const beforeFailure=sha(prefFile);await tag.fill('inspection-ready');await color.fill('#ef4444');
 let release!:()=>void,reached!:()=>void;const blocked=new Promise<void>(resolve=>release=resolve),requested=new Promise<void>(resolve=>reached=resolve);
 const failPreference=async(route:Route)=>{if(route.request().method()!=='PATCH')return route.continue();reached();await blocked;return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Controlled preference unavailable'})});};
 await page.route('**/api/project/preferences',failPreference);await tagSave.click();await requested;await expect(tag).toBeDisabled();await expect(flags).toBeDisabled();await expect(tagSave).toBeDisabled();release();
 await expect(panel.getByRole('alert')).toContainText('Controlled preference unavailable');expect(sha(prefFile)).toBe(beforeFailure);await evidence.screenshot(page,'actual-preference-error-original-bytes-preserved');
 await page.unroute('**/api/project/preferences',failPreference);await savePreference(()=>tagSave.click());const red=await api('/api/project/preferences');expect(red.tag_colors['inspection-ready']).toBe('#ef4444');
 const separate=await api('/api/project/preferences',{expected_revision:red.revision,actor:'fixture-concurrent-writer',changes:{tag_colors:{'concurrent-tag':'#aabbcc'}}},'PATCH');const concurrentHash=sha(prefFile);
 await color.fill('#22d3ee');await savePreference(()=>tagSave.click(),409);await expect(panel.getByRole('alert')).toContainText('Preferences changed');expect(sha(prefFile)).toBe(concurrentHash);expect((await api('/api/project/preferences')).revision).toBe(separate.revision);await evidence.screenshot(page,'actual-preference-revision-conflict-no-overwrite');
 await toggle();await toggle();await expect(flags).toHaveValue('baseline, second');await expect(panel.getByRole('button',{name:'concurrent-tag',exact:true})).toBeVisible();await tag.fill('inspection-ready');await color.fill('#22d3ee');await savePreference(()=>tagSave.click());
 const merged=await api('/api/project/preferences');expect(merged.tag_colors).toEqual({'inspection-ready':'#22d3ee','concurrent-tag':'#aabbcc'});expect(merged.labelset_flags.default).toEqual(['baseline','second']);
 // The same real transport refusal is exercised for flags; removing the fixture requires an explicit click.
 const failFlags=async(route:Route)=>route.request().method()==='PATCH'?route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Controlled flag unavailable'})}):route.continue();
 await flags.fill('kept-after-retry');const mergedHash=sha(prefFile);await page.route('**/api/project/preferences',failFlags);await savePreference(()=>flagSave.click(),503);await expect(panel.getByRole('alert')).toContainText('Controlled flag unavailable');expect(sha(prefFile)).toBe(mergedHash);await page.unroute('**/api/project/preferences',failFlags);await savePreference(()=>flagSave.click());
 const beforeCopyPrefs=await api('/api/project/preferences'),registryFile=path.join(created.project_dir,'labelsets.json');await stage(1);const registryHash=sha(registryFile),registry=await api('/api/project/labelsets'),copyButton=page.getByRole('button',{name:'현재 라벨 복제',exact:true}),copyName=page.getByLabel('새 레이블셋 이름');
 await expect(copyButton).toBeDisabled();await copyName.fill('  ');await expect(copyButton).toBeDisabled();await copyName.fill('Unsubmitted review');const copyCount=copies;await stage(0);expect(copies).toBe(copyCount);expect(sha(registryFile)).toBe(registryHash);await stage(1);await expect(copyName).toHaveValue('');
 const failCopy=async(route:Route)=>route.request().method()==='POST'?route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Controlled label copy unavailable'})}):route.continue();
 await copyName.fill('Copied review');await page.route('**/api/project/labelsets',failCopy);const failed=response('POST','/api/project/labelsets');await copyButton.click();expect((await failed).status()).toBe(503);await expect(page.getByRole('alert').filter({hasText:'Controlled label copy unavailable'})).toBeVisible();expect(sha(registryFile)).toBe(registryHash);expect((await api('/api/project/current')).active_labelset_id).toBe('default');expect(files(defaultRoot)).toEqual(originalLabels);await evidence.screenshot(page,'actual-label-copy-error-no-new-labelset');
 await page.unroute('**/api/project/labelsets',failCopy);const copiedResponse=response('POST','/api/project/labelsets');await copyButton.click();const copiedReply=await copiedResponse;expect(copiedReply.status()).toBe(200);const copied=await copiedReply.json();await expect(page.getByLabel('활성 레이블셋')).toHaveValue(copied.id);await expect(copyName).toHaveValue('');
 expect(copied.name).toBe('Copied review');expect(copied.source_id).toBe('default');const afterCopy=await api('/api/project/labelsets');expect(afterCopy.labelsets).toHaveLength(registry.labelsets.length+1);expect((await api(query)).annotations).toEqual(saved.annotations);expect(files(defaultRoot)).toEqual(originalLabels);expect(files(path.join(created.project_dir,'labelsets',copied.id,'annotations'))).toEqual(originalLabels);
 await stage(0);await toggle();await expect(flags).toHaveValue('');await flags.fill('named-review');await savePreference(()=>flagSave.click());const finalPrefs=await api('/api/project/preferences');expect(finalPrefs.labelset_flags.default).toEqual(['kept-after-retry']);expect(finalPrefs.labelset_flags[copied.id]).toEqual(['named-review']);expect(finalPrefs.tag_colors).toEqual(merged.tag_colors);
 await reload(1);await expect(page.getByLabel('활성 레이블셋')).toHaveValue(copied.id);expect((await api(query)).annotations).toEqual(saved.annotations);await evidence.screenshot(page,'actual-copied-labelset-reopened-with-original-labels');
 await stage(0);await toggle();await expect(flags).toHaveValue('named-review');await expect(panel.getByRole('button',{name:'concurrent-tag',exact:true})).toBeVisible();await expect(panel.getByRole('button',{name:'inspection-ready',exact:true})).toHaveCSS('color','rgb(34, 211, 238)');await evidence.screenshot(page,'actual-reopened-preferences-named-default-isolation');
 expect(files(defaultRoot)).toEqual(originalLabels);for(const original of workspace.images){expect(sha(original.path)).toBe(original.sha256);evidence.addFile(original.path);}for(const file of [prefFile,registryFile])evidence.addFile(file);
 evidence.note('project_display_controls',{actual_ui_and_backend:true,blank_tag_and_copy_disabled:true,missing_actor_no_write:true,unsubmitted_tag_flags_copy_cancelled:true,flag_41_char_actual_422:true,empty_flag_explicit_clear:true,duplicate_flags_normalized:true,controlled_tag_503_preserved_bytes:true,controlled_flag_503_preserved_bytes:true,actual_concurrent_revision_409_preserved_writer:true,explicit_refresh_retry_preserved_unrelated_change:true,controlled_copy_503_no_clone:true,explicit_copy_retry_same_content:true,default_original_annotation_hashes:originalLabels,copied_set:copied,preferences:finalPrefs,before_copy_preferences:beforeCopyPrefs,source_hashes:workspace.images,actual_reopen:true,fixture_labels_not_human_quality_truth:true,no_training_submitted:true,human_quality_approval:false,independent_acceptance:false});page.off('request',observe);
}
test('project display controls preserve labels and concurrent preferences through errors cancel and retry',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const r=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(r.ok(),await r.text()).toBe(true);return r.json();};await exercise(page,workspace,evidence,api,renderer.url);
});
test('native project display controls preserve labels and concurrent preferences through errors cancel and retry',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,status=await electronSession.waitForBackend();const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!r.ok)throw Error(`Owned display HTTP ${r.status}`);return r.json();},{port:status.port,route,body,method});await exercise(window,workspace,evidence,api);
});
