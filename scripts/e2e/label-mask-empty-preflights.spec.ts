import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
test.use({actionTimeout:10_000});
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


// Standalone empty-state preflights. Only the folder chooser response is controlled.
// Original renderer/backend requests, state, source files and custody checks remain real.
const harness=require('./fixtures/harness.cjs');
const UI_SOURCE={
  "src/renderer/components/dataset/DatasetWorkflowPanel.tsx": "3c740fece10eb4515a236844020578d88cb202a106b6f93b36bc3ce07c386186",
  "src/renderer/components/dataset/ExternalMaskExchangePanel.tsx": "6a5d0fd5e8b57d05ae7e91b47ba447eac694a5f7c37509da9d2fbe8d7924f7a4",
  "backend/api/routes_dataset.py": "c70d2649ece6307f8cea87c334f7353a71ede385cbefc86290c56d4d990a2440",
  "backend/api/routes_mask_exchange.py": "f66d4f4273692bf3c46f038c25b56a60ad4b4db5ba3dee9d31c3bac6dcd9a7c5",
  "backend/engine/annotation_formats.py": "dd046ef5e66187577217fe7de19ec6cf0376db5abf30d1b845c5b36a66e9a90c",
  "backend/engine/mask_exchange.py": "e12e6e0f10e9af2267e2f511cc7526ee98f088f0d778ce8959b7e9d6b72fafeb",
  "src/renderer/services/datasetWorkflow.ts": "3c11c8f624b5dc972b0ee9eefba2279893e01b45eed7342988fe87546d3cf2fc",
  "backend/api/routes_dataset_metadata.py": "82c2c3da7b84604df14b65694cf7e4b418ede8a2483b61fa0576536b09de047d",
  "scripts/e2e/fixtures/desktop-host-shim.ts": "4f598862ec0e08189628c72bbb552f72bf8fd73f5a9a05b8da1d3ddf516dcfa6",
  "scripts/e2e/fixtures/harness.cjs": "6fe3bb6411a28bc83c6da36ba0b2480b12de6b86fd08daa61d8d0e76d7dac950",
  "scripts/e2e/fixtures/test.ts": "7a7effa184e71c02414773308e0ea88e16cf83dfbd72d77d47046f2cfc1b8d09",
  "scripts/e2e/qa/appFlow.ts": "4eed3e5dad1d9d65a6b19fc9bb51a1f9b0e23911a39e7f255220bec0ce37fea2",
  "src/renderer/services/hostAdapter.ts": "3e71fb8fdd9169fccd99391d1f5a95a06e22ea20cd1e065a2aadcbcbc0058261"
} as const;
const POINTERS=['/records/116/actions/1/scenarios/empty','/records/117/actions/0/scenarios/empty','/records/117/actions/1/scenarios/empty','/records/117/actions/2/scenarios/empty'] as const;
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

type Reply={status:number;body:string;url:string;headers:Record<string,string>};
type Api=(route:string,body?:unknown,method?:string,headers?:Record<string,string>)=>Promise<Reply>;
type Chooser={install:()=>Promise<void>;select:(directory:string|null)=>Promise<void>;calls:()=>Promise<any[]>;restore:()=>Promise<void>};
async function labelMaskEmpty(page:Page,w:Workspace,e:Evidence,api:Api,chooser:Chooser,native:boolean,origin:string,url?:string){
 const custody:SplitControlCustody={opened:0,closed:0,uncertain:[]};
 const sourceState=()=>Object.fromEntries(Object.entries(UI_SOURCE).map(([relative,expected])=>{const p=splitControlFile(path.join(harness.REPO_ROOT,relative),custody);expect(p.sha256,relative).toBe(expected);return[relative,{raw9:p.raw,sha256:p.sha256}];}));
 let sourceBefore:ReturnType<typeof sourceState>|undefined,baseline:Record<string,unknown>|undefined,protectedApi:any,transport:any,project:any,source='',bundle='',roots:Record<string,string>={};
 const fixtureCalls:any[]=[],rows:any[]=[],secondary:unknown[]=[],cells:any[]=[],contexts=new Map<string,unknown>();
 let installed=false,attached=false,removed=false,restored=false,completed=false,failed=false,primary:unknown;
 const treeState=()=>Object.fromEntries(Object.entries(roots).map(([name,root])=>[name,splitControlTree(root,custody)]));
 const value=async(route:string,body?:unknown,method?:string)=>{
  const started=performance.now(),deadline=started+10_000,headers:Record<string,string>=transport?{'x-vision-project':transport.project_id,'x-vision-context':JSON.stringify(transport)}:{};
  const reply=await splitControlWithin(api(route,body,method,headers),deadline,'actual fixture full response');expect(reply.status,route+': '+reply.body).toBe(200);expect(reply.url).toBe(origin+route);const data=JSON.parse(reply.body);
  const context=reply.headers['x-vision-context']===undefined?null:JSON.parse(reply.headers['x-vision-context']);if(transport&&context!==null)expect(context).toEqual(transport);
  expect(performance.now()).toBeLessThanOrEqual(deadline);fixtureCalls.push({route,method:method||(body===undefined?'GET':'POST'),body:body??null,status:200,response:data,raw_body:reply.body,full_body_sha256:hash(Buffer.from(reply.body)),request_context:transport??null,response_context:context,started,deadline,finished:performance.now()});return{data,reply};
 };
 let scopes:OCROwnedThumbScope[]=[];
 const observe=(request:SplitControlRequest)=>{
  const u=new URL(request.url());if(u.origin!==origin||!u.pathname.startsWith('/api/'))return;
  const started=performance.now(),row:any={request,ordinal:rows.length,method:request.method(),url:request.url(),started,deadline:started+10_000,complete:false,failed:false,pending:Promise.resolve()};rows.push(row);
  row.pending=(async()=>{try{
   expect(request.frame()).toBe(page.mainFrame());const response=await splitControlWithin(request.response(),row.deadline,'original admitted response');expect(response).not.toBeNull();const rh=request.headers(),sh=response!.headers();
   row.request_context=rh['x-vision-context']===undefined?null:JSON.parse(rh['x-vision-context']);row.response_context=sh['x-vision-context']===undefined?null:JSON.parse(sh['x-vision-context']);
   if(rh['x-vision-project']!==undefined)expect(rh['x-vision-project']).toBe(project.id);if(row.request_context!==null)expect(row.request_context).toEqual(transport);if(row.response_context!==null)expect(row.response_context).toEqual(transport);
   if(u.searchParams.has('file_path'))expect(scopes[0].images.filter(image=>path.join(source,image.image)===u.searchParams.get('file_path'))).toHaveLength(1);
   if(u.searchParams.has('folder_path'))expect(u.searchParams.getAll('folder_path')).toEqual([source]);
   if(request.method()==='GET'){
    expect(request.postData()).toBeNull();
    if(u.pathname.startsWith('/api/dataset/thumbnail/')){const full=await ocrOwnedThumbnail(page,response!,request,row.deadline,origin,scopes,contexts,custody);row.status=full.status;row.body=full.body;row.full_body_sha256=full.sha256;}
    else{const full=await splitControlBody(response!,row.deadline);row.status=full.status;row.body=full.body;row.full_body_sha256=full.sha256;}
   }else{
    expect(request.method()).toBe('POST');expect(u.pathname).toBe('/api/dataset/masks/import');expect(u.search).toBe('');expect(rh['x-vision-project']).toBe(project.id);expect(row.request_context).toEqual(transport);expect(row.response_context).toEqual(transport);
    row.request_body=request.postDataJSON();expect(row.request_body).toEqual({import_dir:bundle,mode:'preview',conflict_policy:'reject',actor:'owned-empty-preflight',expected_revisions:{}});
    expect(response!.status()).toBe(422);const bytes=await splitControlWithin(response!.body(),row.deadline,'exact empty-mask error whole body');expect(await splitControlWithin(response!.finished(),row.deadline,'empty-mask HTTP completion')).toBeNull();row.status=422;row.body=JSON.parse(bytes.toString('utf8'));row.full_body_sha256=hash(bytes);expect(row.body).toEqual({detail:'Mask manifest contains no source images'});
   }
   row.finished=performance.now();expect(row.finished).toBeLessThanOrEqual(row.deadline);row.complete=true;
  }catch(cause){row.failed=true;row.failure=cause;}})();
 };
 const settle=async()=>{
  const admitted=[...rows];let drainFailed=false,firstDrain:unknown;
  for(let ordinal=0;ordinal<admitted.length;ordinal++){const row=admitted[ordinal];try{await row.pending;if(row.failed)throw row.failure;expect(row.complete).toBe(true);expect(row.deadline).toBe(row.started+10_000);expect(row.finished).toBeLessThanOrEqual(row.deadline);}catch(cause){if(!drainFailed){drainFailed=true;firstDrain=cause;}else secondary.push({label:'later admitted original request',ordinal,cause});}}
  if(drainFailed)throw firstDrain;
 };
 const apiState=async()=>({team:(await value('/api/team-data')).data,readiness:(await value('/api/team-data/readiness')).data,metadata:(await value('/api/dataset/metadata?limit=10')).data,versions:(await value('/api/dataset/versions')).data,labels:await Promise.all(scopes[0].images.map(image=>value('/api/annotations/'+path.basename(image.image,'.png')+'?file_path='+encodeURIComponent(path.join(source,image.image))).then(x=>x.data)))});
 const same=async()=>{await settle();expect(await apiState()).toEqual(protectedApi);await settle();expect(treeState()).toEqual(baseline);expect(sourceState()).toEqual(sourceBefore);};
 try{
  sourceBefore=sourceState();source=path.join(w.root,'owned-label-mask-empty-source');bundle=path.join(w.root,'owned-empty-mask-manifest');fs.mkdirSync(source);fs.mkdirSync(bundle);
  const images=[100,120].map((blue,i)=>{const image='part-'+i+'.png',file=path.join(source,image),bytes=png(256,3,(x,y)=>[x,y,blue]);fs.writeFileSync(file,bytes,{flag:'wx'});const p=splitControlFile(file,custody);expect(p.bytes).toEqual(bytes);expect([p.bytes.readUInt32BE(16),p.bytes.readUInt32BE(20)]).toEqual([256,256]);return{image,sha256:p.sha256,raw9:p.raw};});
  const manifest={schema_version:1,classes:[{id:0,name:'background',color:'#000000'}],images:[]};fs.writeFileSync(path.join(bundle,'mask_manifest.json'),JSON.stringify(manifest),{flag:'wx'});
  const created=(await value('/api/project/create',{name:'Owned label mask empty preflights',task:'segmentation'})).data;await value('/api/project/update',{source_dataset_dir:source},'PUT');await value('/api/dataset/import',{folder_path:source,task:'segmentation'});
  const current=await value('/api/project/current');project=current.data;expect(project.id).toBe(created.id);expect(project.source_dataset_dir).toBe(source);expect(project.task).toBe('segmentation');expect(path.dirname(project.project_dir)).toBe(native?path.join(w.userData,'projects'):w.projects);expect(fs.realpathSync(project.project_dir)).toBe(project.project_dir);
  expect(current.reply.headers['x-vision-context']).toBeDefined();transport=JSON.parse(current.reply.headers['x-vision-context']);expect(Object.keys(transport).sort()).toEqual(['actor_id','mode','project_id','workspace_id']);expect(transport.project_id).toBe(project.id);expect(transport.mode).toBe('local');for(const key of ['actor_id','workspace_id'])expect(transport[key]).toMatch(/^[A-Za-z0-9_.:-]{1,128}$/);contexts.set(project.id,transport);scopes=[{source,images,project}];
  await apiState();page.on('request',observe);attached=true;if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();
  const panel=page.getByRole('region',{name:'데이터 검토와 라벨 교환',exact:true}),toggle=page.getByRole('button',{name:'이미지 검토·그룹 분할·라벨 교환',exact:true});if(await toggle.getAttribute('aria-expanded')==='false')await toggle.click();await expect(panel).toContainText('검색 결과 2개');
  const labelSummary=panel.getByText('LabelMe · COCO · YOLO 라벨 가져오기/내보내기',{exact:true}),labels=labelSummary.locator('..'),masks=panel.getByLabel('외부 multiclass mask 교환',{exact:true});expect(await labels.evaluate(node=>node.tagName)).toBe('DETAILS');if(await labels.getAttribute('open')===null)await labels.locator(':scope > summary').click();if(await masks.getAttribute('open')===null)await masks.locator(':scope > summary').click();
  await panel.getByLabel('데이터 작업자 이름',{exact:true}).fill('owned-empty-preflight');await chooser.install();installed=true;await settle();protectedApi=await apiState();expect(protectedApi.metadata.items).toHaveLength(2);expect(protectedApi.labels.every((row:any)=>row.annotations.length===0)).toBe(true);await settle();roots={project:project.project_dir,source,empty_bundle:bundle,harness_source:w.dataset};baseline=treeState();expect(sourceState()).toEqual(sourceBefore);
  const imports=()=>rows.filter(row=>row.method!=='GET');const labelFolder=labels.getByRole('button',{name:'라벨 폴더 선택',exact:true}),maskFolder=masks.getByRole('button',{name:'mask 폴더 선택',exact:true}),preview=masks.getByRole('button',{name:'mask 미리보기',exact:true}),apply=masks.getByRole('button',{name:'검토한 mask 라벨 적용',exact:true});
  await expect(labelFolder).toBeEnabled();await expect(labels.getByText('폴더를 선택하세요',{exact:true})).toBeVisible();await expect(labels.getByRole('button',{name:'가져오기 미리보기',exact:true})).toBeDisabled();await expect(labels.getByRole('button',{name:'검토한 라벨 적용',exact:true})).toBeDisabled();await expect(labels.locator('ul')).toHaveCount(0);
  await chooser.select(null);await labelFolder.click();await expect.poll(async()=>(await chooser.calls()).length).toBe(1);await expect(labelFolder).toBeEnabled();await expect(labels.getByText('폴더를 선택하세요',{exact:true})).toBeVisible();await expect(labels.getByRole('button',{name:'가져오기 미리보기',exact:true})).toBeDisabled();expect(imports()).toHaveLength(0);await same();cells.push({pointer:POINTERS[0],owning_action:'라벨 폴더 선택',empty_chooser_response_controlled:true,initial_and_after_empty_selection_directory_absent:true,preview_disabled:true,selection_POSTs:0});
  await expect(maskFolder).toBeEnabled();await expect(masks.getByText('mask_manifest.json 필요',{exact:true})).toBeVisible();await expect(preview).toBeDisabled();await expect(apply).toBeDisabled();await expect(masks.locator('article')).toHaveCount(0);await maskFolder.click();await expect.poll(async()=>(await chooser.calls()).length).toBe(2);await expect(maskFolder).toBeEnabled();await expect(masks.getByText('mask_manifest.json 필요',{exact:true})).toBeVisible();expect(imports()).toHaveLength(0);await same();cells.push({pointer:POINTERS[1],owning_action:'mask 폴더 선택',empty_chooser_response_controlled:true,directory_and_preview_absent:true,selection_POSTs:0});
  await chooser.select(bundle);await maskFolder.click();await expect.poll(async()=>(await chooser.calls()).length).toBe(3);await expect(maskFolder).toBeEnabled();await expect(masks.getByText(bundle,{exact:true})).toBeVisible();await expect(preview).toBeEnabled();await expect(apply).toBeDisabled();expect(imports()).toHaveLength(0);
  await preview.click();await expect(masks.getByRole('alert')).toHaveCount(1);await expect(masks.getByRole('alert')).toHaveText('Mask manifest contains no source images');await expect(preview).toBeEnabled();await expect(masks.locator('article')).toHaveCount(0);await expect(apply).toBeDisabled();await settle();expect(imports()).toHaveLength(1);expect(imports()[0].body).toEqual({detail:'Mask manifest contains no source images'});expect(imports()[0].status).toBe(422);expect(JSON.parse(splitControlFile(path.join(bundle,'mask_manifest.json'),custody).bytes.toString('utf8'))).toEqual(manifest);await same();
  cells.push({pointer:POINTERS[2],owning_action:'mask 미리보기',empty_manifest:manifest,actual_original_backend_status:422,exact_detail:'Mask manifest contains no source images',zero_preview_rows:true,no_generic_error_credit:true});
  await expect(apply).toHaveCount(1);await expect(apply).toBeDisabled();expect(imports().every(row=>row.request_body.mode==='preview')).toBe(true);await same();cells.push({pointer:POINTERS[3],owning_action:'검토한 mask 라벨 적용',initial_and_refused_empty_preview_rows:0,actual_disabled_apply:true,apply_POSTs:0,no_force_click:true});
  expect(cells.map(row=>row.pointer)).toEqual([...POINTERS]);expect(await chooser.calls()).toHaveLength(3);await e.screenshot(page,(native?'native':'browser')+'-label-mask-four-empty-preflights');completed=true;
  e.note('label_mask_empty_preflights',{schema:'modu-vision.label-mask-empty-preflights/v1',selected_pending_pointers:POINTERS,cells,project,transport,source_images:images,manifest,chooser_calls:await chooser.calls(),before:baseline,after:treeState(),protected_API_before:protectedApi,protected_API_after:await apiState(),UI_SOURCE_expected:UI_SOURCE,UI_SOURCE_before:sourceBefore,UI_SOURCE_after:sourceState(),fixture_full_body_calls:fixtureCalls,scope:{source_electron:native,browser:!native,folder_dialog_response_controlled:true,human_OS_picker:false,API_response_mocked:false,folder_selection_invalid:false,apply:false,export:false,annotation_or_metadata_write:false,training:false,inference:false,GPU:false,quality_approval:false,Native_acceptance:false,parent_acceptance:false,registry_credit:false}});
 }catch(cause){failed=true;primary=cause;}
 finally{
  const attempt=async(label:string,work:()=>unknown|Promise<unknown>)=>{try{await work();}catch(cause){secondary.push({label,cause});if(!failed){failed=true;primary=cause;}}};
  await attempt('detach request admission',()=>{if(attached){page.off('request',observe);attached=false;}removed=true;});
  await attempt('all admitted original bodies closed',async()=>{await settle();expect(rows.every(row=>row.complete&&!row.failed)).toBe(true);});
  await attempt('restore only owned chooser function',async()=>{if(installed){await chooser.restore();installed=false;}restored=true;});
  if(baseline!==undefined)await attempt('complete original trees including SQLite WAL SHM',()=>expect(treeState()).toEqual(baseline));
  if(sourceBefore!==undefined)await attempt('original full source raw9/SHA',()=>expect(sourceState()).toEqual(sourceBefore));
  await attempt('all owned file FDs once closed',()=>{expect(custody.opened).toBe(custody.closed);expect(custody.uncertain).toEqual([]);});
  await attempt('closure evidence',()=>e.note('label_mask_empty_preflights_closure',{first_exception:failed?String(primary):null,secondary:secondary.map(row=>String(row)),FD_ledger:custody,all_owned_FDs_closed:custody.opened===custody.closed&&custody.uncertain.length===0,request_observer_removed:removed,owned_chooser_restored:restored,all_original_requests_complete:rows.every(row=>row.complete&&!row.failed),requests:rows.map(({request,pending,failure,...row})=>({...row,failure:row.failed?String(failure):null})),success_assertions_completed:completed}));
 }
 if(failed)throw primary;expect(removed).toBe(true);expect(restored).toBe(true);expect(completed).toBe(true);
}
test('label and mask empty chooser preview and apply preflights preserve originals',async({page,request,renderer,workspace,evidence})=>{
 test.setTimeout(160_000);await installDesktopHostShim(page,renderer.port);
 const chooser:Chooser={install:()=>page.evaluate(()=>{const t=window as any;if(t.__labelMaskEmptyOriginal!==undefined)throw Error('Owned chooser marker already exists');t.__labelMaskEmptyOriginal=t.api.selectFolder;t.__labelMaskEmptyCalls=[];t.__labelMaskEmptyValue=null;t.__labelMaskEmptyOwned=async(options:any)=>{t.__labelMaskEmptyCalls.push({options,result:t.__labelMaskEmptyValue,empty:t.__labelMaskEmptyValue===null});return t.__labelMaskEmptyValue;};t.api.selectFolder=t.__labelMaskEmptyOwned;}),select:async directory=>{await page.evaluate(value=>(window as any).__labelMaskEmptyValue=value,directory);},calls:()=>page.evaluate(()=>(window as any).__labelMaskEmptyCalls),restore:()=>page.evaluate(()=>{const t=window as any;if(t.api.selectFolder!==t.__labelMaskEmptyOwned)throw Error('Chooser ownership changed');t.api.selectFolder=t.__labelMaskEmptyOriginal;for(const key of ['__labelMaskEmptyOriginal','__labelMaskEmptyCalls','__labelMaskEmptyValue','__labelMaskEmptyOwned'])delete t[key];})};
 const api:Api=async(route,body,method,headers)=>{const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),headers,...(body===undefined?{}:{data:body}),timeout:10_000});return{status:response.status(),body:await response.text(),url:response.url(),headers:response.headers()};};
 await labelMaskEmpty(page,workspace,evidence,api,chooser,false,renderer.origin,renderer.url);
});
test('native label and mask empty chooser preview and apply preflights preserve originals',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 test.setTimeout(160_000);const page=electronSession.window,app=electronSession.app,backend=await electronSession.waitForBackend();
 const chooser:Chooser={install:()=>app.evaluate(({dialog})=>{const t=globalThis as any;if(t.__labelMaskEmptyOriginal!==undefined)throw Error('Owned chooser marker already exists');t.__labelMaskEmptyOriginal=dialog.showOpenDialog;t.__labelMaskEmptyCalls=[];t.__labelMaskEmptyValue=null;t.__labelMaskEmptyOwned=async(...args:any[])=>{const result={canceled:false,filePaths:t.__labelMaskEmptyValue===null?[]:[t.__labelMaskEmptyValue]};t.__labelMaskEmptyCalls.push({options:args[args.length-1],result,empty:result.filePaths.length===0});return result;};dialog.showOpenDialog=t.__labelMaskEmptyOwned;}),select:async directory=>{await app.evaluate((_,value)=>(globalThis as any).__labelMaskEmptyValue=value,directory);},calls:()=>app.evaluate(()=>(globalThis as any).__labelMaskEmptyCalls),restore:()=>app.evaluate(({dialog})=>{const t=globalThis as any;if(dialog.showOpenDialog!==t.__labelMaskEmptyOwned)throw Error('Chooser ownership changed');dialog.showOpenDialog=t.__labelMaskEmptyOriginal;for(const key of ['__labelMaskEmptyOriginal','__labelMaskEmptyCalls','__labelMaskEmptyValue','__labelMaskEmptyOwned'])delete t[key];})};
 const api:Api=(route,body,method,headers)=>page.evaluate(async({port,route,body,method,headers})=>{const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),10_000);try{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),headers:{'Content-Type':'application/json',...headers},...(body===undefined?{}:{body:JSON.stringify(body)}),signal:abort.signal});return{status:response.status,body:await response.text(),url:response.url,headers:Object.fromEntries(response.headers.entries())};}finally{clearTimeout(timer);}},{port:backend.port,route,body,method,headers});
 await labelMaskEmpty(page,workspace,evidence,api,chooser,true,`http://127.0.0.1:${backend.port}`);
});
