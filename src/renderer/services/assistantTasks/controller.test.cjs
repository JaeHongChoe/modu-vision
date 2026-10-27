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
test('restoration displays an unfinished same-account task read-only after the current project changes',async()=>{
 const f=fixture();await f.c.propose(proposal);f.setRuntime('different-project');
 const restored=await f.c.restore();assert.equal(restored.id,ID);assert.match(f.c.state.error,/프로젝트|자료/);
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
 const f=fixture();await f.c.propose(proposal);f.task.status='completed';f.setRuntime('project-reset-inputs');
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
