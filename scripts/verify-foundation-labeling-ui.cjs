const assert=require('node:assert/strict');
const fs=require('node:fs');const Module=require('node:module');const path=require('node:path');const test=require('node:test');const ts=require('typescript');
const source=path.resolve(__dirname,'../src/renderer/components/labeling/foundationRequest.ts');
const mod=new Module(source,module);mod.filename=source;mod.paths=Module._nodeModulePaths(path.dirname(source));
mod._compile(ts.transpileModule(fs.readFileSync(source,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,source);
const {foundationAllowed,buildFoundationRequest,regionExample,brushEditTarget,buildModelRequest,labelCategoryPalette}=mod.exports;
test('labeling scope invalidates same-project source/task/labelset changes before async readback',()=>{
  const {labelingScope}=mod.exports;
  const current={projectDir:'/project',task:'segmentation',project:{id:'own',task:'segmentation',active_labelset_id:'default',source_dataset_dir:'/source-a'}};
  const captured=labelingScope(current);
  assert.equal(labelingScope({...current,project:{...current.project}}),captured);
  for(const change of [{source_dataset_dir:'/source-b'},{task:'detection'},{active_labelset_id:'other'},{id:'foreign'}]){
    assert.notEqual(labelingScope({...current,project:{...current.project,...change}}),captured);
  }
});
const ready={providers:{foundation:{ready:true},grounding_dino:{ready:false}},labelset_id:'v2',labelset_version:'hash'};
test('SAM readiness is independent of legacy grounding flag but text needs both',()=>{
  assert.equal(foundationAllowed(ready,''),true);assert.equal(foundationAllowed(ready,'scratch.'),false);
  assert.equal(foundationAllowed({...ready,ready:true,providers:{...ready.providers,foundation:{ready:false}}},''),false);
});
test('full long prompt, negative region examples, native points and six size controls survive request',()=>{
  const text='microscopic ceramic scratch. '.repeat(500);
  const opts={label:'Scratch',prompt:text,device:'cpu',output_geometry:'mask',threshold:.2,text_threshold:.25,max_candidates:30,
    min_area:12,max_area:500,min_width:3,max_width:70,min_height:2,max_height:60,
    positive_examples:[{image_path:'/a.png',roi:[1,2,5,8]}],negative_examples:[{image_path:'/b.png',roi:[9,2,15,8]}],points:[{x:1234,y:5678,label:1}],boxes:[[100,200,400,800]],suggestion_model_id:'feature_a'};
  const body=buildFoundationRequest('/native.png',opts,ready);
  assert.equal(body.prompt,text);assert.equal(body.labelset_version,'hash');assert.equal(body.backend,'foundation');
  for(const key of Object.keys(opts))assert.deepEqual(body[key],opts[key]);
});
test('selected polygon bounds form exact native region example',()=>{
  assert.deepEqual(regionExample('/a.png',{type:'polygon',polygon:[[100,300],[400,200],[300,600]]}),{image_path:'/a.png',roi:[100,200,400,600]});
  assert.equal(regionExample('/a.png',{type:'tag'}),null);
});
test('mask editor chooses selected mask before category mask and preserves other classes',()=>{
  const anns=[{id:'a',type:'brush_mask',category_id:3},{id:'b',type:'brush_mask',category_id:7},{id:'c',type:'polygon',category_id:7}];
  assert.equal(brushEditTarget(anns,'b',3).id,'b');assert.equal(brushEditTarget(anns,'c',7).id,'b');assert.equal(brushEditTarget(anns,null,3).id,'a');
  assert.equal(brushEditTarget(anns,null,8),undefined);
});
test('completed model single and batch requests retain selected device and all size bounds',()=>{
  const options={device:'mps',min_area:1,max_area:40,min_width:2,max_width:20,min_height:3,max_height:30};
  const body=buildModelRequest('job_a','/native.png',.4,['scratch'],options);
  assert.deepEqual(body,{job_id:'job_a',image_path:'/native.png',threshold:.4,keywords:['scratch'],...options});
});
test('imported sparse class palette controls editing and retains unused class identities',()=>{
  const palette=labelCategoryPalette([{id:2,name:'Scratch',color:'#ffffff'},{id:7,name:'Crack',color:'#ffffff'}],
    [{type:'brush_mask',category_id:7,label:'Scratch',color:'#ef4444'}],[{id:0,name:'background',color:'#000000'},{id:7,name:'Scratch',color:'#ef4444'},{id:255,name:'unused',color:'#00ff00'}]);
  assert.equal(palette.find(c=>c.name==='Scratch').id,7);assert.equal(palette.find(c=>c.name==='unused').id,255);
  assert.equal(new Set(palette.map(c=>c.id)).size,palette.length);
});

function mountScopedComponent(component, providers) {
  const slots=[], effects=[]; let cursor=0;
  const react={
    createElement:(type,props,...children)=>({type,props:{...props,children}}),
    useState(initial){const i=cursor++;if(!(i in slots))slots[i]=initial;return[slots[i],value=>{slots[i]=typeof value==='function'?value(slots[i]):value;}];},
    useMemo(fn){cursor++;return fn();},
    useEffect(fn,deps){const i=cursor++;const previous=slots[i];if(!previous||deps.some((value,n)=>value!==previous.deps[n])){previous?.cleanup?.();const entry={deps};slots[i]=entry;effects.push(()=>{entry.cleanup=fn();});}},
  };
  const project={projectDir:'/project',task:'segmentation',project:{id:'own',task:'segmentation',active_labelset_id:'default',source_dataset_dir:'/source-a'}};
  const dataset={images:[],folderPath:'/source-a'};
  const annotation={currentImage:null,annotations:[],selectedAnnotationId:null,setActiveTool(){},activeCategory:{id:1,name:'scratch'},categories:[]};
  const store=state=>Object.assign(selector=>selector?selector(state):state,{getState:()=>state});
  const stubs={
    react,'lucide-react':new Proxy({},{get:(_,key)=>String(key)}),
    '../../stores/useProjectStore':{useProjectStore:store(project)},
    '../../stores/useDatasetStore':{useDatasetStore:store(dataset)},
    '../../stores/useAnnotationStore':{useAnnotationStore:store(annotation)},
    '../../stores/useModelAssistRunStore':{useModelAssistRunStore:store({begin(){},end(){}})},
    '../../stores/useFoundationPromptStore':{useFoundationPromptStore:store({points:[],boxes:[],pointLabel:1,setPointLabel(){},clear(){},bind(){},bindProject(){}})},
    '../../services/hostAdapter':{host:{selectFolder:async()=>{throw new Error('Native folder selection must not run in scoped component tests');}}},
    '../../services/api':{api:{labelSuggestions:providers,dataset:{}}},
    '../../services/datasetWorkflow':{datasetWorkflow:{},workflowError:error=>error.message||String(error)},
    '../../services/foundationLabelingApi':{foundationLabelingApi:providers,labelingJobActive:status=>['queued','running','cancelling'].includes(status)},
    './foundationRequest':mod.exports,
  };
  const filename=path.resolve(__dirname,`../src/renderer/components/labeling/${component}.tsx`);
  const loaded=new Module(filename,module);loaded.filename=filename;loaded.paths=Module._nodeModulePaths(path.dirname(filename));
  loaded.require=specifier=>Object.hasOwn(stubs,specifier)?stubs[specifier]:require(specifier);
  loaded._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,filename);
  global.localStorage={getItem:()=>null,setItem(){}};
  global.window={setInterval:()=>1,clearInterval(){}};
  return{
    project,dataset,
    render(){cursor=0;const tree=loaded.exports[component]({projectDir:'/project',modelId:'model',threshold:.5,disabled:false,onCreated(){},onOpenProposal:async()=>{},onOpenEntry:async()=>{},onRunningChange(){}});effects.splice(0).forEach(fn=>fn());return tree;},
    flatten(tree){return!tree||typeof tree!=='object'?[]:[tree,...(tree.props?.children||[]).flat(Infinity).flatMap(child=>this.flatten(child))];},
  };
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('provider setup late response cannot leak the previous source checkpoint into controls',async()=>{
  const requests=[];
  const ui=mountScopedComponent('CandidateProviderControls',{
    setup:()=>new Promise(resolve=>requests.push(resolve)),featureModels:async()=>({models:[]}),batches:async()=>({batches:[]}),featureJobs:async()=>({jobs:[]}),
  });
  ui.render();
  ui.project.project={...ui.project.project,source_dataset_dir:'/source-b'};
  ui.render();
  assert.equal(requests.length,2,'same project with a new source must reload provider setup');
  const setup=checkpoint=>({...ready,configuration:{feature_checkpoint:checkpoint}});
  requests[0](setup('/old-source-checkpoint'));await settle();
  let checkpoint=ui.flatten(ui.render()).find(node=>node.props?.['aria-label']==='DINOv3 checkpoint 경로');
  assert.equal(checkpoint.props.value,'','late old setup must remain invisible');
  requests[1](setup('/current-source-checkpoint'));await settle();
  checkpoint=ui.flatten(ui.render()).find(node=>node.props?.['aria-label']==='DINOv3 checkpoint 경로');
  assert.equal(checkpoint.props.value,'/current-source-checkpoint');
});

test('bulk history late response cannot populate the next labelset after a same-project switch',async()=>{
  const requests=[];
  const ui=mountScopedComponent('BulkLabelAssist',{listBatches:()=>new Promise(resolve=>requests.push(resolve))});
  ui.render();
  ui.project.project={...ui.project.project,active_labelset_id:'second'};
  ui.render();
  assert.equal(requests.length,2,'same project with a new labelset must reload batch history');
  const batch=id=>({id,status:'completed',entries:[],processed:1,total:1,generated:1,failed:0,zero_candidates:0});
  requests[0]({batches:[batch('old-labelset-batch')]});await settle();
  assert.equal(ui.flatten(ui.render()).some(node=>node.type==='option'&&node.props.value==='old-labelset-batch'),false);
  requests[1]({batches:[batch('current-labelset-batch')]});await settle();
  assert.equal(ui.flatten(ui.render()).some(node=>node.type==='option'&&node.props.value==='current-labelset-batch'),true);
});
