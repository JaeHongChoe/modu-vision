const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const Module = require('node:module'), test = require('node:test'), ts = require('typescript');

function fixture() {
  const slots = [], effects = [], cache = new Map(); let cursor = 0, tree;
  const react = { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }), Fragment: 'Fragment',
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
    project: { id: 'p', source_dataset_dir: '/images', active_labelset_id: 'default' }, isProjectBusy: false, technicalDetails: false, viewMode: 'simple' };
  const data = { folderPath: '/images', totalImages: 10, split: { train: 6, val: 2, test: 2 }, isLoading: false, isSplitting: false,
    importError: null, splitError: null, splitSupported: true, splitUnavailableReason: null, datasetKey: '/images\0detection', splitState: 'saved', classes: {}, classSplitCounts: null };
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
      if (real) return req(name);
      if (name === 'react') return react;
      if (name === 'lucide-react') return new Proxy({}, { get: (_, key) => String(key) });
      if (name.endsWith('/useProjectStore')) return { useProjectStore: hook(project) };
      if (name.endsWith('/useDatasetStore')) return { useDatasetStore: hook(data) };
      if (name.endsWith('/useTrainingStore')) return { useTrainingStore: hook(training) };
      if (name.endsWith('/useComputeStore')) return { useComputeStore: hook(compute) };
      if (name.endsWith('/services/api')) return { api: { training: { warmStartParents: async () => ({ parents: [], total: 0 }) } }, request: async () => ({ states: [{checkpoint_path:'/resume/last.pt',device:'cuda',recipe:{backbone:'yolo26n',epochs:12},next_epoch:2,global_step:4}], excluded: [] }), getApiPersistenceIdentity: () => 'local' };
      if (name.endsWith('/autoRunChannel')) return { registerAutoRun: () => () => {} };
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
  const h = load('trainingStartSummary.ts', true), { TrainingController } = load('TrainingController.tsx');
  const nodes = value => Array.isArray(value) ? value.flatMap(nodes) : value && typeof value === 'object' ? [value, ...nodes(value.props?.children)] : [];
  function render() { cursor = 0; tree = TrainingController(); }
  const scope = () => h.trainingSummaryScope(authority(), { task: project.task, folderPath: data.folderPath, datasetKey: data.datasetKey,
    preset: training.preset, nextDevice: training.nextDevice, nextBatchSize: training.nextBatchSize, selectedProfileId: compute.selectedProfileId,
    profile: compute.profiles.find(row => row.id === compute.selectedProfileId), transportRevision: compute.transportRevision,
    computeLoaded: compute.isLoaded, computeLoading: compute.isLoading, computeLoadError: compute.loadError });
  return { project, data, training, compute, h, scope, render,
    async settle() { for (let i = 0; i < 8; i++) { render(); effects.splice(0).forEach(effect => effect()); await new Promise(setImmediate); } },
    change(label, value) { const field = nodes(tree).find(node => node.props?.['aria-label'] === label); assert.ok(field, label); field.props.onChange({ target: { value } }); },
    summary: () => h.currentTrainingSummary(scope()),
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
