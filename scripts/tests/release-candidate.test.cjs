'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),cp=require('node:child_process');
const hash=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const gatePath=path.resolve(__dirname,'../release_candidate_gate.cjs');
const sourceRoot=process.env.RELEASE_PROPOSAL_SOURCE_ROOT||path.resolve(__dirname,'../..');
const temporaryRoot=path.resolve(__dirname,'../../.test-tmp');
fs.mkdirSync(temporaryRoot,{recursive:true});
function gate(){return require(gatePath);}
function context(){return {event:'workflow_dispatch',ref:'refs/heads/main',sourceSha:'a'.repeat(40),publisher:'ABC1234567',environment:{name:'release-candidate',protection_rules:[{type:'required_reviewers',prevent_self_review:true,reviewers:[{type:'Team',reviewer:{id:3}}]}],deployment_branch_policy:{protected_branches:true,custom_branch_policies:false}},branch:{name:'main',protected:true,commit:{sha:'a'.repeat(40)}}};}
test('CLI refuses absent real publisher before repository API or native checks',()=>{
 const r=cp.spawnSync(process.execPath,[gatePath,'preflight'],{encoding:'utf8',env:{PATH:process.env.PATH,GITHUB_EVENT_NAME:'workflow_dispatch',GITHUB_REF:'refs/heads/main',GITHUB_SHA:'a'.repeat(40)}});
 assert.equal(r.status,1);assert.match(r.stderr,/publisher TeamIdentifier is required/);assert.doesNotMatch(r.stderr,/MODULE_NOT_FOUND/);
});
test('existing protected environment, self-review exclusion and protected exact main are required',()=>{
 assert.equal(gate().checkProtectedContext(context()).status,'protected');
 for(const mutate of [c=>c.environment=null,c=>c.environment.protection_rules=[],c=>c.environment.protection_rules[0].prevent_self_review=false,c=>c.environment.protection_rules[0].reviewers=[],c=>c.environment.deployment_branch_policy.custom_branch_policies=true,c=>c.branch.protected=false,c=>c.branch.commit.sha='b'.repeat(40),c=>c.event='push',c=>c.ref='refs/tags/v1']){const c=context();mutate(c);assert.throws(()=>gate().checkProtectedContext(c));}
});
test('preflight reads existing policy and never creates an environment',async()=>{
 const c=context(),requests=[];
 const report=await gate().preflight({...c,repository:'owner/repo',apiUrl:'https://api.github.com',token:'private-token'},async(url,options)=>{requests.push({url,options});return {ok:true,json:async()=>url.includes('/environments/')?c.environment:c.branch};});
 assert.equal(report.source_sha,c.sourceSha);assert.equal(requests.length,2);assert.ok(requests.every(r=>r.options.method==='GET'));assert.ok(requests.every(r=>r.options.headers.Authorization==='Bearer private-token'));
 assert.ok(!JSON.stringify(report).includes('private-token'));
});
test('missing configured environment fails closed on its API 404',async()=>{
 await assert.rejects(gate().preflight({...context(),repository:'owner/repo',apiUrl:'https://api.github.com',token:'x'},async()=>({ok:false,status:404})),/policy read failed/);
});
function fixture(){
 const root=fs.realpathSync(fs.mkdtempSync(path.join(temporaryRoot,'release-candidate-test-'))),repo=path.join(root,'repo'),candidate=path.join(root,'candidate');fs.mkdirSync(repo);fs.mkdirSync(candidate);
 cp.execFileSync('git',['init','-q'],{cwd:repo});fs.mkdirSync(path.join(repo,'backend'));fs.writeFileSync(path.join(repo,'backend','main.py'),'fixture source');fs.writeFileSync(path.join(repo,'package-lock.json'),'fixture lock');fs.mkdirSync(path.join(repo,'docs'));fs.writeFileSync(path.join(repo,'docs','distribution-license-decision.md'),'fixture reviewed decision');cp.execFileSync('git',['add','.'],{cwd:repo});cp.execFileSync('git',['-c','user.name=Fixture','-c','user.email=controlled-fixture','commit','-qm','fixture'],{cwd:repo});
 const sourceSha=cp.execFileSync('git',['rev-parse','HEAD'],{cwd:repo,encoding:'utf8'}).trim(),app=path.join(candidate,'Vision.app'),resources=path.join(app,'Contents','Resources'),backend=path.join(resources,'backend_bin');fs.mkdirSync(backend,{recursive:true});fs.writeFileSync(path.join(resources,'app.asar'),'fixture renderer');fs.writeFileSync(path.join(backend,'backend'),'fixture binary');fs.writeFileSync(path.join(candidate,'Vision.dmg'),'fixture installer');
 function license(folder,desktop){fs.mkdirSync(folder);fs.writeFileSync(path.join(folder,'LICENSE'),'vendor bytes');fs.writeFileSync(path.join(folder,'manifest.json'),JSON.stringify({schema_version:1,status:'collected',missing:[],...(desktop?{package_lock_sha256:hash('fixture lock')}:{}),files:[{path:'LICENSE',sha256:hash('vendor bytes'),size:12}]}));}
 license(path.join(resources,'third_party_licenses'),true);license(path.join(backend,'third_party_licenses'),false);
 const build='b'.repeat(64),executableHash=hash('fixture binary');fs.writeFileSync(path.join(backend,'backend-release.json'),JSON.stringify({executable:'backend',executable_sha256:executableHash,inventory:{platform:'Darwin',architecture:'arm64',build_identity_sha256:build,offline:true,resources:[{path:'backend/main.py',sha256:hash('fixture source')}]},files:[{path:'backend',sha256:executableHash}],license_texts:{status:'collected'},acceptance:{status:'passed',platform:'Darwin',architecture:'arm64',build_identity_sha256:build,executable_sha256:executableHash,frozen:true,health:{status:'ok'},restart_health:{status:'ok'}}}));
 const pyzToc=path.join(candidate,'PYZ-00.toc');fs.writeFileSync(pyzToc,'fixture compiled module inventory');
 const binding={schema_version:1,source_sha:sourceSha,platform:'darwin',architecture:'arm64',publisher:'ABC1234567',app_name:'Vision.app',installer_name:'Vision.dmg',installer_sha256:hash('fixture installer'),app_asar_sha256:hash('fixture renderer'),backend_build_identity_sha256:build,backend_executable_sha256:executableHash,license_decision_sha256:hash('fixture reviewed decision'),backend_release_sha256:hash(fs.readFileSync(path.join(backend,'backend-release.json'))),pyz_toc_sha256:hash(fs.readFileSync(pyzToc))};
 const bindingPath=path.join(candidate,'candidate-binding.json');fs.writeFileSync(bindingPath,JSON.stringify(binding));const licenseReport=path.join(root,'license-report.json');fs.writeFileSync(licenseReport,JSON.stringify({allowed:true,scopes:{installer:{allowed:true},source:{allowed:true}}}));
 const options={root:repo,candidateDir:candidate,sourceSha,publisher:binding.publisher,bindingPin:hash(fs.readFileSync(bindingPath)),licenseReport,pyzToc};
 const checks=require(path.join(sourceRoot,'scripts','release-readiness.cjs')),licenses=require(path.join(sourceRoot,'build','package-license-texts.cjs'));let nativeCalls=0;
 const deps={platform:'darwin',arch:'arm64',checks:{...checks,nativeSignature(){nativeCalls++;return{status:'verified',publisher:binding.publisher,notarization:'unverified'};}},licenses};
 return {root,repo,candidate,backend,binding,bindingPath,options,deps,nativeCalls:()=>nativeCalls,close:()=>fs.rmSync(root,{recursive:true,force:true})};
}
test('prepared signed artifact yields a source-pinned candidate, never release approval or Windows pass',()=>{const f=fixture();try{const r=gate().candidate(f.options,f.deps);assert.equal(r.status,'candidate_validated');assert.equal(r.source_sha,f.options.sourceSha);assert.equal(r.release_ready,false);assert.equal(r.artifact_signature_ready,false);assert.equal(r.windows_native.status,'waived_by_user');assert.equal(r.windows_native.verified,false);assert.equal(r.installer_contains_app,'unverified');assert.equal(f.nativeCalls(),4);}finally{f.close();}});
test('wrong source, unsigned publisher, modified bytes, held license or changed decision all refuse a candidate',()=>{
 for(const mutate of [f=>f.options.publisher='',f=>f.options.sourceSha='c'.repeat(40),f=>f.options.bindingPin='d'.repeat(64),f=>fs.writeFileSync(path.join(f.candidate,'Vision.dmg'),'changed installer'),f=>fs.writeFileSync(path.join(f.repo,'backend','main.py'),'changed source'),f=>fs.writeFileSync(path.join(f.repo,'docs','distribution-license-decision.md'),'changed decision'),f=>fs.writeFileSync(f.options.licenseReport,JSON.stringify({allowed:false})),f=>f.deps.checks.nativeSignature=()=>({status:'unsigned'}),f=>f.deps.checks.nativeSignature=()=>({status:'verified',publisher:'OTHER12345'})]){const f=fixture();try{mutate(f);assert.throws(()=>gate().candidate(f.options,f.deps));}finally{f.close();}}
});
test('cross-target runner and linked binding fail before native observation',()=>{for(const mutate of [f=>f.deps.platform='linux',f=>f.deps.arch='x64',f=>{fs.renameSync(f.bindingPath,f.bindingPath+'.real');fs.symlinkSync(f.bindingPath+'.real',f.bindingPath);}]){const f=fixture();try{mutate(f);assert.throws(()=>gate().candidate(f.options,f.deps));assert.equal(f.nativeCalls(),0);}finally{f.close();}}});
test('artifact replacement during signature inspection cannot produce a successful manifest',()=>{const f=fixture();try{let n=0;f.deps.checks.nativeSignature=()=>{if(++n===3)fs.writeFileSync(path.join(f.candidate,'Vision.dmg'),'changed during inspection');return{status:'verified',publisher:f.binding.publisher};};assert.throws(()=>gate().candidate(f.options,f.deps),/changed|checksum/);}finally{f.close();}});
test('manifest writes exclusively and cannot overwrite an existing artifact',()=>{const f=fixture();try{const report=gate().candidate(f.options,f.deps),file=path.join(f.root,'manifest.json');gate().writeManifest(file,report);assert.equal(JSON.parse(fs.readFileSync(file)).release_ready,false);assert.throws(()=>gate().writeManifest(file,report),/EEXIST/);}finally{f.close();}});
test('configuration refuses absent publisher, pin or mismatched compiled module inventory before native calls',()=>{for(const mutate of [f=>f.options.publisher='',f=>f.options.bindingPin='',f=>f.options.pyzToc='',f=>fs.writeFileSync(f.options.pyzToc,'different inventory')]){const f=fixture();try{mutate(f);assert.throws(()=>gate().configuration(f.options));assert.equal(f.nativeCalls(),0);}finally{f.close();}}});
test('pinned failed health receipt cannot become acceptance through object truthiness',()=>{const f=fixture();try{const file=path.join(f.backend,'backend-release.json'),receipt=JSON.parse(fs.readFileSync(file));receipt.acceptance.health.status='error';fs.writeFileSync(file,JSON.stringify(receipt));f.binding.backend_release_sha256=hash(fs.readFileSync(file));fs.writeFileSync(f.bindingPath,JSON.stringify(f.binding));f.options.bindingPin=hash(fs.readFileSync(f.bindingPath));assert.throws(()=>gate().candidate(f.options,f.deps),/launch and restart health/);assert.equal(f.nativeCalls(),0);}finally{f.close();}});
test('parsed workflow permits manual protected candidate diagnostics only, with policy and binding before source work',()=>{
 const yaml=require('node:module').createRequire(path.join(sourceRoot,'package.json'))('js-yaml');
 const workflow=yaml.load(fs.readFileSync(path.resolve(__dirname,'../../.github/workflows/release.yml'),'utf8'));
 assert.deepEqual(Object.keys(workflow.on),['workflow_dispatch']);assert.deepEqual(workflow.permissions,{contents:'read',actions:'read'});
 const preflight=workflow.jobs.preflight,candidate=workflow.jobs.candidate;
 assert.equal(preflight.environment,undefined);assert.equal(candidate.needs,'preflight');assert.equal(candidate.environment,'release-candidate');assert.deepEqual(candidate['runs-on'],['self-hosted','macOS','ARM64','vision-release-candidate']);
 assert.match(preflight.if,/workflow_dispatch/);assert.match(preflight.if,/refs\/heads\/main/);assert.match(candidate.if,/workflow_dispatch/);assert.match(candidate.if,/refs\/heads\/main/);
 const runs=candidate.steps.filter(s=>s.run).map(s=>s.run);assert.equal(runs[0],'node scripts/release_candidate_gate.cjs preflight');assert.equal(runs[1],'node scripts/release_candidate_gate.cjs configuration');
 assert.ok(runs.findIndex(s=>s.includes('distribution_release_gate.py'))<runs.findIndex(s=>s==='node scripts/release_candidate_gate.cjs candidate'));
 const allRuns=Object.values(workflow.jobs).flatMap(j=>j.steps.filter(s=>s.run).map(s=>s.run)).join('\n');
 assert.doesNotMatch(allRuns,/--sign|--force|notarytool|security import|security create-keychain|gh release|--publish|electron-builder|curl.*(?:POST|PUT)|defaults write/i);
 assert.ok(Object.values(workflow.jobs).flatMap(j=>j.steps).filter(s=>s.uses).every(s=>/@[a-f0-9]{40}$/.test(s.uses)));
 assert.equal(candidate.env.CSC_IDENTITY_AUTO_DISCOVERY,'false');assert.doesNotMatch(JSON.stringify(workflow),/secrets\./);
 const upload=candidate.steps.at(-1);assert.match(upload.with.path,/\*\.json$/);assert.ok(!Object.keys(workflow.jobs).some(j=>/windows|publish|sign|deploy/i.test(j)));
});
test('stable reader rejects growth at admitted size, oversized inputs, hard links and same-byte named replacement',()=>{
 const root=fs.mkdtempSync(path.join(temporaryRoot,'stable-read-')),file=path.join(root,'bytes');
 const originalRead=fs.readSync;
 try{
   fs.writeFileSync(file,'fixture');assert.throws(()=>gate().stableRead(file,{maxBytes:6}),/byte bound/);
   fs.linkSync(file,file+'.hard');assert.throws(()=>gate().fingerprint(file),/one link/);fs.unlinkSync(file+'.hard');
   let once=false;fs.readSync=(...args)=>{const n=originalRead(...args);if(!once){once=true;fs.appendFileSync(file,'growth');}return n;};
   assert.throws(()=>gate().stableRead(file,{maxBytes:32}),/grew/);fs.readSync=originalRead;
   fs.writeFileSync(file,'fixture');once=false;fs.readSync=(...args)=>{const n=originalRead(...args);if(!once){once=true;fs.renameSync(file,file+'.old');fs.writeFileSync(file,'fixture');}return n;};
   assert.throws(()=>gate().stableRead(file,{maxBytes:32}),/changed/);
 }finally{fs.readSync=originalRead;fs.rmSync(root,{recursive:true,force:true});}
});
test('stable reader catches descriptor ctime changes even when size and bytes stay identical',()=>{
 const root=fs.mkdtempSync(path.join(temporaryRoot,'stable-ctime-')),file=path.join(root,'bytes');fs.writeFileSync(file,'fixture',{mode:0o644});const originalRead=fs.readSync;let once=false;
 try{fs.readSync=(...args)=>{const n=originalRead(...args);if(!once){once=true;fs.chmodSync(file,0o600);}return n;};assert.throws(()=>gate().fingerprint(file),/changed/);}finally{fs.readSync=originalRead;fs.rmSync(root,{recursive:true,force:true});}
});
test('FIFO is rejected without a blocking open or read',()=>{
 const root=fs.mkdtempSync(path.join(temporaryRoot,'stable-fifo-')),file=path.join(root,'fifo');
 try{cp.execFileSync('mkfifo',[file]);const r=cp.spawnSync(process.execPath,['-e','require(process.argv[1]).fingerprint(process.argv[2])',gatePath,file],{encoding:'utf8',timeout:2000});assert.equal(r.error,undefined);assert.equal(r.status,1);assert.match(r.stderr,/regular artifact file/);}finally{fs.rmSync(root,{recursive:true,force:true});}
});
test('license JSON growth is rejected from the same bounded snapshot that would be parsed',()=>{
 const f=fixture(),originalRead=fs.readSync,ino=fs.statSync(f.options.licenseReport).ino;let once=false;
 try{fs.readSync=(...args)=>{const n=originalRead(...args);if(!once&&fs.fstatSync(args[0]).ino===ino){once=true;fs.appendFileSync(f.options.licenseReport,' ');}return n;};assert.throws(()=>gate().candidate(f.options,f.deps),/grew/);assert.equal(f.nativeCalls(),0);}finally{fs.readSync=originalRead;f.close();}
});
test('source change during native observations blocks the candidate',()=>{
 const f=fixture();try{let n=0;f.deps.checks.nativeSignature=()=>{if(++n===3)fs.writeFileSync(path.join(f.repo,'backend','main.py'),'changed during native check');return{status:'verified',publisher:f.binding.publisher};};assert.throws(()=>gate().candidate(f.options,f.deps),/Source checkout changed/);}finally{f.close();}
});
test('native app shell mutation after its first signature check is caught by the final complete-app seal check',()=>{
 const f=fixture(),shell=path.join(f.candidate,'Vision.app','Contents','MacOS','Vision');fs.mkdirSync(path.dirname(shell));fs.writeFileSync(shell,'original native shell');
 try{let n=0;f.deps.checks.nativeSignature=target=>{n++;if(n===2)fs.writeFileSync(shell,'changed native shell');if(target.endsWith('.app')&&n>1&&fs.readFileSync(shell,'utf8')!=='original native shell')return{status:'invalid',publisher:f.binding.publisher};return{status:'verified',publisher:f.binding.publisher};};assert.throws(()=>gate().candidate(f.options,f.deps),/Application signature changed/);assert.equal(n,4);}finally{f.close();}
});
