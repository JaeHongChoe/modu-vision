import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {inflateSync} from 'node:zlib';
import type {Page,Request,Response} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {png} from './qa/appFlow';

test.use({actionTimeout:10_000});
type Reply={status:number;body:string;url:string};
type Api=(route:string,body?:unknown,method?:string,deadline?:number)=>Promise<Reply>;
type Tree=Record<string,{kind:'directory'}|{kind:'file';size:number;sha256:string}>;
type Write={method:string;url:string;body:string|null};
type Clock={request:Request;started:number;deadline:number;finished?:number;status?:number;raw?:string;rawBase64?:string;failure?:string;pending?:Promise<void>};
const sha=(raw:Buffer|string)=>crypto.createHash('sha256').update(raw).digest('hex');
const clone=<T,>(value:T):T=>JSON.parse(JSON.stringify(value));
function assertSourcePixels(raw:Buffer,blue=100){
 expect([100,101]).toContain(blue);
 expect(raw.subarray(0,8)).toEqual(Buffer.from([137,80,78,71,13,10,26,10]));let cursor=8,header:Buffer|undefined;const data:Buffer[]=[];
 while(cursor<raw.length){const size=raw.readUInt32BE(cursor),type=raw.toString('ascii',cursor+4,cursor+8),value=raw.subarray(cursor+8,cursor+8+size);expect(cursor+size+12).toBeLessThanOrEqual(raw.length);
  if(type==='IHDR'){expect(header).toBeUndefined();header=value;}if(type==='IDAT')data.push(value);cursor+=size+12;
 }
 expect(cursor).toBe(raw.length);expect(header).toBeDefined();expect(header!.readUInt32BE(0)).toBe(256);expect(header!.readUInt32BE(4)).toBe(256);expect([...header!.subarray(8)]).toEqual([8,2,0,0,0]);
 const pixels=inflateSync(Buffer.concat(data));expect(pixels.length).toBe(256*769);
 const expected=Buffer.alloc(256*769);
 for(let y=0;y<256;y++)for(let x=0;x<256;x++){const offset=y*769+1+x*3;expected[offset]=x;expected[offset+1]=y;expected[offset+2]=blue;}
 expect(pixels.equals(expected)).toBe(true);
 return {width:256,height:256,pixels:65536,channels:3};
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


// Original bodies are retained before verdicts. Request start to complete body
// has one absolute 10-second budget; a late/failed body cannot qualify a cell.
function transport(page:Page,w:Workspace,e:Evidence,api:Api,prefix:string){
 const clocks=new Map<Request,Clock>(),writes:Write[]=[],calls:any[]=[],snapshots:any[]=[],preObserverResponses:any[]=[];
 const observed=(request:Request)=>{if(!new URL(request.url()).pathname.startsWith('/api/')||request.method()==='OPTIONS')return;
  const started=performance.now();clocks.set(request,{request,started,deadline:started+10_000});
  if(!['GET','HEAD'].includes(request.method()))writes.push({method:request.method(),url:request.url(),body:request.postData()});
 };
 const failed=(request:Request)=>{const row=clocks.get(request);if(row)row.failure=request.failure()?.errorText||'Original request failed';};
 const response=(reply:Response)=>{if(!new URL(reply.url()).pathname.startsWith('/api/')||reply.request().method()==='OPTIONS')return;
  const row=clocks.get(reply.request());if(!row){preObserverResponses.push({url:reply.url(),method:reply.request().method(),status:reply.status(),request_start_clock_unavailable:true});return;}
  row.status=reply.status();row.pending=(async()=>{let timer:NodeJS.Timeout|undefined;
   try{const [raw,terminal]=await Promise.race([Promise.all([reply.body(),reply.finished()]),new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original full-body 10s deadline expired')),Math.max(0,row.deadline-performance.now()));})]);
    expect(terminal).toBeNull();row.finished=performance.now();row.raw=raw.toString('utf8');row.rawBase64=raw.toString('base64');expect(row.finished).toBeLessThanOrEqual(row.deadline);
   }catch(error){row.failure=String(error);}finally{clearTimeout(timer);}
  })();
 };
 page.on('request',observed);page.on('requestfailed',failed);page.on('response',response);
 const settle=async()=>{for(const row of clocks.values()){
  if(!row.pending)await expect.poll(()=>Boolean(row.pending||row.failure),{timeout:Math.max(1,row.deadline-performance.now())}).toBe(true);
  if(row.pending)await row.pending;expect(row.failure).toBeUndefined();expect(typeof row.raw).toBe('string');expect(row.deadline).toBe(row.started+10_000);expect(row.finished!).toBeLessThanOrEqual(row.deadline);
 }};
 const captured=async(reply:Response)=>{const row=clocks.get(reply.request());expect(row).toBeDefined();
  await expect.poll(()=>Boolean(row!.pending),{timeout:Math.max(1,row!.deadline-performance.now())}).toBe(true);await row!.pending;
  expect(row!.failure).toBeUndefined();expect(typeof row!.raw).toBe('string');return {status:row!.status!,body:row!.raw!,url:reply.url(),request:{method:reply.request().method(),url:reply.request().url(),body:reply.request().postData()},started:row!.started,deadline:row!.deadline,finished:row!.finished!};
 };
 const read=async(route:string,body?:unknown,method?:string,absoluteDeadline?:number)=>{const started=performance.now(),deadline=Math.min(started+10_000,absoluteDeadline??Infinity);let timer:NodeJS.Timeout|undefined;
  try{expect(deadline).toBeGreaterThan(started);const reply=await Promise.race([api(route,body,method,deadline),new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original fixture request/full-body deadline expired')),Math.max(0,deadline-performance.now()));})]);
   const row={route,method:method||(body===undefined?'GET':'POST'),request:body??null,...reply,started,deadline,finished:performance.now(),raw_sha256:sha(reply.body),raw_size:Buffer.byteLength(reply.body)};calls.push(row);
   expect(row.finished).toBeLessThanOrEqual(deadline);expect(reply.status,route+': '+reply.body).toBe(200);expect(new URL(reply.url).pathname+new URL(reply.url).search).toBe(route);JSON.parse(reply.body);return reply;
  }finally{clearTimeout(timer);}
 };
 const value=async(route:string,body?:unknown,method?:string,deadline?:number)=>JSON.parse((await read(route,body,method,deadline)).body);
 const storage=()=>page.evaluate(()=>Object.fromEntries(Object.entries(localStorage).sort(([a],[b])=>a.localeCompare(b))));
 const snapshot=async(tag:string,routes:string[],roots:Record<string,string>)=>{const replies:Record<string,Reply>={};for(const route of routes)replies[route]=await read(route);await settle();
  const state={api:replies,trees:Object.fromEntries(Object.entries(roots).map(([key,root])=>[key,protectedTree(root)])),storage:await storage()};
  const folder=path.join(w.logs,prefix+'-'+tag);fs.mkdirSync(folder);fs.writeFileSync(path.join(folder,'snapshot.json'),JSON.stringify({roots,state},null,2),{flag:'wx'});e.addFile(path.join(folder,'snapshot.json'));
  for(const [key,root]of Object.entries(roots))for(const [name,row]of Object.entries(state.trees[key])){const copy=path.join(folder,key,name);if(row.kind==='directory')fs.mkdirSync(copy,{recursive:true});else{fs.mkdirSync(path.dirname(copy),{recursive:true});fs.copyFileSync(path.join(root,name),copy);expect(sha(fs.readFileSync(copy))).toBe(row.sha256);e.addFile(copy);}}
  snapshots.push({tag,folder,state});return state;
 };
 const finish=()=>{page.off('request',observed);page.off('requestfailed',failed);page.off('response',response);
  const originalResponses=[...clocks.values()].map(row=>({url:row.request.url(),method:row.request.method(),request_body:row.request.postData(),started:row.started,deadline:row.deadline,finished:row.finished,status:row.status,raw_text:row.raw,raw_base64:row.rawBase64,raw_sha256:row.rawBase64===undefined?null:sha(Buffer.from(row.rawBase64,'base64')),raw_size:row.rawBase64===undefined?null:Buffer.from(row.rawBase64,'base64').length,failure:row.failure}));
  const file=path.join(w.logs,prefix+'-original-transport.json');fs.writeFileSync(file,JSON.stringify({calls,writes,originalResponses,preObserverResponses,snapshots},null,2),{flag:'wx'});e.addFile(file);
  e.note(prefix+'_transport',{calls,writes,originalResponses,preObserverResponses,snapshots,all_original_bodies_retained:true,absolute_request_and_full_body_ms:10_000});
 };
 return{value,read,settle,captured,snapshot,storage,writes,calls,finish};
}
function nativeApi(page:Page,port:number):Api{return async(route,body,method,outerDeadline)=>{
 const remaining=Math.max(0,(outerDeadline??performance.now()+10_000)-performance.now());
 return page.evaluate(async({port,route,body,method,remaining})=>{const controller=new AbortController(),until=performance.now()+remaining,timer=setTimeout(()=>controller.abort(),remaining);
  try{const reply=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),signal:controller.signal,...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
   const raw=await reply.text();if(performance.now()>until)throw Error('Original native full body exceeded deadline');return {status:reply.status,body:raw,url:reply.url};
  }finally{clearTimeout(timer);}
 },{port,route,body,method,remaining});
};}
function ownedProject(project:any,w:Workspace){expect(path.dirname(project.project_dir)).toBe(path.join(w.userData,'projects'));expect(fs.realpathSync(project.project_dir)).toBe(project.project_dir);expect(fs.lstatSync(project.project_dir).isSymbolicLink()).toBe(false);}
function assertWrites(rows:Write[],expected:Array<{method:string;route:string;body:unknown}>){expect(rows.map(row=>({method:row.method,route:new URL(row.url).pathname,body:JSON.parse(row.body!)}))).toEqual(expected);}
const stableRoutes=['/api/project/current','/api/project/labelsets','/api/project/preferences','/api/dataset/metadata?limit=100','/api/dataset/metadata/statistics','/api/dataset/versions','/api/dataset/revisions'];

function assertResolveRequest(request:{method:string;url:string;body:string|null},identity:any,origin:string){
 const url=new URL(request.url);expect(url.origin).toBe(origin);expect(request.method).toBe('POST');expect(url.pathname).toBe('/api/dataset/library/resolve');expect(url.search).toBe('');
 expect(request.body).not.toBeNull();expect(JSON.parse(request.body!)).toEqual({selections:[{image_uuid:identity.image_uuid,sha256:identity.sha256}]});
}
function assertResolvedIdentity(raw:string,identity:any,revision:string){
 const value=JSON.parse(raw);expect(value.revision_id).toBe(revision);expect(value.active).toBe(true);expect(value.results).toHaveLength(1);
 const result=value.results[0];expect(result.status).toBe('found');expect(result.candidates).toEqual([]);
 expect(result.current.image_uuid).toBe(identity.image_uuid);expect(result.current.sha256).toBe(identity.sha256);
 expect(result.current.relative_path).toBe(identity.relative_path);expect(result.current.file_path).toBe(identity.file_path);
 expect(result.current.valid===true||result.current.valid===1).toBe(true);return value;
}

test('native saved image picker shows exact resolution refusal then explicitly reopens the same validated identity',{tag:'@electron'},async({electronSession,workspace:w,evidence:e})=>{
 test.setTimeout(180_000);const page=electronSession.window,backend=await electronSession.waitForBackend(),io=transport(page,w,e,nativeApi(page,backend.port),'picker-resolve-error');
 try{
  const source=path.join(w.root,'picker-original-source'),folder=path.join(source,'train','good');fs.mkdirSync(folder,{recursive:true});
  const originals=[100,101].map((blue,index)=>{const file=path.join(folder,'part-'+index+'.png');fs.writeFileSync(file,png(256,3,(x,y)=>[x,y,blue]),{flag:'wx'});const raw=fs.readFileSync(file);return{path:file,relative:path.relative(source,file).split(path.sep).join('/'),sha256:sha(raw),size:raw.length,blue,pixels:assertSourcePixels(raw,blue)};});
  const project=await io.value('/api/project/create',{name:'Owned image picker empty and reopen',task:'classification'});ownedProject(project,w);await io.value('/api/project/update',{source_dataset_dir:source},'PUT');
  const importDeadline=performance.now()+10_000,started=await io.value('/api/dataset/imports',{task:'classification',verify:true},undefined,importDeadline);expect(typeof started.job_id).toBe('string');let imported:any;
  while(performance.now()<importDeadline){imported=await io.value('/api/dataset/imports/'+started.job_id,undefined,undefined,importDeadline);if(['completed','failed','aborted','interrupted'].includes(imported.state))break;await new Promise(resolve=>setTimeout(resolve,Math.min(50,Math.max(0,importDeadline-performance.now()))));}
  expect(performance.now()).toBeLessThanOrEqual(importDeadline);expect(imported.state).toBe('completed');expect(imported.job_id).toBe(started.job_id);expect(imported.source.root).toBe(source);const revision=imported.result.revision.revision_id;
  const accepted=await io.value('/api/dataset/imports/'+started.job_id+'/accept',{revision_id:revision,expected_active:null});expect(accepted).toEqual({active_revision:revision,job_id:started.job_id});
  const revisions=await io.value('/api/dataset/revisions');expect(revisions.active_revision).toBe(revision);expect(revisions.revisions).toHaveLength(1);
  const listing=await io.value('/api/dataset/library/images?limit=120');expect(listing.items).toHaveLength(2);expect(listing.revision_id).toBe(revision);expect(listing.active).toBe(true);expect(listing.source_root).toBe(source);expect(listing.next_cursor).toBeNull();
  for(const row of listing.items){const original=originals.find(image=>image.relative===row.relative_path);expect(original).toBeDefined();expect(row.valid===true||row.valid===1).toBe(true);expect(row.sha256).toBe(original!.sha256);expect(row.file_path).toBe(original!.path);expect(typeof row.image_uuid).toBe('string');expect(row.image_uuid.length).toBeGreaterThan(0);expect(sha(fs.readFileSync(row.file_path))).toBe(row.sha256);}
  const first=listing.items[0];await io.settle();await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(4).click();
  await expect(page.getByRole('heading',{name:'검사 플로우 편집기',exact:true})).toBeVisible();const opener=page.getByRole('button',{name:'이미지 변경...',exact:true}),picker=page.getByRole('dialog',{name:'검사 대상 이미지 선택'}),close=picker.getByRole('button',{name:'닫기 (Esc)',exact:true}),confirm=picker.getByRole('button',{name:'선택 확정',exact:true}),cancel=picker.getByRole('button',{name:'취소',exact:true});
  const preferences=async()=>Object.entries(await io.storage()).filter(([key])=>key.startsWith('modu.inspectionImage.v2:')).map(([key,raw])=>({key,raw}));expect(await preferences()).toEqual([]);
  const current=await io.value('/api/project/current');expect(current.id).toBe(project.id);expect(current.source_dataset_dir).toBe(source);expect(current.active_labelset_id).toBe(project.active_labelset_id);
  const routes=[...stableRoutes,'/api/dataset/library/images?limit=120','/api/annotations/'+encodeURIComponent(path.parse(first.file_name).name)+'?file_path='+encodeURIComponent(first.file_path)],roots={project:project.project_dir,source,harness_dataset:w.dataset};
  assertWrites(io.writes,io.calls.filter(row=>!['GET','HEAD'].includes(row.method)).map(row=>({method:row.method,route:row.route,body:row.request})));
  const initial=await io.snapshot('before-accepted-setup',routes,roots);
  await opener.focus();await opener.press('Enter');await expect(picker).toBeVisible();await expect(close).toBeFocused();
  await picker.getByRole('button',{name:'검증된 데이터 버전',exact:true}).click();
  const grid=picker.getByRole('list',{name:'데이터 버전 이미지'}),item=grid.getByRole('listitem').filter({hasText:first.file_name});
  await expect(item).toHaveCount(1);await item.focus();await item.press('Enter');await expect(item).toHaveAttribute('aria-pressed','true');
  await expect(confirm).toBeEnabled();await confirm.focus();await confirm.press('Enter');await expect(picker).toBeHidden();await expect(opener).toBeFocused();
  await expect(page.getByRole('region',{name:'플로우 식별 정보'})).toContainText(first.file_path);
  const remembered=await preferences();expect(remembered).toHaveLength(1);const chosen=JSON.parse(remembered[0].raw);
  expect(chosen).toEqual({source:'dataset',imagePath:first.file_path,imageId:first.image_uuid,imageUuid:first.image_uuid,sha256:first.sha256,relativePath:first.relative_path,fileName:first.file_name,thumbnailUrl:'/api/dataset/thumbnail/'+encodeURIComponent(first.file_name)+'?file_path='+encodeURIComponent(first.file_path)});
  const scope=JSON.parse(remembered[0].key.slice('modu.inspectionImage.v2:'.length));expect(scope).toHaveLength(4);expect(scope.at(-1)).toBe(project.id);
  const before=await io.snapshot('before-refusal',routes,roots),expectedStorage=clone(initial.storage);expectedStorage[remembered[0].key]=remembered[0].raw;
  expect(before.storage).toEqual(expectedStorage);expect(before.api).toEqual(initial.api);expect(before.trees).toEqual(initial.trees);
  expect(io.writes).toHaveLength(4);const writeStart=io.writes.length;
  const resolveOrigin=`http://127.0.0.1:${backend.port}`,resolveRoute='**/api/dataset/library/resolve',faultBody=JSON.stringify({detail:'Owned saved image resolution unavailable'}),faultRequests:Write[]=[];
  const fault=async(route:import('@playwright/test').Route)=>{const request=route.request();expect(request.frame()).toBe(page.mainFrame());
   const row={method:request.method(),url:request.url(),body:request.postData()};assertResolveRequest(row,first,resolveOrigin);faultRequests.push(row);
   await route.fulfill({status:503,contentType:'application/json',body:faultBody});
  };
  await page.route(resolveRoute,fault,{times:1});
  try{
   const pending=page.waitForResponse(reply=>new URL(reply.url()).pathname==='/api/dataset/library/resolve'&&reply.request().method()==='POST');
   await opener.press('Enter');await expect(picker).toBeVisible();await expect(close).toBeFocused();const refused=await io.captured(await pending);
   expect(refused.status).toBe(503);expect(refused.body).toBe(faultBody);assertResolveRequest(refused.request,first,resolveOrigin);expect(faultRequests).toHaveLength(1);
   await expect(picker.getByRole('alert')).toHaveCount(1);await expect(picker.getByRole('alert')).toContainText('저장된 선택을 확인하지 못했습니다');
   await expect(picker.getByRole('alert')).toContainText('Owned saved image resolution unavailable');await expect(picker.getByRole('alert')).toContainText('확인되지 않은 경로는 쓰지 않으니 다시 선택하세요.');
   await expect(picker).toContainText('선택된 이미지가 없습니다.');await expect(confirm).toBeDisabled();await expect(item).toHaveAttribute('aria-pressed','false');
   await expect(page.getByRole('region',{name:'플로우 식별 정보'})).toContainText(first.file_path);expect(await preferences()).toEqual(remembered);
   await io.settle();assertWrites(io.writes.slice(writeStart),[{method:'POST',route:'/api/dataset/library/resolve',body:{selections:[{image_uuid:first.image_uuid,sha256:first.sha256}]}}]);
   await e.screenshot(page,'native-picker-saved-resolution-503-temporary-selection-cleared');
  }finally{await page.unroute(resolveRoute,fault);}
  const afterRefusal=await io.snapshot('after-refusal',routes,roots);expect(afterRefusal).toEqual(before);
  await close.press('Escape');await expect(picker).toBeHidden();await expect(opener).toBeFocused();expect(await preferences()).toEqual(remembered);
  const recoveredPending=page.waitForResponse(reply=>new URL(reply.url()).pathname==='/api/dataset/library/resolve'&&reply.request().method()==='POST');
  await opener.press('Enter');await expect(picker).toBeVisible();await expect(close).toBeFocused();const recovered=await io.captured(await recoveredPending);
  expect(recovered.status,recovered.body).toBe(200);assertResolveRequest(recovered.request,first,resolveOrigin);expect(recovered.request.body).toBe(faultRequests[0].body);
  const resolution=assertResolvedIdentity(recovered.body,first,revision);
  await expect(picker.getByRole('alert')).toHaveCount(0);await expect(picker).toContainText('선택: '+first.relative_path);await expect(item).toHaveAttribute('aria-pressed','true');await expect(confirm).toBeEnabled();
  await e.screenshot(page,'native-picker-saved-resolution-real200-same-identity-restored');
  await confirm.focus();await confirm.press('Enter');await expect(picker).toBeHidden();await expect(opener).toBeFocused();
  await expect(page.getByRole('region',{name:'플로우 식별 정보'})).toContainText(first.file_path);expect(await preferences()).toEqual(remembered);
  const after=await io.snapshot('after-real-recovery-confirm',routes,roots);expect(after).toEqual(before);
  const expectedPostBaseline=[503,200].map(()=>({method:'POST',route:'/api/dataset/library/resolve',body:{selections:[{image_uuid:first.image_uuid,sha256:first.sha256}]}}));
  assertWrites(io.writes.slice(writeStart),expectedPostBaseline);
  const proof=path.join(w.logs,'picker-resolve-error-proof.json'),notes={cell:'F064.native-image-picker-keyboard-focus.error',requirement:'S2-05',project,source,originals,revision,first,chosen,remembered,initial,before,afterRefusal,after,
   setupWrites:io.writes.slice(0,writeStart),postBaselineWrites:io.writes.slice(writeStart),faultRequests,resolution,
   temporarySelection:null,acceptedSelectionRetained:true,durablePreferenceBytesUnchanged:true,onlyReadonlyResolvePosts:true,
   actualQualityHumanTargetParentApproval:false,proofScope:'one controlled owning renderer503 then explicit actual same-identity200 reopen; native captures follow original harness availability'};
  fs.writeFileSync(proof,JSON.stringify(notes,null,2),{flag:'wx'});e.addFile(proof);e.note('picker_resolve_error',notes);
 }finally{io.finish();}
});
