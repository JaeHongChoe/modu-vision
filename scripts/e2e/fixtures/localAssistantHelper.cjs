'use strict';
// TEST ONLY: real service/protocol; generated provider network and native I/O.
// No fallback to the real network, real OAuth browser, OS keychain or OS runner.
const crypto=require('node:crypto'),http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const {load}=require('../../../src/main/localHost/testSupport.cjs');
const ISSUER='https://auth.openai.com',API='https://api.openai.com/v1',CLIENT='oaiapp_browser_fixture';
const ACCESS='fixture-browser-access-secret',REFRESH='fixture-browser-refresh-secret';
function controlledProvider(record=()=>{}){
 const pair=crypto.generateKeyPairSync('rsa',{modulusLength:2048}),jwk={...pair.publicKey.export({format:'jwk'}),kid:'browser-fixture-key',alg:'RS256',use:'sig'};
 const attempts=new Map();let seq=0;
 const output=items=>new Response('event: response.completed\ndata: '+JSON.stringify({type:'response.completed',response:{status:'completed',output:items}})+'\n\n',{headers:{'Content-Type':'text/event-stream'}});
 const text=value=>output([{type:'message',role:'assistant',content:[{type:'output_text',text:value}]}]);
 const fetchImpl=async(url,init={})=>{
  url=String(url);const route=new URL(url).pathname;
  if(url===ISSUER+'/.well-known/openid-configuration'){record({kind:'provider',route});return Response.json({issuer:ISSUER,authorization_endpoint:ISSUER+'/api/accounts/authorize',token_endpoint:ISSUER+'/api/accounts/oauth/token',jwks_uri:ISSUER+'/fixture-jwks',revocation_endpoint:ISSUER+'/fixture-revoke'});}
  if(url===ISSUER+'/fixture-jwks'){record({kind:'provider',route});return Response.json({keys:[jwk]});}
  if(url===ISSUER+'/api/accounts/oauth/token'){
   const form=new URLSearchParams(init.body),attempt=attempts.get(form.get('redirect_uri'));
   if(!attempt||form.get('grant_type')!=='authorization_code'||form.get('client_id')!==CLIENT||form.get('code')!=='controlled-code'||crypto.createHash('sha256').update(form.get('code_verifier')||'').digest('base64url')!==attempt.searchParams.get('code_challenge'))throw Error('Controlled PKCE exchange refused');
   const now=Math.floor(Date.now()/1000),head=Buffer.from(JSON.stringify({alg:'RS256',kid:jwk.kid})).toString('base64url'),body=Buffer.from(JSON.stringify({iss:ISSUER,aud:CLIENT,sub:'synthetic-provider-subject',iat:now,exp:now+3600,nonce:attempt.searchParams.get('nonce')})).toString('base64url'),message=head+'.'+body;
   record({kind:'provider',route,pkce_verified:true});return Response.json({access_token:ACCESS,refresh_token:REFRESH,id_token:message+'.'+crypto.sign('sha256',Buffer.from(message),pair.privateKey).toString('base64url'),token_type:'Bearer',expires_in:3600,scope:'openid profile offline_access resource.invoke chatgpt.tokens.use.direct'});
  }
  if(url===ISSUER+'/fixture-revoke'){record({kind:'provider',route});return new Response('',{status:200});}
  if(url===API+'/models'||url===API+'/responses'){
   if(new Headers(init.headers).get('Authorization')!=='Bearer '+ACCESS)throw Error('Controlled provider authentication refused');
   if(url.endsWith('/models')){record({kind:'provider',route});return Response.json({models:[{slug:'fixture-model',display_name:'Controlled fixture model',visibility:'list'}]});}
   const body=JSON.parse(init.body),items=body.input,latest=items.at(-1);record({kind:'provider',route,model:body.model,image:false});
   if(body.model!=='fixture-model'||body.store!==false||body.stream!==true||JSON.stringify(items).includes('input_image'))throw Error('Controlled request shape refused');
   if(latest?.type==='function_call_output')return text('제어된 도우미 완료');
   const question=String(latest?.content||'');
   if(question.includes('CANCEL_FIXTURE'))return new Promise((resolve,reject)=>{const cancel=()=>{record({kind:'provider_cancelled'});reject(new DOMException('Cancelled','AbortError'));};if(init.signal?.aborted)cancel();else init.signal.addEventListener('abort',cancel,{once:true});});
   const write=question.includes('WRITE_FIXTURE'),read=question.includes('READ_FIXTURE');
   if(!write&&!read)throw Error('Unscripted provider operation refused');
   return output([{type:'function_call',namespace:'modu_vision',name:write?'save_split':'read_dataset_summary',call_id:'call_fixture_'+ ++seq,arguments:JSON.stringify(write?{train_percent:70,val_percent:20,test_percent:10}:{})}]);
  }
  record({kind:'blocked_provider_url'});throw Error('Unmatched provider URL refused; no external fetch exists');
 };
 const openExternal=async value=>{
  const auth=new URL(value);if(auth.origin!==ISSUER||auth.pathname!=='/api/accounts/authorize'||auth.searchParams.get('code_challenge_method')!=='S256')throw Error('No real OAuth browser is permitted');
  const callback=new URL(auth.searchParams.get('redirect_uri'));if(callback.protocol!=='http:'||callback.hostname!=='127.0.0.1'||!callback.port||callback.pathname!=='/auth/callback'||callback.username||callback.password)throw Error('Nonloopback callback refused');
  attempts.set(callback.href,auth);callback.search=new URLSearchParams({state:auth.searchParams.get('state'),code:'controlled-code',client_id:CLIENT}).toString();
  record({kind:'synthetic_oauth_open',loopback_callback:true});
  // Complete only the real listener opened by this service; never open an OS browser.
  setImmediate(()=>{const request=http.get(callback,response=>{response.resume();record({kind:'synthetic_callback',status:response.statusCode});});request.setTimeout(3000,()=>request.destroy());request.on('error',()=>record({kind:'synthetic_callback_failed'}));});
 };
 return {fetchImpl,openExternal};
}
function createHelper({stateDirectory,origin,record=()=>{}}){
 const provider=controlledProvider(record),key=crypto.randomBytes(32),encryptor={isAvailable:()=>true,encrypt(plain){const iv=crypto.randomBytes(12),cipher=crypto.createCipheriv('aes-256-gcm',key,iv),body=Buffer.concat([cipher.update(plain),cipher.final()]);return Buffer.concat([iv,body,cipher.getAuthTag()]);},decrypt(raw){const decipher=crypto.createDecipheriv('aes-256-gcm',key,raw.subarray(0,12));decipher.setAuthTag(raw.subarray(-16));return Buffer.concat([decipher.update(raw.subarray(12,-16)),decipher.final()]).toString();}};
 const {LocalHostService}=load(path.resolve(__dirname,'../../../src/main/localHost/service.ts'));
 const service=new LocalHostService({stateDirectory,origin,encryptor,fetchImpl:provider.fetchImpl,native:{approve:async()=>{record({kind:'controlled_native_approval'});return true;},choose:async()=>({canceled:true,filePaths:[]}),openPath:async()=>{throw Error('No native path action in assistant fixture');},openExternal:provider.openExternal},distribution:{appVersion:'0.1.0',packaged:false,appPath:stateDirectory,executablePath:path.join(stateDirectory,'never-executed'),platform:process.platform,arch:process.arch,runner:async()=>{throw Error('No OS runner in assistant fixture');}}});
 return {handle:async request=>{record({kind:'host',method:request.method,actor_id:request.actor_id,tab_sha256:crypto.createHash('sha256').update(request.tab_id).digest('hex'),...(request.args?.projectAuthority?{project_authority:request.args.projectAuthority}:{}),...(request.method==='assistant.beginRun'?{run_id:request.args.runId,tool:request.args.tool}:{})});return service.handle(request);},dispose:()=>service.dispose()};
}
function assertPrivateEvidencePolicy(trace){if(trace!=='off')throw Error('Assistant cookie/provider fixture requires trace off before authentication');}
module.exports={controlledProvider,createHelper,assertPrivateEvidencePolicy,ACCESS,REFRESH};
if(require.main===module){const [stateDirectory,origin,ledger]=process.argv.slice(2);const record=row=>fs.appendFileSync(ledger,JSON.stringify(row)+'\n',{mode:0o600});const service=createHelper({stateDirectory,origin,record}),{runProtocol}=load(path.resolve(__dirname,'../../../src/main/localHost/entry.ts'));runProtocol(process.stdin,process.stdout,service,()=>process.exit(0));process.stdout.write(JSON.stringify({event:'ready',protocol:1,platform:process.platform,pid:process.pid})+'\n');}
