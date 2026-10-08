import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {inflateSync} from 'node:zlib';
import type {Page,Request,Response} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';

test.use({actionTimeout:10_000});
type Reply={status:number;body:string;url:string};
type Api=(route:string,body?:unknown,method?:string)=>Promise<Reply>;
type Tree=Record<string,{kind:'directory'}|{kind:'file';size:number;sha256:string}>;
type Write={method:string;url:string;body:string|null};
type Clock={request:Request;started:number;deadline:number;finished?:number;status?:number;raw?:string;failure?:unknown;pending?:Promise<void>};
const sha=(raw:Buffer|string)=>crypto.createHash('sha256').update(raw).digest('hex');
const clone=<T,>(value:T):T=>JSON.parse(JSON.stringify(value));

export function canonical(value:any):any{return Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;}
export function assertFullSnapshot(actual:unknown,original:unknown){expect(actual).toEqual(original);}
export function assertDraftRequest(request:Write,origin:string,pipeline:any,context:any){
 expect(request.method).toBe('PUT');expect(request.url).toBe(origin+'/api/flowchart/draft');
 expect(JSON.parse(request.body!)).toEqual({pipeline,context,base_version_id:'none'});
}
export function assertDraftFailure(reply:Pick<Reply,'status'|'body'>){expect(reply.status).toBe(503);expect(JSON.parse(reply.body)).toEqual({detail:'Controlled exact owning draft PUT unavailable'});}
export function assertDraftRecord(record:any,pipeline:any,context:any){
 expect(Object.keys(record).sort()).toEqual(['active_version_id','base_version_id','context','draft_sha256','pipeline','saved_at_ns','version']);
 expect(record.version).toBe(1);expect(record.context).toEqual(context);expect(record.pipeline).toEqual(pipeline);
 expect(record.draft_sha256).toBe(sha(JSON.stringify(canonical(pipeline))));expect(record.base_version_id).toBe('none');expect(record.active_version_id).toBeNull();
 expect(Number.isFinite(record.saved_at_ns)).toBe(true);expect(record.saved_at_ns).toBeGreaterThan(0);
}
export function assertOnlyDraftEffect(before:Record<string,Tree>,after:Record<string,Tree>,relative:string){
 expect(before.project[relative].kind).toBe('file');expect(after.project[relative].kind).toBe('file');
 expect(after.project[relative]).not.toEqual(before.project[relative]);
 const expected=clone(before);expected.project[relative]=after.project[relative];expect(after).toEqual(expected);
}
export function assertSourcePixels(raw:Buffer,blue=100){
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
export function assertAnomalyImport(value:any){
 expect(value).toEqual({status:'success',total_images:2,source_images:2,unlabeled_images:0,classes:{good:2},split:{train:1,val:1,test:0},corrupted_images:[],
  validation:{requested:true,checked_images:2,complete:true,scope:'all images: every image file under the folder was decoded'},split_supported:false,
  split_unavailable_reason:'anomaly 분할은 현재 학습 데이터에 적용되지 않습니다. 원본 데이터의 train/val/test 구성을 사용하세요. / anomaly split is not applied by the training loader; use source train/val/test folders.'});
}

export function assertObservedHistoryReplies(rows:any[],origin:string,source:string,beforeAPI:Record<string,Reply>,pipeline:any){
 const original=(route:string)=>{const reply=beforeAPI[route];expect(reply.status).toBe(200);expect(reply.url).toBe(origin+route);return JSON.parse(reply.body);};
 const project=original('/api/project/current');expect(project.task).toBe('anomaly');expect(project.source_dataset_dir).toBe(source);
 expect(original('/api/dataset/revisions')).toEqual({active_revision:null,revisions:[]});
 expect(original('/api/flowchart/pipeline/active-version')).toEqual({version_id:null});
 expect(original('/api/flowchart/pipelines?source_dataset_path='+encodeURIComponent(source))).toEqual({pipelines:[],total:0});
 for(const node of pipeline.nodes)expect(node.data.model_job_id).toBeNull();
 const libraryURL=origin+'/api/dataset/library/images?state=valid&limit=120';
 const evaluationURL=origin+'/api/evaluation/results?'+new URLSearchParams({source_dataset_path:source,source_task:'anomaly'});
 const counts={missing_revision:0,missing_model:0};
 for(const row of rows){
  for(const value of [row.started,row.deadline,row.finished])expect(Number.isFinite(value)).toBe(true);
  expect(row.deadline).toBe(row.started+10_000);expect(row.finished).toBeGreaterThanOrEqual(row.started);expect(row.finished).toBeLessThanOrEqual(row.deadline);
  expect(typeof row.raw).toBe('string');expect(row.raw_sha256).toBe(sha(row.raw));expect(row.raw_size).toBe(Buffer.byteLength(row.raw));
  if(row.status===200)continue;
  if(row.url===libraryURL){
   expect(row.method).toBe('GET');expect(row.request_body).toBeNull();expect(row.status).toBe(409);
   expect(JSON.parse(row.raw)).toEqual({detail:'No validated dataset revision is active; validate the source and accept a revision first'});counts.missing_revision++;
  }else{
   expect(row.url).toBe(evaluationURL);expect(row.method).toBe('GET');expect(row.request_body).toBeNull();expect(row.status).toBe(404);
   expect(JSON.parse(row.raw)).toEqual({detail:'No completed training job has been selected'});counts.missing_model++;
  }
 }
 expect(counts).toEqual({missing_revision:1,missing_model:1});
}

export function assertInputHistoryBoundary(before:any,after:any,writeSlice:Write[],focused:boolean){
 expect(focused).toBe(true);expect(after.ui).toEqual(before.ui);assertFullSnapshot(after.state,before.state);expect(writeSlice).toEqual([]);
}
export function assertOnlySavedDraftSnapshot(before:any,after:any,relative:string){
 const expectedAPI=clone(before.api);expectedAPI['/api/flowchart/draft']=after.api['/api/flowchart/draft'];expect(after.api).toEqual(expectedAPI);
 expect(after.storage).toEqual(before.storage);assertOnlyDraftEffect(before.trees,after.trees,relative);
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
 const writes:Write[]=[],calls:any[]=[],timings:any[]=[],snapshots:any[]=[],clocks=new Map<Request,Clock>(),preObserverResponses:any[]=[];
 const observed=(request:Request)=>{const endpoint=new URL(request.url()).pathname;if(!endpoint.startsWith('/api/')||request.method()==='OPTIONS')return;
  const started=performance.now();clocks.set(request,{request,started,deadline:started+10_000});
  if(!['GET','HEAD'].includes(request.method()))writes.push({method:request.method(),url:request.url(),body:request.postData()});
 };
 const failed=(request:Request)=>{const row=clocks.get(request);if(row)row.failure=request.failure()?.errorText||'Original request failed';};
 const response=(reply:Response)=>{const row=clocks.get(reply.request());if(!new URL(reply.url()).pathname.startsWith('/api/')||reply.request().method()==='OPTIONS')return;
  if(!row){preObserverResponses.push({method:reply.request().method(),url:reply.url(),status:reply.status(),request_start_clock_unavailable:true});return;}
  expect(row.pending).toBeUndefined();row.status=reply.status();row.pending=(async()=>{let timer:NodeJS.Timeout|undefined;
   try{const [raw,terminal]=await Promise.race([Promise.all([reply.text(),reply.finished()]),new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original response body10s deadline expired')),Math.max(0,row.deadline-performance.now()));})]);
    expect(terminal).toBeNull();row.finished=performance.now();expect(row.finished).toBeLessThanOrEqual(row.deadline);row.raw=raw;
   }catch(error){row.failure=error;}finally{clearTimeout(timer);}
  })();
 };
 page.on('request',observed);page.on('requestfailed',failed);page.on('response',response);
 const settle=async()=>{for(const row of clocks.values()){
  if(!row.pending)await expect.poll(()=>Boolean(row.pending||row.failure),{timeout:Math.max(1,row.deadline-performance.now())}).toBe(true);
  expect(row.failure).toBeUndefined();await row.pending;expect(row.failure).toBeUndefined();expect(typeof row.raw).toBe('string');expect(row.deadline).toBe(row.started+10_000);expect(row.finished!).toBeLessThanOrEqual(row.deadline);
 }};
 const captured=async(reply:Response)=>{const row=clocks.get(reply.request());expect(row).toBeDefined();await row!.pending;expect(row!.failure).toBeUndefined();expect(typeof row!.raw).toBe('string');
  return {status:row!.status!,body:row!.raw!,url:reply.url(),request:{method:reply.request().method(),url:reply.request().url(),body:reply.request().postData()},started:row!.started,deadline:row!.deadline,finished:row!.finished!};};
 const readReply=async(route:string,body?:unknown,method?:string)=>{const started=performance.now(),deadline=started+10_000;let timer:NodeJS.Timeout|undefined;
  try{const reply=await Promise.race([api(route,body,method),new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original fixture fullbody10s deadline expired')),Math.max(0,deadline-performance.now()));})]);
   const finished=performance.now();expect(reply.status,route+': '+reply.body).toBe(200);expect(deadline).toBe(started+10_000);expect(finished).toBeLessThanOrEqual(deadline);
   expect(new URL(reply.url).pathname+new URL(reply.url).search).toBe(route);JSON.parse(reply.body);timings.push({route,started,deadline,finished});return reply;
  }finally{clearTimeout(timer);}
 };
 const value=async(route:string,body?:unknown,method?:string)=>{const reply=await readReply(route,body,method);calls.push({route,method:method||(body===undefined?'GET':'POST'),request:body,...reply});return JSON.parse(reply.body);};
 const source=path.join(w.root,'original-draft-source');fs.mkdirSync(source);const normal=path.join(source,'train','good');fs.mkdirSync(normal,{recursive:true});
 const sourceImages=[100,101].map((blue,index)=>{const file=path.join(normal,index===0?'part.png':'part-1.png');fs.writeFileSync(file,png(256,3,(x,y)=>[x,y,blue]),{flag:'wx'});
  const raw=fs.readFileSync(file);return {path:file,relative:path.relative(source,file).split(path.sep).join('/'),sha256:sha(raw),size:raw.length,blue,RGB:assertSourcePixels(raw,blue)};});
 const image=sourceImages[0].path,originalPNG=fs.readFileSync(image),imageHash=sha(originalPNG),pixelProof=sourceImages[0].RGB;
 const project=await value('/api/project/create',{name:'Owned flow history input boundaries',task:'anomaly'});await value('/api/project/update',{source_dataset_dir:source},'PUT');
 assertAnomalyImport(await value('/api/dataset/import',{folder_path:source,task:'anomaly',validate_images:true}));
 const ownedParent=native?path.join(w.userData,'projects'):w.projects;
 expect(path.dirname(project.project_dir)).toBe(ownedParent);expect(fs.realpathSync(project.project_dir)).toBe(project.project_dir);expect(fs.lstatSync(project.project_dir).isSymbolicLink()).toBe(false);
 const context={project_id:project.id,source_dataset_path:source,labelset_id:project.active_labelset_id||'default'};
 const initial={id:'owned-history-input-boundaries',name:'Original editable no-model flow',nodes:[
  {id:'input-original',position:{x:32,y:170},data:{label:'Input original',node_type:'input'}},
  {id:'roi-original',position:{x:332,y:170},data:{label:'ROI original',node_type:'fixed_roi',params:{roi_bbox:[2,3,20,21]}}},
  {id:'decision-original',position:{x:632,y:170},data:{label:'Decision original',node_type:'decision',rule:'any_defect_is_ng'}},
  {id:'output-original',position:{x:932,y:170},data:{label:'Output original',node_type:'output'}}],edges:[
  {id:'input-roi-original',source:'input-original',target:'roi-original',payload_type:'image'},
  {id:'roi-decision-original',source:'roi-original',target:'decision-original',payload_type:'roi'},
  {id:'decision-output-original',source:'decision-original',target:'output-original',payload_type:'result'}]};
 const saved=await value('/api/flowchart/draft',{pipeline:initial,context,base_version_id:'none'},'PUT');assertDraftRecord(saved,saved.pipeline,context);
 const relative='flowcharts/drafts/'+context.labelset_id+'/'+sha(source).slice(0,16)+'/draft.json',draftFile=path.join(project.project_dir,relative),originalDraft=fs.readFileSync(draftFile);
 const originalDisk=JSON.parse(originalDraft.toString());expect(originalDisk).toEqual(Object.fromEntries(Object.entries(saved).filter(([key])=>key!=='active_version_id')));
 try{
  await settle();if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(4).click();await page.getByRole('tab',{name:'편집',exact:true}).click();
  await expect(page.getByRole('heading',{name:'검사 플로우 편집기',exact:true})).toBeVisible();await expect(page.locator('[data-flow-node-id]')).toHaveCount(4);
  const search=page.getByLabel('플로우 노드 검색',{exact:true});await search.fill('roi-original');await page.getByRole('list',{name:'노드 검색 결과'}).getByRole('button').click();
  const label=page.getByRole('textbox',{name:'노드 명칭',exact:true}),save=page.getByRole('button',{name:'초안 저장',exact:true});
  const undo=page.getByRole('button',{name:'플로우 실행 취소',exact:true}),redo=page.getByRole('button',{name:'플로우 다시 실행',exact:true});
  await expect(label).toHaveValue('ROI original');await expect(undo).toBeDisabled();await expect(redo).toBeDisabled();await expect(save).toBeEnabled();
  await expect(page.getByRole('status').filter({hasText:/^초안 저장됨 · 실행본 활성화 전$/})).toHaveCount(1);
  const rendered=()=>page.evaluate(()=>({nodes:[...document.querySelectorAll<HTMLElement>('[data-flow-node-id]')].map(row=>({id:row.dataset.flowNodeId,label:row.querySelector('h4')?.textContent,x:parseFloat(row.style.left),y:parseFloat(row.style.top)})),edges:[...document.querySelectorAll<SVGElement>('path[data-flow-edge]')].map(row=>({id:row.dataset.flowEdge,source:row.dataset.flowFrom?.split(':')[0],target:row.dataset.flowTo?.split(':')[0]}))}));
  const expectedView=(pipeline:any)=>({nodes:pipeline.nodes.map((row:any)=>({id:row.id,label:row.data.label,x:row.position.x,y:row.position.y})),edges:pipeline.edges.map((row:any)=>({id:row.id,source:row.source,target:row.target}))});
  expect(await rendered()).toEqual(expectedView(saved.pipeline));
  const storage=()=>page.evaluate(()=>Object.fromEntries(Object.entries(localStorage).sort(([a],[b])=>a.localeCompare(b))));
  const endpoints=['/api/project/current','/api/project/labelsets','/api/project/preferences','/api/team-data','/api/team-data/readiness','/api/dataset/metadata?limit=100','/api/dataset/metadata/statistics','/api/dataset/versions','/api/dataset/revisions','/api/flowchart/draft','/api/flowchart/pipeline/active-version','/api/flowchart/pipelines?source_dataset_path='+encodeURIComponent(source)];
  const roots={project:project.project_dir,source,harness_dataset:w.dataset};
  const snapshot=async(tag:string)=>{const replies:Record<string,Reply>={};for(const route of endpoints)replies[route]=await readReply(route);await settle();
   const state={api:replies,trees:Object.fromEntries(Object.entries(roots).map(([key,root])=>[key,protectedTree(root)])),storage:await storage()};
   const folder=path.join(w.logs,'flow-draft-'+tag);fs.mkdirSync(folder);fs.writeFileSync(path.join(folder,'snapshot.json'),JSON.stringify({roots,state},null,2),{flag:'wx'});e.addFile(path.join(folder,'snapshot.json'));
   for(const [key,root]of Object.entries(roots))for(const [name,row]of Object.entries(state.trees[key])){const copy=path.join(folder,key,name);if(row.kind==='directory')fs.mkdirSync(copy,{recursive:true});else{fs.mkdirSync(path.dirname(copy),{recursive:true});fs.copyFileSync(path.join(root,name),copy);e.addFile(copy);}}
   snapshots.push({tag,folder,state});return state;
  };
  const before=await snapshot('before');expect(JSON.parse(before.api['/api/flowchart/draft'].body)).toEqual(saved);expect(JSON.parse(before.api['/api/flowchart/pipeline/active-version'].body)).toEqual({version_id:null});
  const origin=before.api['/api/project/current'].url.replace('/api/project/current','');const setupWrites=[...writes],setupCalls=[...calls];writes.length=0;
  expect(setupWrites.map(row=>({method:row.method,path:new URL(row.url).pathname,body:JSON.parse(row.body!)}))).toEqual(native?setupCalls.filter(row=>row.method!=='GET').map(row=>({method:row.method,path:row.route,body:row.request})):[]);
  const changed=clone(saved.pipeline);changed.nodes.find((row:any)=>row.id==='roi-original').data.label='ROI saved before input-owned history';
  const uiState=async()=>({graph:await rendered(),roi_label:await label.inputValue(),undo_enabled:await undo.isEnabled(),redo_enabled:await redo.isEnabled(),saved_status_count:await page.getByRole('status').filter({hasText:/^초안 저장됨 · 실행본 활성화 전$/}).count()});
  const firstReply=page.waitForResponse(r=>r.request().method()==='PUT'&&r.url()===origin+'/api/flowchart/draft',{timeout:10_000});
  await label.fill('ROI saved before input-owned history');
  const acceptedEdit=await captured(await firstReply);expect(acceptedEdit.status).toBe(200);assertDraftRequest(acceptedEdit.request,origin,changed,context);
  const editedRecord=JSON.parse(acceptedEdit.body);assertDraftRecord(editedRecord,changed,context);
  await expect(page.getByRole('status').filter({hasText:/^초안 저장됨 · 실행본 활성화 전$/})).toHaveCount(1);await expect(undo).toBeEnabled();await expect(redo).toBeDisabled();
  expect(await rendered()).toEqual(expectedView(changed));await expect(label).toHaveValue('ROI saved before input-owned history');
  const undoReady=await snapshot('undo-ready');expect(JSON.parse(undoReady.api['/api/flowchart/draft'].body)).toEqual(editedRecord);assertOnlySavedDraftSnapshot(before,undoReady,relative);
  expect(writes).toEqual([acceptedEdit.request]);const undoUI=await uiState();const undoSliceStart=writes.length;
  await search.fill('roi-original');await search.focus();await page.keyboard.press('Control+z');await expect(search).toBeFocused();
  const afterInputUndo=await snapshot('after-input-undo');const afterUndoUI=await uiState();
  const undoInvalidWrites=writes.slice(undoSliceStart);assertInputHistoryBoundary({state:undoReady,ui:undoUI},{state:afterInputUndo,ui:afterUndoUI},undoInvalidWrites,await search.evaluate(element=>element===document.activeElement));
  await expect(undo).toBeEnabled();await expect(redo).toBeDisabled();expect(fs.readFileSync(draftFile)).not.toEqual(originalDraft);
  const editedRaw=fs.readFileSync(draftFile);expect(JSON.parse(editedRaw.toString())).toEqual(Object.fromEntries(Object.entries(editedRecord).filter(([key])=>key!=='active_version_id')));
  await e.screenshot(page,(native?'native':'browser')+'-input-control-z-retains-available-undo-and-saved-graph');
  const secondReply=page.waitForResponse(r=>r.request().method()==='PUT'&&r.url()===origin+'/api/flowchart/draft',{timeout:10_000});
  await undo.click();const acceptedUndo=await captured(await secondReply);expect(acceptedUndo.status).toBe(200);assertDraftRequest(acceptedUndo.request,origin,saved.pipeline,context);
  const undoneRecord=JSON.parse(acceptedUndo.body);assertDraftRecord(undoneRecord,saved.pipeline,context);expect(undoneRecord.draft_sha256).toBe(saved.draft_sha256);
  await expect(page.getByRole('status').filter({hasText:/^초안 저장됨 · 실행본 활성화 전$/})).toHaveCount(1);await expect(undo).toBeDisabled();await expect(redo).toBeEnabled();
  await expect(label).toHaveValue('ROI original');expect(await rendered()).toEqual(expectedView(saved.pipeline));
  const redoReady=await snapshot('redo-ready');expect(JSON.parse(redoReady.api['/api/flowchart/draft'].body)).toEqual(undoneRecord);assertOnlySavedDraftSnapshot(afterInputUndo,redoReady,relative);
  expect(writes).toEqual([acceptedEdit.request,acceptedUndo.request]);const redoUI=await uiState();const redoSliceStart=writes.length;
  const redoGestures:any[]=[];
  for(const gesture of ['Control+Shift+z','Control+y']){
   await search.fill('roi-original');await search.focus();await page.keyboard.press(gesture);await expect(search).toBeFocused();
   const currentUI=await uiState();expect(currentUI).toEqual(redoUI);expect(writes.slice(redoSliceStart)).toEqual([]);
   redoGestures.push({gesture,input_focused:await search.evaluate(element=>element===document.activeElement),ui:currentUI});
  }
  const afterInputRedo=await snapshot('after-input-redo');const afterRedoUI=await uiState();
  const redoInvalidWrites=writes.slice(redoSliceStart);assertInputHistoryBoundary({state:redoReady,ui:redoUI},{state:afterInputRedo,ui:afterRedoUI},redoInvalidWrites,await search.evaluate(element=>element===document.activeElement));
  await expect(undo).toBeDisabled();await expect(redo).toBeEnabled();await expect(label).toHaveValue('ROI original');
  expect(writes).toHaveLength(2);assertDraftRequest(writes[0],origin,changed,context);assertDraftRequest(writes[1],origin,saved.pipeline,context);
  expect(JSON.parse(fs.readFileSync(draftFile,'utf8'))).toEqual(Object.fromEntries(Object.entries(undoneRecord).filter(([key])=>key!=='active_version_id')));
  assertOnlySavedDraftSnapshot(before,afterInputRedo,relative);expect(fs.existsSync(path.join(project.project_dir,'flowcharts/active.json'))).toBe(false);expect(fs.readFileSync(image)).toEqual(originalPNG);
  for(const row of sourceImages){const raw=fs.readFileSync(row.path);expect(raw.length).toBe(row.size);expect(sha(raw)).toBe(row.sha256);assertSourcePixels(raw,row.blue);}for(const row of w.images)expect(sha(fs.readFileSync(row.path))).toBe(row.sha256);
  await e.screenshot(page,(native?'native':'browser')+'-input-control-shift-z-and-control-y-retain-available-redo');
  await settle();const observedReplies=[...clocks.values()].map(({request,pending:_pending,failure:_failure,...clock})=>({method:request.method(),url:request.url(),request_body:request.postData(),...clock,raw_sha256:sha(clock.raw!),raw_size:Buffer.byteLength(clock.raw!)}));
  assertObservedHistoryReplies(observedReplies,origin,source,before.api,saved.pipeline);
  const proof={cells:['U012.undo.invalid','U012.redo.invalid'],requirement:'S2-08',project,context,source:{path:image,sha256:imageHash,RGB:pixelProof,root:source,images:sourceImages,layout:'train/good normal-only; original disjoint partition train1/val1/test0'},roots,
   original_saved_record:saved,original_raw_draft_sha256:sha(originalDraft),changed_pipeline:changed,accepted_edit:acceptedEdit,edited_record:editedRecord,accepted_undo:acceptedUndo,undone_record:undoneRecord,
   undo_input_gesture:'Control+z',undo_before_ui:undoUI,undo_after_ui:afterUndoUI,redo_before_ui:redoUI,redo_gestures:redoGestures,redo_after_ui:afterRedoUI,
   snapshots,setup_calls:setupCalls,setup_renderer_writes:setupWrites,declared_history_setup_writes:writes,whole_renderer_writes:[...setupWrites,...writes],
   invalid_undo_writes:undoInvalidWrites,invalid_redo_writes:redoInvalidWrites,fixture_read_clocks:timings,original_observed_request_body_clocks:observedReplies,pre_observer_responses:preObserverResponses,
   scope:{source_electron:native,actual_original_source_ui:true,installed_target:false,model_or_GPU_execution:false,quality_or_human_or_parent_approval:false,
    actual_gestures:'search input owns focus; Control+z with Undo available, Control+Shift+z and Control+y with Redo available',
    input_text_native_undo_unchanged_claim:false,internal_history_stack_exported:false,observable_graph_and_history_button_states_preserved:true,
    declared_setup:'two real existing650ms autosaves: ROI label edit then actual toolbar Undo; neither is an invalid action',
    autosave_elapsed650ms_independently_measured:false,undo_error_or_cancel_covered:false,redo_error_or_cancel_covered:false}};
  const file=path.join(w.logs,'flow-history-input-boundaries-proof.json');fs.writeFileSync(file,JSON.stringify(proof,null,2),{flag:'wx'});e.addFile(file);e.note('flow_history_input_boundaries',{proof_path:file,proof_sha256:sha(fs.readFileSync(file)),proof_size:fs.statSync(file).size});
 }finally{await settle();page.off('request',observed);page.off('requestfailed',failed);page.off('response',response);}
}

test('input-focused history shortcuts preserve available undo and redo plus saved source',async({page,request,renderer,workspace,evidence})=>{test.setTimeout(160_000);await installDesktopHostShim(page,renderer.port);
 const api:Api=async(route,body,method)=>{const reply=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});return{status:reply.status(),body:await reply.text(),url:reply.url()};};await exercise(page,workspace,evidence,api,false,renderer.url);});
test('native input-focused history shortcuts preserve graph and durable draft boundary', {tag:'@electron'},async({electronSession,workspace,evidence})=>{test.setTimeout(160_000);const backend=await electronSession.waitForBackend();
 const api:Api=(route,body,method)=>electronSession.window.evaluate(async({port,route,body,method})=>{const reply=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});return{status:reply.status,body:await reply.text(),url:reply.url};},{port:backend.port,route,body,method});await exercise(electronSession.window,workspace,evidence,api,true);});
