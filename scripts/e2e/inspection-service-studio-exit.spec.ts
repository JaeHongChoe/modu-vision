import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {test,expect} from './fixtures/test';
const harness=require('./fixtures/harness.cjs');
const sha=(file:string)=>createHash('sha256').update(fs.readFileSync(file)).digest('hex');
function identity(projectDir:string){
 const code="import json,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from backend.engine.runtime_process_control import owned_inspection_process;root=Path(sys.argv[2]);config=json.loads((root/'service.json').read_text());p=owned_inspection_process(config,root/'state');print(json.dumps(None if p is None else {'pid':p.pid,'birth':p.create_time(),'command_sha256':config['process_command_sha256'],'state_dir':str(root/'state')}))";
 return JSON.parse(execFileSync(harness.resolvePython(),['-I','-B','-c',code,harness.REPO_ROOT,path.join(projectDir,'runtime_service')],{encoding:'utf8',timeout:10_000}).trim());
}
function files(root:string):Record<string,string>{
 const result:Record<string,string>={};
 function visit(folder:string){for(const name of fs.readdirSync(folder)){
  const file=path.join(folder,name),stat=fs.lstatSync(file);
  expect(stat.isSymbolicLink()).toBe(false);
  if(stat.isDirectory())visit(file);else if(stat.isFile())result[path.relative(root,file)]=sha(file);
 }}visit(root);return result;
}

// Actual CPU execution through the independent source daemon after only this
// fixture's Electron application exits. Generated weights and synthetic review
// setup are explicit; no publisher, OS registration or model quality is implied.
test('native Studio exit preserves one owned CPU inspection and durable duplicate result',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 test.setTimeout(240_000);
 const tool=path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/operator_runtime.py');
 const env={...process.env,HOME:workspace.home,USERPROFILE:workspace.home,XDG_CACHE_HOME:path.join(workspace.home,'.cache'),TORCH_HOME:path.join(workspace.home,'.cache','torch'),HF_HOME:path.join(workspace.home,'.cache','huggingface'),VISION_AI_STUDIO_USER_DATA_DIR:workspace.userData};
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[tool,workspace.root],{cwd:harness.REPO_ROOT,env,encoding:'utf8',timeout:90_000}).trim());
 const root=path.join(fixture.project.project_dir,'runtime_service');
 const config=JSON.parse(fs.readFileSync(path.join(root,'service.json'),'utf8'));evidence.redact(config.token);
 const backend=await electronSession.waitForBackend(),page=electronSession.window;
 const api=(route:string,body?:unknown)=>page.evaluate(async({port,route,body})=>{
  const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
  if(!r.ok)throw Error(`Owned Studio HTTP ${r.status}: ${await r.text()}`);return r.json();
 },{port:backend.port,route,body});
 const service=async(route:string,body?:unknown)=>{
  const r=await fetch(`http://127.0.0.1:${config.port}${route}`,{method:body?'POST':'GET',headers:{'X-Vision-Token':config.token,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(10_000)});
  expect(r.status,await r.clone().text()).toBe(body?202:200);return r.json() as Promise<any>;
 };
 let closed=false;
 try{
  await api('/api/project/open',{project_dir:fixture.project.project_dir});await page.reload();
  await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(fixture.project.name);
  await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();
  const panel=page.getByRole('region',{name:'검사 서비스 배포',exact:true});
  await expect(panel).toContainText('현재 응답: stopped');expect(identity(fixture.project.project_dir)).toBeNull();
  const started=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/runtime-services/start'&&r.request().method()==='POST');
  await panel.getByRole('button',{name:'서비스 시작',exact:true}).click();expect((await started).status()).toBe(200);
  await expect(panel).toContainText('현재 응답: ready',{timeout:60_000});
  const active=await api('/api/runtime-services'),epoch=identity(fixture.project.project_dir);
  expect(epoch).not.toBeNull();expect(active.active.deployment_id).toBe(fixture.active.deployment_id);
  expect(active.runtime.device).toBe('cpu');expect(active.native_install.registered).toBe(false);
  const release=active.active.release,releaseFiles=files(release.package_path),policyHash=sha(release.release_policy);
  const runtimeBefore=await service('/v1/runtime'),jobsBefore=await service('/v1/jobs');
  expect(runtimeBefore.status).toBe('ready');expect(runtimeBefore.manifest_sha256).toBe(release.manifest_sha256);
  await evidence.screenshot(page,'native-owned-cpu-service-before-studio-exit');
  // Close the original fixture handle. Neither a numeric PID nor a broad app
  // name supplies shutdown authority, and no second daemon is started here.
  await electronSession.app.close();closed=true;
  expect(page.isClosed()).toBe(true);expect(await harness.waitForPortClosed(backend.port,10_000)).toBe(true);
  expect(identity(fixture.project.project_dir)).toEqual(epoch);
  const runtimeAfter=await service('/v1/runtime');expect(runtimeAfter.status).toBe('ready');
  expect(runtimeAfter.manifest_sha256).toBe(runtimeBefore.manifest_sha256);
  const image=fixture.parity_cohort[0];expect(Object.hasOwn(fixture.images,image)).toBe(true);
  expect(sha(image)).toBe(fixture.images[image]);
  const payload={image_path:image,image_id:'owned-studio-exit-synthetic-part',idempotency_key:'owned-studio-exit-one-image'};
  const admitted=await service('/v1/jobs/file',payload),duplicate=await service('/v1/jobs/file',payload);
  expect(duplicate.job_id).toBe(admitted.job_id);
  let result:any;
  await expect.poll(async()=>{
   result=await service('/v1/jobs/'+admitted.job_id);return result.state;
  },{timeout:90_000,intervals:[100,250,500,1000],message:'Actual source daemon must finish its admitted CPU image after Studio exit'}).toBe('completed');
  expect(result.error).toBeNull();expect(result.dead_letter_reason).toBeNull();
  expect(result.image_sha256).toBe(fixture.images[image]);expect(result.source).toBe('file');
  expect(result.idempotency_key).toBe(payload.idempotency_key);
  // The HTTP contract returns decoded result/runtime_binding objects. Read the
  // exact owned durable row without opening a writable connection to bind the
  // stored bytes to its admission hash and the decoded HTTP readback.
  const read="import json,sqlite3,sys;from pathlib import Path;p=Path(sys.argv[1]);assert p.is_file() and not p.is_symlink();c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;r=c.execute('SELECT runtime_binding_json,runtime_binding_sha256,result_json FROM jobs WHERE job_id=?',(sys.argv[2],)).fetchone();assert r is not None;print(json.dumps(dict(r)));c.close()";
  const stored=JSON.parse(execFileSync(harness.resolvePython(),['-I','-B','-c',read,path.join(root,'state','inspection_service.sqlite3'),admitted.job_id],{encoding:'utf8',timeout:10_000}).trim());
  const binding=JSON.parse(stored.runtime_binding_json),modelResult=JSON.parse(stored.result_json);
  expect(result.runtime_binding).toEqual(binding);expect(result.result).toEqual(modelResult);
  expect(createHash('sha256').update(stored.runtime_binding_json).digest('hex')).toBe(result.runtime_binding_sha256);
  expect(stored.runtime_binding_sha256).toBe(result.runtime_binding_sha256);
  expect(binding.manifest_sha256).toBe(release.manifest_sha256);expect(binding.package_path).toBe(release.package_path);expect(binding.device).toBe('cpu');
  expect(modelResult.runtime_identity).toEqual(binding);expect(['OK','NG','REVIEW']).toContain(modelResult.final_verdict);
  expect(modelResult.status).not.toBe('timeout');expect(modelResult.error_message??null).toBeNull();
  expect(result.verdict).toBe(modelResult.final_verdict);expect(result.model_verdict).toBe(modelResult.final_verdict);
  const persisted=await service('/v1/jobs/'+admitted.job_id),again=await service('/v1/jobs/file',payload);
  expect(persisted).toEqual(result);expect(again.job_id).toBe(admitted.job_id);
  const jobsAfter=await service('/v1/jobs');
  expect(jobsAfter.jobs.length).toBe(jobsBefore.jobs.length+1);
  expect(jobsAfter.jobs.filter((j:any)=>j.job_id===admitted.job_id)).toHaveLength(1);
  const events=await service('/v1/jobs/'+admitted.job_id+'/events');
  expect(events.events.filter((e:any)=>e.state==='completed')).toHaveLength(1);
  expect(identity(fixture.project.project_dir)).toEqual(epoch);expect(files(release.package_path)).toEqual(releaseFiles);
  expect(sha(release.release_policy)).toBe(policyHash);for(const [file,digest] of Object.entries(fixture.images))expect(sha(file)).toBe(digest);
  evidence.note('studio_exit_inspection',{fixture,epoch,active,runtimeBefore,runtimeAfter,admitted,duplicate,result,persisted,events,jobs_before:jobsBefore.jobs.length,jobs_after:jobsAfter.jobs.length,release_files:releaseFiles,release_policy_sha256:policyHash,source_studio_closed:closed,source_studio_backend_port_closed:true,exact_owned_daemon_epoch_preserved:true,actual_cpu_job_after_studio_exit:true,one_input_one_result:true,fixture_home:workspace.home,controlled_synthetic_approval_fixture:true,model_quality_approved:false,installed_target_verified:false,os_registration_executed:false,device_or_reboot_verified:false,complete_process_tree_verified:false,gpu_used:false,windows_excluded:true});
 }finally{
  const stopped=JSON.parse(execFileSync(harness.resolvePython(),[tool,'stop',fixture.project.project_dir],{cwd:harness.REPO_ROOT,env,encoding:'utf8',timeout:30_000}).trim());
  evidence.note('owned_service_cleanup',stopped);expect(stopped.status).toBe('stopped');
  expect(identity(fixture.project.project_dir)).toBeNull();expect(await harness.waitForPortClosed(config.port,10_000)).toBe(true);
 }
});
