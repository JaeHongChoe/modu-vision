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
    : key.endsWith('/classSemantics') ? load(path.resolve(path.dirname(name), key) + '.ts') : req(key);
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
function fixture() {
  const training = { isCurrentData: true, status: 'completed', jobId: 'job' };
  const results = { job_id: 'job', class_semantics: semantics, metrics: { accuracy: 0 }, test_predictions: samples };
  const api = { evaluation: {
    getResults: async () => results,
    getOverkillUnderkill: async () => ({ optimal_threshold: 0.2 }),
    getHeatmap: async () => ({ overlay_base64: null }),
  } };
  const m = load('useEvaluationStore.ts', {
    './useTrainingStore': { useTrainingStore: { getState: () => training } },
    '../services/api': { api, getApiBaseUrl: async () => 'http://test' },
  });
  return { ...m, state: () => m.useEvaluationStore.getState(), api, training, results };
}

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
    f.useEvaluationStore.setState({ testPredictions: samples });
    await f.state().calibrateZeroEscape();
    assert.equal(requests.length, 1, 'scratch is normal by the recorded role even though its alias sounds defective');
    assert.equal(requests[0].body.job_id, 'job');
    assert.equal(f.state().confidenceThreshold, 0.2);
  } finally { global.fetch = previousFetch; f.state().invalidateForDataChange(); }
});

function renderStudio(storeState) {
  const jsx = (type, props) => ({ type, props: props || {}, children: [props?.children].flat(Infinity).filter(v => v !== null && v !== undefined && v !== false) });
  const react = { useEffect() {}, useMemo: fn => fn(), useState: initial => [initial, () => {}] };
  const project = { language: 'ko', task: 'classification', projectDir: '/project', setStep() {} };
  const dataset = { folderPath: '/source', datasetKey: '/source\0classification', isLoading: false, importError: null };
  const evaluation = fixture();
  const state = { ...evaluation.state(), ...storeState };
  const useEvaluationStore = () => state;
  useEvaluationStore.getState = () => state;
  const m = load('../components/evaluation/EvaluationStudio.tsx', {
    'react': react, 'react/jsx-runtime': { jsx, jsxs: jsx }, 'lucide-react': {},
    '../training/useTaskHandoff': { useTaskHandoff: () => null },
    '../../stores/useProjectStore': { useProjectStore: Object.assign(sel => sel ? sel(project) : project, { getState: () => project }) },
    '../../stores/useDatasetStore': { useDatasetStore: sel => sel(dataset) },
    '../../stores/useTrainingStore': { useTrainingStore: sel => sel({ status: 'idle' }) },
    '../../stores/useEvaluationStore': { ...evaluation, useEvaluationStore },
    '../../services/api': { resolveApiUrl: value => value },
    '../common/OperatorGuidanceBanner': {}, '../common/JargonTooltip': {}, '../common/GuardrailBanner': {},
    './ZeroEscapeTradeoffChart': {}, './SynchronizedDualViewport': {},
    './DetectionEvaluationGrains': {}, './ModelComparisonPanel': {}, './ModelDeploymentPanel': {},
    './EvaluationHistoryPanel': {}, './EvaluationEvidencePanel': {},
  });
  const tree = m.EvaluationStudio();
  const nodes = [];
  const walk = node => { if (node && typeof node === 'object') { nodes.push(node); node.children?.forEach(walk); } };
  walk(tree);
  const text = node => typeof node === 'string' || typeof node === 'number' ? String(node) : (node?.children || []).map(text).join('');
  return { nodes, text };
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
