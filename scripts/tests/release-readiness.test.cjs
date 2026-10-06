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
