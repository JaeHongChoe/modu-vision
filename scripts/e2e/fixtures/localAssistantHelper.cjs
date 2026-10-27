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
   const diagnosticQuestion=[...items].reverse().find(row=>row.role==='user'&&String(row.content||'').includes('DIAGNOSTICS_FIXTURE'));
   if(diagnosticQuestion){
    const seed=String(diagnosticQuestion.content).includes('DIAGNOSTICS_FIXTURE_SEED');
    if(seed){
     if(latest?.type==='function_call_output')return text('진단용 계획만 준비했습니다. 아직 승인하거나 실행하지 않았습니다.');
     return output([{type:'function_call',namespace:'modu_vision',name:'propose_task',call_id:'diag_seed_'+ ++seq,
      arguments:JSON.stringify({goal:'진단용 미승인 작업',steps:[{action:'open_step',args:{step:4},label:'사람 확인 후 평가 화면 열기'}]})}]);
    }
    if(latest?.type!=='function_call_output')return output([
     ...['requests','dataset','jobs'].map(section=>({type:'function_call',namespace:'modu_vision',name:'read_app_diagnostics',call_id:'diag_'+section+'_'+ ++seq,arguments:JSON.stringify({section})})),
     {type:'function_call',namespace:'modu_vision',name:'read_task_status',call_id:'diag_task_'+ ++seq,arguments:'{}'},
    ]);
    if(String(latest.call_id).startsWith('diag_fix_plan_'))return text('409 충돌과 빈 JSON 응답을 확인했고, 저장된 작업은 미승인 상태입니다. 새 확인 계획을 제시했습니다. 승인 전에는 자료 변경이나 재실행을 하지 않습니다.');
    const replies=items.filter(row=>row.type==='function_call_output'&&/^diag_(requests|dataset|jobs|task)_/.test(String(row.call_id))).slice(-4);
    if(replies.length!==4)throw Error('Diagnostics fixture requires all four read outputs');
    const values=replies.map(row=>{if(typeof row.output!=='string'||row.output.length>=4000)throw Error('Diagnostics fixture requires bounded JSON');return JSON.parse(row.output);});
    const bySection=section=>values.find(row=>row.section===section),requests=bySection('requests'),data=bySection('dataset'),jobs=bySection('jobs'),task=values.find(row=>Object.hasOwn(row,'task_id'));
    const rows=requests?.requests||[],privacyClean=!['fixture-diagnostics-secret','diagnostics-private.invalid','diagnostics-private@example.invalid','10.77.88.99','/Users/diagnostics-private'].some(value=>JSON.stringify(items).includes(value));
    const safeId=value=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(value)?value:null;
    record({kind:'diagnostics_feedback',tool_names:['read_app_diagnostics:requests','read_app_diagnostics:dataset','read_app_diagnostics:jobs','read_task_status'],
     saw_http_409:rows.some(row=>row.status===409&&row.code==='dataset.SPLIT_CONFLICT'),saw_json_failure:rows.some(row=>row.kind==='json'),
     request_statuses:rows.slice(-8).map(row=>({kind:safeId(row.kind),status:Number.isInteger(row.status)?row.status:null,code:safeId(String(row.code||'').replaceAll('.','_'))})),
     dataset_state:safeId(data?.data?.state),job_snapshot_state:safeId(jobs?.state),jobs_count:Array.isArray(jobs?.jobs)?jobs.jobs.length:null,
     task:task?{task_id:safeId(task.task_id),revision:Number.isSafeInteger(task.revision)?task.revision:null,
      status:['draft','ready','running','waiting_human','waiting_job','paused','failed','completed','cancelled'].includes(task.status)?task.status:null,
      fresh:task.fresh===true,source:['ledger','cache'].includes(task.source)?task.source:null,current_context_matches:task.current_context_matches===true,
      steps_omitted:Number.isSafeInteger(task.steps_omitted)?task.steps_omitted:null}:null,
     output_lengths:replies.map(row=>row.output.length),privacy_clean:privacyClean});
    if(!privacyClean)throw Error('Unsafe fixture diagnostic output');
    if(!rows.some(row=>row.status===409)||!rows.some(row=>row.kind==='json')||!task?.fresh||task.source!=='ledger'||jobs?.state!=='observed')throw Error('Diagnostics fixture did not receive expected current read evidence');
    return output([{type:'message',role:'assistant',content:[{type:'output_text',text:'자료 분할의 409 충돌과 빈 JSON 응답을 읽었습니다. 저장된 작업은 아직 미승인 상태라 실행되지 않았습니다.'}]},
     {type:'function_call',namespace:'modu_vision',name:'propose_task',call_id:'diag_fix_plan_'+ ++seq,
      arguments:JSON.stringify({goal:'자료 오류 확인 계획',steps:[{action:'open_step',args:{step:1},label:'분할 오류와 자료 상태를 사람이 확인'}]})}]);
   }
   if(latest?.type==='function_call_output')return text('제어된 도우미 완료');
   const question=String(latest?.content||'');
   if(question.includes('CANCEL_FIXTURE'))return new Promise((resolve,reject)=>{const cancel=()=>{record({kind:'provider_cancelled'});reject(new DOMException('Cancelled','AbortError'));};if(init.signal?.aborted)cancel();else init.signal.addEventListener('abort',cancel,{once:true});});
   const taskPlan=question.includes('TASK_PLAN_FIXTURE'),taskImport=question.includes('TASK_IMPORT_FIXTURE');
   if(taskPlan||taskImport){
    const steps=taskImport?[
     {action:'configure_training',args:{preset:'fast',device:'cpu',batch_size:8},label:'이어갈 CPU · 배치 8 설정 준비'},
     {action:'import_dataset',args:{},label:'직접 고른 합성 자료 가져오기'},
     {action:'save_split',args:{train_percent:60,val_percent:20,test_percent:20},label:'학습 60% · 검증 20% · 시험 20% 저장'},
     {action:'open_step',args:{step:1},label:'저장한 자료 화면 열기'},
    ]:[
     {action:'save_split',args:{train_percent:70,val_percent:20,test_percent:10},label:'학습 70% · 검증 20% · 시험 10% 저장'},
     {action:'configure_training',args:{preset:'fast',device:'cpu',batch_size:8},label:'CPU · 빠르게 · 배치 8 준비'},
     {action:'open_step',args:{step:3},label:'준비한 학습 화면 열기'},
    ];
    return output([{type:'function_call',namespace:'modu_vision',name:'propose_task',call_id:'call_fixture_'+ ++seq,arguments:JSON.stringify({goal:taskImport?'합성 자료 가져오기와 분할 준비':'합성 자료 분할과 학습 설정 준비',steps})}]);
   }
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
 const choose=async options=>{
  // Only the task-agent spec can arm this one-shot generated native selection. Existing fixtures still cancel.
  const selection=path.join(stateDirectory,'controlled-task-folder.json');
  if(options.title!=='학습 자료 폴더 선택'||!fs.existsSync(selection)){record({kind:'controlled_native_choice',selected:false});return{canceled:true,filePaths:[]};}
  const value=JSON.parse(fs.readFileSync(selection,'utf8')),root=fs.realpathSync(path.dirname(stateDirectory));
  if(typeof value.folder!=='string')throw Error('Controlled task folder is missing');
  const folder=fs.realpathSync(value.folder);
  if(!folder.startsWith(root+path.sep)||!fs.statSync(folder).isDirectory())throw Error('Controlled task folder must belong to the owned workspace');
  fs.unlinkSync(selection);record({kind:'controlled_native_choice',selected:true});return{canceled:false,filePaths:[folder]};
 };
 const {LocalHostService}=load(path.resolve(__dirname,'../../../src/main/localHost/service.ts'));
 const service=new LocalHostService({stateDirectory,origin,encryptor,fetchImpl:provider.fetchImpl,native:{approve:async()=>{record({kind:'controlled_native_approval'});return true;},choose,openPath:async()=>{throw Error('No native path action in assistant fixture');},openExternal:provider.openExternal},distribution:{appVersion:'0.1.0',packaged:false,appPath:stateDirectory,executablePath:path.join(stateDirectory,'never-executed'),platform:process.platform,arch:process.arch,runner:async()=>{throw Error('No OS runner in assistant fixture');}}});
 return {handle:async request=>{record({kind:'host',method:request.method,actor_id:request.actor_id,tab_sha256:crypto.createHash('sha256').update(request.tab_id).digest('hex'),...(request.args?.projectAuthority?{project_authority:request.args.projectAuthority}:{}),...(request.method==='assistant.beginRun'?{run_id:request.args.runId,tool:request.args.tool}:{})});return service.handle(request);},dispose:()=>service.dispose()};
}
function assertPrivateEvidencePolicy(trace){if(trace!=='off')throw Error('Assistant cookie/provider fixture requires trace off before authentication');}
module.exports={controlledProvider,createHelper,assertPrivateEvidencePolicy,ACCESS,REFRESH};
if(require.main===module){const [stateDirectory,origin,ledger]=process.argv.slice(2);const record=row=>fs.appendFileSync(ledger,JSON.stringify(row)+'\n',{mode:0o600});const service=createHelper({stateDirectory,origin,record}),{runProtocol}=load(path.resolve(__dirname,'../../../src/main/localHost/entry.ts'));runProtocol(process.stdin,process.stdout,service,()=>process.exit(0));process.stdout.write(JSON.stringify({event:'ready',protocol:1,platform:process.platform,pid:process.pid})+'\n');}
