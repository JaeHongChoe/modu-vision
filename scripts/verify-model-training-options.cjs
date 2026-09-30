/** Exercise native training choices and parent selector request/context behavior. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');

function load(relative, stubs = {}) {
  const filename = path.join(root, relative);
  const m = new Module(filename, module);
  m.filename = filename;
  m.paths = Module._nodeModulePaths(path.dirname(filename));
  const original = m.require.bind(m);
  m.require = name => name in stubs ? stubs[name] : original(name);
  m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true},
  }).outputText, filename);
  return m.exports;
}

function harness(relative, exportName) {
  const slots = [], effects = [], requests = [];
  let cursor = 0;
  const project = {projectDir: '/projectA', project: {id: 'A', source_dataset_dir: '/dataA', active_labelset_id: 'default'}};
  const react = {
    useState(initial) {const i = cursor++; if (!(i in slots)) slots[i] = initial;
      return [slots[i], value => {slots[i] = typeof value === 'function' ? value(slots[i]) : value;}];},
    useEffect(fn, deps) {const i = cursor++, previous = slots[i];
      if (!previous || deps.some((v, n) => v !== previous.deps[n])) effects.push(() => {
        previous?.cleanup?.(); slots[i] = {deps, cleanup: fn()};
      });},
  };
  const props = {family: 'ocr', datasetPath: '/dataA', value: '', disabled: false,
    onChange(value) {props.value = value;}};
  const component = load(relative, {react,
    '../../stores/useProjectStore': {useProjectStore: selector => selector(project)},
    '../../services/api': {request(url) {return new Promise((resolve, reject) => requests.push({url, resolve, reject}));}},
  })[exportName];
  function render() {cursor = 0; const tree = component(props); effects.splice(0).forEach(fn => fn()); return tree;}
  return {render, props, project, requests};
}
function find(tree, predicate) {
  if (Array.isArray(tree)) return tree.flatMap(x => find(x, predicate));
  if (!tree || typeof tree !== 'object') return [];
  return [...(predicate(tree) ? [tree] : []), ...find(tree.props?.children, predicate)];
}
const flush = async () => {await Promise.resolve(); await Promise.resolve(); await Promise.resolve();};
const parent = id => ({parents: [{job_id: id, checkpoint_sha256: 'a'.repeat(64), semantics: 'weight_initialization'}]});

test('CLS/SEG use DINO and detection uses YOLO with task-specific backend fields', () => {
  const {modelChoices, trainingModelOverrides: options} = load('src/renderer/components/training/modelTrainingOptions.ts');
  assert.deepEqual(options('classification', modelChoices.classification[0].value), {backbone: 'dinov3_vits16'});
  assert.deepEqual(options('segmentation', modelChoices.segmentation[0].value), {model_name: 'dinov3_vits16'});
  assert.deepEqual(options('detection', modelChoices.detection[0].value), {backbone: 'yolo26n'});
  assert.deepEqual(options('anomaly', 'patchcore'), {anomaly_method: 'patchcore'});
  assert.deepEqual(options('segmentation', 'dinov3_vitb16', ' /weights/dino.safetensors '),
    {model_name: 'dinov3_vitb16', pretrained_checkpoint: '/weights/dino.safetensors'});
  assert.throws(() => options('segmentation', 'yolo26n'), /검사 종류와 맞는 모델/);
});

test('server readiness depends on selected model, not unrelated cached weights', () => {
  const {trainingComputeReadiness: ready} = load('src/renderer/utils/trainingComputeReadiness.ts');
  const probe = {ready: true, runtime_ready: true, checks: {
    runtime_dependencies: {timm: true, safetensors: true, huggingface_hub: true, ultralytics: true},
    model_dependencies: {dinov3_vits16: true, yolo26n: true}, pretrained_weights: {},
  }};
  assert.equal(ready(probe, 'segmentation', 'fast', {model_name: 'dinov3_vits16'}).ready, true);
  assert.equal(ready(probe, 'detection', 'fast', {backbone: 'yolo26n'}).ready, true);
  assert.equal(ready(probe, 'classification', 'fast', {backbone: 'resnet18'}).ready, false);
  assert.equal(ready(probe, 'classification', 'fast', {backbone: 'resnet18'}, true).ready, true);
  assert.equal(ready({...probe, checks: {...probe.checks, model_dependencies: {dinov3_vits16: false}}},
    'segmentation', 'fast', {model_name: 'dinov3_vits16'}).ready, false);
  assert.equal(ready({...probe, runtime_ready: false}, 'segmentation', 'fast', {model_name: 'dinov3_vits16'}).ready, false);
  const missing = ready({...probe, checks: {...probe.checks, runtime_dependencies: {timm: false}}},
    'segmentation', 'fast', {model_name: 'dinov3_vits16'});
  assert.equal(missing.ready, false); assert.match(missing.reason, /timm/);
});

test('synthetic anomaly choices emit native geometry and reject incompatible options', () => {
  const {modelChoices, trainingModelOverrides: options} = load('src/renderer/components/training/modelTrainingOptions.ts');
  assert.ok(modelChoices.anomaly.some(choice => choice.value === 'dino_synthetic'));
  assert.deepEqual(options('anomaly', 'dino_synthetic'), {anomaly_method: 'dino_synthetic',
    anomaly_backbone: 'dinov3_vits16', patch_size: 256, stride: 128, patches_per_image: 8, inference_batch_size: 32});
  assert.equal(options('anomaly', 'dino_synthetic', ' /weights/approved.safetensors ',
    {anomaly_backbone: 'dinov3_vitl16', patch_size: 512, stride: 256}).pretrained_checkpoint, '/weights/approved.safetensors');
  assert.throws(() => options('anomaly', 'dino_synthetic', '', {patch_size: 255}), /패치/);
  assert.throws(() => options('anomaly', 'dino_synthetic', '', {stride: 257}), /간격/);
  assert.throws(() => options('anomaly', 'dino_synthetic', '', {anomaly_backbone: 'resnet18'}), /DINO/);
});

test('synthetic anomaly remote readiness requires its selected DINO backbone', () => {
  const {trainingComputeReadiness: ready} = load('src/renderer/utils/trainingComputeReadiness.ts');
  const probe = {ready: true, runtime_ready: true, checks: {
    runtime_dependencies: {timm: true, safetensors: true, huggingface_hub: true},
    model_dependencies: {dinov3_vitl16: true}, pretrained_weights: {},
  }};
  assert.equal(ready(probe, 'anomaly', 'fast', {anomaly_method: 'dino_synthetic', anomaly_backbone: 'dinov3_vitl16'}).ready, true);
  const unsupported = ready(probe, 'anomaly', 'fast', {anomaly_method: 'dino_synthetic', anomaly_backbone: 'dinov3_vitb16'});
  assert.equal(unsupported.ready, false); assert.match(unsupported.reason, /dinov3_vitb16/);
});

test('parent selector serializes synthetic backbone and geometry as query fields', async () => {
  const realApi = load('src/renderer/services/api.ts');
  realApi.setCachedPort(19191);
  const previousFetch = global.fetch;
  let seen;
  global.fetch = async url => {seen = new URL(url); return {ok: true, json: async () => ({parents: [], total: 0})};};
  try {
    await realApi.api.training.warmStartParents('/data', 'anomaly', 'fast', {
      anomaly_method: 'dino_synthetic', anomaly_backbone: 'dinov3_vitl16', patch_size: 512, stride: 256});
    assert.equal(seen.searchParams.get('anomaly_backbone'), 'dinov3_vitl16');
    assert.equal(seen.searchParams.get('patch_size'), '512');
    assert.equal(seen.searchParams.get('stride'), '256');
  } finally {global.fetch = previousFetch;}
});

test('synthetic native geometry controls deliver original-pixel options', () => {
  let component;
  try {component = load('src/renderer/components/training/DinoSyntheticOptions.tsx').DinoSyntheticOptions;}
  catch (error) {if (error.code !== 'ENOENT') throw error;}
  assert.equal(typeof component, 'function', 'Synthetic anomaly geometry controls must be reachable');
  const initial = {anomaly_backbone: 'dinov3_vits16', patch_size: 256, stride: 128, patches_per_image: 8, inference_batch_size: 32};
  const emitted = [];
  const tree = component({options: initial, disabled: false, onChange: value => emitted.push(value)});
  const backbone = find(tree, n => n.type === 'select')[0];
  assert.ok(find(backbone, n => n.type === 'option').some(n => n.props.value === 'dinov3_vitl16'));
  backbone.props.onChange({target: {value: 'dinov3_vitl16'}});
  assert.equal(emitted[0].anomaly_backbone, 'dinov3_vitl16');
  const patch = find(tree, n => n.type === 'input' && n.props['aria-label'] === '원본 패치 크기')[0];
  assert.equal(patch.props.step, 16);
  patch.props.onChange({target: {value: '512'}});
  assert.equal(emitted[1].patch_size, 512);
});

test('training screen forwards selected synthetic geometry and blocks invalid native options', async () => {
  const slots = [], effects = [], starts = [], parents = [];
  let cursor = 0;
  const react = {
    useState(initial) {const index = cursor++; if (!(index in slots)) slots[index] = initial;
      return [slots[index], value => {slots[index] = typeof value === 'function' ? value(slots[index]) : value;}];},
    useEffect(fn, deps) {const index = cursor++, previous = slots[index];
      if (!previous || deps.some((value, n) => value !== previous.deps[n])) effects.push(() => {
        previous?.cleanup?.(); slots[index] = {deps, cleanup: fn()};
      });},
  };
  const noOp = () => {};
  const optionsModule = load('src/renderer/components/training/modelTrainingOptions.ts');
  const Options = load('src/renderer/components/training/DinoSyntheticOptions.tsx').DinoSyntheticOptions;
  const stubs = {react,
    '../../stores/useProjectStore': {useProjectStore: () => ({task: 'anomaly', language: 'ko', setStep: noOp,
      projectDir: '/project', project: {id: 'project', active_labelset_id: 'default'}})},
    '../../stores/useDatasetStore': {useDatasetStore: () => ({folderPath: '/data', totalImages: 2,
      split: {train: 1, val: 1}, datasetKey: '/data\0anomaly', splitSupported: false})},
    '../../stores/useComputeStore': {useComputeStore: () => ({profiles: [], probeResults: {}, isLoaded: true})},
    '../../stores/useTrainingStore': {useTrainingStore: () => ({preset: 'fast', status: 'completed', jobId: 'reopened-anomaly', bestMetric: .4,
      hardware: {}, lossHistory: [], recoverActiveJob: noOp, refreshCurrentJob: noOp,
      startTraining: async (...args) => {starts.push(args);}})},
    '../../services/api': {api: {training: {warmStartParents: (...args) => {
      parents.push(args); return Promise.resolve({parents: []});}}}},
    './modelTrainingOptions': optionsModule,
    './DinoSyntheticOptions': {DinoSyntheticOptions: Options},
    '../../utils/trainingComputeReadiness': load('src/renderer/utils/trainingComputeReadiness.ts'),
    '../../utils/datasetSplitCapability': {isSplitUnavailable: () => true},
  };
  for (const [relative, name] of [['../common/OperatorGuidanceBanner', 'OperatorGuidanceBanner'],
    ['../common/GuardrailBanner', 'GuardrailBanner'], ['../common/LedAnnunciator', 'LedAnnunciator'],
    ['./OscilloscopeLossCurve', 'OscilloscopeLossCurve'], ['./HardwareTelemetryPanel', 'HardwareTelemetryPanel'],
    ['./RecipePresetSelector', 'RecipePresetSelector'], ['./OCRWorkbench', 'OCRWorkbench'],
    ['./RotatedDetectionPanel', 'RotatedDetectionPanel'], ['./DefectGANWorkbench', 'DefectGANWorkbench'],
    ['./EnhancementWorkbench', 'EnhancementWorkbench'], ['./ModelFamilyCatalog', 'ModelFamilyCatalog']]) {
    stubs[relative] = {[name]: name};
  }
  const Screen = load('src/renderer/components/training/TrainingController.tsx', stubs).TrainingController;
  const render = () => {cursor = 0; const tree = Screen(); effects.splice(0).forEach(fn => fn()); return tree;};
  let tree = render();
  assert.equal(find(tree, n => n.props?.['aria-label'] === '학습 모델 구조')[0].props.value, 'padim');
  assert.ok(find(tree, n => n.type === 'span' && n.props.children === '저장 지표')
    .some(n => n.props.title === '저장된 모델 선택 지표 · 검증 수치와 학습 손실은 아래 기록에서 확인'),
    'An anomaly metric recovered with the default next-model choice must not become validation loss');
  find(tree, n => n.props?.['aria-label'] === '학습 모델 구조')[0].props.onChange({target: {value: 'dino_synthetic'}});
  tree = render();
  const panel = find(tree, n => n.type === Options)[0];
  assert.ok(panel, 'The anomaly model selector must expose synthetic options in the training screen');
  assert.ok(find(tree, n => n.type === 'span' && n.props.children === '저장 지표')
    .some(n => n.props.title.includes('저장된 모델 선택 지표')));
  panel.props.onChange({...panel.props.options, anomaly_backbone: 'dinov3_vitl16', patch_size: 512, stride: 256});
  tree = render(); await flush();
  assert.equal(parents.at(-1)[3].anomaly_backbone, 'dinov3_vitl16');
  assert.equal(parents.at(-1)[3].patch_size, 512);
  const start = find(tree, n => n.type === 'button' && String(n.props.children?.[1]?.props.children).includes('AutoML'))[0];
  assert.equal(start.props.disabled, false); await start.props.onClick();
  assert.deepEqual(starts[0][3], {anomaly_method: 'dino_synthetic', anomaly_backbone: 'dinov3_vitl16',
    patch_size: 512, stride: 256, patches_per_image: 8, inference_batch_size: 32});
  const selected = find(tree, n => n.type === Options)[0];
  selected.props.onChange({...selected.props.options, stride: 513});
  tree = render();
  const blocked = find(tree, n => n.type === 'button' && String(n.props.children?.[1]?.props.children).includes('AutoML'))[0];
  assert.equal(blocked.props.disabled, true);
  assert.ok(find(tree, n => n.props?.role === 'alert').some(n => String(n.props.children).includes('간격')));
});

test('completed specialist training reloads compatible parents immediately', async () => {
  const ui = harness('src/renderer/components/training/WarmStartSelector.tsx', 'WarmStartSelector');
  ui.render(); ui.requests[0].resolve(parent('first')); await flush();
  ui.props.refreshKey = 'completed-job'; ui.render();
  assert.equal(ui.requests.length, 2);
  ui.requests[1].resolve(parent('completed-job')); await flush();
  assert.deepEqual(find(ui.render(), n => n.type === 'option').map(n => n.props.value), ['', 'completed-job']);
});

test('catalog and specialist selectors address actual /api routes', () => {
  const catalog = harness('src/renderer/components/training/ModelFamilyCatalog.tsx', 'ModelFamilyCatalog');
  catalog.render(); assert.equal(catalog.requests[0].url, '/api/models/capabilities');
  const selector = harness('src/renderer/components/training/WarmStartSelector.tsx', 'WarmStartSelector');
  selector.render(); assert.equal(new URL(selector.requests[0].url, 'http://localhost').pathname, '/api/ocr/warm-start-parents');
});

test('capability catalog explains method-specific anomaly continuation and dependencies', async () => {
  const ui = harness('src/renderer/components/training/ModelFamilyCatalog.tsx', 'ModelFamilyCatalog');
  ui.render();
  ui.requests[0].resolve({families: [{task: 'anomaly', label: '이상탐지', model: 'PaDiM / DINOv3',
    architectures: ['padim', 'dino_synthetic'], devices: ['cpu'], default_architecture: 'padim',
    prerequisite: '정상 이미지', remote_training: true, continuation: 'statistical_refit', stages: ['train'], missing_dependencies: [],
    methods: [{method: 'dino_synthetic', architectures: ['dinov3_vits16', 'dinov3_vitb16', 'dinov3_vitl16'],
      continuation: 'weight_initialization', prerequisite: '정상 원본에서 합성 결함 패치를 학습합니다.', missing_dependencies: ['timm']}]}]});
  await flush();
  const tree = ui.render();
  const text = find(tree, node => typeof node.props?.children === 'string').map(node => node.props.children).join(' ');
  assert.match(text, /전체 가중치/);
  assert.match(text, /정상 원본에서 합성 결함 패치/);
  assert.ok(find(tree, node => Array.isArray(node.props?.children)).some(node => node.props.children.includes('timm')));
});

test('training loss chart draws only train points when validation loss is absent', () => {
  const react = {useState: initial => [initial, () => {}], useMemo: fn => fn(),
    useRef: () => ({current: null}), useCallback: fn => fn};
  const Chart = load('src/renderer/components/training/OscilloscopeLossCurve.tsx', {react}).OscilloscopeLossCurve;
  const tree = Chart({lossHistory: [{epoch: 1, trainLoss: .4, valLoss: null}], trainLoss: .4, valLoss: null,
    currentEpoch: 1, totalEpochs: 2, currentStep: 0, totalSteps: 0, isTraining: false, etaSeconds: null, language: 'ko'});
  assert.equal(find(tree, node => node.type === 'polyline' && node.props.stroke === '#FBBF24').length, 0);
  assert.equal(find(tree, node => node.type === 'polyline' && node.props.stroke === '#60A5FA').length, 1);
});

test('specialist train, poll, cancel, rotated and GAN requests reach actual HTTP routes', async () => {
  const realApi = load('src/renderer/services/api.ts');
  realApi.setCachedPort(19191);
  const calls = [];
  const previousFetch = global.fetch;
  global.fetch = async (url, options) => {calls.push({url, options}); return {ok: true, json: async () => ({})};};
  try {
    const {specializedApi: api} = load('src/renderer/services/specializedApi.ts', {'./api': realApi});
    await api.startTraining('ocr', '/data', 2, 'parent');
    await api.trainingJobs('defect-gan');
    await api.trainingJob('ocr', 'job');
    await api.cancelTraining('defect-gan', 'job');
    await api.rotated.manifest('/data');
    await api.rotated.saveManifest('/data', []);
    await api.rotated.predict('job', '/data/a.png');
    await api.rotated.train('/data', 2, 'rotated-parent');
    await api.ganReviews(); await api.openGANReview('job', 'review');
    await api.evaluateGAN('job', '/data'); await api.exportGAN('job');
    await api.adopt('job', '/review', []);
    assert.deepEqual(calls.map(call => new URL(call.url).pathname), [
      '/api/ocr/train', '/api/defect-gan/jobs', '/api/ocr/jobs/job', '/api/defect-gan/jobs/job/cancel',
      '/api/rotated-detection/manifest', '/api/rotated-detection/manifest', '/api/rotated-detection/predict',
      '/api/rotated-detection/train', '/api/defect-gan/reviews', '/api/defect-gan/reviews/job/review',
      '/api/defect-gan/evaluate', '/api/defect-gan/export', '/api/defect-gan/adopt',
    ]);
    assert.equal(JSON.parse(calls[0].options.body).warm_start_job_id, 'parent');
    assert.equal(JSON.parse(calls[7].options.body).warm_start_job_id, 'rotated-parent');
  } finally {global.fetch = previousFetch;}
});

test('label-set changes clear parent and refuse a late previous-list response', async () => {
  const ui = harness('src/renderer/components/training/WarmStartSelector.tsx', 'WarmStartSelector');
  ui.render(); ui.props.value = 'old-parent';
  ui.project.project.active_labelset_id = 'reviewed'; ui.render();
  assert.equal(ui.props.value, '');
  ui.requests[1].resolve(parent('new-parent')); await flush();
  ui.requests[0].resolve(parent('old-parent')); await flush();
  assert.deepEqual(find(ui.render(), n => n.type === 'option').map(n => n.props.value), ['', 'new-parent']);
});

test('A to B to A still rejects the first A response and keeps current loading locked', async () => {
  const ui = harness('src/renderer/components/training/WarmStartSelector.tsx', 'WarmStartSelector');
  ui.render(); ui.project.projectDir = '/projectB'; ui.render();
  ui.project.projectDir = '/projectA'; ui.render();
  ui.requests[0].resolve(parent('stale-first-A')); await flush();
  assert.equal(find(ui.render(), n => n.type === 'select')[0].props.disabled, true);
  ui.requests[2].resolve(parent('current-A')); await flush();
  assert.deepEqual(find(ui.render(), n => n.type === 'option').map(n => n.props.value), ['', 'current-A']);
  ui.requests[1].reject(new Error('old B unavailable')); await flush();
  assert.equal(find(ui.render(), n => n.props?.role === 'alert').length, 0);
});
