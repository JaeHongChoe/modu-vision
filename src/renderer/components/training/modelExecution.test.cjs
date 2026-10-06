const assert=require('node:assert/strict'),test=require('node:test'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function hostAdapterModule(){const file=path.join(__dirname,'..','..','services','hostAdapter.ts');const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
function load(file,mocks={}){const name=path.resolve(__dirname,file);assert.ok(fs.existsSync(name),'Workflow implementation exists');const m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);const req=m.require.bind(m);m.require=key=>mocks[key]||(key==='../../services/hostAdapter'?hostAdapterModule():req(key));m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{fileName:name,compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,name);return m.exports;}
test('patch evaluation uses the selected explicit recipe and never its legacy local evaluate alias',async()=>{
 const calls=[],target={isLoaded:true,selectedProfileId:'patch-worker',profiles:[{id:'patch-worker'}],transportRevision:0};
 const api={request:async(url,options)=>{calls.push([url,JSON.parse(options.body)]);return{metrics:{evaluated_split:'test'}};},getApiPersistenceIdentity:()=> 'owned-local',getProjectContextGeneration:()=>0};
 const execution=load('../../services/modelExecution.ts',{'./api':api,'../stores/useComputeStore':{useComputeStore:{getState:()=>target}},'../stores/useProjectStore':{useProjectStore:{getState:()=>({projectDir:'/owned',project:{id:'p',source_dataset_dir:'/source'}})}}});
 const program=load('../../services/modelTrainingProgram.ts',{'./api':api,'./modelExecution':execution}).modelTrainingProgram;
 await program.patch.evaluate('job_patch','/owned/prepared','cpu');
 assert.deepEqual(calls[0],['/api/model-execution/recipes',{task:'patch_classification',stage:'evaluate',execution_target:'selected_compute',compute_profile_id:'patch-worker',device:'cpu',params:{job_id:'job_patch',dataset_path:'/owned/prepared',split:'test'}}]);
 target.selectedProfileId=null;await program.patch.evaluate('job_patch','/owned/prepared','mps');assert.equal(calls[1][1].execution_target,'local');assert.equal(calls[1][1].device,'mps');
 assert.deepEqual(calls.map(row=>row[0]),['/api/model-execution/recipes','/api/model-execution/recipes']);
});
test('remote submission keeps full selected configuration and parent while cancellation uses execution identity',async()=>{
 const calls=[];const m=load('../../services/modelExecution.ts',{'./api':{request:async(url,options)=>{calls.push([url,JSON.parse(options?.body||'{}')]);return{job_id:'job_execute',model_id:'saved',task:'rotation',status:'queued',current_epoch:0,total_epochs:3};}},'../stores/useComputeStore':{useComputeStore:{getState:()=>({selectedProfileId:'worker',profiles:[{id:'worker',gpu_selector:null}],isLoaded:true})}},'../stores/useProjectStore':{useProjectStore:{getState:()=>({project:{source_dataset_dir:'/source'}})}}});
 const row=await m.submitModelTraining('rotation',{dataset_path:'/prepared',epochs:3,batch_size:7,image_size:128,width:8,learning_rate:.002,warm_start_job_id:'parent',device:'cpu'},()=>assert.fail('remote selected'));
 assert.equal(row.job_id,'saved');assert.equal(row.execution_job_id,'job_execute');
 assert.deepEqual(calls[0][1].config_overrides,{epochs:3,batch_size:7,image_size:128,width:8,learning_rate:.002});assert.equal(calls[0][1].warm_start_job_id,'parent');assert.equal(calls[0][1].family_dataset_path,'/prepared');assert.equal(calls[0][1].device,'cpu');
 await m.controlModelTraining(row,'cancel',()=>assert.fail('local cancel'));
 assert.equal(calls[1][0],'/api/compute/jobs/job_execute/cancel');
});
test('remote specialist task opens its saved model and input rather than execution ID',()=>{
 const m=load('taskHandoff.ts');const values=new Map(),storage={getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
 const scope={projectDir:'/project',project:{id:'p',source_dataset_dir:'/source',active_labelset_id:'one'}};
 const row={key:'training:worker:job_execute',id:'job_execute',kind:'training',task:'ocr',status:'completed',source:'/source',labelset:'one',transport:'worker',raw:{execution_job_id:'job_execute',model_id:'saved',dataset_path:'/prepared'}};
 const value=m.saveTaskHandoff(storage,scope,row);assert.equal(value.jobId,'saved');assert.equal(value.executionJobId,'job_execute');assert.equal(value.datasetPath,'/prepared');
});
test('specialist remote scheduling is serialized at admission level and never leaks into model configuration',async()=>{
 const calls=[];const m=load('../../services/modelExecution.ts',{'./api':{request:async(url,options)=>{calls.push(JSON.parse(options.body));return{job_id:'exec',model_id:'saved',status:'queued'};}},'../stores/useComputeStore':{useComputeStore:{getState:()=>({selectedProfileId:'worker',profiles:[{id:'worker'}],isLoaded:true})}},'../stores/useProjectStore':{useProjectStore:{getState:()=>({project:{source_dataset_dir:'/source'}})}}});
 await m.submitModelTraining('patch_classification',{dataset_path:'/patch',epochs:3,queue:false,priority:0,max_runtime_s:90},()=>assert.fail('remote selection'));
 assert.deepEqual(calls[0].config_overrides,{epochs:3});assert.equal(calls[0].queue,false);assert.equal(calls[0].priority,0);assert.equal(calls[0].max_runtime_s,90);
 await m.submitModelTraining('ocr',{dataset_path:'/ocr',epochs:2},()=>assert.fail('remote selection'));
 for(const key of ['queue','priority','max_runtime_s'])assert.equal(key in calls[1],false);
});
test('flow binding retains existing DAG and selected identity; GAN never becomes an inspection node',()=>{
 const m=load('../flowchart/modelFlowHandoff.ts',{'../training/taskHandoff':load('taskHandoff.ts')});const original={id:'flow',name:'Existing',nodes:[{id:'input',position:{x:0,y:0},data:{node_type:'input'}},{id:'old',position:{x:300,y:0},data:{node_type:'inspection',task:'classification',model_job_id:'old_model'}}],edges:[{id:'e',source:'input',target:'old'}]};
 const before=JSON.stringify(original);const candidate={job_id:'saved',task:'ocr',source_dataset_path:'/source',capabilities:{role:'inspection'}};
 const result=m.bindModelToFlow(original,candidate);assert.equal(JSON.stringify(original),before);assert.equal(result.pipeline.nodes.length,3);assert.equal(result.pipeline.edges.length,1);assert.equal(result.pipeline.nodes.at(-1).data.model_job_id,'saved');assert.equal(result.pipeline.nodes[1].data.model_job_id,'old_model');
 assert.throws(()=>m.bindModelToFlow(original,{...candidate,task:'defect_gan'}),/생성/);
 assert.throws(()=>m.bindModelToFlow(original,candidate,'old'),/호환/);
});

test('automated search refuses implicit local execution when a server is selected',async()=>{
 let calls=0;const m=load('../../services/modelExecution.ts',{'./api':{request:()=>assert.fail('no request')},'../stores/useComputeStore':{useComputeStore:{getState:()=>({selectedProfileId:'worker'})}},'../stores/useProjectStore':{useProjectStore:{getState:()=>({})}}});
 await assert.rejects(()=>m.requireLocalSearch(()=>{calls++;}),/로컬/);assert.equal(calls,0);
});

test('every specialist preserves all visible settings in remote payload and local selection invokes only its local route',async()=>{
 for(const family of ['patch_classification','rotation','ocr','rotated_detection','enhancement','defect_gan']){
  const calls=[],state={selectedProfileId:'worker',profiles:[{id:'worker',gpu_selector:'GPU-1'}],isLoaded:true};let localCalls=0;
  const m=load('../../services/modelExecution.ts',{'./api':{request:async(url,options)=>{calls.push(JSON.parse(options.body));return{job_id:'execution',model_id:'candidate',task:family,status:'queued'};}},'../stores/useComputeStore':{useComputeStore:{getState:()=>state}},'../stores/useProjectStore':{useProjectStore:{getState:()=>({project:{source_dataset_dir:'/source'}})}}});
  const config={epochs:2,batch_size:3,image_size:64,learning_rate:.002,seed:11,architecture:'selected',augmentation_profile:'industrial'};
  await m.submitModelTraining(family,{...config,dataset_path:'/prepared',device:'cpu',warm_start_job_id:'parent'},async()=>{localCalls++;return{job_id:'local'};});
  assert.deepEqual(calls[0].config_overrides,config);assert.equal(calls[0].task,family);assert.equal(calls[0].device,'cpu');assert.equal(calls[0].warm_start_job_id,'parent');assert.equal(localCalls,0);
  state.selectedProfileId=null;const row=await m.submitModelTraining(family,{...config,dataset_path:'/prepared',device:'cpu'},async()=>{localCalls++;return{job_id:'local'};});assert.equal(row.job_id,'local');assert.equal(localCalls,1);assert.equal(calls.length,1);
 }
});
test('offered model is isolated by project source labelset server and API identity',()=>{
 const m=load('../flowchart/modelFlowHandoff.ts',{'../training/taskHandoff':load('taskHandoff.ts')});const values=new Map(),storage={getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
 const scope={projectDir:'/project',project:{id:'p',source_dataset_dir:'/source',active_labelset_id:'one'},selectedProfileId:'worker',apiTransportIdentity:'shared:https://api.example'};
 m.saveModelFlowHandoff(storage,scope,'ocr','candidate','/prepared');assert.equal(m.readModelFlowHandoff(storage,scope).modelId,'candidate');
 for(const other of [{...scope,projectDir:'/other'},{...scope,project:{...scope.project,source_dataset_dir:'/other'}},{...scope,project:{...scope.project,active_labelset_id:'two'}},{...scope,selectedProfileId:'other'},{...scope,apiTransportIdentity:'shared:https://other.example'}])assert.equal(m.readModelFlowHandoff(storage,other),null);
});
test('preparation sends the selected preset and checks again after a preset change',async()=>{
 const calls=[],effects=[],refs=[];let cursor=0;const jsx=(type,props)=>({type,props:props||{}});
 const react={useState:initial=>[initial,()=>{}],useRef:initial=>{const i=cursor++;return refs[i]||(refs[i]={current:initial});},useEffect:(fn,deps)=>effects.push(deps)};
 const m=load('TrainingPreparationPanel.tsx',{'react':react,'react/jsx-runtime':{jsx,jsxs:jsx},'lucide-react':{},'../../services/api':{getProjectContextGeneration:()=>0,subscribeProjectContext:()=>()=>{},getApiPersistenceIdentity:()=> 'local',request:async(url,options)=>{calls.push(JSON.parse(options.body));return{ready:false};}},'../../stores/useProjectStore':{useProjectStore:Object.assign(()=>({project:{source_dataset_dir:'/source',active_labelset_id:'one'},projectDir:'/project',setStep:async()=>{}}),{getState:()=>({project:{source_dataset_dir:'/source',active_labelset_id:'one'},projectDir:'/project'})})},'../../stores/useDatasetStore':{useDatasetStore:()=>({totalImages:2})},'../../stores/useComputeStore':{useComputeStore:Object.assign(()=>({selectedProfileId:'server',transportRevision:1,profiles:[]}),{getState:()=>({selectedProfileId:'server',transportRevision:1,profiles:[]})})},'./trainingWorkflow':{familyPreparation:{detection:{label:'Detection'}}},'./ProgramWorkbenchControls':{programButton:''},'./TeamTrainingReadiness':{}});
 function nodes(tree){return [tree,...[tree?.props?.children].flat().filter(value=>value&&typeof value==='object').flatMap(nodes)];}
 for(const preset of ['precision','fast']){cursor=0;const tree=m.TrainingPreparationPanel({family:'detection',model:'faster_rcnn',preset});await nodes(tree).find(node=>node.type==='button'&&node.props.children?.includes('서버 준비 검사')).props.onClick();}
 assert.deepEqual(calls.map(row=>row.preset),['precision','fast']);assert.notEqual(effects[1][0],effects[3][0]);
 const controller=fs.readFileSync(path.join(__dirname,'TrainingController.tsx'),'utf8');assert.match(controller,/<TrainingPreparationPanel[^>]*preset=\{preset\}/);
});
