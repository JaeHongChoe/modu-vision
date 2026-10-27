const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

const tick = () => new Promise(resolve => setImmediate(resolve));
function load(filename, mocks = {}) {
  const loaded = new Module(filename, module);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  const original = loaded.require.bind(loaded);
  loaded.require = name => Object.hasOwn(mocks, name) ? mocks[name] : original(name);
  loaded._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.React, esModuleInterop: true},
  }).outputText, filename);
  return loaded.exports;
}
function nodes(tree) {
  if (!tree || typeof tree !== 'object') return [];
  return [tree, ...[].concat(tree.props?.children || []).flatMap(nodes)];
}
async function settled(predicate, message) {
  for (let index = 0; index < 30; index++) {
    if (predicate()) return;
    await tick();
  }
  assert.fail(message);
}

// The native bearer injection / remote server and React scheduling boundaries
// are controlled. The component handlers, api.request, context epoch and
// persistence identity below are the actual production modules.
function fixture({administrator = true, loggedIn = true, refused = ''} = {}) {
  const previous = {fetch: global.fetch, window: global.window};
  const client = load(path.resolve(__dirname, '../../services/api.ts'));
  const server = 'https://owned-shared.example.invalid';
  const oldContext = {workspace_id: 'owned-workspace', project_id: 'old-project', actor_id: 'owned-user', mode: 'team'};
  const nextContext = {...oldContext, project_id: 'next-project'};
  const connection = {server_url: server, project_id: oldContext.project_id,
    user: {id: oldContext.actor_id, username: 'owned-user', administrator}, expires_at: 2000000000};
  const calls = [], hostSelections = [], effects = [], cleanups = [], syncCalls = [], resets = [];
  const state = {project: {id: oldContext.project_id, project_dir: '/owned/old-project', source_dataset_dir: '/owned/input'},
    isProjectBusy: false, syncCurrentProject: async () => {
      const selected = hostSelections.at(-1);
      assert.equal(client.getProjectContext(),null,'old accepted namespace must retire before current-project discovery');
      assert.equal(selected, nextContext.project_id);
      syncCalls.push(selected);
      state.project = {...state.project, id: selected, project_dir: '/owned/next-project'};
      client.setProjectContext(nextContext);
    }};
  const store = Object.assign(selector => selector(state), {
    getState: () => state, setState: value => Object.assign(state, value),
  });
  const requestOptions = () => ({credentials: 'omit', headers: {Authorization: 'Bearer controlled-owned-session'}});
  const host = {kind: () => 'desktop', shared: {
    requestOptions, connection: async () => connection,
    login: async () => ({...connection, project_id: undefined}),
    select: async id => {hostSelections.push(id); return {...connection, project_id: id};},
  }};
  const values = [loggedIn ? connection : null, server, 'owned-user', 'controlled-test-password', [],
    loggedIn ? oldContext.project_id : '', [], '', '', '', 'viewer', 'new-owned-project',
    {value: '', scope: null}, false, ''];
  let cursor = 0;
  const react = {createElement: (type, props, ...children) => ({type, props: {...props, children}}),
    useEffect: (callback, dependencies) => effects.push({callback, dependencies}),
    useState: initial => {
      const index = cursor++;
      if (values[index] === undefined) values[index] = initial;
      return [values[index], value => {values[index] = typeof value === 'function' ? value(values[index]) : value;}];
    }};
  global.window = {api: {}};
  client.setSharedApiBase(server, requestOptions);
  client.setProjectContext(oldContext);
  const initialGeneration = client.getProjectContextGeneration();
  const initialIdentity = client.getApiPersistenceIdentity();
  global.fetch = async (url, options) => {
    const endpoint = new URL(url).pathname;
    const call = {endpoint, method: (options.method || 'GET').toUpperCase(),
      headers: new Headers(options.headers), body: options.body, credentials: options.credentials,
      contextAtDispatch: client.getProjectContext(), generationAtDispatch: client.getProjectContextGeneration(),
      hostSelectionsAtDispatch: [...hostSelections], syncCallsAtDispatch: [...syncCalls]};
    calls.push(call);
    if (refused === endpoint) return new Response(JSON.stringify({detail: 'Controlled account refusal'}), {status: 403});
    if (endpoint === '/api/accounts/me') return Response.json({
      projects: [{id: nextContext.project_id, path: '/owned/next-project', role: 'trainer'}],
      selected_project_id: nextContext.project_id,
    });
    if (endpoint === '/api/accounts/users') return Response.json({users: [{id: 'owned-user', username: 'owned-user', administrator}]});
    if (endpoint === '/api/accounts/select-project') return Response.json({selected_project_id: nextContext.project_id});
    if (endpoint === '/api/project/create') return Response.json({id: nextContext.project_id, project_dir: '/owned/next-project'});
    if (endpoint === '/api/compute/reservations') return Response.json({reservations: []});
    assert.fail(`Unexpected request ${call.method} ${endpoint}`);
  };
  const Panel = load(path.resolve(__dirname, 'SharedProjectPanel.tsx'), {
    react: {__esModule: true, default: react, ...react},
    'lucide-react': {Users: 'icon', LogIn: 'icon', LogOut: 'icon'},
    '../../services/api': client,
    '../../services/hostAdapter': {host},
    '../../stores/useProjectStore': {useProjectStore: store, saveOpenEdits: async () => {}},
    '../../stores/useComputeStore': {resetComputeTransportCache: () => resets.push(client.getProjectContext())},
    '../../stores/useDatasetStore': {useDatasetStore: Object.assign(selector => selector({isLoading: false, isSplitting: false}), {getState: () => ({setFolderPath: () => {}})})},
    '../../stores/useAnnotationStore': {useAnnotationStore: {getState: () => ({setReviewerName: () => {}, setImages: async () => {}})}},
    '../../services/websocket': {telemetryService: {disconnect: () => {}, connect: async () => {}}},
  }).SharedProjectPanel;
  const tree = Panel();
  return {client, calls, hostSelections, syncCalls, resets, values, oldContext, nextContext,
    initialGeneration, initialIdentity,
    async restore() {
      const mount = effects.filter(effect => Array.isArray(effect.dependencies) && effect.dependencies.length === 0);
      assert.equal(mount.length, 1, 'exercise the real connection-restoration effect');
      cleanups.push(mount[0].callback());
      await settled(() => values[14] || (calls.filter(call => call.endpoint === '/api/accounts/me').length === 1
        && (!administrator || values[6].length === 1)), 'restored account inventory did not settle');
    },
    async action(kind) {
      const list = nodes(tree);
      if (kind === 'activate') list.find(node => node.props?.['aria-label'] === '공동 작업 프로젝트')
        .props.onChange({target: {value: nextContext.project_id}});
      else list.find(node => node.type === 'button' && node.props.children.includes(kind === 'login' ? '연결·로그인' : '만들기'))
        .props.onClick();
      await settled(() => values[13] === false, `${kind} did not settle`);
    },
    async assertProjectRead(context) {
      await client.request('/api/compute/reservations');
      const call = calls.at(-1);
      assert.equal(call.endpoint, '/api/compute/reservations');
      assert.equal(call.headers.get('X-Vision-Project'), context.project_id);
      assert.deepEqual(JSON.parse(call.headers.get('X-Vision-Context')), context);
      assert.deepEqual(client.getProjectContext(), context);
      assert.equal(client.getApiPersistenceIdentity(), initialIdentity);
    },
    close() {
      for (const cleanup of cleanups) if (typeof cleanup === 'function') cleanup();
      global.fetch = previous.fetch;
      if (previous.window === undefined) delete global.window; else global.window = previous.window;
    },
  };
}
function assertGlobalAccount(call) {
  assert.ok(['/api/accounts/me', '/api/accounts/users', '/api/accounts/select-project'].includes(call.endpoint));
  assert.equal(call.headers.get('Authorization'), 'Bearer controlled-owned-session', 'account authentication survives request scoping');
  assert.equal(call.headers.has('X-Vision-Project'), false, `${call.endpoint} must not inherit the selected project header`);
  assert.equal(call.headers.has('X-Vision-Context'), false, `${call.endpoint} must not inherit the selected context header`);
  assert.equal(call.headers.has('Cookie'), false);
  assert.equal(call.credentials, 'omit');
  if (call.endpoint === '/api/accounts/select-project') {
    assert.equal(call.method, 'POST');
    assert.deepEqual(JSON.parse(call.body), {project_id: 'next-project'});
  } else {
    assert.equal(call.method, 'GET');
    assert.equal(call.body, undefined);
  }
}

for (const administrator of [true, false]) test(`restored ${administrator ? 'administrator' : 'viewer'} account reads are global without retiring the accepted project`, {timeout: 2000}, async () => {
  const f = fixture({administrator});
  try {
    await f.restore();
    assert.equal(f.values[14], '');
    assert.deepEqual(f.calls.map(call => call.endpoint), administrator ? ['/api/accounts/me', '/api/accounts/users'] : ['/api/accounts/me']);
    for (const call of f.calls) {
      assertGlobalAccount(call);
      assert.deepEqual(call.contextAtDispatch, f.oldContext, 'null is request-local, not a global context reset');
      assert.equal(call.generationAtDispatch, f.initialGeneration);
    }
    assert.equal(f.client.getProjectContextGeneration(), f.initialGeneration);
    assert.deepEqual(f.hostSelections, []);
    assert.deepEqual(f.syncCalls, []);
    assert.deepEqual(f.resets, []);
    await f.assertProjectRead(f.oldContext);
  } finally {f.close();}
});

for (const refused of ['', '/api/accounts/select-project']) test(`explicit project selection ${refused ? 'refusal' : 'success'} posts globally and preserves selection ownership`, {timeout: 2000}, async () => {
  const f = fixture({refused});
  try {
    await f.action('activate');
    assert.equal(f.calls.length, 1);
    const call = f.calls[0];
    assertGlobalAccount(call);
    assert.deepEqual(call.contextAtDispatch, f.oldContext);
    assert.equal(call.generationAtDispatch, f.initialGeneration);
    assert.deepEqual(call.hostSelectionsAtDispatch, []);
    assert.deepEqual(call.syncCallsAtDispatch, []);
    if (refused) {
      assert.match(f.values[14], /Controlled account refusal/);
      assert.deepEqual(f.hostSelections, []);
      assert.deepEqual(f.syncCalls, []);
      assert.deepEqual(f.resets, []);
      assert.equal(f.values[5], 'old-project');
      assert.equal(f.client.getProjectContextGeneration(), f.initialGeneration);
      await f.assertProjectRead(f.oldContext);
    } else {
      assert.equal(f.values[14], '');
      assert.deepEqual(f.hostSelections, ['next-project']);
      assert.deepEqual(f.syncCalls, ['next-project']);
      assert.equal(f.values[5], 'next-project');
      assert.equal(f.client.getProjectContextGeneration(), f.initialGeneration + 2);
      await f.assertProjectRead(f.nextContext);
    }
  } finally {f.close();}
});

test('project creation refreshes the account inventory globally before accepting the new project', {timeout: 2000}, async () => {
  const f = fixture();
  try {
    await f.action('create');
    assert.equal(f.values[14], '');
    assert.deepEqual(f.calls.map(call => call.endpoint), ['/api/project/create', '/api/accounts/me', '/api/accounts/users']);
    const create = f.calls[0];
    assert.equal(create.method, 'POST');
    assert.deepEqual(JSON.parse(create.body), {name: 'new-owned-project', task: 'classification'});
    for (const call of f.calls.slice(1)) {
      assertGlobalAccount(call);
      assert.deepEqual(call.contextAtDispatch, f.oldContext);
      assert.equal(call.generationAtDispatch, f.initialGeneration);
      assert.deepEqual(call.hostSelectionsAtDispatch, []);
      assert.deepEqual(call.syncCallsAtDispatch, []);
    }
    assert.deepEqual(f.hostSelections, ['next-project']);
    assert.deepEqual(f.syncCalls, ['next-project']);
    await f.assertProjectRead(f.nextContext);
  } finally {f.close();}
});

for (const refused of ['', '/api/accounts/me']) test(`administrator login ${refused ? 'inventory refusal' : 'success'} keeps account authentication global`, {timeout: 2000}, async () => {
  const f = fixture({loggedIn: false, refused});
  try {
    await f.action('login');
    assert.deepEqual(f.calls.map(call => call.endpoint), refused ? ['/api/accounts/me']
      : ['/api/accounts/me', '/api/accounts/users', '/api/accounts/select-project']);
    for (const call of f.calls) {
      assertGlobalAccount(call);
      assert.equal(call.contextAtDispatch, null, 'login already retires the old context until selection is accepted');
      assert.deepEqual(call.hostSelectionsAtDispatch, []);
      assert.deepEqual(call.syncCallsAtDispatch, []);
    }
    if (refused) {
      assert.match(f.values[14], /Controlled account refusal/);
      assert.deepEqual(f.hostSelections, []);
      assert.deepEqual(f.syncCalls, []);
      assert.equal(f.client.getProjectContext(), null);
      await assert.rejects(f.client.request('/api/compute/reservations'), {name: 'ProjectContextRequiredError'});
      assert.equal(f.calls.length, 1, 'unaccepted login never dispatches a project-scoped read');
    } else {
      assert.equal(f.values[14], '');
      assert.deepEqual(f.hostSelections, ['next-project']);
      assert.deepEqual(f.syncCalls, ['next-project']);
      await f.assertProjectRead(f.nextContext);
    }
  } finally {f.close();}
});
