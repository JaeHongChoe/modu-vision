const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');

const nodes=tree=>Array.isArray(tree)?tree.flatMap(nodes):tree&&typeof tree==='object'?[tree,...nodes(tree.props?.children)]:[];
const textOf=node=>[node?.props?.children].flat(Infinity).map(child=>typeof child==='object'?textOf(child):child??'').join('');

// Render the production Studio with controlled store actions. No backend, browser,
// child comparison implementation, or actual held-out evaluation is executed.
function fixture({commonCohort=false,browser=false}={}){
 let index=0,dirty=true,tree,nextLoad=null;const slots=[],effects=[],calls=[];
 const hook=state=>Object.assign(selector=>selector?selector(state):state,{getState:()=>state});
 const react={
  useState(value){const i=index++;if(!(i in slots))slots[i]=typeof value==='function'?value():value;return[slots[i],value=>{slots[i]=typeof value==='function'?value(slots[i]):value;dirty=true;}];},
  useRef(value){const i=index++;return slots[i]??(slots[i]={current:value});},
  useMemo(compute){index++;return compute();},
  useEffect(effect,deps){const i=index++;if(JSON.stringify(slots[i]?.deps)!==JSON.stringify(deps)){const previous=slots[i];slots[i]={deps};effects.push(()=>{previous?.cleanup?.();slots[i].cleanup=effect();});}},
 };
 const project={language:'ko',task:'classification',projectDir:'/project',isProjectBusy:false,
  project:{id:'project-1',project_dir:'/project',task:'classification',source_dataset_dir:'/source',active_labelset_id:'default'},setStep(){}};
 const dataset={folderPath:'/source',datasetKey:'/source\0classification',isLoading:false,importError:null};
 const compute={selectedProfileId:null,profiles:[],transportRevision:1,isLoaded:true};
 const training={status:'idle',jobId:null,warmStartParentJobId:null};
 const handoff={step:4,family:'classification',jobId:'evaluation-model',selectionId:'evaluation-selection'};
 const evaluation={
  jobId:'evaluation-model',isLoading:false,executionEvidence:commonCohort?{
   common_cohort:{dataset_version_id:'version-1',cohort_sha256:'a'.repeat(64)},compute_profile_id:null,device:'cpu',execution_target:'local',
  }:null,
  remoteOperation:null,remoteRequest:null,remoteOperationError:null,metrics:{},classSemantics:null,confusionMatrix:null,
  selectedCell:null,testPredictions:[],filteredPredictions:[],selectedPrediction:null,confidenceThreshold:.5,
  heatmapOverlayBase64:null,heatmapLoading:false,isExportingReport:false,exportedReportPath:null,errorMessage:null,
  overkillAnalysis:null,sampleFilter:'all',isCalibrating:false,calibrationMessage:null,
  invalidateForDataChange(){},loadOverkillUnderkill:async()=>{},refreshRemoteOperation:async()=>{},cancelRemoteEvaluation:async()=>{},
  setSampleFilter(){},selectCell(){},clearCellSelection(){},selectPrediction(){},setConfidenceThreshold(){},
  exportReport:async()=>{},applyOptimalThreshold(){},calibrateZeroEscape:async()=>{},
  loadEvaluation(...args){
   calls.push(args);
   if(!nextLoad)return Promise.resolve();
   const pending=nextLoad;nextLoad=null;evaluation.isLoading=true;evaluation.errorMessage=null;dirty=true;
   return new Promise((resolve,reject)=>{
    pending.resolve=()=>{evaluation.isLoading=false;evaluation.errorMessage=null;dirty=true;resolve();};
    pending.reject=error=>{evaluation.isLoading=false;evaluation.errorMessage=error.message;dirty=true;reject(error);};
   });
  },
 };
 function ModelComparisonPanel(){return null;}
 const childNames=['OperatorGuidanceBanner','JargonTooltip','GuardrailBanner','ZeroEscapeTradeoffChart','SynchronizedDualViewport','DetectionEvaluationGrains','ModelDeploymentPanel','EvaluationHistoryPanel','EvaluationEvidencePanel','FieldReadinessPanel','SpecialistFamilyNote'];
 const children=Object.fromEntries(childNames.map(name=>[name,{[name]:()=>null}]));
 const file=path.join(__dirname,'EvaluationStudio.tsx'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);
 function loadLocal(name){const filename=path.resolve(__dirname,name+'.ts'),local=new Module(filename,module);local.require=key=>key==='react'?react:require(key);local._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);return local.exports;}
 const shared=loadLocal('../../stores/assistContext');
 m.require=name=>{
  if(name==='react')return react;
  if(name==='react/jsx-runtime')return{jsx:(type,props,key)=>({type,props,key}),jsxs:(type,props,key)=>({type,props,key}),Fragment:'fragment'};
  if(name==='lucide-react')return Object.fromEntries(['Activity','FileText','Download','ExternalLink','CheckCircle2','RefreshCw','Target','AlertTriangle','Zap','Loader2'].map(name=>[name,()=>null]));
  if(name.endsWith('/useProjectStore'))return{useProjectStore:hook(project)};
  if(name.endsWith('/useDatasetStore'))return{useDatasetStore:hook(dataset)};
  if(name.endsWith('/useComputeStore'))return{useComputeStore:hook(compute)};
  if(name.endsWith('/useTrainingStore'))return{useTrainingStore:hook(training)};
  if(name.endsWith('/useEvaluationStore'))return{useEvaluationStore:hook(evaluation),computeSampleVerdict:()=> 'REVIEW',isDefectLabel:()=>false,isNormalLabel:()=>false};
  if(name.endsWith('/useTaskHandoff'))return{useTaskHandoff:()=>handoff};
  if(name.endsWith('/assistContext'))return shared;
  if(name.endsWith('/useScopedError'))return loadLocal('../common/useScopedError');
  if(name.endsWith('/services/api'))return{
   api:{datasetVersions:{list:async()=>({versions:[]})},evaluation:{comparisonModels:async()=>({models:[{job_id:'evaluation-model',task:'classification'}]})}},
   resolveApiUrl:value=>value,getApiPersistenceIdentity:()=> 'local',getProjectContextGeneration:()=>1,
  };
  if(name.endsWith('/hostAdapter')){if(!browser)return{host:{kind:()=> 'desktop',can:()=>false}};const cache=new Map();function loadHost(filename){if(cache.has(filename))return cache.get(filename).exports;const local=new Module(filename,module);cache.set(filename,local);local.filename=filename;local.paths=Module._nodeModulePaths(path.dirname(filename));const req=local.require.bind(local);local.require=ref=>ref.startsWith('.')?loadHost(path.resolve(path.dirname(filename),ref)+'.ts'):req(ref);local._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);return local.exports;}return loadHost(path.resolve(__dirname,'../../services/hostAdapter.ts'));}
  if(name.endsWith('/ModelComparisonPanel'))return{ModelComparisonPanel};
  if(name.endsWith('/utils/plainLabels'))return require('../../utils/plainLabels.fixture.cjs');
  if(name.endsWith('/sampleConfidence'))return{accuracyNote:()=> '',smallTestWarning:()=>null};
  const child=children[name.split('/').at(-1)];
  if(child)return name.endsWith('/DetectionEvaluationGrains')?{...child,summarizeDetectionGrains:()=>null}:child;
  throw new Error('Unexpected Studio fixture dependency: '+name);
 };
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
 function render(){index=0;dirty=false;tree=m.exports.EvaluationStudio({});effects.splice(0).forEach(effect=>effect());}
 async function settle(){for(let i=0;i<12;i++){if(dirty)render();await new Promise(setImmediate);if(!dirty)return;}assert.fail('Studio fixture did not settle');}
 function refreshButton(){const button=nodes(tree).find(node=>node.type==='button'&&textOf(node)==='새로고침');assert(button,'actual Studio header refresh');return button;}
 return{
  calls,evaluation,settle,refreshButton,project,shared,treeNodes:()=>nodes(tree),
  rerender(){dirty=true;},
  close(){slots.forEach(slot=>slot?.cleanup?.());},
  refresh(){const button=refreshButton();assert.equal(button.props.disabled,false);return button.props.onClick();},
  defer(){assert.equal(nextLoad,null);const pending={};nextLoad=pending;return pending;},
  alerts:()=>nodes(tree).filter(node=>node.props?.role==='alert').map(textOf),
  comparisonRevision(){return nodes(tree).find(node=>node.type===ModelComparisonPanel).props.refreshRevision;},
 };
}

function observe(action){
 assert(action&&typeof action.then==='function','header action keeps its owned asynchronous completion');
 return action.then(()=>({status:'fulfilled'}),error=>({status:'rejected',error}));
}

test('Studio header owns deferred rejected legacy refresh, retains the store alert, and permits exact retry',async()=>{
 const f=fixture();await f.settle();assert.equal(f.calls.length,1);assert.equal(f.comparisonRevision(),0);
 const pending=f.defer(),outcome=observe(f.refresh());let settled=false;outcome.then(()=>{settled=true;});
 await f.settle();assert.equal(settled,false);assert.equal(f.refreshButton().props.disabled,true);assert.equal(f.comparisonRevision(),1);
 const failure=new Error('No held-out classification images are available for the controlled evaluation');pending.reject(failure);
 assert.deepEqual(await outcome,{status:'fulfilled'},'actual header handler must consume the store action rejection');
 await f.settle();assert.equal(f.evaluation.errorMessage,failure.message);assert.deepEqual(f.alerts(),[failure.message]);assert.equal(f.refreshButton().props.disabled,false);
 assert.deepEqual(f.calls[1],['evaluation-model',{folderPath:'/source',task:'classification'}]);
 const retry=f.defer(),retried=observe(f.refresh());await f.settle();assert.equal(f.comparisonRevision(),2);assert.equal(f.refreshButton().props.disabled,true);
 retry.resolve();assert.deepEqual(await retried,{status:'fulfilled'});await f.settle();
 assert.equal(f.calls.length,3);assert.deepEqual(f.calls[2],f.calls[1]);assert.deepEqual(f.alerts(),[]);assert.equal(f.refreshButton().props.disabled,false);
});

test('evaluation errors reach shared guidance and clear after recovery, source changes and unmount',async()=>{
 const f=fixture();try{
  await f.settle();
  f.evaluation.errorMessage='평가를 완료하지 못했습니다. 시험 이미지를 확인하세요.';f.rerender();await f.settle();
  assert.deepEqual(f.shared.assistPanelErrors(),[{source:'모델 평가',text:f.evaluation.errorMessage}]);
  f.evaluation.errorMessage=null;f.rerender();await f.settle();assert.deepEqual(f.shared.assistPanelErrors(),[]);
  f.evaluation.remoteOperationError='서버 연결을 확인하세요.';f.rerender();await f.settle();
  assert.equal(f.shared.assistPanelErrors()[0]?.text,'서버 연결을 확인하세요.');
  f.evaluation.remoteOperationError=null;f.project.projectDir='/other';f.rerender();await f.settle();
  assert.deepEqual(f.shared.assistPanelErrors(),[]);
  f.project.projectDir='/project';f.rerender();await f.settle();
  f.evaluation.errorMessage='다시 평가하세요.';f.rerender();await f.settle();
  assert.equal(f.shared.assistPanelErrors().length,1);
 }finally{f.close();}
 assert.deepEqual(f.shared.assistPanelErrors(),[]);
});

test('Studio header cohort refresh retains existing cohort and store error surfaces',async()=>{
 const f=fixture({commonCohort:true});await f.settle();const pending=f.defer(),outcome=observe(f.refresh());await f.settle();
 assert.equal(f.comparisonRevision(),1);assert.equal(f.refreshButton().props.disabled,true);
 const [job,options]=f.calls[1];assert.equal(job,'evaluation-model');assert.equal(options.evaluationDatasetVersionId,'version-1');
 assert.equal(options.device,'cpu');assert.equal(options.executionTarget,'local');assert.equal(options.isCurrent(),true);
 const failure=new Error('controlled selected-cohort refusal');pending.reject(failure);
 assert.deepEqual(await outcome,{status:'fulfilled'});await f.settle();
 assert.equal(f.evaluation.errorMessage,failure.message);assert.deepEqual(f.alerts(),[failure.message,failure.message]);
 assert.equal(f.refreshButton().props.disabled,false);assert.equal(f.calls.length,2);
});

test('browser HTML export downloads exact bytes and renders the returned report inside a restricted iframe',async()=>{
 const previousDocument=global.document,previousCreate=URL.createObjectURL;let captured,clicked=0;const anchor={click(){clicked++;},remove(){}};global.document={createElement:()=>anchor,body:{appendChild(){}}};URL.createObjectURL=blob=>{captured=blob;return 'blob:report-fixture';};
 try{const f=fixture({browser:true});await f.settle();const content='<!doctype html><html><body>검사 결과<script>parent.location="https://outside.invalid"</script></body></html>';f.evaluation.metrics={accuracy:1};f.evaluation.testPredictions=[{image_id:'one',file_name:'one.png',file_path:'/one.png',true_label:'ok',predicted_label:'ok',confidence:1}];f.evaluation.exportReport=async()=>{f.evaluation.exportedReportPath='/reports/one.html';f.evaluation.exportedReportContent=content;f.rerender();return '/reports/one.html';};f.rerender();await f.settle();const button=f.treeNodes().find(node=>node.type==='button'&&textOf(node)==='HTML 리포트 내보내기');assert.equal(button.props.disabled,false);await button.props.onClick();await f.settle();assert.equal(clicked,1);assert.equal(await captured.text(),content);assert.equal(anchor.download,'one.html');const frame=f.treeNodes().find(node=>node.type==='iframe');assert(frame);assert.equal(frame.props.sandbox,'');assert.equal(frame.props.referrerPolicy,'no-referrer');assert.match(frame.props.srcDoc,/default-src &apos;none&apos;/);assert(frame.props.srcDoc.endsWith(content));f.close();}finally{global.document=previousDocument;URL.createObjectURL=previousCreate;}
});
