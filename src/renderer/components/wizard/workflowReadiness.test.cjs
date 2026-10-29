const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(){const filename=path.join(__dirname,'workflowReadiness.ts');assert.ok(fs.existsSync(filename),'production readiness contract must exist');const m=new Module(filename,module);m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);return m.exports;}
function loadChecklist(){const filename=path.join(__dirname,'..','dataset','importChecklist.ts');const m=new Module(filename,module);m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);return m.exports;}
// The evaluation card's own range helper, so the footer's step-4 range is checked against the same numbers.
function loadSampleConfidence(){const filename=path.join(__dirname,'..','evaluation','sampleConfidence.ts');const m=new Module(filename,module);m.require=name=>{assert.equal(name,'../dataset/importChecklist');return loadChecklist();};m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);return m.exports;}
// Use the production verdict, controlling only unused module-level dependencies.
function productionVerdict(){
 const compile=(file,mocks)=>{const m=new Module(file,module);m.require=name=>mocks[name];m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;};
 const roles=compile(path.join(__dirname,'../../utils/classSemantics.ts'),{});
 return compile(path.join(__dirname,'../../stores/useEvaluationStore.ts'),{zustand:{create:()=>()=>{}},'../utils/classSemantics':roles,'../services/api':{},'./useProjectStore':{},'./useDatasetStore':{},'./useTrainingStore':{}}).computeSampleVerdict;
}
const computeSampleVerdict=productionVerdict();
const base={stage:1,projectReady:true,sourceReady:true,totalImages:12,train:8,val:4,busy:false,task:'classification',modelJobId:null,flowVersionId:null,flowDirty:false,evidence:{status:'ready',review:{ready:true,counts:{eligible:12}},impact:{models:[],model_evaluations:[],flows:[],flow_evaluations:[],approvals:[],packages:[]}}};
test('each stage has completed/missing inputs and exactly one recommended action',()=>{const {getNextAction}=load();for(let stage=1;stage<=6;stage++){const r=getNextAction({...base,stage,sourceReady:false,totalImages:0});assert.ok(r.missingInputs.length);assert.ok(Array.isArray(r.completedInputs));assert.equal(typeof r.nextAction.label,'string');assert.ok(r.nextAction.step>=1&&r.nextAction.step<=6);}});
test('a detection folder with no label yet counts as imported and sends the person to step 2, not back to the import',()=>{const {getNextAction}=load();
 const fresh={...base,task:'detection',totalImages:0,sourceImages:54,train:0,val:0};
 const one=getNextAction({...fresh,stage:1});assert.equal(one.status,'ready');assert.equal(one.nextAction.label,'라벨 확인하기 (2단계)');assert.ok(one.completedInputs.includes('현재 원본 이미지 54장'));
 assert.equal(getNextAction({...fresh,sourceImages:0,stage:1}).nextAction.label,'데이터 가져오기 (1단계)','an empty folder still asks for the import');});
test('a retained evaluation job does not count as a current model',()=>{const {getNextAction}=load();const r=getNextAction({...base,stage:4,modelJobId:'old'});assert.notEqual(r.status,'ready');assert.equal(r.nextAction.step,3);});
test('current model needs matched checkpoint and data evidence',()=>{const {getNextAction}=load();for(const state of ['changed','unverified','different_source'])assert.notEqual(getNextAction({...base,stage:4,modelJobId:'m',evidence:{...base.evidence,impact:{...base.evidence.impact,models:[{job_id:'m',task:'classification',checkpoint_state:'current',data_state:state,scope_matches:true}]}}}).status,'ready');});
test('empty reports never imply evaluated/approved/deployed readiness',()=>{const {getNextAction}=load();for(const stage of [4,5,6])assert.notEqual(getNextAction({...base,stage}).status,'ready');});
test('checking and failed evidence remain unknown with retry action',()=>{const {getNextAction}=load();for(const status of ['checking','error']){const r=getNextAction({...base,stage:4,evidence:{status,error:'offline'}});assert.equal(r.status,status==='checking'?'checking':'unknown');assert.equal(r.nextAction.kind,'refresh');}});
test('matching saved flow evidence is useful but never deployment authorization',()=>{const {getNextAction}=load();const r=getNextAction({...base,stage:6,flowVersionId:'v',evidence:{...base.evidence,impact:{...base.evidence.impact,flows:[{version_id:'v',state:'current',scope_matches:true}],approvals:[{revision_id:'r',state:'unverified'}],packages:[]}}});assert.notEqual(r.status,'ready');assert.ok(r.missingInputs.length);});
test('a verified active version cannot make a different displayed graph ready',()=>{const {getNextAction}=load();const r=getNextAction({...base,stage:5,flowVersionId:'active-a',flowMatchesSaved:false,evidence:{...base.evidence,impact:{...base.evidence.impact,flows:[{version_id:'active-a',state:'current',scope_matches:true}]}}});assert.notEqual(r.status,'ready');assert.equal(r.nextAction.step,5);});

async function hookFixture({wrongProject=false}={}){
 const filename=path.join(__dirname,'useWorkflowReadiness.ts'),m=new Module(filename,module),slots=[],effects=[],calls=[];let cursor=0;
 const context={workspace_id:'w',project_id:'p',actor_id:'a',mode:'local'};
 const project={project:{id:'p',source_dataset_dir:'/data',active_labelset_id:'default'},projectDir:'/p',task:'classification',activeStep:5,isProjectBusy:false};
 const data={folderPath:'/data',datasetKey:'/data\0classification',totalImages:10,split:{train:6,val:2,test:2},isLoading:false,isSplitting:false};
 const displayed={id:'graph',nodes:[{id:'rule',data:{threshold:0.8}}],edges:[]},saved={...displayed,nodes:[{id:'rule',data:{threshold:0.2}}]};
 const flow={pipeline:displayed,pipelineDirty:false,flowIdentity:{semantic_revision:0}};
 const store=value=>selector=>selector?selector(value):value;
 const mocks={react:{useEffect:fn=>effects.push(fn),useMemo:fn=>fn(),useState:initial=>{const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],v=>slots[i]=typeof v==='function'?v(slots[i]):v];},useRef:value=>{const i=cursor++;return slots[i]??(slots[i]={current:value});}},
 '../../services/api':{getProjectContext:()=>context,getApiPersistenceIdentity:()=>null,subscribeProjectContext:()=>()=>{},request:async endpoint=>endpoint.includes('team-data')?{ready:true,counts:{eligible:10}}:{project_id:wrongProject?'other':'p',source_dataset_path:'/data',labelset_id:'default',models:[],model_evaluations:[],flows:[{version_id:'a',scope_matches:true,state:'current'}],approvals:[],packages:[]},api:{flowchart:{listPipelines:async()=>({pipelines:[{version_id:'a',is_active:true}]}),getPipelineVersion:async id=>{calls.push(id);return saved;}}}},
 '../../stores/useProjectStore':{useProjectStore:store(project)},'../../stores/useDraftReviewStore':{useDraftReviewStore:store({scope:'',pendingImages:0}),draftReviewScope:()=>'scope'},'../../stores/assistContext':{publishAssistContext:()=>{}},'../../stores/workflowEvidence':{subscribeWorkflowEvidence:()=>()=>{},workflowEvidenceRevision:()=>0},'../../stores/useComputeStore':{useComputeStore:store({transportRevision:0})},'../../stores/useDatasetStore':{useDatasetStore:store(data)},'../../stores/useTrainingStore':{useTrainingStore:store({status:'idle'})},'../../stores/useEvaluationStore':{useEvaluationStore:store({jobId:null})},'../../stores/useAnnotationStore':{useAnnotationStore:store({isDirty:false,metadata:null})},'../../stores/useFlowchartStore':{useFlowchartStore:store(flow),flowSemanticKey:value=>JSON.stringify(value)},'./workflowReadiness':load(),'../dataset/importChecklist':loadChecklist(),'../evaluation/sampleConfidence':loadSampleConfidence(),'../../stores/workflowProgress':{publishWorkflowProgress:()=>{},inspectionProgressFor:()=>null,subscribeInspectionProgress:()=>()=>{}},'./stepProgress':{stepsFromEvidence:()=>[]}};
 m.require=name=>mocks[name];m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);
 m.exports.useWorkflowReadiness();effects.forEach(fn=>fn());await new Promise(resolve=>setImmediate(resolve));cursor=0;const result=m.exports.useWorkflowReadiness();return{...result,calls};
}
test('production readiness hook reads the active saved graph before claiming displayed rules match',async()=>{const result=await hookFixture();assert.deepEqual(result.calls,['a']);assert.equal(result.readiness.status,'blocked');assert.match(result.readiness.missingInputs.join(' '),/규칙이 다릅니다/);});
test('production readiness hook rejects evidence from a different project',async()=>{const result=await hookFixture({wrongProject:true});assert.equal(result.readiness.status,'unknown');assert.match(result.readiness.missingInputs.join(' '),/현재 입력과 다릅니다/);assert.deepEqual(result.calls,[]);});

// Unit controls for stage-scoped readiness; no model or deployment authorization.
function scopedHookFixture({stage=2}={}) {
 const filename=path.join(__dirname,'useWorkflowReadiness.ts'),m=new Module(filename,module);
 const slots=[],effectSlots=[],pendingEffects=[],requests=[],pipelineCalls=[],savedCalls=[],published=[],assists=[],pipelines=[];
 let cursor=0,effectCursor=0,contextListener=null,persistence=null,inspection=null,inspectionListener=null;
 let context={workspace_id:'w',project_id:'p',actor_id:'a',mode:'local'};
 const project={project:{id:'p',source_dataset_dir:'/data',active_labelset_id:'default'},projectDir:'/p',task:'classification',activeStep:stage,isProjectBusy:false};
 const data={folderPath:'/data',datasetKey:'/data\0classification',totalImages:12,split:{train:8,val:2,test:2},isLoading:false,isSplitting:false};
 const annotation={isDirty:false,metadata:{revision:1},labelbookVersion:1};
 const training={status:'idle',isCurrentData:false,jobId:null};
 const flow={pipeline:null,pipelineDirty:false,flowIdentity:{semantic_revision:0}};
 const evaluation={jobId:null,testPredictions:[]};
 const store=value=>selector=>selector?selector(value):value;
 const equalDeps=(a,b)=>Boolean(a&&b&&a.length===b.length&&a.every((value,index)=>Object.is(value,b[index])));
 const React={
  useState:initial=>{const index=cursor++;if(!(index in slots))slots[index]=typeof initial==='function'?initial():initial;return[slots[index],value=>{slots[index]=typeof value==='function'?value(slots[index]):value;}];},
  useRef:value=>{const index=cursor++;return slots[index]??(slots[index]={current:value});},
  useEffect:(fn,deps)=>{const index=effectCursor++;if(!equalDeps(effectSlots[index]?.deps,deps))pendingEffects.push({index,fn,deps});},
  useMemo:fn=>fn(),
 };
 const mocks={react:React,
  '../../services/api':{
   getProjectContext:()=>context,getApiPersistenceIdentity:()=>persistence,
   subscribeProjectContext:listener=>{contextListener=listener;return()=>{if(contextListener===listener)contextListener=null;};},
   request:endpoint=>new Promise((resolve,reject)=>requests.push({endpoint,resolve,reject,scope:JSON.stringify([context,persistence])})),
   api:{flowchart:{listPipelines:async folder=>{pipelineCalls.push(folder);return{pipelines:pipelines.slice()};},getPipelineVersion:async id=>{savedCalls.push(id);return null;}}},
  },
  '../../stores/useProjectStore':{useProjectStore:store(project)},
  '../../stores/useDraftReviewStore':{useDraftReviewStore:store({scope:'',pendingImages:0}),draftReviewScope:()=>'scope'},'../../stores/assistContext':{publishAssistContext:value=>assists.push(value)},
  '../../stores/useComputeStore':{useComputeStore:store({transportRevision:0})},
  '../../stores/useDatasetStore':{useDatasetStore:store(data)},
  '../../stores/useTrainingStore':{useTrainingStore:store(training)},
  '../../stores/useEvaluationStore':{useEvaluationStore:store(evaluation),computeSampleVerdict},
  '../../stores/useAnnotationStore':{useAnnotationStore:store(annotation)},
  '../../stores/useFlowchartStore':{useFlowchartStore:store(flow),flowSemanticKey:value=>JSON.stringify(value)},
  '../../stores/workflowEvidence':{subscribeWorkflowEvidence:()=>()=>{},workflowEvidenceRevision:()=>0},
  './workflowReadiness':load(),
  '../dataset/importChecklist':loadChecklist(),
  '../evaluation/sampleConfidence':loadSampleConfidence(),
  '../../stores/workflowProgress':{publishWorkflowProgress:(scope,steps)=>published.push({scope,steps}),
   inspectionProgressFor:scope=>inspection?.scope===scope?inspection.counts:null,subscribeInspectionProgress:listener=>{inspectionListener=listener;return()=>{if(inspectionListener===listener)inspectionListener=null;};}},
  './stepProgress':{stepsFromEvidence:(impact,task)=>impact.models.length?[3]:[]},
 };
 m.require=name=>{assert.ok(Object.hasOwn(mocks,name),`unexpected production dependency ${name}`);return mocks[name];};
 m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);
 const render=()=>{cursor=0;effectCursor=0;return m.exports.useWorkflowReadiness();};
 const runEffects=()=>{for(const {index,fn,deps} of pendingEffects.splice(0)){effectSlots[index]?.cleanup?.();effectSlots[index]={deps,cleanup:fn()};}};
 const mount=()=>{render();runEffects();return render();};
 const settle=async()=>{await new Promise(resolve=>setImmediate(resolve));return render();};
 const unmount=()=>{for(const row of effectSlots)row?.cleanup?.();};
 const impact=(overrides={})=>({project_id:'p',source_dataset_path:'/data',labelset_id:'default',models:[],model_evaluations:[],flows:[],flow_evaluations:[],approvals:[],packages:[],...overrides});
 return{requests,pipelineCalls,savedCalls,published,assists,pipelines,project,data,annotation,training,flow,evaluation,render,runEffects,mount,settle,unmount,impact,
  setContext:value=>{context=value;contextListener?.(value);},
  publishInspection:(scope,counts)=>{inspection=counts?{scope,counts}:null;inspectionListener?.();},
  rebindApiContext:value=>{context=value;},setPersistence:value=>{persistence=value;},
 };
}

test('stage two uses fresh review evidence without impact and preserves exact review blockers',()=>{
 const {getNextAction}=load();
 const ready=getNextAction({...base,stage:2,evidence:{status:'ready',review:{ready:true,counts:{eligible:9}}}});
 assert.equal(ready.status,'ready');assert.equal(ready.nextAction.step,3);assert.equal(ready.nextAction.target,'workflow-training');
 assert.ok(ready.completedInputs.includes('검수 기준을 통과한 이미지 9장'));
 const blocked=getNextAction({...base,stage:2,evidence:{status:'ready',review:{ready:false,counts:{eligible:0},blockers:['현재 원본의 검수 대기']}}});
 assert.equal(blocked.status,'blocked');assert.deepEqual(blocked.missingInputs,['현재 원본의 검수 대기']);assert.equal(blocked.nextAction.target,'workflow-label-review');
});

test('stage two still refuses missing or failed review, source mismatch, busy state and missing project',()=>{
 const {getNextAction}=load();
 for(const evidence of [{status:'ready'},{status:'checking'},{status:'error',error:'readiness offline'}]){
  const result=getNextAction({...base,stage:2,evidence});assert.notEqual(result.status,'ready');assert.equal(result.nextAction.kind,'refresh');
 }
 const evidence={status:'ready',review:{ready:true,counts:{eligible:12}}};
 for(const change of [{projectReady:false},{sourceReady:false},{totalImages:0},{busy:true}])assert.notEqual(getNextAction({...base,stage:2,evidence,...change}).status,'ready');
});

test('stages three through six continue to require impact evidence before their original advice',()=>{
 const {getNextAction}=load();
 for(const stage of [3,4,5,6]){
  const result=getNextAction({...base,stage,evidence:{status:'ready',review:{ready:true,counts:{eligible:12}}}});
  assert.equal(result.status,'unknown');assert.equal(result.nextAction.kind,'refresh');
 }
});

test('production stage two makes only a fresh readiness request and never invokes impact or saved-flow reads',async()=>{
 const fixture=scopedHookFixture();try{
  assert.equal(fixture.mount().readiness.status,'checking');
  assert.deepEqual(fixture.requests.map(row=>row.endpoint),['/api/team-data/readiness']);assert.deepEqual(fixture.pipelineCalls,[]);
  fixture.requests[0].resolve({ready:true,counts:{eligible:7}});const result=await fixture.settle();
  assert.equal(result.readiness.status,'ready');assert.equal(result.readiness.nextAction.step,3);
  assert.ok(result.readiness.completedInputs.includes('검수 기준을 통과한 이미지 7장'));
  assert.deepEqual(fixture.pipelineCalls,[]);assert.deepEqual(fixture.savedCalls,[]);
 }finally{fixture.unmount();}
});

test('production stage two retains real readiness errors and rejects malformed review evidence',async()=>{
 for(const response of [null,{ready:'yes',counts:{eligible:12}},{ready:true,counts:{}},{ready:true,counts:{eligible:NaN}}]){
  const fixture=scopedHookFixture();try{
   fixture.mount();fixture.requests[0].resolve(response);const result=await fixture.settle();
   assert.equal(result.readiness.status,'unknown');assert.equal(result.readiness.nextAction.kind,'refresh');assert.equal(fixture.requests.length,1);assert.deepEqual(fixture.pipelineCalls,[]);
  }finally{fixture.unmount();}
 }
 const fixture=scopedHookFixture();try{
  fixture.mount();fixture.requests[0].reject(new Error('own readiness request failed'));const result=await fixture.settle();
  assert.equal(result.readiness.status,'unknown');assert.deepEqual(result.readiness.missingInputs,['own readiness request failed']);assert.equal(result.readiness.nextAction.kind,'refresh');
 }finally{fixture.unmount();}
});

test('production stage two refresh reads new review evidence and cannot reuse the prior ready result',async()=>{
 const fixture=scopedHookFixture();try{
  fixture.mount();fixture.requests[0].resolve({ready:true,counts:{eligible:12}});const first=await fixture.settle();assert.equal(first.readiness.status,'ready');
  first.refresh();fixture.render();fixture.runEffects();assert.equal(fixture.render().readiness.status,'checking');
  assert.deepEqual(fixture.requests.map(row=>row.endpoint),['/api/team-data/readiness','/api/team-data/readiness']);
  fixture.requests[1].resolve({ready:false,counts:{eligible:0},blockers:['fresh review now pending']});const second=await fixture.settle();
  assert.equal(second.readiness.status,'blocked');assert.deepEqual(second.readiness.missingInputs,['fresh review now pending']);assert.deepEqual(fixture.pipelineCalls,[]);
 }finally{fixture.unmount();}
});

test('a stage two request cannot overwrite a later refresh receipt when it completes last',async()=>{
 const fixture=scopedHookFixture();try{
  const initial=fixture.mount();initial.refresh();fixture.render();fixture.runEffects();
  assert.equal(fixture.requests.length,2);fixture.requests[1].resolve({ready:false,counts:{eligible:0},blockers:['current generation blocked']});
  assert.equal((await fixture.settle()).readiness.status,'blocked');fixture.requests[0].resolve({ready:true,counts:{eligible:12}});
  const final=await fixture.settle();assert.equal(final.readiness.status,'blocked');assert.deepEqual(final.readiness.missingInputs,['current generation blocked']);
 }finally{fixture.unmount();}
});

test('stage two late responses cannot publish after a stage transition or effect cleanup',async()=>{
 const fixture=scopedHookFixture();try{
  fixture.mount();fixture.project.activeStep=1;fixture.render();fixture.runEffects();
  fixture.requests[0].resolve({ready:true,counts:{eligible:12}});const result=await fixture.settle();
  assert.equal(result.readiness.stage,1);assert.equal(result.readiness.nextAction.step,2);
  fixture.project.activeStep=2;fixture.render();fixture.runEffects();assert.equal(fixture.requests.length,2);
  assert.equal(fixture.render().readiness.status,'checking');fixture.unmount();fixture.requests[1].resolve({ready:true,counts:{eligible:12}});
  assert.equal((await fixture.settle()).readiness.status,'checking');
 }finally{fixture.unmount();}
});

test('stage two late responses cannot publish after API scope rebinding',async()=>{
 for(const change of [fixture=>fixture.rebindApiContext({workspace_id:'w',project_id:'p',actor_id:'other',mode:'local'}),fixture=>fixture.setPersistence({provider:'different'})]){
  const fixture=scopedHookFixture();try{
   fixture.mount();change(fixture);fixture.requests[0].resolve({ready:true,counts:{eligible:12}});
   assert.notEqual((await fixture.settle()).readiness.status,'ready');
  }finally{fixture.unmount();}
 }
});

test('stage two current project and source guards prevent requests for a foreign or missing source',()=>{
 for(const change of [fixture=>fixture.setContext({workspace_id:'w',project_id:'other',actor_id:'a',mode:'local'}),fixture=>{fixture.data.folderPath='/other';},fixture=>{fixture.project.projectDir=null;}]){
  const fixture=scopedHookFixture();try{change(fixture);fixture.mount();assert.deepEqual(fixture.requests,[]);assert.deepEqual(fixture.pipelineCalls,[]);assert.notEqual(fixture.render().readiness.status,'ready');}finally{fixture.unmount();}
 }
});

test('moving from stage two to three restores the original full impact query and scope validation',async()=>{
 const fixture=scopedHookFixture();try{
  fixture.mount();fixture.requests[0].resolve({ready:true,counts:{eligible:12}});assert.equal((await fixture.settle()).readiness.status,'ready');
  fixture.project.activeStep=3;fixture.render();fixture.runEffects();
  assert.deepEqual(fixture.requests.map(row=>row.endpoint),['/api/team-data/readiness','/api/team-data/readiness','/api/provenance/impact']);assert.deepEqual(fixture.pipelineCalls,['/data']);
  fixture.requests[1].resolve({ready:true,counts:{eligible:12}});fixture.requests[2].resolve(fixture.impact({project_id:'foreign'}));const result=await fixture.settle();
  assert.equal(result.readiness.status,'unknown');assert.match(result.readiness.missingInputs.join(' '),/현재 입력과 다릅니다/);
 }finally{fixture.unmount();}
});

test('stages three through six keep their production impact request and malformed scope refusal',async()=>{
 for(const stage of [3,4,5,6]){
  const fixture=scopedHookFixture({stage});try{
   fixture.mount();assert.deepEqual(fixture.requests.map(row=>row.endpoint),['/api/team-data/readiness','/api/provenance/impact']);assert.deepEqual(fixture.pipelineCalls,['/data']);
   fixture.requests[0].resolve({ready:true,counts:{eligible:12}});fixture.requests[1].resolve(fixture.impact({source_dataset_path:'/other'}));
   const result=await fixture.settle();assert.equal(result.readiness.status,'unknown');assert.match(result.readiness.missingInputs.join(' '),/현재 입력과 다릅니다/);
  }finally{fixture.unmount();}
 }
});

test('a footer jump opens every closed section around its target',()=>{
 const {openFoldsAround}=load();
 const outer={tagName:'DETAILS',open:false,parentElement:null};
 const middle={tagName:'DIV',parentElement:outer};
 const inner={tagName:'DETAILS',open:false,parentElement:middle};
 const target={tagName:'SECTION',parentElement:inner};
 openFoldsAround(target);
 assert.equal(inner.open,true);assert.equal(outer.open,true);
});

test('a model evaluation that lands re-reads the step 4 evidence',async()=>{
 const fixture=scopedHookFixture({stage:4});
 fixture.mount();
 const impactReads=()=>fixture.requests.filter(row=>row.endpoint==='/api/provenance/impact').length;
 assert.equal(impactReads(),1);
 fixture.evaluation.jobId='m';fixture.evaluation.testPredictions=[{file_path:'a.png'}];
 fixture.render();fixture.runEffects();
 assert.equal(impactReads(),2,'the arrived evaluation triggers a fresh impact read');
 fixture.unmount();
});

test('a ready impact report shares its proven steps with the step bar for this project scope',async()=>{
 const fixture=scopedHookFixture({stage:4});
 fixture.mount();
 for(const row of fixture.requests.splice(0)){row.resolve(row.endpoint.includes('team-data')?{ready:true,counts:{eligible:12}}:fixture.impact({models:[{job_id:'m'}]}));}
 await fixture.settle();
 assert.deepEqual(fixture.published,[{scope:JSON.stringify(['p','/data','default','classification']),steps:[3]}]);
 fixture.unmount();
});

test('stage three blocks an unsaved or stale classification split and sends the user to step 1 (F-02)',()=>{
 const {getNextAction}=load();
 for(const splitState of ['automatic','stale']){const r=getNextAction({...base,stage:3,splitState});assert.equal(r.status,'blocked');assert.equal(r.nextAction.step,1);assert.match(r.nextAction.label,/분할 저장/);}
 assert.notEqual(getNextAction({...base,stage:3,splitState:'saved'}).nextAction.label,'분할 저장하기 (1단계)');
});
test('a model or flow whose training data changed says so and opens what changed (F-09/A3)',()=>{
 const {getNextAction}=load();
 const model={job_id:'m',task:'classification',checkpoint_state:'current',data_state:'changed',scope_matches:true};
 const atFour=getNextAction({...base,stage:4,modelJobId:'m',evidence:{...base.evidence,impact:{...base.evidence.impact,models:[model]}}});
 assert.equal(atFour.status,'blocked');assert.equal(atFour.dataChangedJobId,'m');assert.equal(atFour.nextAction.kind,'details');
 assert.match(atFour.missingInputs[0],/모델 m을\(를\) 학습한 뒤 데이터가 바뀌었습니다/);
 const atFive=getNextAction({...base,stage:5,flowVersionId:'v',flowMatchesSaved:true,evidence:{...base.evidence,impact:{...base.evidence.impact,models:[model],
   flows:[{version_id:'v',state:'revalidation_required',scope_matches:true,model_job_ids:['m']}]}}});
 assert.equal(atFive.dataChangedJobId,'m');assert.match(atFive.missingInputs[0],/검사 흐름의 모델 m/);
 const changedCheckpoint=getNextAction({...base,stage:4,modelJobId:'m',evidence:{...base.evidence,impact:{...base.evidence.impact,models:[{...model,checkpoint_state:'changed'}]}}});
 assert.equal(changedCheckpoint.dataChangedJobId,undefined,'a changed model file is not a data change');assert.equal(changedCheckpoint.nextAction.step,3);
});
test('the footer opens the change details and step 1 takes the version request (F-09/A3)',()=>{
 const footer=fs.readFileSync(path.join(__dirname,'WizardFooter.tsx'),'utf8');
 assert.match(footer,/if\(readiness\.nextAction\.kind==='details'\)\{setExpanded\(true\);return;\}/);
 assert.match(footer,/<DataChangeNotice jobId=\{readiness\.dataChangedJobId\}\/>/);
 const studio=fs.readFileSync(path.join(__dirname,'..','dataset','DatasetStudio.tsx'),'utf8');
 assert.match(studio,/takeDataVersionRequest\(\)/);assert.match(studio,/highlightVersionId=\{highlightVersionId\}/);
});
test('with no model selected and none current, the newest model of the task explains its data change',()=>{
 const {getNextAction}=load();
 const older={job_id:'job_1_a',task:'classification',checkpoint_state:'unverified',data_state:'unverified',scope_matches:true};
 const newer={job_id:'job_2_b',task:'classification',checkpoint_state:'current',data_state:'changed',scope_matches:true};
 const r=getNextAction({...base,stage:4,modelJobId:null,evidence:{...base.evidence,impact:{...base.evidence.impact,models:[newer,older]}}});
 assert.equal(r.dataChangedJobId,'job_2_b');
 const withCurrent=getNextAction({...base,stage:4,modelJobId:null,evidence:{...base.evidence,impact:{...base.evidence.impact,models:[newer,{...older,job_id:'job_3_c',checkpoint_state:'current',data_state:'current'}]}}});
 assert.equal(withCurrent.dataChangedJobId,undefined,'a current model exists: the user picks it instead');
});
test('stage three blocks a one-class classification set and sends the user to step 1 (F-03)',()=>{
 const {getNextAction}=load();
 const one=getNextAction({...base,stage:3,splitState:'saved',populatedClasses:1});
 assert.equal(one.status,'blocked');assert.equal(one.nextAction.step,1);assert.match(one.missingInputs[0],/클래스가 2개 이상/);
 assert.notEqual(getNextAction({...base,stage:3,splitState:'saved',populatedClasses:2}).nextAction.step,1);
});
test('stage three blocks a class below the per-class minimum with the step-1 rule text (F-03)',()=>{
 const {getNextAction}=load(),{classStartBlocker}=loadChecklist();
 const classBlocker=classStartBlocker({OK:24,NG:1},null,{train:20,val:3,test:2});
 const short=getNextAction({...base,stage:3,splitState:'saved',populatedClasses:2,classBlocker});
 assert.equal(short.status,'blocked');assert.equal(short.nextAction.step,1);assert.match(short.missingInputs[0],/2장 이상.*NG 1장/);
 assert.equal(classStartBlocker({OK:24,NG:2},null,{train:20,val:4,test:2}),null);
 assert.notEqual(getNextAction({...base,stage:3,splitState:'saved',populatedClasses:2,classBlocker:null}).nextAction.step,1);
});

// Steps 3–6 carry one number line each, like step 2 (§5.1 direction 1, §5.3); labels and targets stay as pinned above.
test('step 3 counts the running job, says the model is ready after a run on the current data, and stays quiet otherwise',()=>{
 const {getNextAction,stepThreeProgress,progressLine}=load();
 const saved={...base,stage:3,splitState:'saved',populatedClasses:2};
 const running=getNextAction({...saved,trainingStatus:'running',training:{epoch:3,totalEpochs:10,etaSeconds:150,current:true}});
 assert.equal(running.status,'checking');assert.equal(running.nextAction.label,'학습 진행 상태 보기');
 assert.deepEqual(running.progress,{epoch:3,totalEpochs:10,etaSeconds:150,completed:false});
 assert.equal(progressLine(3,running.progress,true),' · 학습 3/10회 · 남은 시간 02:30');
 assert.equal(progressLine(3,{epoch:3,totalEpochs:10,etaSeconds:null,completed:false},true),' · 학습 3/10회','no clock until the job reports one');
 assert.equal(stepThreeProgress({trainingStatus:'queued',training:{epoch:0,totalEpochs:0,etaSeconds:null,current:true},evidence:base.evidence,task:'classification'}),null,'a queued job without an epoch total has nothing to count');
 assert.deepEqual(stepThreeProgress({trainingStatus:'running',training:{epoch:12,totalEpochs:10,etaSeconds:-5,current:true},evidence:base.evidence,task:'classification'}),{epoch:10,totalEpochs:10,etaSeconds:null,completed:false},'the counter never passes its total and a bad clock is left out');
 const done=getNextAction({...saved,trainingStatus:'completed',training:{epoch:10,totalEpochs:10,etaSeconds:null,current:true}});
 assert.equal(done.nextAction.label,'학습 결과 평가 (4단계)');
 assert.deepEqual(done.progress,{epoch:10,totalEpochs:10,etaSeconds:null,completed:true});
 assert.equal(progressLine(3,done.progress,true),' · 모델 준비됨');
 // A run that finished on older data is not a ready model; a current model in the impact report is.
 assert.equal(getNextAction({...saved,trainingStatus:'completed',training:{epoch:10,totalEpochs:10,etaSeconds:null,current:false}}).progress,undefined);
 const model={job_id:'m',task:'classification',checkpoint_state:'current',data_state:'current',scope_matches:true};
 const fromReport=getNextAction({...saved,trainingStatus:'idle',evidence:{...base.evidence,impact:{...base.evidence.impact,models:[model]}}});
 assert.deepEqual(fromReport.progress,{epoch:0,totalEpochs:0,etaSeconds:null,completed:true});
 assert.equal(getNextAction({...saved,trainingStatus:'idle',evidence:{...base.evidence,impact:{...base.evidence.impact,models:[{...model,data_state:'changed'}]}}}).progress,undefined);
 assert.equal(getNextAction({...saved,trainingStatus:'idle',evidence:{...base.evidence,impact:{...base.evidence.impact,models:[{...model,task:'detection'}]}}}).progress,undefined,'another task\'s model does not count');
 assert.equal(getNextAction({...saved,trainingStatus:'idle'}).progress,undefined);
 // The line stays beside a blocker, and the step-2 object keeps its own shape.
 const blocked=getNextAction({...saved,splitState:'stale',trainingStatus:'running',training:{epoch:1,totalEpochs:5,etaSeconds:null,current:true}});
 assert.equal(blocked.status,'blocked');assert.equal(blocked.nextAction.label,'분할 저장하기 (1단계)');assert.deepEqual(blocked.progress,{epoch:1,totalEpochs:5,etaSeconds:null,completed:false});
 assert.equal(getNextAction({...base,stage:2,trainingStatus:'running',training:{epoch:1,totalEpochs:5,etaSeconds:null,current:true}}).progress,undefined,'step 2 never shows the training counter');
 // Set before the busy and evidence checks: the end of a run re-reads the evidence, and the line goes on to '모델 준비됨' without blanking.
 const finished={trainingStatus:'completed',training:{epoch:10,totalEpochs:10,etaSeconds:null,current:true}};
 for(const evidence of [{status:'checking'},{status:'error',error:'offline'},{status:'ready'}]){
  const rereading=getNextAction({...saved,...finished,evidence});
  assert.equal(rereading.nextAction.kind,'refresh');assert.deepEqual(rereading.progress,{epoch:10,totalEpochs:10,etaSeconds:null,completed:true},`evidence ${evidence.status} keeps the line`);
 }
 const busy=getNextAction({...saved,busy:true,trainingStatus:'running',training:{epoch:4,totalEpochs:10,etaSeconds:60,current:true}});
 assert.equal(busy.status,'checking');assert.equal(busy.nextAction.label,'데이터 변경 상태 보기');assert.deepEqual(busy.progress,{epoch:4,totalEpochs:10,etaSeconds:60,completed:false});
 assert.equal(getNextAction({...saved,projectReady:false,trainingStatus:'running',training:{epoch:4,totalEpochs:10,etaSeconds:60,current:true}}).progress,undefined,'without a project nothing is counted');
 assert.equal(getNextAction({...base,stage:2,busy:true,...finished}).progress,undefined,'step 2 stays without it while busy');
 assert.equal(getNextAction({...base,stage:1,...finished}).progress,undefined,'step 1 never shows it');
});

test('step 4 shows the loaded evaluation as an accuracy range, missed defects and false alarms, and nothing before one exists',()=>{
 const {getNextAction,progressLine}=load(),{wilsonInterval,accuracyNote}=loadSampleConfidence();
 const model={job_id:'m',task:'classification',checkpoint_state:'current',data_state:'current',scope_matches:true};
 const withModel={...base,stage:4,modelJobId:'m',evidence:{...base.evidence,impact:{...base.evidence.impact,models:[model],model_evaluations:[{job_id:'m',state:'current',evaluation_id:'e1'}]}}};
 assert.equal(getNextAction(withModel).progress,undefined,'no evaluation loaded: no number line');
 assert.equal(getNextAction({...withModel,evaluation:{samples:0,escapes:0,overkills:0,accuracy:0}}).progress,undefined);
 // 45 of 50 right: the step-4 card's own 95% range, so the footer never reads as one exact number.
 const range=wilsonInterval(45,50),counts={samples:50,escapes:2,overkills:3,accuracy:90,low:Math.round(range.low*100),high:Math.round(range.high*100)};
 assert.deepEqual([counts.low,counts.high],[79,96]);assert.match(accuracyNote(45,50),/ 79~96%\)$/);
 const evaluated=getNextAction({...withModel,evaluation:counts});
 // 2 missed defects: the step is looked at before step 5 (5.3-4), the action opens the 미검 tab.
 assert.equal(evaluated.status,'blocked');assert.equal(evaluated.nextAction.label,'틀린 이미지 보기');
 assert.deepEqual([evaluated.nextAction.step,evaluated.nextAction.target],[4,'workflow-missed-defects']);
 assert.equal(evaluated.missingInputs[0],'모델이 시험 이미지의 불량을 놓쳤습니다');assert.match(evaluated.missingInputs[1],/라벨 수정.*다시 학습.*평가.*→ 버튼/);assert.doesNotMatch(evaluated.missingInputs[1],/점수를 낮추|임계값.*하향|추천 점수 적용/,'a label shown in the simple view only');
 assert.deepEqual(evaluated.progress,counts);
 // False alarms alone keep the ready path: the recommended score trades them for no misses, and the line still shows them.
 const alarms=getNextAction({...withModel,evaluation:{...counts,escapes:0}});
 assert.equal(alarms.status,'ready');assert.equal(alarms.nextAction.label,'검사 흐름 만들기 (5단계)');
 assert.match(progressLine(4,alarms.progress,true),/잘못 잡은 정상 3장/);
 assert.equal(progressLine(4,evaluated.progress,true),' · 정확도 90% (79~96% 사이일 수 있음) · 놓친 불량 2장 · 잘못 잡은 정상 3장','the card\'s "could be" wording, not a certain range');
 assert.equal(progressLine(4,evaluated.progress,false),' · 90% right (could be 79~96%) · 2 missed defects · 3 false alarms');
 assert.equal(progressLine(4,{...counts,low:null},true),' · 정확도 90% · 놓친 불량 2장 · 잘못 잡은 정상 3장','no range without both ends');
 assert.deepEqual(getNextAction({...withModel,evaluation:{...counts,high:undefined}}).progress,{...counts,low:null,high:null},'half a range is dropped');
 // No image has an answer to compare (every verdict REVIEW): the image count only, never a false 0%.
 const unscored=getNextAction({...withModel,evaluation:{samples:5,escapes:0,overkills:0,accuracy:null,low:null,high:null}});
 assert.deepEqual(unscored.progress,{samples:5,escapes:0,overkills:0,accuracy:null,low:null,high:null});
 assert.equal(progressLine(4,unscored.progress,true),' · 시험 5장 · 정답이 없어 정확도를 셀 수 없음');
 assert.equal(progressLine(4,unscored.progress,false),' · 5 test images · no answers to score against');
 assert.equal(getNextAction({...withModel,evaluation:{samples:5,escapes:0,overkills:0,accuracy:null,low:40,high:90}}).progress.low,null,'no range without an accuracy');
 // Not yet recorded as evidence: the counts still show beside the advice to evaluate, marked as not confirmed.
 const pending=getNextAction({...withModel,evidence:{...base.evidence,impact:{...base.evidence.impact,models:[model]}},evaluation:counts});
 assert.equal(pending.nextAction.label,'시험 이미지로 평가하기 (4단계)');assert.deepEqual(pending.progress,{...counts,unconfirmed:true});
 assert.equal(progressLine(4,pending.progress,true),' · 확인 전 결과: 정확도 90% (79~96% 사이일 수 있음) · 놓친 불량 2장 · 잘못 잡은 정상 3장');
 assert.equal(progressLine(4,pending.progress,false),' · unconfirmed: 90% right (could be 79~96%) · 2 missed defects · 3 false alarms');
 // The selected model is not confirmed current (and its data did not change): the same neutral mark.
 const unknownModel=getNextAction({...withModel,modelJobId:'other',evaluation:counts});
 assert.equal(unknownModel.nextAction.label,'학습 모델 확인 (3단계)');assert.deepEqual(unknownModel.progress,{...counts,unconfirmed:true});
 assert.equal(progressLine(4,{...unscored.progress,unconfirmed:true},true),' · 확인 전 결과: 시험 5장 · 정답이 없어 정확도를 셀 수 없음');
 // The model's data changed after training: the counts belong to the data before the change, and the line says so.
 const changed=getNextAction({...withModel,evidence:{...base.evidence,impact:{...base.evidence.impact,models:[{...model,data_state:'changed'}]}},evaluation:counts});
 assert.equal(changed.dataChangedJobId,'m');assert.equal(changed.nextAction.label,'바뀐 내용과 되돌리기');
 assert.deepEqual(changed.progress,{...counts,dataChanged:true},'the data-change notice keeps the counts, marked as from before the change');
 assert.equal(progressLine(4,changed.progress,true),' · 바뀌기 전 데이터 기준: 정확도 90% (79~96% 사이일 수 있음) · 놓친 불량 2장 · 잘못 잡은 정상 3장');
 assert.equal(progressLine(4,changed.progress,false),' · on the data before it changed: 90% right (could be 79~96%) · 2 missed defects · 3 false alarms');
 assert.equal(changed.progress.unconfirmed,undefined,'the data-change mark is the only qualifier');
 assert.equal(progressLine(4,{...unscored.progress,dataChanged:true},true),' · 바뀌기 전 데이터 기준: 시험 5장 · 정답이 없어 정확도를 셀 수 없음');
 assert.equal(evaluated.progress.dataChanged,undefined,'current counts carry no mark');assert.equal(evaluated.progress.unconfirmed,undefined);
});

test('step 5 names the saved version and whether its whole-flow evaluation is current; unsaved edits say so',()=>{
 const {getNextAction,progressLine}=load();
 assert.equal(getNextAction({...base,stage:5}).progress,undefined,'no saved version: the blocker already says so');
 const flows=[{version_id:'v1234567890',state:'current',scope_matches:true}];
 const evaluated=getNextAction({...base,stage:5,flowVersionId:'v1234567890',flowMatchesSaved:true,evidence:{...base.evidence,impact:{...base.evidence.impact,flows}}});
 assert.equal(evaluated.status,'ready');assert.equal(evaluated.nextAction.label,'검사 결과 확인 (6단계)');
 assert.deepEqual(evaluated.progress,{versionId:'v1234567890',evaluated:true,unsaved:false});
 assert.equal(progressLine(5,evaluated.progress,true),' · 저장 버전 v1234567 · 평가 완료');
 const unevaluated=getNextAction({...base,stage:5,flowVersionId:'v1234567890',flowMatchesSaved:true});
 assert.equal(unevaluated.nextAction.label,'검사 흐름 평가 확인 (5단계)');assert.deepEqual(unevaluated.progress,{versionId:'v1234567890',evaluated:false,unsaved:false});
 assert.equal(progressLine(5,unevaluated.progress,true),' · 저장 버전 v1234567 · 평가 전');
 const stale=getNextAction({...base,stage:5,flowVersionId:'v1234567890',flowMatchesSaved:true,evidence:{...base.evidence,impact:{...base.evidence.impact,flows:[{...flows[0],state:'revalidation_required'}]}}});
 assert.equal(stale.progress.evaluated,false,'an evaluation that needs redoing is not current');
 assert.equal(stale.progress.reevaluate,true);
 assert.equal(progressLine(5,stale.progress,true),' · 저장 버전 v1234567 · 평가 다시 필요','an evaluation to redo is not \'평가 전\'');
 assert.equal(progressLine(5,stale.progress,false),' · saved version v1234567 · re-evaluation needed');
 assert.equal(progressLine(5,{...stale.progress,unsaved:true},true),' · 저장 버전 v1234567 · 편집 중','unsaved edits still come first');
 const both=getNextAction({...base,stage:5,flowVersionId:'v1234567890',flowMatchesSaved:true,evidence:{...base.evidence,impact:{...base.evidence.impact,flows:[{...flows[0],state:'revalidation_required'},flows[0]]}}});
 assert.deepEqual(both.progress,{versionId:'v1234567890',evaluated:true,unsaved:false},'a current evaluation wins over an older one to redo');
 const otherScope=getNextAction({...base,stage:5,flowVersionId:'v1234567890',flowMatchesSaved:true,evidence:{...base.evidence,impact:{...base.evidence.impact,flows:[{...flows[0],state:'revalidation_required',scope_matches:false}]}}});
 assert.equal(progressLine(5,otherScope.progress,true),' · 저장 버전 v1234567 · 평가 전','another scope\'s row does not count');
 const editing=getNextAction({...base,stage:5,flowVersionId:'v1234567890',flowDirty:true,evidence:{...base.evidence,impact:{...base.evidence.impact,flows}}});
 assert.equal(editing.nextAction.label,'검사 흐름 저장·평가 (5단계)');assert.deepEqual(editing.progress,{versionId:'v1234567890',evaluated:true,unsaved:true});
 assert.equal(progressLine(5,editing.progress,true),' · 저장 버전 v1234567 · 편집 중');
 const differs=getNextAction({...base,stage:5,flowVersionId:'v1234567890',flowMatchesSaved:false,evidence:{...base.evidence,impact:{...base.evidence.impact,flows}}});
 assert.equal(differs.nextAction.label,'보이는 검사 흐름 저장·평가 (5단계)');assert.deepEqual(differs.progress,{versionId:'v1234567890',evaluated:true,unsaved:false});
});

const inspectedFlow={...base,stage:6,flowVersionId:'v',flowMatchesSaved:true,evidence:{...base.evidence,impact:{...base.evidence.impact,flows:[{version_id:'v',state:'current',scope_matches:true}]}}};
// Count totals cannot prove finish persistence, active execution, stopping or a saved error-free result.
for(const row of [
 {name:'no saved run',inspection:null,status:'unknown',label:/검사 시작/,reason:/검사를 실행/},
 {name:'counts without saved lifecycle',inspection:{inspected:12,total:12,ng:0},status:'unknown',label:/검사 상태/,reason:/완료 상태.*확인/},
 {name:'preparing a new run before any rows arrive',inspection:{inspected:0,total:0,ng:0,isRunning:true},status:'checking',label:/진행 상태/,reason:/진행 중/},
 {name:'unknown saved lifecycle',inspection:{inspected:12,total:12,ng:0,status:'unknown',isRunning:false},status:'unknown',label:/검사 상태/,reason:/완료 상태.*확인/},
 {name:'completed without runtime confirmation',inspection:{inspected:12,total:12,ng:0,status:'completed'},status:'unknown',label:/검사 상태/,reason:/완료 상태.*확인/},
 {name:'zero of twelve running',inspection:{inspected:0,total:12,ng:0,status:'running',isRunning:true},status:'checking',label:/진행 상태/,reason:/진행 중/},
 {name:'twelve of twelve still running',inspection:{inspected:12,total:12,ng:0,status:'running',isRunning:true},status:'checking',label:/진행 상태/,reason:/진행 중/},
 {name:'completed report while runtime still finalizing',inspection:{inspected:12,total:12,ng:0,status:'completed',isRunning:true},status:'checking',label:/진행 상태/,reason:/진행 중/},
 {name:'saved running record without active runtime',inspection:{inspected:12,total:12,ng:0,status:'running',isRunning:false},status:'unknown',label:/검사 상태/,reason:/완료 상태.*확인/},
 {name:'stopped run',inspection:{inspected:4,total:12,ng:0,status:'stopped',isRunning:false},status:'blocked',label:/중단.*검사/,reason:/중단/},
 {name:'completed with failed images',inspection:{inspected:12,total:12,ng:0,failed:2,status:'completed',isRunning:false},status:'blocked',label:/실패.*이미지/,reason:/실패.*2장/},
 {name:'clean saved completion',inspection:{inspected:12,total:12,ng:0,status:'completed',isRunning:false},status:'unknown',label:/승인·패키지·대상/,reason:/검사.*완료/},
])test('step 6 lifecycle guidance: '+row.name,()=>{
 const result=load().getNextAction({...inspectedFlow,inspection:row.inspection});
 assert.equal(result.status,row.status);assert.match(result.nextAction.label,row.label);assert.equal(result.nextAction.step,6);
 assert.match(result.missingInputs.join(' '),row.reason);
 if(row.inspection?.status!=='completed'||row.inspection?.isRunning!==false||row.inspection?.failed)assert.doesNotMatch(result.missingInputs.join(' '),/검사 결과까지 확인했습니다/);
 else assert.match(result.missingInputs.join(' '),/모델 품질.*승인|품질.*배포.*승인/);
});

test('step 6 counts the inspection the panel shows for this source and nothing without one',()=>{
 const {getNextAction,progressLine}=load();
 const flows=[{version_id:'v',state:'current',scope_matches:true}];
 const six={...base,stage:6,flowVersionId:'v',flowMatchesSaved:true,evidence:{...base.evidence,impact:{...base.evidence.impact,flows}}};
 assert.equal(getNextAction(six).progress,undefined);
 assert.equal(getNextAction({...six,inspection:{inspected:0,total:0,ng:0}}).progress,undefined,'an empty run counts nothing');
 const inspected=getNextAction({...six,inspection:{inspected:40,total:40,ng:3,status:'completed',isRunning:false}});
 assert.equal(inspected.nextAction.label,'승인·패키지·대상 확인');assert.deepEqual(inspected.progress,{inspected:40,total:40,ng:3});
 assert.equal(progressLine(6,inspected.progress,true),' · 검사 40장 · 불량 3장');
 assert.equal(progressLine(6,{inspected:12,total:40,ng:1},true),' · 검사 12/40장 · 불량 1장','a run still going shows how far it is');
 // Failed images are done (the panel header's 100/100), and the line names them with the panel's '실패' word.
 const failed=getNextAction({...six,inspection:{inspected:100,total:100,ng:3,failed:5}});
 assert.deepEqual(failed.progress,{inspected:100,total:100,ng:3,failed:5});
 assert.equal(progressLine(6,failed.progress,true),' · 검사 100장 · 불량 3장 · 실패 5장');
 assert.equal(progressLine(6,failed.progress,false),' · 100 inspected · 3 NG · 5 failed');
 assert.deepEqual(getNextAction({...six,inspection:{inspected:4,total:10,ng:0,failed:9}}).progress,{inspected:4,total:10,ng:0,failed:4},'never more failures than images done');
 assert.equal(Object.hasOwn(getNextAction({...six,inspection:{inspected:40,total:40,ng:3,failed:0}}).progress,'failed'),false,'no failures: the shape stays three keys');
 assert.deepEqual(getNextAction({...base,stage:6,inspection:{inspected:40,total:40,ng:3}}).progress,{inspected:40,total:40,ng:3},'the shown run counts even while the flow needs saving');
 assert.deepEqual(getNextAction({...six,stage:5,inspection:{inspected:40,total:40,ng:3}}).progress,{versionId:'v',evaluated:true,unsaved:false},'step 5 shows its saved version, not the inspection');
});

test('the footer line keeps step 2 wording, a shape from another step prints nothing, and the clock reads mm:ss',()=>{
 const {progressLine,formatEta}=load();
 assert.equal(progressLine(2,{confirmed:48,drafts:4,remaining:2},true),' · 확정 48장 / 초안 4장 / 남은 2장');
 assert.equal(progressLine(2,{confirmed:48,drafts:4,remaining:2},false),' · 48 confirmed / 4 drafts / 2 left');
 assert.equal(progressLine(3,{confirmed:48,drafts:4,remaining:2},true),'');
 assert.equal(progressLine(2,null,true),'');assert.equal(progressLine(4,undefined,true),'');
 assert.equal(formatEta(154),'02:34');assert.equal(formatEta(3725),'62:05');assert.equal(formatEta(0),'00:00');
 const footer=fs.readFileSync(path.join(__dirname,'WizardFooter.tsx'),'utf8');
 assert.match(footer,/progressLine\(readiness\.stage,readiness\.progress,ko\)/);
 assert.ok(!footer.includes('progress.confirmed'),'the footer no longer formats step 2 by itself');
 assert.match(footer,/title=\{blocker\?`\$\{blocker\} → \$\{readiness\.nextAction\.label\}\$\{progress\}`/,'the status title carries the full line for the truncated span');
});

test('the production hook feeds the training clock, the evaluation counts and the shown inspection into the number line',async()=>{
 const current={job_id:'m',task:'classification',checkpoint_state:'current',data_state:'current',scope_matches:true};
 const resolveAll=fixture=>{for(const row of fixture.requests.splice(0))row.resolve(row.endpoint.includes('team-data')?{ready:true,counts:{eligible:12}}:fixture.impact({models:[current],model_evaluations:[{job_id:'m',state:'current',evaluation_id:'e1'}]}));};
 const three=scopedHookFixture({stage:3});try{
  Object.assign(three.training,{status:'running',isCurrentData:true,currentEpoch:3,totalEpochs:10,totalEtaSeconds:154,epochEtaSeconds:12});
  three.mount();resolveAll(three);const result=await three.settle();
  assert.deepEqual(result.readiness.progress,{epoch:3,totalEpochs:10,etaSeconds:150,completed:false},'the clock is rounded to ten seconds');
  Object.assign(three.training,{totalEtaSeconds:null,epochEtaSeconds:4});
  assert.equal(three.render().readiness.progress.etaSeconds,10,'the epoch clock stands in for a missing total, never below ten seconds');
  Object.assign(three.training,{status:'completed',currentEpoch:10,totalEtaSeconds:null,epochEtaSeconds:null});
  const between=three.render();
  assert.equal(between.readiness.nextAction.kind,'refresh','the end of the run re-reads the evidence');
  assert.deepEqual(between.readiness.progress,{epoch:10,totalEpochs:10,etaSeconds:null,completed:true},'running goes straight to ready, without a blank line in between');
  three.runEffects();
  const checking=three.render();
  assert.equal(checking.readiness.status,'checking');assert.deepEqual(checking.readiness.progress,{epoch:10,totalEpochs:10,etaSeconds:null,completed:true},'and stays while the evidence is checked');
  resolveAll(three);
  assert.deepEqual((await three.settle()).readiness.progress,{epoch:10,totalEpochs:10,etaSeconds:null,completed:true});
 }finally{three.unmount();}
 const four=scopedHookFixture({stage:4});try{
  four.evaluation.jobId='m';four.evaluation.confidenceThreshold=0.5;four.evaluation.persistedThreshold=0.5;
  four.evaluation.testPredictions=[{file_path:'a.png',ground_truth:'ok',defect_score:.1},{file_path:'b.png',ground_truth:'ng',defect_score:.9},{file_path:'c.png',ground_truth:'ng',defect_score:.3},{file_path:'d.png',ground_truth:'ok',defect_score:.8},{file_path:'e.png'}];
  four.mount();resolveAll(four);const result=await four.settle();
  assert.equal(result.readiness.status,'blocked','one missed defect (c.png): looked at before step 5');assert.equal(result.readiness.nextAction.label,'틀린 이미지 보기');
  assert.deepEqual(result.readiness.progress,{samples:5,escapes:1,overkills:1,accuracy:40,low:12,high:77},'counted with the evaluation screen\'s verdict and range');
  assert.match(loadSampleConfidence().accuracyNote(2,5),/실제는 12~77% 사이/,'the range the step-4 card prints for 2 right of 5');
  four.evaluation.testPredictions=[{file_path:'a.png'},{file_path:'b.png'}];
  assert.deepEqual(four.render().readiness.progress,{samples:2,escapes:0,overkills:0,accuracy:null,low:null,high:null},'no image with an answer: no accuracy, the image count stays');
  four.evaluation.testPredictions=[];
  assert.equal(four.render().readiness.progress,undefined,'no loaded evaluation: no line');
 }finally{four.unmount();}
 const six=scopedHookFixture({stage:6});try{
  six.publishInspection(JSON.stringify(['/data','/p','classification']),{inspected:40,total:40,ng:3});
  six.mount();resolveAll(six);const result=await six.settle();
  assert.deepEqual(result.readiness.progress,{inspected:40,total:40,ng:3});
  six.publishInspection(JSON.stringify(['/other','/p','classification']),{inspected:9,total:9,ng:0});
  assert.equal(six.render().readiness.progress,undefined,'another source\'s run does not count here');
  six.publishInspection(JSON.stringify(['/data','/p','classification']),{inspected:12,total:40,ng:1});
  assert.deepEqual(six.render().readiness.progress,{inspected:12,total:40,ng:1},'a published change reaches the footer without a request');
  six.publishInspection(null,null);
  assert.equal(six.render().readiness.progress,undefined,'the panel closing clears the line');
 }finally{six.unmount();}
});

test('a source switch never renders the previous source\'s step-6 counts, not even for the first render',()=>{
 const fixture=scopedHookFixture({stage:6});try{
  fixture.publishInspection(JSON.stringify(['/data','/p','classification']),{inspected:10,total:10,ng:2});
  assert.deepEqual(fixture.mount().readiness.progress,{inspected:10,total:10,ng:2});
  fixture.runEffects();assert.deepEqual(fixture.assists.at(-1).progress,{inspected:10,total:10,ng:2});
  // The new source renders before any effect runs (the panel's clear, the footer's subscription): it holds no counts yet.
  fixture.data.folderPath='/other';fixture.data.datasetKey='/other\0classification';fixture.project.project.source_dataset_dir='/other';
  assert.equal(fixture.render().readiness.progress,undefined,'the first render for the new source shows no old counts');
  fixture.runEffects();assert.equal(fixture.assists.at(-1).progress,null,'and the assist context never receives them');
  fixture.publishInspection(JSON.stringify(['/other','/p','classification']),{inspected:3,total:9,ng:1});
  assert.deepEqual(fixture.render().readiness.progress,{inspected:3,total:9,ng:1},'the new source\'s own run reaches the footer');
 }finally{fixture.unmount();}
});

test('an edit keeps step 5\'s saved version on the line through the re-check it starts, for the same project and source only',async()=>{
 const {progressLine}=load(),progressLineOf=result=>progressLine(result.readiness.stage,result.readiness.progress,true);
 const fixture=scopedHookFixture({stage:5});try{
  fixture.pipelines.push({version_id:'v1234567890',is_active:true});
  const resolveAll=()=>{for(const row of fixture.requests.splice(0))row.resolve(row.endpoint.includes('team-data')?{ready:true,counts:{eligible:12}}:fixture.impact({flows:[{version_id:'v1234567890',state:'current',scope_matches:true}]}));};
  fixture.mount();resolveAll();
  assert.deepEqual((await fixture.settle()).readiness.progress,{versionId:'v1234567890',evaluated:true,unsaved:false});
  // An edit changes the key (semantic revision) in the same store update that marks the editor dirty.
  Object.assign(fixture.flow,{pipeline:{id:'graph',nodes:[],edges:[]},pipelineDirty:true,flowIdentity:{semantic_revision:1}});
  const edited=fixture.render();
  assert.equal(edited.readiness.nextAction.kind,'refresh','the edit re-reads the evidence');
  assert.equal(progressLineOf(edited),' · 저장 버전 v1234567 · 편집 중','\'편집 중\' shows at once, not after the round trip');
  fixture.runEffects();
  const checking=fixture.render();
  assert.equal(checking.readiness.status,'checking');assert.equal(progressLineOf(checking),' · 저장 버전 v1234567 · 편집 중');
  resolveAll();
  const settled=await fixture.settle();
  assert.equal(settled.readiness.status,'blocked','the carried id never reaches the advice: the new receipt decides it');
  assert.deepEqual(settled.readiness.progress,{versionId:'v1234567890',evaluated:true,unsaved:true});
  // Without unsaved edits nothing is carried: the evaluation state needs the new report.
  Object.assign(fixture.flow,{pipelineDirty:false,flowIdentity:{semantic_revision:2}});
  assert.equal(fixture.render().readiness.progress,undefined);
  // Another project never inherits the version.
  fixture.project.project={id:'q',source_dataset_dir:'/data',active_labelset_id:'default'};
  Object.assign(fixture.flow,{pipelineDirty:true,flowIdentity:{semantic_revision:3}});
  assert.equal(fixture.render().readiness.progress,undefined);
 }finally{fixture.unmount();}
});

test('the draft autosave after an edit keeps step 5\'s \'편집 중\' through the re-check it starts; a kept draft equal to the saved version does not',async()=>{
 const {getNextAction,progressLine}=load(),progressLineOf=result=>progressLine(result.readiness.stage,result.readiness.progress,true);
 const flows=[{version_id:'v1234567890',state:'current',scope_matches:true}];
 const autosaved=getNextAction({...base,stage:5,flowVersionId:'v1234567890',flowMatchesSaved:false,flowEditing:true,evidence:{...base.evidence,impact:{...base.evidence.impact,flows}}});
 assert.equal(autosaved.nextAction.label,'보이는 검사 흐름 저장·평가 (5단계)','the advice keeps reading flowDirty');assert.equal(autosaved.progress.unsaved,true);
 const fixture=scopedHookFixture({stage:5});try{
  fixture.pipelines.push({version_id:'v1234567890',is_active:true});
  const resolveAll=()=>{for(const row of fixture.requests.splice(0))row.resolve(row.endpoint.includes('team-data')?{ready:true,counts:{eligible:12}}:fixture.impact({flows}));};
  fixture.mount();resolveAll();await fixture.settle();
  Object.assign(fixture.flow,{pipeline:{id:'graph',nodes:[],edges:[]},pipelineDirty:true,flowIdentity:{semantic_revision:1}});
  fixture.render();fixture.runEffects();resolveAll();
  assert.equal(progressLineOf(await fixture.settle()),' · 저장 버전 v1234567 · 편집 중');
  // saveDraft 650 ms later (FlowDraftControls): clean against the kept draft, which the server says is not the active version.
  Object.assign(fixture.flow,{pipelineDirty:false,pipelineIsDraft:true,persistedDraftHash:'a'.repeat(64)});
  assert.equal(progressLineOf(fixture.render()),' · 저장 버전 v1234567 · 편집 중','the render that takes the autosave');
  fixture.runEffects();
  const checking=fixture.render();
  assert.equal(checking.readiness.status,'checking','the autosave re-reads the evidence');
  assert.equal(progressLineOf(checking),' · 저장 버전 v1234567 · 편집 중','no blank while it is checked');
  resolveAll();
  const settled=await fixture.settle();
  assert.equal(progressLineOf(settled),' · 저장 버전 v1234567 · 편집 중');
  assert.deepEqual(settled.readiness.missingInputs,['편집 화면과 활성 저장 버전의 규칙이 다릅니다.']);
  // Undone to the saved rules and autosaved again: the server proves the kept draft equals the active version.
  Object.assign(fixture.flow,{pipelineIsDraft:false,persistedDraftHash:'b'.repeat(64),flowIdentity:{semantic_revision:2}});
  fixture.render();fixture.runEffects();resolveAll();
  assert.equal(progressLineOf(await fixture.settle()),' · 저장 버전 v1234567 · 평가 완료','a kept draft hash alone is not an edit');
 }finally{fixture.unmount();}
});

test('after a save moves the editor\'s base version, step 5 never carries the version it replaced',async()=>{
 const {progressLine}=load(),progressLineOf=result=>progressLine(result.readiness.stage,result.readiness.progress,true);
 const fixture=scopedHookFixture({stage:5});try{
  fixture.pipelines.push({version_id:'v1aaaaaaaa',is_active:true});fixture.flow.baseVersionId='v1aaaaaaaa';
  const resolveAll=()=>{for(const row of fixture.requests.splice(0))row.resolve(row.endpoint.includes('team-data')?{ready:true,counts:{eligible:12}}:fixture.impact({flows:[{version_id:'v1aaaaaaaa',state:'current',scope_matches:true}]}));};
  fixture.mount();resolveAll();
  assert.equal(progressLineOf(await fixture.settle()),' · 저장 버전 v1aaaaaa · 평가 완료');
  // The save makes v2 active and the editor's base; the next edit lands before the save's re-check returns.
  fixture.pipelines.splice(0,1,{version_id:'v1aaaaaaaa',is_active:false},{version_id:'v2bbbbbbbb',is_active:true});
  Object.assign(fixture.flow,{pipeline:{id:'graph',nodes:[],edges:[]},pipelineDirty:false,baseVersionId:'v2bbbbbbbb'});
  fixture.render();fixture.runEffects();
  Object.assign(fixture.flow,{pipeline:{id:'graph',nodes:[{id:'n'}],edges:[]},pipelineDirty:true,flowIdentity:{semantic_revision:1}});
  assert.equal(progressLineOf(fixture.render()),'','the edit right after the save does not name the replaced v1');
  fixture.runEffects();
  assert.equal(progressLineOf(fixture.render()),'','nor while its re-check is in flight');
  resolveAll();
  assert.equal(progressLineOf(await fixture.settle()),' · 저장 버전 v2bbbbbb · 편집 중','the new report names v2');
  Object.assign(fixture.flow,{pipeline:{id:'graph',nodes:[{id:'m'}],edges:[]},flowIdentity:{semantic_revision:2}});
  assert.equal(progressLineOf(fixture.render()),' · 저장 버전 v2bbbbbb · 편집 중','and the next edit carries v2');
 }finally{fixture.unmount();}
});

test('a new inspection result replaces the step-6 counts in place; only its own source change or closing clears them',()=>{
 const filename=path.join(__dirname,'..','..','stores','workflowProgress.ts'),m=new Module(filename,module);
 m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);
 const {publishInspectionProgress,inspectionProgressFor,subscribeInspectionProgress}=m.exports;
 let notified=0;subscribeInspectionProgress(()=>{notified++;});
 publishInspectionProgress('a',{inspected:1,total:4,ng:0});publishInspectionProgress('a',{inspected:1,total:4,ng:0});
 assert.equal(notified,1,'the same counts again are silent');
 publishInspectionProgress('a',{inspected:4,total:4,ng:1});
 assert.deepEqual(inspectionProgressFor('a'),{inspected:4,total:4,ng:1});assert.equal(notified,2,'one notice per new result, no blank in between');
 publishInspectionProgress('b',null);
 assert.deepEqual(inspectionProgressFor('a'),{inspected:4,total:4,ng:1});assert.equal(notified,2,'a clear for another source leaves the counts');
 publishInspectionProgress('a',null);
 assert.equal(inspectionProgressFor('a'),null);assert.equal(notified,3);
 publishInspectionProgress('a',null);assert.equal(notified,3,'clearing nothing is silent');
 publishInspectionProgress('a',{inspected:4,total:4,ng:1});publishInspectionProgress('a',{inspected:4,total:4,ng:1,failed:1});
 assert.equal(notified,5,'a failure with the same other counts is a new result');
 publishInspectionProgress('a',{inspected:4,total:4,ng:1,failed:1});assert.equal(notified,5);
 // The panel republishes without clearing first; the clear sits in its own effect keyed by the source alone.
 const panel=fs.readFileSync(path.join(__dirname,'..','inference','BatchInspectionPanel.tsx'),'utf8');
 assert.match(panel,/useEffect\(\(\) => \(\) => publishInspectionProgress\(progressScope, null\), \[progressScope\]\);/);
 assert.equal(panel.match(/publishInspectionProgress\(progressScope, null\)/g).length,1,'no clear before each republish');
 // Done images match the header's processed + errors, and the failures travel with them.
 assert.match(panel,/inspected: counts\.processed \+ counts\.errors, total: counts\.total, ng: counts\.ng, \.\.\.\(counts\.errors \? \{failed: counts\.errors\} : \{\}\)/);
 assert.match(panel,/const progress = summary\.total \? summary\.processed \+ summary\.errors : 0;/);
});

test('status-only inspection transitions notify subscribers and remain confined to their source',()=>{
 const filename=path.join(__dirname,'..','..','stores','workflowProgress.ts'),m=new Module(filename,module);
 m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);
 const {publishInspectionProgress,inspectionProgressFor,subscribeInspectionProgress}=m.exports;
 let notified=0;subscribeInspectionProgress(()=>notified++);
 const counts={inspected:12,total:12,ng:0};
 publishInspectionProgress('own',{...counts,status:'running',isRunning:true});
 publishInspectionProgress('own',{...counts,status:'completed',isRunning:true});
 assert.equal(notified,2,'persisted lifecycle transition is visible without a row-count change');
 publishInspectionProgress('own',{...counts,status:'completed',isRunning:false});
 assert.equal(notified,3,'runtime finish is visible without a row-count change');
 assert.deepEqual(inspectionProgressFor('own'),{...counts,status:'completed',isRunning:false});
 assert.equal(inspectionProgressFor('other'),null,'another source cannot inherit the completed run');
 publishInspectionProgress('other',null);assert.equal(notified,3);
 publishInspectionProgress('own',{...counts,status:'completed',isRunning:false});assert.equal(notified,3,'identical lifecycle stays silent');
});

test('a failed training job guides the person to its error details instead of a ready start action',()=>{
 const {getNextAction}=load();
 const result=getNextAction({...base,stage:3,splitState:'saved',trainingStatus:'failed'});
 assert.equal(result.status,'blocked');
 assert.match(result.missingInputs.join(' '),/학습.*실패/);
 assert.equal(result.nextAction.kind,'details');
 assert.equal(result.nextAction.step,3);
 assert.match(result.nextAction.label,/오류/);
});

// The footer's jump and the first-run guide reach their target by id. Three targets used to be found by their Korean
// button text or title: in English nothing was found, and '프로젝트 관리 열기' only focused the project button (the guide
// closed and nothing opened). Each target now carries its id, and the project button opens its dialog.
test('every footer target is an element id or a named opener in the renderer',()=>{
 const readiness=fs.readFileSync(path.join(__dirname,'workflowReadiness.ts'),'utf8');
 const targets=[...new Set([...readiness.matchAll(/'((?:workflow|flow-area|draft)-[a-z0-9-]+|labeling-canvas|image-class-picker)'/g)].map(m=>m[1]))];
 for(const expected of ['workflow-project','workflow-data','workflow-label-review','workflow-training','labeling-canvas','flow-area-edit','flow-area-evaluate'])assert.ok(targets.includes(expected),expected);
 const root=path.join(__dirname,'..','..');const sources=[];
 const walk=dir=>{for(const entry of fs.readdirSync(dir,{withFileTypes:true})){const file=path.join(dir,entry.name);if(entry.isDirectory())walk(file);else if(/\.tsx$/.test(entry.name))sources.push(fs.readFileSync(file,'utf8'));}};
 walk(root);const all=sources.join('\n');
 const flowAreas=fs.readFileSync(path.join(root,'components','flowchart','FlowEditorWorkspace.tsx'),'utf8');
 for(const target of targets){
  const literal=all.includes(`id="${target}"`)||all.includes(`data-workflow-opens="${target}"`);
  const flowArea=target.startsWith('flow-area-')&&flowAreas.includes('id={`flow-area-${key}`}')&&flowAreas.includes(`['${target.slice('flow-area-'.length)}',`);
  assert.ok(literal||flowArea,`${target} has an element`);
 }
 assert.doesNotMatch(readiness,/textContent\?\.trim\(\)===label|\[title="프로젝트 관리"\]/,'no lookup by interface text');
 assert.match(fs.readFileSync(path.join(root,'components','wizard','WizardHeader.tsx'),'utf8'),/<button type="button" id="workflow-project" aria-haspopup="dialog" aria-expanded=\{showProjectPanel\} data-workflow-open-on-focus onClick=\{\(\) => setShowProjectPanel\(true\)\}/);
 assert.match(fs.readFileSync(path.join(root,'components','onboarding','FirstRunStudio.tsx'),'utf8'),/onClick=\{\(\) => \{ onClose\(\); focusWorkflowTarget\('workflow-project'\); \}\}>프로젝트 관리 열기<\/button>/);
});

test('the jump presses a closed dialog opener and leaves it the focus; other openers and targets are focused',()=>{
 const source=fs.readFileSync(path.join(__dirname,'workflowReadiness.ts'),'utf8');
 const start=source.indexOf('export function focusWorkflowTarget'),end=source.indexOf('\n}\n',start)+3;
 const helpers=source.slice(source.indexOf('function openFoldsAround'),source.indexOf('\n}\n',source.indexOf('function openFoldsAround'))+3);
 const js=ts.transpileModule(helpers+source.slice(start,end),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const make=(id,attrs={})=>{const el={id,attrs:{...attrs},clicks:0,focused:false,scrolled:false,parentElement:null,closest:()=>null,
  getAttribute(name){return name in this.attrs?String(this.attrs[name]):null;},hasAttribute(name){return name in this.attrs;},
  click(){this.clicks++;if('aria-expanded' in this.attrs)this.attrs['aria-expanded']='true';},scrollIntoView(){this.scrolled=true;},focus(){this.focused=true;doc.activeElement=this;}};return el;};
 const frames=[];const doc={activeElement:null,elements:{},getElementById(id){return this.elements[id]||null;},querySelector:()=>null};
 const run=new Function('document','requestAnimationFrame','HTMLElement','exports',js+';exports.focusWorkflowTarget=focusWorkflowTarget;');
 const exports={};run(doc,fn=>frames.push(fn),function(){},exports);
 const flush=()=>{while(frames.length)frames.shift()();};
 const project=make('workflow-project',{'aria-haspopup':'dialog','aria-expanded':'false','data-workflow-open-on-focus':''});
 const queue=make('draft-review-queue',{'aria-expanded':'false','data-workflow-open-on-focus':''});
 const data=make('workflow-data');
 doc.elements={'workflow-project':project,'draft-review-queue':queue,'workflow-data':data};
 exports.focusWorkflowTarget('workflow-project');flush();
 assert.equal(project.clicks,1,'the project dialog is opened');assert.equal(project.focused,false,'the dialog keeps the focus it takes');
 exports.focusWorkflowTarget('draft-review-queue');flush();
 assert.equal(queue.clicks,1);assert.equal(queue.focused,true,'a popover opener is still focused');
 exports.focusWorkflowTarget('workflow-data');flush();
 assert.equal(data.clicks,0);assert.equal(data.focused,true);assert.equal(data.scrolled,true);
 // Step 4's '틀린 이미지 보기' presses the 미검 tab once (the list then shows the missed images) and focuses it.
 const missed=make('workflow-missed-defects',{role:'tab','aria-selected':'false'});doc.elements['workflow-missed-defects']=missed;
 exports.focusWorkflowTarget('workflow-missed-defects');flush();
 assert.equal(missed.clicks,1);assert.equal(missed.focused,true);
 const shown=make('workflow-missed-defects',{role:'tab','aria-selected':'true'});doc.elements['workflow-missed-defects']=shown;
 exports.focusWorkflowTarget('workflow-missed-defects');flush();
 assert.equal(shown.clicks,0,'an already selected tab is not pressed again');assert.equal(shown.focused,true);
});

test('step 4 waits for the evaluation screen\'s read, is not done without its results, and a result with no image to score keeps the ready path',()=>{
 const {getNextAction}=load();
 const model={job_id:'m',task:'classification',checkpoint_state:'current',data_state:'current',scope_matches:true};
 const withModel={...base,stage:4,modelJobId:'m',evidence:{...base.evidence,impact:{...base.evidence.impact,models:[model],model_evaluations:[{job_id:'m',state:'current',evaluation_id:'e1'}]}}};
 const loading=getNextAction({...withModel,evaluationLoading:true});
 assert.deepEqual([loading.status,loading.nextAction.label,loading.nextAction.step,loading.nextAction.kind],['checking','준비도 다시 확인',4,'refresh']);
 // Recorded but not on the screen (a failed read, or a selection that cleared it): the misses are unknown, the step is not done.
 const missing=getNextAction({...withModel,evaluationLoading:false});
 assert.deepEqual([missing.status,missing.nextAction.label,missing.nextAction.step,missing.nextAction.target],['unknown','새로고침 버튼으로 이동 (4단계)',4,'evaluation-refresh']);
 assert.equal(missing.missingInputs[0],'시험 이미지 평가 결과가 화면에 없습니다');
 const failed=getNextAction({...withModel,evaluationError:'연결이 끊겼습니다'});
 assert.deepEqual([failed.status,failed.missingInputs[0]],['blocked','시험 이미지 평가 결과를 읽지 못했습니다: 연결이 끊겼습니다'],'a failed read is for a person to look at');
 const unscored=getNextAction({...withModel,evaluation:{samples:5,escapes:0,overkills:0,accuracy:null,low:null,high:null}});
 assert.equal(unscored.status,'ready');assert.equal(unscored.nextAction.label,'검사 흐름 만들기 (5단계)');
 assert.equal(getNextAction({...withModel,evaluationLoading:true,evaluation:{samples:3,escapes:3,overkills:0,accuracy:0,low:0,high:56}}).nextAction.label,'틀린 이미지 보기','counts already in: the misses decide');
});

for(const row of [
 {name:'saved miss survives a lower preview',saved:.5,preview:.2,score:.3,misses:1},
 {name:'saved success survives a higher preview',saved:.2,preview:.5,score:.3,misses:0},
 {name:'saved zero remains a valid probability threshold',saved:0,preview:.5,score:0,misses:0},
 {name:'distance thresholds above one retain their verdict',saved:8,preview:2,score:4,misses:1},
 {name:'patch-score equality remains a saved miss',saved:.3,preview:.2,score:.3,patch:true,misses:1},
 {name:'live recommendation cannot replace accepted truth',saved:.5,preview:.5,score:.3,misses:1,analysis:true},
 {name:'unknown saved authority cannot advance',saved:null,preview:.2,score:.3,unknown:true},
])test('production readiness '+row.name,async()=>{
 const f=scopedHookFixture({stage:4});try{
  Object.assign(f.evaluation,{jobId:'m',persistedThreshold:row.saved,confidenceThreshold:row.preview,classSemantics:{roles:{OK:'defect'}},testPredictions:[{image_id:'ng',ground_truth:'OK',defect_score:row.score,...(row.patch?{map_semantics:'patch_score'}:{})}],overkillAnalysis:row.analysis?{optimal_threshold:.01,sample_details:[{image_id:'ng',is_defect:false,defect_score:0}]}:null});
  f.mount();for(const request of f.requests)request.resolve(request.endpoint.includes('team-data')?{ready:true,counts:{eligible:12}}:f.impact({models:[{job_id:'m',task:'classification',checkpoint_state:'current',data_state:'current',scope_matches:true}],model_evaluations:[{job_id:'m',state:'current',evaluation_id:'e'}]}));
  const result=(await f.settle()).readiness;
  if(row.unknown){assert.equal(result.status,'unknown');assert.equal(result.progress,undefined);}else{assert.equal(result.progress.escapes,row.misses);assert.equal(result.status,row.misses?'blocked':'ready');}
 }finally{f.unmount();}
});
