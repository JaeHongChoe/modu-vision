// These tests catch unsolicited inference, duplicate/late work, lost context, and a controller accidentally mounted
// inside the closed popover. Only HTTP/host stores and React scheduling are controlled; the prompt store, request
// builder, controller and ModelAssistPanel below are the production implementations.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const {create}=require('zustand');
const compile=(file,mocks={})=>{const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const original=m.require.bind(m);m.require=n=>Object.hasOwn(mocks,n)?mocks[n]:original(n);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,file);return m.exports;};
const promptFile=path.resolve(__dirname,'../../stores/useFoundationPromptStore.ts');
const options={label:'scratch',prompt:'',device:'cpu',threshold:.5,text_threshold:.25,max_candidates:20,output_geometry:'mask',positive_examples:[],negative_examples:[],points:[],boxes:[],exclude_regions:[[1,2,3,4]],min_area:0,min_width:0,min_height:0,class_ids:{scratch:2}};
const setup=()=>({labelset_id:'default',labelset_version:'fresh-v2',configuration:{},providers:{foundation:{ready:true,weights_check:'ok'},grounding_dino:{ready:false}}});
const proposal=()=>({id:'made',image_id:'a',image_path:'/source/a.png',created_at:'2026-10-24T00:00:00Z',backend:'foundation',status:'pending',labelset_id:'default',candidates:[{id:'candidate',confidence:.9,annotation:{id:'shape',type:'bbox',label:'scratch',category_id:2,bbox:[1,2,5,6]}}],accepted_candidate_ids:[]});
const settle=async()=>{for(let i=0;i<4;i++)await new Promise(setImmediate);};
function fixture(){
 const prompt=compile(promptFile).useFoundationPromptStore;
 const project=create(()=>({projectDir:'/project',isProjectBusy:false,project:{id:'p',task:'segmentation',active_labelset_id:'default',source_dataset_dir:'/source'}}));
 const annotation=create(()=>({currentImage:{file_path:'/source/a.png',file_name:'a.png'},annotationLoadStatus:'ready',isDirty:false,activeCategory:{id:2,name:'scratch'},categories:[{id:2,name:'scratch',color:'#f00'}],reviewerName:'Reviewer',setReviewerName(){}}));
 const run=create(set=>({activeOperations:0,begin:()=>set(s=>({activeOperations:s.activeOperations+1})),end:()=>set(s=>({activeOperations:s.activeOperations-1}))}));
 const calls=[],shown=[],errors=[],running=[];let catalog=setup(),blocked=false,epoch=1;
 const provider={setup:async()=>catalog,generate:async body=>{calls.push(body);return proposal();}};
 const requests=compile(path.join(__dirname,'foundationRequest.ts'));
 const mocks={'../../stores/useFoundationPromptStore':{useFoundationPromptStore:prompt},'../../stores/useProjectStore':{useProjectStore:project},'../../stores/useAnnotationStore':{useAnnotationStore:annotation},'../../stores/useModelAssistRunStore':{useModelAssistRunStore:run},'../../services/foundationLabelingApi':{foundationLabelingApi:provider},'../../services/api':{getApiPersistenceIdentity:()=> 'local',getProjectContextGeneration:()=>epoch},'./foundationRequest':requests};
 const scope=requests.labelingScope(project.getState());
 prompt.getState().bindProject(scope);prompt.getState().bind('/source/a.png');
 const attach=()=>compile(path.join(__dirname,'foundationQuickPointController.ts'),mocks).startFoundationQuickPointController({isBlocked:()=>blocked,onCreated:x=>shown.push(x),onError:x=>errors.push(x),onRunning:x=>running.push(x)});
 const configure=(value=options)=>prompt.getState().configureQuickPoint(scope,annotation.getState().currentImage.file_path,value,true,JSON.stringify([annotation.getState().activeCategory,annotation.getState().categories]));
 const enable=()=>{configure();prompt.getState().setQuickPointEnabled(true);};
 return {prompt,project,annotation,run,provider,calls,shown,errors,running,mocks,requests,scope,attach,configure,enable,setCatalog:x=>{catalog=x;},block:x=>{blocked=x;},changeEpoch:()=>epoch++};
}
test('quick point mode starts off; adding points and enabling it never replays old points',async()=>{
 const prompt=compile(promptFile).useFoundationPromptStore;assert.equal(prompt.getState().quickPointEnabled,false);
 const f=fixture(),stop=f.attach();f.configure();f.prompt.getState().addPoint({x:7,y:11,label:0});await settle();assert.equal(f.calls.length,0);
 f.prompt.getState().setQuickPointEnabled(true);await settle();assert.equal(f.calls.length,0);stop();
});
test('a new point makes one pending draft with original coordinates and the freshly read dataset version',async()=>{
 const f=fixture(),stop=f.attach();f.enable();f.prompt.getState().addPoint({x:7,y:11,label:0});await settle();
 assert.equal(f.calls.length,1);assert.deepEqual(f.calls[0],{...options,backend:'foundation',image_path:'/source/a.png',points:[{x:7,y:11,label:0}],labelset_id:'default',labelset_version:'fresh-v2'});
 assert.deepEqual(f.shown,[proposal()]);assert.deepEqual(f.running,[true,false]);assert.equal(f.run.getState().activeOperations,0);assert.equal(f.annotation.getState().isDirty,false);stop();
});
test('busy, dirty, unread annotations and missing/damaged models cannot generate or retry when unblocked',async()=>{
 for(const change of [f=>f.block(true),f=>f.annotation.setState({isDirty:true}),f=>f.annotation.setState({annotationLoadStatus:'loading'}),f=>f.project.setState({isProjectBusy:true}),f=>f.run.getState().begin(),f=>f.setCatalog({...setup(),providers:{...setup().providers,foundation:{ready:false}}}),f=>f.setCatalog({...setup(),providers:{...setup().providers,foundation:{ready:true,weights_check:'truncated'}}})]){
  const f=fixture(),stop=f.attach();f.enable();change(f);f.prompt.getState().addPoint({x:5,y:6,label:1});await settle();assert.equal(f.calls.length,0);
  f.block(false);f.annotation.setState({isDirty:false,annotationLoadStatus:'ready'});f.project.setState({isProjectBusy:false});f.setCatalog(setup());await settle();assert.equal(f.calls.length,0,'unblocking is not another point');stop();
 }
});
test('points during an existing operation never cancel it, overlap, or become a delayed automatic request',async()=>{
 const f=fixture(),stop=f.attach();let finish;f.provider.generate=body=>{f.calls.push(body);return new Promise(resolve=>{finish=resolve;});};
 f.enable();f.prompt.getState().addPoint({x:1,y:2,label:1});await settle();assert.equal(f.calls.length,1);
 f.prompt.getState().addPoint({x:3,y:4,label:0});await settle();assert.equal(f.calls.length,1);assert.equal(f.run.getState().activeOperations,1);
 finish(proposal());await settle();assert.equal(f.calls.length,1);assert.equal(f.shown.length,0,'the result is stale after the next point');assert.equal(f.run.getState().activeOperations,0);stop();
});
test('image, options, dirty round-trip, scope, transport and opt-out changes suppress late results',async()=>{
 for(const change of [f=>f.annotation.setState({currentImage:{file_path:'/source/b.png'}}),f=>f.configure({...options,threshold:.8}),f=>{f.annotation.setState({isDirty:true});f.annotation.setState({isDirty:false});},f=>f.project.setState({project:{...f.project.getState().project,active_labelset_id:'other'}}),f=>f.changeEpoch(),f=>f.prompt.getState().setQuickPointEnabled(false)]){
  const f=fixture(),stop=f.attach();let finish;f.provider.generate=body=>{f.calls.push(body);return new Promise(resolve=>{finish=resolve;});};f.enable();f.prompt.getState().addPoint({x:1,y:2,label:1});await settle();
  assert.equal(f.calls.length,1);change(f);finish(proposal());await settle();assert.equal(f.shown.length,0);assert.equal(f.run.getState().activeOperations,0);stop();
 }
});
test('a changed image while readiness loads never submits the original image request',async()=>{
 const f=fixture(),stop=f.attach();let ready;f.provider.setup=()=>new Promise(resolve=>{ready=resolve;});f.enable();f.prompt.getState().addPoint({x:1,y:2,label:1});
 f.annotation.setState({currentImage:{file_path:'/source/b.png'}});ready(setup());await settle();assert.equal(f.calls.length,0);assert.equal(f.run.getState().activeOperations,0);stop();
});
test('a setup response for another label set cannot authorize a point request',async()=>{
 const f=fixture(),stop=f.attach();f.enable();f.setCatalog({...setup(),labelset_id:'other'});f.prompt.getState().addPoint({x:1,y:2,label:1});await settle();assert.equal(f.calls.length,0);assert.equal(f.run.getState().activeOperations,0);stop();
});
test('clearing or adding a box invalidates a pending point result without triggering another request',async()=>{
 for(const change of [f=>f.prompt.getState().clear(),f=>f.prompt.getState().addBox([1,2,3,4])]){
  const f=fixture(),stop=f.attach();let finish;f.provider.generate=body=>{f.calls.push(body);return new Promise(resolve=>{finish=resolve;});};f.enable();f.prompt.getState().addPoint({x:1,y:2,label:1});await settle();change(f);finish(proposal());await settle();assert.equal(f.calls.length,1);assert.equal(f.shown.length,0);stop();
 }
});
test('leaving the visit does not cancel an in-flight request or deliver it into a later visit',async()=>{
 const f=fixture(),stop=f.attach();let finish;f.provider.generate=body=>{f.calls.push(body);return new Promise(resolve=>{finish=resolve;});};f.enable();f.prompt.getState().addPoint({x:1,y:2,label:1});await settle();stop();
 assert.equal(f.run.getState().activeOperations,1,'the original request still owns the operation');assert.equal(f.prompt.getState().quickPointEnabled,false);
 const stopNext=f.attach();f.enable();finish(proposal());await settle();assert.equal(f.shown.length,0);assert.equal(f.run.getState().activeOperations,0);stopNext();
});
test('same-project image changes keep opt-in but cannot reuse another image exclusion; project and visit changes turn it off',async()=>{
 const f=fixture(),stop=f.attach();f.enable();f.annotation.setState({currentImage:{file_path:'/source/b.png'}});f.prompt.getState().addPoint({x:8,y:9,label:1});await settle();
 assert.equal(f.calls[0].image_path,'/source/b.png');assert.deepEqual(f.calls[0].exclude_regions,[]);assert.deepEqual(f.calls[0].points,[{x:8,y:9,label:1}]);
 f.project.setState({project:{...f.project.getState().project,active_labelset_id:'other'}});assert.equal(f.prompt.getState().quickPointEnabled,false);
 stop();assert.equal(f.prompt.getState().quickPointEnabled,false);
});
test('changing the active class or class palette while the popover is absent disarms point inference before another point',async()=>{
 for(const change of [f=>f.annotation.setState({activeCategory:{id:3,name:'dent'}}),f=>f.annotation.setState({categories:[{id:3,name:'dent',color:'#f00'}]})]){
  const f=fixture(),stop=f.attach();f.enable();change(f);assert.equal(f.prompt.getState().quickPointEnabled,false);f.prompt.getState().addPoint({x:1,y:2,label:1});await settle();assert.equal(f.calls.length,0);assert.equal(f.errors.length,1,'the user is told to check the new class and opt in again');stop();
 }
});
test('errors release only the owned operation, show an error and never start retries',async()=>{
 const f=fixture(),stop=f.attach();f.provider.generate=async body=>{f.calls.push(body);throw Error('controlled inference failure');};f.enable();f.prompt.getState().addPoint({x:1,y:2,label:1});await settle();assert.equal(f.errors.length,1);assert.equal(f.calls.length,1);assert.equal(f.run.getState().activeOperations,0);stop();
});

// Render the real panel AND controls with independent hook lifetimes. Closing removes/unmounts the controls;
// opening creates fresh hooks. This exercises the production lifecycle without starting a browser or another app.
test('closed and open panels keep custom point settings, show explicit previews, and disarm verification closed in flight',async()=>{
 const f=fixture(),instances=new Map(),effects=[];let current;
 const slot=()=>{const index=current.cursor++;return[current.slots,index];};
 const react={createElement:(type,props,...children)=>({type,props:{...props,children}}),
  useState(initial){const [slots,i]=slot();if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v;}];},
  useRef(initial){const [slots,i]=slot();return slots[i]||={current:initial};},useId(){const [,i]=slot();return ':quick-'+i;},useMemo(fn){slot();return fn();},
  useEffect(fn,deps){const [slots,i]=slot(),old=slots[i];if(!old||deps.some((d,n)=>d!==old.deps[n])){old?.cleanup?.();const item={deps};slots[i]=item;effects.push(()=>{item.cleanup=fn();});}},
 };react.default=react;
 const hook=store=>Object.assign(selector=>selector?selector(store.getState()):store.getState(),{getState:store.getState,subscribe:store.subscribe});
 let adoption=0,moves=0,saved=proposal();f.annotation.setState({annotations:[],selectedAnnotationId:null,setActiveTool(){},setActiveImage(){moves++;}});
 Object.assign(f.provider,{featureModels:async()=>({models:[]}),batches:async()=>({batches:[]}),featureJobs:async()=>({jobs:[]})});
 const stubs={...f.mocks,react,'lucide-react':new Proxy({},{get:(_,key)=>String(key)}),
  '../../stores/useFoundationPromptStore':{useFoundationPromptStore:hook(f.prompt)},'../../stores/useAnnotationStore':{useAnnotationStore:hook(f.annotation)},'../../stores/useProjectStore':{useProjectStore:hook(f.project)},'../../stores/useModelAssistRunStore':{useModelAssistRunStore:hook(f.run)},
  '../../stores/useDatasetStore':{useDatasetStore:hook(create(()=>({images:[],folderPath:'/source',hasSelectedFolder:true})))},
  '../../stores/useDraftReviewStore':{useDraftReviewStore:hook(create(()=>({sidePanel:null,assistRequest:0,showSidePanel(){},clearAssistRequest(){},requestOpen(){adoption++;}}))),draftReviewScope:()=> 'scope'},
  '../../services/api':{api:{labelSuggestions:{models:async()=>({models:[]}),list:async()=>({suggestions:[saved]})}}},'../../services/hostAdapter':{host:{can:()=>false}},
  '../../services/datasetWorkflow':{datasetWorkflow:{},workflowError:e=>String(e)},'../../services/foundationLabelingApi':{foundationLabelingApi:f.provider,labelingJobActive:()=>false},
  './popoverRoom':{usePopoverRoom:()=>500},'./draftReview':{draftError:e=>String(e),CHANGED_MODEL_MARK:'changed'},
 };
 for(const name of ['BulkLabelAssist','KoreanConditionLabeler','SuggestionPreview','LabelAssistDeviceSizes'])stubs['./'+name]={[name]:name};stubs['../common/Explain']={Explain:'Explain'};
 stubs['./CandidateProviderControls']=compile(path.join(__dirname,'CandidateProviderControls.tsx'),stubs);
 stubs['./foundationQuickPointController']=compile(path.join(__dirname,'foundationQuickPointController.ts'),f.mocks);
 const {ModelAssistPanel}=compile(path.join(__dirname,'ModelAssistPanel.tsx'),stubs);
 const flat=x=>!x||typeof x!=='object'?[]:[x,...(x.props?.children||[]).flat(Infinity).flatMap(flat)];
 const cleanup=entry=>entry.slots.forEach(item=>item?.cleanup?.());
 function resolve(node,key){
  if(Array.isArray(node))return node.map((child,i)=>resolve(child,key+'/'+i));if(!node||typeof node!=='object')return node;
  if(typeof node.type==='function')return component(node.type,node.props,key+'/'+node.type.name);
  return {...node,props:{...node.props,children:resolve(node.props?.children||[],key+'/children')}};
 }
 function component(fn,props,key){const entry=instances.get(key)||{slots:[]};entry.seen=true;entry.cursor=0;instances.set(key,entry);const previous=current;current=entry;const node=fn(props);current=previous;return resolve(node,key);}
 let tree;
 function render(){for(const entry of instances.values())entry.seen=false;tree=component(ModelAssistPanel,{},'root');for(const [key,entry] of instances)if(!entry.seen){cleanup(entry);instances.delete(key);}effects.splice(0).forEach(fn=>fn());return tree;}
 const paint=async()=>{for(let i=0;i<4;i++){render();await settle();}return tree;};
 const label=name=>flat(tree).find(x=>x.props?.['aria-label']===name);
 const button=name=>flat(tree).find(x=>x.type==='button'&&(x.props['aria-label']===name||x.props.children?.flat(Infinity).includes(name)));
 const previousStorage=Object.getOwnPropertyDescriptor(global,'localStorage');Object.defineProperty(global,'localStorage',{configurable:true,writable:true,value:{getItem:()=>null,setItem(){}}});
 try{
  await paint();assert.equal(flat(tree).filter(x=>x.type==='aside').length,0);
  button('모델 보조 라벨링 패널').props.onClick();await paint();
  label('후보 점수').props.onChange({target:{value:'0.8'}});label('라벨링 실행 장치').props.onChange({target:{value:'auto'}});label('SAM2 출력 형태').props.onChange({target:{value:'polygon'}});await paint();
  label('점 입력 후 후보 만들기').props.onChange({target:{checked:true}});await paint();button('닫기').props.onClick();await paint();
  assert.equal(flat(tree).filter(x=>x.type==='aside').length,0);assert.ok(![...instances.keys()].some(key=>key.includes('CandidateProviderControls')),'the production controls were unmounted');
  f.prompt.getState().addPoint({x:5,y:6,label:1});await paint();
  assert.equal(f.calls.length,1);assert.equal(f.calls[0].threshold,.8);assert.equal(f.calls[0].device,'auto');assert.equal(f.calls[0].output_geometry,'polygon');
  assert.equal(flat(tree).find(x=>x.type==='SuggestionPreview').props.proposal.id,'made');assert.equal(label('후보 점수').props.value,.8,'the automatically reopened controls restore custom settings');
  const successNotice=()=>JSON.stringify(tree).includes('후보 1개를 만들었습니다. 검토 후 선택하세요.');
  assert.equal(successNotice(),true,'automatic opening and its settled proposal-list refresh preserve the creation notice');
  // A point while the panel is OPEN must survive its own parent busy/disabled rerender.
  let finish;f.provider.generate=body=>{f.calls.push(body);return new Promise(resolve=>{finish=resolve;});};const revision=f.prompt.getState().quickOptionsRevision;
  f.prompt.getState().addPoint({x:9,y:10,label:0});await paint();assert.equal(f.calls.length,2);assert.equal(f.prompt.getState().quickOptionsRevision,revision);assert.equal(f.prompt.getState().quickOptions.ready,true);
  saved={...proposal(),id:'opened'};finish(saved);await paint();assert.equal(flat(tree).find(x=>x.type==='SuggestionPreview').props.proposal.id,'opened');
  assert.equal(successNotice(),true,'an already open panel also shows the new creation notice');
  assert.equal(adoption,0);assert.equal(moves,0);assert.equal(f.annotation.getState().currentImage.file_path,'/source/a.png');
  f.annotation.setState({currentImage:{file_path:'/source/b.png',file_name:'b.png'}});await paint();assert.equal(successNotice(),false,'another image cannot inherit the creation notice');
  f.annotation.setState({currentImage:{file_path:'/source/a.png',file_name:'a.png'}});await paint();assert.equal(successNotice(),false,'returning to the image does not replay the old notice');
  // Closing controls during verification cannot strand enabled=true with a remembered transient ready=false.
  const originalSetup=f.provider.setup;let verified;f.provider.setup=()=>new Promise(resolve=>{verified=resolve;});button('파일 확인').props.onClick();await paint();assert.equal(f.prompt.getState().quickPointEnabled,false);
  button('닫기').props.onClick();await paint();verified(setup());f.provider.setup=originalSetup;await paint();f.prompt.getState().addPoint({x:11,y:12,label:1});await paint();assert.equal(f.calls.length,2);
  // An error must survive the list-loading effect when a failed closed-panel request opens the panel.
  button('모델 보조 라벨링 패널').props.onClick();await paint();label('점 입력 후 후보 만들기').props.onChange({target:{checked:true}});await paint();button('닫기').props.onClick();await paint();
  f.provider.generate=async()=>{throw Error('controlled closed-panel failure');};f.prompt.getState().addPoint({x:13,y:14,label:1});await paint();
  assert.ok(flat(tree).some(x=>x.props?.role==='alert'&&JSON.stringify(x).includes('controlled closed-panel failure')));
  const originalProject=f.project.getState();
  for(const change of [()=>f.project.setState({projectDir:'/another-project',project:{...originalProject.project,id:'another'}}),()=>f.project.setState({project:{...originalProject.project,active_labelset_id:'another'}})]){
   f.provider.generate=async body=>{f.calls.push(body);saved=proposal();return saved;};label('점 입력 후 후보 만들기').props.onChange({target:{checked:true}});await paint();
   button('닫기').props.onClick();await paint();f.prompt.getState().addPoint({x:15,y:16,label:1});await paint();assert.equal(successNotice(),true);
   change();await paint();assert.equal(successNotice(),false,'another project or label set cannot inherit the creation notice');
   f.project.setState(originalProject);await paint();assert.equal(successNotice(),false,'restoring the original scope does not replay a consumed notice');
  }
 }finally{for(const entry of instances.values())cleanup(entry);if(previousStorage)Object.defineProperty(global,'localStorage',previousStorage);else delete global.localStorage;}
 assert.equal(f.prompt.getState().quickPointEnabled,false);assert.equal(f.run.getState().activeOperations,0);
});
