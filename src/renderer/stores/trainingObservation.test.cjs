const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function store(reply){
 const name=path.join(__dirname,'useTrainingStore.ts'),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);
 const req=m.require.bind(m);const mock={
  '../services/api':{api:{training:{getStatus:async()=>{if(reply instanceof Error)throw reply;return reply;}}}},
  './useComputeStore':{useComputeStore:{getState:()=>({profiles:[]})}},
  './useDatasetStore':{useDatasetStore:{getState:()=>({})}},
  '../utils/trainingComputeReadiness':{trainingComputeReadiness:()=>({ready:true})}
 };m.require=ref=>ref in mock?mock[ref]:req(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports.useTrainingStore;
}
const observation={cause:'time_limit',next_action:'시간 제한을 늘리세요.',cancel:{stage:'released',complete:true,reservation_released:true,exit_confirmed:true}};
test('core polling retains the recorded next action, cancellation and queue fields',async()=>{
 const s=store({job_id:'owned',status:'aborted',observation,queue_position:null,wait_reason:null});s.setState({jobId:'owned',isCurrentData:true,status:'running',isTraining:true});
 await s.getState().refreshCurrentJob();assert.deepEqual(s.getState().jobObservation,observation);assert.equal(s.getState().isTraining,false);
 const q=store({job_id:'owned',status:'queued',queue_position:2,wait_reason:'device_reserved'});q.setState({jobId:'owned',isCurrentData:true});await q.getState().refreshCurrentJob();assert.equal(q.getState().jobQueuePosition,2);assert.equal(q.getState().jobWaitReason,'device_reserved');
});
test('a failed current read and a reset discard old evidence rather than claiming release',async()=>{
 const s=store(new Error('Controlled missing status'));s.setState({jobId:'owned',jobComputeProfileId:'server',jobObservation:observation,jobQueuePosition:2,jobWaitReason:'priority'});
 await s.getState().refreshCurrentJob();assert.equal(s.getState().jobObservation,null);assert.equal(s.getState().jobQueuePosition,null);assert.equal(s.getState().status,'disconnected');
 s.setState({jobObservation:observation,jobQueuePosition:2});s.getState().resetTraining();assert.equal(s.getState().jobObservation,null);assert.equal(s.getState().jobQueuePosition,null);
});
test('active job recovery retains observation from the same polled identity',async()=>{
 const s=store({job_id:'owned',status:'stopping',observation,queue_position:1,wait_reason:'priority'});await s.getState().recoverActiveJob();assert.equal(s.getState().jobId,'owned');assert.deepEqual(s.getState().jobObservation,observation);assert.equal(s.getState().jobQueuePosition,1);
});
test('the core preserves the actual error catalog detail instead of a generic failure',async()=>{
 const s=store({job_id:'owned',status:'failed',error:{error_code:'OUT_OF_MEMORY',details:'Controlled allocation failed'},observation:{cause:'out_of_memory',next_action:'배치를 줄이세요.'}});
 s.setState({jobId:'owned',isCurrentData:true});await s.getState().refreshCurrentJob();assert.equal(s.getState().startError,'Controlled allocation failed');
});
