'use strict';
// Isolated native composition QA. Production main/index, IPC, protected store,
// preload, renderer and backend are unmodified. Only auth/HTTPS provider transport
// and browser opening are replaced here, before production main is loaded.
// Generated credentials only; never reads another app's authentication files.
const assert=require('node:assert/strict'),crypto=require('node:crypto'),fs=require('node:fs'),path=require('node:path');
const REPO=path.resolve(__dirname,'../..'),harness=require('../e2e/fixtures/harness.cjs');
const write=(file,value)=>fs.writeFileSync(file,JSON.stringify(value,null,2)+'\n',{mode:0o600});
const sha=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');

function installProviderFixture(electron){
 const originalFetch=globalThis.fetch,AUTH='https://auth.openai.com',API='https://api.openai.com/v1',CLIENT='oaiapp_native_generated_qa';
 const keys=crypto.generateKeyPairSync('rsa',{modulusLength:2048}),jwk={...keys.publicKey.export({format:'jwk'}),kid:'generated-native-qa',alg:'RS256',use:'sig'};
 let authorization,plan=false,sequence=[],number=0,limited=false;
 const counts={authOpens:0,usageOpens:0,promptConsent:0,models:0,responses:0,token:0,externalNetwork:0,secretLogAttempts:0};
 const secrets=['native-qa-generated-access','native-qa-generated-refresh','native-qa-generated-subject'];
 const sensitive=new Set(secrets);
 const jwt=nonce=>{const now=Math.floor(Date.now()/1000),head=Buffer.from(JSON.stringify({alg:'RS256',kid:jwk.kid})).toString('base64url'),body=Buffer.from(JSON.stringify({iss:AUTH,aud:CLIENT,sub:secrets[2],exp:now+3600,iat:now,nonce})).toString('base64url'),raw=head+'.'+body,value=raw+'.'+crypto.sign('sha256',Buffer.from(raw),keys.privateKey).toString('base64url');sensitive.add(value);return value;};
 // Detect generated credential leaks before a child log leaves main. Record any
 // attempt as a failing result while suppressing the sensitive value itself.
 for(const stream of [process.stdout,process.stderr]){const originalWrite=stream.write.bind(stream);stream.write=(chunk,...args)=>{let value=Buffer.isBuffer(chunk)?chunk.toString():String(chunk);for(const secret of sensitive)if(value.includes(secret)){counts.secretLogAttempts++;value=value.split(secret).join('[generated credential leak]');}return originalWrite(value,...args);};}
 electron.shell.openExternal=async raw=>{const url=new URL(raw);if(url.origin===AUTH&&url.pathname==='/api/accounts/authorize'){authorization=url;counts.authOpens++;if(url.searchParams.get('prompt')==='consent')counts.promptConsent++;return;}
   if(url.origin==='https://chatgpt.com'){counts.usageOpens++;return;}if(url.origin==='https://openai.com')return;throw Error('Unexpected external browser destination');};
 globalThis.fetch=async(raw,init={})=>{const url=new URL(String(raw));if(url.protocol==='http:'&&url.hostname==='127.0.0.1')return originalFetch(raw,init);
   if(url.href===AUTH+'/.well-known/openid-configuration')return Response.json({issuer:AUTH,authorization_endpoint:AUTH+'/api/accounts/authorize',token_endpoint:AUTH+'/api/accounts/oauth/token',jwks_uri:AUTH+'/jwks',revocation_endpoint:AUTH+'/oauth/revoke'});
   if(url.href===AUTH+'/jwks')return Response.json({keys:[jwk]});
   if(url.href===AUTH+'/api/accounts/oauth/token'){counts.token++;const form=new URLSearchParams(init.body);assert.equal(init.method,'POST');assert.equal(init.redirect,'error');assert.equal(form.get('grant_type'),'authorization_code');assert.equal(form.get('client_id'),CLIENT);assert.equal(form.get('code'),'generated-native-code');assert.equal(form.get('redirect_uri'),authorization.searchParams.get('redirect_uri'));assert.equal(form.get('resource'),API);assert.equal(crypto.createHash('sha256').update(form.get('code_verifier')).digest('base64url'),authorization.searchParams.get('code_challenge'));return Response.json({access_token:secrets[0],refresh_token:secrets[1],id_token:jwt(authorization?.searchParams.get('nonce')),token_type:'Bearer',expires_in:3600,scope:'openid profile email offline_access resource.invoke'+(plan?' chatgpt.tokens.use.direct':'')});}
   if(url.href===AUTH+'/oauth/revoke')return new Response('',{status:200});
   if(url.href===API+'/models'){counts.models++;assert.equal(init.headers.Authorization,'Bearer '+secrets[0]);return Response.json({models:[{slug:'native-qa-model',display_name:'Native QA model',visibility:'list'}]});}
   if(url.href===API+'/responses'){
     counts.responses++;assert.equal(init.headers.Authorization,'Bearer '+secrets[0]);assert.equal(init.redirect,'error');const body=JSON.parse(init.body);assert.equal(body.stream,true);assert.equal(body.store,false);assert.equal(body.model,'native-qa-model');assert.ok(!JSON.stringify(body).includes('input_image'));
     if(limited)return Response.json({error:{code:'subscription_sharing_usage_limit_exceeded',message:'Generated QA limit'}},{status:429,headers:{'x-request-id':'req_native_qa_limit'}});
     const next=sequence.shift()||{text:'격리 도우미 작업을 마쳤습니다.'};
     const output=next.tools?next.tools.map(tool=>({type:'function_call',namespace:'modu_vision',call_id:'call_native_'+(++number),name:tool.name,arguments:JSON.stringify(tool.arguments||{})})):[{type:'message',role:'assistant',content:[{type:'output_text',text:next.text}]}];
     return new Response('event: response.completed\ndata: '+JSON.stringify({type:'response.completed',response:{status:'completed',output}})+'\n\n',{headers:{'content-type':'text/event-stream','x-request-id':'req_native_qa'}});
   }
   counts.externalNetwork++;throw Error('Native QA refuses all non-loopback provider network');
 };
 // Deny direct HTTPS too; fake transport does not delegate these requests.
 for(const method of ['request','get'])require('node:https')[method]=()=>{counts.externalNetwork++;throw Error('HTTPS disabled in native QA');};
 return {async completeLogin(enabled){assert.ok(authorization);plan=enabled;const redirect=new URL(authorization.searchParams.get('redirect_uri'));assert.equal(redirect.hostname,'127.0.0.1');redirect.search=new URLSearchParams({state:authorization.searchParams.get('state'),code:'generated-native-code',client_id:CLIENT}).toString();return(await originalFetch(redirect)).status;},queue(value){sequence=structuredClone(value);},limit(value){limited=value;},snapshot(){return{...counts,sequenceRemaining:sequence.length};},hasSecret(value){return[...sensitive].some(secret=>value.includes(secret));}};
}

function runElectron(){
 const electron=require('electron'),run=JSON.parse(fs.readFileSync(process.env.MV_CHATGPT_NATIVE_RUN,'utf8'));
 assert.equal(electron.app.getAppPath(),run.appDir);assert.equal(electron.app.getName(),run.appName);
 for(const key of ['userData','sessionData','logs','crashDumps'])electron.app.setPath(key,run[key]);
 globalThis.__nativeChatgptQA=installProviderFixture(electron);
 electron.app.once('quit',()=>write(path.join(run.out,'process-'+process.pid+'.json'),globalThis.__nativeChatgptQA.snapshot()));
 require(path.join(run.appDir,'dist-electron/main/index.js'));
}

async function main(out){
 assert.ok(out&&path.isAbsolute(out),'absolute fresh output folder required');assert.ok(!fs.existsSync(out),'output must be new; existing QA profiles are preserved');fs.mkdirSync(out,{recursive:true,mode:0o700});
 const appName='modu-vision-chatgpt-native-'+crypto.randomUUID(),appDir=path.join(out,'app'),workspace=harness.createWorkspace(path.join(out,'workspace'));
 const sourceFiles=['src/main/index.ts','src/main/ipc.ts','src/main/assistant/ipc.ts','src/main/assistant/service.ts','src/main/assistant/siwcProtocol.ts','src/main/assistant/siwcConnection.ts','src/main/assistant/siwcStore.ts','src/preload/index.ts','src/renderer/components/assistant/AssistantPanel.tsx','src/renderer/components/assistant/ChatgptConnectionSetup.tsx','src/renderer/stores/useAssistantStore.ts','scripts/qa/chatgpt-native.cjs'];
 const sourceHashes=Object.fromEntries(sourceFiles.map(file=>[file,sha(path.join(REPO,file))]));
 const run={appName,appDir,out,userData:workspace.userData,sessionData:path.join(out,'sessionData'),logs:workspace.logs,crashDumps:path.join(out,'crashDumps')};
 for(const key of ['sessionData','crashDumps'])fs.mkdirSync(run[key],{mode:0o700});
 await harness.buildRenderer({outDir:path.join(appDir,'dist'),logFile:path.join(out,'renderer-build.log')});
 await harness.buildElectronApp({appDir,logFile:path.join(out,'electron-build.log')});
 write(path.join(appDir,'package.json'),{name:appName,productName:appName,version:'0.0.0',main:'native-bootstrap.cjs'});
 fs.writeFileSync(path.join(appDir,'native-bootstrap.cjs'),`require(${JSON.stringify(__filename)}).runElectron();\n`,{mode:0o600});write(path.join(out,'run.json'),run);
 const { _electron,expect}=require('@playwright/test'),options=harness.electronLaunchOptions({appDir,workspace,electronPath:require('electron'),devServerUrl:await harness.closedLoopbackUrl()});
 options.args=['--use-mock-keychain','--disable-background-networking','--disable-component-update',appDir];options.env.MV_CHATGPT_NATIVE_RUN=path.join(out,'run.json');
 const source=path.join(workspace.root,'twenty-images');for(const original of workspace.images){const dir=path.join(source,original.label.toUpperCase());fs.mkdirSync(dir,{recursive:true});for(let i=0;i<10;i++)fs.copyFileSync(original.path,path.join(dir,i+'.png'));}
 const originalHashes=Object.fromEntries(fs.readdirSync(source).flatMap(label=>fs.readdirSync(path.join(source,label)).map(file=>{const full=path.join(source,label,file);return[full,sha(full)];})));
 const results={mode:'native-production-main-preload-renderer-backend',mockKeychain:true,generatedCredentials:true,actualAccountOrPlanInference:false,sourceImages:20,sourceHashes};
 const stage=value=>{results.stage=value;write(path.join(out,'progress.json'),{stage:value,time:new Date().toISOString()});};
 let app,page;const pageErrors=[],backendStops=[],logs=[];
 const launch=async()=>{app=await _electron.launch({...options,timeout:60000});app.process().stdout?.on('data',bytes=>logs.push(String(bytes)));app.process().stderr?.on('data',bytes=>logs.push(String(bytes)));page=await app.firstWindow();page.on('pageerror',e=>pageErrors.push(e.message));await page.waitForURL(u=>u.protocol==='file:',{timeout:60000});await expect.poll(()=>page.evaluate(()=>window.api.getBackendStatus().then(s=>s.healthy)),{timeout:180000}).toBe(true);};
 const close=async()=>{if(!app)return;const status=await page.evaluate(()=>window.api.getBackendStatus()).catch(()=>null);await app.close();app=null;if(status?.port){const closed=await harness.waitForPortClosed(status.port,10000),alive=status.pid?harness.processAlive(status.pid):false;backendStops.push({port:status.port,pid:status.pid,portClosed:closed,pidAlive:alive});assert.equal(closed,true);assert.equal(alive,false);}};
 const api=async(route,body,method=body===undefined?'GET':'POST')=>page.evaluate(async({route,body,method})=>{const port=await window.api.getBackendPort(),response=await fetch(`http://127.0.0.1:${port}`+route,{method,headers:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});if(!response.ok)throw Error(route+': '+response.status+' '+await response.text());return response.json();},{route,body,method});
 const openPanel=async()=>{await page.getByRole('button',{name:'왜 그런가요?',exact:true}).click();await page.getByRole('button',{name:'도우미에게 묻기',exact:true}).click();return page.getByRole('complementary',{name:'도우미',exact:true});};
 try{
   stage('launch');await launch();const project=await api('/api/project/create',{name:'Native ChatGPT isolated QA',task:'classification'});await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'classification'});await api('/api/dataset/split',{folder_path:source,task:'classification',train_ratio:.8,val_ratio:.1,test_ratio:.1});await page.reload();
   await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);await expect(page.getByRole('button',{name:'3-Way 분할 적용',exact:true})).toBeEnabled();
   let panel=await openPanel(),setup=panel.getByRole('region',{name:'ChatGPT 연결',exact:true});await expect(setup.getByRole('button',{name:'Continue with ChatGPT',exact:true})).toBeVisible();
   stage('identity-only');const begin=setup.getByRole('button',{name:'Continue with ChatGPT',exact:true});await expect(begin).toBeDisabled();await setup.getByRole('checkbox').check();await begin.click();
   assert.equal(await app.evaluate(()=>globalThis.__nativeChatgptQA.completeLogin(false)),200);await expect(setup).toContainText('플랜 사용은 활성화되지 않았습니다');await expect(panel.getByRole('textbox',{name:'도우미에게 묻기',exact:true})).toBeDisabled();
   stage('reconsent');await setup.getByRole('button',{name:'ChatGPT 플랜 사용 켜기',exact:true}).click();assert.equal(await app.evaluate(()=>globalThis.__nativeChatgptQA.completeLogin(true)),200);
   await page.getByRole('dialog',{name:'ChatGPT 플랜 사용 안내',exact:true}).getByRole('button',{name:'안내 확인',exact:true}).click();
   await expect(setup.getByRole('button',{name:'모델 목록 불러오기',exact:true})).toBeEnabled();await setup.getByRole('button',{name:'모델 목록 불러오기',exact:true}).click();await setup.getByRole('combobox',{name:'ChatGPT 모델',exact:true}).selectOption('native-qa-model');
   await expect(panel.getByRole('textbox',{name:'도우미에게 묻기',exact:true})).toBeEnabled();
   const splitFile=path.join(project.dataset_dir,'splits',crypto.createHash('sha256').update(source).digest('hex')+'.json'),initialSha=sha(splitFile);
   stage('read-and-prepare');await app.evaluate(()=>globalThis.__nativeChatgptQA.queue([{tools:[{name:'read_status'},{name:'prefill_split',arguments:{train_percent:70}}]},{text:'상태를 읽고 학습 비율을 채웠습니다. 저장하지 않았습니다.'}]));
   const ask=async(text)=>{await panel.getByRole('textbox',{name:'도우미에게 묻기',exact:true}).fill(text);await panel.getByRole('button',{name:'보내기',exact:true}).click();};
   await ask('상태를 읽고 분할 설정만 채워 주세요.');await expect(panel.getByRole('log',{name:'도우미 대화'})).toContainText('학습 비율 70%를 채웠습니다(저장 안 함).');await expect(panel.getByRole('log',{name:'도우미 대화'})).toContainText('상태를 읽고 학습 비율을 채웠습니다. 저장하지 않았습니다.');await expect(panel.getByRole('textbox',{name:'도우미에게 묻기',exact:true})).toBeEnabled();assert.equal(sha(splitFile),initialSha);
   for(const confirmed of [false,true]){
     stage(confirmed?'confirmed-write':'declined-write');
     const finalText=confirmed?'저장 처리를 끝냈습니다.':'거절 처리를 끝냈습니다.';
     await app.evaluate((_electron,text)=>globalThis.__nativeChatgptQA.queue([{tools:[{name:'save_split',arguments:{train_percent:70,val_percent:20,test_percent:10}}]},{text}]),finalText);await ask('분할을 70/20/10으로 저장하려고 합니다.');
     await expect(panel.getByRole('group',{name:'쓰기 실행 확인',exact:true})).toContainText('학습 70%');assert.equal(sha(splitFile),initialSha);
     await panel.getByRole('button',{name:confirmed?'확인하고 실행':'실행하지 않기',exact:true}).click();await expect(panel.getByRole('group',{name:'쓰기 실행 확인',exact:true})).toHaveCount(0);await expect(panel.getByRole('log',{name:'도우미 대화'})).toContainText(finalText);await expect(panel.getByRole('button',{name:'보내기',exact:true})).toBeVisible();await expect(panel.getByRole('textbox',{name:'도우미에게 묻기',exact:true})).toBeEnabled();
     if(!confirmed)assert.equal(sha(splitFile),initialSha);
   }
   assert.notEqual(sha(splitFile),initialSha);const runs=(await api('/api/assistant/runs')).runs,splitRuns=runs.filter(r=>r.tool==='save_split');assert.equal(splitRuns.length,2);assert.equal(splitRuns[0].result,'refused');assert.equal(splitRuns[1].result,'ok');assert.equal(splitRuns[1].confirmed,true);
   const assignments=JSON.parse(fs.readFileSync(splitFile,'utf8')).assignments,counts=Object.values(assignments).reduce((all,value)=>{all[value]=(all[value]||0)+1;return all;},{});assert.deepEqual(counts,{train:14,val:4,test:2});results.confirmedSplit=counts;results.loggedTools=runs.map(r=>({tool:r.tool,result:r.result,confirmed:r.confirmed}));
   stage('usage-limit');
   await app.evaluate(()=>globalThis.__nativeChatgptQA.limit(true));await ask('한도 상태를 확인합니다.');await expect(panel.getByRole('textbox',{name:'도우미에게 묻기',exact:true})).toBeDisabled();
   results.provider=await app.evaluate(()=>globalThis.__nativeChatgptQA.snapshot());assert.equal(results.provider.promptConsent,1);assert.equal(results.provider.externalNetwork,0);assert.equal(results.provider.authOpens,2);
   await setup.getByText('ChatGPT 사용 한도 때문에 새 질문을 중단했습니다.',{exact:false}).scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'native-plan-limit.png')});await close();
   stage('restart');await launch();panel=await openPanel();setup=panel.getByRole('region',{name:'ChatGPT 연결',exact:true});await expect(page.getByRole('dialog',{name:'ChatGPT 플랜 사용 안내',exact:true})).toHaveCount(0);await expect(panel.getByRole('textbox',{name:'도우미에게 묻기',exact:true})).toBeDisabled();
   const restored=await page.evaluate(()=>window.api.assistant.getState());assert.equal(restored.chatgpt.accounts[0].planUsageAcknowledged,true);assert.equal(restored.chatgpt.accounts[0].usagePaused,true);results.persistedWelcomeAndPause=true;
   assert.equal(await app.evaluate((_electron,value)=>globalThis.__nativeChatgptQA.hasSecret(value),JSON.stringify(restored)),false);assert.equal(await app.evaluate((_electron,value)=>globalThis.__nativeChatgptQA.hasSecret(value),logs.join('')),false);
   const resume=setup.getByRole('button',{name:'사용량 확인 후 요청 재개 허용',exact:true});await expect(resume).toBeDisabled();await resume.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'native-restarted-paused.png')});
   await setup.getByRole('link',{name:'ChatGPT 사용량 관리',exact:true}).click();await expect(resume).toBeEnabled();await resume.click();await expect(panel.getByRole('textbox',{name:'도우미에게 묻기',exact:true})).toBeEnabled();assert.equal((await app.evaluate(()=>globalThis.__nativeChatgptQA.snapshot())).responses,0);results.explicitResumeWithoutAutoSend=true;await close();
   // A retained lock with a genuinely exited owner's PID simulates the filesystem
   // state after a crash. No live app/backend is killed to manufacture this case.
   const child=require('node:child_process').spawnSync(process.execPath,['-e','process.stdout.write(String(process.pid))'],{encoding:'utf8'});assert.equal(child.status,0);const pid=Number(child.stdout);assert.throws(()=>process.kill(pid,0),e=>e.code==='ESRCH');
   const connectionDir=path.join(run.userData,'assistant/chatgpt'),connectionFile=path.join(connectionDir,'chatgpt-sessions.json'),lockFile=path.join(connectionDir,'chatgpt-sessions.lock'),oldCipher=fs.readFileSync(connectionFile),lock=JSON.stringify({schema_version:2,pid,nonce:crypto.randomBytes(32).toString('hex')});assert.ok(!fs.existsSync(lockFile));fs.writeFileSync(lockFile,lock,{mode:0o600});
   stage('stale-store-recovery');await launch();panel=await openPanel();setup=panel.getByRole('region',{name:'ChatGPT 연결',exact:true});await setup.getByRole('button',{name:'종료된 연결 복구 준비',exact:true}).click();const confirm=setup.getByRole('group',{name:'로컬 연결 복구 확인',exact:true});await expect(confirm).toContainText('모든 계정은 다시 로그인');await confirm.getByRole('button',{name:'복구하지 않기',exact:true}).click();assert.deepEqual(fs.readFileSync(connectionFile),oldCipher);assert.equal(fs.readFileSync(lockFile,'utf8'),lock);
   await setup.getByRole('button',{name:'종료된 연결 복구 준비',exact:true}).click();await confirm.getByRole('button',{name:'확인하고 로컬 연결 복구',exact:true}).click();await expect(setup).toContainText('저장된 계정의 로그인이 만료되었습니다');
   const recovered=await page.evaluate(()=>window.api.assistant.getState());assert.equal(recovered.chatgpt.accounts[0].signedIn,false);assert.equal(recovered.chatgpt.accounts[0].model,'native-qa-model');assert.equal(recovered.chatgpt.accounts[0].planUsageAcknowledged,true);const archives=fs.readdirSync(connectionDir).filter(name=>name.startsWith('chatgpt-sessions.recovery-'));assert.equal(archives.length,1);assert.deepEqual(fs.readFileSync(path.join(connectionDir,archives[0])),oldCipher);assert.equal((await app.evaluate(()=>globalThis.__nativeChatgptQA.snapshot())).responses,0);results.explicitEncryptedRecovery=true;await setup.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'native-recovered-signed-out.png')});
   for(const [file,hash]of Object.entries(originalHashes))assert.equal(sha(file),hash);results.outcome='passed';results.pageErrors=pageErrors;assert.deepEqual(pageErrors,[]);
 }catch(error){results.outcome='failed';results.message=error.message;if(page&&!page.isClosed()){await page.screenshot({path:path.join(out,'failure.png')}).catch(()=>{});results.visibleFailure=await page.getByRole('complementary',{name:'도우미',exact:true}).innerText().catch(()=>'panel unavailable');}throw error;}finally{
   try{await close();}catch(error){results.outcome='failed';results.cleanupError=error.message;}
   results.backendStops=backendStops;results.processReceipts=fs.readdirSync(out).filter(name=>/^process-\d+\.json$/.test(name)).map(name=>JSON.parse(fs.readFileSync(path.join(out,name),'utf8')));
   if(sourceFiles.some(file=>sha(path.join(REPO,file))!==sourceHashes[file])){results.outcome='failed';results.sourceChanged=true;}
   if(!results.processReceipts.length||results.processReceipts.some(r=>r.externalNetwork!==0||r.secretLogAttempts!==0)){results.outcome='failed';results.securityError='A native process reported a network or credential-log violation, or failed to close.';}
   const finalLog=logs.join('');if(/native-qa-generated-(?:access|refresh|subject)/.test(finalLog)){results.outcome='failed';results.securityError='Generated credential found in captured native logs';}
   write(path.join(out,'result.json'),results);fs.writeFileSync(path.join(out,'electron.log'),finalLog.replace(/native-qa-generated-(?:access|refresh|subject)/g,'[generated credential leak]'),{mode:0o600});
 }
 assert.equal(results.outcome,'passed',results.cleanupError||results.securityError);
 console.log(JSON.stringify({outcome:results.outcome,out,provider:results.provider,persistedWelcomeAndPause:results.persistedWelcomeAndPause}));
}
module.exports={runElectron,installProviderFixture};
if(require.main===module)main(process.argv[2]).catch(error=>{console.error(error.message);process.exitCode=1;});
