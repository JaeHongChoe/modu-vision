const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function render(props){const name=path.join(__dirname,'FlowEditorWorkspace.tsx'),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);const jsx=(type,props)=>({type,props:props||{}});m.require=ref=>ref==='react/jsx-runtime'?{jsx,jsxs:jsx}:ref==='react'?{}:require(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,name);return m.exports.FlowEditorWorkspace(props);}
function workspaceModule(){const name=path.join(__dirname,'FlowEditorWorkspace.tsx'),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);const jsx=(type,props)=>({type,props:props||{}});m.require=ref=>ref==='react/jsx-runtime'?{jsx,jsxs:jsx}:ref==='react'?{}:require(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,name);return m.exports;}
const walk=n=>!n||typeof n!=='object'?[]:[n,...[n.props?.children].flat(Infinity).flatMap(walk)];
test('four areas expose selected tab and keyboard navigation without replacing panel content',()=>{let next;const nodes=walk(render({area:'edit',onAreaChange:value=>next=value,identity:{revision:3,draft:'미저장 초안',models:[],approval:'미조회'}}));const tabs=nodes.filter(n=>n.props.role==='tab');assert.deepEqual(tabs.map(n=>n.props.children),['편집','테스트','일괄 평가','배포']);assert.equal(tabs[0].props['aria-selected'],true);let focused;tabs[0].props.onKeyDown({key:'ArrowRight',preventDefault(){},currentTarget:{parentElement:{querySelectorAll:()=>tabs.map((_,i)=>({focus:()=>focused=i}))}}});assert.equal(next,'test');assert.equal(focused,1);});
test('identity separates next image from recorded image and never promotes evaluation to approval or deployment',()=>{const text=JSON.stringify(render({area:'test',onAreaChange(){},identity:{revision:4,draft:'편집 초안',models:['job-8'],nextImage:'next.png',inspection:'이전 버전 · recorded.png · hash-original',evaluation:'평가 유효 · version-1',approval:'미조회'}}));for(const expected of ['next.png','recorded.png','hash-original','평가 유효','미조회','대상 적용 응답 확인 필요'])assert.ok(text.includes(expected),expected);});

// Exercise the mounted Studio's actual adoption callbacks. Effects are skipped so
// only the explicitly invoked model verification transport is substituted.
function studioHarness(realStore=null){
 const previousStorage=Object.getOwnPropertyDescriptor(globalThis,'localStorage');Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:()=>null}});
 const values=[],refs=[];let stateIndex=0,refIndex=0,generation=1,resolveVerify,rejectVerify,templateRequests=0,activeVersion='active-v1',activeRequests=0;const changes=[];
 const original={id:'original',name:'Existing saved flow',nodes:[{id:'original',position:{x:0,y:0},data:{node_type:'input',label:'Original'}}],edges:[]};
 const flowState={baseVersionId:null,setBaseVersionId:value=>flowState.baseVersionId=value,pipeline:original,pipelineDirty:false,pipelineIsDraft:false,flowIdentity:{semantic_revision:1},executionChoiceOverride:'local_cpu',replacePipeline:(value,newBasis)=>{changes.push(value);flowState.pipeline=value;if(newBasis)flowState.baseVersionId=newBasis.baseVersionId;},selectNode(){}};
 let unsubscribe=()=>{};if(realStore){realStore.setState({pipeline:original,cleanPipeline:original,baseVersionId:null});Object.assign(flowState,realStore.getState());unsubscribe=realStore.subscribe(state=>Object.assign(flowState,state));}
 const project={language:'ko',task:'anomaly',projectDir:'/project',project:{id:'project',active_labelset_id:'labels'}};
 const store=value=>Object.assign(selector=>selector?selector(value):value,{getState:()=>value});
 const component=()=>null,jsx=(type,props)=>({type,props:props||{}});
 const mocks={react:{useState:initial=>{const i=stateIndex++;if(!(i in values))values[i]=typeof initial==='function'?initial():initial;return [values[i],next=>{values[i]=typeof next==='function'?next(values[i]):next;}];},useRef:initial=>{const i=refIndex++;return refs[i]||(refs[i]={current:initial});},useEffect(){},useLayoutEffect(){}},'react/jsx-runtime':{jsx,jsxs:jsx},'lucide-react':new Proxy({},{get:()=>component}),
 '../../stores/useFlowchartStore':{useFlowchartStore:store(flowState),flowSemanticKey:p=>JSON.stringify(p),isExecutionResultCurrent:()=>false},'../../stores/useDatasetStore':{useDatasetStore:store({folderPath:'/source',datasetKey:'source-key',hasSelectedFolder:true})},'../../stores/useProjectStore':{useProjectStore:store(project)},'../../stores/useEvaluationStore':{useEvaluationStore:store({})},'../../stores/useTrainingStore':{useTrainingStore:store({})},'../../stores/useComputeStore':{useComputeStore:store({profiles:[],isLoaded:true})},
 '../../services/api':{getApiPersistenceIdentity:()=> 'server',getProjectContext:()=>({mode:'team',project_id:'project',actor_id:'actor'}),getProjectContextGeneration:()=>generation,api:{flowchart:{activeVersionId:async()=>{activeRequests++;return {version_id:activeVersion};},verifyModels:()=>new Promise((resolve,reject)=>{resolveVerify=resolve;rejectVerify=reject;}),getFiveModelChainTemplate:()=>{templateRequests+=1;return new Promise(()=>{});}}}},
 './flowchartViewport':{computeFlowchartViewport:()=>({scale:1,contentWidth:800,contentHeight:240}),readableFlowScale:value=>value},'./flowchartStartup':{getFlowchartModelTask:()=>null,getFlowchartModelReferences:p=>p.nodes.filter(n=>n.data.model_job_id).map(n=>({job_id:n.data.model_job_id,task:n.data.task}))},'./flowchartGraph':{nodeClassChoices:()=>[],validateFlowchartGraph:()=>null,locateFlowIssue:()=>null,flowIssuesByTarget:()=>({nodes:new Map(),edges:new Map()})},'./flowHandoff':{flowRecipeLabel:()=> '검사'},'./flowExecution':{},'./FlowRecipeDialog':{FlowRecipeDialog:'RecipeDialog'},'./modelFlowHandoff':{readModelFlowHandoff:()=>null},
 './flowRecipes':{FLOW_RECIPES:[],createFlowRecipe:()=>({id:'preview',name:'Recipe',nodes:[{id:'inspect',position:{x:0,y:0},data:{node_type:'inspection',task:'anomaly',label:'Model'}}],edges:[]})},
 './FlowEditorWorkspace':workspaceModule()};
 const name=path.join(__dirname,'FlowchartStudio.tsx'),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);m.require=ref=>mocks[ref]??new Proxy({},{get:()=>component});m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,name);
 const render=()=>{stateIndex=0;refIndex=0;return m.exports.FlowchartStudio();};
 const text=n=>typeof n==='string'?n:Array.isArray(n)?n.map(text).join(''):text(n?.props?.children||'');
 // The flow finished opening: its model check (and the completed model's binding) ended, so recipes may open.
 const opened=(status={status:'ready'})=>{render();for(const i in values)if(values[i]&&typeof values[i]==='object'&&values[i].status==='checking')values[i]=status;};
 const click=()=>{const tree=render();walk(tree).find(n=>n.type==='button'&&text(n)==='고정 ROI 검사 · 원본 픽셀 좌표').props.onClick();return walk(render()).find(n=>n.type==='RecipeDialog');};
 const open=(status)=>{opened(status);return click();};
 const template=()=>{walk(render()).find(n=>n.type==='button'&&String(n.props.onClick).includes("handleExampleTemplate('chain')")).props.onClick();return templateRequests;};
 return {open,click,template,opened,render,original,flowState,changes,transport:mocks['../../services/api'].api.flowchart,activeRequests:()=>activeRequests,activeVersion:()=>activeVersion,changeActive:value=>activeVersion=value,resolve:async()=>{await Promise.resolve();resolveVerify?.();},reject:async()=>{await Promise.resolve();rejectVerify?.(new Error('verification refused'));},switchGeneration:()=>{generation+=2;},cleanup:()=>{unsubscribe();if(previousStorage)Object.defineProperty(globalThis,'localStorage',previousStorage);else delete globalThis.localStorage;}};
}
const mapped={id:'mapped',name:'Explicit model',nodes:[{id:'inspect',position:{x:0,y:0},data:{node_type:'inspection',label:'Mapped',task:'anomaly',model_job_id:'selected'}}],edges:[]};
test('recipes open again once the opening check ends blocked (no saved flow or model is exactly when one is needed)',()=>{const h=studioHarness();try{assert.ok(h.open({status:'blocked',reason:'saved_flow_unavailable'}));}finally{h.cleanup();}});
test('an example template is not requested while the flow is still opening, and is once it has opened',()=>{const h=studioHarness();try{assert.equal(h.template(),0);h.opened();assert.equal(h.template(),1);}finally{h.cleanup();}});
test('a recipe does not open while the flow is still opening (the graph is about to change)',()=>{const h=studioHarness();try{assert.equal(h.click(),undefined);assert.equal(h.changes.length,0);}finally{h.cleanup();}});
test('actual starter stages a preview; cancel performs no graph mutation',()=>{const h=studioHarness();try{const dialog=h.open();assert.ok(dialog);assert.equal(h.flowState.pipeline,h.original);dialog.props.onClose();assert.equal(h.changes.length,0);assert.equal(walk(h.render()).find(n=>n.type==='RecipeDialog'),undefined);}finally{h.cleanup();}});
test('actual adoption commits once only after successful current verification',async()=>{const h=studioHarness();try{const dialog=h.open(),pending=dialog.props.onAdopt(mapped);assert.equal(h.changes.length,0);h.resolve();await pending;assert.deepEqual(h.changes,[mapped]);}finally{h.cleanup();}});
for(const scenario of ['namespace ABA','graph changed','busy','cancel','refusal'])test(`actual adoption preserves graph when ${scenario}`,async()=>{const h=studioHarness();try{const dialog=h.open(),pending=dialog.props.onAdopt(mapped);const rejected=assert.rejects(pending);if(scenario==='namespace ABA')h.switchGeneration();if(scenario==='graph changed')h.flowState.pipeline={...h.original,name:'Concurrent edit'};if(scenario==='busy')h.flowState.isRunning=true;if(scenario==='cancel')dialog.props.onClose();if(scenario==='refusal')h.reject();else h.resolve();await rejected;assert.equal(h.changes.length,0);}finally{h.cleanup();}});
test('an evaluation counts only for the exact saved version with the same graph hash, and never implies approval',()=>{const {flowWorkspaceIdentity,APPROVAL_LABELS}=workspaceModule();
 const base={revision:2,draft:'저장본',dirty:false,isDraft:false,savedVersions:[{version_id:'v1',pipeline_hash:'h1',is_active:true}],selectedVersionId:'v1',modelNodes:[{id:'node',job:'job-1',task:'anomaly'}]};
 const valid={version_id:'v1',cohort_id:'cohort',graph_sha256:'h1',validity:{valid:true}};
 assert.equal(flowWorkspaceIdentity({...base,evaluation:valid}).evaluationCurrent,true);
 for(const [name,input] of [['another version',{evaluation:{...valid,version_id:'v0'}}],['another graph',{evaluation:{...valid,graph_sha256:'h0'}}],['invalid',{evaluation:{...valid,validity:{valid:false}}}],['unsaved edit',{dirty:true,evaluation:valid}],['draft',{isDraft:true,evaluation:valid}]]){
  const identity=flowWorkspaceIdentity({...base,...input});assert.equal(identity.evaluationCurrent,false,name);assert.equal(identity.approval,APPROVAL_LABELS.none,`${name}: no approval follows from an evaluation`);}});
test('approval labels keep blocked, selection required and verified apart and show checkpoint hashes',()=>{const {flowWorkspaceIdentity,APPROVAL_LABELS}=workspaceModule();
 const base={revision:1,draft:'저장본',dirty:false,isDraft:false,savedVersions:[{version_id:'v1',pipeline_hash:'h1'}],selectedVersionId:'v1',modelNodes:[{id:'node',job:'job-1',task:'anomaly'}]};
 const labels=['blocked','selection_required','ready'].map(status=>flowWorkspaceIdentity({...base,approval:{status,models:[{job_id:'job-1',checkpoint_sha256:'abc123'}]}}).approval);
 assert.deepEqual(labels,[APPROVAL_LABELS.blocked,APPROVAL_LABELS.selection_required,APPROVAL_LABELS.ready]);assert.equal(new Set(labels).size,3);
 assert.match(flowWorkspaceIdentity({...base,approval:{status:'ready',models:[{job_id:'job-1',checkpoint_sha256:'abc123'}]}}).models[0],/sha256 abc123/);});

// Native regression: a restored draft based on "none" can be explicitly replaced
// by a new recipe based on the active version observed before model verification.
test('explicit recipe adoption snapshots active base instead of inheriting old draft none',async()=>{
 const h=studioHarness();try{assert.equal(h.flowState.baseVersionId,null);const pending=h.open().props.onAdopt(mapped);
 await h.resolve();await pending;assert.equal(h.activeRequests(),1);assert.equal(h.flowState.baseVersionId,'active-v1');
 assert.deepEqual(h.changes,[mapped]);}finally{h.cleanup();}});
test('recipe adoption retains the observed base when another writer activates during verification',async()=>{
 const h=studioHarness();try{const pending=h.open().props.onAdopt(mapped);assert.equal(h.activeRequests(),1);h.changeActive('concurrent-v2');
 await h.resolve();await pending;assert.equal(h.flowState.baseVersionId,'active-v1');assert.notEqual(h.flowState.baseVersionId,h.activeVersion(), 'save CAS must still reject the actual concurrent change');
 }finally{h.cleanup();}});

// Exercise the real save dialog with the diff route's explicit stale response.
async function changeDialog(base,stale){
 let cursor=0;const states=[],effects=[],seen=[],confirmed=[];
 const jsx=(type,props)=>({type,props:props||{}});
 const react={useState:initial=>{const index=cursor++;if(!(index in states))states[index]=initial;return[states[index],value=>states[index]=value];},useEffect:effect=>{if(!effects.length)effects.push(effect);}};
 const compile=(name,mocks={})=>{const m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);m.require=ref=>mocks[ref]??require(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,name);return m.exports;};
 const component=compile(path.join(__dirname,'FlowChangeDialog.tsx'),{react,'react/jsx-runtime':{jsx,jsxs:jsx},'../../services/api':{api:{flowchart:{previewChange:async(pipeline,expected)=>{seen.push(expected);return {parent_revision:stale?'concurrent-v2':'active-v1',stale,layout_only:false,semantic_delta:{changes:[{kind:'node_added',node_id:'new',node_type:'fixed_roi'}]}};}}}},'./flowChangeText':compile(path.join(__dirname,'flowChangeText.ts'))}).FlowChangeDialog;
 const render=()=>{cursor=0;return component({mode:'save',pipeline:mapped,baseVersionId:base,onConfirm:async reason=>confirmed.push(reason),onCancel(){}});};
 render();effects[0]();await new Promise(resolve=>setImmediate(resolve));
 walk(render()).find(node=>node.type==='textarea').props.onChange({target:{value:'Explicit new recipe'}});
 const tree=render(),button=walk(tree).filter(node=>node.type==='button').at(-1);
 button.props.onClick();await new Promise(resolve=>setImmediate(resolve));
 return {seen,confirmed,disabled:button.props.disabled,tree};
}

test('restored-none draft replaced by explicit recipe reaches actual enabled save confirmation',async()=>{
 const h=studioHarness();try{const pending=h.open().props.onAdopt(mapped);await h.resolve();await pending;
 const dialog=await changeDialog(h.flowState.baseVersionId,false);assert.deepEqual(dialog.seen,['active-v1']);
 assert.equal(dialog.disabled,false);assert.deepEqual(dialog.confirmed,['Explicit new recipe']);}finally{h.cleanup();}});

test('actual save dialog refuses concurrent active change after explicit recipe snapshot',async()=>{
 const h=studioHarness();try{const pending=h.open().props.onAdopt(mapped);h.changeActive('concurrent-v2');await h.resolve();await pending;
 const dialog=await changeDialog(h.flowState.baseVersionId,true);assert.deepEqual(dialog.seen,['active-v1']);
 assert.equal(dialog.disabled,true);assert.deepEqual(dialog.confirmed,[]);assert.ok(JSON.stringify(dialog.tree).includes('활성 플로우가 바뀌었습니다'));}finally{h.cleanup();}});

// Use the actual Zustand store so a recipe cannot rebase a stale draft via undo.
function actualFlowStore(){
 const name=path.resolve(__dirname,'../../stores/useFlowchartStore.ts'),m=new Module(name,module);
 m.filename=name;m.paths=Module._nodeModulePaths(path.dirname(name));const original=m.require.bind(m);
 m.require=ref=>ref==='../services/api'?{api:{flowchart:{}}}:ref==='../services/flowDraft'?{flowDraft:{}}:ref==='./useProjectStore'?{useProjectStore:{getState:()=>({project:null})}}:ref==='../components/flowchart/flowchartStartup'?{getFlowchartModelTask:node=>node.data.task||null}:original(ref);
 m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);
 return m.exports.useFlowchartStore;
}
test('actual recipe adoption starts a new basis that undo cannot cross into the stale draft',async()=>{
 const store=actualFlowStore(),h=studioHarness(store);try{
 const pending=h.open().props.onAdopt(mapped);await h.resolve();await pending;
 assert.equal(store.getState().baseVersionId,'active-v1');
 store.getState().undo();
 assert.equal(store.getState().pipeline,mapped,'undo must not restore the stale original with a refreshed writable base');
 assert.equal(store.getState().canUndo,false);
 }finally{h.cleanup();}
});

function adoptionVerification(h){
 let enter;const started=new Promise(resolve=>enter=resolve),verify=h.transport.verifyModels;
 h.transport.verifyModels=(...args)=>{enter();return verify(...args);};
 const dialog=h.open(),pending=dialog.props.onAdopt(mapped);
 return {dialog,pending,started};
}
test('active version lookup refusal keeps the graph and stale base unchanged before model verification',async()=>{
 const h=studioHarness();try{let verifications=0;h.transport.activeVersionId=async()=>{throw new Error('active lookup refused');};
 h.transport.verifyModels=async()=>{verifications++;};
 await assert.rejects(h.open().props.onAdopt(mapped),/active lookup refused/);
 assert.equal(h.flowState.pipeline,h.original);assert.equal(h.flowState.baseVersionId,null);
 assert.equal(h.changes.length,0);assert.equal(verifications,0);
 }finally{h.cleanup();}
});
for(const scenario of ['namespace ABA','graph changed','busy','cancel'])test(`actual adoption preserves graph and base when ${scenario} occurs during model verification`,async()=>{
 const h=studioHarness();try{const {dialog,pending,started}=adoptionVerification(h),rejected=assert.rejects(pending);await started;
 if(scenario==='namespace ABA')h.switchGeneration();
 if(scenario==='graph changed')h.flowState.pipeline={...h.original,name:'Concurrent verification edit'};
 if(scenario==='busy')h.flowState.isSaving=true;
 if(scenario==='cancel')dialog.props.onClose();
 const currentGraph=h.flowState.pipeline;await h.resolve();await rejected;
 assert.equal(h.flowState.pipeline,currentGraph);assert.equal(h.flowState.baseVersionId,null);assert.equal(h.changes.length,0);
 }finally{h.cleanup();}
});
test('actual activation during model verification keeps the earlier observed base for save CAS',async()=>{
 const h=studioHarness();try{const {pending,started}=adoptionVerification(h);await started;h.changeActive('concurrent-v2');
 await h.resolve();await pending;assert.equal(h.flowState.baseVersionId,'active-v1');assert.equal(h.changes.length,1);
 assert.notEqual(h.flowState.baseVersionId,h.activeVersion());
 }finally{h.cleanup();}
});
