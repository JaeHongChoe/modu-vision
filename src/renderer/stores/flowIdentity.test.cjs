// S0-05: layout moves keep results; semantic edits keep earlier results as an
// earlier version; failed task changes report an explicit error.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');

function load(file, mocks = {}) {
  const name = path.resolve(__dirname, file);
  const m = new Module(name, module);
  m.filename = name;
  m.paths = Module._nodeModulePaths(__dirname);
  const req = m.require.bind(m);
  m.require = key => (Object.hasOwn(mocks, key) ? mocks[key] : req(key));
  m._compile(ts.transpileModule(fs.readFileSync(name, 'utf8'), {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, name);
  return m.exports;
}

const project = { id: 'p', project_dir: '/workspace', source_dataset_dir: '/source', active_labelset_id: 'default' };
const pipeline = () => ({
  id: 'flow', name: 'flow',
  nodes: [
    { id: 'input', position: { x: 0, y: 0 }, data: { node_type: 'input', label: 'Input' } },
    { id: 'rule', position: { x: 200, y: 0 }, data: { node_type: 'decision', label: 'Rule', threshold: 0.5 } },
  ],
  edges: [{ id: 'e1', source: 'input', target: 'rule' }],
});
const result = verdict => ({ final_verdict: verdict, is_ok: verdict === 'OK', roi_count: 0, crops: [], execution_steps: [] });

/** Fails instead of hanging when a promise does not settle in time. */
function within(promise, ms, message) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`${message}: not settled within ${ms} ms`)), ms); })])
    .finally(() => clearTimeout(timer));
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

function flowStore(run) {
  const m = load('./useFlowchartStore.ts', {
    '../services/api': { api: { flowchart: { run } } },
    '../services/flowDraft': { flowDraft: {} },
    './useProjectStore': { useProjectStore: { getState: () => ({ project }) } },
    '../components/flowchart/flowchartStartup': { getFlowchartModelTask: () => null },
  });
  const store = m.useFlowchartStore;
  store.setState({ pipeline: pipeline(), cleanPipeline: null, selectedImage: { source: 'dataset', imagePath: '/source/a.png', fileName: 'a.png' } });
  return { m, store };
}

test('moving a node keeps a completed result and only advances the layout revision', async () => {
  const { m, store } = flowStore(async () => result('NG'));
  assert.equal(await store.getState().runPipeline(), true);
  const before = store.getState().flowIdentity;
  store.getState().moveNode('rule', { x: 320, y: 40 });
  const state = store.getState();
  assert.equal(state.executionResult?.final_verdict, 'NG', 'layout moves do not discard results');
  assert.equal(state.flowIdentity.semantic_revision, before.semantic_revision);
  assert.equal(state.flowIdentity.layout_revision, before.layout_revision + 1);
  assert.equal(m.isExecutionResultCurrent(state), true);
  assert.equal(state.pipelineDirty, true, 'the moved layout still needs saving');
});

test('a node moved while a run is in flight does not discard that run', async () => {
  const pending = deferred();
  const { m, store } = flowStore(() => pending.promise);
  const running = store.getState().runPipeline();
  store.getState().moveNode('rule', { x: 400, y: 80 });
  pending.resolve(result('OK'));
  assert.equal(await running, true);
  assert.equal(store.getState().executionResult?.final_verdict, 'OK');
  assert.equal(store.getState().errorMessage, null);
  assert.equal(m.isExecutionResultCurrent(store.getState()), true);
});

test('a semantic edit keeps the earlier result as an earlier version, and undo makes it current again', async () => {
  const { m, store } = flowStore(async () => result('NG'));
  await store.getState().runPipeline();
  const ranAt = store.getState().executionIdentity;
  store.getState().updateNodeData('rule', { threshold: 0.8 });
  let state = store.getState();
  assert.equal(state.executionResult?.final_verdict, 'NG', 'the earlier result is preserved');
  assert.ok(state.flowIdentity.semantic_revision > ranAt.semantic_revision);
  assert.equal(m.isExecutionResultCurrent(state), false, 'it is shown as belonging to an earlier version');
  store.getState().undo();
  state = store.getState();
  assert.equal(state.executionResult?.final_verdict, 'NG');
  assert.equal(m.isExecutionResultCurrent(state), true, 'returning to the executed graph makes it current again');
});

test('a semantic edit during a run stores that run as an earlier version', async () => {
  const pending = deferred();
  const { m, store } = flowStore(() => pending.promise);
  const running = store.getState().runPipeline();
  store.getState().updateNodeData('rule', { threshold: 0.9 });
  pending.resolve(result('OK'));
  await running;
  const state = store.getState();
  assert.equal(state.executionResult?.final_verdict, 'OK');
  assert.equal(m.isExecutionResultCurrent(state), false);
});

test('changing the inspected image during a run still discards that run', async () => {
  const pending = deferred();
  const { store } = flowStore(() => pending.promise);
  const running = store.getState().runPipeline();
  store.getState().setSelectedImage({ source: 'dataset', imagePath: '/source/b.png', fileName: 'b.png' });
  pending.resolve(result('OK'));
  assert.equal(await running, false);
  assert.equal(store.getState().executionResult, null);
});

function projectStore(projectApi, { dataset: datasetState = {}, identity = { value: 'local' } } = {}) {
  const value = initial => ({ getState: () => initial, setState: update => Object.assign(initial, update) });
  const annotation = value({ setTask() {}, setImages: async () => true, isDirty: false, saveIfDirty: async () => true });
  // One shared object, so a fake import's updates are what the store reads.
  const defaults = { hasSelectedFolder: false, isLoading: false, isSplitting: false, isGenerating: false, setFolderPath() {} };
  for (const [key, fallback] of Object.entries(defaults)) if (!(key in datasetState)) datasetState[key] = fallback;
  const dataset = value(datasetState);
  const m = load('./useProjectStore.ts', {
    '../services/api': {
      api: { project: { getCurrent: async () => ({ ...project, task: 'classification' }), list: async () => ({ projects: [] }), acceptContext(_project, apply) { apply?.(); }, ...projectApi } },
      getApiPersistenceIdentity: () => identity.value, getProjectContext: () => null, getProjectContextGeneration: () => 0, setCachedPort() {},
    },
    '../services/datasetWorkflow': { datasetWorkflow: {}, workflowError: error => String(error?.message || error) },
    './projectViewState': { projectViewScope: (value, scope) => `${scope}:${value?.id}`, readProjectStep: () => 1, rememberProjectStep() {} },
    './useAnnotationStore': { useAnnotationStore: annotation },
    './useDatasetStore': { useDatasetStore: dataset },
    './useFlowchartStore': { useFlowchartStore: value({ pipelineDirty: false }) },
    './useTrainingStore': { useTrainingStore: value({ isTraining: false }) },
    './useInspectionRunStore': { useInspectionRunStore: value({ isRunning: false }) },
    './useModelAssistRunStore': { useModelAssistRunStore: value({ activeOperations: 0 }) },
  });
  const store = m.useProjectStore;
  store.setState({ task: 'classification', project: { ...project, task: 'classification' }, projectDir: project.project_dir });
  store.dataset = dataset;
  store.annotation = annotation;
  return store;
}

/** A dataset whose import for a new task fails until `repair()` makes it succeed. */
function failingImport() {
  const state = { hasSelectedFolder: true, folderPath: '/source', datasetKey: '/source\0classification',
    lastImportedKey: '/source\0classification', importError: null, isLoading: false };
  let healthy = false;
  state.importFolder = async (folder, task) => {
    const key = `${folder}\0${task}`;
    Object.assign(state, { datasetKey: key, importError: null });
    if (!healthy) { state.importError = 'Import failed'; throw new Error('Import failed'); }
    state.lastImportedKey = key;
  };
  state.ensureImported = async task => {
    if (state.datasetKey === `${state.folderPath}\0${task}`) return;
    await state.importFolder(state.folderPath, task);
  };
  return { state, repair() { healthy = true; } };
}

test('a failed task change returns an explicit error and keeps the project task', async () => {
  const store = projectStore({ update: async () => { const error = new Error('Another job is running'); error.status = 409; throw error; } });
  const outcome = await store.getState().updateTask('segmentation');
  assert.equal(outcome.ok, false);
  assert.match(outcome.error, /Another job is running/);
  assert.equal(outcome.task, 'classification', 'the outcome names the task that is actually active');
  assert.equal(store.getState().task, 'classification');
  assert.equal(store.getState().isProjectBusy, false);
  const unchanged = await store.getState().updateTask('classification');
  assert.deepEqual(unchanged, { ok: true, task: 'classification', changed: false });
});

const other = { id: 'b', project_dir: '/workspace-b', name: 'B', source_dataset_dir: '/source-b', active_labelset_id: 'default', task: 'detection' };

test('a task reply that arrives after another project was selected never changes that project', async () => {
  const reply = deferred();
  let requested;
  const store = projectStore({ update: () => { requested?.(); return reply.promise; } });
  const sent = new Promise(resolve => { requested = resolve; });
  const change = store.getState().updateTask('segmentation');
  await sent;
  store.setState({ project: other, projectDir: other.project_dir, task: other.task, projectName: other.name });
  reply.resolve({ ...project, task: 'segmentation' });
  const outcome = await within(change, 1000, 'the task change settles');
  const state = store.getState();
  assert.deepEqual([state.project.id, state.projectDir, state.task], ['b', '/workspace-b', 'detection'], 'project B stays intact');
  assert.equal(outcome.ok, false);
  assert.equal(outcome.applied, true, 'the earlier project may have changed on the server');
  assert.equal(outcome.task, 'detection');
  assert.equal(state.projectError, null, 'no error from project A is shown in project B');
});

test('a task change refuses a second change and blocks opening another project until it finishes', async () => {
  const reply = deferred();
  let updates = 0, opens = 0;
  const store = projectStore({
    update: () => { updates++; return reply.promise; },
    open: async () => { opens++; return other; },
  });
  const change = store.getState().updateTask('segmentation');
  await new Promise(setImmediate);
  const second = await within(store.getState().updateTask('anomaly'), 1000, 'a second task change is answered at once');
  assert.equal(second.ok, false);
  assert.equal(second.applied, false);
  assert.equal(await within(store.getState().openProject(other.project_dir), 1000, 'a project switch is answered at once'), false,
    'a project switch waits for the task change');
  reply.resolve({ ...project, task: 'segmentation' });
  assert.deepEqual(await change, { ok: true, task: 'segmentation', changed: true });
  assert.deepEqual([updates, opens], [1, 0]);
  assert.equal(store.getState().isProjectBusy, false);
});

test('a task reply for a different project than the one it was sent from is not applied', async () => {
  const store = projectStore({ update: async () => ({ ...other, task: 'segmentation' }) });
  const outcome = await store.getState().updateTask('segmentation');
  assert.equal(outcome.ok, false);
  assert.equal(store.getState().project.id, 'p');
  assert.equal(store.getState().task, 'classification');
});

test('a server-side project switch during a task change is synchronised once the change settles', async () => {
  const reply = deferred();
  let serverProject = { ...project, task: 'classification' };
  const store = projectStore({ getCurrent: async () => serverProject, update: () => reply.promise });
  const change = store.getState().updateTask('segmentation');
  await new Promise(setImmediate);
  serverProject = other;
  await store.getState().syncCurrentProject();
  assert.equal(store.getState().project.id, 'p', 'the sync waits for the task change');
  reply.resolve({ ...project, task: 'segmentation' });
  await within(change, 1000, 'the task change settles');
  for (let tick = 0; tick < 50 && (store.getState().project.id !== 'b' || store.getState().isProjectBusy); tick++) await new Promise(setImmediate);
  assert.deepEqual([store.getState().project.id, store.getState().projectDir, store.getState().task], ['b', '/workspace-b', 'detection']);
  assert.equal(store.getState().isProjectBusy, false);
});

test('renaming a flow keeps the result current; changing its connections makes it an earlier version', async () => {
  const { m, store } = flowStore(async () => result('NG'));
  await store.getState().runPipeline();
  store.getState().replacePipeline({ ...store.getState().pipeline, name: 'renamed', description: 'notes' });
  assert.equal(m.isExecutionResultCurrent(store.getState()), true, 'name and description do not change what the flow computes');
  store.getState().replacePipeline({ ...store.getState().pipeline, edges: [] });
  assert.equal(m.isExecutionResultCurrent(store.getState()), false, 'a removed connection changes what the flow computes');
});

test('a sync deferred behind a refused task change keeps the refusal message for the same project', async () => {
  const reply = deferred();
  const store = projectStore({ update: () => reply.promise });
  const change = store.getState().updateTask('segmentation');
  await new Promise(setImmediate);
  await store.getState().syncCurrentProject();
  const error = new Error('Another job is running'); error.status = 409;
  reply.reject(error);
  const outcome = await within(change, 1000, 'the task change settles');
  assert.equal(outcome.ok, false);
  for (let tick = 0; tick < 50 && store.getState().isProjectBusy; tick++) await new Promise(setImmediate);
  assert.equal(store.getState().isProjectBusy, false);
  assert.match(store.getState().projectError || '', /Another job is running/, 'the deferred sync does not erase the refusal');
});

test('a task change applied on the server whose import failed stays reported until the import is verified again', async () => {
  const data = failingImport();
  let serverProject = { ...project, task: 'classification' };
  const store = projectStore({
    getCurrent: async () => serverProject,
    update: async () => { serverProject = { ...project, task: 'segmentation' }; return serverProject; },
  }, { dataset: data.state });
  store.setState({ activeStep: 3 });
  const outcome = await store.getState().updateTask('segmentation');
  assert.deepEqual([outcome.ok, outcome.applied, store.getState().task], [false, true, 'segmentation']);
  assert.match(store.getState().projectError || '', /Import failed/);
  await store.getState().syncCurrentProject();
  assert.match(store.getState().projectError || '', /Import failed/, 'a reconnect sync without a verified import keeps the failure');
  data.repair();
  store.dataset.getState().datasetKey = '/source\0classification';
  await store.getState().syncCurrentProject();
  assert.equal(store.getState().projectError, null, 'a verified reimport for the active task clears it');
});

test('a failure of one project or server is never shown after switching to another', async () => {
  const data = failingImport();
  const identity = { value: 'local' };
  let serverProject = { ...project, task: 'classification' };
  const store = projectStore({
    getCurrent: async () => serverProject,
    update: async () => { serverProject = { ...project, task: 'segmentation' }; return serverProject; },
  }, { dataset: data.state, identity });
  store.setState({ activeStep: 3 });
  await store.getState().updateTask('segmentation');
  assert.match(store.getState().projectError || '', /Import failed/);
  identity.value = 'shared:https://server';
  await store.getState().syncCurrentProject();
  assert.equal(store.getState().projectError, null, 'another transport does not inherit the local failure');
  identity.value = 'local';
  serverProject = other;
  store.dataset.getState().datasetKey = '/source-b\0detection';
  await store.getState().syncCurrentProject();
  assert.equal(store.getState().project.id, 'b');
  assert.equal(store.getState().projectError, null, 'another project does not inherit the failure');
});

test('reopening the same project keeps an applied failure until its import is verified', async () => {
  const data = failingImport();
  let serverProject = { ...project, task: 'classification' };
  const store = projectStore({
    getCurrent: async () => serverProject,
    update: async () => { serverProject = { ...project, task: 'segmentation' }; return serverProject; },
    open: async () => serverProject,
  }, { dataset: data.state });
  store.setState({ activeStep: 3 });
  await store.getState().updateTask('segmentation');
  assert.equal(await store.getState().openProject(project.project_dir), true);
  assert.match(store.getState().projectError || '', /Import failed/, 'a reopen without a verified import keeps the failure');
  data.repair();
  await store.dataset.getState().importFolder('/source', 'segmentation');
  assert.equal(await store.getState().openProject(project.project_dir), true);
  assert.equal(store.getState().projectError, null, 'a reopen after a verified import for the active task clears it');
});

async function appliedFailure(extraApi = {}) {
  const data = failingImport();
  const server = { project: { ...project, task: 'classification' } };
  const store = projectStore({
    getCurrent: async () => server.project,
    update: async ({ task }) => { server.project = { ...project, task }; return server.project; },
    ...(typeof extraApi === 'function' ? extraApi(server) : extraApi),
  }, { dataset: data.state });
  store.setState({ activeStep: 3 });
  const outcome = await store.getState().updateTask('segmentation');
  assert.deepEqual([outcome.ok, outcome.applied], [false, true]);
  return { store, data, server };
}

test('a backup or a dialog that clears messages never hides an unverified applied failure', async () => {
  const { store } = await appliedFailure({ backup: async () => ({ archive_path: '/backup.zip' }) });
  assert.ok(await store.getState().backupProject('/backups'));
  assert.match(store.getState().projectError || '', /Import failed/, 'a successful backup keeps it');
  store.getState().clearProjectError();
  assert.match(store.getState().projectError || '', /Import failed/, 'clearing messages keeps it');
});

test('a refused retry keeps an unverified applied failure through later syncs', async () => {
  let refuse = false;
  const { store } = await appliedFailure(server => ({
    update: async ({ task }) => {
      if (refuse) { const error = new Error('Another job is running'); error.status = 409; throw error; }
      server.project = { ...project, task };
      return server.project;
    },
  }));
  refuse = true;
  const retry = await store.getState().updateTask('anomaly');
  assert.equal(retry.ok, false);
  assert.match(store.getState().projectError || '', /Another job is running/, 'the refused retry is reported');
  await store.getState().syncCurrentProject();
  assert.match(store.getState().projectError || '', /Import failed/, 'the unresolved applied failure is still reported');
});

test('a sync that fails after seeing another project keeps the failure of the project that stays open', async () => {
  const { store, server } = await appliedFailure();
  server.project = other;
  store.annotation.getState().isDirty = true;
  await store.getState().syncCurrentProject();
  assert.equal(store.getState().project.id, 'p', 'unsaved edits keep the failed project open');
  store.annotation.getState().isDirty = false;
  server.project = { ...project, task: 'segmentation' };
  await store.getState().syncCurrentProject();
  assert.match(store.getState().projectError || '', /Import failed/, 'its failure is still reported');
});

test('activating a label set whose reimport succeeds verifies and clears an applied failure', async () => {
  const { store, data } = await appliedFailure({ activateLabelset: async () => ({ ...project, task: 'segmentation', active_labelset_id: 'second' }) });
  data.repair();
  assert.equal(await store.getState().activateLabelset('second'), true);
  assert.equal(store.getState().projectError, null);
});

test('after a verified reimport the failure can be dismissed and later actions no longer show it', async () => {
  const { store, data } = await appliedFailure({ backup: async () => ({ archive_path: '/backup.zip' }) });
  data.repair();
  await store.dataset.getState().importFolder('/source', 'segmentation');
  store.getState().clearProjectError();
  assert.equal(store.getState().projectError, null, 'a verified import for the active task lets the message be dismissed');
  assert.ok(await store.getState().backupProject('/backups'));
  assert.equal(store.getState().projectError, null);
});

test('a task change that ends in another project does not leave the earlier project failure visible', async () => {
  let release, hold = false, sent;
  const requested = new Promise(resolve => { sent = resolve; });
  const { store } = await appliedFailure(server => ({
    update: ({ task }) => {
      server.project = { ...project, task };
      if (!hold) return Promise.resolve(server.project);
      sent();
      return new Promise(resolve => { release = () => resolve(server.project); });
    },
  }));
  hold = true;
  const change = store.getState().updateTask('anomaly');
  await within(requested, 1000, 'the task change request is sent');
  store.setState({ project: other, projectDir: other.project_dir, task: other.task });
  release();
  const outcome = await within(change, 1000, 'the task change settles');
  assert.equal(outcome.ok, false);
  assert.equal(store.getState().projectError, null, 'project B shows no failure of project A');
});
