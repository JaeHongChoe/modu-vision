// Real panel renders and owned assistContext registrations; only React scheduling, stores and HTTP are controlled.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const compile=(file,mocks={})=>{const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const original=m.require.bind(m);m.require=name=>Object.hasOwn(mocks,name)?mocks[name]:original(name);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,file);return m.exports;};
const store=state=>Object.assign(selector=>selector?selector(state):state,{getState:()=>state});
const nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
const text=node=>node==null||typeof node==='boolean'?'':typeof node==='string'||typeof node==='number'?String(node):Array.isArray(node)?node.map(text).join(''):text(node.props?.children);
const button=(tree,name)=>nodes(tree).find(node=>node.type==='button'&&(node.props['aria-label']===name||text(node).includes(name)));
function runtime(){
 const slots=[],effects=[];let cursor=0,props={};
 const react={createElement:(type,props,...children)=>({type,props:{...props,children}}),
  useState(initial){const i=cursor++;if(!(i in slots))slots[i]={value:typeof initial==='function'?initial():initial};const slot=slots[i];return[slot.value,value=>{slot.value=typeof value==='function'?value(slot.value):value;}];},
  useRef(initial){const i=cursor++;return slots[i]??(slots[i]={current:initial});},useMemo(fn){cursor++;return fn();},
  useEffect(fn,deps){const i=cursor++,old=slots[i];if(!old||deps.length!==old.deps.length||deps.some((value,index)=>!Object.is(value,old.deps[index]))){const next={deps};slots[i]=next;effects.push({old,next,fn});}},
 };react.default=react;
 const unmount=()=>{slots.forEach(slot=>slot?.cleanup?.());slots.length=0;effects.length=0;};
 const render=component=>{cursor=0;const tree=component(props);const pending=effects.splice(0);pending.forEach(item=>item.old?.cleanup?.());pending.forEach(item=>{item.next.cleanup=item.fn();});return tree;};
 return{react,render,unmount,setProps:next=>{props=next;},async settle(component){let tree;for(let i=0;i<8;i++){tree=render(component);await new Promise(setImmediate);}return tree;}};
}
const batch=error=>({id:'batch-1',status:'failed',processed:1,total:1,generated:0,zero_candidates:0,failed:1,error});
function fixture(kind,context=compile(path.resolve(__dirname,'../../stores/assistContext.ts'))){
 const hooks=runtime(),project={projectDir:'/project-A',project:{id:'project-A',task:'detection',active_labelset_id:'default',source_dataset_dir:'/source-A'}};
 const dataset={folderPath:'/source-A',hasSelectedFolder:true,images:[],annotationsChanged:async()=>{}};
 const annotation={currentImage:{file_path:'/source-A/a.png',file_name:'a.png'},categories:[{id:1,name:'scratch',color:'#f00'}],isDirty:false,annotationLoadStatus:'ready',reviewerName:'Reviewer',setReviewerName(){}};
 const review={sidePanel:null,showSidePanel(){},clearAssistRequest(){}};
 const setup={labelset_id:'default',labelset_version:'v1',configuration:{},providers:{grounding_dino:{ready:true,dependency_available:true},foundation:{ready:true,dependency_available:true}}};
 const provider={setup:async()=>setup,batches:async()=>({batches:[]}),startBatch:async()=>batch('일괄 초안 실패. 다시 만드세요.')};
 const api={dataset:{getImages:async()=>({items:[{file_path:'/source-A/a.png'}],total:1})},labelSuggestions:{models:async()=>({models:[{job_id:'model-1',task:'detection'}]}),list:async()=>({suggestions:[]})}};
 const workflow={generateModel:async()=>{throw Error('모델 초안 실패. 다시 만드세요.');}},quick={};
 const mocks={react:hooks.react,'lucide-react':new Proxy({},{get:(_,key)=>String(key)}),'../../services/api':{api},'../../services/hostAdapter':{host:{can:()=>false}},
  '../../services/foundationLabelingApi':{foundationLabelingApi:provider,labelingJobActive:status=>['queued','running','cancelling'].includes(status)},
  '../../stores/useProjectStore':{useProjectStore:store(project)},'../../stores/useDatasetStore':{useDatasetStore:store(dataset)},'../../stores/useAnnotationStore':{useAnnotationStore:store(annotation)},
  '../../stores/useDraftReviewStore':{useDraftReviewStore:store(review),draftReviewScope:()=> 'scope'},'../../stores/useModelAssistRunStore':{useModelAssistRunStore:store({begin(){},end(){}})},
  '../../stores/workflowEvidence':{bumpWorkflowEvidence(){}},'../../stores/assistContext':context,'../../services/datasetWorkflow':{datasetWorkflow:workflow,workflowError:error=>error.message},
  './foundationRequest':compile(path.join(__dirname,'foundationRequest.ts')),'./foundationQuickPointController':{startFoundationQuickPointController:callbacks=>{quick.callbacks=callbacks;return()=>{};}},'./popoverRoom':{usePopoverRoom:()=>500},
 };
 mocks['../common/useScopedError']=compile(path.resolve(__dirname,'../common/useScopedError.ts'),{react:hooks.react});
 mocks['./draftReview']=compile(path.join(__dirname,'draftReview.ts'),{'../../services/datasetWorkflow':mocks['../../services/datasetWorkflow']});
 for(const name of ['LabelAssistDeviceSizes','BulkLabelAssist','KoreanConditionLabeler','CandidateProviderControls','SuggestionPreview'])mocks['./'+name]={[name]:name};mocks['../common/Explain']={Explain:'Explain'};
 const component=compile(path.join(__dirname,kind+'.tsx'),mocks)[kind];
 const props={task:'detection',disabled:false,pendingImages:new Set(),onRunningChange(){}};hooks.setProps(props);
 return{...hooks,component,context,project,dataset,annotation,review,provider,api,workflow,quick,props,paint:()=>hooks.settle(component),once:()=>hooks.render(component)};
}
async function phraseFailure(f){let tree=await f.paint();nodes(tree).find(node=>node.props?.['aria-label']==='찾을 대상 문구').props.onChange({target:{value:'scratch'}});tree=await f.paint();button(tree,'장에 초안 만들기').props.onClick();return f.paint();}
async function modelOpen(f){let tree=await f.paint();button(tree,'모델 보조 라벨링 패널').props.onClick();return f.paint();}
async function modelFailure(f){const tree=await modelOpen(f);button(tree,'제안 생성').props.onClick();return f.paint();}

test('phrase batch failures reach the common guidance and unmount only retires its own registration',async t=>{
 const f=fixture('PhraseDraftCreate'),other=fixture('PhraseDraftCreate',f.context);t.after(()=>{f.unmount();other.unmount();});
 const tree=await phraseFailure(f);assert.match(text(tree),/일괄 초안 실패/);
 assert.deepEqual(f.context.assistPanelErrors(),[{source:'문구 초안',text:'일괄 초안 실패. 다시 만드세요.'}]);
 await phraseFailure(other);f.unmount();assert.equal(f.context.assistPanelErrors().length,1,'a sibling with the same source retains its error');
 other.unmount();assert.deepEqual(f.context.assistPanelErrors(),[]);
});

for(const dimension of ['project','source','labelset','task','folder'])test(`phrase ${dimension} changes retire current and late batch errors through A B A`,async t=>{
 const f=fixture('PhraseDraftCreate');t.after(f.unmount);await phraseFailure(f);
 const events=[],stop=f.context.subscribeAssistContext(()=>events.push(f.context.assistPanelErrors()));t.after(stop);
 const original=structuredClone(f.project),folder=f.dataset.folderPath;f.provider.startBatch=()=>new Promise((_,reject)=>{f.reject=reject;});
 let tree=await f.paint();button(tree,'장에 초안 만들기').props.onClick();await f.paint();
 if(dimension==='project')f.project.projectDir='/project-B';
 if(dimension==='source')f.project.project={...f.project.project,source_dataset_dir:'/source-B'};
 if(dimension==='labelset')f.project.project={...f.project.project,active_labelset_id:'other'};
 if(dimension==='task')f.project.project={...f.project.project,task:'segmentation'};
 if(dimension==='folder')f.dataset.folderPath='/source-B';
 f.once();assert.ok(events.every(errors=>!errors.some(error=>error.text.includes('일괄 초안 실패'))),'no transition publication can carry the previous batch error');
 await f.paint();Object.assign(f.project,original);f.dataset.folderPath=folder;await f.paint();f.reject(Error('늦은 이전 초안 오류'));tree=await f.paint();
 assert.doesNotMatch(text(tree),/늦은 이전 초안 오류/);assert.deepEqual(f.context.assistPanelErrors(),[]);
});

test('phrase setup errors are shared while visible and removed on unmount',async t=>{
 const f=fixture('PhraseDraftCreate');t.after(f.unmount);f.provider.setup=async()=>{throw Error('모델 설정 오류. 경로를 확인하세요.');};
 const tree=await f.paint();assert.ok(nodes(tree).some(node=>node.props?.role==='alert'&&text(node).includes('모델 설정 오류')));
 assert.deepEqual(f.context.assistPanelErrors(),[{source:'문구 초안',text:'모델 설정 오류. 경로를 확인하세요.'}]);
 f.unmount();assert.deepEqual(f.context.assistPanelErrors(),[]);
});

test('model assist errors are shared only while its popover is open',async t=>{
 const f=fixture('ModelAssistPanel');t.after(f.unmount);let tree=await modelFailure(f);
 assert.ok(nodes(tree).some(node=>node.props?.role==='alert'&&text(node).includes('모델 초안 실패')));
 assert.deepEqual(f.context.assistPanelErrors(),[{source:'모델 보조',text:'모델 초안 실패. 다시 만드세요.'}]);
 button(tree,'닫기').props.onClick();await f.paint();assert.deepEqual(f.context.assistPanelErrors(),[]);
 await modelOpen(f);assert.deepEqual(f.context.assistPanelErrors(),[],'reopening cannot revive a cleared error');
});

for(const dimension of ['project','source','labelset','task','image'])test(`model assist ${dimension} changes never publish a previous error in the next scope`,async t=>{
 const f=fixture('ModelAssistPanel');t.after(f.unmount);await modelFailure(f);
 const events=[],stop=f.context.subscribeAssistContext(()=>events.push(f.context.assistPanelErrors()));t.after(stop);
 if(dimension==='project')f.project.projectDir='/project-B';
 if(dimension==='source')f.project.project={...f.project.project,source_dataset_dir:'/source-B'};
 if(dimension==='labelset')f.project.project={...f.project.project,active_labelset_id:'other'};
 if(dimension==='task')f.project.project={...f.project.project,task:'segmentation'};
 if(dimension==='image')f.annotation.currentImage={file_path:'/source-A/b.png',file_name:'b.png'};
 f.once();assert.ok(events.every(errors=>!errors.some(error=>error.text.includes('모델 초안 실패'))));
 const tree=await f.paint();assert.doesNotMatch(text(tree),/모델 초안 실패/);assert.deepEqual(f.context.assistPanelErrors(),[]);
});

test('a collapsed model request cannot publish its late error after reopening',async t=>{
 const f=fixture('ModelAssistPanel');t.after(f.unmount);let reject;f.workflow.generateModel=()=>new Promise((_,no)=>{reject=no;});
 let tree=await modelOpen(f);button(tree,'제안 생성').props.onClick();await f.paint();button(tree,'닫기').props.onClick();await f.paint();
 tree=await modelOpen(f);reject(Error('늦은 모델 오류'));tree=await f.paint();assert.doesNotMatch(text(tree),/늦은 모델 오류/);assert.deepEqual(f.context.assistPanelErrors(),[]);
});

test('automatic opening cannot carry a quick-point error to another image or replay it after returning',async t=>{
 const f=fixture('ModelAssistPanel');t.after(f.unmount);await f.paint();
 // The controller may correctly finish for A just before a queued image change renders B.
 f.quick.callbacks.onError(Error('이전 이미지의 빠른 점 오류'));
 f.annotation.currentImage={file_path:'/source-A/b.png',file_name:'b.png'};
 let tree=await f.paint();assert.doesNotMatch(text(tree),/이전 이미지의 빠른 점 오류/);assert.deepEqual(f.context.assistPanelErrors(),[]);
 f.annotation.currentImage={file_path:'/source-A/a.png',file_name:'a.png'};tree=await f.paint();
 assert.doesNotMatch(text(tree),/이전 이미지의 빠른 점 오류/);assert.deepEqual(f.context.assistPanelErrors(),[]);
});
