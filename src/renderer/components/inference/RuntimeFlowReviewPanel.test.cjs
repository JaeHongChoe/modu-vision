const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const nodes=t=>Array.isArray(t)?t.flatMap(nodes):t&&typeof t==='object'?[t,...nodes(t.props?.children)]:[];
function fixture(){
 let index=0,tree,dirty=true,generation=1,subscriber,previewHandler;
 const slots=[],effects=[],calls=[],props={packagePath:'/project/exports/converted'};
 const report=()=>({base_revision_id:'flowapproval_'+'a'.repeat(32),manifest_sha256:'b'.repeat(64),runtime_acceptance_sha256:'c'.repeat(64),review_revision_id:null,review_valid:false,device_accepted:false,policy:{policy_id:'fixture',revision:1,maximum_escape_rate:0,maximum_overkill_rate:0,maximum_review_rate:0},metrics:{escape_rate:0,overkill_rate:0,review_rate:0,normal_count:2,defect_count:2},outputs:[{relative_path:'part.png',image_sha256:'d'.repeat(64),truth:'NG',decision:'NG',output_sha256:'e'.repeat(64)}]});
 const react={useState(v){const i=index++;if(!(i in slots))slots[i]=typeof v==='function'?v():v;return[slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v;dirty=true;}];},useRef(v){const i=index++;return slots[i]??(slots[i]={current:v});},useEffect(fn,deps){const i=index++;if(JSON.stringify(slots[i]?.deps)!==JSON.stringify(deps)){const previous=slots[i];slots[i]={deps};effects.push(()=>{previous?.cleanup?.();slots[i].cleanup=fn();});}}};
 const api={active:async()=>{calls.push('read-base');return{revision_id:report().base_revision_id,validity:{valid:true}};},runtimePreview:async()=>{calls.push('read-converted');return previewHandler?previewHandler():report();},reviewRuntime:async(revision,body)=>{calls.push({revision,body});return{revision_id:'runtimeflow_'+'f'.repeat(32),device_accepted:false};}};
 const file=path.join(__dirname,'RuntimeFlowReviewPanel.tsx'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const req=m.require.bind(m);
 m.require=n=>n==='react'?react:n.endsWith('/services/api')?{getProjectContextGeneration:()=>generation,subscribeProjectContext:fn=>{subscriber=fn;return()=>{};}}:n.endsWith('/wholeFlowApproval')?{wholeFlowApproval:api}:req(n);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
 function render(){index=0;dirty=false;tree=m.exports.RuntimeFlowReviewPanel(props);effects.splice(0).forEach(f=>f());}
 async function settle(){for(let i=0;i<10;i++){if(dirty)render();await new Promise(setImmediate);}}
 const find=label=>nodes(tree).find(n=>n.props?.['aria-label']===label);
 return{props,calls,report,render,settle,setHandler:fn=>previewHandler=fn,all:()=>nodes(tree),button:()=>nodes(tree).find(n=>n.type==='button'),switchAccount(){generation++;subscriber?.();dirty=true;},async fill(){await settle();find('변환 전체 흐름 검토자').props.onChange({target:{value:'Reviewer'}});find('변환 전체 흐름 검토 이유').props.onChange({target:{value:'Reviewed converted image results'}});find('변환 전체 흐름 직접 검토').props.onChange({target:{checked:true}});await settle();}};
}
test('opening a converted review reads only; explicit review rechecks and uses CAS',async()=>{
 const f=fixture();await f.settle();assert.equal(f.calls.filter(c=>typeof c==='object').length,0);assert.equal(f.button().props.disabled,true);
 await f.fill();await f.button().props.onClick();await f.settle();const writes=f.calls.filter(c=>typeof c==='object');assert.equal(writes.length,1);assert.equal(writes[0].body.expected_revision,null);assert.equal(writes[0].body.holdout_reviewed,true);assert.equal(writes[0].body.package_path,f.props.packagePath);assert.equal(f.button().props.disabled,true);
});
test('changed runtime evidence on save refuses mutation',async()=>{
 const f=fixture();await f.fill();f.setHandler(()=>({...f.report(),manifest_sha256:'changed'}));await f.button().props.onClick();await f.settle();assert.equal(f.calls.filter(c=>typeof c==='object').length,0);assert(f.all().some(n=>n.props?.role==='alert'));
});
test('a retained prior-account button and late read cannot save or reveal evidence',async()=>{
 const f=fixture();await f.fill();const old=f.button();let resolve;f.setHandler(()=>new Promise(r=>resolve=r));const pending=old.props.onClick();await new Promise(setImmediate);const finish=resolve;f.switchAccount();f.setHandler(()=>f.report());await f.settle();finish(f.report());await pending;await f.settle();assert.equal(f.calls.filter(c=>typeof c==='object').length,0);const count=f.calls.length;await old.props.onClick();assert.equal(f.calls.length,count);assert.equal(f.button().props.disabled,true);
});
test('switching packages clears review confirmation and stale save closures',async()=>{
 const f=fixture();await f.fill();const old=f.button();f.props.packagePath='/project/exports/another';f.render();await f.settle();assert.equal(f.button().props.disabled,true);const count=f.calls.length;await old.props.onClick();assert.equal(f.calls.length,count);
});
