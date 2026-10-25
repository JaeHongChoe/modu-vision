const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
// Execute original TSX controller/panel and its three original children.
// Only React scheduling, stores and external request/compute boundaries are
// controlled. No browser, HTTP/backend, decoder, fitting/model or scan runs.
function load(file,mocks){
 const filename=path.resolve(__dirname,file),m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(__dirname);const original=m.require.bind(m);
 m.require=name=>{if(name in mocks)return mocks[name];const sibling=name.startsWith('.')?path.resolve(path.dirname(filename),name+'.ts'):null;return sibling&&fs.existsSync(sibling)?load(sibling,mocks):original(name);};
 m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{fileName:filename,compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);return m.exports;
}
const jsx=(type,props,key)=>({type,key,props:props||{},children:[props?.children].flat().filter(Boolean)});
const nodes=tree=>{const out=[];const walk=x=>{if(Array.isArray(x)){x.forEach(walk);return;}if(x&&typeof x==='object'){out.push(x);x.children?.forEach(walk);}};walk(tree);return out;};
function fixture(){
 let active;const frame=()=>({slots:[],cursor:0,pending:[],disposed:false,dirty:false});
 const react={
  useState(initial){const owner=active,i=owner.cursor++;if(!(i in owner.slots)){const slot={value:typeof initial==='function'?initial():initial};slot.set=value=>{if(!owner.disposed){const next=typeof value==='function'?value(slot.value):value;if(!Object.is(next,slot.value)){slot.value=next;owner.dirty=true;}}};owner.slots[i]=slot;}return[owner.slots[i].value,owner.slots[i].set];},
  useRef(initial){const owner=active,i=owner.cursor++;return owner.slots[i]??(owner.slots[i]={current:initial});},
  useEffect(fn,deps){const owner=active,i=owner.cursor++,prior=owner.slots[i];if(!prior||deps.length!==prior.deps.length||deps.some((v,j)=>!Object.is(v,prior.deps[j]))){const slot={deps:[...deps],cleanup:prior?.cleanup};owner.slots[i]=slot;owner.pending.push(()=>{slot.cleanup?.();slot.cleanup=fn();});}}
 };
 const render=(owner,call)=>{owner.cursor=0;owner.dirty=false;active=owner;try{return call();}finally{active=undefined;}};
 const flush=owner=>owner.pending.splice(0).forEach(fn=>fn());
 const dispose=owner=>{if(owner){owner.disposed=true;owner.slots.forEach(slot=>slot?.cleanup?.());assert.equal(owner.pending.length,0);}};
 const state={projectDir:'/project-A',project:{id:'A',source_dataset_dir:'/source-A',active_labelset_id:'labels-A'},task:'classification',language:'ko',isProjectBusy:false,setStep:async()=>{}};
 const compute={profiles:[],selectedProfileId:null,transportRevision:0,isLoaded:true,isLoading:false,probeResults:{}};
 const store=Object.assign(selector=>selector?selector(state):state,{getState:()=>state});
 const computeStore=Object.assign(selector=>selector?selector(compute):compute,{getState:()=>compute});
 const dataset={folderPath:'',totalImages:0,sourceImages:0,split:{train:0,val:0,test:0},datasetKey:'',isLoading:false,isSplitting:false};
 const datasetStore=Object.assign(selector=>{const value={...dataset,images:[{image_id:'own-image',file_name:'image.png',file_path:state.project.source_dataset_dir+'/image.png'}]};return selector?selector(value):value;},{getState:()=>dataset});
 const training={jobId:'',status:'idle',preset:'default',recoverActiveJob:async()=>{},refreshCurrentJob:async()=>{}};
 const requests=[],allCalls=[];
 const prepared=()=>state.projectDir+'/dataset/rotated_detection/'+state.project.active_labelset_id+'/'+encodeURIComponent(state.project.source_dataset_dir)+'/prepared';
 const samples=()=>['train','val','test'].map((split,i)=>({image:split+'.png',split,label:'saved-'+state.project.id,box:{cx:80,cy:60,width:40,height:20,angle_deg:0},direction_deg:15+i}));
 const manifest=dataset_path=>({dataset_path,sample_count:3,split_counts:{train:1,val:1,test:1},samples:samples()});
 const request=async(url,options)=>{
  allCalls.push({url,method:options?.method||'GET'});
  if(url==='/api/rotated-detection/datasets')return{datasets:[{dataset_path:prepared(),sample_count:3}]};
  if(url==='/api/rotated-detection/jobs')return{jobs:[]};
  if(url.startsWith('/api/rotated-detection/warm-start-parents?')||url.startsWith('/api/rotated-detection/fit-source?')){
   const u=new URL(url,'http://owned.invalid');requests.push({route:u.pathname,datasetPath:u.searchParams.get('dataset_path'),imagePath:u.searchParams.get('image_path'),imageSize:u.searchParams.get('image_size'),projectDir:state.projectDir,projectId:state.project.id,source:state.project.source_dataset_dir,labelset:state.project.active_labelset_id});
   if(u.pathname.endsWith('/fit-source'))return{source_size:[256,256],source_sha256:'a'.repeat(64),preview_data_url:'data:image/png;base64,b3duZWQ='};
   return{parents:[]};
  }
  throw Error('Undeclared external request: '+url);
 };
 const cap={prepared_input:true,architecture_key:'architecture',architectures:['rotated_detector'],metric_key:'mAP50',search_defaults:{architectures:['rotated_detector'],image_sizes:[64]}};
 const program={automated:{capabilities:async()=>({tasks:{rotated_detection:cap}}),jobs:async()=>({jobs:[]})}};
 const mocks={
  react,'react/jsx-runtime':{jsx,jsxs:jsx,Fragment:'fragment'},'lucide-react':{},
  '../../stores/useProjectStore':{useProjectStore:store},'../../stores/useDatasetStore':{useDatasetStore:datasetStore,readDatasetAuthority:()=> 'owned'},'../../stores/useTrainingStore':{useTrainingStore:()=>training},'../../stores/useComputeStore':{useComputeStore:computeStore},
  '../../services/api':{api:{},request,getApiPersistenceIdentity:()=> 'local'},
  '../../services/specializedApi':{specializedApi:{rotated:{models:async()=>({models:[]}),manifest:async p=>manifest(p)}}},
  '../../services/modelExecution':{},
  '../../services/modelTrainingProgram':{modelTrainingProgram:program,activeProgramJob:()=>false,programError:String},
  './useTaskHandoff':{useTaskHandoff:(...args)=>args.length?null:{family:'rotated_detection'}},
  './taskHandoff':{clearTaskHandoff:()=>{},selectHandoffRecord:()=>undefined},
  './TrainingRuntimeSettings':{useTrainingRuntime:()=>({value:'',error:null,onChange:()=>{},getOptions:()=>({})}),TrainingRuntimeSettings:()=>null},
  './ProgramWorkbenchControls':{},'../../utils/datasetSplitCapability':{isSplitUnavailable:()=>false},
  '../../utils/trainingComputeReadiness':{trainingComputeReadiness:()=>({ready:true})},
  './modelTrainingOptions':{modelChoices:{classification:[{value:'model'}]},dinoSyntheticDefaults:{},manualTrainingRecipe:()=>({}),trainingModelOverrides:()=>({})},
  '../wizard/TaskChangeImpactDialog':{taskPreviewScope:()=> 'scope'},'../common/errorActions':{},'./trainingLog':{},
  './TrainingSchedulingSettings':{defaultTrainingScheduling:{},trainingSchedulingOptions:()=>({})},'./useSelectedCoreJob':{useSelectedCoreJob:()=>({job:null,error:null})}
 };
 for(const name of ['../common/OperatorGuidanceBanner','../common/GuardrailBanner','../common/LedAnnunciator','./OscilloscopeLossCurve','./HardwareTelemetryPanel','./RecipePresetSelector','./OCRWorkbench','./DefectGANWorkbench','./EnhancementWorkbench','./ModelFamilyCatalog','./DinoSyntheticOptions','./PatchClassificationWorkbench','./RotationWorkbench','./TrainingPreparationPanel','./JobProgressView','../runtime/OperationsObservabilityPanel','./ModelExecutionEvidence'])mocks[name]={};
 const {WarmStartSelector}=load('WarmStartSelector.tsx',mocks);mocks['./WarmStartSelector']={WarmStartSelector};
 const {AutoDLWorkbench}=load('AutoDLWorkbench.tsx',mocks);mocks['./AutoDLWorkbench']={AutoDLWorkbench};
 const {RotatedBoxFitting}=load('RotatedBoxFitting.tsx',mocks);mocks['./RotatedBoxFitting']={RotatedBoxFitting};
 const {RotatedDetectionPanel}=load('RotatedDetectionPanel.tsx',mocks);mocks['./RotatedDetectionPanel']={RotatedDetectionPanel};
 const {TrainingController}=load('TrainingController.tsx',mocks);
 const controller=frame();let workbench,children=[],key,mounts=0,panelTree,childTrees=[];
 function commit(){
  const host=render(controller,()=>TrainingController()),element=nodes(host).find(n=>n.type===RotatedDetectionPanel);assert.ok(element,'Original controller renders rotated workbench');
  if(!workbench||element.key!==key){children.forEach(dispose);dispose(workbench);workbench=frame();children=[frame(),frame(),frame()];key=element.key;mounts++;}
  panelTree=render(workbench,()=>RotatedDetectionPanel(element.props));
  const kinds=[RotatedBoxFitting,WarmStartSelector,AutoDLWorkbench];
  childTrees=kinds.map((kind,i)=>{const element=nodes(panelTree).find(n=>n.type===kind);assert.ok(element,'Original rotated child is rendered');return render(children[i],()=>kind(element.props));});
  // React passive commits are child-first. A parent's later reset cannot
  // retract an old path already sent by an original child in this commit.
  children.forEach(flush);flush(workbench);flush(controller);
 }
 async function settle(){for(let i=0;i<8;i++){commit();await new Promise(setImmediate);}}
 const f={state,compute,requests,allCalls,prepared,commit,settle,mounts:()=>mounts,panel:()=>panelTree,fitting:()=>childTrees[0],
  async selectImage(image){const selector=nodes(childTrees[0]).find(n=>n.type==='select');assert.ok(selector);selector.props.onChange({target:{value:image}});await settle();},
  table:()=>nodes(panelTree).find(n=>n.type==='textarea'),
  close(){children.forEach(dispose);dispose(workbench);dispose(controller);}
 };return f;
}
for(const intent of ['project','source','labelset'])test(intent+' A B A never sends a retained prepared path or selected image under the next namespace',async t=>{
 const f=fixture();t.after(()=>f.close());await f.settle();
 const initial={projectDir:f.state.projectDir,project:{...f.state.project}},firstPrepared=f.prepared(),firstImage=f.state.project.source_dataset_dir+'/image.png';
 await f.selectImage(firstImage);
 assert.ok(f.requests.some(r=>r.datasetPath===firstPrepared&&r.imageSize===null));
 assert.ok(f.requests.some(r=>r.datasetPath===firstPrepared&&r.imageSize==='64'));
 assert.ok(f.requests.some(r=>r.imagePath===firstImage));
 f.requests.length=0;
 if(intent==='project'){f.state.projectDir='/project-B';f.state.project={id:'B',source_dataset_dir:'/source-B',active_labelset_id:'labels-B'};}
 if(intent==='source')f.state.project={...f.state.project,source_dataset_dir:'/source-B'};
 if(intent==='labelset')f.state.project={...f.state.project,active_labelset_id:'labels-B'};
 f.commit();t.diagnostic(JSON.stringify({phase:'first-new-scope-child-first-commit',intent,requests:f.requests}));
 assert.equal(f.requests.some(r=>r.datasetPath===firstPrepared||r.imagePath===firstImage),false,'Old namespace paths must never be dispatched in the new context before passive reset');
 await f.settle();
 const nextPrepared=f.prepared(),nextImage=f.state.project.source_dataset_dir+'/image.png';
 assert.ok(f.requests.some(r=>r.datasetPath===nextPrepared&&r.imageSize===null));
 assert.ok(f.requests.some(r=>r.datasetPath===nextPrepared&&r.imageSize==='64'));
 assert.equal(nodes(f.fitting()).some(n=>n.type==='svg'),false);
 await f.selectImage(nextImage);assert.ok(f.requests.some(r=>r.imagePath===nextImage));
 f.requests.length=0;f.state.projectDir=initial.projectDir;f.state.project=initial.project;f.commit();
 t.diagnostic(JSON.stringify({phase:'return-A-child-first-commit',intent,requests:f.requests}));
 assert.equal(f.requests.some(r=>r.datasetPath===nextPrepared||r.imagePath===nextImage),false,'Returning A must not send the previous namespace state');
 await f.settle();assert.ok(f.requests.some(r=>r.datasetPath===firstPrepared));assert.equal(nodes(f.fitting()).some(n=>n.type==='svg'),false);
 assert.equal(f.mounts(),3,'Each namespace gets a fresh original workbench before child effects');
 assert.ok(f.allCalls.every(r=>r.method==='GET'),'Regression never starts mutation, training or fitting work');
});
test('same namespace rerender preserves current edited truth and selected original image',async t=>{
 const f=fixture();t.after(()=>f.close());await f.settle();await f.selectImage(f.state.project.source_dataset_dir+'/image.png');
 const draft='owned unsaved operator text';f.table().props.onChange({target:{value:draft}});await f.settle();
 const mounts=f.mounts();f.requests.length=0;await f.settle();
 assert.equal(f.mounts(),mounts);assert.equal(f.table().props.value,draft);
 assert.equal(nodes(f.fitting()).find(n=>n.type==='select').props.value,f.state.project.source_dataset_dir+'/image.png');
 assert.equal(f.requests.length,0,'Stable namespace rerender adds no automatic request');
});
