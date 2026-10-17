const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');const Module=require('node:module');const test=require('node:test');const ts=require('typescript');
function load(file,mocks={}){const name=path.resolve(__dirname,file);assert.ok(fs.existsSync(name),'Task handoff implementation exists');const m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);const req=m.require.bind(m);m.require=key=>{if(key==='./TrainingRuntimeSettings')return{useTrainingRuntime:()=>({value:'',error:null,onChange:()=>{},getOptions:()=>({})}),TrainingRuntimeSettings:()=>null};if(mocks[key])return mocks[key];const sibling=path.resolve(path.dirname(name),key)+'.ts';return key.startsWith('.')&&fs.existsSync(sibling)?load(sibling,mocks):req(key);};m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const memory=()=>{const values=new Map();return{getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};};
const scope={projectDir:'/project',project:{id:'p',source_dataset_dir:'/source',active_labelset_id:'default'}};
const task=(family,kind='training')=>({key:`${kind}:local:exact`,id:'exact',kind,task:family,status:'completed',source:'/source',labelset:'default',transport:'local',raw:{dataset_path:'/project/dataset/exact'}});
test('all ten families route completed candidates to their actual evaluation workspaces',()=>{const {taskDestination}=load('taskHandoff.ts');for(const family of ['classification','segmentation','detection','anomaly'])assert.equal(taskDestination(task(family)).step,4);for(const family of ['patch_classification','ocr','rotated_detection','rotation','defect_gan','enhancement']){const result=taskDestination(task(family));assert.equal(result.step,3);assert.equal(result.family,family);}assert.equal(taskDestination(task('detection','inspection')).step,6);assert.equal(taskDestination(task('detection','export')).step,6);assert.equal(taskDestination(task('labeling','labeling-batch')).step,2);});
test('exact job and family are scoped across reopen; missing target never selects newest model',()=>{const api=load('taskHandoff.ts');const storage=memory();api.saveTaskHandoff(storage,scope,task('ocr','ocr'));assert.equal(api.readTaskHandoff(storage,scope,'ocr').jobId,'exact');assert.equal(api.readTaskHandoff(storage,{...scope,project:{...scope.project,source_dataset_dir:'/other'}},'ocr'),null);assert.equal(api.readTaskHandoff(storage,scope,'rotation'),null);assert.equal(api.selectHandoffRecord([{job_id:'newest'},{job_id:'exact'}],api.readTaskHandoff(storage,scope,'ocr')).job_id,'exact');assert.throws(()=>api.selectHandoffRecord([{job_id:'newest'}],api.readTaskHandoff(storage,scope,'ocr')),/찾지 못/);});
test('actual completed OCR action invokes overlay callback and preserves exact downstream handoff',async()=>{
 let cursor=0;const slots=[],effects=[],calls=[],storage=memory();const state={...scope,task:'detection',setStep:async step=>calls.push(['step',step]),setTask:async family=>calls.push(['task',family])};const useProjectStore=sel=>sel?sel(state):state;useProjectStore.getState=()=>state;
 const react={useState:initial=>{const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],v=>slots[i]=typeof v==='function'?v(slots[i]):v];},useRef:initial=>{const i=cursor++;if(!(i in slots))slots[i]={current:initial};return slots[i];},useEffect:(fn,deps)=>{const i=cursor++;if(!slots[i]||deps.some((v,j)=>v!==slots[i][j])){slots[i]=deps;effects.push(fn);}}};const jsx=(type,props)=>({type,props:props||{},children:[props?.children].flat().filter(Boolean)});
 const m=load('TaskCenter.tsx',{'react':react,'react/jsx-runtime':{jsx,jsxs:jsx},'lucide-react':{},'../../services/jobEventFeed':{onJobEventChanges:()=>()=>{}},'../../services/api':{getProjectContextGeneration:()=>0,subscribeProjectContext:()=>()=>{},getApiPersistenceIdentity:()=> 'local',request:async()=>({tasks:[{kind:'ocr',job_id:'exact',task:'ocr',status:'completed',source_dataset_path:'/source',training_provenance:{labelset_id:'default'},dataset_path:'/project/dataset/exact'}],reservations:[],errors:[],source_dataset_path:'/source',labelset_id:'default'})},'../../stores/useProjectStore':{useProjectStore},'../../stores/useComputeStore':{useComputeStore:Object.assign(sel=>sel({transportRevision:0,profiles:[],selectedProfileId:null}),{getState:()=>({transportRevision:0,selectedProfileId:null})})},'./taskCenterModel':load('taskCenterModel.ts'),'./ProgramWorkbenchControls':{programInput:'',programButton:''},'./taskHandoff':fs.existsSync(path.resolve(__dirname,'taskHandoff.ts'))?load('taskHandoff.ts'):{} });
 const oldStorage=global.localStorage,oldInterval=global.setInterval;global.localStorage=storage;global.setInterval=()=>0;try{const render=()=>{cursor=0;const tree=m.TaskCenter({initialOpen:true,onNavigate:async step=>calls.push(['navigate',step])});effects.splice(0).forEach(fn=>fn());return tree;};render();await new Promise(setImmediate);const tree=render();const nodes=[];const walk=n=>{if(n&&typeof n==='object'){nodes.push(n);n.children?.flat().forEach(walk);}};walk(tree);const familySelector=nodes.find(n=>n.props['aria-label']==='작업 모델 종류');assert.equal(familySelector.children.flat().filter(n=>n?.type==='option').length,11);await nodes.find(n=>n.type==='button'&&n.children.includes('완료 후보 평가로 이동')).props.onClick();assert.deepEqual(calls,[['step',3],['navigate',3]]);const api=load('taskHandoff.ts');assert.equal(api.readTaskHandoff(storage,state,'ocr').jobId,'exact');}finally{global.localStorage=oldStorage;global.setInterval=oldInterval;}
});

test('basic handoff loads the requested evaluation job instead of transient newest completed job',async()=>{const calls=[];const training={isCurrentData:true,status:'completed',jobId:'newest'};const m=load('../../stores/useEvaluationStore.ts',{'./useTrainingStore':{useTrainingStore:{getState:()=>training}},'../services/api':{api:{evaluation:{getOverkillUnderkill:async()=>({}),getResults:async(id)=>{calls.push(id);return{job_id:id,metrics:{accuracy:1},test_predictions:[]};}}},getApiBaseUrl:()=>''}});m.useEvaluationStore.setState({allowLatestRecovery:false});await m.useEvaluationStore.getState().loadEvaluation('exact',{folderPath:'/source',task:'classification'});assert.deepEqual(calls,['exact']);assert.equal(m.useEvaluationStore.getState().jobId,'exact');});

function hookHarness(){let cursor=0;const slots=[],effects=[];const react={useState:initial=>{const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],v=>slots[i]=typeof v==='function'?v(slots[i]):v];},useRef:initial=>{const i=cursor++;if(!(i in slots))slots[i]={current:initial};return slots[i];},useEffect:(fn,deps)=>{const i=cursor++;if(!slots[i]||deps.some((v,j)=>v!==slots[i].deps[j])){slots[i]?.cleanup?.();slots[i]={deps};effects.push(()=>{slots[i].cleanup=fn();});}},useCallback:fn=>fn};return{react,render:fn=>{cursor=0;const result=fn();effects.splice(0).forEach(fn=>fn());return result;}};}
for(const family of ['patch','rotation'])test(`${family} interrupted handoff remains visible when preparation never created a dataset`,async()=>{
 const h=hookHarness(),state={...scope,project:{...scope.project,models_dir:'/project/models'}};const store=Object.assign(sel=>sel?sel(state):state,{getState:()=>state});const compute={selectedProfileId:null,transportRevision:0};const task=family==='patch'?'patch_classification':family;
 const row={job_id:'exact',task,status:'interrupted',output_dir:'/project/models/exact',source_dataset_path:'/source',training_provenance:{labelset_id:'default'}};
 const m=load('useProgramWorkbench.ts',{'react':h.react,'../../stores/useProjectStore':{useProjectStore:store},'../../stores/useComputeStore':{useComputeStore:Object.assign(()=>compute,{getState:()=>compute})},'../../services/api':{getProjectContextGeneration:()=>0,subscribeProjectContext:()=>()=>{},getApiPersistenceIdentity:()=> 'local'},'./useTaskHandoff':{useTaskHandoff:()=>({jobId:'exact',family:task,status:'interrupted',datasetPath:'/source',transport:'local'})},'../../services/modelExecution':{},'../../services/modelTrainingProgram':{programError:String,activeProgramJob:()=>false,modelTrainingProgram:{[family]:{datasets:async()=>({datasets:[]}),jobs:async()=>({jobs:[row]}),models:async()=>({models:[]})}}}});
 h.render(()=>m.useProgramWorkbench(family));await new Promise(setImmediate);const result=h.render(()=>m.useProgramWorkbench(family));assert.equal(result.job?.job_id,'exact');assert.equal(result.dataset,null);assert.match(result.error,/준비 데이터/);
});
test('specialized hook restores requested job rather than first completed journal',async()=>{const h=hookHarness(),state={...scope};const store=sel=>sel(state);store.getState=()=>state;const jobs=['newest','exact'].map(job_id=>({job_id,task:'ocr',status:'completed',source_dataset_path:'/source',training_provenance:{labelset_id:'default'}}));const completed=[];const m=load('useSpecializedTraining.ts',{'react':h.react,'../../stores/useProjectStore':{useProjectStore:store},'../../services/specializedApi':{specializedApi:{trainingJobs:async()=>({jobs})}},'../../services/modelExecution':{},'../../services/api':{getProjectContextGeneration:()=>0,subscribeProjectContext:()=>()=>{},getApiPersistenceIdentity:()=> 'local'},'../../stores/useComputeStore':{useComputeStore:Object.assign(()=>({selectedProfileId:null,transportRevision:0}),{getState:()=>({selectedProfileId:null,transportRevision:0})})},'./scopedTrainingJob':load('scopedTrainingJob.ts'),'./useTaskHandoff':{useTaskHandoff:()=>({jobId:'exact',family:'ocr'})},'./taskHandoff':load('taskHandoff.ts')});h.render(()=>m.useSpecializedTraining('ocr',row=>completed.push(row.job_id)));await new Promise(setImmediate);const result=h.render(()=>m.useSpecializedTraining('ocr',row=>completed.push(row.job_id)));assert.equal(result.job.job_id,'exact');assert.deepEqual(completed,['exact']);});
test('OCR model and prepared dataset selectors restore exact target despite newer defaults',async()=>{const h=hookHarness(),state={...scope};const store=sel=>sel(state);store.getState=()=>state;const jsx=(type,props)=>({type,props:props||{},children:[props?.children].flat().filter(Boolean)});const handoff={status:'completed',jobId:'exact',family:'ocr',datasetPath:'/project/dataset/exact'};const models=['newest','exact'].map(job_id=>({job_id,metadata:{dataset_path:`/project/dataset/${job_id}`}}));const m=load('OCRWorkbench.tsx',{'../../services/modelExecution':{executeModelRecipe:async(_task,_stage,_params,local)=>local()},'./ModelExecutionEvidence':{ModelExecutionEvidence:()=>null},'react':h.react,'react/jsx-runtime':{jsx,jsxs:jsx},'lucide-react':{},'../../stores/useProjectStore':{useProjectStore:store},'../../services/api':{api:{ocr:{models:async()=>({models})}},request:async()=>({datasets:[{dataset_path:'/project/dataset/exact',sample_count:3},{dataset_path:'/project/dataset/newest',sample_count:4}]})},'./useSpecializedTraining':{useSpecializedTraining:()=>({active:false})},'./useTaskHandoff':{useTaskHandoff:()=>handoff},'./taskHandoff':load('taskHandoff.ts'),'./TrainingPreparationPanel':{},'./SpecializedTrainingStatus':{},'./WarmStartSelector':{},'./AutoDLWorkbench':{},'./ProgramWorkbenchControls':{},'./ProjectImagePicker':{},'./preparedSampleRows':{}});h.render(()=>m.OCRWorkbench());await new Promise(setImmediate);const tree=h.render(()=>m.OCRWorkbench());const nodes=[];const walk=n=>{if(n&&typeof n==='object'){nodes.push(n);n.children?.flat().forEach(walk);}};walk(tree);const selectors=nodes.filter(n=>n.type==='select');assert.ok(selectors.some(n=>n.props.value==='exact'),'Exact OCR model is selected');assert.ok(selectors.some(n=>n.props.value==='/project/dataset/exact'),'Matching prepared dataset is selected');selectors.find(n=>n.props.value==='exact').props.onChange({target:{value:'newest'}});h.render(()=>m.OCRWorkbench());handoff.selectionId='select-same-task-again';h.render(()=>m.OCRWorkbench());await new Promise(setImmediate);const repeated=h.render(()=>m.OCRWorkbench());nodes.length=0;walk(repeated);assert.ok(nodes.some(n=>n.type==='select'&&n.props.value==='exact'),'Same-task re-click restores exact OCR model after manual selection changed');});

test('persisted handoff reopens across epochs while server identity and GPU profile remain isolated',()=>{const handoff=load('taskHandoff.ts');const storage=memory();const server={...scope,transportRevision:4,selectedProfileId:'server-a',apiTransportIdentity:'https://server-a/base'};handoff.saveTaskHandoff(storage,server,task('ocr','ocr'));assert.equal(handoff.readTaskHandoff(storage,{...server,transportRevision:0},'ocr').jobId,'exact');assert.equal(handoff.readTaskHandoff(storage,{...server,apiTransportIdentity:'https://server-b/base'},'ocr'),null);assert.equal(handoff.readTaskHandoff(storage,{...server,selectedProfileId:'server-b'},'ocr'),null);const {dataWorkbenchScope}=load('../../services/dataWorkbench.ts',{'./api':{request(){}}});assert.notEqual(dataWorkbenchScope(server),dataWorkbenchScope({...server,transportRevision:5}));assert.notEqual(dataWorkbenchScope(server),dataWorkbenchScope({...server,selectedProfileId:'server-b'}));});

test('diagnostic scope differs for same project after transport-only or selected-server change',()=>{const {dataWorkbenchScope}=load('../../services/dataWorkbench.ts',{'./api':{request(){}}});const state={...scope,transportRevision:4,selectedProfileId:'a'};assert.notEqual(dataWorkbenchScope(state),dataWorkbenchScope({...state,transportRevision:5}));assert.notEqual(dataWorkbenchScope(state),dataWorkbenchScope({...state,selectedProfileId:'b'}));});

test('reopening the same completed task creates a new selection identity',()=>{const api=load('taskHandoff.ts');const storage=memory();const first=api.saveTaskHandoff(storage,scope,task('ocr','ocr'));const next=api.saveTaskHandoff(storage,scope,task('ocr','ocr'));assert.equal(next.jobId,first.jobId);assert.ok(first.selectionId);assert.notEqual(next.selectionId,first.selectionId);});

test('API persistence identity omits credentials and transient parameters and keeps local restart identity stable',()=>{const api=load('../../services/api.ts');api.setCachedPort(8000);assert.equal(api.getApiPersistenceIdentity(),'local');api.setCachedPort(9123);assert.equal(api.getApiPersistenceIdentity(),'local');api.setSharedApiBase(("https://" + "user:password" + "@" + "api-a.example/base/?token=sentinel#private"));assert.equal(api.getApiPersistenceIdentity(),'shared:https://api-a.example/base');api.setSharedApiBase('https://api-a.example/base');assert.equal(api.getApiPersistenceIdentity(),'shared:https://api-a.example/base');api.setSharedApiBase('https://api-b.example/base');assert.equal(api.getApiPersistenceIdentity(),'shared:https://api-b.example/base');api.setSharedApiBase(null);assert.equal(api.getApiPersistenceIdentity(),'local');});

test('review queue storage scope survives reconnect but isolates stable API identity',()=>{const {dataWorkbenchPersistenceScope,dataWorkbenchScope}=load('../../services/dataWorkbench.ts',{'./api':{request(){}}});const initial={...scope,transportRevision:9,selectedProfileId:'gpu-a',apiTransportIdentity:'shared:https://api-a/base'};assert.equal(dataWorkbenchPersistenceScope(initial),dataWorkbenchPersistenceScope({...initial,transportRevision:0}));assert.notEqual(dataWorkbenchScope(initial),dataWorkbenchScope({...initial,transportRevision:0}));assert.notEqual(dataWorkbenchPersistenceScope(initial),dataWorkbenchPersistenceScope({...initial,apiTransportIdentity:'shared:https://api-b/base'}));});


// Exercise actual controller keys, workbench draft state and warm-start effects.
// Passive effects are committed child-first: a parent reset cannot repair a
// request already dispatched by its child's effect in the same commit.
function ocrProjectSwitchHarness() {
  let active;
  const frame = () => ({slots: [], cursor: 0, pending: [], disposed: false});
  const react = {
    useState(initial) {
      const owner = active, i = owner.cursor++;
      if (!(i in owner.slots)) {
        const slot = {value: typeof initial === 'function' ? initial() : initial};
        slot.set = value => { if (!owner.disposed) slot.value = typeof value === 'function' ? value(slot.value) : value; };
        owner.slots[i] = slot;
      }
      return [owner.slots[i].value, owner.slots[i].set];
    },
    useEffect(fn, deps) {
      const owner = active, i = owner.cursor++, previous = owner.slots[i];
      if (!previous || deps.some((value, index) => value !== previous.deps[index])) {
        const slot = {deps: [...deps], cleanup: previous?.cleanup};
        owner.slots[i] = slot;
        owner.pending.push(() => { slot.cleanup?.(); slot.cleanup = fn(); });
      }
    },
  };
  const render = (owner, callback) => {
    owner.cursor = 0; active = owner;
    try { return callback(); } finally { active = undefined; }
  };
  const flush = owner => { for (const effect of owner.pending.splice(0)) effect(); };
  const dispose = owner => {
    if (!owner) return;
    owner.disposed = true;
    for (const slot of owner.slots) slot?.cleanup?.();
    assert.equal(owner.pending.length, 0, 'Previous committed effects have all settled');
  };
  const jsx = (type, props, key) => ({type, key, props: props || {}, children: [props?.children].flat().filter(Boolean)});
  const nodes = tree => {
    const result = [];
    const walk = value => {
      if (Array.isArray(value)) { value.forEach(walk); return; }
      if (!value || typeof value !== 'object') return;
      result.push(value); value.children?.forEach(walk);
    };
    walk(tree); return result;
  };
  const state = {projectDir: '/project-A', project: {id: 'A', source_dataset_dir: '/source-A', active_labelset_id: 'labels-A'},
    task: 'classification', language: 'ko', isProjectBusy: false, setStep: async () => {}};
  const compute = {profiles: [], selectedProfileId: null, transportRevision: 0, isLoaded: true, isLoading: false, probeResults: {}};
  const store = Object.assign(selector => selector ? selector(state) : state, {getState: () => state});
  const dataset = {folderPath: '', totalImages: 0, split: {train: 0, val: 0, test: 0}, datasetKey: '', isLoading: false, isSplitting: false};
  const training = {jobId: '', status: 'idle', preset: 'default', recoverActiveJob: async () => {}, refreshCurrentJob: async () => {}};
  const requests = [];
  const request = async url => {
    if (url === '/api/ocr/datasets') return {datasets: []};
    assert.match(url, /^\/api\/ocr\/warm-start-parents\?/);
    requests.push({datasetPath: new URL(url, 'http://test.invalid').searchParams.get('dataset_path'),
      projectDir: state.projectDir, projectId: state.project.id,
      source: state.project.source_dataset_dir, labelset: state.project.active_labelset_id});
    return {parents: []};
  };
  const mocks = {
    react, 'react/jsx-runtime': {jsx, jsxs: jsx, Fragment: 'fragment'}, 'lucide-react': {},
    '../../stores/useProjectStore': {useProjectStore: store},
    '../../stores/useDatasetStore': {useDatasetStore: Object.assign(() => dataset, {getState: () => dataset})},
    '../../stores/useTrainingStore': {useTrainingStore: () => training},
    '../../stores/useComputeStore': {useComputeStore: Object.assign(() => compute, {getState: () => compute})},
    '../../services/api': {api: {ocr: {models: async () => ({models: []})}}, request, getApiPersistenceIdentity: () => 'local'},
    '../../services/modelExecution': {},
    './useTaskHandoff': {useTaskHandoff: family => family === 'ocr' ? null : {family: 'ocr'}},
    './taskHandoff': {clearTaskHandoff: () => {}, selectHandoffRecord: () => undefined},
    './useSpecializedTraining': {useSpecializedTraining: () => ({active: false})},
    './parentCandidate': {parentCandidateLabel: () => '', parentCandidateNotice: ''},
    './modelAdapterRecipes': {
      ocrRecipeDraft: () => ({mode: 'crop', charset: '', normalizer: 'none', regex: '', minLength: '', maxLength: '', allowedValues: ''}),
      ocrRecipePayload: () => ({}), ocrTrainingOptions: () => ({}), formatOCRRate: String,
    },
    './preparedSampleRows': {}, './ModelExecutionEvidence': {}, './SpecializedTrainingStatus': {},
    './ProgramWorkbenchControls': {}, './ProjectImagePicker': {},
    '../../utils/datasetSplitCapability': {isSplitUnavailable: () => false},
    '../../utils/trainingComputeReadiness': {trainingComputeReadiness: () => ({ready: true})},
    './modelTrainingOptions': {modelChoices: {classification: [{value: 'model'}]}, dinoSyntheticDefaults: {},
      manualTrainingRecipe: () => ({}), trainingModelOverrides: () => ({})},
    '../wizard/TaskChangeImpactDialog': {taskPreviewScope: () => 'scope'},
    '../common/errorActions': {}, './trainingLog': {},
    './TrainingSchedulingSettings': {defaultTrainingScheduling: {}, trainingSchedulingOptions: () => ({})},
    './useSelectedCoreJob': {useSelectedCoreJob: () => null},
  };
  for (const name of ['../common/OperatorGuidanceBanner', '../common/GuardrailBanner', '../common/LedAnnunciator',
    './OscilloscopeLossCurve', './HardwareTelemetryPanel', './RecipePresetSelector', './RotatedDetectionPanel',
    './DefectGANWorkbench', './EnhancementWorkbench', './ModelFamilyCatalog', './DinoSyntheticOptions',
    './PatchClassificationWorkbench', './RotationWorkbench', './AutoDLWorkbench', './TrainingPreparationPanel',
    './JobProgressView', '../runtime/OperationsObservabilityPanel']) mocks[name] = {};
  const {WarmStartSelector} = load('WarmStartSelector.tsx', mocks);
  mocks['./WarmStartSelector'] = {WarmStartSelector};
  const {OCRWorkbench} = load('OCRWorkbench.tsx', mocks);
  mocks['./OCRWorkbench'] = {OCRWorkbench};
  const {TrainingController} = load('TrainingController.tsx', mocks);
  const controller = frame();
  let workbench, warm, currentKey, lastTree, mounts = 0;
  const commit = () => {
    const controllerTree = render(controller, () => TrainingController());
    const element = nodes(controllerTree).find(node => node.type === OCRWorkbench);
    assert.ok(element, 'The original OCR workspace is rendered');
    if (!workbench || element.key !== currentKey) {
      dispose(warm); dispose(workbench); workbench = frame(); warm = frame(); currentKey = element.key; mounts++;
    }
    lastTree = render(workbench, () => OCRWorkbench(element.props));
    const warmElement = nodes(lastTree).find(node => node.type === WarmStartSelector);
    assert.ok(warmElement, 'The actual owning warm-start selector is rendered');
    render(warm, () => WarmStartSelector(warmElement.props));
    flush(warm); flush(workbench); flush(controller);
    return lastTree;
  };
  const input = () => nodes(lastTree).find(node => node.type === 'input' && node.props.placeholder === '/path/to/text-crops');
  return {state, compute, requests, commit, input, mounts: () => mounts,
    async settle() { await new Promise(setImmediate); commit(); await new Promise(setImmediate); },
    close() { dispose(warm); dispose(workbench); dispose(controller); }};
}

test('OCR namespace switches cannot dispatch a retained dataset draft before the parent passive reset', async () => {
  for (const changed of ['projectDir', 'source_dataset_dir', 'active_labelset_id']) {
    const app = ocrProjectSwitchHarness();
    try {
      app.commit(); await app.settle();
      const original = {projectDir: app.state.projectDir, project: {...app.state.project}};
      const oldDraft = '/project-A/prepared/operator-selected';
      app.input().props.onChange({target: {value: oldDraft}}); app.commit(); await app.settle();
      assert.ok(app.requests.some(row => row.datasetPath === oldDraft && row.projectId === 'A'), 'The previous scope really dispatched its selected draft');
      app.requests.length = 0;
      if (changed === 'projectDir') { app.state.projectDir = '/project-B'; app.state.project = {...app.state.project, id: 'B'}; }
      else app.state.project = {...app.state.project, [changed]: changed === 'source_dataset_dir' ? '/source-B' : 'labels-B'};
      app.commit();
      assert.equal(app.input().props.value, '', `${changed}: the new child starts before the parent reset runs`);
      assert.equal(app.requests.some(row => row.datasetPath === oldDraft), false, `${changed}: no stale draft dispatch in the new namespace`);
      await app.settle();
      assert.ok(app.requests.some(row => row.datasetPath === app.state.project.source_dataset_dir
        && row.projectDir === app.state.projectDir && row.projectId === app.state.project.id
        && row.labelset === app.state.project.active_labelset_id), `${changed}: the new source is still queried normally`);
      const nextDraft = '/next-scope/prepared/operator-selected';
      app.input().props.onChange({target: {value: nextDraft}}); app.commit(); await app.settle();
      app.requests.length = 0; app.state.projectDir = original.projectDir; app.state.project = original.project;
      app.commit();
      assert.equal(app.input().props.value, '', `${changed}: returning to A starts a fresh owning draft`);
      assert.equal(app.requests.some(row => row.datasetPath === nextDraft), false, `${changed}: returning to A cannot dispatch B's draft`);
      await app.settle();
      assert.ok(app.requests.some(row => row.datasetPath === original.project.source_dataset_dir && row.projectId === 'A'));
    } finally { app.close(); }
  }
});

test('OCR dataset draft survives a compute transport change within the same namespace', async () => {
  const app = ocrProjectSwitchHarness();
  try {
    app.commit(); await app.settle();
    const draft = '/project-A/prepared/operator-selected';
    app.input().props.onChange({target: {value: draft}}); app.commit(); await app.settle();
    const mounts = app.mounts(); app.compute.transportRevision++; app.compute.selectedProfileId = 'other-worker';
    app.commit(); await app.settle();
    assert.equal(app.mounts(), mounts, 'Changing the worker retains the same OCR workspace');
    assert.equal(app.input().props.value, draft, 'The current namespace draft remains selected');
  } finally { app.close(); }
});
