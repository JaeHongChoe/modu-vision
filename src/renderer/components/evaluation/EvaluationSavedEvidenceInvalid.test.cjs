const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const nodes=t=>Array.isArray(t)?t.flatMap(nodes):t&&typeof t==='object'?[t,...nodes(t.props?.children)]:[];
const text=n=>[n?.props?.children].flat(Infinity).map(x=>typeof x==='object'?text(x):x??'').join('');
function storage(){const values=new Map();return{values,getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};}
function record(id,labelset='default',task='classification'){return{evaluation_id:id,binding:{labelset_id:labelset},result:{task,job_id:'model-'+id,test_predictions:[]},grouped_errors:{product:{},lot:{},ground_truth:{}}};}
function fixture(config={}){
 let index=0,tree,dirty=true;const slots=[],effects=[],requests=[],catalogs=[];
 const compute={selectedProfileId:null,transportRevision:1,...config.compute};
 const project={project:{id:'project-one',active_labelset_id:'default'}};
 const props={sourceFolder:'/source-one',task:'classification',jobId:null};
 const local=config.storage||storage();let identity='local';
 const react={useState(value){const i=index++;if(!(i in slots))slots[i]=typeof value==='function'?value():value;return[slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v;dirty=true;}];},useRef(value){const i=index++;return slots[i]??(slots[i]={current:value});},useEffect(fn,deps){const i=index++;if(JSON.stringify(slots[i]?.deps)!==JSON.stringify(deps)){const previous=slots[i];slots[i]={deps};effects.push(()=>{previous?.cleanup?.();slots[i].cleanup=fn();});}}};
 const hook=state=>selector=>selector?selector(state):state;
 function load(file){const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const req=m.require.bind(m);m.require=n=>n==='react'?react:n.endsWith('/services/api')?{getApiPersistenceIdentity:()=>identity,api:{project:{listLabelsets:async()=>{catalogs.push({projectId:project.project.id,transportRevision:compute.transportRevision,identity});return config.listLabelsets?config.listLabelsets():{labelsets:config.labelsets||[{id:'default',name:'Default'},{id:'second',name:'Second'}]};}}},request:async(url,options)=>{requests.push({url,options});if(options)throw Error('Opening history must not dispatch');const q=new URL(url,'http://local').searchParams;const response={items:(config.records||[record('eval-first'),record('eval-second'),record('eval-other','second'),record('eval-seg','second','segmentation')]).filter(r=>r.result.task===q.get('task')&&(!q.get('labelset_id')||r.binding.labelset_id===q.get('labelset_id')))};return config.reply?config.reply(q,response):response;}}:n.includes('useComputeStore')?{useComputeStore:hook(compute)}:n.includes('useProjectStore')?{useProjectStore:hook(project)}:n==='./EvaluationEvidencePanel'?{EvaluationEvidencePanel:()=>null}:n.startsWith('.')&&fs.existsSync(path.resolve(path.dirname(file),n+'.ts'))?load(path.resolve(path.dirname(file),n+'.ts')):req(n);
 m._compile(ts.transpileModule(fs.readFileSync(path.basename(file)==='EvaluationHistoryPanel.tsx'&&process.env.MV_HISTORY_PANEL_SOURCE?process.env.MV_HISTORY_PANEL_SOURCE:file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);return m.exports;}
 const component=load(path.join(__dirname,'EvaluationHistoryPanel.tsx'));
 function render(){global.localStorage=local;index=0;dirty=false;tree=component.EvaluationHistoryPanel(props);effects.splice(0).forEach(f=>f());}
 async function settle(){for(let i=0;i<20;i++){if(dirty)render();await new Promise(setImmediate);if(!dirty)return;}throw Error('Component did not settle');}
 const all=()=>nodes(tree),selector=()=>all().find(n=>n.type==='select'&&!n.props['aria-label']);
 return{local,compute,project,props,requests,catalogs,all,selector,render,settle,identity(value){identity=value;render();},async change(label,value){const n=label==='record'?selector():all().find(n=>n.props?.['aria-label']===label);assert(n,label);n.props.onChange({target:{value}});await settle();},async refresh(){const n=all().find(n=>n.props?.['aria-label']==='평가 이력 새로고침');assert(n);n.props.onClick();await settle();},async open(){all().find(n=>n.type==='details').props.onToggle({currentTarget:{open:true}});await settle();}};
}

// Independent invalid fixture values; the saved JSON stays inspectable and exact.
const invalidControls=[
 {name:'saved negative TP refuses derived class metrics without replacing raw evidence',key:'tp',value:-1,message:'저장된 클래스별 TP·FN·FP 지표가 유효하지 않아 클래스 지표와 필터를 표시할 수 없습니다.'},
 {name:'saved fractional FN refuses class error filters without coercion',key:'fn',value:0.5,message:'저장된 클래스별 TP·FN·FP 지표가 유효하지 않아 클래스 지표와 필터를 표시할 수 없습니다.'},
 {name:'saved numeric-string score refuses score-bin filters without coercion',key:'defect_score',value:'0.65',message:'저장된 평가 점수가 유효하지 않아 클래스 지표와 필터를 표시할 수 없습니다.'},
];
for(const control of invalidControls)test(control.name,async()=>{
 const good=record('valid-saved-record'),bad=record('invalid-saved-record');
 const sample={file_path:'/source-one/case-0.png',file_name:'case-0.png',defect_score:0.65,object_evidence:{per_class:{Scratch:{tp:1,fn:0,fp:0}}}};
 good.result.test_predictions=[JSON.parse(JSON.stringify(sample))];bad.result.test_predictions=[JSON.parse(JSON.stringify(sample))];
 if(control.key==='defect_score')bad.result.test_predictions[0].defect_score=control.value;
 else bad.result.test_predictions[0].object_evidence.per_class.Scratch[control.key]=control.value;
 const legacy=record('nullable-legacy-record');legacy.result.test_predictions=[{file_path:'/source-one/absence.png'},{file_path:'/source-one/nullable.png',defect_score:null,confidence:null,class_scores:{Scratch:null},pixel_evidence:{per_class:{Scratch:{tp:null,fn:null}}}},{file_path:'/source-one/distance.png',defect_score:4}];
 const original=JSON.parse(JSON.stringify(bad)),legacyOriginal=JSON.parse(JSON.stringify(legacy)),f=fixture({records:[good,bad,legacy]});await f.settle();
 assert(f.all().some(n=>n.type?.name==='EvaluationEvidencePanel'),'valid saved evidence control must remain available');
 await f.change('record',legacy.evaluation_id);assert(!f.all().some(n=>n.props?.role==='alert'),'absent, nullable and finite distance measurements must retain saved display admission');
 assert.deepEqual(f.all().find(n=>n.type?.name==='EvaluationEvidencePanel').props.samples,legacy.result.test_predictions,'legacy unavailable values must not be repaired into zero or coerced');assert.deepEqual(legacy,legacyOriginal);
 await f.change('record',bad.evaluation_id);
 const alerts=f.all().filter(n=>n.props?.role==='alert');
 assert(alerts.some(n=>text(n)===control.message),'invalid saved evidence must refuse dependent metrics and filters');
 assert(!f.all().some(n=>n.type?.name==='EvaluationEvidencePanel'),'invalid saved evidence cannot show stale derived controls');
 assert(f.all().some(n=>n.type==='pre'&&JSON.stringify(JSON.parse(text(n)))===JSON.stringify(bad.result)),'original invalid saved JSON must remain inspectable');
 assert.deepEqual(bad,original,'validation must not repair or rewrite immutable evidence');
 await f.change('record',good.evaluation_id);
 assert(!f.all().some(n=>n.props?.role==='alert'));assert(f.all().some(n=>n.type?.name==='EvaluationEvidencePanel'),'explicit valid record selection must restore derived controls');
 assert(f.requests.every(r=>!r.options),'invalid saved display must not issue reevaluation or business writes');
});
