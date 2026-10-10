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


test('native independent direction rejects invalid values and persists an intentional empty label',{tag:'@electron'},async({electronSession,workspace:w,evidence:e})=>{
 test.setTimeout(180_000);const page=electronSession.window,backend=await electronSession.waitForBackend(),io=transport(page,w,e,nativeApi(page,backend.port),'direction-boundaries');
 try{
  const source=path.join(w.root,'direction-original-source');fs.mkdirSync(source);const image=path.join(source,'part.png');fs.writeFileSync(image,png(256,3,(x,y)=>[x,y,100]),{flag:'wx'});
  const original=fs.readFileSync(image),pixels=assertSourcePixels(original),originalHash=sha(original);
  const project=await io.value('/api/project/create',{name:'Owned independent direction boundaries',task:'segmentation'});ownedProject(project,w);
  await io.value('/api/project/update',{source_dataset_dir:source},'PUT');await io.value('/api/dataset/import',{folder_path:source,task:'segmentation',validate_images:true});
  await io.settle();await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();await page.getByRole('button',{name:'집중 편집',exact:true}).click();
  await expect.poll(async()=>Number((await page.getByTestId('canvas-hud').innerText()).match(/scale\s*([\d.]+)\s*%/)?.[1]||0)).toBeGreaterThan(100);
  await page.getByTitle('100% Zoom (1:1)',{exact:true}).click();await expect(page.getByTestId('canvas-hud')).toContainText('100%');
  const point=async(x:number,y:number)=>{const b=await page.locator('[data-canvas-container]').boundingBox();expect(b).not.toBeNull();return{x:b!.x+(b!.width-256)/2+x,y:b!.y+(b!.height-256)/2+y};};
  const drag=async(x1:number,y1:number,x2:number,y2:number)=>{const a=await point(x1,y1),b=await point(x2,y2);await page.mouse.move(a.x,a.y);await page.mouse.down();await page.mouse.move(b.x,b.y,{steps:8});await page.mouse.up();};
  const query='/api/annotations/part?file_path='+encodeURIComponent(image);
  const save=async()=>{const pending=page.waitForResponse(reply=>new URL(reply.url()).pathname==='/api/annotations/save'&&reply.request().method()==='POST');await page.getByRole('button',{name:'Save Changes',exact:true}).click();const reply=await io.captured(await pending);expect(reply.status,reply.body).toBe(200);expect(JSON.parse(reply.body).status).toBe('saved');return{reply,annotation:await io.value(query)};};
  await page.getByTitle('바운딩 박스 (BBox - 2)',{exact:true}).click();await drag(20,20,70,60);const boxInitial=await save();
  await page.getByRole('button',{name:/^Crack(?: \d+)?$/}).click();await page.getByTitle('회전 바운딩 박스 (Rotated BBox OBB - 3)',{exact:true}).click();await page.getByTitle('100% Zoom (1:1)',{exact:true}).click();await drag(70,150,150,190);
  const direction=page.getByLabel('객체 독립 방향 라벨',{exact:true}),angle=page.getByText('Angle (θ)',{exact:true}).locator('..').locator('..').getByRole('spinbutton');
  await direction.fill('315');await angle.fill('30');const initial=await save();expect(initial.annotation.annotations).toHaveLength(2);
  const obb=initial.annotation.annotations.find((row:any)=>row.type==='rotated_bbox');expect(obb.rotated_bbox).toEqual([110,170,80,40,30]);expect(obb.direction_deg).toBe(315);
  const current=await io.value('/api/project/current');expect(current.id).toBe(project.id);expect(current.source_dataset_dir).toBe(source);expect(current.active_labelset_id).toBe(project.active_labelset_id);
  const mask=initial.annotation.mask_file;expect(path.dirname(mask)).toBe(path.join(current.annotations_dir,'by_dataset',sha(source).slice(0,16),'masks'));
  const label=path.join(path.dirname(path.dirname(mask)),'part.json'),workflow=path.join(path.dirname(label),'metadata','workflow.json');
  const labelRelative=path.relative(project.project_dir,label).split(path.sep).join('/'),workflowRelative=path.relative(project.project_dir,workflow).split(path.sep).join('/');
  expect(labelRelative.startsWith('../')).toBe(false);expect(workflowRelative.startsWith('../')).toBe(false);expect(fs.existsSync(label)).toBe(true);expect(fs.existsSync(workflow)).toBe(true);
  const roots={project:project.project_dir,source,harness_dataset:w.dataset},routes=[...stableRoutes,query];
  const refresh={method:'POST',route:'/api/dataset/import',body:{folder_path:source,task:'segmentation',validate_images:false}};
  const refreshRecords=[...io.calls.filter(row=>row.route==='/api/dataset/import'&&row.request?.validate_images===false)];
  // annotationsChanged refreshes the original manifest after each actual GUI save.
  // The renderer observer retains both complete raw replies and the exact write tuples.
  expect(refreshRecords).toEqual([]);
  assertWrites(io.writes,[...io.calls.filter(row=>!['GET','HEAD'].includes(row.method)).map(row=>({method:row.method,route:row.route,body:row.request})),
    ...[boxInitial,initial].flatMap(row=>[{method:'POST',route:'/api/annotations/save',body:JSON.parse(row.reply.request.body!)},refresh])]);
  const before=await io.snapshot('before-invalid',routes,roots),beforeLabel=fs.readFileSync(label),beforeWorkflow=fs.readFileSync(workflow),beforeMask=fs.readFileSync(mask);const writeStart=io.writes.length;
  for(const invalid of ['-1','360']){await direction.fill(invalid);await direction.press('Tab');await expect(direction).toHaveValue('315');await expect(angle).toHaveValue('30');await expect(page.getByTestId('annotation-save-button')).toContainText('Saved');}
  await io.settle();await page.reload();await expect(page.getByRole('button',{name:/^Crack 1$/})).toBeVisible();await page.getByTitle('Delete annotation',{exact:true}).nth(1).locator('..').locator('..').click();
  await expect(direction).toHaveValue('315');await expect(angle).toHaveValue('30');
  const afterInvalid=await io.snapshot('after-invalid-reopen',routes,roots);expect(afterInvalid).toEqual(before);expect(io.writes.slice(writeStart)).toEqual([]);
  await e.screenshot(page,'native-direction-invalid-existing315-angle30-reopened');
  const emptyStart=io.writes.length;await direction.fill('');await direction.press('Tab');await expect(direction).toHaveValue('');await expect(angle).toHaveValue('30');
  const empty=await save(),request=JSON.parse(empty.reply.request.body!);expect(empty.reply.request.method).toBe('POST');expect(request.image_id).toBe('part');expect(request.image_path).toBe(image);expect(request.expected_revision).toBe(initial.annotation.metadata.revision);
  const expectedRequest=clone(JSON.parse(initial.reply.request.body!));expectedRequest.expected_revision=initial.annotation.metadata.revision;expectedRequest.annotations=initial.annotation.annotations.map((row:any)=>({id:row.id,type:row.type,label:row.label,category_id:row.category_id,bbox:row.bbox,polygon:row.polygon||row.points,points:row.points||row.polygon,is_normal:row.is_normal,color:row.color,rotated_bbox:row.rotated_bbox,direction_deg:row.direction_deg,mask_rle:row.mask_rle}));delete expectedRequest.annotations.find((row:any)=>row.type==='rotated_bbox').direction_deg;expect(request).toEqual(clone(expectedRequest));
  expect(request.annotations).toHaveLength(2);const requestedOBB=request.annotations.find((row:any)=>row.type==='rotated_bbox');expect(Object.hasOwn(requestedOBB,'direction_deg')).toBe(false);expect(requestedOBB.rotated_bbox).toEqual(obb.rotated_bbox);
  const emptyOBB=empty.annotation.annotations.find((row:any)=>row.type==='rotated_bbox');expect(emptyOBB.direction_deg??null).toBeNull();expect(emptyOBB.direction_deg).not.toBe(0);
  const expectedAnnotations=clone(initial.annotation.annotations);expectedAnnotations.find((row:any)=>row.type==='rotated_bbox').direction_deg=null;expect(empty.annotation.annotations).toEqual(expectedAnnotations);
  expect(empty.annotation.metadata.revision).toBe(initial.annotation.metadata.revision+1);expect(empty.annotation.metadata.image_uuid).toBe(initial.annotation.metadata.image_uuid);expect(empty.annotation.metadata.content_hash).toBe(originalHash);expect(empty.annotation.metadata.mask_hash).toBe(initial.annotation.metadata.mask_hash);
  assertWrites(io.writes.slice(emptyStart),[{method:'POST',route:'/api/annotations/save',body:request},refresh]);
  await io.settle();await page.reload();await expect(page.getByRole('button',{name:/^Crack 1$/})).toBeVisible();await page.getByTitle('Delete annotation',{exact:true}).nth(1).locator('..').locator('..').click();await expect(direction).toHaveValue('');await expect(angle).toHaveValue('30');
  const afterEmpty=await io.snapshot('after-empty-reopen',routes,roots),expectedTree=clone(before.trees);const changedPaths=Object.keys(afterEmpty.trees.project).filter(name=>JSON.stringify(afterEmpty.trees.project[name])!==JSON.stringify(before.trees.project[name]));
  expect(changedPaths.sort()).toEqual([labelRelative,workflowRelative].sort());for(const name of changedPaths)expectedTree.project[name]=afterEmpty.trees.project[name];expect(afterEmpty.trees).toEqual(expectedTree);expect(afterEmpty.storage).toEqual(before.storage);
  for(const route of stableRoutes.filter(route=>!route.startsWith('/api/dataset/metadata')))expect(afterEmpty.api[route]).toEqual(before.api[route]);
  const savedJSON=JSON.parse(fs.readFileSync(label,'utf8')),expectedJSON=JSON.parse(beforeLabel.toString());expectedJSON.annotations=expectedAnnotations;expect(savedJSON).toEqual(expectedJSON);expect(fs.readFileSync(mask)).toEqual(beforeMask);
  const priorLedger=JSON.parse(beforeWorkflow.toString()),nextLedger=JSON.parse(fs.readFileSync(workflow,'utf8')),priorRow=priorLedger.images['part.png'],nextRow=nextLedger.images['part.png'];
  expect(nextRow.image_uuid).toBe(priorRow.image_uuid);expect(nextRow.revision).toBe(priorRow.revision+1);expect(nextRow.content_hash).toBe(priorRow.content_hash);expect(nextRow.content_version).toBe(priorRow.content_version);expect(nextRow.mask_hash).toBe(priorRow.mask_hash);expect(nextRow.annotation_hash).toBe(sha(fs.readFileSync(label)));expect(nextRow.audit.slice(0,-1)).toEqual(priorRow.audit);expect(nextRow.audit.at(-1).action).toBe('annotation_changed');
  const allowedLedger=clone(priorLedger);allowedLedger.images['part.png']=nextRow;expect(nextLedger).toEqual(allowedLedger);expect(fs.readFileSync(image)).toEqual(original);expect(sha(fs.readFileSync(image))).toBe(originalHash);assertSourcePixels(fs.readFileSync(image));
  await io.settle();assertWrites(io.writes.slice(emptyStart),[{method:'POST',route:'/api/annotations/save',body:request},refresh]);await e.screenshot(page,'native-direction-intentional-empty-angle30-reopened');
  e.note('independent_direction_boundaries',{cells:[{action:'U020.independent-direction-input',dimension:'invalid'},{action:'U020.independent-direction-input',dimension:'empty'}],project,current,source,image,pixels,original_sha256:originalHash,initial,empty,changedPaths,labelRelative,workflowRelative,
   source_electron:true,actual_UI_input_and_save:true,invalid_rejected_in_renderer_without_write:true,invalid_backend_422_claimed:false,empty_is_absent_not_zero:true,all_complete_protected_trees_unfiltered:true,only_owned_label_and_workflow_changed:true,model_training_or_inference:false,human_annotation_quality_approval:false,parent_82_acceptance:false});
 }finally{io.finish();}
});

// SOURCE-only append: Root alone executes and qualifies this prospective cell.
import {installDesktopHostShim as directionHandoffHost} from './fixtures/desktop-host-shim';
import {handoffApi as directionHandoffApi, handoffProject as directionHandoffProject,
 handoffLateRead as directionHandoffLateRead, handoffWithin as directionHandoffWithin,
 handoffSave as directionHandoffSave, type HandoffApi as DirectionHandoffApi} from './fixtures/remaining-project-handoff';

type DirectionHandoffScope={tag:'A'|'B';project:any;source:string;image:string;source_sha256:string;direction:number;angle:number;saved?:any};
const directionHandoffIdentity=(s:fs.BigIntStats)=>[s.dev,s.ino,s.mode,s.nlink,s.size,s.mtimeNs,s.ctimeNs].map(String);
function directionHandoffTree(root:string):Record<string,unknown>{
 const rows:Record<string,unknown>={};
 try{fs.lstatSync(root);}catch(error){if((error as NodeJS.ErrnoException).code==='ENOENT')return{'.':{kind:'absent'}};throw error;}
 const visit=(file:string)=>{
  const named=fs.lstatSync(file,{bigint:true}),member=path.relative(root,file).split(path.sep).join('/')||'.';
  expect(named.isSymbolicLink()).toBe(false);expect(fs.realpathSync(file)).toBe(file);
  if(named.isDirectory()){
   rows[member]={kind:'directory',identity:directionHandoffIdentity(named)};
   const names=fs.readdirSync(file).sort();for(const name of names)visit(path.join(file,name));
   expect(fs.readdirSync(file).sort()).toEqual(names);expect(directionHandoffIdentity(fs.lstatSync(file,{bigint:true}))).toEqual(directionHandoffIdentity(named));return;
  }
  expect(named.isFile()).toBe(true);expect(named.nlink).toBe(1n);
  const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);let primary:unknown;
  try{
   expect(directionHandoffIdentity(fs.fstatSync(fd,{bigint:true}))).toEqual(directionHandoffIdentity(named));
   const raw=fs.readFileSync(fd);expect(BigInt(raw.length)).toBe(named.size);
   expect(directionHandoffIdentity(fs.fstatSync(fd,{bigint:true}))).toEqual(directionHandoffIdentity(named));
   expect(directionHandoffIdentity(fs.lstatSync(file,{bigint:true}))).toEqual(directionHandoffIdentity(named));
   rows[member]={kind:'file',identity:directionHandoffIdentity(named),sha256:sha(raw),size:raw.length};
  }catch(error){primary=error;throw error;}finally{try{fs.closeSync(fd);}catch(error){if(primary===undefined)throw error;}}
 };visit(root);return rows;
}
function directionHandoffOwnedBrowserProject(project:any,w:Workspace){expect(path.dirname(project.project_dir)).toBe(w.projects);expect(fs.realpathSync(project.project_dir)).toBe(project.project_dir);expect(fs.lstatSync(project.project_dir).isSymbolicLink()).toBe(false);}
async function directionHandoffMake(api:DirectionHandoffApi,w:Workspace,tag:'A'|'B'):Promise<DirectionHandoffScope>{
 const source=path.join(w.root,'direction-handoff-source-'+tag);fs.mkdirSync(source);
 const image=path.join(source,'part.png'),blue=tag==='A'?100:101;
 fs.writeFileSync(image,png(256,3,(x,y)=>[x,y,blue]),{flag:'wx'});const raw=fs.readFileSync(image);assertSourcePixels(raw,blue);
 const made=await api('/api/project/create',{name:'Owned independent direction handoff '+tag,task:'segmentation'});directionHandoffOwnedBrowserProject(made,w);
 await api('/api/project/update',{source_dataset_dir:source},'PUT');
 await api('/api/dataset/import',{folder_path:source,task:'segmentation',validate_images:true});
 const project=await api('/api/project/current');expect(project.id).toBe(made.id);expect(project.source_dataset_dir).toBe(source);expect(project.task).toBe('segmentation');
 await api('/api/project/labelsets');await api('/api/team-data');await api('/api/team-data/readiness');
 const metadata=(await api('/api/dataset/metadata?limit=100')).items;expect(metadata).toHaveLength(1);
 await api('/api/team-data/images/'+metadata[0].image_uuid);await api('/api/annotations/part?file_path='+encodeURIComponent(image));
 return{tag,project,source,image,source_sha256:sha(raw),direction:tag==='A'?315:45,angle:tag==='A'?30:60};
}
async function directionHandoffExercise(page:Page,w:Workspace,e:Evidence,origin:string,url:string){
 const api=directionHandoffApi(page,origin,false),A=await directionHandoffMake(api,w,'A'),B=await directionHandoffMake(api,w,'B');
 expect(A.project.id).not.toBe(B.project.id);expect(A.project.project_dir).not.toBe(B.project.project_dir);expect(A.source_sha256).not.toBe(B.source_sha256);
 const query=(scope:DirectionHandoffScope)=>'/api/annotations/part?file_path='+encodeURIComponent(scope.image);
 const direction=page.getByLabel('객체 독립 방향 라벨',{exact:true});
 const angle=page.getByText('Angle (θ)',{exact:true}).locator('..').locator('..').getByRole('spinbutton');
 const save=page.getByTestId('annotation-save-button'),proof:any[]=[];
 const current=async(scope:DirectionHandoffScope)=>{
  const value=await api('/api/project/current');expect([value.id,value.project_dir,value.source_dataset_dir,value.task,value.active_labelset_id])
   .toEqual([scope.project.id,scope.project.project_dir,scope.source,scope.project.task,scope.project.active_labelset_id]);return value;
 };
 const enter=async(scope:DirectionHandoffScope)=>{
  await current(scope);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
  const focus=page.getByRole('button',{name:'집중 편집',exact:true});if(await focus.getAttribute('aria-pressed')!=='true')await focus.click();
  await expect(page.locator('[data-canvas-container]')).toBeVisible();await expect(save).toBeEnabled();
 };
 const owningContext=async(request:Request,scope:DirectionHandoffScope,deadline:number)=>{
  expect(request.frame()).toBe(page.mainFrame());expect(new URL(request.url()).origin).toBe(origin);
  expect(await directionHandoffWithin(request.headerValue('x-vision-project'),deadline,'owning project header')).toBe(scope.project.id);
  const raw=await directionHandoffWithin(request.headerValue('x-vision-context'),deadline,'owning request context');expect(raw).not.toBeNull();
  const context=JSON.parse(raw!);expect(context.project_id).toBe(scope.project.id);expect(context.mode).toBe('local');
  expect(context.workspace_id).toEqual(expect.any(String));expect(context.actor_id).toEqual(expect.any(String));return context;
 };
 const keepRaw=(label:string,raw:Buffer)=>{
  const file=path.join(w.logs,'direction-'+label+'-original-response.json');fs.writeFileSync(file,raw,{flag:'wx'});e.addFile(file);return{path:file,size:raw.length,sha256:sha(raw)};
 };
 const readAnnotation=async(scope:DirectionHandoffScope)=>{
  await current(scope);const deadline=performance.now()+10_000,route=query(scope);
  const response=await directionHandoffWithin(page.request.get(origin+route,{timeout:Math.max(1,Math.floor(deadline-performance.now()))}),deadline,'owning fixture annotation GET');
  expect(response.status()).toBe(200);expect(new URL(response.url()).pathname+new URL(response.url()).search).toBe(route);
  const raw=await directionHandoffWithin(response.body(),deadline,'owning fixture annotation complete raw body');expect(raw.length).toBeLessThanOrEqual(1024*1024);
  proof.push({project_id:scope.project.id,source:scope.source,image_path:scope.image,method:'GET',path:'/api/annotations/part',status:200,
   transport:'owned fixture API read; not original renderer HTTP provenance',raw:keepRaw(scope.tag+'-annotation-observer-'+proof.length,raw)});
  return JSON.parse(raw.toString('utf8'));
 };
 const responseBody=async(response:Response,scope:DirectionHandoffScope,deadline:number,label:string)=>{
  expect(response.status()).toBe(200);const context=await owningContext(response.request(),scope,deadline);
  const raw=await directionHandoffWithin(response.body(),deadline,'complete '+label+' body');expect(raw.length).toBeLessThanOrEqual(1024*1024);
  expect(await directionHandoffWithin(response.finished(),deadline,label+' finished')).toBeNull();
  const row={project_id:scope.project.id,context,method:response.request().method(),path:new URL(response.url()).pathname,request:response.request().postDataJSON(),status:200,raw:keepRaw(label,raw)};
  proof.push(row);return{body:JSON.parse(raw.toString('utf8')),proof:row};
 };
 const checkSaved=(scope:DirectionHandoffScope,value:any)=>{
  expect(value.image_id).toBe('part');expect([value.image_width,value.image_height]).toEqual([256,256]);expect(value.annotations).toHaveLength(1);
  const obb=value.annotations[0];expect(obb.type).toBe('rotated_bbox');expect(obb.label).toBe('Crack');expect(obb.direction_deg).toBe(scope.direction);
  expect(obb.rotated_bbox).toEqual([110,170,80,40,scope.angle]);expect(value.metadata.file_path).toBe(scope.image);expect(value.metadata.content_hash).toBe(scope.source_sha256);
  expect(value.metadata.image_uuid).toEqual(expect.any(String));expect(value.metadata.image_uuid.length).toBeGreaterThan(0);
  if(scope.saved)expect(value).toEqual(scope.saved);return value;
 };
 const prepare=async(scope:DirectionHandoffScope)=>{
  const before=await readAnnotation(scope);expect(before.annotations).toEqual([]);const frame=performance.now()+10_000;
  await directionHandoffWithin((async()=>{
   // Source LabelingCanvas installs initial fit on actual image load. Wait for its pixels BEFORE ordinary 1:1.
   await page.waitForFunction(()=>{const canvas=document.querySelector<HTMLCanvasElement>('[data-canvas-container] > canvas:first-of-type');
    if(!canvas||canvas.width===0||canvas.height===0)return false;const context=canvas.getContext('2d');return Boolean(context&&context.getImageData(Math.floor(canvas.width/2),Math.floor(canvas.height/2),1,1).data[3]>0);
   },undefined,{timeout:10_000});
   await page.getByTitle('100% Zoom (1:1)',{exact:true}).click();await page.getByRole('button',{name:/^Crack(?: \d+)?$/}).click();
   await page.getByTitle('회전 바운딩 박스 (Rotated BBox OBB - 3)',{exact:true}).click();
   await expect(page.getByTestId('canvas-hud')).toContainText(/VIEW\s*scale\s*100\s*%/,{timeout:10_000});
   await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))));
   await expect(page.getByTestId('canvas-hud')).toContainText(/VIEW\s*scale\s*100\s*%/,{timeout:10_000});
  })(),frame,'loaded original pixels and stable 1:1');
  const box=await page.locator('[data-canvas-container]').boundingBox();expect(box).not.toBeNull();
  const point=(x:number,y:number)=>({x:box!.x+(box!.width-256)/2+x,y:box!.y+(box!.height-256)/2+y});const from=point(70,150),to=point(150,190);
  await page.mouse.move(from.x,from.y);await page.mouse.down();await page.mouse.move(to.x,to.y,{steps:8});await page.mouse.up();
  await expect(direction).toBeVisible();await direction.fill(String(scope.direction));await angle.fill(String(scope.angle));
  await expect(direction).toHaveValue(String(scope.direction));await expect(angle).toHaveValue(String(scope.angle));
  const deadline=performance.now()+10_000;
  const pending=page.waitForResponse(r=>new URL(r.url()).origin===origin&&new URL(r.url()).pathname==='/api/annotations/save'&&r.request().method()==='POST'&&r.request().frame()===page.mainFrame(),{timeout:10_000});
  const imported=page.waitForResponse(r=>new URL(r.url()).origin===origin&&new URL(r.url()).pathname==='/api/dataset/import'&&r.request().method()==='POST'&&r.request().frame()===page.mainFrame()&&r.request().postDataJSON()?.folder_path===scope.source&&r.request().postDataJSON()?.validate_images===false,{timeout:10_000});
  const [saved,refresh]=await directionHandoffWithin(Promise.all([pending,imported,save.click()]),deadline,'actual save and original manifest refresh');
  const savedRaw=await responseBody(saved,scope,deadline,scope.tag+'-saved'),request=saved.request().postDataJSON();
  expect(savedRaw.body.status).toBe('saved');expect(request.image_id).toBe('part');expect(request.image_path).toBe(scope.image);expect(request.expected_revision).toBe(before.metadata.revision);
  expect([request.image_width,request.image_height]).toEqual([256,256]);expect(request.annotations).toHaveLength(1);
  expect(request.annotations[0].direction_deg).toBe(scope.direction);expect(request.annotations[0].rotated_bbox).toEqual([110,170,80,40,scope.angle]);
  expect(refresh.request().postDataJSON()).toEqual({folder_path:scope.source,task:'segmentation',validate_images:false});await responseBody(refresh,scope,deadline,scope.tag+'-manifest-refreshed');
  scope.saved=checkSaved(scope,await readAnnotation(scope));expect(scope.saved.metadata.revision).toBe(before.metadata.revision+1);expect(scope.saved.metadata.image_uuid).toBe(before.metadata.image_uuid);
  expect(savedRaw.body.metadata).toEqual(scope.saved.metadata);await expect(save).toContainText('Saved');
  await e.screenshot(page,'direction-'+scope.tag+'-distinct-direction-and-axis-saved');
 };
 const selectSaved=async(scope:DirectionHandoffScope)=>{
  await current(scope);await expect(page.getByTitle('Delete annotation',{exact:true})).toHaveCount(1);
  await page.getByTitle('Delete annotation',{exact:true}).locator('..').locator('..').click();
  await expect(direction).toHaveValue(String(scope.direction));await expect(angle).toHaveValue(String(scope.angle));await expect(save).toContainText('Saved');
  return checkSaved(scope,await readAnnotation(scope));
 };
 const state=async(scope:DirectionHandoffScope)=>{
  await current(scope);const annotation=checkSaved(scope,await readAnnotation(scope)),metadata=(await api('/api/dataset/metadata?limit=100')).items;
  expect(metadata).toHaveLength(1);expect(metadata[0].image_uuid).toBe(annotation.metadata.image_uuid);expect(metadata[0].content_hash).toBe(scope.source_sha256);
  const labelsets=await api('/api/project/labelsets');const roots={source:scope.source,annotations:scope.project.annotations_dir,models:scope.project.models_dir,reports:scope.project.reports_dir,dataset:scope.project.dataset_dir,labelsets:path.join(scope.project.project_dir,'labelsets'),labelset_registry:path.join(scope.project.project_dir,'labelsets.json')};
  return{annotation,metadata,labelsets,roots:Object.fromEntries(Object.entries(roots).map(([name,root])=>[name,{root,tree:directionHandoffTree(root)}]))};
 };
 const writes:Array<{method:string;path:string;body:unknown}>=[];
 const observe=(request:Request)=>{const u=new URL(request.url());if(u.origin===origin&&u.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(request.method()))writes.push({method:request.method(),path:u.pathname,body:request.postDataJSON()});};
 let late:Awaited<ReturnType<typeof directionHandoffLateRead>>|undefined,primary:unknown;
 try{
  await page.goto(url);await directionHandoffProject(page,A,origin);await enter(A);await prepare(A);const beforeA=await state(A);
  await directionHandoffProject(page,B,origin);await enter(B);await prepare(B);const beforeB=await state(B);
  expect(A.saved.metadata.image_uuid).not.toBe(B.saved.metadata.image_uuid);expect(A.saved.annotations[0].id).not.toBe(B.saved.annotations[0].id);
  await directionHandoffProject(page,A,origin);await enter(A);await selectSaved(A);expect(await state(A)).toEqual(beforeA);
  page.on('request',observe);
  late=await directionHandoffLateRead(page,origin,false,u=>u.pathname==='/api/annotations/part'&&u.searchParams.get('file_path')===A.image);
  const oldDeadline=performance.now()+10_000;
  const oldRequest=page.waitForRequest(request=>{const u=new URL(request.url());return u.origin===origin&&u.pathname==='/api/annotations/part'&&u.searchParams.get('file_path')===A.image&&request.method()==='GET'&&request.frame()===page.mainFrame();},{timeout:10_000});
  const [originalRequest]=await directionHandoffWithin(Promise.all([oldRequest,page.reload()]),oldDeadline,'real reopened A annotation request');
  const oldContext=await owningContext(originalRequest,A,oldDeadline),captured=await directionHandoffWithin(late.ready(),oldDeadline,'original A complete saved label response');checkSaved(A,captured.body);
  const heldRaw=keepRaw('A-held-owning-read',captured.bytes),toB=await directionHandoffProject(page,B,origin);await enter(B);await selectSaved(B);
  const disposition=await late.finish();await expect(direction).toHaveValue(String(B.direction));await expect(angle).toHaveValue(String(B.angle));const afterB=await state(B);expect(afterB).toEqual(beforeB);
  await e.screenshot(page,'direction-B-owning-values-after-old-A-response');
  const toA=await directionHandoffProject(page,A,origin);await enter(A);await selectSaved(A);const afterA=await state(A);expect(afterA).toEqual(beforeA);
  await e.screenshot(page,'direction-A-original315-axis30-restored-after-handoff');
  expect(writes).toEqual([toB,toA].map(row=>({method:'POST',path:'/api/project/open',body:{project_dir:row.project_dir}})));
  directionHandoffSave(e,w,'direction-input-handoff-custody',{beforeA,beforeB,afterA,afterB,owning_save_and_refresh_responses:proof,transitions:[toB,toA],held_original_A_read:{context:oldContext,raw_response:heldRaw,disposition},business_mutations_after_baseline:writes});
  e.note('direction_input_project_handoff',{action:'U020.independent-direction-input',dimensions:['handoff'],actual_browser_UI:true,
   two_owning_UI_annotation_saves:true,distinct_project_and_image_UUIDs:true,same_relative_filename_distinct_original_pixels:true,
   independent_direction_and_axial_angle_exact:true,actual_A_B_A_project_open:[toB,toA],late_A_original_browser_response:disposition,
   complete_source_annotation_model_report_dataset_labelset_trees_and_registry_unchanged:true,annotation_or_dataset_writes_after_baseline:0,
   source_Electron:false,installed_native:false,model_training:false,model_inference:false,human_quality:false,parent_target_acceptance:false});
 }catch(error){primary=error;throw error;}finally{
  let cleanupError:unknown;
  try{page.off('request',observe);}catch(error){cleanupError=error;}
  if(late){try{await late.close();}catch(error){if(cleanupError===undefined)cleanupError=error;}}
  if(primary===undefined&&cleanupError!==undefined)throw cleanupError;
 }
}
test('independent direction and axial angle stay scoped through A B A project handoff and an old owning label read',async({page,renderer,workspace,evidence})=>{
 await directionHandoffHost(page,renderer.port);await directionHandoffExercise(page,workspace,evidence,renderer.origin,renderer.url);
});
