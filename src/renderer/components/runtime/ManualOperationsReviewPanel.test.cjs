const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const nodes=t=>Array.isArray(t)?t.flatMap(nodes):t&&typeof t==='object'?[t,...nodes(t.props?.children)]:[];
function fixture(){
 let index=0,tree,dirty=true,generation=1,subscriber,handler;
 const slots=[],effects=[],calls=[],steps=[],origins=[];
 const props={cycleId:'cycle_'+'a'.repeat(32)};
 const state={projectDir:'/project',project:{id:'p',task:'classification',source_dataset_dir:'/source',active_labelset_id:'default'}};
 const compute={transportRevision:1,selectedProfileId:null};
 const react={useState(v){const i=index++;if(!(i in slots))slots[i]=typeof v==='function'?v():v;return[slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v;dirty=true;}];},useRef(v){const i=index++;return slots[i]??(slots[i]={current:v});},useEffect(fn,deps){const i=index++;if(JSON.stringify(slots[i]?.deps)!==JSON.stringify(deps)){const previous=slots[i];slots[i]={deps};effects.push(()=>{previous?.cleanup?.();slots[i].cleanup=fn();});}}};
 const hook=object=>Object.assign(selector=>selector?selector(object):object,{getState:()=>object});state.setStep=async step=>steps.push(step);
 const file=path.join(__dirname,'ManualOperationsReviewPanel.tsx'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const req=m.require.bind(m);
 m.require=n=>n==='react'?react:n.endsWith('/useProjectStore')?{useProjectStore:hook(state)}:n.endsWith('/useComputeStore')?{useComputeStore:hook(compute)}:n==='./useDeliveryScope'?{useDeliveryScope:extra=>({key:JSON.stringify([state.project,compute.transportRevision,extra]),project:state.project,projectDir:state.projectDir})}:n.endsWith('/services/api')?{getProjectContextGeneration:()=>generation,getApiPersistenceIdentity:()=> 'local',subscribeProjectContext:fn=>{subscriber=fn;return()=>{};},request:async(url,options)=>{calls.push({url,options});return handler?handler():report();}}:n.endsWith('/productDataWorkflow')?{evaluationOriginScope:()=> 'scoped',rememberReviewOrigin:(_storage,scope,id)=>origins.push({scope,id})}:n.endsWith('/taskHandoff')?{clearTaskHandoff(){}}:req(n);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
 function render(){index=0;dirty=false;tree=m.exports.ManualOperationsReviewPanel(props);effects.splice(0).forEach(f=>f());}
 async function settle(){for(let i=0;i<10;i++){if(dirty)render();await new Promise(setImmediate);if(!dirty)return;}}
 function report(status='awaiting_human_review'){return{cycle_id:props.cycleId,state:status,subject:{task:'classification',candidate_job_id:'job_candidate',comparison_id:'comparison_'+'b'.repeat(32),subject_sha256:'c'.repeat(64)},reasons:[],next_step:status==='model_approval_current'?6:status==='awaiting_human_review'?4:null,automatic_action:'none',service_applied:false,device_accepted:false,approval_revision_id:status==='model_approval_current'?'approved':null};}
 return{props,state,compute,report,calls,steps,origins,render,settle,setHandler:fn=>handler=fn,all:()=>nodes(tree),button:()=>nodes(tree).find(n=>n.type==='button'&&n.props['aria-label']==='모델 개선 다음 단계 열기'),switchAccount(){generation++;subscriber?.();dirty=true;},unmount(){for(const s of slots)s?.cleanup?.();}};
}
global.localStorage={getItem:()=>null,setItem(){},removeItem(){}};
test('opening a bound manual cycle reads only and navigation rechecks exact evidence',async()=>{
 const f=fixture();await f.settle();assert.equal(f.calls.length,1);assert.deepEqual(f.steps,[]);assert(f.calls.every(c=>!c.options));
 await f.button().props.onClick();await f.settle();assert.equal(f.calls.length,2);assert.deepEqual(f.steps,[4]);assert.equal(f.origins[0].id,f.report().subject.comparison_id);
});
test('current model approval links packaging without applying a service',async()=>{
 const f=fixture();f.setHandler(()=>f.report('model_approval_current'));await f.settle();assert.deepEqual(f.steps,[]);await f.button().props.onClick();await f.settle();assert.deepEqual(f.steps,[6]);assert(f.calls.every(c=>!c.options));
});
test('changed evidence during navigation refuses the retained ready action',async()=>{
 const f=fixture();await f.settle();f.setHandler(()=>({...f.report('revalidation_required'),reasons:['checkpoint changed']}));await f.button().props.onClick();await f.settle();assert.deepEqual(f.steps,[]);assert.equal(f.button().props.disabled,true);assert(f.all().some(n=>n.props?.role==='alert'));
});
test('a delayed prior-account result and retained button cannot expose or navigate history',async()=>{
 const f=fixture();await f.settle();const old=f.button();let resolve;f.setHandler(()=>new Promise(r=>resolve=r));const pending=old.props.onClick();await new Promise(setImmediate);const finishOld=resolve;f.switchAccount();f.render();await f.settle();finishOld(f.report());await pending;await f.settle();assert.deepEqual(f.steps,[]);const count=f.calls.length;await old.props.onClick();assert.equal(f.calls.length,count);assert.deepEqual(f.origins,[]);
});
test('late cycle response cannot overwrite a new cycle and unbound history has no action',async()=>{
 const f=fixture();let resolve;f.setHandler(()=>new Promise(r=>resolve=r));f.render();await new Promise(setImmediate);const old=resolve,oldReport=f.report();f.props.cycleId='cycle_'+'d'.repeat(32);f.setHandler(()=>({...f.report('unbound_history'),subject:null}));f.render();await f.settle();old(oldReport);await f.settle();assert.equal(f.button().props.disabled,true);assert(!f.all().some(n=>String(n.props?.children).includes('job_candidate')));assert.deepEqual(f.steps,[]);
});
test('wrong cycle response and another task cannot offer a next stage',async()=>{
 const f=fixture();f.setHandler(()=>({...f.report(),cycle_id:'foreign'}));await f.settle();assert.equal(f.button().props.disabled,true);assert(f.all().some(n=>n.props?.role==='alert'));
 const g=fixture();g.state.project.task='detection';await g.settle();assert.equal(g.button().props.disabled,true);assert.deepEqual(g.steps,[]);
});
