const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), Module = require('node:module'), ts = require('typescript');

const ready = { ready: true, model: 'yolo26n', execution_verified: false, dependencies: { missing: [] },
  runtime: { available: true, device: 'cpu', reason: '' },
  weights: { state: 'file_available', filename: 'model.pt', sha256: 'private-file-identity', content_verified: false }, next_actions: [] };

function fixture() {
  const slots = [], effects = [], requests = [], steps = [], checkpoints = []; let cursor = 0, tree, generation = 1, identity = 'local';
  const project = { viewMode: 'simple', task: 'detection', projectDir: '/project', isProjectBusy: false,
    project: { name: '검사', source_dataset_dir: '/images', active_labelset_id: 'default' }, setStep: step => steps.push(step) };
  const dataset = { folderPath: '/images', datasetKey: '/images\0detection', sourceImages: 10, totalImages: 10,
    split: { train: 6, val: 2, test: 2 }, splitState: 'saved', isLoading: false, isSplitting: false, importError: null };
  const compute = { selectedProfileId: 'server', transportRevision: 1, profiles: [{id: 'server', name: '학습 서버'}] };
  const react = {
    createElement: (type, props, ...children) => ({ type, props: { ...props, children } }), Fragment: 'Fragment',
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = {value: typeof initial === 'function' ? initial() : initial};
      return [slots[i].value, value => { slots[i].value = typeof value === 'function' ? value(slots[i].value) : value; }]; },
    useRef(initial) { const i = cursor++; return slots[i] ?? (slots[i] = { current: initial }); },
    useEffect(fn, deps) { const i = cursor++, previous = slots[i]; if (!previous || deps.some((value, j) => !Object.is(value, previous.deps[j]))) {
      const slot = slots[i] = {deps}; effects.push(() => { previous?.cleanup?.(); slot.cleanup = fn(); });
    } },
  };
  react.default = react;
  const hook = state => Object.assign(selector => selector ? selector(state) : state, { getState: () => state });
  const filename = path.join(__dirname, 'TrainingPreparationPanel.tsx'), m = new Module(filename, module);
  m.require = name => {
    if (name === 'react') return react;
    if (name === 'lucide-react') return {CheckCircle: 'CheckCircle', RefreshCw: 'RefreshCw'};
    if (name.endsWith('/services/api')) return {request: (url, options) => new Promise((resolve, reject) => requests.push({url, options, resolve, reject})),
      getApiPersistenceIdentity: () => identity, getProjectContextGeneration: () => generation, subscribeProjectContext: () => () => {}};
    if (name.endsWith('/hostAdapter')) return {host: {localBrowser: () => false}};
    if (name.endsWith('/useProjectStore')) return {useProjectStore: hook(project)};
    if (name.endsWith('/useDatasetStore')) return {useDatasetStore: hook(dataset)};
    if (name.endsWith('/useComputeStore')) return {useComputeStore: hook(compute)};
    if (name === './trainingWorkflow') return {familyPreparation: {detection: {label: '객체 검출', truth: '객체 bbox', route: '라벨링 → 데이터 분할', evaluation: '독립 test'}}};
    if (name === './ProgramWorkbenchControls') return {programButton: 'button'};
    if (name === './TeamTrainingReadiness') return {TeamTrainingReadiness: 'TeamTrainingReadiness'};
    if (name === './WeightsPrefetch') return {WeightsPrefetch: 'WeightsPrefetch', prefetchSupported: () => true};
    throw Error(name);
  };
  m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React, jsxFactory: "react_1.createElement", jsxFragmentFactory: "react_1.Fragment"}}).outputText, filename);
  const nodes = (value, visible = false) => Array.isArray(value) ? value.flatMap(child => nodes(child, visible)) : value && typeof value === 'object'
    ? [value, ...nodes(value.type === 'details' && visible && !value.props.open && !value.props.ref?.current?.open ? value.props.children.filter(child => child?.type === 'summary') : value.props.children, visible)] : [];
  const text = value => Array.isArray(value) ? value.map(text).join('') : value && typeof value === 'object' ? text(value.props?.children) : typeof value === 'string' || typeof value === 'number' ? String(value) : '';
  const props = {family: 'detection', model: 'yolo26n', onCheckpointChange: value => checkpoints.push(value)};
  function render() { cursor = 0; tree = m.exports.TrainingPreparationPanel(props); for (const node of nodes(tree)) {
    if (node.type === 'details' && node.props.ref) node.props.ref.current ??= {open: false, focus() {}};
  } }
  return {project, dataset, compute, props, requests, steps, checkpoints,
    async settle() { for (let i = 0; i < 4; i++) {render(); effects.splice(0).forEach(effect => effect()); await Promise.resolve();} },
    visibleText: () => nodes(tree, true).filter(node => node.type !== 'details').flatMap(node => node.props.children).filter(value => typeof value === 'string').join(' '),
    buttons: () => nodes(tree, true).filter(node => node.type === 'button'),
    click(label) {const button = this.buttons().find(node => text(node).includes(label)); assert.ok(button, label); assert.ok(!button.props.disabled); button.props.onClick();},
    details: () => nodes(tree).filter(node => node.type === 'details'),
    changeIdentity() { identity = 'next'; }, changeGeneration() { generation++; },
    unmount() {slots.forEach(slot => slot?.cleanup?.());},
  };
}

test('simple preparation puts the missing photo or label first and opens the matching recovery step', async () => {
  for (const [setup, label, step] of [
    [f => {f.project.project.source_dataset_dir = '';}, '사진 준비 열기', 1],
    [f => {f.dataset.sourceImages = 0; f.dataset.totalImages = 0;}, '사진 준비 열기', 1],
    [f => {f.dataset.totalImages = 0; f.dataset.split = {train: 0, val: 0, test: 0};}, '정답 붙이기 열기', 2],
    [f => {f.dataset.folderPath = '/old';}, '사진 준비 열기', 1],
    [f => {f.dataset.split = {train: 0, val: 0, test: 0};}, '사진 나누기 열기', 1],
  ]) {
    const f = fixture(); setup(f); await f.settle(); assert.equal(f.buttons().length, 1);
    f.click(label); assert.deepEqual(f.steps, [step]); assert.equal(f.requests.length, 0); f.unmount();
  }
});

test('ready files do not expose technical details or imply quality approval in the simple view', async () => {
  const f = fixture(); await f.settle(); f.click('학습 준비 확인');
  assert.equal(f.requests[0].url, '/api/training-workspace/readiness');
  assert.equal(JSON.parse(f.requests[0].options.body).compute_profile_id, 'server');
  f.requests[0].resolve(ready); await f.settle();
  assert.match(f.visibleText(), /준비 항목.*확인/); assert.match(f.visibleText(), /품질.*확인/);
  assert.doesNotMatch(f.visibleText(), /SHA256|private-file-identity|bbox|pip|cpu/);
  assert.ok(f.details().every(node => !node.props.open));
  f.project.viewMode = 'full'; await f.settle(); assert.match(f.visibleText(), /private-file-identity/); f.unmount();
});

test('a missing program gives one recovery action while its installation command waits in details', async () => {
  const f = fixture(); await f.settle(); f.click('학습 준비 확인');
  f.requests[0].resolve({...ready, ready: false, dependencies: {missing: ['torch']}, next_actions: ['python -m pip install torch']}); await f.settle();
  assert.match(f.visibleText(), /프로그램.*준비/); assert.doesNotMatch(f.visibleText(), /torch|pip|SHA256/);
  assert.equal(f.buttons().length, 1); f.click('준비 방법 보기'); await f.settle();
  assert.match(f.visibleText(), /torch/); f.unmount();
});

test('late readiness responses cannot update another project, source, label set or transport', async () => {
  for (const change of [f => {f.project.projectDir = '/next';}, f => {f.project.project.source_dataset_dir = '/next';},
    f => {f.project.project.active_labelset_id = 'next';}, f => {f.compute.transportRevision++;},
    f => {f.compute.selectedProfileId = 'next';}, f => f.changeIdentity(), f => f.changeGeneration()]) {
    const f = fixture(); await f.settle(); f.click('학습 준비 확인'); change(f); await f.settle();
    f.requests[0].resolve({...ready, selected_checkpoint_path: '/old-project/file.pt'}); await f.settle();
    assert.deepEqual(f.checkpoints, []); assert.doesNotMatch(f.visibleText(), /준비 항목을 확인했습니다/); f.unmount();
  }
});

test('controller input blockers take priority over a ready environment and keep their recovery destination', async () => {
  const f = fixture(); await f.settle(); f.click('학습 준비 확인'); f.requests[0].resolve(ready); await f.settle();
  f.props.inputBlocker = {message: '다른 종류의 사진도 필요합니다.', step: 1}; await f.settle();
  assert.match(f.visibleText(), /다른 종류/); f.click('사진 준비 열기'); assert.deepEqual(f.steps, [1]); f.unmount();
});


test('a controller-owned blocker uses its own recovery callback without opening unrelated preparation details', async () => {
  const f = fixture(); let resolutions = 0;
  f.props.inputBlocker = {message: '서버 연결 검사를 완료하세요.', actionLabel: '학습 위치 확인'};
  f.props.onResolveBlocker = () => {resolutions++;}; await f.settle();
  f.click('학습 위치 확인'); await f.settle();
  assert.equal(resolutions, 1); assert.equal(f.requests.length, 0);
  assert.ok(f.details().every(node => !node.props.ref?.current?.open)); f.unmount();
});
