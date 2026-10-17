// Root-only actual GUI preparation. Source qualification never launches it.
import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import type {Page,Request,Route,Dialog} from '@playwright/test';
import {expect,type Workspace,type Evidence} from './test';
import {handoffApi,handoffWithin,handoffProject,handoffNamespace,handoffSave,type HandoffApi} from './remaining-project-handoff';
import {ownedFile,type Custody} from './owningCancelControl';
import {activateFlowDataset,chooseFlowImage,type FlowNavigation} from './authenticatedFlowBrowser';
const harness=require('./harness.cjs');
const contract=require('./partialNodeCacheContract.cjs');
const startupEvaluation=require('./owned-startup-evaluation.cjs');
type Scope={tag:'A'|'B';origin:string;project:any;fixture:any;weights:any;versions:any;pipeline:any;stop_node_id:string;context:any;image:{path:string;image_id:string}};
function fixture(script:string,args:string[],cap:number){return JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures',script),...args],{encoding:'utf8',timeout:cap}));}
async function create(tag:'A'|'B',w:Workspace,e:Evidence,api:HandoffApi,origin:string):Promise<Scope>{
 const root=path.join(w.root,'f064-partial-'+tag);fs.mkdirSync(root,{mode:0o700});
 const input=fixture('flow_comparison_control.py',[root],15_000);
 await api('/api/project/create',{name:'F064 original CPU cache '+tag,task:'classification'});
 await api('/api/project/update',{source_dataset_dir:input.source},'PUT');
 await api('/api/dataset/import',{folder_path:input.source,task:'classification'});
 const library=await activateFlowDataset(api,'classification',e);expect(library.source_root).toBe(input.source);
 const context=(await api('/api/context')).project_context;
 const project=await api('/api/project/current');expect(context.project_id).toBe(project.id);expect(project.task).toBe('classification');expect(project.source_dataset_dir).toBe(input.source);
 const weights=fixture('comparison_models.py',[input.source,project.models_dir],30_000);
 const versions=fixture('flow_debug_model_control.py',[JSON.stringify(project)],15_000);
 const selected=input.files.find((r:any)=>r.split==='test');expect(selected).toBeDefined();
 const matches=library.items.filter((r:any)=>r.file_path===selected.path);expect(matches).toHaveLength(1);expect(typeof matches[0].image_uuid).toBe('string');expect(matches[0].image_uuid.length).toBeGreaterThan(0);expect(matches[0].sha256).toBe(selected.sha256);
 return {tag,origin,project,fixture:input,weights,versions,pipeline:versions.pipeline,stop_node_id:versions.stop_node_id,context,image:{path:selected.path,image_id:matches[0].image_uuid}};
}
function wireIdentity(q:Request,page:Page){
 const u=new URL(q.url()),headers=q.headers();
 const row={method:q.method(),origin:u.origin,path:u.pathname,search:u.search,main_frame:q.frame()===page.mainFrame(),project_header:headers['x-vision-project'],context:JSON.parse(headers['x-vision-context']),body:q.postDataJSON()};
 return row;
}
function identity(q:Request,page:Page,scope:Scope,preparation:any){
 const row={...wireIdentity(q,page),preparation};contract.request(row,scope);return row;
}
function protectedState(s:Scope,c:Custody){
 const versions=path.join(s.project.project_dir,'flowcharts/versions');
 const files=Object.fromEntries([path.join(versions,s.versions.version_a+'.json'),path.join(versions,s.versions.version_b+'.json'),...s.fixture.files.map((r:any)=>r.path),...Object.keys(s.versions.models).map(job=>path.join(s.project.models_dir,job,'best_model.pt'))].map(file=>[file,ownedFile(file,c)]));
 expect(fs.existsSync(path.join(s.project.project_dir,'flowcharts/active.json'))).toBe(false);
 for(const row of s.fixture.files)expect(files[row.path].sha256).toBe(row.sha256);
 for(const [job,digest]of Object.entries(s.versions.models))expect(files[path.join(s.project.models_dir,job,'best_model.pt')].sha256).toBe(digest);
 return {files,version_names:fs.readdirSync(versions).sort(),source:handoffNamespace(s.fixture.source),models:handoffNamespace(s.project.models_dir),active_flow_absent:true};
}
async function selection(page:Page,s:Scope,w:Workspace,e:Evidence,label:string){
 const deadline=performance.now()+10_000;
 await handoffWithin(expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(s.project.name,{timeout:10_000}),deadline,'owning project title');
 await handoffWithin(page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(4).click(),deadline,'original STEP5');
 await handoffWithin(page.getByRole('tab',{name:'편집',exact:true}).click(),deadline,'original editor tab');
 const wire=page.waitForResponse(r=>r.request().frame()===page.mainFrame()&&r.request().method()==='GET'&&r.url()===s.origin+'/api/flowchart/pipelines/'+s.versions.version_a,{timeout:10_000});
 const discardConfirmations:any[]=[],dialogTasks:Promise<void>[]=[];let dialogCount=0,dialogFailure:unknown;
 const ownDiscard=(dialog:Dialog)=>{
  const record={type:dialog.type(),message:dialog.message(),page_matches_owner:dialog.page()===page,count:++dialogCount,started:performance.now(),deadline};
  const pending=(async()=>{
   try{contract.discardConfirmation(record);await handoffWithin(dialog.accept(),deadline,'original own saved-version discard confirmation');discardConfirmations.push({...record,accepted:true,finished:performance.now()});expect(discardConfirmations[0].finished).toBeLessThanOrEqual(deadline);}
   catch(error){dialogFailure=error;await handoffWithin(dialog.dismiss().catch(()=>{}),deadline,'unqualified dialog refusal');throw error;}
  })();dialogTasks.push(pending);void pending.catch(error=>{dialogFailure=error;});
 };
 page.on('dialog',ownDiscard);
 let response;
 try{
  await handoffWithin(page.getByLabel('저장 버전',{exact:true}).selectOption(s.versions.version_a),deadline,'exact saved version');
  await handoffWithin(Promise.all(dialogTasks),deadline,'original dialog handlers finished');if(dialogFailure)throw dialogFailure;
  response=await handoffWithin(wire,deadline,'actual saved version response');expect(response.status()).toBe(200);
  await handoffWithin(Promise.all(dialogTasks),deadline,'all original dialog handlers finished');if(dialogFailure)throw dialogFailure;
 }finally{page.off('dialog',ownDiscard);}
 const raw=await handoffWithin(response.body(),deadline,'actual saved graph full body');expect(raw.length).toBeLessThanOrEqual(1024*1024);expect(JSON.parse(raw.toString('utf8'))).toEqual(s.pipeline);expect(await handoffWithin(response.finished(),deadline,'saved version response finished')).toBeNull();
 await handoffWithin(chooseFlowImage(page,s.fixture.source,s.image.path),deadline,'exact accepted revision image picker');
 await handoffWithin(page.getByLabel('플로우 노드 검색',{exact:true}).fill(s.stop_node_id),deadline,'exact stop node search');
 await handoffWithin(page.getByRole('list',{name:'노드 검색 결과'}).getByRole('button').first().click(),deadline,'original selected node');
 const labelControl=page.getByRole('textbox',{name:'노드 명칭',exact:true});
 await handoffWithin(expect(labelControl).toHaveValue(s.pipeline.nodes.find((n:any)=>n.id===s.stop_node_id).data.label,{timeout:10_000}),deadline,'selected node label');
 await handoffWithin(expect(page.getByLabel('완료된 학습 모델',{exact:true})).toHaveValue('job_fixture_incumbent',{timeout:10_000}),deadline,'original frozen selected model');
 await handoffWithin(expect(page.getByTitle(path.basename(s.image.path),{exact:true})).toBeVisible({timeout:10_000}),deadline,'selected image visible');
 await handoffWithin(page.getByRole('tab',{name:'테스트',exact:true}).click(),deadline,'test tab');
 await handoffWithin(page.getByLabel('플로우 실행 위치',{exact:true}).selectOption('local_cpu'),deadline,'explicit CPU only');
 await handoffWithin(expect(page.getByRole('button',{name:'선택 노드까지 실행',exact:true})).toBeEnabled({timeout:10_000}),deadline,'original selected-node action enabled');
 return {label,project_id:s.project.id,version_id:s.versions.version_a,stop_node_id:s.stop_node_id,image:s.image,model_job_id:'job_fixture_incumbent',checkpoint_sha256:s.versions.models.job_fixture_incumbent,saved_graph_response_sha256:require('node:crypto').createHash('sha256').update(raw).digest('hex'),selection:'explicit_original_controls_after_reopen_not_automatic_restore',original_saved_version_discard_confirmations:discardConfirmations,started:deadline-10_000,deadline,finished:performance.now()};
}
async function run(page:Page,s:Scope,w:Workspace,e:Evidence,label:string,controlled:boolean){
 const started=performance.now(),deadline=started+10_000;
 await handoffWithin(page.getByRole('tab',{name:'테스트',exact:true}).click(),deadline,'test tab before explicit original run');
 await handoffWithin(expect(page.getByLabel('플로우 실행 위치',{exact:true})).toHaveValue('local_cpu',{timeout:10_000}),deadline,'original CPU selection retained');
 const own=(method:string,path:string)=>(r:any)=>r.request().frame()===page.mainFrame()&&r.request().method()===method&&r.url()===s.origin+path;
 const prepared=page.waitForResponse(own('POST','/api/flowchart/partial-runs/prepare'),{timeout:10_000});
 const waiting=page.waitForResponse(own('POST','/api/flowchart/run'),{timeout:10_000});
 const state=page.waitForResponse(r=>r.request().frame()===page.mainFrame()&&r.request().method()==='GET'&&new URL(r.url()).origin===s.origin&&/^\/api\/flowchart\/partial-runs\/[a-f0-9]{32}$/.test(new URL(r.url()).pathname),{timeout:10_000});
 for(const pending of [prepared,waiting,state])void pending.catch(()=>{});
 await handoffWithin(page.getByRole('button',{name:'선택 노드까지 실행',exact:true}).click(),deadline,'owning selected-node click');
 const prepareResponse=await handoffWithin(prepared,deadline,'original authenticated preparation');
 const prepareBytes=await handoffWithin(prepareResponse.body(),deadline,'full capability body');expect(prepareBytes.length).toBeLessThanOrEqual(1024*1024);
 expect(await handoffWithin(prepareResponse.finished(),deadline,'original capability response finished')).toBeNull();
 const preparation={request:wireIdentity(prepareResponse.request(),page),status:prepareResponse.status(),body:JSON.parse(prepareBytes.toString('utf8'))};
 const response=await handoffWithin(waiting,deadline,'owning actual POST response'),request=identity(response.request(),page,s,preparation);
 const bytes=await handoffWithin(response.body(),deadline,'full selected-node response');expect(bytes.length).toBeLessThanOrEqual(1024*1024);expect(await handoffWithin(response.finished(),deadline,'original response normal finish')).toBeNull();
 const terminalResponse=await handoffWithin(state,deadline,'original post-Run state read');
 const terminalBytes=await handoffWithin(terminalResponse.body(),deadline,'full original state read');expect(await handoffWithin(terminalResponse.finished(),deadline,'original state response finish')).toBeNull();
 const terminal={request:{...wireIdentity(terminalResponse.request(),page),body:null},status:terminalResponse.status(),body:JSON.parse(terminalBytes.toString('utf8'))};
 expect(terminal.status).toBe(200);expect(terminal.body.run_id).toBe(preparation.body.run_id);expect(terminal.request.context).toEqual(s.context);
 expect(terminal.body).toMatchObject({project_id:s.project.id,stop_node_id:s.stop_node_id,execution_target:'local',device:'cpu',state:controlled?'prepared':'completed',execution_finished:!controlled});
 const finished=performance.now();expect(finished).toBeLessThanOrEqual(deadline);
 const file=path.join(w.logs,label+'-response.json');fs.writeFileSync(file,bytes,{flag:'wx'});e.addFile(file);
 for(const [name,value]of [['prepare',prepareBytes],['state',terminalBytes]] as const){const log=path.join(w.logs,label+'-'+name+'.json');fs.writeFileSync(log,value,{flag:'wx'});e.addFile(log);}
 return {label,request,status:response.status(),body:JSON.parse(bytes.toString('utf8')),terminal,controlled_response:controlled,original_backend_execution:!controlled,started,deadline,finished,finished_normally:true,raw_file:file,raw_sha256:require('node:crypto').createHash('sha256').update(bytes).digest('hex'),raw_size:bytes.length};
}
export async function exercisePartialCache(page:Page,w:Workspace,e:Evidence,origin:string,native:boolean,url?:string,options?:{api:HandoffApi;navigate:FlowNavigation}){
 const api=options?.api||handoffApi(page,origin,native),requests:Request[]=[],pageErrors:string[]=[],consoleErrors:string[]=[],handoffs:any[]=[],selections:any[]=[],rows:any[]=[];
 const originalRequest=(q:Request)=>{if(new URL(q.url()).origin===origin&&new URL(q.url()).pathname==='/api/flowchart/run')requests.push(q);};
 const pageError=(error:Error)=>pageErrors.push(error.message),consoleError=(message:any)=>{if(message.type()==='error')consoleErrors.push(message.text());};
 page.on('request',originalRequest);page.on('pageerror',pageError);page.on('console',consoleError);
 let startupPreparation:any,startupProof:any;
 const custody:Custody={opened:0,closed:0,uncertain:[]};let fault:((route:Route)=>Promise<void>)|undefined;let faultCount=0,faultError:unknown;
 try{
  const B=await create('B',w,e,api,origin),A=await create('A',w,e,api,origin);expect(A.project.id).not.toBe(B.project.id);
  const startupScopes=[A,B].map(s=>({project:s.project,context:s.context,source:s.fixture.source,modelHashes:s.versions.models,heldoutPaths:s.fixture.files.filter((r:any)=>r.split==='test').map((r:any)=>r.path)}));startupPreparation=startupEvaluation.observe(page,origin,startupScopes);
  const prepare=async(s:Scope,initial:boolean,label:string)=>{
   const deadline=performance.now()+10_000,scope=startupScopes.find(row=>row.project.id===s.project.id)!,frontier=startupPreparation.arm(scope,deadline);
   const handoff=options?(await handoffWithin(options.navigate(s.project),deadline,'actual owned shared-project preparation selection'),{project_id:s.project.id,actual_shared_project_UI:true}):initial?(await handoffWithin(url?page.goto(url):page.reload(),deadline,'original startup navigation'),null):await handoffWithin(handoffProject(page,{project:s.project,source:s.fixture.source},origin),deadline,'original preparation handoff');
   await handoffWithin(expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(s.project.name,{timeout:10_000}),deadline,'original preparation project');
   await handoffWithin(page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click(),deadline,'original STEP4 automatic evaluation');
   const original=await startupPreparation.wait(scope,frontier,deadline);await handoffWithin(expect(page.getByRole('button',{name:'HTML 리포트 내보내기',exact:true})).toBeEnabled({timeout:10_000}),deadline,'original evaluation result adopted');
   // Leave the original evaluation view before the next project adoption; no graph draft exists during preparation.
   await handoffWithin(page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click(),deadline,'original STEP1 before project handoff');
   return {label,original_responses:original,handoff,started:deadline-10_000,deadline,finished:performance.now(),preparation_only:true,automatic_device:'original_backend_selection_unobserved'};
  };
  const startupStages=[await prepare(A,true,'prepare-A'),await prepare(B,false,'prepare-B'),await prepare(A,false,'return-A-before-baseline')];startupProof={...startupPreparation.snapshot(),stages:startupStages};startupPreparation.close();handoffSave(e,w,'f064-original-automatic-evaluation-preparation',startupProof);
  const before={A:protectedState(A,custody),B:protectedState(B,custody)};
  if(options)await options.navigate(A.project);else if(url)await page.goto(url);else await page.reload();
  selections.push(await selection(page,A,w,e,'initial-A'));
  const pattern=origin+'/api/flowchart/run';
  fault=async(route:Route)=>{try{expect(faultCount).toBe(0);const request=wireIdentity(route.request(),page);expect(request.context).toEqual(A.context);expect(request.body.partial_run_id).toMatch(/^[a-f0-9]{32}$/);faultCount++;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:contract.DETAIL})});}catch(error){faultError=error;await route.abort('failed');}};
  await page.route(pattern,fault);const rejected=await run(page,A,w,e,'error-A',true);if(faultError)throw faultError;contract.failure(rejected);rows.push(rejected);
  await expect(page.getByText('부분 실행의 종료 상태를 확인하지 못했습니다. 중단 상태를 확인한 뒤 다시 실행하세요.',{exact:true})).toBeVisible({timeout:10_000});
  await expect(page.getByRole('button',{name:'선택 노드까지 실행',exact:true})).toBeDisabled();
  const recoveryDeadline=performance.now()+10_000,runId=rejected.request.preparation.body.run_id;
  const cancel=page.waitForResponse(r=>r.url()===origin+'/api/flowchart/partial-runs/'+runId+'/cancel'&&r.request().method()==='POST',{timeout:10_000});
  const stopped=page.waitForResponse(r=>r.url()===origin+'/api/flowchart/partial-runs/'+runId&&r.request().method()==='GET',{timeout:10_000});
  await handoffWithin(page.getByRole('button',{name:'부분 실행 중단',exact:true}).click(),recoveryDeadline,'explicit original unknown owner cancel');
  const cancelResponse=await handoffWithin(cancel,recoveryDeadline,'actual original cancel response'),cancelBody=await handoffWithin(cancelResponse.json(),recoveryDeadline,'original cancel ACK');
  expect(cancelResponse.status()).toBe(200);expect(JSON.parse(cancelResponse.request().headers()['x-vision-context'])).toEqual(A.context);expect(cancelBody.run_id).toBe(runId);
  const stoppedResponse=await handoffWithin(stopped,recoveryDeadline,'matching original terminal GET'),stoppedBody=await handoffWithin(stoppedResponse.json(),recoveryDeadline,'terminal cancelled body');
  expect(stoppedResponse.status()).toBe(200);expect(JSON.parse(stoppedResponse.request().headers()['x-vision-context'])).toEqual(A.context);expect(stoppedBody).toMatchObject({run_id:runId,project_id:A.project.id,state:'cancelled',execution_finished:true});
  expect(await handoffWithin(stoppedResponse.finished(),recoveryDeadline,'terminal cancellation response finished')).toBeNull();
  Object.assign(rejected,{recovery:{cancel:cancelBody,terminal:stoppedBody,explicit_original_cancel:true,finished:performance.now(),deadline:recoveryDeadline}});
  await handoffWithin(expect(page.getByRole('button',{name:'선택 노드까지 실행',exact:true})).toBeEnabled({timeout:10_000}),recoveryDeadline,'retry only after matching terminal receipt');expect(requests).toHaveLength(1);expect(faultCount).toBe(1);expect(protectedState(A,custody)).toEqual(before.A);expect(protectedState(B,custody)).toEqual(before.B);
  await e.screenshot(page,(native?'native':'browser')+'-selected-node-owning503-no-result');
  await page.unroute(pattern,fault);fault=undefined;
  const retry=await run(page,A,w,e,'retry-A',false);contract.partial(retry,A,'miss');rows.push(retry);await expect(page.getByText(contract.DETAIL,{exact:true})).toHaveCount(0);
  const repeat=await run(page,A,w,e,'repeat-A',false);contract.partial(repeat,A,'hit');rows.push(repeat);
  if(options)await options.navigate(A.project);else await page.reload();selections.push(await selection(page,A,w,e,'reopen-A'));const reopened=await run(page,A,w,e,'reopen-A',false);contract.partial(reopened,A,'hit');rows.push(reopened);
  handoffs.push(options?(await options.navigate(B.project),{project_id:B.project.id,actual_shared_project_UI:true}):await handoffProject(page,{project:B.project,source:B.fixture.source},origin));selections.push(await selection(page,B,w,e,'handoff-B'));const other=await run(page,B,w,e,'handoff-B',false);contract.partial(other,B,'miss');rows.push(other);
  handoffs.push(options?(await options.navigate(A.project),{project_id:A.project.id,actual_shared_project_UI:true}):await handoffProject(page,{project:A.project,source:A.fixture.source},origin));selections.push(await selection(page,A,w,e,'return-A'));const returned=await run(page,A,w,e,'return-A',false);contract.partial(returned,A,'hit');rows.push(returned);
  const verified=contract.sequence(rows,A,B);expect(requests).toHaveLength(rows.length);expect(faultCount).toBe(1);expect(pageErrors).toEqual([]);
  await page.getByRole('tab',{name:'편집',exact:true}).click();await expect(page.getByRole('status').filter({hasText:'동일한 부분 실행 근거 재사용'})).toBeVisible();await page.getByRole('tab',{name:'테스트',exact:true}).click();await expect(page.getByRole('status').filter({hasText:'선택 노드 디버그 결과 · 전체 검사·배포 검증은 아직 실행하지 않았습니다.'})).toBeVisible();
  const after={A:protectedState(A,custody),B:protectedState(B,custody)};expect(after).toEqual(before);expect(custody.uncertain).toEqual([]);expect(custody.closed).toBe(custody.opened);
  await e.screenshot(page,(native?'native':'browser')+'-selected-node-return-A-original-cache-hit');
  const proof={schema:'modu-vision.F064-partial-error-reopen-handoff/v1',...verified,A,B,startup_preparation:startupProof,rows,selections,handoffs,before,after,custody,page_errors:pageErrors,console_errors_unfiltered:consoleErrors,actual_original_UI_buttons:true,controlled_error_scope:'one owning selected-node 503; not an actual backend fault or remote-disconnect claim',genuine_retry_and_partial_cache_HTTP200:true,automatic_selection_restoration_claim:false,explicit_selected_node_local_CPU:true,checkpoints_untrained:true,training:false,new_remote_GPU:false,quality_approval:false,installer_acceptance:false,invalid_covered:false,cancel_covered:true,parent_credit:0};
  const record=handoffSave(e,w,'partial-node-cache-error-reopen-handoff-proof',proof);e.note('F064_partial_error_reopen_handoff',{...record,actual_controls:3,invalid_covered:false,cancel_covered:true,parent_credit:0});
 }finally{startupPreparation?.close();if(fault&&!page.isClosed())await page.unroute(origin+'/api/flowchart/run',fault);page.off('request',originalRequest);page.off('pageerror',pageError);page.off('console',consoleError);expect(custody.uncertain).toEqual([]);expect(custody.closed).toBe(custody.opened);}
}
