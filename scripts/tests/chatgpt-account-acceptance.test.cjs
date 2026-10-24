'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const helper=path.resolve(__dirname,'../qa/chatgpt-account-acceptance.cjs'),repoRoot=path.resolve(__dirname,'../..');
function qa(){assert.ok(fs.existsSync(helper),'normal-account preparation helper exists');return require(helper);}
function scratch(t){const dir=fs.mkdtempSync(path.join(os.tmpdir(),'mv-account-prepare-test-'));fs.chmodSync(dir,0o700);t.diagnostic('Preserved QA artifacts: '+dir);return dir;}
function ready(){return{active:'chatgpt',encryptionAvailable:true,providers:[{provider:'chatgpt',model:'selected-model',ready:true}],chatgpt:{active:'private-record',status:'connected',encryptionAvailable:true,accounts:[{recordId:'private-record',label:'private@example.com',signedIn:true,planUsageEnabled:true,planUsageAcknowledged:true,usagePaused:false,model:'selected-model',accessToken:'private-access',refreshToken:'private-refresh'}]}};}
async function inPage(t,bridge,action='status'){const old=global.window;global.window={api:{assistant:bridge}};t.after(()=>{global.window=old;});return qa().probeInPage({action,requestId:'req_0123456789abcdef01234567'});}

test('fresh preparation isolates profiles and refuses existing files/directories/symlinks without modification',t=>{
 const q=qa(),root=scratch(t),a=q.prepareRun({outDir:path.join(root,'a'),repoRoot}),b=q.prepareRun({outDir:path.join(root,'b'),repoRoot});
 assert.match(a.appName,/^modu-vision-account-acceptance-[a-f0-9-]{36}$/);assert.notEqual(a.appName,b.appName);
 assert.equal(a.userData,path.join(a.outDir,'userData'));assert.equal(a.sessionData,path.join(a.outDir,'sessionData'));
 const before=fs.readFileSync(a.metadataFile);assert.throws(()=>q.prepareRun({outDir:a.outDir,repoRoot}),/already exists/);assert.deepEqual(fs.readFileSync(a.metadataFile),before);
 const dangling=path.join(root,'dangling');fs.symlinkSync(path.join(root,'missing'),dangling);assert.throws(()=>q.prepareRun({outDir:dangling,repoRoot}),/already exists/);
 assert.throws(()=>q.prepareRun({outDir:'relative',repoRoot}),/absolute/);
 const receipt=JSON.parse(fs.readFileSync(path.join(a.outDir,'receipt.json'),'utf8'));assert.equal(receipt.actualAccountOrPlanInference,false);assert.equal(receipt.appOpened,false);assert.equal(receipt.outcome,'prepared');
});

test('launch and build children retain native session only, with no inherited credentials or startup hooks',t=>{
 const q=qa(),r=q.prepareRun({outDir:path.join(scratch(t),'run'),repoRoot}),native={HOME:'/native',PATH:'/usr/bin',LANG:'ko_KR.UTF-8',SECURITYSESSIONID:'native-session',OPENAI_API_KEY:'PRIVATE',ANTHROPIC_API_KEY:'PRIVATE',NODE_OPTIONS:'--require /foreign',ELECTRON_FLAGS:'--use-mock-keychain',ELECTRON_RUN_AS_NODE:'1',VISION_AI_STUDIO_USER_DATA_DIR:'/foreign',MV_CHATGPT_NATIVE_RUN:'/fake',AWS_SECRET_ACCESS_KEY:'PRIVATE'};
 assert.deepEqual(q.nativeEnvironment(native),{HOME:'/native',PATH:'/usr/bin',LANG:'ko_KR.UTF-8',SECURITYSESSIONID:'native-session'});
 const options=q.launchOptions(r,{electronPath:'/qa/electron',python:'/qa/python',devServerUrl:'http://127.0.0.1:1',baseEnv:native});
 assert.ok(options.args.includes(r.appDir));assert.ok(!options.args.some(x=>/mock|password-store|remote-debugging/.test(x)));
 assert.equal(options.env.HOME,'/native');assert.equal(options.env.VISION_AI_STUDIO_USER_DATA_DIR,r.userData);assert.equal(options.env.MV_CHATGPT_ACCEPTANCE_RUN,r.metadataFile);assert.equal(options.env.NODE_OPTIONS,undefined);assert.equal(options.env.OPENAI_API_KEY,undefined);
});

test('default CLI prepares/builds only; opening needs its own explicit interactive switch',async t=>{
 const q=qa(),root=scratch(t);let builds=0,opens=0;
 const deps={build:async()=>{builds++;},open:async()=>{opens++;},interactive:true};
 await q.runCli(['--out-dir',path.join(root,'default')],deps);assert.equal(builds,1);assert.equal(opens,0);
 await q.runCli(['--out-dir',path.join(root,'open'),'--open'],deps);assert.equal(builds,2);assert.equal(opens,1);
 await assert.rejects(q.runCli(['--out-dir',path.join(root,'not-interactive'),'--open'],{...deps,interactive:false}),/interactive/);assert.equal(builds,2);assert.equal(opens,1);
 await assert.rejects(q.runCli(['--out-dir',path.join(root,'unknown'),'--send'],deps),/Unknown/);assert.equal(builds,2);
});

test('status preflight is local-only and projects no account labels, identities or credential-shaped extras',async t=>{
 let sends=0;const state=ready();state.chatgpt.error={reason:'private-url https://auth.invalid/?token=private',requestId:'private'};
 const value=await inPage(t,{getState:async()=>state,send:async()=>{sends++;throw Error('unexpected send');}});
 assert.equal(value.ready,true);assert.equal(value.modelSelected,true);assert.equal(value.signedIn,true);assert.equal(sends,0);
 assert.ok(!/private|selected-model|https:|accessToken|refreshToken|recordId|label/.test(JSON.stringify(value)));
});

test('local status reports only the backend health boolean from its existing bridge',async t=>{
 const old=global.window;global.window={api:{assistant:{getState:async()=>ready()},getBackendStatus:async()=>({healthy:true,pid:123,port:456,pythonPath:'/PRIVATE',raw:'PRIVATE'})}};t.after(()=>{global.window=old;});
 const value=await qa().probeInPage({action:'status'});assert.equal(value.backendHealthy,true);assert.ok(!/PRIVATE|pid|port|pythonPath/.test(JSON.stringify(value)));
});

for(const [name,mutate] of [
 ['identity only',s=>{s.chatgpt.accounts[0].planUsageEnabled=false;}],['usage paused',s=>{s.chatgpt.accounts[0].usagePaused=true;}],
 ['wrong model',s=>{s.providers[0].model='other';}],['pending login',s=>{s.chatgpt.status='waiting';}],['store error',s=>{s.chatgpt.status='error';}],
 ['signed out',s=>{s.chatgpt.accounts[0].signedIn=false;}],['no protected storage',s=>{s.chatgpt.encryptionAvailable=false;}],
 ['API recipient',s=>{s.active='openai-compatible';}],
])test('one-request preflight refuses '+name+' without a send',async t=>{
 const state=ready();mutate(state);let sends=0;
 const value=await inPage(t,{getState:async()=>state,send:async()=>{sends++;return{ok:true};}},'one-request');
 assert.equal(value.outcome,'not_ready');assert.equal(sends,0);
});

test('single-request controller requires exact consent, sends once with no tools/context/image and never retries',async t=>{
 const q=qa();let reads=0,sends=0;const old=global.window;global.window={api:{assistant:{getState:async()=>{reads++;return ready();},send:async input=>{sends++;assert.deepEqual(input.context,{});assert.deepEqual(input.tools,[]);assert.equal(Object.hasOwn(input,'image'),false);assert.deepEqual(input.messages,[{role:'user',text:'연결 확인입니다. 도구를 사용하지 말고 연결 확인 완료라고만 답하세요.'}]);assert.match(input.requestId,/^req_[a-f0-9]{24}$/);return{ok:true,text:'PRIVATE RESPONSE',toolCalls:[],stop:'end',replay:[{private:'PRIVATE'}],requestId:'PRIVATE'};}}}};t.after(()=>{global.window=old;});
 const controller=q.createAcceptanceController({evaluate:(fn,arg)=>fn(arg)});
 await assert.rejects(controller.check('yes'),/explicit consent/);assert.equal(reads,0);assert.equal(sends,0);
 const result=await controller.check('I CONSENT TO ONE CHATGPT TEXT REQUEST');assert.equal(sends,1);assert.equal(result.outcome,'completed');assert.equal(result.terminalResponseCompleted,true);assert.equal(result.outputTextCharacters,16);assert.ok(!JSON.stringify(result).includes('PRIVATE'));
 await assert.rejects(controller.check('I CONSENT TO ONE CHATGPT TEXT REQUEST'),/already used/);assert.equal(sends,1);
});

test('failed bridge consumes the explicit attempt and exports only a fixed failure code',async()=>{
 const q=qa();let calls=0;const controller=q.createAcceptanceController({evaluate:async()=>{calls++;throw Error('private-token-and-url');}});
 const result=await controller.check('I CONSENT TO ONE CHATGPT TEXT REQUEST');assert.equal(result.outcome,'bridge_error');assert.equal(result.requestAttempted,'unknown');assert.ok(!JSON.stringify(result).includes('private'));
 await assert.rejects(controller.check('I CONSENT TO ONE CHATGPT TEXT REQUEST'),/already used/);assert.equal(calls,1);
});

test('a previously recorded attempt or failed durable attempt record cannot issue another request',async()=>{
 const q=qa();let evaluations=0;const page={evaluate:async()=>{evaluations++;return{outcome:'completed'};}};
 const old=q.createAcceptanceController(page,{alreadyAttempted:true});await assert.rejects(old.check('I CONSENT TO ONE CHATGPT TEXT REQUEST'),/already used/);assert.equal(evaluations,0);
 const unsafe=q.createAcceptanceController(page,{beforeAttempt:async()=>{throw Error('write failed');}});const result=await unsafe.check('I CONSENT TO ONE CHATGPT TEXT REQUEST');assert.equal(result.outcome,'bridge_error');assert.equal(evaluations,0);
});

test('explicit open can reuse an unchanged prepared build without rebuilding or reading account files',async t=>{
 const q=qa(),run=q.prepareRun({outDir:path.join(scratch(t),'run'),repoRoot});let builds=0,opened;
 await q.runCli(['--run-dir',run.outDir,'--open'],{interactive:true,build:async()=>{builds++;},open:async value=>{opened=value;}});
 assert.equal(builds,0);assert.equal(opened.appName,run.appName);assert.equal(opened.userData,run.userData);
 const metadata=JSON.parse(fs.readFileSync(run.metadataFile));metadata.userData='/foreign';fs.writeFileSync(run.metadataFile,JSON.stringify(metadata));
 await assert.rejects(q.runCli(['--run-dir',run.outDir,'--open'],{interactive:true,open:async()=>assert.fail('tampered paths must not open')}));
});

test('build subprocess receives the same native-only environment without a shell or inherited hooks',async t=>{
 const cp=require('node:child_process'),{EventEmitter}=require('node:events'),original=cp.spawn,old=process.env.NODE_OPTIONS;let captured;
 cp.spawn=(command,args,options)=>{captured={command,args,options};const child=new EventEmitter();queueMicrotask(()=>child.emit('close',0));return child;};
 delete require.cache[require.resolve(helper)];const q=qa();cp.spawn=original;t.after(()=>{delete require.cache[require.resolve(helper)];if(old===undefined)delete process.env.NODE_OPTIONS;else process.env.NODE_OPTIONS=old;});
 process.env.NODE_OPTIONS='--require /private-hook';
 const run=q.prepareRun({outDir:path.join(scratch(t),'run'),repoRoot});await q.buildPreparedApp(run);
 assert.equal(captured.command,process.execPath);assert.deepEqual(captured.args,[helper,'--internal-build',run.metadataFile]);assert.equal(captured.options.env.NODE_OPTIONS,undefined);assert.equal(captured.options.shell,undefined);assert.equal(captured.options.env.HOME,process.env.HOME);
});

test('malformed or non-text success cannot claim completed text acceptance',async t=>{
 const result=await inPage(t,{getState:async()=>ready(),send:async()=>({ok:true,text:'',toolCalls:[],stop:'length',replay:'private'})},'one-request');
 assert.equal(result.outcome,'incomplete_result');assert.equal(result.terminalResponseCompleted,false);assert.ok(!JSON.stringify(result).includes('private'));
});

test('provider errors expose only bounded enum/status metadata, never raw error fields',async t=>{
 const result=await inPage(t,{getState:async()=>ready(),send:async()=>({ok:false,error:{code:'rate_limited',status:429,reason:'private',action:'private',requestId:'private',detail:'private',diagnostics:{providerCode:'private',param:'private',bodyShape:'error_object'}}})},'one-request');
 assert.equal(result.outcome,'error');assert.equal(result.errorCode,'rate_limited');assert.equal(result.httpStatus,429);assert.ok(!JSON.stringify(result).includes('private'));
});

test('missing selected-Python dependencies refuse before launching Electron, without fallback or raw diagnostics',async t=>{
 const q=qa(),run=q.prepareRun({outDir:path.join(scratch(t),'run'),repoRoot});assert.equal(typeof q.launchPreparedApp,'function');let launches=0,checks=0;
 await assert.rejects(q.launchPreparedApp(run,{electronPath:'/qa/electron',launch:async()=>{launches++;},spawnSyncImpl:(command,args,options)=>{checks++;assert.equal(command,'python3');assert.ok(args[1].includes('import fastapi, uvicorn, pydantic, torch, torchvision, cv2, PIL, numpy, sklearn'));assert.equal(options.env.NODE_OPTIONS,undefined);return{status:1,stderr:'PRIVATE IMPORT ERROR'};}}),error=>/dependency preflight/.test(error.message)&&!error.message.includes('PRIVATE'));
 assert.equal(launches,0);assert.equal(checks,1);
});

test('successful preflight pins the exact interpreter selected by Python and launches once',async t=>{
 const q=qa(),run=q.prepareRun({outDir:path.join(scratch(t),'run'),repoRoot});assert.equal(typeof q.launchPreparedApp,'function');let launches=0;
 const result=await q.launchPreparedApp(run,{electronPath:'/qa/electron',devServerUrl:'http://127.0.0.1:1',launch:async options=>{launches++;assert.equal(options.env.VISION_AI_PYTHON,process.execPath);return'controlled-app';},spawnSyncImpl:()=>({status:0,stdout:JSON.stringify({executable:process.execPath})+'\n'})});
 assert.equal(result,'controlled-app');assert.equal(launches,1);
});

test('malformed receipt refuses before app launch; persistence failure after launch always closes it',async t=>{
 const q=qa(),run=q.prepareRun({outDir:path.join(scratch(t),'run'),repoRoot});assert.equal(typeof q.openPreparedApp,'function');let launches=0,closes=0;
 const app={close:async()=>{closes++;}},receiptFile=path.join(run.outDir,'receipt.json');
 fs.writeFileSync(receiptFile,'bad json');await assert.rejects(q.openPreparedApp(run,{launch:async()=>{launches++;return app;}}));assert.equal(launches,0);
 fs.writeFileSync(receiptFile,JSON.stringify({schema_version:1,observations:[]}));
 await assert.rejects(q.openPreparedApp(run,{launch:async()=>{launches++;return app;},persist:()=>{throw Error('controlled storage failure');}}));assert.equal(launches,1);assert.equal(closes,1);
});

test('plain native channel accepts only projected replies and sends fixed commands without inspector transport',async()=>{
 const q=qa(),{EventEmitter}=require('node:events'),{PassThrough}=require('node:stream');assert.equal(typeof q.createNativeChannel,'function');
 const child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();const writes=[];child.stdin={write:value=>writes.push(JSON.parse(value))};
 const channel=q.createNativeChannel(child);child.stdout.write('private authentication URL must not be returned\n');child.stdout.write('MV_ACCOUNT_ACCEPTANCE '+JSON.stringify({kind:'ready',normalStorage:true})+'\n');await channel.ready;
 const waiting=channel.request({action:'status'});assert.deepEqual(writes[0],{id:1,action:'status'});
 child.stdout.write('MV_ACCOUNT_ACCEPTANCE '+JSON.stringify({kind:'result',id:1,value:{action:'status',ready:true,label:'private@example.com',raw:'PRIVATE'}})+'\n');assert.deepEqual(await waiting,{action:'status',ready:true});
 const closed=channel.close();assert.deepEqual(writes[1],{action:'quit'});child.emit('close',0);await closed;
});

test('native storage guard checks actual Electron switches, not only process.argv',()=>{
 const q=qa();assert.equal(typeof q.assertNormalStorage,'function');
 for(const flag of ['use-mock-keychain','password-store','remote-debugging-port','remote-debugging-pipe','inspect','inspect-brk'])assert.throws(()=>q.assertNormalStorage({hasSwitch:value=>value===flag}),/normal native storage/);
 assert.doesNotThrow(()=>q.assertNormalStorage({hasSwitch:()=>false}));
});

test('QA preserves the exact build scripts symlink outside runtime and refuses a restored installer before launch',async t=>{
 const q=qa(),run=q.prepareRun({outDir:path.join(scratch(t),'run'),repoRoot}),source=path.join(repoRoot,'scripts/bootstrap_env.py'),before=fs.readFileSync(source);
 assert.equal(typeof q.disableBootstrapFallback,'function');
 const link=path.join(run.appDir,'scripts');fs.symlinkSync(path.join(repoRoot,'scripts'),link);
 q.disableBootstrapFallback(run);assert.equal(fs.existsSync(path.join(run.appDir,'scripts/bootstrap_env.py')),false);
 assert.equal(fs.readlinkSync(path.join(run.outDir,'build-support/scripts-source-link')),path.join(repoRoot,'scripts'));assert.deepEqual(fs.readFileSync(source),before);
 fs.symlinkSync(path.join(repoRoot,'scripts'),link);let launches=0;
 await assert.rejects(q.launchPreparedApp(run,{electronPath:'/qa/electron',devServerUrl:'http://127.0.0.1:1',launch:async()=>{launches++;},spawnSyncImpl:()=>({status:0,stdout:JSON.stringify({executable:process.execPath})})}),/bootstrap fallback/);assert.equal(launches,0);assert.deepEqual(fs.readFileSync(source),before);
});
