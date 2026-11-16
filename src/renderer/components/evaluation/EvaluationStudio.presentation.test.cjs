const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes)
  : tree && typeof tree === 'object' ? [tree, ...nodes(tree.props?.children)] : [];
const text = tree => Array.isArray(tree) ? tree.map(text).join('')
  : tree && typeof tree === 'object' ? text(tree.props?.children)
  : tree == null || typeof tree === 'boolean' ? '' : String(tree);

function fixture({simple = true, task = 'classification'} = {}) {
  let cursor = 0, dirty = true, tree;
  const slots = [], effects = [], calls = [], focusRequests = [];
  const project = {language:'ko', viewMode:simple ? 'simple' : 'full', task, projectDir:'/project',
    project:{id:'p',project_dir:'/project',task,source_dataset_dir:'/source',active_labelset_id:'default'}, setStep() {calls.push('navigate');}};
  const dataset = {folderPath:'/source',datasetKey:`/source\0${task}`,isLoading:false,importError:null};
  const compute = {isLoaded:true,selectedProfileId:null,profiles:[],transportRevision:1};
  const training = {status:'completed',jobId:'job-raw-one',warmStartParentJobId:null};
  const samples = ['ESCAPE','OVERKILL','CORRECT_NG','CORRECT_OK','REVIEW'].map((verdict,i) => ({
    image_id:`image-${i}`,file_name:`image-${i}.png`,file_path:`/source/${i}.png`,thumbnail_url:'',
    ground_truth:i === 0 || i === 2 ? 'ng' : 'ok',predicted_class:'ok',confidence:.8,verdict,
  }));
  const state = {jobId:'job-raw-one',metrics:{accuracy:.4},classSemantics:null,confusionMatrix:null,
    testPredictions:samples,filteredPredictions:[],selectedPrediction:null,confidenceThreshold:.4,persistedThreshold:.75,
    sampleFilter:'saved_escape',selectedCell:{trueClass:'ng',predClass:'ok'},isLoading:false,
    executionEvidence:null,remoteOperation:null,remoteRequest:null,remoteOperationError:null,
    loadEvaluation:async() => {calls.push('evaluate');},loadOverkillUnderkill:async() => {},invalidateForDataChange(){},
    refreshRemoteOperation:async()=>{},cancelRemoteEvaluation:async()=>{calls.push('cancel');},
    clearCellSelection(){calls.push('clear cell');state.selectedCell=null;},
    setSampleFilter(value){calls.push(['filter',value]);state.sampleFilter=value;dirty=true;},
    selectPrediction:async()=>{calls.push('prediction');},setConfidenceThreshold(){calls.push('threshold');},
    selectCell(){calls.push('matrix');},exportReport:async()=>{calls.push('export');},
    applyOptimalThreshold(){calls.push('apply threshold');},calibrateZeroEscape:async()=>{calls.push('calibrate');},
    overkillAnalysis:null,isCalibrating:false,calibrationMessage:null,errorMessage:null,heatmapLoading:false,
  };
  const models = [1,2].map(i => ({job_id:i === 1 ? 'job-raw-one' : 'job-raw-two',task,
    preset:'fast',created_at:'2026-11-16T10:30:00Z',training_dataset_fingerprint:`fingerprint-${i}`}));
  const versions = [{id:'version-raw-one',name:'시험 원본',image_count:5,status:'verified',
    source_dataset_dir:'/source',task,labelset_id:'default'}];
  const react = {
    useState(value){const i=cursor++;if(!(i in slots))slots[i]=value;return[slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v;dirty=true;}];},
    useRef(value){const i=cursor++;return slots[i]??(slots[i]={current:value});},
    useMemo(fn){cursor++;return fn();},
    useEffect(fn,deps){const i=cursor++;if(JSON.stringify(slots[i]?.deps)!==JSON.stringify(deps)){
      const previous=slots[i];slots[i]={deps};effects.push(()=>{previous?.cleanup?.();slots[i].cleanup=fn();});}},
  };
  const hook = object => Object.assign(selector=>selector ? selector(object) : object,{getState:()=>object});
  function FieldReadinessPanel() {return null;}
  function SpecialistFamilyNote() {return null;}
  const file=path.join(__dirname,'EvaluationStudio.tsx'),mod=new Module(file,module);
  mod.filename=file;mod.paths=Module._nodeModulePaths(__dirname);const req=mod.require.bind(mod);
  function loadLocal(name) {
    const filename=path.resolve(__dirname,name+'.ts'),local=new Module(filename,module);
    local.filename=filename;local.paths=Module._nodeModulePaths(path.dirname(filename));
    local.require=key=>key==='react'?react:require(key);
    local._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);
    return local.exports;
  }
  mod.require=name=> {
    if(name==='react')return react;
    if(name==='react/jsx-runtime')return {jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props}),Fragment:'fragment'};
    if(name.endsWith('/useScopedError'))return loadLocal('../common/useScopedError');
    if(name.endsWith('/assistContext'))return loadLocal('../../stores/assistContext');
    if(name.endsWith('/useProjectStore'))return {useProjectStore:hook(project)};
    if(name.endsWith('/useDatasetStore'))return {useDatasetStore:hook(dataset)};
    if(name.endsWith('/useComputeStore'))return {useComputeStore:hook(compute)};
    if(name.endsWith('/useTrainingStore'))return {useTrainingStore:hook(training)};
    if(name.endsWith('/useEvaluationStore'))return {useEvaluationStore:hook(state),computeSampleVerdict:p=>p.verdict,isDefectLabel:v=>v==='ng',isNormalLabel:v=>v==='ok'};
    if(name.endsWith('/useTaskHandoff'))return {useTaskHandoff:()=>null};
    if(name.endsWith('/workflowReadiness'))return {focusWorkflowTarget:(target,isCurrent)=>focusRequests.push({target,isCurrent})};
    if(name.endsWith('/services/api'))return {api:{datasetVersions:{list:async()=>({versions})},evaluation:{comparisonModels:async()=>({models})}},resolveApiUrl:x=>x,getApiPersistenceIdentity:()=> 'local',getProjectContextGeneration:()=>1};
    if(name.endsWith('/hostAdapter'))return {host:{kind:()=> 'desktop',can:()=>false}};
    if(name.endsWith('/utils/plainLabels'))return {scoreUnitLabel:()=> '확률(0~1)'};
    if(name.endsWith('/sampleConfidence'))return {accuracyNote:()=> '현재 시험 사진 기준',smallTestWarning:()=>null};
    if(name.endsWith('/FieldReadinessPanel'))return {FieldReadinessPanel};
    if(name.endsWith('/SpecialistFamilyNote'))return {SpecialistFamilyNote};
    if(name.startsWith('.'))return new Proxy({},{get:(_,key)=>key==='summarizeDetectionGrains'?()=>null:()=>null});
    return req(name);
  };
  mod._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
  const render=()=>{cursor=0;dirty=false;tree=mod.exports.EvaluationStudio();effects.splice(0).forEach(effect=>effect());};
  async function settle(){for(let i=0;i<10;i++){if(dirty)render();await new Promise(setImmediate);if(!dirty)return;}assert.fail('fixture did not settle');}
  function ancestors(target, node, trail=[]) {
    if(arguments.length===1)node=tree;
    if(Array.isArray(node)){for(const child of node){const result=ancestors(target,child,trail);if(result)return result;}return null;}
    if(!node||typeof node!=='object')return null;
    if(node===target)return trail;
    return node.props?.children === undefined ? null : ancestors(target,node.props.children,[...trail,node]);
  }
  return {state,project,models,versions,calls,focusRequests,exports:mod.exports,FieldReadinessPanel,SpecialistFamilyNote,settle,
    all:()=>nodes(tree),ancestors,render,rerender(){dirty=true;},close(){slots.forEach(slot=>slot?.cleanup?.());}};
}

test('model and dataset options use readable distinct names while retaining exact IDs as values',async()=>{
  const f=fixture();try{await f.settle();
    const options=nodes(f.all().find(n=>n.props?.['aria-label']==='평가 모델')).filter(n=>n.type==='option'&&n.props.value);
    assert.deepEqual(options.map(n=>n.props.value),['job-raw-one','job-raw-two']);
    assert.match(text(options[0]),/모델 1.*사진 분류.*빠른 학습.*2026/);
    assert.notEqual(text(options[0]),text(options[1]));assert(!text(options).includes('job-raw-'));
    const version=nodes(f.all().find(n=>n.props?.['aria-label']==='평가 데이터 버전')).find(n=>n.props?.value==='version-raw-one');
    assert.match(text(version),/시험 원본.*5장.*확인됨/);assert(!text(version).includes('verified'));
    assert.match(text(f.all().find(n=>n.props?.['aria-label']==='선택한 시험 이미지 평가')),/선택한 시험 이미지 평가/);
  }finally{f.close();}
});

test('plain model labels tolerate missing timestamps and preserve each specialized task name without exposing IDs',()=>{
  const f=fixture();assert.equal(typeof f.exports.evaluationModelOptionLabel,'function');
  const labels={classification:'사진 분류',detection:'객체 검출',segmentation:'영역 분할',anomaly:'이상 탐지',
    patch_classification:'부분 사진 분류',ocr:'글자 읽기',rotated_detection:'회전 객체 검출',enhancement:'화질 개선',rotation:'방향 보정'};
  for(const [task,label] of Object.entries(labels)){
    const model={job_id:'raw-private-id',task,preset:'precision',created_at:null};
    const result=f.exports.evaluationModelOptionLabel(model,3,'ko');assert.match(result,new RegExp(label));
    assert.match(result,/모델 4.*정밀 학습.*날짜 기록 없음/);assert(!result.includes(model.job_id));assert.equal(model.job_id,'raw-private-id');
  }
  assert.match(f.exports.evaluationModelOptionLabel({job_id:'raw',task:'classification',preset:null,created_at:'invalid-date'},0,'ko'),/학습 방식 기록 없음.*날짜 기록 없음/);
  assert.match(f.exports.evaluationModelOptionLabel({task:'classification',preset:'legacy-custom',created_at:null},0,'ko'),/다른 학습 방식/,'an unknown recorded style is not a missing record');
});

test('preview error summary buttons clear only the selected cell and select the existing preview filter',async()=>{
  const f=fixture();try{await f.settle();const initialLoads=f.calls.filter(x=>x==='evaluate').length;
    for(const [label,filter] of [['미리보기 미검 이미지 보기','escape'],['미리보기 과검 이미지 보기','overkill']]){
      const button=f.all().find(n=>n.type==='button'&&n.props?.['aria-label']===label);assert(button,`${label} button`);
      assert.equal(button.props.disabled,false);button.props.onClick();await f.settle();
      assert.equal(f.state.sampleFilter,filter);assert.equal(f.state.selectedCell,null);
      assert.deepEqual(f.calls.slice(-2),['clear cell',['filter',filter]]);
    }
    assert.equal(f.calls.filter(x=>x==='evaluate').length,initialLoads);
    assert(!f.calls.some(x=>['threshold','apply threshold','calibrate','export','navigate'].includes(x)));
    assert.equal(f.state.confidenceThreshold,.4);assert.equal(f.state.persistedThreshold,.75);
  }finally{f.close();}
});

test('preview summary reveals the photo list through the existing scoped focus owner and abandons a replaced selection',async()=>{
  const f=fixture();try{await f.settle();
    const button=f.all().find(n=>n.type==='button'&&n.props?.['aria-label']==='미리보기 미검 이미지 보기');
    button.props.onClick();await f.settle();
    assert.equal(f.focusRequests.length,1);
    assert.equal(f.focusRequests[0].target,'evaluation-photo-review');
    assert.equal(f.focusRequests[0].isCurrent(),true);
    const target=f.all().find(n=>n.props?.id==='evaluation-photo-review');
    assert.equal(target?.props.tabIndex,-1,'the focus target must be an actual box, not the display:contents panel');
    assert.match(text(target),/평가 이미지 목록/);
    f.all().find(n=>n.props?.['aria-label']==='평가 모델').props.onChange({target:{value:'job-raw-two'}});
    assert.equal(f.focusRequests[0].isCurrent(),false,'queued focus cannot follow a replaced model selection');
    assert.equal(f.state.persistedThreshold,.75);
    assert.ok(!f.calls.includes('navigate'));
  }finally{f.close();}
});

test('summary identifies preview versus saved criterion and unresolved image verdicts',async()=>{
  const f=fixture();try{await f.settle();const criterion=f.all().find(n=>n.props?.['aria-label']==='평가 결과 기준');
    assert(criterion);assert.match(text(criterion),/미리보기.*0\.40/);assert.match(text(criterion),/저장된.*0\.75/);
    assert.match(text(criterion),/저장된 미검 목록/);
    assert.match(text(f.all().find(n=>n.props?.['aria-label']==='평가 결과 한눈에 보기')),/확인 필요 1장/);
    const saved=f.all().find(n=>n.props?.id==='workflow-missed-defects');assert(saved);assert.equal(saved.props.disabled,false);
  }finally{f.close();}
});

test('simple-view details retain technical IDs and stats while current errors and remote lifecycle stay outside',async()=>{
  const f=fixture();try{f.state.remoteOperation={op_id:'remote-raw-id',state:'running',cancel_requested_at:'requested',cancel_acknowledged_at:null,worker_exit_confirmed:false};f.state.remoteOperationError='취소 연결을 확인하세요';await f.settle();
    for(const label of ['평가 기술 근거','평가 고급 통계와 도구']){
      const detail=f.all().find(n=>n.type==='details'&&n.props?.['aria-label']===label);assert(detail,label);assert.equal(detail.props.open,false);
    }
    const technical=f.all().find(n=>n.props?.['aria-label']==='평가 기술 근거');assert.match(text(technical),/job-raw-one/);assert.match(text(technical),/remote-raw-id/);
    const lifecycle=f.all().find(n=>n.props?.['aria-label']==='원격 평가 상태');assert(lifecycle);
    assert.match(text(lifecycle),/실행 중/);assert.match(text(lifecycle),/취소요청 기록됨/);assert.match(text(lifecycle),/ACK 미확인/);assert.match(text(lifecycle),/worker 종료 미확인/);
    const cancel=f.all().find(n=>n.props?.['aria-label']==='원격 평가 취소');assert(cancel);assert.equal(cancel.props.disabled,true);
    const error=f.all().find(n=>n.props?.role==='alert'&&text(n).includes('취소 연결'));
    for(const target of [lifecycle,cancel,error,f.all().find(n=>n.type===f.SpecialistFamilyNote)]){
      assert(target);assert(!f.ancestors(target).some(n=>n.type==='details'&&!n.props.open),'critical current state stays visible');
    }
  }finally{f.close();}
});

test('full view leaves the same advanced tools mounted and open',async()=>{
  const f=fixture({simple:false});try{await f.settle();const tools=f.all().find(n=>n.props?.['aria-label']==='평가 고급 통계와 도구');assert(tools);assert.equal(tools.props.open,true);
    assert(f.all().some(n=>n.type===f.FieldReadinessPanel));assert(f.all().some(n=>n.type===f.SpecialistFamilyNote));
  }finally{f.close();}
});

test('data and remote-state presentation keeps unknown metadata honest',()=>{
  const f=fixture();
  assert.match(f.exports.evaluationDatasetOptionLabel({name:'',image_count:0,status:'not_checked'},0,'ko'),/자료 1.*이름 없는 이미지 묶음.*0장.*확인 전/);
  assert.match(f.exports.evaluationDatasetOptionLabel({name:'사진',image_count:NaN,status:'not_checked'},1,'ko'),/자료 2.*장수 기록 없음/);
  for(const [state,expected] of Object.entries({running:'실행 중',queued:'대기 중',cancel_requested:'취소 요청됨',cancelled:'취소됨',completed:'실행 완료',failed:'실패',foreign:'상태 확인 필요'})){
    assert.equal(f.exports.remoteEvaluationStateLabel(state,'ko'),expected);
  }
});

test('loading disables summary filters without folding or removing remote cancellation state',async()=>{
  const f=fixture();try{f.state.isLoading=true;f.state.remoteOperation={op_id:'op',state:'running',cancel_requested_at:null,cancel_acknowledged_at:null,worker_exit_confirmed:false};await f.settle();
    for(const label of ['미리보기 미검 이미지 보기','미리보기 과검 이미지 보기'])assert.equal(f.all().find(n=>n.props?.['aria-label']===label).props.disabled,true);
    const cancel=f.all().find(n=>n.props?.['aria-label']==='원격 평가 취소');assert.equal(cancel.props.disabled,false);await cancel.props.onClick();
    assert(f.calls.includes('cancel'));assert(!f.ancestors(cancel).some(n=>n.type==='details'&&!n.props.open));
  }finally{f.close();}
});

test('calibration pending and its exact feedback stay visible outside advanced statistics',async()=>{
  const f=fixture();try{f.state.isCalibrating=true;f.state.calibrationMessage='기준 점수 계산 응답 대기';await f.settle();
    const feedback=f.all().find(n=>n.props?.['aria-label']==='판정 기준 처리 상태');assert(feedback);
    assert.match(text(feedback),/처리 중/);assert.match(text(feedback),/기준 점수 계산 응답 대기/);
    assert(!f.ancestors(feedback).some(n=>n.type==='details'&&!n.props.open));
    f.state.isCalibrating=false;f.state.calibrationMessage='기준 적용 실패 · 저장된 값은 유지했습니다';f.rerender();await f.settle();
    const failure=f.all().find(n=>n.props?.['aria-label']==='판정 기준 처리 상태');assert.match(text(failure),/기준 적용 실패 · 저장된 값은 유지했습니다/);
    assert(!f.ancestors(failure).some(n=>n.type==='details'&&!n.props.open));
  }finally{f.close();}
});
