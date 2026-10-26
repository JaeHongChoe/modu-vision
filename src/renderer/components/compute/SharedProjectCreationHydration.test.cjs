const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

function load(relative, mocks = {}) {
  const filename = path.resolve(__dirname, relative), loaded = new Module(filename, module);
  loaded.filename = filename; loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  const original = loaded.require.bind(loaded);
  loaded.require = name => Object.hasOwn(mocks, name) ? mocks[name] : original(name);
  loaded._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React, esModuleInterop: true,
  }}).outputText, filename);
  return loaded.exports;
}
function nodes(tree) {return !tree || typeof tree !== 'object' ? [] : [tree, ...[].concat(tree.props?.children || []).flatMap(nodes)];}
function stateStore(state) {
  return Object.assign(selector => selector(state), {getState: () => state,
    setState: value => Object.assign(state, typeof value === 'function' ? value(state) : value)});
}

// The handlers, API candidate acceptance, project synchronization, compute store
// and telemetry lifecycle are real. Only React scheduling, remote host/network,
// external WebSocket and unrelated label/flow work are controlled.
async function scenario(outcome = 'success') {
  const previous = {fetch: global.fetch, window: global.window, WebSocket: global.WebSocket,
    localStorage: Object.getOwnPropertyDescriptor(global, 'localStorage')};
  Object.defineProperty(global, 'localStorage', {configurable: true, value: undefined});
  const client = load('../../services/api.ts');
  const compute = load('../../stores/useComputeStore.ts', {'../services/api': client});
  const context = {workspace_id: 'fixture-workspace', project_id: 'first-project', actor_id: 'fixture-user', mode: 'team'};
  const project = {id: 'first-project', name: 'First project', project_dir: '/fixture/first-project',
    task: 'classification', source_dataset_dir: null, active_labelset_id: 'default'};
  const connection = {server_url: 'https://fixture.example.invalid', user: {id: 'fixture-user', username: 'tester', administrator: true}, expires_at: 2000000000};
  const calls = [], refreshes = [], hydration = [], sockets = [], selections = [], held = [];
  const annotation = stateStore({isDirty: false, saveMessage: 'Unsaved fixture labels',
    saveAnnotations: async () => false, setReviewerName() {}, setTask() {}, setImages: async () => {
      if (created && outcome === 'apply_refused') throw new Error('Label view synchronization refused');
      return true;
    }});
  const dataset = stateStore({folderPath: '', hasSelectedFolder: false, isLoading: false, isSplitting: false,
    isGenerating: false, setFolderPath(value) {this.folderPath = value;}});
  const flow = stateStore({pipelineDirty: false, isLoading: false, isSaving: false, isRunning: false, selectedImage: null});
  const projects = load('../../stores/useProjectStore.ts', {
    '../services/api': client,
    '../services/datasetWorkflow': {datasetWorkflow: {}, workflowError: error => error.message},
    './projectViewState': load('../../stores/projectViewState.ts'),
    './useAnnotationStore': {useAnnotationStore: annotation}, './useDatasetStore': {useDatasetStore: dataset},
    './useFlowchartStore': {useFlowchartStore: flow},
    './useTrainingStore': {useTrainingStore: stateStore({isTraining: false})},
    './useInspectionRunStore': {useInspectionRunStore: stateStore({isRunning: false})},
    './useModelAssistRunStore': {useModelAssistRunStore: stateStore({activeOperations: 0})},
  });
  const store = projects.useProjectStore;
  const view = Object.assign(selector => selector(store.getState()), {getState: store.getState, setState: store.setState});
  class Socket {
    static OPEN = 1; static CONNECTING = 0;
    constructor(url) {this.url = url; this.readyState = Socket.CONNECTING; sockets.push(this);
      held.push(['telemetry', store.getState().sharedSessionChanging]);}
    close() {this.readyState = 3; this.onclose?.();}
  }
  global.WebSocket = Socket;
  global.window = {location: {origin: connection.server_url, search: ''}};
  const {telemetryService} = load('../../services/websocket.ts', {'./api': client});
  let created = false, completed;
  global.fetch = async (url, options) => {
    const route = new URL(url).pathname;
    calls.push({route, method: options.method || 'GET', headers: new Headers(options.headers), body: options.body});
    if (route === '/api/accounts/me') {
      if (created && outcome === 'superseded_inventory') {
        client.setProjectContext({...context, actor_id: 'other-user'});
        client.setProjectContext(null); // Same transport/context again, different authority epoch.
      }
      return Response.json({projects: created ? [{id: project.id, path: project.project_dir, role: 'owner'}] : [], selected_project_id: null});
    }
    if (route === '/api/accounts/users') return Response.json({users: [connection.user]});
    if (route === '/api/project/create') {
      created = true;
      return Response.json(project, {headers: {'X-Vision-Context': JSON.stringify(context)}});
    }
    if (route === '/api/project/current') {
      held.push(['sync', store.getState().sharedSessionChanging]);
      if (outcome === 'sync_refused') return Response.json({detail: 'Project synchronization refused'}, {status: 403});
      if (outcome === 'superseded_sync') client.setProjectContext({...context, actor_id: 'other-user'});
      const accepted = outcome === 'wrong_actor' ? {...context, actor_id: 'other-user'} : context;
      return Response.json(project, {headers: outcome === 'missing_context' ? {} : {'X-Vision-Context': JSON.stringify(accepted)}});
    }
    if (route === '/api/project/list') return Response.json({projects: []});
    if (route === '/api/compute/profiles') return outcome === 'compute_refused'
      ? Response.json({detail: 'Current server compute refused'}, {status: 503}) : Response.json({profiles: []});
    if (route === '/api/compute/selection') return Response.json({compute_profile_id: null});
    if (route === '/api/compute/reservations') return Response.json({reservations: []});
    assert.fail(`Unexpected ${options.method || 'GET'} ${route}`);
  };
  const unsubscribe = compute.useComputeStore.subscribe((state, before) => {
    if (state.transportRevision === before.transportRevision) return;
    refreshes.push({context: client.getProjectContext(), project: store.getState().project?.id ?? null});
    held.push(['refresh', store.getState().sharedSessionChanging]);
    hydration.push(state.load().catch(error => error));
    hydration.push(client.request('/api/compute/reservations').catch(error => error));
  });
  const values = [null, connection.server_url, 'tester', 'synthetic-fixture-password', [], '', [], '', '', '',
    'viewer', '', {value: '', scope: null}, false, ''];
  let cursor = 0;
  const react = {createElement: (type, props, ...children) => ({type, props: {...props, children}}),
    useEffect() {}, useState: initial => {
      const index = cursor++; if (values[index] === undefined) values[index] = initial;
      return [values[index], value => {values[index] = typeof value === 'function' ? value(values[index]) : value;
        if (index === 13 && values[index] === false) completed?.();}];
    }};
  const Panel = load('SharedProjectPanel.tsx', {
    react: {__esModule: true, default: react, ...react}, 'lucide-react': {Users: 'icon', LogIn: 'icon', LogOut: 'icon'},
    '../../services/api': client, '../../stores/useComputeStore': compute,
    '../../stores/useProjectStore': {...projects, useProjectStore: view},
    '../../stores/useDatasetStore': {useDatasetStore: dataset}, '../../stores/useAnnotationStore': {useAnnotationStore: annotation},
    '../../services/websocket': {telemetryService}, '../../services/hostAdapter': {host: {
      kind: () => 'browser', can: () => true, shared: {login: async () => connection,
        select: async id => {selections.push(id); return {...connection, project_id: id,
          ...(outcome === 'superseded_selection' ? {user: {...connection.user, id: 'other-user'}} : {})};}},
    }},
  }).SharedProjectPanel;
  async function click(label) {
    const done = new Promise(resolve => {completed = resolve;});
    cursor = 0;
    nodes(Panel()).find(node => node.type === 'button' && node.props.children.includes(label)).props.onClick();
    await done; await Promise.all(hydration);
  }
  try {
    await click('연결·로그인');
    assert.equal(client.getProjectContext(), null, 'a no-project login grants no project authority');
    assert.equal(compute.useComputeStore.getState().isLoaded, false);
    assert.match(compute.useComputeStore.getState().loadError, /공유 프로젝트 선택/);
    assert.equal(calls.some(call => call.route.startsWith('/api/compute/')), false, 'the real store refuses before dispatch');
    assert.equal(sockets.length, 0);
    values[11] = 'First project';
    if (outcome === 'busy') store.setState({isProjectBusy: true});
    if (outcome === 'unsaved') annotation.setState({isDirty: true});
    await click('만들기');
    return {calls, refreshes, sockets, selections, held, values, context, project,
      compute: {...compute.useComputeStore.getState()}, state: {...store.getState()}, authority: client.getProjectContext()};
  } finally {
    unsubscribe(); telemetryService.disconnect(); global.fetch = previous.fetch; global.WebSocket = previous.WebSocket;
    if (previous.window === undefined) delete global.window; else global.window = previous.window;
    if (previous.localStorage) Object.defineProperty(global, 'localStorage', previous.localStorage); else delete global.localStorage;
  }
}

test('first UI-created shared project refreshes the real refused compute load after canonical store acceptance', async () => {
  const value = await scenario();
  assert.equal(value.values[14], '');
  assert.equal(value.compute.loadError, null, 'the no-project refusal cannot survive accepted creation');
  assert.equal(value.compute.isLoaded, true, 'normal local-compute selection is available to training');
  assert.equal(value.compute.selectedProfileId, null);
  assert.deepEqual(value.refreshes, [{context: null, project: null}, {context: value.context, project: 'first-project'}]);
  assert.deepEqual(value.authority, value.context);
  assert.equal(value.state.project.id, 'first-project');
  const reads = value.calls.filter(call => call.route.startsWith('/api/compute/'));
  assert.equal(reads.length, 3);
  for (const read of reads) {
    assert.equal(read.headers.get('X-Vision-Project'), 'first-project');
    assert.deepEqual(JSON.parse(read.headers.get('X-Vision-Context')), value.context);
  }
  assert.equal(value.sockets.length, 1, 'creation explicitly enables telemetry left disconnected by no-project login');
  assert.deepEqual(JSON.parse(new URL(value.sockets[0].url).searchParams.get('project_context')), value.context);
  assert.deepEqual(value.held.filter(([, changing]) => !changing), [], 'sync, refresh and telemetry all hold uploads');
  assert.equal(value.state.sharedSessionChanging, false);
});

for (const outcome of ['sync_refused', 'apply_refused', 'missing_context', 'wrong_actor', 'superseded_inventory', 'superseded_selection', 'superseded_sync']) {
  test(`created project ${outcome} cannot release protected hydration or telemetry`, async () => {
    const value = await scenario(outcome);
    assert.notEqual(value.values[14], '', 'unaccepted creation is reported to the user');
    assert.equal(value.refreshes.length, 1, 'only the original login invalidation occurred');
    assert.equal(value.calls.some(call => call.route.startsWith('/api/compute/')), false);
    assert.equal(value.compute.isLoaded, false);
    assert.equal(value.sockets.length, 0);
    assert.equal(value.state.sharedSessionChanging, false);
    if (outcome === 'superseded_inventory') {
      assert.deepEqual(value.selections, [], 'an expired creation cannot select through a newer host authority');
      assert.equal(value.calls.some(call => call.route === '/api/project/current'), false);
    }
    if (outcome === 'superseded_selection') assert.equal(value.calls.some(call => call.route === '/api/project/current'), false);
    if (outcome === 'sync_refused') assert.match(value.values[14], /Project synchronization refused/);
  });
}

test('accepted creation retains a current server compute refusal instead of enabling training', async () => {
  const value = await scenario('compute_refused');
  assert.equal(value.refreshes.length, 2);
  assert.equal(value.compute.isLoaded, false);
  assert.equal(value.compute.loadError, 'Current server compute refused');
});

for (const outcome of ['busy', 'unsaved']) test(`creation preserves the existing ${outcome} guard`, async () => {
  const value = await scenario(outcome);
  assert.notEqual(value.values[14], '');
  assert.equal(value.calls.some(call => call.route === '/api/project/create'), false);
  assert.equal(value.refreshes.length, 1);
  assert.equal(value.sockets.length, 0);
  assert.equal(value.state.sharedSessionChanging, false);
});
