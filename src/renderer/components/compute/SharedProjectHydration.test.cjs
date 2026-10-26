const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const tick = () => new Promise(setImmediate);

function load(relative, mocks = {}) {
  const file = path.resolve(__dirname, relative), m = new Module(file, module);
  m.filename = file; m.paths = Module._nodeModulePaths(path.dirname(file));
  const original = m.require.bind(m); m.require = name => name in mocks ? mocks[name] : original(name);
  m._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React, esModuleInterop: true,
  }}).outputText, file);
  return m.exports;
}
function nodes(tree) {return !tree || typeof tree !== 'object' ? [] : [tree, ...[].concat(tree.props?.children || []).flatMap(nodes)];}
const routes = ['/api/compute/reservations', '/api/compute/targets', '/api/compute/jobs',
  '/api/compute/inputs?task=classification', '/api/dataset/revisions'];

// Real panel handler, API context/candidate handling and compute invalidation.
// Only network/desktop host and unrelated annotation work are controlled. The
// reset subscriber models the original consumers' immediate hydration reads.
async function scenario(outcome) {
  const previousFetch = global.fetch;
  const api = load('../../services/api.ts');
  const compute = load('../../stores/useComputeStore.ts', {'../services/api': api});
  const context = {workspace_id: 'new-workspace', project_id: 'owned-project', actor_id: 'new-user', mode: 'team'};
  const project = {id: context.project_id, project_dir: '/owned/project', name: 'Owned', task: 'segmentation'};
  const connection = {server_url: 'https://new.example.invalid', user: {id: context.actor_id, username: 'tester', administrator: false}, expires_at: 2000000000};
  const sameOrigin = outcome.startsWith('same_origin');
  const sameProject = outcome === 'same_project' || sameOrigin;
  const state = {project: sameProject ? {...project} : null, projectDir: sameProject ? project.project_dir : null, isProjectBusy: false, projectError: null};
  const calls = [], resets = [], hydrating = [];
  let selectedCalls = 0, releaseLogin;
  const login = new Promise(resolve => {releaseLogin = () => resolve(connection);});
  api.setSharedApiBase(sameOrigin ? connection.server_url : 'https://old.example.invalid');
  api.setProjectContext(outcome === 'same_origin_sync_unaccepted' ? context
    : {...context, workspace_id: sameOrigin ? context.workspace_id : 'old-workspace', actor_id: 'old-user'});
  global.fetch = async (url, options) => {
    const route = new URL(url).pathname, headers = new Headers(options.headers);
    calls.push({route, method: options.method || 'GET', headers});
    if (route === '/api/accounts/me') {
      if (outcome === 'inventory_failure') throw new Error('Inventory refused');
      if (outcome === 'superseded') {
        api.setSharedApiBase('https://other.example.invalid');
        api.setSharedApiBase(connection.server_url); // A→B→A must still retire the first login.
      }
      return new Response(JSON.stringify({projects: outcome === 'no_project' ? [] : [{id: project.id, path: project.project_dir, role: 'owner'}], selected_project_id: outcome === 'no_project' ? null : project.id}), {status: 200});
    }
    if (route === '/api/project/current') return new Response(JSON.stringify(project), {status: 200, headers: {'X-Vision-Context': JSON.stringify(context)}});
    return new Response('{}', {status: 200});
  };
  state.syncCurrentProject = async () => {
    if (outcome === 'sync_unaccepted' || outcome === 'same_origin_sync_unaccepted') {state.projectError = 'Project synchronization refused'; return;}
    const candidate = await api.api.project.getCurrent();
    api.api.project.acceptContext(candidate, () => Object.assign(state, {project: candidate, projectDir: candidate.project_dir}));
    if (outcome === 'context_superseded') api.setProjectContext({...context, actor_id: 'other-user'});
  };
  const store = Object.assign(selector => selector(state), {getState: () => state, setState: value => Object.assign(state, value)});
  const unsubscribe = compute.useComputeStore.subscribe((current, previous) => {
    if (current.transportRevision === previous.transportRevision) return;
    resets.push({context: api.getProjectContext(), project: state.project?.id});
    for (const route of routes) hydrating.push(api.request(route).then(() => null, error => error));
  });
  let completed;
  const done = new Promise(resolve => {completed = resolve;});
  const values = [null, connection.server_url, 'tester', 'fixture-password-123', [], '', [], '', '', '', 'viewer', '', {value: '', scope: null}, false, ''];
  let cursor = 0;
  const react = {createElement: (type, props, ...children) => ({type, props: {...props, children}}), useEffect: () => {}, useState: initial => {
    const index = cursor++; if (values[index] === undefined) values[index] = initial;
    return [values[index], value => {values[index] = typeof value === 'function' ? value(values[index]) : value; if (index === 13 && value === false) completed();}];
  }};
  try {
    const panel = load('SharedProjectPanel.tsx', {
      react: {__esModule: true, default: react, ...react}, 'lucide-react': {Users: 'icon', LogIn: 'icon', LogOut: 'icon'},
      '../../services/api': api, '../../stores/useComputeStore': compute,
      '../../stores/useProjectStore': {useProjectStore: store, saveOpenEdits: async () => {}},
      '../../stores/useDatasetStore': {useDatasetStore: Object.assign(selector => selector({isLoading: false, isSplitting: false}), {getState: () => ({setFolderPath() {}})})},
      '../../stores/useAnnotationStore': {useAnnotationStore: {getState: () => ({setReviewerName() {}, setImages: async () => {}})}},
      '../../services/websocket': {telemetryService: {disconnect() {}, connect: async () => {}}},
      '../../services/hostAdapter': {host: {kind: () => 'desktop', shared: {login: () => login, select: async id => {selectedCalls++; return {...connection, project_id: id};}}}},
    }).SharedProjectPanel;
    nodes(panel()).find(n => n.type === 'button' && n.props.children.includes('연결·로그인')).props.onClick();
    await tick(); assert.equal(resets.length, 0, 'old targets are not retired until authentication succeeds');
    releaseLogin(); await done; await Promise.all(hydrating);
    return {calls, resets, values, selectedCalls, context, project};
  } finally {unsubscribe(); global.fetch = previousFetch;}
}

for (const outcome of ['success', 'same_project', 'same_origin_session']) test(`shared login ${outcome} hydrates only after canonical context and matching project are published`, async () => {
  const value = await scenario(outcome), protectedCalls = value.calls.filter(row => routes.some(route => route.split('?')[0] === row.route));
  assert.equal(value.resets.length, 2, 'immediate invalidation plus one accepted-project refresh');
  assert.equal(value.resets[0].context, null);
  assert.deepEqual(value.resets[1], {context: value.context, project: value.project.id});
  assert.equal(protectedCalls.length, 5);
  for (const row of protectedCalls) {
    assert.equal(row.headers.get('X-Vision-Project'), value.project.id);
    assert.deepEqual(JSON.parse(row.headers.get('X-Vision-Context')), value.context);
  }
  assert.equal(value.values[14], '');
});

for (const outcome of ['inventory_failure', 'sync_unaccepted', 'same_origin_sync_unaccepted', 'context_superseded', 'superseded', 'no_project']) test(`shared login ${outcome} never releases unaccepted hydration`, async () => {
  const value = await scenario(outcome);
  assert.equal(value.resets.length, 1, 'failed, missing or superseded selection cannot publish the second refresh');
  assert.equal(value.calls.filter(row => routes.some(route => route.split('?')[0] === row.route)).length, 0);
  if (outcome === 'superseded') {
    assert.deepEqual(value.values[4], [], 'a superseded account inventory must not replace the visible project list');
    assert.equal(value.selectedCalls, 0, 'a superseded inventory cannot select a project through a newer host session');
    assert.equal(value.calls.some(row => row.route === '/api/accounts/select-project'), false);
  }
  if (outcome !== 'no_project') assert.notEqual(value.values[14], '');
});
