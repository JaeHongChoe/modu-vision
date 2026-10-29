// Real mounted panel handlers; app stores and external run I/O are synthetic. No images/services/model execution.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const tick=()=>new Promise(setImmediate);
function fixture(){
 let cursor=0,dirty=true,tree,epoch=1,paused=false,current=true,authority=true,holdRead=false,releaseRead,afterCreated=()=>{},onPersisted=()=>{},runBehavior=null;
 const slots=[],effects=[],cache=new Map(),handlers=new Map(),calls=[];
 const react={useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],value=>{slots[i]=typeof value==='function'?value(slots[i]):value;dirty=true;}];},useRef(initial){const i=cursor++;return slots[i]??(slots[i]={current:initial});},useEffect(fn,deps){const i=cursor++,old=slots[i];if(!old||deps.some((v,j)=>!Object.is(v,old.deps[j]))){const slot=slots[i]={deps};effects.push(()=>{old?.cleanup?.();slot.cleanup=fn();});}}};
 const project={task:'detection',projectDir:'/p',project:{id:'p',project_dir:'/p',source_dataset_dir:'/s',active_labelset_id:'default',task:'detection'}},dataset={folderPath:'/s',datasetKey:'/s\0detection',hasSelectedFolder:true,importError:null,sourceSaveError:null,isLoading:false,isSplitting:false,lastImportedKey:'/s\0detection',staleDatasetKeys:[]},flow={contextRevision:1,executionChoiceOverride:'local_cpu',pipelineDirty:false,setExecutionChoice(){}};
 const store=state=>Object.assign(selector=>selector?selector(state):state,{getState:()=>state});
 const version={version_id:'v1',is_active:true},pipeline={id:'pipeline',name:'검사',nodes:[],edges:[]};
 const report={run_id:'run1',status:'completed',source_folder:'/s',task:'detection',scope:'test',saved_version_id:'v1',pipeline_id:'pipeline',pipeline_name:'검사',rows:[{image:{file_path:'/s/test.png',file_name:'test.png'},state:'OK'}]};
 const api={dataset:{getImages:async()=>({items:[],total:0})},flowchart:{listPipelines:async()=>({pipelines:[version]}),getPipelineVersion:async()=>{if(holdRead)await new Promise(resolve=>{releaseRead=resolve;});return pipeline;},getActivePipeline:async()=>pipeline},inspections:{listRuns:async()=>({runs:[]}),getRun:async()=>{onPersisted();return report;},createRun:async(pending,_pipeline,config)=>{calls.push(['create',pending.scope,config]);return{run_id:'run1',saved_version_id:'v1',pipeline_hash:'graph',model_sha256:{job:'sha'}};}}};
 const batchFile=path.resolve(__dirname,'batchInspection.ts');
 const mocks={react,'react/jsx-runtime':{jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props})},'lucide-react':{},
  '../../stores/useProjectStore':{useProjectStore:store(project)},'../../stores/useDatasetStore':{useDatasetStore:store(dataset)},'../../stores/useFlowchartStore':{useFlowchartStore:store(flow)},'../../stores/useComputeStore':{useComputeStore:store({profiles:[],selectedProfileId:null,isLoaded:true,load:async()=>{}})},'../../stores/useInspectionRunStore':{useInspectionRunStore:store({setRunning:value=>calls.push(['running',value])})},
  '../../stores/autoRunChannel':{registerAutoRun:()=>()=>{},registerTaskAutoRun:(kind,handler)=>{handlers.set(kind,handler);return()=>{if(handlers.get(kind)===handler)handlers.delete(kind);};}},
  '../../services/api':{api,getApiPersistenceIdentity:()=> 'local',getProjectContext:()=>({project_id:'p',actor_id:'a'}),getProjectContextGeneration:()=>epoch,getApiBaseUrl:async()=> 'http://synthetic',request:async()=>({labelset_id:'default',total:1,assignments:Object.fromEntries(['train','val','test','not_used','not_split'].map(x=>[x,{count:x==='test'?1:0}]))})},
  '../flowchart/flowExecution':{flowExecutionOptions:()=>({executionTarget:'local',device:'cpu'})},'../flowchart/flowHandoff':{activeSavedVersion:rows=>rows.find(row=>row.is_active),savedFlowIdentity:async()=>({pipelineHash:'graph'})},'../flowchart/flowchartStartup':{getFlowchartModelTask:()=>null},'../flowchart/SavedFlowIdentityCard':{},'./ReviewQueuePanel':{},'../training/useTaskHandoff':{useTaskHandoff:()=>null},'../common/EvidenceImageViewer':{},'../common/evidenceViewer':{},
 };
 function load(file){if(cache.has(file))return cache.get(file).exports;const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));cache.set(file,m);const req=m.require.bind(m);m.require=name=>Object.hasOwn(mocks,name)?mocks[name]:name.startsWith('.')?load(['.ts','.tsx'].map(ext=>path.resolve(path.dirname(file),name)+ext).find(fs.existsSync)):req(name);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);return m.exports;}
 const workflowProgress=load(path.resolve(__dirname,'../../stores/workflowProgress.ts'));mocks['../../stores/workflowProgress']=workflowProgress;
 const realBatch=load(batchFile);mocks['./batchInspection']={...realBatch,runBatchInspection:async(options,deps)=>{calls.push(['run',options.scope]);const pending={...report,scope:options.scope,status:'running'};const id=await deps.createRun(pending,pipeline);options.onRunCreated(id);afterCreated();assert.equal(options.stopReason(),null,'agent pause never cancels an already-created inspection');if(runBehavior)return runBehavior(options,report);return{...report,scope:options.scope};}};
 const panel=load(path.resolve(__dirname,'BatchInspectionPanel.tsx')).BatchInspectionPanel;
 function render(){cursor=0;dirty=false;tree=panel();effects.splice(0).forEach(fn=>fn());return tree;}
 const nodes=value=>Array.isArray(value)?value.flatMap(nodes):value&&typeof value==='object'?[value,...nodes(value.props?.children)]:[];
 return {calls,project,dataset,render,api,progress:()=>workflowProgress.inspectionProgressFor(JSON.stringify(['/s','/p','detection'])),progressFor:workflowProgress.inspectionProgressFor,duringRun:fn=>{runBehavior=fn;},async settle(){for(let i=0;i<10;i++){if(dirty)render();await tick();}return tree;},selectAll(){nodes(tree).find(node=>node.props?.id==='batch-inspection-scope').props.onChange({target:{value:'all'}});render();},run:()=>handlers.get('inspect')({isCurrent:()=>current,isPaused:()=>paused,isAuthorityCurrent:()=>authority}),hold(){holdRead=true;},release(){holdRead=false;releaseRead();},readHeld:()=>!!releaseRead,epoch(){epoch++;},pause(){paused=true;},afterCreated:fn=>{afterCreated=fn;},onPersisted:fn=>{onPersisted=fn;},invalidate(){current=false;},close(){slots.forEach(slot=>slot?.cleanup?.());}};
}
function environment(t){const oldWindow=global.window,oldStorage=Object.getOwnPropertyDescriptor(global,'localStorage');global.window={addEventListener(){},removeEventListener(){}};Object.defineProperty(global,'localStorage',{configurable:true,value:{getItem:()=>null}});t.after(()=>{global.window=oldWindow;if(oldStorage)Object.defineProperty(global,'localStorage',oldStorage);else delete global.localStorage;});}
test('task inspection explicitly runs the held-out test scope and returns its exact saved run ID even when manual scope was all',async t=>{environment(t);const f=fixture();await f.settle();f.selectAll();const result=await f.run();assert.equal(result.error,null);assert.equal(result.receipt.run_id,'run1');assert.equal(result.receipt.scope,'test');assert.equal(f.calls.find(c=>c[0]==='create')[1],'test');f.close();});
test('an A-B-A namespace change before run creation refuses the late task handler without writing',async t=>{environment(t);const f=fixture();await f.settle();f.hold();const run=f.run();while(!f.readHeld())await tick();f.epoch();f.epoch();f.release();const result=await run;assert.equal(result.receipt.not_started,true);assert.match(result.error,/대상/);assert.ok(!f.calls.some(c=>c[0]==='create'));f.close();});
test('pausing the assistant after run creation preserves the job and records its completed receipt',async t=>{environment(t);const f=fixture();await f.settle();f.afterCreated(()=>f.pause());const result=await f.run();assert.equal(result.receipt.run_id,'run1');assert.equal(result.receipt.status,'completed');assert.ok(!f.calls.some(c=>/stop|cancel/.test(c[0])));f.close();});
test('a source switch during final record read retains the already-created run ID for reconciliation',async t=>{environment(t);const f=fixture();await f.settle();f.onPersisted(()=>f.invalidate());const result=await f.run();assert.equal(result.receipt.run_id,'run1');assert.equal(result.receipt.scope,'test');f.close();});


// Exercise the mounted publisher with complete rows before the persisted run has finished.
test('mounted inspection publishes real lifecycle and clears a previous completion as soon as a new run starts',async t=>{
 environment(t);const f=fixture();try{
  await f.settle();await f.run();await f.settle();
  assert.deepEqual(f.progress(),{inspected:1,total:1,ng:0,status:'completed',isRunning:false});
  f.hold();const second=f.run();
  assert.notEqual(f.progress()?.status,'completed','the start handler retires previous completion before consumers render again');
  f.render();
  assert.notEqual(f.progress()?.status,'completed','the first render after starting cannot retain the previous completion');
  while(!f.readHeld())await tick();await f.settle();
  assert.notEqual(f.progress()?.status,'completed','starting again retires the previous completed evidence');
  assert.equal(f.progress()?.isRunning,true,'active preparation stays visible before a new report exists');
  f.release();await second;await f.settle();
  assert.deepEqual(f.progress(),{inspected:1,total:1,ng:0,status:'completed',isRunning:false});
 }finally{f.close();}
});

test('mounted inspection publishes a status-only persisted completion and runtime finish separately',async t=>{
 environment(t);const f=fixture();let next,finish;
 f.duringRun(async(options,report)=>{
  next=options.onUpdate;next({...report,status:'running'});
  await new Promise(resolve=>{finish=resolve;});return report;
 });
 try{
  await f.settle();const run=f.run();while(!finish)await tick();await f.settle();
  assert.deepEqual(f.progress(),{inspected:1,total:1,ng:0,status:'running',isRunning:true},'all rows done does not finish the saved run');
  next({run_id:'run1',source_folder:'/s',task:'detection',scope:'test',pipeline_id:'pipeline',pipeline_name:'검사',saved_version_id:'v1',status:'completed',rows:[{image:{file_path:'/s/test.png',file_name:'test.png'},state:'OK'}]});
  await f.settle();assert.equal(f.progress().status,'completed');assert.equal(f.progress().isRunning,true,'saved completion still waits for the current runtime');
  finish();await run;await f.settle();
  assert.deepEqual(f.progress(),{inspected:1,total:1,ng:0,status:'completed',isRunning:false});
 }finally{finish?.();f.close();}
});


test('mounted inspection never publishes the previous completed lifecycle in the next source scope',async t=>{
 environment(t);const f=fixture();try{
  await f.settle();await f.run();await f.settle();assert.equal(f.progress().status,'completed');
  f.dataset.folderPath='/other';f.dataset.datasetKey='/other\0detection';f.project.project.source_dataset_dir='/other';
  f.render();
  const nextScope=JSON.stringify(['/other','/p','detection']);
  assert.equal(f.progressFor(nextScope),null,'source reset happens after this render; previous report must stay out of the new scope');
  assert.equal(f.progress(),null,'cleanup retires the old scope');
  await f.settle();assert.equal(f.progressFor(nextScope),null,'no report for the new source has arrived');
 }finally{f.close();}
});
