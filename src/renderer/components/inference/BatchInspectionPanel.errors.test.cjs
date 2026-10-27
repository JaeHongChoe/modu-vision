const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), Module = require('node:module'), ts = require('typescript');
const tick = () => new Promise(setImmediate);
const never = () => new Promise(() => {});
function fixture({historyRun} = {}) {
  let cursor = 0, dirty = true, tree;
  const slots = [], effects = [], modules = new Map();
  const react = {
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
      return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; dirty = true; }]; },
    useRef(initial) { const i = cursor++; return slots[i] ?? (slots[i] = {current: initial}); },
    useEffect(fn, deps) { const i = cursor++, previous = slots[i];
      if (!previous || deps.some((value, n) => !Object.is(value, previous.deps[n]))) {
        const slot = slots[i] = {deps}; effects.push(() => { previous?.cleanup?.(); slot.cleanup = fn(); });
      }
    },
  };
  const project = {task: 'detection', projectDir: '/project', isProjectBusy: false,
    project: {id: 'p', project_dir: '/project', source_dataset_dir: '/source', task: 'detection', active_labelset_id: 'default'}};
  const dataset = {folderPath: '/source', datasetKey: '/source\0detection', hasSelectedFolder: true,
    importError: null, sourceSaveError: null, isLoading: false, isSplitting: false, lastImportedKey: 'import', staleDatasetKeys: []};
  const flow = {contextRevision: 0, executionChoiceOverride: 'local_cpu', pipelineDirty: false};
  const store = state => Object.assign(selector => selector ? selector(state) : state, {getState: () => state});
  let mode = historyRun ? 'row' : 'errors', oldReject;
  const old = new Promise((_, reject) => { oldReject = reject; }); old.catch(() => {});
  const apiModule = {getApiPersistenceIdentity: () => 'local', getProjectContextGeneration: () => 0, getProjectContext: () => null,
    request: async () => { if (mode === 'errors') throw Error('statistics request failed'); if (mode === 'count-ready') return {labelset_id: project.project.active_labelset_id, total: 0, assignments: Object.fromEntries(['train','val','test','not_used','not_split'].map(part => [part, {count: 0}]))}; return never(); },
    api: {flowchart: {listPipelines: async () => {if (mode === 'errors') throw Error('저장 흐름을 다시 확인하세요.'); return never();}},
      inspections: {listRuns: async () => {if (mode === 'errors') throw Error('검사 기록을 다시 확인하세요.'); return mode === 'held' ? old : mode === 'pending' ? never() : historyRun ? {runs: [historyRun]} : never();}, getRun: async () => historyRun}}};
  const mocks = {
    react, 'react/jsx-runtime': {jsx: (type, props) => ({type, props}), jsxs: (type, props) => ({type, props})},
    'lucide-react': {}, '../../services/api': apiModule,
    '../../stores/useProjectStore': {useProjectStore: store(project)}, '../../stores/useDatasetStore': {useDatasetStore: store(dataset)},
    '../../stores/useFlowchartStore': {useFlowchartStore: store(flow)},
    '../../stores/useComputeStore': {useComputeStore: store({profiles: [], isLoaded: true, load: async () => {}})},
    '../../stores/useInspectionRunStore': {useInspectionRunStore: store({setRunning() {}})},
    '../../stores/autoRunChannel': {registerAutoRun: () => () => {}, registerTaskAutoRun: () => () => {}},
    '../../stores/workflowProgress': {publishInspectionProgress() {}},
    '../flowchart/flowExecution': {}, '../flowchart/flowHandoff': {activeSavedVersion: () => undefined},
    '../flowchart/SavedFlowIdentityCard': {}, './ReviewQueuePanel': {}, '../training/useTaskHandoff': {useTaskHandoff: () => null},
    '../common/EvidenceImageViewer': {}, '../common/evidenceViewer': {},
    '../flowchart/flowchartStartup': {getFlowchartModelTask: () => null},
  };
  function load(filename) {
    if (modules.has(filename)) return modules.get(filename).exports;
    const m = new Module(filename, module); m.filename = filename; m.paths = Module._nodeModulePaths(path.dirname(filename)); modules.set(filename, m);
    const original = m.require.bind(m);
    m.require = name => {
      if (Object.hasOwn(mocks, name)) return mocks[name];
      const local = name.startsWith('.') ? path.resolve(path.dirname(filename), name) : '';
      const found = ['.ts', '.tsx'].map(ext => local + ext).find(file => local && fs.existsSync(file));
      return found ? load(found) : original(name);
    };
    m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
    }}).outputText, filename);
    return m.exports;
  }
  const context = load(path.resolve(__dirname, '../../stores/assistContext.ts'));
  const {BatchInspectionPanel} = load(path.join(__dirname, 'BatchInspectionPanel.tsx'));
  const render = () => {cursor = 0; dirty = false; tree = BatchInspectionPanel(); effects.splice(0).forEach(fn => fn());};
  return {project, dataset, flow, context, render, mode(value) {mode = value; dirty = true;}, oldReject,
    async settle() {for (let n = 0; n < 12; n++) {if (dirty) render(); await tick();} return tree;},
    invalidate() {dirty = true;}, tree: () => tree, close() {slots.forEach(slot => {slot?.cleanup?.(); if (slot) slot.cleanup = undefined;});}};
}
function environment(t) {
  const oldWindow = global.window, oldStorage = Object.getOwnPropertyDescriptor(global, 'localStorage');
  global.window = {addEventListener() {}, removeEventListener() {}};
  Object.defineProperty(global, 'localStorage', {configurable: true, value: {getItem: () => null}});
  t.after(() => {global.window = oldWindow; if (oldStorage) Object.defineProperty(global, 'localStorage', oldStorage); else delete global.localStorage;});
}
test('current inspection count, flow and history errors reach shared guidance and retire with the panel', async t => {
  environment(t); const f = fixture(); try { await f.settle();
  const errors = f.context.assistPanelErrors();
  assert.equal(errors.length, 1);
  assert.equal(errors[0].source, '검사 실행');
  assert.match(errors[0].text, /이미지 수를 확인하지 못했습니다/);
  assert.match(errors[0].text, /저장 흐름을 다시 확인/);
  assert.match(errors[0].text, /검사 기록을 다시 확인/);
  f.close(); assert.deepEqual(f.context.assistPanelErrors(), []);
  } finally { f.close(); }
});
test('label-set, project and source transitions never publish old inspection errors or late failures', async t => {
  environment(t); const f = fixture(); try { await f.settle();
  assert.ok(f.context.assistPanelErrors().length);
  f.mode('held'); f.project.project.active_labelset_id = 'new-labels'; f.invalidate(); f.render();
  assert.deepEqual(f.context.assistPanelErrors(), []);
  await f.settle();
  f.mode('pending'); f.project.project.id = 'new-project'; f.invalidate(); f.render();
  f.oldReject(Error('retired label-set failure')); await f.settle();
  assert.deepEqual(f.context.assistPanelErrors(), []);
  f.dataset.folderPath = '/other'; f.dataset.datasetKey = '/other\0detection'; f.project.project.source_dataset_dir = '/other'; f.invalidate();
  await f.settle(); assert.deepEqual(f.context.assistPanelErrors(), []);
  } finally { f.close(); }
});

const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes) : tree && typeof tree === 'object' ? [tree, ...nodes(tree.props?.children)] : [];
test('retrying the actual count read clears its resolved shared error without hiding other failures', async t => {
  environment(t); const f = fixture(); try {
    await f.settle();
    const retry = nodes(f.tree()).find(node => node.type === 'button' && node.props.children === '이미지 수 다시 확인');
    assert.ok(retry); f.mode('count-ready'); retry.props.onClick(); await f.settle();
    const errors = f.context.assistPanelErrors(); assert.equal(errors.length, 1);
    assert.doesNotMatch(errors[0].text, /이미지 수를 확인하지 못했습니다/);
    assert.match(errors[0].text, /저장 흐름을 다시 확인/);
    assert.match(errors[0].text, /검사 기록을 다시 확인/);
    f.project.isProjectBusy = true; f.invalidate(); await f.settle();
    assert.deepEqual(f.context.assistPanelErrors(), []);
  } finally { f.close(); }
});

test('the selected failed image shares its visible error and retires it on label-set change', async t => {
  environment(t);
  const run = {run_id: 'saved', task: 'detection', source_folder: '/source', canonical_source_folder: '/source',
    scope: 'test', pipeline_name: 'Saved QA flow', created_at: '2026-10-24T00:00:00Z', status: 'completed', counts: {NG: 0, REVIEW: 0}, total: 1,
    rows: [{image: {file_path: '/source/one.png', file_name: 'one.png', image_id: 'one'}, state: 'error', error: '선택한 이미지 검사가 실패했습니다.'}]};
  const f = fixture({historyRun: run}); try {
    await f.settle();
    assert.deepEqual(f.context.assistPanelErrors(), [{source: '검사 실행', text: '선택한 이미지 검사가 실패했습니다.'}]);
    // Hold reads in the next namespace instead of serving the old run as new evidence.
    f.mode('pending'); f.project.project.active_labelset_id = 'other'; f.invalidate(); f.render();
    assert.deepEqual(f.context.assistPanelErrors(), []);
  } finally { f.close(); }
});
