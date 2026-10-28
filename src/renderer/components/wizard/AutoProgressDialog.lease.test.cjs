// Actual oneclick UI store at the task-controller boundary; no app/provider/model calls.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const tick=()=>new Promise(setImmediate);
function fixture(){const cache=new Map(),calls=[];let publish,resolve,reject;
 function load(file){if(cache.has(file))return cache.get(file).exports;const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));cache.set(file,m);const req=m.require.bind(m);m.require=name=>{
   if(name.endsWith('/useAssistantTaskStore'))return{startOneclickTask:observer=>{calls.push('start');publish=observer;return new Promise((yes,no)=>{resolve=yes;reject=no;});},pauseOneclickTask:async()=>{calls.push('pause');}};
   if(name.endsWith('/executionLease'))return load(path.resolve(__dirname,'../../services/assistantTasks/executionLease.ts'));
   if(name.endsWith('/assistantTasks/appActions'))return{defaultAppSplit:()=>({train:80,val:10,test:10})};
   if(name.endsWith('/useProjectStore'))return{useProjectStore:{getState:()=>({activeStep:3,setStep:async()=>{}})}};
   if(name.startsWith('.'))return{};return req(name);
 };m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);return m.exports;}
 const lease=load(path.resolve(__dirname,'../../services/assistantTasks/executionLease.ts')),store=load(path.join(__dirname,'AutoProgressDialog.tsx')).useAutoProgressStore;
 return{lease,store,calls,finish:()=>resolve(null),fail:()=>reject(Error('task ledger unavailable')),publish:status=>publish({status,steps:[{id:'s',action:'start_training',status:status==='completed'?'completed':'waiting_job'}],current_step_id:'s',message:'저장된 작업 상태'},null)};
}
test('assistant ownership rejects automatic start with an explicit reason before task creation',()=>{const f=fixture(),token=f.lease.acquireExecutionLease('assistant');f.store.getState().start();assert.deepEqual(f.calls,[]);assert.equal(f.store.getState().run.state,'failed');assert.match(f.store.getState().run.stop.reason,/도우미.*진행/);f.lease.releaseExecutionLease(token);});
test('oneclick invokes the common controller once, keeps waiting jobs active, and pauses only on explicit cancel',async()=>{const f=fixture();f.store.getState().start();f.store.getState().start();assert.deepEqual(f.calls,['start']);f.publish('waiting_job');assert.equal(f.store.getState().run.state,'running');f.store.getState().cancel();assert.deepEqual(f.calls,['start','pause']);f.publish('paused');assert.equal(f.store.getState().run.state,'cancelled');f.finish();await tick();});
test('durable completion and ledger errors have distinct visible outcomes',async()=>{const done=fixture();done.store.getState().start();done.publish('completed');done.finish();await tick();assert.equal(done.store.getState().run.state,'done');const broken=fixture();broken.store.getState().start();broken.fail();await tick();assert.equal(broken.store.getState().run.state,'failed');assert.match(broken.store.getState().run.stop.reason,/ledger unavailable/);});
