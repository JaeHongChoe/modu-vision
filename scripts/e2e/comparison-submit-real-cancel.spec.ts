import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page,Request,Response} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {handoffApi,handoffWithin} from './fixtures/remaining-project-handoff';
import {png} from './qa/appFlow';
const harness=require('./fixtures/harness.cjs');
const JOBS='/api/evaluation/model-comparisons/jobs',HISTORY='/api/evaluation/model-comparisons';
const hash=(b:Buffer|string)=>crypto.createHash('sha256').update(b).digest('hex');
const raw=(s:fs.BigIntStats)=>[s.dev,s.ino,s.mode,s.nlink,s.size,s.mtimeNs,s.ctimeNs].map(String);
test.use({actionTimeout:10_000});
type Custody={opened:number;closed:number;uncertain:Array<{file:string;fd:number;error:string}>};
function ownedFile(file:string,c:Custody){
 const before=fs.lstatSync(file,{bigint:true});expect(before.isFile()).toBe(true);expect(before.isSymbolicLink()).toBe(false);expect(before.nlink).toBe(1n);
 let fd:number|undefined,first:unknown,result:{identity:string[];sha256:string;size:number}|undefined;
 try{fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);c.opened++;expect(raw(fs.fstatSync(fd,{bigint:true}))).toEqual(raw(before));const bytes=fs.readFileSync(fd);expect(BigInt(bytes.length)).toBe(before.size);expect(raw(fs.fstatSync(fd,{bigint:true}))).toEqual(raw(before));expect(raw(fs.lstatSync(file,{bigint:true}))).toEqual(raw(before));result={identity:raw(before),sha256:hash(bytes),size:bytes.length};}
 catch(error){first=error;}finally{if(fd!==undefined){const own=fd;fd=undefined;try{fs.closeSync(own);c.closed++;}catch(error){c.uncertain.push({file,fd:own,error:String(error)});first??=error;}}}
 if(first!==undefined)throw first;return result!;
}
function ownedTree(root:string,c:Custody):Record<string,unknown>{
 const result:Record<string,unknown>={};const visit=(file:string,relative:string)=>{const before=fs.lstatSync(file,{bigint:true});expect(before.isSymbolicLink()).toBe(false);
  if(before.isDirectory()){const children=fs.readdirSync(file).sort();result[relative]={kind:'directory',identity:raw(before),children};for(const name of children)visit(path.join(file,name),relative==='.'?name:relative+'/'+name);expect(fs.readdirSync(file).sort()).toEqual(children);expect(raw(fs.lstatSync(file,{bigint:true}))).toEqual(raw(before));}
  else result[relative]={kind:'file',...ownedFile(file,c)};
 };visit(root,'.');return result;
}
async function exercise(page:Page,w:Workspace,e:Evidence,native:boolean,origin:string,url?:string){
 const api=handoffApi(page,origin,native),c:Custody={opened:0,closed:0,uncertain:[]};
 const source=path.join(w.root,'f057-owned-real-cancel-source');
 // Bounded real CPU work gives the ordinary cancel control time to appear.
 // No model delay/stub, fake worker, route response, training or GPU is used.
 for(const split of ['train','val','test'])for(const label of ['OK','NG']){const dir=path.join(source,split,label);fs.mkdirSync(dir,{recursive:true});const count=split==='test'?256:1;for(let i=0;i<count;i++)fs.writeFileSync(path.join(dir,label+'-'+String(i).padStart(3,'0')+'.png'),png(64,3,(x,y)=>[label==='OK'?210:30,x,(y+i)%255]),{flag:'wx'});}
 const created=await api('/api/project/create',{name:'F057 owned actual comparison cancellation',task:'classification'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'classification'});
 const project=await api('/api/project/current');expect(project.id).toBe(created.id);expect(project.source_dataset_dir).toBe(source);expect(path.dirname(project.project_dir)).toBe(native?path.join(w.userData,'projects'):w.projects);
 const q='?'+new URLSearchParams({source_dataset_path:source,task:'classification'}).toString();
 const models=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/comparison_models.py'),source,project.models_dir],{cwd:harness.REPO_ROOT,env:{...process.env,VISION_AI_STUDIO_USER_DATA_DIR:w.userData},encoding:'utf8',timeout:60_000}));expect(models.checkpoint_kind).toBe('untrained_deterministic');
 const baseline='job_fixture_incumbent',candidate='job_fixture_candidate';
 const seed=await api(JOBS,{source_dataset_path:source,task:'classification',incumbent_job_id:baseline,candidate_job_id:candidate,max_images:1,full_test:false,execution_target:'local_cpu',compute_profile_id:null,device:'cpu',incumbent_task:'classification',candidate_task:'classification',incumbent_params:{threshold:.5},candidate_params:{threshold:.5}});
 let seedJob:any;await expect.poll(async()=>{seedJob=await api(JOBS+'/'+seed.job_id+q);if(['failed','interrupted','cancelled'].includes(seedJob.status))throw Error(JSON.stringify(seedJob));return seedJob.status;},{timeout:60_000}).toBe('completed');
 const seedReport=await api(HISTORY+'/'+seedJob.report_id+q);expect(seedReport.images).toHaveLength(1);expect(seedReport.summary.error_images).toBe(0);
 const beforeJobs=await api(JOBS+q),beforeHistory=await api(HISTORY+q),beforeDeployment=await api('/api/model-deployments/active'+q),beforeDeploymentHistory=await api('/api/model-deployments/history'+q);
 expect(beforeJobs.jobs).toHaveLength(1);expect(beforeJobs.jobs[0]).toEqual(seedJob);expect(beforeDeployment.active).toBeNull();
 const panel=page.getByRole('region',{name:'현행과 후보 모델 비교',exact:true});
 const a=panel.getByLabel('비교 기준 모델',{exact:true}),b=panel.getByLabel('후보 모델',{exact:true});
 const submit=panel.getByRole('button',{name:'동일 test 이미지로 비교',exact:true}),cancel=panel.getByRole('button',{name:'비교 중단',exact:true});
 const captures:Array<any>=[],writes:Array<any>=[],errors:string[]=[];let observing=false,primary:unknown,job:any,terminal:any;
 const frame=<T>(label:string,fn:(deadline:number)=>Promise<T>)=>{const deadline=performance.now()+10_000;return handoffWithin(fn(deadline),deadline,'F057 actual cancel '+label);};
 const owner=(request:Request)=>{expect(request.frame()).toBe(page.mainFrame());expect(request.headers()['x-vision-project']).toBe(project.id);expect(JSON.parse(request.headers()['x-vision-context']).project_id).toBe(project.id);};
 const matches=(request:Request,method:string,pathname:string)=>{const u=new URL(request.url());return request.method()===method&&request.frame()===page.mainFrame()&&u.origin===origin&&u.pathname===pathname;};
 const original=async(response:Response,label:string,status:number)=>frame(label,async deadline=>{owner(response.request());expect(response.status()).toBe(status);const bytes=await handoffWithin(response.body(),deadline,'original body');expect(bytes.length).toBeLessThanOrEqual(1024*1024);expect(await handoffWithin(response.finished(),deadline,'original response completion')).toBeNull();const file=path.join(w.logs,'f057-real-cancel-'+String(captures.length)+'-'+label+'.json');fs.writeFileSync(file,bytes,{flag:'wx'});e.addFile(file);captures.push({label,file,sha256:hash(bytes),size:bytes.length,method:response.request().method(),path:new URL(response.url()).pathname,status,main_frame:true,finished:true,original_HTTP:true,controlled_response:false});return JSON.parse(bytes.toString('utf8'));});
 const observe=(request:Request)=>{const u=new URL(request.url());if(u.origin===origin&&u.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(request.method())){owner(request);writes.push({method:request.method(),path:u.pathname,body:request.postDataJSON()});}};
 const onError=(error:Error)=>errors.push(String(error));
 const retained=()=>({source:ownedTree(source,c),models:ownedTree(project.models_dir,c),annotations:ownedTree(project.annotations_dir,c),dataset:ownedTree(project.dataset_dir,c),reports:ownedTree(path.join(project.reports_dir,'model_comparisons'),c)});
 let before:ReturnType<typeof retained>|undefined,after:ReturnType<typeof retained>|undefined;
 const snapshot=()=>page.evaluate(()=>Object.fromEntries(Object.entries(localStorage).filter(([key])=>key.startsWith('vision-comparison-job:'))));
 let beforeSelection:any,acceptedSelection:any,terminalSelection:any;
 try{
  if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click();await expect(a).toBeEnabled();await a.selectOption(baseline);await b.selectOption(candidate);await expect(submit).toBeEnabled();
  const select=panel.getByLabel('저장된 모델 비교',{exact:true});await expect(select.locator('option[value="'+seedReport.comparison_id+'"]')).toHaveCount(1);
  const reportWire=page.waitForResponse(r=>matches(r.request(),'GET',HISTORY+'/'+seedReport.comparison_id),{timeout:10_000});void reportWire.catch(()=>{});await select.selectOption(seedReport.comparison_id);expect(await original(await reportWire,'seed-original-open',200)).toEqual(seedReport);
  before=retained();expect(Object.keys(before.source).filter(name=>name.startsWith('test/')&&name.endsWith('.png'))).toHaveLength(512);beforeSelection=await snapshot();await e.screenshot(page,'f057-real-cancel-owned-original-ready');
  page.on('request',observe);page.on('pageerror',onError);observing=true;
  const postWire=page.waitForResponse(r=>matches(r.request(),'POST',JOBS),{timeout:10_000});void postWire.catch(()=>{});await submit.click();const post=await postWire;const payload=post.request().postDataJSON();expect(payload).toMatchObject({source_dataset_path:source,task:'classification',incumbent_job_id:baseline,candidate_job_id:candidate,full_test:true,execution_target:'local_cpu',compute_profile_id:null,device:'cpu',incumbent_task:'classification',candidate_task:'classification'});job=await original(post,'actual-created-job',202);
  expect(job.job_id).not.toBe(seedJob.job_id);expect(job.payload).toEqual(payload);expect(job.durable).toBe(true);expect(job.resumable).toBe(false);expect(['queued','running']).toContain(job.status);
  // The test refuses a fast-completed job; late cancellation is not credited.
  await expect(cancel).toBeVisible();await expect(cancel).toBeEnabled();await expect(a).toBeDisabled();await expect(b).toBeDisabled();acceptedSelection=await snapshot();
  const terminalWire=page.waitForResponse(async response=>{if(!matches(response.request(),'GET',JOBS+'/'+job.job_id))return false;const u=new URL(response.url());if(u.searchParams.get('source_dataset_path')!==source||u.searchParams.get('task')!=='classification'||response.status()!==200)return false;const bytes=await response.body();return JSON.parse(bytes.toString('utf8')).status==='cancelled';},{timeout:60_000});void terminalWire.catch(()=>{});
  const cancelWire=page.waitForResponse(r=>matches(r.request(),'POST',JOBS+'/'+job.job_id+'/cancel'),{timeout:10_000});void cancelWire.catch(()=>{});await cancel.click();const cancelResponse=await cancelWire;const cancelURL=new URL(cancelResponse.url());expect(cancelURL.searchParams.get('source_dataset_path')).toBe(source);expect(cancelURL.searchParams.get('task')).toBe('classification');const requested=await original(cancelResponse,'actual-cancel-request',200);expect(requested.job_id).toBe(job.job_id);expect(requested.cancel_requested).toBe(1);expect(requested.cancel_disposition).not.toBe('late_terminal');expect(requested.status).not.toBe('completed');
  terminal=await original(await terminalWire,'actual-original-terminal-poll',200);expect(terminal).toMatchObject({job_id:job.job_id,status:'cancelled',state:'aborted',cancel_requested:1,report_id:null,result_available:false,durable:true,resumable:false,payload});expect(terminal.cancel_disposition).toBe('requested');expect(terminal.binding).toEqual(job.binding);expect(terminal.attempts).toEqual(expect.any(Array));
  await expect(panel.getByRole('status')).toContainText('cancelled');await expect(a).toBeEnabled();await expect(b).toBeEnabled();await expect(a).toHaveValue(baseline);await expect(b).toHaveValue(candidate);await expect(submit).toBeEnabled();terminalSelection=await snapshot();expect(terminalSelection).toEqual(acceptedSelection);await e.screenshot(page,'f057-real-cancel-original-terminal-receipt');
  expect(await api(JOBS+'/'+job.job_id+q)).toEqual(terminal);const afterJobs=await api(JOBS+q);expect(afterJobs.jobs).toHaveLength(2);expect(afterJobs.jobs.find((row:any)=>row.job_id===seedJob.job_id)).toEqual(seedJob);expect(afterJobs.jobs.find((row:any)=>row.job_id===job.job_id)).toEqual(terminal);
  expect(await api(HISTORY+q)).toEqual(beforeHistory);expect(await api('/api/model-deployments/active'+q)).toEqual(beforeDeployment);expect(await api('/api/model-deployments/history'+q)).toEqual(beforeDeploymentHistory);
  // Only normal cleanup of this accepted job is awaited; no report is staged
  // or discarded by this test. Original report bytes and all retained roots
  // must actually match after the worker's own finalization becomes visible.
  await expect.poll(()=>fs.readdirSync(path.join(project.reports_dir,'model_comparisons')).sort(),{timeout:10_000}).toEqual(Object.keys(before.reports).filter(name=>name!=='.'));
  after=retained();expect(after).toEqual(before);expect(writes).toHaveLength(2);expect(writes[0]).toEqual({method:'POST',path:JOBS,body:payload});expect(writes[1].method).toBe('POST');expect(writes[1].path).toBe(JOBS+'/'+job.job_id+'/cancel');expect(errors).toEqual([]);
  const restoredWire=page.waitForResponse(r=>matches(r.request(),'GET',HISTORY+'/'+seedReport.comparison_id),{timeout:10_000});void restoredWire.catch(()=>{});await select.selectOption(seedReport.comparison_id);expect(await original(await restoredWire,'original-history-reopen',200)).toEqual(seedReport);await expect(select).toHaveValue(seedReport.comparison_id);await e.screenshot(page,'f057-real-cancel-original-report-preserved-reopened');
  const proof={schema:'modu-vision.f057-original-submit-cancel-proof/v1',cell:'F057.comparison-manual-submit.cancel',native,project,source,generated_test_images:512,models,original_seed_job:seedJob,original_seed_report:seedReport,before_jobs:beforeJobs,after_jobs:afterJobs,before_history:beforeHistory,after_history:await api(HISTORY+q),before_deployment:beforeDeployment,after_deployment:await api('/api/model-deployments/active'+q),before_deployment_history:beforeDeploymentHistory,after_deployment_history:await api('/api/model-deployments/history'+q),created_job:job,cancel_reply:requested,terminal_job:terminal,original_payload:payload,original_bodies:captures,retained_before:before,retained_after:after,original_choice_before:beforeSelection,original_choice_after_acceptance:acceptedSelection,original_choice_at_terminal:terminalSelection,writes,page_errors:errors,actual_UI_cancel:true,actual_server_job_cancel:true,transport_aborted:false,response_fabricated:false,scope_navigation_abandonment:false,worker_process_exit_claim:false,remaining_four_cells_pending:true,model_deployment:false,model_training:false,quality_approved:false,installed_native_accepted:false};
  const file=path.join(w.logs,'f057-original-submit-cancel-proof.json');fs.writeFileSync(file,JSON.stringify(proof,null,2)+'\n',{flag:'wx'});e.addFile(file);e.note('f057_original_submit_cancel',proof);
 }catch(error){primary=error;}
 finally{
  // Teardown never touches a foreign job. A failure may leave only this
  // original accepted job active; ask its original scoped backend to cancel.
  let cleanup:any=null;
  if(job?.job_id&&terminal?.status!=='cancelled')try{const seen=await api(JOBS+'/'+job.job_id+q);expect(seen.payload).toEqual(job.payload);if(['queued','running'].includes(seen.status)){await api(JOBS+'/'+job.job_id+'/cancel'+q,{},'POST');await expect.poll(async()=>{cleanup=await api(JOBS+'/'+job.job_id+q);return ['cancelled','completed','failed','interrupted'].includes(cleanup.status);},{timeout:60_000}).toBe(true);}else cleanup=seen;}catch(error){primary??=error;}
  if(observing){page.off('request',observe);page.off('pageerror',onError);}
  try{expect(c.opened).toBe(c.closed);expect(c.uncertain).toEqual([]);}catch(error){primary??=error;}
  e.note('f057_original_submit_cancel_closure',{opened:c.opened,closed:c.closed,uncertain:c.uncertain,custom_routes:0,cleanup_scope:job?.job_id||null,cleanup_receipt:cleanup,first_exception:primary===undefined?null:String(primary),original_backend_process_closure_required_from_fixture:true,worker_process_exit_claim:false});
 }
 if(primary!==undefined)throw primary;
}
test('original manual comparison submit cancels its accepted owned CPU job and preserves earlier records',async({page,renderer,workspace,evidence})=>{await installDesktopHostShim(page,renderer.port);await exercise(page,workspace,evidence,false,renderer.origin,renderer.url);});
test('native original manual comparison submit cancels its accepted owned CPU job and preserves earlier records',{tag:'@electron'},async({electronSession,workspace,evidence})=>{expect(process.env.MV_E2E_NATIVE_FULL_BODY_READS).toBe('1');const backend=await electronSession.waitForBackend();await exercise(electronSession.window,workspace,evidence,true,`http://127.0.0.1:${backend.port}`);});
