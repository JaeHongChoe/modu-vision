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
   summary(){return{loaded:!!this.state.task,status:this.state.task?.status};}
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
