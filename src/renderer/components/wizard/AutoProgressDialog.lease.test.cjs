// Actual AutoProgressStore and lease, controlled pure runner. No app actions/services are executed.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const tick=()=>new Promise(setImmediate);
function fixture(){const cache=new Map(),calls=[];let resolveRun,rejectRun,deps,failSync=false;
 function load(file){if(cache.has(file))return cache.get(file).exports;const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));cache.set(file,m);const req=m.require.bind(m);m.require=name=>{
   if(name==='./autoProgress')return{runAutoProgress:(input,publish,options)=>{calls.push(['run',options]);deps=input;if(failSync)throw Error('runner failed');publish({state:'running',log:[],stop:null});return new Promise((resolve,reject)=>{resolveRun=value=>{publish(value);resolve(value);};rejectRun=reject;});}};
   if(name.endsWith('/executionLease'))return load(path.resolve(__dirname,'../../services/assistantTasks/executionLease.ts'));
   if(name.endsWith('/assistantTasks/appActions'))return{defaultAppSplit:()=>({train:80,val:10,test:10}),performAutoAppAction:async()=>{calls.push(['unexpected-app-write']);}};
   if(name.endsWith('/useProjectStore'))return{useProjectStore:{getState:()=>({activeStep:3,setStep:async()=>{}})}};
   if(name.endsWith('/assistContext'))return{currentAssistContext:()=>null};
   if(name.endsWith('/workflowEvidence'))return{bumpWorkflowEvidence(){}};
   if(name.startsWith('.'))return{};
   return req(name);
 };m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);return m.exports;}
 const lease=load(path.resolve(__dirname,'../../services/assistantTasks/executionLease.ts')),store=load(path.join(__dirname,'AutoProgressDialog.tsx')).useAutoProgressStore;
 return{lease,store,calls,resolve(state='done'){resolveRun({state,log:[],stop:null});},reject(){rejectRun(Error('runner rejected'));},deps:()=>deps,syncThrow(){failSync=true;}};
}
test('assistant ownership rejects automatic start with an explicit reason before any runner call',()=>{const f=fixture(),token=f.lease.acquireExecutionLease('assistant');f.store.getState().start();assert.equal(f.calls.length,0);assert.equal(f.store.getState().run.state,'failed');assert.match(f.store.getState().run.stop.reason,/도우미.*진행/);assert.equal(f.lease.executionLeaseOwner(),'assistant');f.lease.releaseExecutionLease(token);});
test('automatic execution acquires synchronously and releases its token only when the run settles',async()=>{const f=fixture();f.store.getState().start({deferDrafts:true});assert.equal(f.lease.executionLeaseOwner(),'automatic');assert.deepEqual(f.calls,[['run',{choices:{deferDrafts:true}}]]);assert.equal(f.lease.acquireExecutionLease('assistant'),null);f.store.getState().start();assert.equal(f.calls.length,1);f.store.getState().cancel();assert.equal(f.deps().cancelled(),true);assert.equal(f.lease.executionLeaseOwner(),'automatic','cancel only requests agent progression stop; an already-started action retains ownership');f.resolve('cancelled');await tick();assert.equal(f.lease.executionLeaseOwner(),null);assert.equal(f.store.getState().run.state,'cancelled');assert.equal(f.store.getState().cancelRequested,true);});
test('rejected and synchronous runner failures display the reason and always return the lease',async()=>{for(const synchronous of [false,true]){const f=fixture();if(synchronous)f.syncThrow();f.store.getState().start();if(!synchronous)f.reject();await tick();assert.equal(f.lease.executionLeaseOwner(),null);assert.equal(f.store.getState().run.state,'failed');assert.match(f.store.getState().run.stop.reason,/runner/);assert.ok(!f.calls.some(row=>row[0]==='unexpected-app-write'));}});
