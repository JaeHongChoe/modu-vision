'use strict';
// Normal production app, isolated OS profile. There are no provider/browser
// fixtures here. Preparing/building never starts Electron or an account action.
const assert=require('node:assert/strict'),crypto=require('node:crypto'),fs=require('node:fs'),path=require('node:path');
const {spawn,spawnSync}=require('node:child_process');
const REPO=path.resolve(__dirname,'../..');
const CONSENT='I CONSENT TO ONE CHATGPT TEXT REQUEST';
const SOURCES=['src/main/index.ts','src/main/ipc.ts','src/main/assistant/ipc.ts','src/main/assistant/service.ts','src/main/assistant/siwcProtocol.ts','src/main/assistant/siwcConnection.ts','src/main/assistant/siwcStore.ts','src/preload/index.ts','src/renderer/components/assistant/AssistantPanel.tsx','src/renderer/components/assistant/ChatgptConnectionSetup.tsx','src/renderer/stores/useAssistantStore.ts','src/renderer/services/assistantSession.ts','scripts/qa/chatgpt-account-acceptance.cjs'];
const hash=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const write=(file,value)=>fs.writeFileSync(file,JSON.stringify(value,null,2)+'\n',{mode:0o600});
function exists(file){try{fs.lstatSync(file);return true;}catch(error){if(error.code==='ENOENT')return false;throw error;}}

function nativeEnvironment(base=process.env){
 const result={};
 for(const [key,value] of Object.entries(base))if(/^(HOME|USERPROFILE|USER|LOGNAME|PATH|TMPDIR|TEMP|TMP|LANG|LC_.*|SHELL|SYSTEMROOT|WINDIR|COMSPEC|DISPLAY|WAYLAND_DISPLAY|DBUS_SESSION_BUS_ADDRESS|XDG_RUNTIME_DIR|XAUTHORITY|__CF_USER_TEXT_ENCODING|SECURITYSESSIONID)$/i.test(key))result[key]=value;
 return result;
}
function prepareRun({outDir,repoRoot=REPO,python}){
 assert.ok(typeof outDir==='string'&&path.isAbsolute(outDir),'absolute fresh output directory required');
 assert.ok(!exists(outDir),'output already exists; preserve the existing profile');
 if(python)assert.ok(path.isAbsolute(python)&&fs.statSync(python).isFile(),'explicit Python must be an absolute executable file');
 const run={schema_version:1,outDir,repoRoot:fs.realpathSync(repoRoot),appName:'modu-vision-account-acceptance-'+crypto.randomUUID(),python:python||(process.platform==='win32'?'python':'python3')};
 for(const name of ['appDir','userData','sessionData','logs','crashDumps'])run[name]=path.join(outDir,name==='appDir'?'app':name);
 run.metadataFile=path.join(outDir,'run.json');run.sourceHashes=Object.fromEntries(SOURCES.map(file=>[file,hash(path.join(run.repoRoot,file))]));
 fs.mkdirSync(outDir,{recursive:true,mode:0o700});for(const name of ['appDir','userData','sessionData','logs','crashDumps'])fs.mkdirSync(run[name],{mode:0o700});
 write(run.metadataFile,run);
 write(path.join(outDir,'receipt.json'),{schema_version:1,mode:'normal-native-real-account-preparation',outcome:'prepared',appOpened:false,actualAccountOrPlanInference:false,mockKeychain:false,generatedCredentials:false,sourceHashes:run.sourceHashes,observations:[]});
 return run;
}
function readRun(file){
 assert.ok(path.isAbsolute(file)&&path.basename(file)==='run.json','invalid run metadata location');
 const stat=fs.lstatSync(file);assert.ok(stat.isFile()&&!stat.isSymbolicLink()&&stat.size<64*1024,'invalid run metadata file');
 const run=JSON.parse(fs.readFileSync(file,'utf8')),root=path.dirname(file);
 assert.equal(run.schema_version,1);assert.equal(run.outDir,root);assert.equal(run.metadataFile,file);assert.equal(run.repoRoot,fs.realpathSync(REPO));
 assert.match(run.appName,/^modu-vision-account-acceptance-[a-f0-9-]{36}$/);
 for(const name of ['appDir','userData','sessionData','logs','crashDumps']){assert.equal(run[name],path.join(root,name==='appDir'?'app':name));const s=fs.lstatSync(run[name]);assert.ok(s.isDirectory()&&!s.isSymbolicLink(),'run directory must not be a symlink');}
 for(const source of SOURCES)assert.equal(run.sourceHashes[source],hash(path.join(REPO,source)),'prepared source changed; prepare a fresh run');
 assert.ok(typeof run.python==='string'&&(path.isAbsolute(run.python)||['python','python3'].includes(run.python)),'invalid Python command');
 return run;
}
async function internalBuild(file){
 const run=readRun(file),harness=require('../e2e/fixtures/harness.cjs');
 await harness.buildRenderer({repoRoot:run.repoRoot,outDir:path.join(run.appDir,'dist'),logFile:path.join(run.outDir,'renderer-build.log')});
 await harness.buildElectronApp({repoRoot:run.repoRoot,appDir:run.appDir,logFile:path.join(run.outDir,'electron-build.log')});
 disableBootstrapFallback(run);
 // Only native path initialization precedes the untouched production main.
 write(path.join(run.appDir,'package.json'),{name:run.appName,productName:run.appName,version:'0.0.0',main:'acceptance-bootstrap.cjs'});
 fs.writeFileSync(path.join(run.appDir,'acceptance-bootstrap.cjs'),`require(${JSON.stringify(__filename)}).startProductionApp();\n`,{mode:0o600});
 readRun(file);
}
function assertNoBootstrapFallback(run){assert.equal(exists(path.join(run.appDir,'scripts/bootstrap_env.py')),false,'QA runtime bootstrap fallback must be unavailable');}
function disableBootstrapFallback(run){
 const link=path.join(run.appDir,'scripts');assert.ok(fs.lstatSync(link).isSymbolicLink(),'expected harness scripts symlink');
 assert.equal(path.resolve(path.dirname(link),fs.readlinkSync(link)),path.join(run.repoRoot,'scripts'),'unexpected scripts link target');
 const support=path.join(run.outDir,'build-support'),saved=path.join(support,'scripts-source-link');assert.equal(exists(saved),false,'preserve existing build support');
 fs.mkdirSync(support,{mode:0o700});fs.renameSync(link,saved);assertNoBootstrapFallback(run);
}
function buildPreparedApp(run){
 return new Promise((resolve,reject)=>{
  const child=spawn(process.execPath,[__filename,'--internal-build',run.metadataFile],{cwd:run.repoRoot,env:nativeEnvironment(),stdio:'ignore'});
  child.once('error',()=>reject(Error('isolated build could not start')));child.once('close',code=>code===0?resolve():reject(Error('isolated build failed; inspect local build logs')));
 });
}
function startProductionApp(){
 const {app,BrowserWindow}=require('electron'),run=readRun(process.env.MV_CHATGPT_ACCEPTANCE_RUN);
 assertNoBootstrapFallback(run);
 assert.equal(app.getAppPath(),run.appDir);assert.equal(app.getName(),run.appName);
 assertNormalStorage(app.commandLine);
 assert.ok(!process.argv.some(arg=>/^--(?:use-mock-keychain|password-store|remote-debugging(?:-port|-pipe)?|inspect(?:-brk)?)(?:=|$)/.test(arg)),'normal OS protected storage required');
 for(const key of ['userData','sessionData','logs','crashDumps'])app.setPath(key,run[key]);
 require(path.join(run.appDir,'dist-electron/main/index.js'));
 const emit=value=>process.stdout.write('\nMV_ACCOUNT_ACCEPTANCE '+JSON.stringify(value)+'\n');
 app.whenReady().then(async()=>{
  const expected=require('node:url').pathToFileURL(path.join(run.appDir,'dist/index.html')).href;
  let win;
  const deadline=Date.now()+60000;
  while(Date.now()<deadline){win=BrowserWindow.getAllWindows().find(item=>!item.isDestroyed()&&item.webContents.getURL().split(/[?#]/,1)[0]===expected&&!item.webContents.isLoadingMainFrame());if(win)break;await new Promise(resolve=>setTimeout(resolve,50));}
  if(!win){app.quit();return;}
  assertNormalStorage(app.commandLine);emit({kind:'ready',normalStorage:true});
  const rl=require('node:readline').createInterface({input:process.stdin});let queue=Promise.resolve();
  rl.on('close',()=>app.quit());
  rl.on('line',line=>{queue=queue.then(async()=>{
   let command;try{command=JSON.parse(line);}catch{return;}
   if(command.action==='quit'){app.quit();return;}
   if(!Number.isInteger(command.id)||command.id<1||!['status','one-request'].includes(command.action))return;
   try{
    assertNormalStorage(app.commandLine);
    if(win.isDestroyed()||win.webContents.getURL().split(/[?#]/,1)[0]!==expected)throw Error('untrusted window');
    const args={action:command.action,...(command.action==='one-request'?{requestId:command.requestId}:{})};
    const value=await win.webContents.executeJavaScript('('+probeInPage.toString()+')('+JSON.stringify(args)+')');
    emit({kind:'result',id:command.id,value});
   }catch{emit({kind:'error',id:command.id});}
  }).catch(()=>{app.quit();});});
 }).catch(()=>{app.quit();});
}
function assertNormalStorage(commandLine){
 for(const flag of ['use-mock-keychain','password-store','remote-debugging-port','remote-debugging-pipe','inspect','inspect-brk'])assert.equal(commandLine.hasSwitch(flag),false,'normal native storage and a non-debugging app are required');
}
function launchOptions(run,{electronPath,python=run.python,devServerUrl,baseEnv=process.env}){
 const harness=require('../e2e/fixtures/harness.cjs');
 const options=harness.electronLaunchOptions({appDir:run.appDir,workspace:{userData:run.userData,home:path.join(run.outDir,'home')},python,devServerUrl,baseEnv:nativeEnvironment(baseEnv),electronPath});
 options.args=['--disable-background-networking','--disable-component-update',run.appDir];options.env.MV_CHATGPT_ACCEPTANCE_RUN=run.metadataFile;
 return options;
}
async function launchPreparedApp(run,{launch,electronPath,devServerUrl,spawnSyncImpl=spawnSync}){
 assertNoBootstrapFallback(run);
 // Match supervisor.ensureDependencies before opening the app: its fallback
 // bootstrapper may install packages if these imports fail. Resolve sys.executable
 // so the supervisor cannot silently choose another interpreter from PATH.
 const code='import fastapi, uvicorn, pydantic, torch, torchvision, cv2, PIL, numpy, sklearn\nimport sys, json\nprint(json.dumps({"executable": sys.executable}))';
 let checked,python;
 try{
  checked=spawnSyncImpl(run.python,['-c',code],{cwd:run.appDir,env:nativeEnvironment(),encoding:'utf8',timeout:6000,windowsHide:true,maxBuffer:1024*1024});
  assert.equal(checked.status,0);python=JSON.parse(checked.stdout.trim().split('\n').pop()).executable;
  assert.ok(typeof python==='string'&&path.isAbsolute(python)&&fs.statSync(python).isFile());
 }catch{throw Error('selected Python dependency preflight failed; choose a complete local interpreter explicitly');}
 const harness=require('../e2e/fixtures/harness.cjs');
 return launch({...launchOptions(run,{electronPath,python,devServerUrl:devServerUrl||await harness.closedLoopbackUrl()}),timeout:60000});
}
function readReceipt(run){
 const raw=JSON.parse(fs.readFileSync(path.join(run.outDir,'receipt.json'),'utf8'));
 assert.ok(raw?.schema_version===1&&Array.isArray(raw.observations)&&raw.observations.length<=100,'invalid acceptance receipt');
 const observations=raw.observations.map(projectObservation);
 return{schema_version:1,mode:'normal-native-real-account-preparation',outcome:'opened_awaiting_human',appOpened:false,actualAccountOrPlanInference:null,mockKeychain:false,generatedCredentials:false,sourceHashes:run.sourceHashes,observations,oneRequestAttemptConsumed:raw.oneRequestAttemptConsumed===true};
}
function projectObservation(item){
  assert.ok(item&&['status','one-request'].includes(item.action),'invalid receipt observation');
  const clean={action:item.action};
  for(const key of ['providerSelected','encryptionAvailable','signedIn','planUsageEnabled','modelSelected','usagePaused','ready','backendHealthy','terminalResponseCompleted','providerRequestIdAvailable'])if(typeof item[key]==='boolean')clean[key]=item[key];
  if(['idle','waiting','exchanging','connected','error','unavailable'].includes(item.status))clean.status=item.status;
  if(['unavailable','not_ready','completed','incomplete_result','error','bridge_error'].includes(item.outcome))clean.outcome=item.outcome;
  if(typeof item.requestAttempted==='boolean'||item.requestAttempted==='unknown')clean.requestAttempted=item.requestAttempted;
  if(typeof item.at==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(item.at))clean.at=item.at;
  for(const [key,min,max] of [['httpStatus',100,599],['outputTextCharacters',0,2*1024*1024],['toolCalls',-1,1000]])if(Number.isInteger(item[key])&&item[key]>=min&&item[key]<=max)clean[key]=item[key];
  if(['no_provider','no_key','storage_unavailable','bad_key','forbidden','billing','not_found','bad_request','rate_limited','overloaded','server','network','timeout','cancelled','bad_response','invalid_input','image_refused','unknown'].includes(item.errorCode))clean.errorCode=item.errorCode;
  if(item.proof==='production_siwc_completed_response_gate')clean.proof=item.proof;
  return clean;
}
function createNativeChannel(child){
 let sequence=0,buffer='',ended=false,readyResolve,readyReject,closedResolve;
 const pending=new Map(),ready=new Promise((resolve,reject)=>{readyResolve=resolve;readyReject=reject;}),closed=new Promise(resolve=>{closedResolve=resolve;});
 ready.catch(()=>{});
 const bootTimer=setTimeout(()=>readyReject(Error('native app readiness timed out')),60000);
 child.once('error',()=>{clearTimeout(bootTimer);readyReject(Error('native app failed to start'));});
 child.once('close',()=>{ended=true;clearTimeout(bootTimer);readyReject(Error('native app closed before readiness'));for(const item of pending.values()){clearTimeout(item.timer);item.reject(Error('native app closed'));}pending.clear();closedResolve();});
 child.stderr?.resume();
 child.stdout.on('data',bytes=>{
  buffer+=String(bytes);if(buffer.length>65536){buffer='';return;}
  let end;
  while((end=buffer.indexOf('\n'))!==-1){
   const line=buffer.slice(0,end);buffer=buffer.slice(end+1);if(!line.startsWith('MV_ACCOUNT_ACCEPTANCE '))continue;
   let value;try{value=JSON.parse(line.slice('MV_ACCOUNT_ACCEPTANCE '.length));}catch{continue;}
   if(value.kind==='ready'&&value.normalStorage===true){clearTimeout(bootTimer);readyResolve({normalStorage:true});continue;}
   const item=pending.get(value.id);if(!item)continue;
   pending.delete(value.id);clearTimeout(item.timer);
   try{assert.equal(value.kind,'result');assert.equal(value.value?.action,item.action);item.resolve(projectObservation(value.value));}catch{item.reject(Error('native observation failed'));}
  }
 });
 return{ready,closed,
  request(args){
   assert.ok(['status','one-request'].includes(args.action),'fixed acceptance command required');assert.equal(ended,false,'native app already closed');
   const id=++sequence;
   return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{pending.delete(id);reject(Error('native command timed out'));},30000);pending.set(id,{resolve,reject,timer,action:args.action});child.stdin.write(JSON.stringify({id,action:args.action,...(args.action==='one-request'?{requestId:args.requestId}:{})})+'\n');});
  },
  async close(){
   if(ended)return;
   child.stdin.write(JSON.stringify({action:'quit'})+'\n');
   let timer;try{await Promise.race([closed,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('native app shutdown timed out; inspect the owned process')),30000);})]);}finally{clearTimeout(timer);}
  },
 };
}
function spawnNormalApp(options){
 return createNativeChannel(spawn(options.executablePath,options.args,{cwd:options.cwd,env:options.env,stdio:['pipe','pipe','pipe']}));
}

// This function is serialized into the real renderer by Playwright. Project
// private state and provider responses before anything crosses back to Node.
async function probeInPage({action,requestId}){
 const bridge=window.api?.assistant;
 if(!bridge)return{action,outcome:'unavailable',ready:false,requestAttempted:false};
 const state=await bridge.getState(),chat=state?.chatgpt,account=chat?.accounts?.find(item=>item.recordId===chat.active),provider=state?.providers?.find(item=>item.provider==='chatgpt');
 const status=['idle','waiting','exchanging','connected','error'].includes(chat?.status)?chat.status:'unavailable';
 const preflight={providerSelected:state?.active==='chatgpt',encryptionAvailable:chat?.encryptionAvailable===true,signedIn:account?.signedIn===true,planUsageEnabled:account?.planUsageEnabled===true,modelSelected:Boolean(account?.model&&provider?.model===account.model),usagePaused:account?.usagePaused===true,status};
 preflight.ready=preflight.providerSelected&&preflight.encryptionAvailable&&preflight.signedIn&&preflight.planUsageEnabled&&preflight.modelSelected&&!preflight.usagePaused&&provider?.ready===true&&!['waiting','exchanging','error','unavailable'].includes(status);
 if(action==='status'){
  let backendHealthy;try{const backend=await window.api.getBackendStatus?.();if(backend)backendHealthy=backend.healthy===true;}catch{}
  return{action,...preflight,...(typeof backendHealthy==='boolean'?{backendHealthy}:{})};
 }
 if(action!=='one-request'||!/^req_[a-f0-9]{24}$/.test(requestId))throw Error('invalid acceptance action');
 if(!preflight.ready)return{action,outcome:'not_ready',requestAttempted:false,...preflight};
 const result=await bridge.send({requestId,context:{},messages:[{role:'user',text:'연결 확인입니다. 도구를 사용하지 말고 연결 확인 완료라고만 답하세요.'}],tools:[]});
 if(result?.ok===true){
  const length=typeof result.text==='string'?result.text.length:0,calls=Array.isArray(result.toolCalls)?result.toolCalls.length:-1;
  const completed=length>0&&length<=2*1024*1024&&calls===0&&result.stop==='end';
  return{action,outcome:completed?'completed':'incomplete_result',requestAttempted:true,terminalResponseCompleted:completed,proof:'production_siwc_completed_response_gate',outputTextCharacters:Math.min(length,2*1024*1024),toolCalls:Math.max(-1,Math.min(calls,1000)),providerRequestIdAvailable:typeof result.requestId==='string'};
 }
 const codes=['no_provider','no_key','storage_unavailable','bad_key','forbidden','billing','not_found','bad_request','rate_limited','overloaded','server','network','timeout','cancelled','bad_response','invalid_input','image_refused'];
 const error=result?.error;
 return{action,outcome:'error',requestAttempted:true,terminalResponseCompleted:false,errorCode:codes.includes(error?.code)?error.code:'unknown',...(Number.isInteger(error?.status)&&error.status>=100&&error.status<=599?{httpStatus:error.status}:{})};
}
function createAcceptanceController(page,{alreadyAttempted=false,beforeAttempt=async()=>{}}={}){
 let attempted=alreadyAttempted;
 return{
  status:()=>page.evaluate(probeInPage,{action:'status'}),
  async check(consent){
   assert.equal(consent,CONSENT,'explicit consent phrase required');assert.equal(attempted,false,'single request attempt already used');attempted=true;
   try{await beforeAttempt();return await page.evaluate(probeInPage,{action:'one-request',requestId:'req_'+crypto.randomBytes(12).toString('hex')});}
   catch{return{action:'one-request',outcome:'bridge_error',requestAttempted:'unknown',terminalResponseCompleted:false};}
  },
 };
}
async function openPreparedApp(run,{launch=async value=>launchPreparedApp(value,{launch:spawnNormalApp,electronPath:require('electron')}),persist=write}={}){
 run=readRun(run.metadataFile);
 assertNoBootstrapFallback(run);
 const receipt=readReceipt(run),readline=require('node:readline');
 let rl,app;
 try{
  app=await launch(run);receipt.appOpened=true;persist(path.join(run.outDir,'receipt.json'),receipt);
  const native=await app.ready;assert.equal(native.normalStorage,true);receipt.normalStorageSwitchesAbsent=true;persist(path.join(run.outDir,'receipt.json'),receipt);
  const page={evaluate:(_fn,args)=>app.request(args)};
  const controller=createAcceptanceController(page,{alreadyAttempted:receipt.oneRequestAttemptConsumed===true,beforeAttempt:async()=>{receipt.oneRequestAttemptConsumed=true;persist(path.join(run.outDir,'receipt.json'),receipt);}});
  console.log('Use the app controls to read terms, log in, enable plan usage and choose a model. No account action is automated.');
  console.log('Commands: status (local metadata only), quit. UI questions may make up to six provider calls.');
  console.log('Only after explicit permission to consume one request, type exactly: '+CONSENT);
  console.log('The one-request check sends one fixed benign text with no tools, project context or image. It never retries.');
  rl=readline.createInterface({input:process.stdin,output:process.stdout});app.closed?.then(()=>rl.close());
  for await(const line of rl){
   if(line==='quit')break;
   if(line!=='status'&&line!==CONSENT){console.log('Unrecognized command; no action taken.');continue;}
   let observation;
   try{observation=line==='status'?await controller.status():await controller.check(line);}catch{console.log('Action refused or local state unavailable; no automatic retry.');continue;}
   receipt.observations.push({at:new Date().toISOString(),...observation});receipt.observations=receipt.observations.slice(-100);
   if(observation.action==='one-request'){receipt.outcome=observation.outcome;receipt.actualAccountOrPlanInference=observation.outcome==='completed'?true:null;}
   persist(path.join(run.outDir,'receipt.json'),receipt);console.log(JSON.stringify(observation));
  }
 }finally{rl?.close();if(app){await app.close();receipt.appClosed=true;persist(path.join(run.outDir,'receipt.json'),receipt);}}
}
async function runCli(args,{build=buildPreparedApp,open=openPreparedApp,interactive=Boolean(process.stdin.isTTY)}={}){
 let outDir,runDir,openApp=false,python;
 for(let i=0;i<args.length;i++){
  if(args[i]==='--out-dir'){assert.ok(args[i+1]&&!args[i+1].startsWith('--'),'output directory required');outDir=args[++i];}
  else if(args[i]==='--run-dir'){assert.ok(args[i+1]&&!args[i+1].startsWith('--'),'prepared directory required');runDir=args[++i];}
  else if(args[i]==='--python'){assert.ok(args[i+1]&&!args[i+1].startsWith('--'),'Python path required');python=args[++i];}
  else if(args[i]==='--open')openApp=true;
  else throw Error('Unknown argument; use --out-dir NEW_ABSOLUTE_DIRECTORY [--python ABSOLUTE_PYTHON] [--open], or --run-dir PREPARED_DIRECTORY --open');
 }
 assert.ok(!openApp||interactive,'--open requires an interactive terminal for explicit consent controls');
 assert.ok(!runDir||!outDir&&!python&&openApp,'a prepared run requires --open and cannot change its paths or interpreter');
 const run=runDir?readRun(path.join(runDir,'run.json')):prepareRun({outDir,python});if(!runDir)await build(run);if(openApp)await open(run);return run;
}
module.exports={prepareRun,nativeEnvironment,launchOptions,launchPreparedApp,openPreparedApp,probeInPage,createAcceptanceController,runCli,startProductionApp,buildPreparedApp,createNativeChannel,assertNormalStorage,disableBootstrapFallback};
if(require.main===module){
 const work=process.argv[2]==='--internal-build'?internalBuild(process.argv[3]):runCli(process.argv.slice(2)).then(run=>console.log(JSON.stringify({outcome:'prepared',appOpened:process.argv.includes('--open'),outDir:run.outDir})));
 work.catch(()=>{console.error('Account acceptance preparation failed. Inspect the isolated build logs or command arguments locally.');process.exitCode=1;});
}
