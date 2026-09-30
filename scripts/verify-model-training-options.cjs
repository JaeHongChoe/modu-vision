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
