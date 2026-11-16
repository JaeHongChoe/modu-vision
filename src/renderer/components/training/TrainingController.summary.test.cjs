const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const Module = require('node:module'), test = require('node:test'), ts = require('typescript');

function fixture({warmParentsFailure = null, resumeFailure = null, deferResume = false} = {}) {
  const slots = [], effects = [], cache = new Map(), resumeReads = [], taskHandlers = new Map(), savedJob = {job:null,error:null}; let cursor = 0, tree;
  const storageValues = new Map();
  const storage = {
    get length() { return storageValues.size; },
    key(index) { return [...storageValues.keys()][index] ?? null; },
    getItem(key) { return storageValues.get(String(key)) ?? null; },
    setItem(key, value) { storageValues.set(String(key), String(value)); },
    removeItem(key) { storageValues.delete(String(key)); },
    clear() { storageValues.clear(); },
  };
  const react = { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }), Fragment: 'Fragment',
    useRef(initial) { const i = cursor++; return slots[i] ?? (slots[i] = { current: initial }); },
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = { value: typeof initial === 'function' ? initial() : initial };
      const slot = slots[i]; return [slot.value, value => { slot.value = typeof value === 'function' ? value(slot.value) : value; }]; },
    useEffect(fn, deps) { const i = cursor++, old = slots[i];
      if (!old || deps.some((dep, index) => !Object.is(dep, old.deps[index]))) {
        const slot = slots[i] = { deps }; effects.push(() => { old?.cleanup?.(); slot.cleanup = fn(); });
      }
    },
  };
  react.default = react;
  const hook = state => Object.assign(selector => selector ? selector(state) : state, { getState: () => state });
  const project = { task: 'detection', activeStep: 3, language: 'ko', setStep() {}, projectDir: '/project',
    project: { id: 'p', task: 'detection', source_dataset_dir: '/images', active_labelset_id: 'default' }, isProjectBusy: false, technicalDetails: false, viewMode: 'simple' };
  const data = { folderPath: '/images', totalImages: 10, split: { train: 6, val: 2, test: 2 }, isLoading: false, isSplitting: false,
    importError: null, splitError: null, splitSupported: true, splitUnavailableReason: null, datasetKey: '/images\0detection', sourceAuthority: 'authority', splitState: 'saved', classes: {}, classSplitCounts: null };
  const training = { jobId: null, status: 'idle', isTraining: false, isRecoveringTraining: false, preset: 'fast', nextDevice: 'cpu', nextBatchSize: null,
    setPreset() {}, lossHistory: [], hardware: {}, bestMetric: null, startTraining: async () => assert.fail('summary never starts training'), stopTraining: async () => {},
    recoverActiveJob: async () => {}, refreshCurrentJob: async () => {}, reconnectCurrentJob: async () => {}, setNextSettings() {} };
  const compute = { profiles: [], selectedProfileId: null, transportRevision: 1, isLoaded: true, isLoading: false, loadError: null, error: null, probeResults: {}, probePendingId: null, probeProfile: async () => {} };
  const authority = () => JSON.stringify([project.projectDir, project.project.id, project.project.source_dataset_dir, project.project.active_labelset_id]);
  function load(file, real = false) {
    const filename = path.resolve(__dirname, file);
    if (cache.has(filename)) return cache.get(filename).exports;
    const m = new Module(filename, module); m.filename = filename; m.paths = Module._nodeModulePaths(path.dirname(filename)); cache.set(filename, m);
    const req = m.require.bind(m);
    m.require = name => {
      if (name === './trainingStartSummary' || name === './modelTrainingOptions' || name === './inputPreparation' || name === './trainingWorkflow') return load(name.slice(2) + '.ts', true);
      if (name.endsWith('/workflowReadiness')) return load('../wizard/workflowReadiness.ts', true);
      if (name === 'react') return react;
      if (name === 'react/jsx-runtime') return {jsx: (type, props, key) => ({type, props, key}), jsxs: (type, props, key) => ({type, props, key}), Fragment: 'Fragment'};
      if (name.endsWith('/assistContext')) return load('../../stores/assistContext.ts', true);
      if (name.endsWith('/useScopedError')) return load('../common/useScopedError.ts', true);
      if (real) return req(name);
      if (name === 'lucide-react') return new Proxy({}, { get: (_, key) => String(key) });
      if (name.endsWith('/useProjectStore')) return { useProjectStore: hook(project) };
      if (name.endsWith('/useDatasetStore')) return { useDatasetStore: hook(data), readDatasetAuthority: () => data.currentAuthority||'authority' };
      if (name.endsWith('/useTrainingStore')) return { useTrainingStore: hook(training) };
      if (name.endsWith('/useComputeStore')) return { useComputeStore: hook(compute) };
      if (name.endsWith('/services/api')) return { api: { training: { warmStartParents: async () => {if (warmParentsFailure) throw Error(warmParentsFailure); return { parents: [], total: 0 };} } }, request: async () => {if (deferResume) return new Promise((resolve, reject) => resumeReads.push({resolve, reject})); if (resumeFailure) throw Error(resumeFailure); return { states: [{checkpoint_path:'/resume/last.pt',device:'cuda',recipe:{backbone:'yolo26n',epochs:12},next_epoch:2,global_step:4}], excluded: [] };}, getApiPersistenceIdentity: () => compute.apiIdentity||'local' };
      if (name.endsWith('/autoRunChannel')) return { registerAutoRun: () => () => {}, registerTaskAutoRun: (kind, handler, settings) => {const record={handler,settings};taskHandlers.set(kind,record);return()=>{if(taskHandlers.get(kind)===record)taskHandlers.delete(kind);};} };
      if (name.endsWith('/datasetSplitCapability')) return { isSplitUnavailable: () => false };
      if (name.endsWith('/trainingComputeReadiness')) return load('../../utils/trainingComputeReadiness.ts', true);
      if (name.endsWith('/taskHandoff')) return load('taskHandoff.ts', true);
      if (name === './useTaskHandoff') return { useTaskHandoff: () => null };
      if (name === './useSelectedCoreJob') return { useSelectedCoreJob: () => savedJob };
      if (name === './TrainingSchedulingSettings') return load('TrainingSchedulingSettings.tsx', true);
      if (name.endsWith('/errorActions')) return { trainingPresetBatchSize: () => 8 };
      if (name === './trainingLog') return { trainingLogLines: () => ['Current core log'] };
      if (name.endsWith('/TaskChangeImpactDialog')) return { taskPreviewScope: authority, TaskChangeImpactDialog: 'TaskChangeImpactDialog' };
      if (name.endsWith('/importChecklist')) return { classGaps: () => ({ thin: [] }), classStartBlocker: () => null, splitMissesLabels: () => false };
      return new Proxy({}, { get: (_, key) => String(key) });
    };
    const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    } }).outputText;
    // Browser storage belongs to this mounted fixture, independent of whether
    // the host Node version exposes a native global localStorage implementation.
    new Function('exports', 'require', 'module', '__filename', '__dirname', 'localStorage', compiled)
      .call(m.exports, m.exports, m.require.bind(m), m, filename, path.dirname(filename), storage);
    return m.exports;
  }
  const h = load('trainingStartSummary.ts', true), shared = load('../../stores/assistContext.ts', true), { TrainingController } = load('TrainingController.tsx');
  const nodes = value => Array.isArray(value) ? value.flatMap(nodes) : value && typeof value === 'object' ? [value, ...nodes(typeof value.type === 'function' ? value.type(value.props) : value.props?.children), ...nodes(value.props?.blocks?.map(block => block.content))] : [];
  function render() { cursor = 0; tree = TrainingController(); }
  const scope = () => h.trainingSummaryScope(authority(), { task: project.task, folderPath: data.folderPath, datasetKey: data.datasetKey,
    preset: training.preset, nextDevice: training.nextDevice, nextBatchSize: training.nextBatchSize, selectedProfileId: compute.selectedProfileId,
    profile: compute.profiles.find(row => row.id === compute.selectedProfileId), transportRevision: compute.transportRevision,
    computeLoaded: compute.isLoaded, computeLoading: compute.isLoading, computeLoadError: compute.loadError });
  return { focusTarget: load('../wizard/workflowReadiness.ts',true).focusWorkflowTarget, project, data, training, compute, h, shared, scope, render, resumeReads, savedJob, storage, taskHandler: () => taskHandlers.get('train')?.handler,
    async settle() { for (let i = 0; i < 8; i++) { render(); effects.splice(0).forEach(effect => effect()); await new Promise(setImmediate); } },
    change(label, value) { const field = nodes(tree).find(node => node.props?.['aria-label'] === label); assert.ok(field, label); field.props.onChange({ target: { value } }); },
    summary: () => h.currentTrainingSummary(scope()),
    catalog: () => nodes(tree).find(node => node.type === 'ModelFamilyCatalog')?.props,
    workflow: () => nodes(tree).find(node => node.type === 'TrainingDataWorkflow')?.props,
    preparation: () => nodes(tree).find(node => node.type === 'TrainingPreparationPanel')?.props,
    dom() {
      const elements = []; let focused = null;
      function materialize(value, parent = null) {
        if (Array.isArray(value)) return value.forEach(child => materialize(child, parent));
        if (!value || typeof value !== 'object') return;
        if (typeof value.type === 'function') return materialize(value.type(value.props), parent);
        const element = {tagName: String(value.type).toUpperCase(), key: value.key, props: value.props, parentElement: parent, open: Boolean(value.props.open),
          focus() {focused = this;}, scrollIntoView() {},
          querySelector(selector) {return elements.find(candidate => {
            let owner = candidate.parentElement; while (owner && owner !== this) owner = owner.parentElement;
            return owner === this && (selector === 'details' ? candidate.tagName === 'DETAILS' : selector === 'button:not(:disabled)' ? candidate.tagName === 'BUTTON' && !candidate.props.disabled : false);
          }) || null;}};
        elements.push(element); materialize(value.props.children, element); materialize(value.props.blocks?.map(block => ({type:'WorkflowBlock',props:{'data-workflow-block':block.id,children:block.content}})), element);
      }
      materialize(tree);
      return {getElementById: id => elements.find(element => element.props.id === id) || null, focused: () => focused, elements};
    },
    alerts: () => nodes(tree).filter(node => node.props?.role === 'alert').map(node => nodes(node).flatMap(child => child.props?.children || []).filter(value => typeof value === 'string').join('')),
    start() { const button = nodes(tree).find(node => node.type === 'button' && nodes(node).some(child => child.props?.children?.includes?.('선택 설정으로 학습 시작'))); assert.ok(button); assert.equal(button.props.disabled, false); return button.props.onClick(); },
    unmount() { slots.forEach(slot => slot?.cleanup?.()); },
  };
}

test('the mounted training controller publishes actual model and weight selections, then retires them on unmount', async () => {
  const f = fixture(); await f.settle();
  assert.match(f.summary()?.model || '', /YOLO26 nano/);
  f.change('학습 모델 구조', 'yolo26s'); f.change('사전학습 가중치 파일', '/private/cache/selected.pt'); await f.settle();
  assert.match(f.summary().model, /YOLO26 small/);
  assert.match(f.summary().weights, /selected\.pt.*준비 확인 필요/);
  assert.doesNotMatch(f.summary().weights, /\/private|준비 완료/);
  f.change('재학습 시작 모델', 'job_parent123'); await f.settle();
  assert.match(f.summary().weights, /완료 모델.*arent123/);
  f.unmount(); assert.equal(f.summary(), null);
});

test('the visible training failure reaches common guidance and clearing or unmounting removes only its owned entry', async () => {
  const f = fixture(); await f.settle();
  f.training.startError = '학습 가중치를 읽지 못했습니다. 파일을 다시 선택하세요.'; await f.settle();
  assert.deepEqual(f.shared.assistPanelErrors(), [{source: '학습', text: f.training.startError}]);
  f.training.startError = null; f.training.jobStatusError = '작업 연결이 끊겼습니다. 상태를 다시 확인하세요.'; await f.settle();
  assert.deepEqual(f.shared.assistPanelErrors(), [{source: '학습', text: f.training.jobStatusError}]);
  f.training.jobStatusError = null; await f.settle(); assert.deepEqual(f.shared.assistPanelErrors(), []);
  f.training.stopError = '중단 결과를 확인하지 못했습니다. 상태를 다시 확인하세요.'; await f.settle();
  assert.deepEqual(f.shared.assistPanelErrors(), [{source: '학습', text: f.training.stopError}]);
  const other = f.shared.registerPanelError('학습', '다른 패널이 소유한 오류');
  f.unmount(); assert.deepEqual(f.shared.assistPanelErrors(), [{source: '학습', text: '다른 패널이 소유한 오류'}]); other();
});

test('training resource, invalid configuration and parent/resume list errors are readable in common guidance', async () => {
  const f = fixture(); await f.settle(); f.compute.loadError = '서버 목록 연결 실패'; await f.settle();
  assert.deepEqual(f.shared.assistPanelErrors(), [{source:'학습',text:'서버 설정 확인 실패: 서버 목록 연결 실패'}]);
  f.compute.loadError = null; f.change('수동 학습 횟수', '0'); await f.settle();
  assert.match(f.shared.assistPanelErrors()[0]?.text || '', /학습 횟수/);
  f.change('수동 학습 횟수', ''); await f.settle(); assert.deepEqual(f.shared.assistPanelErrors(), []); f.unmount();
  const lists = fixture({warmParentsFailure:'부모 모델 목록 연결 실패',resumeFailure:'중단 작업 목록 연결 실패'}); await lists.settle();
  assert.match(lists.shared.assistPanelErrors()[0]?.text || '', /재학습 모델 목록: 부모 모델 목록 연결 실패/);
  assert.match(lists.shared.assistPanelErrors()[0]?.text || '', /중단 작업 목록 연결 실패/); lists.unmount();
});

test('a still-invalid training input remains in guidance after changing source or labelset', async () => {
  for (const change of [
    f => {f.project.project.active_labelset_id = 'next';},
    f => {f.data.folderPath = '/new'; f.data.datasetKey = '/new\0detection'; f.project.project.source_dataset_dir = '/new';},
  ]) {
    const f = fixture(); await f.settle(); f.change('수동 학습 횟수', '0'); await f.settle();
    assert.match(f.shared.assistPanelErrors()[0]?.text || '', /학습 횟수/);
    change(f); await f.settle(); assert.ok(f.alerts().some(text => /학습 횟수/.test(text)), 'the current form remains invalid');
    assert.match(f.shared.assistPanelErrors()[0]?.text || '', /학습 횟수/); f.unmount();
  }
});

test('current compute failures survive source and labelset changes but retire with the transport or selected profile', async () => {
  for (const field of ['loadError', 'error']) {
    const f = fixture(); await f.settle(); f.compute[field] = '현재 서버 설정 연결 실패'; await f.settle();
    f.project.project.active_labelset_id = 'next'; await f.settle();
    assert.deepEqual(f.shared.assistPanelErrors(), [{source:'학습',text:'서버 설정 확인 실패: 현재 서버 설정 연결 실패'}]);
    f.data.folderPath = '/new'; f.data.datasetKey = '/new\0detection'; f.project.project.source_dataset_dir = '/new'; await f.settle();
    assert.ok(f.alerts().some(text => text.includes('현재 서버 설정 연결 실패')));
    assert.deepEqual(f.shared.assistPanelErrors(), [{source:'학습',text:'서버 설정 확인 실패: 현재 서버 설정 연결 실패'}]);
    f.compute.transportRevision++; await f.settle(); assert.deepEqual(f.shared.assistPanelErrors(), []);
    f.compute[field] = null; await f.settle(); f.compute[field] = '현재 서버 설정 연결 실패'; await f.settle();
    assert.equal(f.shared.assistPanelErrors().length, 1, 'a fresh request on the new connection can report the same failure');
    f.compute.selectedProfileId = 'other'; f.compute.profiles = [{id:'other',name:'다른 서버'}]; await f.settle();
    assert.deepEqual(f.shared.assistPanelErrors(), []); f.unmount();
  }
});

test('a pending resume-list reply belongs to its original project and labelset while the next scope may report its own failure', async () => {
  for (const change of [f => {f.project.project.active_labelset_id = 'next';}, f => {f.project.project.id = 'next';}]) {
    const f = fixture({deferResume:true}); await f.settle(); assert.equal(f.resumeReads.length, 1);
    change(f); await f.settle();
    f.resumeReads[0].reject(Error('이전 범위의 중단 작업 조회 실패')); await f.settle();
    assert.ok(!f.alerts().some(text => text.includes('이전 범위'))); assert.deepEqual(f.shared.assistPanelErrors(), []);
    assert.equal(f.resumeReads.length, 2, 'the new source authority gets its own list read');
    f.resumeReads[1].reject(Error('현재 범위의 중단 작업 조회 실패')); await f.settle();
    assert.match(f.shared.assistPanelErrors()[0]?.text || '', /현재 범위의 중단 작업 조회 실패/); f.unmount();
  }
});

test('training errors retained during project, source, task or labelset changes never acquire the next scope', async () => {
  for (const change of [
    f => {f.project.project.id = 'other'; f.project.projectDir = '/other';},
    f => {f.data.folderPath = '/other'; f.data.datasetKey = '/other\0detection'; f.project.project.source_dataset_dir = '/other';},
    f => {f.project.task = f.project.project.task = 'segmentation'; f.data.datasetKey = '/images\0segmentation';},
    f => {f.project.project.active_labelset_id = 'other';},
    f => {f.compute.transportRevision++;},
  ]) {
    const f = fixture(); await f.settle(); f.training.startError = '이전 입력의 학습 실패'; await f.settle();
    assert.equal(f.shared.assistPanelErrors().length, 1);
    change(f); const seen = []; const unsubscribe = f.shared.subscribeAssistContext(() => seen.push(...f.shared.assistPanelErrors()));
    await f.settle(); assert.deepEqual(f.shared.assistPanelErrors(), []); assert.deepEqual(seen, []);
    f.training.startError = null; await f.settle(); f.training.startError = '이전 입력의 학습 실패'; await f.settle();
    assert.deepEqual(f.shared.assistPanelErrors(), [{source: '학습', text: '이전 입력의 학습 실패'}], 'a fresh failed attempt may have the same message');
    unsubscribe(); f.unmount();
  }
});

test('a late start failure cannot publish an error after the labelset changes', async () => {
  const f = fixture(); await f.settle(); let reject;
  f.training.startTraining = () => new Promise((_, no) => {reject = no;}); await f.settle();
  const pending = f.start(); f.project.project.active_labelset_id = 'next'; await f.settle();
  reject(Error('이전 라벨 세트 요청 실패')); await pending; await f.settle();
  assert.deepEqual(f.shared.assistPanelErrors(), []); f.unmount();
});

test('changing source cannot publish the previous model or checkpoint under the new source scope', async () => {
  const f = fixture(); await f.settle();
  f.change('학습 모델 구조', 'yolo26s'); f.change('사전학습 가중치 파일', '/old/selected.pt'); await f.settle();
  f.data.folderPath = '/new'; f.data.datasetKey = '/new\0detection'; f.project.project.source_dataset_dir = '/new';
  assert.equal(f.summary(), null);
  const seen = []; const unsubscribe = f.h.subscribeTrainingSummary(() => { const value = f.summary(); if (value) seen.push(value); });
  await f.settle();
  assert.match(f.summary()?.model || '', /YOLO26 nano/);
  assert.ok(seen.length > 0);
  assert.ok(seen.every(value => !/YOLO26 small|selected\.pt/.test(value.model + value.weights)));
  unsubscribe(); f.unmount();
});

test('preset and worker resets cannot publish a retired warm parent or resume state under the next settings scope', async () => {
  for (const {change,resume} of [
    {change:f => { f.training.preset = 'precision'; }},
    {change:f => { f.compute.transportRevision++; },resume:true},
    {change:f => { f.compute.selectedProfileId = 'server'; f.compute.profiles = [{ id: 'server', name: '새 서버' }]; }},
  ]) {
    const f = fixture(); await f.settle();
    f.change(resume?'정확한 학습 이어가기':'재학습 시작 모델',resume?'/resume/last.pt':'job_parent123');
    await f.settle(); assert.match(f.summary().weights, resume?/중단 작업/:/완료 모델/);
    change(f); assert.equal(f.summary(), null);
    const seen = []; const unsubscribe = f.h.subscribeTrainingSummary(() => { const value = f.summary(); if (value) seen.push(value); });
    await f.settle(); assert.ok(seen.length > 0);
    assert.ok(seen.every(value => !/완료 모델|중단 작업/.test(value.weights)), 'the reset happens before any new-scope publication');
    unsubscribe(); f.unmount();
  }
});

test('the real task handler starts exactly the mounted custom model, checkpoint, parent and epoch settings and returns its job receipt', async () => {
  const f=fixture(),starts=[];f.training.startTraining=async(...args)=>{starts.push(args);f.training.jobId='task-custom-job';};
  await f.settle();f.change('학습 모델 구조','yolo26s');f.change('사전학습 가중치 파일','/local/custom.pt');f.change('수동 학습 횟수','5');await f.settle();f.change('재학습 시작 모델','parent-job');await f.settle();
  const expected=JSON.parse(JSON.stringify(f.summary().executionSettings));
  const result=await f.taskHandler()({isCurrent:()=>true,isPaused:()=>false,isAuthorityCurrent:()=>true});
  assert.equal(result.error,null);assert.equal(result.receipt.job_id,'task-custom-job');assert.deepEqual(result.receipt.settings,expected);
  assert.equal(starts.length,1);assert.equal(starts[0][0],'/images');assert.equal(starts[0][1],'detection');assert.equal(starts[0][2],'parent-job');
  assert.equal(starts[0][3].backbone,'yolo26s');assert.equal(starts[0][3].pretrained_checkpoint,'/local/custom.pt');assert.equal(starts[0][3].epochs,5);
  f.unmount();
});
test('stale and paused task handlers refuse before dispatch and changing published model settings retires an old closure', async () => {
  const f=fixture(),starts=[];f.training.startTraining=async(...args)=>{starts.push(args);f.training.jobId='job';};await f.settle();const old=f.taskHandler();
  assert.match((await old({isCurrent:()=>false,isPaused:()=>false})).error,/바뀌/);
  assert.match((await old({isCurrent:()=>true,isPaused:()=>true})).error,/일시정지/);
  f.change('학습 모델 구조','yolo26s');await f.settle();assert.match((await old({isCurrent:()=>true,isPaused:()=>false})).error,/설정/);assert.equal(starts.length,0);f.unmount();
});


test('saved input rules reach current training settings and never cross source or labelset scope', async () => {
  const f=fixture();f.project.task='classification';f.project.project.task='classification';f.data.datasetKey='/images\0classification';
  const recipe={version:1,operations:[{op:'rotate90',turns:1}]};
  f.training.inputRecipe=recipe;f.training.inputRecipeScope=JSON.stringify(['local','p','default','/images','classification']);
  await f.settle();assert.deepEqual(f.summary().executionSettings.config_overrides.input_recipe,recipe);
  f.project.project.active_labelset_id='other';await f.settle();assert.equal(f.summary().executionSettings.config_overrides.input_recipe,undefined);
});

test('a saved next-training recipe never crosses API transports for an identical project and source',async()=>{
 const f=fixture();f.project.task=f.project.project.task='classification';f.data.datasetKey='/images\0classification';
 const recipe={version:1,operations:[{op:'rotate90',turns:1}]};f.training.inputRecipe=recipe;f.training.inputRecipeScope=JSON.stringify(['local','p','default','/images','classification']);
 await f.settle();assert.deepEqual(f.summary().executionSettings.config_overrides.input_recipe,recipe);
 f.compute.apiIdentity='other-transport';await f.settle();assert.equal(f.summary().executionSettings.config_overrides.input_recipe,undefined);
});


test('preparation recovery requests scoped model settings reveal while current status focuses outside the wizard and guards remain', async () => {
  const previousDocument = global.document;
  try {
    for (const [setup, expected] of [
      [async f => {f.change('수동 학습 횟수', '0');}, 'training-model-settings'],
      [async f => {f.change('학습 시간 제한 (분)', '-1');}, 'training-scheduling-settings'],
      [async f => {f.compute.selectedProfileId = 'gpu'; f.compute.profiles = [{id: 'gpu', name: 'GPU 서버'}];}, 'training-compute-settings'],
      [async f => {f.training.isRecoveringTraining = true;}, 'training-current-status'],
    ]) {
      const f = fixture(); let probeCalls = 0; f.compute.probeProfile = async () => {probeCalls++;};
      await f.settle(); await setup(f); await f.settle();
      const before = f.preparation(); assert.ok(before.inputBlocker); assert.equal(typeof before.onResolveBlocker, 'function');
      const dom = f.dom(); global.document = dom; before.onResolveBlocker();
      const target = dom.getElementById(expected); assert.ok(target, expected); assert.equal(probeCalls, 0, 'recovery opens the existing probe control without running it');
      if(expected==='training-current-status')assert.equal(dom.focused(),target);
      else {
        f.render();const request=f.workflow().revealRequest;
        assert.deepEqual({scope:request?.scope,blockId:request?.blockId,targetId:request?.targetId},{scope:f.workflow().scope,blockId:'model',targetId:expected});
        assert.equal(dom.focused(),null,'the shell owns focus after the hidden pane becomes visible');
      }
      const button = dom.elements.find(element => element.tagName === 'BUTTON' && element.props['aria-describedby'] === 'training-start-reason');
      assert.equal(button?.props.disabled, true, 'navigation does not clear the actual blocking condition'); f.unmount();
    }
  } finally {global.document = previousDocument;}
});


test('training shows preprocessing before model purpose in a stable workflow across viewing modes', async () => {
  const f=fixture();await f.settle();
  const simple=f.dom().elements,find=(rows,name)=>rows.find(node=>node.tagName===name.toUpperCase());
  const input=find(simple,'TrainingInputPreparation'),catalog=find(simple,'ModelFamilyCatalog'),preparation=find(simple,'TrainingPreparationPanel');
  assert.ok(simple.indexOf(input)<simple.indexOf(catalog),'preprocessing comes before choosing the model');
  assert.ok(simple.indexOf(catalog)<simple.indexOf(preparation));
  assert.equal(input.props.disabled,false);const scope=f.workflow().scope;
  f.project.viewMode='full';await f.settle();
  const full=f.dom().elements;
  assert.ok(full.indexOf(find(full,'TrainingInputPreparation'))<full.indexOf(find(full,'ModelFamilyCatalog')));
  assert.equal(f.workflow().scope,scope,'view changes retain the workflow selection and mounted editor');
  f.unmount();
});

test('advanced model fields retain their selected values across mode changes while running stop stays outside disclosures', async () => {
  const f = fixture(); await f.settle();
  f.change('학습 모델 구조', 'yolo26s');
  f.change('수동 학습 횟수', '7'); await f.settle();
  f.project.viewMode = 'full'; await f.settle();
  let nodes = f.dom().elements;
  assert.equal(nodes.find(node => node.props['aria-label'] === '학습 모델 구조').props.value, 'yolo26s');
  assert.equal(nodes.find(node => node.props['aria-label'] === '수동 학습 횟수').props.value, '7');
  f.project.viewMode = 'simple'; f.training.isTraining = true; f.training.status = 'training';
  let stops = 0; f.training.stopTraining = async () => { stops++; }; await f.settle();
  nodes = f.dom().elements;
  const stop = nodes.find(node => node.tagName === 'BUTTON' && node.props.onClick?.name === 'handleAbort');
  assert.ok(stop, 'running job exposes stop');
  let parent = stop.parentElement; while (parent) { assert.notEqual(parent.tagName, 'DETAILS'); parent = parent.parentElement; }
  assert.equal(stop.props.disabled, false); await stop.props.onClick(); assert.equal(stops, 1);
  f.training.isStopRequestPending = true; await f.settle();
  assert.equal(f.dom().elements.find(node => node.tagName === 'BUTTON' && node.props.onClick?.name === 'handleAbort').props.disabled, true);
  f.unmount();
});

test('closing training location cannot hide transfer state or disconnected recovery while a job is active or recovering', async t => {
  const priorWindow = global.window;
  global.window = {setInterval: () => 1, clearInterval: () => {}};
  t.after(() => { global.window = priorWindow; });
  const f = fixture(); await f.settle();
  f.training.jobId = 'remote-job'; f.training.jobComputeProfileId = 'server';
  f.training.isTraining = true; f.training.status = 'transferring'; f.training.transferProgress = 43;
  await f.settle();
  const assertVisible = element => {
    assert.ok(element, 'current job location and recovery remain mounted');
    for (let owner = element.parentElement; owner; owner = owner.parentElement) {
      assert.notEqual(owner.tagName, 'DETAILS', 'manually closing location settings cannot hide current state');
    }
  };
  let nodes = f.dom().elements;
  const current = nodes.find(node => node.props['aria-label'] === '현재 작업 위치와 복구');
  assertVisible(current);
  assert.ok(nodes.some(node => node.props.style?.width === '43%'), 'actual transfer progress remains rendered');
  f.training.status = 'disconnected'; f.training.isRecoveringTraining = true;
  let reconnected = 0; f.training.reconnectCurrentJob = async () => { reconnected++; }; await f.settle();
  nodes = f.dom().elements;
  const text = value => Array.isArray(value) ? value.map(text).join('') : value && typeof value === 'object' ? text(value.props?.children) : String(value ?? '');
  const reconnect = nodes.find(node => node.tagName === 'BUTTON' && text(node.props.children).includes('연결 다시 확인'));
  assertVisible(reconnect); await reconnect.props.onClick(); assert.equal(reconnected, 1);
  f.unmount();
});


test('training starts with connected data preparation before model selection without starting work', async () => {
  const f=fixture();await f.settle();
  assert.ok(f.workflow(),'the preparation workflow is mounted');
  assert.deepEqual(f.workflow().blocks.map(block=>block.id),['photos','labels','split','preprocess','model']);
  assert.match(f.workflow().blocks.find(block=>block.id==='preprocess').summary,/기본 입력/,'detection does not claim unsupported transforms');
  f.data.folderPath='/retired';await f.settle();
  assert.equal(f.workflow().blocks[0].status,'attention','old-source counts cannot mark photos ready');
  f.unmount();
});


test('running or recovering training keeps model selection locked even through a retained callback', async()=>{
 for(const key of ['isTraining','isRecoveringTraining']){
  const f=fixture();await f.settle();const choose=f.catalog().onSelect;
  f.training[key]=true;await f.settle();assert.equal(f.catalog().disabled,true);
  choose('ocr');await f.settle();assert.equal(f.catalog().selectedFamily,'detection');
  f.unmount();
 }
});
test('classification with only one populated class does not mark truth ready',async()=>{
 const f=fixture();f.project.task='classification';f.project.project.task='classification';f.data.datasetKey='/images\0classification';f.data.classes={Only:10};
 await f.settle();assert.equal(f.workflow().blocks.find(block=>block.id==='labels').status,'attention');f.unmount();
});

function blockOwner(element){for(let node=element?.parentElement;node;node=node.parentElement){if(node.props['data-workflow-block'])return node.props['data-workflow-block'];}return null;}
test('the final model block contains next-run location, recipe, advanced controls and the single start button',async()=>{
 const f=fixture();await f.settle();const elements=f.dom().elements;
 for(const id of ['training-compute-settings','training-model-settings','training-scheduling-settings']){const target=elements.find(n=>n.props.id===id);assert.ok(target,id);assert.equal(blockOwner(target),'model',id+' belongs to the final setup pane');}
 const preset=elements.find(n=>n.tagName==='RECIPEPRESETSELECTOR');assert.ok(preset);assert.equal(blockOwner(preset),'model');
 const starts=elements.filter(n=>n.tagName==='BUTTON'&&n.props.onClick?.name==='handleStart');assert.equal(starts.length,1);assert.equal(blockOwner(starts[0]),'model');
 f.change('학습 모델 구조','yolo26s');f.change('수동 학습 횟수','7');await f.settle();const startsCalled=[];f.training.startTraining=async(...args)=>startsCalled.push(args);await f.settle();await f.start();assert.equal(startsCalled.length,1);assert.equal(startsCalled[0][3].backbone,'yolo26s');assert.equal(startsCalled[0][3].epochs,7);f.unmount();
});
test('model catalog browsing does not change the wizard navigation scope',async()=>{
 const f=fixture();await f.settle();const scope=f.workflow().scope;f.catalog().onSelect('ocr');await f.settle();assert.equal(f.workflow().scope,scope);assert.equal(f.catalog().selectedFamily,'ocr');f.unmount();
});
test('catalog changes clear only their fixture handoff and never share browser storage or replace the Node global',async()=>{
 const globalStorage=Object.getOwnPropertyDescriptor(globalThis,'localStorage');
 const first=fixture(),second=fixture();
 assert.ok(first.storage&&second.storage,'each fixture supplies its own browser storage');
 assert.notEqual(first.storage,second.storage);
 const key='vision-task-handoff:["/project","p","/images","default","local","local"]';
 first.storage.setItem(key,'first handoff');second.storage.setItem(key,'second handoff');
 first.storage.setItem('unrelated','keep first');second.storage.setItem('unrelated','keep second');
 await first.settle();await second.settle();first.catalog().onSelect('ocr');await first.settle();
 assert.equal(first.storage.getItem(key),null,'the real controller clears its own scoped handoff');
 assert.equal(second.storage.getItem(key),'second handoff','another mounted fixture keeps the same-scope record');
 assert.equal(first.storage.getItem('unrelated'),'keep first');assert.equal(second.storage.getItem('unrelated'),'keep second');
 second.catalog().onSelect('rotation');await second.settle();assert.equal(second.storage.getItem(key),null);
 assert.deepEqual(Object.getOwnPropertyDescriptor(globalThis,'localStorage'),globalStorage,'no ambient Node storage is installed or replaced');
 first.unmount();second.unmount();
});
test('core job status, failure, logs and saved history stay outside wizard panels when browsing another model',async()=>{
 const f=fixture();await f.settle();f.training.jobId='finished-core';f.training.status='completed';f.training.startError='Current core failure';f.savedJob.job={job_id:'saved-core',status:'completed'};f.savedJob.error='Saved history read failure';await f.settle();f.catalog().onSelect('ocr');await f.settle();
 const elements=f.dom().elements;const current=elements.find(n=>n.props.id==='training-current-status');assert.ok(current,'core status remains after specialty browsing');assert.equal(blockOwner(current),null);
 const log=elements.find(n=>n.props['aria-label']==='학습 로그');assert.ok(log);assert.equal(blockOwner(log),null);
 const saved=elements.find(n=>n.props['aria-label']==='선택한 저장 학습 작업');assert.ok(saved);assert.equal(blockOwner(saved),null);
 assert.ok(f.alerts().some(value=>value.includes('Current core failure')));assert.ok(f.alerts().some(value=>value.includes('Saved history read failure')));f.unmount();
});
test('repeated settings remediation requests carry a fresh sequence and the current authority scope',async()=>{
 const f=fixture();await f.settle();f.change('수동 학습 횟수','0');await f.settle();f.preparation().onResolveBlocker();f.render();const first=f.workflow().revealRequest;assert.ok(first);
 f.preparation().onResolveBlocker();f.render();const second=f.workflow().revealRequest;assert.ok(second.sequence>first.sequence);assert.equal(second.targetId,'training-model-settings');
 f.project.project.active_labelset_id='new';await f.settle();assert.notEqual(f.workflow().scope,first.scope,'a retired reveal cannot own the next labelset');f.unmount();
});

test('an idle workspace keeps empty runtime telemetry out of setup, while recovery still exposes current status',async()=>{
 const f=fixture();await f.settle();assert.equal(f.dom().getElementById('training-current-status'),null);assert.equal(f.dom().elements.some(n=>n.tagName==='OSCILLOSCOPELOSSCURVE'),false);
 f.training.isRecoveringTraining=true;await f.settle();assert.ok(f.dom().getElementById('training-current-status'));f.unmount();
});

test('settings remediation from a specialty catalog choice reveals the actual core settings without changing project task',async()=>{
 const f=fixture();await f.settle();f.compute.selectedProfileId='gpu';f.compute.profiles=[{id:'gpu',name:'GPU 서버'}];await f.settle();f.catalog().onSelect('ocr');await f.settle();
 const button=f.dom().elements.find(n=>n.tagName==='BUTTON'&&n.props.children==='학습 위치 확인');assert.ok(button);button.props.onClick();f.render();
 assert.equal(f.catalog().selectedFamily,f.project.task);assert.equal(f.workflow().revealRequest.targetId,'training-compute-settings');assert.equal(f.project.task,'detection');f.unmount();
});

test('simple view keeps settings recovery outside a hidden model pane',async()=>{
 const f=fixture();await f.settle();f.project.viewMode='simple';await f.settle();f.change('수동 학습 횟수','0');await f.settle();
 const recovery=f.dom().elements.find(n=>n.tagName==='BUTTON'&&n.props.children==='모델 설정 확인');assert.ok(recovery,'global remediation remains reachable on another wizard block');assert.equal(blockOwner(recovery),null);f.unmount();
});

test('retired recovery callbacks cannot change the next project model selection',async()=>{
 const f=fixture();await f.settle();f.change('수동 학습 횟수','0');await f.settle();const recover=f.preparation().onResolveBlocker;
 f.project.task='classification';f.project.project={...f.project.project,id:'next',task:'classification'};f.data.datasetKey='/images\0classification';await f.settle();
 assert.equal(f.catalog().selectedFamily,'classification');recover();f.render();assert.equal(f.catalog().selectedFamily,'classification');assert.equal(f.workflow().revealRequest,undefined);f.unmount();
});
test('recovery reads live ownership before a React render and refuses changed source, actor, API or transport',async()=>{
 for(const mutate of [f=>{f.project.project.source_dataset_dir='/next';f.data.folderPath='/next';f.data.datasetKey='/next\0detection';},f=>{f.data.currentAuthority='new-actor';},f=>{f.compute.apiIdentity='new-api';f.data.currentAuthority='new-api-authority';},f=>{f.compute.transportRevision++;f.compute.selectedProfileId='next-profile';}]){
  const f=fixture();await f.settle();f.change('수동 학습 횟수','0');await f.settle();const recover=f.preparation().onResolveBlocker;
  f.catalog().onSelect('ocr');f.render();mutate(f);recover();f.render();assert.equal(f.catalog().selectedFamily,'ocr','a stale callback cannot change parent selection before a render');assert.equal(f.workflow().revealRequest,undefined);f.unmount();
 }
});


test('the mounted controller routes explicit model and preprocessing targets through its current React owner',async()=>{
 const previous=global.document;global.document={};
 try{const f=fixture();await f.settle();f.catalog().onSelect('ocr');await f.settle();
  f.focusTarget('training-next-settings',()=>true);f.render();assert.equal(f.catalog().selectedFamily,'detection');
  assert.equal(f.workflow().revealRequest.blockId,'model');assert.equal(f.workflow().revealRequest.targetId,'training-next-settings');
  f.focusTarget('training-input-preparation',()=>true);f.render();assert.equal(f.workflow().revealRequest.blockId,'preprocess');
  assert.equal(f.workflow().revealRequest.isCurrent(),true);f.unmount();assert.equal(f.workflow().revealRequest.isCurrent(),false);
 }finally{global.document=previous;}
});

test('the registered controller owner refuses live authority changes before a replacement render',async()=>{
 const previous=global.document;global.document={};
 try{for(const mutate of [f=>{f.project.task='classification';},f=>{f.project.project.active_labelset_id='new-labels';},f=>{f.data.folderPath='/next';},f=>{f.data.currentAuthority='new-actor';},f=>{f.compute.transportRevision++;}]){
  const f=fixture();await f.settle();f.catalog().onSelect('ocr');await f.settle();mutate(f);
  f.focusTarget('training-next-settings',()=>true);f.render();assert.equal(f.catalog().selectedFamily,'ocr');assert.equal(f.workflow().revealRequest,undefined);f.unmount();
 }}finally{global.document=previous;}
});
