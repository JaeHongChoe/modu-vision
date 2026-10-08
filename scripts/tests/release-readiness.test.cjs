/* Controlled native-command responses test the gate; no publisher is provisioned. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),vm=require('node:vm'),crypto=require('node:crypto');
const digest=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
function fixture(platform){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'owned-release-readiness-'));
 const binary=path.join(root,platform==='win32'?'backend.exe':'backend');fs.writeFileSync(binary,'controlled native artifact');
 const hash=digest(fs.readFileSync(binary)),build='a'.repeat(64),architecture='x64';
 fs.writeFileSync(path.join(root,'backend-release.json'),JSON.stringify({executable:path.basename(binary),executable_sha256:hash,
   inventory:{platform,architecture,build_identity_sha256:build,offline:true},files:[{path:path.basename(binary),sha256:hash}],
   acceptance:{status:'passed',platform,architecture,frozen:true,health:{status:'ok'},restart_health:{status:'ok'},executable_sha256:hash,build_identity_sha256:build}}));
 const child={execFileSync(command){return command==='powershell.exe'?JSON.stringify({status:'Valid',publisher:'Controlled Publisher'}):'';},spawnSync(){return{status:0,stdout:'',stderr:'TeamIdentifier=CONTROLLED\nSignature=Developer ID Application'};}};
 const module={exports:{}};
 vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../release-readiness.cjs'),'utf8'),{module,require:name=>name==='node:child_process'?child:require(name),process:{platform,arch:architecture},console},{filename:'release-readiness.cjs'});
 return{root,binary,readiness:options=>module.exports.readiness({backendDir:root,app:binary,physicalAcceptance:true,...options}),close:()=>fs.rmSync(root,{recursive:true,force:true})};
}
test('matching macOS signatures without notarization never qualify a release',()=>{
 const f=fixture('darwin');try{const result=f.readiness();assert.equal(result.status,'runtime_ready');assert.equal(result.signature.status,'verified');assert.equal(result.signature.notarization,'unverified');assert.equal(result.release_ready,false);}finally{f.close();}
});
test('a caller boolean cannot substitute for reviewed Windows target and quality receipts',()=>{
 const f=fixture('win32');try{const result=f.readiness();assert.equal(result.status,'runtime_ready');assert.equal(result.signature.status,'verified');assert.equal(result.release_ready,false);}finally{f.close();}
});
test('failed artifact identity retains an explicit non-ready release result',()=>{
 const f=fixture('darwin');try{fs.writeFileSync(f.binary,'changed after inventory');const result=f.readiness();assert.equal(result.status,'failed');assert.equal(result.release_ready,false);}finally{f.close();}
});

for(const field of ['health','restart_health'])for(const status of [null,'error','foreign']){
 test('packaging refuses '+field+' '+(status===null?'without a status':status),()=>{
  const f=fixture('darwin');try{
   const file=path.join(f.root,'backend-release.json'),release=JSON.parse(fs.readFileSync(file,'utf8'));
   release.acceptance[field]=status===null?{}:{status};fs.writeFileSync(file,JSON.stringify(release));
   const result=f.readiness();assert.equal(result.status,'failed');assert.match(result.error,/launch\/restart acceptance/);assert.equal(result.release_ready,false);
  }finally{f.close();}
 });
}
for(const frozen of ['false','true',1]){
 test('packaging refuses non-boolean frozen '+JSON.stringify(frozen),()=>{
  const f=fixture('darwin');try{
   const file=path.join(f.root,'backend-release.json'),release=JSON.parse(fs.readFileSync(file,'utf8'));
   release.acceptance.frozen=frozen;fs.writeFileSync(file,JSON.stringify(release));
   const result=f.readiness();assert.equal(result.status,'failed');assert.match(result.error,/launch\/restart acceptance/);assert.equal(result.release_ready,false);
  }finally{f.close();}
 });
}
for(const status of ['ok','ready']){
 test('packaging retains the original successful health contract '+status,()=>{
  const f=fixture('darwin');try{
   const file=path.join(f.root,'backend-release.json'),release=JSON.parse(fs.readFileSync(file,'utf8'));
   release.acceptance.health.status=status;release.acceptance.restart_health.status=status;fs.writeFileSync(file,JSON.stringify(release));
   const result=f.readiness();assert.equal(result.status,'runtime_ready');assert.equal(result.release_ready,false);
  }finally{f.close();}
 });
}
test('source workflow selects the owned parallel-intent cleanup regressions exactly once',()=>{
 const source=fs.readFileSync(path.resolve(__dirname,'../../.github/workflows/ci.yml'),'utf8');
 const selection=source.split('\n').filter(line=>line.trim()==='backend/tests/test_runtime_update_parallel_intent.py');
 assert.equal(selection.length,1);
 assert.ok(source.includes('backend/tests/test_application_backend_intent_composition.py'));
});
