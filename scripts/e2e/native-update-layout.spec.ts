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
function managerClass():any {
 const file=path.join(harness.REPO_ROOT,'src/main/portableUpdate.ts'),m=new Module(file,module);m.filename=file;m.paths=(Module as any)._nodeModulePaths(path.dirname(file));
 const original=m.require.bind(m);m.require=(key:string)=>{
  if(key!=='./releaseTrust')return original(key);
  const f=path.join(harness.REPO_ROOT,'src/main/releaseTrust.ts'),t=new Module(f,module);t.filename=f;t.paths=m.paths;
  (t as any)._compile(ts.transpileModule(fs.readFileSync(f,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,f);return t.exports;
 };
 (m as any)._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);return m.exports.PortableUpdateManager;
}
test('macOS native layout review shows signed internal links and refuses changed installed targets',async({page,renderer,workspace,evidence})=>{
 test.skip(process.platform!=='darwin','Native schema2 installation requires the actual macOS host');test.setTimeout(150_000);
 const python=harness.resolvePython(),folder=path.join(workspace.root,'native-layout-controls');fs.mkdirSync(folder);
 const seeded=JSON.parse(execFileSync(python,['-c',`
import json,sys,shutil
from pathlib import Path
from backend.tests.test_global_migration import owned
from backend.tests.test_native_update_layout import native
p=Path(sys.argv[1]);root,*_=owned(p);value=native(p);release=p/'release';release.mkdir();shutil.copytree(value['directory'],release/'artifacts');shutil.copyfile(value['envelope'],release/'release.json');print(json.dumps({'root':str(root),'authority':str(value['authority']),'manifest':str(release/'release.json')}))
`,folder],{cwd:harness.REPO_ROOT,encoding:'utf8'}));
 const resources=path.join(folder,'resources');fs.mkdirSync(path.join(resources,'backend_bin'),{recursive:true});fs.copyFileSync(seeded.authority,path.join(resources,'release-trust.json'));
 const binary=Buffer.from('Qualification fixture binding only; actual source CLI runner is used by this case'),sha=(b:Buffer)=>crypto.createHash('sha256').update(b).digest('hex');
 fs.writeFileSync(path.join(resources,'backend_bin/vision_ai_backend'),binary);fs.writeFileSync(path.join(resources,'backend_bin/backend-release.json'),JSON.stringify({executable:'vision_ai_backend',executable_sha256:sha(binary),inventory:{build_identity_sha256:'a'.repeat(64),platform:'Darwin',architecture:process.arch==='arm64'?'arm64':'x86_64'}}));
 const Manager=managerClass(),commands:string[]=[];
 const manager=new Manager({packaged:true,platform:process.platform,arch:process.arch,resourcesPath:resources,userDataPath:workspace.userData,appPath:path.join(workspace.root,'Current.app'),
  signature:async()=>({status:'verified',publisher:'Qualification fixture'}),
  runner:async(_file:string,args:string[])=>{commands.push(args[1]);return runFile(python,['-m','backend.engine.runtime_update',...args.slice(1)],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30000});}});
 await page.exposeFunction('__nativeLayoutDispatch',async(operation:string,args:any[])=>{
  if(operation==='select')return manager.select(seeded.root);if(operation==='inspect')return manager.inspect();
  if(operation==='preview')return manager.preview(seeded.manifest,args[0]);if(operation==='apply')return manager.apply(args[0]);
  throw Error('Unknown controlled operation');
 });
 await installDesktopHostShim(page,renderer.port);await page.addInitScript(()=>{
  const invoke=(op:string,args:any[]=[]) => (window as any).__nativeLayoutDispatch(op,args);
  Object.assign((window as any).api,{getDistributionStatus:async()=>({app_version:'0.1.0',platform:'darwin',architecture:'arm64',signature:{status:'development',reason:'Qualification only',checked_at:'controlled'},update:{configured:false,configuration:null,status:'not_configured',release:null,automatic_update_available:false}}),selectPortableUpdateHome:()=>invoke('select'),inspectPortableUpdate:()=>invoke('inspect'),previewPortableUpdate:(c:string)=>invoke('preview',[c]),applyPortableUpdate:(id:string)=>invoke('apply',[id])});
 });
 await page.goto(renderer.url);const created=await page.request.post(renderer.origin+'/api/project/create',{data:{name:'Native layout review fixture',task:'classification'}});expect(created.ok()).toBe(true);await page.reload();
 await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();await page.getByRole('button',{name:'설치·진단',exact:true}).click();
 const panel=page.getByRole('region',{name:'별도 portable 앱 업데이트'});await panel.getByRole('button',{name:'portable 설치 폴더 선택',exact:true}).click();await expect(panel).toContainText('버전 0.0.0');
 await panel.getByRole('button',{name:'portable 변경 내용 확인',exact:true}).click();await expect(panel).toContainText('macOS 앱 · 내부 연결 3개');await expect(panel).toContainText('앱 파일 3개');
 const apply=panel.getByRole('button',{name:'검토한 portable 업데이트 적용',exact:true});await expect(apply).toBeDisabled();await evidence.screenshot(page,'native-layout-review-internal-links');
 await panel.getByLabel('선택한 portable 설치의 학습·검사가 종료되고 백업을 확인했습니다',{exact:true}).check();await apply.click();await expect(panel).toContainText('버전 1.0.0 · 데이터 세대 1');
 const readback=JSON.parse(execFileSync(python,['-c',`
import sys,json,subprocess,os
from pathlib import Path
from backend.engine.runtime_update import launch_plan
from backend.tests.test_native_update_layout import FRAME
root=Path(sys.argv[1]);command=launch_plan(root,sys.argv[2],pinned_authority_sha256=sys.argv[3]);file=Path(command['argv'][0]);result=subprocess.run(command['argv'],capture_output=True,text=True,timeout=5);link=file.parents[3]/(FRAME+'/Resources');original=os.readlink(link);link.unlink();link.symlink_to('Versions/A');print(json.dumps({'output':result.stdout,'exit_code':result.returncode,'link':str(link),'original':original,'database_fence':command['database_pointer']['fence'],'original_labels_preserved':(root/'projects/labels.json').read_bytes()==b'{"label":"original"}'}))
`,seeded.root,path.join(resources,'release-trust.json'),sha(fs.readFileSync(path.join(resources,'release-trust.json')))],{cwd:harness.REPO_ROOT,encoding:'utf8'}));
 expect(readback.output).toBe('owned-native-layout\n');expect(readback.exit_code).toBe(0);expect(readback.original_labels_preserved).toBe(true);
 const inspect=panel.getByRole('button',{name:'portable 상태 다시 읽기',exact:true});await inspect.click();await expect(panel.getByRole('alert')).toContainText('Installed native link target changed');await expect(apply).toHaveCount(0);await evidence.screenshot(page,'native-layout-changed-target-refused');
 fs.unlinkSync(readback.link);fs.symlinkSync(readback.original,readback.link);await inspect.click();await expect(panel.getByRole('alert')).toHaveCount(0);await expect(panel).toContainText('버전 1.0.0 · 데이터 세대 1');await expect(inspect).toBeEnabled();
 await page.reload();await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();await page.getByRole('button',{name:'설치·진단',exact:true}).click();await panel.getByRole('button',{name:'portable 설치 폴더 선택',exact:true}).click();await expect(panel).toContainText('버전 1.0.0 · 데이터 세대 1');await evidence.screenshot(page,'native-layout-restored-reopened');
 evidence.note('native_layout_review',{actual_main_manager:true,actual_signed_schema2_and_python_cli:true,actual_internal_links:3,actual_fixture_entrypoint_output:readback.output,original_labels_preserved:readback.original_labels_preserved,changed_target_refused_before_launch:true,restored_exact_raw_target:true,reopened_database_fence:1,commands,os_installer:false,native_codesign:'Controlled output only; no real publisher',renderer_host:'browser with explicit controlled bridge',human_quality_or_release_approval:false,independent_acceptance:false});
});
