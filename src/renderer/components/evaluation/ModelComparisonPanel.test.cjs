const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const nodes=t=>Array.isArray(t)?t.flatMap(nodes):t&&typeof t==='object'?[t,...nodes(t.props?.children)]:[];
global.localStorage={getItem:()=>null,setItem(){},removeItem(){}};
function fixture(config={}){
 let index=0,tree,dirty=true;const slots=[],effects=[],calls=[],urls=[];let pending=null,resolveCreate;
 const compute={selectedProfileId:'server',transportRevision:1,profiles:[{id:'server',name:'GPU2',gpu_selector:'2'}]};
 const hook=object=>Object.assign(selector=>selector?selector(object):object,{getState:()=>object});
 const react={useState(value){const i=index++;if(!(i in slots))slots[i]=value;return[slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v;dirty=true;}];},useRef(value){const i=index++;return slots[i]??(slots[i]={current:value});},useEffect(fn,deps){const i=index++;if(JSON.stringify(slots[i]?.deps)!==JSON.stringify(deps)){const previous=slots[i];slots[i]={deps};effects.push(()=>{previous?.cleanup?.();slots[i].cleanup=fn();});}}};
 const file=path.join(__dirname,'ModelComparisonPanel.tsx'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const req=m.require.bind(m);
 const models=[{job_id:'incumbent',task:'classification'},{job_id:'candidate',task:'classification'}];
 m.require=n=>n==='react'?react:n.endsWith('/services/datasetWorkflow')?{datasetWorkflow:{}}:n.endsWith('/services/teamDataApi')?{teamDataApi:{}}:n.endsWith('/useAnnotationStore')?{useAnnotationStore:hook({reviewerName:'kai'})}:n.endsWith('/evidenceLabeling')?{openEvidenceLabeling:async()=>{throw new Error('Unexpected edit action');},rememberEvidenceEdit(){}}:n.endsWith('/EvidenceImageViewer')?{EvidenceImageViewer:()=>null}:n.includes('useComputeStore')?{useComputeStore:hook(compute)}:n.includes('useProjectStore')?{useProjectStore:hook({projectDir:'/project',project:{id:'p',source_dataset_dir:'/source',active_labelset_id:'default'}})}:n.endsWith('/useTaskHandoff')?{useTaskHandoff:()=>config.handoff||null}:n.includes('productDataWorkflow')?{consumeReviewContext:(_storage,_scope,ids)=>{const value=config.origin;config.origin=null;return value&&ids.includes(value.comparison_id)?value:null;},evaluationOriginScope:()=>null}:n.endsWith('/services/api')?{getApiPersistenceIdentity:()=> 'local',api:{evaluation:{comparisonModels:async()=>({models:config.models||models}),listComparisons:async()=>({comparisons:config.report?[config.report]:[]}),getComparison:async id=>config.getReport?config.getReport(id):config.report}},request:async(url,options)=>{urls.push(url);if(options){calls.push(JSON.parse(options.body));if(pending)return new Promise(resolve=>{resolveCreate=resolve;});return config.created||{job_id:'controlled',status:'completed'};}if(url.includes('/export?'))return config.export?config.export():{report:config.report,saved_report_sha256:'a'.repeat(64)};if(url.includes('/jobs/'))return config.polled||config.created||{job_id:'controlled',status:'completed',completed_images:0,total_images:0,report_id:null,error:null};return{jobs:config.jobs||[]};}}:req(n);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
 function render(){index=0;dirty=false;tree=m.exports.ModelComparisonPanel({projectDir:'/project',sourceFolder:'/source',task:'classification',preferredJobId:'candidate',preferredParentJobId:'incumbent',language:'ko',...config.props});effects.splice(0).forEach(f=>f());}
 async function settle(){for(let i=0;i<8;i++){if(dirty)render();await new Promise(setImmediate);if(!dirty)return;}}
 return{compute,calls,urls,settle,render,defer(){pending=true;},resolve(){resolveCreate({job_id:'stale-request',status:'completed'});},all:()=>nodes(tree),change:async(label,value)=>{const n=nodes(tree).find(n=>n.props?.['aria-label']===label);assert(n,label);n.props.onChange({target:{value}});await settle();},run:async()=>{const n=nodes(tree).find(n=>n.type==='button'&&nodes(n).some(child=>[child.props?.children].flat().includes('동일 test 이미지로 비교')));assert(n);await n.props.onClick();await settle();}};
}
test('production comparison submits explicitly selected server/device',async()=>{const f=fixture();await f.settle();await f.change('비교 실행 장치','cpu');await f.run();assert.equal(f.calls[0].execution_target,'selected_compute');assert.equal(f.calls[0].compute_profile_id,'server');assert.equal(f.calls[0].device,'cpu');});
test('production comparison with local selection labels and submits host CPU',async()=>{const f=fixture();f.compute.selectedProfileId=null;await f.settle();await f.run();assert.equal(f.calls[0].execution_target,'local_cpu');assert.equal(f.calls[0].device,'cpu');assert.equal(f.calls[0].compute_profile_id,null);assert(f.all().some(n=>String(n.props?.children).includes('로컬 CPU')));});
test('unavailable selected profile disables submission instead of falling back',async()=>{const f=fixture();f.compute.profiles=[];await f.settle();const n=f.all().find(n=>n.type==='button'&&nodes(n).some(child=>[child.props?.children].flat().includes('동일 test 이미지로 비교')));assert.equal(n.props.disabled,true);});

test('same profile ID configuration change discards stale production submission response',async()=>{const f=fixture();await f.settle();f.defer();const running=f.run();await new Promise(setImmediate);f.compute.profiles[0].gpu_selector='3';f.render();await f.settle();f.resolve();await running;assert(!f.all().some(n=>String(n.props?.children).includes('stale-request')));});

test('polling completion updates the same job in the reopen selector',async()=>{const f=fixture({created:{job_id:'controlled',status:'running',completed_images:0,total_images:8},polled:{job_id:'controlled',status:'completed',completed_images:8,total_images:8}});await f.settle();await f.run();const row=f.all().find(n=>n.type==='option'&&n.props.value==='controlled');assert(row);assert(row.props.children.includes('completed'));assert(row.props.children.includes(8));assert(!row.props.children.includes('running'));});

function savedReport(){
 const metric={counts:{tp:0,tn:1,fp:0,fn:0},accuracy:1,precision_ng:null,recall_ng:null,miss_rate:null,overkill_rate:0};
 return{comparison_id:'comparison_'+'c'.repeat(32),created_at:'2026-10-04T16:00:00Z',selected_image_count:1,total_test_images:1,incumbent_job_id:'incumbent',candidate_job_id:'candidate',images:[],limitations:[],model_sha256:{incumbent:'a',candidate:'b'},summary:{binary_metrics:{scope:'shared_known_truth_binary_verdicts',selected_images:1,evaluated_images:1,excluded:{unknown_truth:0,review:0,error:0},incumbent:metric,candidate:metric}}};
}
function exportButton(f){return f.all().find(n=>n.type==='button'&&n.props.children==='비교 근거 JSON 저장');}
function downloads(){
 const prior=global.document;const saved=[];
 global.document={createElement:()=>({href:'',download:'',click(){saved.push({href:this.href,filename:this.download});}})};
 return{saved,restore(){global.document=prior;}};
}
test('saved binary metric view keeps absent-class rates unavailable',async()=>{const f=fixture({report:savedReport()});await f.settle();await f.change('저장된 모델 비교',savedReport().comparison_id);assert(f.all().some(n=>n.type==='table'&&n.props['aria-label']==='공통 표본 판정 지표'));assert(f.all().some(n=>n.type==='td'&&n.props.children==='산출 불가'));});
test('production JSON download re-reads the scoped export and retains its report identity',async()=>{let requests=0;const report=savedReport();const f=fixture({report,export:()=>{requests++;return{report,saved_report_sha256:'a'.repeat(64)};}});const d=downloads();try{await f.settle();await f.change('저장된 모델 비교',report.comparison_id);const button=exportButton(f);assert(button);assert.equal(button.props.disabled,false);button.props.onClick();await f.settle();assert.equal(requests,1);assert.equal(d.saved.length,1);assert.equal(d.saved[0].filename,report.comparison_id+'-evidence.json');}finally{d.restore();}});
test('a target change while export is pending discards the old-context download',async()=>{let resolve;const report=savedReport();const f=fixture({report,export:()=>new Promise(r=>{resolve=r;})});const d=downloads();try{await f.settle();await f.change('저장된 모델 비교',report.comparison_id);const button=exportButton(f);assert(button);button.props.onClick();await f.settle();assert.equal(exportButton(f).props.disabled,true);f.compute.profiles[0].gpu_selector='3';f.render();await f.settle();resolve({report});await f.settle();assert.equal(d.saved.length,0);assert(!exportButton(f));}finally{d.restore();}});

function compareJob(id,status='completed',report_id=null){return{job_id:id,status,total_images:8,completed_images:status==='running'?2:8,cancel_requested:0,report_id,error:null,result_available:!!report_id,payload:{execution_target:'local_cpu',compute_profile_id:null,device:'cpu'}};}
const textOf=n=>[n?.props?.children].flat(Infinity).map(x=>typeof x==='object'?textOf(x):x??'').join('');
test('task-center completed comparison handoff reads the exact job and its verified report',async()=>{
 const report=savedReport();const chosen=compareJob('compare-chosen','completed',report.comparison_id);
 const f=fixture({report,jobs:[compareJob('compare-other','running'),chosen],polled:chosen,handoff:{kind:'model_comparison',jobId:'compare-chosen',executionJobId:'compare-chosen',comparisonTask:'classification',selectionId:'selection-1'}});
 await f.settle();assert(f.urls.some(url=>url.includes('/jobs/compare-chosen?')));
 assert(!f.urls.some(url=>url.includes('/jobs/compare-other?')));
 const selector=f.all().find(n=>n.props?.['aria-label']==='비교 작업 다시 열기');assert(selector);assert.equal(selector.props.value,'compare-chosen');
 assert(f.all().some(n=>n.props?.['aria-label']==='공통 표본 판정 지표'));assert.equal(f.calls.length,0,'opening never dispatches a new comparison');
});
test('missing handed comparison remains an explicit refusal instead of selecting another running job',async()=>{
 const f=fixture({jobs:[compareJob('compare-other','running')],handoff:{kind:'model_comparison',jobId:'compare-missing',comparisonTask:'classification',selectionId:'selection-2'}});
 await f.settle();assert(f.all().some(n=>n.props?.role==='alert'&&textOf(n).includes('compare-missing')));
 assert(!f.urls.some(url=>url.includes('/jobs/compare-other?')));assert.equal(f.calls.length,0);
});

test('manual comparison choice is restored after remount without dispatch',async()=>{
 const prior=global.localStorage;const values=new Map();global.localStorage={getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
 try{const chosen=compareJob('compare-two');const jobs=[compareJob('compare-one'),chosen];
 const first=fixture({jobs,polled:chosen});await first.settle();await first.change('비교 작업 다시 열기','compare-two');
 const reopened=fixture({jobs,polled:chosen});await reopened.settle();
 assert(reopened.urls.some(url=>url.includes('/jobs/compare-two?')));assert.equal(reopened.calls.length,0);
 assert.equal(reopened.all().find(n=>n.props?.['aria-label']==='비교 작업 다시 열기').props.value,'compare-two');
 }finally{global.localStorage=prior;}
});
test('a mismatched comparison response cannot replace the selected job or publish its report',async()=>{
 const prior=global.window;global.window={setTimeout:()=>0,clearTimeout(){}};
 try{const chosen=compareJob('compare-chosen');const f=fixture({jobs:[chosen],polled:compareJob('compare-foreign','completed',savedReport().comparison_id),report:savedReport(),handoff:{kind:'model_comparison',jobId:chosen.job_id,comparisonTask:'classification',selectionId:'mismatch'}});
 await f.settle();assert(f.all().some(n=>n.props?.role==='alert'&&textOf(n).includes('일치하지')));
 assert(!f.all().some(n=>n.props?.['aria-label']==='공통 표본 판정 지표'));
 assert.equal(f.all().find(n=>n.props?.['aria-label']==='비교 작업 다시 열기').props.value,'compare-chosen');
 }finally{global.window=prior;}
});

test('a delayed saved report cannot overwrite a newer task-center comparison handoff',async()=>{
 const old=savedReport(),next={...savedReport(),comparison_id:'comparison_'+'d'.repeat(32)};let resolveOld;
 const chosen=compareJob('compare-new','completed',next.comparison_id);
 const config={report:old,jobs:[chosen],polled:chosen,getReport:id=>id===old.comparison_id?new Promise(r=>{resolveOld=r;}):Promise.resolve(next)};
 const f=fixture(config);await f.settle();await f.change('저장된 모델 비교',old.comparison_id);
 config.handoff={kind:'model_comparison',jobId:chosen.job_id,comparisonTask:'classification',selectionId:'new-handoff'};f.render();await f.settle();
 resolveOld(old);await f.settle();
 assert.equal(f.all().find(n=>n.props?.['aria-label']==='저장된 모델 비교').props.value,next.comparison_id);
 assert.equal(f.all().find(n=>n.props?.['aria-label']==='비교 작업 다시 열기').props.value,chosen.job_id);
});

function persistentSelection(){const prior=global.localStorage,values=new Map();global.localStorage={getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};return{values,restore(){global.localStorage=prior;}};}
test('manual saved report survives remount after the same persistent task-center handoff',async()=>{
 const storage=persistentSelection();try{
 const old=savedReport(),manual={...old,comparison_id:'comparison_'+'e'.repeat(32)},chosen=compareJob('compare-chosen','completed',old.comparison_id);
 const config={report:manual,jobs:[chosen],polled:chosen,getReport:id=>Promise.resolve(id===old.comparison_id?old:manual),handoff:{kind:'model_comparison',jobId:chosen.job_id,comparisonTask:'classification',selectionId:'same-handoff'}};
 const first=fixture(config);await first.settle();await first.change('저장된 모델 비교',manual.comparison_id);
 const second=fixture(config);await second.settle();
 assert.equal(second.all().find(n=>n.props?.['aria-label']==='저장된 모델 비교').props.value,manual.comparison_id);
 assert.equal(second.all().find(n=>n.props?.['aria-label']==='비교 작업 다시 열기').props.value,'');
 assert(!second.urls.some(url=>url.includes('/jobs/compare-chosen?')));assert.equal(second.calls.length,0);
 }finally{storage.restore();}
});
test('a new explicit task-center selection supersedes the remembered manual report',async()=>{
 const storage=persistentSelection();try{const manual=savedReport(),newReport={...manual,comparison_id:'comparison_'+'f'.repeat(32)},chosen=compareJob('compare-new','completed',newReport.comparison_id);
 const first=fixture({report:manual});await first.settle();await first.change('저장된 모델 비교',manual.comparison_id);
 const reopened=fixture({report:newReport,jobs:[chosen],polled:chosen,handoff:{kind:'model_comparison',jobId:chosen.job_id,comparisonTask:'classification',selectionId:'new-selection'}});await reopened.settle();
 assert.equal(reopened.all().find(n=>n.props?.['aria-label']==='저장된 모델 비교').props.value,newReport.comparison_id);
 assert.equal(reopened.all().find(n=>n.props?.['aria-label']==='비교 작업 다시 열기').props.value,chosen.job_id);assert.equal(reopened.calls.length,0);
 }finally{storage.restore();}
});
test('missing remembered report refuses instead of falling back to a running comparison',async()=>{
 const storage=persistentSelection(),priorWindow=global.window;global.window={setTimeout:()=>0,clearTimeout(){}};try{
 const manual=savedReport(),first=fixture({report:manual});first.compute.selectedProfileId=null;await first.settle();await first.change('저장된 모델 비교',manual.comparison_id);
 const second=fixture({jobs:[compareJob('compare-running','running')]});second.compute.selectedProfileId=null;await second.settle();
 assert(second.all().some(n=>n.props?.role==='alert'&&textOf(n).includes(manual.comparison_id)));
 assert(!second.urls.some(url=>url.includes('/jobs/compare-running?')));assert.equal(second.calls.length,0);
 }finally{storage.restore();global.window=priorWindow;}
});
test('manual job survives remount after the same persistent task-center handoff',async()=>{
 const storage=persistentSelection();try{const old=compareJob('compare-old'),manual=compareJob('compare-manual');
 const config={jobs:[old,manual],polled:old,handoff:{kind:'model_comparison',jobId:old.job_id,comparisonTask:'classification',selectionId:'same-selection'}};
 const first=fixture(config);await first.settle();config.polled=manual;await first.change('비교 작업 다시 열기',manual.job_id);
 const second=fixture(config);await second.settle();assert.equal(second.all().find(n=>n.props?.['aria-label']==='비교 작업 다시 열기').props.value,manual.job_id);assert.equal(second.calls.length,0);
 }finally{storage.restore();}
});
test('a refused report identity cannot replace the last valid remembered choice',async()=>{
 const storage=persistentSelection();try{const firstReport=savedReport(),wrong={...firstReport,comparison_id:'comparison_'+'0'.repeat(32)};
 const config={report:firstReport,getReport:()=>Promise.resolve(firstReport)};const first=fixture(config);await first.settle();await first.change('저장된 모델 비교',firstReport.comparison_id);
 config.getReport=()=>Promise.resolve(wrong);await first.change('저장된 모델 비교','comparison_'+'1'.repeat(32));
 assert(first.all().some(n=>n.props?.role==='alert'&&textOf(n).includes('일치하지')));
 const second=fixture({report:firstReport});await second.settle();assert.equal(second.all().find(n=>n.props?.['aria-label']==='저장된 모델 비교').props.value,firstReport.comparison_id);assert.equal(second.calls.length,0);
 }finally{storage.restore();}
});
test('explicit review-queue return supersedes an old remembered job and persists its report',async()=>{
 const storage=persistentSelection();try{const report=savedReport(),job=compareJob('compare-old'),handoff={kind:'model_comparison',jobId:job.job_id,comparisonTask:'classification',selectionId:'old-selection'};
 const first=fixture({jobs:[job],polled:job,handoff});await first.settle();
 const second=fixture({report,jobs:[job],polled:job,handoff,origin:{comparison_id:report.comparison_id,file_path:'/source/a.png',image_id:'a'}});await second.settle();
 assert.equal(second.all().find(n=>n.props?.['aria-label']==='저장된 모델 비교').props.value,report.comparison_id);
 assert.equal(second.all().find(n=>n.props?.['aria-label']==='비교 작업 다시 열기').props.value,'');assert.equal(second.calls.length,0);
 const reopened=fixture({report,jobs:[job],polled:job,handoff});await reopened.settle();
 assert.equal(reopened.all().find(n=>n.props?.['aria-label']==='저장된 모델 비교').props.value,report.comparison_id);
 }finally{storage.restore();}
});
test('a fresh task-center handoff discards an older pending review return',async()=>{
 const storage=persistentSelection();try{const report=savedReport(),old=compareJob('compare-old'),fresh=compareJob('compare-fresh','completed',report.comparison_id);
 const first=fixture({jobs:[old],polled:old,handoff:{kind:'model_comparison',jobId:old.job_id,comparisonTask:'classification',selectionId:'old-selection'}});await first.settle();
 const config={report,jobs:[fresh],polled:fresh,handoff:{kind:'model_comparison',jobId:fresh.job_id,comparisonTask:'classification',selectionId:'fresh-selection'},origin:{comparison_id:report.comparison_id,file_path:'/source/old.png'}};
 const second=fixture(config);await second.settle();assert.equal(second.all().find(n=>n.props?.['aria-label']==='비교 작업 다시 열기').props.value,fresh.job_id);
 assert.equal(config.origin,null);assert.equal(second.calls.length,0);
 }finally{storage.restore();}
});


test('late preferred-model updates cannot reverse manually selected baseline and candidate',async()=>{
 const config={props:{preferredJobId:'incumbent',preferredParentJobId:null}};const f=fixture(config);await f.settle();
 await f.change('비교 기준 모델','incumbent');await f.change('후보 모델','candidate');
 config.props.preferredJobId='candidate';config.props.preferredParentJobId='incumbent';f.render();await f.settle();
 config.props.preferredParentJobId=null;f.render();await f.settle();await f.run();
 assert.equal(f.calls[0].incumbent_job_id,'incumbent');assert.equal(f.calls[0].candidate_job_id,'candidate');
});
test('an explicitly cleared manual candidate stays empty after a recommendation update',async()=>{
 const config={props:{preferredJobId:'candidate'}};const f=fixture(config);await f.settle();await f.change('후보 모델','');
 config.props.preferredJobId='incumbent';f.render();await f.settle();
 assert.equal(f.all().find(n=>n.props?.['aria-label']==='후보 모델').props.value,'');
});

test('manual model choices cannot leak to a different data source',async()=>{
 const config={props:{sourceFolder:'/source'}};const f=fixture(config);await f.settle();await f.change('후보 모델','');
 config.props.sourceFolder='/other-source';f.render();await f.settle();assert.equal(f.all().find(n=>n.props?.['aria-label']==='후보 모델').props.value,'candidate');
});
test('a disappeared manual model refuses rather than substituting a recommendation',async()=>{
 const config={props:{preferredJobId:'candidate'}};const f=fixture(config);await f.settle();await f.change('후보 모델','candidate');
 config.models=[{job_id:'incumbent',task:'classification'}];config.props.preferredJobId='incumbent';f.render();await f.settle();
 assert.equal(f.all().find(n=>n.props?.['aria-label']==='후보 모델').props.value,'');assert(f.all().some(n=>n.props?.role==='alert'&&textOf(n).includes('수동으로 선택한 비교 모델')));await f.run();assert.equal(f.calls.length,0);
});

test('late recommendation reload preserves the manually selected pair threshold',async()=>{
 const config={props:{preferredJobId:'candidate'}};const f=fixture(config);await f.settle();await f.change('비교 기준 모델','incumbent');await f.change('후보 모델','candidate');await f.change('비교 후보 임계값',.37);
 config.props.preferredJobId='incumbent';f.render();await f.settle();assert.equal(f.all().find(n=>n.props?.['aria-label']==='비교 후보 임계값').props.value,.37);
});
