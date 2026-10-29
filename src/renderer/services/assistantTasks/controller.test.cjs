const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file,cache=new Map()){if(cache.has(file))return cache.get(file).exports;const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));cache.set(file,m);const base=m.require.bind(m);m.require=name=>name.startsWith('.')&&fs.existsSync(path.resolve(path.dirname(file),name)+'.ts')?load(path.resolve(path.dirname(file),name)+'.ts',cache):base(name);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
const file=path.join(__dirname,'controller.ts'),ID='task_'+'a'.repeat(32);
function fixture(){
 let connection='account-model-1',ready=true,runtime='project1',resolve,executions=0;const calls=[],states=[];
 let task={schema_version:1,id:ID,revision:1,status:'draft',goal:'자료 확인',binding:'',plan_sha256:'a'.repeat(64),approval:null,current_step_id:null,message:null,created_at:'now',updated_at:'now',steps:[{id:'step_1',action:'open_step',args:{step:1},label:'자료 확인',status:'pending',attempt_id:null,receipt:null,error:null}]};
 const clone=x=>JSON.parse(JSON.stringify(x));
 const client={list:async()=>[clone(task)],get:async()=>clone(task),create:async plan=>{calls.push(['create',plan]);task={...task,...plan,steps:plan.steps.map((step,index)=>({id:'step_'+(index+1),status:'pending',attempt_id:null,receipt:null,error:null,...step}))};return clone(task);},event:async(id,event)=>{calls.push(['event',event]);assert.equal(event.expected_revision,task.revision);task={...task,revision:task.revision+1};if(event.type==='approve'){task.status='ready';task.approval={binding:task.binding,plan_sha256:task.plan_sha256,confirmed_at:'now'};}return clone(task);}};
 const deps={client,binding:()=>runtime,connection:()=>connection,ready:()=>ready,mayStart:()=>true,onChange:state=>states.push(state),
  execute:async()=>({kind:'completed',receipt:{opened:true}}),reconcile:async()=>({kind:'needs_reconciliation',message:'확인 필요'}),
  runnerFactory:runner=>({run:async value=>{executions++;return new Promise(r=>{resolve=async()=>{const outcome=await runner.execute(value.steps[0],{isCurrent:()=>true,isPaused:()=>false});r({...value,status:'completed',message:outcome.kind});};});},pause:async()=>null})};
 const Controller=load(file).AssistantTaskController,c=new Controller(deps);
 return {c,client,deps,calls,states,get task(){return task;},setConnection:v=>{connection=v;},setReady:v=>{ready=v;},setRuntime:v=>{runtime=v;},finish:()=>resolve(),get executions(){return executions;}};
}
const proposal={goal:'자료 확인',steps:[{action:'open_step',args:{step:1},label:'자료 확인'}]};
test('actual execution intent authorizes and runs the saved plan while advice remains draft',async()=>{
 const f=fixture(),event=f.client.event;
 f.client.event=async(id,value)=>{const result=await event(id,value);if(value.type==='authorize'){
   f.task.status='ready';f.task.authorization={source:value.authorization_source,scope:{workspace_id:'w',actor_id:'a'},binding:f.task.binding,plan_sha256:f.task.plan_sha256,policy_version:'continuous-execution-v1',authorized_at:'now',allowed_steps:f.task.steps.map(row=>row.id)};return structuredClone(f.task);
 }return result;};
 const running=f.c.propose(proposal,{source:'user_request'});await new Promise(r=>setImmediate(r));
 assert.equal(f.executions,1);assert.equal(f.task.approval,null);assert.equal(f.task.authorization.source,'user_request');
 assert.equal(f.calls.filter(row=>row[0]==='event')[0][1].type,'authorize');
 await f.finish();await running;assert.equal(f.c.state.task.status,'completed');
});
test('only an authorized no-effect input wait is automatically rechecked and explicit pauses stay paused',async()=>{
 const f=fixture();await f.c.propose(proposal);f.task.authorization={source:'user_request',scope:{workspace_id:'w',actor_id:'a'},binding:f.task.binding,plan_sha256:f.task.plan_sha256,policy_version:'continuous-execution-v1',authorized_at:'now',allowed_steps:['step_1']};
 f.task.status='waiting_human';f.task.current_step_id='step_1';Object.assign(f.task.steps[0],{status:'waiting_human',attempt_id:'a',receipt:{not_started:true,reason_code:'waiting_input'}});f.c.state.task=structuredClone(f.task);
 const original=f.client.event;f.client.event=async(id,value)=>{const saved=await original(id,value);if(value.type==='recheck'){f.task.status='needs_reconciliation';return structuredClone(f.task);}return saved;};
 assert.equal(typeof f.c.recheckInputs,'function');const pending=f.c.recheckInputs();await new Promise(r=>setImmediate(r));assert.equal(f.executions,1);assert.equal(f.calls.at(-1)[1].type,'recheck');assert.equal(f.calls.at(-1)[1].confirmed,undefined);await f.finish();await pending;
 f.task.status='paused';f.c.state.task=structuredClone(f.task);await f.c.recheckInputs();assert.equal(f.executions,1);
});
for(const phase of ['create','authorize'])test(`stopping GPT while ${phase} awaits never dispatches the newly proposed plan`,async()=>{
 const f=fixture(),signal=new AbortController();let release;const gate=new Promise(resolve=>{release=resolve;});
 const create=f.client.create,event=f.client.event;
 f.client.create=async plan=>{if(phase==='create')await gate;return create(plan);};
 f.client.event=async(id,value)=>{const result=await event(id,value);if(value.type==='authorize'){
  Object.assign(f.task,{status:'ready',authorization:{source:value.authorization_source,scope:{workspace_id:'w',actor_id:'a'},binding:f.task.binding,plan_sha256:f.task.plan_sha256,policy_version:'continuous-execution-v1',authorized_at:'now',allowed_steps:f.task.steps.map(row=>row.id)}});
  if(phase==='authorize')await gate;return structuredClone(f.task);
 }if(value.type==='pause'){f.task.status='paused';return structuredClone(f.task);}return result;};
 const pending=f.c.propose(proposal,{source:'user_request',signal:signal.signal});await new Promise(setImmediate);signal.abort();release();await new Promise(setImmediate);
 if(f.executions)await f.finish();await pending;
 assert.equal(f.executions,0);assert.equal(f.task.status,phase==='create'?'draft':'paused');
 if(phase==='create')assert.equal(f.calls.filter(row=>row[0]==='event').length,0);
});
function waitingSignalFixture(){
 const f=fixture(),event=f.client.event;let runs=0;
 f.client.event=async(id,value)=>{await event(id,value);if(value.type==='authorize'){
  f.task.status='ready';f.task.authorization={source:value.authorization_source,scope:{workspace_id:'w',actor_id:'a'},binding:f.task.binding,plan_sha256:f.task.plan_sha256,policy_version:'continuous-execution-v1',authorized_at:'now',allowed_steps:f.task.steps.map(row=>row.id)};
 }if(value.type==='pause')f.task.status='paused';return structuredClone(f.task);};
 f.deps.runnerFactory=()=>({run:async()=>{runs++;f.task.status='waiting_job';f.task.current_step_id=f.task.steps[0].id;Object.assign(f.task.steps[0],{status:'waiting_job',attempt_id:'waiting-attempt',receipt:{job_id:'external-job'},error:null});return structuredClone(f.task);},pause:async()=>null});
 return{...f,get task(){return f.task;},get runs(){return runs;}};
}
test('the same GPT turn stop after propose returns waiting_job persists pause before another poll can continue',async()=>{
 const f=waitingSignalFixture(),abort=new AbortController();await f.c.propose(proposal,{source:'user_request',signal:abort.signal});assert.equal(f.runs,1);
 abort.abort();await new Promise(setImmediate);await f.c.pollWaiting();
 assert.equal(f.runs,1);assert.equal(f.c.state.task.status,'paused');assert.equal(f.task.steps[0].receipt.job_id,'external-job');
 assert.equal(f.calls.filter(row=>row[0]==='event'&&row[1].type==='pause').length,1);
});
test('an invalidated old turn signal cannot pause a new scope owned waiting task',async()=>{
 const f=waitingSignalFixture(),old=new AbortController();await f.c.propose(proposal,{source:'user_request',signal:old.signal});
 f.c.invalidateConnection();f.setConnection('account-b');const fresh=new AbortController();await f.c.propose(proposal,{source:'user_request',signal:fresh.signal});
 old.abort();await new Promise(setImmediate);assert.equal(f.c.state.task.status,'waiting_job');assert.equal(f.runs,2);assert.equal(f.calls.filter(row=>row[0]==='event'&&row[1].type==='pause').length,0);
});
test('a later explicit approval can start the inert draft left by a stopped GPT request',async()=>{
 const f=fixture(),abort=new AbortController(),create=f.client.create;let release;
 f.client.create=async plan=>{await new Promise(resolve=>{release=resolve;});return create(plan);};
 const pending=f.c.propose(proposal,{source:'user_request',signal:abort.signal});await new Promise(setImmediate);abort.abort();release();await pending;
 const approved=f.c.approve(ID,1);await new Promise(setImmediate);assert.equal(f.executions,1);await f.finish();await approved;
});
test('proposal prepares an immutable local plan without executing or approving it',async()=>{
 const f=fixture();await f.c.propose(proposal);assert.equal(f.executions,0);assert.equal(f.calls.length,1);assert.deepEqual(f.calls[0][1].binding,JSON.stringify(['project1','account-model-1']));
 assert.equal(f.c.state.task.status,'draft');assert.equal(f.c.state.busy,false);
 await assert.rejects(f.c.propose({...proposal,binding:'model supplied authority'}),/계획/);
});
test('only the current explicitly reviewed plan can be approved, with no overlapping task execution',async()=>{
 const f=fixture();await f.c.propose(proposal);await assert.rejects(f.c.approve(ID,0),/바뀌/);assert.equal(f.calls.length,1);
 const pending=f.c.approve(ID,1);await new Promise(r=>setImmediate(r));assert.equal(f.executions,1);assert.equal(f.calls[1][1].confirmed,true);
 await assert.rejects(f.c.propose(proposal),/진행|확인/);await f.finish();await pending;assert.equal(f.c.state.busy,false);
});
test('a provider change cannot be concealed by an action-owned project transition',async()=>{
 const f=fixture();await f.c.propose(proposal);const pending=f.c.approve(ID,1);await new Promise(r=>setImmediate(r));f.setConnection('account-model-2');
 await assert.rejects(f.finish(),/연결/); // The wrapper rejects before accepting any runtime result into a new account.
 // The fake runner keeps the unresolved execution promise: no second mutation or automatic retry is attempted.
 assert.equal(f.executions,1);assert.equal(f.calls.filter(x=>x[0]==='event').length,1);
 void pending;
});
test('task restoration and status summary never run work or expose local authority/receipts',async()=>{
 const f=fixture();await f.c.propose(proposal);await f.c.restore();assert.equal(f.executions,0);
 const summary=JSON.stringify(f.c.summary());assert.ok(!summary.includes('account-model-1'));assert.ok(!summary.includes('project1'));assert.ok(!summary.includes('plan_sha256'));
 f.setReady(false);await assert.rejects(f.c.approve(ID,1),/연결/);assert.equal(f.executions,0);
});
test('double proposal or confirmation activation is refused before a second mutation',async()=>{
 const f=fixture(),first=f.c.propose(proposal);await assert.rejects(f.c.propose(proposal),/진행|확인/);await first;
 const approving=f.c.approve(ID,1);await assert.rejects(f.c.approve(ID,1),/진행|확인/);await new Promise(r=>setImmediate(r));await f.finish();await approving;
 assert.equal(f.calls.filter(x=>x[0]==='create').length,1);assert.equal(f.executions,1);
});
test('resume is single flight before reading the saved revision',async()=>{
 const f=fixture();await f.c.propose(proposal);f.c.state.task.status='paused';f.task.status='paused';
 const one=f.c.resume(),two=f.c.resume();await new Promise(r=>setImmediate(r));
 assert.equal(f.executions,1);await f.finish();await Promise.all([one,two]);
});
test('another inactive project stays in history until explicitly selected read-only',async()=>{
 const f=fixture();await f.c.propose(proposal);f.setRuntime('different-project');
 assert.equal(await f.c.restore(),null);assert.equal(f.c.state.task,null);assert.equal(f.c.state.history[0].id,ID);assert.equal(f.c.state.history[0].contextMatches,false);
 const restored=await f.c.restore(ID);assert.equal(restored.id,ID);assert.match(f.c.state.error,/프로젝트|자료/);
 await assert.rejects(f.c.approve(ID,1),/프로젝트|자료/);assert.equal(f.executions,0);
 f.setConnection('different-account');assert.equal(await f.c.restore(),null);
});
test('ledger read failure surfaces an error and never dispatches an app action',async()=>{
 const Controller=load(file).AssistantTaskController;let fail=false,executions=0;
 const task={schema_version:1,id:ID,revision:1,status:'waiting_job',goal:'확인',binding:JSON.stringify(['p','c']),plan_sha256:'a'.repeat(64),approval:null,current_step_id:'step_1',created_at:'now',updated_at:'now',steps:[{id:'step_1',action:'start_training',args:{},label:'학습',status:'waiting_job',attempt_id:'attempt_'+'a'.repeat(32),receipt:{job_id:'j'},error:null}]};
 const c=new Controller({client:{list:async()=>[task],get:async()=>{if(fail)throw Error('controlled ledger offline');return task;},event:async()=>{throw Error('must not mutate');}},binding:()=> 'p',connection:()=> 'c',ready:()=>true,mayStart:()=>true,execute:async()=>{executions++;return{kind:'completed'};},reconcile:async()=>{executions++;return{kind:'completed'};},onChange:()=>{}});
 await c.restore();fail=true;await c.pollWaiting();assert.match(c.state.error,/offline/);assert.equal(executions,0);assert.equal(c.state.busy,false);
});

test('an account A to B to A reconnect during approval cannot lend the old confirmation to a new session',async()=>{
 const f=fixture();await f.c.propose(proposal);let release;
 const original=f.client.get;f.client.get=()=>new Promise(r=>{release=r;});
 const approving=f.c.approve(ID,1);f.setConnection('other');f.c.invalidateConnection();f.setConnection('account-model-1');
 release(await original(ID));await assert.rejects(approving,/바뀌/);assert.equal(f.executions,0);assert.equal(f.calls.filter(x=>x[0]==='event').length,0);
});
test('a lost unstarted claim response never retries the claim or dispatches an app mutation',async()=>{
 const Controller=load(file).AssistantTaskController;let effects=0,events=0;
 const step={id:'step_1',action:'import_dataset',args:{},label:'가져오기',status:'waiting_human',attempt_id:'attempt_'+'c'.repeat(32),receipt:{not_started:true},error:null};
 const task={schema_version:1,id:ID,revision:3,status:'needs_reconciliation',goal:'확인',binding:JSON.stringify(['p','c']),plan_sha256:'a'.repeat(64),approval:null,current_step_id:'step_1',created_at:'now',updated_at:'now',steps:[step]};
 const c=new Controller({client:{list:async()=>[task],get:async()=>structuredClone(task),event:async(_id,e)=>{events++;step.receipt=e.receipt;step.status='uncertain';task.revision++;throw Error('controlled claim acknowledgement lost');}},binding:()=> 'p',connection:()=> 'c',ready:()=>true,mayStart:()=>true,execute:async()=>{effects++;return{kind:'completed'};},reconcile:async()=>({kind:'needs_reconciliation'}),onChange:()=>{},runnerFactory:deps=>({pause:async()=>null,run:async t=>{await deps.reconcile(t.steps[0],{isCurrent:()=>true,isPaused:()=>false});return t;}})});
 // The runtime can dispatch only after this unique durable claim is acknowledged.
 c['deps'].reconcile=async(_step,control)=>{await control.checkpoint({not_started:false,unstarted_claim:{nonce:'ca955548-1f35-4346-9c8b-98a10f99a501',phase:'claimed'}});effects++;return {kind:'completed'};};
 await c.restore();await c.resume();assert.equal(events,1);assert.equal(effects,0);assert.match(c.state.error,/acknowledgement/);
});
test('completed records remain visible after reload even when ephemeral inputs reset',async()=>{
 const f=fixture();await f.c.propose(proposal);f.task.status='completed';f.task.approval={binding:f.task.binding,plan_sha256:f.task.plan_sha256,confirmed_at:'now'};f.setRuntime('project-reset-inputs');
 f.deps.settingsRecovery=saved=>saved==='project1'?{label:'저장 설정 복원',restore:()=>true}:null;
 const restored=await f.c.restore();assert.equal(restored.id,ID);assert.equal(restored.status,'completed');assert.equal(f.executions,0);
});
test('saved settings restoration is explicit, context scoped, and never runs a step',async()=>{
 const f=fixture();await f.c.propose(proposal);f.task.status='paused';f.task.approval={binding:f.task.binding,plan_sha256:f.task.plan_sha256,confirmed_at:'now'};
 f.setRuntime('reset-inputs');let changes=0;
 f.deps.settingsRecovery=saved=>saved==='project1'?{label:'저장 설정 복원 · CPU · 배치 8',restore:()=>{changes++;f.setRuntime('project1');return true;}}:null;
 await f.c.restore();assert.equal(changes,0);assert.equal(f.c.state.contextMatches,false);assert.match(f.c.state.settingsRecoveryLabel,/CPU/);
 await f.c.restoreSettings();assert.equal(changes,1);assert.equal(f.c.state.contextMatches,true);assert.equal(f.executions,0);assert.equal(f.calls.filter(x=>x[0]==='event').length,0);
});
test('an old restore-settings click cannot survive a project or input A-B-A while reading its ledger',async()=>{
 const f=fixture();await f.c.propose(proposal);f.task.status='paused';f.task.approval={binding:f.task.binding,plan_sha256:f.task.plan_sha256,confirmed_at:'now'};
 f.setRuntime('reset-inputs');let generation=1,writes=0,release;f.deps.generation=()=>generation;
 f.deps.settingsRecovery=()=>({label:'저장 설정 복원',restore:()=>{writes++;f.setRuntime('project1');return true;}});
 await f.c.restore();const original=f.client.get;f.client.get=()=>new Promise(r=>{release=r;});
 const restoring=f.c.restoreSettings();generation=3;release(await original(ID));
 await assert.rejects(restoring,/바뀌/);assert.equal(writes,0);
});
for(const kind of ['approve','resume'])test(`${kind} refuses a runtime A-B-A while its current record is being read`,async()=>{
 const f=fixture();await f.c.propose(proposal);let generation=1,release;f.deps.generation=()=>generation;
 if(kind==='resume'){f.task.status='paused';f.c.state.task.status='paused';}
 const original=f.client.get;f.client.get=()=>new Promise(r=>{release=r;});
 const pending=kind==='approve'?f.c.approve(ID,1):f.c.resume();pending.catch(()=>{});generation=3;release(await original(ID));
 await new Promise(r=>setImmediate(r));if(f.executions)await f.finish();
 await assert.rejects(pending,/바뀌/);assert.equal(f.executions,0);assert.equal(f.calls.filter(x=>x[0]==='event').length,0);
});
test('live context refresh changes read-only navigation ownership without running any action',async()=>{
 const f=fixture();await f.c.propose(proposal);assert.equal(f.c.state.contextMatches,true);f.setRuntime('different-project');
 f.c.refreshContext();assert.equal(f.c.state.contextMatches,false);assert.equal(f.executions,0);
});
test('later saved observations may retain an existing claim without consuming it again',async()=>{
 const Controller=load(file).AssistantTaskController;let events=0,continued=0;
 const receipt={not_started:false,unstarted_claim:{nonce:'ca955548-1f35-4346-9c8b-98a10f99a501',phase:'claimed'},job_id:'original-job'};
 const step={id:'step_1',action:'evaluate_flow',args:{},label:'확인',status:'uncertain',attempt_id:'attempt_'+'c'.repeat(32),receipt,error:null};
 const task={schema_version:1,id:ID,revision:3,status:'needs_reconciliation',goal:'확인',binding:JSON.stringify(['p','c']),plan_sha256:'a'.repeat(64),approval:null,current_step_id:'step_1',created_at:'now',updated_at:'now',steps:[step]};
 const c=new Controller({client:{list:async()=>[task],get:async()=>structuredClone(task),event:async(_id,e)=>{events++;step.receipt=e.receipt;task.revision++;return structuredClone(task);}},binding:()=> 'p',connection:()=> 'c',ready:()=>true,mayStart:()=>true,execute:async()=>({kind:'completed'}),reconcile:async(_step,control)=>{await control.checkpoint({...receipt,recomputation:{phase:'verified'}});continued++;return{kind:'needs_reconciliation'};},onChange:()=>{},runnerFactory:deps=>({pause:async()=>null,run:async t=>{await deps.reconcile(t.steps[0],{isCurrent:()=>true,isPaused:()=>false});return t;}})});
 await c.restore();await c.resume();assert.equal(continued,1);assert.equal(events,1);assert.equal(c.state.error,null);
});
test('task observation reads fresh durable results without changing execution state or dispatching',async()=>{
 const f=fixture();await f.c.propose(proposal);const before=structuredClone(f.c.state),version=f.c.version,calls=f.calls.length;f.task.revision=4;f.task.status='completed';f.task.steps[0].status='completed';f.task.steps[0].receipt={job_id:'job_observed'};
 assert.equal(typeof f.c.observe,'function');const result=await f.c.observe();assert.equal(result.fresh,true);assert.equal(result.source,'ledger');assert.equal(result.revision,4);assert.equal(result.steps[0].result.job_id,'job_observed');assert.deepEqual(f.c.state,before);assert.equal(f.c.version,version);assert.equal(f.calls.length,calls);assert.equal(f.executions,0);
});
test('failed task observation explicitly marks its cached result stale and preserves the exact sanitized failure',async()=>{
 const f=fixture();await f.c.propose(proposal);f.client.get=async()=>{throw Object.assign(Error('HTTP 409 ledger conflict at /private/plan.json'),{status:409,code:'conflict'});};
 assert.equal(typeof f.c.observe,'function');const result=await f.c.observe();assert.equal(result.fresh,false);assert.equal(result.source,'cache');assert.equal(result.revision,1);assert.match(result.read_error.message,/ledger conflict/);assert.equal(result.read_error.status,409);assert.doesNotMatch(JSON.stringify(result),/\/private/);assert.equal(f.executions,0);
});
for(const kind of ['runtime','connection','controller'])test(`late task observation cannot survive a ${kind} authority change`,async()=>{
 const f=fixture();await f.c.propose(proposal);let runtimeGeneration=1,connectionGeneration=1,release;f.deps.generation=()=>runtimeGeneration;f.deps.connectionGeneration=()=>connectionGeneration;
 const original=f.client.get;f.client.get=()=>new Promise(resolve=>{release=resolve;});assert.equal(typeof f.c.observe,'function');const pending=f.c.observe();
 if(kind==='runtime')runtimeGeneration=3;else if(kind==='connection')connectionGeneration=3;else f.c.invalidateConnection();release({...await original(ID),revision:9,message:'old account failure'});
 const result=await pending;assert.equal(result.fresh,false);assert.equal(result.loaded,false);assert.doesNotMatch(JSON.stringify(result),/old account|task_aaaa/);assert.equal(f.executions,0);
});
test('an older in-flight ledger snapshot cannot supersede newer cached task evidence',async()=>{
 const f=fixture();await f.c.propose(proposal);let release;f.client.get=()=>new Promise(resolve=>{release=resolve;});const old=structuredClone(f.c.state.task),pending=f.c.observe();
 f.c.state.task={...f.c.state.task,revision:8,status:'completed'};release(old);const result=await pending;assert.equal(result.fresh,false);assert.equal(result.revision,8);assert.equal(result.status,'completed');assert.equal(f.executions,0);
});
for(const malformed of [null,{}, {id:'task_'+'b'.repeat(32)}, {revision:0}, {steps:null}, {status:'made_up'}])test(`malformed ledger observation is stale cache rather than execution evidence ${JSON.stringify(malformed)}`,async()=>{
 const f=fixture();await f.c.propose(proposal);const before=structuredClone(f.c.state);f.client.get=async()=>malformed&&Object.keys(malformed).length?{...f.task,...malformed}:malformed;const result=await f.c.observe();assert.equal(result.fresh,false);assert.equal(result.revision,1);assert.equal(result.status,'draft');assert.ok(result.read_error);assert.deepEqual(f.c.state,before);assert.equal(f.executions,0);
});

// Recovery fixtures use only an in-memory ledger; no provider or app mutation is available.
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function recoveryFixture(){
 const f=fixture();let generation=1;f.deps.generation=()=>generation;f.bumpGeneration=()=>{generation++;};
 f.client.event=async(id,event)=>{f.calls.push(['event',event]);assert.equal(id,f.task.id);assert.equal(event.expected_revision,f.task.revision);f.task.revision++;if(event.type==='pause')f.task.status='paused';return structuredClone(f.task);};
 f.c.deps.readTimeoutMs=10;return f;
}
async function saved(f,status='waiting_human',stepStatus='waiting_human'){
 await f.c.propose(proposal);f.task.status=status;f.task.steps[0].status=stepStatus;f.task.current_step_id='step_1';
 if(status!=='draft')f.task.approval={binding:f.task.binding,plan_sha256:f.task.plan_sha256,confirmed_at:'now'};
 await f.c.restore(ID);return f.c.state.task;
}
test('safe human wait is freshly read and paused before preparing a new request without starting work',async()=>{
 const f=recoveryFixture(),old=await saved(f),before=structuredClone(old);let reads=0;const get=f.client.get;f.client.get=async(id,signal)=>{reads++;assert.ok(signal instanceof AbortSignal);return get(id);};
 assert.equal(await f.c.prepareNewRequest(old.id,old.revision),true);assert.equal(reads,1);assert.equal(f.c.state.planningNew,true);assert.equal(f.c.state.task.id,old.id);assert.equal(f.c.state.task.status,'paused');assert.equal(f.c.state.task.steps[0].status,before.steps[0].status);
 assert.equal(f.calls.filter(row=>row[0]==='event').length,1);assert.equal(f.calls.at(-1)[1].type,'pause');assert.equal(f.executions,0);
 const summary=f.c.summary();assert.equal(summary.planning_new,true);assert.equal(summary.active_requested_plan,false);assert.equal(summary.loaded,false);assert.equal(summary.task_id,undefined);assert.equal(summary.steps,undefined);
 const calls=f.calls.length;await f.c.observe();assert.equal(reads,1);assert.equal(f.calls.length,calls);
});
for(const status of ['running','uncertain','waiting_job'])test(`new-request preparation preserves unresolved ${status} ownership and explains its step`,async()=>{
 const f=recoveryFixture(),old=await saved(f,status==='uncertain'?'needs_reconciliation':status,status),before=structuredClone(f.c.state.task),calls=f.calls.length;
 assert.equal(await f.c.prepareNewRequest(old.id,old.revision),false);assert.equal(f.c.state.planningNew,false);assert.deepEqual(f.c.state.task,before);assert.match(f.c.state.error,/자료 확인/);assert.equal(f.calls.length,calls);assert.equal(f.c.ownsExecution(),true);assert.equal(f.executions,0);
});
for(const status of ['draft','paused','completed'])test(`preparing a quiescent ${status} record preserves it without an invented ledger event`,async()=>{
 const f=recoveryFixture(),old=await saved(f,status,status==='completed'?'completed':'pending'),events=f.calls.length;
 assert.equal(await f.c.prepareNewRequest(old.id,old.revision),true);assert.equal(f.c.state.task.status,status);assert.equal(f.calls.length,events);assert.equal(f.c.state.planningNew,true);
});
test('a ready plan is paused and old captured approval/resume callbacks cannot run in prepared mode',async()=>{
 const f=recoveryFixture(),old=await saved(f,'ready','pending');assert.equal(await f.c.prepareNewRequest(old.id,old.revision),true);const calls=f.calls.length;
 await assert.rejects(f.c.approve(old.id,old.revision),/새 요청/);await assert.rejects(f.c.resume(),/새 요청/);assert.equal(f.calls.length,calls);assert.equal(f.executions,0);assert.equal(f.c.blocksOtherWrites(),false);
});
test('a failed new proposal retains the old held plan and prepared mode until a new draft is saved',async()=>{
 const f=recoveryFixture(),old=await saved(f,'draft','pending');await f.c.prepareNewRequest(old.id,old.revision);const held=structuredClone(f.c.state.task),create=f.client.create;
 f.client.create=async()=>{throw Error('controlled create acknowledgement lost');};await assert.rejects(f.c.propose(proposal),/acknowledgement/);assert.deepEqual(f.c.state.task,held);assert.equal(f.c.state.planningNew,true);
 f.client.create=async plan=>({...await create(plan),id:'task_'+'b'.repeat(32),status:'draft'});await f.c.propose(proposal);assert.equal(f.c.state.task.id,'task_'+'b'.repeat(32));assert.equal(f.c.state.planningNew,false);assert.equal(f.executions,0);
});
test('prepare refuses a changed saved revision before pausing or changing requested intent',async()=>{
 const f=recoveryFixture(),old=await saved(f);f.task.revision++;assert.equal(await f.c.prepareNewRequest(old.id,old.revision),false);assert.equal(f.c.state.planningNew,false);assert.equal(f.calls.filter(row=>row[0]==='event').length,0);assert.match(f.c.state.error,/바뀌/);
});
for(const change of ['runtime','account'])test(`late prepare cannot survive ${change} A-B-A or pause a stale plan`,async()=>{
 const f=recoveryFixture(),old=await saved(f);let release;const get=f.client.get;f.client.get=()=>new Promise(resolve=>{release=resolve;});const pending=f.c.prepareNewRequest(old.id,old.revision);
 if(change==='runtime')f.bumpGeneration();else{f.setConnection('other');f.c.invalidateConnection();f.setConnection('account-model-1');}
 release(await get(ID));assert.equal(await pending,false);assert.equal(f.c.state.planningNew,false);assert.equal(f.calls.filter(row=>row[0]==='event').length,0);assert.equal(f.executions,0);
});
test('default restore prefers the newest current-binding completed record over an older paused plan',async()=>{
 const f=recoveryFixture();await saved(f,'paused','pending');const old=structuredClone(f.task),latest={...structuredClone(old),id:'task_'+'b'.repeat(32),status:'completed',updated_at:'2026-10-28T02:00:00Z'};old.updated_at='2026-10-28T01:00:00Z';f.client.list=async()=>[old,latest];
 assert.equal((await f.c.restore()).id,latest.id);assert.equal(f.c.state.task.status,'completed');assert.equal(f.executions,0);
});
test('default restoration gives unresolved execution first ownership even over a newer completed plan',async()=>{
 const f=recoveryFixture();await saved(f,'paused','uncertain');const old=structuredClone(f.task),latest={...structuredClone(old),id:'task_'+'b'.repeat(32),status:'completed',updated_at:'z',steps:[]};f.client.list=async()=>[old,latest];
 assert.equal((await f.c.restore()).id,old.id);assert.equal(f.c.ownsExecution(),true);await assert.rejects(f.c.restore(latest.id),/결과|확인/);assert.equal(f.c.state.task.id,old.id);
});
test('history is bounded actor-scoped summaries and explicit selection only reads a plan',async()=>{
 const f=recoveryFixture();await saved(f,'paused','pending');f.setRuntime('new-project');await f.c.loadHistory();assert.equal(f.c.state.history.length,1);assert.deepEqual(Object.keys(f.c.state.history[0]).sort(),['contextMatches','goal','id','status','updated_at'].sort());assert.equal(f.c.state.history[0].contextMatches,false);
 assert.equal(await f.c.restore(),null);assert.equal(f.c.state.task,null);const calls=f.calls.length;await f.c.restore(ID);assert.equal(f.c.state.task.id,ID);assert.equal(f.c.state.contextMatches,false);assert.equal(f.calls.length,calls);assert.equal(f.executions,0);
});
test('stopping a GET retires its late reply and does not clear a newer create wait',async()=>{
 const f=recoveryFixture();await saved(f,'draft','pending');let releaseRead,releaseCreate,signal;const old=structuredClone(f.task);f.client.get=(_id,value)=>{signal=value;return new Promise(resolve=>{releaseRead=resolve;});};
 const reading=f.c.restore(ID);assert.equal(typeof releaseRead,'function','the GET callback is registered synchronously');assert.equal(f.c.state.readPending,true);f.c.stopRead();assert.equal(signal.aborted,true);assert.equal(f.c.state.readPending,false);assert.equal(f.c.state.loading,false);
 f.client.create=()=>new Promise(resolve=>{releaseCreate=resolve;});const creating=f.c.propose(proposal);assert.equal(typeof releaseCreate,'function','the create callback is registered synchronously');f.c.stopRead();assert.equal(f.c.state.loading,true);releaseRead({...old,revision:99});await reading;assert.equal(f.c.state.task.revision,old.revision);assert.equal(f.c.state.loading,true);
 releaseCreate({...old,id:'task_'+'b'.repeat(32)});await creating;assert.equal(f.c.state.task.id,'task_'+'b'.repeat(32));assert.equal(f.c.state.loading,false);
});
test('GET deadline releases the owned display wait without retry and ignores a late saved result',{timeout:500},async()=>{
 const f=recoveryFixture();await saved(f,'draft','pending');let release,reads=0,signal;const old=structuredClone(f.c.state.task);f.client.get=(_id,value)=>{reads++;signal=value;return new Promise(resolve=>{release=resolve;});};
 await f.c.restore(ID);assert.equal(reads,1);assert.equal(signal.aborted,true);assert.equal(f.c.state.readPending,false);assert.equal(f.c.state.loading,false);assert.match(f.c.state.error,/15초|응답|시간/);release({...old,revision:99});await tick();assert.deepEqual(f.c.state.task,old);assert.equal(f.executions,0);
});
test('restore and history ignore a project A-B-A response and cannot select its old record',async()=>{
 const f=recoveryFixture();await saved(f,'draft','pending');let release;const old=structuredClone(f.task);f.client.list=()=>new Promise(resolve=>{release=resolve;});
 const pending=f.c.restore();f.bumpGeneration();release([{...old,revision:99}]);assert.equal(await pending,null);assert.notEqual(f.c.state.task.revision,99);assert.equal(f.c.state.loading,false);
});

test('newest recoverable completed record outranks an older exact-binding paused record',async()=>{
 const f=recoveryFixture();await saved(f,'paused','pending');const old={...structuredClone(f.task),updated_at:'2026-10-28T01:00:00Z'},latest={...structuredClone(old),id:'task_'+'b'.repeat(32),binding:JSON.stringify(['project1-saved-inputs','account-model-1']),status:'completed',updated_at:'2026-10-28T02:00:00Z'};
 latest.approval={binding:latest.binding,plan_sha256:latest.plan_sha256,confirmed_at:'now'};f.deps.settingsRecovery=value=>value==='project1-saved-inputs'?{label:'저장 설정 복원',restore:()=>true}:null;f.client.list=async()=>[old,latest];
 assert.equal((await f.c.restore()).id,latest.id);assert.equal(f.c.state.settingsRecoveryLabel,'저장 설정 복원');assert.equal(f.executions,0);
});
test('an authoritative resolved snapshot can retire cached unresolved ownership without running anything',async()=>{
 const f=recoveryFixture();await saved(f,'waiting_job','waiting_job');const complete={...structuredClone(f.task),revision:f.task.revision+1,status:'completed',steps:f.task.steps.map(step=>({...step,status:'completed'}))};f.client.list=async()=>[complete];
 assert.equal((await f.c.restore()).status,'completed');assert.equal(f.c.ownsExecution(),false);assert.equal(f.executions,0);
});
test('a fresh unresolved revision is retained when an old safe card tries to prepare a new request',async()=>{
 const f=recoveryFixture(),old=await saved(f,'draft','pending');f.task.revision++;f.task.status='needs_reconciliation';f.task.steps[0].status='uncertain';
 assert.equal(await f.c.prepareNewRequest(old.id,old.revision),false);assert.equal(f.c.state.task.revision,f.task.revision);assert.equal(f.c.state.task.steps[0].status,'uncertain');assert.equal(f.c.ownsExecution(),true);assert.equal(f.c.state.planningNew,false);assert.equal(f.calls.filter(row=>row[0]==='event').length,0);
});
test('stopRead cannot time out or retire a confirmed safe pause event',async()=>{
 const f=recoveryFixture(),old=await saved(f);let release;const event=f.client.event;f.client.event=(_id,value)=>new Promise(resolve=>{release=async()=>resolve(await event(_id,value));});
 const preparing=f.c.prepareNewRequest(old.id,old.revision);await tick();assert.equal(f.c.state.loading,true);assert.equal(f.c.state.readPending,false);f.c.stopRead();await new Promise(resolve=>setTimeout(resolve,20));assert.equal(f.c.state.loading,true);assert.equal(f.c.state.planningNew,false);await release();assert.equal(await preparing,true);assert.equal(f.c.state.planningNew,true);
});
test('pause owns its mutation wait and cannot overwrite a newly selected account with a late result',async()=>{
 const f=recoveryFixture();await saved(f,'ready','pending');let release;const event=f.client.event;f.client.event=(_id,value)=>new Promise(resolve=>{release=async()=>resolve(await event(_id,value));});
 const pausing=f.c.pause();assert.equal(f.c.state.loading,true);assert.equal(await f.c.prepareNewRequest(ID,1),false);f.setConnection('other-account');f.c.invalidateConnection();await release();await pausing;assert.equal(f.c.state.task,null);assert.equal(f.c.state.loading,false);assert.equal(f.executions,0);
});
test('history summaries are bounded and a late account reply cannot replace the new history',async()=>{
 const f=recoveryFixture();await saved(f,'paused','pending');const original=structuredClone(f.task);f.client.list=async()=>Array.from({length:60},(_,index)=>({...original,id:'task_'+index.toString(16).padStart(32,'0'),goal:'x'.repeat(500)}));
 await f.c.loadHistory();assert.equal(f.c.state.history.length,50);assert.ok(f.c.state.history.every(row=>row.goal.length<=180));let release;f.client.list=()=>new Promise(resolve=>{release=resolve;});const pending=f.c.loadHistory();f.setConnection('other');f.c.invalidateConnection();release([original]);await pending;assert.deepEqual(f.c.state.history,[]);assert.equal(f.c.state.task,null);
});
test('explicit history selection refuses a record saved under another connected account',async()=>{
 const f=recoveryFixture();await saved(f,'paused','pending');f.setConnection('different-account');f.c.invalidateConnection();
 await assert.rejects(f.c.restore(ID),/연결|계정/);assert.equal(f.c.state.task,null);assert.equal(f.c.state.planningNew,false);assert.equal(f.executions,0);
});
test('loading history does not clear an unresolved task error that had stopped job polling',async()=>{
 const f=recoveryFixture();await saved(f,'waiting_job','waiting_job');f.c.state.error='controlled result still requires confirmation';await f.c.loadHistory();assert.equal(f.c.state.error,'controlled result still requires confirmation');assert.equal(f.c.state.task.status,'waiting_job');assert.equal(f.executions,0);
});
test('selecting waiting-job history does not enroll it in automatic continuation',async()=>{
 const f=recoveryFixture();await saved(f,'waiting_job','waiting_job');const pending=f.c.pollWaiting();await tick();if(f.executions)await f.finish();await pending;assert.equal(f.executions,0);assert.equal(f.c.canPollWaiting(),false);
});
test('history context labels track live project changes without a ledger or provider request',async()=>{
 const f=recoveryFixture();await saved(f,'paused','pending');await f.c.loadHistory();const calls=f.calls.length;assert.equal(f.c.state.history[0].contextMatches,true);f.setRuntime('different-project');f.c.refreshContext();assert.equal(f.c.state.history[0].contextMatches,false);f.setRuntime('project1');f.c.refreshContext();assert.equal(f.c.state.history[0].contextMatches,true);assert.equal(f.calls.length,calls);assert.equal(f.executions,0);
});

// Independent review round 1: display reads must not retire already confirmed execution.
test('stopping an observation GET preserves an already confirmed runner and its result evidence',async()=>{
 const cache=new Map(),Controller=load(file,cache).AssistantTaskController,lease=load(path.join(__dirname,'executionLease.ts'),cache);
 const clone=value=>structuredClone(value),events=[];let ledger,entered,releaseAction,releaseRead,readSignal,control,effects=0,deferDisplay=false;
 const dispatched=new Promise(resolve=>{entered=resolve;}),actionDone=new Promise(resolve=>{releaseAction=resolve;});
 const client={list:async()=>[clone(ledger)],create:async plan=>{
   ledger={schema_version:1,id:ID,revision:1,status:'draft',...clone(plan),plan_sha256:'a'.repeat(64),approval:null,current_step_id:null,message:null,created_at:'now',updated_at:'now',steps:plan.steps.map((step,index)=>({...clone(step),id:'step_'+(index+1),status:'pending',attempt_id:null,receipt:null,error:null}))};return clone(ledger);
 },get:async(id,signal)=>{assert.equal(id,ID);if(deferDisplay&&signal){readSignal=signal;return new Promise(resolve=>{releaseRead=resolve;});}return clone(ledger);},event:async(id,event)=>{
   assert.equal(id,ID);assert.equal(event.expected_revision,ledger.revision);assert.equal(event.binding,ledger.binding);events.push(clone(event));
   const step=ledger.steps[0];
   if(event.type==='approve'){ledger.status='ready';ledger.approval={binding:ledger.binding,plan_sha256:ledger.plan_sha256,confirmed_at:'now'};}
   else if(event.type==='begin_step'){step.status='running';step.attempt_id=event.attempt_id;ledger.status='running';ledger.current_step_id=step.id;}
   else if(event.type==='complete_step'){assert.equal(step.attempt_id,event.attempt_id);assert.equal(step.status,'running');step.status='completed';step.receipt=clone(event.receipt);ledger.status='completed';ledger.current_step_id=null;}
   else assert.fail('unexpected confirmed-runner ledger event '+event.type);
   ledger.revision++;return clone(ledger);
 }};
 const c=new Controller({client,binding:()=> 'project1',connection:()=> 'account-model-1',ready:()=>true,mayStart:()=>true,onChange:()=>{},execute:async(_step,value)=>{effects++;control=value;entered();await actionDone;return{kind:'completed',receipt:{opened:true,job_id:'job_confirmed_read_stop'}};},reconcile:async()=>assert.fail('a stopped GET must not require reconciliation')});
 await c.propose(proposal);const approving=c.approve(ID,1);await dispatched;assert.equal(c.state.busy,true);assert.equal(lease.executionLeaseOwner(),'assistant');const version=c.version;
 deferDisplay=true;const oldObservation=clone(ledger),observing=c.observe();assert.equal(c.state.readPending,true);c.stopRead();const authorityAfterStop=control.isAuthorityCurrent(),leaseAfterStop=lease.executionLeaseOwner();const observed=await observing;
 releaseAction();const result=await approving;releaseRead({...oldObservation,revision:99});await tick();
 assert.equal(readSignal.aborted,true);assert.equal(c.state.readPending,false);assert.equal(authorityAfterStop,true);assert.equal(leaseAfterStop,'assistant');assert.equal(c.version,version);assert.equal(result.status,'completed');assert.equal(c.state.task.status,'completed');assert.equal(c.state.task.revision,4);assert.deepEqual(c.state.task.steps[0].receipt,{opened:true,job_id:'job_confirmed_read_stop'});assert.equal(effects,1);assert.deepEqual(events.map(event=>event.type),['approve','begin_step','complete_step']);assert.equal(lease.executionLeaseOwner(),null);assert.equal(c.state.busy,false);assert.equal(observed.fresh,false);assert.notEqual(c.state.task.revision,99);
});
for(const status of ['running','waiting_job','uncertain'])test(`new-request preparation retains ${status} ownership after an authoritative same-account runtime-binding transition`,async()=>{
 const f=recoveryFixture(),old=await saved(f,'paused','pending'),fresh={...structuredClone(f.task),revision:old.revision+2,binding:JSON.stringify(['project-after-owned-transition','account-model-1']),status:status==='uncertain'?'needs_reconciliation':status};
 Object.assign(fresh.steps[0],{status,attempt_id:'attempt_'+'d'.repeat(32),receipt:{job_id:'job_authoritative_transition'}});f.client.get=async()=>structuredClone(fresh);const calls=f.calls.length;
 assert.equal(await f.c.prepareNewRequest(old.id,old.revision),false);assert.equal(f.c.state.task.revision,fresh.revision);assert.equal(f.c.state.task.binding,fresh.binding);assert.equal(f.c.state.task.steps[0].status,status);assert.equal(f.c.ownsExecution(),true);assert.equal(f.c.state.planningNew,false);assert.equal(f.c.state.contextMatches,false);assert.match(f.c.state.error,/결과|확인/);
 const summary=f.c.summary();assert.equal(summary.current_context_matches,false);assert.equal(summary.execution_evidence_withheld,true);assert.equal(summary.steps,undefined);assert.doesNotMatch(JSON.stringify(summary),/job_authoritative_transition/);
 await assert.rejects(f.c.propose(proposal),/진행|확인/);assert.equal(f.calls.length,calls);assert.equal(f.executions,0);
});
test('preparation never adopts changed-account unresolved ownership from a fresh response',async()=>{
 const f=recoveryFixture(),old=await saved(f,'paused','pending'),before=structuredClone(f.c.state.task),fresh={...structuredClone(f.task),revision:old.revision+2,binding:JSON.stringify(['project-after-transition','foreign-account-model']),status:'waiting_job'};Object.assign(fresh.steps[0],{status:'waiting_job',attempt_id:'attempt_'+'e'.repeat(32),receipt:{job_id:'foreign_private_job'}});f.client.get=async()=>structuredClone(fresh);const calls=f.calls.length;
 assert.equal(await f.c.prepareNewRequest(old.id,old.revision),false);assert.deepEqual(f.c.state.task,before);assert.equal(f.c.state.planningNew,false);assert.equal(f.c.ownsExecution(),false);assert.doesNotMatch(JSON.stringify(f.c.summary()),/foreign_private_job|foreign-account/);assert.equal(f.calls.length,calls);assert.equal(f.executions,0);
});
for(const changed of ['id','hash','goal','args','approval'])test(`changed-binding unresolved response cannot replace the held immutable ${changed}`,async()=>{
 const f=recoveryFixture(),old=await saved(f,'paused','pending'),before=structuredClone(f.c.state.task),fresh={...structuredClone(f.task),revision:old.revision+1,binding:JSON.stringify(['project-after-transition','account-model-1']),status:'needs_reconciliation'};Object.assign(fresh.steps[0],{status:'uncertain',attempt_id:'attempt_'+'f'.repeat(32)});
 if(changed==='id')fresh.id='task_'+'f'.repeat(32);if(changed==='hash')fresh.plan_sha256='b'.repeat(64);if(changed==='goal')fresh.goal='다른 계획';if(changed==='args')fresh.steps[0].args={step:2};if(changed==='approval')fresh.approval={...fresh.approval,confirmed_at:'new-unreviewed-grant'};f.client.get=async()=>structuredClone(fresh);
 assert.equal(await f.c.prepareNewRequest(old.id,old.revision),false);assert.deepEqual(f.c.state.task,before);assert.equal(f.c.state.planningNew,false);assert.equal(f.executions,0);
});
test('a stopped observation cannot publish an already-resolved GET after its wait token is retired',async()=>{
 const f=recoveryFixture();await saved(f,'paused','pending');const old=structuredClone(f.c.state.task),version=f.c.version;let release;f.client.get=()=>new Promise(resolve=>{release=resolve;});const observing=f.c.observe();release({...old,revision:99,status:'completed'});await Promise.resolve();f.c.stopRead();const observed=await observing;
 assert.equal(f.c.version,version);assert.equal(observed.fresh,false);assert.equal(observed.revision,old.revision);assert.equal(f.c.state.task.revision,old.revision);assert.equal(f.c.state.readPending,false);
});
test('a cached draft may gain its valid original-plan human grant while retaining transitioned unresolved ownership',async()=>{
 const f=recoveryFixture(),old=await saved(f,'draft','pending'),fresh={...structuredClone(f.task),revision:old.revision+3,binding:JSON.stringify(['project-after-transition','account-model-1']),status:'waiting_job'};fresh.approval={binding:old.binding,plan_sha256:old.plan_sha256,confirmed_at:'2026-10-28T02:00:00Z'};Object.assign(fresh.steps[0],{status:'waiting_job',attempt_id:'attempt_'+'d'.repeat(32),receipt:{job_id:'job_draft_transition'}});f.client.get=async()=>structuredClone(fresh);
 assert.equal(await f.c.prepareNewRequest(old.id,old.revision),false);assert.equal(f.c.state.task.revision,fresh.revision);assert.deepEqual(f.c.state.task.approval,fresh.approval);assert.equal(f.c.ownsExecution(),true);await assert.rejects(f.c.propose(proposal),/진행|확인/);assert.equal(f.executions,0);
});
for(const changed of ['goal','args','approval'])test(`same-binding unresolved response cannot replace the held immutable ${changed}`,async()=>{
 const f=recoveryFixture(),old=await saved(f,'paused','pending'),before=structuredClone(f.c.state.task),fresh={...structuredClone(f.task),revision:old.revision+1,status:'needs_reconciliation'};Object.assign(fresh.steps[0],{status:'uncertain',attempt_id:'attempt_'+'f'.repeat(32)});
 if(changed==='goal')fresh.goal='다른 계획';if(changed==='args')fresh.steps[0].args={step:2};if(changed==='approval')fresh.approval={...fresh.approval,confirmed_at:'new-unreviewed-grant'};f.client.get=async()=>structuredClone(fresh);
 assert.equal(await f.c.prepareNewRequest(old.id,old.revision),false);assert.deepEqual(f.c.state.task,before);assert.equal(f.c.state.planningNew,false);assert.equal(f.executions,0);
});
