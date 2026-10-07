#!/usr/bin/env node
'use strict';
// Read-only candidate validation. No signer, updater, installer or publisher is invoked.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),cp=require('node:child_process');
const ENVIRONMENT='release-candidate';
const SHA=/^[a-f0-9]{64}$/;
const FILE_LIMIT=2*1024*1024*1024,JSON_LIMIT=8*1024*1024;
function publisher(value){
  if(typeof value!=='string'||!value)throw new Error('A real publisher TeamIdentifier is required before release preparation');
  if(!/^[A-Z0-9]{10}$/.test(value)||/^(.)\1{9}$/.test(value))throw new Error('Publisher must be the actual 10-character Apple TeamIdentifier');
  return value;
}
function source(value){if(typeof value!=='string'||!/^[a-f0-9]{40}$/.test(value))throw new Error('Exact source commit is required');return value;}
function checkProtectedContext(c){
  publisher(c.publisher);source(c.sourceSha);
  if(c.event!=='workflow_dispatch'||c.ref!=='refs/heads/main')throw new Error('Only manual dispatch on protected main is permitted');
  if(!c.environment||c.environment.name!==ENVIRONMENT)throw new Error('Existing release-candidate environment is required');
  const rule=c.environment.protection_rules?.find(r=>r.type==='required_reviewers');
  if(!rule||rule.prevent_self_review!==true||!Array.isArray(rule.reviewers)||!rule.reviewers.length||!rule.reviewers.every(r=>['User','Team'].includes(r.type)&&Number.isSafeInteger(r.reviewer?.id)&&r.reviewer.id>0))throw new Error('Required reviewers and prevention of self-review must already be configured');
  const policy=c.environment.deployment_branch_policy;
  if(!policy||policy.protected_branches!==true||policy.custom_branch_policies!==false)throw new Error('Environment must permit protected branches only');
  if(c.branch?.name!=='main'||c.branch.protected!==true||c.branch.commit?.sha!==c.sourceSha)throw new Error('The dispatched exact main commit must still be protected and current');
  return {schema_version:1,status:'protected',source_sha:c.sourceSha,publisher:c.publisher,environment:ENVIRONMENT,reviewer_count:rule.reviewers.length,release_ready:false};
}
async function preflight(c,request=globalThis.fetch){
  // This local check precedes API requests. The API only reads existing settings.
  publisher(c.publisher);source(c.sourceSha);
  if(c.event!=='workflow_dispatch'||c.ref!=='refs/heads/main')throw new Error('Only manual dispatch on protected main is permitted');
  if(!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(c.repository||'')||!c.token)throw new Error('Read-only repository policy access is required');
  const base=new URL(c.apiUrl||'https://api.github.com');
  if(base.protocol!=='https:'||base.username||base.password||base.search||base.hash)throw new Error('HTTPS repository API URL is required');
  const get=async tail=>{
    const response=await request(base.href.replace(/\/$/,'')+'/repos/'+c.repository+tail,{method:'GET',redirect:'error',headers:{Authorization:'Bearer '+c.token,Accept:'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'},signal:AbortSignal.timeout(15000)});
    if(!response.ok)throw new Error('Existing release policy read failed (HTTP '+response.status+')');
    return response.json();
  };
  const environment=await get('/environments/'+ENVIRONMENT),branch=await get('/branches/main');
  return checkProtectedContext({...c,environment,branch});
}
function stableRead(file,{maxBytes=FILE_LIMIT,collect=false}={}){
  if(!Number.isSafeInteger(maxBytes)||maxBytes<0||maxBytes>FILE_LIMIT)throw new Error('Invalid reviewed file byte bound');
  const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW|fs.constants.O_NONBLOCK);
  try{
    const before=fs.fstatSync(fd,{bigint:true});
    if(!before.isFile()||before.nlink!==1n)throw new Error('Expected regular artifact file with one link');
    if(before.size>BigInt(maxBytes))throw new Error('Artifact exceeds reviewed byte bound');
    const expected=Number(before.size),hash=crypto.createHash('sha256'),buffer=Buffer.alloc(64*1024),chunks=[];let total=0;
    // Read at most the admitted size plus one growth sentinel, never an open
    // ended stream. A continuously growing regular file cannot extend this loop.
    while(total<=expected){
      const count=fs.readSync(fd,buffer,0,Math.min(buffer.length,expected-total+1),null);
      if(!count)break;total+=count;
      if(total>expected)throw new Error('Artifact grew while inspecting');
      hash.update(buffer.subarray(0,count));if(collect)chunks.push(Buffer.from(buffer.subarray(0,count)));
    }
    const after=fs.fstatSync(fd,{bigint:true}),named=fs.lstatSync(file,{bigint:true});
    const keys=['dev','ino','nlink','size','mtimeNs','ctimeNs'];
    if(total!==expected||!named.isFile()||named.isSymbolicLink()||keys.some(key=>before[key]!==after[key]||before[key]!==named[key]))throw new Error('Artifact changed while inspecting');
    return {sha256:hash.digest('hex'),size:total,...(collect?{bytes:Buffer.concat(chunks,total)}:{})};
  }finally{fs.closeSync(fd);}
}
function fingerprint(file){return stableRead(file).sha256;}
function jsonSnapshot(file,maxBytes=JSON_LIMIT){const snapshot=stableRead(file,{maxBytes,collect:true});return {...snapshot,value:JSON.parse(snapshot.bytes)};}
function basename(value,suffix){if(typeof value!=='string'||path.basename(value)!==value||value.includes('\\')||/[\x00-\x1f\x7f]/.test(value)||!value.endsWith(suffix)||value===suffix)throw new Error('Invalid prepared artifact name');return value;}
function configuration(o){
  publisher(o.publisher);source(o.sourceSha);
  if(!SHA.test(o.bindingPin||''))throw new Error('Independent candidate-binding SHA-256 pin is required');
  if(!o.candidateDir||!path.isAbsolute(o.candidateDir)||/[\x00-\x1f\x7f]/.test(o.candidateDir))throw new Error('Absolute operator-prepared candidate directory is required');
  const directory=path.resolve(o.candidateDir);
  if(fs.realpathSync(directory)!==directory||!fs.lstatSync(directory).isDirectory())throw new Error('Candidate directory must be a real directory');
  const bindingFile=path.join(directory,'candidate-binding.json');
  const snapshot=jsonSnapshot(bindingFile,64*1024);
  if(snapshot.sha256!==o.bindingPin)throw new Error('Candidate binding checksum differs from independent pin');
  const binding=snapshot.value;
  if(binding.schema_version!==1||binding.source_sha!==o.sourceSha||binding.publisher!==o.publisher||binding.platform!=='darwin'||binding.architecture!=='arm64')throw new Error('Candidate source, publisher or native target differs from dispatch');
  basename(binding.app_name,'.app');basename(binding.installer_name,'.dmg');
  if(!o.pyzToc||!path.isAbsolute(o.pyzToc)||!SHA.test(binding.pyz_toc_sha256||'')||fingerprint(o.pyzToc)!==binding.pyz_toc_sha256)throw new Error('Exact reviewed PyInstaller inventory is required');
  return {directory,bindingFile,binding,backendDir:path.join(directory,binding.app_name,'Contents','Resources','backend_bin')};
}
function candidate(o,deps){
  publisher(o.publisher);source(o.sourceSha);
  const platform=deps?.platform||process.platform,arch=deps?.arch||process.arch;
  if(platform!=='darwin'||arch!=='arm64')throw new Error('Run candidate checks on actual macOS arm64; Windows native acceptance is waived, not verified');
  const root=path.resolve(o.root||path.resolve(__dirname,'..'));
  const {directory,bindingFile,binding}=configuration(o);
  const pins=new Map();
  const pin=(file,expected)=>{if(!SHA.test(expected||''))throw new Error('Missing SHA-256 binding');const observed=fingerprint(file);if(observed!==expected)throw new Error('Candidate checksum differs: '+path.basename(file));pins.set(file,observed);return observed;};
  pin(bindingFile,o.bindingPin);
  const git=(args)=>cp.execFileSync('git',args,{cwd:root,encoding:'utf8',timeout:15000}).trim();
  const checkSource=()=>{if(git(['rev-parse','HEAD'])!==o.sourceSha||git(['status','--porcelain','--untracked-files=no']))throw new Error('Source checkout changed from the dispatched commit');};checkSource();
  const app=path.join(directory,basename(binding.app_name,'.app')),installer=path.join(directory,basename(binding.installer_name,'.dmg'));
  if(!fs.lstatSync(app).isDirectory()||fs.lstatSync(app).isSymbolicLink())throw new Error('Prepared application must be a real app bundle');
  const resources=path.join(app,'Contents','Resources'),backendDir=path.join(resources,'backend_bin');
  pin(installer,binding.installer_sha256);pin(path.join(resources,'app.asar'),binding.app_asar_sha256);
  pin(path.join(root,'docs','distribution-license-decision.md'),binding.license_decision_sha256);
  const checks=deps?.checks||require(path.join(root,'scripts','release-readiness.cjs'));
  const licenses=deps?.licenses||require(path.join(root,'build','package-license-texts.cjs'));
  const releaseFile=path.join(backendDir,'backend-release.json'),releaseSnapshot=jsonSnapshot(releaseFile);
  if(releaseSnapshot.sha256!==binding.backend_release_sha256)throw new Error('Backend receipt checksum differs from independent pin');
  pins.set(releaseFile,releaseSnapshot.sha256);pin(o.pyzToc,binding.pyz_toc_sha256);
  const backend=checks.validateBackend(backendDir,'darwin','arm64');
  if(JSON.stringify(backend.release)!==JSON.stringify(releaseSnapshot.value))throw new Error('Backend receipt changed during validation');
  if(backend.build_identity_sha256!==binding.backend_build_identity_sha256||backend.executable_sha256!==binding.backend_executable_sha256||backend.offline!==true)throw new Error('Frozen backend build or offline contract differs from approved binding');
  if(!['ok','ready'].includes(backend.release.acceptance.health?.status)||!['ok','ready'].includes(backend.release.acceptance.restart_health?.status))throw new Error('Actual native launch and restart health receipts are required');
  pin(backend.executable,binding.backend_executable_sha256);
  if(!Array.isArray(backend.release.inventory.resources)||!backend.release.inventory.resources.length)throw new Error('Frozen backend source inventory is required');
  for(const row of backend.release.inventory.resources){
    if(typeof row.path!=='string'||path.isAbsolute(row.path)||row.path.split(/[\\/]/).includes('..'))throw new Error('Invalid backend source resource path');
    const file=path.join(root,row.path);if(!fs.realpathSync(file).startsWith(fs.realpathSync(root)+path.sep))throw new Error('Backend source escapes checkout');pin(file,row.sha256);
  }
  const bundledLicenses={};
  for(const [kind,folder] of [['python',path.join(backendDir,'third_party_licenses')],['desktop',path.join(resources,'third_party_licenses')]]){
    const manifestFile=path.join(folder,'manifest.json'),manifestSnapshot=jsonSnapshot(manifestFile);
    const receipt=licenses.verifyBundle(folder);
    if(JSON.stringify(receipt)!==JSON.stringify(manifestSnapshot.value))throw new Error('License receipt changed during validation');
    if(receipt.status!=='collected'||!receipt.files.length||!Array.isArray(receipt.missing)||receipt.missing.length)throw new Error('Full bundled '+kind+' license bytes are required');
    if(kind==='desktop')pin(path.join(root,'package-lock.json'),receipt.package_lock_sha256);
    pins.set(manifestFile,manifestSnapshot.sha256);for(const row of receipt.files)pin(path.join(folder,row.path),row.sha256);
    bundledLicenses[kind]={status:receipt.status,file_count:receipt.files.length};
  }
  if(!o.licenseReport)throw new Error('Existing fresh distribution license gate report is required');
  const licenseSnapshot=jsonSnapshot(o.licenseReport),license=licenseSnapshot.value;
  if(license.allowed!==true||license.scopes?.source?.allowed!==true||license.scopes?.installer?.allowed!==true)throw new Error('Existing distribution license gate holds release preparation');
  pins.set(o.licenseReport,licenseSnapshot.sha256);
  // validateBackend consumes its real native launch/restart acceptance; signatures
  // use the same read-only observer as release-readiness, including frozen child.
  const signatures={app:checks.nativeSignature(app,'darwin'),backend:checks.nativeSignature(backend.executable,'darwin'),installer:checks.nativeSignature(installer,'darwin')};
  for(const [kind,value] of Object.entries(signatures))if(value.status!=='verified'||value.publisher!==o.publisher)throw new Error('Verified expected publisher missing or mismatched on '+kind);
  checks.validateBackend(backendDir,'darwin','arm64');checkSource();for(const [file,expected]of pins)if(fingerprint(file)!==expected)throw new Error('Artifact changed after inspection: '+path.basename(file));
  // app.asar and the frozen child do not inventory the native shell and every
  // app resource. Re-observe the complete app seal last, after all other checks.
  signatures.app=checks.nativeSignature(app,'darwin');
  if(signatures.app.status!=='verified'||signatures.app.publisher!==o.publisher)throw new Error('Application signature changed after artifact inspection');
  return {schema_version:1,checked_at:new Date().toISOString(),status:'candidate_validated',source_sha:o.sourceSha,platform:'darwin',architecture:'arm64',publisher:o.publisher,binding_sha256:o.bindingPin,
    artifacts:{app:binding.app_name,installer:binding.installer_name,installer_sha256:binding.installer_sha256,app_asar_sha256:binding.app_asar_sha256,backend_build_identity_sha256:backend.build_identity_sha256,backend_executable_sha256:backend.executable_sha256},
    backend_acceptance:backend.release.acceptance,signatures,bundled_licenses:bundledLicenses,distribution_license_gate:'allowed',
    windows_native:{status:'waived_by_user',verified:false},installer_contains_app:'unverified',artifact_signature_ready:false,release_ready:false,
    prerequisites:['Reviewed notarization and Gatekeeper receipt for delivered installer','Verify installer contains the exact checked application and target launch/restart','Representative model truth and physical equipment acceptance','First-use pilot, operational owner and source-bound release decision']};
}
function writeManifest(file,report){fs.writeFileSync(file,JSON.stringify(report,null,2)+'\n',{flag:'wx',mode:0o600});}
async function cli(){
  const mode=process.argv[2],env=process.env;
  publisher(env.RELEASE_TEAM_ID);
  if(mode==='preflight'){
    const report=await preflight({event:env.GITHUB_EVENT_NAME,ref:env.GITHUB_REF,sourceSha:env.GITHUB_SHA,publisher:env.RELEASE_TEAM_ID,repository:env.GITHUB_REPOSITORY,apiUrl:env.GITHUB_API_URL,token:env.GITHUB_TOKEN});
    if(env.GITHUB_OUTPUT)fs.appendFileSync(env.GITHUB_OUTPUT,'publisher='+report.publisher+'\n');console.log(JSON.stringify(report,null,2));return;
  }
  const options={root:env.GITHUB_WORKSPACE,candidateDir:env.RELEASE_CANDIDATE_DIR,sourceSha:env.GITHUB_SHA,publisher:env.RELEASE_TEAM_ID,bindingPin:env.RELEASE_BINDING_SHA256,licenseReport:env.RELEASE_LICENSE_REPORT,pyzToc:env.RELEASE_PYZ_TOC};
  if(mode==='configuration'){
    const checked=configuration(options);
    if(env.GITHUB_OUTPUT)fs.appendFileSync(env.GITHUB_OUTPUT,'backend_dir='+checked.backendDir+'\n');
    console.log(JSON.stringify({status:'configured',source_sha:options.sourceSha,binding_sha256:options.bindingPin,release_ready:false},null,2));return;
  }
  if(mode!=='candidate')throw new Error('Use preflight, configuration or candidate');
  if(!env.RELEASE_CANDIDATE_OUTPUT)throw new Error('Exclusive candidate manifest output path is required');
  const report=candidate(options);
  writeManifest(env.RELEASE_CANDIDATE_OUTPUT,report);console.log(JSON.stringify(report,null,2));
}
module.exports={checkProtectedContext,preflight,configuration,stableRead,fingerprint,candidate,writeManifest};
if(require.main===module)cli().catch(error=>{console.error(String(error.message||error));process.exitCode=1;});
