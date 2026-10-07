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
 const original=m.require.bind(m);m.require=(key:string)=>key==='./releaseTrust'?load('releaseTrust.ts'):original(key);
 (m as any)._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);return m.exports;
}

test('portable review binds actual signed bytes and preserves new writes through app recovery',async({page,renderer,workspace,evidence})=>{
 const python=harness.resolvePython(),folder=path.join(workspace.root,'portable-controls');fs.mkdirSync(folder);
 const seeded=JSON.parse(execFileSync(python,['-c',`
import json,sys,shutil
from pathlib import Path
from backend.tests.test_global_migration import owned
from backend.tests.test_service_s6_04 import fixture
temp=Path(sys.argv[1]);root,*_=owned(temp);value=fixture(temp)
release=temp/'release';release.mkdir();shutil.copytree(value['directory'],release/'artifacts')
shutil.copyfile(value['envelope'],release/'release.json')
print(json.dumps({'root':str(root),'authority':str(value['authority']),'manifest':str(release/'release.json')}))
`,folder],{cwd:harness.REPO_ROOT,encoding:'utf8'}));
 const resources=path.join(folder,'resources');fs.mkdirSync(resources);fs.mkdirSync(path.join(resources,'backend_bin'));
 fs.copyFileSync(seeded.authority,path.join(resources,'release-trust.json'));
 const binary=Buffer.from('controlled runtime binding; actual source CLI runner used only by this test'),hash=(b:Buffer)=>crypto.createHash('sha256').update(b).digest('hex');
 fs.writeFileSync(path.join(resources,'backend_bin/vision_ai_backend'),binary);
 fs.writeFileSync(path.join(resources,'backend_bin/backend-release.json'),JSON.stringify({executable:'vision_ai_backend',executable_sha256:hash(binary),inventory:{build_identity_sha256:'a'.repeat(64),platform:process.platform==='darwin'?'Darwin':'Linux',architecture:process.arch==='arm64'?'arm64':'x86_64'}}));
 const {PortableUpdateManager}=load();const commands:string[]=[];
 const manager=new PortableUpdateManager({packaged:true,platform:process.platform,arch:process.arch,resourcesPath:resources,userDataPath:workspace.userData,appPath:path.join(workspace.root,'Current.app'),
  // These are controlled native-signature outputs; no real publisher acceptance.
  signature:async()=>({status:'verified',publisher:'Qualification fixture'}),
  runner:async(_file:string,args:string[])=>{commands.push(args[1]);return runFile(python,['-m','backend.engine.runtime_update',...args.slice(1)],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30000});}});
 let cancelled=true;
 await page.exposeFunction('__portableDispatch',async(operation:string,args:any[])=>{
  if(operation==='select'){if(cancelled){cancelled=false;return null;}return manager.select(seeded.root);}
  if(operation==='inspect')return manager.inspect();
  if(operation==='preview')return manager.preview(seeded.manifest,args[0]);
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
 evidence.note('portable_update_review',{actual_main_manager:true,actual_python_signed_update_and_forward_recovery:true,commands,source_changed_after_review_refused:true,controlled_application_entrypoint_executed:true,new_user_preserved:true,labels_preserved:observed.labels_preserved,database_fence:observed.database_fence,native_signatures:'controlled output only; not real publisher acceptance',renderer_host:'browser with controlled bridge',native_electron:false,windows:false});
});

test('native desktop refuses unprovisioned portable changes before opening a directory dialog',{tag:'@electron'},async({electronSession,evidence})=>{
 const status=await electronSession.waitForBackend(),page=electronSession.window;
 await page.evaluate(async port=>{const result=await fetch(`http://127.0.0.1:${port}/api/project/create`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'Native portable refusal',task:'classification'})});if(!result.ok)throw Error('Project creation failed');},status.port);
 await page.reload();await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();await page.getByRole('button',{name:'설치·진단',exact:true}).click();
 const panel=page.getByRole('region',{name:'별도 portable 앱 업데이트'});await panel.getByRole('button',{name:'portable 설치 폴더 선택',exact:true}).click();
 await expect(panel.getByRole('alert')).toContainText('packaged application');await expect(panel.getByText('선택한 설치:',{exact:false})).toHaveCount(0);
 await evidence.screenshot(page,'native-portable-unprovisioned-refusal');evidence.note('portable_native_refusal',{actual_electron_main_preload_renderer:true,unprovisioned_development_refused:true,directory_dialog_opened:false,installation_or_database_modified:false,real_signed_installation_qualified:false});
});
