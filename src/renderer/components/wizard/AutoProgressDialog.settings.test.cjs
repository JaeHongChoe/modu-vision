const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const ts = require('typescript');

function fixture() {
  const project = { task: 'detection', activeStep: 1, projectDir: '/project', isProjectBusy: false,
    project: { id: 'project-1', source_dataset_dir: '/images', active_labelset_id: 'default' } };
  const dataset = { folderPath: '/images', datasetKey: '/images\0detection', trainRatio: .8, isLoading: false, importError: null };
  const training = { preset: 'precision', nextDevice: 'cpu', nextBatchSize: null };
  const compute = { profiles: [], selectedProfileId: null, transportRevision: 1, isLoaded: true, isLoading: false, loadError: null };
  let authority = 'local:context-1', children, closes = 0;
  const steps = [];
  project.setStep = async step => { steps.push(step); project.activeStep = step; };
  const hook = state => Object.assign(selector => selector ? selector(state) : state, { getState: () => state });
  const cache = new Map();
  const helperPath = path.resolve(__dirname, '../training/trainingStartSummary.ts');
  const helper = () => fs.existsSync(helperPath) ? load(helperPath) : {};
  function load(filename) {
    if (cache.has(filename)) return cache.get(filename).exports;
    const m = new Module(filename, module); m.filename = filename; m.paths = Module._nodeModulePaths(path.dirname(filename));
    cache.set(filename, m);
    const req = m.require.bind(m);
    m.require = name => {
      if (name.endsWith('/useProjectStore')) return { useProjectStore: hook(project) };
      if (name.endsWith('/useDatasetStore')) return { useDatasetStore: hook(dataset) };
      if (name.endsWith('/useTrainingStore')) return { useTrainingStore: hook(training) };
      if (name.endsWith('/useComputeStore')) return { useComputeStore: hook(compute) };
      if (name.endsWith('/useEvaluationStore') || name.endsWith('/useFlowchartStore')) return {};
      if (name.endsWith('/useAnnotationStore')) return { useAnnotationStore: hook({ reviewerName: '' }) };
      if (name.endsWith('/services/api')) return { api: {}, subscribeProjectContext: () => () => {} };
      if (name.endsWith('/services/flowEvaluation')) return { flowEvaluation: {} };
      if (name.endsWith('/stores/assistContext')) return { currentAssistContext: () => null };
      if (name.endsWith('/stores/autoRunChannel')) return {};
      if (name.endsWith('/stores/workflowEvidence')) return { bumpWorkflowEvidence() {} };
      if (name.endsWith('/WorkspaceDialog')) return { WorkspaceDialog: props => { children = props.children; return React.createElement('div', { role: 'dialog' }, props.children); } };
      if (name === './workflowReadiness') return { focusWorkflowTarget() {} };
      if (name.endsWith('/TaskChangeImpactDialog')) return { taskPreviewScope: () => JSON.stringify([authority, project.projectDir, project.project.id, project.project.active_labelset_id, project.project.source_dataset_dir]) };
      if (name.startsWith('.')) {
        const base = path.resolve(path.dirname(filename), name);
        return load(fs.existsSync(base + '.ts') ? base + '.ts' : base + '.tsx');
      }
      return req(name);
    };
    m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    }).outputText, filename);
    return m.exports;
  }
  const dialog = load(path.join(__dirname, 'AutoProgressDialog.tsx'));
  const nodes = value => Array.isArray(value) ? value.flatMap(nodes) : value && typeof value === 'object' ? [value, ...nodes(value.props?.children)] : [];
  const text = value => value == null || typeof value === 'boolean' ? '' : typeof value === 'object' ? Array.isArray(value) ? value.map(text).join('') : text(value.props?.children) : String(value);
  return { project, dataset, training, compute, steps, helper,
    setAuthority(value) { authority = value; },
    scope() { return helper().trainingSummaryScope(JSON.stringify([authority, project.projectDir, project.project.id, project.project.active_labelset_id, project.project.source_dataset_dir]), {
      task: project.task, folderPath: dataset.folderPath, datasetKey: dataset.datasetKey, preset: training.preset,
      nextDevice: training.nextDevice, nextBatchSize: training.nextBatchSize, selectedProfileId: compute.selectedProfileId,
      profile: compute.profiles.find(row => row.id === compute.selectedProfileId), transportRevision: compute.transportRevision,
      computeLoaded: compute.isLoaded, computeLoading: compute.isLoading, computeLoadError: compute.loadError,
    }); },
    render() { return renderToStaticMarkup(React.createElement(dialog.AutoProgressDialog, { onClose: () => { closes++; } })); },
    settingsButton() { return nodes(children).find(node => node.type === 'button' && /학습 설정 확인·변경/.test(text(node))); },
    closes: () => closes,
  };
}

test('before starting away from step 3 the dialog names real default model, current preset/device and unverified weights', () => {
  const f = fixture(); const html = f.render();
  assert.match(html, /분할 80\/10\/10/);
  assert.match(html, /YOLO26 nano/);
  assert.match(html, /정밀 후보 학습/);
  assert.match(html, /CPU/);
  assert.match(html, /가중치[^<]*준비 확인 필요/);
  assert.ok(f.settingsButton(), 'a direct training settings entry is visible before the start');
  f.training.nextDevice = null;
  assert.match(f.render(), /자동 감지/);
  assert.doesNotMatch(f.render(), /가중치[^<]*준비 완료/);
});

test('matching step-3 selections are read, while leaving step 3 or changing authority/source/settings retires them', () => {
  const mutations = [
    f => { f.project.activeStep = 2; },
    f => { f.project.project.id = 'project-2'; },
    f => { f.project.project.active_labelset_id = 'other'; },
    f => { f.dataset.folderPath = '/new'; f.dataset.datasetKey = '/new\0detection'; f.project.project.source_dataset_dir = '/new'; },
    f => { f.dataset.datasetKey = 'not-current'; },
    f => { f.training.preset = 'fast'; },
    f => { f.training.nextDevice = 'mps'; },
    f => { f.training.nextBatchSize = 4; },
    f => { f.compute.transportRevision++; },
    f => { f.compute.selectedProfileId = 'server'; f.compute.profiles = [{ id: 'server', name: '새 서버', gpu_selector: '2' }]; },
    f => { f.compute.isLoaded = false; },
    f => f.setAuthority('shared:context-2'),
    f => { f.project.isProjectBusy = true; },
  ];
  for (const mutate of mutations) {
    const f = fixture(); f.project.activeStep = 3;
    const h = f.helper(); assert.equal(typeof h.publishTrainingSummary, 'function', 'the mounted screen can publish its selection');
    h.publishTrainingSummary(f.scope(), { model: 'YOLO26 small', weights: '지정 파일 selected.pt · 준비 확인 필요', device: 'CUDA' });
    const current = f.render(); assert.match(current, /YOLO26 small/); assert.match(current, /selected\.pt/); assert.match(current, /CUDA/);
    mutate(f);
    const retired = f.render(); assert.doesNotMatch(retired, /YOLO26 small|selected\.pt/);
    assert.match(retired, /가중치[^<]*준비 확인 필요/);
  }
});

test('a training settings entry waits for safe step navigation and leaves the dialog open when navigation is refused', async () => {
  const f = fixture(); f.render(); const button = f.settingsButton(); assert.ok(button);
  let resolve;
  f.project.setStep = step => { f.steps.push(step); return new Promise(done => { resolve = done; }); };
  const pending = button.props.onClick(); assert.deepEqual(f.steps, [3]); assert.equal(f.closes(), 0);
  resolve(); await pending; assert.equal(f.closes(), 0, 'unsaved-label refusal keeps the settings dialog in place');
  f.project.setStep = async step => { f.steps.push(step); f.project.activeStep = step; };
  await button.props.onClick(); assert.equal(f.closes(), 1);
});

test('a replaced or unmounted training screen cannot leave a readable old summary', () => {
  const f = fixture(), h = f.helper();
  assert.equal(typeof h.publishTrainingSummary, 'function');
  let changes = 0; const unsubscribe = h.subscribeTrainingSummary(() => { changes++; });
  const old = h.publishTrainingSummary('scope', { model: 'old', weights: 'old' });
  const latest = h.publishTrainingSummary('scope', { model: 'new', weights: 'new' });
  old(); assert.equal(h.currentTrainingSummary('scope').model, 'new');
  assert.equal(h.currentTrainingSummary('different'), null);
  latest(); assert.equal(h.currentTrainingSummary('scope'), null); assert.equal(changes, 3);
  unsubscribe();
});

test('a settings navigation finishing after a project switch cannot close the new project dialog', async () => {
  const f = fixture(); f.render(); const button = f.settingsButton(); assert.ok(button);
  let resolve;
  f.project.setStep = () => new Promise(done => { resolve = done; });
  const pending = button.props.onClick();
  f.project.project.id = 'project-2'; f.project.activeStep = 3;
  resolve(); await pending;
  assert.equal(f.closes(), 0);
});
