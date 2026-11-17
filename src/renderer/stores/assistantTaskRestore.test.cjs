// Real controller + task store + panel mount; only ledger/runtime boundaries are synthetic.
// A restore is GET-only: no provider, account, native grant or application action exists here.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const tick=async()=>{for(let i=0;i<4;i++)await new Promise(setImmediate);};
function load(file,mocks={},cache=new Map()){
 if(cache.has(file))return cache.get(file).exports;
 const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));cache.set(file,m);const original=m.require.bind(m);
 m.require=name=>{if(Object.hasOwn(mocks,name))return mocks[name];if(name.startsWith('.')){const target=path.resolve(path.dirname(file),name)+'.ts';if(fs.existsSync(target))return load(target,mocks,cache);}return original(name);};
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{fileName:file,compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,file);return m.exports;
}
function fixture(t){
 const lists=[],calls={get:0,create:0,event:0,execute:0,reconcile:0,provider:0},contextListeners=new Set();
 let runtime='before-hydration',generation=1,instance,exported,cleanup;
 const connection={config:{account:'account-model-1',generation:1,ready:true},configuring:false,busy:false,writing:false,pending:null};
 const source=()=>{const listeners=new Set();return{getState:()=>({project:null,projectDir:null}),subscribe:fn=>{listeners.add(fn);return()=>listeners.delete(fn);},emit:()=>{for(const listener of listeners)listener();}};};
 const project=source(),dataset=source(),training=source(),compute=source();
 const forbidden=kind=>()=>{calls[kind]++;throw Error('Read-only restore attempted '+kind);};
 const saved=(binding=runtime,revision=3)=>({schema_version:1,id:'task_'+'a'.repeat(32),revision,status:'waiting_human',goal:'이전 폴더 선택 대기',binding:JSON.stringify([binding,connection.config.account]),
  plan_sha256:'a'.repeat(64),approval:{binding:JSON.stringify([binding,connection.config.account]),plan_sha256:'a'.repeat(64),confirmed_at:'now'},authorization:null,
  current_step_id:'step_1',created_at:'now',updated_at:'now',message:'자료 폴더 선택을 취소했습니다.',steps:[{id:'step_1',action:'import_dataset',args:{},label:'폴더 선택',status:'waiting_human',attempt_id:'attempt_a',receipt:{not_started:true},error:null}]});
 const client={list:signal=>new Promise((resolve,reject)=>lists.push({signal,resolve,reject})),get:async()=>{calls.get++;return saved();},create:forbidden('create'),event:forbidden('event')};
 const ActualController=load(path.resolve(__dirname,'../services/assistantTasks/controller.ts')).AssistantTaskController;
 class Controller extends ActualController{constructor(deps){super(deps);if(!instance)instance=this;}}
 const mocks={
  './useProjectStore':{useProjectStore:project},'./useDatasetStore':{useDatasetStore:dataset},'./useTrainingStore':{useTrainingStore:training},'./useComputeStore':{useComputeStore:compute},
  '../services/assistantTasks/client':{assistantTaskClient:client},'../services/assistantTasks/controller':{AssistantTaskController:Controller},
  '../services/assistantTasks/connection':{taskConnectionKey:c=>c?.account??'none',taskConnectionGeneration:c=>c?.generation??0,taskConnectionReady:c=>Boolean(c?.ready)},
  '../services/assistantTasks/runtime':{taskRuntimeBinding:()=>runtime,taskRuntimeGeneration:()=>generation,executeTaskAction:forbidden('execute'),reconcileTaskAction:forbidden('reconcile'),taskActionCatalog:()=>[],inspectTaskScreen:()=>({})},
  '../services/assistantTasks/settingsRecovery':{trainingSettingsRecovery:()=>null},'../services/assistantTasks/qualification':{qualifyCompletedTaskStep:forbidden('execute')},
  '../services/assistantContext':{reducePaths:v=>v},'../services/api':{getProjectContext:()=>null,getApiPersistenceIdentity:()=> 'fixture',subscribeProjectContext:fn=>{contextListeners.add(fn);return()=>contextListeners.delete(fn);}},
  './workflowEvidence':{subscribeWorkflowEvidence:()=>()=>{}},'../services/hostAdapter':{host:{assistant:forbidden('provider')}},
 };
 exported=load(path.join(__dirname,'useAssistantTaskStore.ts'),mocks);const store=exported.useAssistantTaskStore;
 exported.configureTaskConnection(()=>connection);
 const hook=Object.assign(selector=>selector(store.getState()),{getState:store.getState});
 function mount(){
  cleanup?.();const effects=[];
  const react={createElement:(type,props,...children)=>({type,props:{...props,children}}),useEffect:effect=>effects.push(effect)};
  const panel=load(path.resolve(__dirname,'../components/assistant/AssistantTaskPanel.tsx'),{
   react:{__esModule:true,default:react,...react},'../../stores/useAssistantTaskStore':{...exported,useAssistantTaskStore:hook},
   '../../stores/useProjectStore':{useProjectStore:project},'./AssistantTaskCard':{AssistantTaskCard:'TaskCard'},
  }).AssistantTaskPanel;
  panel({ready:connection.config.ready&&!connection.configuring,blocked:false,connectionKey:connection.config.account});
  const cleanups=effects.map(effect=>effect()).filter(value=>typeof value==='function');cleanup=()=>{for(const finish of cleanups)finish();};
 }
 t.after(async()=>{cleanup?.();store.getState().stopRead();await tick();});
 return{store,exported,lists,calls,saved,mount,connection,controller:()=>instance,
  context(next=runtime,channel='project'){runtime=next;generation++;if(channel==='api')for(const notify of contextListeners)notify(null);else({project,dataset,training,compute}[channel]).emit();},
  notify(){project.emit();dataset.emit();},unmount(){cleanup?.();cleanup=null;},
  noWrites(){assert.deepEqual(calls,{get:calls.get,create:0,event:0,execute:0,reconcile:0,provider:0});},
 };
}

test('a discarded hydration restore gets one fresh current-context list without replaying work',async t=>{
 const f=fixture(t);f.mount();await tick();assert.equal(f.lists.length,1);
 const old=f.saved();f.context('hydrated-source');f.notify();f.notify();
 f.lists[0].resolve([old]);await tick();
 assert.equal(f.lists.length,2,'the successful but stale list must be replaced by one fresh GET');
 assert.equal(f.store.getState().task,null,'the first authority snapshot remains discarded');
 f.lists[1].resolve([f.saved()]);await tick();
 assert.equal(f.store.getState().task.goal,'이전 폴더 선택 대기');
 assert.equal(f.store.getState().task.status,'waiting_human');assert.equal(f.lists.length,2);f.noWrites();
});

for(const channel of ['dataset','training','compute','api'])test(`${channel} context notification refreshes only the discarded saved-state read`,async t=>{
 const f=fixture(t);f.mount();await tick();const old=f.saved();
 f.context('accepted-'+channel,channel);f.lists[0].resolve([old]);await tick();
 assert.equal(f.lists.length,2);assert.equal(f.store.getState().task,null);
 const fresh=f.saved();fresh.status='paused';f.lists[1].resolve([fresh]);await tick();
 assert.equal(f.store.getState().task.status,'paused');assert.equal(f.lists.length,2);f.noWrites();
});

test('A to B to A hydration cannot publish the first snapshot and notifications coalesce',async t=>{
 const f=fixture(t);f.mount();await tick();const old=f.saved('before-hydration',99);
 f.context('temporary-source');f.context('before-hydration');for(let i=0;i<10;i++)f.notify();
 f.lists[0].resolve([old]);await tick();assert.equal(f.lists.length,2);assert.equal(f.store.getState().task,null);
 f.lists[1].resolve([f.saved('before-hydration',4)]);await tick();
 assert.equal(f.store.getState().task.revision,4);for(let i=0;i<10;i++)f.notify();await tick();assert.equal(f.lists.length,2);f.noWrites();
});

test('a stable empty ledger is read once without a timer or notification loop',async t=>{
 const f=fixture(t);f.mount();await tick();f.lists[0].resolve([]);await tick();
 for(let i=0;i<10;i++)f.notify();await tick();
 assert.equal(f.lists.length,1);assert.equal(f.store.getState().task,null);assert.equal(f.store.getState().error,null);f.noWrites();
});

for(const action of ['stopRead','unmount'])test(`${action} retires automatic restore intent without a hidden retry`,async t=>{
 const f=fixture(t);f.mount();await tick();const old=f.saved();
 if(action==='stopRead')f.store.getState().stopRead();else f.unmount();
 f.context('hydrated-after-stop');f.lists[0].resolve([old]);await tick();
 assert.equal(f.lists.length,1);assert.equal(f.store.getState().task,null);f.noWrites();
});

test('a real read error remains visible and context changes do not silently retry it',async t=>{
 const f=fixture(t);f.mount();await tick();f.lists[0].reject(Error('controlled saved-task ledger unavailable'));await tick();
 assert.match(f.store.getState().error,/ledger unavailable/);f.context('another-source');await tick();
 assert.equal(f.lists.length,1);assert.equal(f.store.getState().task,null);f.noWrites();
});

test('a timed-out read does not restart or accept its late reply',{timeout:1000},async t=>{
 const f=fixture(t);f.controller().deps.readTimeoutMs=5;f.mount();await tick();const old=f.saved();
 await new Promise(resolve=>setTimeout(resolve,15));await tick();assert.match(f.store.getState().error,/초|응답|시간/);
 f.context('another-source');f.lists[0].resolve([old]);await tick();
 assert.equal(f.lists.length,1);assert.equal(f.store.getState().task,null);f.noWrites();
});

test('explicit history selection retires automatic intent and keeps its selected record',async t=>{
 const f=fixture(t);f.mount();await tick();f.lists[0].resolve([]);await tick();
 await f.store.getState().restore('task_'+'a'.repeat(32));
 assert.equal(f.store.getState().task.revision,3);f.context('different-source');await tick();
 assert.equal(f.store.getState().task.revision,3);assert.equal(f.lists.length,1);assert.equal(f.calls.get,1);f.noWrites();
});

for(const blocked of ['planningNew','busy','existingTask'])test(`${blocked} never starts an automatic ledger read`,async t=>{
 const f=fixture(t);f.controller().set(blocked==='existingTask'?{task:f.saved()}:{[blocked]:true});f.mount();
 f.context('hydrated-source');await tick();assert.equal(f.lists.length,0);f.noWrites();
});

test('a changed account authority requires a fresh panel intent and rejects the old completion',async t=>{
 const f=fixture(t);f.mount();await tick();const old=f.saved('before-hydration',99);
 f.connection.config.generation++;f.exported.notifyTaskConnectionChange();await tick();
 f.lists[0].resolve([old]);await tick();assert.equal(f.lists.length,1);assert.equal(f.store.getState().task,null);
 assert.equal(f.store.getState().restoreConnectionRevision,1);
 f.mount();await tick();assert.equal(f.lists.length,2);f.lists[1].resolve([f.saved('before-hydration',4)]);await tick();
 assert.equal(f.store.getState().task.revision,4);f.noWrites();
});
