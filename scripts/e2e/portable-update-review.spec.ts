import path from 'node:path';
import fs from 'node:fs';
import crypto from 'node:crypto';
import {execFileSync,execFile} from 'node:child_process';
import {promisify} from 'node:util';
import Module from 'node:module';
import ts from 'typescript';
import {test,expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness=require('./fixtures/harness.cjs'),runFile=promisify(execFile);
function load(name='portableUpdate.ts'):any {
 const file=path.join(harness.REPO_ROOT,'src/main',name),m=new Module(file,module);m.filename=file;m.paths=(Module as any)._nodeModulePaths(path.dirname(file));
 const original=m.require.bind(m);m.require=(key:string)=>['./releaseTrust','./persistentLaunch'].includes(key)?load(key.slice(2)+'.ts'):original(key);
 (m as any)._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);return m.exports;
}

test('portable review binds actual signed bytes and preserves new writes through app recovery',async({page,renderer,workspace,evidence})=>{
 const python=harness.resolvePython(),folder=path.join(workspace.root,'portable-controls');fs.mkdirSync(folder);
 const seeded=JSON.parse(execFileSync(python,['-c',`
import json,sys,shutil
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from backend.tests.test_global_migration import owned
from backend.tests.test_service_s6_04 import fixture
temp=Path(sys.argv[1]);root,*_=owned(temp);value=fixture(temp)
signing=temp/'fixture-signing-key.der';signing.write_bytes(value['key'].private_bytes(serialization.Encoding.DER,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));signing.chmod(0o600)
release=temp/'release';release.mkdir();shutil.copytree(value['directory'],release/'artifacts')
shutil.copyfile(value['envelope'],release/'release.json')
print(json.dumps({'root':str(root),'authority':str(value['authority']),'manifest':str(release/'release.json')}))
`,folder],{cwd:harness.REPO_ROOT,encoding:'utf8'}));
 const resources=path.join(folder,'resources');fs.mkdirSync(resources);fs.mkdirSync(path.join(resources,'backend_bin'));
 fs.copyFileSync(seeded.authority,path.join(resources,'release-trust.json'));
 const binary=Buffer.from('controlled runtime binding; actual source CLI runner used only by this test'),hash=(b:Buffer)=>crypto.createHash('sha256').update(b).digest('hex');
 fs.writeFileSync(path.join(resources,'backend_bin/vision_ai_backend'),binary);
 fs.writeFileSync(path.join(resources,'backend_bin/backend-release.json'),JSON.stringify({executable:'vision_ai_backend',executable_sha256:hash(binary),inventory:{build_identity_sha256:'a'.repeat(64),platform:process.platform==='darwin'?'Darwin':'Linux',architecture:process.arch==='arm64'?'arm64':'x86_64'}}));
 const {PortableUpdateManager}=load();const commands:string[]=[];let interruptInstall:string|null=null,manifest=seeded.manifest;
 const manager=new PortableUpdateManager({packaged:true,platform:process.platform,arch:process.arch,resourcesPath:resources,userDataPath:workspace.userData,appPath:path.join(workspace.root,'Current.app'),
  // These are controlled native-signature outputs; no real publisher acceptance.
  signature:async()=>({status:'verified',publisher:'Qualification fixture'}),
  runner:async(_file:string,args:string[])=>{commands.push(args[1]);
   if(args[1]==='install'&&interruptInstall){const boundary=interruptInstall;interruptInstall=null;return runFile(python,['-c',`
import os,sys
from backend.engine import runtime_update as update
boundary=sys.argv[1]
def interrupt(point):
 if point==boundary:os._exit(91)
update._checkpoint=interrupt
raise SystemExit(update.main(sys.argv[2:]))
`,boundary,...args.slice(1)],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30000});}
   return runFile(python,['-m','backend.engine.runtime_update',...args.slice(1)],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30000});}});
 let cancelled=true;
 await page.exposeFunction('__portableDispatch',async(operation:string,args:any[])=>{
  if(operation==='select'){if(cancelled){cancelled=false;return null;}return manager.select(seeded.root);}
  if(operation==='inspect')return manager.inspect();
  if(operation==='preview')return manager.preview(manifest,args[0]);
  if(operation==='apply')return manager.apply(args[0]);
  if(operation==='recover')return manager.recover(args[0],args[1]);
  throw Error('Unknown controlled operation');
 });
 await installDesktopHostShim(page,renderer.port);
 await page.addInitScript(()=>{
  const invoke=(operation:string,args:any[]=[]) => (window as any).__portableDispatch(operation,args);
  Object.assign((window as any).api,{
   getDistributionStatus:async()=>({app_version:'0.1.0',platform:'darwin',architecture:'arm64',signature:{status:'development',reason:'Controlled renderer; no native publisher acceptance',checked_at:'controlled'},update:{configured:false,configuration:null,status:'not_configured',release:null,automatic_update_available:false}}),
   selectPortableUpdateHome:()=>invoke('select'),inspectPortableUpdate:()=>invoke('inspect'),previewPortableUpdate:(c:string)=>invoke('preview',[c]),applyPortableUpdate:(id:string)=>invoke('apply',[id]),recoverPortableUpdate:(a:string,expected:unknown)=>invoke('recover',[a,expected])});
 });
 await page.goto(renderer.url);
 const created=await page.request.post(renderer.origin+'/api/project/create',{data:{name:'Portable update review controls',task:'classification'}});expect(created.ok()).toBe(true);
 await page.reload();await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();await page.getByRole('button',{name:'설치·진단',exact:true}).click();
 const panel=page.getByRole('region',{name:'별도 portable 앱 업데이트'}),select=panel.getByRole('button',{name:'portable 설치 폴더 선택',exact:true});
 await select.click();await expect(panel.getByText('선택한 설치:',{exact:false})).toHaveCount(0);
 await select.click();await expect(panel).toContainText('버전 0.0.0');
 await panel.getByRole('button',{name:'portable 변경 내용 확인',exact:true}).click();await expect(panel).toContainText('0.0.0 → 1.0.0');
 const apply=panel.getByRole('button',{name:'검토한 portable 업데이트 적용',exact:true}),confirm=panel.getByLabel('선택한 portable 설치의 학습·검사가 종료되고 백업을 확인했습니다',{exact:true});
 await expect(apply).toBeDisabled();await confirm.check();
 fs.writeFileSync(path.join(seeded.root,'projects/labels.json'),'new write after review');
 await apply.click();await expect(panel.getByRole('alert')).toContainText('review');await expect(apply).toHaveCount(0);expect(fs.existsSync(path.join(seeded.root,'application-active.json'))).toBe(false);
 await panel.getByRole('button',{name:'portable 상태 다시 읽기',exact:true}).click();await panel.getByRole('button',{name:'portable 변경 내용 확인',exact:true}).click();await confirm.check();await apply.click();
 await expect(panel).toContainText('앱·데이터 전환 확인됨');await expect(panel).toContainText('버전 1.0.0 · 데이터 세대 1');
 const newUser=JSON.parse(execFileSync(python,['-c',`
import json,sys
from pathlib import Path
from backend.engine.shared_accounts import AccountStore
root=Path(sys.argv[1]);user=AccountStore(root/'auth/accounts.sqlite').create_user('post-update-user','controlled-fixture-password-123')
print(json.dumps({'id':user['id']}))
`,seeded.root],{cwd:harness.REPO_ROOT,encoding:'utf8'}));
 await confirm.check();await panel.getByRole('button',{name:'새 쓰기 보존하며 복구',exact:true}).click();await expect(panel).toContainText('데이터 세대 2');
 const observed=JSON.parse(execFileSync(python,['-c',`
import json,sys
from pathlib import Path
from backend.engine.shared_accounts import AccountStore
from backend.engine.runtime_update import launch_plan
root=Path(sys.argv[1]);result=launch_plan(root,sys.argv[2],pinned_authority_sha256=sys.argv[3])
print(json.dumps({'user_ids':[u['id'] for u in AccountStore(root/'auth/accounts.sqlite').users()], 'labels_preserved':(root/'projects/labels.json').read_bytes()==b'new write after review','version':result['version'],'database_fence':result['database_pointer']['fence'],'launch_argv':result['argv']}))
`,seeded.root,path.join(resources,'release-trust.json'),hash(fs.readFileSync(path.join(resources,'release-trust.json')))],{cwd:harness.REPO_ROOT,encoding:'utf8'}));
 expect(observed.user_ids).toContain(newUser.id);expect(observed.labels_preserved).toBe(true);expect(observed.database_fence).toBe(2);
 expect(execFileSync(observed.launch_argv[0],[],{encoding:'utf8'})).toBe('portable-qualified\n');
 await page.reload();await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();await page.getByRole('button',{name:'설치·진단',exact:true}).click();await select.click();await expect(panel).toContainText('버전 1.0.0 · 데이터 세대 2');
 await page.setViewportSize({width:700,height:850});await panel.scrollIntoViewIfNeeded();await evidence.screenshot(page,'portable-update-reopen-new-writes');
 // A committed finish is idempotent, then two actual child exit boundaries
 // exercise the explicit pre-database abort and post-database finish controls.
 await confirm.check();await panel.getByRole('button',{name:'같은 업데이트 마무리',exact:true}).click();await expect(select).toBeEnabled();await expect(panel).toContainText('앱·데이터 전환 확인됨');await expect(panel).toContainText('데이터 세대 2');
 const next=JSON.parse(execFileSync(python,['-c',`
import json,sys,shutil
from pathlib import Path
from cryptography.hazmat.primitives.serialization import load_der_private_key
from backend.tests.test_service_s6_04 import fixture
temp=Path(sys.argv[1]);key=load_der_private_key((temp/'fixture-signing-key.der').read_bytes(),password=None)
value=fixture(temp,version='1.1.0',key=key,authority=temp/'authority.json')
release=temp/'release-next';release.mkdir();shutil.copytree(value['directory'],release/'artifacts');shutil.copyfile(value['envelope'],release/'release.json')
print(json.dumps({'manifest':str(release/'release.json')}))
`,folder],{cwd:harness.REPO_ROOT,encoding:'utf8'}));manifest=next.manifest;
 for(const boundary of ['before_database','after_database']){
  await panel.getByRole('button',{name:'portable 변경 내용 확인',exact:true}).click();await expect(panel).toContainText('1.0.0 → 1.1.0');await confirm.check();
  interruptInstall=boundary;await apply.click();await expect(panel.getByRole('alert')).toBeVisible();
  await panel.getByRole('button',{name:'portable 상태 다시 읽기',exact:true}).click();await expect(panel).toContainText('중단된 업데이트 · 복구 필요');
  const abort=panel.getByRole('button',{name:'데이터 전환 전 설치 취소',exact:true});
  if(boundary==='before_database'){await expect(abort).toBeVisible();await expect(abort).toBeDisabled();await confirm.check();await abort.click();await expect(panel).toContainText('버전 1.0.0 · 데이터 세대 2');}
  else{await expect(abort).toHaveCount(0);await confirm.check();await panel.getByRole('button',{name:'같은 업데이트 마무리',exact:true}).click();await expect(panel).toContainText('앱·데이터 전환 확인됨');await expect(select).toBeEnabled();await expect(panel).toContainText('버전 1.1.0 · 데이터 세대 3');}
 }
 await page.reload();await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();await page.getByRole('button',{name:'설치·진단',exact:true}).click();await select.click();await expect(panel).toContainText('버전 1.1.0 · 데이터 세대 3');
 await evidence.screenshot(page,'portable-update-interruption-finish-reopen');
 evidence.note('portable_update_review',{actual_main_manager:true,actual_python_signed_update_and_forward_recovery:true,commands,source_changed_after_review_refused:true,controlled_application_entrypoint_executed:true,new_user_preserved:true,labels_preserved:observed.labels_preserved,database_fence:observed.database_fence,pre_database_abort_reopens_prior_pair:true,post_database_finish_reopens_new_pair:true,post_database_abort_absent:true,controlled_child_exit_boundaries:['before_database','after_database'],physical_power_loss:false,native_signatures:'controlled output only; not real publisher acceptance',renderer_host:'browser with controlled bridge',native_electron:false,windows:false});
});

test('native desktop refuses unprovisioned portable changes before opening a directory dialog',{tag:'@electron'},async({electronSession,evidence})=>{
 const status=await electronSession.waitForBackend(),page=electronSession.window;
 await page.evaluate(async port=>{const result=await fetch(`http://127.0.0.1:${port}/api/project/create`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'Native portable refusal',task:'classification'})});if(!result.ok)throw Error('Project creation failed');},status.port);
 await page.reload();await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();await page.getByRole('button',{name:'설치·진단',exact:true}).click();
 const panel=page.getByRole('region',{name:'별도 portable 앱 업데이트'});await panel.getByRole('button',{name:'portable 설치 폴더 선택',exact:true}).click();
 await expect(panel.getByRole('alert')).toContainText('packaged application');await expect(panel.getByText('선택한 설치:',{exact:false})).toHaveCount(0);
 await evidence.screenshot(page,'native-portable-unprovisioned-refusal');evidence.note('portable_native_refusal',{actual_electron_main_preload_renderer:true,unprovisioned_development_refused:true,directory_dialog_opened:false,installation_or_database_modified:false,real_signed_installation_qualified:false});
});

test('controlled launch lifecycle UI preserves response-loss readback and durable state across reopening',async({page,renderer,workspace,evidence})=>{
 const folder=path.join(workspace.root,'controlled-launch-ui');fs.mkdirSync(folder);
 const root=path.join(folder,'selected-home'),resources=path.join(folder,'resources');fs.mkdirSync(root);fs.mkdirSync(path.join(resources,'backend_bin'),{recursive:true});
 const hash=(b:Buffer|string)=>crypto.createHash('sha256').update(b).digest('hex'),binary=Buffer.from('controlled manager executable binding; never spawned');
 fs.writeFileSync(path.join(resources,'backend_bin/vision_ai_backend'),binary);
 const keys=crypto.generateKeyPairSync('ed25519');
 fs.writeFileSync(path.join(resources,'release-trust.json'),JSON.stringify({schema_version:1,publisher:'Controlled UI fixture',keys:{fixture:keys.publicKey.export({type:'spki',format:'der'}).toString('base64')},revoked_key_ids:[],allowed_origins:['https://release.example.test'],compatibility:{api_context:1,worker:1,runtime:1,dataset_index:1}}));
 fs.writeFileSync(path.join(resources,'backend_bin/backend-release.json'),JSON.stringify({executable:'vision_ai_backend',executable_sha256:hash(binary),inventory:{build_identity_sha256:'a'.repeat(64),platform:process.platform==='darwin'?'Darwin':'Linux',architecture:process.arch==='arm64'?'arm64':'x86_64',owned_application_launch_controller_protocol:1,resources:['scripts/frozen_backend_entry.py','backend/engine/application_launch_controller.py','backend/engine/application_launch_handshake.py','backend/engine/application_launch_lease.py'].map(p=>({path:p,sha256:hash(fs.readFileSync(path.join(harness.REPO_ROOT,p)))}))}}));
 const expected={installation_id:'1'.repeat(32),update_id:'2'.repeat(32),database_fence:3};
 const state={status:'committed',version:'1.0.0',...expected,allowed_recovery:['finish','forward'],application_started:false};
 let requests=0,inspections=0;
 let lifecycle:any={schema_version:1,status:'absent',nonce:null,...expected,bootstrap_binding_verified:false,readiness:'unverified',native_app_handshake_verified:false,backend_handshake_verified:false,actual_application_inference_verified:false,release_ready:false};
 const Manager=load().PortableUpdateManager;
 const manager=new Manager({packaged:true,platform:process.platform,arch:process.arch,resourcesPath:resources,userDataPath:workspace.userData,appPath:path.join(folder,'Current.app'),signature:async()=>({status:'verified',publisher:'Controlled UI fixture'}),
  runner:async(_file:string,args:string[])=>{if(args[0]==='--owned-application-launch-controller'){expect(args).toContain('--inspect');inspections++;return{stdout:JSON.stringify(lifecycle)+'\n',stderr:''};}expect(args.slice(0,2)).toEqual(['--offline-application-update','inspect']);return{stdout:JSON.stringify(state),stderr:''};},
  launchRunner:async(_file:string,args:string[])=>{expect(args[0]).toBe('--owned-application-launch-controller');expect(args).not.toContain('--inspect');requests++;lifecycle={...lifecycle,status:'starting',nonce:'3'.repeat(32)};throw Error('Controlled launch response loss');}});
 await page.exposeFunction('__controlledLaunchUI',async(op:string,args:any[])=>{
  if(op==='select')return manager.select(root);if(op==='inspect')return manager.inspect();if(op==='launch')return manager.launch(args[0]);if(op==='launch-inspect')return manager.inspectLaunch(args[0]);throw Error('Unexpected controlled UI operation');
 });
 await installDesktopHostShim(page,renderer.port);await page.addInitScript(()=>{
  const invoke=(op:string,args:any[]=[]) => (window as any).__controlledLaunchUI(op,args);
  Object.assign((window as any).api,{getDistributionStatus:async()=>({app_version:'0.1.0',platform:'darwin',architecture:'arm64',signature:{status:'development',reason:'Controlled UI only',checked_at:'controlled'},update:{configured:false,configuration:null,status:'not_configured',release:null,automatic_update_available:false}}),selectPortableUpdateHome:()=>invoke('select'),inspectPortableUpdate:()=>invoke('inspect'),launchPortableUpdate:(expected:unknown)=>invoke('launch',[expected]),inspectPortableLaunch:(expected:unknown)=>invoke('launch-inspect',[expected])});
 });
 await page.goto(renderer.url);const created=await page.request.post(renderer.origin+'/api/project/create',{data:{name:'Controlled launch lifecycle UI',task:'classification'}});expect(created.ok()).toBe(true);
 const open=async()=>{await page.reload();await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();await page.getByRole('button',{name:'설치·진단',exact:true}).click();};await open();
 const panel=page.getByRole('region',{name:'별도 portable 앱 업데이트'}),launch=panel.getByRole('button',{name:'전환한 portable 앱 시작',exact:true}),refresh=panel.getByRole('button',{name:'portable 상태 다시 읽기',exact:true});
 await panel.getByRole('button',{name:'portable 설치 폴더 선택',exact:true}).click();await expect(panel).toContainText('앱 실행 기록 없음');await expect(launch).toBeEnabled();
 await launch.click();await expect(panel.getByRole('alert')).toContainText('Controlled launch response loss');await expect(launch).toBeDisabled();expect(requests).toBe(1);await panel.getByRole('alert').scrollIntoViewIfNeeded();await evidence.screenshot(page,'portable-launch-response-loss-no-retry');
 lifecycle={...lifecycle,status:'ready',bootstrap_binding_verified:true,readiness:'authenticated_controller_binding_only'};await refresh.click();await expect(panel).toContainText('앱과 백엔드의 설치 연결 확인됨');await expect(launch).toBeDisabled();await expect(panel.getByRole('button',{name:'portable 변경 내용 확인',exact:true})).toBeDisabled();
 await open();await panel.getByRole('button',{name:'portable 설치 폴더 선택',exact:true}).click();await expect(panel).toContainText('앱과 백엔드의 설치 연결 확인됨');await expect(launch).toBeDisabled();expect(requests).toBe(1);
 lifecycle={...lifecycle,status:'recovery_required',reason:'Controlled original controller birth loss'};await refresh.click();await expect(panel).toContainText('앱 실행 확인·복구 필요');await expect(launch).toBeDisabled();expect(requests).toBe(1);await panel.getByText('앱 실행 확인·복구 필요',{exact:true}).scrollIntoViewIfNeeded();await evidence.screenshot(page,'portable-launch-reopened-recovery-no-retry');
 evidence.note('controlled_launch_lifecycle_ui',{actual_browser_renderer:true,actual_main_manager:true,controlled_cli_and_signature_responses:true,persistent_controller_executed:false,native_application_executed:false,launch_requests:requests,launch_inspections:inspections,response_loss_not_retried:true,reselection_restores_durable_readback:true,update_and_recovery_blocked_while_launch_unresolved:true,actual_inference:false,real_publisher_acceptance:false,release_ready:false});
});
