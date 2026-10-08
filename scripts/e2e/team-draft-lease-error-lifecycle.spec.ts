import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {Page,Request,Route,Response} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';

test.use({actionTimeout:10_000});
type Reply={status:number;body:string;url:string};
type Api=(route:string,body?:unknown,method?:string)=>Promise<Reply>;
type CapturedRequest={method:string;url:string;body:string|null};
type Write={index:number;method:string;endpoint:string;body:string|null;lease_token_sha256?:string};
type DraftEntry=[string,string];
type Tree=Record<string,{kind:'directory'}|{kind:'file';size:number;sha256:string}>;
const sha=(raw:Buffer)=>crypto.createHash('sha256').update(raw).digest('hex');
const clone=<T,>(value:T):T=>JSON.parse(JSON.stringify(value));
const requestValue=(request:Request):CapturedRequest=>({method:request.method(),url:request.url(),body:request.postData()});

export function assertExactSnapshot(actual:unknown,original:unknown){expect(actual).toEqual(original);}
export function assertDraftIdentity(entries:DraftEntry[],original:DraftEntry,image:string,uuid:string,hash:string){
 expect(entries).toEqual([original]);const value=JSON.parse(entries[0][1]);
 expect(value.schema).toBe(1);expect(value.image_path).toBe(image);expect(value.image_uuid).toBe(uuid);
 expect(value.source_sha256).toBe(hash);expect([value.width,value.height]).toEqual([256,256]);
 expect(typeof value.nonce).toBe('string');expect(value.nonce.length).toBeGreaterThan(0);
 expect(Number.isSafeInteger(value.base_revision)).toBe(true);expect(value.base_revision).toBeGreaterThan(0);
}
export function assertApplyRead(request:CapturedRequest,origin:string,query:string){
 expect(request).toEqual({method:'GET',url:origin+query,body:null});
}
export function assertControlledFailure(reply:Pick<Reply,'status'|'body'>,detail:string){
 expect(reply.status).toBe(503);expect(JSON.parse(reply.body)).toEqual({detail});
}
export function assertLeaseAcquire(request:CapturedRequest,origin:string,uuid:string,revision:number,actor:string){
 expect(request.method).toBe('POST');expect(request.url).toBe(origin+'/api/team-data/images/'+uuid+'/lease/acquire');
 expect(JSON.parse(request.body!)).toEqual({expected_revision:revision,actor,ttl_seconds:120});
}
export function redactedWrite(index:number,method:string,endpoint:string,body:string|null):Write{
 const result:Write={index,method,endpoint,body};if(body===null)return result;
 const value=JSON.parse(body);if(Object.hasOwn(value,'lease_token')){
  expect(endpoint).toMatch(/^\/api\/team-data\/images\/[^/]+\/lease\/(release|renew)$/);
  expect(typeof value.lease_token).toBe('string');expect(value.lease_token.length).toBeGreaterThan(0);
  result.lease_token_sha256=sha(Buffer.from(value.lease_token));value.lease_token='[REDACTED-OWNED-LEASE]';result.body=JSON.stringify(value);
 }
 return result;
}
export function assertLeaseMutation(previous:any,next:any,actor:string,phase:'acquired'|'released'){
 expect(next.revision).toBe(previous.revision+1);expect(next.audit).toHaveLength(previous.audit.length+1);
 const event=next.audit.at(-1);expect(Object.keys(event).sort()).toEqual(['action','actor','at','changes','id','revision']);
 expect(event.id).toMatch(/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/);
 expect(typeof event.at).toBe('string');expect(Number.isNaN(Date.parse(event.at))).toBe(false);
 expect(event.actor).toBe(actor);expect(event.action).toBe('edit_lease_'+phase);expect(event.revision).toBe(next.revision);
 expect(event.changes).toEqual(phase==='acquired'?{owner:actor}:{});
 if(phase==='acquired'){
  expect(Object.keys(next.team.edit_lease).sort()).toEqual(['expires_at','owner']);expect(next.team.edit_lease.owner).toBe(actor);
  expect(Number.isFinite(next.team.edit_lease.expires_at)).toBe(true);expect(next.team.edit_lease.expires_at).toBeGreaterThan(0);
 }else expect(next.team.edit_lease).toBeNull();
 const expected=clone(previous);expected.revision=next.revision;expected.audit.push(event);expected.team.edit_lease=next.team.edit_lease;
 expect(next).toEqual(expected);
}

function protectedTree(root:string):Tree{
 const result:Tree={};const walk=(directory:string)=>{
  const entry=fs.lstatSync(directory);expect(entry.isSymbolicLink()).toBe(false);expect(entry.isDirectory()).toBe(true);
  expect(fs.realpathSync(directory)).toBe(directory);result[path.relative(root,directory).split(path.sep).join('/')]={kind:'directory'};
  for(const name of fs.readdirSync(directory).sort()){
   const file=path.join(directory,name),before=fs.lstatSync(file);expect(before.isSymbolicLink()).toBe(false);
   if(before.isDirectory())walk(file);else{
    expect(before.isFile()).toBe(true);expect(before.nlink).toBe(1);
    const descriptor=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);let raw:Buffer;
    try{const opened=fs.fstatSync(descriptor);expect([opened.dev,opened.ino,opened.size]).toEqual([before.dev,before.ino,before.size]);raw=fs.readFileSync(descriptor);
     const after=fs.fstatSync(descriptor),named=fs.lstatSync(file);
     expect([after.dev,after.ino,after.size,after.mtimeMs,after.ctimeMs]).toEqual([opened.dev,opened.ino,opened.size,opened.mtimeMs,opened.ctimeMs]);
     expect([named.dev,named.ino,named.size,named.mtimeMs,named.ctimeMs]).toEqual([opened.dev,opened.ino,opened.size,opened.mtimeMs,opened.ctimeMs]);
    }finally{fs.closeSync(descriptor);}
    result[path.relative(root,file).split(path.sep).join('/')]={kind:'file',size:raw!.length,sha256:sha(raw!)};
   }
  }
 };walk(root);return result;
}

async function exercise(page:Page,w:Workspace,e:Evidence,api:Api,native:boolean,url?:string){
 const prefix=native?'source-electron':'browser',name='Owned draft apply and lease error lifecycle',actor='fixture-operator';
 const writes:Write[]=[],calls:any[]=[],timings:any[]=[],cells:any[]=[],snapshots:any[]=[],faults:any[]=[];let sequence=0;
 const observe=(request:Request)=>{const endpoint=new URL(request.url()).pathname;
  if(endpoint.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(request.method()))writes.push(redactedWrite(++sequence,request.method(),endpoint,request.postData()));
 };
 page.on('request',observe);
 const readReply=async(route:string,body?:unknown,method?:string)=>{
  const started=performance.now(),deadline=started+10_000;let timer:NodeJS.Timeout|undefined;
  try{
   const reply=await Promise.race([api(route,body,method),new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original owned fixture API deadline expired')),Math.max(0,deadline-performance.now()));})]);
   const finished=performance.now();expect(deadline).toBe(started+10_000);expect(finished).toBeLessThanOrEqual(deadline);
   expect(reply.status,route).toBe(200);expect(new URL(reply.url).pathname+new URL(reply.url).search).toBe(route);
   JSON.parse(reply.body);timings.push({route,started,deadline,finished});return reply;
  }finally{clearTimeout(timer);}
 };
 const value=async(route:string,body?:unknown,method?:string)=>{
  const reply=await readReply(route,body,method);calls.push({route,method:method||(body===undefined?'GET':'POST'),request:body,...reply});return JSON.parse(reply.body);
 };
 const source=path.join(w.root,'draft-lease-source');fs.mkdirSync(source);
 const image=path.join(source,'part.png');fs.writeFileSync(image,png(256,3,(x,y)=>[x,y,100]),{flag:'wx'});
 const imageHash=sha(fs.readFileSync(image));
 const project=await value('/api/project/create',{name,task:'segmentation'});
 await value('/api/project/update',{source_dataset_dir:source},'PUT');await value('/api/dataset/import',{folder_path:source,task:'segmentation'});
 await value('/api/team-data/books',{expected_version:0,actor:'fixture-owner',title:'Controlled original draft book',categories:[
  {id:0,name:'OK',color:'#10b981'},{id:2,name:'Scratch',color:'#f59e0b'}]});
 const saved=await value('/api/annotations/save',{image_id:'part',image_path:image,image_width:256,image_height:256,actor,
  annotations:[{id:'original-label',type:'bbox',label:'Scratch',category_id:2,color:'#f59e0b',bbox:[2,3,20,21]}]});
 const uuid=saved.metadata.image_uuid,query='/api/annotations/part?file_path='+encodeURIComponent(image),imageRoute='/api/team-data/images/'+uuid;
 const drafts=()=>page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.startsWith('modu-annotation-draft:v1:')).sort(([a],[b])=>a.localeCompare(b))) as Promise<DraftEntry[]>;
 const storage=()=>page.evaluate(()=>Object.fromEntries(Object.entries(localStorage).sort(([a],[b])=>a.localeCompare(b))));
 const rows=()=>page.getByTitle('Delete annotation',{exact:true}),save=()=>page.getByTestId('annotation-save-button');
 const enter=async()=>{
  await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
  const focus=page.getByRole('button',{name:'집중 편집',exact:true});if(await focus.getAttribute('aria-pressed')!=='true')await focus.click();
  await expect(page.getByText('1 / 1',{exact:true})).toBeVisible();
 };
 const team=page.getByRole('dialog',{name:'팀 데이터 작업',exact:true});
 const compare=page.getByRole('dialog',{name:'서버 라벨과 로컬 초안 비교',exact:true});
 const compareButton=()=>page.getByRole('button',{name:'저장 전 초안 비교·복구',exact:true});
 const apply=()=>compare.getByRole('button',{name:'비교한 초안을 명시적으로 적용',exact:true});
 const closeCompare=async()=>{await compare.getByRole('button',{name:'서버 라벨과 로컬 초안 비교 닫기',exact:true}).click();await expect(compare).toHaveCount(0);};
 const screenshot=(label:string)=>e.screenshot(page,prefix+'-draft-lease-'+label);
 const captured=async(response:Response)=>({status:response.status(),body:await response.text(),url:response.url(),request:requestValue(response.request())});
 const responseFor=(method:string,route:string)=>page.waitForResponse(response=>response.request().method()===method&&new URL(response.url()).pathname+new URL(response.url()).search===route,{timeout:10_000});
 let applyFault:((route:Route)=>Promise<void>)|undefined,leaseFault:((route:Route)=>Promise<void>)|undefined;
 try{
  if(url)await page.goto(url);else await page.reload();await enter();
  await page.getByRole('button',{name:'팀 작업 · 라벨 기준·검수',exact:true}).click();await expect(team).toBeVisible();
  await team.getByLabel('팀 작업자 이름',{exact:true}).fill(actor);
  await team.getByRole('button',{name:'팀 데이터 작업 닫기',exact:true}).click();await expect(team).toHaveCount(0);
  await expect(rows()).toHaveCount(1);
  await page.getByTitle('100% Zoom (1:1)',{exact:true}).click();await expect(page.getByTestId('canvas-hud')).toContainText('100%');
  await page.getByRole('button',{name:/^Scratch(?: \d+)?$/}).click();await page.getByTitle('바운딩 박스 (BBox - 2)',{exact:true}).click();
  const box=await page.locator('[data-canvas-container]').boundingBox();expect(box).not.toBeNull();
  const point=(x:number,y:number)=>({x:box!.x+(box!.width-256)/2+x,y:box!.y+(box!.height-256)/2+y});
  const a=point(40,40),b=point(80,80);await page.mouse.move(a.x,a.y);await page.mouse.down();await page.mouse.move(b.x,b.y,{steps:6});await page.mouse.up();
  await expect(rows()).toHaveCount(2);await expect(page.getByRole('region',{name:'라벨 연결 복구'})).toContainText('이 컴퓨터에 초안 보존됨');
  const originalEntries=await drafts();expect(originalEntries).toHaveLength(1);const originalDraftEntry=originalEntries[0],originalDraft=JSON.parse(originalDraftEntry[1]);
  assertDraftIdentity(originalEntries,originalDraftEntry,image,uuid,imageHash);
  expect(originalDraft.base_annotations).toEqual((await value(query)).annotations);
  expect(originalDraft.annotations).toHaveLength(2);expect(originalDraft.annotations[0]).toEqual(originalDraft.base_annotations[0]);
  expect(originalDraft.annotations[1].bbox).toEqual([40,40,80,80]);expect(originalDraft.annotations[1].label).toBe('Scratch');
  const settings=(await value('/api/team-data')).settings;expect(settings.editing_enabled).toBe(false);
  await value('/api/team-data/settings',{expected_revision:settings.revision,actor:'fixture-owner',changes:{editing_enabled:true}},'PUT');
  const assigned=await value(imageRoute+'/assign',{expected_revision:(await value(query)).metadata.revision,actor:'fixture-owner',assignee:actor,priority:50});
  await page.reload();await enter();await expect(rows()).toHaveCount(1);await expect(compareButton()).toBeVisible();
  await expect(page.getByText('편집 시작 필요',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'편집 시작',exact:true})).toBeEnabled();
  assertDraftIdentity(await drafts(),originalDraftEntry,image,uuid,imageHash);
  const current=await value('/api/project/current'),ownedRoot=native?path.join(w.userData,'projects'):w.projects;
  expect(path.dirname(current.project_dir)).toBe(ownedRoot);
  for(const root of [ownedRoot,current.project_dir]){expect(fs.lstatSync(root).isSymbolicLink()).toBe(false);expect(fs.realpathSync(root)).toBe(root);}
  expect(current.id).toBe(project.id);expect(current.source_dataset_dir).toBe(source);
  const origin=new URL((await readReply('/api/project/current')).url).origin;
  const setupWrites=writes.slice();const setupCalls=calls.slice();
  const expectedSetup=setupCalls.filter(call=>call.method!=='GET').map(call=>[call.method,new URL(origin+call.route).pathname]);
  expect(setupWrites.map(write=>[write.method,write.endpoint])).toEqual(native?expectedSetup:[]);
  if(native)for(let index=0;index<setupWrites.length;index++)expect(JSON.parse(setupWrites[index].body!)).toEqual(setupCalls.filter(call=>call.method!=='GET')[index].request);
  const uiStart=writes.length;
  const roots={project:current.project_dir as string,source,harness_dataset:path.join(w.root,'dataset')};
  const routes={project:'/api/project/current',labelsets:'/api/project/labelsets',annotations:query,
   metadata:'/api/dataset/metadata/image?image_path='+encodeURIComponent(image),team_image:imageRoute,team:'/api/team-data',
   queue:'/api/team-data/queue?offset=0&limit=30',readiness:'/api/team-data/readiness',
   images:'/api/dataset/images?folder_path='+encodeURIComponent(source)+'&task=segmentation&offset=0&limit=120'};
  const snapshot=async()=>{
   const apiRows:Record<string,Reply>={};for(const[key,route]of Object.entries(routes))apiRows[key]=await readReply(route);
   return{api:apiRows,trees:Object.fromEntries(Object.entries(roots).map(([key,root])=>[key,protectedTree(root)])),storage:await storage()};
  };
  const protect=async(label:string)=>{
   const state=await snapshot(),folder=path.join(w.logs,'draft-lease-protected-'+label);expect(fs.existsSync(folder)).toBe(false);fs.mkdirSync(folder);
   for(const[key,root]of Object.entries(roots)){
    fs.cpSync(root,path.join(folder,key),{recursive:true,errorOnExist:true});expect(protectedTree(path.join(folder,key))).toEqual(state.trees[key]);
    for(const[relative,entry]of Object.entries(state.trees[key]))if(entry.kind==='file')e.addFile(path.join(folder,key,relative));
   }
   const proof=path.join(folder,'snapshot.json');fs.writeFileSync(proof,JSON.stringify({roots,state}),{flag:'wx'});e.addFile(proof);snapshots.push({label,folder,state});return{state,folder};
  };
  const unchanged=async(before:Awaited<ReturnType<typeof protect>>)=>{
   const after=await snapshot();assertExactSnapshot(after,before.state);
   for(const[key,root]of Object.entries(roots))for(const[relative,entry]of Object.entries(before.state.trees[key]))if(entry.kind==='file')
    expect(fs.readFileSync(path.join(root,relative))).toEqual(fs.readFileSync(path.join(before.folder,key,relative)));
   assertDraftIdentity(await drafts(),originalDraftEntry,image,uuid,imageHash);await expect(rows()).toHaveCount(1);return after;
  };
  const baseline=await protect('before');expect(JSON.parse(baseline.state.api.team_image.body).image).toEqual(assigned.image);
  expect(JSON.parse(baseline.state.api.annotations.body).metadata.team.edit_lease).toBeNull();
  const originalRGB=await page.evaluate(async(data)=>{
   const image=new Image();await new Promise<void>((resolve,reject)=>{image.onload=()=>resolve();image.onerror=()=>reject(Error('Owned source decode refused'));image.src=data;});
   const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;const context=canvas.getContext('2d')!;context.drawImage(image,0,0);
   const pixels=context.getImageData(0,0,image.width,image.height).data;let matched=0;
   for(let y=0;y<256;y++)for(let x=0;x<256;x++){const i=(y*256+x)*4;if(pixels[i]!==x||pixels[i+1]!==y||pixels[i+2]!==100||pixels[i+3]!==255)throw Error('Owned full source RGB differs');matched++;}
   return{width:image.width,height:image.height,pixels:matched};
  },'data:image/png;base64,'+fs.readFileSync(image).toString('base64'));
  expect(originalRGB).toEqual({width:256,height:256,pixels:65536});
  const openComparison=async()=>{
   const promise=responseFor('GET',query);await compareButton().click();const reply=await captured(await promise);
   expect(reply.status).toBe(200);assertApplyRead(reply.request,origin,query);
   expect(JSON.parse(reply.body)).toEqual(JSON.parse((await readReply(query)).body));
   for(const[title,items]of [['초안 작성 당시',originalDraft.base_annotations],['현재 서버 라벨',JSON.parse(reply.body).annotations],['보존된 로컬 초안',originalDraft.annotations]] as const){
    const pane=compare.locator('details').filter({hasText:title});await expect(pane).toHaveCount(1);expect(JSON.parse(await pane.locator('pre').innerText())).toEqual(items);
   }
   await expect(compare.getByRole('checkbox')).not.toBeChecked();await expect(apply()).toBeDisabled();return reply;
  };
  const cancelRead=await openComparison();await compare.getByRole('checkbox').check();await expect(apply()).toBeEnabled();
  const cancelStart=writes.length;await closeCompare();const cancelAfter=await unchanged(baseline);expect(writes.slice(cancelStart)).toEqual([]);
  await screenshot('accepted-compare-cancel-keeps-original-draft');
  cells.push({action:'apply-draft',dimension:'cancel',comparison:cancelRead,after:cancelAfter,dialog_closed:true,discarded:false,applied:false,server_saved:false});
  const initialComparison=await openComparison();await compare.getByRole('checkbox').check();
  const applyDetail='Controlled exact draft apply GET unavailable';let applyCount=0;
  applyFault=async route=>{expect(route.request().frame()).toBe(page.mainFrame());assertApplyRead(requestValue(route.request()),origin,query);applyCount++;
   await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:applyDetail})});};
  await page.route('**/api/annotations/part?*',applyFault);const applyStart=writes.length,failedRead=responseFor('GET',query);
  await apply().click();const failedApply=await captured(await failedRead);assertControlledFailure(failedApply,applyDetail);
  await expect(compare.getByRole('alert')).toHaveText(applyDetail);await expect(compare.getByRole('checkbox')).toBeChecked();await expect(apply()).toBeEnabled();
  await screenshot('apply-503-preserves-comparison-accepted-draft');
  await page.unroute('**/api/annotations/part?*',applyFault);applyFault=undefined;expect(applyCount).toBe(1);
  const applyAfter=await unchanged(baseline);expect(writes.slice(applyStart)).toEqual([]);
  faults.push({action:'apply-draft',detail:applyDetail,count:applyCount,reply:failedApply});
  cells.push({action:'apply-draft',dimension:'error',initial_comparison:initialComparison,reply:failedApply,after:applyAfter,comparison_retained:true,accepted_retained:true,apply_enabled_after_busy:true,server_saved:false});
  await closeCompare();
  const leaseDetail='Controlled exact lease acquisition unavailable',leaseRevision=JSON.parse(baseline.state.api.metadata.body).revision;
  let leaseCount=0;leaseFault=async route=>{expect(route.request().frame()).toBe(page.mainFrame());assertLeaseAcquire(requestValue(route.request()),origin,uuid,leaseRevision,actor);leaseCount++;
   await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:leaseDetail})});};
  await page.route('**/api/team-data/images/*/lease/acquire',leaseFault);const leaseStart=writes.length,failedLeaseWait=responseFor('POST',imageRoute+'/lease/acquire');
  await page.getByRole('button',{name:'편집 시작',exact:true}).click();const failedLease=await captured(await failedLeaseWait);assertControlledFailure(failedLease,leaseDetail);
  await expect(page.getByRole('alert').filter({hasText:leaseDetail})).toHaveText(leaseDetail);
  await expect(page.getByRole('button',{name:'편집 시작',exact:true})).toBeEnabled();await expect(page.getByText('현재 이미지 편집 중',{exact:true})).toHaveCount(0);
  await page.unroute('**/api/team-data/images/*/lease/acquire',leaseFault);leaseFault=undefined;expect(leaseCount).toBe(1);
  const leaseAfter=await unchanged(baseline);expect(writes.slice(leaseStart)).toHaveLength(1);
  assertLeaseAcquire(failedLease.request,origin,uuid,leaseRevision,actor);await screenshot('lease-503-no-owned-lease');
  faults.push({action:'edit-start',detail:leaseDetail,count:leaseCount,reply:failedLease});
  cells.push({action:'edit-start',dimension:'error',reply:failedLease,after:leaseAfter,no_server_lease:true,no_client_owned_lease:true,no_renew_or_release_on_failure:true});

  // Normal recovery is a declared durable lease transaction, separate from the
  // three zero-effect/error assertions above. Never save the bearer token.
  const acquireStart=Date.now()/1000,acceptedWait=responseFor('POST',imageRoute+'/lease/acquire');
  await page.getByRole('button',{name:'편집 시작',exact:true}).click();const acceptedResponse=await acceptedWait,accepted=await acceptedResponse.json();
  expect(acceptedResponse.status()).toBe(200);assertLeaseAcquire(requestValue(acceptedResponse.request()),origin,uuid,leaseRevision,actor);
  expect(accepted.lease_token).toEqual(expect.any(String));e.redact(accepted.lease_token);const tokenHash=sha(Buffer.from(accepted.lease_token));
  const originalImage=JSON.parse(baseline.state.api.team_image.body).image;assertLeaseMutation(originalImage,accepted.image,actor,'acquired');
  expect(accepted.image.team.edit_lease.expires_at).toBeGreaterThanOrEqual(acquireStart+120);
  expect(accepted.image.team.edit_lease.expires_at).toBeLessThanOrEqual(Date.now()/1000+120);
  await expect(page.getByText('현재 이미지 편집 중',{exact:true})).toBeVisible();
  const releaseWait=responseFor('POST',imageRoute+'/lease/release');await page.getByRole('button',{name:'편집 종료',exact:true}).click();
  const releasedResponse=await releaseWait,released=await releasedResponse.json();expect(releasedResponse.status()).toBe(200);
  const releaseRequest=JSON.parse(releasedResponse.request().postData()!);expect(releaseRequest).toEqual({expected_revision:accepted.image.revision,actor,lease_token:accepted.lease_token});
  assertLeaseMutation(accepted.image,released.image,actor,'released');await expect(page.getByText('편집 시작 필요',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'편집 시작',exact:true})).toBeEnabled();assertDraftIdentity(await drafts(),originalDraftEntry,image,uuid,imageHash);
  const recovered=await protect('after-owned-lease-release');
  expect(JSON.parse(recovered.state.api.team_image.body).image).toEqual(released.image);
  expect(JSON.parse(recovered.state.api.annotations.body).annotations).toEqual(JSON.parse(baseline.state.api.annotations.body).annotations);
  const workflowFiles=Object.keys(baseline.state.trees.project).filter(name=>name.endsWith('/metadata/workflow.json'));expect(workflowFiles).toHaveLength(1);
  const workflow=workflowFiles[0],ledgerBefore=JSON.parse(fs.readFileSync(path.join(baseline.folder,'project',workflow),'utf8'));
  const ledgerAfter=JSON.parse(fs.readFileSync(path.join(recovered.folder,'project',workflow),'utf8'));
  const imageKeys=Object.keys(ledgerBefore.images).filter(key=>ledgerBefore.images[key].image_uuid===uuid);expect(imageKeys).toHaveLength(1);
  const expectedLedger=clone(ledgerBefore);expectedLedger.images[imageKeys[0]]=released.image;expect(ledgerAfter).toEqual(expectedLedger);
  const expectedTrees=clone(baseline.state.trees);expectedTrees.project[workflow]=recovered.state.trees.project[workflow];expect(recovered.state.trees).toEqual(expectedTrees);
  for(const[key,root]of Object.entries(roots))for(const[relative,entry]of Object.entries(baseline.state.trees[key]))if(entry.kind==='file'&&!(key==='project'&&relative===workflow))
   expect(fs.readFileSync(path.join(root,relative))).toEqual(fs.readFileSync(path.join(baseline.folder,key,relative)));
  for(const key of ['project','labelsets','team','readiness','images'])expect(recovered.state.api[key]).toEqual(baseline.state.api[key]);
  for(const key of ['annotations','metadata','team_image','queue']){
   const previous=JSON.parse(baseline.state.api[key].body);let expected:any;
   if(key==='annotations')expected={...previous,metadata:released.image};
   else if(key==='metadata')expected=released.image;
   else if(key==='team_image')expected={...previous,image:released.image};
   else expected={...previous,items:previous.items.map((row:any)=>row.image_uuid===uuid?released.image:row)};
   expect(recovered.state.api[key].status).toBe(baseline.state.api[key].status);
   expect(recovered.state.api[key].url).toBe(baseline.state.api[key].url);expect(JSON.parse(recovered.state.api[key].body)).toEqual(expected);
  }
  expect(recovered.state.storage).toEqual(baseline.state.storage);
  const allowedUI=writes.slice(uiStart);expect(allowedUI.map(write=>[write.method,write.endpoint])).toEqual([
   ['POST',imageRoute+'/lease/acquire'],['POST',imageRoute+'/lease/acquire'],['POST',imageRoute+'/lease/release']]);
  expect(allowedUI[0].body).toBe(allowedUI[1].body);expect(allowedUI[2].lease_token_sha256).toBe(tokenHash);

  const recoveryComparison=await openComparison();await compare.getByRole('checkbox').check();const applyRecovery=responseFor('GET',query);
  await apply().click();const applied=await captured(await applyRecovery);expect(applied.status).toBe(200);assertApplyRead(applied.request,origin,query);
  expect(JSON.parse(applied.body)).toEqual(JSON.parse(recovered.state.api.annotations.body));await expect(compare).toHaveCount(0);
  await expect(rows()).toHaveCount(2);await expect(save()).toContainText('Save Changes');
  const nextEntries=await drafts();expect(nextEntries).toHaveLength(1);expect(nextEntries[0][0]).toBe(originalDraftEntry[0]);
  const nextDraft=JSON.parse(nextEntries[0][1]);expect(nextDraft.annotations).toEqual(originalDraft.annotations);
  expect(nextDraft.base_annotations).toEqual(JSON.parse(recovered.state.api.annotations.body).annotations);expect(nextDraft.base_revision).toBe(released.image.revision);
  expect(nextDraft.nonce).not.toBe(originalDraft.nonce);
  const expectedDraft={...originalDraft,base_revision:released.image.revision,base_annotations:nextDraft.base_annotations,nonce:nextDraft.nonce,saved_at:nextDraft.saved_at};
  expect(nextDraft).toEqual(expectedDraft);expect(Number.isNaN(Date.parse(nextDraft.saved_at))).toBe(false);
  const final=await protect('after-explicit-apply');expect(final.state.api).toEqual(recovered.state.api);expect(final.state.trees).toEqual(recovered.state.trees);
  const expectedStorage={...recovered.state.storage,[nextEntries[0][0]]:nextEntries[0][1]};expect(final.state.storage).toEqual(expectedStorage);
  expect(writes.slice(uiStart)).toEqual(allowedUI);expect(sha(fs.readFileSync(image))).toBe(imageHash);await screenshot('explicit-apply-200-dirty-editor-not-server-save');
  const proof={schema_version:1,record_id:'F024',actions:{'apply-draft':['error','cancel'],'edit-start':['error']},project:{id:project.id,dir:current.project_dir,source},
   original_image:{path:image,sha256:imageHash,image_uuid:uuid,RGB:originalRGB},original_draft:{entry:originalDraftEntry,sha256:sha(Buffer.from(originalDraftEntry[1]))},
   roots,routes,snapshots,cells,faults,fixture_calls:calls,fixture_setup:setupCalls,renderer_setup_writes:setupWrites,ui_writes:allowedUI,all_renderer_writes:writes,timings,
   recovery:{acquire:{status:200,request:requestValue(acceptedResponse.request()),body:{...accepted,lease_token:'[REDACTED-OWNED-LEASE]'},lease_token_sha256:tokenHash},
    release:{status:200,request:redactedWrite(0,'POST',imageRoute+'/lease/release',releasedResponse.request().postData()),body:released},
    comparison:recoveryComparison,applied,draft_entry:nextEntries[0],server_saved:false},
   scope:{actual_source_ui:true,source_electron:native,installed_target:false,controlled_synthetic_labels:true,human_truth_or_quality_approval:false,
    actual_model_inference:false,gpu:false,cancel_existing_dialog_only:true,successful_lease_writes_declared:true,token_redacted_not_exported:true}};
  const proofPath=path.join(w.logs,'team-draft-lease-error-proof.json');fs.writeFileSync(proofPath,JSON.stringify(proof),{flag:'wx'});e.addFile(proofPath);e.addFile(image);
  const bytes=fs.readFileSync(proofPath);e.note('team_draft_lease_error_lifecycle',{proof_path:proofPath,proof_sha256:sha(bytes),proof_size:bytes.length});
 }finally{
  page.off('request',observe);
  if(!page.isClosed()){
   if(applyFault)await page.unroute('**/api/annotations/part?*',applyFault);
   if(leaseFault)await page.unroute('**/api/team-data/images/*/lease/acquire',leaseFault);
  }
 }
}

test('draft apply503 and accepted dialog cancel preserve the original draft; lease503 explicitly recovers',async({page,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);
 const api:Api=async(route,body,method)=>{
  const response=await page.request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),data:body});
  return{status:response.status(),body:await response.text(),url:response.url()};
 };
 await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native draft apply503 cancel and exact lease503 recovery preserve owned source labels and draft',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,status=await electronSession.waitForBackend();
 const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{
  const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),
   headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  return{status:response.status,body:await response.text(),url:response.url};
 },{port:status.port,route,body,method});
 await exercise(page,workspace,evidence,api,true);
});
