import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page,Request,Route,Response,Locator} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {handoffApi} from './fixtures/remaining-project-handoff';
import {png} from './qa/appFlow';
const harness=require('./fixtures/harness.cjs');
const hash=(bytes:Buffer|string)=>crypto.createHash('sha256').update(bytes).digest('hex');
test.use({actionTimeout:10_000});

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

const CELLS=[
 '/records/138/actions/0/scenarios/empty','/records/138/actions/0/scenarios/invalid',
 '/records/138/actions/0/scenarios/error','/records/138/actions/0/scenarios/reopen',
 '/records/138/actions/1/scenarios/empty','/records/138/actions/1/scenarios/invalid',
 '/records/138/actions/1/scenarios/error',
] as const;

async function exercise(page:Page,w:Workspace,e:Evidence,native:boolean,origin:string,url?:string){
 const api=handoffApi(page,origin,native),custody:SplitControlCustody={opened:0,closed:0,uncertain:[]};
 const source=path.join(w.root,'u016-seven-controlled-source');fs.mkdirSync(source);
 const inputs=['error','disagreement','threshold'].map((name,index)=>{const file=path.join(source,name+'.png');fs.writeFileSync(file,png(64,3,(x,y)=>[x,y,50+index]),{flag:'wx'});return {name,file,sha256:hash(fs.readFileSync(file))};});
 const created=await api('/api/project/create',{name:'U016 owned seven saved-review controls',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 const project=await api('/api/project/current');expect(project.id).toBe(created.id);expect(project.source_dataset_dir).toBe(source);
 expect(path.dirname(project.project_dir)).toBe(native?path.join(w.userData,'projects'):w.projects);
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/review_queue_reports.py'),w.root,project.project_dir,source],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
 expect(fixture.controlled_reports_not_model_inference).toBe(true);expect(splitControlFile(fixture.path,custody).sha256).toBe(fixture.sha256);
 const queue=await api('/api/data-workbench/review-queues',{evaluation_id:fixture.record.evaluation_id,threshold:.5,margin:.05});
 expect(queue.items.map((row:any)=>[row.relative_path,row.priority,row.state])).toEqual([['error.png',400,'pending'],['disagreement.png',200,'pending'],['threshold.png',100,'pending']]);
 expect(queue).toMatchObject({cursor:0,revision:1,history:[],origin:{evaluation_id:fixture.record.evaluation_id}});
 const queuePath='/api/data-workbench/review-queues/'+queue.id;
 const params=new URLSearchParams({source_dataset_path:source,task:'segmentation'}).toString();
 for(const route of ['/api/project/preferences','/api/project/labelsets','/api/team-data','/api/team-data/readiness','/api/model-deployments/active?'+params,'/api/model-deployments/history?'+params,'/api/training/jobs'])await api(route);
 const metadata=(await api('/api/dataset/metadata?limit=100')).items;expect(metadata).toHaveLength(3);
 for(const input of inputs){const image=metadata.find((row:any)=>row.file_path===input.file);expect(image.content_hash).toBe(input.sha256);expect(image.workflow_state).not.toBe('approved');expect((await api('/api/annotations/'+input.name+'?file_path='+encodeURIComponent(input.file))).annotations).toEqual([]);}
 const panel=page.getByRole('region',{name:'저장된 검토 큐',exact:true}),stages=page.getByRole('navigation',{name:'Workflow Stages'});
 const choice=panel.getByRole('combobox',{name:'저장 검토 큐 선택',exact:true});
 const current=panel.getByRole('button',{name:'현재 항목 열기',exact:true}),returnOrigin=panel.getByRole('button',{name:'원래 평가·비교로 돌아가기',exact:true});
 const reviewNext=panel.getByRole('button',{name:'검토 완료 · 다음',exact:true});
 const enter=async()=>{await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);await stages.getByRole('button').nth(1).click();const focus=page.getByRole('button',{name:'집중 편집',exact:true});if(await focus.getAttribute('aria-pressed')==='true')await focus.click();const summary=page.getByText('저장 검토 큐 · 오류·불일치·임계값 우선',{exact:true});if(await summary.locator('..').getAttribute('open')===null)await summary.click();await expect(panel).toBeVisible();await expect(choice).toHaveValue(queue.id);};
 const mount=async()=>{if(url)await page.goto(url);else await page.reload();await enter();await expect(panel).toContainText('검토 진행 0 / 3');};
 const protectedState=()=>({source:splitControlTree(source,custody),annotations:splitControlTree(project.annotations_dir,custody),models:splitControlTree(project.models_dir,custody),reports:splitControlTree(project.reports_dir,custody)});
 const durable=async()=>({queue:await api(queuePath),list:await api('/api/data-workbench/review-queues'),jobs:await api('/api/training/jobs'),deployment:await api('/api/model-deployments/active?'+params)});
 const origins=()=>page.evaluate(()=>Object.fromEntries(Object.entries(localStorage).filter(([key])=>key.startsWith('modu-evaluation-return:')||key.startsWith('modu-comparison-return:'))));
 const captures:Array<any>=[],writes:Array<any>=[],pageErrors:string[]=[],controls:Array<{close:()=>Promise<void>}>=[],proofs:Record<string,any>={};
 let ownQueueReads=0;
 const observer=(request:Request)=>{const address=new URL(request.url());if(address.origin!==origin)return;if(request.method()==='GET'&&request.frame()===page.mainFrame()&&address.pathname===queuePath&&address.search==='')ownQueueReads++;if(address.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(request.method()))writes.push({method:request.method(),path:address.pathname,body:request.postData()});};
 const errorObserver=(error:Error)=>pageErrors.push(String(error));
 const originalGet=async(button:Locator,label:string,status:number)=>{
  const deadline=performance.now()+10_000;
  const waiting=page.waitForResponse(response=>{const request=response.request(),address=new URL(response.url());return request.method()==='GET'&&request.frame()===page.mainFrame()&&address.origin===origin&&address.pathname===queuePath&&address.search==='';},{timeout:10_000});
  await splitControlWithin(button.click(),deadline,'U016 owning '+label+' click');const response=await splitControlWithin(waiting,deadline,'U016 original '+label+' response');
  expect(response.status()).toBe(status);const bytes=await splitControlWithin(response.body(),deadline,'U016 original '+label+' fullbody');expect(await splitControlWithin(response.finished(),deadline,'U016 original '+label+' completion')).toBeNull();
  const file=path.join(w.logs,'u016-'+String(captures.length)+'-'+label+'-actual-body.json');fs.writeFileSync(file,bytes,{flag:'wx'});e.addFile(file);
  const row={label,request:response.request(),body:JSON.parse(bytes.toString('utf8')),method:'GET',origin,path:queuePath,main_frame:true,status,sha256:hash(bytes),size:bytes.length,file,finished:true};captures.push(row);return row;
 };
 const controlled503=async(label:string)=>{
  const pattern=origin+queuePath,body=JSON.stringify({detail:'U016 controlled owning '+label+' GET unavailable'});
  let owned:Request|undefined,ownedRoute:Route|undefined,error:unknown,done=false,terminal=false,count=0;
  let resolveDone!:()=>void,resolveTerminal!:()=>void;const completed=new Promise<void>(resolve=>resolveDone=resolve),disposition=new Promise<void>(resolve=>resolveTerminal=resolve);
  const ended=(request:Request)=>{if(request===owned){terminal=true;resolveTerminal();}};
  page.on('requestfinished',ended);page.on('requestfailed',ended);
  const handler=async(route:Route)=>{const request=route.request(),address=new URL(request.url());
   if(count!==0||request.method()!=='GET'||request.frame()!==page.mainFrame()||address.origin!==origin||address.pathname!==queuePath||address.search!==''){await route.continue();return;}
   count++;owned=request;ownedRoute=route;const deadline=performance.now()+10_000;
   try{await splitControlWithin(route.fulfill({status:503,contentType:'application/json',body}),deadline,'U016 disclosed owning503');}
   catch(cause){error=cause;try{await splitControlWithin(route.abort('aborted'),deadline,'U016 failed handler owned abort');}catch{/* Preserve first handler error. */}}
   finally{done=true;resolveDone();}
  };
  await page.route(pattern,handler);
  const close=async()=>{const deadline=performance.now()+10_000;let first=error;
   try{if(owned){await splitControlWithin(completed,deadline,'U016 handler join');if(!terminal&&ownedRoute)await splitControlWithin(ownedRoute.abort('aborted'),deadline,'U016 owned unresolved route abort');await splitControlWithin(disposition,deadline,'U016 original request disposition');expect(done&&terminal).toBe(true);}await splitControlWithin(page.unroute(pattern,handler),deadline,'U016 owning route removal');}
   catch(cause){if(first===undefined)first=cause;}
   finally{page.off('requestfinished',ended);page.off('requestfailed',ended);}
   if(first!==undefined)throw first;expect(count).toBe(1);
  };
  const result={close,body,count:()=>count,request:()=>owned};controls.push(result);return result;
 };
 let primary:unknown,baseline:ReturnType<typeof protectedState>|undefined,baselineDurable:any,changed=false,originalBytes:Buffer|undefined;
 const replaceOwnedReport=(bytes:Buffer)=>{const file=fixture.path,pin=splitControlFile(file,custody);expect(path.dirname(file)).toBe(path.join(project.reports_dir,'evaluations'));let fd:number|undefined,first:unknown;
  try{fd=fs.openSync(file,fs.constants.O_WRONLY|fs.constants.O_NOFOLLOW);custody.opened++;expect(splitControlRaw(fs.fstatSync(fd,{bigint:true}))).toEqual(pin.raw);fs.writeFileSync(fd,bytes);fs.ftruncateSync(fd,bytes.length);}
  catch(cause){first=cause;}
  finally{if(fd!==undefined){const own=fd;fd=undefined;try{fs.closeSync(own);custody.closed++;}catch(cause){custody.uncertain.push({file,fd:own,error:String(cause)});if(first===undefined)first=cause;}}}
  if(first!==undefined)throw first;expect(splitControlFile(file,custody).sha256).toBe(hash(bytes));
 };
 try{
  await mount();await expect(current).toBeEnabled();await expect(returnOrigin).toBeEnabled();
  // One actual successful image open initializes original label reads before
  // the measured failures. It grants no new already-verified success credit.
  const initialized=await originalGet(current,'initial-open',200);expect(initialized.body).toEqual(queue);await expect(reviewNext).toBeEnabled();
  baselineDurable=await durable();expect(baselineDurable.queue).toEqual(queue);expect(baselineDurable.jobs.jobs).toEqual([]);baseline=protectedState();const baselineOrigins=await origins();
  page.on('request',observer);page.on('pageerror',errorObserver);
  // Genuine blank option, no invented ID or DOM mutation.
  const emptyReadStart=ownQueueReads;await choice.selectOption('');await expect(choice).toHaveValue('');await expect(current).toHaveCount(0);await expect(returnOrigin).toHaveCount(0);expect(ownQueueReads).toBe(emptyReadStart);
  expect(protectedState()).toEqual(baseline);expect(await durable()).toEqual(baselineDurable);expect(await origins()).toEqual(baselineOrigins);expect(writes).toEqual([]);
  proofs.current_empty={pointer:CELLS[0],real_blank_option:true,button_absent:true,action_GETs:0};proofs.origin_empty={pointer:CELLS[4],real_blank_option:true,button_absent:true,action_GETs:0};await e.screenshot(page,'u016-empty-current-and-origin-absent');
  await choice.selectOption(queue.id);await expect(current).toBeEnabled();await expect(returnOrigin).toBeEnabled();
  for(const [button,label,pointer] of [[current,'current-error',CELLS[2]],[returnOrigin,'origin-error',CELLS[6]]] as const){
   const beforeOrigins=await origins(),control=await controlled503(label);let row:any,first:unknown;
   try{row=await originalGet(button,label,503);expect(row.request).toBe(control.request());expect(JSON.stringify(row.body)).toBe(control.body);await expect(panel.getByRole('alert')).toContainText(row.body.detail);await expect(panel.getByRole('status')).toHaveCount(0);await expect(panel).toBeVisible();await expect(current).toBeEnabled();await expect(returnOrigin).toBeEnabled();await expect(choice).toHaveValue(queue.id);expect(await origins()).toEqual(beforeOrigins);expect(protectedState()).toEqual(baseline);expect(writes).toEqual([]);await e.screenshot(page,'u016-'+label+'-owning503-preserved');}
   catch(cause){first=cause;}
   finally{try{await control.close();controls.splice(controls.indexOf(control),1);}catch(cause){if(first===undefined)first=cause;}}
   if(first!==undefined)throw first;expect(control.count()).toBe(1);expect(await durable()).toEqual(baselineDurable);
   // Deliberate fresh original200 image-open verifies that the actual owning
   // route is gone and error state clears without queue cursor advancement.
   const recovery=await originalGet(current,label+'-recovery',200);expect(recovery.request).not.toBe(row.request);expect(recovery.body).toEqual(queue);await expect(panel.getByRole('alert')).toHaveCount(0);await expect(reviewNext).toBeEnabled();expect(protectedState()).toEqual(baseline);expect(await origins()).toEqual(beforeOrigins);
   proofs[label]={pointer,controlled_transport503:true,original_request:row.sha256,real_original_recovery200:recovery.sha256,queue_cursor_revision_history_unchanged:true,origin_navigation_not_committed:true};
  }
  // Current-open.reopen is independent: real document reload, restored saved
  // selection and another original same-queue GET followed by exact image UI.
  await page.reload();await enter();await expect(choice).toHaveValue(queue.id);await expect(current).toBeEnabled();
  const reopened=await originalGet(current,'document-reopen-current',200);expect(captures.slice(0,-1).some(row=>row.request===reopened.request)).toBe(false);expect(reopened.body).toEqual(queue);await expect(reviewNext).toBeEnabled();expect(await durable()).toEqual(baselineDurable);expect(protectedState()).toEqual(baseline);expect(await origins()).toEqual(baselineOrigins);
  proofs.current_reopen={pointer:CELLS[3],real_document_reload:true,same_project_id:project.id,same_queue_id:queue.id,same_cursor_revision_history:true,current_item_file:queue.items[0].file_path,original_GET200:reopened.sha256};await e.screenshot(page,'u016-real-document-current-open-reopened');
  // Change only the controlled fixture origin, retaining a valid production
  // evidence digest. The queue keeps its original origin digest, so the real
  // backend validator must mark it stale. Source images/labels stay untouched.
  originalBytes=splitControlFile(fixture.path,custody).bytes;
  const derivative=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/review_queue_stale_origin.py'),w.root,fixture.path,fixture.sha256],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
  expect(derivative.controlled_origin_only_not_model_inference).toBe(true);expect(derivative.files_written).toBe(0);expect(derivative.original_file_sha256).toBe(fixture.sha256);expect(derivative.original_evidence_sha256).toBe(queue.origin.evidence_sha256);
  const changedBytes=Buffer.from(derivative.body_base64,'base64');expect(hash(changedBytes)).toBe(derivative.changed_file_sha256);expect(hash(changedBytes)).not.toBe(fixture.sha256);
  const changedRecord=JSON.parse(changedBytes.toString('utf8'));expect(changedRecord.binding.fixture_origin_revision).toBe(2);delete changedRecord.binding.fixture_origin_revision;changedRecord.evidence_sha256=fixture.record.evidence_sha256;expect(changedRecord).toEqual(fixture.record);
  const invalidReadStart=ownQueueReads;changed=true;replaceOwnedReport(changedBytes);const changedReports=splitControlTree(project.reports_dir,custody),protectedInvalid={source:baseline.source,annotations:baseline.annotations,models:baseline.models};
  const invalidDeadline=performance.now()+10_000;
  const waitingInvalid=page.waitForResponse(response=>{const request=response.request(),address=new URL(response.url());return request.method()==='GET'&&request.frame()===page.mainFrame()&&address.origin===origin&&address.pathname==='/api/data-workbench/review-queues'&&address.search==='';},{timeout:10_000});
  await splitControlWithin(page.reload(),invalidDeadline,'U016 real stale origin document mount');await splitControlWithin(enter(),invalidDeadline,'U016 real stale queue restored');
  const invalidResponse=await splitControlWithin(waitingInvalid,invalidDeadline,'U016 original UI stale queue list');expect(invalidResponse.status()).toBe(200);
  const invalidBytes=await splitControlWithin(invalidResponse.body(),invalidDeadline,'U016 original stale list fullbody');expect(await splitControlWithin(invalidResponse.finished(),invalidDeadline,'U016 original stale list completion')).toBeNull();
  const invalidList=JSON.parse(invalidBytes.toString('utf8'));const invalidFile=path.join(w.logs,'u016-'+String(captures.length)+'-real-stale-list-actual-body.json');fs.writeFileSync(invalidFile,invalidBytes,{flag:'wx'});e.addFile(invalidFile);
  captures.push({label:'real-stale-list',request:invalidResponse.request(),body:invalidList,method:'GET',origin,path:'/api/data-workbench/review-queues',main_frame:true,status:200,sha256:hash(invalidBytes),size:invalidBytes.length,file:invalidFile,finished:true});
  await expect(panel).toContainText('원본 변경으로 재생성 필요');await expect(panel.getByRole('alert')).toContainText('Saved review origin changed');await expect(current).toBeDisabled();await expect(returnOrigin).toBeDisabled();await expect(choice).toHaveValue(queue.id);
  expect(invalidList.queues).toHaveLength(1);expect(invalidList.queues[0]).toMatchObject({...queue,stale:true});expect(invalidList.queues[0].error).toContain('Saved review origin changed');expect(await api('/api/data-workbench/review-queues')).toEqual(invalidList);
  expect(ownQueueReads).toBe(invalidReadStart);expect(splitControlTree(project.reports_dir,custody)).toEqual(changedReports);expect({source:splitControlTree(source,custody),annotations:splitControlTree(project.annotations_dir,custody),models:splitControlTree(project.models_dir,custody)}).toEqual(protectedInvalid);expect(writes).toEqual([]);expect(await origins()).toEqual(baselineOrigins);
  proofs.current_invalid={pointer:CELLS[1],real_backend_stale_origin:true,original_UI_queue_list200_sha256:hash(invalidBytes),button_disabled:true,action_GETs:0,origin_before_sha256:fixture.sha256,origin_changed_sha256:hash(changedBytes)};proofs.origin_invalid={pointer:CELLS[5],real_backend_stale_origin:true,original_UI_queue_list200_sha256:hash(invalidBytes),button_disabled:true,action_GETs:0};await e.screenshot(page,'u016-real-stale-origin-current-and-origin-disabled');
  replaceOwnedReport(originalBytes);expect(splitControlFile(fixture.path,custody).sha256).toBe(fixture.sha256);changed=false;await page.reload();await enter();await expect(panel).not.toContainText('원본 변경으로 재생성 필요');await expect(panel.getByRole('alert')).toHaveCount(0);await expect(current).toBeEnabled();await expect(returnOrigin).toBeEnabled();
  expect(await durable()).toEqual(baselineDurable);expect(splitControlFile(fixture.path,custody).sha256).toBe(fixture.sha256);for(const input of inputs)expect(splitControlFile(input.file,custody).sha256).toBe(input.sha256);
  expect({source:splitControlTree(source,custody),annotations:splitControlTree(project.annotations_dir,custody),models:splitControlTree(project.models_dir,custody)}).toEqual(protectedInvalid);
  const afterReports=splitControlTree(project.reports_dir,custody),changedMember=path.relative(project.reports_dir,fixture.path).split(path.sep).join('/');expect(Object.keys(afterReports)).toEqual(Object.keys(baseline.reports));for(const member of Object.keys(afterReports))if(member!==changedMember)expect(afterReports[member]).toEqual(baseline.reports[member]);
  expect(writes).toEqual([]);expect(pageErrors).toEqual([]);
  e.note('saved_review_seven_controls',{selected_pending_pointers:CELLS,cells:proofs,project,inputs,fixture,queue,protected_before:baseline,protected_after:protectedState(),durable_before:baselineDurable,durable_after:await durable(),controlled_report_original_raw:baseline.reports[changedMember],controlled_report_restored_raw:afterReports[changedMember],original_body_captures:captures.map(({request,...row})=>row),writes,pageErrors,controlled_origin_derivative:derivative,one_owned_controlled_report_changed_then_restored:true,source_annotations_models_full_raw_unchanged:true,original_controlled_report_bytes_restored:true,controlled_report_mtime_ctime_intentionally_changed_not_claimed_equal:true,other_report_files_full_raw_unchanged:true,no_job_inference_training_deployment:true,no_cancel_credit:true,native,installed_native_quality_human_acceptance:false});
 }catch(cause){primary=cause;}
 finally{
  for(const control of controls){try{await control.close();}catch(cause){if(primary===undefined)primary=cause;}}
  if(changed&&originalBytes){try{replaceOwnedReport(originalBytes);expect(splitControlFile(fixture.path,custody).sha256).toBe(fixture.sha256);changed=false;}catch(cause){if(primary===undefined)primary=cause;}}
  page.off('request',observer);page.off('pageerror',errorObserver);
  try{expect(custody.opened).toBe(custody.closed);expect(custody.uncertain).toEqual([]);expect(changed).toBe(false);}catch(cause){if(primary===undefined)primary=cause;}
  try{e.note('saved_review_seven_closure',{owned_FDs_opened:custody.opened,owned_FDs_closed:custody.closed,uncertain:custody.uncertain,controlled_route_count:controls.length,fixture_report_restored:!changed,first_exception:primary===undefined?null:String(primary)});}catch(cause){if(primary===undefined)primary=cause;}
 }
 if(primary!==undefined)throw primary;
}

test('saved review seven current and origin controls preserve owned queue through empty stale-origin error and reopen',async({page,renderer,workspace,evidence})=>{await installDesktopHostShim(page,renderer.port);await exercise(page,workspace,evidence,false,renderer.origin,renderer.url);});
test('native saved review seven current and origin controls preserve owned originals through empty stale-origin error and reopen',{tag:'@electron'},async({electronSession,workspace,evidence})=>{expect(process.env.MV_E2E_NATIVE_FULL_BODY_READS).toBe('1');const backend=await electronSession.waitForBackend();await exercise(electronSession.window,workspace,evidence,true,`http://127.0.0.1:${backend.port}`);});
