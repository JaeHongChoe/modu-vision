// Production task-store scheduling; controller, ledger/runtime and connection boundaries are synthetic.
// Timers are deterministic. No provider, account verification, network, model or app mutation is reachable.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
function fixture(t){
 const originalTimeout=global.setTimeout,originalClearTimeout=global.clearTimeout;
 const timers=new Map(),calls={polls:0,reads:0,lists:0,reconciles:0,invalidations:0,provider:0,executes:0};let timerSequence=0,owner=null,mode='waiting',releaseDeferred;
 global.setTimeout=(callback,delay)=>{const id=++timerSequence;timers.set(id,{callback,delay});return id;};
 global.clearTimeout=id=>{timers.delete(id);};
 t.after(()=>{timers.clear();global.setTimeout=originalTimeout;global.clearTimeout=originalClearTimeout;});
 const config={account:'account-a',generation:1,ready:true};
 const connection={config,configuring:false,busy:false,writing:false,pending:null};
 const task={id:'task_polling',status:'waiting_job',revision:3,current_step_id:'step_1',steps:[{id:'step_1',action:'start_training',status:'waiting_job',args:{},attempt_id:'attempt_polling',receipt:{job_id:'job_polling'},label:'학습'}]};
 const clone=value=>JSON.parse(JSON.stringify(value));
 const providerForbidden=()=>{calls.provider++;assert.fail('job polling must never verify or call a provider');};
 const client={list:async()=>{calls.lists++;return[clone(task)];},get:async id=>{calls.reads++;assert.equal(id,task.id);return clone(task);},event:async()=>assert.fail('the store scheduler cannot write ledger events directly'),create:async()=>assert.fail('polling cannot propose a task')};
 let instance;
 class ControlledController {
   constructor(deps){this.deps=deps;this.state={task:null,busy:false,loading:false,error:null};instance=this;}
   set(patch){this.state={...this.state,...patch};this.deps.onChange({...this.state});}
   async restore(){const rows=await this.deps.client.list();this.set({task:rows[0],busy:false,loading:false,error:null});return this.state.task;}
   async pollWaiting(){
     calls.polls++;
     if(!this.deps.ready()||!this.deps.mayStart()||this.state.busy||this.state.loading||this.state.task?.status!=='waiting_job')return null;
     if(mode==='throw')throw Error('controlled ledger polling failure');
     if(mode==='deferred'){await new Promise(resolve=>{releaseDeferred=resolve;});return null;}
     const saved=await this.deps.client.get(this.state.task.id);
     const result=await this.deps.reconcile(saved.steps[0],{isCurrent:()=>true,isPaused:()=>false});
     this.set({task:{...saved,status:result.kind==='completed'?'completed':'waiting_job'},busy:false,loading:false,error:null});
     return this.state.task;
   }
   invalidateConnection(){calls.invalidations++;this.set({task:null,loading:false,error:null});}
   refreshContext(){}
   blocksOtherWrites(){return false;}
   canPollWaiting(){return true;}
   summary(){return{loaded:!!this.state.task,status:this.state.task?.status};}
   async observe(){const saved=await this.deps.client.get(this.state.task.id);return{loaded:true,fresh:true,status:saved.status,revision:saved.revision};}
 }
 const filename=path.join(__dirname,'useAssistantTaskStore.ts'),m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(path.dirname(filename));const originalRequire=m.require.bind(m);
 const mocks={
  './useProjectStore':{useProjectStore:{}},'./useDatasetStore':{useDatasetStore:{}},'./useComputeStore':{useComputeStore:{}},
  './useTrainingStore':{useTrainingStore:{getState:()=>({setNextSettings:()=>assert.fail('polling cannot restore settings')})}},
  '../services/assistantTasks/settingsRecovery':{trainingSettingsRecovery:()=>null},
  '../services/assistantTasks/client':{assistantTaskClient:client},
  '../services/assistantTasks/controller':{AssistantTaskController:ControlledController},
  '../services/assistantTasks/connection':{taskConnectionKey:value=>value?.account||'none',taskConnectionGeneration:value=>value?.generation??0,taskConnectionReady:value=>value?.ready===true},
  '../services/assistantTasks/executionLease':{executionLeaseOwner:()=>owner},
  '../services/assistantTasks/runtime':{taskRuntimeBinding:()=> 'project',taskRuntimeGeneration:()=> 'generation',executeTaskAction:async()=>{calls.executes++;assert.fail('polling must only reconcile an existing job');},reconcileTaskAction:async step=>{calls.reconciles++;assert.equal(step.receipt.job_id,'job_polling');return{kind:mode==='completed'?'completed':'waiting_job',receipt:{job_id:'job_polling'}};},taskActionCatalog:()=>[],inspectTaskScreen:()=>({})},
  '../services/assistantContext':{reducePaths:text=>text},
  // Any future direct provider/verification dependency in this store must be an explicit test failure.
  '../services/hostAdapter':{host:{assistant:providerForbidden}},
 };
 m.require=name=>Object.hasOwn(mocks,name)?mocks[name]:name.startsWith('.')?assert.fail('unexpected task-store dependency: '+name):originalRequire(name);
 m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);
 const exported=m.exports,store=exported.useAssistantTaskStore;
 exported.configureTaskConnection(()=>connection);
 return{calls,config,connection,store,exported,timers,controller:()=>instance,setOwner:value=>{owner=value;},setMode:value=>{mode=value;},releaseDeferred:()=>releaseDeferred(),
   async restore(){await store.getState().restore();await flush();},
   fire(){assert.equal(timers.size,1,'there is exactly one scheduled poll');const [id,timer]=timers.entries().next().value;timers.delete(id);assert.ok(timer.delay>=1500&&timer.delay<=15000,'poll backoff stays bounded');timer.callback();},
   async fireAndFlush(){this.fire();await flush();},
 };
}
test('waiting-job polling reschedules when provider busy temporarily defers work without onChange',async t=>{const f=fixture(t);await f.restore();assert.equal(f.timers.size,1);f.connection.busy=true;await f.fireAndFlush();assert.equal(f.calls.polls,1);assert.equal(f.calls.reads,0);assert.equal(f.calls.reconciles,0);assert.equal(f.store.getState().task.status,'waiting_job');assert.equal(f.timers.size,1,'a deferred no-op must not consume the only timer');f.connection.busy=false;await f.fireAndFlush();assert.equal(f.calls.reads,1);assert.equal(f.calls.reconciles,1);assert.equal(f.timers.size,1);assert.equal(f.calls.provider,0);assert.equal(f.calls.executes,0);});
test('automatic writer ownership defers waiting-job polling and releasing it permits the next scheduled check',async t=>{const f=fixture(t);await f.restore();f.setOwner('automatic');await f.fireAndFlush();assert.equal(f.calls.polls,1);assert.equal(f.calls.reads,0);assert.equal(f.timers.size,1);f.setOwner(null);await f.fireAndFlush();assert.equal(f.calls.reads,1);assert.equal(f.calls.reconciles,1);assert.equal(f.timers.size,1);assert.equal(f.calls.provider,0);});
test('polling errors become visible and stop scheduling while completed jobs also retire the timer',async t=>{const f=fixture(t);await f.restore();f.setMode('throw');await f.fireAndFlush();assert.match(f.store.getState().error,/controlled ledger polling failure/);assert.equal(f.timers.size,0);assert.equal(f.calls.provider,0);f.setMode('completed');await f.restore();assert.equal(f.timers.size,1);await f.fireAndFlush();assert.equal(f.store.getState().task.status,'completed');assert.equal(f.timers.size,0);assert.equal(f.calls.executes,0);});
test('account change cancels a queued poll and a late deferred callback cannot resurrect it',async t=>{const f=fixture(t);await f.restore();f.config.account='account-b';f.config.generation++;f.exported.notifyTaskConnectionChange();assert.equal(f.calls.invalidations,1);assert.equal(f.timers.size,0);assert.equal(f.store.getState().task,null);assert.equal(f.calls.polls,0);f.config.account='account-a';f.config.generation++;f.exported.notifyTaskConnectionChange();await f.restore();f.setMode('deferred');f.fire();await flush();assert.equal(f.calls.polls,1);assert.equal(f.timers.size,0);f.config.account='account-c';f.config.generation++;f.exported.notifyTaskConnectionChange();f.releaseDeferred();await flush();assert.equal(f.timers.size,0);assert.equal(f.store.getState().task,null);assert.equal(f.calls.provider,0);assert.equal(f.calls.executes,0);});
test('unready connection stops background scheduling without contacting a provider for verification',async t=>{const f=fixture(t);await f.restore();f.config.ready=false;await f.fireAndFlush();assert.equal(f.calls.polls,1);assert.equal(f.calls.reads,0);assert.equal(f.timers.size,0);assert.equal(f.calls.provider,0);assert.equal(f.calls.executes,0);});
test('model status observation leaves the job poll and reconciliation untouched while cached context needs no read',async t=>{
 const f=fixture(t);await f.restore();assert.equal(typeof f.exported.assistantTaskTools.cachedStatus,'function');assert.equal(f.exported.assistantTaskTools.cachedStatus().status,'waiting_job');assert.equal(f.calls.reads,0);
 const value=await f.exported.assistantTaskTools.status();assert.equal(value.fresh,true);assert.equal(value.revision,3);assert.equal(f.calls.reads,1);assert.equal(f.calls.reconciles,0);assert.equal(f.calls.provider,0);assert.equal(f.calls.executes,0);assert.equal(f.timers.size,1);
});
test('a late error from a previous account cannot appear in the newly selected account',async t=>{
 const f=fixture(t);await f.restore();let reject;
 f.controller().pollWaiting=()=>new Promise((_resolve,fail)=>{reject=fail;});
 f.fire();await flush();f.config.account='new-account';f.config.generation++;f.exported.notifyTaskConnectionChange();
 reject(Error('old account private operation failed'));await flush();
 assert.equal(f.store.getState().task,null);assert.equal(f.store.getState().error,null);assert.equal(f.timers.size,0);
});

test('an old refresh tail cannot resume the newly selected account task',async t=>{
 const f=fixture(t);await f.restore();let release,resumes=0;
 f.controller().restore=()=>new Promise(r=>{release=r;});f.controller().resume=async()=>{resumes++;};
 const refreshing=f.store.getState().refresh();f.config.account='new-account';f.config.generation++;f.exported.notifyTaskConnectionChange();
 f.controller().set({task:{id:'new-account-approved-task',status:'needs_reconciliation'},busy:false,error:null});release(null);await refreshing;await flush();
 assert.equal(resumes,0);assert.equal(f.store.getState().task.id,'new-account-approved-task');
});
test('scheduler leaves an explicitly selected history record read-only until intentional continuation',async t=>{
 const f=fixture(t);f.controller().canPollWaiting=()=>false;await f.restore();assert.equal(f.timers.size,0);assert.equal(f.calls.polls,0);assert.equal(f.calls.reconciles,0);assert.equal(f.calls.provider,0);
 f.controller().canPollWaiting=()=>true;await f.restore();assert.equal(f.timers.size,1);await f.fireAndFlush();assert.equal(f.calls.reconciles,1);assert.equal(f.calls.provider,0);
});

// Review round 2 integrates the production controller, runner, read helper and store scheduler.
// Only ledger/runtime/connection boundaries and timers are controlled; no provider is available.
function productionFixture(t,{alreadyWaiting=false}={}){
 const originals={setTimeout:global.setTimeout,clearTimeout:global.clearTimeout},timers=new Map();let timerId=0,instance,deferDisplay=false,releaseRead,readSignal,releaseAction,entered,failLedger=false;
 global.setTimeout=(callback,delay)=>{const id=++timerId;timers.set(id,{callback,delay});return id;};global.clearTimeout=id=>timers.delete(id);
 t.after(()=>{timers.clear();global.setTimeout=originals.setTimeout;global.clearTimeout=originals.clearTimeout;});
 const clone=value=>structuredClone(value),ID='task_'+'a'.repeat(32),binding=JSON.stringify(['project','account-a']),calls={executes:0,reconciles:0,provider:0,events:[]};
 const plan={goal:'학습',binding,steps:[{action:'start_training',args:{},label:'학습'}]};
 const record=status=>({schema_version:1,id:ID,revision:status==='draft'?1:3,status,...clone(plan),plan_sha256:'a'.repeat(64),approval:status==='draft'?null:{binding,plan_sha256:'a'.repeat(64),confirmed_at:'now'},current_step_id:status==='draft'?null:'step_1',created_at:'now',updated_at:'now',message:null,steps:[{...clone(plan.steps[0]),id:'step_1',status:status==='draft'?'pending':'waiting_job',attempt_id:status==='draft'?null:'attempt_'+'b'.repeat(32),receipt:status==='draft'?null:{job_id:'job_original_wait'},error:null}]});
 let ledger=alreadyWaiting?record('waiting_job'):null;const actionEntered=new Promise(resolve=>{entered=resolve;}),actionDone=new Promise(resolve=>{releaseAction=resolve;});
 const client={list:async()=>ledger?[clone(ledger)]:[],create:async input=>{ledger={...record('draft'),...clone(input),steps:record('draft').steps};return clone(ledger);},get:async(id,signal)=>{assert.equal(id,ID);if(deferDisplay&&signal){readSignal=signal;return new Promise(resolve=>{releaseRead=resolve;});}if(failLedger)throw Error('controlled durable job ledger failure');return clone(ledger);},event:async(id,event)=>{
   assert.equal(id,ID);assert.equal(event.expected_revision,ledger.revision);assert.equal(event.binding,ledger.binding);calls.events.push(clone(event));const step=ledger.steps[0];
   if(event.type==='approve'){ledger.status='ready';ledger.approval={binding,plan_sha256:ledger.plan_sha256,confirmed_at:'now'};}
   else if(event.type==='begin_step'){assert.equal(step.status,'pending');step.status='running';step.attempt_id=event.attempt_id;ledger.status='running';ledger.current_step_id=step.id;}
   else if(event.type==='wait_step'||event.type==='reconcile_step'){assert.equal(event.wait_kind,'job');assert.equal(event.attempt_id,step.attempt_id);assert.equal(event.receipt.job_id,'job_original_wait');step.status='waiting_job';step.receipt=clone(event.receipt);ledger.status='waiting_job';}
   else assert.fail('unexpected job supervision event '+event.type);
   ledger.revision++;return clone(ledger);
 }};
 const cache=new Map();function loadTs(filename){if(cache.has(filename))return cache.get(filename).exports;const m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(path.dirname(filename));cache.set(filename,m);const base=m.require.bind(m);m.require=name=>name.startsWith('.')&&fs.existsSync(path.resolve(path.dirname(filename),name)+'.ts')?loadTs(path.resolve(path.dirname(filename),name)+'.ts'):base(name);m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);return m.exports;}
 const controllerFile=path.resolve(__dirname,'../services/assistantTasks/controller.ts'),Controller=loadTs(controllerFile).AssistantTaskController,lease=loadTs(path.resolve(__dirname,'../services/assistantTasks/executionLease.ts'));
 class CapturedController extends Controller{constructor(deps){super(deps);instance=this;}}
 const config={account:'account-a',generation:1,ready:true},connection={config,configuring:false,busy:false,writing:false,pending:null};
 const forbidden=()=>{calls.provider++;assert.fail('provider must never be called by task observation/polling');};
 const filename=path.join(__dirname,'useAssistantTaskStore.ts'),m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(__dirname);const originalRequire=m.require.bind(m);
 const mocks={'./useProjectStore':{useProjectStore:{}},'./useDatasetStore':{useDatasetStore:{}},'./useComputeStore':{useComputeStore:{}},'./useTrainingStore':{useTrainingStore:{getState:()=>({setNextSettings:()=>assert.fail('no settings restoration')})}},'../services/assistantTasks/settingsRecovery':{trainingSettingsRecovery:()=>null},'../services/assistantTasks/client':{assistantTaskClient:client},'../services/assistantTasks/controller':{AssistantTaskController:CapturedController},'../services/assistantTasks/connection':{taskConnectionKey:value=>value?.account||'none',taskConnectionGeneration:value=>value?.generation??0,taskConnectionReady:value=>value?.ready===true},'../services/assistantTasks/executionLease':lease,'../services/assistantTasks/runtime':{taskRuntimeBinding:()=> 'project',taskRuntimeGeneration:()=> 'generation',executeTaskAction:async(_step,control)=>{calls.executes++;entered(control);await actionDone;assert.equal(control.isAuthorityCurrent(),true);return{kind:'waiting_job',receipt:{job_id:'job_original_wait'}};},reconcileTaskAction:async(step,control)=>{calls.reconciles++;assert.equal(step.receipt.job_id,'job_original_wait');assert.equal(control.isAuthorityCurrent(),true);return{kind:'waiting_job',receipt:clone(step.receipt)};},taskActionCatalog:()=>[],inspectTaskScreen:()=>({})},'../services/assistantContext':{reducePaths:text=>text},'../services/hostAdapter':{host:{assistant:forbidden}}};
 m.require=name=>Object.hasOwn(mocks,name)?mocks[name]:name.startsWith('.')?assert.fail('unexpected production-store dependency '+name):originalRequire(name);m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);const exported=m.exports,store=exported.useAssistantTaskStore;exported.configureTaskConnection(()=>connection);
 const settle=async()=>{for(let index=0;index<80;index++)await Promise.resolve();};
 return{store,controller:()=>instance,calls,timers,actionEntered,releaseAction,lease,get ledger(){return ledger;},get readSignal(){return readSignal;},deferObservation(){deferDisplay=true;return exported.assistantTaskTools.status();},releaseObservation(){releaseRead({...clone(ledger),revision:99});},setLedgerFailure(value){failLedger=value;},settle,
   async restore(){await store.getState().restore();await settle();},
   async firePoll(){assert.equal(timers.size,1,'ordinary job supervision owns exactly one timer');const[id,timer]=timers.entries().next().value;timers.delete(id);timer.callback();await settle();},
 };
}
test('production store preserves job supervision when a busy confirmed action reaches waiting_job after observation stop',async t=>{
 const f=productionFixture(t);await f.store.getState().propose({goal:'학습',steps:[{action:'start_training',args:{},label:'학습'}]});const approving=f.store.getState().approve(f.ledger.id,1);await f.actionEntered;assert.equal(f.store.getState().busy,true);assert.equal(f.lease.executionLeaseOwner(),'assistant');
 const observing=f.deferObservation();f.store.getState().stopRead();const observed=await observing;f.releaseAction();await approving;await f.settle();assert.equal(f.readSignal.aborted,true);assert.equal(observed.read_error.code,'read_stopped');assert.equal(f.store.getState().error,null);assert.equal(f.store.getState().task.status,'waiting_job');assert.equal(f.calls.executes,1);assert.equal(f.timers.size,1);assert.equal(f.lease.executionLeaseOwner(),null);
 const attempt=f.ledger.steps[0].attempt_id;f.releaseObservation();await f.settle();assert.notEqual(f.store.getState().task.revision,99);await f.firePoll();assert.equal(f.calls.reconciles,1);assert.equal(f.calls.executes,1);assert.equal(f.ledger.steps[0].attempt_id,attempt);assert.equal(f.ledger.steps[0].receipt.job_id,'job_original_wait');assert.deepEqual(f.calls.events.map(event=>event.type),['approve','begin_step','wait_step','reconcile_step']);assert.equal(f.timers.size,1);assert.equal(f.calls.provider,0);
});
test('production store keeps an already waiting_job task polling when its observation GET is stopped',async t=>{
 const f=productionFixture(t,{alreadyWaiting:true});await f.restore();assert.equal(f.timers.size,1);const observing=f.deferObservation();f.store.getState().stopRead();const observed=await observing;await f.settle();assert.equal(f.readSignal.aborted,true);assert.equal(observed.read_error.code,'read_stopped');assert.equal(f.store.getState().error,null);assert.equal(f.timers.size,1);f.releaseObservation();await f.settle();assert.notEqual(f.store.getState().task.revision,99);await f.firePoll();assert.equal(f.calls.reconciles,1);assert.equal(f.calls.executes,0);assert.equal(f.calls.provider,0);assert.equal(f.timers.size,1);
});
test('production store preserves a genuine ledger error and its stopped polling gate across observation stop',async t=>{
 const f=productionFixture(t,{alreadyWaiting:true});await f.restore();f.setLedgerFailure(true);await f.firePoll();assert.match(f.store.getState().error,/controlled durable job ledger failure/);assert.equal(f.timers.size,0);const genuine=f.store.getState().error,observing=f.deferObservation();f.store.getState().stopRead();const observed=await observing;await f.settle();assert.equal(observed.read_error.code,'read_stopped');assert.equal(f.store.getState().error,genuine);assert.equal(f.timers.size,0);assert.equal(f.calls.reconciles,0);assert.equal(f.calls.executes,0);assert.equal(f.calls.provider,0);f.releaseObservation();await f.settle();assert.equal(f.store.getState().error,genuine);assert.equal(f.timers.size,0);
});
