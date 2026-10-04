const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const nodes=t=>Array.isArray(t)?t.flatMap(nodes):t&&typeof t==='object'?[t,...nodes(t.props?.children)]:[];
const record=(id,job='candidate',extra={})=>({evaluation_id:id,created_at:1791150000,result:{job_id:job,task:'ocr',split:'test',sample_count:8,exact_match_accuracy:1},binding:{checkpoint_sha256:job==='incumbent'?'a'.repeat(64):'b'.repeat(64),dataset_fingerprint:'frozen-source',family_dataset_sha256:'heldout1',source_dataset_path:'/source'},...extra});
function fixture(config={}){
 let index=0,tree,dirty=true,generation=1,listener,resolveHistory,resolveApproval;
 const slots=[],effects=[],calls=[];const project={projectDir:'/project'},dataset={folderPath:'/source'};
 const hook=object=>Object.assign(selector=>selector(object),{getState:()=>object});
 const react={useState(value){const i=index++;if(!(i in slots))slots[i]=typeof value==='function'?value():value;return[slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v;dirty=true;}];},useRef(value){const i=index++;return slots[i]??(slots[i]={current:value});},useEffect(fn,deps){const i=index++;if(JSON.stringify(slots[i]?.deps)!==JSON.stringify(deps)){const previous=slots[i];slots[i]={deps};effects.push(()=>{previous?.cleanup?.();slots[i].cleanup=fn();});}}};
 const rows=config.records||[record('candidate-eval'),record('incumbent-eval','incumbent'),record('wrong-cohort','incumbent',{binding:{...record('x','incumbent').binding,family_dataset_sha256:'other'}}),record('validation-eval','incumbent',{result:{...record('x','incumbent').result,split:'val'}})];
 const active=config.active===undefined?{job_id:'incumbent',checkpoint_sha256:'a'.repeat(64),valid:true}:config.active;
 const api={getApiPersistenceIdentity:()=> 'local:actor-'+generation,getProjectContextGeneration:()=>generation,subscribeProjectContext(fn){listener=fn;return()=>{};},request:async(url,options)=>{
  if(options){calls.push(JSON.parse(options.body));if(config.deferApproval)return new Promise(r=>{resolveApproval=r;});return {revision:{revision_id:'controlled'}};}
  if(url.includes('model-deployments/active')){if(config.failActive)throw Error('Controlled active-state outage');return{active};}
  if(config.deferHistory)return new Promise(r=>{resolveHistory=r;});return{items:rows};
 }};
 const file=path.join(__dirname,'SpecializedApprovalPanel.tsx'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const req=m.require.bind(m);
 m.require=n=>n==='react'?react:n.includes('useProjectStore')?{useProjectStore:hook(project)}:n.includes('useDatasetStore')?{useDatasetStore:hook(dataset)}:n.endsWith('/services/api')?api:req(n);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{fileName:file,compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
 function render(){index=0;dirty=false;tree=m.exports.SpecializedApprovalPanel();effects.splice(0).forEach(f=>f());}
 async function settle(){for(let i=0;i<10;i++){if(dirty)render();await new Promise(setImmediate);if(!dirty)return;}}
 const all=()=>nodes(tree),field=label=>all().find(n=>n.props?.['aria-label']===label),change=async(label,value)=>{field(label).props.onChange({target:{value}});await settle();};
 const review=async()=>{all().find(n=>n.type==='input'&&n.props.type==='checkbox').props.onChange({target:{checked:true}});await change('특수 모델 검토자','fixture reviewer');await change('특수 모델 승인 근거','Synthetic handler test only');};
 const button=()=>all().find(n=>n.type==='button'&&n.props.children==='품질 기준 검증 후 승인');
 return{calls,all,field,change,review,button,settle,render,switchActor(){generation++;listener?.();},resolveHistory(){resolveHistory({items:rows});},resolveApproval(){resolveApproval({revision:{revision_id:'stale-actor'}});}};
}
test('incumbent is selected from matching actual saved test records, never entered as text',async()=>{
 const f=fixture();await f.settle();await f.change('특수 모델 평가 승인','candidate-eval');const select=f.field('특수 모델 기준 평가');assert.equal(select.type,'select');assert.deepEqual(nodes(select).filter(n=>n.type==='option'&&n.props.value).map(n=>n.props.value),['incumbent-eval']);await f.change('특수 모델 기준 평가','incumbent-eval');await f.review();assert.equal(f.button().props.disabled,false);await f.button().props.onClick();await f.settle();assert.equal(f.calls[0].evaluation_id,'candidate-eval');assert.equal(f.calls[0].incumbent_evaluation_id,'incumbent-eval');
});
test('candidate change clears the prior incumbent and independent-review acknowledgement',async()=>{
 const f=fixture({records:[record('candidate-eval'),record('other-candidate'),record('incumbent-eval','incumbent')]});await f.settle();await f.change('특수 모델 평가 승인','candidate-eval');await f.change('특수 모델 기준 평가','incumbent-eval');await f.review();await f.change('특수 모델 평가 승인','other-candidate');assert.equal(f.field('특수 모델 기준 평가').props.value,'');assert.equal(f.all().find(n=>n.type==='input'&&n.props.type==='checkbox').props.checked,false);assert.equal(f.button().props.disabled,true);
});
test('unknown current approval state blocks a submission even with filled reviewer controls',async()=>{
 const f=fixture({failActive:true});await f.settle();await f.change('특수 모델 평가 승인','candidate-eval');await f.review();assert.equal(f.button().props.disabled,true);assert.equal(f.calls.length,0);
});
test('a forged incumbent ID cannot be posted outside the saved compatible options',async()=>{
 const f=fixture();await f.settle();await f.change('특수 모델 평가 승인','candidate-eval');await f.change('특수 모델 기준 평가','not-a-saved-record');await f.review();assert.equal(f.button().props.disabled,true);await f.button().props.onClick();assert.equal(f.calls.length,0);
});
test('late history from the previous actor cannot populate the new approval panel',async()=>{
 const f=fixture({deferHistory:true});await f.settle();f.switchActor();f.resolveHistory();await f.settle();assert.equal(nodes(f.field('특수 모델 평가 승인')).filter(n=>n.type==='option'&&n.props.value).length,0);assert.equal(f.field('특수 모델 평가 승인').props.value,'');assert.equal(f.button().props.disabled,true);
});
test('late approval response cannot show a revision in a different actor namespace',async()=>{
 const f=fixture({deferApproval:true});await f.settle();await f.change('특수 모델 평가 승인','candidate-eval');await f.change('특수 모델 기준 평가','incumbent-eval');await f.review();const pending=f.button().props.onClick();await f.settle();f.switchActor();await f.settle();f.resolveApproval();await pending;await f.settle();assert(!f.all().some(n=>n.props?.role==='status'&&String(n.props.children).includes('stale-actor')));
});
test('a failed active read can be retried explicitly and previous selections stay cleared',async()=>{
 const config={failActive:true};const f=fixture(config);await f.settle();assert.equal(f.button().props.disabled,true);config.failActive=false;
 const refresh=f.all().find(n=>n.type==='button'&&n.props['aria-label']==='특수 모델 평가 기록 새로 고침');assert(refresh,'a failed read needs an explicit retry');refresh.props.onClick();await f.settle();await f.change('특수 모델 평가 승인','candidate-eval');assert.equal(nodes(f.field('특수 모델 기준 평가')).filter(n=>n.type==='option'&&n.props.value).length,1);assert.equal(f.field('특수 모델 기준 평가').props.value,'');assert.equal(f.button().props.disabled,true);
});
test('refresh requires a fresh candidate and independent-review acknowledgement',async()=>{
 const f=fixture();await f.settle();await f.change('특수 모델 평가 승인','candidate-eval');await f.change('특수 모델 기준 평가','incumbent-eval');await f.review();const refresh=f.all().find(n=>n.type==='button'&&n.props['aria-label']==='특수 모델 평가 기록 새로 고침');assert(refresh);refresh.props.onClick();await f.settle();assert.equal(f.field('특수 모델 평가 승인').props.value,'');assert.equal(f.field('특수 모델 기준 평가').props.value,'');assert.equal(f.all().find(n=>n.type==='input'&&n.props.type==='checkbox').props.checked,false);assert.equal(f.button().props.disabled,true);
});
test('first approval has no incumbent while invalid existing approval never becomes first approval',async()=>{
 const first=fixture({active:null});await first.settle();await first.change('특수 모델 평가 승인','candidate-eval');await first.review();assert.equal(first.button().props.disabled,false);await first.button().props.onClick();await first.settle();assert.equal(first.calls[0].incumbent_evaluation_id,null);
 const invalid=fixture({active:{job_id:'incumbent',checkpoint_sha256:'a'.repeat(64),valid:false}});await invalid.settle();await invalid.change('특수 모델 평가 승인','candidate-eval');await invalid.change('특수 모델 기준 평가','incumbent-eval');await invalid.review();assert.equal(invalid.button().props.disabled,true);await invalid.button().props.onClick();assert.equal(invalid.calls.length,0);
});
