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

function harness({ holdSave = false, holdImport = false, holdImages = false, holdSplit = false, withImages = false, importFailure, saveFailure } = {}) {
  let generation = 0, backend = 'A', port = 8001, cursor = 0;
  let context = {workspace_id: 'workspace', project_id: 'A', actor_id: 'actor', mode: 'local'};
  const project = { task: 'classification', language: 'en', projectDir: '/projects/A', backendPort: port,
    project: { id: 'A', name: 'A', project_dir: '/projects/A', task: 'classification', source_dataset_dir: '/source/A' } };
  const hook = state => Object.assign(selector => selector ? selector(state) : state, {
    getState: () => state,
    setState: change => Object.assign(state, typeof change === 'function' ? change(state) : change),
  });
  const projectStore = hook(project), calls = [], pickers = [], saves = [], imports = [], imageReads = [], imageRequests = [], summaries = [], splits = [], slots = [], effects = [];
  const summary = { total_images: 3, source_images: 3, classes: { OK: 3 }, split: { train: 2, val: 1, test: 0 } };
  const imageResult = folder => ({items: withImages ? [{image_id: 'part', file_name: 'part.png', file_path: `${folder}/part.png`,
    thumbnail_url: `/api/dataset/thumbnail/part.png?file_path=${folder}/part.png`, label: 'OK', split: 'train', width: 24, height: 24}] : [], total: 3});
  const api = {
    project: { update: async data => {
      calls.push({ kind: 'save', backend, project: project.project.id, folder: data.source_dataset_dir });
      if (saveFailure) throw saveFailure;
      const saved = { ...project.project, source_dataset_dir: data.source_dataset_dir };
      if (holdSave) { const pending = deferred(); saves.push(pending); await pending.promise; }
      return saved;
    } },
    dataset: {
      import: async data => {
        calls.push({ kind: 'import', backend, project: project.project.id, folder: data.folder_path });
        if (importFailure) throw importFailure;
        if (holdImport) { const pending = deferred(); imports.push(pending); return pending.promise; }
        return summary;
      },
      getImages: async data => {
        imageReads.push({backend, port, ...data});
        if (holdImages) {const pending = deferred(); imageRequests.push(pending); return pending.promise;}
        return imageResult(data.folder_path);
      },
      currentSummary: async data => {summaries.push({backend, ...data}); return summary;},
      split: async data => {
        calls.push({kind: 'split', backend, ...data});
        if (holdSplit) {const pending = deferred(); splits.push(pending); return pending.promise;}
        return {split: {train: 1, val: 1, test: 1}};
      },
    },
    datasetImports: { revisions: async () => ({ revisions: [], active_revision: null }) },
  };
  const apiModule = { api, resolveApiUrl: x => `http://127.0.0.1:${port}${x}`, getProjectContextGeneration: () => generation,
    getApiPersistenceIdentity: () => backend === 'A' ? 'local' : `shared:https://${backend}.invalid`, getProjectContext: () => context };
  const inert = { getState: () => ({ invalidateForDataChange() {} }), setState() {} };
  const datasetModule = load(path.resolve(__dirname, '../../stores/useDatasetStore.ts'), {
    '../services/api': apiModule, './useProjectStore': { useProjectStore: projectStore },
    './useTrainingStore': { useTrainingStore: inert }, './useEvaluationStore': { useEvaluationStore: inert },
    './useFlowchartStore': { useFlowchartStore: inert },
  });
  const store = datasetModule.useDatasetStore;
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
  const scopedThumbnail = load(path.resolve(__dirname, '../common/ScopedDatasetThumbnail.tsx'), {'../../services/api': apiModule});
  const filename = path.join(__dirname, 'DatasetStudio.tsx'), m = new Module(filename, module);
  m.filename = filename; m.paths = Module._nodeModulePaths(__dirname);
  const original = m.require.bind(m);
  m.require = name => name === 'react' ? react : name === 'react/jsx-runtime' ? { jsx, jsxs: jsx }
    : name === '../../stores/useProjectStore' ? { useProjectStore: projectStore }
    : name === '../../stores/useDatasetStore' ? { readDatasetAuthority: datasetModule.readDatasetAuthority,
      useDatasetStore: Object.assign(selector => selector ? selector(store.getState()) : store.getState(), {getState: store.getState}) }
    : name === '../common/ScopedDatasetThumbnail' ? scopedThumbnail
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
  const changeAuthority = ({transport = backend, backendPort = port, advanceGeneration = true} = {}) => {
    if (advanceGeneration) generation++;
    backend = transport; port = backendPort; context = null;
  };
  const move = ({ id = project.project.id, transport = backend, backendPort = port, advanceGeneration = true } = {}) => {
    changeAuthority({transport, backendPort, advanceGeneration});
    projectStore.setState({ projectDir: `/projects/${id}`, backendPort: port, project: {
      id, name: id, project_dir: `/projects/${id}`, task: 'classification', source_dataset_dir: `/source/${id}`,
    } });
    store.setState({ folderPath: `/source/${id}`, datasetKey: `/source/${id}\0classification`, totalImages: 17, isLoading: false });
  };
  return { store, project, render, select, pickers, saves, imports, imageReads, imageRequests, imageResult, summaries, splits, summary, calls, move, changeAuthority,
    acceptAuthority: () => {generation++; context = {workspace_id: `workspace-${backend}`, project_id: project.project.id, actor_id: 'actor', mode: backend === 'A' ? 'local' : 'team'};},
    // This source harness sees the real scoped component's input, not decoded DOM pixels.
    gallery: () => nodes(render()).filter(node => node.type === scopedThumbnail.ScopedDatasetThumbnail).map(node => node.props.path),
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

test('a rejected import leaves the previous persisted project source untouched', async () => {
  const h = harness({ importFailure: Error('Unsupported dataset folder') });
  await assert.rejects(h.store.getState().importFolder('/rejected', 'classification'), /Unsupported/);
  assert.equal(h.project.project.source_dataset_dir, '/source/A');
  assert.deepEqual(h.calls.map(call => call.kind), ['import']);
  assert.match(h.store.getState().importError, /Unsupported/);
  assert.equal(h.store.getState().isLoading, false);
});

test('pending validation cannot publish the chosen source before it succeeds', async () => {
  const h = harness({ holdImport: true });
  const pending = h.store.getState().importFolder('/pending', 'classification');
  await tick();
  const sourceBeforeValidation = h.project.project.source_dataset_dir;
  const callsBeforeValidation = h.calls.map(call => call.kind);
  h.imports[0].resolve(h.summary); await pending;
  assert.equal(sourceBeforeValidation, '/source/A');
  assert.deepEqual(callsBeforeValidation, ['import']);
  assert.deepEqual(h.calls.map(call => call.kind), ['import', 'save']);
  assert.equal(h.project.project.source_dataset_dir, '/pending');
  assert.equal(h.store.getState().totalImages, 3);
});

test('failed validation never announces that unimported data merely failed to save', async () => {
  const h = harness({ importFailure: Error('Unsupported dataset folder'), saveFailure: Error('Project is locked') });
  await assert.rejects(h.store.getState().importFolder('/rejected', 'classification'), /Unsupported/);
  assert.equal(h.store.getState().sourceSaveError, null);
  assert.deepEqual(h.calls.map(call => call.kind), ['import']);
  assert.equal(h.project.project.source_dataset_dir, '/source/A');
});

for (const [name, change] of [['shared backend', {transport: 'B'}],
  ['local backend port', {backendPort: 8002, advanceGeneration: false}], ['accepted context epoch', {}]]) {
test(`a loaded gallery retires its old pixels when the ${name} changes before project acceptance`, async () => {
  const h = harness({withImages: true});
  await h.store.getState().importFolder('/source/A', 'classification');
  assert.equal(h.gallery().length, 1, 'the original accepted source is visible');
  h.changeAuthority(change);
  assert.deepEqual(h.gallery(), [], 'old source thumbnails must not be rebound under the new authority');
  h.acceptAuthority();
  await h.store.getState().importFolder('/source/current', 'classification');
  assert.equal(h.gallery().length, 1, 'a freshly accepted source may show its own pixels');
  assert.match(h.gallery()[0], /\/source\/current\/part.png/);
});
}

test('a gallery refresh cannot dispatch the old source to a newly selected backend', async () => {
  const h = harness({withImages: true});
  await h.store.getState().importFolder('/source/A', 'classification');
  const before = h.imageReads.slice();
  h.changeAuthority({transport: 'B'});
  await h.store.getState().loadImages();
  assert.deepEqual(h.imageReads, before, 'a new source must be accepted before reading its gallery');
});

test('a late gallery reply cannot adopt old pixels after a same-folder backend change', async () => {
  const h = harness({withImages: true, holdImages: true});
  const pending = h.store.getState().importFolder('/source/A', 'classification'); await tick();
  assert.equal(h.imageRequests.length, 1);
  h.changeAuthority({transport: 'B'});
  h.imageRequests[0].resolve(h.imageResult('/source/A')); await pending;
  assert.deepEqual(h.store.getState().images, []);
  assert.deepEqual(h.gallery(), []);
});

test('same-folder same-ID source restores once after backend acceptance, never during selection', async () => {
  const h = harness({withImages: true});
  await h.store.getState().importFolder('/source/A', 'classification'); h.render();
  h.changeAuthority({transport: 'B'}); h.render(); await tick();
  assert.deepEqual(h.summaries, [], 'the old folder cannot be read before project acceptance');
  h.acceptAuthority(); h.render(); await h.store.getState().ensureImported('classification'); await tick(); h.render(); await tick();
  assert.deepEqual(h.summaries, [{backend: 'B', folder_path: '/source/A', task: 'classification'}]);
  assert.equal(h.gallery().length, 1, 'the accepted new backend supplies current pixels');
  h.render(); await tick(); assert.equal(h.summaries.length, 1, 'acceptance does not create a restore loop');
});

test('old-source split and annotation refresh actions cannot dispatch on a new backend', async () => {
  const h = harness({withImages: true});
  await h.store.getState().importFolder('/source/A', 'classification');
  const before = h.calls.slice(); h.changeAuthority({transport: 'B'});
  await h.store.getState().applySplit(.5, .25, .25); await h.store.getState().annotationsChanged();
  assert.deepEqual(h.calls, before);
});

test('a late old-backend split reply cannot change the current split summary', async () => {
  const h = harness({withImages: true, holdSplit: true});
  await h.store.getState().importFolder('/source/A', 'classification');
  const before = h.store.getState().split, pending = h.store.getState().applySplit(.5, .25, .25); await tick();
  h.changeAuthority({transport: 'B'}); h.splits[0].resolve({split: {train: 99, val: 0, test: 0}}); await pending;
  assert.deepEqual(h.store.getState().split, before);
});
