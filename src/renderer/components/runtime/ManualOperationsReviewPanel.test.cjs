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
 m.require=n=>n==='react'?react:n.endsWith('/useProjectStore')?{useProjectStore:hook(state)}:n.endsWith('/useComputeStore')?{useComputeStore:hook(compute)}:n==='./useDeliveryScope'?{useDeliveryScope:extra=>({key:JSON.stringify([state.project,compute.transportRevision,extra]),project:state.project,projectDir:state.projectDir})}:n.endsWith('/services/api')?{getProjectContextGeneration:()=>generation,getApiPersistenceIdentity:()=> 'local',subscribeProjectContext:fn=>{subscriber=fn;return()=>{};},request:async(url,options)=>{calls.push({url,options});return handler?handler(url,options):report();}}:n.endsWith('/productDataWorkflow')?{evaluationOriginScope:()=> 'scoped',rememberReviewOrigin:(_storage,scope,id)=>origins.push({scope,id})}:n.endsWith('/taskHandoff')?{clearTaskHandoff(){}}:req(n);
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

async function fillPreparation(f){
 f.props.flows=[{version_id:'e'.repeat(32),pipeline_hash:'f'.repeat(64),name:'Original flow',source_dataset_path:'/source'}];
 f.setHandler(()=>f.report('model_approval_current'));f.render();await f.settle();
 for(const [label,value] of [['개선 후보 원본 플로우','e'.repeat(32)],['후보 플로우 준비 담당자','Explicit control'],['후보 플로우 준비 이유','Prepare an independent inactive candidate graph']]){
  f.all().find(n=>n.props?.['aria-label']===label).props.onChange({target:{value}});await f.settle();
 }
 return f.all().find(n=>n.props?.['aria-label']==='검토용 후보 플로우 준비');
}
test('explicit candidate preparation posts exact pins and reopens an inactive graph without activation',async()=>{
 const f=fixture(),button=await fillPreparation(f);assert.equal(button.props.disabled,false);
 let prepared=null;
 f.setHandler((url,options)=>{
  if(options){assert.equal(options.method,'POST');assert(url.endsWith('/prepare-flow'));const body=JSON.parse(options.body);
   assert.equal(body.expected_graph_sha256,'f'.repeat(64));assert.equal(body.expected_subject_sha256,'c'.repeat(64));
   assert.equal(body.source_version_id,'e'.repeat(32));prepared={version_id:'1'.repeat(32),receipt_sha256:'2'.repeat(64),flow_activated:false,service_applied:false};return prepared;
  }
  return {...f.report('model_approval_current'),...(prepared?{prepared_flow:prepared,next_step:5}:{})};
 });
 await button.props.onClick();await f.settle();assert.equal(f.calls.filter(c=>c.options).length,1);assert.deepEqual(f.steps,[]);
 await f.button().props.onClick();await f.settle();assert.deepEqual(f.steps,[5]);assert(f.calls.filter(c=>c.options).every(c=>c.url.endsWith('/prepare-flow')));
});
test('changed model authority and a delayed account response prevent candidate preparation',async()=>{
 const f=fixture(),button=await fillPreparation(f);f.setHandler(()=>f.report('revalidation_required'));
 await button.props.onClick();await f.settle();assert(f.calls.every(c=>!c.options));
 const g=fixture(),retained=await fillPreparation(g);let finish;g.setHandler(()=>new Promise(resolve=>finish=resolve));
 const pending=retained.props.onClick();await new Promise(setImmediate);const old=finish;g.switchAccount();g.render();await g.settle();old(g.report('model_approval_current'));await pending;await g.settle();
 assert(g.calls.every(c=>!c.options));assert.deepEqual(g.steps,[]);
});

function deliveryReport(f,hash='8'.repeat(64)){
 return {...f.report('model_approval_current'),next_step:6,
  prepared_flow:{version_id:'1'.repeat(32),receipt_sha256:'2'.repeat(64),flow_activated:false,service_applied:false},
  delivery:{whole_flow_current:true,whole_flow_revision_id:'flowapproval_'+'4'.repeat(32),service_application_recorded:true,
   service_runtime_ready:true,service_deployment_id:'7'.repeat(32),snapshot_sha256:hash,device_accepted:false,reasons:[]}};
}
const paragraphText=f=>f.all().filter(n=>n.type==='p').flatMap(n=>[n.props.children].flat(Infinity)).filter(v=>typeof v==='string').join(' ');
test('current candidate graph and running service readback replace the pending graph text',async()=>{
 const f=fixture();f.setHandler(()=>deliveryReport(f));await f.settle();
 assert(paragraphText(f).includes('현재 전체 흐름 검토 확인'));assert(paragraphText(f).includes('현재 독립 서비스 응답 확인'));
 assert(!paragraphText(f).includes('전체 흐름 검토 대기'));await f.button().props.onClick();await f.settle();assert.deepEqual(f.steps,[6]);assert(f.calls.every(c=>!c.options));
});
test('delivery snapshot changes fence retained navigation and stopped service is shown separately',async()=>{
 const f=fixture();f.setHandler(()=>deliveryReport(f));await f.settle();const retained=f.button();
 f.setHandler(()=>({...deliveryReport(f,'9'.repeat(64)),delivery:{...deliveryReport(f,'9'.repeat(64)).delivery,service_runtime_ready:false}}));
 await retained.props.onClick();await f.settle();assert.deepEqual(f.steps,[]);assert(f.all().some(n=>n.props?.role==='alert'));
 assert(paragraphText(f).includes('서비스 적용 기록 확인'));assert(!paragraphText(f).includes('현재 독립 서비스 응답 확인'));
});
