import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
const harness=require('./fixtures/harness.cjs');
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(file:string)=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');


// U015 two remaining controls. No cancellation, invalid input, model or training is credited.
import type {Response as QueueEmptyResponse} from '@playwright/test';
import {handoffApi} from './fixtures/remaining-project-handoff';
test.use({actionTimeout:10_000});
const hash=(bytes:Buffer|string)=>crypto.createHash('sha256').update(bytes).digest('hex');
type SplitControlRequest=import('@playwright/test').Request;
type SplitControlRow={image:string;text:string;split:'train'|'val'|'test'};
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
  // SQLite's -shm file is its shared read index: a read-only reader of a WAL database updates it (the step-4
  // evidence re-read opens model_deployments.sqlite3). It stays listed; data writes show in the database and -wal.
  else if(file.endsWith('.sqlite3-shm')){expect(first.isFile()).toBe(true);rows[relative]={kind:'sqlite_read_index'};}
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

const QUEUE_REMAINING_POINTERS=[
 '/records/137/actions/8/scenarios/error',
 '/records/137/actions/7/scenarios/reopen',
] as const;
type QueueRemainingWire={request:SplitControlRequest;started:number;deadline:number;complete:boolean;status?:number;sha256?:string;controlled?:boolean;failure?:string;failed:boolean;error?:unknown;owned?:Promise<void>;responseAt?:number;bodyAt?:number;finishedAt?:number};
async function queueRemainingExercise(page:Page,w:Workspace,e:Evidence,native:boolean,origin:string,url?:string){
 const api:Api=handoffApi(page,origin,native),custody:SplitControlCustody={opened:0,closed:0,uncertain:[]};
 const source=path.join(w.root,'queue-remaining-source');fs.mkdirSync(source);
 const inputs=['error','disagreement','threshold'].map((name,index)=>{const file=path.join(source,name+'.png');fs.writeFileSync(file,png(256,3,(x,y)=>[x,y,50+index]),{flag:'wx'});return {name,file,sha256:hash(fs.readFileSync(file))};});
 const created=await api('/api/project/create',{name:'Owned remaining queue controls',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 const project=await api('/api/project/current'),owner=native?path.join(w.userData,'projects'):w.projects;
 expect(project.id).toBe(created.id);expect(project.source_dataset_dir).toBe(source);expect(path.dirname(project.project_dir)).toBe(owner);expect(fs.realpathSync(source)).toBe(source);expect(fs.realpathSync(project.project_dir)).toBe(project.project_dir);
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/review_queue_reports.py'),w.root,project.project_dir,source],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
 expect(fixture.controlled_reports_not_model_inference).toBe(true);expect(splitControlFile(fixture.path,custody).sha256).toBe(fixture.sha256);
 const queue=await api('/api/data-workbench/review-queues',{evaluation_id:fixture.record.evaluation_id,threshold:.5,margin:.05});
 expect(queue.items.map((row:any)=>[row.relative_path,row.priority,row.state])).toEqual([['error.png',400,'pending'],['disagreement.png',200,'pending'],['threshold.png',100,'pending']]);expect(queue).toMatchObject({cursor:0,revision:1,history:[],origin:{evaluation_id:fixture.record.evaluation_id}});
 const team=await api('/api/team-data');expect(team.settings.editing_enabled).toBe(false);
 await api('/api/team-data/settings',{expected_revision:team.settings.revision,actor:'owned-U015-remaining-fixture',changes:{review_enabled:true,approved_only_training:true}},'PUT');
 // Original GET constructors materialize only owned default files before custody starts.
 const params=new URLSearchParams({source_dataset_path:source,task:'segmentation'}).toString();
 for(const endpoint of ['/api/model-deployments/active?'+new URLSearchParams({source_dataset_path:source,task:'ocr'}),'/api/model-deployments/active?'+params,'/api/model-deployments/history?'+params,'/api/fleet/targets','/api/fleet/capabilities','/api/fleet/rollouts','/api/runtime-services/capture-groups','/api/runtime-services'])await api(endpoint,undefined,'GET');
 const queuePath='/api/data-workbench/review-queues/'+queue.id;
 const metadata=(await api('/api/dataset/metadata?limit=100')).items;expect(metadata).toHaveLength(3);
 for(const input of inputs){const image=metadata.find((row:any)=>row.file_path===input.file);expect(image).toBeTruthy();expect(image.content_hash).toBe(input.sha256);expect(image.workflow_state).not.toBe('approved');await api('/api/team-data/images/'+image.image_uuid);const annotation=await api('/api/annotations/'+input.name+'?file_path='+encodeURIComponent(input.file));expect(annotation.annotations).toEqual([]);}
 const endpoints=['/api/project/current','/api/project/preferences','/api/project/labelsets','/api/team-data','/api/team-data/readiness','/api/dataset/versions','/api/dataset/metadata?limit=100','/api/dataset/metadata/split','/api/data-workbench/review-evaluations','/api/data-workbench/review-queues',queuePath,'/api/training/jobs'];
 const readState=async()=>{const result:Record<string,any>={};for(const endpoint of endpoints)result[endpoint]=await api(endpoint,undefined,'GET');for(const input of inputs)result['annotation:'+input.file]=await api('/api/annotations/'+input.name+'?file_path='+encodeURIComponent(input.file));return result;};
 await readState();

 const reads:QueueRemainingWire[]=[],writes:Array<{method:string;path:string;body:string|null}>=[],trainingRequests:string[]=[];
 const ownedFailures=new Set<SplitControlRequest>(),handlerFaults:unknown[]=[],handlerTasks:Promise<void>[]=[];
 const controlledMessage='Controlled owning U015 source evaluation GET failure',controlledBody=JSON.stringify({detail:controlledMessage});
 const failureURL=origin+'/api/data-workbench/review-evaluations';let controlledCount=0,routeInstalled=false,routeRemoved=true;
 const observe=(request:SplitControlRequest)=>{const u=new URL(request.url());if(u.origin!==origin||!u.pathname.startsWith('/api/'))return;
  if(request.method()==='GET'){const started=performance.now(),row:QueueRemainingWire={request,started,deadline:started+10_000,complete:false,failed:false};reads.push(row);
   // Begin the same owned response/body/finish chain at the request event.
   // Every await retains this original event's deadline; navigation cannot defer body acquisition.
   row.owned=(async()=>{const response=await splitControlWithin(request.response(),row.deadline,'owned API GET response');expect(response).not.toBeNull();row.responseAt=performance.now();
    const controlled=ownedFailures.has(request);expect(response!.status()).toBe(controlled?503:200);const bytes=await splitControlWithin(response!.body(),row.deadline,'GET whole bytes');row.bodyAt=performance.now();expect(await splitControlWithin(response!.finished(),row.deadline,'GET completion')).toBeNull();row.finishedAt=performance.now();if(controlled)expect(bytes.toString('utf8')).toBe(controlledBody);
    row.complete=true;row.controlled=controlled;row.status=response!.status();row.sha256=hash(bytes);
   })().catch(cause=>{row.failed=true;row.error=cause;try{row.failure=String(cause);}catch{row.failure='<exception rendering failed>';}});
  }
  else if(!['HEAD','OPTIONS'].includes(request.method())){writes.push({method:request.method(),path:u.pathname,body:request.postData()});if(/\/(train|training|jobs|evaluate|predict)(\/|$)/.test(u.pathname))trainingRequests.push(u.pathname);}
 };
 const settleOriginalReads=async()=>{let first:unknown,failed=false;for(const row of reads){try{expect(row.owned).toBeTruthy();await row.owned;if(row.failed)throw row.error;expect(row.complete).toBe(true);}catch(cause){if(!failed){failed=true;first=cause;}}}if(failed)throw first;};
 const navigationBarriers:Array<{label:string;at:number;reads:Array<{started:number;deadline:number;responseAt:number;bodyAt:number;finishedAt:number;sha256:string}>}>=[];
 const beforeNavigation=async(label:string)=>{await settleOriginalReads();const at=performance.now();
  const settled=reads.map(row=>{expect(row.complete).toBe(true);expect(row.failed).toBe(false);expect(row.responseAt).toBeLessThanOrEqual(row.bodyAt!);expect(row.bodyAt).toBeLessThanOrEqual(row.finishedAt!);expect(row.finishedAt).toBeLessThanOrEqual(at);expect(row.finishedAt).toBeLessThan(row.deadline);expect(row.sha256).toMatch(/^[0-9a-f]{64}$/);return {started:row.started,deadline:row.deadline,responseAt:row.responseAt!,bodyAt:row.bodyAt!,finishedAt:row.finishedAt!,sha256:row.sha256!};});
  navigationBarriers.push({label,at,reads:settled});
 };
 const treeState=()=>({source:splitControlTree(source,custody),project:splitControlTree(project.project_dir,custody)});
 const panel=page.getByRole('region',{name:'저장된 검토 큐',exact:true}),stages=page.getByRole('navigation',{name:'Workflow Stages'});
 const choice=panel.getByRole('combobox',{name:'저장 검토 큐 선택',exact:true}),evaluation=panel.getByRole('combobox',{name:'검토 큐 원본 평가',exact:true});
 const prepare=panel.getByRole('button',{name:'수정·검수 데이터로 학습 준비',exact:true}),create=panel.getByRole('button',{name:'우선순위 큐 저장',exact:true});
 const openPanel=async()=>{await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);await stages.getByRole('button').nth(1).click();const focus=page.getByRole('button',{name:'집중 편집',exact:true});if(await focus.getAttribute('aria-pressed')==='true')await focus.click();const summary=page.getByText('저장 검토 큐 · 오류·불일치·임계값 우선',{exact:true});if(await summary.locator('..').getAttribute('open')===null)await summary.click();await expect(panel).toBeVisible();};
 const mount=async()=>{await openPanel();await expect(choice).toHaveValue(queue.id);await expect(panel).toContainText('검토 진행 0 / 3');};
 const remount=async()=>{await beforeNavigation('own panel remount');await stages.getByRole('button').nth(0).click();await openPanel();};
 const optionValues=()=>evaluation.locator('option').evaluateAll(options=>options.map(option=>(option as HTMLOptionElement).value));
 const failEvaluation=(route:import('@playwright/test').Route)=>{const task=(async()=>{const request=route.request(),u=new URL(request.url());
  if(controlledCount!==0||request.method()!=='GET'||u.origin!==origin||u.pathname!=='/api/data-workbench/review-evaluations'||u.search!==''||request.frame()!==page.mainFrame()){await route.continue();return;}
  controlledCount++;ownedFailures.add(request);const deadline=performance.now()+10_000;await splitControlWithin(route.fulfill({status:503,contentType:'application/json',body:controlledBody}),deadline,'disclosed owning evaluation failure');
 })().catch(cause=>{handlerFaults.push(cause);});handlerTasks.push(task);return task;};
 let baseline:ReturnType<typeof treeState>|undefined,baselineState:Record<string,any>|undefined,proof:Record<string,unknown>|undefined,primary:unknown,failed=false,removed=false,completed=false;const secondary:Array<{stage:string;error:string}>=[];
 const retain=(stage:string,cause:unknown)=>{if(!failed){failed=true;primary=cause;}else secondary.push({stage,error:String(cause)});};
 const removeRoute=async()=>{if(routeInstalled){await page.unroute(failureURL,failEvaluation);routeInstalled=false;routeRemoved=true;}await Promise.all(handlerTasks);if(handlerFaults.length)throw handlerFaults[0];};
 const readinessClick=async(expected:unknown)=>{await expect(prepare).toBeEnabled();const deadline=performance.now()+10_000;
  const waiting=page.waitForResponse(response=>response.request().method()==='GET'&&new URL(response.url()).origin===origin&&new URL(response.url()).pathname==='/api/team-data/readiness'&&new URL(response.url()).search==='',{timeout:10_000});await splitControlWithin(prepare.click(),deadline,'own preparation button');const response:QueueEmptyResponse=await splitControlWithin(waiting,deadline,'own readiness response');const full=await splitControlBody(response,deadline);expect(full.body).toEqual(expected);expect(reads.filter(row=>row.request===response.request())).toHaveLength(1);await expect(panel.getByRole('alert')).toHaveText((expected as any).blockers.join(' · '));await expect(page.getByRole('region',{name:'모델 학습 허브',exact:true})).toHaveCount(0);await expect(choice).toHaveValue(queue.id);return full;};
 page.on('request',observe);
 try{
  // Ordinary document and panel mounts, complete original GETs, and defaults all precede custody.
  // This is an initialization boundary, not a promise that later reload cannot touch SQLite SHM.
  await beforeNavigation('initial document mount');if(url)await page.goto(url);else await page.reload();await mount();await settleOriginalReads();
  await remount();await expect(choice).toHaveValue(queue.id);await settleOriginalReads();
  await beforeNavigation('initial document reload');await page.reload();await mount();await settleOriginalReads();
  baselineState=await readState();const readiness=baselineState['/api/team-data/readiness'];expect(readiness.ready).toBe(false);expect(readiness.counts.total).toBe(3);expect(readiness.counts.approved).toBe(0);expect(readiness.counts.eligible).toBe(0);expect(readiness.eligible_image_uuids).toEqual([]);expect(readiness.blockers).toContain('학습에 사용할 이미지가 없습니다. 라벨 승인·미사용 상태를 확인하세요.');expect(baselineState['/api/team-data'].settings.approved_only_training).toBe(true);expect(baselineState['/api/training/jobs'].jobs).toEqual([]);expect(baselineState[queuePath]).toEqual(queue);expect(baselineState['/api/data-workbench/review-queues'].queues).toEqual([queue]);
  const baselineEvaluation=await evaluation.inputValue(),expectedOptions=['',...baselineState['/api/data-workbench/review-evaluations'].evaluations.map((row:any)=>row.id)];expect(expectedOptions).toContain(baselineEvaluation);expect(baselineEvaluation).not.toBe('');expect(await optionValues()).toEqual(expectedOptions);await expect(create).toBeEnabled();
  await settleOriginalReads();baseline=treeState();expect(writes).toEqual([]);
  const same=async()=>{await settleOriginalReads();expect(treeState()).toEqual(baseline);expect(await readState()).toEqual(baselineState);expect(writes).toEqual([]);expect(trainingRequests).toEqual([]);};
  // Disclosed, exact owning evaluation-list transport failure; saved data are never altered.
  routeInstalled=true;routeRemoved=false;await page.route(failureURL,failEvaluation);
  const failureDeadline=performance.now()+10_000,waitingFailure=page.waitForResponse(response=>ownedFailures.has(response.request()),{timeout:10_000});
  await splitControlWithin(remount(),failureDeadline,'own evaluation panel remount');const failureResponse=await splitControlWithin(waitingFailure,failureDeadline,'own evaluation503');expect(failureResponse.status()).toBe(503);const failedBytes=await splitControlWithin(failureResponse.body(),failureDeadline,'own503 wholebody');expect(failedBytes.toString('utf8')).toBe(controlledBody);expect(await splitControlWithin(failureResponse.finished(),failureDeadline,'own503 completion')).toBeNull();
  await expect(panel.getByRole('alert')).toContainText(controlledMessage);await expect(evaluation).toHaveValue('');expect(await optionValues()).toEqual(['']);await expect(create).toBeDisabled();await expect(prepare).toHaveCount(0);await expect(choice).toHaveValue('');await settleOriginalReads();expect(controlledCount).toBe(1);expect(ownedFailures.size).toBe(1);expect(treeState()).toEqual(baseline);expect(writes).toEqual([]);expect(trainingRequests).toEqual([]);await e.screenshot(page,`${native?'native':'browser'}-queue-owning-evaluation503-no-stale-actions`);
  await removeRoute();const recoverDeadline=performance.now()+10_000,waitingRecovery=page.waitForResponse(response=>response.request().method()==='GET'&&response.url()===failureURL,{timeout:10_000});await splitControlWithin(remount(),recoverDeadline,'real evaluation recovery mount');const recoveryResponse=await splitControlWithin(waitingRecovery,recoverDeadline,'real evaluation recovery200');expect(recoveryResponse.request()).not.toBe(failureResponse.request());expect(ownedFailures.has(recoveryResponse.request())).toBe(false);const recovery=await splitControlBody(recoveryResponse,recoverDeadline);expect(recovery.body).toEqual(baselineState['/api/data-workbench/review-evaluations']);await expect(choice).toHaveValue(queue.id);await expect(evaluation).toHaveValue(baselineEvaluation);expect(await optionValues()).toEqual(expectedOptions);await expect(create).toBeEnabled();await expect(prepare).toBeEnabled();await expect(panel.getByRole('alert')).toHaveCount(0);await same();await e.screenshot(page,`${native?'native':'browser'}-queue-real-evaluation200-recovery`);
  // Separate a7 document-reopen proof: actual reload, exact original readiness before and after.
  const beforeReady=await readinessClick(readiness);await same();const reopenBefore=treeState();
  await beforeNavigation('credited real document reopen');const reopenDeadline=performance.now()+10_000;await splitControlWithin(page.reload(),reopenDeadline,'real document reopen');await splitControlWithin(mount(),reopenDeadline,'same project queue restored');await settleOriginalReads();expect(treeState()).toEqual(reopenBefore);expect(await readState()).toEqual(baselineState);await expect(evaluation).toHaveValue(baselineEvaluation);expect(await optionValues()).toEqual(expectedOptions);await expect(prepare).toBeEnabled();await expect(panel.getByRole('alert')).toHaveCount(0);
  const afterReady=await readinessClick(readiness);expect(afterReady).toEqual(beforeReady);await same();await e.screenshot(page,`${native?'native':'browser'}-queue-preparation-control-real-document-reopen`);
  proof={selected_pending_pointers:QUEUE_REMAINING_POINTERS,cells:[{action:'U015.saved-queue-origin-choice',dimension:'error',disclosed_exact_owning_GET503:true,controlled_failure_count:controlledCount,own_request:{method:failureResponse.request().method(),url:failureResponse.request().url(),main_frame:failureResponse.request().frame()===page.mainFrame(),project_id:project.id,project_dir:project.project_dir,source},failure_body_sha256:hash(failedBytes),no_stale_evaluation_or_queue_actions:true,own_original_recovery200:recovery,recovery_distinct_from_controlled_request:true,exact_route_removed_before_recovery:true},{action:'U015.saved-queue-prepare-training',dimension:'reopen',real_document_reload:true,original_readiness_before:beforeReady,original_readiness_after:afterReady,same_saved_queue_and_project:true,same_blocked_policy_no_job_started:true,full_tree_before:reopenBefore,full_tree_after:treeState()}],project,inputs,fixture,original_saved_queue:queue,baseline_state:baselineState,after_state:await readState(),protected_before:baseline,protected_after:treeState(),writes,training_requests:trainingRequests,read_ledger:reads.map(row=>({url:row.request.url(),method:row.request.method(),started:row.started,deadline:row.deadline,complete:row.complete,status:row.status,sha256:row.sha256,controlled:row.controlled===true,failure:row.failure,responseAt:row.responseAt,bodyAt:row.bodyAt,finishedAt:row.finishedAt})),navigation_barriers:navigationBarriers,original_event_10s_GET_tasks_started_eagerly:true,controlled_responses_are_transport_simulation_not_backend_failures:true,real_HTTP200_recovery:true,initialization_original_GETs_completed_before_full_snapshot:true,SQLite_metadata_not_ignored:true,no_cancel_invalid_empty_handoff_or_training_credit:true,controlled_saved_report_not_model_inference:true,installed_Native_model_quality_human_parent_acceptance:false,native};completed=true;
 }catch(cause){retain('scenario',cause);}
 finally{
  try{await removeRoute();}catch(cause){retain('own route removal or handler closure',cause);}
  try{page.off('request',observe);removed=true;}catch(cause){retain('observer removal',cause);}
  try{await settleOriginalReads();expect(reads.every(row=>row.complete&&!row.failed)).toBe(true);}catch(cause){retain('owned GET closure',cause);}
  if(baseline!==undefined){try{expect(treeState()).toEqual(baseline);}catch(cause){retain('final full tree custody',cause);}}
  if(baselineState!==undefined){try{expect(await readState()).toEqual(baselineState);}catch(cause){retain('final original body custody',cause);}}
  try{expect(custody.opened).toBe(custody.closed);expect(custody.uncertain).toEqual([]);expect(routeInstalled).toBe(false);expect(routeRemoved).toBe(true);if(handlerFaults.length)throw handlerFaults[0];}catch(cause){retain('owned FD and route closure',cause);}
  try{e.note('queue_remaining_control_closure',{all_owned_FDs_closed:custody.opened===custody.closed&&custody.uncertain.length===0,FD_ledger:custody,request_observer_removed:removed,own_route_removed:routeRemoved&&!routeInstalled,all_owned_GETs_complete:reads.every(row=>row.complete&&!row.failure),controlled_GET503_count:controlledCount,handler_fault_count:handlerFaults.length,secondary,first_exception:failed?String(primary):null,actual_success_assertions_completed:completed});}catch(cause){retain('closure evidence',cause);}
 }
 if(failed)throw primary;expect(removed).toBe(true);expect(completed).toBe(true);expect(proof).toBeTruthy();e.note('saved_queue_remaining_controls',proof);
}
test('saved queue owning evaluation error recovery and real preparation document reopen preserve exact saved controls',async({page,renderer,workspace,evidence})=>{await installDesktopHostShim(page,renderer.port);await queueRemainingExercise(page,workspace,evidence,false,renderer.origin,renderer.url);});
test('native saved queue owning evaluation error recovery and real preparation document reopen preserve exact originals',{tag:'@electron'},async({electronSession,workspace,evidence})=>{const page=electronSession.window,backend=await electronSession.waitForBackend();await queueRemainingExercise(page,workspace,evidence,true,`http://127.0.0.1:${backend.port}`);});
