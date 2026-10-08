const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const nodes=t=>Array.isArray(t)?t.flatMap(nodes):t&&typeof t==='object'?[t,...nodes(t.props?.children)]:[];
const record=(id,job='candidate',extra={})=>({evaluation_id:id,created_at:1791150000,result:{job_id:job,task:'ocr',split:'test',sample_count:8,exact_match_accuracy:1},binding:{checkpoint_sha256:job==='incumbent'?'a'.repeat(64):'b'.repeat(64),dataset_fingerprint:'frozen-source',family_dataset_sha256:'heldout1',source_dataset_path:'/source'},...extra});
function fixture(config={}){
 let index=0,tree,dirty=true,generation=1,listener,resolveHistory,resolveApproval;
 const slots=[],effects=[],calls=[],reads=[];const project=config.project||{projectDir:'/project',project:{id:'owned',project_dir:'/project',source_dataset_dir:'/source',task:'segmentation'},task:'segmentation',isProjectBusy:false},dataset=config.dataset||{folderPath:'/source',datasetKey:'/source\0segmentation',isLoading:false,importError:null};
 const hook=object=>{const get=typeof object==='function'?object:()=>object;return Object.assign(selector=>selector(get()),{getState:get});};
 const react={useState(value){const i=index++;if(!(i in slots))slots[i]=typeof value==='function'?value():value;return[slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v;dirty=true;}];},useRef(value){const i=index++;return slots[i]??(slots[i]={current:value});},useEffect(fn,deps){const i=index++;if(JSON.stringify(slots[i]?.deps)!==JSON.stringify(deps)){const previous=slots[i];slots[i]={deps};effects.push(()=>{previous?.cleanup?.();slots[i].cleanup=fn();});}}};
 const rows=config.records||[record('candidate-eval'),record('incumbent-eval','incumbent'),record('wrong-cohort','incumbent',{binding:{...record('x','incumbent').binding,family_dataset_sha256:'other'}}),record('validation-eval','incumbent',{result:{...record('x','incumbent').result,split:'val'}})];
 const active=config.active===undefined?{job_id:'incumbent',checkpoint_sha256:'a'.repeat(64),valid:true}:config.active;
 const api={getApiPersistenceIdentity:()=> 'local:actor-'+generation,getProjectContextGeneration:()=>generation,subscribeProjectContext(fn){listener=fn;return()=>{};},request:async(url,options)=>{
  if(options){calls.push(JSON.parse(options.body));if(config.deferApproval)return new Promise(r=>{resolveApproval=r;});return {revision:{revision_id:'controlled'}};}
  reads.push(url);if(url.includes('model-deployments/active')){if(config.failActive)throw Error('Controlled active-state outage');return{active};}
  if(config.deferHistory)return new Promise(r=>{resolveHistory=r;});return{items:rows};
 }};
 const file=path.join(__dirname,'SpecializedApprovalPanel.tsx'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const req=m.require.bind(m);
 m.require=n=>n==='react'?react:n.includes('useProjectStore')?{useProjectStore:hook(project)}:n.includes('useDatasetStore')?{useDatasetStore:hook(dataset)}:n.endsWith('/services/api')?api:req(n);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{fileName:file,compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
 function render(){index=0;dirty=false;tree=m.exports.SpecializedApprovalPanel();effects.splice(0).forEach(f=>f());}
 async function settle(){for(let i=0;i<10;i++){if(dirty)render();await new Promise(setImmediate);if(!dirty)return;}}
 const all=()=>nodes(tree),field=label=>all().find(n=>n.props?.['aria-label']===label),change=async(label,value)=>{field(label).props.onChange({target:{value}});await settle();};
 const review=async()=>{all().find(n=>n.type==='input'&&n.props.type==='checkbox').props.onChange({target:{checked:true}});await change('특수 모델 검토자','fixture reviewer');await change('특수 모델 승인 근거','Synthetic handler test only');};
 const button=()=>all().find(n=>n.type==='button'&&n.props.children==='품질 기준 검증 후 승인');
 return{calls,reads,project,dataset,all,field,change,review,button,settle,render,switchActor(){generation++;listener?.();},resolveHistory(){resolveHistory({items:rows});},resolveApproval(){resolveApproval({revision:{revision_id:'stale-actor'}});}};
}
test('incumbent is selected from matching actual saved test records, never entered as text',async()=>{
 const f=fixture();await f.settle();await f.change('특수 모델 평가 승인','candidate-eval');const select=f.field('특수 모델 기준 평가');assert.equal(select.type,'select');assert.deepEqual(nodes(select).filter(n=>n.type==='option'&&n.props.value).map(n=>n.props.value),['incumbent-eval']);await f.change('특수 모델 기준 평가','incumbent-eval');await f.review();assert.equal(f.button().props.disabled,false);await f.button().props.onClick();await f.settle();assert.equal(f.calls[0].evaluation_id,'candidate-eval');assert.equal(f.calls[0].incumbent_evaluation_id,'incumbent-eval');
});
test('candidate change clears the prior incumbent and independent-review acknowledgement',async()=>{
 const f=fixture({records:[record('candidate-eval'),record('other-candidate'),record('incumbent-eval','incumbent')]});await f.settle();await f.change('특수 모델 평가 승인','candidate-eval');await f.change('특수 모델 기준 평가','incumbent-eval');await f.review();await f.change('특수 모델 평가 승인','other-candidate');assert.equal(f.field('특수 모델 기준 평가').props.value,'');assert.equal(f.all().find(n=>n.type==='input'&&n.props.type==='checkbox').props.checked,false);assert.equal(f.button().props.disabled,true);
});
test('unknown current approval state blocks a submission even with filled reviewer controls',async()=>{
 const f=fixture({failActive:true});await f.settle();await f.change('특수 모델 평가 승인','candidate-eval');await f.review();assert.equal(f.button().props.disabled,true);assert.equal(f.calls.length,0);
});
test('a forged incumbent ID cannot be posted outside the saved compatible options',async()=>{
 const f=fixture();await f.settle();await f.change('특수 모델 평가 승인','candidate-eval');await f.change('특수 모델 기준 평가','not-a-saved-record');await f.review();assert.equal(f.button().props.disabled,true);await f.button().props.onClick();assert.equal(f.calls.length,0);
});
test('late history from the previous actor cannot populate the new approval panel',async()=>{
 const f=fixture({deferHistory:true});await f.settle();f.switchActor();f.resolveHistory();await f.settle();assert.equal(nodes(f.field('특수 모델 평가 승인')).filter(n=>n.type==='option'&&n.props.value).length,0);assert.equal(f.field('특수 모델 평가 승인').props.value,'');assert.equal(f.button().props.disabled,true);
});
test('late approval response cannot show a revision in a different actor namespace',async()=>{
 const f=fixture({deferApproval:true});await f.settle();await f.change('특수 모델 평가 승인','candidate-eval');await f.change('특수 모델 기준 평가','incumbent-eval');await f.review();const pending=f.button().props.onClick();await f.settle();f.switchActor();await f.settle();f.resolveApproval();await pending;await f.settle();assert(!f.all().some(n=>n.props?.role==='status'&&String(n.props.children).includes('stale-actor')));
});
test('a failed active read can be retried explicitly and previous selections stay cleared',async()=>{
 const config={failActive:true};const f=fixture(config);await f.settle();assert.equal(f.button().props.disabled,true);config.failActive=false;
 const refresh=f.all().find(n=>n.type==='button'&&n.props['aria-label']==='특수 모델 평가 기록 새로 고침');assert(refresh,'a failed read needs an explicit retry');refresh.props.onClick();await f.settle();await f.change('특수 모델 평가 승인','candidate-eval');assert.equal(nodes(f.field('특수 모델 기준 평가')).filter(n=>n.type==='option'&&n.props.value).length,1);assert.equal(f.field('특수 모델 기준 평가').props.value,'');assert.equal(f.button().props.disabled,true);
});
test('refresh requires a fresh candidate and independent-review acknowledgement',async()=>{
 const f=fixture();await f.settle();await f.change('특수 모델 평가 승인','candidate-eval');await f.change('특수 모델 기준 평가','incumbent-eval');await f.review();const refresh=f.all().find(n=>n.type==='button'&&n.props['aria-label']==='특수 모델 평가 기록 새로 고침');assert(refresh);refresh.props.onClick();await f.settle();assert.equal(f.field('특수 모델 평가 승인').props.value,'');assert.equal(f.field('특수 모델 기준 평가').props.value,'');assert.equal(f.all().find(n=>n.type==='input'&&n.props.type==='checkbox').props.checked,false);assert.equal(f.button().props.disabled,true);
});
test('first approval has no incumbent while invalid existing approval never becomes first approval',async()=>{
 const first=fixture({active:null});await first.settle();await first.change('특수 모델 평가 승인','candidate-eval');await first.review();assert.equal(first.button().props.disabled,false);await first.button().props.onClick();await first.settle();assert.equal(first.calls[0].incumbent_evaluation_id,null);
 const invalid=fixture({active:{job_id:'incumbent',checkpoint_sha256:'a'.repeat(64),valid:false}});await invalid.settle();await invalid.change('특수 모델 평가 승인','candidate-eval');await invalid.change('특수 모델 기준 평가','incumbent-eval');await invalid.review();assert.equal(invalid.button().props.disabled,true);await invalid.button().props.onClick();assert.equal(invalid.calls.length,0);
});


function actualProjectRestoreFixture(){
 const project={id:'restore-owned',name:'Restore owned project',project_dir:'/project',source_dataset_dir:'/source',active_labelset_id:'default',task:'segmentation'};
 const values=new Map();globalThis.localStorage={getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value)};
 const value=initial=>({getState:()=>initial,setState:update=>Object.assign(initial,typeof update==='function'?update(initial):update)});
 let releaseImages,imagesEntered;const entered=new Promise(resolve=>imagesEntered=resolve),held=new Promise(resolve=>releaseImages=resolve);const events=[];
 const dataset=value({folderPath:'./datasets/synthetic',datasetKey:null,lastImportedKey:null,staleDatasetKeys:[],isLoading:false,isSplitting:false,isGenerating:false,importError:null,
  setFolderPath(folder){events.push('folder:'+folder);Object.assign(dataset.getState(),{folderPath:folder,datasetKey:null});},
  ensureImported:async task=>{events.push('ensure:'+task);Object.assign(dataset.getState(),{datasetKey:'/source\0segmentation',lastImportedKey:'/source\0segmentation'});}});
 const annotation=value({setImages:async()=>{events.push('annotation:'+store.getState().project?.id);imagesEntered();await held;return true;},setTask(){},isDirty:false});
 const file=path.join(__dirname,'../../stores/useProjectStore.ts'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const req=m.require.bind(m);
 const mocks={'../services/api':{api:{project:{getCurrent:async()=>project,list:async()=>({projects:[]}),acceptContext(selected,apply){assert.equal(selected,project);events.push('accepted-context');apply();}}},getApiPersistenceIdentity:()=> 'local',getProjectContext:()=>null,getProjectContextGeneration:()=>1},
  '../services/datasetWorkflow':{},'./useAnnotationStore':{useAnnotationStore:annotation},'./useDatasetStore':{useDatasetStore:dataset},'./useFlowchartStore':{useFlowchartStore:value({})},
  './useTrainingStore':{useTrainingStore:value({})},'./useInspectionRunStore':{useInspectionRunStore:value({})},'./useModelAssistRunStore':{useModelAssistRunStore:value({activeOperations:0})}};
 m.require=key=>Object.hasOwn(mocks,key)?mocks[key]:key==='./projectViewState'?loadProjectView():req(key);
 function loadProjectView(){const file=path.join(__dirname,'../../stores/projectViewState.ts'),sub=new Module(file,module);sub.filename=file;sub.paths=m.paths;sub._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return sub.exports;}
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);
 const store=m.exports.useProjectStore;loadProjectView().rememberProjectStep(globalThis.localStorage,project,'local',3);
 return{store,dataset,project,events,entered,release:releaseImages};
}

test('actual restored evaluation stage waits for source hydration before specialized reads',async()=>{
 const world=actualProjectRestoreFixture(),sync=world.store.getState().syncCurrentProject();await world.entered;
 assert.equal(world.store.getState().activeStep,3);assert.equal(world.store.getState().project,world.project);assert.equal(world.store.getState().isProjectBusy,true);
 assert.equal(world.dataset.getState().folderPath,'./datasets/synthetic');assert.deepEqual(world.events,['accepted-context','annotation:restore-owned']);
 const f=fixture({project:()=>world.store.getState(),dataset:world.dataset.getState(),active:null});await f.settle();
 const readsBeforeRelease=[...f.reads];world.release();await sync;f.render();await f.settle();
 assert.deepEqual(readsBeforeRelease,[],'restored accepted stage must not emit the default-source GET while setImages holds hydration');
 assert.deepEqual(f.reads,['/api/evaluation/history?source_dataset_path=%2Fsource&task=ocr','/api/model-deployments/active?source_dataset_path=%2Fsource&task=ocr']);
 assert.equal(world.store.getState().activeStep,3);assert.equal(world.store.getState().isProjectBusy,false);assert.equal(world.dataset.getState().datasetKey,'/source\0segmentation');
 await f.change('특수 모델 평가 승인','candidate-eval');await f.review();assert.equal(f.button().props.disabled,false);
});

test('busy wrong-source wrong-task unimported and failed source states emit no specialized reads or approvals',async()=>{
 const cases=[{project:{isProjectBusy:true}},{project:{task:'classification'}},{project:{projectDir:'/other'}},{project:{project:null}},
  {dataset:{folderPath:'./datasets/synthetic'}},{dataset:{datasetKey:null}},{dataset:{datasetKey:'/source\0classification'}},{dataset:{isLoading:true}},{dataset:{importError:'failed source import'}}];
 for(const state of cases){const f=fixture();Object.assign(f.project,state.project);Object.assign(f.dataset,state.dataset);await f.settle();
  assert.deepEqual(f.reads,[],JSON.stringify(state));const button=f.button();
  if(button){assert.equal(button.props.disabled,true);await button.props.onClick();}assert.deepEqual(f.calls,[]);}
});

test('a previously rendered approval handler rejects a pending source binding without rerender',async()=>{
 const f=fixture({active:null});await f.settle();await f.change('특수 모델 평가 승인','candidate-eval');await f.review();const submitted=f.button().props.onClick;
 assert.equal(f.button().props.disabled,false);f.project.isProjectBusy=true;await submitted();assert.deepEqual(f.calls,[],'capture must re-read actual busy/binding state before posting');
});

test('late saved history cannot populate a source that is still synchronizing',async()=>{
 const f=fixture({deferHistory:true});await f.settle();f.project.isProjectBusy=true;f.resolveHistory();await f.settle();
 assert.equal(nodes(f.field('특수 모델 평가 승인')).filter(n=>n.type==='option'&&n.props.value).length,0);assert.equal(f.button().props.disabled,true);
});

test('late approval result cannot show a revision while the source is synchronizing',async()=>{
 const f=fixture({deferApproval:true});await f.settle();await f.change('특수 모델 평가 승인','candidate-eval');await f.change('특수 모델 기준 평가','incumbent-eval');await f.review();
 const pending=f.button().props.onClick();await f.settle();f.project.isProjectBusy=true;f.resolveApproval();await pending;await f.settle();
 assert(!f.all().some(n=>n.props?.role==='status'&&String(n.props.children).includes('stale-actor')));assert.equal(f.button().props.disabled,true);
});
