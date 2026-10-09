import fs from 'node:fs';
import {createHash} from 'node:crypto';
import type {Page,Route} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(file:string)=>createHash('sha256').update(fs.readFileSync(file)).digest('hex');

// Assigning disposable synthetic work is a metadata action. No annotation,
// model truth, review vote, training or production account is changed here.
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const project=await api('/api/project/create',{name:'Owned assignment controls',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
 await api('/api/dataset/import',{folder_path:workspace.dataset,task:'segmentation'});
 await api('/api/team-data');await api('/api/team-data/readiness');
 const rows=await api('/api/team-data/queue?offset=0&limit=30');expect(rows.items).toHaveLength(2);
 const image=rows.items.find((row:any)=>row.file_path===workspace.images[0].path);expect(image).toBeTruthy();
 if(url)await page.goto(url);else await page.reload();
 await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
 await page.getByRole('button',{name:'집중 편집',exact:true}).click();
 await page.getByRole('button',{name:'팀 작업 · 라벨 기준·검수',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'팀 데이터 작업',exact:true});
 const current=dialog.getByRole('region',{name:'현재 이미지 팀 작업',exact:true});
 const queue=dialog.getByRole('region',{name:'팀 작업 목록',exact:true});
 const endpoint='/api/team-data/images/'+image.image_uuid+'/assign';
 const mutations:Array<{method:string;pathname:string}>=[];
 const observe=(request:any)=>{const pathname=new URL(request.url()).pathname;if(request.method()!=='GET'&&(pathname.startsWith('/api/team-data')||pathname.startsWith('/api/annotations')||pathname.startsWith('/api/training')))mutations.push({method:request.method(),pathname});};
 page.on('request',observe);
 const transport=async(route:Route)=>{
  if(route.request().method()==='POST'&&new URL(route.request().url()).pathname===endpoint)await route.fulfill({status:503,json:{detail:'Controlled assignment POST transport failure'}});
  else await route.continue();
 };
 try{
  await queue.getByRole('button',{name:image.relative_path,exact:true}).click();
  await expect(current).toContainText(image.relative_path);
  const actor=dialog.getByLabel('팀 작업자 이름',{exact:true}),apply=current.getByRole('button',{name:'작업 배정',exact:true});
  await actor.fill('');await expect(apply).toBeDisabled();expect(mutations).toEqual([]);
  const emptyBaseline=(await api('/api/team-data/images/'+image.image_uuid)).image;
  await current.scrollIntoViewIfNeeded();await expect(apply).toBeInViewport();await evidence.screenshot(page,`${native?'native':'browser'}-assignment-empty-actor-disabled-no-command`);
  expect((await api('/api/team-data/images/'+image.image_uuid)).image).toEqual(emptyBaseline);
  await actor.fill('controlled-assignment-manager');await current.getByLabel('이미지 담당자',{exact:true}).fill('controlled-assignment-target');
  await current.getByLabel('작업 우선순위',{exact:true}).fill('151');
  const baseline=(await api('/api/team-data/images/'+image.image_uuid)).image;
  expect(baseline.annotation_hash).toBeNull();expect(baseline.mask_hash).toBeNull();
  const invalidReply=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===endpoint);
  await apply.click();const invalid=await invalidReply;expect(invalid.status()).toBe(422);const invalidBody=await invalid.json();
  expect(invalidBody.detail).toEqual(expect.arrayContaining([expect.objectContaining({loc:['body','priority'],input:151,ctx:{le:100}})]));
  const invalidAlert=dialog.getByRole('alert');await expect(invalidAlert).toBeVisible();await expect(invalidAlert).toContainText('priority');await expect(invalidAlert).toContainText('100');
  expect((await api('/api/team-data/images/'+image.image_uuid)).image).toEqual(baseline);
  await invalidAlert.scrollIntoViewIfNeeded();await expect(invalidAlert).toBeInViewport();await evidence.screenshot(page,`${native?'native':'browser'}-assignment-priority-actual-422-no-record-write`);
  await current.getByLabel('작업 우선순위',{exact:true}).fill('60');
  await page.route('**/api/team-data/images/*/assign',transport);
  const refusedReply=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===endpoint);
  await apply.click();const refused=await refusedReply;expect(refused.status()).toBe(503);
  const refusedBody=await refused.json();expect(refusedBody).toEqual({detail:'Controlled assignment POST transport failure'});
  const error=dialog.getByRole('alert').filter({hasText:'Controlled assignment POST transport failure'});await expect(error).toBeVisible();
  expect((await api('/api/team-data/images/'+image.image_uuid)).image).toEqual(baseline);
  await error.scrollIntoViewIfNeeded();await expect(error).toBeInViewport();await evidence.screenshot(page,`${native?'native':'browser'}-assignment-valid-post-controlled-503-record-preserved`);
  await page.unroute('**/api/team-data/images/*/assign',transport);
  const retryReply=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===endpoint);
  await apply.click();const retry=await retryReply;expect(retry.status()).toBe(200);const assigned=(await retry.json()).image;
  await expect(dialog.getByRole('alert')).toHaveCount(0);
  expect(assigned.image_uuid).toBe(image.image_uuid);expect(assigned.revision).toBe(baseline.revision+1);
  expect(assigned.team.assignment.assignee).toBe('controlled-assignment-target');expect(assigned.team.assignment.priority).toBe(60);
  expect(assigned.annotation_hash).toBe(baseline.annotation_hash);expect(assigned.mask_hash).toBe(baseline.mask_hash);
  expect((await api('/api/team-data/images/'+image.image_uuid)).image).toEqual(assigned);
  await expect(queue).toContainText('담당 controlled-assignment-target');await expect(queue).toContainText('우선 60');
  expect(mutations).toEqual([{method:'POST',pathname:endpoint},{method:'POST',pathname:endpoint},{method:'POST',pathname:endpoint}]);
  await current.scrollIntoViewIfNeeded();await evidence.screenshot(page,`${native?'native':'browser'}-assignment-explicit-real-retry-one-revision`);
  for(const file of workspace.images)expect(sha(file.path)).toBe(file.sha256);
  evidence.note('assignment_controls',{record_id:'F024',action:'assign',dimensions:['empty','invalid','error'],project_id:project.id,image_uuid:image.image_uuid,baseline,assigned,empty:{blank_actor_disabled:true,no_assign_command_dispatched:true,selected_image_record_preserved:true},invalid:{priority:151,status:422,response:invalidBody,record_unchanged:true},error:{valid_priority:60,controlled_exact_POST_status:503,original_POST_not_dispatched:true,record_unchanged:true,explicit_real_retry_status:200,one_revision_increment:true},ui_mutations:mutations,source_images:workspace.images,controlled_assignments_not_human_truth:true,source_ui:true,source_electron:native,annotation_or_review_write:false,actual_model_inference:false,quality_accepted:false,installed_target_verified:false,gpu_used:false,windows_excluded:true});
 }finally{page.off('request',observe);if(!page.isClosed())await page.unroute('**/api/team-data/images/*/assign',transport);}
}
test('assignment empty invalid priority and exact POST failure preserve records before real retry',async({page,renderer,workspace,evidence})=>{
 const api:Api=async(route,body,method)=>{const response=await page.request.fetch(renderer.origin+route,{method:method||(body?'POST':'GET'),data:body});expect(response.ok(),await response.text()).toBe(true);return response.json();};
 await installDesktopHostShim(page,renderer.port);await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native assignment empty invalid and exact POST failure preserve annotations before real retry',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,status=await electronSession.waitForBackend();
 const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!response.ok)throw Error(`Owned assignment HTTP ${response.status}`);return response.json();},{port:status.port,route,body,method});
 await exercise(page,workspace,evidence,api,true);
});

// SOURCE-only extension: actual execution and registry credit remain Root-owned.
import handoffPath from 'node:path';
import {createHash as handoffCreateHash} from 'node:crypto';
import type {Request as HandoffRequest, Route as HandoffRoute} from '@playwright/test';
import {png as handoffPng} from './qa/appFlow';
type HandoffApi = (route:string,body?:unknown,method?:string)=>Promise<any>;
const handoffHash=(bytes:Buffer)=>handoffCreateHash('sha256').update(bytes).digest('hex');
const handoffTree=(root:string):Record<string,{sha256:string;size:number}>=>{
 const rows:Record<string,{sha256:string;size:number}>={};
 const walk=(dir:string)=>{for(const entry of fs.readdirSync(dir,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name))){
  const file=handoffPath.join(dir,entry.name),info=fs.lstatSync(file);expect(info.isSymbolicLink()).toBe(false);
  if(info.isDirectory())walk(file);else{expect(info.isFile()).toBe(true);const bytes=fs.readFileSync(file);rows[handoffPath.relative(root,file).split(handoffPath.sep).join('/') ]={sha256:handoffHash(bytes),size:bytes.length};}
 }};walk(root);return rows;
};
async function handoffWithin<T>(promise:Promise<T>,deadline:number,label:string):Promise<T>{
 const remaining=deadline-performance.now();if(remaining<=0)throw Error('Original 10s handoff frame expired: '+label);
 let timer:ReturnType<typeof setTimeout>|undefined;
 return Promise.race([promise,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original 10s handoff frame expired: '+label)),remaining);})]).finally(()=>{if(timer)clearTimeout(timer);});
}
async function handoffProject(page:Page,scope:{project:any;source:string}){
 const deadline=performance.now()+10_000;
 await page.getByTitle('프로젝트 관리',{exact:true}).click();const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});
 await dialog.getByRole('button',{name:'최근 프로젝트',exact:true}).click();
 const wire=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/api/project/open'&&r.request().postDataJSON()?.project_dir===scope.project.project_dir,{timeout:10_000});
 const item=dialog.getByRole('button').filter({has:page.locator('span[title]').filter({hasText:scope.project.project_dir})});
 await expect(item).toHaveCount(1);await expect(item).toBeEnabled();await item.click();
 const response=await handoffWithin(wire,deadline,'project open');expect(response.status()).toBe(200);const bytes=await response.body();
 expect(JSON.parse(bytes.toString('utf8'))).toEqual(scope.project);expect(await response.finished()).toBeNull();
 await expect(dialog).toHaveCount(0);await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name);
 return {project_id:scope.project.id,project_dir:scope.project.project_dir,status:200,response_sha256:handoffHash(bytes)};
}
async function handoffLabels(w:Workspace,api:HandoffApi,family:string,tag:'A'|'B'){
 const source=handoffPath.join(w.root,family+'-handoff-'+tag);fs.mkdirSync(source);const image=handoffPath.join(source,'part.png');
 fs.writeFileSync(image,handoffPng(256,3,(x,y)=>[x,y,tag==='A'?103:201]));
 const project=await api('/api/project/create',{name:family+' handoff '+tag,task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 await api('/api/team-data/books',{expected_version:0,actor:'fixture-owner',title:'Handoff '+tag,categories:[{id:0,name:'OK',color:'#10b981'},{id:2,name:'Scratch',color:'#f59e0b'}]});
 const saved=await api('/api/annotations/save',{image_id:'part',image_path:image,image_width:256,image_height:256,actor:'handoff-labeler-'+tag,
  annotations:[{id:'handoff-original-'+tag,type:'bbox',label:'Scratch',category_id:2,bbox:[2,3,20,21]}]});
 await api('/api/team-data');await api('/api/team-data/readiness');await api('/api/team-data/queue?offset=0&limit=30');
 const activeProject=await api('/api/project/current');expect(activeProject.id).toBe(project.id);
 return {tag,project:activeProject,source,image,uuid:saved.metadata.image_uuid,query:'/api/annotations/part?file_path='+encodeURIComponent(image),imageRoute:'/api/team-data/images/'+saved.metadata.image_uuid};
}
async function handoffEnterLabels(page:Page,scope:{image:string;tag:string},team:boolean){
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
 await page.getByRole('button',{name:'집중 편집',exact:true}).click();
 if(team){await page.getByRole('button',{name:'팀 작업 · 라벨 기준·검수',exact:true}).click();const dialog=page.getByRole('dialog',{name:'팀 데이터 작업',exact:true});
  await dialog.getByRole('region',{name:'팀 작업 목록',exact:true}).getByRole('button',{name:'part.png',exact:true}).click();
  await expect(dialog.getByRole('region',{name:'현재 이미지 팀 작업',exact:true})).toContainText('handoff-labeler-'+scope.tag);return dialog;
 }
 await expect(page.getByRole('region',{name:'라벨 연결 복구',exact:true})).toBeVisible();return null;
}
async function handoffLateRead(page:Page,origin:string,native:boolean,match:(u:URL)=>boolean){
 const pattern=origin+'/api/**';let release!:()=>void,reached!:()=>void,finished!:()=>void;
 const gate=new Promise<void>(r=>release=r),ready=new Promise<void>(r=>reached=r),done=new Promise<void>(r=>finished=r);
 let captured:{status:number;bytes:Buffer;sha256:string;body:any}|undefined,primary:unknown,routeError:unknown,request:HandoffRequest|undefined,deadline=0,armed=true;
 let resolveDisposition!:(row:{kind:'finished'|'failed';failure:string|null})=>void;
 const disposition=new Promise<{kind:'finished'|'failed';failure:string|null}>(r=>resolveDisposition=r);
 const ended=(r:HandoffRequest)=>{if(r===request)resolveDisposition({kind:'finished',failure:null});};
 const failed=(r:HandoffRequest)=>{if(r===request)resolveDisposition({kind:'failed',failure:r.failure()?.errorText??null});};
 page.on('requestfinished',ended);page.on('requestfailed',failed);
 const handler=async(route:HandoffRoute)=>{
  const own=route.request(),address=new URL(own.url());
  if(!armed||own.method()!=='GET'||address.origin!==origin||address.searchParams.has('_modu_handoff_delegate')||!match(address)){await route.continue();return;}
  armed=false;request=own;deadline=performance.now()+10_000;
  try{
   let bytes:Buffer,status:number,contentType:string|null;
   if(native){
    // This is a separate authenticated renderer GET, not original UI200 provenance.
    const headers:Record<string,string>={};for(const name of ['content-type','x-vision-project','x-vision-context']){const value=await own.headerValue(name);if(value!==null)headers[name]=value;}
    const delegate=new URL(own.url());delegate.searchParams.set('_modu_handoff_delegate',handoffCreateHash('sha256').update(own.url()+String(Date.now())).digest('hex'));
    const snapshot=await page.evaluate(async({url,headers,remaining})=>{
     const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),remaining);
     try{const response=await fetch(url,{method:'GET',headers,signal:abort.signal});const raw=new Uint8Array(await response.arrayBuffer());
      if(raw.length>1024*1024)throw Error('Controlled handoff snapshot exceeds 1MiB');let binary='';for(const byte of raw)binary+=String.fromCharCode(byte);
      return {status:response.status,contentType:response.headers.get('content-type'),base64:btoa(binary)};
     }finally{clearTimeout(timer);}
    },{url:delegate.toString(),headers,remaining:Math.max(1,Math.floor(deadline-performance.now()))});
    status=snapshot.status;contentType=snapshot.contentType;bytes=Buffer.from(snapshot.base64,'base64');
   }else{const snapshot=await route.fetch({timeout:Math.max(1,Math.floor(deadline-performance.now()))});status=snapshot.status();contentType=snapshot.headers()['content-type']??null;bytes=await snapshot.body();}
   expect(status).toBe(200);expect(bytes.length).toBeLessThanOrEqual(1024*1024);
   captured={status,bytes,sha256:handoffHash(bytes),body:JSON.parse(bytes.toString('utf8'))};reached();
   await handoffWithin(gate,deadline,'held read release');
   try{await route.fulfill({status,body:bytes,headers:contentType?{'content-type':contentType}:{}});}catch(error){routeError=error;}
  }catch(error){primary=error;reached();}finally{finished();}
 };
 await page.route(pattern,handler);
 return {
  ready:async()=>{await handoffWithin(ready,performance.now()+10_000,'captured original read');if(primary)throw primary;expect(captured).toBeTruthy();return captured!;},
  finish:async()=>{release();await handoffWithin(done,deadline,'route completion');if(primary)throw primary;const row=await handoffWithin(disposition,deadline,'original Request disposition');
   if(row.kind==='failed')expect(row.failure).toContain('ERR_ABORTED');else{if(routeError)throw routeError;const response=await request!.response();expect(response).not.toBeNull();expect(response!.status()).toBe(200);expect(handoffHash(await response!.body())).toBe(captured!.sha256);expect(await response!.finished()).toBeNull();}
   await page.evaluate(()=>new Promise<void>(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>r()))));
   return {request_disposition:row,captured_sha256:captured!.sha256,captured_bytes:captured!.bytes.length,snapshot_transport:native?'separate_authenticated_renderer_GET_controlled_original_UI_reply':'original_browser_request_actual_response',original_UI_response_delivered:row.kind==='finished',native_original_HTTP200_provenance:false};},
  close:async()=>{release();if(!page.isClosed())await page.unroute(pattern,handler);page.off('requestfinished',ended);page.off('requestfailed',failed);},
 };
}
async function assignmentProjectHandoff(page:Page,w:Workspace,e:Evidence,api:HandoffApi,native:boolean,origin:string,url?:string){
 const A=await handoffLabels(w,api,'assignment','A'),B=await handoffLabels(w,api,'assignment','B');expect(A.project.id).not.toBe(B.project.id);expect(A.uuid).not.toBe(B.uuid);
 const initial:Record<string,any>={};
 for(const scope of [A,B]){await api('/api/project/open',{project_dir:scope.project.project_dir});const image=(await api(scope.imageRoute)).image;
  await api(scope.imageRoute+'/assign',{expected_revision:image.revision,actor:'fixture-owner',assignee:'owned-'+scope.tag,priority:scope.tag==='A'?17:83});
  initial[scope.tag]=(await api(scope.imageRoute)).image;
 }
 const trees={A:{source:handoffTree(A.source),annotations:handoffTree(A.project.annotations_dir)},B:{source:handoffTree(B.source),annotations:handoffTree(B.project.annotations_dir)}};
 await api('/api/project/open',{project_dir:A.project.project_dir});if(url)await page.goto(url);else await page.reload();
 let dialog=(await handoffEnterLabels(page,A,true))!;const current=()=>dialog.getByRole('region',{name:'현재 이미지 팀 작업',exact:true});
 await expect(current().getByLabel('이미지 담당자',{exact:true})).toHaveValue('owned-A');await expect(current().getByLabel('작업 우선순위',{exact:true})).toHaveValue('17');
 await dialog.getByLabel('팀 작업자 이름',{exact:true}).fill('fixture-unsent-owner');await current().getByLabel('이미지 담당자',{exact:true}).fill('unsent-A');await current().getByLabel('작업 우선순위',{exact:true}).fill('91');
 const writes:Array<{method:string;path:string}>=[];const observe=(r:HandoffRequest)=>{const p=new URL(r.url()).pathname;if(r.method()!=='GET'&&/^\/api\/(team-data|annotations|training|dataset\/metadata)(\/|$)/.test(p))writes.push({method:r.method(),path:p});};page.on('request',observe);
 const late=await handoffLateRead(page,origin,native,u=>u.pathname===A.imageRoute);let primary:unknown;
 try{
  await dialog.getByRole('button',{name:'새로고침',exact:true}).click();const captured=await late.ready();expect(captured.body.image).toEqual(initial.A);expect(captured.body.image.image_uuid).toBe(A.uuid);
  await dialog.getByRole('button',{name:'팀 데이터 작업 닫기',exact:true}).click();await expect(dialog).toHaveCount(0);
  const toB=await handoffProject(page,B);dialog=(await handoffEnterLabels(page,B,true))!;
  await expect(current().getByLabel('이미지 담당자',{exact:true})).toHaveValue('owned-B');await expect(current().getByLabel('작업 우선순위',{exact:true})).toHaveValue('83');
  expect((await api(B.imageRoute)).image).toEqual(initial.B);const outcome=await late.finish();
  await expect(current()).toContainText('handoff-labeler-B');await expect(current()).not.toContainText('handoff-labeler-A');await expect(current().getByLabel('이미지 담당자',{exact:true})).toHaveValue('owned-B');
  await e.screenshot(page,`${native?'native':'browser'}-assignment-B-after-old-A-read`);
  await dialog.getByRole('button',{name:'팀 데이터 작업 닫기',exact:true}).click();const toA=await handoffProject(page,A);dialog=(await handoffEnterLabels(page,A,true))!;
  await expect(current().getByLabel('이미지 담당자',{exact:true})).toHaveValue('owned-A');await expect(current().getByLabel('작업 우선순위',{exact:true})).toHaveValue('17');expect((await api(A.imageRoute)).image).toEqual(initial.A);
  expect(writes).toEqual([]);for(const scope of [A,B]){expect(handoffTree(scope.source)).toEqual(trees[scope.tag].source);expect(handoffTree(scope.project.annotations_dir)).toEqual(trees[scope.tag].annotations);}
  await e.screenshot(page,`${native?'native':'browser'}-assignment-A-return-persisted-form`);
  e.note('assignment_project_handoff',{record_id:'F024',action:'assign',dimension:'handoff',projects:[A.project.id,B.project.id,A.project.id],image_uuids:[A.uuid,B.uuid,A.uuid],toB,toA,
   read_only_late_A:outcome,raw_A_response_sha256:captured.sha256,unsent_A_form_not_submitted:true,B_persisted_assignment_exact:true,return_A_persisted_assignment_exact:true,
   annotations_and_source_namespace_hashes:trees,business_mutations:writes,actual_GUI_project_switch:true,controlled_fixture_assignments_not_human_truth:true,
   actual_model_inference:false,quality_human_installed_target_parent_approval:false,source_electron:native});
 }catch(error){primary=error;throw error;}finally{page.off('request',observe);try{await late.close();}catch(error){if(!primary)throw error;e.note('assignment_handoff_secondary_cleanup',{type:error instanceof Error?error.name:'unknown'});}}
}
test('assignment project handoff A B A rejects a late A read and preserves unsent form custody',async({page,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:HandoffApi=async(route,body,method)=>{const r=await page.request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(r.ok(),await r.text()).toBe(true);return r.json();};
 await assignmentProjectHandoff(page,workspace,evidence,api,false,renderer.origin,renderer.url);
});
test('native assignment project handoff A B A retains exact UUID and read only records',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,backend=await electronSession.waitForBackend(),origin=`http://127.0.0.1:${backend.port}`;
 const api:HandoffApi=(route,body,method)=>page.evaluate(async({origin,route,body,method})=>{const r=await fetch(origin+route,{method:method||(body===undefined?'GET':'POST'),headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});if(!r.ok)throw Error(`Owned handoff fixture HTTP ${r.status}`);return r.json();},{origin,route,body,method});
 await assignmentProjectHandoff(page,workspace,evidence,api,true,origin);
});
