const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');

function load(file, mocks = {}) {
  const name = path.resolve(__dirname, file);
  const m = new Module(name, module);
  m.filename = name;
  m.paths = Module._nodeModulePaths(__dirname);
  const req = m.require.bind(m);
  m.require = key => Object.hasOwn(mocks, key) ? mocks[key]
    : key.endsWith('/classSemantics') || key.endsWith('/hostAdapter') || key.endsWith('/browserSession') ? load(path.resolve(path.dirname(name), key) + '.ts') : req(key);
  m._compile(ts.transpileModule(fs.readFileSync(name, 'utf8'), {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, name);
  return m.exports;
}

const semantics = { version: 1, roles: { OK: 'defect', scratch: 'normal', pending: 'unknown' } };
const sample = (id, truth, prediction, confidence = 0.9) => ({
  image_id: id, file_name: `${id}.png`, file_path: `/source/${id}.png`,
  ground_truth: truth, predicted_class: prediction, confidence, thumbnail_url: '',
});
const samples = [sample('escape', 'OK', 'scratch'), sample('overkill', 'scratch', 'OK')];
function fixture(task = 'classification') {
  const training = { isCurrentData: true, status: 'completed', jobId: 'job' };
  const results = { job_id: 'job', class_semantics: semantics, metrics: { accuracy: 0 }, test_predictions: samples };
  const api = { evaluation: {
    getResults: async () => results,
    calibrateZeroEscape: async body => (await fetch('http://test/api/evaluation/zero-escape-calibrate', {method:'POST',body:JSON.stringify(body)})).json(),
    getOverkillUnderkill: async () => ({ optimal_threshold: 0.2 }),
    getHeatmap: async () => ({ overlay_base64: null }),
  } };
  const m = load('useEvaluationStore.ts', {
    './useProjectStore': {useProjectStore:{getState:()=>({projectDir:'/project',task,isProjectBusy:false,project:{id:'A',project_dir:'/project',task,source_dataset_dir:'/source',active_labelset_id:'default'}})}},
    './useDatasetStore': {useDatasetStore:{getState:()=>({folderPath:'/source',datasetKey:`/source\0${task}`,hasSelectedFolder:true,isLoading:false,importError:null})}},
    './useTrainingStore': { useTrainingStore: { getState: () => training } },
    '../services/api': { api, getApiBaseUrl: async () => 'http://test', getProjectContext:()=>null,getProjectContextGeneration:()=>0,getApiPersistenceIdentity:()=> 'local' },
  });
  return { ...m, state: () => m.useEvaluationStore.getState(), api, training, results };
}

test('heldout anomaly results cannot send a threshold replacement even for probability scores', async () => {
  const f = fixture('anomaly');
  await f.state().loadEvaluation('job', { folderPath: '/source', task: 'anomaly' });
  f.useEvaluationStore.setState({metrics:{evaluated_split:'test',anomaly_mode:'classification',score_spec:{domain:'probability'}}});
  const previousFetch = global.fetch;
  let posts = 0;
  global.fetch = async () => { posts++; return {ok:true,json:async()=>({optimal_threshold:.2})}; };
  try {
    await f.state().calibrateZeroEscape();
    assert.equal(posts, 0);
    assert.equal(f.state().confidenceThreshold, .5);
    assert.equal(f.state().calibrationSuccess, false);
    assert.match(f.state().calibrationMessage, /시험/);
  } finally { global.fetch = previousFetch; }
});

test('recorded roles control fallback verdicts and override numeric and normal aliases', () => {
  const f = fixture();
  assert.equal(f.computeSampleVerdict(samples[0], 0.5, null, semantics.roles), 'ESCAPE');
  assert.equal(f.computeSampleVerdict(samples[1], 0.5, null, semantics.roles), 'OVERKILL');
  assert.equal(f.isDefectLabel(0, { 0: 'defect' }), true);
  assert.equal(f.isNormalLabel('scratch', semantics.roles), true);
  assert.equal(f.isNormalLabel('OK', semantics.roles), false);
  assert.equal(f.isNormalLabel('pending', semantics.roles), false);
  assert.equal(f.isNormalLabel(undefined, semantics.roles), false);
  assert.equal(f.computeSampleVerdict(sample('unknown', undefined, 'scratch'), 0.5, null, semantics.roles), 'REVIEW');
});

test('actual evaluation store retains response roles for filtering and clears them on scope or job failure', async () => {
  const f = fixture();
  await f.state().loadEvaluation('job', { folderPath: '/source', task: 'classification' });
  assert.deepEqual(f.state().classSemantics, semantics);
  f.state().setSampleFilter('escape');
  assert.deepEqual(f.state().filteredPredictions.map(p => p.image_id), ['escape']);
  f.state().setSampleFilter('overkill');
  assert.deepEqual(f.state().filteredPredictions.map(p => p.image_id), ['overkill']);
  f.state().invalidateForDataChange();
  assert.equal(f.state().classSemantics, null);
  await f.state().loadEvaluation('job', { folderPath: '/source', task: 'classification' });
  delete f.results.class_semantics;
  await f.state().loadEvaluation('job', { folderPath: '/source', task: 'classification' });
  assert.equal(f.state().classSemantics, null, 'legacy response cannot reuse previous model roles');
  f.results.class_semantics = semantics;
  await f.state().loadEvaluation('job', { folderPath: '/source', task: 'classification' });
  f.api.evaluation.getResults = async () => { throw new Error('model unavailable'); };
  await assert.rejects(() => f.state().loadEvaluation('job', { folderPath: '/source', task: 'classification' }), /unavailable/);
  assert.equal(f.state().classSemantics, null);
  f.useEvaluationStore.setState({ classSemantics: semantics });
  f.training.status = 'running';
  await f.state().loadEvaluation();
  assert.equal(f.state().classSemantics, null, 'an ineligible model clears its recorded roles');
});

test('threshold calibration requires a real normal role and respects explicit alias overrides', async () => {
  const f = fixture();
  await f.state().loadEvaluation('job', { folderPath: '/source', task: 'classification' });
  f.useEvaluationStore.setState({ testPredictions: [sample('ng', 'OK', 'OK'), sample('pending', 'pending', 'OK'), sample('missing', undefined, 'OK')] });
  f.state().applyOptimalThreshold();
  assert.equal(f.state().confidenceThreshold, 0.5);
  const previousFetch = global.fetch;
  const requests = [];
  global.fetch = async (url, options) => { requests.push({ url, body: JSON.parse(options.body) }); return {
    ok: true, json: async () => ({ optimal_threshold: 0.2 }),
  }; };
  try {
    await f.state().calibrateZeroEscape();
    assert.equal(requests.length, 0, 'missing truth cannot supply the required normal cohort');
    f.useEvaluationStore.setState({ testPredictions: samples, isLoading:true });
    await f.state().calibrateZeroEscape();
    assert.equal(requests.length,0,'a pending result cannot calibrate stale samples');
    f.useEvaluationStore.setState({isLoading:false});
    await f.state().calibrateZeroEscape();
    assert.equal(requests.length, 1, 'scratch is normal by the recorded role even though its alias sounds defective');
    assert.equal(requests[0].body.job_id, 'job');
    assert.equal(f.state().confidenceThreshold, 0.2);
  } finally { global.fetch = previousFetch; f.state().invalidateForDataChange(); }
});

function renderStudio(storeState, options = {}) {
  const jsx = (type, props) => ({ type, props: props || {}, children: [props?.children].flat(Infinity).filter(v => v !== null && v !== undefined && v !== false) });
  let cursor = 0; const slots = [];
  const react = { useEffect() {}, useRef: value => ({current:value}), useMemo: fn => fn(), useState: initial => {const i=cursor++;if(!(i in slots))slots[i]=initial;return [slots[i], value => {slots[i]=typeof value==='function'?value(slots[i]):value;}];} };
  const project = { language: 'ko', task: 'classification', projectDir: '/project', setStep() {}, ...options };
  const dataset = { folderPath: '/source', datasetKey: '/source\0classification', isLoading: false, importError: null };
  const evaluation = fixture();
  const state = { ...evaluation.state(), ...storeState };
  const useEvaluationStore = () => state;
  useEvaluationStore.getState = () => state;
  const m = load('../components/evaluation/EvaluationStudio.tsx', {
    'react': react, 'react/jsx-runtime': { jsx, jsxs: jsx }, 'lucide-react': {},
    '../common/useScopedError': load('../components/common/useScopedError.ts', { react }),
    '../../stores/assistContext': load('assistContext.ts'),
    '../training/useTaskHandoff': { useTaskHandoff: () => null },
    '../../stores/useProjectStore': { useProjectStore: Object.assign(sel => sel ? sel(project) : project, { getState: () => project }) },
    '../../stores/useDatasetStore': { useDatasetStore: sel => sel(dataset) },
    '../../stores/useTrainingStore': { useTrainingStore: Object.assign(sel => sel({ status: 'idle' }), {getState:()=>({status:'idle'})}) },
    '../../stores/useComputeStore': {useComputeStore: Object.assign(() => ({profiles:[],selectedProfileId:null,transportRevision:0}), {getState:()=>({selectedProfileId:null})})},
    '../../stores/useEvaluationStore': { ...evaluation, useEvaluationStore },
    '../../services/api': { resolveApiUrl: value => value, getApiPersistenceIdentity:()=> 'local', getProjectContextGeneration:()=>0 },
    '../common/OperatorGuidanceBanner': {}, '../common/JargonTooltip': {}, '../common/GuardrailBanner': {},
    './ZeroEscapeTradeoffChart': {}, './SynchronizedDualViewport': {},
    './DetectionEvaluationGrains': {}, './ModelComparisonPanel': {}, './ModelDeploymentPanel': {},
    './EvaluationHistoryPanel': {}, './EvaluationEvidencePanel': {},
    '../../utils/plainLabels': require('../utils/plainLabels.fixture.cjs'), './sampleConfidence': { accuracyNote: () => '', smallTestWarning: () => null }, '../common/FieldReadinessPanel': {}, '../common/SpecialistFamilyNote': {},
  });
  const render = () => {cursor=0;return m.EvaluationStudio();};
  const tree = render();
  const nodes = [];
  const walk = node => { if (node && typeof node === 'object') { nodes.push(node); node.children?.forEach(walk); } };
  walk(tree);
  const text = node => typeof node === 'string' || typeof node === 'number' ? String(node) : (node?.children || []).map(text).join('');
  return { nodes, text, rerender() {nodes.length=0;walk(render());return this;} };
}

test('rendered evaluation cells, badges, and calibration controls use the recorded class roles', () => {
  const view = renderStudio({
    jobId: 'job', classSemantics: semantics, testPredictions: samples, filteredPredictions: samples,
    selectedPrediction: samples[0],
    confusionMatrix: { classes: ['OK', 'scratch'], matrix: [[0, 1], [1, 0]] },
  });
  const cells = view.nodes.filter(n => n.type === 'td' && n.props.onClick);
  assert.match(cells[1].props.className, /bg-\[#450A0A\]/, 'OK defect → scratch normal is an escape cell');
  const badges = view.nodes.filter(n => n.props.verdict);
  assert.deepEqual(badges.map(n => n.props.verdict), ['ESCAPE', 'OVERKILL']);
  const calibration = view.nodes.filter(n => n.type === 'button' && ['평가 임계값 적용', '검증 임계값 적용'].includes(view.text(n)));
  assert.ok(calibration.length);
  assert.ok(calibration.every(n => !n.props.disabled));
  const unresolved = renderStudio({ jobId: 'job', classSemantics: semantics,
    testPredictions: [sample('ng', 'OK', 'OK'), sample('pending', 'pending', 'OK')], filteredPredictions: [] });
  assert.equal(unresolved.nodes.find(n => n.type === 'button' && unresolved.text(n) === '평가 임계값 적용').props.disabled, true);
});

test('fixed test-cohort result keeps its matrix visible and disables unsupported reporting and calibration',()=>{
 const view=renderStudio({jobId:'job',metrics:{accuracy:.25},classSemantics:semantics,testPredictions:samples,
  filteredPredictions:samples,selectedPrediction:samples[0],executionEvidence:{common_cohort:{dataset_version_id:'v1'}},
  confusionMatrix:{classes:['OK','scratch'],matrix:[[0,1],[1,0]]}});
 const buttons=view.nodes.filter(n=>n.type==='button'&&['HTML 리포트 내보내기','JSON','평가 임계값 적용','검증 임계값 적용'].includes(view.text(n)));
 assert.equal(buttons.length,4);assert(buttons.every(n=>n.props.disabled));
 assert(view.nodes.some(n=>n.type==='td'&&n.props.onClick),'fixed result matrix is still inspectable');
 assert.match(buttons.find(n=>view.text(n)==='JSON').props.title,/코호트/);
});


test('loading a replacement result disables report and calibration actions on stale visible samples',()=>{
 const view=renderStudio({jobId:'job',isLoading:true,metrics:{accuracy:.25},classSemantics:semantics,
  testPredictions:samples,filteredPredictions:samples,selectedPrediction:samples[0],executionEvidence:null,
  confusionMatrix:{classes:['OK','scratch'],matrix:[[0,1],[1,0]]}});
 const buttons=view.nodes.filter(n=>n.type==='button'&&['HTML 리포트 내보내기','JSON','평가 임계값 적용','검증 임계값 적용'].includes(view.text(n)));
 assert.equal(buttons.length,4);assert(buttons.every(n=>n.props.disabled));
});

test('distance evaluation inherits calibrated threshold and sends a bound heatmap override',async()=>{
 const f=fixture(),spec={domain:'distance',unit:'mahalanobis_distance',direction:'higher_is_defect',calibration_id:'saved-model',threshold:8};
 f.results.metrics={active_threshold:8,score_spec:spec};
 f.results.test_predictions=[{...sample('raw','scratch','scratch',4),defect_score:4,score_spec:spec}];
 const calls=[];f.api.evaluation.getHeatmap=async(...args)=>{calls.push(args);return {overlay_base64:null};};
 await f.state().loadEvaluation('job');
 assert.equal(f.state().confidenceThreshold,8);
 f.state().setConfidenceThreshold(9);
 await f.state().updateHeatmap();
 assert.deepEqual(calls.at(-1)[4],{...spec,threshold:9});
 assert.equal(f.computeSampleVerdict(f.results.test_predictions[0],9,null,semantics.roles),'CORRECT_OK');
});

for(const row of [
 {name:'legacy probability baseline',metrics:{},want:.5},
 {name:'probability zero',metrics:{active_threshold:0},want:0},
 {name:'distance above one',metrics:{active_threshold:8,score_spec:{domain:'distance'}},want:8},
 {name:'missing anomaly authority',task:'anomaly',metrics:{},want:null},
 {name:'missing distance authority',metrics:{score_spec:{domain:'distance'}},want:null},
 ...[null,-1,1.1,NaN,Infinity,'0.2'].map(value=>({name:'invalid active '+value,metrics:{active_threshold:value},want:null})),
 {name:'recommendation metric is not saved authority',task:'anomaly',metrics:{optimal_threshold:.2},want:null},
 {name:'legacy saved calibration',metrics:{active_threshold:.5},saved:{zero_underkill_calibrated:true,optimal_threshold:.2},want:.2},
 {name:'saved evidence agrees',metrics:{active_threshold:.5},saved:{zero_underkill_calibrated:true,optimal_threshold:0,calibration_evidence:{threshold:0}},want:0},
 {name:'saved evidence conflicts',metrics:{active_threshold:.5},saved:{zero_underkill_calibrated:true,optimal_threshold:.2,calibration_evidence:{threshold:.3}},want:null},
 {name:'saved evidence is malformed',metrics:{active_threshold:.5},saved:{zero_underkill_calibrated:true,optimal_threshold:.2,calibration_evidence:{}},want:null},
 {name:'saved calibration threshold invalid',metrics:{active_threshold:.5},saved:{zero_underkill_calibrated:true,optimal_threshold:2},want:null},
])test('accepted evaluation resolves '+row.name,async()=>{
 const f=fixture(row.task);Object.assign(f.results,{task:row.task||'classification',metrics:row.metrics,...row.saved});
 await f.state().loadEvaluation('job');assert.equal(f.state().persistedThreshold,row.want);
 if(row.want!==null)assert.equal(f.state().confidenceThreshold,row.want);
 f.state().invalidateForDataChange();assert.equal(f.state().persistedThreshold,null);
});

test('rendered preview mismatch explains the saved readiness threshold while preview badges remain interactive',()=>{
 const view=renderStudio({jobId:'job',persistedThreshold:.5,confidenceThreshold:.2,classSemantics:semantics,testPredictions:[{...samples[0],defect_score:.3}],filteredPredictions:[{...samples[0],defect_score:.3}]});
 assert.match(view.text(view.nodes[0]),/미리보기.*0\.20.*저장.*0\.50/);
 assert.equal(view.nodes.find(n=>n.props.verdict).props.verdict,'CORRECT_NG');
 const aligned=renderStudio({jobId:'job',persistedThreshold:.2,confidenceThreshold:.2});assert.doesNotMatch(aligned.text(aligned.nodes[0]),/미리보기.*저장/);
});

test('rendered unknown saved authority never formats a missing threshold as a number',()=>{
 for(const persistedThreshold of [undefined,null])assert.doesNotThrow(()=>renderStudio({jobId:'job',persistedThreshold,confidenceThreshold:.2}));
});

// Exercise the actual footer destination through the rendered Studio handler and real store.
function focusSavedMisses(view){
 const target=view.nodes.find(node=>node.props.id==='workflow-missed-defects');
 assert.ok(target,'saved-miss destination is rendered');
 const previous={document:global.document,requestAnimationFrame:global.requestAnimationFrame,HTMLElement:global.HTMLElement};
 const element={parentElement:null,getAttribute:key=>target.props[key]===undefined?null:String(target.props[key]),hasAttribute:key=>Object.hasOwn(target.props,key),click:()=>target.props.onClick(),scrollIntoView(){},focus(){global.document.activeElement=element;}};
 try{global.document={getElementById:id=>id==='workflow-missed-defects'?element:null,activeElement:null};global.requestAnimationFrame=fn=>fn();global.HTMLElement=class{};load('../components/wizard/workflowReadiness.ts').focusWorkflowTarget('workflow-missed-defects');}
 finally{for(const [key,value] of Object.entries(previous)){if(value===undefined)delete global[key];else global[key]=value;}}
}
for(const alreadySelected of [false,true])test('footer saved-miss destination reveals accepted saved misses with preview escape '+(alreadySelected?'already selected':'unselected'),async()=>{
 const f=fixture();f.results.metrics={active_threshold:.5};f.results.test_predictions=[{...sample('saved-miss','OK','OK'),defect_score:.3},{...sample('normal','scratch','scratch'),defect_score:.1}];
 await f.state().loadEvaluation('job',{folderPath:'/source',task:'classification'});f.state().setConfidenceThreshold(.2);
 f.useEvaluationStore.setState({overkillAnalysis:{optimal_threshold:.1,sample_details:[{image_id:'saved-miss',is_defect:false,defect_score:0}]}});
 if(alreadySelected)f.state().setSampleFilter('escape');
 const enteredHeatmaps=[];f.api.evaluation.getHeatmap=async(...args)=>{enteredHeatmaps.push(args);return{};};
 focusSavedMisses(renderStudio(f.state()));
 assert.equal(enteredHeatmaps.at(-1)[2],.5,'entering review refreshes heatmap even when selected image stays the same');
 assert.equal(f.state().overkillAnalysis.optimal_threshold,.1,'review preserves GET recommendation data');
 assert.deepEqual(f.state().filteredPredictions.map(row=>row.image_id),['saved-miss']);
 assert.equal(f.state().confidenceThreshold,.2,'review does not replace preview threshold');
 f.state().computeFilteredList();await f.state().loadOverkillUnderkill();f.state().computeFilteredList();
 assert.deepEqual(f.state().filteredPredictions.map(row=>row.image_id),['saved-miss'],'recomputation and arriving analysis cannot erase saved review');
 const saved=renderStudio(f.state());assert.match(saved.text(saved.nodes[0]),/저장된.*0\.50/);assert.doesNotMatch(saved.text(saved.nodes[0]),/현재 검증 표본에서 미검 0건/);
 assert.equal(saved.nodes.find(node=>node.props.verdict).props.verdict,'ESCAPE');
 focusSavedMisses(saved);assert.equal(f.state().sampleFilter,'saved_escape','repeated footer focus keeps expanded saved review');
 const returnButton=saved.nodes.find(node=>node.props.id==='workflow-missed-defects');assert.equal(returnButton.type,'button');assert.equal(returnButton.props.type,'button','native button supports keyboard activation');assert.equal(saved.text(returnButton),'미리보기로 돌아가기');
 returnButton.props.onClick();assert.equal(f.state().sampleFilter,'escape');assert.equal(f.state().confidenceThreshold,.2);focusSavedMisses(renderStudio(f.state()));

 assert.equal(saved.nodes.find(node=>node.props.onThresholdChange).props.savedReviewVerdict,'ESCAPE');
 assert.equal(saved.nodes.find(node=>node.props.onThresholdChange).props.confidenceThreshold,.5,'selected-image viewport uses saved review threshold');
 const heatmaps=[];f.api.evaluation.getHeatmap=async(...args)=>{heatmaps.push(args);return{};};await f.state().updateHeatmap();assert.equal(heatmaps.at(-1)[2],.5);
 f.state().setConfidenceThreshold(.1);assert.notEqual(f.state().sampleFilter,'saved_escape','intentional threshold adjustment returns to preview');
 assert.equal(f.state().persistedThreshold,.5);focusSavedMisses(renderStudio(f.state()));
 const previewTab=saved.nodes.find(node=>node.props.role==='tab'&&saved.text(node).startsWith('미검'));previewTab.props.onClick();
 assert.deepEqual(f.state().filteredPredictions,[],'ordinary escape tab returns to interactive preview including live analysis');
 f.state().invalidateForDataChange();
});
test('saved review clears its matrix restriction and resets on accepted-result replacement and invalidation',async()=>{
 const f=fixture();f.results.metrics={active_threshold:.5};f.results.test_predictions=[{...samples[0],defect_score:.3}];
 await f.state().loadEvaluation('job',{folderPath:'/source',task:'classification'});f.state().setConfidenceThreshold(.2);
 f.useEvaluationStore.setState({selectedCell:{trueClass:'scratch',predClass:'scratch'},confusionMatrix:{classes:['OK','scratch'],matrix:[[0,1],[0,0]],cell_samples:{'scratch:scratch':['other']}}});
 focusSavedMisses(renderStudio(f.state()));assert.equal(f.state().selectedCell,null);assert.equal(f.state().filteredPredictions.length,1);
 f.state().selectCell('scratch','scratch');assert.notEqual(f.state().sampleFilter,'saved_escape','ordinary matrix selection returns to preview');
 focusSavedMisses(renderStudio(f.state()));assert.equal(f.state().sampleFilter,'saved_escape');
 let finish;f.api.evaluation.getResults=()=>new Promise(resolve=>{finish=resolve;});const pending=f.state().loadEvaluation('job',{folderPath:'/source',task:'classification'});
 assert.notEqual(f.state().sampleFilter,'saved_escape');assert.equal(f.state().persistedThreshold,null);
 finish({...f.results,metrics:{active_threshold:.8},test_predictions:[{...samples[1],defect_score:.1}]});await pending;
 assert.equal(f.state().sampleFilter,'all');assert.deepEqual(f.state().filteredPredictions.map(row=>row.image_id),['overkill']);
 f.state().setSampleFilter('normal');f.api.evaluation.getResults=async()=>({...f.results,metrics:{active_threshold:.8},test_predictions:[{...samples[1],defect_score:.1}]});await f.state().loadEvaluation('job',{folderPath:'/source',task:'classification'});assert.equal(f.state().sampleFilter,'normal','replacement preserves ordinary preview filter choices');
 focusSavedMisses(renderStudio(f.state()));f.state().invalidateForDataChange();assert.equal(f.state().sampleFilter,'all');assert.equal(f.state().persistedThreshold,null);
});

test('unknown saved authority cannot enter review or retain a stale selected image',async()=>{
 const f=fixture('anomaly');f.results.task='anomaly';await f.state().loadEvaluation('job',{folderPath:'/source',task:'anomaly'});
 const view=renderStudio(f.state());const target=view.nodes.find(node=>node.props.id==='workflow-missed-defects');assert.equal(target.props.disabled,true);
 f.state().setSampleFilter('saved_escape');assert.notEqual(f.state().sampleFilter,'saved_escape');
 f.useEvaluationStore.setState({persistedThreshold:.5,testPredictions:[{...samples[0],defect_score:.9}],selectedPrediction:samples[1]});
 f.state().setSampleFilter('saved_escape');assert.deepEqual(f.state().filteredPredictions,[]);assert.equal(f.state().selectedPrediction,null);f.state().invalidateForDataChange();
});

test('matrix exit from saved review refreshes the preview heatmap for the same selected image',async()=>{
 const f=fixture();f.results.metrics={active_threshold:.5};f.results.test_predictions=[{...samples[0],defect_score:.3}];
 await f.state().loadEvaluation('job',{folderPath:'/source',task:'classification'});f.state().setConfidenceThreshold(.8);f.state().setSampleFilter('saved_escape');
 const heatmaps=[];f.api.evaluation.getHeatmap=async(...args)=>{heatmaps.push(args);return{};};
 f.state().selectCell('OK','scratch');assert.equal(f.state().sampleFilter,'escape');assert.equal(f.state().selectedPrediction.image_id,'escape');
 assert.equal(heatmaps.at(-1)?.[2],.8,'same-image mode exit must replace its saved-threshold heatmap with preview authority');f.state().invalidateForDataChange();
});

const pendingHeatmap=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
for(const differentImage of [false,true])test('saved review clears '+(differentImage?'different-image':'same-image')+' preview pixels while pending and after rejected replacement',async()=>{
 const f=fixture(),miss={...sample('saved-miss','OK','OK'),defect_score:.3},other={...sample('other','OK','OK'),defect_score:.9};
 f.results.metrics={active_threshold:.5};f.results.test_predictions=differentImage?[other,miss]:[miss,other];
 try{
  await f.state().loadEvaluation('job',{folderPath:'/source',task:'classification'});f.state().setConfidenceThreshold(.2);
  f.api.evaluation.getHeatmap=async()=>({overlay_base64:'preview-pixels'});await f.state().updateHeatmap();assert.equal(f.state().heatmapOverlayBase64,'preview-pixels');
  const replacement=pendingHeatmap();f.api.evaluation.getHeatmap=()=>replacement.promise;focusSavedMisses(renderStudio(f.state()));
  assert.equal(f.state().selectedPrediction.image_id,'saved-miss');assert.equal(f.state().heatmapOverlayBase64,null,'preview pixels disappear before saved replacement settles');assert.equal(f.state().heatmapLoading,true);
  replacement.reject(Error('controlled saved refresh failure'));await new Promise(setImmediate);
  assert.equal(f.state().heatmapOverlayBase64,null);assert.equal(f.state().heatmapLoading,false);assert.match(f.state().heatmapError,/불러오지 못/);
  const saved=renderStudio(f.state());assert.match(saved.nodes.find(node=>node.props.onThresholdChange).props.heatmapError,/불러오지 못/);
  f.state().setSampleFilter('all');const preview=renderStudio(f.state());assert.equal(preview.nodes.find(node=>node.props.onThresholdChange).props.heatmapError,null,'ordinary preview error presentation is unchanged');
 }finally{f.state().invalidateForDataChange();}
});
test('in-flight preview response cannot replace pending or successful saved-review pixels',async()=>{
 const f=fixture();f.results.metrics={active_threshold:.5};f.results.test_predictions=[{...samples[0],defect_score:.3}];
 try{
  await f.state().loadEvaluation('job',{folderPath:'/source',task:'classification'});f.state().setConfidenceThreshold(.2);
  const old=pendingHeatmap();f.api.evaluation.getHeatmap=()=>old.promise;const oldRequest=f.state().updateHeatmap();
  const saved=pendingHeatmap();f.api.evaluation.getHeatmap=()=>saved.promise;focusSavedMisses(renderStudio(f.state()));assert.equal(f.state().heatmapOverlayBase64,null);
  saved.resolve({overlay_base64:'saved-pixels'});await new Promise(setImmediate);assert.equal(f.state().heatmapOverlayBase64,'saved-pixels');
  old.resolve({overlay_base64:'late-preview-pixels'});await oldRequest;assert.equal(f.state().heatmapOverlayBase64,'saved-pixels');assert.equal(f.state().heatmapError,null);
 }finally{f.state().invalidateForDataChange();}
});
test('preview threshold transition invalidates saved overlay and in-flight response before debounce requests again',async()=>{
 const f=fixture();f.results.metrics={active_threshold:.5};f.results.test_predictions=[{...samples[0],defect_score:.3}];
 try{
  await f.state().loadEvaluation('job',{folderPath:'/source',task:'classification'});f.state().setSampleFilter('saved_escape');
  f.api.evaluation.getHeatmap=async()=>({overlay_base64:'saved-pixels'});await f.state().updateHeatmap();
  const old=pendingHeatmap();f.api.evaluation.getHeatmap=()=>old.promise;const oldRequest=f.state().updateHeatmap();
  f.state().setConfidenceThreshold(.8);assert.equal(f.state().heatmapOverlayBase64,null,'threshold change synchronously removes old authority');
  old.resolve({overlay_base64:'late-saved-pixels'});await oldRequest;assert.equal(f.state().heatmapOverlayBase64,null,'old authority is revoked during debounce');assert.equal(f.state().heatmapError,null);
 }finally{f.state().invalidateForDataChange();}
});
test('replacement evaluation read revokes old heatmap pixels and ownership immediately',async()=>{
 const f=fixture();f.results.metrics={active_threshold:.5};f.results.test_predictions=[{...samples[0],defect_score:.3}];
 try{
  await f.state().loadEvaluation('job',{folderPath:'/source',task:'classification'});f.useEvaluationStore.setState({heatmapOverlayBase64:'old-evaluation-pixels'});
  const old=pendingHeatmap();f.api.evaluation.getHeatmap=()=>old.promise;const oldRequest=f.state().updateHeatmap();
  const read=pendingHeatmap();f.api.evaluation.getResults=()=>read.promise;const replacement=f.state().loadEvaluation('job',{folderPath:'/source',task:'classification'});
  assert.equal(f.state().heatmapOverlayBase64,null);old.resolve({overlay_base64:'late-old-evaluation-pixels'});await oldRequest;assert.equal(f.state().heatmapOverlayBase64,null);
  read.resolve({...f.results,test_predictions:[]});await replacement;assert.equal(f.state().selectedPrediction,null);assert.equal(f.state().heatmapOverlayBase64,null);
 }finally{f.state().invalidateForDataChange();}
});

// Removing an eligibility branch or routing a canonical metric to the wrong help
// must change the actual rendered controls; no source-string assertions.
for (const row of [
  {name:'pending results',state:{isLoading:true},reason:/불러오는 중|확인 중/,english:/loading|pending/i},
  {name:'fixed common cohort',state:{executionEvidence:{common_cohort:{dataset_version_id:'v1'}}},reason:/코호트.*지원/,english:/cohort.*support/i},
  {name:'heldout anomaly test',state:{metrics:{anomaly_mode:'classification',evaluated_split:'test'}},reason:/시험.*고정.*임계값/,english:/test.*fixed.*threshold/i},
  {name:'distance scores',state:{metrics:{score_spec:{domain:'distance'}}},reason:/거리.*저장.*임계값/,english:/distance.*saved.*threshold/i},
  {name:'missing model',state:{jobId:null},reason:/평가.*모델/,english:/evaluated model/i},
  {name:'missing normal truth',state:{testPredictions:[samples[0]]},reason:/NG와 OK.*모두/,english:/both.*NG.*OK/i},
]) test('rendered unavailable calibration explains '+row.name+' without mutating the saved or preview criterion',()=>{
  for(const language of ['ko','en']){
    let calls=0; const input={jobId:'job',persistedThreshold:.5,confidenceThreshold:.2,classSemantics:semantics,testPredictions:samples,
      filteredPredictions:samples,overkillAnalysis:{optimal_threshold:.1},calibrateZeroEscape:()=>calls++,applyOptimalThreshold:()=>calls++,setConfidenceThreshold:()=>calls++,...row.state};
    const view=renderStudio(input,{language});
    view.nodes.find(n=>n.type==='button'&&/과검\/미검 최적화|Zero-Escape/.test(view.text(n))).props.onClick();view.rerender();
    const text=view.text(view.nodes[0]);assert.match(text,language==='ko'?row.reason:row.english);
    if(row.name!=='missing normal truth')assert.doesNotMatch(text,/NG와 OK 검증 예측이 모두 있어야 과검/,'a capability refusal cannot blame absent classes');
    const apply=view.nodes.filter(n=>n.type==='button'&&/^(평가 임계값 적용|검증 임계값 적용|Evaluation Image Threshold Analysis|Apply)/.test(view.text(n)));
    assert.ok(apply.length);assert.ok(apply.every(n=>n.props.disabled));
    assert.doesNotMatch(text,/즉시 임계값 하향|유출 위험 0%|Zero Escape Risk/);
    assert.equal(calls,0);assert.equal(input.persistedThreshold,.5);assert.equal(input.confidenceThreshold,.2);
  }
});

test('rendered canonical detection and segmentation metrics preserve case and select distinct AP help',()=>{
 const view=renderStudio({metrics:{mAP_50:.6,mAP_50_95:.3,miou:.4,mdice:.5,map50:.7,map50_95:.2}});
 const labels=['mAP@0.5','mAP@0.5:0.95','평균 IoU','평균 Dice','mAP@0.5','mAP@0.5:0.95'];
 const metricLabels=view.nodes.filter(n=>n.type==='span'&&labels.includes(view.text(n)));
 assert.deepEqual(metricLabels.map(n=>view.text(n)),labels);assert.ok(metricLabels.every(n=>!n.props.className.split(/\s+/).includes('uppercase')));
 const tooltips=view.nodes.filter(n=>n.props.termKey&&['map_50','map_50_95','iou','dice'].includes(n.props.termKey));
 assert.deepEqual(tooltips.map(n=>n.props.termKey),['map_50','map_50_95','iou','dice','map_50','map_50_95']);
 const eligible=renderStudio({jobId:'job',classSemantics:semantics,testPredictions:samples,overkillAnalysis:{optimal_threshold:.1}});
 const apply=eligible.nodes.find(n=>n.type==='button'&&eligible.text(n)==='평가 임계값 적용');assert.equal(apply.props.disabled,false);
});

test('rendered zero-miss sample result does not claim zero operating escape risk',()=>{
 const view=renderStudio({jobId:'job',testPredictions:[sample('good-ng','NG','NG')],filteredPredictions:[]});
 view.nodes.find(n=>n.type==='button'&&/과검\/미검 최적화/.test(view.text(n))).props.onClick();view.rerender();
 assert.doesNotMatch(view.text(view.nodes[0]),/유출 위험 0%|Zero Escape Risk/);assert.match(view.text(view.nodes[0]),/평가 표본|evaluation sample/i);
});

for(const row of [
 {name:'no evaluation model',state:{jobId:null,testPredictions:[]},want:/평가 결과 없음/},
 {name:'no predictions',state:{testPredictions:[]},want:/검증 예측 없음/},
 {name:'no NG truth',state:{testPredictions:[sample('normal','normal','normal')]},want:/NG 정답 표본 없음/},
 {name:'missing truth',state:{testPredictions:[sample('unknown',undefined,'scratch')],classSemantics:semantics},want:/정답 역할.*확인/},
 {name:'replacement pending',state:{isLoading:true,testPredictions:[]},want:/불러오는 중/},
])test('rendered miss summary preserves '+row.name+' as unavailable rather than measured zero',()=>{
 const view=renderStudio({jobId:'job',filteredPredictions:[],...row.state});
 view.nodes.find(n=>n.type==='button'&&/과검\/미검 최적화/.test(view.text(n))).props.onClick();view.rerender();
 const card=view.nodes.find(n=>n.type==='div'&&String(n.props.className).startsWith('p-3 rounded-[4px] border'));
 assert.ok(card);assert.match(view.text(card),row.want);assert.doesNotMatch(view.text(card),/평가 표본에서 미검 0건/);
});

for(const row of [{name:'missed NG',score:.1,want:/1건.*놓친 이미지/},{name:'detected NG',score:.9,want:/0건.*평가 표본에서 미검 0건/}])
test('rendered miss summary honors per-image NG truth without a class label: '+row.name,()=>{
 const view=renderStudio({jobId:'job',confidenceThreshold:.5,testPredictions:[{...sample('per-image',undefined,'normal'),is_defect:true,defect_score:row.score}],filteredPredictions:[]});
 view.nodes.find(n=>n.type==='button'&&/과검\/미검 최적화/.test(view.text(n))).props.onClick();view.rerender();
 const card=view.nodes.find(n=>n.type==='div'&&String(n.props.className).startsWith('p-3 rounded-[4px] border'));
 assert.match(view.text(card),row.want);assert.doesNotMatch(view.text(card),/표본 없음|정답 역할.*확인/);
 assert.equal(view.nodes.find(n=>n.type==='button'&&view.text(n)==='평가 임계값 적용').props.disabled,true,'existing class-label-based calibration guard is unchanged');
});

test('eligible calibration with no prior analysis offers calculation rather than blaming missing classes',()=>{
 const view=renderStudio({jobId:'job',classSemantics:semantics,testPredictions:samples,overkillAnalysis:null});
 assert.equal(view.nodes.find(n=>n.type==='button'&&view.text(n)==='평가 임계값 적용').props.disabled,false);
 assert.match(view.text(view.nodes[0]),/아직 계산하지 않음/);
 view.nodes.find(n=>n.type==='button'&&/과검\/미검 최적화/.test(view.text(n))).props.onClick();view.rerender();
 assert.match(view.text(view.nodes[0]),/아직 임계값 분석 결과가 없습니다/);
 assert.doesNotMatch(view.text(view.nodes[0]),/NG와 OK 검증 예측이 모두 있어야/);
});
