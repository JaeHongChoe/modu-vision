const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const nodes=t=>Array.isArray(t)?t.flatMap(nodes):t&&typeof t==='object'?[t,...nodes(t.props?.children)]:[];
function fixture(){
 let index=0,tree,dirty=true,generation=1,subscriber,handler;
 const slots=[],effects=[],calls=[];
 const state={projectDir:'/project',project:{id:'p',task:'classification',source_dataset_dir:'/source',active_labelset_id:'default'}};
 const react={useState(v){const i=index++;if(!(i in slots))slots[i]=typeof v==='function'?v():v;return[slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v;dirty=true;}];},useRef(v){const i=index++;return slots[i]??(slots[i]={current:v});},useEffect(fn,deps){const i=index++;if(JSON.stringify(slots[i]?.deps)!==JSON.stringify(deps)){const old=slots[i];slots[i]={deps};effects.push(()=>{old?.cleanup?.();slots[i].cleanup=fn();});}}};
 const file=path.join(__dirname,'ModelOperationsPanel.tsx'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const req=m.require.bind(m);
 const hook=selector=>selector(state),snapshot=()=>({active:false,policy:null,watcher:{running:false},cycles:[{cycle_id:'cycle_old_account',status:'awaiting_approval',phase:'finish',created_at:1,error:null,result:{},events:[]}]});
 m.require=n=>n==='react'?react:n.endsWith('/useProjectStore')?{useProjectStore:hook}:n==='./ManualOperationsReviewPanel'?{ManualOperationsReviewPanel:()=>null}:n==='./useDeliveryScope'?{useDeliveryScope:()=>({key:JSON.stringify(state.project)})}:n.endsWith('/services/api')?{getProjectContextGeneration:()=>generation,subscribeProjectContext:fn=>{subscriber=fn;return()=>{};},api:{flowchart:{listPipelines:async()=>({pipelines:[]})}},request:async(url,options)=>{calls.push({url,options});if(options)return{};if(url.endsWith('/models'))return{models:[]};return handler?handler():snapshot();}}:req(n);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
 function render(){index=0;dirty=false;tree=m.exports.ModelOperationsPanel();effects.splice(0).forEach(f=>f());}
 async function settle(){for(let i=0;i<10;i++){if(dirty)render();await new Promise(setImmediate);if(!dirty)return;}}
 return{state,render,settle,calls,all:()=>nodes(tree),setHandler:fn=>handler=fn,snapshot,switchAccount(){generation++;subscriber?.();dirty=true;},button:()=>nodes(tree).find(n=>n.type==='button'&&n.props.children==='실행·감시 취소')};
}
test('retained operations callback cannot mutate a newly selected project',async()=>{
 const f=fixture();await f.settle();const old=f.button();f.state.project={...f.state.project,id:'other',source_dataset_dir:'/other'};f.render();await f.settle();const count=f.calls.length;old.props.onClick();await f.settle();assert.equal(f.calls.length,count);
});
test('a delayed prior-account operations journal is discarded',async()=>{
 const f=fixture();let resolve;f.setHandler(()=>new Promise(r=>resolve=r));f.render();await new Promise(setImmediate);const old=resolve;
 f.switchAccount();f.setHandler(()=>({...f.snapshot(),cycles:[]}));f.render();await f.settle();old(f.snapshot());await f.settle();
 assert(!f.all().some(n=>n.type==='article'));assert(f.calls.every(c=>!c.options));
});
