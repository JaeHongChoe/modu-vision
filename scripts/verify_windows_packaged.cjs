'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const digest=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const normalize=p=>p.replaceAll('\\','/').toLowerCase();
function verifyIdentity(identity,executable){
 assert.equal(identity.packaged,true,'requires actual packaged application');
 assert.equal(normalize(identity.execPath),normalize(executable),'delivered executable differs');
 assert.match(normalize(identity.resourcesPath),/\/resources$/,'packaged resources missing');
}
function verifyPreflight(results){
 for(const stage of ['train','evaluate','infer','export']){
  const row=results[stage];assert.equal(row?.passed,true,stage+' did not pass');
  assert.equal(row.evidence?.architecture,'resnet18');assert.equal(row.evidence?.device,'cpu');
 }
 const exported=results.export.evidence;assert(['OK','NG'].includes(exported.verdict),'export did not inspect image');
 assert(Object.keys(exported.model_steps||{}).length,'export model step missing');
 assert(Object.values(exported.model_steps).every(s=>['passed','flagged_ng'].includes(s)),'export model failed');
}
function verifyObservation(value,exe,sha){
 for(const kind of ['backend','preflight'])assert(value.processes.some(p=>p.kind===kind&&normalize(p.executable)===normalize(exe)&&p.sha256===sha),'actual frozen '+kind+' identity missing');
 assert(value.artifacts.some(p=>p.path.endsWith('/models/job_preflight/best_model.pt')&&/^[a-f0-9]{64}$/.test(p.sha256)),'CPU checkpoint hash missing');
 assert(value.artifacts.some(p=>p.path.endsWith('/data/test/NG/NG_test_0.png')&&/^[a-f0-9]{64}$/.test(p.sha256)),'known-image hash missing');
}
function launchEnv(base,userData){
 const env={};for(const [key,value]of Object.entries(base))if(!/^(VISION_|MODU_|MV_E2E_|CSC_|WIN_CSC_|GH_TOKEN$|GITHUB_TOKEN$|NODE_OPTIONS$|ELECTRON_RUN_AS_NODE$|PYTHONPATH$|PYTHONHOME$|PYTHON_PATH$|VITE_)/i.test(key))env[key]=value;
 return {...env,VISION_AI_STUDIO_USER_DATA_DIR:userData,HF_HUB_OFFLINE:'1',TRANSFORMERS_OFFLINE:'1',PYTHONDONTWRITEBYTECODE:'1',OMP_NUM_THREADS:'2',MKL_NUM_THREADS:'2'};
}
function redact(value){return String(value).replace(/(?:Authorization|X-Vision-Token|api[_-]?token|password)["']?\s*[:=]\s*["']?(?:Bearer\s+)?[^\s,;"']+/gi,'[redacted]');}
function verifyReadback(first,second){assert.deepEqual(second,first,'restart lost or replaced persisted CPU evidence');}
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function waitFor(fn,timeout,label){const until=Date.now()+timeout;let last;while(Date.now()<until){try{const value=await fn();if(value)return value;}catch(error){last=error;}await delay(250);}throw new Error(label+' timed out'+(last?': '+redact(last.message):''));}
async function api(page,route,payload){
 return page.evaluate(async({route,payload})=>{const port=await window.api.getBackendPort();if(!port)throw Error('backend not ready');const response=await fetch(`http://127.0.0.1:${port}${route}`,{signal:AbortSignal.timeout(15000),method:payload?'POST':'GET',...(payload?{headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{})});if(!response.ok)throw Error(`API ${route} status ${response.status}`);return response.json();},{route,payload});
}
function workerResults(reply){const all=reply.workers?.[0]?.preflight||{};return Object.fromEntries(['train','evaluate','infer','export'].map(stage=>[stage,all[`classification:${stage}:cpu`]]));}
async function closeOwned(application){let timer;try{await Promise.race([application.close(),new Promise((_,reject)=>{timer=setTimeout(()=>{application.process().kill();reject(Error('owned application close timed out'));},30000);})]);}finally{clearTimeout(timer);}}
function finalizeEvidence(receipt,out,root,diagnostics,io={remove:fs.rmSync,write:fs.writeFileSync}){
 let cleaned=true;
 try{io.remove(root,{recursive:true,force:true,maxRetries:3,retryDelay:1000});}
 catch(error){cleaned=false;receipt.status='failed';receipt.cleanup='failed';receipt.cleanup_error=/^[A-Z0-9_]{1,32}$/.test(error.code||'')?error.code:'CleanupError';diagnostics.push('Owned temporary state cleanup failed: '+receipt.cleanup_error);}
 receipt.finished_at=new Date().toISOString();
 io.write(path.join(out,'packaged-receipt.json'),JSON.stringify(receipt,null,2));io.write(path.join(out,'harness-diagnostics.log'),diagnostics.join('\n'));
 return cleaned;
}
async function run(options){
 assert.equal(process.platform,'win32','Windows target required; never launch local UI');
 const out=path.resolve(options.output),bundle=path.resolve(options.bundle),exe=path.join(bundle,'Vision AI Studio.exe');fs.mkdirSync(out,{recursive:true});
 const root=fs.mkdtempSync(path.join(options.temp,'Vision 패키지 CPU ')),userData=path.join(root,'사용자 데이터');
 const receipt={schema_version:1,status:'failed',started_at:new Date().toISOString(),source_sha:process.env.GITHUB_SHA||null,run_id:process.env.GITHUB_RUN_ID||null,target:'Windows Server2025 x64 hosted; unsigned packaged executable',qualification:{Windows11_clean_install:'unverified',signing:'unverified',system_installation:'not_run',update_recovery:'unverified',uninstall:'not_run',hardware:'unverified',model_quality:'unverified'},launches:[],cleanup:'pending'};
 const diagnostics=[];let app,observer;
 try{
  const release=JSON.parse(fs.readFileSync(path.join(bundle,'resources/backend_bin/backend-release.json'),'utf8'));
  const backend=path.join(bundle,'resources/backend_bin',release.executable);assert.equal(digest(backend),release.executable_sha256);
  assert.equal(release.acceptance?.status,'passed','native acceptance missing');assert.equal(release.acceptance?.frozen,true);
  receipt.build_identity_sha256=release.inventory.build_identity_sha256;receipt.backend_sha256=digest(backend);receipt.asar_sha256=digest(path.join(bundle,'resources/app.asar'));receipt.app_sha256=digest(exe);
  const { _electron: electron }=require('@playwright/test');let initial;
  for(let iteration=0;iteration<2;iteration++){
   app=await electron.launch({executablePath:exe,args:[],cwd:root,env:launchEnv(process.env,userData),timeout:120000});
   const identity=await app.evaluate(({app})=>({packaged:app.isPackaged,execPath:process.execPath,resourcesPath:process.resourcesPath,pid:process.pid,userData:app.getPath('userData')}));verifyIdentity(identity,exe);assert.equal(normalize(identity.userData),normalize(userData));
   const observed=path.join(out,`processes-${iteration}.json`);
   observer=spawn(options.python,[path.join(__dirname,'watch_windows_packaged.py'),'--pid',String(identity.pid),'--user-data',userData,'--output',observed],{stdio:['pipe','pipe','pipe'],env:launchEnv(process.env,userData)});
   observer.stderr.on('data',chunk=>diagnostics.push(redact(chunk.toString())));observer.on('error',error=>diagnostics.push(redact(error.message)));observer.stdin.on('error',error=>diagnostics.push(error.code||'observer pipe error'));
   const page=await app.firstWindow();page.on('pageerror',error=>diagnostics.push(redact(error.message)));
   await waitFor(async()=>page.url().startsWith('file:')&&await page.evaluate(()=>Boolean(window.api)),120000,'packaged renderer');
   const health=await waitFor(()=>api(page,'/health'),120000,'frozen backend health');
   if(iteration===0){
    await api(page,'/api/workers/local/preflight',{task:'classification',device:'cpu',stages:['train','evaluate','infer','export']});
    const reply=await waitFor(async()=>{const value=await api(page,'/api/workers');return !value.running_preflight&&value.last_preflight?value:null;},900000,'CPU preflight');
    assert.equal(reply.last_preflight.error,null,'CPU preflight failed');initial=workerResults(reply);verifyPreflight(initial);receipt.cpu_results=initial;
   }else{verifyReadback(initial,workerResults(await api(page,'/api/workers')));receipt.restart_readback='identical';}
   await page.screenshot({path:path.join(out,`packaged-${iteration}.png`)});
   receipt.launches.push({identity,health,renderer_url:page.url()});
   await closeOwned(app);app=null;await delay(3000);observer.stdin.end('x');
   const code=observer.exitCode!==null?observer.exitCode:await Promise.race([new Promise(resolve=>observer.once('exit',resolve)),delay(10000).then(()=>{throw Error('observer exit timed out');})]);observer=null;assert.equal(code,0,'process observer failed');
   const observation=JSON.parse(fs.readFileSync(observed,'utf8'));assert.deepEqual(observation.remaining_processes,[],'owned backend processes remain after app close');
   if(iteration===0)verifyObservation(observation,backend,release.executable_sha256);
   else assert(observation.processes.some(p=>p.kind==='backend'&&normalize(p.executable)===normalize(backend)&&p.sha256===release.executable_sha256),'restart frozen backend identity missing');
  }
  receipt.status='passed';
 }catch(error){receipt.error=redact(error.message);throw error;}
 finally{
  let cleanupError=false;if(app)try{await closeOwned(app);}catch(error){cleanupError=true;diagnostics.push(redact(error.message));}
  if(observer){observer.stdin.end('x');if(observer.exitCode===null)await Promise.race([new Promise(resolve=>observer.once('exit',resolve)),delay(5000)]);if(observer.exitCode===null)observer.kill();}
  receipt.finished_at=new Date().toISOString();receipt.cleanup=cleanupError?'owned application forced close; inspect diagnostics':'owned app handles closed; observer stopped';
  // Never upload userData: cleanup is part of qualification, before its final receipt.
  if(!finalizeEvidence(receipt,out,root,diagnostics))throw Error('Owned temporary state cleanup failed');
 }
}
module.exports={verifyIdentity,verifyPreflight,verifyObservation,launchEnv,verifyReadback,redact,workerResults,finalizeEvidence,run};
if(require.main===module){const args=process.argv.slice(2),options={};for(let i=0;i<args.length;i+=2)options[args[i].replace(/^--/,'')]=args[i+1];run(options).catch(error=>{process.stderr.write(redact(error.message)+'\n');process.exitCode=1;});}
