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

// New standalone Source-only derivative. Root alone runs and qualifies these eight cells.
const harness=require('./fixtures/harness.cjs');
const sha=(raw:Buffer|string)=>crypto.createHash('sha256').update(raw).digest('hex');
const F064_POINTERS=[
 "/records/63/actions/6/scenarios/empty",
 "/records/63/actions/6/scenarios/error",
 "/records/63/actions/7/scenarios/invalid",
 "/records/63/actions/2/scenarios/invalid",
 "/records/63/actions/5/scenarios/empty",
 "/records/63/actions/1/scenarios/empty",
 "/records/63/actions/4/scenarios/empty",
 "/records/63/actions/4/scenarios/cancel"
] as const;
const UI_SOURCE={
 "scripts/e2e/fixtures/flow_navigation_control.py": "b5752088a03ff1640510208e15a17b3f1389295c62dadabaf2ff6511a929ee73",
 "scripts/e2e/fixtures/flow_template_control.py": "81f4a1844d05021d6c4c668627a732120c11aa20e45294ba55da0bfca410572a",
 "scripts/e2e/fixtures/flow_large_dag_control.py": "6434d381ed45d68922c058544d0b515caa3d19d93d88c90028a3463a9903d871",
 "scripts/e2e/qa/appFlow.ts": "4eed3e5dad1d9d65a6b19fc9bb51a1f9b0e23911a39e7f255220bec0ce37fea2",
 "src/renderer/components/flowchart/FlowchartStudio.tsx": "1a5c0f37dd636062a223fd3548df064afba785dba272537265df39f0a76b8c39",
 "src/renderer/components/flowchart/FlowEditorWorkspace.tsx": "e58f0b7a62572516d9ea04cbb45dafdfb104e805753e9f13a3c8f67c88210ee7",
 "src/renderer/components/flowchart/FlowDraftControls.tsx": "fe003c308ecd62b03aea4106e86d446b7a71d008ba0486b36132e6a1b39eb02b",
 "src/renderer/components/flowchart/FlowGraphNavigator.tsx": "656832c214f41742d5bc0f66c9f8983c5fb6892cfaf3b42b48ee353f282da20a",
 "src/renderer/components/flowchart/FlowInspectorPanel.tsx": "c108ee18336c2d3cdc2accb200eb739b0a3252c7fcbd60f35902e66da06c6707",
 "src/renderer/components/flowchart/CustomNode.tsx": "97350539036826f52411bc8defe234d638e2c18fde5092a5f104af7557dd1828",
 "src/renderer/components/flowchart/ImagePickerModal.tsx": "0ef27d643ddc2fddd5014e2c27087116eefbc87eb5dd92c95b732a401ceb6e4e",
 "src/renderer/stores/useFlowchartStore.ts": "99796bbeabb039f63044a05c65a343c6d1d2b4ef6a253afcd588480a9222bb35",
 "src/renderer/services/api.ts": "379c49c5aa0d6a4abc772adf9f73c6f4b7b181f2713fbbb2d0623cf292e932ab",
 "src/renderer/components/flowchart/FlowWorkspacePanel.tsx": "6e7c74ae465e6a426601de756a91a9966615195f52c1e6aa9e4691b187baa8bf",
 "src/renderer/components/flowchart/FlowChangeDialog.tsx": "2e66a530f565b8211829d1cb3507bf2d9d1e7aa7173a1a37a54de98d19230347",
 "src/renderer/components/flowchart/FlowRecipeDialog.tsx": "09418ce7091fd126a9f341fddf0acb9d30f20fe955268c71ae21f25204c935ba",
 "src/renderer/components/flowchart/flowchartGraph.ts": "2db91b8c7d514e39a1dfbb5edf60c52c82d69b3c51aadb47e17b9cfe9e640743",
 "src/renderer/components/flowchart/flowchartViewport.ts": "65564c68bb1242e5c78767cdbc1b77596815b14ef071d68bfb96b42799e5aac1",
 "src/renderer/components/common/ImagePickerModal.tsx": "169020303e9b7a1de70cc483d3a19a7881bbb84b22ed73e87053f06e9af7f72f",
 "backend/api/routes_flowchart.py": "8f8964c35542aade10baf627341ecf5190d75f62438ddfc52fdad52706cd952a",
 "src/renderer/components/flowchart/FlowNodeDebugger.tsx": "82b81c5ceb3556f4bdc14fd7a93edbe9657a528a1af140c5755aa9e6daa83a2f",
 "src/renderer/components/flowchart/flowWorkspace.ts": "17c39a0236efa5b444fcc99751bc8db95d613071c916ecf0e3ec44ebb49253e4",
 "scripts/e2e/fixtures/remaining-project-handoff.ts": "7a7c74a2f42a5d6ad991dadc97626c1f8330f962edaf73fd461d31f9c4764904",
 "scripts/e2e/fixtures/test.ts": "7a7effa184e71c02414773308e0ea88e16cf83dfbd72d77d47046f2cfc1b8d09",
 "scripts/e2e/fixtures/harness.cjs": "6fe3bb6411a28bc83c6da36ba0b2480b12de6b86fd08daa61d8d0e76d7dac950",
 "scripts/e2e/fixtures/desktop-host-shim.ts": "4f598862ec0e08189628c72bbb552f72bf8fd73f5a9a05b8da1d3ddf516dcfa6",
 "backend/api/routes_dataset.py": "92df70e8ad696b3d1a81eab5c290132c0802768ffaaeaa778eb0aad76eeabca3"
} as const;
const DIALOG_SOURCE={
 "src/renderer/components/flowchart/flowRecipes.ts": "56b176c1d85124b9a4c3333d543a137b1adc12592e6386c636a514a45ebac17b",
 "src/renderer/components/common/WorkspaceDialog.tsx": "2f973de1f48350ce695f425cac6b158ab4801dbf02cd7c6755fa6f530f15a291",
 "src/renderer/components/common/AsyncAction.tsx": "9f91e1eabc83afa661bc6fcddd405da0e2cd8324eee15e7e16052b7bde645a00"
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
type FlowRead={request:SplitControlRequest;started:number;deadline:number;post_baseline:boolean;complete:boolean;pending:Promise<void>;
 status?:number;body?:unknown;sha256?:string;size?:number;request_context?:unknown;response_context?:unknown;finished?:number;failed?:boolean;failure?:unknown;retired_gallery_read?:boolean;failure_text?:string};
type OCROwnedThumbScope={source:string;images:Array<{image:string;sha256:string}>;project:{id:string}};
async function ocrOwnedThumbnail(page:Page,response:import('@playwright/test').Response,request:SplitControlRequest,deadline:number,origin:string,scopes:OCROwnedThumbScope[],contexts:Map<string,unknown>,custody:SplitControlCustody){
 const u=new URL(request.url());expect(request.method()).toBe('GET');expect(u.origin).toBe(origin);expect(Array.from(u.searchParams.keys()).sort()).toEqual(['file_path']);
 const matches=scopes.flatMap(scope=>scope.images.filter(image=>path.join(scope.source,image.image)===u.searchParams.get('file_path')).map(image=>({scope,image})));expect(matches).toHaveLength(1);const {scope,image}=matches[0],file=path.join(scope.source,image.image);
 expect(u.pathname).toBe('/api/dataset/thumbnail/'+path.basename(image.image));expect(Object.fromEntries(u.searchParams.entries())).toEqual({file_path:file});expect(contexts.has(scope.project.id)).toBe(true);
 const rh=request.headers(),sh=response.headers(),requestContext=rh['x-vision-context']===undefined?null:JSON.parse(rh['x-vision-context']);if(rh['x-vision-project']!==undefined)expect(rh['x-vision-project']).toBe(scope.project.id);if(requestContext!==null)expect(requestContext).toEqual(contexts.get(scope.project.id));
 expect(sh['x-vision-context']).toBeDefined();const responseContext=JSON.parse(sh['x-vision-context']);expect(responseContext).toEqual(contexts.get(scope.project.id));expect(responseContext.project_id).toBe(scope.project.id);
 const sourcePin=splitControlFile(file,custody);expect(sourcePin.sha256).toBe(image.sha256);expect(sourcePin.bytes.subarray(0,8).toString('hex')).toBe('89504e470d0a1a0a');expect([sourcePin.bytes.readUInt32BE(16),sourcePin.bytes.readUInt32BE(20)]).toEqual([256,256]);
 const revalidated=response.status()===304;if(!revalidated){expect(response.status()).toBe(200);expect(sh['content-type']).toBe('image/jpeg');}let bytes=revalidated?Buffer.alloc(0):await splitControlWithin(response.body(),deadline,'whole owning JPEG thumbnail body');if(bytes.length===0){/* A 304 is the browser revalidating a thumbnail it already holds (the server sends an ETag); it has no body, so it is read again below like an empty body. *//* The app reads gallery thumbnails with fetch and keeps a Blob (6dd4c1f); the browser then retains no body for the test. The same URL is read once more from inside the page with the same context headers, so it carries the app's own credentials (Electron adds its token only to the window's requests, never to the test's request context, F-22), and that answer is checked in full below. */const again=await splitControlWithin(page.evaluate(async ({url,headers})=>{const reply=await fetch(url,{headers,cache:'no-store'});return {status:reply.status,type:reply.headers.get('content-type'),context:reply.headers.get('x-vision-context'),bytes:Array.from(new Uint8Array(await reply.arrayBuffer()))};},{url:request.url(),headers:Object.fromEntries(Object.entries(request.headers()).filter(([key])=>key.startsWith('x-vision-')))}),deadline,'owning JPEG thumbnail re-read');expect(again.status).toBe(200);expect(again.type).toBe('image/jpeg');if(sh['x-vision-context']!==undefined)expect(JSON.parse(again.context!)).toEqual(JSON.parse(sh['x-vision-context']));bytes=Buffer.from(again.bytes);}expect(bytes.length).toBeGreaterThan(0);if(!revalidated&&sh['content-length']!==undefined)expect(sh['content-length']).toBe(String(bytes.length));expect(bytes.subarray(0,2).toString('hex')).toBe('ffd8');expect(bytes.subarray(-2).toString('hex')).toBe('ffd9');expect(await splitControlWithin(response.finished(),deadline,'owning JPEG HTTP completion')).toBeNull();
 const decoded=await splitControlWithin(page.evaluate(async data=>{let bitmap:ImageBitmap|undefined,result:{width:number;height:number;bitmap_closed:boolean}|undefined,failed=false,primary:unknown;
  try{bitmap=await createImageBitmap(new Blob([new Uint8Array(data)],{type:'image/jpeg'}));result={width:bitmap.width,height:bitmap.height,bitmap_closed:false};}catch(cause){failed=true;primary=cause;}
  finally{if(bitmap!==undefined){const owned=bitmap;bitmap=undefined;try{owned.close();if(result)result.bitmap_closed=true;}catch(cause){if(!failed){failed=true;primary=cause;}else if(primary instanceof Error)primary.message+='; owned bitmap close failed: '+String(cause);}}}
  if(failed)throw primary;return result!;
 },Array.from(bytes)),deadline,'full owning JPEG decode and bitmap closure');expect(decoded).toEqual({width:128,height:128,bitmap_closed:true});expect(splitControlFile(file,custody)).toEqual(sourcePin);
 return {status:200,sha256:hash(bytes),body:{kind:'owning_binary_thumbnail',media_type:'image/jpeg',byte_length:bytes.length,whole_body_sha256:hash(bytes),decoded,default_size:128,source_geometry:{width:256,height:256},file_path:file,source_image_sha256:sourcePin.sha256,source_raw9:sourcePin.raw,query:Object.fromEntries(u.searchParams.entries()),owner_project_id:scope.project.id,request_context:requestContext,response_context:responseContext,response_finished:null,no_JSON_decode:true,no_cache_status_exception:true}};
}

async function flowRefusalExercise(page:Page,w:Workspace,e:Evidence,api:FlowApi,native:boolean,origin:string,url?:string){
 const custody:SplitControlCustody={opened:0,closed:0,uncertain:[]};
 const sourceState=()=>Object.fromEntries(Object.entries({...UI_SOURCE,...DIALOG_SOURCE}).map(([relative,expected])=>{
  const file=path.join(harness.REPO_ROOT,relative),pin=splitControlFile(file,custody);expect(pin.sha256,relative).toBe(expected);return[relative,{path:file,raw9:pin.raw,sha256:pin.sha256}];
 }));
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
 const source=path.join(w.root,'owned-F064-refusal-source'),normal=path.join(source,'train','good');fs.mkdirSync(normal,{recursive:true});
 const originals=[100,101].map((blue,index)=>{
  const file=path.join(normal,index?'part-1.png':'part.png'),bytes=png(256,3,(x,y)=>[x,y,blue]);fs.writeFileSync(file,bytes,{flag:'wx'});
  const pin=splitControlFile(file,custody);expect(pin.bytes).toEqual(bytes);return {file,image:path.relative(source,file).split(path.sep).join('/'),sha256:pin.sha256,raw9:pin.raw,RGB:assertSourcePixels(pin.bytes,blue)};
 });
 const created=await value('/api/project/create',{name:'Owned F064 existing refusal controls',task:'anomaly'});
 await value('/api/project/update',{source_dataset_dir:source},'PUT');assertAnomalyImport(await value('/api/dataset/import',{folder_path:source,task:'anomaly',validate_images:true}));
 const owningProjectReply=await readReply('/api/project/current'),project=owningProjectReply.value;expect(project.id).toBe(created.id);expect(project.source_dataset_dir).toBe(source);expect(project.task).toBe('anomaly');
 const owningTransport=owningProjectReply.reply.headers['x-vision-context'];expect(owningTransport).toBeDefined();transport=JSON.parse(owningTransport);expect(Object.keys(transport).sort()).toEqual(['actor_id','mode','project_id','workspace_id']);expect(transport.project_id).toBe(project.id);expect(transport.mode).toBe('local');for(const key of ['actor_id','workspace_id'])expect(transport[key]).toMatch(/^[A-Za-z0-9_.:-]{1,128}$/);
 expect(path.dirname(project.project_dir)).toBe(native?path.join(w.userData,'projects'):w.projects);expect(fs.realpathSync(project.project_dir)).toBe(project.project_dir);expect(fs.lstatSync(project.project_dir).isSymbolicLink()).toBe(false);
 const context={project_id:project.id,source_dataset_path:source,labelset_id:project.active_labelset_id||'default'};
 const initial={id:'owned-F064-refusal-draft',name:'Owned editable no-model F064 refusal controls',nodes:[
  {id:'input-original',position:{x:32,y:170},data:{label:'Input original',node_type:'input'}},
  {id:'roi-original',position:{x:332,y:170},data:{label:'ROI original',node_type:'fixed_roi',params:{roi_bbox:[2,3,20,21]}}},
  {id:'inspection-original',position:{x:632,y:410},data:{label:'Inspection original',node_type:'inspection',task:'anomaly',model_job_id:null,threshold:0.5,params:{min_defect_area_px:8}}},
  {id:'decision-original',position:{x:632,y:170},data:{label:'Decision original',node_type:'decision',rule:'any_defect_is_ng'}},
  {id:'output-original',position:{x:932,y:170},data:{label:'Output original',node_type:'output'}}],edges:[
  {id:'input-roi-original',source:'input-original',target:'roi-original',payload_type:'image'},
  {id:'roi-decision-original',source:'roi-original',target:'decision-original',payload_type:'roi'},
  {id:'decision-output-original',source:'decision-original',target:'output-original',payload_type:'result'}]};
 const saved=await value('/api/flowchart/draft',{pipeline:initial,context,base_version_id:'none'},'PUT');assertDraftRecord(saved,saved.pipeline,context);
 for(const node of saved.pipeline.nodes)expect(node.data.model_job_id).toBeNull();
 const draftFile=path.join(project.project_dir,'flowcharts','drafts',context.labelset_id,crypto.createHash('sha256').update(source).digest('hex').slice(0,16),'draft.json'),draftPin=splitControlFile(draftFile,custody);
 expect(JSON.parse(draftPin.bytes.toString('utf8'))).toEqual(Object.fromEntries(Object.entries(saved).filter(([key])=>key!=='active_version_id')));
 const roots={project:project.project_dir,source,harness_dataset:w.dataset};
 const treeState=()=>Object.fromEntries(Object.entries(roots).map(([name,root])=>[name,splitControlTree(root,custody)]));
 const beforeInitialization=treeState();
 // Actual initial metadata/readiness transactions are completed before the protected baseline; no SHM/WAL exclusions.
 for(const route of ['/api/project/labelsets','/api/team-data','/api/team-data/readiness','/api/dataset/metadata?limit=100','/api/dataset/metadata/statistics','/api/flow-evaluations']){const reply=await value(route);if(route==='/api/flow-evaluations')expect(reply).toEqual({evaluations:[],total:0});}
 for(const image of originals)await value('/api/annotations/part?file_path='+encodeURIComponent(image.file));
 const endpoints=['/api/project/current','/api/project/labelsets','/api/project/preferences','/api/team-data','/api/team-data/readiness','/api/dataset/metadata?limit=100','/api/dataset/metadata/statistics','/api/dataset/versions','/api/dataset/revisions','/api/flowchart/draft','/api/flowchart/pipeline/active-version','/api/flowchart/models/catalog?source_dataset_path='+encodeURIComponent(source),'/api/flow-workspace/comparisons','/api/flowchart/pipelines?source_dataset_path='+encodeURIComponent(source),'/api/flow-evaluations'];
 const reads:FlowRead[]=[],writes:Array<{method:string;url:string;body:string|null;post_baseline:boolean}>=[],contexts=new Map<string,unknown>();
 const scope:OCROwnedThumbScope={source,images:originals,project};
 let reachedBaseline=false,baseline:ReturnType<typeof treeState>|undefined,baselineAPI:Record<string,unknown>|undefined,proof:Record<string,unknown>|undefined;
 let failed=false,primary:unknown,removed=false,completed=false;const secondary:unknown[]=[];
 const observe=(request:SplitControlRequest)=>{
  const u=new URL(request.url());if(u.origin!==origin||!u.pathname.startsWith('/api/'))return;
  if(request.method()!=='GET'){if(!['HEAD','OPTIONS'].includes(request.method()))writes.push({method:request.method(),url:request.url(),body:request.postData(),post_baseline:reachedBaseline});return;}
  const started=performance.now(),row:FlowRead={request,started,deadline:started+10_000,post_baseline:reachedBaseline,complete:false,pending:Promise.resolve()};reads.push(row);
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
   const response=await splitControlWithin(request.response(),row.deadline,'original F064 API GET response');expect(response).not.toBeNull();
   expect(request.frame()).toBe(page.mainFrame());expect(request.postData()).toBeNull();
   const rh=request.headers(),sh=response!.headers();row.request_context=rh['x-vision-context']===undefined?null:JSON.parse(rh['x-vision-context']);row.response_context=sh['x-vision-context']===undefined?null:JSON.parse(sh['x-vision-context']);
   if(rh['x-vision-project']!==undefined)expect(rh['x-vision-project']).toBe(project.id);
   if(row.request_context!==null){expect((row.request_context as any).project_id).toBe(project.id);if(transport)expect(row.request_context).toEqual(transport);}
   if(row.response_context!==null){expect((row.response_context as any).project_id).toBe(project.id);if(transport)expect(row.response_context).toEqual(transport);}
   if(row.post_baseline){expect(row.request_context).toEqual(transport);expect(rh['x-vision-project']).toBe(project.id);}
   if(u.pathname.startsWith('/api/dataset/thumbnail/')){
    expect(transport).toBeDefined();contexts.set(project.id,transport);const full=await ocrOwnedThumbnail(page,response!,request,row.deadline,origin,[scope],contexts,custody);row.status=full.status;row.sha256=full.sha256;row.body=full.body;row.size=(full.body as any).byte_length;
   }else{
    const bytes=await splitControlWithin(response!.body(),row.deadline,'whole original F064 API body');expect(await splitControlWithin(response!.finished(),row.deadline,'original F064 API completion')).toBeNull();
    row.status=response!.status();row.body=JSON.parse(bytes.toString('utf8'));row.sha256=hash(bytes);row.size=bytes.length;
    if(row.status!==200){
     expect(row.request_context).not.toBeNull();expect((row.request_context as any).project_id).toBe(project.id);
     if(u.pathname==='/api/dataset/library/images'){
      expect(u.search).toBe('?state=valid&limit=120');expect(row.status).toBe(409);expect(row.body).toEqual({detail:'No validated dataset revision is active; validate the source and accept a revision first'});
     }else{
      expect(u.pathname).toBe('/api/evaluation/results');expect(u.search).toBe('?'+new URLSearchParams({source_dataset_path:source,source_task:'anomaly'}).toString());expect(row.status).toBe(404);expect(row.body).toEqual({detail:'학습을 끝낸 모델을 아직 고르지 않았습니다. 학습을 먼저 끝낸 뒤 평가할 모델을 고르세요.'});
     }
    }else if(u.pathname==='/api/project/current'){expect((row.body as any).id).toBe(project.id);expect((row.body as any).source_dataset_dir).toBe(source);}
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
 const rendered=()=>page.evaluate(()=>({nodes:[...document.querySelectorAll<HTMLElement>('[data-flow-node-id]')].map(row=>({id:row.dataset.flowNodeId,label:row.querySelector('h4')?.textContent,x:parseFloat(row.style.left),y:parseFloat(row.style.top)})),edges:[...document.querySelectorAll<SVGElement>('path[data-flow-edge]')].map(row=>({id:row.dataset.flowEdge,source:row.dataset.flowFrom?.split(':')[0],target:row.dataset.flowTo?.split(':')[0]}))}));
 const expectedView={nodes:saved.pipeline.nodes.map((row:any)=>({id:row.id,label:row.data.label,x:row.position.x,y:row.position.y})),edges:saved.pipeline.edges.map((row:any)=>({id:row.id,source:row.source,target:row.target}))};
 const readAPI=async()=>{const replies:Record<string,unknown>={};for(const route of endpoints){const {reply,value:body}=await readReply(route);replies[route]={status:reply.status,url:reply.url,raw_body:reply.body,raw_body_sha256:hash(Buffer.from(reply.body)),body};}await settle();return replies;};
 const clean=async()=>{
  const identity=page.getByRole('region',{name:'플로우 식별 정보',exact:true});await expect(identity).toHaveCount(1);await expect(identity).toBeVisible();
  const savedState=identity.getByText(/^편집 r[0-9]+ · 초안 저장됨 · 실행본 활성화 전$/);await expect(savedState).toHaveCount(1);await expect(savedState).toBeVisible();
  const areas=page.getByRole('tablist',{name:'플로우 작업 영역',exact:true});await expect(areas).toHaveCount(1);await expect(areas).toBeVisible();const selected=areas.getByRole('tab',{selected:true});await expect(selected).toHaveCount(1);
  const mode=await selected.getAttribute('id');expect(['flow-area-edit','flow-area-evaluate']).toContain(mode);
  if(mode==='flow-area-edit'){
   await expect(areas.getByRole('tab',{name:'편집',exact:true})).toHaveAttribute('aria-selected','true');await expect(page.getByRole('tabpanel',{name:'편집',exact:true})).toBeVisible();
   await expect(page.getByRole('status').filter({hasText:/^초안 저장됨 · 실행본 활성화 전$/})).toHaveCount(1);
   await expect(page.getByRole('button',{name:'플로우 실행 취소',exact:true})).toBeDisabled();await expect(page.getByRole('button',{name:'플로우 다시 실행',exact:true})).toBeDisabled();
  }else{
   await expect(areas.getByRole('tab',{name:'일괄 평가',exact:true})).toHaveAttribute('aria-selected','true');const panel=page.getByRole('tabpanel',{name:'일괄 평가',exact:true});await expect(panel).toHaveCount(1);await expect(panel).toBeVisible();
   await expect(page.getByRole('tabpanel',{name:'편집',exact:true})).toHaveCount(0);await expect(page.getByRole('status').filter({hasText:/^초안 저장됨 · 실행본 활성화 전$/})).toHaveCount(0);
   await expect(page.getByRole('button',{name:'플로우 실행 취소',exact:true})).toHaveCount(0);await expect(page.getByRole('button',{name:'플로우 다시 실행',exact:true})).toHaveCount(0);
  }
 };
 const same=async()=>{expect(await readAPI()).toEqual(baselineAPI);await settle();expect(writes.filter(row=>row.post_baseline)).toEqual([]);expect(treeState()).toEqual(baseline);expect(sourceState()).toEqual(sourceBefore);expect(await rendered()).toEqual(expectedView);expect(splitControlFile(draftFile,custody)).toEqual(draftPin);await clean();};
 page.on('request',observe);
 try{
  if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
  // Expert-control regression: choose the existing full view through its ordinary UI, before the baseline.
  const showEverything=page.getByRole('button',{name:'모두 보기',exact:true});if(await showEverything.isVisible())await showEverything.click();await expect(page.getByRole('button',{name:'간단히 보기',exact:true})).toBeVisible();
  await settle();const current=await readReply('/api/project/current');const rawContext=current.reply.headers['x-vision-context'];expect(rawContext).toBeDefined();expect(JSON.parse(rawContext)).toEqual(transport);contexts.set(project.id,transport);
  await galleryReadsSettled(page);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(4).click();await page.getByRole('tab',{name:'편집',exact:true}).click();
  await expect(page.getByRole('heading',{name:'검사 플로우 편집기',exact:true})).toBeVisible();await expect(page.locator('[data-flow-node-id]')).toHaveCount(5);expect(await rendered()).toEqual(expectedView);
   const quick=page.locator('details').filter({has:page.locator('summary').filter({hasText:/^빠른 노드 추가·연결·실행 자원$/})});await expect(quick).toHaveCount(1);if(await quick.getAttribute('open')===null)await quick.locator(':scope > summary').click();await clean();
   const storageState=()=>page.evaluate(()=>Object.fromEntries(Object.keys(localStorage).sort().map(key=>[key,localStorage.getItem(key)])));
   const separator=page.getByRole('separator',{name:'속성 패널 크기 조절',exact:true}),preferenceKey='modu-vision:flow-inspector-layout:v1';await expect(separator).toBeVisible();
   const layoutBeforeNormalization=await storageState();await separator.focus();await separator.press('Home');
   const min=Number(await separator.getAttribute('aria-valuemin')),max=Number(await separator.getAttribute('aria-valuemax'));expect(min).toBe(260);expect(Number.isInteger(max)).toBe(true);expect(max).toBeGreaterThanOrEqual(min);expect(max).toBeLessThanOrEqual(560);await expect(separator).toHaveAttribute('aria-valuenow',String(min));
   await expect.poll(async()=>JSON.parse((await storageState())[preferenceKey]||'null')).toEqual({version:1,width:min,collapsed:false});const layoutBaseline=await storageState();expect(Object.keys(layoutBaseline)).toEqual(Object.keys(layoutBeforeNormalization));for(const key of Object.keys(layoutBaseline))if(key!==preferenceKey)expect(layoutBaseline[key]).toBe(layoutBeforeNormalization[key]);
  await settle();baselineAPI=await readAPI();baseline=treeState();expect(baseline.source).toEqual(beforeInitialization.source);expect(splitControlFile(draftFile,custody)).toEqual(draftPin);
  expect((baselineAPI['/api/flowchart/draft'] as any).body).toEqual(saved);expect((baselineAPI['/api/flowchart/pipeline/active-version'] as any).body).toEqual({version_id:null});expect((baselineAPI['/api/dataset/revisions'] as any).body).toEqual({active_revision:null,revisions:[]});expect((baselineAPI['/api/flowchart/pipelines?source_dataset_path='+encodeURIComponent(source)] as any).body).toEqual({pipelines:[],total:0});expect((baselineAPI['/api/flow-evaluations'] as any).body).toEqual({evaluations:[],total:0});
  const modelRoute='/api/flowchart/models/catalog?source_dataset_path='+encodeURIComponent(source);expect((baselineAPI[modelRoute] as any).body).toEqual({models:[],total:0});expect((baselineAPI['/api/flow-workspace/comparisons'] as any).body.comparisons).toEqual([]);
  reachedBaseline=true;
   const inspector=page.getByRole('complementary',{name:'플로우 속성 패널',exact:true});
   const input=page.getByRole('button',{name:'Connect to ROI original IMAGE IN',exact:true}),output=page.getByRole('button',{name:'Start connection from Input original IMAGE OUT',exact:true});
   await expect(input).toHaveAttribute('data-flow-port','roi-original:in:0');await expect(output).toHaveAttribute('data-flow-port','input-original:out:0');await expect(input).toBeEnabled();await expect(output).toBeEnabled();
   const pending=page.getByRole('button',{name:'연결 시작: Input original · 취소',exact:true});await expect(pending).toHaveCount(0);
   await input.focus();await input.press('Enter');const emptyPort=page.getByRole('alert').filter({hasText:'먼저 출발 노드의 출력 포트를 클릭하세요.'});await expect(emptyPort).toHaveCount(1);await expect(emptyPort).toHaveText('먼저 출발 노드의 출력 포트를 클릭하세요.닫기');await same();await emptyPort.getByRole('button',{name:'닫기',exact:true}).click();await expect(emptyPort).toHaveCount(0);await same();
   await output.focus();await output.press('Enter');await expect(pending).toBeVisible();await input.focus();await input.press('Enter');const duplicate=page.getByRole('alert').filter({hasText:'이미 연결된 노드입니다.'});await expect(duplicate).toHaveCount(1);await expect(duplicate).toHaveText('이미 연결된 노드입니다.닫기');await expect(pending).toBeVisible();await same();await duplicate.getByRole('button',{name:'닫기',exact:true}).click();await expect(duplicate).toHaveCount(0);await pending.click();await expect(pending).toHaveCount(0);await page.waitForTimeout(700);await same();
   const lastOutput=page.locator('[data-flow-node-id="output-original"] [data-flow-node-focus]');await lastOutput.focus();await lastOutput.press('Enter');await expect(lastOutput).toHaveAttribute('aria-label','Output original 노드 · 선택됨');await lastOutput.press('Delete');const lastOutputError=page.getByRole('alert').filter({hasText:'마지막 출력 노드는 삭제할 수 없습니다.'});await expect(lastOutputError).toHaveCount(1);await expect(lastOutputError).toHaveText('마지막 출력 노드는 삭제할 수 없습니다.닫기');await same();await lastOutputError.getByRole('button',{name:'닫기',exact:true}).click();await expect(lastOutputError).toHaveCount(0);await page.waitForTimeout(700);await same();
   // Actual keyboard boundary attempts change only the live app-local layout, restored exactly.
   await separator.focus();await separator.press('Home');await expect(separator).toHaveAttribute('aria-valuenow',String(min));await separator.press('ArrowRight');await expect(separator).toHaveAttribute('aria-valuenow',String(min));await separator.press('End');await expect(separator).toHaveAttribute('aria-valuenow',String(max));await separator.press('ArrowLeft');await expect(separator).toHaveAttribute('aria-valuenow',String(max));await separator.press('Home');await expect(separator).toHaveAttribute('aria-valuenow',String(min));await expect.poll(storageState).toEqual(layoutBaseline);await same();
   const inspection=page.locator('[data-flow-node-id="inspection-original"] [data-flow-node-focus]');await inspection.focus();await inspection.press('Enter');await expect(inspection).toHaveAttribute('aria-label','Inspection original 노드 · 선택됨');const model=inspector.getByRole('combobox',{name:'완료된 학습 모델',exact:true});await expect(model).toBeVisible();await expect(model).toHaveValue('');await expect(model.locator('option')).toHaveCount(1);await expect(model.locator('option')).toHaveText('모델 선택');await expect(model.locator('option')).toHaveAttribute('value','');await expect(inspector.getByText('현재 데이터와 작업이 검증된 완료 모델만 표시합니다.',{exact:true})).toBeVisible();await same();
   await page.getByRole('tab',{name:'일괄 평가',exact:true}).click();await expect(page.getByRole('tab',{name:'일괄 평가',exact:true})).toHaveAttribute('aria-selected','true');const comparisonPanel=page.locator('details').filter({has:page.locator('summary').filter({hasText:/^고정 테스트 세트 · 플로우 버전 A\/B 비교$/})});await expect(comparisonPanel).toHaveCount(1);if(await comparisonPanel.getAttribute('open')===null)await comparisonPanel.locator(':scope > summary').click();const history=comparisonPanel.getByRole('combobox',{name:'저장 비교 결과',exact:true});await expect(history).toBeVisible();await history.focus();await expect(history).toHaveValue('');await expect(history.locator('option')).toHaveCount(1);await expect(history.locator('option')).toHaveText('이전 비교 결과 다시 열기');await expect(history.locator('option')).toHaveAttribute('value','');await expect(comparisonPanel.getByRole('button',{name:'선택 대상에서 비교 실행·결과 저장',exact:true})).toBeDisabled();await expect(comparisonPanel.locator('summary').filter({hasText:/^고정 모델·입력 근거$/})).toHaveCount(0);await same();await page.getByRole('tab',{name:'편집',exact:true}).click();await expect(page.getByRole('tab',{name:'편집',exact:true})).toHaveAttribute('aria-selected','true');await same();
   // Shared fixed-ROI recipe mapping preflight only, not OBB execution, adoption, save or activation.
   const newFlow=page.locator('details').filter({has:page.locator('summary').filter({hasText:/^새 플로우 ▾$/})});await expect(newFlow).toHaveCount(1);if(await newFlow.getAttribute('open')===null)await newFlow.locator(':scope > summary').click();const recipeButton=newFlow.getByRole('button',{name:'고정 ROI 검사 · 원본 픽셀 좌표',exact:true});await expect(recipeButton).toBeEnabled();await recipeButton.click();const recipe=page.getByRole('dialog',{name:'레시피 미리보기·모델 매핑',exact:true});await expect(recipe).toBeVisible();await expect(recipe.getByRole('heading',{name:'고정 ROI 검사',exact:true})).toBeVisible();const mapping=recipe.getByRole('combobox',{name:'검사 모델 레시피 모델',exact:true});await expect(mapping).toBeEnabled();await expect(mapping).toHaveValue('');await expect(mapping.locator('option')).toHaveCount(1);await expect(mapping.locator('option')).toHaveText('이 작업의 완료 모델 없음');await expect(mapping.locator('option')).toHaveAttribute('value','');await expect(recipe.getByRole('combobox',{name:'검사 모델 클래스 적용 범위',exact:true})).toBeDisabled();const adopt=recipe.getByRole('button',{name:'매핑 확인·새 초안으로 채택',exact:true});await expect(adopt).toBeDisabled();await expect(adopt).toHaveAccessibleDescription('검사 모델: 완료 모델을 선택하세요.');await same();
   await recipe.getByRole('button',{name:'취소 · 현재 그래프 유지',exact:true}).click();await expect(recipe).toHaveCount(0);await page.waitForTimeout(700);await same();await recipeButton.click();await expect(recipe).toBeVisible();await expect(mapping).toHaveValue('');await expect(mapping.locator('option')).toHaveCount(1);await expect(adopt).toBeDisabled();await expect(adopt).toHaveAccessibleDescription('검사 모델: 완료 모델을 선택하세요.');await recipe.getByRole('button',{name:'취소 · 현재 그래프 유지',exact:true}).click();await expect(recipe).toHaveCount(0);await same();expect(await storageState()).toEqual(layoutBaseline);
  await e.screenshot(page,(native?'native':'browser')+'-F064-eight-existing-refusal-controls');
  const after=treeState();const initializationChanges=Object.fromEntries(Object.keys(roots).map(key=>[key,[...new Set([...Object.keys(beforeInitialization[key]),...Object.keys(baseline![key])])].sort().filter(name=>JSON.stringify(beforeInitialization[key][name])!==JSON.stringify(baseline![key][name])).map(name=>({relative:name,before:beforeInitialization[key][name]??null,after:baseline![key][name]??null}))]));
  proof={schema:'modu-vision.F064-existing-refusal-controls/v1',selected_pending_pointers:F064_POINTERS,cells:[{"pointer":"/records/63/actions/6/scenarios/empty","action":"F064.native-keyboard-typed-port-connection","dimension":"empty","graph_and_saved_draft_unchanged":true,"no_API_write":true,"actual_keyboard":"ROI input Enter without pending output","exact_error":"먼저 출발 노드의 출력 포트를 클릭하세요."},{"pointer":"/records/63/actions/6/scenarios/error","action":"F064.native-keyboard-typed-port-connection","dimension":"error","graph_and_saved_draft_unchanged":true,"no_API_write":true,"actual_keyboard":"Input output then existing ROI input","exact_error":"이미 연결된 노드입니다.","existing_edge":"input-roi-original"},{"pointer":"/records/63/actions/7/scenarios/invalid","action":"F064.native-keyboard-delete-undo-redo","dimension":"invalid","graph_and_saved_draft_unchanged":true,"no_API_write":true,"actual_keyboard":"single output selection then Delete","exact_error":"마지막 출력 노드는 삭제할 수 없습니다."},{"pointer":"/records/63/actions/2/scenarios/invalid","action":"F064.large-dag-inspector-layout","dimension":"invalid","graph_and_saved_draft_unchanged":true,"no_API_write":true,"actual_keyboard":"Home/ArrowRight and End/ArrowLeft beyond live bounds","app_local_only":true,"whole_normalized_preference_restored":true},{"pointer":"/records/63/actions/5/scenarios/empty","action":"F064.native-keyboard-inspection-add-select","dimension":"empty","graph_and_saved_draft_unchanged":true,"no_API_write":true,"actual_keyboard":"select ordinary null-model inspection","own_combobox":"완료된 학습 모델","catalog":{"models":[],"total":0},"option_text":"모델 선택"},{"pointer":"/records/63/actions/1/scenarios/empty","action":"F064.actual-frozen-flow-history-reopen","dimension":"empty","graph_and_saved_draft_unchanged":true,"no_API_write":true,"scope":"actual empty saved comparison selector/history only","own_combobox":"저장 비교 결과","comparisons":[],"option_text":"이전 비교 결과 다시 열기","frozen_execution_claim":false},{"pointer":"/records/63/actions/4/scenarios/empty","action":"F064.obb-native-fitted-roi-flow-save","dimension":"empty","graph_and_saved_draft_unchanged":true,"no_API_write":true,"scope":"shared fixed-ROI model mapping preflight only","option_text":"이 작업의 완료 모델 없음","exact_disabled_reason":"검사 모델: 완료 모델을 선택하세요.","OBB_fittedROI_execution_save_qualified":false},{"pointer":"/records/63/actions/4/scenarios/cancel","action":"F064.obb-native-fitted-roi-flow-save","dimension":"cancel","graph_and_saved_draft_unchanged":true,"no_API_write":true,"scope":"local mapping dialog before adoption","own_button":"취소 · 현재 그래프 유지","closed_and_reopened_mapping_empty":true,"inflight_save_server_cancel_OBB_claim":false}],
   layout_before_normalization:layoutBeforeNormalization,layout_baseline:layoutBaseline,layout_after:await storageState(),inspector_actual_bounds:{min,max},model_catalog:(baselineAPI[modelRoute] as any).body,comparison_history:(baselineAPI['/api/flow-workspace/comparisons'] as any).body,
   source_images:originals,project,draft_context:context,transport_context:transport,original_saved_draft:saved,original_draft_fullpin:{path:draftFile,raw9:draftPin.raw,sha256:draftPin.sha256,bytes:draftPin.bytes.length},roots,owned_before_initialization:beforeInitialization,exact_initialization_changes:initializationChanges,protected_before:baseline,protected_after:after,baseline_API:baselineAPI,after_API:await readAPI(),UI_SOURCE_expected:UI_SOURCE,DIALOG_SOURCE_expected:DIALOG_SOURCE,UI_SOURCE_count:27,DIALOG_SOURCE_count:3,UI_SOURCE_before:sourceBefore,UI_SOURCE_after:sourceState(),fixture_fullbody_clocks:fixtureCalls,post_baseline_writes:writes.filter(row=>row.post_baseline),setup_renderer_writes:writes.filter(row=>!row.post_baseline),
   original_read_contract:'Every original GET completes and reads its whole bytes within its original10s; only exact owning library409 and missing-model evaluation404 are negative; all others200 including actual JPEG decode',full_SQLite_WAL_SHM_and_directory_namespace_included:true,no_route_mock_store_delegate_fake_checkpoint_or_model_job:true,scope:{source_electron:native,browser:!native,installed_native:false,training:false,inference:false,GPU:false,quality_approval:false,Native_acceptance:false,parent_acceptance:false,registry_credit:false}};
  expect(proof.after_API).toEqual(baselineAPI);expect(writes.filter(row=>new URL(row.url).pathname==='/api/flowchart/run'||/\/(train|training|predict|evaluate|jobs)(\/|$)/.test(new URL(row.url).pathname))).toEqual([]);await same();completed=true;
 }catch(cause){failed=true;primary=cause;}
 finally{
  const attempt=async(label:string,work:()=>unknown|Promise<unknown>)=>{try{await work();}catch(cause){secondary.push({label,cause});if(!failed){failed=true;primary=cause;}}};
  await attempt('request admission detach',()=>{page.off('request',observe);removed=true;});
  await attempt('original admitted GET full closure',async()=>{await settle();expect(reads.every(row=>row.complete&&!row.failed)).toBe(true);});
  if(baseline!==undefined)await attempt('full final declared trees including SQLite WAL SHM',()=>expect(treeState()).toEqual(baseline));
  await attempt('all finite UI/source full SHA/raw9',()=>expect(sourceState()).toEqual(sourceBefore));
  await attempt('once-only owned FD closure',()=>{expect(custody.opened).toBe(custody.closed);expect(custody.uncertain).toEqual([]);});
  await attempt('closure evidence',()=>e.note('F064_existing_refusal_controls_closure',{first_exception:failed?String(primary):null,secondary:secondary.map(row=>String(row)),FD_ledger:custody,all_owned_FDs_closed:custody.opened===custody.closed&&custody.uncertain.length===0,request_observer_removed:removed,all_original_GETs_complete:reads.every(row=>row.complete&&!row.failed),original_reads:reads.map(({request,pending:_pending,failure,...row})=>({...row,url:request.url(),method:request.method(),failure:row.failed?String(failure):null})),success_assertions_completed:completed}));
 }
 if(failed)throw primary;expect(removed).toBe(true);expect(completed).toBe(true);expect(proof).toBeDefined();e.note('F064_existing_refusal_controls',proof);
}
test('F064 empty model history mapping and keyboard layout connection deletion refusals preserve saved originals',async({page,request,renderer,workspace,evidence})=>{
 test.setTimeout(160_000);await installDesktopHostShim(page,renderer.port);
 const api:FlowApi=async(route,body,method,headers)=>{const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),headers,...(body===undefined?{}:{data:body}),timeout:10_000});return {status:response.status(),body:await response.text(),url:response.url(),headers:response.headers()};};
 await flowRefusalExercise(page,workspace,evidence,api,false,renderer.origin,renderer.url);
});
test('native F064 empty model history mapping and keyboard layout connection deletion refusals preserve saved originals',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 test.setTimeout(160_000);const {window}=electronSession,backend=await electronSession.waitForBackend();
 const api:FlowApi=(route,body,method,headers)=>window.evaluate(async({port,route,body,method,headers})=>{const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),10_000);try{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),headers:{'Content-Type':'application/json',...headers},...(body===undefined?{}:{body:JSON.stringify(body)}),signal:abort.signal});return {status:response.status,body:await response.text(),url:response.url,headers:Object.fromEntries(response.headers.entries())};}finally{clearTimeout(timer);}},{port:backend.port,route,body,method,headers});
 await flowRefusalExercise(window,workspace,evidence,api,true,`http://127.0.0.1:${backend.port}`);
});
