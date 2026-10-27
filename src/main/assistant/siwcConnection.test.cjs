// Live-connection boundary tests: generated credentials, fake HTTPS transport and a real owned loopback listener.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),crypto=require('node:crypto');
function load(file=path.join(__dirname,'siwcConnection.ts'),cache=new Map()){
 assert.ok(fs.existsSync(file),'SIWC live connection exists');if(cache.has(file))return cache.get(file).exports;
 const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));cache.set(file,m);const original=m.require.bind(m);
 m.require=name=>name.startsWith('.')?load(path.resolve(path.dirname(file),name)+'.ts',cache):original(name);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);return m.exports;
}
const NOW=1792800000000,CLIENT='oaiapp_connection_test',ISSUER='https://auth.openai.com',API='https://api.openai.com/v1';
const keys=crypto.generateKeyPairSync('rsa',{modulusLength:2048}),jwk={...keys.publicKey.export({format:'jwk'}),kid:'test-key',alg:'RS256',use:'sig'};
function jwt(change={}){const head=Buffer.from(JSON.stringify({alg:'RS256',kid:jwk.kid})).toString('base64url'),body=Buffer.from(JSON.stringify({iss:ISSUER,aud:CLIENT,sub:'test-subject',exp:NOW/1000+3600,iat:NOW/1000,...change})).toString('base64url');return head+'.'+body+'.'+crypto.sign('sha256',Buffer.from(head+'.'+body),keys.privateKey).toString('base64url');}
const rawToken=(nonce,extra={})=>({access_token:'fake-access',refresh_token:'fake-refresh',id_token:jwt({nonce}),token_type:'Bearer',expires_in:3600,scope:'openid profile offline_access resource.invoke chatgpt.tokens.use.direct',...extra});
const credentials=(extra={})=>({issuer:ISSUER,subject:'test-subject',clientId:CLIENT,accessToken:'fake-access',refreshToken:'fake-refresh',idToken:jwt(),scopes:['chatgpt.tokens.use.direct'],expiresAt:NOW+3600000,planUsageEnabled:true,...extra});
function store(initial=null){const records=new Map(initial?[['record-1',{tokens:structuredClone(initial),registration:{issuer:initial.issuer,subject:initial.subject,clientId:initial.clientId},model:'test-model',label:'시험 계정'}]]:[]);let active=initial?'record-1':null;
 return {records,state:()=>({accounts:[...records].map(([recordId,r])=>({recordId,label:r.label,model:r.model,signedIn:!!r.tokens,planUsageEnabled:!!r.tokens?.planUsageEnabled,planUsageAcknowledged:r.planUsageAcknowledged===true,usagePaused:r.usagePaused===true})),active,encryptionAvailable:true}),hostId:()=> 'urn:uuid:00000000-0000-4000-8000-000000000001',registration:id=>records.get(id)?.registration??null,credentials:id=>structuredClone(records.get(id||active)?.tokens??null),async save(tokens,options={}){const id=options.recordId||'record-'+(records.size+1),prior=records.get(id);records.set(id,{...prior,tokens:structuredClone(tokens),registration:{issuer:tokens.issuer,subject:tokens.subject,clientId:tokens.clientId},model:prior?.model||'',label:options.label||prior?.label||'ChatGPT 계정'});if(options.activate!==false)active=id;return (await this.state()).accounts.find(r=>r.recordId===id);},clearCredentials:id=>{const r=records.get(id);if(r)r.tokens=null;},setActive:id=>{active=id;},setModel:(id,model)=>{records.get(id).model=model;},setLabel:(id,label)=>{records.get(id).label=label;},acknowledgePlanUsage:id=>{records.get(id).planUsageAcknowledged=true;},setUsagePaused:(id,paused)=>{records.get(id).usagePaused=paused;}};
}
const completed=()=>new Response('event: response.completed\ndata: '+JSON.stringify({type:'response.completed',response:{status:'completed',output:[{type:'message',role:'assistant',content:[{type:'output_text',text:'완료'}]}]}})+'\n\n',{headers:{'content-type':'text/event-stream'}});
const providerOutput=output=>new Response('event: response.completed\ndata: '+JSON.stringify({type:'response.completed',response:{status:'completed',output}})+'\n\n',{headers:{'content-type':'text/event-stream'}});
function setup(options={}){const calls=[],opened=[],s=options.store||store();let auth;
 const fetchImpl=async(url,init={})=>{calls.push({url:String(url),init});if(options.fetchOverride){const response=await options.fetchOverride(String(url),init);if(response)return response;}
  if(String(url)===ISSUER+'/.well-known/openid-configuration')return Response.json({issuer:ISSUER,authorization_endpoint:ISSUER+'/api/accounts/authorize',token_endpoint:ISSUER+'/api/accounts/oauth/token',jwks_uri:ISSUER+'/jwks',revocation_endpoint:ISSUER+'/oauth/revoke'});
  if(String(url)===ISSUER+'/jwks')return Response.json({keys:[jwk]});
  if(String(url)===ISSUER+'/api/accounts/oauth/token')return Response.json(rawToken(auth?.searchParams.get('nonce'),{...options.tokenExtra,...(options.subject?{id_token:jwt({nonce:auth?.searchParams.get('nonce'),sub:options.subject})}:{})}));
  if(String(url)===ISSUER+'/oauth/revoke')return new Response('',{status:200});
  if(String(url)===API+'/models')return Response.json({models:[{slug:'test-model',display_name:'Test',visibility:'list'},{slug:'hidden-model',visibility:'hidden'}]});
  if(String(url)===API+'/responses')return completed();throw Error('Unexpected test URL');};
 const C=load().SiwcConnection,c=new C({store:s,fetchImpl,now:()=>NOW,attemptTimeoutMs:1000,timeoutMs:500,...options.connectionOptions,openExternal:async url=>{opened.push(url);auth=new URL(url);if(options.opener)await options.opener(url);}});
 return {c,s,calls,opened,auth:()=>auth,callback:async(extra={})=>{const u=new URL(auth.searchParams.get('redirect_uri'));u.search=new URLSearchParams({state:auth.searchParams.get('state'),code:'fake-code',client_id:CLIENT,...extra});return fetch(u);}};
}
async function settled(c){for(let n=0;n<50;n++){if(!['waiting','exchanging'].includes((await c.state()).status))return (await c.state());await new Promise(r=>setTimeout(r,5));}throw Error('callback did not settle');}
function timedConnection(t,options={}){
 t.mock.timers.enable({apis:['setTimeout']});
 const f=setup({...options,connectionOptions:{timeoutMs:undefined,...options.connectionOptions}});t.after(async ()=>(await f.c.dispose()));return f;
}
const textRequest={system:'안내',messages:[{role:'user',text:'진단 결과'}],tools:[]};
const nextTurn=()=>new Promise(resolve=>setImmediate(resolve));
function observe(promise){let value={state:'pending'};const settled=promise.then(result=>value={state:'completed',result},error=>value={state:error.detail?.code||'unexpected_error'});return{value:()=>value,settled};}

test('default text completion accepts terminal SSE after the 15 second control deadline',async t=>{
 let controller,started;const ready=new Promise(resolve=>{started=resolve;}),frame=new TextEncoder().encode(await completed().text());
 const response=new Response(new ReadableStream({start(value){controller=value;},pull(){started();return new Promise(()=>{});}},{highWaterMark:0}),{headers:{'Content-Type':'text/event-stream'}});
 const f=timedConnection(t,{store:store(credentials()),fetchOverride:async url=>url.endsWith('/responses')?response:null});
 t.after(async ()=>{try{(await controller.close());}catch{}});const observed=observe(f.c.complete(textRequest));await ready;
 t.mock.timers.tick(16000);await nextTurn();assert.equal(observed.value().state,'pending');
 controller.enqueue(frame);(await controller.close());await observed.settled;assert.equal(observed.value().state,'completed');assert.equal(observed.value().result.text,'완료');
});

test('default text completion still aborts a transport that ignores cancellation at 90 seconds',async t=>{
 let started,release,signal;const ready=new Promise(resolve=>{started=resolve;});
 const f=timedConnection(t,{store:store(credentials()),fetchOverride:async(url,init)=>url.endsWith('/responses')?new Promise(resolve=>{release=resolve;signal=init.signal;started();}):null});
 const observed=observe(f.c.complete(textRequest));await ready;t.mock.timers.tick(89999);await nextTurn();assert.equal(observed.value().state,'pending');
 t.mock.timers.tick(1);await observed.settled;assert.equal(observed.value().state,'timeout');assert.equal(signal.aborted,true);
 release(completed());await nextTurn();assert.equal(observed.value().state,'timeout');
});

for(const blocked of ['models','discovery','refresh'])test(`long text completion keeps the ${blocked} control deadline at 15 seconds`,async t=>{
 let started,signal;const ready=new Promise(resolve=>{started=resolve;});
 const f=timedConnection(t,{store:store(credentials(blocked==='models'?{}:{expiresAt:NOW+1})),fetchOverride:async(url,init)=>{
  if(url===(blocked==='models'?API+'/models':blocked==='discovery'?ISSUER+'/.well-known/openid-configuration':ISSUER+'/api/accounts/oauth/token'))return new Promise(()=>{signal=init.signal;started();});return null;
 }});
 const observed=observe(blocked==='models'?f.c.listModels():f.c.complete(textRequest));await ready;
 t.mock.timers.tick(14999);await nextTurn();assert.equal(observed.value().state,'pending');t.mock.timers.tick(1);await observed.settled;
 assert.equal(observed.value().state,'timeout');assert.equal(signal.aborted,true);assert.equal(f.calls.some(call=>call.url.endsWith('/responses')),false);
});

for(const input of [{constructorMs:40,wantMs:40},{constructorMs:20,inputMs:60,wantMs:60},{inputMs:30,wantMs:30}])test(`text completion honors explicit timeout ${JSON.stringify(input)}`,async t=>{
 let started;const ready=new Promise(resolve=>{started=resolve;});
 const f=timedConnection(t,{store:store(credentials()),connectionOptions:{timeoutMs:input.constructorMs},fetchOverride:async url=>url.endsWith('/responses')?new Promise(()=>{started();}):null});
 const observed=observe(f.c.complete({...textRequest,...(input.inputMs===undefined?{}:{timeoutMs:input.inputMs})}));await ready;
 t.mock.timers.tick(input.wantMs-1);await nextTurn();assert.equal(observed.value().state,'pending');t.mock.timers.tick(1);await observed.settled;assert.equal(observed.value().state,'timeout');
});

test('text completion rejects timeout above the existing 120 second bound before transport',async t=>{
 const f=timedConnection(t,{store:store(credentials())});await assert.rejects(f.c.complete({...textRequest,timeoutMs:120001}),error=>error.detail?.code==='bad_response');assert.equal(f.calls.length,0);
});

for(const change of ['cancel','account'])test(`extended text completion retains ${change} fencing after 15 seconds`,async t=>{
 let started,release,signal;const ready=new Promise(resolve=>{started=resolve;}),abort=new AbortController();
 const f=timedConnection(t,{store:store(credentials()),fetchOverride:async(url,init)=>url.endsWith('/responses')?new Promise(resolve=>{release=resolve;signal=init.signal;started();}):null});
 const observed=observe(f.c.complete({...textRequest,signal:abort.signal}));await ready;t.mock.timers.tick(16000);await nextTurn();assert.equal(observed.value().state,'pending');
 if(change==='cancel')abort.abort();else (await f.c.selectAccount(null));await observed.settled;assert.equal(observed.value().state,'cancelled');assert.equal(signal.aborted,true);
 release(completed());await nextTurn();assert.equal(observed.value().state,'cancelled');
});
test('only identity-only explicit enablement reconsents with its saved registration',async t=>{
 const f=setup({store:store(credentials({scopes:['openid'],planUsageEnabled:false}))});t.after(async ()=>(await f.c.dispose()));
 await f.c.begin({termsAccepted:true,recordId:'record-1',enablePlanUsage:true});assert.equal(f.auth().searchParams.get('prompt'),'consent');assert.equal(f.auth().searchParams.get('client_id'),CLIENT);assert.equal(f.auth().searchParams.has('force_reconsent'),false);(await f.c.cancel());
 await f.c.begin({termsAccepted:true,recordId:'record-1'});assert.equal(f.auth().searchParams.has('prompt'),false);
});
test('usage limit pauses the affected account across selection and requires explicit resume without auto request',async t=>{
 let limited=true;const s=store(credentials()),f=setup({store:s,fetchOverride:async url=>url.endsWith('/responses')&&limited?Response.json({error:{code:'subscription_sharing_usage_limit_exceeded',message:'private-token'}},{status:429}):null});t.after(async ()=>(await f.c.dispose()));
 const request={system:'안내',messages:[{role:'user',text:'상태'}],tools:[]};await assert.rejects(f.c.complete(request),e=>e.detail?.code==='rate_limited');assert.equal((await f.c.state()).accounts[0].usagePaused,true);const count=f.calls.length;
 (await f.c.selectAccount(null));(await f.c.selectAccount('record-1'));await assert.rejects(f.c.complete(request),e=>e.detail?.code==='rate_limited');assert.equal(f.calls.length,count);
 limited=false;(await f.c.resumeUsage('record-1'));assert.equal(f.calls.length,count);assert.equal((await f.c.state()).accounts[0].usagePaused,false);assert.equal((await f.c.complete(request)).text,'완료');
});
test('welcome acknowledgement belongs to the selected plan-enabled record and survives reconnect',async t=>{
 const s=store(credentials()),f=setup({store:s});t.after(async ()=>(await f.c.dispose()));assert.notEqual((await f.c.state()).accounts[0].planUsageAcknowledged,true);(await f.c.acknowledgePlanUsage('record-1'));assert.equal((await f.c.state()).accounts[0].planUsageAcknowledged,true);
 await f.c.begin({termsAccepted:true,recordId:'record-1'});await f.callback();await settled(f.c);assert.equal((await f.c.state()).accounts[0].planUsageAcknowledged,true);
});
test('admission401 preserves signed-in credentials and asks to inspect account scopes instead of reconnect',async t=>{
 const s=store(credentials()),f=setup({store:s,fetchOverride:async url=>url.endsWith('/responses')?Response.json({detail:'private-token'},{status:401,headers:{'x-request-id':'req_safe'}}):null});t.after(async ()=>(await f.c.dispose()));
 await assert.rejects(f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[]}),e=>e.detail?.status===401&&e.detail?.diagnostics?.bodyShape==='detail'&&e.detail?.requestId==='req_safe'&&!/다시 연결/.test(e.detail?.action)&&!JSON.stringify(e.detail).includes('private-token'));assert.ok((await s.credentials()));assert.equal((await f.c.state()).accounts[0].signedIn,true);
});
test('structured policy and unsupported failures keep safe code param and actual status',async t=>{
 const f=setup({store:store(credentials()),fetchOverride:async url=>url.endsWith('/responses')?Response.json({error:{code:'subscription_sharing_unsupported_capability',param:'tools[0]',message:'private-token'}},{status:400}):null});t.after(async ()=>(await f.c.dispose()));
 await assert.rejects(f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[]}),e=>e.detail?.diagnostics?.providerCode==='subscription_sharing_unsupported_capability'&&e.detail?.diagnostics?.param==='tools[0]'&&e.detail?.status===400&&!JSON.stringify(e.detail).includes('private-token'));
});
test('definitive temporary refresh admission failure preserves credentials while terminal refresh requires reconnect',async t=>{
 for(const [status,code,retained] of [[503,'subscription_sharing_usage_unavailable',true],[400,'invalid_client',true],[400,'invalid_grant',false]]){
  const s=store(credentials({expiresAt:NOW+1})),f=setup({store:s,fetchOverride:async url=>url.endsWith('/oauth/token')?Response.json({error:code},{status}):null});t.after(async ()=>(await f.c.dispose()));
  await assert.rejects(f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[]}),e=>retained?!/다시 연결/.test(e.detail?.action):e.detail?.code==='no_key');assert.equal(!!(await s.credentials()),retained);assert.equal((await f.c.state()).accounts[0].signedIn,retained);
 }
});
test('stale lease recovery exposes only a ticket and requires the explicit current confirmation without network',async t=>{
 const s=store(credentials()),read=s.state;let attempts=0;s.state=()=>{throw Object.assign(Error('private-file'),{code:'store_locked'});};s.recoveryStatus=()=>({available:true,reason:'stale_owner',ticket:'a'.repeat(64)});s.recover=async input=>{attempts++;assert.equal(input.ticket,'a'.repeat(64));assert.equal(input.confirmed,true);(await s.clearCredentials('record-1'));s.state=read;};
 const f=setup({store:s});t.after(async ()=>(await f.c.dispose()));const state=(await f.c.state());assert.equal(state.recovery?.available,true);assert.equal(state.recovery?.ticket,'a'.repeat(64));assert.ok(!JSON.stringify(state).includes('private-file'));assert.equal(attempts,0);
 (await assert.rejects(async ()=>(await f.c.recover({ticket:'a'.repeat(64),confirmed:false}))));assert.equal(attempts,0);(await f.c.recover({ticket:'a'.repeat(64),confirmed:true}));assert.equal(attempts,1);assert.equal((await f.c.state()).accounts[0].signedIn,false);assert.equal(f.calls.length,0);assert.equal(f.opened.length,0);
});
test('invalid recovery cannot discard the owner of an in-flight rotating refresh',async t=>{
 let release;const s=store(credentials({expiresAt:NOW+1}));s.recoveryStatus=()=>({available:false,reason:'none'});s.recover=()=>{throw Error('must not recover active storage');};
 const f=setup({store:s,fetchOverride:async url=>url.endsWith('/oauth/token')?new Promise(resolve=>{release=resolve;}):null});t.after(async ()=>(await f.c.dispose()));const request={system:'안내',messages:[{role:'user',text:'상태'}],tools:[]},pending=f.c.complete(request);
 while(!release)await new Promise(r=>setImmediate(r));(await assert.rejects(async ()=>(await f.c.recover({ticket:'a'.repeat(64),confirmed:true}))));release(Response.json(rawToken(undefined,{refresh_token:'safe-replacement'})));assert.equal((await pending).text,'완료');assert.equal((await s.credentials()).refreshToken,'safe-replacement');
});
test('login requires terms and safe storage, listens before opening, never returns authorization material',async t=>{const f=setup();t.after(async ()=>(await f.c.dispose()));await assert.rejects(f.c.begin({termsAccepted:false}));assert.equal(f.opened.length,0);const state=await f.c.begin({termsAccepted:true});assert.equal(state.status,'waiting');assert.equal((await f.c.state()).accounts.length,0);assert.equal(JSON.stringify(state).includes('nonce'),false);assert.equal(f.auth().hostname,'auth.openai.com');const reply=await f.callback();assert.equal(reply.status,200);const done=await settled(f.c);assert.equal(done.status,'connected');assert.equal(done.accounts.length,1);assert.equal(JSON.stringify(done).includes('fake-access'),false);assert.equal(f.calls.every(c=>c.init.redirect==='error'),true);});
test('model discovery is an authenticated read and requires explicit visible model selection before inference',async t=>{const f=setup({store:store(credentials())});t.after(async ()=>(await f.c.dispose()));(await f.s.setModel('record-1',''));await assert.rejects(f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[]}),e=>e.detail?.code==='not_found');const models=await f.c.listModels();assert.deepEqual(models,[{id:'test-model',label:'Test'}]);assert.equal(f.calls.filter(c=>c.url.endsWith('/responses')).length,0);(await assert.rejects(async ()=>(await f.c.selectModel('record-1','hidden-model'))));const prior=(await f.c.authority());(await f.c.selectModel('record-1','test-model'));assert.notEqual((await f.c.authority()),prior);const reply=await f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[]});assert.equal(reply.text,'완료');assert.equal(reply.stop,'end');const request=f.calls.find(c=>c.url.endsWith('/responses'));assert.equal(request.init.headers.Authorization,'Bearer fake-access');const body=JSON.parse(request.init.body);assert.equal(body.store,false);assert.equal(body.stream,true);assert.equal(body.model,'test-model');assert.deepEqual(body.input,[{role:'user',content:'상태'}]);assert.equal(JSON.stringify(reply).includes('fake-access'),false);});
test('model discovery accepts a catalog above 512 KiB while preserving visible order and explicit selection',async t=>{
 const raw=JSON.stringify({models:[{slug:'z-model',display_name:'Z model',visibility:'list',base_instructions:'x'.repeat(568000)},{slug:'hidden-model',visibility:'hidden'},{slug:'a-model',display_name:'A model',visibility:'list'}]});
 assert.ok(Buffer.byteLength(raw)>524288&&Buffer.byteLength(raw)<2097152);
 const f=setup({store:store(credentials()),fetchOverride:async url=>url===API+'/models'?new Response(raw,{headers:{'Content-Type':'application/json'}}):null});t.after(async ()=>(await f.c.dispose()));(await f.s.setModel('record-1',''));
 const models=await f.c.listModels();assert.deepEqual(models,[{id:'z-model',label:'Z model'},{id:'a-model',label:'A model'}]);
 await assert.rejects(f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[]}),e=>e.detail?.code==='not_found');
 (await assert.rejects(async ()=>(await f.c.selectModel('record-1','hidden-model'))));const prior=(await f.c.authority());(await f.c.selectModel('record-1','a-model'));assert.notEqual((await f.c.authority()),prior);assert.equal((await f.c.state()).accounts[0].model,'a-model');
 assert.equal(f.calls.filter(c=>c.url.endsWith('/responses')).length,0);
});
test('model discovery rejects catalogs above 2 MiB with or without a declared length',async t=>{
 const raw=JSON.stringify({models:[{slug:'test-model',display_name:'Test',visibility:'list',base_instructions:'x'.repeat(2097152)}]});
 for(const declared of [false,true]){const headers={'Content-Type':'application/json',...(declared?{'Content-Length':String(Buffer.byteLength(raw))}:{})};
  const f=setup({store:store(credentials()),fetchOverride:async url=>url===API+'/models'?new Response(raw,{headers}):null});t.after(async ()=>(await f.c.dispose()));
  await assert.rejects(f.c.listModels(),e=>e.detail?.code==='bad_response');(await assert.rejects(async ()=>(await f.c.selectModel('record-1','test-model'))));assert.equal(f.calls.filter(c=>c.url.endsWith('/responses')).length,0);
 }
});
test('larger model catalogs still reject invalid or duplicate visible slugs and more than 1000 rows',async t=>{
 for(const models of [[{slug:'invalid model',visibility:'list'}],[{slug:'test-model',visibility:'list'},{slug:'test-model',visibility:'list'}],Array.from({length:1001},(_,i)=>({slug:'hidden-'+i,visibility:'hidden'}))]){
  const f=setup({store:store(credentials()),fetchOverride:async url=>url===API+'/models'?Response.json({models,metadata:'x'.repeat(568000)}):null});t.after(async ()=>(await f.c.dispose()));
  await assert.rejects(f.c.listModels(),e=>e.detail?.code==='bad_response');(await assert.rejects(async ()=>(await f.c.selectModel('record-1','test-model'))));
 }
});
test('missing plan scope retains verified account identity but sends neither model nor inference request',async t=>{const f=setup({tokenExtra:{scope:'openid profile email'}});t.after(async ()=>(await f.c.dispose()));await f.c.begin({termsAccepted:true});await f.callback();const state=await settled(f.c);assert.equal(state.accounts[0].signedIn,true);assert.equal(state.accounts[0].planUsageEnabled,false);await assert.rejects(f.c.listModels(),e=>e.detail?.code==='forbidden');await assert.rejects(f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[]}),e=>e.detail?.code==='forbidden');assert.equal(f.calls.some(c=>c.url.startsWith(API)),false);});
test('state mismatch never exchanges a code or overwrites retained registration',async t=>{const s=store(credentials()),f=setup({store:s});t.after(async ()=>(await f.c.dispose()));await f.c.begin({termsAccepted:true,recordId:'record-1'});assert.equal(f.auth().searchParams.get('client_id'),CLIENT);assert.equal(f.auth().searchParams.get('id_token_hint'),credentials().idToken);await f.callback({state:'wrong-state'});assert.equal((await settled(f.c)).status,'error');assert.equal(f.calls.some(c=>c.url===ISSUER+'/api/accounts/oauth/token'),false);assert.equal((await s.credentials()).subject,'test-subject');});
test('a signed token from a different returning subject never overwrites selected registration',async t=>{const s=store(credentials()),f=setup({store:s,subject:'another-subject'});t.after(async ()=>(await f.c.dispose()));await f.c.begin({termsAccepted:true,recordId:'record-1'});const reply=await f.callback();assert.equal(reply.status,400);assert.equal((await settled(f.c)).status,'error');assert.equal((await s.credentials()).subject,'test-subject');assert.equal(JSON.stringify((await f.c.state())).includes('another-subject'),false);});
test('signout clears persisted credentials despite failed revocation and late sign-in cannot rehydrate',async t=>{const f=setup({store:store(credentials()),fetchOverride:async url=>url.endsWith('/oauth/revoke')?new Response('fake-refresh',{status:503}):null});t.after(async ()=>(await f.c.dispose()));const old=(await f.c.authority());const out=await f.c.signOut('record-1');assert.equal(out.remoteRevocationConfirmed,false);assert.equal((await f.s.credentials()),null);assert.ok((await f.s.registration('record-1')));assert.notEqual((await f.c.authority()),old);assert.equal(JSON.stringify(out).includes('fake-refresh'),false);await assert.rejects(f.c.listModels(),e=>e.detail?.code==='no_key');});
test('returning login after signout reuses host/client but never includes a logged-out id-token hint',async t=>{const f=setup({store:store(credentials())});t.after(async ()=>(await f.c.dispose()));await f.c.signOut('record-1');await f.c.begin({termsAccepted:true,recordId:'record-1'});assert.equal(f.auth().searchParams.get('client_id'),CLIENT);assert.equal(f.auth().searchParams.has('agent_name_hint'),false);assert.equal(f.auth().searchParams.has('id_token_hint'),false);assert.equal(f.auth().searchParams.get('ext_agent_host_id'),(await f.s.hostId()));});
test('a redirect, untrusted discovery origin or oversized response fails before tokens are saved',async t=>{for(const mode of ['redirect','discovery','size']){const f=setup({fetchOverride:async url=>{if(url.endsWith('/.well-known/openid-configuration')){if(mode==='redirect')return new Response('',{status:302,headers:{Location:'https://attacker.invalid'}});if(mode==='size')return new Response('x'.repeat(262145));return Response.json({issuer:ISSUER,authorization_endpoint:ISSUER+'/api/accounts/authorize',token_endpoint:ISSUER+'/api/accounts/oauth/token',jwks_uri:'https://attacker.invalid/jwks'});}return null;}});t.after(async ()=>(await f.c.dispose()));await f.c.begin({termsAccepted:true});await f.callback();const state=await settled(f.c);assert.equal(state.status,'error');assert.equal(f.s.records.size,0);assert.equal(f.calls.some(c=>c.url.includes('attacker.invalid')),false);assert.equal(f.calls.some(c=>c.url===ISSUER+'/api/accounts/oauth/token'),false);}});
test('refresh is single-flight, persists its replacement before inference and survives connection restart',async t=>{const s=store(credentials({expiresAt:NOW+1}));let refreshCalls=0;const f=setup({store:s,fetchOverride:async(url,init)=>{if(url.endsWith('/oauth/token')){refreshCalls++;const form=new URLSearchParams(init.body);assert.equal(form.get('grant_type'),'refresh_token');assert.equal(form.has('scope'),false);await new Promise(r=>setTimeout(r,10));return Response.json(rawToken(undefined,{access_token:'rotated-access',refresh_token:'rotated-refresh'}));}if(url.endsWith('/responses')){assert.equal((await s.credentials()).accessToken,'rotated-access');assert.equal(init.headers.Authorization,'Bearer rotated-access');}return null;}});t.after(async ()=>(await f.c.dispose()));const request={system:'안내',messages:[{role:'user',text:'상태'}],tools:[]};const [a,b]=await Promise.all([f.c.complete(request),f.c.complete(request)]);assert.equal(a.text,'완료');assert.equal(b.text,'완료');assert.equal(refreshCalls,1);assert.equal((await s.credentials()).refreshToken,'rotated-refresh');(await f.c.dispose());const restarted=setup({store:s});t.after(async ()=>(await restarted.c.dispose()));assert.equal((await restarted.c.complete(request)).text,'완료');assert.equal(restarted.calls.some(c=>c.url.endsWith('/oauth/token')),false);});
test('uncertain refresh clears stored credentials and cannot retry a rotating token after restart',async t=>{const s=store(credentials({expiresAt:NOW+1}));let calls=0;const f=setup({store:s,fetchOverride:async url=>{if(url.endsWith('/oauth/token')){calls++;throw Error('fake-refresh must never leak');}return null;}});t.after(async ()=>(await f.c.dispose()));const request={system:'안내',messages:[{role:'user',text:'상태'}],tools:[]};await assert.rejects(f.c.complete(request),e=>!JSON.stringify(e.detail).includes('fake-refresh'));assert.equal((await s.credentials()),null);await assert.rejects(f.c.complete(request),e=>e.detail?.code==='no_key');assert.equal(calls,1);(await f.c.dispose());const restarted=setup({store:s});t.after(async ()=>(await restarted.c.dispose()));await assert.rejects(restarted.c.complete(request),e=>e.detail?.code==='no_key');assert.equal(restarted.calls.length,0);});
async function refreshDiskStore(t){
 const root=fs.realpathSync(fs.mkdtempSync(path.join(require('node:os').tmpdir(),'siwc-refresh-fence-'))),dir=path.join(root,'assistant');let failures=0;
 const encryptor={isAvailable:()=>true,encrypt:plain=>{if(failures>0){failures--;throw Error('generated storage failure');}return Buffer.from('enc:'+Buffer.from(plain).toString('hex'));},decrypt:cipher=>Buffer.from(cipher.toString().slice(4),'hex').toString()};
 const {SiwcStore}=load(path.join(__dirname,'siwcStore.ts')),s=new SiwcStore(dir,encryptor),account=(await s.save(credentials({expiresAt:NOW+1})));(await s.setModel(account.recordId,'test-model'));
 // These files contain only generated test credentials and remain available for failure inspection.
 t.after(async ()=>(await s.close()));return {s,dir,encryptor,SiwcStore,failWrites:count=>{failures=count;}};
}
for(const failure of ['uncertain transport','rotated replacement save'])test(`failed credential clearing after ${failure} keeps restart locked`,async t=>{
 const disk=(await refreshDiskStore(t)),before=fs.readFileSync(disk.s.file);let refreshCalls=0;
 const f=setup({store:disk.s,fetchOverride:async url=>{if(!url.endsWith('/oauth/token'))return null;refreshCalls++;disk.failWrites(Infinity);if(failure==='uncertain transport')throw Error('generated transport failure');return Response.json(rawToken(undefined,{refresh_token:'generated-rotated-refresh'}));}});t.after(async ()=>(await f.c.dispose()));
 const request={system:'안내',messages:[{role:'user',text:'상태'}],tools:[]};await assert.rejects(f.c.complete(request));assert.equal((await f.c.state()).accounts[0].signedIn,false);
 await assert.rejects(f.c.complete(request));assert.equal(refreshCalls,1);assert.deepEqual(fs.readFileSync(disk.s.file),before);
 (await f.c.dispose());const restarted=new disk.SiwcStore(disk.dir,disk.encryptor);t.after(async ()=>(await restarted.close()));
 (await assert.rejects(async ()=>(await restarted.open()),error=>error.code==='store_locked'));assert.deepEqual(fs.readFileSync(disk.s.file),before);
});
test('dispose retries a transient credential clear before allowing a signed-out restart',async t=>{
 const disk=(await refreshDiskStore(t));let refreshCalls=0;
 const f=setup({store:disk.s,fetchOverride:async url=>{if(!url.endsWith('/oauth/token'))return null;refreshCalls++;disk.failWrites(1);throw Error('generated transport failure');}});t.after(async ()=>(await f.c.dispose()));
 await assert.rejects(f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[]}));assert.equal((await f.c.state()).accounts[0].signedIn,false);(await f.c.dispose());
 const restarted=new disk.SiwcStore(disk.dir,disk.encryptor);t.after(async ()=>(await restarted.close()));(await restarted.open());assert.equal((await restarted.credentials())===null,true);assert.equal((await restarted.state()).accounts[0].signedIn,false);assert.equal(refreshCalls,1);
});
test('failed signout clearing during refresh keeps restart locked after the detached refresh settles',async t=>{
 const disk=(await refreshDiskStore(t));let release,started;const ready=new Promise(resolve=>{started=resolve;});
 const f=setup({store:disk.s,fetchOverride:async url=>{if(!url.endsWith('/oauth/token'))return null;disk.failWrites(Infinity);started();return new Promise(resolve=>{release=resolve;});}});t.after(async ()=>(await f.c.dispose()));
 const failed=assert.rejects(f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[]}));await ready;
 await assert.rejects(f.c.signOut((await disk.s.state()).active));release(Response.json(rawToken(undefined,{refresh_token:'generated-rotated-refresh'})));await failed;await new Promise(resolve=>setImmediate(resolve));(await f.c.dispose());
 const restarted=new disk.SiwcStore(disk.dir,disk.encryptor);t.after(async ()=>(await restarted.close()));(await assert.rejects(async ()=>(await restarted.open()),error=>error.code==='store_locked'));
});
test('a successfully persisted reconnect replaces the unsafe record before restart',async t=>{
 const disk=(await refreshDiskStore(t)),recordId=(await disk.s.state()).active;
 const f=setup({store:disk.s,fetchOverride:async(url,init)=>{if(!url.endsWith('/oauth/token')||new URLSearchParams(init.body).get('grant_type')!=='refresh_token')return null;disk.failWrites(Infinity);throw Error('generated transport failure');}});t.after(async ()=>(await f.c.dispose()));
 await assert.rejects(f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[]}));disk.failWrites(0);
 await f.c.begin({termsAccepted:true,recordId});await f.callback();assert.equal((await settled(f.c)).accounts[0].signedIn,true);(await f.c.dispose());
 const restarted=new disk.SiwcStore(disk.dir,disk.encryptor);t.after(async ()=>(await restarted.close()));(await restarted.open());assert.equal((await restarted.state()).accounts[0].signedIn,true);
});
test('stateless tool outputs replay namespace call IDs and require terminal completed SSE',async t=>{const tool={name:'read_status',description:'상태',parameters:{type:'object',properties:{}}},calls=[{id:'call_test',name:'read_status',arguments:{}}];const f=setup({store:store(credentials())});t.after(async ()=>(await f.c.dispose()));const response=await f.c.complete({system:'안내',messages:[{role:'user',text:'상태'},{role:'assistant',text:'',toolCalls:calls},{role:'tool',toolCallId:'call_test',name:'read_status',content:'준비됨'}],tools:[tool]});assert.equal(response.text,'완료');const body=JSON.parse(f.calls.find(c=>c.url.endsWith('/responses')).init.body);assert.equal(body.input[1].namespace,'modu_vision');assert.equal(body.input[2].call_id,'call_test');assert.equal(body.tools[0].type,'namespace');for(const stream of ['event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"early"}\n\n','event: response.failed\ndata: {"type":"response.failed","response":{"error":{"code":"subscription_sharing_usage_limit_exceeded","message":"fake-access"}}}\n\n']){const broken=setup({store:store(credentials()),fetchOverride:async url=>url.endsWith('/responses')?new Response(stream,{headers:{'Content-Type':'text/event-stream'}}):null});t.after(async ()=>(await broken.c.dispose()));await assert.rejects(broken.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[]}),e=>!JSON.stringify(e.detail).includes('fake-access'));}});
test('completed streams without text or tool calls return an empty-response error without a receipt or retry',async t=>{
 for(const output of [[],[{type:'reasoning',summary:[{type:'summary_text',text:'검토만 완료'}],content:[],encrypted_content:null}],[{type:'message',role:'assistant',content:[]}],[{type:'message',role:'assistant',content:[{type:'output_text',text:''}]}]]){
  const response=providerOutput(output);response.headers.delete('content-type');response.headers.set('x-request-id','req_empty');
  const f=setup({store:store(credentials()),fetchOverride:async url=>url.endsWith('/responses')?response:null});t.after(async ()=>(await f.c.dispose()));
  await assert.rejects(f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[]}),e=>e.detail?.code==='bad_response'&&e.detail.reason==='ChatGPT가 요청을 끝냈지만 답변이나 도구 요청을 보내지 않았습니다.'&&e.detail.status===200&&e.detail.requestId==='req_empty');
  assert.equal(f.calls.filter(c=>c.url.endsWith('/responses')).length,1);const count=f.calls.length;
  await assert.rejects(f.c.complete({system:'안내',messages:[{role:'user',text:'상태'},{role:'assistant',text:'',replay:output}],tools:[]}),e=>e.detail?.code==='bad_response');assert.equal(f.calls.length,count);
 }
});
test('delta and malformed output-item-done cannot bypass strict stream validation',async t=>{
 const prefix='event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"중간 답변"}\n\n'+'event: response.output_item.done\ndata: '+JSON.stringify({type:'response.output_item.done',item:{type:'message',role:'assistant',content:[{type:'output_text',text:'중간 답변'}]}})+'\n\n';
 const response=new Response(Buffer.from(prefix+await providerOutput([]).text())),f=setup({store:store(credentials()),fetchOverride:async url=>url.endsWith('/responses')?response:null});t.after(async ()=>(await f.c.dispose()));
 await assert.rejects(f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[]}),e=>e.detail?.code==='bad_response');assert.equal(f.calls.filter(c=>c.url.endsWith('/responses')).length,1);
});
test('validated done snapshots with an empty terminal output receive text or tool replay receipts',async t=>{
 const tool={name:'read_status',description:'상태',parameters:{type:'object',properties:{}}};
 for(const toolOnly of [false,true]){const item=toolOnly?{id:'fc_snapshot',type:'function_call',status:'completed',namespace:'modu_vision',name:'read_status',call_id:'call_snapshot',arguments:'{}'}:{id:'msg_snapshot',type:'message',status:'completed',role:'assistant',phase:'final_answer',content:[{type:'output_text',text:'완료'}]},tools=toolOnly?[tool]:[];let calls=0;
  const raw='event: response.output_item.done\ndata: '+JSON.stringify({type:'response.output_item.done',output_index:0,item})+'\n\n'+await providerOutput([]).text();
  const f=setup({store:store(credentials()),fetchOverride:async url=>url.endsWith('/responses')?++calls===1?new Response(Buffer.from(raw)):completed():null});t.after(async ()=>(await f.c.dispose()));
  const answer=await f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools});assert.equal(answer.text,toolOnly?'':'완료');assert.equal(answer.stop,toolOnly?'tool':'end');assert.deepEqual(answer.replay,[item]);
  const tail=toolOnly?{role:'tool',toolCallId:'call_snapshot',name:'read_status',content:'준비됨'}:{role:'user',text:'다음'},next=await f.c.complete({system:'안내',messages:[{role:'user',text:'상태'},{role:'assistant',text:'',replay:answer.replay},tail],tools});assert.equal(next.text,'완료');assert.equal(calls,2);
 }
});
test('a valid tool-only completed response remains successful and receipted for the next tool result',async t=>{
 const tools=[{name:'read_status',description:'상태',parameters:{type:'object',properties:{}}}],output=[{type:'function_call',namespace:'modu_vision',name:'read_status',call_id:'call_tool_only',arguments:'{}'}];let count=0;
 const f=setup({store:store(credentials()),fetchOverride:async url=>{if(!url.endsWith('/responses'))return null;if(++count>1)return completed();const response=providerOutput(output);response.headers.delete('content-type');return response;}});t.after(async ()=>(await f.c.dispose()));
 const first=await f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools});assert.equal(first.text,'');assert.equal(first.stop,'tool');assert.equal(first.toolCalls.length,1);assert.deepEqual(first.replay,output);
 const next=await f.c.complete({system:'안내',messages:[{role:'user',text:'상태'},{role:'assistant',text:'',replay:first.replay},{role:'tool',toolCallId:'call_tool_only',name:'read_status',content:'준비됨'}],tools});assert.equal(next.text,'완료');assert.equal(count,2);
});
test('missing or blank response MIME and exact event-stream MIME still require completed SSE',async t=>{
 const raw=Buffer.from(await completed().arrayBuffer());
 for(const mime of [undefined,'','   ','text/event-stream','Text/Event-Stream; charset=utf-8','text/event-stream ; charset=utf-8']){
  const response=new Response(raw,{headers:mime===undefined?{}:{'Content-Type':mime}});
  if(mime===undefined)assert.equal(response.headers.get('content-type'),null);
  const f=setup({store:store(credentials()),fetchOverride:async url=>url.endsWith('/responses')?response:null});t.after(async ()=>(await f.c.dispose()));
  const answer=await f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[]});assert.equal(answer.text,'완료');assert.equal(answer.stop,'end');assert.equal(f.calls.filter(c=>c.url.endsWith('/responses')).length,1);
 }
});
test('explicit non-SSE MIME and event-stream lookalikes reject even a valid completed SSE body',async t=>{
 const raw=Buffer.from(await completed().arrayBuffer());
 for(const mime of ['application/json','text/html','text/plain','text/event-streaming','text/event-stream+json','text/event-stream-other','text/event-stream text/plain','text/event-stream, application/json',';charset=utf-8']){
  const f=setup({store:store(credentials()),fetchOverride:async url=>url.endsWith('/responses')?new Response(raw,{headers:{'Content-Type':mime}}):null});t.after(async ()=>(await f.c.dispose()));
  await assert.rejects(f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[]}),e=>e.detail?.code==='bad_response');
 }
});
test('missing response MIME does not accept JSON HTML malformed or partial SSE and preserves streamed failures',async t=>{
 const cases=[['{"output_text":"fake-access"}','bad_response'],['<html>fake-access</html>','bad_response'],['event: response.completed\ndata: {broken}\n\n','bad_response'],['event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"fake-access"}\n\n','bad_response'],['event: response.incomplete\ndata: {"type":"response.incomplete","response":{"status":"incomplete"}}\n\n','bad_response'],['event: response.failed\ndata: {"type":"response.failed","response":{"error":{"code":"subscription_sharing_usage_limit_exceeded","message":"fake-access"}}}\n\n','rate_limited'],[null,'bad_response']];
 for(const [raw,code] of cases){const f=setup({store:store(credentials()),fetchOverride:async url=>url.endsWith('/responses')?new Response(raw===null?null:Buffer.from(raw)):null});t.after(async ()=>(await f.c.dispose()));
  await assert.rejects(f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[]}),e=>e.detail?.code===code&&!JSON.stringify(e.detail).includes('fake-access'));
  if(code==='rate_limited')assert.equal((await f.c.state()).accounts[0].usagePaused,true);
 }
});
test('missing response MIME retains total-stream and individual-event byte limits',async t=>{
 for(const raw of [':'+ 'x'.repeat(2*1024*1024)+'\n\n','event: response.output_text.delta\ndata: '+JSON.stringify({type:'response.output_text.delta',delta:'x'.repeat(256*1024)})+'\n\n']){
  const f=setup({store:store(credentials()),fetchOverride:async url=>url.endsWith('/responses')?new Response(Buffer.from(raw)):null});t.after(async ()=>(await f.c.dispose()));
  await assert.rejects(f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[]}),e=>e.detail?.code==='bad_response');
 }
});
test('missing response MIME keeps cancellation and account authority checks during body reading',async t=>{
 for(const change of ['cancel','account']){let started,controller;const ready=new Promise(resolve=>{started=resolve;}),response=new Response(new ReadableStream({start(c){controller=c;},pull(){started();return new Promise(()=>{});}},{highWaterMark:0}));
  const f=setup({store:store(credentials()),fetchOverride:async url=>url.endsWith('/responses')?response:null});t.after(async ()=>{try{(await controller.close());}catch{}(await f.c.dispose());});const abort=new AbortController(),pending=f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[],signal:abort.signal});
  const rejected=assert.rejects(pending,e=>e.detail?.code==='cancelled');let timer;try{await Promise.race([ready,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('SSE reader never started')),200);})]);}finally{clearTimeout(timer);}
  if(change==='cancel')abort.abort();else (await f.c.selectAccount(null));await rejected;assert.equal((await f.c.state()).active,change==='cancel'?'record-1':null);
 }
});
test('caller cancellation and account mutation abort in-flight inference even if injected transport ignores abort',async t=>{for(const change of ['cancel','account']){let release,started;const ready=new Promise(r=>{started=r;}),f=setup({store:store(credentials()),fetchOverride:async url=>url.endsWith('/responses')?new Promise(r=>{release=r;started();}):null});t.after(async ()=>(await f.c.dispose()));const abort=new AbortController(),flight=f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[],signal:abort.signal});const rejected=assert.rejects(flight,e=>e.detail?.code==='cancelled');await ready;if(change==='cancel')abort.abort();else (await f.c.selectAccount(null));await rejected;release(completed());await new Promise(r=>setImmediate(r));assert.equal((await f.c.state()).active,change==='cancel'?'record-1':null);}});
test('cancelling a callback exchange closes the listener and a delayed valid token cannot save an account',async t=>{let release,started;const ready=new Promise(r=>{started=r;}),f=setup({fetchOverride:async url=>url.endsWith('/oauth/token')?new Promise(r=>{release=r;started();}):null});t.after(async ()=>(await f.c.dispose()));await f.c.begin({termsAccepted:true});const callback=f.callback();await ready;assert.equal((await f.c.state()).status,'exchanging');(await f.c.cancel());release(Response.json(rawToken(f.auth().searchParams.get('nonce'))));await callback;await new Promise(r=>setTimeout(r,10));assert.equal(f.s.records.size,0);assert.equal((await f.c.state()).status,'idle');await assert.rejects(f.callback());});
test('dispose aborts an outstanding remote signout without restoring credentials',async t=>{let began,signal;const ready=new Promise(r=>{began=r;}),f=setup({store:store(credentials()),fetchOverride:async(url,init)=>{if(url.endsWith('/oauth/revoke')){signal=init.signal;began();return new Promise(()=>{});}return null;}});t.after(async ()=>(await f.c.dispose()));const out=f.c.signOut('record-1');await ready;assert.equal((await f.s.credentials()),null);(await f.c.dispose());await new Promise(r=>setTimeout(r,5));assert.equal(signal.aborted,true);assert.equal((await out).remoteRevocationConfirmed,undefined);});
test('dispose releases the protected store lease once for normal application restart',async ()=>{const s=store();let closed=0;s.close=()=>{closed++;};const f=setup({store:s});(await f.c.dispose());(await f.c.dispose());assert.equal(closed,1);});
test('quitting during refresh clears ambiguous retained tokens before releasing the store for restart',async t=>{const s=store(credentials({expiresAt:NOW+1}));let release,started;const ready=new Promise(r=>{started=r});let closed=0;s.close=async ()=>{closed++;assert.equal((await s.credentials()),null)};const f=setup({store:s,fetchOverride:async url=>url.endsWith('/oauth/token')?new Promise(r=>{release=r;started()}):null});const request={system:'안내',messages:[{role:'user',text:'상태'}],tools:[]};const pending=f.c.complete(request);const failed=assert.rejects(pending,e=>e.detail?.code==='cancelled');await ready;(await f.c.dispose());assert.equal(closed,1);release(Response.json(rawToken(undefined,{refresh_token:'remote-rotated'})));await failed;await new Promise(r=>setImmediate(r));const next=setup({store:s});t.after(async ()=>(await next.c.dispose()));await assert.rejects(next.c.complete(request),e=>e.detail?.code==='no_key');assert.equal(next.calls.length,0);});
test('public settings reads use an unopened-store peek and never reopen the store after dispose',async ()=>{const s=store();let reads=0,peeks=0;s.state=()=>{reads++;throw Error('state must not open');};s.peekState=()=>{peeks++;return {accounts:[],active:null,encryptionAvailable:true};};const f=setup({store:s});assert.equal((await f.c.state()).status,'idle');assert.equal(reads,0);assert.equal(peeks,1);(await f.c.dispose());(await f.c.state());assert.equal(peeks,1);assert.equal(reads,0);});
test('safe provider request IDs survive HTTP and streamed failures without copying raw error bodies',async t=>{for(const stream of [false,true]){const f=setup({store:store(credentials()),fetchOverride:async url=>url.endsWith('/responses')?stream?new Response('event: response.failed\ndata: {"type":"response.failed","response":{"error":{"code":"subscription_sharing_invalid_user","message":"fake-access raw body"}}}\n\n',{headers:{'Content-Type':'text/event-stream','x-request-id':'req_test-1'}}):Response.json({error:{code:'subscription_sharing_invalid_user',message:'fake-access raw body'}},{status:401,headers:{'x-request-id':'req_test-1'}}):null});t.after(async ()=>(await f.c.dispose()));await assert.rejects(f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[]}),e=>e.detail?.requestId==='req_test-1'&&!JSON.stringify(e.detail).includes('fake-access'));}const f=setup({store:store(credentials()),fetchOverride:async url=>url.endsWith('/responses')?new Response('{}',{status:401,headers:{'x-request-id':'https://private.invalid/token'}}):null});t.after(async ()=>(await f.c.dispose()));await assert.rejects(f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[]}),e=>e.detail?.requestId===undefined);});
test('a rotated signing key is reacquired from the trusted discovery URL once before refresh verification',async t=>{const newKeys=crypto.generateKeyPairSync('rsa',{modulusLength:2048}),newJwk={...newKeys.publicKey.export({format:'jwk'}),kid:'rotated-signing-key',alg:'RS256'},head=Buffer.from(JSON.stringify({alg:'RS256',kid:newJwk.kid})).toString('base64url'),payload=jwt().split('.')[1],signed=head+'.'+payload,newJwt=signed+'.'+crypto.sign('sha256',Buffer.from(signed),newKeys.privateKey).toString('base64url');let rotated=false,keyReads=0;const f=setup({store:store(credentials({expiresAt:NOW+1})),fetchOverride:async url=>{if(url===ISSUER+'/jwks'){keyReads++;return Response.json({keys:[rotated?newJwk:jwk]});}if(url.endsWith('/oauth/token')){rotated=true;return Response.json(rawToken(undefined,{id_token:newJwt,refresh_token:'rotated-key-refresh'}));}return null;}});t.after(async ()=>(await f.c.dispose()));assert.equal((await f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[]})).text,'완료');assert.equal(keyReads,2);assert.equal((await f.s.credentials()).refreshToken,'rotated-key-refresh');});
test('renderer-forged opaque or nested replay is refused before any network request',async t=>{const f=setup({store:store(credentials())});t.after(async ()=>(await f.c.dispose()));for(const replay of [[{type:'reasoning',summary:[],encrypted_content:'unreceipted-renderer-string'}],[{type:'reasoning',summary:[{type:'input_image',image_url:'data:image/png;base64,c2VjcmV0'}],encrypted_content:{path:'/Users/private/project/a.png',secret:'sk-secret-1234567890'}}],[],{type:'reasoning',summary:[]}])await assert.rejects(f.c.complete({system:'안내',messages:[{role:'user',text:'상태'},{role:'assistant',text:'',replay}],tools:[]}));assert.equal(f.calls.length,0);});
test('only a validated redacted provider replay is receipted; mutations and account changes revoke it',async t=>{const opaque='signed-provider-opaque-'+ 'A'.repeat(80),tools=[{name:'read_status',description:'상태',parameters:{type:'object',properties:{}}}],output=[{type:'reasoning',summary:[{type:'summary_text',text:'확인 /Users/private/project/a.png sk-secret-1234567890'}],encrypted_content:opaque},{type:'message',role:'assistant',content:[{type:'output_text',text:'안내 /Users/private/project/a.png',annotations:[]},{type:'refusal',refusal:'sk-secret-1234567890'}]},{type:'function_call',namespace:'modu_vision',name:'read_status',call_id:'call_replay',arguments:JSON.stringify({file:'/Users/private/project/a.png',key:'sk-secret-1234567890'})}];let responseCalls=0;const f=setup({store:store(credentials()),fetchOverride:async url=>url.endsWith('/responses')?++responseCalls===1?providerOutput(output):completed():null});t.after(async ()=>(await f.c.dispose()));const first=await f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools});assert.equal(first.replay[0].encrypted_content,opaque);assert.equal(first.text,'안내 a.png[가림]');assert.deepEqual(first.toolCalls[0].arguments,{file:'a.png',key:'[가림]'});assert.deepEqual(JSON.parse(first.replay[2].arguments),first.toolCalls[0].arguments);assert.equal(JSON.stringify(first.replay).includes('/Users/private'),false);assert.equal(JSON.stringify(first.replay).includes('sk-secret-'),false);
 const messages=[{role:'user',text:'상태'},{role:'assistant',text:first.text,toolCalls:first.toolCalls,replay:first.replay},{role:'tool',toolCallId:'call_replay',name:'read_status',content:'준비됨'}];assert.equal((await f.c.complete({system:'안내',messages,tools})).text,'완료');assert.equal(JSON.parse(f.calls.filter(c=>c.url.endsWith('/responses'))[1].init.body).input[1].encrypted_content,opaque);
 const changed=structuredClone(messages);changed[1].replay[0].encrypted_content+='tampered';await assert.rejects(f.c.complete({system:'안내',messages:changed,tools}));assert.equal(responseCalls,2);(await f.c.selectAccount(null));(await f.c.selectAccount('record-1'));await assert.rejects(f.c.complete({system:'안내',messages,tools}));assert.equal(responseCalls,2);
});
test('malformed provider replay cannot be returned as success or become an outbound receipt',async t=>{const malicious=[{type:'reasoning',summary:[{type:'summary_text',text:'정상',image_url:'data:image/png;base64,c2VjcmV0'}],encrypted_content:'opaque'}],f=setup({store:store(credentials()),fetchOverride:async url=>url.endsWith('/responses')?providerOutput(malicious):null});t.after(async ()=>(await f.c.dispose()));await assert.rejects(f.c.complete({system:'안내',messages:[{role:'user',text:'상태'}],tools:[]}));const count=f.calls.length;await assert.rejects(f.c.complete({system:'안내',messages:[{role:'user',text:'상태'},{role:'assistant',text:'',replay:malicious}],tools:[]}));assert.equal(f.calls.length,count);});
test('only a saved and activated login tells the service that ChatGPT is now the chosen recipient',async t=>{
 const f=setup();t.after(async ()=>(await f.c.dispose()));const connected=[];f.c.onConnected(id=>connected.push(id));
 await f.c.begin({termsAccepted:true});await f.callback({error:'access_denied'});assert.equal((await settled(f.c)).status,'error');
 assert.deepEqual(connected,[],'a denied login is not a choice');
 await f.c.begin({termsAccepted:true});await f.callback();const state=await settled(f.c);
 assert.equal(state.status,'connected');assert.deepEqual(connected,[state.active]);
 f.c.onConnected(()=>{throw Error('listener failure');});(await f.c.cancel());
 await f.c.begin({termsAccepted:true,recordId:state.active});await f.callback();assert.equal((await settled(f.c)).status,'connected','a listener cannot fail the saved login');
});
