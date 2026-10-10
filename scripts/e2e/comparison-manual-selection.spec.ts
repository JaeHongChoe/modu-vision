import fs from 'node:fs';import path from 'node:path';import crypto from 'node:crypto';import {execFileSync} from 'node:child_process';import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';import {installDesktopHostShim} from './fixtures/desktop-host-shim';import {png} from './qa/appFlow';
const harness=require('./fixtures/harness.cjs');type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
async function exercise(page:Page,w:Workspace,e:Evidence,api:Api,native:boolean,url?:string){
 const source=path.join(w.root,'manual-comparison-source'),originals:Record<string,string>={};
 for(const split of ['train','val','test'])for(const label of ['OK','NG']){const folder=path.join(source,split,label);fs.mkdirSync(folder,{recursive:true});const file=path.join(folder,label+'.png');fs.writeFileSync(file,png(64,3,(x,y)=>[label==='OK'?220:40,x,y]));originals[file]=crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');}
 const project=await api('/api/project/create',{name:'Manual comparison choice',task:'classification'});await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'classification'});
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/comparison_models.py'),source,project.models_dir],{cwd:harness.REPO_ROOT,env:{...process.env,VISION_AI_STUDIO_USER_DATA_DIR:w.userData},encoding:'utf8',timeout:60_000}));
 if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click();
 const panel=page.getByRole('region',{name:'현행과 후보 모델 비교'}),baseline=panel.getByLabel('비교 기준 모델',{exact:true}),candidate=panel.getByLabel('후보 모델',{exact:true});
 await baseline.selectOption('job_fixture_candidate');await candidate.selectOption('');
 await page.getByLabel('평가 모델',{exact:true}).selectOption('job_fixture_candidate');await expect(candidate).toBeEnabled();await expect(candidate).toHaveValue('');await expect(panel.getByRole('button',{name:'동일 test 이미지로 비교',exact:true})).toBeDisabled();
 await candidate.selectOption('job_fixture_incumbent');await page.getByLabel('평가 모델',{exact:true}).selectOption('job_fixture_incumbent');await expect(candidate).toBeEnabled();await expect(baseline).toHaveValue('job_fixture_candidate');await expect(candidate).toHaveValue('job_fixture_incumbent');
 const pending=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/evaluation/model-comparisons/jobs'&&r.request().method()==='POST');await panel.getByRole('button',{name:'동일 test 이미지로 비교',exact:true}).click();const response=await pending;expect(response.ok(),await response.text()).toBe(true);const body=response.request().postDataJSON();expect(body.incumbent_job_id).toBe('job_fixture_candidate');expect(body.candidate_job_id).toBe('job_fixture_incumbent');
 const created=await response.json(),query='?'+new URLSearchParams({source_dataset_path:source,task:'classification'});let job:any;await expect.poll(async()=>{job=await api('/api/evaluation/model-comparisons/jobs/'+created.job_id+query);if(job.status==='failed')throw Error(JSON.stringify(job));return job.status;},{timeout:60_000}).toBe('completed');
 const report=await api('/api/evaluation/model-comparisons/'+job.report_id+query);expect(report.incumbent_job_id).toBe(body.incumbent_job_id);expect(report.candidate_job_id).toBe(body.candidate_job_id);expect(report.images).toHaveLength(2);expect(report.summary.error_images).toBe(0);expect((await api('/api/model-deployments/active'+query)).active).toBeNull();
 for(const [file,hash] of Object.entries(originals))expect(crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')).toBe(hash);
 await expect(panel.getByLabel('저장된 모델 비교',{exact:true})).toHaveValue(job.report_id);await e.screenshot(page,`${native?'native':'browser'}-manual-comparison-submitted-pair`);e.note('manual_comparison_choice',{project_id:project.id,fixture,originals,body,job,report,explicit_empty_preserved:true,recommendation_cannot_reverse_manual_pair:true,actual_cpu_comparison:true,actual_training:false,synthetic_not_quality_approval:true,native});
}
test('manual comparison pair and explicit empty survive evaluation model changes',async({page,request,renderer,workspace,evidence})=>{await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const r=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(r.ok(),await r.text()).toBe(true);return r.json();};await exercise(page,workspace,evidence,api,false,renderer.url);});
test('native manual comparison pair and explicit empty survive evaluation model changes',{tag:'@electron'},async({electronSession,workspace,evidence})=>{const {window}=electronSession,status=await electronSession.waitForBackend();const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!r.ok)throw Error(`Owned comparison API ${r.status}: ${await r.text()}`);return r.json();},{port:status.port,route,body,method});await exercise(window,workspace,evidence,api,true);});

// SOURCE-only append. Root owns every future fixture/model/UI execution.
import type {Request as ManualHandoffRequest} from '@playwright/test';
import {handoffProject as manualProject, handoffLateRead as manualLateRead,
  handoffWithin as manualWithin} from './fixtures/remaining-project-handoff';

type ManualScope={tag:'A'|'B';project:any;source:string;fixture:any;baseline:string;candidate:string;
  job?:any;report?:any;reportFile?:string;postProof?:any};
const manualIdentity=(s:fs.BigIntStats)=>[s.dev,s.ino,s.mode,s.nlink,s.size,s.mtimeNs,s.ctimeNs].map(String);
const manualSHA=(b:Buffer|string)=>crypto.createHash('sha256').update(b).digest('hex');
function manualRemember(primary:unknown,secondary:unknown,label:string):unknown {
  if(primary===undefined)return secondary;
  if(primary instanceof Error)try{primary.message+='; secondary '+label+': '+String(secondary);}catch{/* retain original */}
  return primary;
}
function manualFile(file:string,root:string){
  const base=path.resolve(root),target=path.resolve(file),rel=path.relative(base,target);
  expect(rel!==''&&rel!=='..'&&!rel.startsWith('..'+path.sep)&&!path.isAbsolute(rel)).toBe(true);
  const ancestors:Array<[string,string[]]>=[];
  for(let parent=path.dirname(target);;parent=path.dirname(parent)){
    const st=fs.lstatSync(parent,{bigint:true});expect(st.isDirectory()&&!st.isSymbolicLink()).toBe(true);
    ancestors.push([parent,manualIdentity(st)]);if(parent===base)break;expect(path.dirname(parent)).not.toBe(parent);
  }
  const st=fs.lstatSync(target,{bigint:true});expect(st.isFile()&&!st.isSymbolicLink()).toBe(true);expect(st.nlink).toBe(1n);
  let fd:number|undefined,bytes:Buffer|undefined,primary:unknown;
  try{
    fd=fs.openSync(target,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW|fs.constants.O_NONBLOCK);
    expect(manualIdentity(fs.fstatSync(fd,{bigint:true}))).toEqual(manualIdentity(st));
    bytes=fs.readFileSync(fd);expect(BigInt(bytes.length)).toBe(st.size);
    expect(manualIdentity(fs.fstatSync(fd,{bigint:true}))).toEqual(manualIdentity(st));
  }catch(error){primary=error;}finally{if(fd!==undefined)try{fs.closeSync(fd);}catch(error){primary=manualRemember(primary,error,'leaf close');}}
  if(primary!==undefined)throw primary;
  expect(manualIdentity(fs.lstatSync(target,{bigint:true}))).toEqual(manualIdentity(st));
  for(const [ancestor,pin] of ancestors)expect(manualIdentity(fs.lstatSync(ancestor,{bigint:true}))).toEqual(pin);
  return {path:target,raw:manualIdentity(st),size:bytes!.length,sha256:manualSHA(bytes!),bytes:bytes!};
}
function manualTree(root:string){
  const rows:Record<string,any>={};
  const visit=(folder:string)=>{
    const before=fs.lstatSync(folder,{bigint:true});expect(before.isDirectory()&&!before.isSymbolicLink()).toBe(true);
    rows[path.relative(root,folder).split(path.sep).join('/')||'.']={kind:'directory',raw:manualIdentity(before)};
    const names=fs.readdirSync(folder).sort();
    for(const name of names){const file=path.join(folder,name),st=fs.lstatSync(file,{bigint:true});
      expect(st.isSymbolicLink()).toBe(false);
      if(st.isDirectory())visit(file);else{expect(st.isFile()).toBe(true);const {bytes,...pin}=manualFile(file,root);rows[path.relative(root,file).split(path.sep).join('/')]={kind:'file',...pin};}
    }
    expect(fs.readdirSync(folder).sort()).toEqual(names);
    expect(manualIdentity(fs.lstatSync(folder,{bigint:true}))).toEqual(manualIdentity(before));
  };
  visit(root);return {root,rows};
}
function manualCustody(scope:ManualScope){
  const roots={source:scope.source,annotations:scope.project.annotations_dir,models:scope.project.models_dir,
    reports:scope.project.reports_dir,dataset:scope.project.dataset_dir};
  const trees=Object.fromEntries(Object.entries(roots).map(([role,root])=>[role,manualTree(String(root))]));
  const topFiles:Record<string,any>={};
  for(const name of fs.readdirSync(scope.project.project_dir).sort()){
    const file=path.join(scope.project.project_dir,name),st=fs.lstatSync(file,{bigint:true});
    expect(st.isSymbolicLink()).toBe(false);
    if(st.isFile()){const {bytes,...pin}=manualFile(file,scope.project.project_dir);topFiles[name]=pin;}
    else expect(st.isDirectory()).toBe(true);
  }
  expect(topFiles['project.json']).toBeTruthy();
  return {project_id:scope.project.id,source:scope.source,trees,topFiles};
}
function manualSaved(scope:ManualScope){
  const pin=manualFile(scope.reportFile!,scope.project.reports_dir),saved=JSON.parse(pin.bytes.toString('utf8'));
  expect(saved).toEqual(scope.report);expect(saved.project_id).toBe(scope.project.id);
  expect(saved.source_dataset_path).toBe(scope.source);expect(saved.task).toBe('classification');
  expect(saved.labelset_id).toBe(scope.project.active_labelset_id||'default');
  expect(saved.incumbent_job_id).toBe(scope.baseline);expect(saved.candidate_job_id).toBe(scope.candidate);
  expect(saved.model_sha256).toEqual({incumbent:manualFile(path.join(scope.project.models_dir,scope.baseline,'best_model.pt'),scope.project.models_dir).sha256,
    candidate:manualFile(path.join(scope.project.models_dir,scope.candidate,'best_model.pt'),scope.project.models_dir).sha256});
  expect(saved.status).toBe('completed');expect(saved.images).toHaveLength(2);expect(saved.summary.error_images).toBe(0);
  for(const row of saved.images){const image=manualFile(row.file_path,scope.source);expect(image.sha256).toBe(row.image_sha256);}
  const {bytes,...reportPin}=pin;return {reportPin,report:saved};
}
async function manualExercise(page:Page,w:Workspace,e:Evidence,origin:string,url:string){
  const api:Api=async(route,body,method)=>{
    const deadline=performance.now()+10_000;
    const response=await manualWithin(page.request.fetch(origin+route,{method:method||(body===undefined?'GET':'POST'),
      ...(body===undefined?{}:{data:body}),timeout:Math.max(1,Math.floor(deadline-performance.now()))}),deadline,'fixture API');
    expect(response.ok()).toBe(true);return manualWithin(response.json(),deadline,'fixture API body');
  };
  const make=async(tag:'A'|'B'):Promise<ManualScope>=>{
    const source=path.join(w.root,'manual-handoff-source-'+tag);
    for(const split of ['train','val','test'])for(const label of ['OK','NG']){
      const folder=path.join(source,split,label);fs.mkdirSync(folder,{recursive:true});
      fs.writeFileSync(path.join(folder,label+'.png'),png(64,3,(x,y)=>[label==='OK'?(tag==='A'?220:200):(tag==='A'?40:60),x,y]),{flag:'wx'});
    }
    let project=await api('/api/project/create',{name:'Manual owning comparison '+tag,task:'classification'});
    project=await api('/api/project/update',{source_dataset_dir:source},'PUT');
    await api('/api/dataset/import',{folder_path:source,task:'classification'});
    project=await api('/api/project/current');expect(project.source_dataset_dir).toBe(source);
    const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/comparison_models.py'),source,project.models_dir],
      {cwd:harness.REPO_ROOT,env:{...process.env,VISION_AI_STUDIO_USER_DATA_DIR:w.userData},encoding:'utf8',timeout:60_000}));
    expect(fixture.checkpoint_kind).toBe('untrained_deterministic');
    return {tag,project,source,fixture,baseline:tag==='A'?'job_fixture_candidate':'job_fixture_incumbent',
      candidate:tag==='A'?'job_fixture_incumbent':'job_fixture_candidate'};
  };
  const A=await make('A'),B=await make('B');expect(A.project.id).not.toBe(B.project.id);expect(A.source).not.toBe(B.source);
  const lifecycleRows:Array<any>=[],pendingLifecycle=new Set<Promise<void>>();
  let lifecycleSerial=0,visitFloor=0,visitScope:ManualScope=B;
  const lifecycleProofs:Array<any>=[];
  const readObserver=(request:ManualHandoffRequest)=>{
    const address=new URL(request.url());
    if(address.origin!==origin||request.method()!=='GET'
      ||!(address.pathname==='/api/provenance/impact'||address.pathname.startsWith('/api/model-deployments/')))return;
    const row:any={id:lifecycleSerial++,path:address.pathname,source:address.searchParams.get('source_dataset_path'),
      project:request.headers()['x-vision-project'],context:request.headers()['x-vision-context'],main_frame:false};
    lifecycleRows.push(row);
    let waiting:Promise<void>;
    waiting=(async()=>{
      row.main_frame=request.frame()===page.mainFrame();expect(row.main_frame).toBe(true);
      const response=await request.response();
      if(!response){row.failed=request.failure()?.errorText||'No original response';return;}
      row.status=response.status();const bytes=await response.body();expect(bytes.length).toBeLessThanOrEqual(1024*1024);
      row.bytes=bytes;row.sha256=manualSHA(bytes);row.byte_count=bytes.length;
      row.body=JSON.parse(bytes.toString('utf8'));row.finished=await response.finished();
    })().catch(error=>{row.failed=String(error);}).finally(()=>{pendingLifecycle.delete(waiting);});
    pendingLifecycle.add(waiting);
  };
  const ownOpen=async(scope:ManualScope)=>{
    visitScope=scope;visitFloor=lifecycleSerial;return manualProject(page,scope,origin);
  };
  const ownReload=async(scope:ManualScope)=>{
    visitScope=scope;visitFloor=lifecycleSerial;return page.reload();
  };
  const drainLifecycle=async(deadline:number)=>{
    for(;;){
      const serial=lifecycleSerial;
      await manualWithin(Promise.all(Array.from(pendingLifecycle)),deadline,'original lifecycle bodies finished');
      await manualWithin(page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve())))),
        deadline,'original lifecycle effects committed');
      if(pendingLifecycle.size===0&&serial===lifecycleSerial)return;
    }
  };
  const settleLifecycle=async(scope:ManualScope,label:string)=>{
    const deadline=performance.now()+10_000;
    expect(visitScope.project.id).toBe(scope.project.id);
    const approval=page.getByRole('region',{name:'모델 승인과 롤백',exact:true});
    const refresh=approval.getByRole('button',{name:'새로고침',exact:true});
    await manualWithin(expect(refresh).toBeEnabled({timeout:Math.max(1,Math.floor(deadline-performance.now()))}),deadline,'own deployment reads ready');
    await drainLifecycle(deadline);
    const impact=lifecycleRows.filter(row=>row.id>=visitFloor&&row.path==='/api/provenance/impact'
      &&row.project===scope.project.id).slice(-1)[0];
    expect(impact).toBeTruthy();expect(impact.failed).toBeUndefined();expect(impact.main_frame).toBe(true);
    expect(impact.status).toBe(200);expect(impact.finished).toBeNull();
    expect(JSON.parse(impact.context).project_id).toBe(scope.project.id);
    expect(impact.body.project_id).toBe(scope.project.id);expect(impact.body.source_dataset_path).toBe(scope.source);
    const owns=(response:any,route:string)=>{
      const request=response.request(),address=new URL(response.url());
      return address.origin===origin&&address.pathname===route&&request.method()==='GET'
        &&request.frame()===page.mainFrame()&&address.searchParams.get('source_dataset_path')===scope.source
        &&address.searchParams.get('task')==='classification'
        &&request.headers()['x-vision-project']===scope.project.id
        &&request.headers()['x-vision-context']===impact.context;
    };
    const wire=Promise.all(['/api/model-deployments/active','/api/model-deployments/history',
      '/api/model-deployments/assess/'+scope.report.comparison_id].map(route=>
      page.waitForResponse(response=>owns(response,route),{timeout:Math.max(1,Math.floor(deadline-performance.now()))})));
    void wire.catch(()=>{}); // Own rejection immediately; the unchanged awaited wire still throws below.
    await manualWithin(refresh.click(),deadline,'ordinary owning approval refresh');
    const responses=await manualWithin(wire,deadline,'original owning active history assessment');
    const completed:Array<any>=[];
    for(const response of responses){
      expect(response.status()).toBe(200);
      const bytes=await manualWithin(response.body(),deadline,'original owning refresh body');
      expect(bytes.length).toBeLessThanOrEqual(1024*1024);
      expect(await manualWithin(response.finished(),deadline,'original owning refresh finished')).toBeNull();
      completed.push({path:new URL(response.url()).pathname,body:JSON.parse(bytes.toString('utf8')),
        bytes,sha256:manualSHA(bytes),byte_count:bytes.length,finished:true,
        project:response.request().headers()['x-vision-project'],context:response.request().headers()['x-vision-context']});
    }
    expect(completed[0].body.active).toBeNull();expect(completed[0].body.field_runtime_applied).toBe(false);
    expect(completed[1].body.revisions).toEqual([]);
    expect(completed[2].body.comparison_id).toBe(scope.report.comparison_id);
    expect(completed[2].body.candidate_job_id).toBe(scope.candidate);expect(completed[2].body.status).toBe('needs_review');
    await drainLifecycle(deadline);
    expect(lifecycleRows.filter(row=>row.id>impact.id&&row.path==='/api/provenance/impact')).toEqual([]);
    expect(pendingLifecycle.size).toBe(0);
    const proofIndex=lifecycleProofs.length,rawProofs:Array<any>=[];
    for(const row of [impact,...completed]){
      const file=path.join(w.logs,'manual-'+scope.tag+'-lifecycle-'+proofIndex+'-'+rawProofs.length+'.json');
      fs.writeFileSync(file,row.bytes,{flag:'wx'});e.addFile(file);
      rawProofs.push({path:row.path,body_sha256:row.sha256,body_bytes:row.byte_count,raw_body_file:file,
        original_body_finished:true,project:row.project,context:row.context});
    }
    const {bytes,...database}=manualFile(path.join(scope.project.project_dir,'model_deployments.sqlite3'),scope.project.project_dir);
    lifecycleProofs.push({label,project_id:scope.project.id,source:scope.source,visit_floor:visitFloor,impact_id:impact.id,
      original_reads:rawProofs,model_deployments_database:database,pending_reads:0,ordinary_UI_refresh:true,
      model_activation:false,sidecar_exclusion:false,SQLite_checkpoint_or_transaction:false,original_frame_ms:10_000});
  };
  const posts:Array<any>=[],observe=(request:ManualHandoffRequest)=>{
    const address=new URL(request.url());if(address.origin===origin&&address.pathname==='/api/evaluation/model-comparisons/jobs'&&request.method()==='POST')
      posts.push({body:request.postDataJSON(),main_frame:request.frame()===page.mainFrame(),headers:{
        'x-vision-project':request.headers()['x-vision-project'],'x-vision-context':request.headers()['x-vision-context']}});
  };
  page.on('request',observe);page.on('request',readObserver);
  const panel=()=>page.getByRole('region',{name:'현행과 후보 모델 비교'});
  const enter=async(scope:ManualScope)=>{
    const deadline=performance.now()+10_000;
    await manualWithin(expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name,{timeout:10_000}),deadline,'own project title');
    await manualWithin(page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click(),deadline,'evaluation stage');
    await manualWithin(expect(panel().getByLabel('비교 기준 모델',{exact:true})).toBeEnabled({timeout:10_000}),deadline,'own catalog settled');
    const current=await api('/api/project/current');expect(current.id).toBe(scope.project.id);expect(current.source_dataset_dir).toBe(scope.source);
  };
  const view=async(scope:ManualScope)=>{
    const deadline=performance.now()+10_000,p=panel();
    await manualWithin(expect(p.getByLabel('저장된 모델 비교',{exact:true})).toHaveValue(scope.report.comparison_id,{timeout:10_000}),deadline,'saved report selection');
    await manualWithin(expect(p).toContainText(scope.baseline+' → '+scope.candidate,{timeout:10_000}),deadline,'saved manual pair');
    await manualWithin(expect(p.locator('[data-comparison-image]')).toHaveCount(2,{timeout:10_000}),deadline,'saved original images');
    for(const row of scope.report.images)await manualWithin(expect(p.locator('[data-comparison-image]').filter({hasText:row.file_name}))
      .toHaveAttribute('data-comparison-image',row.file_path,{timeout:10_000}),deadline,'saved exact original path');
    const q='?'+new URLSearchParams({source_dataset_path:scope.source,task:'classification'});
    expect(await api('/api/evaluation/model-comparisons/'+scope.report.comparison_id+q)).toEqual(scope.report);
    await settleLifecycle(scope,'saved owning report view');
    return manualSaved(scope);
  };
  const submit=async(scope:ManualScope)=>{
    const p=panel();await p.getByLabel('비교 기준 모델',{exact:true}).selectOption(scope.baseline);
    await p.getByLabel('후보 모델',{exact:true}).selectOption(scope.candidate);
    await page.getByLabel('평가 모델',{exact:true}).selectOption(scope.candidate);
    await expect(p.getByLabel('비교 기준 모델',{exact:true})).toHaveValue(scope.baseline);
    await expect(p.getByLabel('후보 모델',{exact:true})).toHaveValue(scope.candidate);
    const deadline=performance.now()+10_000;
    const wire=page.waitForResponse(response=>{const address=new URL(response.url());return address.origin===origin
      &&address.pathname==='/api/evaluation/model-comparisons/jobs'&&response.request().method()==='POST'
      &&response.request().frame()===page.mainFrame()&&response.request().postDataJSON()?.source_dataset_path===scope.source;},{timeout:10_000});
    void wire.catch(()=>{}); // Own early rejection; the unchanged same wire is still awaited below.
    await manualWithin(p.getByRole('button',{name:'동일 test 이미지로 비교',exact:true}).click(),deadline,'actual comparison submit');
    const response=await manualWithin(wire,deadline,'actual comparison acceptance');expect(response.status()).toBe(202);
    const body=response.request().postDataJSON();expect(body.source_dataset_path).toBe(scope.source);expect(body.task).toBe('classification');
    expect(body.incumbent_job_id).toBe(scope.baseline);expect(body.candidate_job_id).toBe(scope.candidate);
    expect(body.execution_target).toBe('local_cpu');expect(body.device).toBe('cpu');expect(body.compute_profile_id).toBeNull();
    const headers=response.request().headers();expect(headers['x-vision-project']).toBe(scope.project.id);
    const context=JSON.parse(headers['x-vision-context']);expect(context.project_id).toBe(scope.project.id);expect(context.mode).toBe('local');
    const raw=await manualWithin(response.body(),deadline,'original acceptance bytes');expect(raw.length).toBeLessThanOrEqual(1024*1024);
    expect(await manualWithin(response.finished(),deadline,'original acceptance finished')).toBeNull();
    const created=JSON.parse(raw.toString('utf8'));expect(created.job_id).toBeTruthy();
    const responseFile=path.join(w.logs,'manual-'+scope.tag+'-actual-job-acceptance.json');fs.writeFileSync(responseFile,raw,{flag:'wx'});e.addFile(responseFile);
    const query='?'+new URLSearchParams({source_dataset_path:scope.source,task:'classification'});
    await expect.poll(async()=>{scope.job=await api('/api/evaluation/model-comparisons/jobs/'+created.job_id+query);
      if(scope.job.status==='failed')throw Error(JSON.stringify(scope.job));return scope.job.status;},{timeout:60_000}).toBe('completed');
    expect(scope.job.result_available).toBe(true);expect(scope.job.payload.source_dataset_path).toBe(scope.source);
    expect(scope.job.payload.incumbent_job_id).toBe(scope.baseline);expect(scope.job.payload.candidate_job_id).toBe(scope.candidate);
    scope.report=await api('/api/evaluation/model-comparisons/'+scope.job.report_id+query);
    scope.reportFile=path.join(scope.project.reports_dir,'model_comparisons',scope.job.report_id+'.json');
    const saved=await view(scope);expect((await api('/api/model-deployments/active'+query)).active).toBeNull();
    e.addFile(scope.reportFile);for(const name of [scope.baseline,scope.candidate])e.addFile(path.join(scope.project.models_dir,name,'best_model.pt'));
    scope.postProof={body,context,status:202,raw_sha256:manualSHA(raw),raw_bytes:raw.length,actual_main_frame:true,
      original_response_finished:true,job_id:created.job_id,report_id:scope.job.report_id,saved};
  };
  let primary:unknown,late:Awaited<ReturnType<typeof manualLateRead>>|undefined;
  let before:Record<string,any>|undefined;const after:Record<string,any>={},checks:Record<string,boolean>={},openProofs:Array<any>=[];
  try{
  await page.goto(url);await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(B.project.name);
    openProofs.push(await ownOpen(A));await enter(A);
    const p=panel();await p.getByLabel('후보 모델',{exact:true}).selectOption(A.candidate);
    await p.getByLabel('비교 기준 모델',{exact:true}).selectOption('');
    await page.getByLabel('평가 모델',{exact:true}).selectOption('job_fixture_candidate');
    await expect(p.getByLabel('비교 기준 모델',{exact:true})).toBeEnabled();await expect(p.getByLabel('비교 기준 모델',{exact:true})).toHaveValue('');
    await expect(p.getByRole('button',{name:'동일 test 이미지로 비교',exact:true})).toBeDisabled();expect(posts).toHaveLength(0);
    await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))));expect(posts).toHaveLength(0);
    await e.screenshot(page,'manual-baseline-empty-no-submit');
    await submit(A);await e.screenshot(page,'manual-A-reversed-pair-saved');
    openProofs.push(await ownOpen(B));await enter(B);await submit(B);await e.screenshot(page,'manual-B-owning-pair-saved');
    expect(A.report.comparison_id).not.toBe(B.report.comparison_id);expect(A.job.job_id).not.toBe(B.job.job_id);expect(posts).toHaveLength(2);
    before={A:manualCustody(A),B:manualCustody(B)};const savedA=manualSaved(A),savedB=manualSaved(B);
    openProofs.push(await ownOpen(A));await enter(A);await view(A);
    await ownReload(A);await enter(A);const reopenedA=await view(A);expect(reopenedA).toEqual(savedA);expect(posts).toHaveLength(2);
    await e.screenshot(page,'manual-A-exact-pair-reopened');
    late=await manualLateRead(page,origin,false,address=>address.pathname==='/api/evaluation/model-comparisons/'+A.report.comparison_id
      &&address.searchParams.get('source_dataset_path')===A.source&&address.searchParams.get('task')==='classification');
    const choosing=panel().getByLabel('저장된 모델 비교',{exact:true}).selectOption(A.report.comparison_id);
    const captured=await late.ready();await choosing;expect(captured.body).toEqual(A.report);
    openProofs.push(await ownOpen(B));await enter(B);await view(B);
    const disposition=await late.finish();await view(B);expect(posts).toHaveLength(2);
    await e.screenshot(page,'manual-B-excludes-late-A-report');
    openProofs.push(await ownOpen(A));await enter(A);const returnedA=await view(A);expect(returnedA).toEqual(savedA);
    await ownReload(A);await enter(A);expect(await view(A)).toEqual(savedA);expect(manualSaved(B)).toEqual(savedB);expect(posts).toHaveLength(2);
    await e.screenshot(page,'manual-A-return-exact-saved-pair');
    for(const item of posts){expect(item.main_frame).toBe(true);const owner=item.body.source_dataset_path===A.source?A:B;
      expect(item.body.source_dataset_path).toBe(owner.source);expect(item.headers['x-vision-project']).toBe(owner.project.id);
      expect(JSON.parse(item.headers['x-vision-context']).project_id).toBe(owner.project.id);}
    e.note('manual_pair_handoff',{cells:['F057.comparison-manual-baseline.empty','F057.comparison-manual-baseline.reopen',
      'F057.comparison-manual-candidate.reopen','F057.comparison-manual-submit.reopen','F057.comparison-manual-submit.handoff'],
      A:{project_id:A.project.id,source:A.source,fixture:A.fixture,job:A.job,report:A.report,post:A.postProof},
      B:{project_id:B.project.id,source:B.source,fixture:B.fixture,job:B.job,report:B.report,post:B.postProof},
      baseline_empty:{actual_UI_choice:true,submit_disabled:true,comparison_POST_count:0},comparison_POST_count:posts.length,
      saved_pair_reopened:true,unsaved_manual_selectors_persisted:false,openProofs,late_original_A:{captured_sha256:captured.sha256,
        captured_bytes:captured.bytes.length,captured_report_id:captured.body.comparison_id,disposition},
      actual_CPU_inference:true,actual_training:false,model_activation:false,synthetic_not_quality_approval:true,
      installed_native_execution:false,independent_acceptance:false});
  }catch(error){primary=error;}finally{
    try{page.off('request',observe);}catch(error){primary=manualRemember(primary,error,'request observer removal');}
    if(late)try{await late.close();}catch(error){primary=manualRemember(primary,error,'held original read close');}
    if(before)try{await settleLifecycle(visitScope,'final retained custody');}
    catch(error){primary=manualRemember(primary,error,'final original lifecycle settlement');}
    if(before)for(const scope of [A,B]){
      try{after[scope.tag]=manualCustody(scope);expect(after[scope.tag]).toEqual(before[scope.tag]);checks[scope.tag]=true;}
      catch(error){checks[scope.tag]=false;primary=manualRemember(primary,error,scope.tag+' full retained roots');}
    }
    try{e.note('manual_pair_retained_custody',{before,after,checks,scope:'two complete source/annotation/model/report/dataset roots plus project top-level files; global runtime SQLite outside these roots',
      all_retained_roles_attempted:before!==undefined,actual_execution_by_Source_author:false});}
    catch(error){primary=manualRemember(primary,error,'custody evidence note');}
    try{page.off('request',readObserver);}catch(error){primary=manualRemember(primary,error,'lifecycle observer removal');}
    try{e.note('manual_sqlite_lifecycle_settlement',{proofs:lifecycleProofs,read_dispositions:lifecycleRows.map(({bytes,body,...row})=>row),
      original_body_completion_required:true,every_ordinary_project_top_file_retained:true,no_sleep_retry_checkpoint_filter_signal:true,
      actual_execution_by_Source_author:false});}catch(error){primary=manualRemember(primary,error,'lifecycle proof note');}
  }
  if(primary!==undefined)throw primary;
}
test('manual baseline empty and exact saved comparison pair reopen through owning A B A project handoff',
  async({page,renderer,workspace,evidence})=>{
    page.setDefaultTimeout(10_000);page.setDefaultNavigationTimeout(10_000);
    await installDesktopHostShim(page,renderer.port);await manualExercise(page,workspace,evidence,renderer.origin,renderer.url);
  });
