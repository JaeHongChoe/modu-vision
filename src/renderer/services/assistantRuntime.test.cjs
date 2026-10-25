// Production runtime, guidance and draft ordering; only backend I/O and app store boundaries are controlled.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function fixture(){
 const project={projectDir:'/project',task:'classification',isProjectBusy:false,project:{id:'p',project_dir:'/project',task:'classification',source_dataset_dir:'/source',active_labelset_id:'default'}};
 const data={hasSelectedFolder:true,totalImages:10,unlabeledImages:2,folderPath:'/source',datasetKey:'/source\0classification',isLoading:false,isSplitting:false,classes:{OK:6,NG:4},split:{train:6,val:2,test:2},splitState:'saved',images:[]};
 const training={preset:'fast',nextDevice:'cpu',nextBatchSize:8,isTraining:false,status:'idle',setPreset(p){this.preset=p;},setNextSettings(s){if(s.device)this.nextDevice=s.device;if(s.batchSize)this.nextBatchSize=s.batchSize;}};
 const compute={isLoaded:true,isLoading:false,loadError:null,selectedProfileId:null,transportRevision:0,profiles:[{id:'server'}]};
 const annotation={currentImage:{file_path:'/source/a.png'},images:[]};
 const evaluation={jobId:'job1',isLoading:false,confidenceThreshold:.5,metrics:{evaluated_split:'val',selection_overlap:true},testPredictions:[{ground_truth:'OK',defect_score:.2},{ground_truth:'NG',defect_score:.4}],overkillAnalysis:null,executionEvidence:{job_id:'job1',task:'classification',evaluated_at:'2026-10-24'}};
 const calls=[],cache=new Map(),writes=new Map();let epoch=1,opened=true,sourceCurrent=true,listRelease;
 const writeResult=(name,value)=>writes.has(name)?writes.get(name).promise:Promise.resolve(value);
 data.applySplit=async(...args)=>{calls.push(['split-write',...args]);await writeResult('split');};
 training.startTraining=async(...args)=>{calls.push(['training-write',...args]);await writeResult('training');};
 annotation.applyImageClass=(...args)=>calls.push(['apply-class',...args]);data.annotationsChanged=async()=>{calls.push(['refresh-annotations']);};
 const drafts=[{id:'high',image_path:'/source/high.png',status:'pending',created_at:'2026-01-01',candidates:[{confidence:.9}]},{id:'low',image_path:'/source/low.png',status:'pending',created_at:'2026-01-02',candidates:[{confidence:.3}]},{id:'empty',image_path:'/source/empty.png',status:'pending',created_at:'2026-01-01',candidates:[]},{id:'closed',image_path:'/source/c.png',status:'accepted',created_at:'2026-01-01',candidates:[{confidence:.1}]}];
 const ready={ready:true,model:'dinov3_vits16',runtime:{available:true,device:'cpu'},dependencies:{missing:[]},weights:{state:'file_available',filename:'/weights/model.safetensors',sha256:'ab'.repeat(32),content_verified:false},input:{split_counts:{train:6,val:2,test:2}},next_actions:[],execution_verified:false};
 project.setStep=async step=>{calls.push(['step',step]);};project.openImageForLabeling=async(id,file)=>{calls.push(['image',id,file]);if(opened)annotation.currentImage={file_path:file};return opened;};
 const store=state=>({getState:()=>state});const draftStore={requestOpen:(...args)=>calls.push(['requestOpen',...args])};
 const request=async(url,options)=>{calls.push(['request',url,options]);if(url==='/api/models/capabilities')return{families:[{task:'classification',default_architecture:'dinov3_vits16'}]};if(url.startsWith('/api/training-workspace/readiness'))return ready;throw Error('Unexpected backend request '+url);};
 const mocks={'../stores/assistContext':{currentAssistContext:()=>null},'../stores/useProjectStore':{useProjectStore:store(project)},'../stores/useDatasetStore':{useDatasetStore:store(data)},'../stores/useTrainingStore':{useTrainingStore:store(training)},'../stores/useAnnotationStore':{useAnnotationStore:store(annotation)},'../stores/useComputeStore':{useComputeStore:store(compute)},'../stores/useDraftReviewStore':{useDraftReviewStore:store(draftStore),draftReviewScope:s=>JSON.stringify([s.projectDir,s.project.active_labelset_id])},
 '../stores/useEvaluationStore':{useEvaluationStore:store(evaluation),currentEvaluationGuidanceSource:()=>sourceCurrent?{source:{folderPath:'/source',task:'classification'},jobId:'job1'}:null,computeSampleVerdict:(p,t)=>p.ground_truth==='NG'?(p.defect_score>=t?'CORRECT_NG':'ESCAPE'):(p.defect_score>=t?'OVERKILL':'CORRECT_OK'),isNormalLabel:l=>l==='OK',isDefectLabel:l=>l==='NG'},'../stores/workflowEvidence':{bumpWorkflowEvidence(){calls.push(['bump-evidence']);}},'../components/wizard/workflowReadiness':{focusWorkflowTarget:id=>calls.push(['focus',id])},'./datasetWorkflow':{workflowError:e=>String(e)},'../../services/datasetWorkflow':{workflowError:e=>String(e)},
 './api':{request,getApiPersistenceIdentity:()=> 'local',getProjectContextGeneration:()=>epoch,getProjectContext:()=>null,api:{dataset:{setImageClass:async body=>{calls.push(['class-write',body]);return writeResult('class',{class:body.class_name,class_source:'override',metadata:{},changed:true});}},labelSuggestions:{list:async()=>listRelease?new Promise(resolve=>{listRelease=resolve;}):{suggestions:drafts}}}}};
 function load(file){if(cache.has(file))return cache.get(file).exports;const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));cache.set(file,m);const original=m.require.bind(m);m.require=name=>Object.hasOwn(mocks,name)?mocks[name]:name.startsWith('.')?load(path.resolve(path.dirname(file),name)+'.ts'):original(name);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
 const runtime=load(path.join(__dirname,'assistantRuntime.ts')).runtimeToolDeps;
 return{runtime,project,data,training,compute,evaluation,annotation,calls,ready,epoch:()=>epoch++,blockOpen:()=>{opened=false;},staleEvaluation:()=>{sourceCurrent=false;},holdList:()=>{listRelease=true;},releaseList:()=>listRelease({suggestions:drafts}),holdWrite:name=>{let release;const promise=new Promise(resolve=>{release=resolve;});writes.set(name,{promise,release});},releaseWrite:(name,value)=>writes.get(name).release(value)};
}
test('preparation reads the real backend split/weights/device endpoint and names the assistant default-start scope',async()=>{
 const f=fixture(),result=await f.runtime.trainingPreparation();assert.equal(result.ready,true);assert.equal(result.settings.model,'dinov3_vits16');assert.equal(result.settings.scope,'assistant_default_start');assert.equal(result.execution_verified,false);assert.deepEqual(result.input.split_counts,{train:6,val:2,test:2});
 const request=f.calls.find(row=>row[1].startsWith('/api/training-workspace/readiness'));assert.equal(request[2],undefined);const query=new URL(request[1],'http://local').searchParams;assert.equal(query.get('device'),'cpu');assert.equal(query.get('preset'),'fast');assert.ok(!JSON.stringify(result).includes('/weights/'));
 f.calls.length=0;f.compute.selectedProfileId='server';await f.runtime.trainingPreparation();const remote=f.calls.find(row=>row[1].startsWith('/api/training-workspace/readiness'));assert.equal(remote[2].method,'POST');assert.equal(JSON.parse(remote[2].body).compute_profile_id,'server');assert.ok(!f.calls.some(row=>/training\/start|prefetch|import-weights/.test(row[1])));
});
test('settings update the existing stores without starting training and reject a stale call or local device on a server',async()=>{
 const f=fixture();await f.runtime.prefillTraining({preset:'precision',device:'mps',batch_size:12});assert.equal(f.training.preset,'precision');assert.equal(f.training.nextDevice,'mps');assert.equal(f.training.nextBatchSize,12);assert.deepEqual(f.calls,[]);
 await assert.rejects(f.runtime.prefillTraining({preset:'fast'},()=>false),e=>e.code==='context_changed');assert.equal(f.training.preset,'precision');f.compute.selectedProfileId='server';await assert.rejects(f.runtime.prefillTraining({device:'cpu'}),e=>e.code==='server_device');
});
test('opening a draft reuses pending ordering, guarded image opening and the scoped queue request including its order',async()=>{
 const f=fixture(),list=await f.runtime.pendingDrafts('uncertain',12);assert.deepEqual(list.drafts.map(row=>row.id),['low','high']);assert.equal(list.total,2);assert.ok(!JSON.stringify(list).includes('/source/'));
 const opened=await f.runtime.openPendingDraft(undefined,'uncertain');assert.equal(opened.id,'low');assert.deepEqual(f.calls.filter(row=>row[0]==='image'),[['image','low','/source/low.png']]);assert.deepEqual(f.calls.find(row=>row[0]==='requestOpen'),['requestOpen','low',undefined,'["/project","default"]','uncertain']);
});
test('failed editing save or a scope change during list fetch never opens the queue or adopts a draft',async()=>{
 const f=fixture();f.blockOpen();await assert.rejects(f.runtime.openPendingDraft('low','confident'),e=>e.code==='image_not_opened');assert.ok(!f.calls.some(row=>row[0]==='requestOpen'));
 const second=fixture();second.holdList();const pending=second.runtime.openPendingDraft(undefined,'uncertain');second.epoch();second.releaseList();await assert.rejects(pending,e=>e.code==='context_changed');assert.deepEqual(second.calls,[]);
});
test('evaluation guidance reads loaded source-bound verdicts and refuses an old source without reporting its counts',()=>{
 const f=fixture(),current=f.runtime.evaluationGuidance();assert.equal(current.loaded,true);assert.equal(current.current.escapes,1);assert.equal(current.conditions.evaluated_split,'val');f.staleEvaluation();const stale=f.runtime.evaluationGuidance();assert.equal(stale.loaded,false);assert.equal(stale.current,undefined);assert.deepEqual(f.calls,[]);
});
test('an unresolved class name is not counted as a known normal even when a verdict helper falls back to OK',()=>{
 const f=fixture();f.evaluation.testPredictions.push({ground_truth:'unconfirmed',defect_score:.1});
 const result=f.runtime.evaluationGuidance();assert.equal(result.current.known_normals,1);assert.equal(result.current.unresolved,1);assert.equal(result.recommendation,null);
});
test('late class writes do not update a different UI or refresh its evidence',async()=>{
 const f=fixture();f.holdWrite('class');let current=true;
 const pending=f.runtime.setImageClass({path:'/source/a.png',name:'a.png',currentClass:'OK',folderClass:'OK'},'NG',()=>current);
 current=false;f.releaseWrite('class',{class:'NG',class_source:'override',metadata:{},changed:true});
 await assert.rejects(pending,error=>error.code==='context_changed');
 assert.deepEqual(f.calls.map(row=>row[0]),['class-write'],'the A request was sent, but B UI and evidence stay untouched');
 const currentCase=fixture();await currentCase.runtime.setImageClass({path:'/source/a.png',name:'a.png'},'NG',()=>true);
 assert.deepEqual(currentCase.calls.map(row=>row[0]),['class-write','apply-class','refresh-annotations','bump-evidence']);
});
test('split and training outcomes recheck context before reading current-store errors or job ids',async()=>{
 for(const [name,change] of [['split','epoch'],['training','epoch'],['split','same-profile'],['training','same-profile']]){
  const f=fixture();f.holdWrite(name);const reads=[];
  if(change==='same-profile')f.compute.selectedProfileId='server';
  Object.defineProperty(name==='split'?f.data:f.training,name==='split'?'splitError':'jobId',{get(){reads.push(name);return name==='split'?'B error':'B job';}});
  const pending=name==='split'?f.runtime.applySplit(.7,.2,.1,()=>true):f.runtime.startTraining(()=>true);
  if(change==='same-profile')f.compute.profiles[0]={id:'server',host:'another-server'};else f.epoch();
  f.releaseWrite(name);await assert.rejects(pending,error=>error.code==='context_changed');assert.deepEqual(reads,[]);
 }
});
test('a mutation may consume its own settings or update metadata while preserving authority',async()=>{
 const f=fixture();let preflight=true;const check=()=>preflight;check.afterMutation=()=>true;
 f.holdWrite('training');const pending=f.runtime.startTraining(check);
 preflight=false;f.training.nextDevice=null;f.training.nextBatchSize=null;f.training.jobId='A_job';f.releaseWrite('training');
 assert.deepEqual(await pending,{jobId:'A_job'});
 const klass=fixture();let full=true;const classCheck=()=>full;classCheck.afterMutation=()=>true;klass.holdWrite('class');
 const applying=klass.runtime.setImageClass({path:'/source/a.png',name:'a.png'},'NG',classCheck);
 full=false;klass.releaseWrite('class',{class:'NG',metadata:{revision:2},changed:true});
 assert.equal((await applying).changed,true);assert.ok(klass.calls.some(row=>row[0]==='apply-class'));
});
