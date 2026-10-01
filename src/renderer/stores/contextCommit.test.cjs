// Project context is accepted only when the store accepts a selected project (S1-01 P1 hooks, S0-05 store side).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');

function load(file, mocks) {
  const name = path.resolve(__dirname, file);
  const m = new Module(name, module);
  m.filename = name;
  m.paths = Module._nodeModulePaths(__dirname);
  const req = m.require.bind(m);
  m.require = key => (Object.hasOwn(mocks, key) ? mocks[key] : req(key));
  m._compile(ts.transpileModule(fs.readFileSync(name, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, name);
  return m.exports;
}

const first = { id: 'first', name: 'First', project_dir: '/fixture/first', task: 'classification', source_dataset_dir: '/source-a', active_labelset_id: 'default' };
const second = { id: 'second', name: 'Second', project_dir: '/fixture/second', task: 'detection', source_dataset_dir: '/source-b', active_labelset_id: 'default' };

function harness({ server = () => first, accept = () => true, dirty = false, open, create } = {}) {
  const value = initial => ({ getState: () => initial, setState: update => Object.assign(initial, update) });
  const log = [];
  const commits = [];
  let store;
  const annotation = value({ setTask() {}, setImages: async () => { log.push(`apply:${store.getState().project?.id}`); return true; }, isDirty: dirty, saveIfDirty: async () => true });
  const dataset = value({ hasSelectedFolder: true, folderPath: '/source-a', datasetKey: '/source-a\0classification', lastImportedKey: '/source-a\0classification',
    importError: null, isLoading: false, isSplitting: false, isGenerating: false, setFolderPath() {},
    importFolder: async (folder, task) => { log.push(`import:${folder}`); Object.assign(dataset.getState(), { folderPath: folder, datasetKey: `${folder}\0${task}`, lastImportedKey: `${folder}\0${task}` }); },
    ensureImported: async task => { log.push(`ensure:${task}`); } });
  const responses = [];
  const respond = project => { const body = { ...project }; responses.push(body); return body; };
  const projectApi = {
    getCurrent: async () => respond(server()),
    open: open || (async dir => respond(dir === second.project_dir ? second : first)),
    create: create || (async () => respond(second)),
    restore: async () => respond(second),
    list: async () => ({ projects: [] }),
    update: async data => ({ ...first, ...data }),
    acceptContext: (response, apply) => {
      commits.push(response); log.push(`accept:${response.id}:${store.getState().project?.id}`);
      if (accept(response) === false) throw new Error('프로젝트 선택 문맥이 바뀌었습니다.');
      apply?.();
    },
  };
  const m = load('./useProjectStore.ts', {
    '../services/api': {
      api: { project: projectApi },
      getApiPersistenceIdentity: () => 'local', getProjectContext: () => null, getProjectContextGeneration: () => 0, setCachedPort() {},
    },
    '../services/datasetWorkflow': { datasetWorkflow: {}, workflowError: error => String(error?.message || error) },
    './projectViewState': { projectViewScope: (p, scope) => `${scope}:${p?.id}`, readProjectStep: () => 3, rememberProjectStep() {} },
    './useAnnotationStore': { useAnnotationStore: annotation },
    './useDatasetStore': { useDatasetStore: dataset },
    './useFlowchartStore': { useFlowchartStore: value({ pipelineDirty: false }) },
    './useTrainingStore': { useTrainingStore: value({ isTraining: false }) },
    './useInspectionRunStore': { useInspectionRunStore: value({ isRunning: false }) },
    './useModelAssistRunStore': { useModelAssistRunStore: value({ activeOperations: 0 }) },
  });
  store = m.useProjectStore;
  store.setState({ project: first, projectDir: first.project_dir, task: first.task, activeStep: 3 });
  const setProjectApi = overrides => Object.assign(projectApi, overrides);
  return { m, store, log, commits, responses, annotation, setProjectApi };
}

test('a sync refused because of unsaved edits never commits the server selection', async () => {
  const { store, commits } = harness({ server: () => second, dirty: true });
  await store.getState().syncCurrentProject();
  assert.equal(store.getState().project.id, 'first');
  assert.ok(store.getState().projectError);
  assert.deepEqual(commits, [], 'the visible project keeps its request context');
});

test('an accepted sync accepts the exact selected response once and binds the project before reading its data', async () => {
  const { store, commits, responses, log } = harness({ server: () => second });
  await store.getState().syncCurrentProject();
  assert.equal(store.getState().project.id, 'second');
  assert.equal(commits.length, 1);
  assert.equal(commits[0], responses[0], 'the response object the store accepted');
  assert.deepEqual(log.slice(0, 3), ['accept:second:first', 'apply:second', 'ensure:detection'],
    'accepted while A is shown, then B is bound before annotation and dataset work');
});

test('a refused or failing acceptance leaves the project, task and data untouched', async () => {
  const failing = harness({ server: () => second, accept: () => { throw new Error('candidate expired'); } });
  await failing.store.getState().syncCurrentProject();
  assert.equal(failing.store.getState().project.id, 'first');
  assert.ok(!failing.log.some(entry => entry.startsWith('apply:') || entry.startsWith('ensure:')), failing.log.join(' > '));
  const { store, log } = harness({ server: () => second, accept: () => false });
  await store.getState().syncCurrentProject();
  assert.deepEqual([store.getState().project.id, store.getState().task], ['first', 'classification']);
  assert.match(store.getState().projectError || '', /문맥이 바뀌었습니다/);
  assert.ok(!log.some(entry => entry.startsWith('apply:') || entry.startsWith('ensure:') || entry.startsWith('import:')), log.join(' > '));
});

test('opening and creating projects commit the response before importing; a stale open changes nothing', async () => {
  const opened = harness();
  assert.equal(await opened.store.getState().openProject(second.project_dir), true);
  assert.equal(opened.commits.at(-1).id, 'second');
  assert.ok(opened.log.indexOf('accept:second:first') < opened.log.indexOf('apply:second'), opened.log.join(' > '));
  assert.ok(opened.log.indexOf('apply:second') < opened.log.indexOf('ensure:detection'), opened.log.join(' > '));
  const created = harness();
  assert.equal(await created.store.getState().createProject({ name: 'Second', task: 'detection' }), true);
  assert.equal(created.commits.at(-1).id, 'second');
  const stale = harness({ accept: response => response.id !== 'second' });
  assert.equal(await stale.store.getState().openProject(second.project_dir), false);
  assert.equal(stale.store.getState().project.id, 'first');
});

test('the daemon-restart reopen inside saveOpenEdits accepts the reopened current project', async () => {
  const { m, commits } = harness({ server: () => second });
  await m.saveOpenEdits();
  assert.equal(commits.length, 1);
  assert.equal(commits[0].id, 'first', 'the store reopens and binds the project it is editing');
  const { m: refused } = harness({ server: () => second, accept: () => false });
  await assert.rejects(refused.saveOpenEdits(), /문맥이 바뀌었습니다/);
});

test('edits made while an open, create or restore request is in flight refuse the switch before accepting it', async () => {
  for (const action of ['open', 'create', 'restore']) {
    const world = harness();
    const editDuringRequest = async () => { world.annotation.getState().isDirty = true; return { ...second }; };
    const store = world.store;
    const api = { open: editDuringRequest, create: editDuringRequest, restore: editDuringRequest };
    const run = {
      open: () => store.getState().openProject(second.project_dir),
      create: () => store.getState().createProject({ name: 'Second', task: 'detection' }),
      restore: () => store.getState().restoreProject('/backup.zip', '/restored'),
    }[action];
    world.setProjectApi(api);
    assert.equal(await run(), false, `${action} is refused`);
    assert.equal(store.getState().project.id, 'first', `${action} keeps the visible project`);
    assert.deepEqual(world.commits, [], `${action} never accepts the new context`);
    assert.ok(!world.log.some(entry => entry.startsWith('apply:')), `${action} does not touch label data`);
    assert.match(store.getState().projectError || '', /새 편집/);
  }
});
