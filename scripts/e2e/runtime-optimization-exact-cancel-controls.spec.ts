import path from 'node:path';
import {test,expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

test('optimization renderer refuses cached source and foreign poll responses before review',async({page,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);
 const created=await page.request.post(renderer.origin+'/api/project/create',{data:{name:'Optimization source binding control',task:'classification'}});expect(created.ok(),await created.text()).toBe(true);
 const project=await created.json();
 const updated=await page.request.put(renderer.origin+'/api/project/update',{data:{source_dataset_dir:workspace.dataset}});expect(updated.ok(),await updated.text()).toBe(true);
 const packagePath=path.join(project.project_dir,'exports/flows/controlled-source');
 const row={package_id:'controlled-source',name:'Controlled input package record',package_path:packagePath,integrity:'verified',scope_matches:true,approval_present:false,models:[],parity:{status:'not_run'},optimization_jobs:[]};
 const options={package_dir:packagePath,input_receipt:{source_dataset_path:workspace.dataset}};
 const cached={job_id:'cached',status:'failed',package_path:'',options:{...options,input_receipt:{source_dataset_path:path.join(workspace.root,'previous-source')}},error:'previous source run',result:null};
 const own={job_id:'owned',status:'running',package_path:'',options,error:null,result:null};
 let foreign=true,reviewRequests=0;const submissions:any[]=[];
 const answer=(route:any,value:unknown)=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(value)});
 await page.route('**/api/product-delivery/packages',r=>answer(r,{packages:[row],selected_package_id:null,scope:{project_id:project.id,source_dataset_path:workspace.dataset,task:'classification'}}));
 await page.route('**/api/product-delivery/packages/controlled-source/select',r=>answer(r,row));
 await page.route('**/api/export/runtime-capabilities',r=>answer(r,{torch_devices:['cpu'],openvino:{available:true,devices:['CPU']},native_sdk:{languages:[],transport:'controlled'}}));
 await page.route('**/api/dataset/images?**',r=>{const split=new URL(r.request().url()).searchParams.get('split');const input=workspace.images[split==='train'?0:1];return answer(r,{total:1,limit:128,offset:0,items:[{file_path:input.path,file_name:path.basename(input.path),width:32,height:32,split:split||'test'}]});});
 await page.route('**/api/export/flow/optimize',r=>{submissions.push(r.request().postDataJSON());return answer(r,own);});
 await page.route('**/api/export/flow/optimization-jobs/*',r=>answer(r,new URL(r.request().url()).pathname.endsWith('/cached')?cached:foreign?{...own,job_id:'foreign',status:'completed'}:{...own,status:'failed',error:'controlled conversion stopped'}));
 await page.route('**/api/export/flow/optimization-jobs/*/approval-prerequisites',r=>{reviewRequests++;return answer(r,{approval_revision_ids:{}});});
 await page.route('**/api/export/flow/optimization-jobs/*/heldout-results/*',r=>{reviewRequests++;return answer(r,null);});
 const key='runtime-optimization:'+JSON.stringify([project.project_dir,packagePath,workspace.dataset,'classification']);
 await page.addInitScript(key=>localStorage.setItem(key,'cached'),key);
 await page.goto(renderer.url);
 await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();
 await page.getByRole('button',{name:'최적화로 이동',exact:true}).click();
 const panel=page.getByRole('region',{name:'Runtime 최적화와 양자화'});
 await expect(panel.getByRole('alert')).toContainText('패키지·원본 소스');
 await expect(panel.getByLabel('선택한 최적화 작업',{exact:true})).toHaveCount(0);
 expect(reviewRequests).toBe(0);
 await panel.getByLabel('정밀도',{exact:false}).selectOption('int8');
 await panel.getByRole('button',{name:'독립 후보 패키지 생성',exact:true}).click();
 await expect(panel.getByLabel('선택한 최적화 작업',{exact:true})).toContainText('owned');
 await expect(panel.getByRole('alert')).toContainText('ID');
 await expect(panel.getByLabel('선택한 최적화 작업',{exact:true})).not.toContainText('foreign');
 await expect(panel.getByRole('button',{name:'검토 후 새 승인 패키지 생성',exact:true})).toHaveCount(0);
 expect(reviewRequests).toBe(0);expect(submissions).toHaveLength(1);
 expect(submissions[0]).toMatchObject({package_dir:packagePath,source_dataset_path:workspace.dataset,precision:'int8',calibration_images:[workspace.images[0].path],validation_images:[workspace.images[1].path]});
 await panel.getByRole('alert').scrollIntoViewIfNeeded();await evidence.screenshot(page,'optimization-foreign-source-and-job-refusal');
 foreign=false;await expect(panel.getByRole('status')).toContainText('controlled conversion stopped');
 await expect(panel.getByRole('alert')).toHaveCount(0);
 expect(await page.evaluate(key=>localStorage.getItem(key),key)).toBe('owned');
 await panel.getByRole('status').scrollIntoViewIfNeeded();await evidence.screenshot(page,'optimization-owned-poll-recovery');
 evidence.note('optimization_binding',{actual_renderer:true,owned_project_backend:true,optimization_transport:'controlled responses; no model conversion or quality approval',cached_other_source_refused:true,foreign_poll_job_refused:true,foreign_review_requests:reviewRequests,matching_poll_resumed:true,distinct_calibration_validation_paths:true,actual_int8_execution:false,native_electron:false});
});

test('optimization lifecycle refuses missing input and failed submission, cancels exact job and reopens',async({page,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);
 const created=await page.request.post(renderer.origin+'/api/project/create',{data:{name:'Optimization lifecycle control',task:'classification'}});expect(created.ok(),await created.text()).toBe(true);
 const project=await created.json();
 expect((await page.request.put(renderer.origin+'/api/project/update',{data:{source_dataset_dir:workspace.dataset}})).ok()).toBe(true);
 const packagePath=path.join(project.project_dir,'exports/flows/controlled-lifecycle');
 const row={package_id:'controlled-lifecycle',name:'Controlled lifecycle input package',package_path:packagePath,integrity:'verified',scope_matches:true,approval_present:false,models:[],parity:{status:'not_run'},optimization_jobs:[]};
 const options={package_dir:packagePath,input_receipt:{source_dataset_path:workspace.dataset}};
 let empty=true,unavailable=true,submitFailure=true,foreignCancel=true,reviewRequests=0;
 let owned={job_id:'owned-lifecycle',status:'running',package_path:'',options,error:null,result:null};
 const submissions:any[]=[],cancels:string[]=[];
 const answer=(route:any,value:unknown,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
 await page.route('**/api/product-delivery/packages',r=>answer(r,{packages:[row],selected_package_id:null,scope:{project_id:project.id,source_dataset_path:workspace.dataset,task:'classification'}}));
 await page.route('**/api/product-delivery/packages/controlled-lifecycle/select',r=>answer(r,row));
 await page.route('**/api/export/runtime-capabilities',r=>answer(r,{torch_devices:['cpu'],openvino:{available:!unavailable,devices:unavailable?[]:['CPU'],error:unavailable?'controlled optional runtime absent':undefined},native_sdk:{languages:[],transport:'controlled'}}));
 await page.route('**/api/dataset/images?**',r=>{
  const split=new URL(r.request().url()).searchParams.get('split'),input=workspace.images[split==='train'?0:1];
  return answer(r,{total:empty?0:1,limit:128,offset:0,items:empty?[]:[{file_path:input.path,file_name:path.basename(input.path),width:32,height:32,split:split||'test'}]});
 });
 await page.route('**/api/export/flow/optimize',r=>{submissions.push(r.request().postDataJSON());return submitFailure?answer(r,{detail:'controlled pack input refused'},422):answer(r,owned);});
 await page.route('**/api/export/flow/optimization-jobs/*',r=>answer(r,owned));
 await page.route('**/api/export/flow/optimization-jobs/*/cancel',r=>{
  cancels.push(new URL(r.request().url()).pathname);
  if(foreignCancel)return answer(r,{...owned,job_id:'foreign-cancel',status:'cancelled'});
  owned={...owned,status:'cancelled'};return answer(r,owned);
 });
 await page.route('**/api/export/flow/optimization-jobs/*/approval-prerequisites',r=>{reviewRequests++;return answer(r,{approval_revision_ids:{}});});
 await page.route('**/api/export/flow/optimization-jobs/*/heldout-results/*',r=>{reviewRequests++;return answer(r,null);});
 const open=async()=>{
  await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();
  await page.getByRole('button',{name:'최적화로 이동',exact:true}).click();
  return page.getByRole('region',{name:'Runtime 최적화와 양자화'});
 };
 await page.goto(renderer.url);let panel=await open();
 await expect(panel.getByRole('alert')).toContainText('controlled optional runtime absent');
 await expect(panel.getByRole('button',{name:'독립 후보 패키지 생성',exact:true})).toBeDisabled();
 await expect(panel.getByRole('group',{name:'변환 오차 검증 · test / val'}).getByText('명시된 split의 이미지가 없습니다.',{exact:true})).toBeVisible();
 expect(submissions).toHaveLength(0);await panel.scrollIntoViewIfNeeded();await evidence.screenshot(page,'optimization-empty-and-unavailable-refused');
 empty=false;unavailable=false;await page.reload();panel=await open();
 await panel.getByLabel('정밀도',{exact:false}).selectOption('int8');
 const submit=panel.getByRole('button',{name:'독립 후보 패키지 생성',exact:true});
 const calibration=panel.getByRole('group',{name:'보정 이미지 · train'}).getByRole('checkbox');
 const validation=panel.getByRole('group',{name:'변환 오차 검증 · test / val'}).getByRole('checkbox');
 await calibration.uncheck();await expect(submit).toBeDisabled();await calibration.check();
 await validation.uncheck();await expect(submit).toBeDisabled();await validation.check();
 await submit.click();await expect(panel.getByRole('alert')).toContainText('controlled pack input refused');
 await expect(panel.getByLabel('선택한 최적화 작업',{exact:true})).toHaveCount(0);
 await expect(submit).toBeEnabled();await expect(calibration).toBeChecked();await expect(validation).toBeChecked();
 await panel.getByRole('alert').scrollIntoViewIfNeeded();await evidence.screenshot(page,'optimization-submission-error-keeps-input');
 submitFailure=false;await submit.click();await expect(panel.getByRole('status')).toContainText('running');
 const cancel=panel.getByRole('button',{name:'변환 중단',exact:true});
 await cancel.click();await expect(panel.getByRole('alert')).toContainText('ID');
 await expect(panel.getByRole('status')).toContainText('running');
 await expect(panel.getByLabel('선택한 최적화 작업',{exact:true})).not.toContainText('foreign-cancel');
 foreignCancel=false;await cancel.click();await expect(panel.getByRole('status')).toContainText('cancelled');
 await expect(panel.getByRole('alert')).toHaveCount(0);
 await expect(cancel).toHaveCount(0);expect(cancels).toEqual(Array(2).fill('/api/export/flow/optimization-jobs/owned-lifecycle/cancel'));
 await page.reload();panel=await open();
 await expect(panel.getByLabel('선택한 최적화 작업',{exact:true})).toContainText('owned-lifecycle');
 await expect(panel.getByRole('status')).toContainText('cancelled');
 await expect(panel.getByRole('button',{name:'검토 후 새 승인 패키지 생성',exact:true})).toHaveCount(0);
 expect(reviewRequests).toBe(0);expect(submissions).toHaveLength(2);
 expect(submissions[1]).toMatchObject({package_dir:packagePath,source_dataset_path:workspace.dataset,precision:'int8',calibration_images:[workspace.images[0].path],validation_images:[workspace.images[1].path]});
 await panel.getByRole('status').scrollIntoViewIfNeeded();await evidence.screenshot(page,'optimization-exact-cancel-reopened');
 evidence.note('optimization_lifecycle',{actual_renderer:true,owned_project_backend:true,optimization_transport:'controlled HTTP; no model execution or actual worker cancellation',empty_and_unavailable_blocked:true,unselected_calibration_or_validation_blocked:true,failed_submission_kept_selection:true,foreign_cancel_refused:true,cancel_requested_job:'owned-lifecycle',matching_cancel_reopened:true,foreign_review_requests:reviewRequests,actual_conversion:false,native_electron:false,quality_accepted:false});
});

// The original two tests above are an exact retained byte prefix. This extra
// case qualifies controlled renderer transport, not an optimization worker.
import fs from 'node:fs';
import {createHash} from 'node:crypto';
import type {Page,Request,Response,Route} from '@playwright/test';

const optimizationControlHash=(bytes:Buffer|string)=>createHash('sha256').update(bytes).digest('hex');
const optimizationWirePath=(request:Request)=>new URL(request.url()).pathname;

test('optimization exact cancel has no empty POST, preserves owned 503 retry and fences A B A late receipt',async({page,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);
 const origin=renderer.origin;
 type Owner={tag:'A'|'B';project:any;source:string;packagePath:string;row:any;key:string;images:Array<{path:string;sha256:string}>;jobs:Map<string,any>;next:number};
 const owners:Owner[]=[];
 const setup:any[]=[];
 const api=async(route:string,data?:unknown,method?:string)=>{
  const response=await page.request.fetch(origin+route,{method:method||(data===undefined?'GET':'POST'),...(data===undefined?{}:{data})});
  const body=await response.body();expect(response.ok(),body.toString('utf8')).toBe(true);
  setup.push({channel:'owned APIRequestContext fixture setup/readback',method:method||(data===undefined?'GET':'POST'),path:route,status:response.status(),body:JSON.parse(body.toString('utf8')),sha256:optimizationControlHash(body)});
  return JSON.parse(body.toString('utf8'));
 };
 for(const tag of ['A','B'] as const){
  const source=tag==='A'?workspace.dataset:path.join(workspace.root,'optimization-source-B');
  const images=workspace.images.map((image,index)=>{
   const file=tag==='A'?image.path:path.join(source,String(index),path.basename(image.path));
   if(tag==='B'){fs.mkdirSync(path.dirname(file),{recursive:true});fs.copyFileSync(image.path,file,fs.constants.COPYFILE_EXCL);}
   const bytes=fs.readFileSync(file);expect(optimizationControlHash(bytes)).toBe(image.sha256);return {path:file,sha256:image.sha256};
  });
  let project=await api('/api/project/create',{name:'Exact optimization cancel '+tag,task:'classification'});
  project=await api('/api/project/update',{source_dataset_dir:source},'PUT');
  const packagePath=path.join(project.project_dir,'exports/flows/controlled-cancel-'+tag);
  fs.mkdirSync(packagePath,{recursive:true});
  const input={controlled_package_record:true,tag,project_id:project.id,source_dataset_path:source,images,actual_flow_or_model:false};
  fs.writeFileSync(path.join(packagePath,'controlled-input.json'),JSON.stringify(input),{flag:'wx'});
  const row={package_id:'controlled-cancel-'+tag,name:'Controlled exact cancel package '+tag,package_path:packagePath,integrity:'verified',scope_matches:true,approval_present:false,models:[],parity:{status:'not_run'},optimization_jobs:[]};
  owners.push({tag,project,source,packagePath,row,key:'runtime-optimization:'+JSON.stringify([project.project_dir,packagePath,source,'classification']),images,jobs:new Map(),next:0});
 }
 const [A,B]=owners;
 await api('/api/project/open',{project_dir:A.project.project_dir});
 const packageInputs=()=>owners.map(owner=>({tag:owner.tag,package_path:owner.packagePath,controlled_input_sha256:optimizationControlHash(fs.readFileSync(path.join(owner.packagePath,'controlled-input.json'))),images:owner.images.map(image=>({path:image.path,sha256:optimizationControlHash(fs.readFileSync(image.path))}))}));
 const originalInputs=packageInputs();
 const scopedStorage=()=>page.evaluate(keys=>Object.fromEntries(keys.map(key=>[key,localStorage.getItem(key)])),owners.map(owner=>owner.key));
 const storage:any[]=[];
 const wire:any[]=[];const controlled:any[]=[];const ownedMutations:any[]=[];const pageErrors:string[]=[];const consoles:any[]=[];const pending:Set<Promise<void>>=new Set();
 const requests=new Map<Request,any>();let sequence=0;let failCancel=true;let holdNextA=false;let heldRequest:Request|undefined;let heldJob:any;let releaseHeld:(()=>void)|undefined;let heldSettled:Promise<void>|undefined;let heldHandlerError:unknown;let reviewRequests=0;
 let heldOutcome:any;let finishHeld:(value:any)=>void;
 const heldDisposition=new Promise<any>(resolve=>{finishHeld=resolve;});
 let heldDeadline:number|undefined;
 let heldSettlementJoin:{promise:Promise<void>,completed_monotonic_ms:number}|undefined;
 // One monotonic original ten-second budget begins before releasing the
 // original held route and covers settlement, disposition, and full reads.
 const withinHeld=async<T>(operation:string,start:()=>Promise<T>,onJoined?:(completedAt:number)=>void):Promise<T>=>{
  if(heldDeadline===undefined)throw Error('Original held deadline not started: '+operation);
  const remaining=heldDeadline-performance.now();if(!(remaining>0))throw Error('Original held ten-second deadline expired: '+operation);
  let timer:ReturnType<typeof setTimeout>|undefined;
  const owned=Promise.resolve().then(()=>{if(performance.now()>=heldDeadline!)throw Error('Original held ten-second deadline expired before start: '+operation);return start();});void owned.catch(()=>undefined);
  try{const value=await Promise.race([owned,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original held ten-second deadline expired: '+operation)),remaining);})]);const completedAt=performance.now();if(completedAt>=heldDeadline)throw Error('Original held ten-second deadline expired after completion: '+operation);if(onJoined)onJoined(completedAt);return value;}
  finally{if(timer!==undefined)clearTimeout(timer);}
 };
 const relevant=(request:Request)=>{
  const url=new URL(request.url());return url.origin===origin&&request.frame()===page.mainFrame()&&(
   url.pathname.startsWith('/api/export/flow/optimization-jobs/')||url.pathname==='/api/export/flow/optimize'||url.pathname==='/api/export/runtime-capabilities'||url.pathname.startsWith('/api/product-delivery/packages')||url.pathname==='/api/dataset/images'||url.pathname==='/api/project/open');
 };
 const observeRequest=(request:Request)=>{const url=new URL(request.url());if(url.origin===origin&&request.frame()===page.mainFrame()&&!['GET','HEAD'].includes(request.method()))ownedMutations.push({method:request.method(),path:url.pathname,body:request.postData()?request.postDataJSON():null,project_id:request.headers()['x-vision-project']??null});if(!relevant(request))return;const headers=request.headers();const row={sequence:++sequence,url:request.url(),method:request.method(),path:optimizationWirePath(request),query:new URL(request.url()).search,body:request.postData()?request.postDataJSON():null,project_id:headers['x-vision-project']??null,project_context:headers['x-vision-context']?JSON.parse(headers['x-vision-context']):null};requests.set(request,row);wire.push(row);};
 const observeResponse=(response:Response)=>{
  const request=response.request();if(!requests.has(request))return;
  const original=requests.get(request);
  const capture=(async()=>{
   const bytes=request===heldRequest?await withinHeld('original full response body',()=>response.body()):await response.body();expect(bytes.length).toBeLessThanOrEqual(1024*1024);
   expect(request===heldRequest?await withinHeld('original full response finished',()=>response.finished()):await response.finished()).toBeNull();
   const file=path.join(workspace.logs,'optimization-cancel-wire-'+original.sequence+'.json');fs.writeFileSync(file,bytes,{flag:'wx'});evidence.addFile(file);
   Object.assign(original,{status:response.status(),response_body:JSON.parse(bytes.toString('utf8')),response_sha256:optimizationControlHash(bytes),response_bytes:bytes.length,response_file:file,finished:true});
  })();pending.add(capture);void capture.catch(()=>undefined);
 };
 const finished=(request:Request)=>{if(request===heldRequest)finishHeld({kind:'finished'});};
 const failed=(request:Request)=>{if(requests.has(request))requests.get(request).failure=request.failure()?.errorText??null;if(request===heldRequest)finishHeld({kind:'failed',failure:request.failure()?.errorText??null});};
 const onConsole=(message:any)=>{if(message.type()==='error')consoles.push({text:message.text(),location:message.location()});};
 const onPageError=(error:Error)=>pageErrors.push(error.message);
 page.on('request',observeRequest);page.on('response',observeResponse);page.on('requestfinished',finished);page.on('requestfailed',failed);page.on('console',onConsole);page.on('pageerror',onPageError);
 const ownerOf=(request:Request)=>{
  expect(new URL(request.url()).origin).toBe(origin);expect(request.frame()).toBe(page.mainFrame());
  const owner=owners.find(item=>item.project.id===request.headers()['x-vision-project']);expect(owner,'Original UI request must carry an exact original owner').toBeTruthy();return owner!;
 };
 const answer=async(route:Route,body:any,status=200)=>{
  const request=route.request();const owner=ownerOf(request);controlled.push({owner:owner.tag,method:request.method(),path:optimizationWirePath(request),request_body:request.postData()?request.postDataJSON():null,status,response_body:body,backend_dispatch_executed:false});
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
 };
 const makeJob=(owner:Owner)=>{const id='owned-cancel-'+owner.tag+'-'+owner.next++;const job={job_id:id,status:'running',package_path:'',options:{package_dir:owner.packagePath,input_receipt:{source_dataset_path:owner.source}},error:null,result:null};owner.jobs.set(id,job);return job;};
 const scopedJob=(request:Request)=>{const owner=ownerOf(request);const parts=optimizationWirePath(request).split('/');const id=decodeURIComponent(parts[5]);const job=owner.jobs.get(id);expect(job,'Only exact previously submitted own job is routable').toBeTruthy();return {owner,id,job};};
 await page.route('**/api/product-delivery/packages',route=>{const owner=ownerOf(route.request());return answer(route,{packages:[owner.row],selected_package_id:null,scope:{project_id:owner.project.id,source_dataset_path:owner.source,task:'classification'}});});
 await page.route('**/api/product-delivery/packages/*/select',route=>{const owner=ownerOf(route.request());expect(optimizationWirePath(route.request())).toBe('/api/product-delivery/packages/'+owner.row.package_id+'/select');return answer(route,owner.row);});
 await page.route('**/api/export/runtime-capabilities',route=>answer(route,{torch_devices:['cpu'],openvino:{available:true,devices:['CPU']},native_sdk:{languages:[],transport:'controlled'}}));
 await page.route('**/api/dataset/images?**',route=>{
  const owner=ownerOf(route.request());const url=new URL(route.request().url());expect(url.searchParams.get('folder_path')).toBe(owner.source);
  const split=url.searchParams.get('split');const image=owner.images[split==='train'?0:1];return answer(route,{total:1,limit:128,offset:0,items:[{file_path:image.path,file_name:path.basename(image.path),width:32,height:32,split:split||'test'}]});
 });
 await page.route('**/api/export/flow/optimize',route=>{const owner=ownerOf(route.request());expect(route.request().method()).toBe('POST');expect(route.request().postDataJSON()).toEqual({package_dir:owner.packagePath,source_dataset_path:owner.source,precision:'int8',device:'CPU',cpu_threads:1,calibration_images:[owner.images[0].path],validation_images:[owner.images[1].path]});return answer(route,makeJob(owner));});
 await page.route('**/api/export/flow/optimization-jobs/*',route=>{expect(route.request().method()).toBe('GET');return answer(route,scopedJob(route.request()).job);});
 await page.route('**/api/export/flow/optimization-jobs/*/cancel',async route=>{
  expect(route.request().method()).toBe('POST');const {owner,id,job}=scopedJob(route.request());
  if(failCancel){expect(owner).toBe(A);failCancel=false;return answer(route,{detail:'controlled exact owner cancel unavailable'},503);}
  if(holdNextA){expect(owner).toBe(A);holdNextA=false;heldRequest=route.request();heldJob=JSON.parse(JSON.stringify(job));const release=new Promise<void>(resolve=>{releaseHeld=resolve;});heldSettled=(async()=>{await release;try{await answer(route,{...heldJob,status:'cancelled'});}catch(error){heldHandlerError=error;}})();await heldSettled;return;}
  const cancelled={...job,status:'cancelled'};owner.jobs.set(id,cancelled);return answer(route,cancelled);
 });
 await page.route('**/api/export/flow/optimization-jobs/*/approval-prerequisites',route=>{reviewRequests++;return answer(route,{approval_revision_ids:{}});});
 await page.route('**/api/export/flow/optimization-jobs/*/heldout-results/*',route=>{reviewRequests++;return answer(route,null);});
 const delivery=()=>page.getByRole('dialog',{name:'패키지·장치·설치·진단',exact:true});
 const panel=()=>page.getByRole('region',{name:'Runtime 최적화와 양자화'});
 const open=async()=>{await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();await delivery().getByRole('button',{name:'최적화로 이동',exact:true}).click();await expect(panel()).toBeVisible();await expect(panel().getByRole('combobox',{name:'검증 장치',exact:true})).toHaveValue('CPU');};
 const closeDelivery=async()=>{await delivery().getByRole('button',{name:'패키지·장치·설치·진단 닫기',exact:true}).click();await expect(delivery()).toHaveCount(0);};
 const openProject=async(owner:Owner)=>{
  await page.getByTitle('프로젝트 관리',{exact:true}).click();const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});await dialog.getByRole('button',{name:'폴더에서 열기',exact:true}).click();await dialog.getByPlaceholder('/path/to/project',{exact:true}).fill(owner.project.project_dir);
  const reply=page.waitForResponse(response=>new URL(response.url()).origin===origin&&optimizationWirePath(response.request())==='/api/project/open'&&response.request().method()==='POST'&&response.request().postDataJSON()?.project_dir===owner.project.project_dir);
  void reply.catch(()=>undefined);await dialog.getByRole('button',{name:'프로젝트 열기',exact:true}).click();const response=await reply;expect(response.status()).toBe(200);expect((await response.json()).id).toBe(owner.project.id);await expect(dialog).toHaveCount(0);await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(owner.project.name);expect((await api('/api/project/current')).id).toBe(owner.project.id);
 };
 const selected=()=>panel().getByLabel('선택한 최적화 작업',{exact:true});
 const cancel=()=>panel().getByRole('button',{name:'변환 중단',exact:true});
 const submit=()=>panel().getByRole('button',{name:'독립 후보 패키지 생성',exact:true});
 const createJob=async(owner:Owner)=>{await panel().getByLabel('정밀도',{exact:false}).selectOption('int8');const next='owned-cancel-'+owner.tag+'-'+owner.next;await submit().click();await expect(selected()).toContainText(next);await expect(panel().getByRole('status')).toContainText('running');expect(await scopedStorage()).toMatchObject({[owner.key]:next});return next;};
 const shot=async(name:string)=>{await panel().scrollIntoViewIfNeeded();await evidence.screenshot(page,name);};
 let primary:unknown;const secondary:any[]=[];
 try{
  await page.goto(renderer.url);await open();
  await expect(selected()).toHaveCount(0);await expect(cancel()).toHaveCount(0);await expect(submit()).toBeEnabled();
  storage.push({stage:'A empty',value:await scopedStorage()});expect(storage.at(-1).value).toEqual({[A.key]:null,[B.key]:null});expect(wire.filter(row=>row.method==='POST'&&row.path.endsWith('/cancel'))).toHaveLength(0);await shot('optimization-empty-exact-cancel-absent');
  const a0=await createJob(A);await cancel().click();await expect(panel().getByRole('alert')).toContainText('controlled exact owner cancel unavailable');await expect(selected()).toContainText(a0);await expect(panel().getByRole('status')).toContainText('running');await expect(cancel()).toBeEnabled();expect(packageInputs()).toEqual(originalInputs);storage.push({stage:'A 503 retains exact job',value:await scopedStorage()});expect(storage.at(-1).value).toEqual({[A.key]:a0,[B.key]:null});await shot('optimization-exact-owned-503-preserves-job-and-input');
  await cancel().click();await expect(panel().getByRole('status')).toContainText('cancelled');await expect(panel().getByRole('alert')).toHaveCount(0);await expect(cancel()).toHaveCount(0);
  expect(wire.filter(row=>row.method==='POST'&&row.path.endsWith('/cancel')).map(row=>row.path)).toEqual(Array(2).fill('/api/export/flow/optimization-jobs/'+a0+'/cancel'));
  const a1=await createJob(A);holdNextA=true;await cancel().click();await expect.poll(()=>Boolean(heldRequest)).toBe(true);expect(heldJob.job_id).toBe(a1);
  await closeDelivery();await openProject(B);await open();await expect(selected()).toHaveCount(0);await expect(cancel()).toHaveCount(0);const b0=await createJob(B);storage.push({stage:'B own running while A cancel held',value:await scopedStorage()});expect(storage.at(-1).value).toEqual({[A.key]:a1,[B.key]:b0});await expect(selected()).not.toContainText(a1);await shot('optimization-B-exact-running-no-stale-A');
  // This is an explicit controlled terminal poll record, not an observed
  // worker outcome. It permits a new actual UI submit when A is reopened.
  A.jobs.set(a1,{...A.jobs.get(a1),status:'failed',error:'controlled old A terminal poll before fresh submit'});
  await closeDelivery();await openProject(A);await open();await expect(selected()).toContainText(a1);await expect(panel().getByRole('status')).toContainText('failed');await expect(cancel()).toHaveCount(0);const a2=await createJob(A);expect(a2).not.toBe(a1);storage.push({stage:'A fresh new job before old response',value:await scopedStorage()});expect(storage.at(-1).value).toEqual({[A.key]:a2,[B.key]:b0});
  heldDeadline=performance.now()+10_000;releaseHeld!();const originalHeldSettlement=heldSettled!;await withinHeld('original held fulfill settlement',()=>originalHeldSettlement,completedAt=>{heldSettlementJoin={promise:originalHeldSettlement,completed_monotonic_ms:completedAt};});heldOutcome=await withinHeld('original request finished or failed',()=>heldDisposition);
  if(heldOutcome.kind==='finished'){expect(heldHandlerError).toBeUndefined();const response=await withinHeld('original request response',()=>heldRequest!.response());expect(response).not.toBeNull();expect(response!.status()).toBe(200);expect(await withinHeld('original response JSON',()=>response!.json())).toMatchObject({job_id:a1,status:'cancelled',options:{package_dir:A.packagePath,input_receipt:{source_dataset_path:A.source}}});expect(await withinHeld('original response finished',()=>response!.finished())).toBeNull();}else{expect(heldOutcome.failure).toMatch(/ABORT|cancel/i);expect(heldHandlerError).toBeTruthy();}
  await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))));await expect(selected()).toContainText(a2);await expect(selected()).not.toContainText(a1);await expect(panel().getByRole('status')).toContainText('running');await expect(cancel()).toBeEnabled();expect(await scopedStorage()).toEqual({[A.key]:a2,[B.key]:b0});expect(packageInputs()).toEqual(originalInputs);await shot('optimization-A-reentered-fresh-job-old-cancel-fenced');
  await cancel().click();await expect(panel().getByRole('status')).toContainText('cancelled');await expect(cancel()).toHaveCount(0);expect(wire.filter(row=>row.method==='POST'&&row.path.endsWith('/cancel')).map(row=>row.path)).toEqual([a0,a0,a1,a2].map(id=>'/api/export/flow/optimization-jobs/'+id+'/cancel'));expect(B.jobs.get(b0).status).toBe('running');
  await page.reload();await open();await expect(selected()).toContainText(a2);await expect(selected()).not.toContainText(a1);await expect(panel().getByRole('status')).toContainText('cancelled');await expect(cancel()).toHaveCount(0);await expect(panel().getByRole('button',{name:'검토 후 새 승인 패키지 생성',exact:true})).toHaveCount(0);storage.push({stage:'A exact current cancel reopened',value:await scopedStorage()});expect(storage.at(-1).value).toEqual({[A.key]:a2,[B.key]:b0});await shot('optimization-exact-fresh-cancel-reopened-no-worker-claim');
  const all=await Promise.allSettled([...pending]);for(const result of all)if(result.status==='rejected')throw result.reason;
  const badResponses=wire.filter(row=>row.status>=400);expect(badResponses).toHaveLength(1);expect(badResponses[0]).toMatchObject({method:'POST',path:'/api/export/flow/optimization-jobs/'+a0+'/cancel',project_id:A.project.id,status:503,response_body:{detail:'controlled exact owner cancel unavailable'},finished:true});
  expect(consoles).toHaveLength(1);expect(consoles[0].text).toMatch(/503/);expect(new URL(consoles[0].location.url).origin).toBe(origin);expect(new URL(consoles[0].location.url).pathname).toBe('/api/export/flow/optimization-jobs/'+a0+'/cancel');expect(pageErrors).toEqual([]);expect(reviewRequests).toBe(0);expect(packageInputs()).toEqual(originalInputs);const allowedMutationPaths=new Set(['/api/project/open','/api/export/flow/optimize',...owners.map(owner=>'/api/product-delivery/packages/'+owner.row.package_id+'/select'),...[a0,a1,a2].map(id=>'/api/export/flow/optimization-jobs/'+id+'/cancel')]);expect(ownedMutations.filter(row=>row.method!=='POST'||!allowedMutationPaths.has(row.path))).toEqual([]);expect(ownedMutations.filter(row=>row.path==='/api/export/flow/optimize')).toHaveLength(4);expect(ownedMutations.filter(row=>row.path==='/api/project/open').map(row=>row.body)).toEqual([{project_dir:B.project.project_dir},{project_dir:A.project.project_dir}]);
 }catch(error){primary=error;throw error;}finally{
  if(releaseHeld){if(heldDeadline===undefined)heldDeadline=performance.now()+10_000;releaseHeld();}
  if(heldSettled&&heldSettlementJoin?.promise!==heldSettled)try{const originalPendingHeldSettlement=heldSettled;await withinHeld('original held cleanup settlement under unchanged deadline',()=>originalPendingHeldSettlement,completedAt=>{heldSettlementJoin={promise:originalPendingHeldSettlement,completed_monotonic_ms:completedAt};});}catch(error){secondary.push({operation:'original held route cleanup settlement',error:String(error)});}
  const remaining=await Promise.allSettled([...pending]);for(const result of remaining)if(result.status==='rejected')secondary.push({operation:'original owned response full-body observation',error:String(result.reason)});
  evidence.note('optimization_exact_cancel_controls',{record_id:'F099',action:'optimization-exact-job-cancel',dimensions:['empty','error','handoff'],actual_renderer_contract:true,controlled_HTTP_responses:true,controlled_old_A_terminal_poll:true,old_receipt_disposition:heldOutcome??null,held_deadline_monotonic_ms:heldDeadline??null,held_original_budget_ms:10000,original_held_settlement_join:heldSettlementJoin?{exact_current_original_promise:heldSettlementJoin.promise===heldSettled,completed_monotonic_ms:heldSettlementJoin.completed_monotonic_ms}:null,old_handler_error:heldHandlerError?String(heldHandlerError):null,setup,wires:wire,controlled,owned_mutations:ownedMutations,storage,package_inputs_before:originalInputs,package_inputs_after:packageInputs(),intentional_console_errors:consoles,page_errors:pageErrors,review_requests:reviewRequests,first_exception:primary?String(primary):null,secondary,actual_conversion:false,actual_worker_cancellation:false,actual_model_inference:false,training:false,GPU:false,native_electron:false,quality_accepted:false,parent_credit:0});
  page.off('request',observeRequest);page.off('response',observeResponse);page.off('requestfinished',finished);page.off('requestfailed',failed);page.off('console',onConsole);page.off('pageerror',onPageError);
  if(primary===undefined&&secondary.length)throw Error(JSON.stringify(secondary));
 }
});
