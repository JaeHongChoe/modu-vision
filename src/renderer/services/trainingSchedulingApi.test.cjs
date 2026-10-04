const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file,mocks={}){const name=path.resolve(__dirname,file),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(path.dirname(name));const req=m.require.bind(m);m.require=ref=>ref in mocks?mocks[ref]:req(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
test('training API serializes an explicit version, false queue, zero priority and runtime seconds',async()=>{
 const previous=global.fetch,calls=[];global.fetch=async(url,options)=>{calls.push({url,options});return{ok:true,json:async()=>({job_id:'job'}),headers:new Headers()};};
 try{const {api}=load('api.ts');await api.training.start({task:'classification',preset:'fast',dataset_path:'/synthetic',dataset_version_id:'version-1',queue:false,priority:0,max_runtime_s:90});
 const body=JSON.parse(calls[0].options.body);assert.equal(body.dataset_version_id,'version-1');assert.equal(body.queue,false);assert.equal(body.priority,0);assert.equal(body.max_runtime_s,90);
 await api.training.start({task:'classification',preset:'fast',dataset_path:'/synthetic'});const legacy=JSON.parse(calls[1].options.body);assert.equal('max_runtime_s' in legacy,false);assert.equal('queue' in legacy,false);assert.equal('priority' in legacy,false);
 }finally{global.fetch=previous;}
});
function store(remote=false){const calls=[];const {useTrainingStore}=load('../stores/useTrainingStore.ts',{
 '../services/api':{api:{training:{start:async body=>{calls.push(body);return{job_id:'job',status:'queued',phase:'queued',compute_profile_id:remote?'server':null};}}}},
 './useComputeStore':{useComputeStore:{getState:()=>({isLoaded:true,loadError:null,selectedProfileId:remote?'server':null,getSelectedProfile:()=>({name:'Synthetic server'}),probeResults:{server:{device_name:'cpu'}}})}},
 './useDatasetStore':{useDatasetStore:{getState:()=>({isSplitting:false})}},'../utils/trainingComputeReadiness':{trainingComputeReadiness:()=>({ready:true})},
 });return{store:useTrainingStore,calls};}
test('the actual training store carries local scheduling and preserves the queued response',async()=>{
 const f=store();await f.store.getState().startTraining('/synthetic','classification',undefined,{backbone:'resnet18'},{queue:false,priority:4,max_runtime_s:90});
 assert.equal(f.calls[0].queue,false);assert.equal(f.calls[0].priority,4);assert.equal(f.calls[0].max_runtime_s,90);assert.equal(f.store.getState().status,'queued');
});
test('invalid scheduling fails before store mutation or API submission',async()=>{
 for(const options of [{priority:11},{priority:1.5},{max_runtime_s:NaN},{max_runtime_s:0},{max_runtime_s:604801},{queue:'false'}]){
  const f=store();await assert.rejects(f.store.getState().startTraining('/synthetic','classification',undefined,{},options),/우선순위|시간 제한|대기열/);assert.equal(f.calls.length,0);assert.equal(f.store.getState().status,'idle');assert.equal(f.store.getState().jobId,null);
 }
});
test('remote runtime passes through but unsupported remote scheduling cannot silently submit',async()=>{
 for(const options of [{queue:false},{priority:1}]){const f=store(true);await assert.rejects(f.store.getState().startTraining('/synthetic','classification',undefined,{},options),/서버.*대기열/);assert.equal(f.calls.length,0);}
 const f=store(true);await f.store.getState().startTraining('/synthetic','classification',undefined,{},{max_runtime_s:60});assert.equal(f.calls[0].max_runtime_s,60);
});
