import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page,Request,Route} from '@playwright/test';
import {test,expect} from './fixtures/test';
const harness=require('./fixtures/harness.cjs');
const sha=(file:string)=>createHash('sha256').update(fs.readFileSync(file)).digest('hex');
function hashes(root:string):Record<string,string>{
 const result:Record<string,string>={};
 const visit=(folder:string)=>{for(const item of fs.readdirSync(folder,{withFileTypes:true})){
  const file=path.join(folder,item.name);expect(item.isSymbolicLink()).toBe(false);
  if(item.isDirectory())visit(file);else if(item.isFile())result[path.relative(root,file).split(path.sep).join('/')]=sha(file);
 }};visit(root);return result;
}
function identity(projectDir:string){
 const code="import json,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from backend.engine.runtime_process_control import owned_inspection_process;root=Path(sys.argv[2]);config=json.loads((root/'service.json').read_text());p=owned_inspection_process(config,root/'state');print(json.dumps(None if p is None else {'pid':p.pid,'birth':p.create_time(),'command_sha256':config['process_command_sha256'],'state_dir':str(root/'state')}))";
 return JSON.parse(execFileSync(harness.resolvePython(),['-I','-B','-c',code,harness.REPO_ROOT,path.join(projectDir,'runtime_service')],{encoding:'utf8',timeout:10_000}).trim());
}
async function openProject(page:Page,projectDir:string){
 await page.getByTitle('프로젝트 관리',{exact:true}).click();const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});
 await dialog.getByRole('button',{name:'폴더에서 열기',exact:true}).click();await dialog.getByPlaceholder('/path/to/project',{exact:true}).fill(projectDir);
 const reply=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/project/open'&&r.request().method()==='POST');
 await dialog.getByRole('button',{name:'프로젝트 열기',exact:true}).click();expect((await reply).status()).toBe(200);await expect(dialog).toHaveCount(0);
}
async function delivery(page:Page){
 await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();
 const panel=page.getByRole('region',{name:'검사 서비스 배포',exact:true});await expect(panel).toBeVisible();return panel;
}
async function closeDelivery(page:Page){
 await page.getByRole('button',{name:'패키지·장치·설치·진단 닫기',exact:true}).click();await expect(page.getByRole('region',{name:'검사 서비스 배포',exact:true})).toHaveCount(0);
}

// This uses actual converted weights and OpenVINO CPU execution. The generated
// all-OK model and permissive synthetic reviews are controls, not human truth.
test('native reviewed OpenVINO CPU service preserves exact epoch through empty invalid error cancel and reopen',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 test.setTimeout(480_000);
 const page=electronSession.window,status=await electronSession.waitForBackend();
 const api=(route:string,body?:unknown,method?:string)=>page.evaluate(async({port,route,body,method})=>{
  const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
  if(!response.ok)throw Error(`Owned converted service ${route}: HTTP ${response.status}`);return response.json();
 },{port:status.port,route,body,method});
 expect(process.env.MV_E2E_OPENVINO_PYTHON,'This actual IR case requires the explicitly selected interpreter').toBeTruthy();
 const capabilities=await api('/api/export/runtime-capabilities');expect(capabilities.openvino.available,JSON.stringify(capabilities.openvino)).toBe(true);expect(capabilities.openvino.devices).toContain('CPU');
 const env={...process.env,HOME:workspace.home,USERPROFILE:workspace.home,XDG_CACHE_HOME:path.join(workspace.home,'.cache'),TORCH_HOME:path.join(workspace.home,'.cache','torch'),HF_HOME:path.join(workspace.home,'.cache','huggingface'),VISION_AI_STUDIO_USER_DATA_DIR:workspace.userData};
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/converted_flow_control.py'),workspace.root],{cwd:harness.REPO_ROOT,env,encoding:'utf8',timeout:120_000}).trim());
 const emptyProject=await api('/api/project/create',{name:'Empty converted service controls',task:'classification',project_dir:path.join(workspace.projects,'empty-converted-control')});
 await api('/api/project/open',{project_dir:fixture.project.project_dir});
 let release:()=>void=()=>{},holdHandler:((route:Route)=>Promise<void>)|undefined,removeObservers:()=>void=()=>{};
 const mutations:Array<{method:string;pathname:string}>=[];
 const observe=(r:Request)=>{const pathname=new URL(r.url()).pathname;if(r.method()!=='GET'&&pathname.startsWith('/api/runtime-services'))mutations.push({method:r.method(),pathname});};
 page.on('request',observe);
 try{
  await page.reload();await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();
  const library=page.getByRole('region',{name:'저장된 검사 패키지 보관함'});
  await library.getByRole('article').filter({hasText:'original_cpu_control'}).getByRole('button',{name:'최적화로 이동',exact:true}).click();
  const optimization=page.getByRole('region',{name:'Runtime 최적화와 양자화'}),cohort=optimization.getByRole('group',{name:'변환 오차 검증 · test / val'});
  await expect(cohort.getByRole('checkbox')).toHaveCount(16);for(const checkbox of await cohort.getByRole('checkbox').all())await checkbox.uncheck();
  await cohort.getByRole('checkbox',{name:'ok_00.png',exact:true}).check();await cohort.getByRole('checkbox',{name:'ng_00.png',exact:true}).check();
  await optimization.getByRole('button',{name:'독립 후보 패키지 생성',exact:true}).click();
  await expect(optimization.getByRole('status').first()).toContainText('completed',{timeout:160_000});
  await expect(optimization.getByText('2 / 2 동일 판정·공간 결과',{exact:false})).toBeVisible();
  await optimization.getByRole('button',{name:'다음',exact:true}).click();await expect(optimization.getByText('2 / 2 · passed',{exact:false})).toBeVisible();
  const precision=optimization.getByRole('group',{name:'정밀도·Runtime 별도 승인'});
  await precision.getByLabel('검토자',{exact:true}).fill('Synthetic fixture authority');
  await precision.getByLabel('검토 근거',{exact:true}).fill('Actual two-image CPU and IR process controls; no manufacturing quality acceptance');
  await precision.getByRole('checkbox').check();await precision.getByRole('button',{name:'검토 후 새 승인 패키지 생성',exact:true}).click();
  const review=optimization.getByRole('group',{name:'변환 후 전체 흐름 검토'});await expect(review.getByText('정상 1 · 불량 1',{exact:false})).toBeVisible({timeout:40_000});
  await review.getByLabel('변환 전체 흐름 검토자',{exact:true}).fill('Synthetic fixture authority');
  await review.getByLabel('변환 전체 흐름 검토 이유',{exact:true}).fill('Actual frozen OK/NG controls under permissive synthetic policy; not human quality acceptance');
  await review.getByLabel('변환 전체 흐름 직접 검토',{exact:true}).check();await review.getByRole('button',{name:'변환 전체 흐름 검토 저장',exact:true}).click();
  await expect(review.getByRole('status')).toContainText('변환 후 전체 흐름 검토를 저장했습니다.');
  await library.getByRole('button',{name:'새로고침',exact:true}).click();
  const approved=library.getByRole('article').filter({hasText:/approved_runtime_/});await expect(approved).toHaveCount(1);
  const saved=(await api('/api/product-delivery/packages')).packages.find((row:any)=>row.name.startsWith('approved_runtime_'));
  expect(saved.scope_matches&&saved.approval_present&&saved.integrity==='verified').toBe(true);
  const packageFiles=hashes(saved.package_path);expect(Object.keys(packageFiles)).toContain('openvino_models.json');
  const prepare=approved.getByRole('button',{name:'배포 준비',exact:true});await prepare.click();await expect(prepare).toBeEnabled();
  let panel=page.getByRole('region',{name:'검사 서비스 배포',exact:true});
  await expect(panel.locator(':scope > div').getByLabel('배포할 저장 패키지',{exact:true})).toHaveValue(saved.package_id);
  await panel.getByLabel('서비스 실행 장치',{exact:true}).selectOption('openvino:CPU');await panel.getByLabel('서비스 검토자',{exact:true}).fill('Synthetic fixture authority');
  const applied=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/runtime-services/apply'&&r.request().method()==='POST');
  await panel.getByRole('button',{name:'승인 패키지 적용',exact:true}).click();expect((await applied).status()).toBe(200);
  await expect(panel).toContainText('현재 응답: ready · 장치 openvino:CPU',{timeout:60_000});
  const active=await api('/api/runtime-services'),epoch=identity(fixture.project.project_dir);expect(epoch).not.toBeNull();
  expect(active.active.release.manifest_sha256).toBe(saved.manifest_sha256);expect(active.runtime.manifest_sha256).toBe(saved.manifest_sha256);
  expect(active.runtime.device).toBe('openvino:CPU');expect(active.active.release.whole_flow_review.runtime_review_sha256).toMatch(/^[0-9a-f]{64}$/);
  expect(active.native_install.registered).toBe(false);expect(active.native_install.verified).toBe(false);
  const serviceRoot=path.join(fixture.project.project_dir,'runtime_service'),config=JSON.parse(fs.readFileSync(path.join(serviceRoot,'service.json'),'utf8'));evidence.redact(config.token);
  const input=fixture.heldout[1],inputHash=sha(input);expect(fixture.images[path.relative(fixture.source,input).split(path.sep).join('/')]).toBe(inputHash);
  const uploaded=await fetch(`http://127.0.0.1:${config.port}/v1/jobs/upload`,{method:'POST',headers:{'X-Vision-Token':config.token},body:fs.readFileSync(input),signal:AbortSignal.timeout(10_000)});
  expect(uploaded.status).toBe(202);const admitted=await uploaded.json() as any;let job:any;
  await expect.poll(async()=>{const response=await fetch(`http://127.0.0.1:${config.port}/v1/jobs/${admitted.job_id}`,{headers:{'X-Vision-Token':config.token},signal:AbortSignal.timeout(10_000)});expect(response.status).toBe(200);job=await response.json();return job.state;},{timeout:40_000,intervals:[100,250,500,1000]}).toBe('completed');
  expect(job.error).toBeNull();expect(job.image_sha256).toBe(inputHash);expect(job.runtime_binding.device).toBe('openvino:CPU');
  expect(job.runtime_binding.manifest_sha256).toBe(saved.manifest_sha256);expect(job.result.runtime_identity).toEqual(job.runtime_binding);
  expect(job.model_verdict).toBe('OK'); // The generated all-OK weights miss this synthetic NG image.
  const releaseFiles=hashes(active.active.release.package_path),policyHash=sha(active.active.release.release_policy);
  const preserved=async()=>{const state=await api('/api/runtime-services');expect(state.active).toEqual(active.active);expect(state.history).toEqual(active.history);expect(state.runtime.manifest_sha256).toBe(saved.manifest_sha256);expect(state.runtime.device).toBe('openvino:CPU');expect(identity(fixture.project.project_dir)).toEqual(epoch);return state;};
  const ready=async()=>{await expect(panel).toContainText('현재 응답: ready · 장치 openvino:CPU');await expect(panel).toContainText(saved.manifest_sha256);};
  await panel.scrollIntoViewIfNeeded();await evidence.screenshot(page,'native-actual-reviewed-ir-service-and-input-completed');

  const manual=()=>panel.getByLabel('승인 패키지 경로',{exact:true}),apply=()=>panel.getByRole('button',{name:'승인 패키지 적용',exact:true});
  await panel.locator('details').filter({has:page.getByLabel('승인 패키지 경로',{exact:true})}).locator(':scope > summary').click();
  const beforeEmpty=mutations.length;await manual().fill('');await expect(apply()).toBeDisabled();await expect(panel).toContainText('승인 포함 패키지가 필요합니다.');
  expect(mutations.length).toBe(beforeEmpty);await preserved();await apply().scrollIntoViewIfNeeded();await expect(apply()).toBeInViewport();await evidence.screenshot(page,'native-ir-empty-package-path-disabled-no-apply');
  const missing=path.join(workspace.root,'absent-reviewed-converted-package');expect(fs.existsSync(missing)).toBe(false);await manual().fill(missing);
  const invalidReply=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/runtime-services/apply'&&r.request().method()==='POST');
  await apply().click();const invalid=await invalidReply;expect(invalid.status()).toBe(409);const invalidBody=await invalid.json();
  const invalidAlert=panel.getByRole('alert').filter({hasText:String(invalidBody.detail)});await expect(invalidAlert).toBeVisible();await invalidAlert.scrollIntoViewIfNeeded();await expect(invalidAlert).toBeInViewport();
  await preserved();expect(fs.existsSync(missing)).toBe(false);await evidence.screenshot(page,'native-ir-absent-package-actual-409-keeps-epoch');
  await manual().fill(saved.package_path);await expect(manual()).toHaveValue(saved.package_path);
  await expect(panel.getByLabel('서비스 실행 장치',{exact:true})).toHaveValue('openvino:CPU');
  await expect(panel.getByLabel('서비스 검토자',{exact:true})).toHaveValue('Synthetic fixture authority');
  const flowRevision=await panel.getByLabel('서비스 전체 흐름 검토 revision',{exact:true}).inputValue();
  const configBeforeError=sha(path.join(serviceRoot,'service.json'));
  let applyErrorRequests=0,applyErrorPayload:any;
  const applyTransport=async(route:Route)=>{
   if(route.request().method()!=='POST'){await route.continue();return;}
   applyErrorRequests++;applyErrorPayload=route.request().postDataJSON();
   // This exact valid IR apply is fulfilled before backend dispatch. It is a
   // controlled transport failure, not an actual service or model failure.
   await route.fulfill({status:503,json:{detail:'Controlled converted IR apply transport failure'}});
  };
  await page.route('**/api/runtime-services/apply',applyTransport);
  const applyErrorReply=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/runtime-services/apply'&&r.request().method()==='POST');
  await apply().click();expect((await applyErrorReply).status()).toBe(503);expect(applyErrorRequests).toBe(1);
  expect(applyErrorPayload).toEqual({package_path:saved.package_path,device:'openvino:CPU',reviewer:'Synthetic fixture authority',whole_flow_revision_id:flowRevision.trim()||null});
  const applyError=panel.getByRole('alert').filter({hasText:'Controlled converted IR apply transport failure'});
  await expect(applyError).toBeVisible();await applyError.scrollIntoViewIfNeeded();await expect(applyError).toBeInViewport();
  await page.unroute('**/api/runtime-services/apply',applyTransport);await preserved();
  expect(sha(path.join(serviceRoot,'service.json'))).toBe(configBeforeError);expect(hashes(active.active.release.package_path)).toEqual(releaseFiles);expect(sha(active.active.release.release_policy)).toBe(policyHash);
  await evidence.screenshot(page,'native-ir-controlled-apply-503-same-owned-epoch');
  const isStatus=(request:Request)=>request.method()==='GET'&&new URL(request.url()).pathname==='/api/runtime-services';
  const transport=async(route:Route)=>{if(isStatus(route.request()))await route.fulfill({status:503,json:{detail:'Controlled converted IR status transport failure'}});else await route.continue();};
  await page.route('**/api/runtime-services',transport);await panel.getByRole('button',{name:'상태 새로고침',exact:true}).click();
  const error=panel.getByRole('alert').filter({hasText:'Controlled converted IR status transport failure'});await expect(error).toBeVisible();await error.scrollIntoViewIfNeeded();await expect(error).toBeInViewport();
  expect(identity(fixture.project.project_dir)).toEqual(epoch);await evidence.screenshot(page,'native-ir-controlled-status-503-same-owned-epoch');
  await page.unroute('**/api/runtime-services',transport);
  const retryReply=page.waitForResponse(r=>isStatus(r.request()));await panel.getByRole('button',{name:'상태 새로고침',exact:true}).click();expect((await retryReply).status()).toBe(200);await ready();await preserved();
  // Refresh does not clear the cached alert; an actual close/reopen remount does.
  await closeDelivery(page);panel=await delivery(page);await ready();

  let reached!:()=>void,handled!:()=>void;
  const held=new Promise<void>(r=>reached=r),done=new Promise<void>(r=>handled=r),released=new Promise<void>(r=>release=r);
  let captured:any,heldRequest:Request|undefined,holdError:unknown,armed=true;
  holdHandler=async route=>{
   if(!armed||!isStatus(route.request())){await route.continue();return;}armed=false;heldRequest=route.request();
   try{
    // This separate renderer-authenticated real GET has exact original pins.
    // The held original native UI200 is a controlled snapshot, not original
    // delayed HTTP200 provenance and no session token is extracted.
    captured=await api('/api/runtime-services');reached();await released;await route.fulfill({status:200,json:captured});
   }catch(cause){holdError=cause;reached();}finally{handled();}
  };
  await page.route('**/api/runtime-services',holdHandler);await panel.getByRole('button',{name:'상태 새로고침',exact:true}).click();await held;if(holdError)throw holdError;
  expect(captured.active).toEqual(active.active);expect(captured.history).toEqual(active.history);expect(captured.runtime.manifest_sha256).toBe(saved.manifest_sha256);expect(identity(fixture.project.project_dir)).toEqual(epoch);
  let resolveDisposition!:(value:{kind:'finished'|'failed';failure:string|null})=>void;
  const disposition=new Promise<{kind:'finished'|'failed';failure:string|null}>(r=>resolveDisposition=r);
  const finished=(request:Request)=>{if(request===heldRequest)resolveDisposition({kind:'finished',failure:null});};
  const failed=(request:Request)=>{if(request===heldRequest)resolveDisposition({kind:'failed',failure:request.failure()?.errorText??null});};
  page.on('requestfinished',finished);page.on('requestfailed',failed);removeObservers=()=>{page.off('requestfinished',finished);page.off('requestfailed',failed);};
  const beforeCancel=mutations.length;await closeDelivery(page);await openProject(page,emptyProject.project_dir);panel=await delivery(page);
  await expect(panel).toContainText('현재 응답: stopped');await expect(panel.getByRole('button',{name:'서비스 시작',exact:true})).toBeDisabled();
  const emptyState=await api('/api/runtime-services');expect(emptyState.active).toBeNull();expect(emptyState.history).toEqual([]);
  release();await done;let timer:ReturnType<typeof setTimeout>|undefined;
  const outcome=await Promise.race([disposition,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Exact original IR status Request neither finished nor failed')),10_000);})]).finally(()=>{if(timer)clearTimeout(timer);});removeObservers();
  if(outcome.kind==='finished'){if(holdError)throw holdError;const response=await heldRequest!.response();expect(response).not.toBeNull();expect(response!.status()).toBe(200);expect((await response!.json()).runtime.manifest_sha256).toBe(saved.manifest_sha256);expect(await response!.finished()).toBeNull();}
  else expect(outcome.failure).toContain('ERR_ABORTED');
  await page.evaluate(()=>new Promise<void>(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>r()))));
  await page.unroute('**/api/runtime-services',holdHandler);holdHandler=undefined;
  await expect(panel).toContainText('현재 응답: stopped');await expect(panel).not.toContainText(saved.manifest_sha256);expect(mutations.length).toBe(beforeCancel);expect(identity(fixture.project.project_dir)).toEqual(epoch);
  await panel.scrollIntoViewIfNeeded();await evidence.screenshot(page,'native-ir-cancelled-view-empty-project-no-stale-epoch');
  await closeDelivery(page);await openProject(page,fixture.project.project_dir);panel=await delivery(page);await ready();await preserved();
  await page.reload();panel=await delivery(page);await ready();const reopened=await preserved();
  await panel.scrollIntoViewIfNeeded();await evidence.screenshot(page,'native-ir-original-manifest-and-epoch-reopened');
  expect(hashes(saved.package_path)).toEqual(packageFiles);expect(hashes(active.active.release.package_path)).toEqual(releaseFiles);expect(sha(active.active.release.release_policy)).toBe(policyHash);
  expect(hashes(fixture.package.package_path)).toEqual(fixture.original_package_files);expect(hashes(fixture.source)).toEqual(fixture.images);expect(hashes(fixture.project.models_dir)).toEqual(fixture.model_files);
  evidence.note('converted_service_controls',{record_id:'F099',action:'actual-ir-owned-managed-service',dimensions:['empty','invalid','error','cancel','reopen'],fixture,capabilities,saved,active,epoch,input_sha256:inputHash,job,package_files:packageFiles,release_files:releaseFiles,policy_sha256:policyHash,empty:{blank_package_disabled:true,no_apply:true},invalid:{status:409,body:invalidBody,absent_path_not_created:true,active_history_epoch_preserved:true},error:{action:'POST /api/runtime-services/apply',controlled_status:503,exact_reviewed_ir_payload:applyErrorPayload,controlled_request_count:applyErrorRequests,backend_dispatch_executed:false,actual_service_failed:false,active_history_epoch_preserved:true,config_sha256:configBeforeError,release_package_policy_preserved:true},status_refresh_control:{action:'GET /api/runtime-services',controlled_status:503,actual_retry_status:200,qualifies_apply_error:false},cancel:{explicit_close:true,actual_project_switch:true,empty_project_id:emptyProject.id,empty_state:emptyState,exact_request_disposition:outcome,readonly_http_request_aborted:outcome.kind==='failed',controlled_original_ui_reply_delivered:outcome.kind==='finished',native_original_delayed_http200_provenance:false,separate_authenticated_renderer_GET_snapshot:true,captured_original_state:captured,current_state_not_repainted:true,no_service_mutation:true},reopened,mutations,actual_conversion:true,actual_openvino_cpu_job:true,actual_source_electron:true,fixture_home:workspace.home,synthetic_control:true,all_ok_weights_miss_synthetic_ng:true,quality_accepted:false,human_truth_accepted:false,physical_target_verified:false,installed_application_verified:false,os_registration_executed:false,complete_process_tree_verified:false,gpu_used:false,windows_excluded:true});
 }finally{
  release();removeObservers();page.off('request',observe);if(holdHandler&&!page.isClosed())await page.unroute('**/api/runtime-services',holdHandler);
  // The selected UI scope may have changed on failure. This fixed source CLI
  // stops only the original disposable service via its exact owner predicate.
  const code="import json,sys;sys.path.insert(0,sys.argv[1]);from backend.engine.managed_service import ManagedService;print(json.dumps(ManagedService(sys.argv[2]).stop()))";
  const stopped=JSON.parse(execFileSync(harness.resolvePython(),['-I','-B','-c',code,harness.REPO_ROOT,fixture.project.project_dir],{cwd:harness.REPO_ROOT,env,encoding:'utf8',timeout:30_000}).trim());
  evidence.note('owned_ir_service_cleanup',stopped);expect(stopped.status).toBe('stopped');expect(identity(fixture.project.project_dir)).toBeNull();
  const config=JSON.parse(fs.readFileSync(path.join(fixture.project.project_dir,'runtime_service/service.json'),'utf8'));expect(await harness.waitForPortClosed(config.port,10_000)).toBe(true);
 }
});
