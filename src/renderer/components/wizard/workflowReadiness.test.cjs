const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(){const filename=path.join(__dirname,'workflowReadiness.ts');assert.ok(fs.existsSync(filename),'production readiness contract must exist');const m=new Module(filename,module);m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);return m.exports;}
const base={stage:1,projectReady:true,sourceReady:true,totalImages:12,train:8,val:4,busy:false,task:'classification',modelJobId:null,flowVersionId:null,flowDirty:false,evidence:{status:'ready',review:{ready:true,counts:{eligible:12}},impact:{models:[],model_evaluations:[],flows:[],flow_evaluations:[],approvals:[],packages:[]}}};
test('each stage has completed/missing inputs and exactly one recommended action',()=>{const {getNextAction}=load();for(let stage=1;stage<=6;stage++){const r=getNextAction({...base,stage,sourceReady:false,totalImages:0});assert.ok(r.missingInputs.length);assert.ok(Array.isArray(r.completedInputs));assert.equal(typeof r.nextAction.label,'string');assert.ok(r.nextAction.step>=1&&r.nextAction.step<=6);}});
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
 const mocks={react:{useEffect:fn=>effects.push(fn),useState:initial=>{const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],v=>slots[i]=typeof v==='function'?v(slots[i]):v];},useRef:value=>{const i=cursor++;return slots[i]??(slots[i]={current:value});}},
 '../../services/api':{getProjectContext:()=>context,getApiPersistenceIdentity:()=>null,subscribeProjectContext:()=>()=>{},request:async endpoint=>endpoint.includes('team-data')?{ready:true,counts:{eligible:10}}:{project_id:wrongProject?'other':'p',source_dataset_path:'/data',labelset_id:'default',models:[],model_evaluations:[],flows:[{version_id:'a',scope_matches:true,state:'current'}],approvals:[],packages:[]},api:{flowchart:{listPipelines:async()=>({pipelines:[{version_id:'a',is_active:true}]}),getPipelineVersion:async id=>{calls.push(id);return saved;}}}},
 '../../stores/useProjectStore':{useProjectStore:store(project)},'../../stores/workflowEvidence':{subscribeWorkflowEvidence:()=>()=>{},workflowEvidenceRevision:()=>0},'../../stores/useComputeStore':{useComputeStore:store({transportRevision:0})},'../../stores/useDatasetStore':{useDatasetStore:store(data)},'../../stores/useTrainingStore':{useTrainingStore:store({status:'idle'})},'../../stores/useEvaluationStore':{useEvaluationStore:store({jobId:null})},'../../stores/useAnnotationStore':{useAnnotationStore:store({isDirty:false,metadata:null})},'../../stores/useFlowchartStore':{useFlowchartStore:store(flow),flowSemanticKey:value=>JSON.stringify(value)},'./workflowReadiness':load(),'../../stores/workflowProgress':{publishWorkflowProgress:()=>{}},'./stepProgress':{stepsFromEvidence:()=>[]}};
 m.require=name=>mocks[name];m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);
 m.exports.useWorkflowReadiness();effects.forEach(fn=>fn());await new Promise(resolve=>setImmediate(resolve));cursor=0;const result=m.exports.useWorkflowReadiness();return{...result,calls};
}
test('production readiness hook reads the active saved graph before claiming displayed rules match',async()=>{const result=await hookFixture();assert.deepEqual(result.calls,['a']);assert.equal(result.readiness.status,'blocked');assert.match(result.readiness.missingInputs.join(' '),/규칙이 다릅니다/);});
test('production readiness hook rejects evidence from a different project',async()=>{const result=await hookFixture({wrongProject:true});assert.equal(result.readiness.status,'unknown');assert.match(result.readiness.missingInputs.join(' '),/현재 입력과 다릅니다/);assert.deepEqual(result.calls,[]);});

// Unit controls for stage-scoped readiness; no model or deployment authorization.
function scopedHookFixture({stage=2}={}) {
 const filename=path.join(__dirname,'useWorkflowReadiness.ts'),m=new Module(filename,module);
 const slots=[],effectSlots=[],pendingEffects=[],requests=[],pipelineCalls=[],savedCalls=[],published=[];
 let cursor=0,effectCursor=0,contextListener=null,persistence=null;
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
 };
 const mocks={react:React,
  '../../services/api':{
   getProjectContext:()=>context,getApiPersistenceIdentity:()=>persistence,
   subscribeProjectContext:listener=>{contextListener=listener;return()=>{if(contextListener===listener)contextListener=null;};},
   request:endpoint=>new Promise((resolve,reject)=>requests.push({endpoint,resolve,reject,scope:JSON.stringify([context,persistence])})),
   api:{flowchart:{listPipelines:async folder=>{pipelineCalls.push(folder);return{pipelines:[]};},getPipelineVersion:async id=>{savedCalls.push(id);return null;}}},
  },
  '../../stores/useProjectStore':{useProjectStore:store(project)},
  '../../stores/useComputeStore':{useComputeStore:store({transportRevision:0})},
  '../../stores/useDatasetStore':{useDatasetStore:store(data)},
  '../../stores/useTrainingStore':{useTrainingStore:store(training)},
  '../../stores/useEvaluationStore':{useEvaluationStore:store(evaluation)},
  '../../stores/useAnnotationStore':{useAnnotationStore:store(annotation)},
  '../../stores/useFlowchartStore':{useFlowchartStore:store(flow),flowSemanticKey:value=>JSON.stringify(value)},
  '../../stores/workflowEvidence':{subscribeWorkflowEvidence:()=>()=>{},workflowEvidenceRevision:()=>0},
  './workflowReadiness':load(),
  '../../stores/workflowProgress':{publishWorkflowProgress:(scope,steps)=>published.push({scope,steps})},
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
 return{requests,pipelineCalls,savedCalls,published,project,data,annotation,training,flow,evaluation,render,runEffects,mount,settle,unmount,impact,
  setContext:value=>{context=value;contextListener?.(value);},
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
