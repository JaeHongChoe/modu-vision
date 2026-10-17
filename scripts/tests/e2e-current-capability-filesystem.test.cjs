'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const test=require('node:test');
const ts=require('typescript');
const {expect}=require('@playwright/test');
const root=path.resolve(__dirname,'../..');
function config(platform,grant){
 const source=fs.readFileSync(path.join(root,'playwright.config.ts'),'utf8');
 const exports={};
 const environment={MV_E2E_RUN_ID:'unit-capability-only',...(grant?{MV_E2E_F057_BROWSER_GRANT:grant}:{})};
 const requireConfig=name=>name==='@playwright/test'?{defineConfig:value=>value}:require(name);
 vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,
  {exports,require:requireConfig,process:{platform,argv:['node','playwright','--list'],env:environment},__dirname:root});
 return exports.default.projects.find(project=>project.name==='browser');
}
for(const platform of ['linux','darwin'])test(`${platform} without the explicit F057 grant leaves only its manual OS spec unselected`,()=>{
 const browser=config(platform);
 assert.equal(browser.testIgnore,'**/manual-model-selector-browser-os-escape.spec.ts');
 assert.equal(browser.metadata.F057_browser_OS.selected,false);
 assert.equal(browser.metadata.F057_browser_OS.reason,platform==='darwin'?'grant_not_provided':'unsupported_platform');
 assert.equal(browser.grepInvert.test('@electron'),true);
});
test('a supplied Darwin grant keeps the original strict manual fixture selected, even if its path is invalid',()=>{
 const browser=config('darwin','/nonexistent/grant-must-be-validated-by-original-fixture.json');
 assert.equal(browser.testIgnore,undefined);
 assert.equal(browser.metadata.F057_browser_OS.selected,true);
 assert.equal(browser.metadata.F057_browser_OS.reason,'provided_requires_original_fixture_validation');
 const file=path.join(root,'scripts/e2e/fixtures/browserOSPopupBridge.ts'),source=fs.readFileSync(file,'utf8');
 const parsed=ts.createSourceFile(file,source,ts.ScriptTarget.Latest,true);
 const selected=parsed.statements.filter(node=>(ts.isFunctionDeclaration(node)&&['readJSON','browserGrant'].includes(node.name.text))
  || (ts.isVariableStatement(node)&&node.declarationList.declarations.some(item=>item.name.getText(parsed)==='raw9')));
 const exports={};vm.runInNewContext(ts.transpileModule(selected.map(node=>node.getText(parsed)).join('\n'),
  {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,
  {exports,fs,path,expect,hash:()=>assert.fail('Invalid grant must refuse before hashing app bytes'),
   process:{env:{MV_E2E_F057_BROWSER_GRANT:'/nonexistent/grant-must-be-validated-by-original-fixture.json'}}});
 assert.throws(()=>exports.browserGrant(),error=>error.code==='ENOENT');
});
test('an explicitly supplied grant on an unsupported platform fails configuration instead of becoming an omitted pass',()=>{
 assert.throws(()=>config('linux','/nonexistent/explicit-grant.json'),/F057.*macOS/);
});
function delta(platform){
 const file=path.join(root,'scripts/e2e/derived-adoption-import-four-controls.spec.ts'),source=fs.readFileSync(file,'utf8');
 const parsed=ts.createSourceFile(file,source,ts.ScriptTarget.Latest,true);
 const node=parsed.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name.text==='namespaceDelta');
 assert(node);
 const compiled=ts.transpileModule(node.getText(parsed)+'\nexports.delta=namespaceDelta;',{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const exports={};vm.runInNewContext(compiled,{exports,expect,process:{platform}});return exports.delta;
}
function namespace(platform,added=false){
 const row=(kind,ino,links,names)=>({kind,raw:['1',String(ino),String(kind==='directory'?0o40700:0o100600),String(links),'40','100','100','501','20'],...(names?{names}:{sha256:'a'.repeat(64)})});
 const links=platform==='darwin'?3+(added?1:0):2;
 return {'':row('directory',1,3,['metadata']),metadata:row('directory',2,links,added?['new.json','old.json']:['old.json']),
  'metadata/old.json':row('file',3,1),...(added?{'metadata/new.json':row('file',4,1)}:{})};
}
for(const platform of ['linux','darwin'])test(`${platform} verifies exact declared file additions using that filesystem's directory link rule`,()=>{
 const result=delta(platform)(namespace(platform),namespace(platform,true),relative=>relative==='metadata/new.json',['metadata/new.json']);
 assert.deepEqual(Array.from(result.added_paths),['metadata/new.json']);
 assert.equal(result.declared_directory_link_deltas_verified,true);
});
for(const damage of ['regular-file-link','unplanned-addition'])test(`filesystem semantics still refuses ${damage}`,()=>{
 const before=namespace('linux'),after=namespace('linux',true);
 if(damage==='regular-file-link')after['metadata/old.json'].raw[3]='2';
 assert.throws(()=>delta('linux')(before,after,relative=>relative==='metadata/new.json',damage==='unplanned-addition'?[]:['metadata/new.json']));
});
