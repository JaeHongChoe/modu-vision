const test=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const Module=require('node:module');const ts=require('typescript');
function load(file){const filename=path.join(__dirname,file),m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(__dirname);m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);return m.exports;}
test('DINO scopes map to native controls while old default calls keep the same recipe',()=>{
 const options=load('modelTrainingOptions.ts').trainingModelOverrides;
 assert.deepEqual(options('classification','dinov3_vits16'),{backbone:'dinov3_vits16'});
 assert.deepEqual(options('classification','dinov3_vits16','',{},'image',{train_mode:'partial',partial_blocks:3}),{backbone:'dinov3_vits16',train_mode:'partial',partial_blocks:3});
 assert.equal(options('segmentation','dinov3_vitb16','',{},'image',{train_mode:'full'}).train_mode,'full');
 assert.throws(()=>options('classification','resnet18','',{},'image',{train_mode:'full'}),/DINO/);
 assert.throws(()=>options('classification','dinov3_vits16','',{},'image',{train_mode:'partial',partial_blocks:0}),/블록/);
});

test('AutoDL sends the selected remote profile and seed through its enabled action',async()=>{
 const slots=[],effects=[],sent=[];let cursor=0;
 const react={useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return [slots[i],value=>slots[i]=typeof value==='function'?value(slots[i]):value];},useRef(initial){const i=cursor++;return slots[i]??(slots[i]={current:initial});},useEffect(fn,deps){const i=cursor++,prior=slots[i];if(!prior||deps.some((value,index)=>value!==prior[index])){slots[i]=deps;effects.push(fn);}}};
 const project={projectDir:'/project',project:{source_dataset_dir:'/data',active_labelset_id:'default'}};
 const capability={architectures:['dinov3_vits16'],search_defaults:{architectures:['dinov3_vits16'],learning_rates:[.001],batch_sizes:[1],image_sizes:[64]},prepared_input:false,metric_key:'val_loss'};
 const filename=path.join(__dirname,'AutoDLWorkbench.tsx'),m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(__dirname);const original=m.require.bind(m);
 const stubs={react,'./ProgramWorkbenchControls':{ProgramField:'field',TrainingDeviceSelector:'device',programInput:'',programButton:'',programPrimary:''},'./trainingWorkflow':{validateTrainingBudget:()=>null},'../../stores/useComputeStore':{useComputeStore:()=>({selectedProfileId:'selected-worker',transportRevision:0,profiles:[],isLoading:false})},'../../stores/useProjectStore':{useProjectStore:select=>select(project)},'../../services/api':{getApiPersistenceIdentity:()=>'',request:async()=>({parents:[]})},'./useTaskHandoff':{useTaskHandoff:()=>null},'./scopedTrainingJob':{scopedTrainingJob:()=>null},'../../services/modelTrainingProgram':{activeProgramJob:()=>false,programError:String,modelTrainingProgram:{automated:{capabilities:async()=>({tasks:{classification:capability}}),jobs:async()=>({jobs:[]}),start:async body=>{sent.push(body);return {search_id:'a'.repeat(32),status:'queued',trials:[]};}}}}};
 m.require=name=>name in stubs?stubs[name]:original(name);m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,filename);
 function render(){cursor=0;const tree=m.exports.AutoDLWorkbench({task:'classification'});effects.splice(0).forEach(fn=>fn());return tree;}
 function find(node,predicate){if(Array.isArray(node))return node.flatMap(child=>find(child,predicate));if(!node||typeof node!=='object')return [];return [...(predicate(node)?[node]:[]),...find(node.props?.children,predicate)];}
 const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};render();await flush();const tree=render();const button=find(tree,node=>node.type==='button'&&node.props.children==='측정 학습 시작')[0];assert.equal(button.props.disabled,false);button.props.onClick();await flush();assert.equal(sent.length,1);assert.equal(sent[0].compute_profile_id,'selected-worker');assert.equal(sent[0].seed,0);assert.equal(sent[0].base_config.train_mode,'head_only');
});
