// Exercise the actual dialog, project store and HTTP context client together.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');

function load(file, mocks = {}) {
  const filename = path.resolve(__dirname, file);
  const m = new Module(filename, module);
  m.filename = filename;
  m.paths = Module._nodeModulePaths(path.dirname(filename));
  const requireOriginal = m.require.bind(m);
  m.require = name => Object.hasOwn(mocks, name) ? mocks[name] : requireOriginal(name);
  m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, filename);
  return m.exports;
}

const tick = () => new Promise(resolve => setImmediate(resolve));
async function until(check) {
  for (let i = 0; i < 100; i++) { if (check()) return; await tick(); }
  assert.fail('The owned task request did not reach the expected boundary');
}
const valueStore = value => Object.assign(selector => selector ? selector(value) : value, {
  getState: () => value, setState: update => Object.assign(value, update),
});

async function exercise({ change, failure = false }) {
  const oldWindow = Object.getOwnPropertyDescriptor(globalThis, 'window');
  const oldFetch = Object.getOwnPropertyDescriptor(globalThis, 'fetch');
  try {
    const api = load('../services/api.ts');
    const project = { id: 'same-project', name: 'Fixture', project_dir: '/fixture/project',
      task: 'classification', source_dataset_dir: null };
    const context = actor => ({ workspace_id: 'fixture-workspace', project_id: project.id, actor_id: actor, mode: 'team' });
    let release, sent = false;
    const pending = new Promise(resolve => { release = resolve; });
    Object.defineProperty(globalThis, 'window', { configurable: true, value: { api: { getBackendPort: async () => 8123 } } });
    Object.defineProperty(globalThis, 'fetch', { configurable: true, value: async (url, options) => {
      const origin = JSON.parse(new Headers(options.headers).get('X-Vision-Context')) || context('first-actor');
      const changing = url.endsWith('/api/project/update');
      if (changing) { sent = true; await pending; if (failure) throw new Error('Old request failed'); }
      return { ok: true, status: 200, headers: new Headers({ 'X-Vision-Context': JSON.stringify(origin) }),
        json: async () => ({ ...project, task: changing ? 'segmentation' : 'classification' }) };
    } });
    const annotationCalls = [];
    const annotation = valueStore({ isDirty: false, setTask: task => annotationCalls.push(task) });
    const dataset = valueStore({ isLoading: false, isSplitting: false, hasSelectedFolder: false,
      totalImages: 0, split: { train: 0, val: 0, test: 0 } });
    const flow = valueStore({ pipelineDirty: false });
    const storeModule = load('./useProjectStore.ts', {
      '../services/api': api, './useAnnotationStore': { useAnnotationStore: annotation },
      '../services/datasetWorkflow': {}, './useDatasetStore': { useDatasetStore: dataset },
      './useFlowchartStore': { useFlowchartStore: flow },
      './useTrainingStore': { useTrainingStore: valueStore({ isTraining: false }) },
      './useInspectionRunStore': { useInspectionRunStore: valueStore({ isRunning: false }) },
      './useModelAssistRunStore': { useModelAssistRunStore: valueStore({ activeOperations: 0 }) },
      './projectViewState': load('./projectViewState.ts'),
    });
    const store = storeModule.useProjectStore;
    store.setState({ project, projectDir: project.project_dir, task: project.task });
    api.setProjectContext(context('first-actor'));
    const renderStore = Object.assign(selector => selector ? selector(store.getState()) : store.getState(), { getState: store.getState });
    const Action = () => null, jsx = (type, props) => ({ type, props: props || {} }), results = [];
    const dialog = load('../components/wizard/TaskChangeImpactDialog.tsx', {
      react: { useEffect() {}, useState: initial => [typeof initial === 'function' ? initial() : initial, () => {}],
        useRef: value => ({ current: value }) }, 'react/jsx-runtime': { jsx, jsxs: jsx },
      '../../services/api': api, '../../stores/useProjectStore': { ...storeModule, useProjectStore: renderStore },
      '../../stores/useDatasetStore': { useDatasetStore: dataset }, '../../stores/useAnnotationStore': { useAnnotationStore: annotation },
      '../../stores/useFlowchartStore': { useFlowchartStore: flow },
      '../common/WorkspaceDialog': { WorkspaceDialog: () => null }, '../common/AsyncAction': { AsyncAction: Action },
    });
    const tree = dialog.TaskChangeImpactDialog({ nextTask: 'segmentation', scope: dialog.taskPreviewScope(),
      onClose() {}, onResult: outcome => results.push(outcome) });
    const nodes = node => !node || typeof node !== 'object' ? [] : [node,
      ...[node.props?.children].flat(Infinity).flatMap(nodes)];
    nodes(tree).find(node => node.type === Action && node.props.children === '영향 확인 후 변경').props.onClick();
    await until(() => sent);
    if (change) {
      api.setProjectContext(context('second-actor'));
      if (change === 'aba') api.setProjectContext(context('first-actor'));
      store.setState({ projectError: 'Current namespace recovery reason' });
    }
    release();
    await until(() => !store.getState().isProjectBusy);
    await tick();
    if (change) {
      assert.equal(store.getState().task, 'classification', 'A late response cannot commit into another accepted namespace');
      assert.equal(store.getState().projectError, 'Current namespace recovery reason', 'Old errors cannot clear current recovery guidance');
      assert.deepEqual(annotationCalls, []);
      assert.deepEqual(results, []);
    } else {
      assert.equal(store.getState().task, 'segmentation');
      assert.deepEqual(annotationCalls, ['segmentation']);
      assert.equal(results.length, 1);
      assert.equal(results[0].ok, true);
    }
  } finally {
    if (oldWindow) Object.defineProperty(globalThis, 'window', oldWindow); else delete globalThis.window;
    if (oldFetch) Object.defineProperty(globalThis, 'fetch', oldFetch); else delete globalThis.fetch;
  }
}

test('late confirmed task response cannot mutate a new accepted actor namespace', () => exercise({ change: 'actor' }));
test('leaving and returning to the same namespace still rejects an old task response', () => exercise({ change: 'aba' }));
test('a failed old task request preserves new namespace recovery guidance', () => exercise({ change: 'actor', failure: true }));
test('a current namespace confirmed task change still commits exactly once', () => exercise({ change: null }));
