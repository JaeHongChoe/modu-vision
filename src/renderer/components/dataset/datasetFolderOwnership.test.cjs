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

function harness({ holdSave = false, holdImport = false, holdImages = false, holdSplit = false, holdSummary = false, withImages = false, hostKind = 'desktop', importFailure, saveFailure, onInvalidate } = {}) {
  let generation = 0, backend = 'A', port = 8001, cursor = 0;
  let context = {workspace_id: 'workspace', project_id: 'A', actor_id: 'actor', mode: 'local'};
  const project = { task: 'classification', language: 'en', projectDir: '/projects/A', backendPort: port,
    project: { id: 'A', name: 'A', project_dir: '/projects/A', task: 'classification', source_dataset_dir: '/source/A' } };
  const hook = state => Object.assign(selector => selector ? selector(state) : state, {
    getState: () => state,
    setState: change => Object.assign(state, typeof change === 'function' ? change(state) : change),
  });
  const projectStore = hook(project), calls = [], pickers = [], saves = [], imports = [], imageReads = [], imageRequests = [], summaries = [], summaryReads = [], splits = [], slots = [], effects = [];
  const summary = { total_images: 3, source_images: 3, classes: { OK: 3 }, split: { train: 2, val: 1, test: 0 } };
  const imageResult = folder => ({items: withImages ? [{image_id: 'part', file_name: 'part.png', file_path: `${folder}/part.png`,
    thumbnail_url: `/api/dataset/thumbnail/part.png?file_path=${folder}/part.png`, label: 'OK', split: 'train', width: 24, height: 24}] : [], total: 3});
  const api = {
    project: { update: async data => {
      calls.push({ kind: 'save', backend, project: project.project.id, folder: data.source_dataset_dir,
        ...(data.expected_source ? {expected_source: data.expected_source} : {}) });
      const saved = { ...project.project, source_dataset_dir: data.source_dataset_dir };
      if (holdSave) { const pending = deferred(); saves.push(pending); await pending.promise; }
      if (saveFailure) throw saveFailure;
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
      currentSummary: async data => {summaries.push({backend, ...data}); if (holdSummary) {const pending = deferred(); summaryReads.push(pending); return pending.promise;} return summary;},
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
  const downstream = require('zustand').create(() => ({ invalidations: 0, invalidateForDataChange() {
    downstream.setState(state => ({ invalidations: state.invalidations + 1 }));
    onInvalidate?.();
  } }));
  const inert = { getState: () => ({ invalidateForDataChange() {} }), setState() {} };
  const datasetModule = load(path.resolve(__dirname, '../../stores/useDatasetStore.ts'), {
    '../services/api': apiModule, './useProjectStore': { useProjectStore: projectStore },
    './useTrainingStore': { useTrainingStore: downstream }, './useEvaluationStore': { useEvaluationStore: inert },
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
  const helpers = Object.fromEntries(['classDistribution', 'datasetImportView', 'splitCalibrator', 'datasetLayout', 'importChecklist'].map(name =>
    ['./' + name, load(path.join(__dirname, name + '.ts'), {})]));
  const shared = load(path.resolve(__dirname, '../../stores/assistContext.ts'), {});
  helpers['../../stores/assistContext'] = shared;
  const services = path.resolve(__dirname, '../../services');
  const browserHost = load(path.join(services, 'hostAdapter.ts'), {
    './linkedSources': load(path.join(services, 'linkedSources.ts'), {}),
    './browserRemote': load(path.join(services, 'browserRemote.ts'), {}),
    './api': load(path.join(services, 'api.ts'), {}),
    './browserSession': load(path.join(services, 'browserSession.ts'), {}),
  });
  const browserInput = load(path.join(__dirname, 'BrowserFolderPathInput.tsx'), {
    '../../services/hostAdapter': browserHost,
  }).BrowserFolderPathInput;
  helpers['./BrowserFolderPathInput'] = { BrowserFolderPathInput: browserInput };
  helpers['../common/useScopedError'] = load(path.resolve(__dirname, '../common/useScopedError.ts'), {react});
  helpers['../../utils/datasetSplitCapability'] = load(path.resolve(__dirname, '../../utils/datasetSplitCapability.ts'), {});
  const scopedThumbnail = load(path.resolve(__dirname, '../common/ScopedDatasetThumbnail.tsx'), {'../../services/api': apiModule, '../../stores/thumbnailDiagnostics': {recordThumbnailRead: () => {}}});
  const filename = path.join(__dirname, 'DatasetStudio.tsx'), m = new Module(filename, module);
  m.filename = filename; m.paths = Module._nodeModulePaths(__dirname);
  const original = m.require.bind(m);
  m.require = name => name === 'react' ? react : name === 'react/jsx-runtime' ? { jsx, jsxs: jsx }
    : name === '../../stores/useProjectStore' ? { useProjectStore: projectStore }
    : name === '../../stores/useDatasetStore' ? { readDatasetAuthority: datasetModule.readDatasetAuthority,
      useDatasetStore: Object.assign(selector => selector ? selector(store.getState()) : store.getState(), {getState: store.getState}) }
    : name === '../common/ScopedDatasetThumbnail' ? scopedThumbnail
    : name === '../../services/api' ? apiModule
    : name === '../../services/hostAdapter' ? { host: { kind: () => hostKind, selectFolder: () => {
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
  return { store, downstream, api, ready: () => store.setState({sourceAuthority: datasetModule.readDatasetAuthority(), totalImages: 100, classes: {OK: 99, NG: 1}, split: {train: 80, val: 10, test: 10}, splitState: 'saved', labelsMissing: true, classSplitCounts: {OK: {train: 80, val: 10, test: 9}, NG: {train: 0, val: 0, test: 1}}}), project, shared, render, select, pickers, saves, imports, imageReads, imageRequests, imageResult, summaries, summaryReads, splits, summary, calls, move, changeAuthority,
    acceptAuthority: () => {generation++; context = {workspace_id: `workspace-${backend}`, project_id: project.project.id, actor_id: 'actor', mode: backend === 'A' ? 'local' : 'team'};},
    browserMarkup: () => {
      const node = nodes(render()).find(node => node.type === browserInput);
      assert.ok(node, 'DatasetStudio keeps the browser path entry in its toolbar');
      return require('react-dom/server').renderToStaticMarkup(require('react').createElement(browserInput, node.props));
    },
    // This source harness sees the real scoped component's input, not decoded DOM pixels.
    gallery: () => nodes(render()).filter(node => node.type === scopedThumbnail.ScopedDatasetThumbnail).map(node => node.props.path),
    unmount: () => slots.forEach(slot => slot?.cleanup?.()) };
}

test('a folder dialog failure reaches guidance while a late failure from the previous labelset does not', async () => {
  const h = harness(); const pending = h.select(); h.pickers[0].reject(Error('폴더 창을 열지 못했습니다. 경로 입력을 사용하세요.')); await pending; h.render();
  assert.deepEqual(h.shared.assistPanelErrors(), [{source: '데이터 준비', text: '폴더 창을 열지 못했습니다. 경로 입력을 사용하세요.'}]);
  const late = h.select(); h.project.project.active_labelset_id = 'next'; h.render();
  h.pickers[1].reject(Error('이전 라벨 세트의 폴더 창 실패')); await late; h.render();
  assert.deepEqual(h.shared.assistPanelErrors(), []); h.unmount();
});

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

// A shared login, project switch or logout remounts this step (browser-team-session): a verified-version dialog opened
// before the last remount closes with it and its ZIP upload is cancelled. While the change runs the dialog stays shut
// and the step says why; afterwards it opens as before.
test('a shared session change holds the verified-version dialog, says why, and releases it when it ends', () => {
  const h = harness();
  const all = tree => { const found = []; const visit = node => {
    if (Array.isArray(node)) return node.forEach(visit);
    if (node && typeof node === 'object') { found.push(node); visit(node.props?.children); }
  }; visit(tree); return found; };
  const view = () => {
    const tree = all(h.render());
    const opener = tree.find(node => node.type === 'button' && all(node.props.children).some(child => child.props?.children === '검증된 데이터 버전'));
    const notice = tree.find(node => node.props?.role === 'status' && /공동 작업 서버 연결을 바꾸는 중/.test(String(node.props.children)));
    const dialog = tree.find(node => node.props?.projectId === 'A' && 'registeredSource' in node.props);
    return { opener, notice, dialog };
  };
  h.project.sharedSessionChanging = true;
  let shown = view();
  assert.equal(shown.opener.props.disabled, true, 'the dialog cannot open while the change can still remount the step');
  assert.match(shown.opener.props.title, /공동 작업 서버 연결을 바꾸는 중입니다/);
  assert.ok(shown.notice, 'the step says why the dialog is held');
  h.project.sharedSessionChanging = false;
  shown = view();
  assert.equal(shown.opener.props.disabled, false);
  assert.equal(shown.notice, undefined);
  assert.equal(shown.dialog, undefined);
  shown.opener.props.onClick();
  assert.ok(view().dialog, 'the dialog opens once the change has ended');
});

const renderedText=node=>typeof node==='string'||typeof node==='number'?String(node):Array.isArray(node)?node.map(renderedText).join(''):renderedText(node?.props?.children||[]);
function renderedNodes(node,output=[]){if(Array.isArray(node)){node.forEach(n=>renderedNodes(n,output));return output;}if(node&&typeof node==='object'){output.push(node);renderedNodes(node.props?.children,output);}return output;}
function uploadedPanel(h) {
  h.project.project.annotations_dir = '/projects/A/annotations';
  h.project.project.active_labelset_id = 'default';
  h.ready();
  renderedNodes(h.render()).find(node => node.type === 'button' && renderedText(node) === '검증된 데이터 버전').props.onClick();
  return renderedNodes(h.render()).find(node => node.props?.projectId === 'A' && 'registeredSource' in node.props).props;
}
test('explicit ZIP source selection uses the existing import lifecycle with its original source precondition', async () => {
  const h = harness({hostKind: 'browser', withImages: true}), panel = uploadedPanel(h);
  assert.deepEqual(h.calls, [], 'opening the panel does not select or import an archive');
  await panel.onUseUploadedSource('/uploaded/source');
  assert.equal(h.project.project.source_dataset_dir, '/uploaded/source');
  assert.equal(h.store.getState().folderPath, '/uploaded/source');
  assert.deepEqual(h.calls, [
    {kind: 'import', backend: 'A', project: 'A', folder: '/uploaded/source'},
    {kind: 'save', backend: 'A', project: 'A', folder: '/uploaded/source', expected_source: {
      project_id: 'A', source_dataset_dir: '/source/A', task: 'classification', annotations_dir: '/projects/A/annotations', active_labelset_id: 'default',
    }},
  ]);
  assert.equal(renderedNodes(h.render()).some(node => node.props?.projectId === 'A' && 'registeredSource' in node.props), false);
  h.unmount();
});
test('a refused ZIP source write reports failure and reopens the previous source without another write', async () => {
  const h = harness({hostKind: 'browser', withImages: true, saveFailure: new Error('project.manage refused')}), panel = uploadedPanel(h);
  await assert.rejects(panel.onUseUploadedSource('/uploaded/source'), /project.manage refused/);
  assert.equal(h.project.project.source_dataset_dir, '/source/A');
  assert.equal(h.store.getState().folderPath, '/source/A');
  assert.deepEqual(h.summaries, [{backend: 'A', folder_path: '/source/A', task: 'classification'}]);
  assert.equal(h.calls.filter(call => call.kind === 'save').length, 1, 'the restore never writes the old source');
  assert.equal(h.store.getState().sourceSaveError, null, 'the restored old source has no pending retry of the refused ZIP');
  h.unmount();
});
test('a late ZIP import cannot save or restore over a newly selected project', async () => {
  const h = harness({hostKind: 'browser', holdImport: true}), panel = uploadedPanel(h);
  const pending = panel.onUseUploadedSource('/uploaded/source'); await tick();
  h.move({id: 'B'}); h.imports[0].resolve(h.summary);
  await assert.rejects(pending, /바뀌었습니다/);
  assert.equal(h.project.project.id, 'B');
  assert.equal(h.store.getState().folderPath, '/source/B');
  assert.equal(h.calls.some(call => call.kind === 'save'), false);
  assert.deepEqual(h.summaries, []);
  h.unmount();
});
test('a refused ZIP source write restores an empty project without a rollback write', async () => {
  const h = harness({hostKind: 'browser', saveFailure: new Error('source conflict 409')});
  h.project.project.source_dataset_dir = null; h.store.getState().setFolderPath('');
  const panel = uploadedPanel(h);
  await assert.rejects(panel.onUseUploadedSource('/uploaded/source'), /source conflict 409/);
  assert.equal(h.project.project.source_dataset_dir, null);
  assert.equal(h.store.getState().folderPath, '');
  assert.equal(h.store.getState().hasSelectedFolder, false);
  assert.deepEqual(h.summaries, []);
  assert.equal(h.calls.filter(call => call.kind === 'save').length, 1);
  assert.equal(h.calls.find(call => call.kind === 'save').expected_source.source_dataset_dir, null);
  h.unmount();
});
test('a late ZIP source-save refusal cannot reset a newer project', async () => {
  const h = harness({hostKind: 'browser', holdSave: true, saveFailure: new Error('project.manage refused')}), panel = uploadedPanel(h);
  const pending = panel.onUseUploadedSource('/uploaded/source'); await tick();
  h.move({id: 'B'}); h.saves[0].resolve();
  await assert.rejects(pending);
  assert.equal(h.project.project.id, 'B'); assert.equal(h.store.getState().folderPath, '/source/B');
  assert.deepEqual(h.summaries, []); h.unmount();
});
test('a late failed readonly restore cannot clear a newer project', async () => {
  const h = harness({hostKind: 'browser', holdSummary: true, saveFailure: new Error('project.manage refused')}), panel = uploadedPanel(h);
  const pending = panel.onUseUploadedSource('/uploaded/source'); await tick();
  assert.equal(h.summaryReads.length, 1);
  h.move({id: 'B'}); h.summaryReads[0].reject(new Error('old source unavailable'));
  await assert.rejects(pending, /project.manage refused/);
  assert.equal(h.project.project.id, 'B'); assert.equal(h.store.getState().folderPath, '/source/B'); h.unmount();
});
test('desktop import panels keep their existing source workflow without a browser-only action', () => {
  const h = harness(), panel = uploadedPanel(h);
  assert.equal(panel.onUseUploadedSource, undefined); assert.equal(panel.sourceSelectionScope, undefined); h.unmount();
});
for (const viewMode of ['simple', 'full']) {
  test(`an empty browser project opens ZIP intake in ${viewMode} view`, () => {
    const h = harness({hostKind: 'browser'});
    h.project.viewMode = viewMode;
    h.project.project.source_dataset_dir = null;
    h.store.setState({folderPath: '', hasSelectedFolder: false, datasetKey: null});
    const opener = () => renderedNodes(h.render()).find(node => node.type === 'button' && renderedText(node) === '검증된 데이터 버전');
    assert.equal(opener().props.disabled, false, 'a browser ZIP does not require a registered server folder');
    assert.match(opener().props.title, /ZIP/);
    opener().props.onClick();
    const panel = renderedNodes(h.render()).find(node => node.props?.projectId === 'A' && 'registeredSource' in node.props);
    assert.ok(panel, 'the existing intake panel opens for the selected project');
    assert.equal(panel.props.registeredSource, null);
    assert.equal(panel.props.datasetPath, '');
    h.unmount();
  });
  test(`empty browser ZIP entry retains project and session guards in ${viewMode} view`, () => {
    for (const blocked of ['missing project', 'different project directory', 'different task', 'project busy', 'loading', 'session changing']) {
      const h = harness({hostKind: 'browser'});
      h.project.viewMode = viewMode;
      h.project.project.source_dataset_dir = null;
      h.store.setState({folderPath: '', hasSelectedFolder: false, datasetKey: null});
      if (blocked === 'missing project') h.project.project.id = '';
      if (blocked === 'different project directory') h.project.projectDir = '/projects/B';
      if (blocked === 'different task') h.project.project.task = 'segmentation';
      if (blocked === 'project busy') h.project.isProjectBusy = true;
      if (blocked === 'loading') h.store.setState({isLoading: true});
      if (blocked === 'session changing') h.project.sharedSessionChanging = true;
      const opener = renderedNodes(h.render()).find(node => node.type === 'button' && renderedText(node) === '검증된 데이터 버전');
      assert.equal(opener.props.disabled, true, blocked);
      h.unmount();
    }
  });
  test(`empty desktop project still requires its dataset folder in ${viewMode} view`, () => {
    const h = harness();
    h.project.viewMode = viewMode;
    h.project.project.source_dataset_dir = null;
    h.store.setState({folderPath: '', hasSelectedFolder: false, datasetKey: null});
    const opener = renderedNodes(h.render()).find(node => node.type === 'button' && renderedText(node) === '검증된 데이터 버전');
    assert.equal(opener.props.disabled, true);
    h.unmount();
  });
}
for(const viewMode of ['simple','full'])test('Korean '+viewMode+' split guidance reaches the same actual split action',async()=>{
 const h=harness();h.project.language='ko';h.project.viewMode=viewMode;
 h.store.setState({sourceAuthority:null});const importing=h.store.getState().importFolder('/source/A', 'classification');await importing;
 const split=renderedNodes(h.render()).find(n=>n.type==='button'&&renderedText(n)==='이 비율로 나누기');
 assert.ok(split,'the checklist action name locates the real split button');assert.equal(split.props.disabled,false);
 await split.props.onClick();assert.equal(h.calls.filter(c=>c.kind==='split').length,1);h.unmount();
});


// The actual Zustand store and its synchronous subscriptions define these boundaries.
test('annotation refresh atomically becomes busy/stale before invalidation and refuses direct split', async () => {
  let h; const observed=[];
  h=harness({holdImport:true,onInvalidate:()=>observed.push(h.store.getState())}); h.ready();
  const updates=[];h.store.subscribe(state=>updates.push(state));
  const pending=h.store.getState().annotationsChanged();
  const first=updates[0];
  assert.equal(first.isLoading,true);assert.deepEqual(first.split,{train:0,val:0,test:0});
  assert.equal(first.classSplitCounts,null);assert.ok(first.staleDatasetKeys.includes(first.datasetKey));
  assert.equal(observed.length,1);assert.equal(observed[0],first);
  await h.store.getState().applySplit(.7,.2,.1);assert.equal(h.calls.filter(c=>c.kind==='split').length,0);
  h.imports[0].resolve(h.summary);await pending;
  assert.equal(h.store.getState().isLoading,false);assert.equal(h.imageReads.length,1);
});
for(const boundary of ['start publication','downstream invalidation'])for(const replacement of ['nested A','B','A to B to A']){
 test(`annotation owner retires after ${replacement} at ${boundary}`,async()=>{
  let h,armed=true,nested;const replace=()=>{
   if(!armed)return;armed=false;
   if(replacement!=='nested A')h.store.setState({folderPath:'/source/B',datasetKey:'/source/B\0classification'});
   if(replacement==='A to B to A')h.store.setState({folderPath:'/source/A',datasetKey:'/source/A\0classification'});
   nested=h.store.getState().annotationsChanged();
  };
  h=harness({holdImport:true,onInvalidate:boundary==='downstream invalidation'?replace:undefined});h.ready();
  if(boundary==='start publication')h.store.subscribe(replace);
  const outer=h.store.getState().annotationsChanged();
  assert.equal(h.imports.length,1,'only the replacement can dispatch');
  assert.equal(h.downstream.getState().invalidations,boundary==='start publication'?1:2);
  h.imports[0].resolve(h.summary);await Promise.all([outer,nested]);
  assert.equal(h.store.getState().totalImages,3);assert.equal(h.store.getState().isLoading,false);
 });
}
test('a result publication that starts a new refresh cannot dispatch the old gallery',async()=>{
 const h=harness({holdImport:true});h.ready();let next;
 h.store.subscribe((state,previous)=>{if(previous.isLoading&&!state.isLoading&&!next)next=state.annotationsChanged();});
 const first=h.store.getState().annotationsChanged();h.imports[0].resolve(h.summary);await first;
 assert.equal(h.imageReads.length,0);assert.equal(h.store.getState().isLoading,true);
 h.imports[1].resolve({...h.summary,total_images:7});await next;
 assert.equal(h.store.getState().totalImages,7);assert.equal(h.imageReads.length,1);
});
for(const fail of [false,true])test(`old refresh ${fail?'failure':'success'} cannot finish a newer same-source refresh`,async()=>{
 const h=harness({holdImport:true});h.ready();const old=h.store.getState().annotationsChanged(),current=h.store.getState().annotationsChanged();
 fail?h.imports[0].reject(Error('retired failure')):h.imports[0].resolve({...h.summary,total_images:999});await old;
 assert.equal(h.store.getState().isLoading,true);assert.equal(h.store.getState().importError,null);assert.equal(h.store.getState().totalImages,100);
 h.imports[1].resolve(h.summary);await current;assert.equal(h.store.getState().isLoading,false);
});
test('owned refresh failure atomically ends busy with an error and refuses split without effects',async()=>{
 const h=harness({holdImport:true});h.ready();const updates=[];h.store.subscribe(s=>updates.push(s));
 const pending=h.store.getState().annotationsChanged();h.imports[0].reject(Error('summary unavailable'));await pending;
 assert.equal(updates.length,2);assert.equal(updates[1].isLoading,false);assert.equal(updates[1].importError,'summary unavailable');
 const before=h.store.getState(),invalidations=h.downstream.getState().invalidations;
 await before.applySplit(.7,.2,.1);assert.equal(h.calls.filter(c=>c.kind==='split').length,0);
 assert.equal(h.store.getState(),before);assert.equal(h.downstream.getState().invalidations,invalidations);
});
for(const afterSuccess of [false,true])test(`retired gallery cannot publish ${afterSuccess?'after':'during'} annotation refresh`,async()=>{
 const h=harness({holdImport:true,holdImages:true});h.ready();const gallery=h.store.getState().loadImages();
 const refresh=h.store.getState().annotationsChanged();
 if(afterSuccess){h.imports[0].resolve(h.summary);await tick();}
 h.imageRequests[0].resolve({...h.imageResult('/old'),class_split_counts:{OLD:{train:99,val:0,test:0}}});await gallery;
 assert.equal(h.store.getState().classSplitCounts,null);assert.deepEqual(h.store.getState().images,[]);
 if(!afterSuccess){h.imports[0].resolve(h.summary);await tick();}
 h.imageRequests[1].resolve(h.imageResult('/new'));await refresh;
});
test('an old import awaiting gallery cannot clear a newer annotation busy flag in finally',async()=>{
 const h=harness({holdImport:true,holdImages:true});
 const old=h.store.getState().importFolder('/source/A','classification');h.imports[0].resolve(h.summary);await tick();
 const refresh=h.store.getState().annotationsChanged();h.imageRequests[0].resolve(h.imageResult('/old'));await old;
 assert.equal(h.store.getState().isLoading,true);
 h.imports[1].resolve(h.summary);await tick();h.imageRequests[1].resolve(h.imageResult('/new'));await refresh;
});
for(const fail of [false,true])test(`already admitted split ${fail?'failure':'success'} finishes without clearing annotation busy`,async()=>{
 const h=harness({holdSplit:true,holdImport:true});h.ready();const split=h.store.getState().applySplit(.7,.2,.1);
 const refresh=h.store.getState().annotationsChanged();
 if(fail){h.splits[0].reject(Error('split failed'));await assert.rejects(split,/split failed/);}
 else {h.splits[0].resolve({split:{train:70,val:20,test:10}});await tick();h.imports[1].resolve(h.summary);await split;}
 assert.equal(h.store.getState().isSplitting,false);assert.equal(h.store.getState().isLoading,true);
 h.imports[0].resolve(h.summary);await refresh;
});
for(const change of ['refresh','authority','source'])test(`first filtered gallery publication cannot dispatch fallback after ${change}`,async()=>{
 const h=harness({holdImages:true,holdImport:true});h.ready();h.store.setState({activeClassFilter:'NG',classSplitCounts:null});let once=true,refresh;
 h.store.subscribe(state=>{if(once&&state.totalImagesCount===3){once=false;if(change==='refresh')refresh=state.annotationsChanged();else if(change==='source')h.store.setState({folderPath:'/source/B',datasetKey:'/source/B\0classification'});else h.changeAuthority({transport:'B'});}});
 const gallery=h.store.getState().loadImages();h.imageRequests[0].resolve(h.imageResult('/source/A'));await tick();
 try {
  assert.equal(h.imageReads.length,1,'retired fallback must never dispatch');assert.equal(h.store.getState().classSplitCounts,null);
 } finally {
  for(const request of h.imageRequests)request.resolve(h.imageResult('/source/A'));
  await gallery;
  if(refresh){h.imports[0].resolve(h.summary);await tick();for(const request of h.imageRequests)request.resolve({...h.imageResult('/source/A'),class_split_counts:{NG:{train:1,val:1,test:1}}});await refresh;}
 }
});
test('a current filtered gallery retains its full-class fallback',async()=>{
 const h=harness({holdImages:true});h.ready();h.store.setState({activeClassFilter:'NG',classSplitCounts:null});
 const gallery=h.store.getState().loadImages();h.imageRequests[0].resolve(h.imageResult('/source/A'));await tick();
 assert.equal(h.imageReads.length,2);assert.equal(h.imageReads[1].class_name,undefined);
 const counts={NG:{train:1,val:1,test:1}};h.imageRequests[1].resolve({class_split_counts:counts});await gallery;
 assert.deepEqual(h.store.getState().classSplitCounts,counts);
});
for(const viewMode of ['simple','full'])for(const completion of ['zero','valid','imbalanced','failure'])test(`${viewMode} pending metadata hides stale advice and ${completion} restores only current guidance`,async()=>{
 const h=harness({holdImport:true});h.project.language='ko';h.project.viewMode=viewMode;h.ready();
 const oldText=renderedText(h.render());for(const oldAdvice of ['라벨이 아직 없습니다','부족한 클래스: NG 1장','극심한 클래스 불균형'])assert.ok(oldText.includes(oldAdvice),oldAdvice);
 const refresh=h.store.getState().annotationsChanged();
 const pendingText=renderedText(h.render());
 assert.match(pendingText,/데이터 정보를 확인하는 중/);
 for(const stale of ['분할을 아직 적용하지 않았습니다','극심한 클래스 불균형','분할 예상','적용된 분할 집계 확인 중','저장된 분할','라벨이 아직 없습니다','부족한 클래스','이미지가 없는 클래스','가장 많은 클래스','아래는 바뀔 분할의 예상'])assert.equal(pendingText.includes(stale),false,stale);
 const button=renderedNodes(h.render()).find(n=>n.type==='button'&&renderedText(n)==='이 비율로 나누기');assert.equal(button.props.disabled,true);
 if(completion==='failure')h.imports[0].reject(Error('summary unavailable'));
 else h.imports[0].resolve({...h.summary,split:completion==='zero'?{train:0,val:0,test:0}:h.summary.split,classes:completion==='imbalanced'?{OK:99,NG:1}:{OK:3},split_state:completion==='zero'?'stale':'saved'});
 await refresh;const text=renderedText(h.render());assert.equal(text.includes('데이터 정보를 확인하는 중'),false);
 assert.equal(text.includes('분할을 아직 적용하지 않았습니다'),completion==='zero');
 assert.equal(text.includes('극심한 클래스 불균형'),completion==='imbalanced');
 const completedButton=renderedNodes(h.render()).find(n=>n.type==='button'&&renderedText(n)==='이 비율로 나누기');assert.equal(completedButton.props.disabled,completion==='failure');
 if(completion==='failure'){assert.match(text,/summary unavailable/);for(const stale of ['분할 예상','저장된 분할','라벨이 아직 없습니다','부족한 클래스','가장 많은 클래스','아래는 바뀔 분할의 예상'])assert.equal(text.includes(stale),false,stale);}
 if(completion==='imbalanced')assert.ok(text.includes('부족한 클래스: NG 1장'),'completed current class advice returns');
 h.unmount();
});

test('an untouched empty source retains existing split guidance without a summary read error',()=>{
 const h=harness();const text=renderedText(h.render());
 assert.equal(text.includes('Dataset information is unavailable'),false);
 assert.equal(text.includes('Preview of the new split'),true);h.unmount();
});
for(const boundary of ['start publication','downstream invalidation','response'])for(const identity of ['folder','source authority','read authority'])test(`annotation refresh retires on exact ${identity} replacement at ${boundary}`,async()=>{
 let h,armed=true;const change=()=>{if(!armed)return;armed=false;
  if(identity==='folder')h.store.setState({folderPath:'/source/other'});
  else if(identity==='source authority')h.store.setState({sourceAuthority:'replacement authority'});
  else h.changeAuthority({transport:'B'});
 };
 h=harness({holdImport:true,onInvalidate:boundary==='downstream invalidation'?change:undefined});h.ready();
 if(boundary==='start publication')h.store.subscribe(change);
 const refresh=h.store.getState().annotationsChanged();
 if(boundary==='response'){change();h.imports[0].resolve({...h.summary,total_images:999});}
 else assert.equal(h.imports.length,0,'a replaced identity must not dispatch');
 await refresh;assert.equal(h.imageReads.length,0);assert.equal(h.store.getState().totalImages,100);
});
for(const replacement of ['folder','source authority','read authority'])test(`a fresh result publication cannot dispatch gallery after ${replacement} changes`,async()=>{
 const h=harness({holdImport:true});h.ready();let once=true;
 h.store.subscribe((state,previous)=>{if(once&&previous.isLoading&&!state.isLoading){once=false;
  if(replacement==='folder')h.store.setState({folderPath:'/source/other'});
  else if(replacement==='source authority')h.store.setState({sourceAuthority:'replacement authority'});
  else h.changeAuthority({transport:'B'});
 }});
 const refresh=h.store.getState().annotationsChanged();h.imports[0].resolve(h.summary);await refresh;
 assert.equal(h.imageReads.length,0);
});


for (const boundary of ['project', 'bootstrap', 'labelset']) {
  test(`browser path help survives ${boundary} changes while the old picker error retires`, async () => {
    const previous = global.window; global.window = {};
    const h = harness();
    try {
      const checkHelp = () => {
        const html = h.browserMarkup();
        assert.match(html, /aria-describedby="[^"]+"/);
        assert.match(html, />브라우저[^<]*백엔드[^<]*읽을 수 있는[^<]*경로[^<]*<\/p>/);
      };
      checkHelp();
      const pending = h.select();
      h.pickers[0].reject(Error('이전 폴더 선택 오류')); await pending; h.render();
      assert.deepEqual(h.shared.assistPanelErrors(), [{ source: '데이터 준비', text: '이전 폴더 선택 오류' }]);
      checkHelp();
      if (boundary === 'project') h.move({ id: 'B' });
      else if (boundary === 'bootstrap') { h.changeAuthority({ transport: 'B' }); h.project.isProjectBusy = true; }
      else h.project.project.active_labelset_id = 'next';
      h.render(); checkHelp(); assert.deepEqual(h.shared.assistPanelErrors(), []);
      if (boundary === 'bootstrap') { h.acceptAuthority(); h.project.isProjectBusy = false; h.render(); }
      checkHelp(); assert.deepEqual(h.shared.assistPanelErrors(), [], 'capability help does not revive retired picker failures');
      assert.deepEqual(h.calls, [], 'guidance alone never imports or saves a path');
    } finally { h.unmount(); global.window = previous; }
  });
}
