import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {inflateSync} from 'node:zlib';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';

/** Step 1's gallery reads its thumbnails itself and drops a read when the page leaves the step. A user sees the
 *  gallery before moving on; the spec waits until every gallery thumbnail is displayed. */
async function galleryReadsSettled(page:Page){
 if(!(await page.getByRole('button',{name:'데이터셋 폴더 열기',exact:true}).isVisible()))return;
 const thumbs=page.locator('button[aria-label$=" 라벨링에서 열기"] img');
 await expect.poll(async()=>{const shown=await thumbs.count();const ready=await thumbs.and(page.locator('[data-read-state="ready"]')).count();return shown>0&&ready===shown;},{timeout:10_000}).toBe(true);
}

test.use({actionTimeout:10_000});
type OwnedApi=(route:string,body?:unknown,method?:string)=>Promise<any>;
const hash=(data:Buffer)=>crypto.createHash('sha256').update(data).digest('hex');

type SplitControlRequest=import('@playwright/test').Request;
type SplitControlCustody={opened:number;closed:number;uncertain:Array<{file:string;fd:number;error:string}>};
const splitControlRaw=(s:fs.BigIntStats)=>[s.dev,s.ino,s.mode,s.nlink,s.size,s.mtimeNs,s.ctimeNs,s.uid,s.gid].map(String);
function splitControlFile(file:string,custody:SplitControlCustody):{bytes:Buffer;raw:string[];sha256:string}{
 const first=fs.lstatSync(file,{bigint:true});expect(first.isFile()).toBe(true);expect(first.isSymbolicLink()).toBe(false);expect(first.nlink).toBe(1n);
 let fd:number|undefined,primary:unknown,failed=false,result:{bytes:Buffer;raw:string[];sha256:string}|undefined;
 try{fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);custody.opened++;expect(splitControlRaw(fs.fstatSync(fd,{bigint:true}))).toEqual(splitControlRaw(first));
  const bytes=fs.readFileSync(fd);expect(BigInt(bytes.length)).toBe(first.size);expect(splitControlRaw(fs.fstatSync(fd,{bigint:true}))).toEqual(splitControlRaw(first));expect(splitControlRaw(fs.lstatSync(file,{bigint:true}))).toEqual(splitControlRaw(first));result={bytes,raw:splitControlRaw(first),sha256:hash(bytes)};
 }catch(cause){failed=true;primary=cause;}
 finally{if(fd!==undefined){const owned=fd;fd=undefined;try{fs.closeSync(owned);custody.closed++;}catch(cause){custody.uncertain.push({file,fd:owned,error:String(cause)});if(!failed){failed=true;primary=cause;}else if(primary instanceof Error)primary.message+='; owned file close failed: '+String(cause);}}}
 if(failed)throw primary;return result!;
}
function splitControlTree(root:string,custody:SplitControlCustody):Record<string,unknown>{
 const rows:Record<string,unknown>={};const walk=(file:string,relative:string)=>{
  const first=fs.lstatSync(file,{bigint:true});expect(first.isSymbolicLink()).toBe(false);
  if(first.isDirectory()){const names=fs.readdirSync(file).sort();rows[relative]={kind:'directory',raw:splitControlRaw(first),names};for(const name of names)walk(path.join(file,name),relative?relative+'/'+name:name);expect(fs.readdirSync(file).sort()).toEqual(names);expect(splitControlRaw(fs.lstatSync(file,{bigint:true}))).toEqual(splitControlRaw(first));}
  else{expect(first.isFile()).toBe(true);const pin=splitControlFile(file,custody);rows[relative]={kind:'file',raw:pin.raw,sha256:pin.sha256};}
 };walk(root,'');return rows;
}
async function splitControlWithin<T>(operation:Promise<T>,deadline:number,label:string):Promise<T>{
 const remaining=deadline-performance.now();if(remaining<=0)throw Error('Original 10s split-control frame expired: '+label);let timer:ReturnType<typeof setTimeout>|undefined;
 try{return await Promise.race([operation,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original 10s split-control frame expired: '+label)),remaining);})]);}finally{if(timer!==undefined)clearTimeout(timer);}
}
async function splitControlBody(response:import('@playwright/test').Response,deadline:number){
 expect(response.status()).toBe(200);const bytes=await splitControlWithin(response.body(),deadline,'full response body');expect(await splitControlWithin(response.finished(),deadline,'HTTP completion')).toBeNull();return {body:JSON.parse(bytes.toString('utf8')),sha256:hash(bytes),status:200};
}

// Standalone Source-only model-less draft reopen derivative. Root alone executes and qualifies three cells.
const harness=require('./fixtures/harness.cjs');
const sha=(raw:Buffer|string)=>crypto.createHash('sha256').update(raw).digest('hex');
const F064_POINTERS=[
 '/records/63/actions/5/scenarios/reopen',
 '/records/63/actions/9/scenarios/reopen',
 '/records/63/actions/6/scenarios/reopen',
] as const;
const FLOW_DRAFT_SERVICE={
 'src/renderer/services/flowDraft.ts':'f8cd76aab7d1045c3af1fa41f68ae09daa757c0db05772f4085e2e093348b800',
 'backend/engine/flowchart_engine.py':'ce9e52a393d0dd3210a9282a5b67a4eb4e5a97edab7c5e95be1ed32a32cc82eb',
} as const;
const UI_SOURCE={
 "scripts/e2e/fixtures/flow_navigation_control.py": "b5752088a03ff1640510208e15a17b3f1389295c62dadabaf2ff6511a929ee73",
 "scripts/e2e/fixtures/flow_template_control.py": "81f4a1844d05021d6c4c668627a732120c11aa20e45294ba55da0bfca410572a",
 "scripts/e2e/fixtures/flow_large_dag_control.py": "6434d381ed45d68922c058544d0b515caa3d19d93d88c90028a3463a9903d871",
 "scripts/e2e/qa/appFlow.ts": "4eed3e5dad1d9d65a6b19fc9bb51a1f9b0e23911a39e7f255220bec0ce37fea2",
 "src/renderer/components/flowchart/FlowchartStudio.tsx": "5ea47e19d8d4be9b42bc26990a7779bc2c1295dbec7b423cc416f47c9aa9b675",
 "src/renderer/components/flowchart/FlowEditorWorkspace.tsx": "e58f0b7a62572516d9ea04cbb45dafdfb104e805753e9f13a3c8f67c88210ee7",
 "src/renderer/components/flowchart/FlowDraftControls.tsx": "3bd10fc68ea4ae2dc600dfad2b6c51b56d74d213f7483923107295aac303f5a8",
 "src/renderer/components/flowchart/FlowGraphNavigator.tsx": "656832c214f41742d5bc0f66c9f8983c5fb6892cfaf3b42b48ee353f282da20a",
 "src/renderer/components/flowchart/FlowInspectorPanel.tsx": "c108ee18336c2d3cdc2accb200eb739b0a3252c7fcbd60f35902e66da06c6707",
 "src/renderer/components/flowchart/CustomNode.tsx": "97350539036826f52411bc8defe234d638e2c18fde5092a5f104af7557dd1828",
 "src/renderer/components/flowchart/ImagePickerModal.tsx": "0ef27d643ddc2fddd5014e2c27087116eefbc87eb5dd92c95b732a401ceb6e4e",
 "src/renderer/stores/useFlowchartStore.ts": "00fa0e410ad822ce1ccbb9353f96635e8626dad965693164458b5450eb58426d",
 "src/renderer/services/api.ts": "8e9a6005af48cfa385463cef4521cffcbfa5096172ba14ad0cffc9ab6746357e",
 "src/renderer/components/flowchart/FlowWorkspacePanel.tsx": "6e7c74ae465e6a426601de756a91a9966615195f52c1e6aa9e4691b187baa8bf",
 "src/renderer/components/flowchart/FlowChangeDialog.tsx": "2e66a530f565b8211829d1cb3507bf2d9d1e7aa7173a1a37a54de98d19230347",
 "src/renderer/components/flowchart/FlowRecipeDialog.tsx": "09418ce7091fd126a9f341fddf0acb9d30f20fe955268c71ae21f25204c935ba",
 "src/renderer/components/flowchart/flowchartGraph.ts": "2db91b8c7d514e39a1dfbb5edf60c52c82d69b3c51aadb47e17b9cfe9e640743",
 "src/renderer/components/flowchart/flowchartViewport.ts": "65564c68bb1242e5c78767cdbc1b77596815b14ef071d68bfb96b42799e5aac1",
 "src/renderer/components/common/ImagePickerModal.tsx": "4ad3777c3152c0204afc4f2d517b056f5720e0b4d2caa90c4ab383b5c994d966",
 "backend/api/routes_flowchart.py": "5046ce2c7019657b9ffe8ade5fd494d1286a3766ab1ee2e1ca3cb37f02150276",
 "src/renderer/components/flowchart/FlowNodeDebugger.tsx": "82b81c5ceb3556f4bdc14fd7a93edbe9657a528a1af140c5755aa9e6daa83a2f",
 "src/renderer/components/flowchart/flowWorkspace.ts": "17c39a0236efa5b444fcc99751bc8db95d613071c916ecf0e3ec44ebb49253e4",
 "scripts/e2e/fixtures/remaining-project-handoff.ts": "7a7c74a2f42a5d6ad991dadc97626c1f8330f962edaf73fd461d31f9c4764904",
 "scripts/e2e/fixtures/test.ts": "7a7effa184e71c02414773308e0ea88e16cf83dfbd72d77d47046f2cfc1b8d09",
 "scripts/e2e/fixtures/harness.cjs": "6fe3bb6411a28bc83c6da36ba0b2480b12de6b86fd08daa61d8d0e76d7dac950",
 "scripts/e2e/fixtures/desktop-host-shim.ts": "4f598862ec0e08189628c72bbb552f72bf8fd73f5a9a05b8da1d3ddf516dcfa6",
 "backend/api/routes_dataset.py": "6915177047d0b19a40a52315c69e38e709c55a7ec4c33c224ce9bf209de61cbb"
} as const;
export function canonical(value:any):any{return Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;}
export function assertDraftRecord(record:any,pipeline:any,context:any){
 expect(Object.keys(record).sort()).toEqual(['active_version_id','base_version_id','context','draft_sha256','pipeline','saved_at_ns','version']);
 expect(record.version).toBe(1);expect(record.context).toEqual(context);expect(record.pipeline).toEqual(pipeline);
 expect(record.draft_sha256).toBe(sha(JSON.stringify(canonical(pipeline))));expect(record.base_version_id).toBe('none');expect(record.active_version_id).toBeNull();
 expect(Number.isFinite(record.saved_at_ns)).toBe(true);expect(record.saved_at_ns).toBeGreaterThan(0);
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
  split_unavailable_reason:'anomaly 분할은 현재 학습 데이터에 적용되지 않습니다. 원본 데이터의 train/val/test 구성을 사용하세요. / anomaly split is not applied by the training loader; use source train/val/test folders.',
  excluded_undecodable:{excluded:[],restored:[],recorded:true},split_state:'source_folders',labels_missing:false,expected_layout:"train/good (정상만), test/good, test/<결함 이름> (선택: ground_truth/<결함 이름>/<이름>_mask.png), 또는 OK/ 와 NG/"});
}

type FlowReply={status:number;body:string;url:string;headers:Record<string,string>};
type FlowApi=(route:string,body?:unknown,method?:string,headers?:Record<string,string>)=>Promise<FlowReply>;
type FlowRead={request:SplitControlRequest;started:number;deadline:number;post_baseline:boolean;complete:boolean;pending:Promise<void>;document_bootstrap?:{epoch:number;project_id:string;started:number;retired_gallery_read?:boolean;failure_text?:string};bootstrap_unscoped?:boolean;
 status?:number;body?:unknown;sha256?:string;size?:number;request_context?:unknown;response_context?:unknown;finished?:number;failed?:boolean;failure?:unknown};
type OCROwnedThumbScope={source:string;images:Array<{image:string;sha256:string}>;project:{id:string}};
async function ocrOwnedThumbnail(page:Page,response:import('@playwright/test').Response,request:SplitControlRequest,deadline:number,origin:string,scopes:OCROwnedThumbScope[],contexts:Map<string,unknown>,custody:SplitControlCustody){
 const u=new URL(request.url());expect(request.method()).toBe('GET');expect(u.origin).toBe(origin);expect(Array.from(u.searchParams.keys()).sort()).toEqual(['file_path']);
 const matches=scopes.flatMap(scope=>scope.images.filter(image=>path.join(scope.source,image.image)===u.searchParams.get('file_path')).map(image=>({scope,image})));expect(matches).toHaveLength(1);const {scope,image}=matches[0],file=path.join(scope.source,image.image);
 expect(u.pathname).toBe('/api/dataset/thumbnail/'+path.basename(image.image));expect(Object.fromEntries(u.searchParams.entries())).toEqual({file_path:file});expect(contexts.has(scope.project.id)).toBe(true);
 const rh=request.headers(),sh=response.headers(),requestContext=rh['x-vision-context']===undefined?null:JSON.parse(rh['x-vision-context']);if(rh['x-vision-project']!==undefined)expect(rh['x-vision-project']).toBe(scope.project.id);if(requestContext!==null)expect(requestContext).toEqual(contexts.get(scope.project.id));
 expect(sh['x-vision-context']).toBeDefined();const responseContext=JSON.parse(sh['x-vision-context']);expect(responseContext).toEqual(contexts.get(scope.project.id));expect(responseContext.project_id).toBe(scope.project.id);
 const sourcePin=splitControlFile(file,custody);expect(sourcePin.sha256).toBe(image.sha256);expect(sourcePin.bytes.subarray(0,8).toString('hex')).toBe('89504e470d0a1a0a');expect([sourcePin.bytes.readUInt32BE(16),sourcePin.bytes.readUInt32BE(20)]).toEqual([256,256]);
 expect(response.status()).toBe(200);expect(sh['content-type']).toBe('image/jpeg');let bytes=await splitControlWithin(response.body(),deadline,'whole owning JPEG thumbnail body');if(bytes.length===0){/* The app reads gallery thumbnails with fetch and keeps a Blob (6dd4c1f); the browser then retains no body for the test. The same URL is read once more with the same context headers and that answer is checked in full below. */const again=await splitControlWithin(page.request.get(request.url(),{headers:Object.fromEntries(Object.entries(request.headers()).filter(([key])=>key.startsWith('x-vision-')))}),deadline,'owning JPEG thumbnail re-read');expect(again.status()).toBe(200);expect(again.headers()['content-type']).toBe('image/jpeg');if(sh['x-vision-context']!==undefined)expect(JSON.parse(again.headers()['x-vision-context'])).toEqual(JSON.parse(sh['x-vision-context']));bytes=await again.body();}expect(bytes.length).toBeGreaterThan(0);if(sh['content-length']!==undefined)expect(sh['content-length']).toBe(String(bytes.length));expect(bytes.subarray(0,2).toString('hex')).toBe('ffd8');expect(bytes.subarray(-2).toString('hex')).toBe('ffd9');expect(await splitControlWithin(response.finished(),deadline,'owning JPEG HTTP completion')).toBeNull();
 const decoded=await splitControlWithin(page.evaluate(async data=>{let bitmap:ImageBitmap|undefined,result:{width:number;height:number;bitmap_closed:boolean}|undefined,failed=false,primary:unknown;
  try{bitmap=await createImageBitmap(new Blob([new Uint8Array(data)],{type:'image/jpeg'}));result={width:bitmap.width,height:bitmap.height,bitmap_closed:false};}catch(cause){failed=true;primary=cause;}
  finally{if(bitmap!==undefined){const owned=bitmap;bitmap=undefined;try{owned.close();if(result)result.bitmap_closed=true;}catch(cause){if(!failed){failed=true;primary=cause;}else if(primary instanceof Error)primary.message+='; owned bitmap close failed: '+String(cause);}}}
  if(failed)throw primary;return result!;
 },Array.from(bytes)),deadline,'full owning JPEG decode and bitmap closure');expect(decoded).toEqual({width:128,height:128,bitmap_closed:true});expect(splitControlFile(file,custody)).toEqual(sourcePin);
 return {status:200,sha256:hash(bytes),body:{kind:'owning_binary_thumbnail',media_type:'image/jpeg',byte_length:bytes.length,whole_body_sha256:hash(bytes),decoded,default_size:128,source_geometry:{width:256,height:256},file_path:file,source_image_sha256:sourcePin.sha256,source_raw9:sourcePin.raw,query:Object.fromEntries(u.searchParams.entries()),owner_project_id:scope.project.id,request_context:requestContext,response_context:responseContext,response_finished:null,no_JSON_decode:true,no_cache_status_exception:true}};
}

async function flowModelLessReopenExercise(page:Page,w:Workspace,e:Evidence,api:FlowApi,native:boolean,origin:string,url?:string){
 const custody:SplitControlCustody={opened:0,closed:0,uncertain:[]};
 const sourceState=()=>Object.fromEntries(Object.entries({...UI_SOURCE,...FLOW_DRAFT_SERVICE}).map(([relative,expected])=>{
  const file=path.join(harness.REPO_ROOT,relative),pin=splitControlFile(file,custody);expect(pin.sha256,relative).toBe(expected);return[relative,{path:file,raw9:pin.raw,sha256:pin.sha256}];
 }));
 // Exact defaults and omitted-null capture_group serialization from authenticated FlowNodeData/FlowNode/FlowEdge/FlowchartPipeline (engine103–162).
 const normalizeSavedPipeline=(pipeline:any)=>{
  expect(Object.keys(pipeline).every(key=>['id','name','description','nodes','edges','execution_config','capture_group_policy'].includes(key))).toBe(true);
  const nodes=pipeline.nodes.map((node:any)=>{
   expect(Object.keys(node).every(key=>['id','type','position','data'].includes(key))).toBe(true);expect(Object.keys(node.position).sort()).toEqual(['x','y']);
   expect(Object.keys(node.data).every(key=>['label','node_type','task','model_job_id','threshold','score_spec','crop_padding','rule','params'].includes(key))).toBe(true);
   return {id:node.id,type:node.type??'custom',position:node.position,data:{task:'anomaly',model_job_id:null,threshold:0.5,score_spec:null,crop_padding:10,rule:'any_defect_is_ng',params:{},...node.data}};
  });
  const edges=pipeline.edges.map((edge:any)=>{expect(Object.keys(edge).every(key=>['id','source','target','label','isBranch','payload_type','predicate'].includes(key))).toBe(true);return {label:null,isBranch:null,payload_type:null,predicate:null,...edge};});
  expect(pipeline.capture_group_policy??null).toBeNull();expect(pipeline.execution_config).toEqual({max_workers:1,device_slots:1});
  return {id:pipeline.id,name:pipeline.name,description:pipeline.description,nodes,edges,execution_config:pipeline.execution_config};
 };
 const sourceBefore=sourceState(),fixtureCalls:Array<Record<string,unknown>>=[];
 let transport:any;
 const readReply=async(route:string,body?:unknown,method?:string)=>{
  const started=performance.now(),deadline=started+10_000,headers:Record<string,string>=transport?{'x-vision-project':transport.project_id,'x-vision-context':JSON.stringify(transport)}:{};
  const reply=await splitControlWithin(api(route,body,method,headers),deadline,'actual owned fixture full API body');
  expect(reply.status,route+': '+reply.body).toBe(200);expect(reply.url).toBe(origin+route);const value=JSON.parse(reply.body),finished=performance.now();expect(finished).toBeLessThanOrEqual(deadline);
  const responseContext=reply.headers['x-vision-context']===undefined?null:JSON.parse(reply.headers['x-vision-context']);if(transport&&responseContext!==null)expect(responseContext).toEqual(transport);
  fixtureCalls.push({method:method||(body===undefined?'GET':'POST'),route,request:body??null,status:200,raw_body:reply.body,raw_body_sha256:hash(Buffer.from(reply.body)),raw_body_bytes:Buffer.byteLength(reply.body),started,deadline,finished,request_context:transport??null,response_context:responseContext});return{reply,value};
 };
 const value=async(route:string,body?:unknown,method?:string)=>(await readReply(route,body,method)).value;
 type Scope={tag:'A'|'B';source:string;images:any[];project:any;context:any;transport:any;saved:any;draftFile:string};
 const make=async(tag:'A'|'B'):Promise<Scope>=>{
  transport=undefined;const source=path.join(w.root,'owned-F064-reopen-source-'+tag),normal=path.join(source,'train','good');fs.mkdirSync(normal,{recursive:true});
  const images=[100,101].map((blue,index)=>{
   const file=path.join(normal,index?'part-1.png':'part.png'),bytes=png(256,3,(x,y)=>[x,y,blue]);fs.writeFileSync(file,bytes,{flag:'wx'});
   const pin=splitControlFile(file,custody);expect(pin.bytes).toEqual(bytes);return {file,image:path.relative(source,file).split(path.sep).join('/'),sha256:pin.sha256,raw9:pin.raw,RGB:assertSourcePixels(pin.bytes,blue)};
  });
  const created=await value('/api/project/create',{name:'Owned F064 model-less reopen '+tag,task:'anomaly'});
  await value('/api/project/update',{source_dataset_dir:source},'PUT');assertAnomalyImport(await value('/api/dataset/import',{folder_path:source,task:'anomaly',validate_images:true}));
  const current=await readReply('/api/project/current'),project=current.value;expect(project.id).toBe(created.id);expect(project.source_dataset_dir).toBe(source);expect(project.task).toBe('anomaly');
  expect(current.reply.headers['x-vision-context']).toBeDefined();transport=JSON.parse(current.reply.headers['x-vision-context']);expect(Object.keys(transport).sort()).toEqual(['actor_id','mode','project_id','workspace_id']);expect(transport.project_id).toBe(project.id);expect(transport.mode).toBe('local');for(const key of ['actor_id','workspace_id'])expect(transport[key]).toMatch(/^[A-Za-z0-9_.:-]{1,128}$/);
  expect(path.dirname(project.project_dir)).toBe(native?path.join(w.userData,'projects'):w.projects);expect(fs.realpathSync(project.project_dir)).toBe(project.project_dir);expect(fs.lstatSync(project.project_dir).isSymbolicLink()).toBe(false);
  const context={project_id:project.id,source_dataset_path:source,labelset_id:project.active_labelset_id||'default'};
  const initial={id:'owned-F064-reopen-'+tag,name:'Owned editable model-less F064 '+tag,nodes:[
   {id:'input-original',position:{x:32,y:170},data:{label:'Input original',node_type:'input'}},
   {id:'roi-original',position:{x:332,y:170},data:{label:'ROI original',node_type:'fixed_roi',params:{roi_bbox:[2,3,20,21]}}},
   {id:'decision-original',position:{x:632,y:170},data:{label:'Decision original',node_type:'decision',rule:'any_defect_is_ng'}},
   {id:'output-original',position:{x:932,y:170},data:{label:'Output original',node_type:'output'}}],edges:[
   {id:'input-roi-original',source:'input-original',target:'roi-original',payload_type:'image'},
   {id:'roi-decision-original',source:'roi-original',target:'decision-original',payload_type:'roi'},
   {id:'decision-output-original',source:'decision-original',target:'output-original',payload_type:'result'}]};
  const saved=await value('/api/flowchart/draft',{pipeline:initial,context,base_version_id:'none'},'PUT');assertDraftRecord(saved,saved.pipeline,context);for(const node of saved.pipeline.nodes)expect(node.data.model_job_id).toBeNull();
  const draftFile=path.join(project.project_dir,'flowcharts','drafts',context.labelset_id,crypto.createHash('sha256').update(source).digest('hex').slice(0,16),'draft.json');
  expect(JSON.parse(splitControlFile(draftFile,custody).bytes.toString('utf8'))).toEqual(Object.fromEntries(Object.entries(saved).filter(([key])=>key!=='active_version_id')));
  // Complete actual lazy metadata/readiness/annotation transactions before any protected reopen baseline.
  for(const route of ['/api/project/labelsets','/api/team-data','/api/team-data/readiness','/api/dataset/metadata?limit=100','/api/dataset/metadata/statistics'])await value(route);
  for(const image of images)await value('/api/annotations/part?file_path='+encodeURIComponent(image.file));
  return {tag,source,images,project,context,transport:JSON.parse(JSON.stringify(transport)),saved,draftFile};
 };
 const B=await make('B'),A=await make('A'),scopes=[A,B],contexts=new Map<string,unknown>(scopes.map(scope=>[scope.project.id,scope.transport]));
 const roots={A_project:A.project.project_dir,A_source:A.source,B_project:B.project.project_dir,B_source:B.source,harness_dataset:w.dataset};
 const treeState=()=>Object.fromEntries(Object.entries(roots).map(([name,root])=>[name,splitControlTree(root,custody)]));
 const beforeInitialization=treeState();let B_initial:any;
 const reads:FlowRead[]=[],writes:Array<any>=[];let reachedBaseline=false,baseline:ReturnType<typeof treeState>|undefined,active=A;
  const RELOAD_BOOTSTRAP_PATHS=['/api/dataset/revisions','/api/compute/profiles','/api/project/current','/api/compute/selection'] as const;
  let documentBootstrap:{epoch:number;project_id:string;started:number}|undefined,documentBootstrapEpoch=0;const documentBootstrapProofs:any[]=[];
 let failed=false,primary:unknown,removed=false,completed=false;const secondary:unknown[]=[],transitions:any[]=[],cells:any[]=[],saveProofs:any[]=[],reopenProofs:any[]=[];
 const scopeFor=(context:any)=>{const matches=scopes.filter(scope=>context?.project_id===scope.project.id);expect(matches).toHaveLength(1);expect(context).toEqual(matches[0].transport);return matches[0];};
 const observe=(request:SplitControlRequest)=>{
  const u=new URL(request.url());if(u.origin!==origin||!u.pathname.startsWith('/api/'))return;
  if(request.method()!=='GET'){
   if(['HEAD','OPTIONS'].includes(request.method()))return;
   const row:any={request,method:request.method(),url:request.url(),body:request.postData(),post_baseline:reachedBaseline,started:performance.now(),complete:false,pending:Promise.resolve()};row.deadline=row.started+10_000;writes.push(row);
   row.pending=(async()=>{try{
   if(u.pathname.startsWith('/api/dataset/thumbnail/')&&!row.post_baseline){
    // Step 1's gallery owns its thumbnail reads and aborts them when the page leaves the step before a body arrives
    // (ScopedDatasetThumbnail, 6dd4c1f). Such a read is recorded as retired by the gallery; any other failure, and
    // every thumbnail that completes, is checked in full as before.
    // The browser's own end event decides: a response object of an aborted read may report no failure and an empty
    // body. Both listeners are attached in the same tick the request starts, so neither event can be missed.
    let detach=()=>{};
    const outcome=await splitControlWithin(new Promise<'failed'|'finished'>(resolve=>{
     const end=(kind:'failed'|'finished')=>(ended:SplitControlRequest)=>{if(ended===request){detach();resolve(kind);}};
     const onFailed=end('failed'),onFinished=end('finished');page.on('requestfailed',onFailed);page.on('requestfinished',onFinished);
     detach=()=>{page.off('requestfailed',onFailed);page.off('requestfinished',onFinished);};
    }),row.deadline,'original F064 thumbnail end event').finally(()=>detach());
    if(outcome==='failed'){const failure=request.failure()?.errorText??'';expect(failure).toMatch(/ERR_ABORTED|aborted|cancel/i);
     row.retired_gallery_read=true;row.failure_text=failure;row.finished=performance.now();row.complete=true;return;}
   }
    expect(request.frame()).toBe(page.mainFrame());const response=await splitControlWithin(request.response(),row.deadline,'own original mutation response');expect(response).not.toBeNull();
    const bytes=await splitControlWithin(response!.body(),row.deadline,'own whole mutation response');expect(await splitControlWithin(response!.finished(),row.deadline,'own original mutation finished')).toBeNull();expect(response!.status()).toBe(200);
    row.status=200;row.raw_body=bytes.toString('utf8');row.sha256=hash(bytes);row.size=bytes.length;row.response=JSON.parse(row.raw_body);row.request_body=request.postDataJSON();
    const rh=request.headers(),sh=response!.headers();row.request_context=rh['x-vision-context']===undefined?null:JSON.parse(rh['x-vision-context']);row.response_context=sh['x-vision-context']===undefined?null:JSON.parse(sh['x-vision-context']);
    if(u.pathname==='/api/flowchart/draft'){
     expect(row.method).toBe('PUT');expect(u.search).toBe('');expect(row.request_context).not.toBeNull();const owner=scopeFor(row.request_context);expect(rh['x-vision-project']).toBe(owner.project.id);expect(row.response_context).toEqual(owner.transport);
     expect(Object.keys(row.request_body).sort()).toEqual(['base_version_id','context','pipeline']);expect(row.request_body.context).toEqual(owner.context);expect(row.request_body.base_version_id).toBe('none');
     assertDraftRecord(row.response,normalizeSavedPipeline(row.request_body.pipeline),owner.context);row.project_id=owner.project.id;
    }else{
     expect(u.pathname).toBe('/api/project/open');expect(u.search).toBe('');expect(row.method).toBe('POST');const matches=scopes.filter(scope=>row.request_body?.project_dir===scope.project.project_dir);expect(matches).toHaveLength(1);expect(row.request_body).toEqual({project_dir:matches[0].project.project_dir});expect(row.response).toEqual(matches[0].project);expect(row.response_context).toEqual(matches[0].transport);if(row.request_context!==null)scopeFor(row.request_context);
    }
    row.finished=performance.now();expect(row.finished).toBeLessThanOrEqual(row.deadline);row.complete=true;
   }catch(cause){row.failed=true;row.failure=cause;}})();return;
  }
  const started=performance.now(),row:FlowRead={request,started,deadline:started+10_000,post_baseline:reachedBaseline,complete:false,pending:Promise.resolve(),document_bootstrap:documentBootstrap?{...documentBootstrap}:undefined};reads.push(row);
  row.pending=(async()=>{try{
   const response=await splitControlWithin(request.response(),row.deadline,'original F064 API GET response');expect(response).not.toBeNull();expect(request.frame()).toBe(page.mainFrame());expect(request.postData()).toBeNull();
   const rh=request.headers(),sh=response!.headers();row.request_context=rh['x-vision-context']===undefined?null:JSON.parse(rh['x-vision-context']);row.response_context=sh['x-vision-context']===undefined?null:JSON.parse(sh['x-vision-context']);
   const owner=row.response_context!==null?scopeFor(row.response_context):row.request_context!==null?scopeFor(row.request_context):active;
   if(rh['x-vision-project']!==undefined)expect(rh['x-vision-project']).toBe(owner.project.id);if(row.request_context!==null)expect(row.request_context).toEqual(owner.transport);if(row.response_context!==null)expect(row.response_context).toEqual(owner.transport);
   if(u.searchParams.has('source_dataset_path'))expect(u.searchParams.getAll('source_dataset_path')).toEqual([owner.source]);if(u.searchParams.has('file_path'))expect(owner.images.filter(image=>image.file===u.searchParams.get('file_path'))).toHaveLength(1);
   if(u.pathname.startsWith('/api/dataset/thumbnail/')){
    const full=await ocrOwnedThumbnail(page,response!,request,row.deadline,origin,scopes,contexts,custody);row.status=full.status;row.sha256=full.sha256;row.body=full.body;row.size=(full.body as any).byte_length;
   }else{
     const bootstrapUnscoped=row.document_bootstrap!==undefined&&row.request_context===null&&(RELOAD_BOOTSTRAP_PATHS as readonly string[]).includes(u.pathname);
     if(bootstrapUnscoped){expect(u.search).toBe('');expect(rh['x-vision-project']).toBeUndefined();expect(rh['x-vision-context']).toBeUndefined();expect(row.started).toBeGreaterThanOrEqual(row.document_bootstrap!.started);expect(owner.project.id).toBe(row.document_bootstrap!.project_id);expect(row.response_context).toEqual(owner.transport);row.bootstrap_unscoped=true;}
     else if(row.post_baseline){if(u.pathname==='/api/project/current'&&row.request_context===null){expect(u.search).toBe('');expect(rh['x-vision-project']).toBeUndefined();expect(row.response_context).toEqual(owner.transport);}else{expect(row.request_context).toEqual(owner.transport);expect(rh['x-vision-project']).toBe(owner.project.id);}}
    const bytes=await splitControlWithin(response!.body(),row.deadline,'whole original F064 API body');expect(await splitControlWithin(response!.finished(),row.deadline,'original F064 API completion')).toBeNull();row.status=response!.status();row.body=JSON.parse(bytes.toString('utf8'));row.sha256=hash(bytes);row.size=bytes.length;
     if(bootstrapUnscoped){expect(row.status).toBe(200);expect(sh['content-type']).toMatch(/^application\/json(?:\s*;|$)/);if(u.pathname==='/api/dataset/revisions')expect(row.body).toEqual({active_revision:null,revisions:[]});else if(u.pathname==='/api/compute/profiles')expect(row.body).toEqual({profiles:[]});else if(u.pathname==='/api/compute/selection')expect(row.body).toEqual({compute_profile_id:null});else expect(row.body).toEqual(owner.project);}
    if(row.status!==200){
     expect(row.request_context).toEqual(owner.transport);
     if(u.pathname==='/api/dataset/library/images'){expect(u.search).toBe('?state=valid&limit=120');expect(row.status).toBe(409);expect(row.body).toEqual({detail:'No validated dataset revision is active; validate the source and accept a revision first'});}
     else{expect(u.pathname).toBe('/api/evaluation/results');expect(u.search).toBe('?'+new URLSearchParams({source_dataset_path:owner.source,source_task:'anomaly'}).toString());expect(row.status).toBe(404);expect(row.body).toEqual({detail:'학습을 끝낸 모델을 아직 고르지 않았습니다. 학습을 먼저 끝낸 뒤 평가할 모델을 고르세요.'});}
    }else if(u.pathname==='/api/project/current'){expect(row.body).toEqual(owner.project);}
   }
   row.finished=performance.now();expect(row.finished).toBeLessThanOrEqual(row.deadline);row.complete=true;
  }catch(cause){row.failed=true;row.failure=cause;}})();
 };
 const settle=async()=>{
  const admitted=[...reads];let drainFailed=false,firstDrain:unknown;
  for(let ordinal=0;ordinal<admitted.length;ordinal++){
   const row=admitted[ordinal];
   try{await row.pending;if(row.failed)throw row.failure;expect(row.complete).toBe(true);expect(row.deadline).toBe(row.started+10_000);expect(row.finished!).toBeLessThanOrEqual(row.deadline);}
   catch(cause){if(!drainFailed){drainFailed=true;firstDrain=cause;}else secondary.push({label:'later original admitted GET',ordinal,cause});}
  }
  if(drainFailed)throw firstDrain;
 };
 const settleWrites=async()=>{
  const admitted=[...writes];let drainFailed=false,firstDrain:unknown;
  for(let ordinal=0;ordinal<admitted.length;ordinal++){const row=admitted[ordinal];try{await row.pending;if(row.failed)throw row.failure;expect(row.complete).toBe(true);expect(row.finished).toBeLessThanOrEqual(row.deadline);}catch(cause){if(!drainFailed){drainFailed=true;firstDrain=cause;}else secondary.push({label:'later original admitted mutation',ordinal,cause});}}
  if(drainFailed)throw firstDrain;
 };
 const rendered=()=>page.evaluate(()=>({nodes:[...document.querySelectorAll<HTMLElement>('[data-flow-node-id]')].map(row=>({id:row.dataset.flowNodeId,label:row.querySelector('h4')?.textContent,x:parseFloat(row.style.left),y:parseFloat(row.style.top)})),edges:[...document.querySelectorAll<SVGElement>('path[data-flow-edge]')].map(row=>({id:row.dataset.flowEdge,source:row.dataset.flowFrom?.split(':')[0],target:row.dataset.flowTo?.split(':')[0]}))}));
 const view=(pipeline:any)=>({nodes:pipeline.nodes.map((node:any)=>({id:node.id,label:node.data.label,x:node.position.x,y:node.position.y})),edges:pipeline.edges.map((edge:any)=>({id:edge.id,source:edge.source,target:edge.target}))});
 const save=page.getByRole('button',{name:'초안 저장',exact:true});
 const savedStatus=async(expectedUndo:boolean)=>{
  const identity=page.getByRole('region',{name:'플로우 식별 정보',exact:true});await expect(identity).toHaveCount(1);await expect(identity).toBeVisible();const state=identity.getByText(/^편집 r[0-9]+ · 초안 저장됨 · 실행본 활성화 전$/);await expect(state).toHaveCount(1);await expect(state).toBeVisible();
  await expect(page.getByRole('tab',{name:'편집',exact:true})).toHaveAttribute('aria-selected','true');await expect(page.getByRole('tabpanel',{name:'편집',exact:true})).toBeVisible();await expect(page.getByRole('status').filter({hasText:/^초안 저장됨 · 실행본 활성화 전$/})).toHaveCount(1);await expect(save).toBeEnabled();if(expectedUndo)await expect(page.getByRole('button',{name:'플로우 실행 취소',exact:true})).toBeEnabled();else await expect(page.getByRole('button',{name:'플로우 실행 취소',exact:true})).toBeDisabled();await expect(page.getByRole('button',{name:'플로우 다시 실행',exact:true})).toBeDisabled();
 };
 const readAPI=async(scope:Scope)=>{
  transport=scope.transport;expect((await readReply('/api/project/current')).value).toEqual(scope.project);
  const routes=['/api/project/current','/api/project/labelsets','/api/project/preferences','/api/team-data','/api/team-data/readiness','/api/dataset/metadata?limit=100','/api/dataset/metadata/statistics','/api/dataset/versions','/api/dataset/revisions','/api/flowchart/draft','/api/flowchart/pipeline/active-version','/api/flowchart/pipelines?source_dataset_path='+encodeURIComponent(scope.source),...scope.images.map(image=>'/api/annotations/part?file_path='+encodeURIComponent(image.file))];
  const replies:Record<string,unknown>={};for(const route of routes){const {reply,value:body}=await readReply(route);replies[route]={status:200,url:reply.url,raw_body:reply.body,raw_body_sha256:hash(Buffer.from(reply.body)),body};}
  expect((replies['/api/flowchart/draft'] as any).body).toEqual(scope.saved);assertDraftRecord(scope.saved,scope.saved.pipeline,scope.context);expect((replies['/api/flowchart/pipeline/active-version'] as any).body).toEqual({version_id:null});expect((replies['/api/dataset/revisions'] as any).body).toEqual({active_revision:null,revisions:[]});expect((replies[routes[11]] as any).body).toEqual({pipelines:[],total:0});
  const pin=splitControlFile(scope.draftFile,custody);expect(JSON.parse(pin.bytes.toString('utf8'))).toEqual(Object.fromEntries(Object.entries(scope.saved).filter(([key])=>key!=='active_version_id')));await settle();await settleWrites();return{API:replies,draft_fullpin:{path:scope.draftFile,raw9:pin.raw,sha256:pin.sha256,size:pin.bytes.length}};
 };
 const enter=async(scope:Scope)=>{
  active=scope;transport=scope.transport;await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name);await galleryReadsSettled(page);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(4).click();await page.getByRole('tab',{name:'편집',exact:true}).click();await expect(page.getByRole('heading',{name:'검사 플로우 편집기',exact:true})).toBeVisible();await expect(page.locator('[data-flow-node-id]')).toHaveCount(scope.saved.pipeline.nodes.length);await expect.poll(rendered).toEqual(view(scope.saved.pipeline));await savedStatus(false);await settle();await settleWrites();
 };
  const documentReload=async(initial:boolean)=>{
   expect(documentBootstrap).toBeUndefined();await settle();await settleWrites();const readStart=reads.length,phase={epoch:++documentBootstrapEpoch,project_id:A.project.id,started:performance.now()};documentBootstrap=phase;
   try{if(initial&&url)await page.goto(url);else await page.reload();await enter(A);}finally{documentBootstrap=undefined;}
   // Close phase admission first; finish the exact admitted rows under each original event-start deadline.
   await settle();await settleWrites();const admitted=reads.slice(readStart).filter(row=>row.document_bootstrap?.epoch===phase.epoch),unscoped=admitted.filter(row=>row.bootstrap_unscoped===true);
   expect([...new Set(unscoped.map(row=>new URL(row.request.url()).pathname))].sort()).toEqual([...RELOAD_BOOTSTRAP_PATHS].sort());
   for(const row of unscoped){expect(row.complete&&!row.failed).toBe(true);expect(row.status).toBe(200);expect(row.request_context).toBeNull();expect(row.response_context).toEqual(A.transport);expect(row.finished!).toBeLessThanOrEqual(row.deadline);}
   documentBootstrapProofs.push({phase,initial,ordinary_document_navigation:true,allowlist:[...RELOAD_BOOTSTRAP_PATHS],phase_admission_closed:true,all_event_start_10s_reads_complete:true,reads:unscoped.map(({request,pending,failure,...row})=>({...row,url:request.url(),method:request.method(),failure:row.failed?String(failure):null}))});
  };
 const switchProject=async(scope:Scope)=>{
  await settle();await settleWrites();const deadline=performance.now()+10_000;
  await splitControlWithin(page.getByTitle('프로젝트 관리',{exact:true}).click(),deadline,'ordinary project manager');const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});await splitControlWithin(dialog.getByRole('button',{name:'최근 프로젝트',exact:true}).click(),deadline,'ordinary recent project list');
  const item=dialog.getByRole('button').filter({has:page.locator('span[title]').filter({hasText:scope.project.project_dir})});await expect(item).toHaveCount(1);await expect(item).toBeEnabled();const firstWrite=writes.length;active=scope;
  await splitControlWithin(item.click(),deadline,'exact ordinary recent project click');await expect(dialog).toHaveCount(0);await enter(scope);await settleWrites();
  const own=writes.slice(firstWrite).filter(row=>new URL(row.url).pathname==='/api/project/open');expect(own).toHaveLength(1);expect(own[0].request_body).toEqual({project_dir:scope.project.project_dir});expect(own[0].response).toEqual(scope.project);transitions.push({project_id:scope.project.id,project_dir:scope.project.project_dir,request_body:own[0].request_body,status:own[0].status,sha256:own[0].sha256,started:own[0].started,deadline:own[0].deadline,finished:own[0].finished});
 };
 const assertOnlyDraftChanges=(before:ReturnType<typeof treeState>,after:ReturnType<typeof treeState>)=>{
  expect(Object.keys(after)).toEqual(Object.keys(before));const relative=path.relative(A.project.project_dir,A.draftFile).split(path.sep).join('/'),ancestors=new Set<string>();let parent=path.posix.dirname(relative);while(parent!=='.'){ancestors.add(parent);parent=path.posix.dirname(parent);}ancestors.add('');
  for(const role of Object.keys(before)){
   const left=before[role] as Record<string,any>,right=after[role] as Record<string,any>;expect(Object.keys(right)).toEqual(Object.keys(left));
   for(const name of Object.keys(left)){
    if(role==='A_project'&&name===relative){expect(right[name].kind).toBe('file');expect(right[name].raw[0]).toBe(left[name].raw[0]);for(const index of[2,3,7,8])expect(right[name].raw[index]).toBe(left[name].raw[index]);continue;}
    if(role==='A_project'&&ancestors.has(name)){
     expect(right[name].kind).toBe('directory');expect(right[name].names).toEqual(left[name].names);for(const index of[0,1,2,3,4,7,8])expect(right[name].raw[index]).toBe(left[name].raw[index]);for(const index of[5,6])expect(BigInt(right[name].raw[index])>=BigInt(left[name].raw[index])).toBe(true);continue;
    }
    expect(right[name],role+'/'+name).toEqual(left[name]);
   }
  }
 };
 const newSave=async(start:number,match:(pipeline:any)=>boolean,kind:string)=>{
  const deadline=performance.now()+10_000;await splitControlWithin(expect.poll(()=>writes.slice(start).filter(row=>row.method==='PUT'&&new URL(row.url).pathname==='/api/flowchart/draft'&&row.project_id===A.project.id&&row.complete&&match(row.request_body.pipeline)).length,{timeout:10_000}).toBeGreaterThan(0),deadline,'genuine owning '+kind);
  await settleWrites();const rows=writes.slice(start).filter(row=>row.method==='PUT'&&new URL(row.url).pathname==='/api/flowchart/draft'&&row.project_id===A.project.id&&match(row.request_body.pipeline));expect(rows.length).toBeGreaterThan(0);const row=rows[rows.length-1];expect(row.complete&&!row.failed).toBe(true);A.saved=row.response;
  await savedStatus(true);expect(await rendered()).toEqual(view(A.saved.pipeline));const full=await readAPI(A);saveProofs.push({kind,original_debounce_ms:650,request:row.request_body,response:row.response,request_context:row.request_context,response_context:row.response_context,status:200,raw_body_sha256:row.sha256,raw_body_bytes:row.size,started:row.started,deadline:row.deadline,finished:row.finished,whole_readback:full});return row;
 };
 const explicit=async(match:(pipeline:any)=>boolean)=>{const start=writes.length,deadline=performance.now()+10_000;await splitControlWithin(save.click(),deadline,'ordinary explicit draft save');return newSave(start,match,'explicit-button');};
 const reopen=async(pointer:string,details:Record<string,unknown>,beforeAction:ReturnType<typeof treeState>)=>{
  await settle();await settleWrites();assertOnlyDraftChanges(beforeAction,treeState());const A_before=await readAPI(A);await savedStatus(true);baseline=treeState();reachedBaseline=true;const start=writes.length,readStart=reads.length;
  await switchProject(B);const B_after=await readAPI(B);expect(B_after).toEqual(B_initial);expect(treeState()).toEqual(baseline);expect(sourceState()).toEqual(sourceBefore);
  await switchProject(A);expect(await readAPI(A)).toEqual(A_before);await documentReload(false);const A_after=await readAPI(A);expect(A_after).toEqual(A_before);expect(treeState()).toEqual(baseline);expect(sourceState()).toEqual(sourceBefore);expect(await rendered()).toEqual(view(A.saved.pipeline));
  const changed=writes.slice(start);expect(changed).toHaveLength(2);for(const row of changed){expect(row.method).toBe('POST');expect(new URL(row.url).pathname).toBe('/api/project/open');expect(row.complete&&!row.failed).toBe(true);}
  const replies=reads.slice(readStart);for(const scope of[B,A])expect(replies.some(row=>new URL(row.request.url()).pathname==='/api/flowchart/draft'&&(row.body as any)?.context?.project_id===scope.project.id&&(row.body as any)?.draft_sha256===scope.saved.draft_sha256)).toBe(true);
  reopenProofs.push({pointer,details,A_before,B_after,A_after,whole_saved_pipeline:A.saved.pipeline,exact_saved_hash:A.saved.draft_sha256,protected_before:baseline,protected_after:treeState(),allowed_writes:changed.map(({request,pending,failure,...row})=>({...row,failure:row.failed?String(failure):null})),own_draft_GETs:replies.filter(row=>new URL(row.request.url()).pathname==='/api/flowchart/draft').map(({request,pending,failure,...row})=>({...row,url:request.url(),failure:row.failed?String(failure):null}))});cells.push({pointer,dimension:'reopen',...details,ordinary_project_transitions:['A','B','A'],full_graph_context_hash_raw9_reopened:true});reachedBaseline=false;
 };
 page.on('request',observe);
 try{
  await documentReload(true);await switchProject(B);B_initial=await readAPI(B);await switchProject(A);await readAPI(A);await settle();await settleWrites();const initialized=treeState();
  expect(initialized.A_source).toEqual(beforeInitialization.A_source);expect(initialized.B_source).toEqual(beforeInitialization.B_source);expect(initialized.harness_dataset).toEqual(beforeInitialization.harness_dataset);
  const quick=page.locator('details').filter({has:page.locator('summary').filter({hasText:/^빠른 노드 추가·연결·실행 자원$/})});const openQuick=async()=>{await expect(quick).toHaveCount(1);if(await quick.getAttribute('open')===null)await quick.locator(':scope > summary').click();};await openQuick();
  // a5.reopen: actual button keyboard activation, generated ID, and actual node keyboard selection.
  const inspectionBefore=treeState(),inspectionBase=JSON.parse(JSON.stringify(A.saved.pipeline)),inspectionStart=writes.length,inspectionButton=quick.getByRole('button',{name:'검사 모델',exact:true});await expect(inspectionButton).toBeEnabled();await inspectionButton.focus();await inspectionButton.press('Space');
  await expect(page.locator('[data-flow-node-id]')).toHaveCount(5);const inspectionIDs=(await page.locator('[data-flow-node-id]').evaluateAll(rows=>rows.map(row=>row.getAttribute('data-flow-node-id')))).filter(id=>!inspectionBase.nodes.some((node:any)=>node.id===id));expect(inspectionIDs).toHaveLength(1);const inspectionId=inspectionIDs[0]!;expect(inspectionId).toMatch(/^node_inspection_[0-9]+_[a-z0-9]{1,5}$/);
  const inspectionMatch=(pipeline:any)=>pipeline.nodes.length===5&&pipeline.nodes.some((node:any)=>node.id===inspectionId);
  await newSave(inspectionStart,inspectionMatch,'650ms-autosave');const inspection=A.saved.pipeline.nodes.find((node:any)=>node.id===inspectionId);expect(inspection.data.node_type).toBe('inspection');expect(inspection.data.label).toBe('검사 모델 1');expect(inspection.data.task).toBe('anomaly');expect(inspection.data.model_job_id).toBeNull();expect(inspection.data.threshold).toBe(0.5);expect(inspection.data.params).toEqual({min_defect_area_px:8});expect(inspection.position).toEqual({x:670,y:80});expect(A.saved.pipeline.nodes.slice(0,4)).toEqual(inspectionBase.nodes);expect(A.saved.pipeline.edges).toEqual(inspectionBase.edges);
  const inspectionFocus=page.locator('[data-flow-node-id="'+inspectionId+'"] [data-flow-node-focus]');await inspectionFocus.focus();await inspectionFocus.press('Space');await expect(inspectionFocus).toHaveAttribute('aria-label','검사 모델 1 노드 · 선택됨');await expect(page.getByRole('textbox',{name:'노드 명칭',exact:true})).toHaveValue('검사 모델 1');await explicit(inspectionMatch);
  await reopen(F064_POINTERS[0],{action:'F064.native-keyboard-inspection-add-select',actual_key:'Space',generated_inspection_id:inspectionId,selected_own_node:true,model_job_id:null},inspectionBefore);
  // a9.reopen: actual ROI button and actual numeric input Enter/blur commits; [x,y,x+width,y+height].
  await openQuick();const roiBefore=treeState(),roiBase=JSON.parse(JSON.stringify(A.saved.pipeline)),roiStart=writes.length,roiButton=quick.getByRole('button',{name:'고정 ROI',exact:true});await expect(roiButton).toBeEnabled();await roiButton.focus();await roiButton.press('Space');await expect(page.locator('[data-flow-node-id]')).toHaveCount(6);
  const roiIDs=(await page.locator('[data-flow-node-id]').evaluateAll(rows=>rows.map(row=>row.getAttribute('data-flow-node-id')))).filter(id=>!roiBase.nodes.some((node:any)=>node.id===id));expect(roiIDs).toHaveLength(1);const roiId=roiIDs[0]!;expect(roiId).toMatch(/^node_fixed_roi_[0-9]+_[a-z0-9]{1,5}$/);await expect(page.locator('[data-flow-node-id="'+roiId+'"] [data-flow-node-focus]')).toHaveAttribute('aria-label','고정 ROI 2 노드 · 선택됨');
  for(const [label,amount] of[['X 시작 (px)','16'],['Y 시작 (px)','18'],['너비 (px)','40'],['높이 (px)','44']]){await expect(save).toBeEnabled();const input=page.getByRole('spinbutton',{name:label,exact:true});await expect(input).toHaveCount(1);await input.fill(amount);await input.press('Enter');await expect(input).toHaveValue(amount);}
  const roiMatch=(pipeline:any)=>pipeline.nodes.length===6&&JSON.stringify(pipeline.nodes.find((node:any)=>node.id===roiId)?.data.params.roi_bbox)==='[16,18,56,62]';await newSave(roiStart,roiMatch,'650ms-autosave');const roi=A.saved.pipeline.nodes.find((node:any)=>node.id===roiId);expect(roi.data.node_type).toBe('fixed_roi');expect(roi.data.label).toBe('고정 ROI 2');expect(roi.data.model_job_id).toBeNull();expect(roi.data.params).toEqual({roi_bbox:[16,18,56,62]});expect(roi.position).toEqual({x:325,y:320});expect(A.saved.pipeline.nodes.slice(0,5)).toEqual(roiBase.nodes);expect(A.saved.pipeline.edges).toEqual(roiBase.edges);await explicit(roiMatch);
  await reopen(F064_POINTERS[1],{action:'F064.native-keyboard-fixed-roi-node-add',actual_key:'Space',generated_ROI_id:roiId,actual_numeric_input_Enter_commits:true,roi_bbox:[16,18,56,62],within_original_256_square:true},roiBefore);
  // a6.reopen: real typed ROI OUT -> IMAGE/ROI IN controls. No runnable-model or activation claim.
  await openQuick();const edgeBefore=treeState(),edgeBase=JSON.parse(JSON.stringify(A.saved.pipeline)),edgeStart=writes.length;
  const from=page.getByRole('button',{name:'Start connection from 고정 ROI 2 ROI OUT',exact:true}),to=page.getByRole('button',{name:'Connect to 검사 모델 1 IMAGE/ROI IN',exact:true});await expect(from).toHaveAttribute('data-flow-port',roiId+':out:0');await expect(to).toHaveAttribute('data-flow-port',inspectionId+':in:0');await expect(from).toBeEnabled();await expect(to).toBeEnabled();await from.focus();await from.press('Enter');await expect(page.getByRole('button',{name:'연결 시작: 고정 ROI 2 · 취소',exact:true})).toBeVisible();await to.focus();await to.press('Enter');await expect(page.getByRole('button',{name:'연결 시작: 고정 ROI 2 · 취소',exact:true})).toHaveCount(0);
  const edgeMatch=(pipeline:any)=>pipeline.edges.length===edgeBase.edges.length+1&&pipeline.edges.some((edge:any)=>edge.source===roiId&&edge.target===inspectionId&&edge.payload_type==='roi');await newSave(edgeStart,edgeMatch,'650ms-autosave');const added=A.saved.pipeline.edges.filter((edge:any)=>!edgeBase.edges.some((old:any)=>old.id===edge.id));expect(added).toHaveLength(1);expect(added[0].id).toMatch(/^edge_[0-9]+_[a-z0-9]{1,8}$/);expect(added[0].source).toBe(roiId);expect(added[0].target).toBe(inspectionId);expect(added[0].payload_type).toBe('roi');expect(A.saved.pipeline.edges.slice(0,edgeBase.edges.length)).toEqual(edgeBase.edges);expect(A.saved.pipeline.nodes).toEqual(edgeBase.nodes);await explicit(edgeMatch);
  await reopen(F064_POINTERS[2],{action:'F064.native-keyboard-typed-port-connection',actual_key:'Enter',source_ROI_id:roiId,target_inspection_id:inspectionId,generated_edge_id:added[0].id,payload_type:'roi',actual_pending_marker_finished:true},edgeBefore);
  await settle();await settleWrites();baseline=treeState();expect(sourceState()).toEqual(sourceBefore);expect(writes.filter(row=>new URL(row.url).pathname==='/api/flowchart/run'||/\/(train|training|predict|evaluate|jobs)(\/|$)/.test(new URL(row.url).pathname))).toEqual([]);expect(cells.map(cell=>cell.pointer)).toEqual([...F064_POINTERS]);
  await e.screenshot(page,(native?'native':'browser')+'-F064-three-model-less-reopen');completed=true;
  e.note('F064_model_less_reopen',{schema:'modu-vision.F064-model-less-reopen/v1',selected_pending_pointers:F064_POINTERS,cells,projects:scopes.map(scope=>scope.project),contexts:scopes.map(scope=>scope.context),transport_contexts:scopes.map(scope=>scope.transport),source_images:scopes.map(scope=>scope.images),original_initialization_before:beforeInitialization,own_initialized_baseline:initialized,save_proofs:saveProofs,reopen_proofs:reopenProofs,document_bootstrap_proofs:documentBootstrapProofs,transitions,protected_final:baseline,UI_SOURCE_expected:UI_SOURCE,FLOW_DRAFT_SERVICE_expected:FLOW_DRAFT_SERVICE,UI_SOURCE_before:sourceBefore,UI_SOURCE_after:sourceState(),fixture_fullbody_clocks:fixtureCalls,renderer_mutations:writes.map(({request,pending,failure,...row})=>({...row,failure:row.failed?String(failure):null})),scope:{source_electron:native,browser:!native,installed_native:false,training:false,inference:false,GPU:false,active_flow:false,quality_approval:false,Native_acceptance:false,parent_acceptance:false,registry_credit:false}});
 }catch(cause){failed=true;primary=cause;}
 finally{
  const attempt=async(label:string,work:()=>unknown|Promise<unknown>)=>{try{await work();}catch(cause){secondary.push({label,cause});if(!failed){failed=true;primary=cause;}}};
  await attempt('request admission detach',()=>{page.off('request',observe);removed=true;});
  await attempt('original admitted GET full closure',async()=>{await settle();expect(reads.every(row=>row.complete&&!row.failed)).toBe(true);});
  await attempt('original admitted mutation full closure',async()=>{await settleWrites();expect(writes.every(row=>row.complete&&!row.failed)).toBe(true);});
  if(baseline!==undefined)await attempt('full final declared trees including SQLite WAL SHM',()=>expect(treeState()).toEqual(baseline));
  await attempt('all finite UI/source full SHA/raw9',()=>expect(sourceState()).toEqual(sourceBefore));
  await attempt('once-only owned FD closure',()=>{expect(custody.opened).toBe(custody.closed);expect(custody.uncertain).toEqual([]);});
  await attempt('closure evidence',()=>e.note('F064_model_less_reopen_closure',{first_exception:failed?String(primary):null,secondary:secondary.map(row=>String(row)),FD_ledger:custody,all_owned_FDs_closed:custody.opened===custody.closed&&custody.uncertain.length===0,request_observer_removed:removed,all_original_GETs_complete:reads.every(row=>row.complete&&!row.failed),all_original_mutations_complete:writes.every(row=>row.complete&&!row.failed),original_reads:reads.map(({request,pending:_pending,failure,...row})=>({...row,url:request.url(),method:request.method(),failure:row.failed?String(failure):null})),success_assertions_completed:completed}));
 }
 if(failed)throw primary;expect(removed).toBe(true);expect(completed).toBe(true);
}

test('F064 model-less inspection ROI and typed connection drafts reopen with exact saved graphs',async({page,request,renderer,workspace,evidence})=>{
 test.setTimeout(160_000);await installDesktopHostShim(page,renderer.port);
 const api:FlowApi=async(route,body,method,headers)=>{const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),headers,...(body===undefined?{}:{data:body}),timeout:10_000});return {status:response.status(),body:await response.text(),url:response.url(),headers:response.headers()};};
 await flowModelLessReopenExercise(page,workspace,evidence,api,false,renderer.origin,renderer.url);
});
test('native F064 model-less inspection ROI and typed connection drafts reopen with exact saved graphs',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 test.setTimeout(160_000);const {window}=electronSession,backend=await electronSession.waitForBackend();
 const api:FlowApi=(route,body,method,headers)=>window.evaluate(async({port,route,body,method,headers})=>{const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),10_000);try{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),headers:{'Content-Type':'application/json',...headers},...(body===undefined?{}:{body:JSON.stringify(body)}),signal:abort.signal});return {status:response.status,body:await response.text(),url:response.url,headers:Object.fromEntries(response.headers.entries())};}finally{clearTimeout(timer);}},{port:backend.port,route,body,method,headers});
 await flowModelLessReopenExercise(window,workspace,evidence,api,true,`http://127.0.0.1:${backend.port}`);
});
