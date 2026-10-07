import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page,Route,Request} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness=require('./fixtures/harness.cjs');
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
type Identity={pid:number;birth:number;command_sha256:string;state_dir:string}|null;
const sha=(file:string)=>createHash('sha256').update(fs.readFileSync(file)).digest('hex');

// This observer uses the production exact state-dir/birth/command predicate.
// A PID scan alone is never a lease release or complete-tree exit receipt.
function identity(projectDir:string):Identity{
 const code="import json,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from backend.engine.runtime_process_control import owned_inspection_process;root=Path(sys.argv[2]);config=json.loads((root/'service.json').read_text());p=owned_inspection_process(config,root/'state');print(json.dumps(None if p is None else {'pid':p.pid,'birth':p.create_time(),'command_sha256':config['process_command_sha256'],'state_dir':str(root/'state')}))";
 return JSON.parse(execFileSync(harness.resolvePython(),['-I','-B','-c',code,harness.REPO_ROOT,path.join(projectDir,'runtime_service')],{encoding:'utf8',timeout:10_000}).trim());
}
async function projectViaUi(page:Page,projectDir:string){
 await page.getByTitle('프로젝트 관리',{exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});
 await dialog.getByRole('button',{name:'폴더에서 열기',exact:true}).click();
 await dialog.getByPlaceholder('/path/to/project',{exact:true}).fill(projectDir);
 const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/project/open'&&r.request().method()==='POST');
 await dialog.getByRole('button',{name:'프로젝트 열기',exact:true}).click();
 const opened=await response;expect(opened.status(),await opened.text()).toBe(200);
 await expect(dialog).toHaveCount(0);
}
async function delivery(page:Page){
 await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();
 const panel=page.getByRole('region',{name:'검사 서비스 배포',exact:true});
 await expect(panel).toBeVisible();return panel;
}
async function closeDelivery(page:Page){
 await page.getByRole('button',{name:'패키지·장치·설치·진단 닫기',exact:true}).click();
 await expect(page.getByRole('region',{name:'검사 서비스 배포',exact:true})).toHaveCount(0);
}

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string,closeOwnedApp?:()=>Promise<void>){
 const tool=path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/operator_runtime.py');
 const env={...process.env,HOME:workspace.home,USERPROFILE:workspace.home,XDG_CACHE_HOME:path.join(workspace.home,'.cache'),TORCH_HOME:path.join(workspace.home,'.cache','torch'),HF_HOME:path.join(workspace.home,'.cache','huggingface'),VISION_AI_STUDIO_USER_DATA_DIR:workspace.userData};
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[tool,workspace.root],{cwd:harness.REPO_ROOT,env,encoding:'utf8',timeout:90_000}).trim());
 const empty=await api('/api/project/create',{name:'Empty service lifecycle control',task:'classification',project_dir:path.join(workspace.projects,'empty-service-control')});
 const originalService=path.join(fixture.project.project_dir,'runtime_service');
 const mutations:Array<{method:string;route:string}>=[];
 page.on('request',r=>{const p=new URL(r.url()).pathname;if(r.method()!=='GET'&&p.startsWith('/api/runtime-services'))mutations.push({method:r.method(),route:p});});
 const transitions:any[]=[];let releaseHeld:(()=>void)|undefined,holdHandler:((route:Route)=>Promise<void>)|undefined;
 try{
  await api('/api/project/open',{project_dir:fixture.project.project_dir});
  if(url)await page.goto(url);else await page.reload();
  await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(fixture.project.name);
  let panel=await delivery(page);
  await expect(panel).toContainText('현재 응답: stopped');
  const initial=await api('/api/runtime-services');expect(initial.active.deployment_id).toBe(fixture.active.deployment_id);
  expect(initial.native_install.registered).toBe(false);expect(initial.native_install.verified).toBe(false);
  expect(identity(fixture.project.project_dir)).toBeNull();
  const control=async(name:string,route:string,status:string)=>{
   const reply=page.waitForResponse(r=>new URL(r.url()).pathname===route&&r.request().method()==='POST');
   await panel.getByRole('button',{name,exact:true}).click();const response=await reply;
   expect(response.status(),await response.text()).toBe(200);
   await expect(panel).toContainText('현재 응답: '+status,{timeout:60_000});
   const state=await api('/api/runtime-services'),owned=identity(fixture.project.project_dir);
   expect(state.active.deployment_id).toBe(initial.active.deployment_id);expect(state.history).toEqual(initial.history);
   expect(state.active.release.manifest_sha256).toBe(fixture.active.release.manifest_sha256);
   if(status==='ready'){
    expect(owned).not.toBeNull();expect(state.runtime.device).toBe('cpu');
    expect(state.runtime.manifest_sha256).toBe(fixture.active.release.manifest_sha256);
   }else{expect(owned).toBeNull();expect(await harness.waitForPortClosed(state.port,10_000)).toBe(true);}
   transitions.push({name,status,owned,port:state.port,manifest:state.active.release.manifest_sha256});return {state,owned};
  };
  const first=await control('서비스 시작','/api/runtime-services/start','ready');
  await evidence.screenshot(page,`${native?'native':'browser'}-owned-service-ready`);
  await panel.locator('details').filter({has:page.getByLabel('승인 패키지 경로',{exact:true})}).locator('summary').click();
  const missing=path.join(workspace.root,'absent-reviewed-package');expect(fs.existsSync(missing)).toBe(false);
  await panel.getByLabel('승인 패키지 경로',{exact:true}).fill(missing);
  await panel.getByLabel('서비스 검토자',{exact:true}).fill('Controlled lifecycle reviewer');
  const refusalReply=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/runtime-services/apply'&&r.request().method()==='POST');
  await panel.getByRole('button',{name:'승인 패키지 적용',exact:true}).click();const refusal=await refusalReply;
  expect(refusal.status()).toBe(409);const refused=await refusal.json();
  const refusalAlert=panel.getByRole('alert').filter({hasText:String(refused.detail)});
  await expect(refusalAlert).toBeVisible();await refusalAlert.scrollIntoViewIfNeeded();await expect(refusalAlert).toBeInViewport();
  const afterRefusal=await api('/api/runtime-services');expect(afterRefusal.active).toEqual(initial.active);expect(afterRefusal.history).toEqual(initial.history);
  expect(identity(fixture.project.project_dir)).toEqual(first.owned);expect(fs.existsSync(missing)).toBe(false);
  await evidence.screenshot(page,`${native?'native':'browser'}-missing-package-refused`);
  await control('서비스 중지','/api/runtime-services/stop','stopped');
  const restarted=await control('서비스 시작','/api/runtime-services/start','ready');
  expect(restarted.owned!.birth).toBeGreaterThan(first.owned!.birth);expect(restarted.state.port).toBe(first.state.port);

  // A transport failure is explicit. It is not an invented service failure or
  // model verdict; the independent daemon remains the same owned epoch.
  const transport:((route:Route)=>Promise<void>)=async route=>{
   if(route.request().method()==='GET')await route.fulfill({status:503,json:{detail:'Controlled lifecycle status transport failure'}});else await route.continue();
  };
  await page.route('**/api/runtime-services',transport);
  await panel.getByRole('button',{name:'상태 새로고침',exact:true}).click();
  const transportAlert=panel.getByRole('alert').filter({hasText:'Controlled lifecycle status transport failure'});
  await expect(transportAlert).toBeVisible();await transportAlert.scrollIntoViewIfNeeded();await expect(transportAlert).toBeInViewport();
  expect(identity(fixture.project.project_dir)).toEqual(restarted.owned);
  await page.unroute('**/api/runtime-services',transport);
  await evidence.screenshot(page,`${native?'native':'browser'}-status-transport-refusal`);

  let heldResolve!:()=>void,doneResolve!:()=>void;
  const held=new Promise<void>(r=>{heldResolve=r;}),done=new Promise<void>(r=>{doneResolve=r;}),released=new Promise<void>(r=>{releaseHeld=r;});
  let captured:any,heldError:unknown,heldRequest:Request|undefined,armed=true;
  holdHandler=async route=>{
   if(!armed||route.request().method()!=='GET'){await route.continue();return;}armed=false;heldRequest=route.request();
   try{
    if(native){
     // APIRequestContext bypasses Electron's frame-bound token injection.
     // Use a separate real authenticated renderer GET, then hold its state as
     // an explicitly controlled reply to the original UI request. No token is
     // extracted and this is not original delayed native HTTP200 provenance.
     captured=await api('/api/runtime-services');heldResolve();await released;
     await route.fulfill({status:200,json:captured});
    }else{
     const response=await route.fetch();expect(response.status()).toBe(200);
     captured=await response.json();heldResolve();await released;await route.fulfill({response});
    }
   }catch(cause){heldError=cause;heldResolve();}finally{doneResolve();}
  };
  await page.route('**/api/runtime-services',holdHandler);
  const beforeCancel=mutations.length;
  await panel.getByRole('button',{name:'상태 새로고침',exact:true}).click();await held;
  if(heldError)throw heldError;
  expect(captured.runtime.status).toBe('ready');expect(captured.runtime.manifest_sha256).toBe(fixture.active.release.manifest_sha256);
  // Explicitly cancel the view, then open a different real disposable project.
  // The readonly HTTP request is deliberately completed, not called aborted.
  await closeDelivery(page);await projectViaUi(page,empty.project_dir);panel=await delivery(page);
  await expect(panel).toContainText('현재 응답: stopped');await expect(panel.getByRole('button',{name:'서비스 시작',exact:true})).toBeDisabled();
  const emptyState=await api('/api/runtime-services');expect(emptyState.active).toBeNull();expect(emptyState.history).toEqual([]);
  const lateReply=page.waitForResponse(r=>r.request()===heldRequest);
  releaseHeld!();const delivered=await lateReply;expect(delivered.status()).toBe(200);
  expect((await delivered.json()).runtime.manifest_sha256).toBe(captured.runtime.manifest_sha256);
  await delivered.finished();await done;if(heldError)throw heldError;
  await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))));
  await page.unroute('**/api/runtime-services',holdHandler);holdHandler=undefined;
  await expect(panel).toContainText('현재 응답: stopped');await expect(panel).not.toContainText(fixture.active.release.manifest_sha256);
  expect(mutations.length).toBe(beforeCancel);expect(identity(fixture.project.project_dir)).toEqual(restarted.owned);
  await evidence.screenshot(page,`${native?'native':'browser'}-cancelled-view-empty-project`);
  await closeDelivery(page);await projectViaUi(page,fixture.project.project_dir);panel=await delivery(page);
  await expect(panel).toContainText('현재 응답: ready');await expect(panel).toContainText(fixture.active.release.manifest_sha256);
  expect(identity(fixture.project.project_dir)).toEqual(restarted.owned);
  await page.reload();panel=await delivery(page);await expect(panel).toContainText('현재 응답: ready');
  const reopened=await api('/api/runtime-services');expect(reopened.active).toEqual(initial.active);expect(reopened.history).toEqual(initial.history);
  await evidence.screenshot(page,`${native?'native':'browser'}-exact-active-release-reopened`);
  await panel.locator('details').filter({has:page.getByLabel('승인 패키지 경로',{exact:true})}).locator('summary').click();
  await panel.getByLabel('승인 패키지 경로',{exact:true}).fill('');
  await expect(panel.getByRole('button',{name:'승인 패키지 적용',exact:true})).toBeDisabled();
  const beforeHandoff=mutations.length;
  await panel.getByRole('button',{name:'승인·패키지 확인 (6단계)',exact:true}).click();
  await expect(page.getByRole('heading',{name:'검사 결과 및 플로우 배포',exact:true})).toBeVisible();
  expect(mutations.length).toBe(beforeHandoff);expect(identity(fixture.project.project_dir)).toEqual(restarted.owned);
  await evidence.screenshot(page,`${native?'native':'browser'}-explicit-package-stage-handoff`);

  const config=JSON.parse(fs.readFileSync(path.join(originalService,'service.json'),'utf8'));evidence.redact(config.token);
  const serviceApi=async(route:string)=>{const r=await fetch(`http://127.0.0.1:${config.port}${route}`,{headers:{'X-Vision-Token':config.token},signal:AbortSignal.timeout(10_000)});expect(r.status).toBe(200);return r.json() as Promise<any>;};
  const beforeClose=await serviceApi('/v1/runtime');expect(beforeClose.manifest_sha256).toBe(fixture.active.release.manifest_sha256);
  let afterOwnedAppClose:any=null;
  if(closeOwnedApp){
   await closeOwnedApp();afterOwnedAppClose=await serviceApi('/v1/runtime');
   expect(afterOwnedAppClose.status).toBe('ready');expect(afterOwnedAppClose.manifest_sha256).toBe(beforeClose.manifest_sha256);
   expect(identity(fixture.project.project_dir)).toEqual(restarted.owned);
  }
  for(const [file,digest] of Object.entries(fixture.images))expect(sha(file)).toBe(digest);
  evidence.note('service_lifecycle_controls',{fixture,empty_project:empty,initial,transitions,refusal:{status:409,body:refused,active_preserved:true,epoch_preserved:true},transport:{controlled_status:503,owned_epoch_unchanged:true},cancel:{explicit_view_close:true,readonly_http_request_aborted:false,actual_project_state_snapshot:captured,snapshot_transport:native?'separate_authenticated_renderer_GET_controlled_original_UI_reply':'original_browser_request_actual_response',native_original_delayed_http200_provenance:false,controlled_original_ui_reply:native,new_project_id:empty.id,new_project_state:emptyState,late_original_reply_crossed_view_boundary:true,service_mutations:mutations.length-beforeCancel},reopened,handoff:{stage:6,service_mutations:mutations.length-beforeHandoff,inspection_executed:false},beforeClose,afterOwnedAppClose,service_mutations:mutations,fixture_home:workspace.home,actual_owned_cpu_service_transitions:true,actual_source_electron_fixture_closed:!!closeOwnedApp,actual_model_inference_in_this_case:false,source_ui:true,controlled_synthetic_approval_fixture:true,os_registration_executed:false,installed_application_accepted:false,dedicated_account_reboot_device_accepted:false,process_tree_exit_qualified:false,model_quality_accepted:false,gpu_used:false,windows_excluded:true});
 }finally{
  releaseHeld?.();if(holdHandler&&!page.isClosed())await page.unroute('**/api/runtime-services',holdHandler);
  const stopped=JSON.parse(execFileSync(harness.resolvePython(),[tool,'stop',fixture.project.project_dir],{cwd:harness.REPO_ROOT,env,encoding:'utf8',timeout:30_000}).trim());
  evidence.note('owned_service_cleanup',stopped);expect(stopped.status).toBe('stopped');expect(identity(fixture.project.project_dir)).toBeNull();
  const config=JSON.parse(fs.readFileSync(path.join(originalService,'service.json'),'utf8'));expect(await harness.waitForPortClosed(config.port,10_000)).toBe(true);
 }
}
test('owned CPU service lifecycle keeps exact release across refusal cancelled view and reopen',async({page,renderer,workspace,evidence})=>{
 test.setTimeout(240_000);
 const api:Api=async(route,body,method)=>{const r=await page.request.fetch(renderer.origin+route,{method:method||(body?'POST':'GET'),data:body});expect(r.ok(),await r.text()).toBe(true);return r.json();};
 await installDesktopHostShim(page,renderer.port);await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native owned CPU lifecycle and independent response after its source Studio closes',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 test.setTimeout(300_000);const backend=await electronSession.waitForBackend(),page=electronSession.window;
 const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!r.ok)throw Error(`Owned lifecycle HTTP ${r.status}: ${await r.text()}`);return r.json();},{port:backend.port,route,body,method});
 await exercise(page,workspace,evidence,api,true,undefined,()=>electronSession.app.close());
});
