const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const crypto=require('node:crypto');
const Module=require('node:module');
const test=require('node:test');
const ts=require('typescript');

function load(relative,electron) {
  const filename=path.resolve(__dirname,'../src/main',relative);
  const loaded=new Module(filename,module);loaded.filename=filename;loaded.paths=Module._nodeModulePaths(path.dirname(filename));
  const original=loaded.require.bind(loaded);
  loaded.require=name=>name==='electron'?electron:original(name);
  loaded._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,filename);
  return loaded.exports;
}
const {DistributionManager}=load('distributionStatus.ts',null);
const digest=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');

test('manual release download records interruption and preserves installed executable',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vision-delivery-'));
  try {
    const installed=path.join(root,'installed.exe');fs.writeFileSync(installed,'working installation');
    const original=digest(fs.readFileSync(installed));
    const bytes=Buffer.from('candidate');
    const manifest={version:'0.2.0',channel:'stable',platform:'win32',arch:'x64',url:'https://release.example/candidate.exe',sha256:digest(bytes),size:bytes.length};
    const manager=new DistributionManager({appVersion:'0.1.0',packaged:true,appPath:root,executablePath:installed,userDataPath:root,platform:'win32',arch:'x64',
      runner:async()=>({stdout:JSON.stringify({status:'Valid',publisher:'Fixture Publisher'}),stderr:''}),
      fetcher:async url=>{
        if(String(url).endsWith('manifest.json'))return new Response(JSON.stringify(manifest));
        let count=0;
        return new Response(new ReadableStream({pull(controller){if(count++===0)controller.enqueue(bytes.subarray(0,2));else controller.error(new Error('connection interrupted'));}}));
      }});
    await manager.configure({channel:'stable',manifest_url:'https://release.example/manifest.json'});
    await manager.check();
    await assert.rejects(manager.download(),/interrupted/);
    const state=await manager.status();
    assert.equal(state.update.recovery.status,'failed');
    assert.equal(state.update.recovery.candidate_sha256,manifest.sha256);
    assert.equal(state.update.recovery.installed_sha256,original);
    assert.equal(digest(fs.readFileSync(installed)),original);
    assert.equal(fs.readdirSync(path.join(root,'updates')).some(name=>name.endsWith('.partial')),false);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('packaged launcher refuses a missing frozen backend instead of system dependency installation',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vision-launcher-'));
  const previous=process.resourcesPath;process.resourcesPath=root;
  try {
    const {BackendSupervisor}=load('supervisor.ts',{app:{isPackaged:true,getPath:()=>root,getAppPath:()=>root}});
    const supervisor=new BackendSupervisor();
    assert.throws(()=>supervisor.resolveStandaloneBinary(),/frozen backend/i);
  } finally {process.resourcesPath=previous;fs.rmSync(root,{recursive:true,force:true});}
});

test('manual download restart exposes an interrupted journal and removes only its own partial file',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vision-interrupted-'));
  try {
    const updates=path.join(root,'updates');fs.mkdirSync(updates);
    const partial=path.join(updates,'.owned.partial');const unrelated=path.join(updates,'.other.partial');
    fs.writeFileSync(partial,'half');fs.writeFileSync(unrelated,'unrelated');
    fs.writeFileSync(path.join(root,'distribution-delivery.json'),JSON.stringify({schema_version:1,status:'downloading',version:'0.2.0',manifest_sha256:'a'.repeat(64),candidate_sha256:'b'.repeat(64),installed_sha256:null,partial_path:partial,checked_at:new Date().toISOString()}));
    const manager=new DistributionManager({appVersion:'0.1.0',packaged:false,appPath:root,executablePath:process.execPath,userDataPath:root,platform:process.platform,arch:process.arch});
    const state=await manager.status();
    assert.equal(state.update.recovery.status,'interrupted');
    assert.equal(fs.existsSync(partial),false);assert.equal(fs.existsSync(unrelated),true);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('packaged frozen startup allows runtime initialization before port discovery',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vision-cold-launch-'));
  const previous=process.resourcesPath;process.resourcesPath=root;
  let supervisor;
  try {
    const directory=path.join(root,'backend_bin');fs.mkdirSync(directory);
    const name=process.platform==='win32'?'vision_ai_backend.exe':'vision_ai_backend';
    if(process.platform==='win32')return;
    const binary=path.join(directory,name);
    fs.writeFileSync(binary,`#!/usr/bin/env node\nconst http=require('node:http');setTimeout(()=>{const server=http.createServer((req,res)=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify({status:'ok',version:'0.1.0',device:'cpu',device_name:'Fixture CPU'}));});server.listen(0,'127.0.0.1',()=>console.log('VISION_AI_STUDIO_PORT='+server.address().port));},250);`);fs.chmodSync(binary,0o755);
    fs.writeFileSync(path.join(directory,'backend-release.json'),JSON.stringify({executable:name,executable_sha256:digest(fs.readFileSync(binary)),inventory:{build_identity_sha256:'a'.repeat(64)}}));
    const {BackendSupervisor}=load('supervisor.ts',{app:{isPackaged:true,getPath:()=>root,getAppPath:()=>root,getVersion:()=>'0.1.0'}});
    supervisor=new BackendSupervisor({portDiscoveryTimeoutMs:50,autoRestart:false,gracefulShutdownTimeoutMs:500});
    assert.ok(await supervisor.start()>0);
    assert.equal(supervisor.getStatusInfo().runtimeIdentity.executable_sha256,digest(fs.readFileSync(binary)));
  } finally {if(supervisor)await supervisor.stop();process.resourcesPath=previous;fs.rmSync(root,{recursive:true,force:true});}
});


test('development onedir uses only executable files and explicit source QA uses private project storage',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vision-dev-resolver-'));
  const previousSource=process.env.VISION_AI_STUDIO_DEV_SOURCE_BACKEND;
  const previousData=process.env.VISION_AI_STUDIO_USER_DATA_DIR;
  try {
    const directory=path.join(root,'dist-backend','vision_ai_backend');fs.mkdirSync(directory,{recursive:true});
    const binary=path.join(directory,process.platform==='win32'?'vision_ai_backend.exe':'vision_ai_backend');fs.writeFileSync(binary,'native fixture');
    const userData=path.join(root,'private-state');
    const {BackendSupervisor}=load('supervisor.ts',{app:{isPackaged:false,getPath:()=>userData,getAppPath:()=>root}});
    const supervisor=new BackendSupervisor();
    assert.equal(supervisor.resolveStandaloneBinary(),binary);
    process.env.VISION_AI_STUDIO_DEV_SOURCE_BACKEND='1';
    process.env.VISION_AI_STUDIO_USER_DATA_DIR=userData;
    assert.equal(supervisor.resolveStandaloneBinary(),null);
    assert.equal(supervisor.resolveProjectDir(),path.join(userData,'projects'));
  } finally {
    if(previousSource===undefined)delete process.env.VISION_AI_STUDIO_DEV_SOURCE_BACKEND;else process.env.VISION_AI_STUDIO_DEV_SOURCE_BACKEND=previousSource;
    if(previousData===undefined)delete process.env.VISION_AI_STUDIO_USER_DATA_DIR;else process.env.VISION_AI_STUDIO_USER_DATA_DIR=previousData;
    fs.rmSync(root,{recursive:true,force:true});
  }
});

test('failed native spawn without a PID rejects promptly and shutdown does not wait for a nonexistent child',async()=>{
  if(process.platform==='win32')return;
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vision-spawn-failure-'));
  let supervisor;
  try {
    const directory=path.join(root,'dist-backend','vision_ai_backend');fs.mkdirSync(directory,{recursive:true});
    const binary=path.join(directory,'vision_ai_backend');fs.writeFileSync(binary,'not executable');fs.chmodSync(binary,0o600);
    const {BackendSupervisor}=load('supervisor.ts',{app:{isPackaged:false,getPath:()=>root,getAppPath:()=>root,getVersion:()=>'0.1.0'}});
    supervisor=new BackendSupervisor({portDiscoveryTimeoutMs:1500,gracefulShutdownTimeoutMs:1000,autoRestart:false});
    const started=Date.now();
    await assert.rejects(supervisor.start(),/EACCES|spawn/);
    assert.ok(Date.now()-started<750,'failed spawn must not wait for discovery timeout');
    const stopped=Date.now();await supervisor.stop();
    assert.ok(Date.now()-stopped<250,'no PID means no live process to terminate');
    assert.equal(supervisor.getStatusInfo().pid,null);
  } finally {if(supervisor)await supervisor.stop();fs.rmSync(root,{recursive:true,force:true});}
});
