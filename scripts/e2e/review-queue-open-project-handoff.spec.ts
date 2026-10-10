import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import type {Page,Request,Route} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
import {handoffApi,handoffHash,handoffWithin,handoffProject,handoffLateRead,
 handoffRoots,handoffAssertRoots,handoffSave,type HandoffApi} from './fixtures/remaining-project-handoff';
const harness=require('./fixtures/harness.cjs');
test.use({actionTimeout:10_000});

// Controlled reports and setup-only cursor moves are not model inference,
// human review, queue-create credit, or saved label approval.
function originalFile(file:string){
 const before=fs.lstatSync(file);expect(before.isSymbolicLink()).toBe(false);expect(before.isFile()).toBe(true);expect(before.nlink).toBe(1);
 const fields=(s:fs.Stats)=>[s.dev,s.ino,s.mode,s.nlink,s.size,s.mtimeMs,s.ctimeMs];
 const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);let failed=false;
 try{const opened=fs.fstatSync(fd);expect(fields(opened)).toEqual(fields(before));const bytes=fs.readFileSync(fd);
  expect(bytes.length).toBe(opened.size);expect(fields(fs.fstatSync(fd))).toEqual(fields(opened));expect(fields(fs.lstatSync(file))).toEqual(fields(opened));
  return {bytes,sha256:handoffHash(bytes),size:bytes.length};
 }catch(error){failed=true;throw error;}finally{try{fs.closeSync(fd);}catch(error){if(!failed)throw error;}}
}
const annotationRoute=(file:string)=>'/api/annotations/'+path.basename(file,'.png')+'?file_path='+encodeURIComponent(file);
async function prepare(api:HandoffApi,w:Workspace,tag:'A'|'B'){
 const source=path.join(w.root,'saved-queue-open-'+tag);fs.mkdirSync(source);
 const inputs=['error','disagreement','threshold'].map((name,i)=>{const file=path.join(source,name+'.png');
  fs.writeFileSync(file,png(64,3,(x,y)=>[x,y,(tag==='A'?40:140)+i]),{flag:'wx'});return {name,path:file,sha256:originalFile(file).sha256};});
 const project=await api('/api/project/create',{name:'Owned saved queue open handoff '+tag,task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation',validate_images:false});
 for(const row of inputs)await api('/api/annotations/save',{image_id:row.name,image_path:row.path,image_width:64,image_height:64,actor:'controlled-queue-open-fixture',
  annotations:[{id:'owned-'+tag+'-'+row.name,type:'bbox',label:'Defect',category_id:1,bbox:[2,3,12,13]}]});
 const seed=()=>JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/review_queue_reports.py'),w.root,project.project_dir,source],
  {cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
 const chosenOrigin=seed(),otherOrigin=seed();expect(chosenOrigin.controlled_reports_not_model_inference).toBe(true);expect(otherOrigin.controlled_reports_not_model_inference).toBe(true);
 expect(chosenOrigin.record.evaluation_id).not.toBe(otherOrigin.record.evaluation_id);
 const created=await api('/api/data-workbench/review-queues',{evaluation_id:chosenOrigin.record.evaluation_id,threshold:.5,margin:.05});
 expect(created.items.map((row:any)=>[row.relative_path,row.priority,row.state])).toEqual([['error.png',400,'pending'],['disagreement.png',200,'pending'],['threshold.png',100,'pending']]);
 const chosen=await api('/api/data-workbench/review-queues/'+created.id+'/advance',{expected_revision:1,relative_path:'error.png',state:'skipped',actor:'owned-setup-'+tag});
 expect(chosen).toMatchObject({cursor:1,revision:2});expect(chosen.history).toHaveLength(1);expect(chosen.history[0]).toMatchObject({relative_path:'error.png',state:'skipped',actor:'owned-setup-'+tag});
 const other=await api('/api/data-workbench/review-queues',{evaluation_id:otherOrigin.record.evaluation_id,threshold:.5,margin:.05});
 expect(other.id).not.toBe(chosen.id);expect(other.created_at).toBeGreaterThan(chosen.created_at);expect(other).toMatchObject({cursor:0,revision:1,history:[]});
 expect(await api('/api/data-workbench/review-queues')).toEqual({queues:[other,chosen]});
 const metadata=[],annotations:Record<string,any>={};
 await api('/api/team-data');await api('/api/team-data/readiness');await api('/api/project/preferences');await api('/api/project/labelsets');
 for(const row of inputs){const m=await api('/api/dataset/metadata/image?image_path='+encodeURIComponent(row.path));
  expect(m.file_path).toBe(row.path);expect(m.content_hash).toBe(row.sha256);expect(m.image_uuid).toBeTruthy();expect(m.workflow_state).not.toBe('approved');metadata.push(m);
  annotations[row.path]=await api(annotationRoute(row.path));expect(annotations[row.path].annotations).toHaveLength(1);}
 const current=await api('/api/project/current');expect(current.id).toBe(project.id);expect(current.source_dataset_dir).toBe(source);
 return {tag,source,project:current,inputs,metadata,annotations,chosen,other,chosenOrigin,otherOrigin};
}
type Scope=Awaited<ReturnType<typeof prepare>>;
async function exercise(page:Page,w:Workspace,e:Evidence,native:boolean,origin:string,url?:string){
 const api=handoffApi(page,origin,native),A=await prepare(api,w,'A'),B=await prepare(api,w,'B');
 expect(A.project.id).not.toBe(B.project.id);expect(A.metadata.some(a=>B.metadata.some(b=>a.image_uuid===b.image_uuid))).toBe(false);
 const requests:Array<{request:Request;started:number;deadline:number}>=[];
 const started=(request:Request)=>{const u=new URL(request.url());if(request.method()==='GET'&&u.origin===origin&&request.frame()===page.mainFrame()){
  const now=performance.now();requests.push({request,started:now,deadline:now+10_000});}};
 const pendingReads=new Set<Promise<{ok:true;value:any}|{ok:false;error:unknown}>>();
 const wire=(pathname:string,query:(u:URL)=>boolean,deadline:number,scope:Scope)=>{
  const pending=page.waitForRequest(r=>{const u=new URL(r.url());return r.frame()===page.mainFrame()&&r.method()==='GET'&&u.origin===origin&&u.pathname===pathname&&query(u);},{timeout:Math.max(1,Math.floor(deadline-performance.now()))});
  const observation=(async()=>{const request=await handoffWithin(pending,deadline,'original owning GET start');const timing=requests.find(row=>row.request===request);expect(timing).toBeTruthy();
   const bound=Math.min(deadline,timing!.deadline),projectHeader=await handoffWithin(request.headerValue('x-vision-project'),bound,'original project header'),contextHeader=await handoffWithin(request.headerValue('x-vision-context'),bound,'original context header');
   if(!pathname.startsWith('/api/dataset/raw/')){expect(projectHeader).toBe(scope.project.id);expect(contextHeader).not.toBeNull();expect(JSON.parse(contextHeader!).project_id).toBe(scope.project.id);}
   const response=await handoffWithin(request.response(),bound,'owning GET response');expect(response).not.toBeNull();expect(response!.request()).toBe(request);expect(response!.status()).toBe(200);
   const bytes=await handoffWithin(response!.body(),bound,'owning complete raw body');expect(bytes.length).toBeLessThanOrEqual(1024*1024);expect(await handoffWithin(response!.finished(),bound,'owning GET finished')).toBeNull();
   return {bytes,proof:{method:'GET',origin,path:pathname,owning_project_id:scope.project.id,request_project_header:projectHeader,request_context_project_id:contextHeader===null?null:JSON.parse(contextHeader).project_id,query_sha256:handoffHash(new URL(request.url()).search),main_frame:true,status:200,
    request_started_ms:timing!.started,absolute_deadline_ms:bound,finished_ms:performance.now(),response_sha256:handoffHash(bytes),bytes:bytes.length}};})();
  // Attach both terminal handlers before the normal click starts. A click
  // failure cannot leave an unhandled original request-wait rejection.
  const outcome=observation.then(value=>({ok:true as const,value}),error=>({ok:false as const,error}));pendingReads.add(outcome);
  return async()=>{const result=await outcome;pendingReads.delete(outcome);if(!result.ok)throw result.error;return result.value;};
 };
 const panel=()=>page.getByRole('region',{name:'저장된 검토 큐',exact:true});
 const choice=()=>panel().getByLabel('저장 검토 큐 선택',{exact:true});
 const enter=async(scope:Scope)=>{const deadline=performance.now()+10_000;
  await handoffWithin(page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click(),deadline,'labeling entry');
  const focus=page.getByRole('button',{name:'집중 편집',exact:true});if(await focus.getAttribute('aria-pressed')==='true')await handoffWithin(focus.click(),deadline,'ordinary focus exit');
  const summary=page.getByText('저장 검토 큐 · 오류·불일치·임계값 우선',{exact:true});if(await summary.locator('..').getAttribute('open')===null)await handoffWithin(summary.click(),deadline,'queue details open');
  await handoffWithin(expect(panel()).toBeVisible({timeout:Math.max(1,Math.floor(deadline-performance.now()))}),deadline,'own queue panel');
  await handoffWithin(expect(choice().locator('option[value="'+scope.chosen.id+'"]')).toHaveCount(1,{timeout:Math.max(1,Math.floor(deadline-performance.now()))}),deadline,'chosen option');
  return panel();};
 const selected=async(scope:Scope)=>{await expect(choice()).toHaveValue(scope.chosen.id,{timeout:10_000});await expect(panel()).toContainText('검토 진행 1 / 3',{timeout:10_000});
  const foreign=scope.tag==='A'?B:A;for(const q of[foreign.chosen,foreign.other])await expect(choice().locator('option[value="'+q.id+'"]')).toHaveCount(0);
  expect((await api('/api/data-workbench/review-queues')).queues).toEqual([scope.other,scope.chosen]);};
 const assertImage=async(scope:Scope)=>{const target=scope.chosen.items[scope.chosen.cursor],image=scope.metadata.find(row=>row.file_path===target.file_path);expect(image).toBeTruthy();
  const filmstrip=page.locator('[data-labeling-filmstrip]'),thumb=filmstrip.getByRole('img',{name:path.basename(target.file_path),exact:true});
  await expect(thumb.locator('..')).toHaveClass(/border-blue-500/,{timeout:10_000});expect(new URL((await thumb.getAttribute('src'))!,page.url()).searchParams.get('file_path')).toBe(target.file_path);
  await expect(page.getByTestId('annotation-save-button')).toHaveText('Saved',{timeout:10_000});return {queue_id:scope.chosen.id,cursor:scope.chosen.cursor,revision:scope.chosen.revision,
   file_path:target.file_path,image_uuid:image.image_uuid,image_revision:image.revision,source_sha256:target.source_sha256};};
 const rawPattern=origin+'/api/dataset/raw/**',fallback=(route:Route)=>route.fallback();
 const openImage=async(scope:Scope)=>{
  // Choose another genuine thumbnail first so the normal open performs fresh
  // owning raw/annotation reads rather than relying on the existing selection.
  await page.locator('[data-labeling-filmstrip]').getByRole('img',{name:'threshold.png',exact:true}).click();
  const target=scope.chosen.items[scope.chosen.cursor],deadline=performance.now()+10_000;
  const queue=wire('/api/data-workbench/review-queues/'+scope.chosen.id,()=>true,deadline,scope),raw=wire('/api/dataset/raw/'+encodeURIComponent(path.basename(target.file_path)),u=>u.searchParams.get('file_path')===target.file_path,deadline,scope),labels=wire('/api/annotations/'+path.basename(target.file_path,'.png'),u=>u.searchParams.get('file_path')===target.file_path,deadline,scope);
  await handoffWithin(panel().getByRole('button',{name:'현재 항목 열기',exact:true}).click(),deadline,'actual current-item button');
  const [q,r,l]=await Promise.all([queue(),raw(),labels()]);expect(JSON.parse(q.bytes.toString('utf8'))).toEqual(scope.chosen);expect(r.bytes).toEqual(originalFile(target.file_path).bytes);expect(handoffHash(r.bytes)).toBe(target.source_sha256);
  expect(JSON.parse(l.bytes.toString('utf8'))).toEqual(scope.annotations[target.file_path]);const image=scope.metadata.find(row=>row.file_path===target.file_path);
  expect(JSON.parse(l.bytes.toString('utf8')).metadata).toMatchObject({file_path:target.file_path,content_hash:target.source_sha256,image_uuid:image.image_uuid,revision:image.revision});return {queue:q.proof,raw:r.proof,annotations:l.proof,selected:await assertImage(scope)};};
 const reload=async(scope:Scope)=>{const deadline=performance.now()+10_000,catalog=wire('/api/data-workbench/review-queues',()=>true,deadline,scope);
  await handoffWithin(url?page.goto(url):page.reload(),deadline,'actual document reopen');await enter(scope);const c=await catalog();expect(JSON.parse(c.bytes.toString('utf8'))).toEqual({queues:[scope.other,scope.chosen]});await selected(scope);return c.proof;};
 const readbacks=async(scope:Scope)=>{const result:Record<string,unknown>={};for(const endpoint of['/api/team-data','/api/team-data/readiness','/api/project/preferences','/api/project/labelsets','/api/dataset/versions','/api/dataset/metadata/split','/api/dataset/metadata?limit=100','/api/data-workbench/review-evaluations'])result[endpoint]=await api(endpoint);
  for(const row of scope.inputs)result[annotationRoute(row.path)]=await api(annotationRoute(row.path));
  result.queues=await api('/api/data-workbench/review-queues');for(const q of[scope.chosen,scope.other])result[q.id]=await api('/api/data-workbench/review-queues/'+q.id);return result;};
 const state=async(scope:Scope)=>{const originalReadbacks=await readbacks(scope);
  const remembered=await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.startsWith('modu-review-queue:')));
  const owning=remembered.filter(([key])=>{const saved=JSON.parse(key.slice('modu-review-queue:'.length));
   return saved[0]===scope.project.project_dir&&saved[1]===scope.project.id&&saved[2]===scope.project.task&&saved[3]===scope.source&&saved[4]==='default';});
  expect(owning).toHaveLength(1);expect(owning[0][1]).toBe(scope.chosen.id);expect(owning[0][1]).not.toBe(scope.other.id);
  return {roots:handoffRoots(scope),readbacks:originalReadbacks,remembered_queue:owning[0],project_json:originalFile(path.join(scope.project.project_dir,'project.json')).sha256,
  labelsets_json:originalFile(path.join(scope.project.project_dir,'labelsets.json')).sha256};};
 const writes:Array<{method:string;path:string;body:unknown}>=[],observe=(request:Request)=>{const u=new URL(request.url());if(u.origin===origin&&u.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(request.method()))writes.push({method:request.method(),path:u.pathname,body:request.postDataJSON()});};
 let late:Awaited<ReturnType<typeof handoffLateRead>>|undefined,failed=false;
 try{
  page.on('request',started);await page.route(rawPattern,fallback);
  if(url)await page.goto(url);else await page.reload();await enter(B);await choice().selectOption(B.chosen.id);await selected(B);await openImage(B);
  await handoffProject(page,A,origin);await enter(A);await choice().selectOption(A.chosen.id);await selected(A);await openImage(A);
  await handoffProject(page,B,origin);await enter(B);await selected(B);const beforeB=await state(B);
  await handoffProject(page,A,origin);await enter(A);await selected(A);const beforeA=await state(A);expect(beforeA.remembered_queue[0]).not.toBe(beforeB.remembered_queue[0]);page.on('request',observe);
  const A_reopen=await reload(A),A_open=await openImage(A);await e.screenshot(page,`${native?'source-electron':'browser'}-saved-queue-A-nondefault-reopen-original-open`);
  late=await handoffLateRead(page,origin,native,u=>u.pathname==='/api/data-workbench/review-queues/'+A.chosen.id);
  await panel().getByRole('button',{name:'현재 항목 열기',exact:true}).click();const oldOpen=await late.ready();expect(oldOpen.body).toEqual(A.chosen);
  const open_toB=await handoffProject(page,B,origin);await enter(B);await selected(B);const B_open=await openImage(B),oldOpenOutcome=await late.finish();await selected(B);expect(await assertImage(B)).toEqual(B_open.selected);
  await late.close();late=undefined;await e.screenshot(page,`${native?'source-electron':'browser'}-saved-queue-B-after-old-A-open-reply`);
  const B_reopen=await reload(B);const open_toA=await handoffProject(page,A,origin);await enter(A);await selected(A);const A_return_open=await openImage(A);
  await e.screenshot(page,`${native?'source-electron':'browser'}-saved-queue-return-A-cursor-history-original`);
  late=await handoffLateRead(page,origin,native,u=>u.pathname==='/api/data-workbench/review-queues');
  if(url)await page.goto(url);else await page.reload();const oldCatalog=await late.ready();expect(oldCatalog.body).toEqual({queues:[A.other,A.chosen]});
  const selected_toB=await handoffProject(page,B,origin);await enter(B);await selected(B);const B_selected_open=await openImage(B),oldCatalogOutcome=await late.finish();await selected(B);expect(await assertImage(B)).toEqual(B_selected_open.selected);
  await late.close();late=undefined;await e.screenshot(page,`${native?'source-electron':'browser'}-saved-queue-B-after-old-A-catalog-reply`);
  const B_selected_reopen=await reload(B);const selected_toA=await handoffProject(page,A,origin);await enter(A);await selected(A);const A_selected_reopen=await reload(A),finalA=await openImage(A);
  await e.screenshot(page,`${native?'source-electron':'browser'}-saved-queue-A-remembered-not-newest-after-B-reopen`);
  const afterA=await state(A);handoffAssertRoots(beforeA.roots,afterA.roots);expect(afterA).toEqual(beforeA);
  const toB_final=await handoffProject(page,B,origin);await enter(B);await selected(B);const afterB=await state(B);handoffAssertRoots(beforeB.roots,afterB.roots);expect(afterB).toEqual(beforeB);
  const toA_final=await handoffProject(page,A,origin);await enter(A);await selected(A);expect(await state(A)).toEqual(beforeA);
  const transitions=[open_toB,open_toA,selected_toB,selected_toA,toB_final,toA_final];expect(writes).toEqual(transitions.map(t=>({method:'POST',path:'/api/project/open',body:{project_dir:t.project_dir}})));
  for(const scope of[A,B]){for(const row of scope.inputs)expect(originalFile(row.path).sha256).toBe(row.sha256);for(const record of[scope.chosenOrigin,scope.otherOrigin])expect(originalFile(record.path).sha256).toBe(record.sha256);}
  const proof={cells:['U015.saved-queue-open.handoff','U015.saved-queue-selected-reopen.handoff'],projects:[A.project.id,B.project.id,A.project.id],
   queue_ids:{A:A.chosen.id,A_default_newest:A.other.id,B:B.chosen.id,B_default_newest:B.other.id},nondefault_selection_explicit:true,
   initial_setup_only_cursor_history:{A:A.chosen,B:B.chosen},A_reopen,A_open,open_toB,B_open,oldOpenOutcome,B_reopen,open_toA,A_return_open,
   selected_toB,B_selected_open,oldCatalogOutcome,B_selected_reopen,selected_toA,A_selected_reopen,finalA,toB_final,toA_final,
   old_read_captured:{open:oldOpen.sha256,catalog:oldCatalog.sha256},full_original_states:{A:beforeA,B:beforeB},full_after_states:{A:afterA,B:afterB},writes,
   original_labels_reports_UUIDs_source_bytes_cursor_revision_history_preserved:true,controlled_saved_reports_not_model_inference:true,
   native_held_reply_snapshot_transport:native?'separate_authenticated_renderer_GET_controlled_original_UI_reply':'original_browser_request_actual_response',
   actual_training_inference_GPU_human_quality_installed_target_parent_approval:false};handoffSave(e,w,'saved-queue-open-reopen-handoff-proof',proof);e.note('saved_queue_open_selected_reopen_project_handoff',proof);
 }catch(error){failed=true;throw error;}finally{
  let cleanup:unknown,cleanupFailed=false;for(const close of[()=>late?.close(),async()=>{let first:unknown,hasFailure=false;for(const pending of pendingReads){const outcome=await pending;pendingReads.delete(pending);if(!outcome.ok&&!hasFailure){hasFailure=true;first=outcome.error;}}if(hasFailure)throw first;},()=>page.isClosed()?undefined:page.unroute(rawPattern,fallback)]){
   try{await close();}catch(error){if(!cleanupFailed){cleanupFailed=true;cleanup=error;}}}
  page.off('request',observe);page.off('request',started);if(cleanupFailed){try{e.note('saved_queue_handoff_secondary_cleanup',{type:cleanup instanceof Error?cleanup.name:typeof cleanup});}catch{}if(!failed)throw cleanup;}
 }
}
test('saved queue open and remembered nondefault selection stay scoped through A B A project handoff',async({page,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);await exercise(page,workspace,evidence,false,renderer.origin,renderer.url);
});
test('native saved queue open fences old replies and reopens only the owning remembered queue',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,backend=await electronSession.waitForBackend();await exercise(page,workspace,evidence,true,`http://127.0.0.1:${backend.port}`);
});

// One additive browser case. The complete original two-case prefix above is unchanged.
async function exerciseQueueAdvanceHandoff(page:Page,w:Workspace,e:Evidence,origin:string,url:string){
 const api=handoffApi(page,origin,false),A=await prepare(api,w,'A'),B=await prepare(api,w,'B');
 expect(A.project.id).not.toBe(B.project.id);expect(A.metadata.some(a=>B.metadata.some(b=>a.image_uuid===b.image_uuid))).toBe(false);
 const expected=new Map<Scope,any>([[A,structuredClone(A.chosen)],[B,structuredClone(B.chosen)]]);
 const actor=(scope:Scope)=>'Owned queue advance handoff '+scope.tag;
 const panel=()=>page.getByRole('region',{name:'저장된 검토 큐',exact:true});
 const choice=()=>panel().getByLabel('저장 검토 큐 선택',{exact:true});
 const rawPattern=origin+'/api/dataset/raw/**',fallback=(route:Route)=>route.fallback();
 const timings=new Map<Request,{started:number;deadline:number}>(),pending=new Set<Promise<{ok:true;value:any}|{ok:false;error:unknown}>>();
 const writes:Array<{method:string;path:string;body:unknown}>=[],allowedWrites:typeof writes=[];
 const started=(request:Request)=>{const u=new URL(request.url());if(u.origin===origin&&request.frame()===page.mainFrame()){
  const now=performance.now();timings.set(request,{started:now,deadline:now+10_000});}};
 const observe=(request:Request)=>{const u=new URL(request.url());if(u.origin===origin&&u.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(request.method())){
  expect(request.frame()).toBe(page.mainFrame());writes.push({method:request.method(),path:u.pathname,body:request.postDataJSON()});}};
 const wire=(scope:Scope,method:'GET'|'POST',pathname:string,query:(u:URL)=>boolean,deadline:number,body?:unknown)=>{
  const waiting=page.waitForRequest(request=>{const u=new URL(request.url());return request.frame()===page.mainFrame()&&request.method()===method&&u.origin===origin&&u.pathname===pathname&&query(u);},
   {timeout:Math.max(1,Math.floor(deadline-performance.now()))});
  const operation=(async()=>{const request=await handoffWithin(waiting,deadline,'owning queue request start'),timing=timings.get(request);expect(timing).toBeTruthy();
   const bound=Math.min(deadline,timing!.deadline),projectHeader=await handoffWithin(request.headerValue('x-vision-project'),bound,'owning project header'),contextHeader=await handoffWithin(request.headerValue('x-vision-context'),bound,'owning context header');
   if(!pathname.startsWith('/api/dataset/raw/')){expect(projectHeader).toBe(scope.project.id);expect(contextHeader).not.toBeNull();expect(JSON.parse(contextHeader!).project_id).toBe(scope.project.id);}
   if(method==='POST')expect(request.postDataJSON()).toEqual(body);
   const response=await handoffWithin(request.response(),bound,'owning queue response');expect(response).not.toBeNull();expect(response!.request()).toBe(request);expect(response!.status()).toBe(200);
   const bytes=await handoffWithin(response!.body(),bound,'owning complete response bytes');expect(bytes.length).toBeLessThanOrEqual(1024*1024);
   expect(await handoffWithin(response!.finished(),bound,'owning response complete')).toBeNull();
   return {bytes,proof:{method,origin,path:pathname,body:body??null,owning_project_id:scope.project.id,owning_source:scope.source,
    project_header:projectHeader,context_project_id:contextHeader===null?null:JSON.parse(contextHeader).project_id,query_sha256:handoffHash(new URL(request.url()).search),
    main_frame:true,status:200,request_started_ms:timing!.started,absolute_deadline_ms:bound,finished_ms:performance.now(),sha256:handoffHash(bytes),size:bytes.length}};})();
  const outcome=operation.then(value=>({ok:true as const,value}),error=>({ok:false as const,error}));pending.add(outcome);
  return async()=>{const result=await outcome;pending.delete(outcome);if(!result.ok)throw result.error;return result.value;};
 };
 const enter=async(scope:Scope)=>{const deadline=performance.now()+10_000;
  await handoffWithin(page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click(),deadline,'labeling entry');
  const focus=page.getByRole('button',{name:'집중 편집',exact:true});if(await handoffWithin(focus.getAttribute('aria-pressed'),deadline,'ordinary focus state')==='true')await handoffWithin(focus.click(),deadline,'ordinary focus exit');
  const summary=page.getByText('저장 검토 큐 · 오류·불일치·임계값 우선',{exact:true});if(await handoffWithin(summary.locator('..').getAttribute('open'),deadline,'queue details state')===null)await handoffWithin(summary.click(),deadline,'queue details open');
  await handoffWithin(expect(panel()).toBeVisible({timeout:Math.max(1,Math.floor(deadline-performance.now()))}),deadline,'owning queue panel');
  await handoffWithin(expect(choice().locator('option[value="'+scope.chosen.id+'"]')).toHaveCount(1,{timeout:Math.max(1,Math.floor(deadline-performance.now()))}),deadline,'owning queue option');
 };
 const selected=async(scope:Scope)=>{const queue=expected.get(scope),foreign=scope===A?B:A;
  await expect(choice()).toHaveValue(queue.id,{timeout:10_000});await expect(panel()).toContainText('검토 진행 '+queue.cursor+' / 3',{timeout:10_000});
  for(const q of[foreign.chosen,foreign.other])await expect(choice().locator('option[value="'+q.id+'"]')).toHaveCount(0);
  expect(await api('/api/project/current')).toEqual(scope.project);expect(await api('/api/data-workbench/review-queues')).toEqual({queues:[scope.other,queue]});
  expect(await api('/api/data-workbench/review-queues/'+queue.id)).toEqual(queue);
 };
 const image=async(scope:Scope,target:any,raw:any,labels:any)=>{expect(raw.bytes).toEqual(originalFile(target.file_path).bytes);expect(handoffHash(raw.bytes)).toBe(target.source_sha256);
  const annotation=JSON.parse(labels.bytes.toString('utf8'));expect(annotation).toEqual(scope.annotations[target.file_path]);const metadata=scope.metadata.find(row=>row.file_path===target.file_path);expect(metadata).toBeTruthy();
  expect(annotation.metadata).toMatchObject({file_path:target.file_path,content_hash:target.source_sha256,image_uuid:metadata.image_uuid,revision:metadata.revision});
  const thumb=page.locator('[data-labeling-filmstrip]').getByRole('img',{name:path.basename(target.file_path),exact:true});await expect(thumb.locator('..')).toHaveClass(/border-blue-500/,{timeout:10_000});
  expect(new URL((await thumb.getAttribute('src'))!,page.url()).searchParams.get('file_path')).toBe(target.file_path);await expect(page.getByTestId('annotation-save-button')).toHaveText('Saved',{timeout:10_000});
  return {raw:raw.proof,annotations:labels.proof,file_path:target.file_path,image_uuid:metadata.image_uuid,image_revision:metadata.revision,source_sha256:target.source_sha256};
 };
 const imageReads=(scope:Scope,target:any,deadline:number)=>({
  raw:wire(scope,'GET','/api/dataset/raw/'+encodeURIComponent(path.basename(target.file_path)),u=>u.searchParams.get('file_path')===target.file_path,deadline),
  annotations:wire(scope,'GET','/api/annotations/'+path.basename(target.file_path,'.png'),u=>u.searchParams.get('file_path')===target.file_path,deadline)});
 const openCurrent=async(scope:Scope)=>{const queue=expected.get(scope);if(queue.cursor===queue.items.length){await expect(panel()).toContainText('큐의 모든 항목을 검토했습니다.');
   for(const name of['현재 항목 열기','검토 완료 · 다음','보류 · 다음'])await expect(panel().getByRole('button',{name,exact:true})).toBeDisabled();return null;}
  await page.locator('[data-labeling-filmstrip]').getByRole('img',{name:'error.png',exact:true}).click();
  const target=queue.items[queue.cursor],deadline=performance.now()+10_000,q=wire(scope,'GET','/api/data-workbench/review-queues/'+queue.id,()=>true,deadline),reads=imageReads(scope,target,deadline);
  await handoffWithin(panel().getByRole('button',{name:'현재 항목 열기',exact:true}).click(),deadline,'actual owning current-item open');
  const [actual,raw,labels]=await Promise.all([q(),reads.raw(),reads.annotations()]);expect(JSON.parse(actual.bytes.toString('utf8'))).toEqual(queue);
  return {queue:actual.proof,image:await image(scope,target,raw,labels)};
 };
 const setActor=async(scope:Scope)=>{const deadline=performance.now()+10_000,toggle=page.getByRole('button',{name:'이미지 정보·검토',exact:true});
  await handoffWithin(toggle.click(),deadline,'reviewer information open');await handoffWithin(page.getByRole('textbox',{name:'작업자·검토자 이름',exact:true}).fill(actor(scope)),deadline,'owning synthetic reviewer');
  await handoffWithin(toggle.click(),deadline,'reviewer information close');};
 const readbacks=async(scope:Scope)=>{const values:Record<string,unknown>={};for(const endpoint of['/api/team-data','/api/team-data/readiness','/api/project/preferences','/api/project/labelsets','/api/dataset/versions','/api/dataset/metadata/split','/api/dataset/metadata?limit=100','/api/data-workbench/review-evaluations'])values[endpoint]=await api(endpoint);
  for(const row of scope.inputs)values[annotationRoute(row.path)]=await api(annotationRoute(row.path));return values;};
 const state=async(scope:Scope)=>{const values=await readbacks(scope),queues=await api('/api/data-workbench/review-queues'),chosen=await api('/api/data-workbench/review-queues/'+scope.chosen.id),other=await api('/api/data-workbench/review-queues/'+scope.other.id);
  expect(queues).toEqual({queues:[scope.other,expected.get(scope)]});expect(chosen).toEqual(expected.get(scope));expect(other).toEqual(scope.other);
  const remembered=await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.startsWith('modu-review-queue:'))),own=remembered.filter(([key])=>{const saved=JSON.parse(key.slice('modu-review-queue:'.length));return saved[0]===scope.project.project_dir&&saved[1]===scope.project.id&&saved[2]===scope.project.task&&saved[3]===scope.source&&saved[4]==='default';});
  expect(own).toHaveLength(1);expect(own[0][1]).toBe(scope.chosen.id);return {roots:handoffRoots(scope),readbacks:values,queues,chosen,other,remembered:own[0],
   project_json:originalFile(path.join(scope.project.project_dir,'project.json')).sha256,labelsets_json:originalFile(path.join(scope.project.project_dir,'labelsets.json')).sha256};};
 const before=new Map<Scope,Awaited<ReturnType<typeof state>>>(),queueMembers=new Map<Scope,string>();
 const canonical=(value:any):any=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
 const assertState=async(scope:Scope)=>{await selected(scope);const actual=await state(scope),baseline=before.get(scope)!,member=queueMembers.get(scope)!;
  handoffAssertRoots(baseline.roots,actual.roots,{dataset:[member]});expect(actual.roots.dataset.snapshot.files[member]).toEqual({size:Buffer.byteLength(JSON.stringify(canonical(expected.get(scope)))),sha256:handoffHash(JSON.stringify(canonical(expected.get(scope))))});
  expect(originalFile(path.join(scope.project.dataset_dir,member)).bytes).toEqual(Buffer.from(JSON.stringify(canonical(expected.get(scope)))));
  expect({...actual,roots:baseline.roots,queues:baseline.queues,chosen:baseline.chosen}).toEqual(baseline);return actual;};
 const advance=async(scope:Scope,status:'reviewed'|'skipped')=>{const prior=expected.get(scope),index=prior.cursor,target=prior.items[index],nextTarget=prior.items[index+1],endpoint='/api/data-workbench/review-queues/'+prior.id+'/advance';
  expect(prior.scope).toEqual({source:scope.source,task:scope.project.task,labelset_id:'default'});expect(target.file_path.startsWith(scope.source+path.sep)).toBe(true);
  const body={expected_revision:prior.revision,relative_path:target.relative_path,state:status,actor:actor(scope)},deadline=performance.now()+10_000,response=wire(scope,'POST',endpoint,()=>true,deadline,body),reads=nextTarget?imageReads(scope,nextTarget,deadline):null;
  allowedWrites.push({method:'POST',path:endpoint,body});const button=panel().getByRole('button',{name:status==='reviewed'?'검토 완료 · 다음':'보류 · 다음',exact:true});
  await handoffWithin(expect(button).toBeEnabled({timeout:Math.max(1,Math.floor(deadline-performance.now()))}),deadline,'owning advance enabled');await handoffWithin(button.click(),deadline,'actual owning advance button');
  const received=await response(),saved=JSON.parse(received.bytes.toString('utf8')),next=structuredClone(prior);expect(saved.items[index].reviewed_at).toEqual(expect.any(Number));expect(Number.isFinite(saved.items[index].reviewed_at)).toBe(true);
  expect(saved.history[index].at).toEqual(expect.any(Number));expect(Number.isFinite(saved.history[index].at)).toBe(true);
  next.items[index]={...next.items[index],state:status,actor:actor(scope),reviewed_at:saved.items[index].reviewed_at};next.cursor++;next.revision++;next.history.push({relative_path:target.relative_path,state:status,actor:actor(scope),at:saved.history[index].at});
  expect(saved).toEqual(next);expected.set(scope,next);const responseFile=path.join(w.logs,'queue-advance-'+scope.tag+'-'+status+'-actual-response.json');fs.writeFileSync(responseFile,received.bytes,{flag:'wx'});e.addFile(responseFile);
  let nextImage:any=null;if(reads){const [raw,labels]=await Promise.all([reads.raw(),reads.annotations()]);nextImage=await image(scope,nextTarget,raw,labels);}
  await selected(scope);if(!nextTarget)await openCurrent(scope);expect(writes).toEqual(allowedWrites);const after=await assertState(scope);
  await e.screenshot(page,'browser-queue-advance-'+scope.tag+'-'+status+'-owning-state');return {project_id:scope.project.id,source:scope.source,queue_id:prior.id,old_cursor:prior.cursor,old_revision:prior.revision,state:status,
   request:received.proof,response_file:responseFile,complete_saved_queue:saved,next_original_image:nextImage,after};};
 const transition=async(scope:Scope)=>{allowedWrites.push({method:'POST',path:'/api/project/open',body:{project_dir:scope.project.project_dir}});const proof=await handoffProject(page,scope,origin);
  await enter(scope);await selected(scope);await setActor(scope);const opened=await openCurrent(scope),retained=await assertState(scope);expect(writes).toEqual(allowedWrites);return {project_open:proof,opened,retained};};
 let failed=false;
 try{
  page.on('request',started);await page.route(rawPattern,fallback);await page.goto(url);await enter(B);await choice().selectOption(B.chosen.id);await selected(B);await openCurrent(B);await setActor(B);before.set(B,await state(B));
  await handoffProject(page,A,origin);await enter(A);await choice().selectOption(A.chosen.id);await selected(A);await openCurrent(A);await setActor(A);before.set(A,await state(A));
  for(const scope of[A,B]){const baseline=before.get(scope)!,members=Object.keys(baseline.roots.dataset.snapshot.files).filter(name=>name.endsWith('/review_queues/'+scope.chosen.id+'.json'));expect(members).toHaveLength(1);queueMembers.set(scope,members[0]);
   const lock=path.posix.join(path.posix.dirname(path.posix.dirname(members[0])),'review_queue.lock');expect(baseline.roots.dataset.snapshot.files[lock]).toEqual({size:0,sha256:handoffHash(Buffer.alloc(0))});
   expect(baseline.roots.dataset.snapshot.files[members[0]]).toEqual({size:Buffer.byteLength(JSON.stringify(canonical(scope.chosen))),sha256:handoffHash(JSON.stringify(canonical(scope.chosen)))});}
  expect(before.get(A)!.remembered[0]).not.toBe(before.get(B)!.remembered[0]);handoffSave(e,w,'saved-queue-advance-handoff-before',{A:before.get(A),B:before.get(B)});page.on('request',observe);
  const A_review=await advance(A,'reviewed'),toB=await transition(B),B_skip=await advance(B,'skipped'),toA=await transition(A),A_skip=await advance(A,'skipped'),backB=await transition(B),B_review=await advance(B,'reviewed'),backA=await transition(A);
  const afterA=await assertState(A);expect(afterA.chosen).toMatchObject({cursor:3,revision:4});expect(afterA.chosen.history.map((row:any)=>[row.state,row.actor])).toEqual([['skipped','owned-setup-A'],['reviewed',actor(A)],['skipped',actor(A)]]);
  const finalB=await transition(B),afterB=await assertState(B);expect(afterB.chosen).toMatchObject({cursor:3,revision:4});expect(afterB.chosen.history.map((row:any)=>[row.state,row.actor])).toEqual([['skipped','owned-setup-B'],['skipped',actor(B)],['reviewed',actor(B)]]);
  const finalA=await transition(A);expect(await assertState(A)).toEqual(afterA);expect(writes).toEqual(allowedWrites);expect(writes).toHaveLength(10);
  for(const scope of[A,B]){for(const row of scope.inputs)expect(originalFile(row.path).sha256).toBe(row.sha256);for(const record of[scope.chosenOrigin,scope.otherOrigin])expect(originalFile(record.path).sha256).toBe(record.sha256);}
  await e.screenshot(page,'browser-queue-advance-return-A-complete-own-history');const proof={cells:['U015.saved-queue-review-next.handoff','U015.saved-queue-skip-next.handoff'],
   projects:[A.project.id,B.project.id,A.project.id],initial_setup_skip_not_UI_credit:true,controlled_reports_not_model_inference:true,queue_ids:{A:A.chosen.id,B:B.chosen.id,A_unmodified:A.other.id,B_unmodified:B.other.id},
   A_review,toB,B_skip,toA,A_skip,backB,B_review,backA,finalB,finalA,afterA,afterB,all_real_UI_writes:writes,exact_allowed_writes:allowedWrites,
   four_real_advance_POSTs_six_real_project_open_POSTs:true,complete_scope_source_origin_revision_cursor_item_history_actor_joins:true,
   five_roots_per_project_all_members_guarded_only_exact_chosen_queue_bytes_changed:true,preexisting_empty_queue_locks_unchanged:true,
   original_source_bytes_UUIDs_revisions_annotations_team_policy_reports_project_labelsets_other_queues_preserved:true,
   source_browser_only:true,native_Electron_installed_or_windows_coverage:false,queue_review_not_human_label_or_quality_approval:true,
   model_training_inference_GPU_parent_or_signed_acceptance:false};handoffSave(e,w,'saved-queue-advance-project-handoff-proof',proof);e.note('saved_queue_advance_project_handoff',proof);
 }catch(error){failed=true;throw error;}finally{
  let cleanup:unknown,cleanupFailed=false;for(const outcome of pending){const result=await outcome;pending.delete(outcome);if(!result.ok&&!cleanupFailed){cleanupFailed=true;cleanup=result.error;}}
  try{if(!page.isClosed())await page.unroute(rawPattern,fallback);}catch(error){if(!cleanupFailed){cleanupFailed=true;cleanup=error;}}
  page.off('request',observe);page.off('request',started);if(cleanupFailed){try{e.note('saved_queue_advance_secondary_cleanup',{type:cleanup instanceof Error?cleanup.name:typeof cleanup});}catch{}if(!failed)throw cleanup;}
 }
}
test('real saved queue review and skip advances retain only the owning cursor and history through A B A project handoff',async({page,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);await exerciseQueueAdvanceHandoff(page,workspace,evidence,renderer.origin,renderer.url);
});
