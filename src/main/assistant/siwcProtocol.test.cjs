// Offline protocol only: generated keys, synthetic tokens and injected transports. No listener, browser, files or fetch.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),crypto=require('node:crypto');
function load(file=path.join(__dirname,'siwcProtocol.ts'),cache=new Map()){
 assert.ok(fs.existsSync(file),'offline SIWC protocol exists');if(cache.has(file))return cache.get(file).exports;
 const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));cache.set(file,m);const original=m.require.bind(m);
 m.require=name=>name.startsWith('.')?load(path.resolve(path.dirname(file),name)+'.ts',cache):original(name);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);return m.exports;
}
const NOW=1792800000000,CLIENT='oaiapp_synthetic',HOST='urn:uuid:00000000-0000-4000-8000-000000000001';
const keys=crypto.generateKeyPairSync('rsa',{modulusLength:2048}),publicKey={...keys.publicKey.export({format:'jwk'}),kid:'synthetic-key',alg:'RS256',use:'sig'};
function jwt(change={}){const head=Buffer.from(JSON.stringify({alg:'RS256',kid:publicKey.kid})).toString('base64url'),body=Buffer.from(JSON.stringify({iss:'https://auth.openai.com',aud:CLIENT,sub:'synthetic-user',exp:NOW/1000+3600,iat:NOW/1000,nonce:'expected-nonce',...change})).toString('base64url');return head+'.'+body+'.'+crypto.sign('sha256',Buffer.from(head+'.'+body),keys.privateKey).toString('base64url');}
const token=(change={})=>({access_token:'synthetic-access',refresh_token:'synthetic-refresh',id_token:jwt(),token_type:'Bearer',expires_in:3600,scope:'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct',...change});
function credentials(protocol){return protocol.acceptAuthorizationTokens(token(),{clientId:CLIENT,nonce:'expected-nonce',jwks:{keys:[publicKey]},nowMs:NOW});}
const event=(type,extra={})=>'event: '+type+'\r\ndata: '+JSON.stringify({type,...extra})+'\r\n\r\n';
const completed=(output=[])=>event('response.completed',{response:{id:'resp_synthetic',status:'completed',output}});
async function* bytes(text,width=7){const b=Buffer.from(text);for(let i=0;i<b.length;i+=width)yield b.subarray(i,i+width);}
const allow=[{name:'read_status',description:'상태',parameters:{type:'object',properties:{},additionalProperties:false}}];
const doneItem=(output_index,item)=>event('response.output_item.done',{output_index,item});
const addedItem=(output_index,item)=>event('response.output_item.added',{output_index,item});
const doneMessage=(id,text='완료')=>({id,type:'message',role:'assistant',status:'completed',phase:'final_answer',content:[{type:'output_text',text,annotations:[],logprobs:[]}]});

test('explicit plan enablement asks consent while ordinary returning sign-in does not',()=>{
 const p=load(),options={hostId:HOST,appName:'Modu Vision',redirectUri:'http://127.0.0.1:43210/auth/callback',clientId:CLIENT};
 const regular=new URL(p.createAuthorizationAttempt(options).authorizationUrl),enable=new URL(p.createAuthorizationAttempt({...options,promptConsent:true}).authorizationUrl);
 assert.equal(regular.searchParams.has('prompt'),false);assert.equal(enable.searchParams.get('prompt'),'consent');assert.equal(enable.searchParams.has('force_reconsent'),false);assert.match(enable.searchParams.get('scope'),/chatgpt\.tokens\.use\.direct/);
});
test('streamed failures preserve transport status and safe structured diagnostics without provider messages',async()=>{
 const p=load();await assert.rejects(p.readSiwcResponse(bytes(event('response.failed',{response:{error:{code:'subscription_sharing_unsupported_capability',param:'tools[0]',message:'private-token'}}})),{tools:[],httpStatus:200}),e=>e.status===200&&e.diagnostics?.providerCode==='subscription_sharing_unsupported_capability'&&e.diagnostics?.param==='tools[0]'&&e.diagnostics?.bodyShape==='response_error'&&!JSON.stringify(e).includes('private-token'));
});
test('error diagnostics omit secret-like unknown codes and arbitrary params while preserving documented structural fields',()=>{
 const p=load(),unsafe=p.siwcResponseError(400,{error:{code:'private_access_token_123',param:'sk-live-secret',message:'private-message'}});assert.equal(unsafe.code,'http_error');assert.deepEqual(unsafe.diagnostics,{bodyShape:'error_object'});assert.ok(!JSON.stringify(unsafe).includes('private_access_token'));assert.ok(!JSON.stringify(unsafe).includes('sk-live-secret'));
 const safe=p.siwcResponseError(400,{error:{code:'subscription_sharing_unsupported_capability',param:'tools[0].type'}});assert.equal(safe.diagnostics.providerCode,'subscription_sharing_unsupported_capability');assert.equal(safe.diagnostics.param,'tools[0].type');
});

test('text-only replay rejects nested image payloads, arbitrary keys and invalid encrypted content',()=>{
 const p=load(),base={model:'synthetic-model',tools:allow};
 for(const item of [
  {type:'reasoning',summary:[{type:'input_image',image_url:'data:image/png;base64,c2VjcmV0'}],encrypted_content:'opaque'},
  {type:'reasoning',summary:[],encrypted_content:{path:'/Users/private/project/a.png',secret:'sk-secret-1234567890'}},
  {type:'reasoning',summary:[{type:'summary_text',text:'안내',image_url:'data:image/png;base64,c2VjcmV0'}]},
  {type:'message',role:'assistant',content:[{type:'output_text',text:'안내',image_url:'data:image/png;base64,c2VjcmV0'}]},
  {type:'message',role:'assistant',content:[{type:'refusal',refusal:'거절',secret:{path:'/Users/private/project/a.png'}}]},
 ])assert.throws(()=>p.buildSiwcTextRequest({...base,input:[{role:'user',content:'상태'},item]}),e=>e.code==='invalid_input');
});
test('completed responses validate nested replay before returning success and redact plaintext while preserving opaque reasoning',async()=>{
 const p=load(),bad=[{type:'reasoning',summary:[{type:'input_image',image_url:'data:image/png;base64,c2VjcmV0'}],encrypted_content:'opaque'},
 {type:'message',role:'assistant',content:[{type:'output_text',text:'안내',private:{path:'/Users/private/project/a.png'}}]},
 {type:'message',content:[{type:'output_text',text:'missing assistant role'}]}];
 for(const item of bad)await assert.rejects(p.readSiwcResponse(bytes(completed([item])),{tools:allow}),e=>e.code==='invalid_input'||e.code==='bad_stream');
 const opaque='signed-provider-opaque-'+ 'A'.repeat(80),output=[{type:'reasoning',summary:[{type:'summary_text',text:'검토 /Users/private/project/a.png sk-secret-1234567890'}],encrypted_content:opaque},
 {type:'message',role:'assistant',content:[{type:'output_text',text:'완료 /Users/private/project/a.png',annotations:[]},{type:'refusal',refusal:'sk-secret-1234567890'}]},
 {type:'function_call',namespace:'modu_vision',name:'read_status',call_id:'call_a',arguments:JSON.stringify({file:'/Users/private/project/a.png',key:'sk-secret-1234567890'})}];
 const answer=await p.readSiwcResponse(bytes(completed(output)),{tools:allow});assert.equal(answer.replay[0].encrypted_content,opaque);assert.equal(answer.replay[0].summary[0].text,'검토 a.png [가림]');assert.equal(answer.text,'완료 a.png[가림]');assert.deepEqual(answer.toolCalls[0].arguments,{file:'a.png',key:'[가림]'});assert.deepEqual(JSON.parse(answer.replay[2].arguments),answer.toolCalls[0].arguments);
});
test('documented assistant phase survives completed output and the next typed or plain-text history request',async()=>{
 const p=load();for(const phase of [undefined,null,'commentary','final_answer']){
  const optional=phase===undefined?{}:{phase},message={type:'message',id:'msg_synthetic',role:'assistant',status:'completed',...optional,content:[{type:'output_text',text:'완료',annotations:[],logprobs:[]}]};
  const answer=await p.readSiwcResponse(bytes(completed([message])),{tools:[]});assert.equal(answer.text,'완료');assert.deepEqual(answer.replay,[message]);
  const next=p.buildSiwcTextRequest({model:'synthetic-model',input:[{role:'user',content:'상태'},...answer.replay,{role:'user',content:'다음'}],tools:[]});assert.deepEqual(next.input[1],message);
  const plain={role:'assistant',content:'완료',...optional},history=p.buildSiwcTextRequest({model:'synthetic-model',input:[plain],tools:[]});assert.deepEqual(history.input,[plain]);
 }
});
test('assistant phase rejects arbitrary values, non-assistant roles and unrelated message fields',async()=>{
 const p=load();for(const phase of ['', 'analysis', 'final', 0, false, [], {}]){
  assert.throws(()=>p.buildSiwcTextRequest({model:'m',input:[{role:'assistant',content:'안내',phase}],tools:[]}),e=>e.code==='invalid_input');
  await assert.rejects(p.readSiwcResponse(bytes(completed([{type:'message',role:'assistant',phase,content:[{type:'output_text',text:'안내'}]}])),{tools:[]}),e=>e.code==='invalid_input');
 }
 for(const role of ['user','developer'])for(const phase of [null,'commentary','final_answer'])assert.throws(()=>p.buildSiwcTextRequest({model:'m',input:[{role,content:'안내',phase}],tools:[]}),e=>e.code==='invalid_input');
 const extra={type:'message',role:'assistant',phase:'final_answer',content:[{type:'output_text',text:'안내'}],private:'forbidden'};
 assert.throws(()=>p.buildSiwcTextRequest({model:'m',input:[extra],tools:[]}),e=>e.code==='invalid_input');await assert.rejects(p.readSiwcResponse(bytes(completed([extra])),{tools:[]}),e=>e.code==='invalid_input');
});
test('documented empty reasoning content survives completed output and replay',async()=>{
 const p=load();for(const optional of [{content:[]},{content:[],encrypted_content:'synthetic-opaque'}]){
  const output=[{type:'reasoning',id:'reason_synthetic',summary:[],...optional},{type:'message',role:'assistant',content:[{type:'output_text',text:'완료'}]}];
  const answer=await p.readSiwcResponse(bytes(completed(output)),{tools:[]});assert.equal(answer.text,'완료');assert.deepEqual(answer.replay,output);
  const next=p.buildSiwcTextRequest({model:'synthetic-model',input:[{role:'user',content:'상태'},...answer.replay,{role:'user',content:'다음'}],tools:[]});assert.deepEqual(next.input.slice(1,3),output);
 }
});
test('documented nullable encrypted content survives completed output and replay',async()=>{
 const p=load();for(const optional of [{encrypted_content:null},{content:[],encrypted_content:null}]){
  const output=[{type:'reasoning',id:'reason_synthetic',summary:[],...optional},{type:'message',role:'assistant',content:[{type:'output_text',text:'완료'}]}];
  const answer=await p.readSiwcResponse(bytes(completed(output)),{tools:[]});assert.equal(answer.text,'완료');assert.deepEqual(answer.replay,output);
  const next=p.buildSiwcTextRequest({model:'synthetic-model',input:[{role:'user',content:'상태'},...answer.replay,{role:'user',content:'다음'}],tools:[]});assert.deepEqual(next.input.slice(1,3),output);
 }
});
test('reasoning defaults keep nonempty or nonarray content, invalid encrypted data and unknown output rejected',async()=>{
 const p=load(),base={type:'reasoning',summary:[]};for(const item of [
  ...[null,'',{},[{type:'reasoning_text',text:'unsupported'}],[{type:'input_image',image_url:'data:image/png;base64,c2VjcmV0'}]].map(content=>({...base,content})),
  ...[0,false,{},[], 'x'.repeat(100001)].map(encrypted_content=>({...base,encrypted_content})),
  {...base,content:[],encrypted_content:null,private:'forbidden'},
  {type:'unknown_output',content:[]},
 ]){
  assert.throws(()=>p.buildSiwcTextRequest({model:'m',input:[{role:'user',content:'상태'},item],tools:[]}),e=>e.code==='invalid_input');
  await assert.rejects(p.readSiwcResponse(bytes(completed([item]),1024),{tools:[]}),e=>e.code==='invalid_input');
 }
});

test('PKCE is S256 without padding; each authorization attempt uses fresh values and no network',()=>{
 const p=load();assert.equal(p.pkceChallenge('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'),'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM');
 const options={hostId:HOST,appName:'Modu Vision',redirectUri:'http://127.0.0.1:54321/auth/callback',now:()=>NOW};
 const a=p.createAuthorizationAttempt(options),b=p.createAuthorizationAttempt(options),url=new URL(a.authorizationUrl);
 assert.equal(url.origin,'https://auth.openai.com');assert.equal(url.searchParams.get('client_id'),'dynamic_agent_client');assert.equal(url.searchParams.get('resource'),'https://api.openai.com/v1');assert.equal(url.searchParams.get('code_challenge_method'),'S256');assert.equal(url.searchParams.get('agent_name_hint'),'Modu Vision');assert.notEqual(url.searchParams.get('state'),new URL(b.authorizationUrl).searchParams.get('state'));assert.notEqual(a.nonce,b.nonce);assert.equal(url.searchParams.has('code_verifier'),false);
 const callback=options.redirectUri+'?state='+url.searchParams.get('state')+'&code=synthetic-code&client_id='+CLIENT;
 const grant=a.acceptCallback(callback);assert.equal(grant.form.get('redirect_uri'),options.redirectUri);assert.equal(p.pkceChallenge(grant.form.get('code_verifier')),url.searchParams.get('code_challenge'));assert.equal(grant.form.get('client_id'),CLIENT);assert.equal(grant.form.has('client_secret'),false);assert.throws(()=>a.acceptCallback(callback),e=>e.code==='attempt_used');
});
test('callback validates exact loopback URI, single state, issued client and expiry before exchange',()=>{
 const p=load();let now=NOW;const options={hostId:HOST,appName:'Modu Vision',redirectUri:'http://127.0.0.1:43210/auth/callback',clientId:CLIENT,now:()=>now};
 for(const redirectUri of ['http://localhost:43210/auth/callback','https://127.0.0.1:43210/auth/callback','http://127.0.0.1:43210/callback'])assert.throws(()=>p.createAuthorizationAttempt({...options,redirectUri}));
 const attempt=p.createAuthorizationAttempt(options),url=new URL(attempt.authorizationUrl);assert.equal(url.searchParams.has('agent_name_hint'),false);
 const good=options.redirectUri+'?state='+url.searchParams.get('state')+'&code=c';
 for(const mutate of [s=>s.replace('43210','43211'),s=>s.replace('/auth/callback','/callback'),s=>s+'&state=duplicate',s=>s+'&client_id=oaiapp_other',s=>s.replace(/state=[^&]+/,'state=wrong'),s=>s+'#fragment']){
  const separate=p.createAuthorizationAttempt(options),base=options.redirectUri+'?state='+new URL(separate.authorizationUrl).searchParams.get('state')+'&code=c';assert.throws(()=>separate.acceptCallback(mutate(base)));
 }
 assert.equal(attempt.acceptCallback(good).clientId,CLIENT);
 const expired=p.createAuthorizationAttempt(options);now+=600001;assert.throws(()=>expired.acceptCallback(good),e=>e.code==='attempt_expired');
});
test('denial is state-bound and never produces a grant; callback scope cannot enable plan use',()=>{
 const p=load(),a=p.createAuthorizationAttempt({hostId:HOST,appName:'Modu Vision',redirectUri:'http://127.0.0.1:54321/auth/callback',now:()=>NOW}),state=new URL(a.authorizationUrl).searchParams.get('state');
 assert.throws(()=>a.acceptCallback('http://127.0.0.1:54321/auth/callback?error=access_denied&state='+state),e=>e.code==='access_denied');
 const noPlan=p.acceptAuthorizationTokens(token({scope:'openid profile email'}),{clientId:CLIENT,nonce:'expected-nonce',jwks:{keys:[publicKey]},nowMs:NOW});assert.equal(noPlan.planUsageEnabled,false);
});
test('ID token requires a trusted signature, issuer, audience, expiry, nonce and selected subject',()=>{
 const p=load(),expect={clientId:CLIENT,nonce:'expected-nonce',nowMs:NOW};assert.equal(p.verifySiwcIdToken(jwt(),{keys:[publicKey]},expect).subject,'synthetic-user');
 for(const change of [{iss:'https://evil.invalid'},{aud:'oaiapp_other'},{exp:NOW/1000},{nonce:'other'},{sub:''}])assert.throws(()=>p.verifySiwcIdToken(jwt(change),{keys:[publicKey]},expect));
 assert.throws(()=>p.verifySiwcIdToken(jwt(),{keys:[]},expect));assert.throws(()=>p.verifySiwcIdToken(jwt(),{keys:[publicKey]},{...expect,expectedSubject:'someone-else'}));
 const segments=jwt().split('.');segments[1]=Buffer.from(JSON.stringify({sub:'forged'})).toString('base64url');assert.throws(()=>p.verifySiwcIdToken(segments.join('.'),{keys:[publicKey]},expect));
});
test('ES256 trusted P-256 keys verify JOSE signatures and reject altered signatures',()=>{
 const p=load(),ec=crypto.generateKeyPairSync('ec',{namedCurve:'prime256v1'}),jwk={...ec.publicKey.export({format:'jwk'}),kid:'synthetic-ec',alg:'ES256'},head=Buffer.from(JSON.stringify({alg:'ES256',kid:jwk.kid})).toString('base64url'),payload=jwt().split('.')[1],signed=head+'.'+payload;
 const signature=crypto.sign('sha256',Buffer.from(signed),{key:ec.privateKey,dsaEncoding:'ieee-p1363'}),expect={clientId:CLIENT,nonce:'expected-nonce',nowMs:NOW};
 assert.equal(p.verifySiwcIdToken(signed+'.'+signature.toString('base64url'),{keys:[jwk]},expect).subject,'synthetic-user');signature[0]^=1;assert.throws(()=>p.verifySiwcIdToken(signed+'.'+signature.toString('base64url'),{keys:[jwk]},expect));
});
test('same-session refresh is single-flight and atomically rotates token/scope/expiry without interpreting earliest_refresh_at',async()=>{
 const p=load(),calls=[];let release;const session=new p.SiwcTokenSession(credentials(p),{jwks:{keys:[publicKey]},now:()=>NOW,transport:request=>{calls.push(request);return new Promise(resolve=>{release=resolve;});}});
 const a=session.refresh(),b=session.refresh();assert.equal(calls.length,1);assert.equal(calls[0].form.get('refresh_token'),'synthetic-refresh');assert.equal(calls[0].form.has('scope'),false);assert.equal(calls[0].form.get('resource'),'https://api.openai.com/v1');
 release({status:200,body:token({access_token:'rotated-access',refresh_token:'rotated-refresh',scope:'openid',earliest_refresh_at:{unknown:'opaque'}})});
 assert.equal((await a).refreshToken,'rotated-refresh');await b;assert.equal(session.credentials().accessToken,'rotated-access');assert.equal(session.credentials().planUsageEnabled,false);assert.equal(session.credentials().expiresAt,NOW+3600000);
 const c=session.refresh();assert.equal(calls[1].form.get('refresh_token'),'rotated-refresh');release({status:200,body:token({refresh_token:'latest-refresh'})});await c;
});
test('transient refresh and invalid-client failures preserve credentials; unusable refresh clears only tokens',async()=>{
 const p=load();for(const [status,code,cleared,recovery] of [[503,'subscription_sharing_user_unavailable',false,'retry_later'],[401,'subscription_sharing_invalid_user',false,'inspect_credentials'],[400,'invalid_client',false,'configure_client'],[400,'invalid_grant',true,'sign_in']]){
  const session=new p.SiwcTokenSession(credentials(p),{jwks:{keys:[publicKey]},transport:async()=>({status,body:{error:code,error_description:'synthetic-refresh must never leak'}})});
  await assert.rejects(session.refresh(),e=>e.recovery===recovery&&!e.message.includes('must never leak'));assert.equal(session.credentials()===null,cleared);assert.equal(session.state().clientId,CLIENT);
 }
});
test('sign-out stops requests, posts discovery-provided revoke form, clears local tokens and retains registration',async()=>{
 const p=load(),calls=[];const session=new p.SiwcTokenSession(credentials(p),{jwks:{keys:[publicKey]},stopRequests:()=>calls.push('stop'),transport:async request=>{calls.push(request);return{status:200,body:null};}});
 const result=await session.signOut({revocation_endpoint:'https://auth.openai.com/oauth/revoke'});assert.equal(calls[0],'stop');assert.equal(calls[1].form.get('token_type_hint'),'refresh_token');assert.equal(calls[1].form.get('client_id'),CLIENT);assert.equal(result.remoteRevocationConfirmed,true);assert.equal(session.credentials(),null);assert.equal(session.state().clientId,CLIENT);await assert.rejects(session.refresh(),e=>e.code==='signed_out');
 const unavailable=new p.SiwcTokenSession(credentials(p),{jwks:{keys:[publicKey]},transport:async()=>({status:503})});assert.equal((await unavailable.signOut({revocation_endpoint:'https://auth.openai.com/oauth/revoke'})).remoteRevocationConfirmed,false);assert.equal(unavailable.credentials(),null);
});
test('sign-out waits for a known rotating replacement and cannot be rehydrated by a late refresh',async()=>{
 const p=load(),calls=[];let release;const s=new p.SiwcTokenSession(credentials(p),{jwks:{keys:[publicKey]},now:()=>NOW,transport:request=>{calls.push(request);return request.form.has('grant_type')?new Promise(resolve=>{release=resolve;}):Promise.resolve({status:200});}});
 const refreshing=s.refresh();const rejected=assert.rejects(refreshing,e=>e.code==='signed_out');const signingOut=s.signOut({revocation_endpoint:'https://auth.openai.com/oauth/revoke'});
 release({status:200,body:token({refresh_token:'rotated-for-revoke'})});assert.equal((await signingOut).remoteRevocationConfirmed,true);await rejected;assert.equal(calls[1].form.get('token'),'rotated-for-revoke');assert.equal(s.credentials(),null);
});
test('an unobserved refresh outcome remains uncertain after timeout and cannot claim remote sign-out confirmation',async()=>{
 const p=load();let release;const s=new p.SiwcTokenSession(credentials(p),{jwks:{keys:[publicKey]},now:()=>NOW,transport:request=>request.form.has('grant_type')?new Promise(resolve=>{release=resolve;}):Promise.resolve({status:200})});
 await assert.rejects(s.refresh({timeoutMs:10}),e=>e.code==='timeout');
 assert.equal((await s.signOut({revocation_endpoint:'https://auth.openai.com/oauth/revoke'})).remoteRevocationConfirmed,false);
 release({status:200,body:token({refresh_token:'late-unknown-rotation'})});await Promise.resolve();assert.equal(s.credentials(),null);
});
test('cancelling one refresh waiter keeps the shared rotation observable and cancellation never waits for a broken revoke',async()=>{
 const p=load();let release;const s=new p.SiwcTokenSession(credentials(p),{jwks:{keys:[publicKey]},now:()=>NOW,transport:()=>new Promise(resolve=>{release=resolve;})});
 const abort=new AbortController(),cancelled=s.refresh({signal:abort.signal}),other=s.refresh();abort.abort();await assert.rejects(cancelled,e=>e.code==='cancelled');release({status:200,body:token({refresh_token:'observed'})});assert.equal((await other).refreshToken,'observed');
 const stopped=new AbortController(),out=s.signOut({revocation_endpoint:'https://auth.openai.com/oauth/revoke'},{signal:stopped.signal,timeoutMs:1000});stopped.abort();assert.equal((await out).remoteRevocationConfirmed,false);assert.equal(s.credentials(),null);
});
test('text request uses namespace functions and omits unsupported SIWC fields',()=>{
 const p=load(),body=p.buildSiwcTextRequest({model:'synthetic-model',instructions:'instructions',input:[{role:'user',content:'상태를 읽어줘'}],tools:allow});
 assert.equal(body.store,false);assert.equal(body.stream,true);assert.deepEqual(body.tools,[{type:'namespace',name:'modu_vision',description:'Modu Vision application tools',tools:allow.map(t=>({type:'function',...t}))}]);
 for(const key of ['temperature','previous_response_id','max_output_tokens','background','metadata'])assert.equal(Object.hasOwn(body,key),false);
 assert.throws(()=>p.buildSiwcTextRequest({model:'m',input:[{role:'system',content:'no'}],tools:allow}));
});
test('completed namespace calls and their call_id outputs can be replayed without server-side conversation state',async()=>{
 const p=load(),output=[{type:'reasoning',id:'r',summary:[],encrypted_content:'synthetic-encrypted'},{type:'function_call',id:'f',namespace:'modu_vision',name:'read_status',call_id:'call_a',arguments:'{}'}];
 const answer=await p.readSiwcResponse(bytes(completed(output)),{tools:allow});
 const body=p.buildSiwcTextRequest({model:'synthetic-model',input:[{role:'user',content:'상태'},...answer.replay,{type:'function_call_output',call_id:'call_a',output:'{"stage":3}'}],tools:allow});
 assert.deepEqual(body.input.slice(1,3),output);assert.equal(body.input[3].call_id,'call_a');assert.deepEqual(body.include,['reasoning.encrypted_content']);
 assert.throws(()=>p.buildSiwcTextRequest({model:'m',input:[{type:'function_call_output',call_id:'unknown',output:'x'}],tools:allow}),e=>e.code==='invalid_input');
});
test('authorization token acceptance cannot skip the pending nonce and streaming temporary errors preserve retry recovery',async()=>{
 const p=load();for(const nonce of [undefined,''])assert.throws(()=>p.acceptAuthorizationTokens(token(),{clientId:CLIENT,nonce,jwks:{keys:[publicKey]},nowMs:NOW}),e=>e.code==='invalid_nonce');
 await assert.rejects(p.readSiwcResponse(bytes(event('response.failed',{response:{error:{code:'subscription_sharing_usage_unavailable'}}})),{tools:allow}),e=>e.recovery==='retry_later');
});
test('a matching callback route consumes the attempt even when state validation fails',()=>{
 const p=load(),redirectUri='http://127.0.0.1:54321/auth/callback',a=p.createAuthorizationAttempt({hostId:HOST,appName:'Modu Vision',redirectUri,now:()=>NOW}),state=new URL(a.authorizationUrl).searchParams.get('state');
 assert.throws(()=>a.acceptCallback(redirectUri+'?state=wrong&code=c&client_id='+CLIENT),e=>e.code==='invalid_state');
 assert.throws(()=>a.acceptCallback(redirectUri+'?state='+state+'&code=c&client_id='+CLIENT),e=>e.code==='attempt_used');
});
test('SSE survives every UTF-8 byte and CRLF boundary; only completed output yields text and allowlisted namespace calls',async()=>{
 const p=load(),output=[{type:'message',role:'assistant',content:[{type:'output_text',text:'완료했습니다'}]},{type:'function_call',namespace:'modu_vision',name:'read_status',call_id:'call_a',arguments:'{}'}];
 const stream=': keepalive\r\n\r\n'+event('response.output_text.delta',{delta:'partial'})+completed(output);
 const result=await p.readSiwcResponse(bytes(stream,1),{tools:allow});assert.equal(result.text,'완료했습니다');assert.deepEqual(result.toolCalls,[{id:'call_a',namespace:'modu_vision',name:'read_status',arguments:{}}]);assert.deepEqual(result.replay,output);
});
test('SIWC compatibility accepts validated done-only text at an empty completed output and preserves redacted replay',async()=>{
 const p=load(),item=doneMessage('msg_done','완료 /Users/private/project/a.png sk-secret-1234567890');
 const stream=event('response.created',{response:{id:'resp_synthetic'}})+event('response.output_text.delta',{delta:'discarded'})+doneItem(0,item)+completed([]);
 const answer=await p.readSiwcResponse(bytes(stream,1),{tools:[]});assert.equal(answer.text,'완료 a.png [가림]');assert.equal(answer.replay[0].phase,'final_answer');assert.equal(JSON.stringify(answer).includes('/Users/private'),false);assert.equal(JSON.stringify(answer).includes('sk-secret-'),false);
 const next=p.buildSiwcTextRequest({model:'m',input:[{role:'user',content:'상태'},...answer.replay,{role:'user',content:'다음'}],tools:[]});assert.deepEqual(next.input[1],answer.replay[0]);
});
test('SIWC compatibility orders fully done snapshots by contiguous index and binds added identity',async()=>{
 const p=load(),a=doneMessage('msg_a','A'),b=doneMessage('msg_b','B');
 const stream=addedItem(1,{id:'msg_b',type:'message',status:'in_progress'})+addedItem(0,{id:'msg_a',type:'message',status:'in_progress'})+doneItem(1,b)+doneItem(0,a)+completed([]);
 const answer=await p.readSiwcResponse(bytes(stream),{tools:[]});assert.equal(answer.text,'AB');assert.deepEqual(answer.replay.map(row=>row.id),['msg_a','msg_b']);
 const full=Array.from({length:128},(_,i)=>doneItem(i,doneMessage('msg_'+i))).reverse().join('');assert.equal((await p.readSiwcResponse(bytes(full+completed([]),1024),{tools:[]})).replay.length,128);
});
test('SIWC done tool snapshots validate once per candidate and replay only allowlisted calls',async()=>{
 const p=load(),item={id:'fc_done',type:'function_call',status:'completed',namespace:'modu_vision',name:'read_status',call_id:'call_done',arguments:'{}'};
 for(const output of [[],[item]]){const answer=await p.readSiwcResponse(bytes(doneItem(0,item)+completed(output)),{tools:allow});assert.equal(answer.text,'');assert.equal(answer.toolCalls.length,1);assert.equal(answer.toolCalls[0].name,'read_status');
  const next=p.buildSiwcTextRequest({model:'m',input:[{role:'user',content:'상태'},...answer.replay,{type:'function_call_output',call_id:'call_done',output:'준비됨'}],tools:allow});assert.equal(next.input[2].call_id,'call_done');
 }
 await assert.rejects(p.readSiwcResponse(bytes(doneItem(0,item)+completed([])),{tools:[]}),e=>e.code==='invalid_tool_call');
 await assert.rejects(p.readSiwcResponse(bytes(doneItem(0,item)+doneItem(1,{...item,id:'fc_other'})+completed([])),{tools:allow}),e=>e.code==='invalid_tool_call');
});
test('done snapshots require bounded integer indexes and bounded nonempty item ids',async()=>{
 const p=load(),item=doneMessage('msg_done');
 for(const index of [undefined,null,-1,0.5,'0',128,Number.MAX_SAFE_INTEGER,Number.MAX_SAFE_INTEGER+1])await assert.rejects(p.readSiwcResponse(bytes(doneItem(index,item)+completed([])),{tools:[]}),e=>e.code==='bad_stream');
 for(const id of [undefined,null,'','x'.repeat(201)])await assert.rejects(p.readSiwcResponse(bytes(doneItem(0,{...item,id})+completed([])),{tools:[]}),e=>e.code==='bad_stream');
 assert.equal((await p.readSiwcResponse(bytes(doneItem(0,doneMessage('x'.repeat(200)))+completed([])),{tools:[]})).text,'완료');
});
test('done snapshots reject duplicate conflicting sparse or unfinished item lifecycles even with a nonempty terminal',async()=>{
 const p=load(),a=doneMessage('msg_a'),b=doneMessage('msg_b'),added={id:'msg_a',type:'message',status:'in_progress'};
 const streams=[addedItem(0,added)+doneItem(0,b),addedItem(0,added)+doneItem(0,{id:'msg_a',type:'reasoning',summary:[]}),doneItem(0,a)+doneItem(0,a),doneItem(0,a)+doneItem(0,b),doneItem(0,a)+doneItem(1,a),addedItem(0,added)+addedItem(0,added),doneItem(0,a)+addedItem(0,added),doneItem(1,b),doneItem(0,a)+doneItem(2,b),addedItem(0,added),doneItem(0,a)+addedItem(1,{id:'msg_b',type:'message'})];
 for(const stream of streams)for(const output of [[],[a,b]])await assert.rejects(p.readSiwcResponse(bytes(stream+completed(output)),{tools:[]}),e=>e.code==='bad_stream');
});
test('done snapshots keep strict nested fields phase namespace arguments and output type validation',async()=>{
 const p=load(),message=doneMessage('msg_bad'),call={id:'fc_bad',type:'function_call',namespace:'modu_vision',name:'read_status',call_id:'call_bad',arguments:'{}'};
 const bad=[{...message,role:'user'},{...message,status:'in_progress'},{...message,phase:'analysis'},{...message,private:'forbidden'},{...message,content:[{type:'input_image',image_url:'data:image/png;base64,c2VjcmV0'}]},{...message,content:[{type:'output_text',text:'안내',private:'forbidden'}]},{...call,namespace:'other'},{...call,name:'delete_everything'},{...call,arguments:'null'},{id:'reason_bad',type:'reasoning',summary:[],content:[{type:'reasoning_text',text:'unsupported'}]},{id:'unknown',type:'unknown_output'}];
 for(const item of bad)await assert.rejects(p.readSiwcResponse(bytes(doneItem(0,item)+completed([])),{tools:allow}),e=>['bad_stream','invalid_input','invalid_tool_call'].includes(e.code));
});
test('terminal completed status and an output array remain mandatory before any done fallback',async()=>{
 const p=load(),item=doneMessage('msg_done');
 for(const response of [null,{status:'completed'},{status:'completed',output:null},{status:'completed',output:{}},{status:'in_progress',output:[]},{status:'incomplete',output:[]}])await assert.rejects(p.readSiwcResponse(bytes(doneItem(0,item)+event('response.completed',{response})),{tools:[]}),e=>e.code==='bad_stream');
 await assert.rejects(p.readSiwcResponse(bytes(doneItem(0,item)+completed([{...item,private:'forbidden'}])),{tools:[]}),e=>e.code==='invalid_input');
});
test('nonempty terminal output is authoritative only when deeply equal to every observed raw done item',async()=>{
 const p=load(),item=doneMessage('msg_done','완료'),reordered={content:[{logprobs:[],annotations:[],text:'완료',type:'output_text'}],phase:'final_answer',status:'completed',role:'assistant',type:'message',id:'msg_done'};
 assert.equal((await p.readSiwcResponse(bytes(doneItem(0,item)+completed([reordered])),{tools:[]})).text,'완료');
 assert.equal((await p.readSiwcResponse(bytes(doneItem(0,item)+completed([item,doneMessage('msg_extra','추가')])),{tools:[]})).text,'완료추가');
 const conflicts=[[item,{...item,id:'msg_other'}],[item,{...item,phase:'commentary'}],[item,doneMessage('msg_done','다른 답변')],[doneMessage('msg_secret','/Users/alpha/a.png sk-secret-1234567890'),doneMessage('msg_secret','/Users/beta/a.png sk-secret-9999999999')],[{id:'reason',type:'reasoning',summary:[],encrypted_content:'opaque-a'},{id:'reason',type:'reasoning',summary:[],encrypted_content:'opaque-b'}],[{id:'fc',type:'function_call',namespace:'modu_vision',name:'read_status',call_id:'call_a',arguments:'{"stage":1}'},{id:'fc',type:'function_call',namespace:'modu_vision',name:'read_status',call_id:'call_a',arguments:'{"stage":2}'}]];
 for(const [done,final] of conflicts)await assert.rejects(p.readSiwcResponse(bytes(doneItem(0,done)+completed([final])),{tools:allow}),e=>e.code==='bad_stream');
 await assert.rejects(p.readSiwcResponse(bytes(doneItem(0,item)+doneItem(1,doneMessage('msg_second'))+completed([item])),{tools:[]}),e=>e.code==='bad_stream');
});
test('created and completed response ids agree when both exist without requiring legacy missing ids',async()=>{
 const p=load(),item=doneMessage('msg_done'),created=id=>event('response.created',{response:{id}});
 await assert.rejects(p.readSiwcResponse(bytes(created('other')+doneItem(0,item)+completed([])),{tools:[]}),e=>e.code==='bad_stream');
 await assert.rejects(p.readSiwcResponse(bytes(created('resp_synthetic')+created('other')+doneItem(0,item)+completed([])),{tools:[]}),e=>e.code==='bad_stream');
 for(const id of [null,'','x'.repeat(201)])await assert.rejects(p.readSiwcResponse(bytes(created(id)+doneItem(0,item)+completed([])),{tools:[]}),e=>e.code==='bad_stream');
 for(const stream of [created('resp_synthetic')+doneItem(0,item)+event('response.completed',{response:{status:'completed',output:[]}}),created(undefined)+doneItem(0,item)+completed([]),event('response.completed',{response:{status:'completed',output:[item]}})])assert.equal((await p.readSiwcResponse(bytes(stream),{tools:[]})).text,'완료');
});
test('done snapshots never bypass failed incomplete malformed EOF cancellation timeout or byte limits',async()=>{
 const p=load(),prefix=doneItem(0,doneMessage('msg_done'));
 const tails=[['','stream_truncated'],['data: [DONE]\n\n','stream_truncated'],[event('response.failed',{response:{error:{code:'subscription_sharing_usage_limit_exceeded'}}})+completed([]),'subscription_sharing_usage_limit_exceeded'],[event('response.incomplete',{response:{status:'incomplete'}})+completed([]),'response_incomplete'],['event: response.completed\ndata: {broken}\n\n','bad_stream']];
 for(const [tail,code] of tails)await assert.rejects(p.readSiwcResponse(bytes(prefix+tail),{tools:[]}),e=>e.code===code);
 const long=event('response.output_text.delta',{delta:'x'.repeat(1000)});
 await assert.rejects(p.readSiwcResponse((async function*(){yield Buffer.from(prefix);yield Buffer.from(long+completed([]));})(),{tools:[],maxBytes:Buffer.byteLength(prefix)+20}),e=>e.code==='response_too_large');
 await assert.rejects(p.readSiwcResponse(bytes(prefix+long+completed([]),256),{tools:[],maxEventBytes:500}),e=>e.code==='response_too_large');
 for(const cancel of [false,true]){let seen,reads=0,returned=false;const ready=new Promise(resolve=>{seen=resolve;}),source={[Symbol.asyncIterator](){return{next(){if(reads++===0)return Promise.resolve({done:false,value:Buffer.from(prefix)});seen();return new Promise(()=>{});},return:async()=>{returned=true;return{done:true};}};}};
  const abort=new AbortController(),flight=p.readSiwcResponse(source,{tools:[],signal:abort.signal,timeoutMs:cancel?1000:10}),rejected=assert.rejects(flight,e=>e.code===(cancel?'cancelled':'timeout'));await ready;if(cancel)abort.abort();await rejected;assert.equal(returned,true);
 }
});
test('partial, DONE, incomplete, failed and malformed streams cannot become a successful response',async()=>{
 const p=load();for(const [stream,code] of [[event('response.output_text.delta',{delta:'partial'}),'stream_truncated'],['data: [DONE]\n\n','stream_truncated'],[event('response.incomplete',{response:{status:'incomplete'}}),'response_incomplete'],[event('response.failed',{response:{error:{code:'subscription_sharing_usage_limit_exceeded',message:'do not echo synthetic token'}}}),'subscription_sharing_usage_limit_exceeded'],['data: {broken}\n\n','bad_stream'],[event('error',{code:'server_error',message:'never echo'}),'server_error']])await assert.rejects(p.readSiwcResponse(bytes(stream),{tools:allow}),e=>e.code===code&&!e.message.includes('echo'));
});
test('foreign/missing namespace, dotted or unknown function, malformed arguments and duplicate call ids refuse all tool output',async()=>{
 const p=load(),base={type:'function_call',namespace:'modu_vision',name:'read_status',call_id:'call_a',arguments:'{}'};
 for(const change of [{namespace:'other'},{namespace:undefined},{name:'modu_vision.read_status'},{name:'delete_everything'},{arguments:'null'},{arguments:'{broken'}])await assert.rejects(p.readSiwcResponse(bytes(completed([{...base,...change}]),1024),{tools:allow}),e=>e.code==='invalid_tool_call');
 await assert.rejects(p.readSiwcResponse(bytes(completed([base,base])),{tools:allow}),e=>e.code==='invalid_tool_call');
});
test('an in-flight request cannot gain a new function through a mutated caller allowlist',async()=>{
 const p=load(),tools=structuredClone(allow);let release;const stream={[Symbol.asyncIterator](){return{next:()=>new Promise(resolve=>{release=resolve;}),return:async()=>({done:true})};}};
 const pending=p.readSiwcResponse(stream,{tools});tools.push({name:'delete_everything',description:'not in original request',parameters:{type:'object'}});
 release({done:false,value:Buffer.from(completed([{type:'function_call',namespace:'modu_vision',name:'delete_everything',call_id:'new',arguments:'{}'}]))});
 await assert.rejects(pending,e=>e.code==='invalid_tool_call');
});
test('stream limits and cancellation are bounded even if the injected iterator ignores abort',async()=>{
 const p=load();await assert.rejects(p.readSiwcResponse(bytes('data: '+ 'x'.repeat(100)),{tools:allow,maxBytes:20}),e=>e.code==='response_too_large');
 let returned=false;const stalled={[Symbol.asyncIterator](){return{next:()=>new Promise(()=>{}),return:async()=>{returned=true;return{done:true};}};}};
 const abort=new AbortController(),pending=p.readSiwcResponse(stalled,{tools:allow,signal:abort.signal,timeoutMs:1000});abort.abort();await assert.rejects(pending,e=>e.code==='cancelled');assert.equal(returned,true);
 await assert.rejects(p.readSiwcResponse(stalled,{tools:allow,timeoutMs:10}),e=>e.code==='timeout');
});
