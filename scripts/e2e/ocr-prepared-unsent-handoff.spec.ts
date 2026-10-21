import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
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

// F030 unsent alphabet draft and saved alphabet/split handoff, exactly three pending cells.
// Actual owning controls; no model jobs, in-flight cancellation or fake checkpoints.
const harness=require('./fixtures/harness.cjs');
const UI_SOURCE={
 "src/renderer/components/training/OCRWorkbench.tsx": "769a2316ba1770bccd1c6c78987d99ee779ddf64d82f4c080ad5280924e51125",
 "backend/api/routes_ocr.py": "f3a3c76fdf6789e13239c4f980fdd677aaa811e94e55c4151165b2e7f99fcc29",
 "src/renderer/services/api.ts": "8e9a6005af48cfa385463cef4521cffcbfa5096172ba14ad0cffc9ab6746357e",
 "backend/engine/prepared_family_datasets.py": "656b31e84535da564ea3325b5d88f95414951ec7bf30c58970c72c9645b0f53d",
 "src/renderer/components/training/ProjectImagePicker.tsx": "96d972f0a2afc0791b0c7b68365c36ce65c4c6527e29af509a12cd88e73b9522",
 "src/renderer/components/training/TrainingPreparationPanel.tsx": "2b8fd81f3188d4fcda2add4e1bb32b2c31a681c4b9c7257193c00823ee968262",
 "src/renderer/components/training/useSpecializedTraining.ts": "d0291891ab24c05a5078429c909be256565dd88e49424e9f736c819101f7c25e",
 "src/renderer/components/training/TrainingRuntimeSettings.tsx": "d9f6ad8b79c0a6639521ab4d2b9f3efc76a3d5962222666e8a8f03da86009080",
 "src/renderer/stores/useProjectStore.ts": "e8b9619b256664d7a4dfb53ae137865adea3bb62a5dd6c0e7becca2ea8e78647",
 "src/renderer/components/training/TeamTrainingReadiness.tsx": "f835409d64acd7972b6253b94c280793e1bc47b00decc6b47d8fcd498c1ea984",
 "src/renderer/components/training/WarmStartSelector.tsx": "e5817228ee17f5b4e2e07f9f8fb43293f636a2cb51a51dbfc15720c16665007f",
 "backend/engine/ocr.py": "1e42167362e251c567f0e5c66a7a678f1907f98df46302fcfa0a638c8a803fd1",
 "backend/api/routes_dataset.py": "2a3732363608c05610811f76a21ef584710ae085402f3d8b748d6877431350c3",
 "src/renderer/components/training/modelAdapterRecipes.ts": "b5423202cb00e407e477053c7f081ea072754e27e028f97e029fb8fb04dd298b",
 "src/renderer/components/training/ProgramWorkbenchControls.tsx": "f982a95aefdd2b5360bedefbe4703e80bdd81d18e3a24e5cbcbc9297a2b95a51"
} as const;
type OCREmptyWire={request:SplitControlRequest;deadline:number;complete:boolean;status?:number;sha256?:string;body?:unknown;request_context?:unknown;response_context?:unknown;expected_refusal?:string;failure?:string;retired_gallery_read?:boolean;retired?:OCRRetiredThumbnail};
type OCRRetiredThumbnail={failure:string;headers_status:number|null;owner_project_id:string;file_path:string;resource_type:string;request_context:unknown;response_context:unknown};
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
// Step 1's gallery retires its own in-flight thumbnail reads when the page leaves the step or the project
// (ScopedDatasetThumbnail aborts them, 6dd4c1f), before or after their headers. After the headers the browser keeps no
// body, so response.body() fails ("No data found for resource") or finished() never settles. The browser's own end
// event decides: both listeners are attached before the request observer, so no end can be missed. Only an aborted
// main-frame fetch GET of one of this spec's own originals is set aside, with its URL, owner, contexts and any headers
// checked; every thumbnail that finishes, and every other GET, keeps its full status, body and completion checks.
type OCRThumbnailEnd='failed'|'finished';
function ocrThumbnailEnds(page:Page,origin:string){
 const ends=new Map<SplitControlRequest,{ended:Promise<OCRThumbnailEnd>;resolve:(kind:OCRThumbnailEnd)=>void}>();
 const end=(kind:OCRThumbnailEnd)=>(request:SplitControlRequest)=>{ends.get(request)?.resolve(kind);};const onFailed=end('failed'),onFinished=end('finished');
 return {
  attach:()=>{page.on('requestfailed',onFailed);page.on('requestfinished',onFinished);},
  detach:()=>{page.off('requestfailed',onFailed);page.off('requestfinished',onFinished);},
  track:(request:SplitControlRequest)=>{const u=new URL(request.url());if(request.method()!=='GET'||u.origin!==origin||!u.pathname.startsWith('/api/dataset/thumbnail/')||request.frame()!==page.mainFrame()||request.resourceType()!=='fetch')return;
   let resolve:(kind:OCRThumbnailEnd)=>void=()=>{};const ended=new Promise<OCRThumbnailEnd>(done=>{resolve=done;});ends.set(request,{ended,resolve});},
  ended:(request:SplitControlRequest)=>ends.get(request)?.ended,
 };
}
async function ocrRetiredThumbnail(page:Page,request:SplitControlRequest,deadline:number,origin:string,scopes:OCROwnedThumbScope[],contexts:Map<string,unknown>):Promise<OCRRetiredThumbnail>{
 const u=new URL(request.url());expect(request.method()).toBe('GET');expect(u.origin).toBe(origin);expect(request.frame()===page.mainFrame()).toBe(true);expect(request.resourceType()).toBe('fetch');expect(Array.from(u.searchParams.keys()).sort()).toEqual(['file_path']);
 const matches=scopes.flatMap(scope=>scope.images.filter(image=>path.join(scope.source,image.image)===u.searchParams.get('file_path')).map(image=>({scope,image})));expect(matches).toHaveLength(1);const {scope,image}=matches[0],file=path.join(scope.source,image.image);
 expect(u.pathname).toBe('/api/dataset/thumbnail/'+path.basename(image.image));expect(Object.fromEntries(u.searchParams.entries())).toEqual({file_path:file});expect(contexts.has(scope.project.id)).toBe(true);
 const rh=request.headers(),requestContext=rh['x-vision-context']===undefined?null:JSON.parse(rh['x-vision-context']);if(rh['x-vision-project']!==undefined)expect(rh['x-vision-project']).toBe(scope.project.id);if(requestContext!==null)expect(requestContext).toEqual(contexts.get(scope.project.id));
 const failure=request.failure()?.errorText??'';expect(failure).toMatch(/ERR_ABORTED|aborted|cancel/i);
 // Headers that arrived before the abort are still the owner's: status, media type and response context are checked.
 const headers=await splitControlWithin(request.response(),deadline,'retired owning thumbnail headers');let responseContext:unknown=null;
 if(headers!==null){expect([200,304]).toContain(headers.status());const sh=headers.headers();if(headers.status()===200)expect(sh['content-type']).toBe('image/jpeg');expect(sh['x-vision-context']).toBeDefined();responseContext=JSON.parse(sh['x-vision-context']);expect(responseContext).toEqual(contexts.get(scope.project.id));}
 return {failure,headers_status:headers===null?null:headers.status(),owner_project_id:scope.project.id,file_path:file,resource_type:request.resourceType(),request_context:requestContext,response_context:responseContext};
}
// Prepared truth ownership; abandon only an unsent draft, then inspect saved A/B/A handoff.
async function ocrPreparedUnsentHandoffExercise(page:Page,w:Workspace,e:Evidence,api:OwnedApi,native:boolean,origin:string,url?:string){
 const custody:SplitControlCustody={opened:0,closed:0,uncertain:[]};
 const sourceState=()=>Object.fromEntries(Object.entries(UI_SOURCE).map(([relative,expected])=>{const file=path.join(harness.REPO_ROOT,relative),pin=splitControlFile(file,custody);expect(pin.sha256).toBe(expected);return [relative,{path:file,raw:pin.raw,sha256:pin.sha256}];}));const sourceBefore=sourceState();
 const make=async(tag:'A'|'B')=>{
  const source=path.join(w.root,'ocr-prepared-unsent-handoff-source-'+tag),images:Array<{image:string;sha256:string}>=[],rows:Array<{image:string;text:string;split:'train'|'val'|'test'}>=[],sourceHashes:Record<string,string>={};
  for(let i=0;i<12;i++){const image=`batch-${i}/part.png`,file=path.join(source,image),bytes=png(256,3,(x,y)=>[x,y,35+i*13]);fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,bytes,{flag:'wx'});const pin=splitControlFile(file,custody);expect(pin.bytes).toEqual(bytes);images.push({image,sha256:pin.sha256});sourceHashes[image]=pin.sha256;rows.push({image,text:tag==='A'?'AB12':'BA12',split:i<8?'train':i<10?'val':'test'});}
  const sourceOriginal=splitControlTree(source,custody),created=await splitControlWithin(api('/api/project/create',{name:'Owned OCR prepared truth handoff '+tag,task:'classification'}),performance.now()+10_000,'own prepared-fixture project creation');
  await splitControlWithin(api('/api/project/update',{source_dataset_dir:source},'PUT'),performance.now()+10_000,'own prepared-fixture source setup');await splitControlWithin(api('/api/dataset/import',{folder_path:source,task:'classification'}),performance.now()+10_000,'own prepared-fixture image import');
  const project=await splitControlWithin(api('/api/project/current'),performance.now()+10_000,'own prepared-fixture current project');expect(project.id).toBe(created.id);expect(project.source_dataset_dir).toBe(source);expect(path.dirname(project.project_dir)).toBe(native?path.join(w.userData,'projects'):w.projects);
  const prepared=await splitControlWithin(api('/api/ocr/prepare',{source_dataset_path:source,samples:rows}),performance.now()+10_000,'fixture-only twelve-row OCR preparation');expect(prepared.sample_count).toBe(12);expect(prepared.samples.map(({image,text,split}:any)=>({image,text,split}))).toEqual(rows);expect(prepared.alphabet).toBe('12AB');expect(path.dirname(prepared.dataset_path)).toBe(path.join(project.dataset_dir,'ocr'));expect(path.basename(prepared.dataset_path)).toMatch(/^[0-9a-f]{32}$/);
  const manifest=path.join(prepared.dataset_path,'ocr.json'),manifestPin=splitControlFile(manifest,custody),manifestBody=JSON.parse(manifestPin.bytes.toString('utf8')),sourceMap=Object.fromEntries(rows.map(row=>[row.image,{source_relative_path:row.image,source_sha256:sourceHashes[row.image]}])),preparedTree=splitControlTree(prepared.dataset_path,custody);
  expect(manifestBody).toEqual({version:1,samples:rows.map(row=>({...row,source_sha256:sourceHashes[row.image]})),source_dataset_path:source,source_map:sourceMap});expect(prepared.provenance.split_counts).toEqual({train:8,val:2,test:2});expect(prepared.provenance.source_image_count).toBe(12);expect(prepared.provenance.source_sha256).toEqual(sourceHashes);expect(prepared.provenance.manifest_sha256).toBe(manifestPin.sha256);expect(prepared.provenance.source_dataset_path).toBe(source);expect(prepared.provenance.source_map).toEqual(sourceMap);
  for(const row of rows){expect((preparedTree[row.image] as {sha256:string}).sha256).toBe(sourceHashes[row.image]);expect(prepared.samples.find((item:any)=>item.image===row.image).source_sha256).toBe(sourceHashes[row.image]);}
  const digest=crypto.createHash('sha256').update(Buffer.from('ocr-dataset-v1\0')).update(manifestPin.bytes);for(const row of [...rows].sort((a,b)=>a.image<b.image?-1:a.image>b.image?1:0))digest.update(Buffer.from('\0'+row.image+'\0'+sourceHashes[row.image]));expect(prepared.provenance.dataset_sha256).toBe('sha256:'+digest.digest('hex'));expect(splitControlTree(source,custody)).toEqual(sourceOriginal);expect(sourceOriginal).not.toHaveProperty('ocr.json');
  return {tag,source,images,rows,sourceHashes,project,prepared,manifest,manifestPin,manifestBody,preparedTree,sourceOriginal,TSV:rows.map(row=>`${row.image}\t${row.text}\t${row.split}`).join('\n')};
 };
 const A=await make('A'),B=await make('B');
 const ownedContexts=new Map<string,unknown>();
 const thumbnailEnds=ocrThumbnailEnds(page,origin);
 const reads:OCREmptyWire[]=[],writes:Array<{method:string;path:string;body:string|null}>=[],modelRequests:Array<{method:string;path:string;body:string|null}>=[];
 const observe=(request:SplitControlRequest)=>{const u=new URL(request.url());if(u.origin!==origin||!u.pathname.startsWith('/api/'))return;
  if(request.method()==='GET'){reads.push({request,deadline:performance.now()+10_000,complete:false});thumbnailEnds.track(request);}
  else if(!['HEAD','OPTIONS'].includes(request.method())){const row={method:request.method(),path:u.pathname,body:request.postData()};writes.push(row);if(u.pathname.startsWith('/api/ocr/')||/\/(train|training|jobs|evaluate|predict)(\/|$)/.test(u.pathname))modelRequests.push(row);}
 };
 const settleOriginalReads=async()=>{for(const row of reads){if(row.complete)continue;try{
  // Step 1's gallery aborts its own thumbnail reads when the page leaves the step (6dd4c1f), before or after the
  // headers: the browser's end event decides, and the retired read is checked and recorded, not credited.
  const ended=thumbnailEnds.ended(row.request);
  if(ended!==undefined&&await splitControlWithin(ended,row.deadline,'original owned thumbnail end event')==='failed'){row.retired=await ocrRetiredThumbnail(page,row.request,row.deadline,origin,[A,B],ownedContexts);row.retired_gallery_read=true;row.complete=true;continue;}
  const response=await splitControlWithin(row.request.response(),row.deadline,'original owned empty API GET');
  expect(response).not.toBeNull();
  const u=new URL(row.request.url()),rh=row.request.headers(),sh=response!.headers();
  row.request_context=rh['x-vision-context']===undefined?null:JSON.parse(rh['x-vision-context']);row.response_context=sh['x-vision-context']===undefined?null:JSON.parse(sh['x-vision-context']);
  if(u.pathname.startsWith('/api/dataset/thumbnail/')){
   const full=await ocrOwnedThumbnail(page,response!,row.request,row.deadline,origin,[A,B],ownedContexts,custody);row.status=full.status;row.sha256=full.sha256;row.body=full.body;
  }else{
   const full=await splitControlBody(response!,row.deadline);row.status=full.status;row.sha256=full.sha256;row.body=full.body;if(row.request_context!==null&&row.response_context!==null)expect(row.response_context).toEqual(row.request_context);
  }
  row.complete=true;
 }catch(cause){row.failure=String(cause);throw cause;}}};
 const treeState=()=>({A:{source:splitControlTree(A.source,custody),project:splitControlTree(A.project.project_dir,custody)},B:{source:splitControlTree(B.source,custody),project:splitControlTree(B.project.project_dir,custody)}});
 const ocr=page.locator('details').filter({has:page.locator('summary').filter({hasText:'문자 인식 모델 실험'})}).first();
 const expand=async()=>{const details=ocr.locator('details').filter({has:page.locator('summary').filter({hasText:'정답 표·가져오기 상세 설정'})});if(await details.getAttribute('open')===null)await details.locator('summary').click();};
 const table=ocr.getByRole('textbox',{name:/^정답 표 · 한 줄에/}),dataset=ocr.getByPlaceholder('/path/to/text-crops',{exact:true}),save=ocr.getByRole('button',{name:'정답과 이미지 해시 저장',exact:true}),train=ocr.getByRole('button',{name:'OCR 후보 학습',exact:true}),evaluate=ocr.getByRole('button',{name:'시험 분할 평가',exact:true});
 const flow=ocr.getByRole('button',{name:'검사 플로우·배포 패키지',exact:true});
 const completed=ocr.locator('label').filter({hasText:'완료 후보 모델'}).getByRole('combobox'),mode=ocr.getByRole('combobox',{name:'OCR 인식 방식',exact:true}),picker=ocr.getByRole('combobox',{name:'문자 원본 이미지 선택',exact:true});
 const assertPrepared=async(scope:typeof A)=>{
  expect(splitControlTree(scope.source,custody)).not.toHaveProperty('ocr.json');await expect(table).toHaveValue('');await expect(dataset).toHaveValue(scope.prepared.dataset_path);await expect(save).toBeDisabled();await expect(train).toBeEnabled();await expect(evaluate).toBeDisabled();await expect(flow).toBeDisabled();
  await expect(completed).toHaveCount(1);await expect(completed).toHaveValue('');await expect(completed.locator('option')).toHaveCount(1);await expect(completed.locator('option')).toHaveText('완료 모델 없음');expect(await completed.locator('option').getAttribute('value')).toBe('');await expect(mode).toHaveValue('crop');await expect(ocr.getByText('검증된 정답 12개',{exact:true})).toBeVisible();await expect(ocr.getByRole('combobox',{name:'프로젝트에 저장된 문자 정답',exact:true})).toHaveValue(scope.prepared.dataset_path);
  await expect(ocr.getByRole('combobox',{name:'모델 학습 실행 위치',exact:true})).toHaveValue('');await expect(ocr.getByRole('combobox',{name:'학습 평가 장치',exact:true})).toHaveValue('cpu');
 };
 const switchProject=async(scope:typeof A)=>{const deadline=performance.now()+10_000;await splitControlWithin(page.getByTitle('프로젝트 관리',{exact:true}).click(),deadline,'project manager');const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});await splitControlWithin(dialog.getByRole('button',{name:'최근 프로젝트',exact:true}).click(),deadline,'recent projects');
  const response=page.waitForResponse(reply=>reply.url()===origin+'/api/project/open'&&reply.request().method()==='POST'&&reply.request().postDataJSON()?.project_dir===scope.project.project_dir,{timeout:10_000});void response.catch(()=>{});const item=dialog.getByRole('button').filter({has:page.locator('span[title]').filter({hasText:scope.project.project_dir})});await expect(item).toHaveCount(1);await splitControlWithin(item.click(),deadline,'normal project selection');const wire=await splitControlWithin(response,deadline,'original project open');expect(wire.request().postDataJSON()).toEqual({project_dir:scope.project.project_dir});const full=await splitControlBody(wire,deadline);expect([full.body.id,full.body.project_dir,full.body.source_dataset_dir]).toEqual([scope.project.id,scope.project.project_dir,scope.source]);await splitControlWithin(expect(dialog).toHaveCount(0),deadline,'manager closure');return {project_id:scope.project.id,project_dir:scope.project.project_dir,response_sha256:full.sha256,status:full.status};
 };
 const openOCR=async(scope:typeof A)=>{await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(2).click();const button=page.getByRole('region',{name:'모델 학습 허브'}).getByRole('button',{name:/^문자 인식/});if(await button.getAttribute('aria-pressed')!=='true')await button.click();await expand();};
 const warmups:Array<unknown>=[];
 const initialize=async(scope:typeof A,action:()=>Promise<unknown>,label:string,beforeBaseline:boolean)=>{
  const deadline=performance.now()+10_000;
  const wait=(pathname:string)=>{const promise=page.waitForResponse(reply=>{const u=new URL(reply.url());return u.origin===origin&&u.pathname===pathname&&reply.request().method()==='GET'&&reply.request().headers()['x-vision-project']===scope.project.id;},{timeout:10_000});void promise.catch(()=>{});return promise;};
  const datasets=wait('/api/ocr/datasets'),models=wait('/api/ocr/models'),readiness=wait('/api/team-data/readiness');
  const imageResponse=page.waitForResponse(reply=>{const u=new URL(reply.url());return reply.request().method()==='GET'&&u.origin===origin&&u.pathname==='/api/dataset/images'&&u.searchParams.get('folder_path')===scope.source&&u.searchParams.get('task')==='classification'&&u.searchParams.get('limit')==='48'&&u.searchParams.get('offset')==='0';},{timeout:10_000});void imageResponse.catch(()=>{});
  await splitControlWithin(action(),deadline,'ordinary OCR initialization '+label);await splitControlWithin(openOCR(scope),deadline,'own OCR page '+label);
  const wire=[await splitControlWithin(datasets,deadline,'empty datasets'),await splitControlWithin(models,deadline,'empty models'),await splitControlWithin(readiness,deadline,'original stage3 readiness'),await splitControlWithin(imageResponse,deadline,'original picker initialization')];
  const full=[];for(const reply of wire)full.push(await splitControlBody(reply,deadline));expect(full[0].body).toEqual({datasets:[{dataset_path:scope.prepared.dataset_path,sample_count:12,provenance:scope.prepared.provenance}]});expect(full[1].body).toEqual({models:[]});expect(typeof full[2].body.ready).toBe('boolean');expect(full[2].body.counts.total).toBe(12);
  const readyMessage=full[2].body.ready?`학습 대상 ${full[2].body.counts.eligible||0}장 · 승인 ${full[2].body.counts.approved||0}장 · 검수 대기 ${full[2].body.counts.pending||0}장. 기존 시험 분할을 유지합니다.`:full[2].body.blockers.join(' · ')||'학습할 승인 데이터를 준비하세요.';
  const readyPanel=ocr.getByRole('region',{name:'팀 검수 학습 준비',exact:true});await splitControlWithin(expect(readyPanel.getByText(readyMessage,{exact:true})).toBeVisible(),deadline,'current readiness body rendered');await splitControlWithin(expect(readyPanel.getByRole('button',{name:'대상 다시 확인',exact:true})).toBeEnabled(),deadline,'current readiness request completed');
  const query=Object.fromEntries(new URL(wire[3].url()).searchParams.entries());expect(Array.from(new URL(wire[3].url()).searchParams.keys()).sort()).toEqual(['folder_path','limit','offset','task']);expect(query).toEqual({folder_path:scope.source,task:'classification',limit:'48',offset:'0'});expect([full[3].body.total,full[3].body.limit,full[3].body.offset]).toEqual([12,48,0]);expect(full[3].body.items.map((row:any)=>row.file_path).sort()).toEqual(scope.images.map(row=>path.join(scope.source,row.image)).sort());
  const contexts=wire.map(reply=>{const rh=reply.request().headers(),sh=reply.headers();expect(rh['x-vision-project']).toBe(scope.project.id);expect(rh['x-vision-context']).toBeDefined();const request=JSON.parse(rh['x-vision-context']);expect(request.project_id).toBe(scope.project.id);const response=sh['x-vision-context']===undefined?null:JSON.parse(sh['x-vision-context']);if(response!==null)expect(response).toEqual(request);return {request,response};});
  for(const row of contexts)expect(row.request).toEqual(contexts[0].request);ownedContexts.set(scope.project.id,contexts[0].request);
  await splitControlWithin(expect(picker).toBeEnabled(),deadline,'picker initialization complete');await splitControlWithin(expect(picker.locator('option')).toHaveCount(13),deadline,'twelve owning originals');await assertPrepared(scope);await settleOriginalReads();
  const result={label,project_id:scope.project.id,source:scope.source,request_urls:wire.map(reply=>reply.url()),contexts,whole_HTTP200:full,response_finished:null,picker_query:query,picker_enabled:true,picker_option_count:13,readiness_message:readyMessage,initialization_before_fixture_baseline:beforeBaseline};warmups.push(result);return result;
 };
 const reopens:Array<unknown>=[];
 const readSaved=async(scope:typeof A,label:string)=>{const deadline=performance.now()+10_000,waiting=page.waitForResponse(reply=>{const u=new URL(reply.url());return reply.request().method()==='GET'&&u.origin===origin&&u.pathname==='/api/ocr/manifest'&&u.searchParams.get('dataset_path')===scope.prepared.dataset_path;},{timeout:10_000});void waiting.catch(()=>{});await splitControlWithin(ocr.getByRole('button',{name:'저장된 정답 읽기',exact:true}).click(),deadline,'real owning saved truth read');const wire=await splitControlWithin(waiting,deadline,'whole owning prepared manifest'),u=new URL(wire.url());expect(Array.from(u.searchParams.keys()).sort()).toEqual(['dataset_path']);expect(Object.fromEntries(u.searchParams.entries())).toEqual({dataset_path:scope.prepared.dataset_path});const rh=wire.request().headers(),sh=wire.headers();expect(rh['x-vision-project']).toBe(scope.project.id);expect(JSON.parse(rh['x-vision-context'])).toEqual(ownedContexts.get(scope.project.id));if(sh['x-vision-context']!==undefined)expect(JSON.parse(sh['x-vision-context'])).toEqual(ownedContexts.get(scope.project.id));const full=await splitControlBody(wire,deadline);expect(full.body).toEqual(scope.prepared);await splitControlWithin(expect(table).toHaveValue(scope.TSV),deadline,'literal whole saved truth rendered');await splitControlWithin(expect(train).toBeEnabled(),deadline,'valid saved local training enabled');expect(splitControlFile(scope.manifest,custody)).toEqual(scope.manifestPin);expect(splitControlTree(scope.prepared.dataset_path,custody)).toEqual(scope.preparedTree);reopens.push({label,project_id:scope.project.id,query:{dataset_path:scope.prepared.dataset_path},response:full,response_finished:null});return full;};
 const beforeInitialization=treeState();
 let baseline:ReturnType<typeof treeState>|undefined,proof:Record<string,unknown>|undefined,failed=false,primary:unknown,removed=false,endsRemoved=false,completedAssertions=false;const secondary:string[]=[];
 thumbnailEnds.attach();page.on('request',observe);
 try{
  await initialize(B,async()=>{if(url)await page.goto(url);else await page.reload();},'B first prepared owning initialization',true);await readSaved(B,'B initial full saved truth');await initialize(A,()=>switchProject(A),'A first prepared owning initialization',true);await readSaved(A,'A initial full saved truth');await settleOriginalReads();baseline=treeState();expect(baseline.A.source).toEqual(beforeInitialization.A.source);expect(baseline.B.source).toEqual(beforeInitialization.B.source);
  const initializationChanges:Record<string,unknown>={};for(const tag of ['A','B']as const){const prior=beforeInitialization[tag].project,current=baseline[tag].project;initializationChanges[tag]=Array.from(new Set([...Object.keys(prior),...Object.keys(current)])).sort().filter(name=>JSON.stringify(prior[name])!==JSON.stringify(current[name])).map(name=>({relative:name,before:prior[name]??null,after:current[name]??null}));}const writeStart=writes.length;expect(modelRequests).toEqual([]);
  const same=async()=>{await settleOriginalReads();expect(treeState()).toEqual(baseline);expect(sourceState()).toEqual(sourceBefore);expect(modelRequests).toEqual([]);expect(splitControlFile(A.manifest,custody)).toEqual(A.manifestPin);expect(splitControlFile(B.manifest,custody)).toEqual(B.manifestPin);};
  // Draft a different train alphabet in the actual table; Save is available but never clicked.
  const draftRows=A.rows.map((row,index)=>({...row,text:index===0?row.text+'Z':row.text})),draftTSV=draftRows.map(row=>`${row.image}\t${row.text}\t${row.split}`).join('\n');expect(draftTSV).not.toBe(A.TSV);expect(draftRows[0].split).toBe('train');expect(draftRows[0].text).toBe('AB12Z');
  const draftDeadline=performance.now()+10_000;await splitControlWithin(table.fill(draftTSV),draftDeadline,'actual unsent alphabet draft');await splitControlWithin(expect(table).toHaveValue(draftTSV),draftDeadline,'whole unsent draft rendered');await splitControlWithin(expect(save).toBeEnabled(),draftDeadline,'owning save available but unsent');await splitControlWithin(expect(train).toBeDisabled(),draftDeadline,'unvalidated draft cannot train');await same();expect(writes.slice(writeStart)).toEqual([]);await e.screenshot(page,`${native?'native':'browser'}-ocr-unsent-alphabet-draft`);
  const leave=await initialize(B,()=>switchProject(B),'B prepared truth handoff',false);await expect(table).toHaveValue('');await readSaved(B,'B exact distinct saved alphabet and splits');await expect(table).toHaveValue(B.TSV);expect(B.TSV).not.toBe(A.TSV);await same();
  const back=await initialize(A,()=>switchProject(A),'A prepared truth handoff',false);await expect(table).toHaveValue('');await readSaved(A,'A exact saved alphabet and splits after draft abandonment');await expect(table).toHaveValue(A.TSV);expect(await table.inputValue()).not.toBe(draftTSV);await same();
  expect(A.prepared.alphabet).toBe('12AB');expect(B.prepared.alphabet).toBe('12AB');expect(A.prepared.provenance.split_counts).toEqual({train:8,val:2,test:2});expect(B.prepared.provenance.split_counts).toEqual({train:8,val:2,test:2});expect(A.prepared.dataset_path).not.toBe(B.prepared.dataset_path);expect(A.project.id).not.toBe(B.project.id);expect(A.manifestPin.sha256).not.toBe(B.manifestPin.sha256);
  expect(writes.slice(writeStart)).toEqual([{method:'POST',path:'/api/project/open',body:JSON.stringify({project_dir:B.project.project_dir})},{method:'POST',path:'/api/project/open',body:JSON.stringify({project_dir:A.project.project_dir})}]);expect(modelRequests).toEqual([]);await e.screenshot(page,`${native?'native':'browser'}-ocr-owning-alphabet-split-handoff`);
  proof={selected_pending_pointers:['/records/29/actions/1/scenarios/cancel','/records/29/actions/1/scenarios/handoff','/records/29/actions/2/scenarios/handoff'],cells:[{action:'F030.ocr-native-unknown-training-alphabet',dimension:'cancel',contract:'Only unsent train-alphabet draft abandonment through real B/A; no request/server/job cancellation',saved_train_alphabet:'12AB',unsent_train_character:'Z',prepare_POSTs_after_baseline:0,saved_manifest_namespace_unchanged:true},{action:'F030.ocr-native-unknown-training-alphabet',dimension:'handoff',contract:'Whole saved train-derived alphabet and exact ownership through ordinary A/B/A',whole_alphabet:'12AB',distinct_project_and_manifest_IDs:true,no_training_or_model_job:true},{action:'F030.ocr-native-hash-bound-splits',dimension:'handoff',contract:'Whole saved8/2/2,image hashes,UUID,source_map,digest and owning GET contexts through actual A/B/A',split_counts:{train:8,val:2,test:2},full_saved_manifest_and_source_hashes:true,no_new_preparation_after_baseline:true}],unsent_draft:{rows:draftRows,TSV:draftTSV,prior_saved_TSV:A.TSV,returned_saved_TSV:await table.inputValue(),Save_clicked:false,prepare_POSTs:0,in_flight_cancel_claim:false},UI_SOURCE_expected:UI_SOURCE,UI_SOURCE_before:sourceBefore,UI_SOURCE_after:sourceState(),fixture_setup_only:[A,B].map(scope=>({project:scope.project,source:scope.source,source_original:scope.sourceOriginal,original_images:scope.images,literal_rows:scope.rows,prepared_response:scope.prepared,manifest_path:scope.manifest,manifest_pin:{raw:scope.manifestPin.raw,sha256:scope.manifestPin.sha256},whole_manifest:scope.manifestBody,prepared_tree:scope.preparedTree,no_preparation_success_cell_credit:true})),warmups,reopens,leave,back,owned_before_initialization:beforeInitialization,exact_initialization_metadata_changes:initializationChanges,protected_before:baseline,protected_after:treeState(),write_requests:writes.slice(writeStart),model_requests:modelRequests,original_reads:reads.map(({request,...row})=>({...row,method:request.method(),url:request.url()})),full_SQLite_WAL_SHM_and_namespace_included:true,initial_backend_metadata_transactions_finished_before_baseline:true,no_route_mocks_or_store_delegates:true,no_post_baseline_prepare_training_evaluation_prediction_jobs_POSTs:true,no_accepted_quality_Runtime_Native_or_parent_credit:true};completedAssertions=true;
 }catch(cause){failed=true;primary=cause;}
 finally{
  try{page.off('request',observe);removed=true;}catch(cause){secondary.push('request observer removal: '+String(cause));if(!failed){failed=true;primary=cause;}}
  try{await settleOriginalReads();expect(reads.every(row=>row.complete&&!row.failure)).toBe(true);}catch(cause){secondary.push('original GET closure: '+String(cause));if(!failed){failed=true;primary=cause;}}
  try{thumbnailEnds.detach();endsRemoved=true;}catch(cause){secondary.push('thumbnail end observer removal: '+String(cause));if(!failed){failed=true;primary=cause;}}
  if(baseline!==undefined){try{expect(treeState()).toEqual(baseline);}catch(cause){secondary.push('final full target tree: '+String(cause));if(!failed){failed=true;primary=cause;}}}
  try{expect(sourceState()).toEqual(sourceBefore);}catch(cause){secondary.push('final exact UI_SOURCE custody: '+String(cause));if(!failed){failed=true;primary=cause;}}
  try{expect(custody.opened).toBe(custody.closed);expect(custody.uncertain).toEqual([]);}catch(cause){secondary.push('owned FD closure: '+String(cause));if(!failed){failed=true;primary=cause;}}
  try{e.note('ocr_prepared_unsent_handoff_closure',{first_exception:failed?String(primary):null,secondary,all_owned_FDs_closed:custody.opened===custody.closed&&custody.uncertain.length===0,FD_ledger:custody,request_observer_removed:removed,thumbnail_end_observer_removed:endsRemoved,all_original_GETs_complete:reads.every(row=>row.complete&&!row.failure),retired_gallery_thumbnail_reads:reads.filter(row=>row.retired_gallery_read===true).map(row=>({url:row.request.url(),...row.retired})),actual_success_assertions_completed:completedAssertions});}catch(cause){secondary.push('closure receipt: '+String(cause));if(!failed){failed=true;primary=cause;}}
 }
 if(failed)throw primary;expect(removed).toBe(true);expect(endsRemoved).toBe(true);expect(completedAssertions).toBe(true);expect(proof).toBeTruthy();e.note('OCR_prepared_unsent_handoff',proof);
}
test('OCR unsent alphabet draft and saved alphabet split handoff preserve whole originals',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:OwnedApi=async(route,body,method)=>{const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(response.status()).toBe(200);const bytes=await response.body();return JSON.parse(bytes.toString('utf8'));};await ocrPreparedUnsentHandoffExercise(page,workspace,evidence,api,false,renderer.origin,renderer.url);
});
test('native OCR unsent alphabet draft and saved alphabet split handoff preserve whole originals',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,backend=await electronSession.waitForBackend();const api:OwnedApi=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(response.status!==200)throw Error(`Owned OCR prepared fixture API: HTTP ${response.status}`);const bytes=await response.arrayBuffer();return JSON.parse(new TextDecoder().decode(bytes));},{port:backend.port,route,body,method});await ocrPreparedUnsentHandoffExercise(window,workspace,evidence,api,true,`http://127.0.0.1:${backend.port}`);
});
