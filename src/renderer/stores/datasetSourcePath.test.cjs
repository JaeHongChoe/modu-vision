// A dataset picked under one spelling is imported under the path the project API saved (the backend resolves symlinks,
// and on Windows a mapped network drive to its UNC path), so training's "current source" check is met.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');

function load(api, project, evaluation = { allowLatestRecovery: null }) {
  const name = path.resolve(__dirname, 'useDatasetStore.ts');
  const m = new Module(name, module);
  m.filename = name;
  m.paths = Module._nodeModulePaths(__dirname);
  const store = getState => ({ getState: () => getState });
  const mocks = {
    '../services/api': { api },
    './useTrainingStore': { useTrainingStore: store({ invalidateForDataChange() {} }) },
    './useEvaluationStore': { useEvaluationStore: { getState: () => ({ invalidateForDataChange(allow) { evaluation.allowLatestRecovery = allow; } }),
      setState: update => Object.assign(evaluation, update) } },
    './useFlowchartStore': { useFlowchartStore: store({ invalidateForDataChange() {} }) },
    './useProjectStore': { useProjectStore: { setState: update => Object.assign(project, typeof update === 'function' ? update(project) : update) } },
  };
  const req = m.require.bind(m);
  m.require = key => (Object.hasOwn(mocks, key) ? mocks[key] : req(key));
  m._compile(ts.transpileModule(fs.readFileSync(name, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, name);
  return m.exports.useDatasetStore;
}

function harness(saved, hold) {
  const imports = [];
  const evaluation = { allowLatestRecovery: null };
  const project = { project: { id: 'p1', name: 'P', project_dir: '/projects/p1', source_dataset_dir: null } };
  const resolved = folder => typeof saved === 'function' ? saved(folder) : saved === undefined ? folder : saved;
  const api = {
    project: { update: async ({ source_dataset_dir }) => {
      if (hold) await hold(source_dataset_dir);
      if (saved instanceof Error) throw saved;
      return { id: 'p1', name: 'P', project_dir: '/projects/p1', source_dataset_dir: resolved(source_dataset_dir) }; } },
    dataset: {
      import: async request => { imports.push(request.folder_path); return { total_images: 3, split: { train: 2, val: 1, test: 0 }, classes: {} }; },
      getImages: async () => ({ images: [], total: 0 }),
    },
  };
  return { store: load(api, project, evaluation), imports, project, evaluation };
}

test('an import follows the source path the project API saved', async () => {
  const { store, imports, project } = harness('/private/var/qa/data');
  await store.getState().importFolder('/var/qa/data', 'segmentation');
  const state = store.getState();
  assert.equal(state.folderPath, '/private/var/qa/data');
  assert.equal(state.datasetKey, '/private/var/qa/data\0segmentation');
  assert.equal(state.lastImportedKey, '/private/var/qa/data\0segmentation');
  assert.deepEqual(imports, ['/private/var/qa/data'], 'the import reads the saved spelling');
  assert.equal(project.project.source_dataset_dir, state.folderPath, 'training sees the project source and the dataset as the same');
  assert.equal(state.isLoading, false);
});

test('an unchanged path stays as picked, and a failed save keeps the picked path with its reason', async () => {
  const same = harness(undefined);
  await same.store.getState().importFolder('Z:\\line3\\data', 'classification');
  assert.equal(same.store.getState().folderPath, 'Z:\\line3\\data');
  assert.deepEqual(same.imports, ['Z:\\line3\\data']);
  const failed = harness(new Error('project locked'));
  await failed.store.getState().importFolder('/var/qa/data', 'segmentation');
  assert.equal(failed.store.getState().folderPath, '/var/qa/data');
  assert.match(failed.store.getState().sourceSaveError, /project locked/);
});

test('the same folder picked again through its other spelling is the same folder: no latest-model recovery, task changes marked stale', async () => {
  const canonical = folder => folder.replace(/^\/var\//, '/private/var/');
  const first = harness(canonical);
  await first.store.getState().importFolder('/var/qa/data', 'segmentation');
  assert.equal(first.evaluation.allowLatestRecovery, true, 'a first pick may recover the latest model of that source');
  await first.store.getState().importFolder('/var/qa/data', 'segmentation');
  assert.equal(first.evaluation.allowLatestRecovery, false, 'picking the same folder again is not a new source');
  await first.store.getState().importFolder('/var/qa/data', 'classification');
  assert.deepEqual(first.store.getState().staleDatasetKeys.sort(),
    ['/private/var/qa/data\0classification', '/private/var/qa/data\0segmentation'], 'another task in the same folder marks both');
  assert.equal(first.evaluation.allowLatestRecovery, false);
  await first.store.getState().importFolder('/var/qa/other', 'classification');
  assert.equal(first.evaluation.allowLatestRecovery, true, 'another folder may recover again');
});

test('an empty saved path is not adopted, and a newer pick made while the path is saved keeps the newer folder', async () => {
  const empty = harness(() => null);
  await empty.store.getState().importFolder('/var/qa/data', 'segmentation');
  assert.equal(empty.store.getState().folderPath, '/var/qa/data');
  let releaseFirst;
  const held = harness(folder => folder.replace(/^\/var\//, '/private/var/'),
    folder => folder.endsWith('first') ? new Promise(resolve => { releaseFirst = resolve; }) : Promise.resolve());
  const firstImport = held.store.getState().importFolder('/var/qa/first', 'segmentation');
  await new Promise(setImmediate);
  await held.store.getState().importFolder('/var/qa/second', 'segmentation');
  releaseFirst();
  await firstImport;
  assert.equal(held.store.getState().folderPath, '/private/var/qa/second', 'the older answer never replaces the newer pick');
  assert.deepEqual(held.imports, ['/private/var/qa/second']);
});

function loadProjectStore(dataset, update, getCurrent) {
  const name = path.resolve(__dirname, 'useProjectStore.ts');
  const m = new Module(name, module);
  m.filename = name;
  m.paths = Module._nodeModulePaths(__dirname);
  const value = initial => ({ getState: () => initial, setState: change => Object.assign(initial, typeof change === 'function' ? change(initial) : change) });
  const project = { id: 'p1', name: 'P', project_dir: '/projects/p1', task: 'segmentation', source_dataset_dir: null };
  const mocks = {
    '../services/api': { api: { project: { getCurrent: getCurrent || (async () => project), update } },
      getApiPersistenceIdentity: () => 'local', getProjectContext: () => null, getProjectContextGeneration: () => 0, setCachedPort() {} },
    '../services/datasetWorkflow': { datasetWorkflow: {}, workflowError: error => String(error?.message || error) },
    './projectViewState': { projectViewScope: () => 'scope', readProjectStep: () => 3, rememberProjectStep() {} },
    './useAnnotationStore': { useAnnotationStore: value({ isDirty: false, saveIfDirty: async () => true }) },
    './useDatasetStore': { useDatasetStore: dataset },
    './useFlowchartStore': { useFlowchartStore: value({ pipelineDirty: false, isRunning: false, isLoading: false, isSaving: false }) },
    './useTrainingStore': { useTrainingStore: value({ isTraining: false }) },
    './useInspectionRunStore': { useInspectionRunStore: value({ isRunning: false }) },
    './useModelAssistRunStore': { useModelAssistRunStore: value({ activeOperations: 0 }) },
  };
  const req = m.require.bind(m);
  m.require = key => (Object.hasOwn(mocks, key) ? mocks[key] : req(key));
  m._compile(ts.transpileModule(fs.readFileSync(name, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, name);
  m.exports.useProjectStore.setState({ project, projectDir: project.project_dir, task: project.task });
  return m.exports;
}

test('a source save retried before a project action follows the saved spelling too', async () => {
  const picked = '/var/qa/data\0segmentation';
  const state = { hasSelectedFolder: true, folderPath: '/var/qa/data', datasetKey: picked, lastImportedKey: picked, staleDatasetKeys: [picked],
    importError: null, sourceSaveError: 'project locked', isLoading: false, isSplitting: false, isGenerating: false };
  const dataset = { getState: () => state, setState: change => Object.assign(state, typeof change === 'function' ? change(state) : change) };
  const saved = [];
  const { saveOpenEdits, useProjectStore } = loadProjectStore(dataset, async ({ source_dataset_dir }) => {
    saved.push(source_dataset_dir);
    return { id: 'p1', name: 'P', project_dir: '/projects/p1', task: 'segmentation', source_dataset_dir: source_dataset_dir.replace(/^\/var\//, '/private/var/') };
  });
  await saveOpenEdits();
  assert.deepEqual(saved, ['/var/qa/data']);
  assert.equal(state.sourceSaveError, null);
  assert.equal(state.folderPath, '/private/var/qa/data');
  assert.equal(state.datasetKey, '/private/var/qa/data\0segmentation');
  assert.equal(state.lastImportedKey, '/private/var/qa/data\0segmentation');
  assert.ok(state.staleDatasetKeys.includes('/private/var/qa/data\0segmentation'), 'a stale import stays stale under the saved spelling');
  assert.equal(useProjectStore.getState().project.source_dataset_dir, state.folderPath, 'training sees the project source and the dataset as the same');
});

const canonicalVar = folder => folder.replace(/^\/var\//, '/private/var/');

test('a newer pick made while the project store module loads keeps the newer folder', async () => {
  let newer = null, store = null;
  const evaluation = { allowLatestRecovery: null };
  const project = { project: { id: 'p1', name: 'P', project_dir: '/projects/p1', source_dataset_dir: null } };
  const imports = [];
  const name = path.resolve(__dirname, 'useDatasetStore.ts');
  const m = new Module(name, module);
  m.filename = name;
  m.paths = Module._nodeModulePaths(__dirname);
  const holder = { useProjectStore: { setState: update => Object.assign(project, typeof update === 'function' ? update(project) : update) } };
  const mocks = {
    '../services/api': { api: { project: { update: async ({ source_dataset_dir }) => ({ id: 'p1', name: 'P', project_dir: '/projects/p1', source_dataset_dir: canonicalVar(source_dataset_dir) }) },
      dataset: { import: async request => { imports.push(request.folder_path); return { total_images: 3, split: { train: 2, val: 1, test: 0 }, classes: {} }; }, getImages: async () => ({ images: [], total: 0 }) } } },
    './useTrainingStore': { useTrainingStore: { getState: () => ({ invalidateForDataChange() {} }) } },
    './useEvaluationStore': { useEvaluationStore: { getState: () => ({ invalidateForDataChange(allow) { evaluation.allowLatestRecovery = allow; } }), setState: update => Object.assign(evaluation, update) } },
    './useFlowchartStore': { useFlowchartStore: { getState: () => ({ invalidateForDataChange() {} }) } },
  };
  // The dynamic import of the project store is the moment a newer pick lands.
  Object.defineProperty(mocks, './useProjectStore', { enumerable: true, get() {
    if (!newer) newer = store.getState().importFolder('/var/qa/newer', 'segmentation');
    return holder;
  } });
  const req = m.require.bind(m);
  m.require = key => (Object.hasOwn(mocks, key) ? mocks[key] : req(key));
  m._compile(ts.transpileModule(fs.readFileSync(name, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, name);
  store = m.exports.useDatasetStore;
  await store.getState().importFolder('/var/qa/older', 'segmentation');
  await newer;
  assert.equal(store.getState().folderPath, '/private/var/qa/newer');
  assert.deepEqual(imports, ['/private/var/qa/newer'], 'the older pick never imports over the newer one');
});

test('the saved spelling still respects stale marks, an explicit choice and a save that failed before', async () => {
  const stale = harness(canonicalVar);
  await stale.store.getState().importFolder('/var/qa/other', 'segmentation');
  stale.store.setState(state => ({ staleDatasetKeys: [...state.staleDatasetKeys, '/private/var/qa/data\0segmentation'] }));
  await stale.store.getState().importFolder('/var/qa/data', 'segmentation');
  assert.equal(stale.evaluation.allowLatestRecovery, false, 'a stale source never recovers its latest model');
  const picked = harness(canonicalVar);
  await picked.store.getState().importFolder('/var/qa/other', 'segmentation');
  picked.store.setState(state => ({ staleDatasetKeys: [...state.staleDatasetKeys, '/var/qa/data\0segmentation'] }));
  await picked.store.getState().importFolder('/var/qa/data', 'segmentation');
  assert.equal(picked.evaluation.allowLatestRecovery, false, 'a stale mark under the picked spelling counts too');
  const chosen = harness(canonicalVar);
  await chosen.store.getState().importFolder('/var/qa/data', 'segmentation');
  await chosen.store.getState().importFolder('/var/qa/data', 'segmentation', true);
  assert.equal(chosen.evaluation.allowLatestRecovery, true, 'an explicit choice is kept');
  let fail = true;
  const retried = harness(folder => { if (fail) { fail = false; throw new Error('project locked'); } return canonicalVar(folder); });
  await retried.store.getState().importFolder('/var/qa/data', 'segmentation');
  assert.equal(retried.store.getState().lastImportedKey, '/var/qa/data\0segmentation', 'the failed save left the picked spelling');
  await retried.store.getState().importFolder('/var/qa/data', 'segmentation');
  assert.equal(retried.evaluation.allowLatestRecovery, false, 'the same folder again, whichever spelling recorded it');
});

test('a retried source save never adopts over a newer pick or a re-import in progress', async () => {
  for (const change of [{ datasetKey: '/projects/other\0segmentation', folderPath: '/projects/other' }, { isLoading: true }]) {
    const picked = '/var/qa/data\0segmentation';
    // Like zustand, every update replaces the state object; a snapshot read before the save stays as it was.
    let state = { hasSelectedFolder: true, folderPath: '/var/qa/data', datasetKey: picked, lastImportedKey: picked, staleDatasetKeys: [],
      importError: null, sourceSaveError: 'project locked', isLoading: false, isSplitting: false, isGenerating: false };
    const dataset = { getState: () => state, setState: next => { state = { ...state, ...(typeof next === 'function' ? next(state) : next) }; } };
    const { saveOpenEdits } = loadProjectStore(dataset, async ({ source_dataset_dir }) => {
      state = { ...state, ...change };  // lands while the save is in flight
      return { id: 'p1', name: 'P', project_dir: '/projects/p1', task: 'segmentation', source_dataset_dir: canonicalVar(source_dataset_dir) };
    });
    await saveOpenEdits().catch(() => undefined);
    assert.notEqual(state.folderPath, '/private/var/qa/data', JSON.stringify(change));
    assert.notEqual(state.datasetKey, '/private/var/qa/data\0segmentation');
  }
});


test('reopening a saved source only reads its scoped summary and never saves/imports it', async () => {
  const writes = [];
  const summary = {total_images:6,source_images:6,classes:{defect:6},split:{train:4,val:1,test:1}};
  const api = {project:{update:async()=>{writes.push('update');throw new Error('viewer cannot write');}},
    dataset:{import:async()=>{writes.push('import');throw new Error('viewer cannot import');},
      currentSummary:async data=>{assert.deepEqual(data,{folder_path:'/saved/source',task:'segmentation'});return summary;},
      getImages:async()=>({items:[],total:6})}};
  const store=load(api,{project:{id:'p1'}});
  store.getState().setFolderPath('/saved/source');
  await store.getState().ensureImported('segmentation');
  assert.deepEqual(writes,[]);
  assert.equal(store.getState().importError,null);
  assert.equal(store.getState().sourceSaveError,null);
  assert.equal(store.getState().totalImages,6);
  assert.equal(store.getState().lastImportedKey,'/saved/source\0segmentation');
});

test('a saved-source mismatch refuses restoration instead of importing a different folder', async () => {
  const api={dataset:{currentSummary:async()=>{throw new Error('saved source changed');}},project:{update:async()=>{throw new Error('must not save');}}};
  const store=load(api,{project:{id:'p1'}});
  store.getState().setFolderPath('/saved/source');
  await assert.rejects(store.getState().ensureImported('segmentation'),/saved source changed/);
  assert.match(store.getState().importError,/saved source changed/);
  assert.equal(store.getState().lastImportedKey,null);
});


test('clean logout remains possible after membership revocation without project reads or writes',async()=>{
  const dataset={getState:()=>({hasSelectedFolder:false}),setState:()=>{}};
  const {saveOpenEdits}=loadProjectStore(dataset,()=>assert.fail('no source save'),async()=>{throw new Error('membership revoked');});
  await saveOpenEdits({allowCleanDisconnect:true});
});
