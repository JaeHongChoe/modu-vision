import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {inflateSync} from 'node:zlib';
import type {Page,Request,Route,Response} from '@playwright/test';
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
const DETAIL='Controlled exact owning draft PUT unavailable';

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

export function assertObservedDraftReplies(rows:any[],origin:string,source:string,beforeAPI:Record<string,Reply>,pipeline:any){
 const original=(route:string)=>{const reply=beforeAPI[route];expect(reply.status).toBe(200);expect(reply.url).toBe(origin+route);return JSON.parse(reply.body);};
 const project=original('/api/project/current');expect(project.task).toBe('anomaly');expect(project.source_dataset_dir).toBe(source);
 expect(original('/api/dataset/revisions')).toEqual({active_revision:null,revisions:[]});
 expect(original('/api/flowchart/pipeline/active-version')).toEqual({version_id:null});
 expect(original('/api/flowchart/pipelines?source_dataset_path='+encodeURIComponent(source))).toEqual({pipelines:[],total:0});
 for(const node of pipeline.nodes)expect(node.data.model_job_id).toBeNull();
 const libraryURL=origin+'/api/dataset/library/images?state=valid&limit=120';
 const evaluationURL=origin+'/api/evaluation/results?'+new URLSearchParams({source_dataset_path:source,source_task:'anomaly'});
 const counts={draft_failure:0,missing_revision:0,missing_model:0};
 for(const row of rows){
  for(const value of [row.started,row.deadline,row.finished])expect(Number.isFinite(value)).toBe(true);
  expect(row.deadline).toBe(row.started+10_000);expect(row.finished).toBeGreaterThanOrEqual(row.started);expect(row.finished).toBeLessThanOrEqual(row.deadline);
  expect(typeof row.raw).toBe('string');expect(row.raw_sha256).toBe(sha(row.raw));expect(row.raw_size).toBe(Buffer.byteLength(row.raw));
  if(row.status===200)continue;
  if(row.url===origin+'/api/flowchart/draft'&&row.method==='PUT'){
   assertDraftFailure({status:row.status,body:row.raw});counts.draft_failure++;
  }else if(row.url===libraryURL){
   expect(row.method).toBe('GET');expect(row.request_body).toBeNull();expect(row.status).toBe(409);
   expect(JSON.parse(row.raw)).toEqual({detail:'No validated dataset revision is active; validate the source and accept a revision first'});counts.missing_revision++;
  }else{
   expect(row.url).toBe(evaluationURL);expect(row.method).toBe('GET');expect(row.request_body).toBeNull();expect(row.status).toBe(404);
   expect(JSON.parse(row.raw)).toEqual({detail:'No completed training job has been selected'});counts.missing_model++;
  }
 }
 expect(counts).toEqual({draft_failure:1,missing_revision:1,missing_model:1});
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
 const project=await value('/api/project/create',{name:'Owned flow draft save error',task:'anomaly'});await value('/api/project/update',{source_dataset_dir:source},'PUT');
 assertAnomalyImport(await value('/api/dataset/import',{folder_path:source,task:'anomaly',validate_images:true}));
 const ownedParent=native?path.join(w.userData,'projects'):w.projects;
 expect(path.dirname(project.project_dir)).toBe(ownedParent);expect(fs.realpathSync(project.project_dir)).toBe(project.project_dir);expect(fs.lstatSync(project.project_dir).isSymbolicLink()).toBe(false);
 const context={project_id:project.id,source_dataset_path:source,labelset_id:project.active_labelset_id||'default'};
 const initial={id:'owned-draft-error',name:'Original editable no-model flow',nodes:[
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
 let fault:((route:Route)=>Promise<void>)|undefined;const pattern='**/api/flowchart/draft';let faultCount=0;let faultRequest:Write|undefined;
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
  const changed=clone(saved.pipeline);changed.nodes.find((row:any)=>row.id==='roi-original').data.label='ROI retained unsaved503';
  fault=async(route:Route)=>{const request=route.request();if(request.method()!=='PUT'){await route.continue();return;}
   faultCount++;faultRequest={method:request.method(),url:request.url(),body:request.postData()};assertDraftRequest(faultRequest,origin,changed,context);
   await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:DETAIL})});
  };
  await page.route(pattern,fault);const failedReply=page.waitForResponse(r=>r.request().method()==='PUT'&&r.url()===origin+'/api/flowchart/draft',{timeout:10_000});
  await label.fill('ROI retained unsaved503');const rejected=await captured(await failedReply);assertDraftFailure(rejected);await expect(page.getByText(DETAIL,{exact:true})).toBeVisible();
  await expect(save).toBeEnabled();await expect(page.getByRole('status').filter({hasText:/^편집 초안 · 변경 사항 미저장$/})).toHaveCount(1);await expect(undo).toBeEnabled();await expect(redo).toBeDisabled();
  await expect(label).toHaveValue('ROI retained unsaved503');expect(await rendered()).toEqual(expectedView(changed));expect(faultCount).toBe(1);
  const afterError=await snapshot('after-error');assertFullSnapshot(afterError,before);expect(fs.readFileSync(draftFile)).toEqual(originalDraft);
  expect(writes).toEqual([faultRequest]);assertDraftRequest(writes[0],origin,changed,context);expect(faultCount).toBe(1);
  await e.screenshot(page,(native?'native':'browser')+'-draft-put503-preserves-original-and-dirty-editor');
  await page.unroute(pattern, fault);fault=undefined;const acceptedReply=page.waitForResponse(r=>r.request().method()==='PUT'&&r.url()===origin+'/api/flowchart/draft',{timeout:10_000});
  await save.click();const accepted=await captured(await acceptedReply);expect(accepted.status).toBe(200);assertDraftRequest(accepted.request,origin,changed,context);const recovered=JSON.parse(accepted.body);assertDraftRecord(recovered,changed,context);
  await expect(page.getByText(DETAIL,{exact:true})).toHaveCount(0);await expect(page.getByRole('status').filter({hasText:/^초안 저장됨 · 실행본 활성화 전$/})).toHaveCount(1);await expect(save).toBeEnabled();
  await expect(label).toHaveValue('ROI retained unsaved503');await expect(undo).toBeEnabled();await expect(redo).toBeDisabled();expect(await rendered()).toEqual(expectedView(changed));
  const after=await snapshot('after-explicit-save');const afterDraft=JSON.parse(after.api['/api/flowchart/draft'].body);expect(afterDraft).toEqual(recovered);assertDraftRecord(afterDraft,changed,context);
  const expectedAPI=clone(before.api);expectedAPI['/api/flowchart/draft']=after.api['/api/flowchart/draft'];expect(after.api).toEqual(expectedAPI);expect(after.storage).toEqual(before.storage);
  assertOnlyDraftEffect(before.trees,after.trees,relative);expect(JSON.parse(fs.readFileSync(draftFile,'utf8'))).toEqual(Object.fromEntries(Object.entries(recovered).filter(([key])=>key!=='active_version_id')));
  expect(writes).toHaveLength(2);for(const row of writes)assertDraftRequest(row,origin,changed,context);expect(writes[0]).toEqual(writes[1]);expect(faultCount).toBe(1);
  expect(fs.existsSync(path.join(project.project_dir,'flowcharts/active.json'))).toBe(false);expect(fs.readFileSync(image)).toEqual(originalPNG);
  for(const row of sourceImages){const raw=fs.readFileSync(row.path);expect(raw.length).toBe(row.size);expect(sha(raw)).toBe(row.sha256);assertSourcePixels(raw,row.blue);}for(const row of w.images)expect(sha(fs.readFileSync(row.path))).toBe(row.sha256);
  await e.screenshot(page,(native?'native':'browser')+'-explicit-original-draft-save200-readback-recovers-same-edit');
  await settle();const observedReplies=[...clocks.values()].map(({request,pending:_pending,failure:_failure,...clock})=>({method:request.method(),url:request.url(),request_body:request.postData(),...clock,raw_sha256:sha(clock.raw!),raw_size:Buffer.byteLength(clock.raw!)}));
  assertObservedDraftReplies(observedReplies,origin,source,before.api,saved.pipeline);
  const proof={cell:'U012.save-draft.error',project,context,source:{path:image,sha256:imageHash,RGB:pixelProof,root:source,images:sourceImages,layout:'train/good normal-only; original disjoint partition train1/val1/test0'},roots,original_saved_record:saved,original_raw_draft_sha256:sha(originalDraft),changed_pipeline:changed,
   rejected,accepted,recovered,after_error_preserved:true,snapshots,setup_calls:setupCalls,setup_renderer_writes:setupWrites,post_baseline_writes:writes,whole_renderer_writes:[...setupWrites,...writes],fixture_read_clocks:timings,original_observed_request_body_clocks:observedReplies,pre_observer_responses:preObserverResponses,
   scope:{source_electron: native,actual_original_source_ui:true,installed_target:false,model_or_GPU_execution: false,cancel_covered:false,quality_or_human_or_parent_approval:false,failed_same_target_policy:'650ms autosave failure suppresses same-target automatic retry; only explicit save retries',policy_source_controls_separate_from_actual_response_slice:true}};
  const file=path.join(w.logs,'flow-draft-save-error-proof.json');fs.writeFileSync(file,JSON.stringify(proof,null,2),{flag:'wx'});e.addFile(file);e.note('flow_draft_save_error',{proof_path:file,proof_sha256:sha(fs.readFileSync(file)),proof_size:fs.statSync(file).size});
 }finally{if(fault)await page.unroute(pattern,fault);await settle();page.off('request',observed);page.off('requestfailed',failed);page.off('response',response);}
}

test('owning draft PUT503 preserves saved graph and explicitly recovers same dirty edit',async({page,request,renderer,workspace,evidence})=>{test.setTimeout(160_000);await installDesktopHostShim(page,renderer.port);
 const api:Api=async(route,body,method)=>{const reply=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});return{status:reply.status(),body:await reply.text(),url:reply.url()};};await exercise(page,workspace,evidence,api,false,renderer.url);});
test('native owning draft PUT503 retains original source and explicit save200 readback', {tag:'@electron'},async({electronSession,workspace,evidence})=>{test.setTimeout(160_000);const backend=await electronSession.waitForBackend();
 const api:Api=(route,body,method)=>electronSession.window.evaluate(async({port,route,body,method})=>{const reply=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});return{status:reply.status,body:await reply.text(),url:reply.url};},{port:backend.port,route,body,method});await exercise(electronSession.window,workspace,evidence,api,true);});

// SOURCE-only append. Root alone executes and qualifies the owned fixture.
import {handoffApi as draftHandoffApi, handoffProject as draftHandoffProject,
  handoffWithin as draftHandoffWithin, handoffSave as draftHandoffSave,
  type HandoffApi as DraftHandoffApi} from './fixtures/remaining-project-handoff';

type DraftHandoffScope={tag:'A'|'B';project:any;source:string;context:any;seed:any;saved:any;
  roiId:string;label:string;draftFile:string;images:Array<{path:string;size:number;sha256:string;blue:number}>;readback?:any};
type DraftHandoffClock={request:Request;started:number;deadline:number;pending?:Promise<void>;failure?:unknown;
  finished?:number;status?:number;raw?:Buffer;context?:any;project?:string};
const draftHandoffRaw7=(s:fs.BigIntStats)=>[s.dev,s.ino,s.mode,s.nlink,s.size,s.mtimeNs,s.ctimeNs].map(String);

// Complete declared trees, including their directories and absent roots. No
// lifecycle SQLite stores outside these explicit roots are claimed here.
function draftHandoffTree(root:string):Record<string,unknown>{
 const result:Record<string,unknown>={};
 try{fs.lstatSync(root,{bigint:true});}catch(error){if((error as NodeJS.ErrnoException).code==='ENOENT')return{'.':{kind:'absent'}};throw error;}
 const visit=(file:string)=>{
  const named=fs.lstatSync(file,{bigint:true}),identity=draftHandoffRaw7(named),member=path.relative(root,file).split(path.sep).join('/')||'.';
  expect(named.isSymbolicLink()).toBe(false);expect(fs.realpathSync(file)).toBe(file);
  if(named.isDirectory()){
   const names=fs.readdirSync(file).sort();result[member]={kind:'directory',identity};
   for(const name of names)visit(path.join(file,name));
   expect(fs.readdirSync(file).sort()).toEqual(names);expect(draftHandoffRaw7(fs.lstatSync(file,{bigint:true}))).toEqual(identity);return;
  }
  expect(named.isFile()).toBe(true);expect(named.nlink).toBe(1n);
  const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);let primary:unknown;
  try{
   expect(draftHandoffRaw7(fs.fstatSync(fd,{bigint:true}))).toEqual(identity);const raw=fs.readFileSync(fd);
   expect(BigInt(raw.length)).toBe(named.size);expect(draftHandoffRaw7(fs.fstatSync(fd,{bigint:true}))).toEqual(identity);
   expect(draftHandoffRaw7(fs.lstatSync(file,{bigint:true}))).toEqual(identity);result[member]={kind:'file',identity,size:raw.length,sha256:sha(raw)};
  }catch(error){primary=error;throw error;}finally{try{fs.closeSync(fd);}catch(error){if(primary===undefined)throw error;}}
 };visit(root);return result;
}

function draftHandoffPixels(raw:Buffer,blue:number){
 let cursor=8,header:Buffer|undefined;const chunks:Buffer[]=[];expect(raw.subarray(0,8)).toEqual(Buffer.from([137,80,78,71,13,10,26,10]));
 while(cursor<raw.length){const count=raw.readUInt32BE(cursor),kind=raw.toString('ascii',cursor+4,cursor+8);expect(cursor+count+12).toBeLessThanOrEqual(raw.length);
  if(kind==='IHDR'){expect(header).toBeUndefined();header=raw.subarray(cursor+8,cursor+8+count);}if(kind==='IDAT')chunks.push(raw.subarray(cursor+8,cursor+8+count));cursor+=count+12;}
 expect(cursor).toBe(raw.length);expect(header).toBeDefined();expect([...header!]).toEqual([...Buffer.from([0,0,1,0,0,0,1,0,8,2,0,0,0])]);
 const actual=inflateSync(Buffer.concat(chunks)),expected=Buffer.alloc(256*769);
 for(let y=0;y<256;y++)for(let x=0;x<256;x++){const offset=y*769+1+x*3;expected[offset]=x;expected[offset+1]=y;expected[offset+2]=blue;}
 expect(actual.equals(expected)).toBe(true);return{width:256,height:256,pixels:65536,channels:3,blue};
}

async function draftHandoffMake(api:DraftHandoffApi,w:Workspace,tag:'A'|'B'):Promise<DraftHandoffScope>{
 const source=path.join(w.root,'saved-flow-draft-source-'+tag),normal=path.join(source,'train','good');fs.mkdirSync(normal,{recursive:true});
 const images=[0,1].map(index=>{
  const blue=(tag==='A'?100:120)+index,file=path.join(normal,index?'part-1.png':'part.png');fs.writeFileSync(file,png(256,3,(x,y)=>[x,y,blue]),{flag:'wx'});
  const raw=fs.readFileSync(file);draftHandoffPixels(raw,blue);return{path:file,size:raw.length,sha256:sha(raw),blue};
 });
 const made=await api('/api/project/create',{name:'Owned saved flow draft handoff '+tag,task:'anomaly'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');assertAnomalyImport(await api('/api/dataset/import',{folder_path:source,task:'anomaly',validate_images:true}));
 const project=await api('/api/project/current');expect(project.id).toBe(made.id);expect(project.source_dataset_dir).toBe(source);expect(path.dirname(project.project_dir)).toBe(w.projects);
 const context={project_id:project.id,source_dataset_path:source,labelset_id:project.active_labelset_id||'default'},suffix=tag.toLowerCase(),roiId='roi-'+suffix;
 const pipeline={id:'owned-draft-handoff-'+suffix,name:'Owned editable no-model flow '+tag,nodes:[
  {id:'input-'+suffix,position:{x:32,y:170},data:{label:'Input '+tag,node_type:'input'}},
  {id:roiId,position:{x:332,y:170},data:{label:'ROI initial '+tag,node_type:'fixed_roi',params:{roi_bbox:tag==='A'?[2,3,20,21]:[12,13,40,41]}}},
  {id:'decision-'+suffix,position:{x:632,y:170},data:{label:'Decision '+tag,node_type:'decision',rule:'any_defect_is_ng'}},
  {id:'output-'+suffix,position:{x:932,y:170},data:{label:'Output '+tag,node_type:'output'}}],edges:[
  {id:'input-roi-'+suffix,source:'input-'+suffix,target:roiId,payload_type:'image'},
  {id:'roi-decision-'+suffix,source:roiId,target:'decision-'+suffix,payload_type:'roi'},
  {id:'decision-output-'+suffix,source:'decision-'+suffix,target:'output-'+suffix,payload_type:'result'}]};
 const seed=await api('/api/flowchart/draft',{pipeline,context,base_version_id:'none'},'PUT');assertDraftRecord(seed,seed.pipeline,context);
 for(const node of seed.pipeline.nodes)expect(node.data.model_job_id).toBeNull();
 await api('/api/project/labelsets');await api('/api/team-data');await api('/api/team-data/readiness');await api('/api/dataset/metadata?limit=100');
 for(const image of images)await api('/api/annotations/part?file_path='+encodeURIComponent(image.path));
 const draftFile=path.join(project.project_dir,'flowcharts','drafts',context.labelset_id,sha(source).slice(0,16),'draft.json');
 expect(JSON.parse(fs.readFileSync(draftFile,'utf8'))).toEqual(Object.fromEntries(Object.entries(seed).filter(([key])=>key!=='active_version_id')));
 return{tag,project,source,context,seed,saved:seed,roiId,label:'ROI persisted '+tag,draftFile,images};
}

async function draftHandoffExercise(page:Page,w:Workspace,e:Evidence,origin:string,url:string){
 const api=draftHandoffApi(page,origin,false),A=await draftHandoffMake(api,w,'A'),B=await draftHandoffMake(api,w,'B');
 expect(A.project.id).not.toBe(B.project.id);expect(A.project.project_dir).not.toBe(B.project.project_dir);expect(A.source).not.toBe(B.source);
 expect(A.seed.draft_sha256).not.toBe(B.seed.draft_sha256);expect(new Set([...A.images,...B.images].map(row=>row.sha256)).size).toBe(4);
 const writes:Array<{request:Request;method:string;path:string;body:any}>=[],clocks=new Map<Request,DraftHandoffClock>();let primary:unknown,baseline=false;
 const observed=(request:Request)=>{
  const u=new URL(request.url());if(u.origin!==origin||!u.pathname.startsWith('/api/')||request.method()==='OPTIONS')return;
  if(!['GET','HEAD'].includes(request.method()))writes.push({request,method:request.method(),path:u.pathname,body:request.postDataJSON()});
  if(u.pathname==='/api/flowchart/draft'){const started=performance.now();clocks.set(request,{request,started,deadline:started+10_000});}
 };
 const failed=(request:Request)=>{const row=clocks.get(request);if(row)row.failure=request.failure()?.errorText||'Original draft request failed';};
 const replied=(response:Response)=>{
  const row=clocks.get(response.request());if(!row)return;
  row.pending=(async()=>{try{
   const deadline=row.deadline;row.status=response.status();expect(row.status).toBe(200);expect(row.request.frame()).toBe(page.mainFrame());
   row.project=(await draftHandoffWithin(row.request.headerValue('x-vision-project'),deadline,'original draft owner header'))!;
   const context=await draftHandoffWithin(row.request.headerValue('x-vision-context'),deadline,'original draft context header');expect(context).not.toBeNull();row.context=JSON.parse(context!);
   row.raw=await draftHandoffWithin(response.body(),deadline,'original complete draft response');expect(row.raw.length).toBeLessThanOrEqual(1024*1024);
   expect(await draftHandoffWithin(response.finished(),deadline,'original draft response finished')).toBeNull();row.finished=performance.now();expect(row.finished).toBeLessThanOrEqual(deadline);
  }catch(error){row.failure=error;}})();
 };
 const drain=async()=>{for(const row of clocks.values()){
  if(!row.pending)await draftHandoffWithin(expect.poll(()=>Boolean(row.pending||row.failure),{timeout:Math.max(1,row.deadline-performance.now())}).toBe(true),row.deadline,'original draft response arrival');
  expect(row.failure).toBeUndefined();await row.pending;expect(row.failure).toBeUndefined();expect(row.deadline).toBe(row.started+10_000);
  expect(row.raw).toBeDefined();expect(row.finished!).toBeLessThanOrEqual(row.deadline);
 }};
 const rendered=()=>page.evaluate(()=>({nodes:[...document.querySelectorAll<HTMLElement>('[data-flow-node-id]')].map(row=>({id:row.dataset.flowNodeId,label:row.querySelector('h4')?.textContent,x:parseFloat(row.style.left),y:parseFloat(row.style.top)})),edges:[...document.querySelectorAll<SVGElement>('path[data-flow-edge]')].map(row=>({id:row.dataset.flowEdge,source:row.dataset.flowFrom?.split(':')[0],target:row.dataset.flowTo?.split(':')[0]}))}));
 const view=(pipeline:any)=>({nodes:pipeline.nodes.map((row:any)=>({id:row.id,label:row.data.label,x:row.position.x,y:row.position.y})),edges:pipeline.edges.map((row:any)=>({id:row.id,source:row.source,target:row.target}))});
 const savedStatus=page.getByRole('status').filter({hasText:/^초안 저장됨 · 실행본 활성화 전$/}),save=page.getByRole('button',{name:'초안 저장',exact:true});
 const enter=async(scope:DraftHandoffScope,reopened:boolean)=>{
  expect(await api('/api/project/current')).toEqual(scope.project);
  await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name);
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(4).click();await page.getByRole('tab',{name:'편집',exact:true}).click();
  await expect(page.getByRole('heading',{name:'검사 플로우 편집기',exact:true})).toBeVisible();await expect(page.locator('[data-flow-node-id]')).toHaveCount(4);
  await expect.poll(rendered).toEqual(view(scope.saved.pipeline));await expect(savedStatus).toHaveCount(1);await expect(save).toBeEnabled();
  if(reopened){await expect(page.getByRole('button',{name:'플로우 실행 취소',exact:true})).toBeDisabled();await expect(page.getByRole('button',{name:'플로우 다시 실행',exact:true})).toBeDisabled();}
  await page.getByLabel('플로우 노드 검색',{exact:true}).fill(scope.roiId);await page.getByRole('list',{name:'노드 검색 결과'}).getByRole('button').click();
  await expect(page.getByRole('textbox',{name:'노드 명칭',exact:true})).toHaveValue(scope.saved.pipeline.nodes.find((row:any)=>row.id===scope.roiId).data.label);await drain();
 };
 const responses:any[]=[],snapshots:any[]=[],transitions:any[]=[];
 const rawResponse=(label:string,raw:Buffer)=>{const file=path.join(w.logs,label+'.json');fs.writeFileSync(file,raw,{flag:'wx'});e.addFile(file);return{path:file,size:raw.length,sha256:sha(raw)};};
 const savePrepared=async(scope:DraftHandoffScope)=>{
  const changed=clone(scope.seed.pipeline);changed.nodes.find((row:any)=>row.id===scope.roiId).data.label=scope.label;
  const capture=async(response:Response,kind:'650ms-autosave'|'explicit-button')=>{
   const row=clocks.get(response.request());expect(row).toBeDefined();await drain();expect(row!.failure).toBeUndefined();
   expect(row!.project).toBe(scope.project.id);expect(row!.context.project_id).toBe(scope.project.id);expect(row!.context.mode).toBe('local');
   expect(typeof row!.context.workspace_id).toBe('string');expect(typeof row!.context.actor_id).toBe('string');
   assertDraftRequest({method:response.request().method(),url:response.url(),body:response.request().postData()},origin,changed,scope.context);
   const record=JSON.parse(row!.raw!.toString('utf8'));assertDraftRecord(record,changed,scope.context);scope.saved=record;
   await expect(savedStatus).toHaveCount(1);await expect(save).toBeEnabled();await expect.poll(rendered).toEqual(view(changed));await drain();
   expect(await api('/api/flowchart/draft')).toEqual(record);
   const disk=fs.readFileSync(scope.draftFile);expect(JSON.parse(disk.toString('utf8'))).toEqual(Object.fromEntries(Object.entries(record).filter(([key])=>key!=='active_version_id')));
   responses.push({kind,project_id:scope.project.id,context:scope.context,actual_owner_context:row!.context,started:row!.started,deadline:row!.deadline,finished:row!.finished,
    method:'PUT',path:'/api/flowchart/draft',status:200,request:response.request().postDataJSON(),response:record,raw_response:rawResponse('saved-flow-'+scope.tag+'-'+kind,row!.raw!),disk_sha256:sha(disk)});
  };
  const automaticDeadline=performance.now()+10_000;
  const automatic=page.waitForResponse(r=>r.url()===origin+'/api/flowchart/draft'&&r.request().method()==='PUT'&&r.request().frame()===page.mainFrame(),{timeout:Math.max(1,automaticDeadline-performance.now())});
  await draftHandoffWithin(page.getByRole('textbox',{name:'노드 명칭',exact:true}).fill(scope.label),automaticDeadline,'ordinary node edit');
  await capture(await draftHandoffWithin(automatic,automaticDeadline,'genuine650ms autosave'),'650ms-autosave');
  // The same already-clean graph is explicitly saved by its real owning
  // button. Both responses are finished before any custody baseline.
  const explicitDeadline=performance.now()+10_000;
  const explicit=page.waitForResponse(r=>r.url()===origin+'/api/flowchart/draft'&&r.request().method()==='PUT'&&r.request().frame()===page.mainFrame(),{timeout:Math.max(1,explicitDeadline-performance.now())});
  await draftHandoffWithin(save.click(),explicitDeadline,'ordinary explicit draft button');await capture(await draftHandoffWithin(explicit,explicitDeadline,'explicit draft save'),'explicit-button');
  await page.evaluate(()=>new Promise<void>(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>r()))));await expect(savedStatus).toHaveCount(1);await expect(save).toBeEnabled();await drain();
 };
 const readback=async(scope:DraftHandoffScope)=>{
  const value={project:await api('/api/project/current'),draft:await api('/api/flowchart/draft'),labelsets:await api('/api/project/labelsets'),
   metadata:await api('/api/dataset/metadata?limit=100'),active:await api('/api/flowchart/pipeline/active-version'),pipelines:await api('/api/flowchart/pipelines?source_dataset_path='+encodeURIComponent(scope.source)),
   annotations:await Promise.all(scope.images.map(image=>api('/api/annotations/part?file_path='+encodeURIComponent(image.path)))),revisions:await api('/api/dataset/revisions')};
  expect(value.project).toEqual(scope.project);expect(value.draft).toEqual(scope.saved);assertDraftRecord(value.draft,scope.saved.pipeline,scope.context);
  expect(value.metadata.items).toHaveLength(2);expect(new Set(value.metadata.items.map((row:any)=>row.image_uuid)).size).toBe(2);
  expect(value.active).toEqual({version_id:null});expect(value.pipelines).toEqual({pipelines:[],total:0});
  expect(JSON.parse(fs.readFileSync(scope.draftFile,'utf8'))).toEqual(Object.fromEntries(Object.entries(scope.saved).filter(([key])=>key!=='active_version_id')));
  return value;
 };
 const custody=(scope:DraftHandoffScope)=>{
  const roots={source:scope.source,annotations:scope.project.annotations_dir,models:scope.project.models_dir,reports:scope.project.reports_dir,dataset:scope.project.dataset_dir,
   labelsets:path.join(scope.project.project_dir,'labelsets'),flowcharts:path.join(scope.project.project_dir,'flowcharts')};
  const value={roots,trees:Object.fromEntries(Object.entries(roots).map(([key,root])=>[key,draftHandoffTree(root)])),
   project_config:draftHandoffTree(path.join(scope.project.project_dir,'project.json')),labelset_registry:draftHandoffTree(path.join(scope.project.project_dir,'labelsets.json'))};
  expect(fs.existsSync(path.join(scope.project.project_dir,'flowcharts','active.json'))).toBe(false);
  for(const image of scope.images){const raw=fs.readFileSync(image.path);expect(raw.length).toBe(image.size);expect(sha(raw)).toBe(image.sha256);draftHandoffPixels(raw,image.blue);}return value;
 };
 const both=()=>({A:custody(A),B:custody(B),harness_dataset:draftHandoffTree(w.dataset)});
 let baselineTrees:ReturnType<typeof both>|undefined;
 page.on('request',observed);page.on('requestfailed',failed);page.on('response',replied);
 try{
  await page.goto(url);await enter(B,true);await savePrepared(B);B.readback=await readback(B);await e.screenshot(page,'saved-flow-B-actual-put200-before-handoff');
  transitions.push(await draftHandoffProject(page,A,origin));await enter(A,true);await savePrepared(A);A.readback=await readback(A);await e.screenshot(page,'saved-flow-A-actual-put200-before-handoff');
  await drain();baselineTrees=both();expect(writes.filter(row=>row.path==='/api/flowchart/draft')).toHaveLength(4);
  for(const scope of[A,B])expect(writes.filter(row=>row.path==='/api/flowchart/draft'&&row.body.context.project_id===scope.project.id)).toHaveLength(2);
  const setupWrites=writes.map(({request:_request,...row})=>row);writes.length=0;const clockStart=clocks.size;baseline=true;
  snapshots.push({tag:'A-before',state:baselineTrees,readback:A.readback});
  transitions.push(await draftHandoffProject(page,B,origin));await enter(B,true);expect(await readback(B)).toEqual(B.readback);await drain();expect(both()).toEqual(baselineTrees);
  snapshots.push({tag:'B-after',state:both(),readback:B.readback});await e.screenshot(page,'saved-flow-B-own-graph-and-hash-after-A-to-B');
  transitions.push(await draftHandoffProject(page,A,origin));await enter(A,true);expect(await readback(A)).toEqual(A.readback);await drain();expect(both()).toEqual(baselineTrees);
  snapshots.push({tag:'A-return',state:both(),readback:A.readback});await e.screenshot(page,'saved-flow-A-return-own-graph-and-hash');
  expect(writes.map(({request:_request,...row})=>row)).toEqual([{method:'POST',path:'/api/project/open',body:{project_dir:B.project.project_dir}},{method:'POST',path:'/api/project/open',body:{project_dir:A.project.project_dir}}]);
  const owningReads=[...clocks.values()].slice(clockStart);expect(owningReads.length).toBeGreaterThanOrEqual(2);
  for(const scope of[B,A])expect(owningReads.some(row=>row.request.method()==='GET'&&row.project===scope.project.id&&JSON.parse(row.raw!.toString('utf8')).draft_sha256===scope.saved.draft_sha256)).toBe(true);
  const actualDraftReplies=[...clocks.values()].map((row,index)=>({method:row.request.method(),url:row.request.url(),request:row.request.postData(),project_id:row.project,owner_context:row.context,
   started:row.started,deadline:row.deadline,finished:row.finished,status:row.status,raw_response:rawResponse('saved-flow-observed-reply-'+index,row.raw!)}));
  draftHandoffSave(e,w,'flow-draft-project-handoff-proof',{schema:'modu-vision.flow-draft-project-handoff-proof/v1',cells:['U012.save-draft.handoff'],projects:[A.project,B.project],contexts:[A.context,B.context],
   originals:[A.images,B.images],seeds:[A.seed,B.seed],saved_graphs:[A.saved,B.saved],actual_UI_puts:responses,actual_project_transitions:transitions,
   setup_renderer_writes:setupWrites,post_baseline_renderer_writes:writes.map(({request:_request,...row})=>row),actual_original_draft_replies:actualDraftReplies,snapshots,
   exact_topology_positions_ROI_labels_contexts_and_hashes:true,all_declared_seven_trees_per_project_plus_registries_and_harness_unchanged:true,
   policy:{original_debounce_ms:650,baseline_after_auto_and_explicit_full_response_and_clean_status:true,dirty_unmount_persistence_not_disabled:true,no_dirty_project_switch:true,post_baseline_draft_writes:0},
   scope:{browser:true,source_Electron:false,installed_native:false,training:false,model_or_GPU_execution:false,active_flow:false,quality_or_human_or_parent_target_acceptance:false,unrelated_project_lifecycle_SQLite_namespace:false}});
 }catch(error){primary=error;throw error;}finally{
  let cleanupError:unknown;const cleanupRows:Array<{role:string;unchanged:boolean;error_type?:string}>=[];
  const attempt=async(role:string,work:()=>unknown|Promise<unknown>)=>{try{await work();cleanupRows.push({role,unchanged:true});}catch(error){
   if(cleanupError===undefined)cleanupError=error;cleanupRows.push({role,unchanged:false,error_type:error instanceof Error?error.name:typeof error});}};
  await attempt('original-draft-responses',drain);await attempt('request-observer-remove',()=>page.off('request',observed));
  await attempt('failed-observer-remove',()=>page.off('requestfailed',failed));await attempt('response-observer-remove',()=>page.off('response',replied));
  if(baseline){await attempt('A-full-declared-custody',()=>expect(custody(A)).toEqual(baselineTrees!.A));
   await attempt('B-full-declared-custody',()=>expect(custody(B)).toEqual(baselineTrees!.B));
   await attempt('original-harness-dataset',()=>expect(draftHandoffTree(w.dataset)).toEqual(baselineTrees!.harness_dataset));}
  await attempt('durable-final-custody',()=>draftHandoffSave(e,w,'flow-draft-project-handoff-final-custody',{
   baseline_reached:baseline,primary_present:primary!==undefined,roles:[...cleanupRows],original_error_preserved:true}));
  if(primary===undefined&&cleanupError!==undefined)throw cleanupError;
 }
}
test('saved editable flow graphs and hashes stay scoped through A B A project handoff after owning draft saves',async({page,renderer,workspace,evidence})=>{
 test.setTimeout(240_000);await installDesktopHostShim(page,renderer.port);await draftHandoffExercise(page,workspace,evidence,renderer.origin,renderer.url);
});
