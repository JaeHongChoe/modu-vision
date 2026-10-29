'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto');
const {controlledProvider,createHelper,assertPrivateEvidencePolicy,ACCESS,REFRESH}=require('../e2e/fixtures/localAssistantHelper.cjs');
test('controlled provider cannot fall through to real network or nonloopback OAuth callbacks',async()=>{const records=[],p=controlledProvider(row=>records.push(row));await assert.rejects(p.fetchImpl('https://example.com/anything'),/Unmatched/);await assert.rejects(p.fetchImpl('https://api.openai.com/v1/responses',{headers:{Authorization:'Bearer wrong'}}),/authentication/);await assert.rejects(p.openExternal('https://auth.openai.com/api/accounts/authorize?code_challenge_method=S256&redirect_uri=https%3A%2F%2Fevil.invalid%2Fauth%2Fcallback'),/Nonloopback/);assert(!JSON.stringify(records).includes(ACCESS));});
test('real helper completes generated PKCE login, lists models and cancels provider work without exposing generated secrets',async t=>{const root=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'browser-assistant-helper-'))),records=[],h=createHelper({stateDirectory:path.join(root,'host'),origin:'https://localhost:8443',record:row=>records.push(row)});t.after(()=>{h.dispose();fs.rmSync(root,{recursive:true,force:true});});let seq=0;const call=async(method,args={})=>{const row=await h.handle({id:'r'+ ++seq,actor_id:'fixture-actor',session_id:'fixture-session',tab_id:'fixture-tab',method,args});assert.equal(row.ok,true,JSON.stringify(row));return row.result;};await call('host.approve',{origin:'https://localhost:8443',accountLabel:'fixture only'});await call('assistant.chatgpt.begin',{input:{termsAccepted:true}});let state;for(let i=0;i<100;i++){state=await call('assistant.getState');if(state.chatgpt.accounts.length)break;await new Promise(r=>setTimeout(r,10));}assert.equal(state.chatgpt.accounts[0].signedIn,true);assert(records.some(r=>r.pkce_verified));const account=state.chatgpt.accounts[0].recordId;assert.equal(state.chatgpt.accounts[0].model,'');const models=await call('assistant.listModels',{provider:'chatgpt'});assert.equal(models.ok,true);assert.deepEqual(models.models,[{id:'fixture-model',label:'Controlled fixture model',capabilities:{imageInput:null,reasoningEfforts:null,source:'unknown'}}]);await call('assistant.chatgpt.selectModel',{recordId:account,model:'fixture-model'});const input={requestId:'req_0123456789abcdef',messages:[{role:'user',text:'CANCEL_FIXTURE'}],context:{},tools:[]};const pending=call('assistant.send',{input,projectAuthority:{epoch:0,scope:'local:'+'0'.repeat(64)}});for(let i=0;i<100&&!records.some(r=>r.route==='/v1/responses');i++)await new Promise(r=>setTimeout(r,5));assert.equal(await call('assistant.cancel',{requestId:input.requestId}),true);assert.equal((await pending).error.code,'cancelled');assert(records.some(r=>r.kind==='provider_cancelled'));for(const secret of [ACCESS,REFRESH]){assert(!JSON.stringify(state).includes(secret));assert(!JSON.stringify(records).includes(secret));const files=fs.readdirSync(path.join(root,'host','actors'));assert.equal(files.length,1);const accountFile=path.join(root,'host','actors',files[0],'chatgpt','chatgpt-sessions.json');assert(fs.existsSync(accountFile));assert(!fs.readFileSync(accountFile,'utf8').includes(secret));}});

test('assistant fixture refuses unsafe raw trace recording before any authentication or helper starts',()=>{assert.doesNotThrow(()=>assertPrivateEvidencePolicy('off'));for(const trace of ['on','retain-on-failure','on-first-retry',{mode:'retain-on-failure'},undefined])assert.throws(()=>assertPrivateEvidencePolicy(trace),/requires trace off before authentication/);});

test('recovery plan fixture has a distinct goal while preserving the original action plan',async()=>{
 const provider=controlledProvider(()=>{});
 const plan=async question=>{const r=await provider.fetchImpl('https://api.openai.com/v1/responses',{headers:{Authorization:'Bearer '+ACCESS},body:JSON.stringify({model:'fixture-model',store:false,stream:true,input:[{role:'user',content:question}]})});
  return JSON.parse(JSON.parse((await r.text()).split('data: ')[1]).response.output[0].arguments);};
 const original=await plan('TASK_PLAN_FIXTURE'),recovery=await plan('TASK_PLAN_FIXTURE_RECOVERY');
 assert.equal(original.goal,'합성 자료 분할과 학습 설정 준비');
 assert.equal(recovery.goal,'이전 계획 보관 뒤 새 합성 자료 준비');
 assert.deepEqual(recovery.steps,original.steps);
});

test('diagnostics marker reads bounded status then proposes an unapproved follow-up without logging raw output',async()=>{
 const records=[],provider=controlledProvider(row=>records.push(row));
 const send=async input=>{const r=await provider.fetchImpl('https://api.openai.com/v1/responses',{headers:{Authorization:'Bearer '+ACCESS},body:JSON.stringify({model:'fixture-model',store:false,stream:true,input})});
  return JSON.parse((await r.text()).split('data: ')[1]).response.output;};
 const input=[{role:'user',content:'DIAGNOSTICS_FIXTURE_READ 원인과 저장 작업 상태를 확인해 주세요.'}];
 const calls=await send(input);assert.deepEqual(calls.map(row=>[row.name,JSON.parse(row.arguments)]),[
  ['read_app_diagnostics',{section:'requests'}],['read_app_diagnostics',{section:'dataset'}],['read_app_diagnostics',{section:'jobs'}],['read_task_status',{}],
 ]);
 const data=[{section:'requests',requests:[{kind:'http',status:409,code:'dataset.SPLIT_CONFLICT',message:'정제된 오류'},{kind:'json',status:null,code:null,message:'Unexpected end of JSON input'}]},
  {section:'dataset',data:{state:'current',images:20},errors:[{message:'Unexpected end of JSON input'}]},
  {section:'jobs',state:'observed',jobs:[],omitted:0},
  {task_id:'task_'+'1'.repeat(32),revision:1,status:'draft',fresh:true,source:'ledger',current_context_matches:true,steps:[{status:'pending'}],steps_omitted:0}];
 const outputs=calls.map((row,i)=>({type:'function_call_output',call_id:row.call_id,output:JSON.stringify(data[i])}));
 const followup=await send([...input,...calls,...outputs]);
 assert.equal(followup[0].type,'message');assert.equal(followup[1].name,'propose_task');
 assert.equal(JSON.parse(followup[1].arguments).steps[0].action,'open_step');
 const observed=records.find(row=>row.kind==='diagnostics_feedback');assert(observed);
 assert.equal(observed.saw_http_409,true);assert.equal(observed.saw_json_failure,true);assert.equal(observed.task.fresh,true);assert.equal(observed.task.revision,1);
 assert.equal(observed.job_snapshot_state,'observed');assert.deepEqual(observed.tool_names,['read_app_diagnostics:requests','read_app_diagnostics:dataset','read_app_diagnostics:jobs','read_task_status']);
 for(const secret of [ACCESS,REFRESH,'정제된 오류','Unexpected end of JSON input'])assert(!JSON.stringify(records).includes(secret));
});
