const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const test = require('node:test');

// Actual component callbacks and project-store transitions; only React's hook
// scheduler, sibling stores, and API/IPC/telemetry transports are controlled.
const tick = () => new Promise(resolve => setImmediate(resolve));
function hostAdapterModule(name='hostAdapter.ts'){const file=path.join(__dirname,'..','..','services',name);const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const original=m.require.bind(m);m.require=key=>key==='./browserSession'?hostAdapterModule('browserSession.ts'):original(key);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
function load(relative, mocks) {
  const filename = path.resolve(__dirname, relative), loaded = new Module(filename, module);
  loaded.filename = filename; loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  const original = loaded.require.bind(loaded);
  loaded.require = name => Object.hasOwn(mocks, name) ? mocks[name] : name === '../../services/hostAdapter' ? hostAdapterModule() : original(name);
  loaded._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText, filename);
  return loaded.exports;
}
function nodes(node) {
  return !node || typeof node !== 'object' ? [] : [node, ...[node.props?.children].flat(Infinity).flatMap(nodes)];
}
function text(node) {
  return node == null || typeof node === 'boolean' ? '' : typeof node === 'object'
    ? [node.props?.children].flat(Infinity).map(text).join('') : String(node);
}
const first = { id: 'first', project_dir: '/projects/first', name: 'First', task: 'classification', source_dataset_dir: '/first-source', active_labelset_id: 'default' };
const second = { ...first, id: 'second', project_dir: '/projects/second', name: 'Second', source_dataset_dir: '/second-source' };
const context = project => ({ workspace_id: 'workspace', project_id: project.id, actor_id: 'owner', mode: 'team' });

async function harness({ connected = true, additive = true } = {}) {
  let backend = first, accepted = additive ? context(first) : null, identity = 'shared:https://fixture.invalid';
  let connection = connected ? { server_url: 'https://fixture.invalid', project_id: first.id,
    user: { id: 'owner', username: 'owner', administrator: true }, expires_at: 9999999999 } : null;
  const calls = [], writes = [];
  const value = state => ({ getState: () => state, setState: update => Object.assign(state, update) });
  const annotation = value({ isDirty: false, saveIfDirty: async () => true, setTask() {}, setImages: async () => true, setReviewerName() {} });
  const dataset = value({ hasSelectedFolder: false, isLoading: false, isSplitting: false, isGenerating: false,
    setFolderPath(folderPath) { this.folderPath = folderPath; }, ensureImported: async () => {} });
  const projectApi = {
    getCurrent: async () => backend, list: async () => ({ projects: [] }),
    acceptContext(project, apply) { accepted = additive ? context(project) : null; apply?.(); },
    open: async directory => { backend = directory === second.project_dir ? second : first; return backend; },
    create: async () => { calls.push('create'); backend = second; return second; },
    update: async body => { calls.push('update'); writes.push({ project_id: accepted?.project_id || backend.id, ...body }); backend = { ...backend, ...body }; return backend; },
  };
  const projectModule = load('../../stores/useProjectStore.ts', {
    '../services/api': { api: { project: projectApi }, getApiPersistenceIdentity: () => identity,
      getProjectContext: () => accepted, getProjectContextGeneration: () => 0, setCachedPort() {} },
    '../services/datasetWorkflow': { datasetWorkflow: {}, workflowError: e => e.message || String(e) },
    './projectViewState': { projectViewScope: (p, i) => `${i}:${p?.id}`, readProjectStep: () => 1, rememberProjectStep() {} },
    './useAnnotationStore': { useAnnotationStore: annotation }, './useDatasetStore': { useDatasetStore: dataset },
    './useFlowchartStore': { useFlowchartStore: value({ pipelineDirty: false }) },
    './useTrainingStore': { useTrainingStore: value({ isTraining: false }) },
    './useInspectionRunStore': { useInspectionRunStore: value({ isRunning: false }) },
    './useModelAssistRunStore': { useModelAssistRunStore: value({ activeOperations: 0 }) },
  });
  const store = projectModule.useProjectStore;
  store.setState({ project: first, projectDir: first.project_dir, projectName: first.name, task: first.task });
  const facade = Object.assign(selector => selector ? selector(store.getState()) : store.getState(), {
    getState: store.getState, setState: store.setState, subscribe: store.subscribe,
  });
  const hooks = []; let cursor = 0, effects = [], tree, dirty = false;
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const react = {
    useState(initial) { const i = cursor++; if (!hooks[i]) hooks[i] = { value: typeof initial === 'function' ? initial() : initial };
      return [hooks[i].value, next => { const v = typeof next === 'function' ? next(hooks[i].value) : next;
        if (!Object.is(v, hooks[i].value)) { hooks[i].value = v; dirty = true; } }]; },
    useRef(initial) { const i = cursor++; if (!hooks[i]) hooks[i] = { current: initial }; return hooks[i]; },
    useEffect(fn, deps) { const i = cursor++; if (!hooks[i] || !same(hooks[i].deps, deps)) {
      const previous = hooks[i]; hooks[i] = { deps }; effects.push(() => { previous?.cleanup?.(); hooks[i].cleanup = fn(); }); } },
    useMemo(fn, deps) { const i = cursor++; if (!hooks[i] || !same(hooks[i].deps, deps)) hooks[i] = { deps, value: fn() }; return hooks[i].value; },
  };
  const jsx = (type, props) => ({ type, props: props || {} });
  const previousWindow = global.window;
  let afterSelect = () => {};
  global.window = { api: {
    getSharedConnection: async () => connection,
    loginSharedServer: async () => { calls.push('login'); connection = { server_url: 'https://fixture.invalid', project_id: first.id, user: { id: 'owner', username: 'owner', administrator: true }, expires_at: 9999999999 }; return connection; },
    selectSharedProject: async id => { calls.push('select-ipc'); connection = { ...connection, project_id: id }; afterSelect(); return connection; },
    disconnectSharedServer: async () => { calls.push('disconnect'); connection = null; },
  } };
  const Panel = load('./SharedProjectPanel.tsx', {
    react: { __esModule: true, default: react, ...react }, 'react/jsx-runtime': { jsx, jsxs: jsx },
    'lucide-react': { Users: 'icon', LogIn: 'icon', LogOut: 'icon' },
    '../../services/api': { api: { project: projectApi }, getProjectContext: () => accepted,
      getApiPersistenceIdentity: () => identity, setSharedApiBase: base => { identity = base ? `shared:${base}` : 'local'; },
      request: async (endpoint, options) => {
        if (endpoint === '/api/accounts/select-project') { calls.push('select-api'); backend = JSON.parse(options.body).project_id === second.id ? second : first; }
        return endpoint === '/api/accounts/me' ? { projects: [first, second].map(p => ({ id: p.id, path: p.project_dir, role: 'owner' })), selected_project_id: backend.id } : { users: [] };
      } },
    '../../stores/useProjectStore': { useProjectStore: facade, saveOpenEdits: projectModule.saveOpenEdits },
    '../../stores/useComputeStore': { resetComputeTransportCache() {} },
    '../../stores/useDatasetStore': { useDatasetStore: dataset }, '../../stores/useAnnotationStore': { useAnnotationStore: annotation },
    '../../services/websocket': { telemetryService: { disconnect() {}, connect: async () => {} } },
  }).SharedProjectPanel;
  const render = () => { cursor = 0; dirty = false; tree = Panel(); const pending = effects; effects = []; pending.forEach(fn => fn()); return tree; };
  const settle = async () => { for (let i = 0; i < 8; i++) { render(); await tick(); } assert.equal(dirty, false, 'effects settle without repeatedly overwriting local edits'); return tree; };
  const field = label => nodes(render()).find(n => n.props['aria-label'] === label);
  const button = label => nodes(render()).find(n => n.type === 'button' && (n.props['aria-label'] === label || text(n) === label));
  const edit = (label, input) => { const f = field(label); assert.ok(f, label); f.props.onChange({ target: { value: input } }); };
  await settle(); calls.length = 0;
  return { store, calls, writes, render, settle, field, button, edit,
    setAfterSelect(fn) { afterSelect = fn; },
    accept(project, newIdentity = identity) { backend = project; identity = newIdentity; accepted = additive ? context(project) : null; store.setState({ project, projectDir: project.project_dir, task: project.task }); },
    setIdentity(next) { identity = next; },
    setContext(next) { accepted = next; },
    close() { hooks.forEach(h => h?.cleanup?.()); global.window = previousWindow; },
  };
}

for (const action of ['select', 'login', 'disconnect', 'create', 'source']) test(`project busy refuses shared ${action}, including programmatic callbacks`, async () => {
  const h = await harness({ connected: action !== 'login' });
  try {
    if (action === 'login') { h.edit('공동 작업 서버 주소', 'https://fixture.invalid'); h.edit('공유 계정 이름', 'owner'); h.edit('공유 계정 비밀번호', 'fixture-password'); }
    if (action === 'create') h.edit('공유 프로젝트 이름', 'Created');
    if (action === 'source') h.edit('서버 데이터 원본 경로', '/new-source');
    h.store.setState({ isProjectBusy: true });
    const control = action === 'select' ? h.field('공동 작업 프로젝트') : h.button({ login: '연결·로그인', disconnect: '공동 작업 서버 연결 해제', create: '만들기', source: '원본 지정' }[action]);
    assert.ok(control); const disabled = control.props.disabled;
    if (action === 'select') control.props.onChange({ target: { value: second.id } }); else control.props.onClick();
    await h.settle();
    assert.deepEqual(h.calls, [], 'busy callback must not send mutations even if invoked directly');
    assert.equal(disabled, true, 'busy control is visibly disabled');
  } finally { h.close(); }
});

test('source draft follows accepted scope and preserves user edits across unrelated renders', async () => {
  const h = await harness();
  try {
    assert.equal(h.field('서버 데이터 원본 경로').props.value, first.source_dataset_dir);
    h.edit('서버 데이터 원본 경로', '/user-edited-source');
    h.store.setState({ projectName: 'Unrelated display update' }); await h.settle();
    assert.equal(h.field('서버 데이터 원본 경로').props.value, '/user-edited-source');
    h.accept(second); await h.settle();
    assert.equal(h.field('서버 데이터 원본 경로').props.value, '', 'connection still names first; do not expose a mismatched draft');
    assert.equal(h.button('원본 지정').props.disabled, true);
  } finally { h.close(); }
});

test('deferred production sync cannot prefill old source and updates draft after acceptance', async () => {
  const h = await harness();
  try {
    h.setAfterSelect(() => h.store.setState({ isProjectBusy: true }));
    h.field('공동 작업 프로젝트').props.onChange({ target: { value: second.id } }); await h.settle();
    assert.equal(h.store.getState().project.id, first.id, 'production sync deferred while project busy');
    assert.equal(h.field('서버 데이터 원본 경로').props.value, '', 'old source must clear during pending project selection');
    assert.equal(h.button('원본 지정').props.disabled, true);
    h.store.setState({ isProjectBusy: false }); await h.store.getState().syncCurrentProject(); await h.settle();
    assert.equal(h.store.getState().project.id, second.id);
    assert.equal(h.field('서버 데이터 원본 경로').props.value, second.source_dataset_dir);
  } finally { h.close(); }
});

for (const mismatch of ['project', 'transport', 'actor', 'workspace']) test(`stale source callback cannot write after ${mismatch} scope changes`, async () => {
  const h = await harness();
  try {
    h.edit('서버 데이터 원본 경로', '/draft-before-change'); const old = h.button('원본 지정');
    if (mismatch === 'project') h.accept(second);
    else if (mismatch === 'transport') h.setIdentity('shared:https://other.invalid');
    else h.setContext({ ...context(first), [mismatch === 'actor' ? 'actor_id' : 'workspace_id']: 'different' });
    old.props.onClick(); await h.settle();
    assert.deepEqual(h.writes, [], 'old callback cannot submit its captured draft under new authority');
  } finally { h.close(); }
});

test('accepted current source updates its project with explicit committed context', async () => {
  const h = await harness();
  try {
    h.edit('서버 데이터 원본 경로', '/current-source');
    assert.equal(h.button('원본 지정').props.disabled, false);
    h.button('원본 지정').props.onClick(); await h.settle();
    assert.deepEqual(h.writes, [{ project_id: first.id, source_dataset_dir: '/current-source' }]);
    assert.equal(h.store.getState().project.source_dataset_dir, '/current-source');
  } finally { h.close(); }
});

test('absent committed team context cannot authorize a source draft by matching IDs alone', async () => {
  const h = await harness({ additive: false });
  try {
    h.edit('서버 데이터 원본 경로', '/unbound-source');
    const submit = h.button('원본 지정');
    submit.props.onClick(); await h.settle();
    assert.deepEqual(h.writes, []);
    assert.equal(submit.props.disabled, true);
  } finally { h.close(); }
});
