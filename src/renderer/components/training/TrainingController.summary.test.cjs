const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const Module = require('node:module'), test = require('node:test'), ts = require('typescript');

function fixture({warmParentsFailure = null, resumeFailure = null, deferResume = false} = {}) {
  const slots = [], effects = [], cache = new Map(), resumeReads = [], taskHandlers = new Map(); let cursor = 0, tree;
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
    setPreset() {}, lossHistory: [], hardware: {}, startTraining: async () => assert.fail('summary never starts training'), stopTraining: async () => {},
    recoverActiveJob: async () => {}, refreshCurrentJob: async () => {}, reconnectCurrentJob: async () => {}, setNextSettings() {} };
  const compute = { profiles: [], selectedProfileId: null, transportRevision: 1, isLoaded: true, isLoading: false, loadError: null, error: null, probeResults: {}, probePendingId: null, probeProfile: async () => {} };
  const authority = () => JSON.stringify([project.projectDir, project.project.id, project.project.source_dataset_dir, project.project.active_labelset_id]);
  function load(file, real = false) {
    const filename = path.resolve(__dirname, file);
    if (cache.has(filename)) return cache.get(filename).exports;
    const m = new Module(filename, module); m.filename = filename; m.paths = Module._nodeModulePaths(path.dirname(filename)); cache.set(filename, m);
    const req = m.require.bind(m);
    m.require = name => {
      if (name === './trainingStartSummary' || name === './modelTrainingOptions') return load(name.slice(2) + '.ts', true);
      if (name === 'react') return react;
      if (name.endsWith('/assistContext')) return load('../../stores/assistContext.ts', true);
      if (name.endsWith('/useScopedError')) return load('../common/useScopedError.ts', true);
      if (real) return req(name);
      if (name === 'lucide-react') return new Proxy({}, { get: (_, key) => String(key) });
      if (name.endsWith('/useProjectStore')) return { useProjectStore: hook(project) };
      if (name.endsWith('/useDatasetStore')) return { useDatasetStore: hook(data), readDatasetAuthority: () => 'authority' };
      if (name.endsWith('/useTrainingStore')) return { useTrainingStore: hook(training) };
      if (name.endsWith('/useComputeStore')) return { useComputeStore: hook(compute) };
      if (name.endsWith('/services/api')) return { api: { training: { warmStartParents: async () => {if (warmParentsFailure) throw Error(warmParentsFailure); return { parents: [], total: 0 };} } }, request: async () => {if (deferResume) return new Promise((resolve, reject) => resumeReads.push({resolve, reject})); if (resumeFailure) throw Error(resumeFailure); return { states: [{checkpoint_path:'/resume/last.pt',device:'cuda',recipe:{backbone:'yolo26n',epochs:12},next_epoch:2,global_step:4}], excluded: [] };}, getApiPersistenceIdentity: () => 'local' };
      if (name.endsWith('/autoRunChannel')) return { registerAutoRun: () => () => {}, registerTaskAutoRun: (kind, handler, settings) => {const record={handler,settings};taskHandlers.set(kind,record);return()=>{if(taskHandlers.get(kind)===record)taskHandlers.delete(kind);};} };
      if (name.endsWith('/datasetSplitCapability')) return { isSplitUnavailable: () => false };
      if (name.endsWith('/trainingComputeReadiness')) return { trainingComputeReadiness: () => ({ ready: true }) };
      if (name === './useTaskHandoff') return { useTaskHandoff: () => null };
      if (name === './useSelectedCoreJob') return { useSelectedCoreJob: () => ({ job: null, error: null }) };
      if (name === './TrainingSchedulingSettings') return { defaultTrainingScheduling: {}, trainingSchedulingOptions: () => ({}), TrainingSchedulingSettings: 'TrainingSchedulingSettings' };
      if (name.endsWith('/errorActions')) return { trainingPresetBatchSize: () => 8 };
      if (name === './trainingLog') return { trainingLogLines: () => [] };
      if (name.endsWith('/TaskChangeImpactDialog')) return { taskPreviewScope: authority, TaskChangeImpactDialog: 'TaskChangeImpactDialog' };
      if (name.endsWith('/importChecklist')) return { classGaps: () => ({ thin: [] }), classStartBlocker: () => null, splitMissesLabels: () => false };
      return new Proxy({}, { get: (_, key) => String(key) });
    };
    m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React, esModuleInterop: true,
    } }).outputText, filename);
    return m.exports;
  }
  const h = load('trainingStartSummary.ts', true), shared = load('../../stores/assistContext.ts', true), { TrainingController } = load('TrainingController.tsx');
  const nodes = value => Array.isArray(value) ? value.flatMap(nodes) : value && typeof value === 'object' ? [value, ...nodes(value.props?.children)] : [];
  function render() { cursor = 0; tree = TrainingController(); }
  const scope = () => h.trainingSummaryScope(authority(), { task: project.task, folderPath: data.folderPath, datasetKey: data.datasetKey,
    preset: training.preset, nextDevice: training.nextDevice, nextBatchSize: training.nextBatchSize, selectedProfileId: compute.selectedProfileId,
    profile: compute.profiles.find(row => row.id === compute.selectedProfileId), transportRevision: compute.transportRevision,
    computeLoaded: compute.isLoaded, computeLoading: compute.isLoading, computeLoadError: compute.loadError });
  return { project, data, training, compute, h, shared, scope, render, resumeReads, taskHandler: () => taskHandlers.get('train')?.handler,
    async settle() { for (let i = 0; i < 8; i++) { render(); effects.splice(0).forEach(effect => effect()); await new Promise(setImmediate); } },
    change(label, value) { const field = nodes(tree).find(node => node.props?.['aria-label'] === label); assert.ok(field, label); field.props.onChange({ target: { value } }); },
    summary: () => h.currentTrainingSummary(scope()),
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
