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


test('native image picker refuses empty selection and reopens the exact validated identity with keyboard focus',{tag:'@electron'},async({electronSession,workspace:w,evidence:e})=>{
 test.setTimeout(180_000);const page=electronSession.window,backend=await electronSession.waitForBackend(),io=transport(page,w,e,nativeApi(page,backend.port),'picker-empty-reopen');
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
  const before=await io.snapshot('before-empty',routes,roots),writeStart=io.writes.length;
  await opener.focus();await opener.press('Enter');await expect(picker).toBeVisible();await expect(close).toBeFocused();await expect(confirm).toBeDisabled();
  await picker.getByRole('button',{name:'로컬 파일',exact:true}).click();const field=picker.getByPlaceholder('예: /path/to/inspection-image.png',{exact:true});await expect(field).toHaveValue('');await expect(picker).toContainText('선택된 이미지가 없습니다.');await expect(confirm).toBeDisabled();
  await field.fill('   ');await expect(field).toHaveValue('   ');await expect(confirm).toBeDisabled();await expect(picker).toContainText('선택된 이미지가 없습니다.');
  await close.focus();await close.press('Shift+Tab');await expect(cancel).toBeFocused();await cancel.press('Tab');await expect(close).toBeFocused();await close.press('Escape');await expect(picker).toBeHidden();await expect(opener).toBeFocused();
  expect(await preferences()).toEqual([]);const afterEmpty=await io.snapshot('after-empty',routes,roots);expect(afterEmpty).toEqual(before);expect(io.writes.slice(writeStart)).toEqual([]);await e.screenshot(page,'native-picker-empty-escape-restores-opener');
  await opener.press('Enter');await expect(close).toBeFocused();await picker.getByRole('button',{name:'검증된 데이터 버전',exact:true}).click();
  const grid=picker.getByRole('list',{name:'데이터 버전 이미지'}),item=grid.getByRole('listitem').filter({hasText:first.file_name});await expect(item).toHaveCount(1);await item.focus();await item.press('Enter');await expect(item).toHaveAttribute('aria-pressed','true');await expect(confirm).toBeEnabled();await confirm.focus();await confirm.press('Enter');await expect(picker).toBeHidden();await expect(opener).toBeFocused();
  await expect(page.getByRole('region',{name:'플로우 식별 정보'})).toContainText(first.file_path);const remembered=await preferences();expect(remembered).toHaveLength(1);
  const chosen=JSON.parse(remembered[0].raw);expect(chosen).toEqual({source:'dataset',imagePath:first.file_path,imageId:first.image_uuid,imageUuid:first.image_uuid,sha256:first.sha256,relativePath:first.relative_path,fileName:first.file_name,thumbnailUrl:'/api/dataset/thumbnail/'+encodeURIComponent(first.file_name)+'?file_path='+encodeURIComponent(first.file_path)});
  const scope=JSON.parse(remembered[0].key.slice('modu.inspectionImage.v2:'.length));expect(scope).toHaveLength(4);expect(scope.at(-1)).toBe(project.id);const postConfirm=await io.snapshot('before-reopen',routes,roots),expectedStorage=clone(before.storage);expectedStorage[remembered[0].key]=remembered[0].raw;expect(postConfirm.storage).toEqual(expectedStorage);expect(postConfirm.api).toEqual(before.api);expect(postConfirm.trees).toEqual(before.trees);
  const resolveStart=io.writes.length,pending=page.waitForResponse(reply=>new URL(reply.url()).pathname==='/api/dataset/library/resolve'&&reply.request().method()==='POST');await opener.press('Enter');await expect(close).toBeFocused();const resolved=await io.captured(await pending);
  expect(resolved.status,resolved.body).toBe(200);const resolveRequest={selections:[{image_uuid:first.image_uuid,sha256:first.sha256}]};expect(JSON.parse(resolved.request.body!)).toEqual(resolveRequest);
  const resolution=JSON.parse(resolved.body);expect(resolution.revision_id).toBe(revision);expect(resolution.active).toBe(true);expect(resolution.results).toHaveLength(1);const result=resolution.results[0];expect(result.status).toBe('found');expect(result.current.image_uuid).toBe(first.image_uuid);expect(result.current.sha256).toBe(first.sha256);expect(result.current.relative_path).toBe(first.relative_path);expect(result.current.file_path).toBe(first.file_path);expect(result.current.valid===true||result.current.valid===1).toBe(true);expect(result.candidates).toEqual([]);
  await expect(picker).toContainText('선택: '+first.relative_path);await expect(confirm).toBeEnabled();await expect(grid.getByRole('listitem').filter({hasText:first.file_name})).toHaveAttribute('aria-pressed','true');expect(await preferences()).toEqual(remembered);
  await close.focus();await close.press('Shift+Tab');await expect(confirm).toBeFocused();await confirm.press('Tab');await expect(close).toBeFocused();await close.press('Escape');await expect(picker).toBeHidden();await expect(opener).toBeFocused();
  const afterReopen=await io.snapshot('after-reopen',routes,roots);expect(afterReopen).toEqual(postConfirm);expect(afterReopen.trees).toEqual(before.trees);
  assertWrites(io.writes.slice(resolveStart),[{method:'POST',route:'/api/dataset/library/resolve',body:resolveRequest}]);
  assertWrites(io.writes.slice(writeStart),[{method:'POST',route:'/api/dataset/library/resolve',body:resolveRequest}]);
  for(const image of originals){expect(sha(fs.readFileSync(image.path))).toBe(image.sha256);assertSourcePixels(fs.readFileSync(image.path),image.blue);}await io.settle();await e.screenshot(page,'native-picker-resolved-original-escape-restores-opener');
  e.note('native_picker_empty_reopen',{cells:[{action:'F064.native-image-picker-keyboard-focus',dimension:'empty'},{action:'F064.native-image-picker-keyboard-focus',dimension:'reopen'}],project,current,source,originals,started,imported,importDeadline,accepted,revision,listing,first,chosen,remembered,scope,resolved,resolution,
   source_electron:true,actual_keyboard_selection_and_reopen:true,actual_original_resolve_UUID_and_SHA:true,empty_and_whitespace_confirmation_disabled:true,focus_wrap_and_escape_restore:true,all_complete_protected_trees_unfiltered:true,same_scoped_preference_bytes:true,model_training_inference_or_flow_execution:false,native_file_dialog_used:false,native_installed_release_acceptance:false,human_quality_approval:false,parent_82_acceptance:false});
 }finally{io.finish();}
});


// F064 handoff: the saved picker identity and keyboard focus belong to one project.
// Two original controlled inventories share file names but never their path, UUID or bytes.
function pickerHandoffWrite(file:string,raw:Buffer|string){
 const fd=fs.openSync(file,fs.constants.O_WRONLY|fs.constants.O_CREAT|fs.constants.O_EXCL,0o600);let first:unknown,failed=false;
 try{const bytes=Buffer.isBuffer(raw)?raw:Buffer.from(raw);let at=0;while(at<bytes.length){const n=fs.writeSync(fd,bytes,at,bytes.length-at);expect(n).toBeGreaterThan(0);at+=n;}fs.fsyncSync(fd);}
 catch(error){failed=true;first=error;}
 finally{try{fs.closeSync(fd);}catch(error){if(!failed){failed=true;first=error;}}}
 if(failed)throw first;
}
function pickerHandoffCustody(root:string){
 const rows:Record<string,unknown>={};
 const raw7=(s:fs.BigIntStats)=>[s.dev,s.ino,s.mode,s.nlink,s.size,s.mtimeNs,s.ctimeNs].map(String);
 const walk=(directory:string)=>{
  const before=fs.lstatSync(directory,{bigint:true});expect(before.isDirectory()).toBe(true);expect(before.isSymbolicLink()).toBe(false);expect(fs.realpathSync(directory)).toBe(directory);
  const names=fs.readdirSync(directory).sort();rows[path.relative(root,directory).split(path.sep).join('/')]={kind:'directory',raw7:raw7(before),names};
  for(const name of names){const file=path.join(directory,name),named=fs.lstatSync(file,{bigint:true});expect(named.isSymbolicLink()).toBe(false);
   if(named.isDirectory()){walk(file);continue;}
   expect(named.isFile()).toBe(true);expect(named.nlink).toBe(1n);expect(fs.realpathSync(file)).toBe(file);
   const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);let first:unknown,failed=false,raw:Buffer|undefined;
   try{expect(raw7(fs.fstatSync(fd,{bigint:true}))).toEqual(raw7(named));raw=fs.readFileSync(fd);expect(BigInt(raw.length)).toBe(named.size);
    expect(raw7(fs.fstatSync(fd,{bigint:true}))).toEqual(raw7(named));expect(raw7(fs.lstatSync(file,{bigint:true}))).toEqual(raw7(named));}
   catch(error){failed=true;first=error;}
   finally{try{fs.closeSync(fd);}catch(error){if(!failed){failed=true;first=error;}}}
   if(failed)throw first;
   rows[path.relative(root,file).split(path.sep).join('/')]={kind:'file',raw7:raw7(named),size:raw!.length,sha256:sha(raw!)};
  }
  expect(fs.readdirSync(directory).sort()).toEqual(names);expect(raw7(fs.lstatSync(directory,{bigint:true}))).toEqual(raw7(before));
 };walk(root);return rows;
}

test('native saved image picker identity and keyboard focus stay owned through A B A project handoff',{tag:'@electron'},async({electronSession,workspace:w,evidence:e})=>{
 test.setTimeout(180_000);const page=electronSession.window,backend=await electronSession.waitForBackend(),io=transport(page,w,e,nativeApi(page,backend.port),'picker-project-handoff');
 let primary:unknown,hasPrimary=false;const cleanupErrors:string[]=[],visits:any[]=[],openingReplies:any[]=[];
 try{
  const make=async(letter:'A'|'B')=>{
   const source=path.join(w.root,'picker-handoff-original-'+letter),folder=path.join(source,'train','good');fs.mkdirSync(folder,{recursive:true});
   const originals=[0,1].map(index=>{const blue=letter==='A'?100+index:101-index,file=path.join(folder,'part-'+index+'.png');pickerHandoffWrite(file,png(256,3,(x,y)=>[x,y,blue]));const raw=fs.readFileSync(file);
    return{path:file,relative:path.relative(source,file).split(path.sep).join('/'),sha256:sha(raw),size:raw.length,blue,pixels:assertSourcePixels(raw,blue)};});
   const project=await io.value('/api/project/create',{name:'Owned picker handoff '+letter,task:'classification'});ownedProject(project,w);expect(project.task).toBe('classification');await io.value('/api/project/update',{source_dataset_dir:source},'PUT');
   const importDeadline=performance.now()+10_000,started=await io.value('/api/dataset/imports',{task:'classification',verify:true},undefined,importDeadline);expect(typeof started.job_id).toBe('string');let imported:any;
   while(performance.now()<importDeadline){imported=await io.value('/api/dataset/imports/'+started.job_id,undefined,undefined,importDeadline);if(['completed','failed','aborted','interrupted'].includes(imported.state))break;await new Promise(resolve=>setTimeout(resolve,Math.min(50,Math.max(0,importDeadline-performance.now()))));}
   expect(performance.now()).toBeLessThanOrEqual(importDeadline);expect(imported.state).toBe('completed');expect(imported.job_id).toBe(started.job_id);expect(imported.source.root).toBe(source);
   const revision=imported.result.revision.revision_id,accepted=await io.value('/api/dataset/imports/'+started.job_id+'/accept',{revision_id:revision,expected_active:null});expect(accepted).toEqual({active_revision:revision,job_id:started.job_id});
   const revisions=await io.value('/api/dataset/revisions');expect(revisions.active_revision).toBe(revision);expect(revisions.revisions).toHaveLength(1);
   const listing=await io.value('/api/dataset/library/images?limit=120');expect(listing.items).toHaveLength(2);expect(listing.revision_id).toBe(revision);expect(listing.active).toBe(true);expect(listing.source_root).toBe(source);expect(listing.next_cursor).toBeNull();
   for(const row of listing.items){const image=originals.find(value=>value.relative===row.relative_path);expect(image).toBeDefined();expect(row.valid===true||row.valid===1).toBe(true);expect(row.sha256).toBe(image!.sha256);expect(row.file_path).toBe(image!.path);expect(typeof row.image_uuid).toBe('string');expect(row.image_uuid.length).toBeGreaterThan(0);expect(sha(fs.readFileSync(row.file_path))).toBe(row.sha256);}
   const first=listing.items.find((row:any)=>row.file_name==='part-0.png');expect(first).toBeDefined();
   return{letter,project,source,originals,revision,started,imported,accepted,listing,first};
  };
  const a=await make('A'),b=await make('B');expect(a.project.id).not.toBe(b.project.id);expect(a.revision).not.toBe(b.revision);expect(a.first.image_uuid).not.toBe(b.first.image_uuid);expect(a.first.sha256).not.toBe(b.first.sha256);expect(a.first.file_name).toBe(b.first.file_name);expect(a.first.relative_path).toBe(b.first.relative_path);
  await io.settle();await page.reload();
  const picker=page.getByRole('dialog',{name:'검사 대상 이미지 선택'}),opener=page.getByRole('button',{name:'이미지 변경...',exact:true}),close=picker.getByRole('button',{name:'닫기 (Esc)',exact:true}),confirm=picker.getByRole('button',{name:'선택 확정',exact:true}),cancel=picker.getByRole('button',{name:'취소',exact:true});
  const expectedSelection=(owner:typeof a)=>({source:'dataset',imagePath:owner.first.file_path,imageId:owner.first.image_uuid,imageUuid:owner.first.image_uuid,sha256:owner.first.sha256,relativePath:owner.first.relative_path,fileName:owner.first.file_name,thumbnailUrl:'/api/dataset/thumbnail/'+encodeURIComponent(owner.first.file_name)+'?file_path='+encodeURIComponent(owner.first.file_path)});
  const preferences=async()=>Object.entries(await io.storage()).filter(([key])=>key.startsWith('modu.inspectionImage.v2:')).map(([key,raw])=>({key,raw,scope:JSON.parse(key.slice('modu.inspectionImage.v2:'.length)),value:JSON.parse(raw)}));
  const selectionFor=async(owner:typeof a)=>{const rows=(await preferences()).filter(row=>row.scope.at(-1)===owner.project.id);expect(rows).toHaveLength(1);expect(rows[0].scope).toHaveLength(4);expect(rows[0].value).toEqual(expectedSelection(owner));return rows[0];};
  const owningCurrent=async(owner:typeof a)=>{await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(owner.project.name);const current=await io.value('/api/project/current');expect(current.id).toBe(owner.project.id);expect(current.project_dir).toBe(owner.project.project_dir);expect(current.source_dataset_dir).toBe(owner.source);expect(current.active_labelset_id).toBe(owner.project.active_labelset_id);return current;};
  const enterFlow=async(owner:typeof a)=>{await owningCurrent(owner);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(4).click();await expect(page.getByRole('heading',{name:'검사 플로우 편집기',exact:true})).toBeVisible();await expect(opener).toBeEnabled();await io.settle();};
  const open=async(owner:typeof a)=>{
   await io.settle();await page.getByTitle('프로젝트 관리',{exact:true}).click();const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});await expect(dialog).toBeVisible();
   const wire=page.waitForResponse(reply=>new URL(reply.url()).pathname==='/api/project/open'&&reply.request().method()==='POST'&&reply.request().postData()===JSON.stringify({project_dir:owner.project.project_dir}),{timeout:10_000});void wire.catch(()=>undefined);
   await dialog.getByRole('button').filter({hasText:owner.project.name}).click();const reply=await wire,captured=await io.captured(reply);expect(captured.status,captured.body).toBe(200);expect(JSON.parse(captured.request.body!)).toEqual({project_dir:owner.project.project_dir});const returned=JSON.parse(captured.body);expect(returned.id).toBe(owner.project.id);expect(returned.source_dataset_dir).toBe(owner.source);expect(returned.project_dir).toBe(owner.project.project_dir);await expect(dialog).toBeHidden();openingReplies.push({owner:owner.letter,...captured});await enterFlow(owner);
  };
  const initialChoose=async(owner:typeof a)=>{
   await enterFlow(owner);await opener.focus();await opener.press('Enter');await expect(picker).toBeVisible();await expect(close).toBeFocused();await expect(confirm).toBeDisabled();await picker.getByRole('button',{name:'검증된 데이터 버전',exact:true}).click();
   const grid=picker.getByRole('list',{name:'데이터 버전 이미지'});await expect(grid.getByRole('listitem')).toHaveCount(2);const item=grid.getByRole('listitem').filter({hasText:owner.first.file_name});await expect(item).toHaveCount(1);await item.focus();await item.press('Enter');await expect(item).toHaveAttribute('aria-pressed','true');await expect(confirm).toBeEnabled();await confirm.focus();await confirm.press('Enter');await expect(picker).toBeHidden();await expect(opener).toBeFocused();await expect(page.getByRole('region',{name:'플로우 식별 정보'})).toContainText(owner.first.file_path);await selectionFor(owner);await io.settle();
  };
  const resolveOpen=async(owner:typeof a)=>{
   await opener.focus();const request={selections:[{image_uuid:owner.first.image_uuid,sha256:owner.first.sha256}]};
   const wire=page.waitForResponse(reply=>new URL(reply.url()).pathname==='/api/dataset/library/resolve'&&reply.request().method()==='POST'&&reply.request().postData()===JSON.stringify(request),{timeout:10_000});void wire.catch(()=>undefined);
   await opener.press('Enter');await expect(picker).toBeVisible();await expect(close).toBeFocused();const reply=await wire,captured=await io.captured(reply),headers=reply.request().headers(),responseHeaders=await reply.allHeaders();expect(captured.status,captured.body).toBe(200);expect(JSON.parse(captured.request.body!)).toEqual(request);expect(headers['x-vision-project']).toBe(owner.project.id);const context=JSON.parse(headers['x-vision-context']);expect(context.project_id).toBe(owner.project.id);expect(context.mode).toBe('local');expect(JSON.parse(responseHeaders['x-vision-context'])).toEqual(context);
   const resolution=JSON.parse(captured.body);expect(resolution.revision_id).toBe(owner.revision);expect(resolution.active).toBe(true);expect(resolution.results).toHaveLength(1);const result=resolution.results[0];expect(result.status).toBe('found');expect(result.current.image_uuid).toBe(owner.first.image_uuid);expect(result.current.sha256).toBe(owner.first.sha256);expect(result.current.relative_path).toBe(owner.first.relative_path);expect(result.current.file_path).toBe(owner.first.file_path);expect(result.current.valid===true||result.current.valid===1).toBe(true);expect(result.candidates).toEqual([]);
   await expect(picker).toContainText('선택: '+owner.first.relative_path);await expect(confirm).toBeEnabled();const grid=picker.getByRole('list',{name:'데이터 버전 이미지'});await expect(grid.getByRole('listitem')).toHaveCount(2);await expect(grid.getByRole('listitem').filter({hasText:owner.first.file_name})).toHaveAttribute('aria-pressed','true');
   return{request,captured,request_headers:headers,response_headers:responseHeaders,context,resolution};
  };
  const revisit=async(owner:typeof a,other:typeof a,tag:string)=>{
   const current=await owningCurrent(owner),resolved=await resolveOpen(owner);await selectionFor(owner);
   await close.focus();await close.press('Shift+Tab');await expect(confirm).toBeFocused();await confirm.press('Tab');await expect(close).toBeFocused();await confirm.focus();await confirm.press('Enter');await expect(picker).toBeHidden();await expect(opener).toBeFocused();const identity=page.getByRole('region',{name:'플로우 식별 정보'});await expect(identity).toContainText(owner.first.file_path);await expect(identity).not.toContainText(other.first.file_path);
   const reopened=await resolveOpen(owner);await close.focus();await close.press('Shift+Tab');await expect(confirm).toBeFocused();await confirm.press('Tab');await expect(close).toBeFocused();await close.press('Escape');await expect(picker).toBeHidden();await expect(opener).toBeFocused();await expect(identity).toContainText(owner.first.file_path);await expect(identity).not.toContainText(other.first.file_path);await io.settle();await e.screenshot(page,'native-picker-'+tag+'-owns-identity-and-opener');visits.push({owner:owner.letter,current,resolved,reopened,preference:await selectionFor(owner)});
  };
  // Prime both owning views and annotation metadata before the immutable A/B/A boundary.
  expect(await preferences()).toEqual([]);await initialChoose(b);await open(a);await initialChoose(a);await open(b);await revisit(b,a,'warm-B');
  await io.value('/api/annotations/'+encodeURIComponent(path.parse(b.first.file_name).name)+'?file_path='+encodeURIComponent(b.first.file_path));await open(a);await revisit(a,b,'warm-A');
  const remembered=await preferences();expect(remembered).toHaveLength(2);const choiceA=await selectionFor(a),choiceB=await selectionFor(b);expect(choiceA.key).not.toBe(choiceB.key);expect(choiceA.scope.slice(0,3)).toEqual(choiceB.scope.slice(0,3));
  const roots={project_A:a.project.project_dir,project_B:b.project.project_dir,source_A:a.source,source_B:b.source,harness_dataset:w.dataset};
  const custody=()=>Object.fromEntries(Object.entries(roots).map(([key,root])=>[key,pickerHandoffCustody(root)]));
  const routes=[...stableRoutes,'/api/dataset/library/images?limit=120','/api/annotations/'+encodeURIComponent(path.parse(a.first.file_name).name)+'?file_path='+encodeURIComponent(a.first.file_path)];
  const before=await io.snapshot('before-ABA',routes,roots),beforeRaw=custody(),writeStart=io.writes.length,visitStart=visits.length;
  await revisit(a,b,'A-first');await open(b);await revisit(b,a,'B');await open(a);await revisit(a,b,'A-return');
  const after=await io.snapshot('after-ABA',routes,roots),afterRaw=custody();expect(after).toEqual(before);expect(afterRaw).toEqual(beforeRaw);expect(await preferences()).toEqual(remembered);
  const actualVisits=visits.slice(visitStart);expect(actualVisits.map(row=>row.owner)).toEqual(['A','B','A']);const expectedWrites:Array<{method:string;route:string;body:unknown}>=[];
  for(const [index,owner]of [a,b,a].entries()){if(index)expectedWrites.push({method:'POST',route:'/api/project/open',body:{project_dir:owner.project.project_dir}});for(let n=0;n<2;n++)expectedWrites.push({method:'POST',route:'/api/dataset/library/resolve',body:{selections:[{image_uuid:owner.first.image_uuid,sha256:owner.first.sha256}]}});}
  assertWrites(io.writes.slice(writeStart),expectedWrites);for(const owner of [a,b])for(const image of owner.originals){const raw=fs.readFileSync(image.path);expect(raw.length).toBe(image.size);expect(sha(raw)).toBe(image.sha256);assertSourcePixels(raw,image.blue);}await io.settle();
  const proof=path.join(w.logs,'picker-project-handoff-custody.json');pickerHandoffWrite(proof,JSON.stringify({roots,beforeRaw,afterRaw,before,after},null,2));e.addFile(proof);
  e.note('native_picker_project_handoff',{cells:[{action:'F064.native-image-picker-keyboard-focus',dimension:'handoff'}],owners:[a,b],openingReplies,visits,actualVisits,choiceA,choiceB,remembered,expectedWrites,proof,
   source_electron:true,actual_owning_project_ABA:true,distinct_source_UUID_SHA_revision:true,original_resolve_headers_and_full_bodies:true,explicit_keyboard_confirmation_and_escape_focus_restore:true,all_protected_roots_raw7_hash_namespace_exact:true,
   native_file_dialog_used:false,model_training_inference_or_flow_execution:false,native_installed_release_acceptance:false,human_quality_approval:false,parent_82_acceptance:false});
 }catch(error){hasPrimary=true;primary=error;}
 finally{
  try{await io.settle();}catch(error){cleanupErrors.push(String(error));if(!hasPrimary){hasPrimary=true;primary=error;}}
  try{io.finish();}catch(error){cleanupErrors.push(String(error));if(!hasPrimary){hasPrimary=true;primary=error;}}
  try{e.note('picker_project_handoff_cleanup',{errors:cleanupErrors,original_primary_preserved:hasPrimary,independent_transport_settle_and_finish_attempted:true});}catch(error){if(!hasPrimary){hasPrimary=true;primary=error;}}
 }
 if(hasPrimary)throw primary;
});
