const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const mockApi = {
  dataset: {
    import: async () => { throw new Error('not configured'); },
    getImages: async () => ({ total: 0, items: [] }),
    split: async () => ({ split: { train: 0, val: 0, test: 0 } }),
  },
};
const downstreamInvalidations = { training: 0, evaluation: 0, flowchart: 0 };
const downstreamStores = Object.fromEntries(
  Object.keys(downstreamInvalidations).map((name) => [name, {
    getState: () => ({ invalidateForDataChange: () => { downstreamInvalidations[name] += 1; } }),
  }])
);

const storePath = path.resolve(__dirname, '../src/renderer/stores/useDatasetStore.ts');
const source = fs.readFileSync(storePath, 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const storeModule = new Module(storePath, module);
storeModule.filename = storePath;
storeModule.paths = Module._nodeModulePaths(path.dirname(storePath));
const originalRequire = storeModule.require.bind(storeModule);
storeModule.require = (specifier) => {
  if (specifier === '../services/api') return { api: mockApi };
  if (specifier === './useTrainingStore') return { useTrainingStore: downstreamStores.training };
  if (specifier === './useEvaluationStore') return { useEvaluationStore: downstreamStores.evaluation };
  if (specifier === './useFlowchartStore') return { useFlowchartStore: downstreamStores.flowchart };
  return originalRequire(specifier);
};
storeModule._compile(compiled, storePath);
const store = storeModule.exports.useDatasetStore;

const summary = (task, split = { train: 0, val: 0, test: 0 }) => ({
  total_images: task === 'segmentation' ? 80 : 88,
  source_images: 88,
  unlabeled_images: task === 'segmentation' ? 8 : 0,
  classes: task === 'segmentation' ? { defect_mask: 80 } : { good: 88 },
  split,
  split_supported: true,
  split_unavailable_reason: null,
});

function reset(folder = '/data') {
  store.setState({
    folderPath: folder, hasSelectedFolder: true, datasetKey: null, importError: null,
    totalImages: 0, sourceImages: 0, unlabeledImages: 0, classes: {},
    split: { train: 0, val: 0, test: 0 }, images: [], totalImagesCount: 0,
    page: 1, activeSplitFilter: 'all', activeClassFilter: null,
    isLoading: false, isSplitting: false, splitError: null,
    splitSupported: null, splitUnavailableReason: null, staleDatasetKeys: [],
  });
}

test('default folder is not imported before the operator selects data', async () => {
  reset('./datasets/synthetic');
  store.setState({ hasSelectedFolder: false });
  let importCount = 0;
  mockApi.dataset.import = async () => { importCount += 1; return summary('segmentation'); };
  await store.getState().ensureImported('classification');
  await store.getState().ensureImported('segmentation');
  assert.equal(importCount, 0);
  assert.equal(store.getState().importError, null);
  assert.equal(store.getState().totalImages, 0);
});

test('task change reimports the same folder and clears unsupported task counts', async () => {
  reset();
  const imports = [];
  mockApi.dataset.import = async ({ task }) => {
    imports.push(task);
    if (task === 'classification') throw new Error('Flat LabelMe data requires segmentation');
    return summary(task);
  };
  mockApi.dataset.getImages = async () => ({ total: 88, items: [] });

  await store.getState().ensureImported('segmentation');
  assert.equal(store.getState().totalImages, 80);
  assert.equal(store.getState().unlabeledImages, 8);
  assert.deepEqual(store.getState().split, { train: 0, val: 0, test: 0 });
  await store.getState().ensureImported('segmentation');
  assert.deepEqual(imports, ['segmentation']);

  await assert.rejects(store.getState().ensureImported('classification'), /requires segmentation/);
  assert.deepEqual(imports, ['segmentation', 'classification']);
  assert.equal(store.getState().totalImages, 0);
  assert.equal(store.getState().totalImagesCount, 0);
  assert.deepEqual(store.getState().classes, {});
  assert.deepEqual(store.getState().split, { train: 0, val: 0, test: 0 });
  assert.match(store.getState().importError, /requires segmentation/);
});

test('structured segmentation import marks resplitting unavailable with its reason', async () => {
  reset('/structured');
  mockApi.dataset.import = async () => ({
    ...summary('segmentation'),
    split_supported: false,
    split_unavailable_reason: '구조화 images/masks 데이터는 재분할할 수 없습니다.',
  });
  mockApi.dataset.getImages = async () => ({ total: 80, items: [] });
  await store.getState().importFolder('/structured', 'segmentation');
  assert.equal(store.getState().splitSupported, false);
  assert.match(store.getState().splitUnavailableReason, /images\/masks/);
});

test('late response for an old task cannot replace the latest import', async () => {
  reset('/shared');
  let finishOld;
  mockApi.dataset.import = ({ task }) => task === 'segmentation'
    ? new Promise((resolve) => { finishOld = resolve; })
    : Promise.resolve(summary(task, { train: 70, val: 18, test: 0 }));
  mockApi.dataset.getImages = async () => ({ total: 88, items: [] });

  const oldImport = store.getState().importFolder('/shared', 'segmentation');
  await store.getState().importFolder('/shared', 'classification');
  finishOld(summary('segmentation', { train: 56, val: 16, test: 8 }));
  await oldImport;
  assert.equal(store.getState().totalImages, 88);
  assert.deepEqual(store.getState().classes, { good: 88 });
  assert.deepEqual(store.getState().split, { train: 70, val: 18, test: 0 });
});

test('late split result cannot replace counts after a task change', async () => {
  reset('/shared');
  let finishSplit;
  mockApi.dataset.import = async ({ task }) => summary(task, task === 'classification'
    ? { train: 70, val: 18, test: 0 }
    : { train: 0, val: 0, test: 0 });
  mockApi.dataset.getImages = async () => ({ total: 88, items: [] });
  mockApi.dataset.split = () => new Promise((resolve) => { finishSplit = resolve; });

  await store.getState().importFolder('/shared', 'segmentation');
  const oldSplit = store.getState().applySplit(0.7, 0.2, 0.1);
  await store.getState().importFolder('/shared', 'classification');
  const afterTaskChange = { ...downstreamInvalidations };
  finishSplit({ split: { train: 56, val: 16, test: 8 } });
  await oldSplit;
  assert.deepEqual(store.getState().split, { train: 70, val: 18, test: 0 });
  assert.equal(store.getState().isSplitting, false);
  assert.deepEqual(downstreamInvalidations, afterTaskChange);
});

test('failed split preserves the current model and evaluation state', async () => {
  reset('/labelme');
  const datasetKey = '/labelme\0segmentation';
  store.setState({ datasetKey, split: { train: 7, val: 2, test: 1 } });
  mockApi.dataset.split = async () => { throw new Error('unsupported split'); };
  const before = { ...downstreamInvalidations };
  await assert.rejects(store.getState().applySplit(0.6, 0.2, 0.2), /unsupported split/);
  assert.deepEqual(downstreamInvalidations, before);
  assert.deepEqual(store.getState().split, { train: 7, val: 2, test: 1 });
  assert.equal(store.getState().isSplitting, false);
  assert.match(store.getState().splitError, /unsupported split/);
  assert.equal(store.getState().staleDatasetKeys.includes(datasetKey), false);
});

test('structured split failure exposes the backend detail', async () => {
  reset('/paired');
  store.setState({ datasetKey: '/paired\0segmentation', split: { train: 7, val: 2, test: 1 } });
  mockApi.dataset.split = async () => {
    throw { detail: { message_ko: '재분할 불가', details: 'images+masks 구성은 지원하지 않습니다' } };
  };
  await assert.rejects(store.getState().applySplit(0.6, 0.2, 0.2));
  assert.match(store.getState().splitError, /images\+masks 구성은 지원하지 않습니다/);
});

test('successful split invalidates dependent results only after the response arrives', async () => {
  reset('/labelme');
  const datasetKey = '/labelme\0segmentation';
  store.setState({ datasetKey, split: { train: 7, val: 2, test: 1 }, splitError: 'previous split failed' });
  let finishSplit;
  mockApi.dataset.split = () => new Promise((resolve) => { finishSplit = resolve; });
  const before = { ...downstreamInvalidations };

  const splitting = store.getState().applySplit(0.6, 0.2, 0.2);
  assert.equal(store.getState().isSplitting, true);
  assert.equal(store.getState().splitError, null);
  assert.deepEqual(store.getState().split, { train: 7, val: 2, test: 1 });
  assert.deepEqual(downstreamInvalidations, before);
  assert.equal(store.getState().staleDatasetKeys.includes(datasetKey), false);

  finishSplit({ split: { train: 6, val: 2, test: 2 } });
  await splitting;
  assert.deepEqual(store.getState().split, { train: 6, val: 2, test: 2 });
  assert.equal(store.getState().staleDatasetKeys.includes(datasetKey), true);
  for (const name of Object.keys(before)) assert.equal(downstreamInvalidations[name], before[name] + 1);
});

test('split request uses the selected dataset task', async () => {
  reset('/labelme');
  store.setState({ datasetKey: '/labelme\0segmentation' });
  let request;
  mockApi.dataset.split = async (body) => {
    request = body;
    return { split: { train: 7, val: 2, test: 1 } };
  };
  await store.getState().applySplit(0.7, 0.2, 0.1);
  assert.equal(request.folder_path, '/labelme');
  assert.equal(request.task, 'segmentation');
});

test('gallery paging keeps page, page size, and total image count', async () => {
  reset('/pages');
  const calls = [];
  mockApi.dataset.getImages = async (query) => {
    calls.push(query);
    return { total: 88, items: [{ id: `page-${query.offset}` }] };
  };
  await store.getState().loadImages(2);
  assert.equal(calls[0].offset, store.getState().pageSize);
  assert.equal(store.getState().page, 2);
  assert.equal(store.getState().totalImagesCount, 88);
  assert.equal(store.getState().images[0].id, `page-${store.getState().pageSize}`);
});

test('gallery requests the selected task and ignores an older task response', async () => {
  reset('/structured');
  const calls = [];
  let finishSegmentation;
  mockApi.dataset.getImages = (query) => {
    calls.push(query);
    if (calls.length === 1) return new Promise((resolve) => { finishSegmentation = resolve; });
    return Promise.resolve({ total: 1, items: [{ image_id: 'detection-image' }] });
  };
  store.setState({ datasetKey: '/structured\0segmentation' });
  const oldPage = store.getState().loadImages(1);
  store.setState({ datasetKey: '/structured\0detection' });
  await store.getState().loadImages(1);
  finishSegmentation({ total: 1, items: [{ image_id: 'segmentation-image' }] });
  await oldPage;
  assert.deepEqual(calls.map((query) => query.task), ['segmentation', 'detection']);
  assert.equal(store.getState().images[0].image_id, 'detection-image');
});

test('images API serializes task in its query string', async () => {
  const apiPath = path.resolve(__dirname, '../src/renderer/services/api.ts');
  const apiCompiled = ts.transpileModule(fs.readFileSync(apiPath, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const apiModule = new Module(apiPath, module);
  apiModule.filename = apiPath;
  apiModule.paths = Module._nodeModulePaths(path.dirname(apiPath));
  apiModule._compile(apiCompiled, apiPath);
  const originalFetch = global.fetch;
  let requestedUrl;
  global.fetch = async (url) => {
    requestedUrl = String(url);
    return { ok: true, json: async () => ({ total: 0, items: [] }) };
  };
  try {
    await apiModule.exports.api.dataset.getImages({ folder_path: '/structured', task: 'segmentation' });
    assert.equal(new URL(requestedUrl).searchParams.get('task'), 'segmentation');
  } finally {
    global.fetch = originalFetch;
  }
});

test('label save invalidates downstream results and clears split before refresh returns', async () => {
  reset('/labeled');
  store.setState({ datasetKey: '/labeled\0segmentation', totalImages: 80,
    split: { train: 56, val: 16, test: 8 }, images: [{ image_id: 'sample' }] });
  let finishRefresh;
  mockApi.dataset.import = () => new Promise((resolve) => { finishRefresh = resolve; });
  const before = { ...downstreamInvalidations };
  const refreshing = store.getState().annotationsChanged();
  assert.deepEqual(store.getState().split, { train: 0, val: 0, test: 0 });
  assert.equal(store.getState().images.length, 1);
  for (const name of Object.keys(before)) assert.equal(downstreamInvalidations[name], before[name] + 1);
  finishRefresh({ ...summary('segmentation'), total_images: 81, unlabeled_images: 7 });
  await refreshing;
  assert.equal(store.getState().totalImages, 81);
  assert.equal(store.getState().unlabeledImages, 7);
  assert.deepEqual(store.getState().split, { train: 0, val: 0, test: 0 });
});

test('dataset counters render saved partition sizes, not ratio preview values', () => {
  const studio = fs.readFileSync(path.resolve(__dirname, '../src/renderer/components/dataset/DatasetStudio.tsx'), 'utf8');
  assert.doesNotMatch(studio, /split\.(?:train|val|test)\s*\|\|\s*(?:trainCount|valCount|testCount)/);
  assert.match(studio, /labelKo: `학습용 \(\$\{split\.train\}\)`/);
});
