const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function load(filename,mocks){const m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(path.dirname(filename));const original=m.require.bind(m);m.require=name=>name in mocks?mocks[name]:original(name);m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText,filename);return m.exports;}
function nodes(tree){if(!tree||typeof tree!=='object')return [];return [tree,...[].concat(tree.props?.children||[]).flatMap(nodes)];}
async function harness(){
  const project={id:'project',project_dir:'/project',source_dataset_dir:'/source',active_labelset_id:'default'};
  const dataset={folderPath:'/source',datasetKey:'/source\0segmentation',hasSelectedFolder:true,isLoading:false,importError:null};
  const compute={transportRevision:0};const owner={project,task:'segmentation'};let apiIdentity='local';const drafts=new Map(),attemptedDraftWrites=[];let fail=false,finish,delay=false,activations=0;
  const context=()=>({project_id:project.id,source_dataset_path:project.source_dataset_dir,labelset_id:project.active_labelset_id});
  const initial={id:'fixed',name:'ROI draft',nodes:[{id:'roi',position:{x:0,y:0},data:{node_type:'fixed_roi',label:'ROI',params:{roi_bbox:[0,0,64,64]}}},{id:'inspect',position:{x:100,y:0},data:{node_type:'inspection',label:'Inspect',task:'segmentation',model_job_id:null}}],edges:[]};
  const api={flowchart:{getPipeline:async()=>structuredClone(initial),getActivePipeline:async()=>structuredClone(initial),getActivePipelineRecord:async()=>({version_id:null,pipeline:structuredClone(initial)}),activeVersionId:async()=>({version_id:null}),savePipeline:async()=>{activations++;return {};}}};
  const flowDraft={get:async()=>{const row=drafts.get(JSON.stringify(context()));if(!row)throw Object.assign(new Error('No draft'),{status:404});return structuredClone(row);},save:async(pipeline,scope,baseVersionId)=>{attemptedDraftWrites.push({pipeline:structuredClone(pipeline),scope:structuredClone(scope),baseVersionId});if(delay)await new Promise(resolve=>{finish=resolve;});if(fail)throw new Error('Disk unavailable');const row={context:scope,pipeline:structuredClone(pipeline),draft_sha256:'b'.repeat(64),active_version_id:null};drafts.set(JSON.stringify(scope),row);return row;}};
  const projectHook=selector=>selector?selector(owner):owner;projectHook.getState=()=>owner;
  const store=load(path.resolve(__dirname,'../../stores/useFlowchartStore.ts'),{'../services/api':{api},'../services/flowDraft':{flowDraft},'./useProjectStore':{useProjectStore:projectHook},'../components/flowchart/flowchartStartup':{getFlowchartModelTask:node=>node.data.task||null}}).useFlowchartStore;
  await store.getState().loadPipeline(true,'segmentation','/source');
  const flowHook=selector=>selector?selector(store.getState()):store.getState();flowHook.getState=store.getState;
  const datasetHook=selector=>selector?selector(dataset):dataset;datasetHook.getState=()=>dataset;
  const computeHook=selector=>selector?selector(compute):compute;computeHook.getState=()=>compute;
  const effects=[],refs=[];let refIndex=0;const react={createElement:(type,props,...children)=>({type,props:{...props,children}}),useEffect:callback=>effects.push(callback),useRef:initial=>refs[refIndex++]||(refs[refIndex-1]={current:initial})};
  const jsx=(type,props)=>({type,props});
  const controls=load(path.resolve(__dirname,'FlowDraftControls.tsx'),{react:{__esModule:true,default:react,...react},'react/jsx-runtime':{jsx,jsxs:jsx},'lucide-react':{Save:'icon'},'../../services/api':{getApiPersistenceIdentity:()=>apiIdentity},'../../stores/useFlowchartStore':{useFlowchartStore:flowHook},'../../stores/useProjectStore':{useProjectStore:projectHook},'../../stores/useDatasetStore':{useDatasetStore:datasetHook},'../../stores/useComputeStore':{useComputeStore:computeHook}});
  const clock=new Map();let clockId=0;const originalSet=global.setTimeout,originalClear=global.clearTimeout;
  global.setTimeout=(callback,delay)=>{const id=++clockId;clock.set(id,{callback,delay});return id;};global.clearTimeout=id=>clock.delete(id);
  const cleanup=[];
  function render(blocked=false){effects.length=0;refIndex=0;const tree=controls.FlowDraftControls({blocked,activeVersion:false,hasSavedVersion:false});for(const effect of effects){const fn=effect();if(typeof fn==='function')cleanup.push(fn);}return tree;}
  return {store,drafts,compute,owner,dataset,initial,render,clock,attemptedDraftWrites,activations:()=>activations,runTimers:async()=>{for(const [id,row] of [...clock]){clock.delete(id);row.callback();}await tick();},leave:async()=>{for(const fn of cleanup.splice(0))fn();await tick();},fail:()=>{fail=true;},recover:()=>{fail=false;},delay:()=>{delay=true;},finish:()=>finish(),switchApi:()=>{apiIdentity='shared:https://other';compute.transportRevision++;},restore:()=>{global.setTimeout=originalSet;global.clearTimeout=originalClear;}};
}
test('model-independent draft button saves ROI and reopens it without activating an executable flow',async()=>{const h=await harness();try{h.store.getState().updateNodeData('roi',{params:{roi_bbox:[12,20,268,276]}});const tree=h.render();const button=nodes(tree).find(n=>n.type==='button');assert.equal(button.props.disabled,false);await button.props.onClick();assert.match(h.store.getState().persistedDraftHash,/^[a-f0-9]{64}$/);assert.equal(h.activations(),0);h.store.getState().invalidateForDataChange();await h.store.getState().loadPipeline(true,'segmentation','/source');assert.deepEqual(h.store.getState().pipeline.nodes[0].data.params.roi_bbox,[12,20,268,276]);assert.equal(h.store.getState().pipelineIsDraft,true);}finally{h.restore();}});
test('debounced auto-draft persists dirty ROI through stage departure and reopen',async()=>{const h=await harness();try{h.store.getState().updateNodeData('roi',{params:{roi_bbox:[4,8,260,264]}});h.render();assert.equal(h.drafts.size,0);assert.equal(h.clock.size,1);await h.runTimers();assert.equal(h.drafts.size,1);assert.equal(h.activations(),0);h.store.getState().invalidateForDataChange();await h.store.getState().loadPipeline(true,'segmentation','/source');assert.deepEqual(h.store.getState().pipeline.nodes[0].data.params.roi_bbox,[4,8,260,264]);}finally{h.restore();}});
test('leaving the stage before debounce safely flushes dirty draft',async()=>{const h=await harness();try{h.store.getState().updateNodeData('roi',{params:{roi_bbox:[1,2,257,258]}});h.render();await h.leave();assert.equal(h.clock.size,0);assert.equal(h.drafts.size,1);assert.equal(h.activations(),0);}finally{h.restore();}});
for(const guard of ['isRunning','isSaving','historyGroupStart','blocked'])test(`auto-draft never writes during ${guard}`,async()=>{const h=await harness();try{h.store.getState().updateNodeData('roi',{params:{roi_bbox:[1,2,257,258]}});if(guard!=='blocked')h.store.setState({[guard]:guard==='historyGroupStart'?h.store.getState().pipeline:true});h.render(guard==='blocked');await h.runTimers();await h.leave();assert.equal(h.drafts.size,0);assert.equal(h.clock.size,0);}finally{h.restore();}});
test('an in-flight old transport auto-save cannot mark the new UI as a durable draft',async()=>{const h=await harness();try{h.store.getState().updateNodeData('roi',{params:{roi_bbox:[1,2,257,258]}});h.delay();h.render();await h.runTimers();h.switchApi();h.finish();await tick();assert.equal(h.store.getState().pipelineDirty,true);assert.equal(h.store.getState().persistedDraftHash,null);}finally{h.restore();}});
test('failed save retains unsaved status and does not auto-retry the same failed graph indefinitely',async()=>{const h=await harness();try{h.store.getState().updateNodeData('roi',{params:{roi_bbox:[1,2,257,258]}});h.fail();h.render();await h.runTimers();const tree=h.render();assert.equal(h.store.getState().pipelineDirty,true);assert.equal(h.store.getState().persistedDraftHash,null);assert.equal(h.clock.size,0);const status=nodes(tree).find(n=>n.props.role==='status');assert.match(status.props.children.join(''),/미저장|저장 전/);}finally{h.restore();}});


// A 650ms callback can already be queued when React's effect cleanup is pending.
// Fire the retained real component callback after the real store save transition;
// count every attempted draft save before either the fixture failure or durable write.
test('queued 650ms autosave does not repeat a completed explicit save before effect cleanup',async()=>{
 const h=await harness();try{
  h.store.getState().updateNodeData('roi',{params:{roi_bbox:[12,20,268,276]}});
  const target=h.store.getState().pipeline,tree=h.render(),button=nodes(tree).find(n=>n.type==='button');
  assert.equal(h.clock.size,1);assert.equal([...h.clock.values()][0].delay,650);
  assert.equal(await button.props.onClick(),true);assert.equal(h.attemptedDraftWrites.length,1);
  assert.equal(h.store.getState().pipeline,target);assert.equal(h.store.getState().pipelineDirty,false);
  const durable=structuredClone([...h.drafts]);assert.match(h.store.getState().persistedDraftHash,/^[a-f0-9]{64}$/);
  await h.runTimers();
  assert.equal(h.attemptedDraftWrites.length,1,'A queued autosave must not write an already-clean saved target');
  assert.deepEqual([...h.drafts],durable);assert.equal(h.store.getState().pipeline,target);
  assert.equal(h.store.getState().pipelineDirty,false);assert.equal(h.activations(),0);assert.equal(h.clock.size,0);
 }finally{h.restore();}
});
test('queued 650ms autosave does not retry a failed same target before effect cleanup but explicit retry still saves',async()=>{
 const h=await harness();try{
  h.store.getState().updateNodeData('roi',{params:{roi_bbox:[7,9,263,265]}});
  const target=h.store.getState().pipeline,tree=h.render(),button=nodes(tree).find(n=>n.type==='button');
  assert.equal(h.clock.size,1);assert.equal([...h.clock.values()][0].delay,650);h.fail();
  assert.equal(await button.props.onClick(),false);assert.equal(h.attemptedDraftWrites.length,1);
  assert.equal(h.store.getState().pipeline,target);assert.equal(h.store.getState().pipelineDirty,true);
  assert.equal(h.store.getState().persistedDraftHash,null);assert.equal(h.drafts.size,0);
  await h.runTimers();
  assert.equal(h.attemptedDraftWrites.length,1,'A queued autosave must not retry the failed same target');
  assert.equal(h.drafts.size,0);assert.equal(h.store.getState().pipelineDirty,true);
  const failure=nodes(h.render()).find(n=>n.props.role==='status');assert.match(failure.props.children.join(''),/미저장|저장 전/);assert.equal(h.clock.size,0);
  h.recover();assert.equal(await button.props.onClick(),true);assert.equal(h.attemptedDraftWrites.length,2);
  assert.deepEqual(h.attemptedDraftWrites[1],h.attemptedDraftWrites[0]);assert.equal(h.drafts.size,1);
  assert.equal(h.store.getState().pipeline,target);assert.equal(h.store.getState().pipelineDirty,false);
  assert.match(h.store.getState().persistedDraftHash,/^[a-f0-9]{64}$/);assert.equal(h.activations(),0);
 }finally{h.restore();}
});
for(const guard of ['isLoading','isRunning','isSaving','historyGroupStart','blocked','source','labelset','task','transport'])test(`queued 650ms autosave rechecks live ${guard} before any draft write`,async()=>{
 const h=await harness();try{
  h.store.getState().updateNodeData('roi',{params:{roi_bbox:[3,4,259,260]}});h.render();
  assert.equal(h.clock.size,1);assert.equal([...h.clock.values()][0].delay,650);
  if(['isLoading','isRunning','isSaving','historyGroupStart'].includes(guard))h.store.setState({[guard]:guard==='historyGroupStart'?h.store.getState().pipeline:true});
  else if(guard==='blocked')h.render(true);
  else if(guard==='source')h.dataset.folderPath='/different-source';
  else if(guard==='labelset')h.owner.project={...h.owner.project,active_labelset_id:'different-labelset'};
  else if(guard==='task')h.owner.task='anomaly';
  else h.switchApi();
  await h.runTimers();assert.equal(h.attemptedDraftWrites.length,0);assert.equal(h.drafts.size,0);
  assert.equal(h.store.getState().pipelineDirty,true);assert.equal(h.store.getState().persistedDraftHash,null);assert.equal(h.activations(),0);
 }finally{h.restore();}
});
test('a fresh edited graph remains eligible after a different target failed autosave',async()=>{
 const h=await harness();try{
  h.store.getState().updateNodeData('roi',{params:{roi_bbox:[1,2,257,258]}});const failed=h.store.getState().pipeline;
  h.fail();h.render();await h.runTimers();assert.equal(h.attemptedDraftWrites.length,1);assert.equal(h.drafts.size,0);
  h.recover();h.store.getState().updateNodeData('roi',{params:{roi_bbox:[5,6,261,262]}});const next=h.store.getState().pipeline;
  assert.notEqual(next,failed);h.render();assert.equal(h.clock.size,1);await h.runTimers();
  assert.equal(h.attemptedDraftWrites.length,2);assert.equal(h.drafts.size,1);assert.notDeepEqual(h.attemptedDraftWrites[1].pipeline,h.attemptedDraftWrites[0].pipeline);
  assert.equal(h.store.getState().pipeline,next);assert.equal(h.store.getState().pipelineDirty,false);assert.equal(h.activations(),0);
 }finally{h.restore();}
});
test('explicit save authority remains available for a clean pipeline with no autosave timer',async()=>{
 const h=await harness();try{
  assert.equal(h.store.getState().pipelineDirty,false);const tree=h.render(),button=nodes(tree).find(n=>n.type==='button');
  assert.equal(h.clock.size,0);assert.equal(button.props.disabled,false);assert.equal(await button.props.onClick(),true);
  assert.equal(h.attemptedDraftWrites.length,1);assert.equal(h.drafts.size,1);assert.equal(h.store.getState().pipelineDirty,false);assert.equal(h.activations(),0);
 }finally{h.restore();}
});
