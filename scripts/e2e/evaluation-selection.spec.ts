import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page,Request,Route} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
const harness=require('./fixtures/harness.cjs');
test.use({actionTimeout:10_000});
type OwnedApi=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(bytes:Buffer)=>crypto.createHash('sha256').update(bytes).digest('hex');
const withinFrame=async<T>(work:Promise<T>,deadline:number):Promise<T>=>{const remaining=deadline-Date.now();if(remaining<=0)throw Error('Owned evaluation action absolute10s deadline exhausted');let timer:ReturnType<typeof setTimeout>|undefined;try{return await Promise.race([work,new Promise<T>((_,reject)=>{timer=setTimeout(()=>reject(Error('Owned evaluation action absolute10s deadline exhausted')),remaining);})]);}finally{if(timer)clearTimeout(timer);}};
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:OwnedApi,native:boolean,url?:string,apiOrigin?:string){
 const source=path.join(workspace.root,'selection-source');fs.mkdirSync(source);const original=path.join(source,'part.png');fs.writeFileSync(original,png(64,3,(x,y)=>[x,y,60]));const sourceHash=sha(fs.readFileSync(original));
 const name='Exact saved evaluation selection fixture',project=await api('/api/project/create',{name,task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 await api('/api/project/labelsets',{name:'Other selection labelset'});const sets=await api('/api/project/labelsets'),secondSet=sets.labelsets.find((entry:any)=>entry.id!=='default').id;
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/evaluation_selection_reports.py'),workspace.root,project.project_dir,source,secondSet],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
 const selected=fixture.items.find((r:any)=>r.labelset_id==='default'&&r.variant==='valid'),alternate=fixture.items.find((r:any)=>r.variant==='selection_segmentation'),other=fixture.items.find((r:any)=>r.labelset_id===secondSet&&r.variant==='valid'),classification=fixture.items.find((r:any)=>r.variant==='selection_classification');
 const writes:string[]=[];page.on('request',request=>{if(request.method()!=='GET'&&/\/(evaluation|train|jobs)(\/|$)/.test(new URL(request.url()).pathname))writes.push(`${request.method()} ${new URL(request.url()).pathname}`);});
 const summary=page.locator('summary').filter({hasText:'평가 이력 · 제품/Lot별 오류'}),history=summary.locator('..'),selector=history.getByLabel(/^모델별 저장 평가/),labelset=history.getByLabel('평가 라벨 세트',{exact:true}),family=history.getByLabel('평가 이력 모델 종류',{exact:true}),group=history.getByLabel('평가 오류 집계 기준',{exact:true});
 const navigate=async(expectedName=name)=>{if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(expectedName);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click();};
 const open=async()=>{if(await history.getAttribute('open')===null)await summary.click();};
 const identity=history.locator('details').filter({has:page.locator('summary').filter({hasText:'평가 버전·데이터·모델 해시 확인'})}).first();
 const assertSelected=async(item:any)=>{await expect(selector).toHaveValue(item.record.evaluation_id);await expect(identity.locator('pre')).toContainText(item.record.evidence_sha256);await expect(identity.locator('pre')).toContainText(item.record.evaluation_id);};
 const choose=async(item:any)=>{await family.selectOption(item.record.result.task);await labelset.selectOption(item.labelset_id);await expect(selector.locator(`option[value="${item.record.evaluation_id}"]`)).toHaveCount(1);await selector.selectOption(item.record.evaluation_id);await assertSelected(item);};
 const refresh=async(expectedStatus=200)=>{const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/evaluation/history');await history.getByRole('button',{name:'평가 이력 새로고침',exact:true}).click();expect((await response).status()).toBe(expectedStatus);};
 const captureSelection=async(name:string)=>{await selector.scrollIntoViewIfNeeded();await expect(selector).toBeInViewport();await expect(labelset).toBeInViewport();await expect(family).toBeInViewport();await evidence.screenshot(page,`${native?'native':'browser'}-${name}`);};
 await navigate();await open();await choose(selected);await group.selectOption('lot');await refresh();await expect(labelset).toHaveValue('default');await assertSelected(selected);await expect(group).toHaveValue('lot');
 // A controlled transport failure exercises the real renderer refusal. Saved
 // reports remain unchanged; recovery must use the exact scoped preference.
 const failedQuery=/\/api\/evaluation\/history\?/;
 const failRefresh=async(route:import('@playwright/test').Route)=>{
  expect(new URL(route.request().url()).searchParams.get('source_dataset_path')).toBe(source);
  expect(new URL(route.request().url()).searchParams.get('task')).toBe('segmentation');
  expect(new URL(route.request().url()).searchParams.get('labelset_id')).toBe('default');
  await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Controlled evaluation refresh transport failure'})});
 };
 await page.route(failedQuery,failRefresh);
 try{
  await refresh(503);await expect(history.getByRole('alert')).toContainText('Controlled evaluation refresh transport failure');
  await expect(selector).toHaveCount(0);await expect(identity).toHaveCount(0);
  await expect(history.locator('summary').filter({hasText:'객체·픽셀·문자 오류와 분포 분석'})).toHaveCount(0);
  await expect(history.getByRole('button',{name:'선택 모델 재평가 · 새 이력 저장',exact:true})).toBeDisabled();
  await expect(history.getByRole('button',{name:'평가 이력 새로고침',exact:true})).toBeEnabled();
  await history.getByRole('alert').scrollIntoViewIfNeeded();await evidence.screenshot(page,`${native?'native':'browser'}-refresh-transport-refusal`);
 }finally{await page.unroute(failedQuery,failRefresh);}
 await refresh();await expect(history.getByRole('alert')).toHaveCount(0);await assertSelected(selected);
 await expect(labelset).toHaveValue('default');await expect(group).toHaveValue('lot');await captureSelection('refresh-exact-record-recovered');
 await navigate();await expect(history).toHaveAttribute('open','');await expect(family).toHaveValue('segmentation');await expect(labelset).toHaveValue('default');await assertSelected(selected);await expect(group).toHaveValue('lot');await captureSelection('refresh-exact-record-reopened');
 // One actual refresh GET is held before dispatch. Leaving the stage cancels
 // the view lifetime; the genuine unchanged response may still finish. This
 // does not claim an HTTP abort, model execution, or a human review decision.
 const cancelFrame=Date.now()+10_000,remaining=()=>{const value=cancelFrame-Date.now();if(value<=0)throw Error('Owned evaluation action absolute10s deadline exhausted');return value;};
 const historyURL=(request:Request)=>{const target=new URL(request.url());return request.method()==='GET'&&request.frame()===page.mainFrame()&&target.origin===apiOrigin&&target.pathname==='/api/evaluation/history'&&target.searchParams.get('source_dataset_path')===source&&target.searchParams.get('task')==='segmentation'&&target.searchParams.get('labelset_id')==='default';};
 let heldRequest:Request|undefined,refreshCalls=0,releaseRefresh!:()=>void,enterRefresh!:()=>void;
 const refreshHeld=new Promise<void>(resolve=>releaseRefresh=resolve),refreshEntered=new Promise<void>(resolve=>enterRefresh=resolve);
 const holdRefresh=async(route:Route)=>{if(!heldRequest&&historyURL(route.request())){heldRequest=route.request();refreshCalls++;enterRefresh();await refreshHeld;}await route.continue();};
 const choices=()=>page.evaluate(()=>Object.fromEntries(Object.keys(localStorage).filter(key=>key.startsWith('vision-evaluation-record:')).sort().map(key=>[key,localStorage.getItem(key)])));
 const beforeCancelChoices=await choices();let cancelledRefresh:any;
 await page.route(failedQuery,holdRefresh);
 const lateRefresh=page.waitForResponse(response=>!!heldRequest&&response.request()===heldRequest,{timeout:remaining()});
 try{
  await history.getByRole('button',{name:'평가 이력 새로고침',exact:true}).click({timeout:remaining()});await withinFrame(refreshEntered,cancelFrame);
  await expect(history.getByRole('status')).toContainText('평가 기록 확인 중…',{timeout:remaining()});
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click({timeout:remaining()});await expect(history).toHaveCount(0,{timeout:remaining()});
  releaseRefresh();const actual=await withinFrame(lateRefresh,cancelFrame);expect(actual.status()).toBe(200);const body=await withinFrame(actual.json(),cancelFrame);await withinFrame(actual.finished(),cancelFrame);
  const expected=fixture.items.filter((item:any)=>item.labelset_id==='default'&&item.record.result.task==='segmentation').map((item:any)=>item.record).sort((a:any,b:any)=>a.evaluation_id.localeCompare(b.evaluation_id));
  expect(body.total).toBe(expected.length);expect([...body.items].sort((a:any,b:any)=>a.evaluation_id.localeCompare(b.evaluation_id))).toEqual(expected);expect(refreshCalls).toBe(1);expect(await withinFrame(choices(),cancelFrame)).toEqual(beforeCancelChoices);
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click({timeout:remaining()});await expect(selector).toHaveValue(selected.record.evaluation_id,{timeout:remaining()});await expect(identity.locator('pre')).toContainText(selected.record.evidence_sha256,{timeout:remaining()});
  expect(cancelFrame-Date.now()).toBeGreaterThan(0);cancelledRefresh={request:{method:heldRequest!.method(),url:heldRequest!.url()},status:actual.status(),response:body,view_unmounted_before_release:true,HTTP_abort_claimed:false,saved_choices_before:beforeCancelChoices,saved_choices_after:await withinFrame(choices(),cancelFrame),elapsed_ms:10_000-(cancelFrame-Date.now())};expect(cancelFrame-Date.now()).toBeGreaterThan(0);
 }finally{releaseRefresh();await page.unroute(failedQuery,holdRefresh);}
 await assertSelected(selected);await captureSelection('refresh-late-response-cancelled-view-reopened');
 // The refreshed exact report hands its pinned original to the existing
 // read-only viewer through one actual production evidence-image GET.
 const handoffFrame=Date.now()+10_000,handoffRemaining=()=>{const value=handoffFrame-Date.now();if(value<=0)throw Error('Owned evaluation action absolute10s deadline exhausted');return value;};
 const analysis=history.locator('details').filter({has:page.locator('summary').filter({hasText:'객체·픽셀·문자 오류와 분포 분석'})}).first();
 if(await analysis.getAttribute('open')===null)await analysis.locator('summary').first().click({timeout:handoffRemaining()});
 const originalButton=analysis.getByRole('button',{name:'원본 위에 평가 마스크 보기',exact:true});await expect(originalButton).toBeEnabled({timeout:handoffRemaining()});
 const imagePath=`/api/evaluation/history/${selected.record.evaluation_id}/evidence-image`;
 const imageReply=page.waitForResponse(response=>{const target=new URL(response.url());return response.request().method()==='GET'&&response.request().frame()===page.mainFrame()&&target.origin===apiOrigin&&target.pathname===imagePath&&target.searchParams.get('source_dataset_path')===source&&target.searchParams.get('task')==='segmentation'&&target.searchParams.get('image_path')===original;},{timeout:handoffRemaining()});
 await originalButton.click({timeout:handoffRemaining()});const actualImage=await withinFrame(imageReply,handoffFrame);expect(actualImage.status()).toBe(200);const imageBody=await withinFrame(actualImage.json(),handoffFrame);await withinFrame(actualImage.finished(),handoffFrame);
 expect(imageBody.image_path).toBe(original);expect(imageBody.image_sha256).toBe(sourceHash);expect(imageBody.evaluation_id).toBe(selected.record.evaluation_id);expect(imageBody.original_size).toEqual([64,64]);
 const viewer=page.getByRole('dialog',{name:'이미지 판정 근거 보기',exact:true});await expect(viewer).toBeVisible({timeout:handoffRemaining()});await expect(viewer).toContainText(selected.record.evaluation_id,{timeout:handoffRemaining()});await expect(viewer).toContainText(sourceHash,{timeout:handoffRemaining()});await expect(viewer).toContainText(original,{timeout:handoffRemaining()});await expect(viewer.getByLabel('근거 이미지 종류')).toHaveValue('original',{timeout:handoffRemaining()});
 const raster=await withinFrame(viewer.getByRole('img',{name:'평가 입력 원본',exact:true}).evaluate(async element=>{const image=element as HTMLImageElement;await image.decode();const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;const context=canvas.getContext('2d')!;context.drawImage(image,0,0);return{width:canvas.width,height:canvas.height,rgba:Array.from(context.getImageData(0,0,canvas.width,canvas.height).data)};}),handoffFrame);
 const expectedRGBA:number[]=[];for(let y=0;y<64;y++)for(let x=0;x<64;x++)expectedRGBA.push(x,y,60,255);expect([raster.width,raster.height]).toEqual([64,64]);expect(raster.rgba).toEqual(expectedRGBA);
 const refreshHandoff={request:{method:actualImage.request().method(),url:actualImage.url()},status:actualImage.status(),evaluation_id:imageBody.evaluation_id,image_path:imageBody.image_path,image_sha256:imageBody.image_sha256,original_size:imageBody.original_size,actual_decoded_rgba_sha256:sha(Buffer.from(raster.rgba)),read_only_viewer:true};
 await viewer.getByRole('button',{name:'저장 평가로 돌아가기',exact:true}).click({timeout:handoffRemaining()});await expect(viewer).toHaveCount(0,{timeout:handoffRemaining()});await expect(originalButton).toBeFocused({timeout:handoffRemaining()});await expect(selector).toHaveValue(selected.record.evaluation_id,{timeout:handoffRemaining()});await expect(identity.locator('pre')).toContainText(selected.record.evidence_sha256,{timeout:handoffRemaining()});
 expect(handoffFrame-Date.now()).toBeGreaterThan(0);await captureSelection('refreshed-record-original-viewer-return');
 await choose(other);await refresh();await expect(labelset).toHaveValue(secondSet);await assertSelected(other);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click();await assertSelected(other);await navigate();await expect(labelset).toHaveValue(secondSet);await assertSelected(other);
 await choose(selected);const report=selected.report_path,held=report+'.held';
 try{fs.renameSync(report,held);await refresh();await expect(history.getByRole('alert')).toContainText(selected.record.evaluation_id);await expect(selector).toHaveValue('');await expect(history.locator('summary').filter({hasText:'객체·픽셀·문자 오류와 분포 분석'})).toHaveCount(0);await captureSelection('missing-exact-record-refused');await selector.selectOption(alternate.record.evaluation_id);await assertSelected(alternate);}finally{if(fs.existsSync(held))fs.renameSync(held,report);}
 await refresh();await assertSelected(alternate);
 // Delay a real scoped GET; the server response is unchanged and is released after a new choice is visible.
 let release!:()=>void,entered!:()=>void;const gate=new Promise<void>(resolve=>release=resolve),started=new Promise<void>(resolve=>entered=resolve);const pattern=/\/api\/evaluation\/history\?/;
 await page.route(pattern,async route=>{if(new URL(route.request().url()).searchParams.get('labelset_id')===secondSet){entered();await gate;}await route.continue();});const delayed=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/evaluation/history'&&new URL(r.url()).searchParams.get('labelset_id')===secondSet);await labelset.selectOption(secondSet);await started;await labelset.selectOption('default');await assertSelected(alternate);release();expect((await delayed).status()).toBe(200);await page.unroute(pattern);await assertSelected(alternate);await expect(labelset).toHaveValue('default');
 const originScope=JSON.stringify([project.id,source,'segmentation','default','local','local']);await page.evaluate(({key,value})=>localStorage.setItem(key,JSON.stringify(value)),{key:'modu-evaluation-return:'+originScope,value:{evaluation_id:selected.record.evaluation_id,image_id:null,file_path:original}});await navigate();await assertSelected(selected);await expect(history).toHaveAttribute('open','');await navigate();await assertSelected(selected);
 await choose(classification);await group.selectOption('ground_truth');await navigate();await expect(family).toHaveValue('classification');await expect(labelset).toHaveValue('default');await assertSelected(classification);await expect(group).toHaveValue('ground_truth');
 const secondProject=await api('/api/project/create',{name:'Other selection project',task:'segmentation'});await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});await navigate('Other selection project');await expect(history).not.toHaveAttribute('open','');await open();await expect(family).toHaveValue('segmentation');await expect(labelset).toHaveValue('');await expect(selector).toHaveCount(0);await expect(history).toContainText('저장된 평가 이력이 없습니다.');
 await refresh();await expect(history).toContainText('저장된 평가 이력이 없습니다.');await expect(selector).toHaveCount(0);await expect(identity).toHaveCount(0);await expect(history.getByRole('alert')).toHaveCount(0);
 await history.getByRole('button',{name:'평가 이력 새로고침',exact:true}).scrollIntoViewIfNeeded();await evidence.screenshot(page,`${native?'native':'browser'}-empty-project-explicit-refresh`);
 await api('/api/project/open',{project_dir:project.project_dir});await navigate();await expect(history).toHaveAttribute('open','');await expect(family).toHaveValue('classification');await assertSelected(classification);await choose(selected);await assertSelected(selected);await captureSelection('original-project-choice-restored');
 for(const item of fixture.items){expect(sha(fs.readFileSync(item.report_path))).toBe(item.report_sha256);evidence.addFile(item.report_path);}for(const input of fixture.inputs){expect(sha(fs.readFileSync(input.path))).toBe(input.sha256);evidence.addFile(input.path);}expect(sha(fs.readFileSync(original))).toBe(sourceHash);expect(writes).toEqual([]);
 evidence.note('evaluation_selection',{fixture,project_id:project.id,other_project_id:secondProject.id,source_path:original,source_sha256:sourceHash,exact_refresh:true,exact_reload:true,second_labelset_reopened:true,missing_record_refused:true,manual_replacement_explicit:true,late_labelset_response_fenced:true,review_return_supersedes_manual:true,family_and_group_reopened:true,project_scope_isolated:true,original_project_choice_restored:true,readonly_writes:writes,controlled_reports_not_model_inference:true});
 evidence.note('evaluation_refresh_cancel_handoff',{action:'F059.native-saved-evaluation-refresh',cancel:cancelledRefresh,handoff:refreshHandoff,source_ui:true,source_electron:native,saved_reports_and_input_bytes_unchanged:true,original10s_absolute_frames:true,controlled_saved_reports:true,actual_model_inference:false,human_label_approval:false,model_quality_approved:false,target_execution_verified:false});
 evidence.note('evaluation_refresh_pending_dimensions',{action:'F059.native-saved-evaluation-refresh',empty:{project_id:secondProject.id,refresh_clicked:true,http_status:200,selector_absent:true,record_identity_absent:true,visible_empty_state:true},error:{controlled_http_status:503,visible_error:'Controlled evaluation refresh transport failure',stale_record_and_result_absent:true,reevaluation_disabled:true,explicit_retry_http_status:200,restored_evaluation_id:selected.record.evaluation_id,restored_evidence_sha256:selected.record.evidence_sha256},reopen:{actual_renderer_reload:true,project_id:project.id,task:'segmentation',labelset_id:'default',group:'lot',evaluation_id:selected.record.evaluation_id,evidence_sha256:selected.record.evidence_sha256},saved_report_and_input_bytes_unchanged:true,evaluation_training_job_writes:writes,controlled_saved_reports:true,controlled_transport_fixture:true,model_inference_executed:false,human_label_approval:false,model_quality_approved:false,target_execution_verified:false});
}
test('saved evaluation selection retains exact model and labelset and refuses unavailable records',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:OwnedApi=async(route,body,method)=>{const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(response.ok(),await response.text()).toBe(true);return response.json();};await exercise(page,workspace,evidence,api,false,renderer.url,renderer.origin);
});
test('native saved evaluation selection retains exact model and labelset and refuses unavailable records',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,backend=await electronSession.waitForBackend();const api:OwnedApi=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!response.ok)throw Error(`Owned selection API: HTTP ${response.status}`);return response.json();},{port:backend.port,route,body,method});await exercise(window,workspace,evidence,api,true,undefined,`http://127.0.0.1:${backend.port}`);
});
