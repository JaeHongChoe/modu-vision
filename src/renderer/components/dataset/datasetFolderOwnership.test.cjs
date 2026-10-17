const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

const tick = () => new Promise(setImmediate);
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
function load(filename, mocks) {
  const m = new Module(filename, module);
  m.filename = filename;
  m.paths = Module._nodeModulePaths(path.dirname(filename));
  const original = m.require.bind(m);
  m.require = name => Object.hasOwn(mocks, name) ? mocks[name] : original(name);
  m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText, filename);
  return m.exports;
}

function harness({ holdSave = false } = {}) {
  let generation = 0, backend = 'A', port = 8001, cursor = 0;
  const project = { task: 'classification', language: 'en', projectDir: '/projects/A', backendPort: port,
    project: { id: 'A', name: 'A', project_dir: '/projects/A', source_dataset_dir: '/source/A' } };
  const hook = state => Object.assign(selector => selector ? selector(state) : state, {
    getState: () => state,
    setState: change => Object.assign(state, typeof change === 'function' ? change(state) : change),
  });
  const projectStore = hook(project), calls = [], pickers = [], saves = [], slots = [], effects = [];
  const summary = { total_images: 3, source_images: 3, classes: { OK: 3 }, split: { train: 2, val: 1, test: 0 } };
  const api = {
    project: { update: async data => {
      calls.push({ kind: 'save', backend, project: project.project.id, folder: data.source_dataset_dir });
      const saved = { ...project.project, source_dataset_dir: data.source_dataset_dir };
      if (holdSave) { const pending = deferred(); saves.push(pending); await pending.promise; }
      return saved;
    } },
    dataset: {
      import: async data => {
        calls.push({ kind: 'import', backend, project: project.project.id, folder: data.folder_path });
        return summary;
      },
      getImages: async () => ({ items: [], total: 3 }),
      currentSummary: async () => summary,
    },
    datasetImports: { revisions: async () => ({ revisions: [], active_revision: null }) },
  };
  const apiModule = { api, resolveApiUrl: x => `http://127.0.0.1:${port}${x}`, getProjectContextGeneration: () => generation,
    getApiPersistenceIdentity: () => backend, getProjectContext: () => null };
  const inert = { getState: () => ({ invalidateForDataChange() {} }), setState() {} };
  const store = load(path.resolve(__dirname, '../../stores/useDatasetStore.ts'), {
    '../services/api': apiModule, './useProjectStore': { useProjectStore: projectStore },
    './useTrainingStore': { useTrainingStore: inert }, './useEvaluationStore': { useEvaluationStore: inert },
    './useFlowchartStore': { useFlowchartStore: inert },
  }).useDatasetStore;
  store.setState({ folderPath: '/source/A', hasSelectedFolder: true, datasetKey: '/source/A\0classification' });
  const same = (a, b) => a && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const react = {
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
      return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }]; },
    useRef(initial) { const i = cursor++; return slots[i] ?? (slots[i] = { current: initial }); },
    useCallback(fn, deps) { const i = cursor++; if (!same(slots[i]?.deps, deps)) slots[i] = { deps, fn }; return slots[i].fn; },
    useEffect(fn, deps) { const i = cursor++; if (!same(slots[i]?.deps, deps)) {
      const previous = slots[i]; slots[i] = { deps }; effects.push(() => { previous?.cleanup?.(); slots[i].cleanup = fn(); });
    } },
  };
  const jsx = (type, props) => ({ type, props });
  const helpers = Object.fromEntries(['classDistribution', 'datasetImportView'].map(name =>
    ['./' + name, load(path.join(__dirname, name + '.ts'), {})]));
  helpers['../../utils/datasetSplitCapability'] = load(path.resolve(__dirname, '../../utils/datasetSplitCapability.ts'), {});
  const filename = path.join(__dirname, 'DatasetStudio.tsx'), m = new Module(filename, module);
  m.filename = filename; m.paths = Module._nodeModulePaths(__dirname);
  const original = m.require.bind(m);
  m.require = name => name === 'react' ? react : name === 'react/jsx-runtime' ? { jsx, jsxs: jsx }
    : name === '../../stores/useProjectStore' ? { useProjectStore: projectStore }
    : name === '../../stores/useDatasetStore' ? { useDatasetStore: selector => selector ? selector(store.getState()) : store.getState() }
    : name === '../../services/api' ? apiModule
    : name === '../../services/hostAdapter' ? { host: { selectFolder: () => {
      const pending = deferred(); pickers.push(pending); return pending.promise;
    } }, hostErrorMessage: error => error.message }
    : name === '../runtime/useDeliveryScope' ? { useDeliveryScope: () => ({ key: `${backend}:${project.project.id}` }) }
    : Object.hasOwn(helpers, name) ? helpers[name] : name.startsWith('.') ? new Proxy({}, { get: () => () => null }) : original(name);
  m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText, filename);
  const render = () => { cursor = 0; const tree = m.exports.DatasetStudio(); effects.splice(0).forEach(fn => fn()); return tree; };
  const nodes = tree => { const result = []; const visit = node => {
    if (Array.isArray(node)) return node.forEach(visit);
    if (node && typeof node === 'object') { result.push(node); visit(node.props?.children); }
  }; visit(tree); return result; };
  const select = () => nodes(render()).find(node => node.type === 'button'
    && nodes(node).some(child => child.props?.children === 'Open Dataset Folder')).props.onClick();
  const move = ({ id = project.project.id, transport = backend, backendPort = port, advanceGeneration = true } = {}) => {
    if (advanceGeneration) generation++;
    backend = transport; port = backendPort;
    projectStore.setState({ projectDir: `/projects/${id}`, backendPort: port, project: {
      id, name: id, project_dir: `/projects/${id}`, source_dataset_dir: `/source/${id}`,
    } });
    store.setState({ folderPath: `/source/${id}`, datasetKey: `/source/${id}\0classification`, totalImages: 17, isLoading: false });
  };
  return { store, project, render, select, pickers, saves, calls, move,
    unmount: () => slots.forEach(slot => slot?.cleanup?.()) };
}

for (const [name, change] of [['project', { id: 'B', advanceGeneration: false }],
  ['backend with the same project ID', { transport: 'B' }],
  ['local backend port with the same project ID', { backendPort: 8002, advanceGeneration: false }]]) {
  test(`a folder picker opened in A cannot import or save into the next ${name}`, async () => {
    const h = harness(); const pending = h.select(); h.move(change);
    h.pickers[0].resolve('/picked/A'); await pending;
    assert.deepEqual(h.calls, [], 'the old folder must never reach the next authority');
    assert.equal(h.project.project.source_dataset_dir, `/source/${change.id || 'A'}`);
    assert.equal(h.store.getState().totalImages, 17);
  });
}
test('a dismissed folder picker leaves the existing source untouched', async () => {
  const h = harness(); const pending = h.select(); h.pickers[0].resolve(null); await pending;
  assert.deepEqual(h.calls, []); assert.equal(h.store.getState().folderPath, '/source/A');
});
test('a replaced picker and an unmounted picker cannot adopt their late answers', async () => {
  const h = harness(); const first = h.select(), second = h.select();
  h.pickers[1].resolve('/picked/latest'); await second;
  h.pickers[0].resolve('/picked/old'); await first;
  assert.equal(h.project.project.source_dataset_dir, '/picked/latest');
  assert.equal(h.store.getState().folderPath, '/picked/latest');
  const unmounted = harness(); const late = unmounted.select(); unmounted.unmount();
  unmounted.pickers[0].resolve('/picked/unmounted'); await late;
  assert.deepEqual(unmounted.calls, []);
});
for (const change of [{ transport: 'B' }, { backendPort: 8002, advanceGeneration: false }]) {
test(`a source-save response from the old ${change.transport ? 'shared' : 'local'} backend cannot overwrite the new same-ID project`, async () => {
  const h = harness({ holdSave: true });
  const pending = h.store.getState().importFolder('/picked', 'classification'); await tick();
  h.move(change);
  h.store.setState({ folderPath: '/picked', datasetKey: '/picked\0classification' });
  h.saves[0].resolve(); await pending;
  assert.equal(h.project.project.source_dataset_dir, '/source/A');
  assert.equal(h.store.getState().totalImages, 17);
  assert.equal(h.calls.some(call => call.backend === 'B'), false);
});
}
