const assert=require('node:assert/strict'),test=require('node:test'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file,mocks={}){const name=path.resolve(__dirname,file);assert.ok(fs.existsSync(name),'Project view implementation exists');const m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);const req=m.require.bind(m);m.require=key=>mocks[key]||(key==='./projectViewState'?load('./projectViewState.ts'):req(key));m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const project={id:'p',project_dir:'/workspace',source_dataset_dir:'/source',active_labelset_id:'one',task:'segmentation',name:'Project'};
test('remembered stage is bounded and isolated by project source labelset and API identity',()=>{
 const m=load('./projectViewState.ts'),rows=new Map(),storage={getItem:k=>rows.get(k)||null,setItem:(k,v)=>rows.set(k,v)};
 m.rememberProjectStep(storage,project,'local',5);assert.equal(m.readProjectStep(storage,project,'local'),5);
 for(const other of [{...project,id:'other'},{...project,project_dir:'/other'},{...project,source_dataset_dir:'/other'},{...project,active_labelset_id:'two'}])assert.equal(m.readProjectStep(storage,other,'local'),1);
 assert.equal(m.readProjectStep(storage,project,'shared:other'),1);assert.equal(m.readProjectStep(storage,{...project,source_dataset_dir:null},'local'),1);
 assert.equal(m.readProjectStep({getItem:()=> '99'},project,'local'),1);assert.doesNotThrow(()=>m.rememberProjectStep({setItem(){throw Error('storage unavailable');}},project,'local',3));
});
test('actual project store remembers navigation and restores it on a fresh app session',async()=>{
 const values=new Map();globalThis.localStorage={getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v)};
 const store=value=>({getState:()=>value,setState:update=>Object.assign(value,update)});
 const annotation=store({setImages:async()=>true,setTask(){},isDirty:false});
 const imports=[];const dataset=store({setFolderPath(){},isLoading:false,isSplitting:false,isGenerating:false,ensureImported:async task=>{imports.push(task);}});
 const mocks={'../services/api':{api:{project:{getCurrent:async()=>project,list:async()=>({projects:[]}),acceptContext(_project,apply){apply?.();}}},getApiPersistenceIdentity:()=> 'local'},'./useAnnotationStore':{useAnnotationStore:annotation},'../services/datasetWorkflow':{},'./useDatasetStore':{useDatasetStore:dataset},'./useFlowchartStore':{useFlowchartStore:store({})},'./useTrainingStore':{useTrainingStore:store({})},'./useInspectionRunStore':{useInspectionRunStore:store({})},'./useModelAssistRunStore':{useModelAssistRunStore:store({activeOperations:0})}};
 const first=load('./useProjectStore.ts',mocks).useProjectStore;first.setState({project,projectDir:project.project_dir});await first.getState().setStep(3);
 const fresh=load('./useProjectStore.ts',mocks).useProjectStore;await fresh.getState().syncCurrentProject();assert.equal(fresh.getState().activeStep,3);assert.deepEqual(imports,['segmentation']);assert.equal(fresh.getState().isProjectBusy,false);
});
