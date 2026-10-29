const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

function load(relative, dependencies = {}) {
  const file = path.resolve(__dirname, relative), loaded = new Module(file, module);
  loaded.filename = file; loaded.paths = Module._nodeModulePaths(path.dirname(file));
  const original = loaded.require.bind(loaded);
  loaded.require = name => name in dependencies ? dependencies[name] : original(name);
  loaded._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true,
  }}).outputText, file);
  return loaded.exports;
}

// Real BrowserSession, BrowserHost, API context acceptance and compute store;
// only the HTTPS/native process boundary is replaced with deterministic replies.
async function scenario(options, run) {
  const previousWindow = global.window, previousFetch = global.fetch;
  const origin = options.origin || 'https://localhost:8443';
  global.window = {location: {origin, search: ''}};
  let clock = 1000, holdSession = false, releaseSession;
  const context = {workspace_id: 'qa-workspace', project_id: 'qa-project',
    actor_id: options.actor || 'qa-actor', mode: 'team'};
  const calls = [], response = (value, status = 200, headers) => new Response(JSON.stringify(value), {status, headers});
  global.fetch = async (url, request = {}) => {
    const route = new URL(url).pathname; calls.push({route, method: request.method || 'GET'});
    if (route === '/api/accounts/login') return response({expires_at: 2000,
      csrf_token: 'synthetic_csrf_proof_1234567890',
      user: {id: 'qa-actor', username: 'syntheticqa', administrator: options.admin ?? false}});
    if (route === '/api/local-host/capabilities') return response({capabilities: options.native === false ? {} : {projects: true}});
    if (route === '/api/local-host/session') {
      if (holdSession) {holdSession = false; return new Promise(resolve => {releaseSession = () => resolve(response({tab_id: 'qa-tab-after-acceptance'}));});}
      return response({tab_id: 'qa-native-tab'});
    }
    if (route === '/api/local-host/session/dispose') return response({ok: true});
    if (route === '/api/project/current' || route === '/api/local-host/projects/current') return response({id: context.project_id, project_dir: '/synthetic/project'},
      200, {'X-Vision-Context': JSON.stringify(context)});
    if (route === '/api/compute/profiles') return options.admin
      ? response({profiles: [{id: 'admin-profile', name: 'Existing SSH profile'}]})
      : response({detail: 'Server administrator permission required for SSH compute configuration'}, 403);
    if (route === '/api/compute/selection') return options.admin
      ? response({compute_profile_id: 'admin-profile'})
      : response({detail: 'Server administrator permission required for SSH compute configuration'}, 403);
    throw new Error('Unexpected fixture route: ' + route);
  };
  const api = load('../services/api.ts');
  const {BrowserSession} = load('../services/browserSession.ts');
  const boundary = {browserSession: new BrowserSession({origin: () => window.location.origin,
    fetch: (...args) => fetch(...args), now: () => clock}), localBrowserHost: null};
  boundary.setBrowserHost = host => {boundary.localBrowserHost = host;};
  load('../services/browserHost.ts', {'./api': api, './browserSession': boundary});
  const compute = load('useComputeStore.ts', {'../services/api': api, '../services/browserSession': boundary});
  try {
    await boundary.browserSession.login({server_url: origin, username: 'syntheticqa', password: 'synthetic-fixture-only'});
    boundary.browserSession.select(options.project || context.project_id);
    api.setSharedApiBase(origin);
    if (options.accept !== false) {
      const current = await api.api.project.getCurrent(); api.api.project.acceptContext(current);
    }
    await run({api, compute, boundary, calls, context,
      expire: () => {clock = 3000;}, hold: () => {holdSession = true;},
      release: () => {assert.ok(releaseSession, 'native readiness discovery was held'); releaseSession();}});
  } finally {releaseSession?.(); global.fetch = previousFetch; global.window = previousWindow;}
}

for (const admin of [false, 0]) test(`accepted native localhost nonadministrator ${admin} can prepare CPU without SSH administration`,
  async () => scenario({admin}, async ({compute, calls}) => {
    await compute.useComputeStore.getState().load();
    const state = compute.useComputeStore.getState();
    assert.equal(state.isLoaded, true); assert.equal(state.isLoading, false);
    assert.deepEqual(state.profiles, []); assert.equal(state.selectedProfileId, null);
    assert.equal(state.loadError, null); assert.equal(state.error, null);
    assert.deepEqual(calls.filter(row => row.route.startsWith('/api/compute/')), []);
  }));

for (const [name, options] of [
  ['generic shared server', {origin: 'https://shared.example.invalid', native: false}],
  ['missing native projects capability', {native: false}],
  ['different accepted actor', {actor: 'other-actor'}],
  ['different selected project', {project: 'other-project'}],
]) test(`${name} retains the genuine SSH refusal`, async () => scenario(options, async ({compute, calls}) => {
  await assert.rejects(compute.useComputeStore.getState().load(), /Server administrator permission required/);
  assert.equal(compute.useComputeStore.getState().isLoaded, false);
  assert.match(compute.useComputeStore.getState().loadError, /Server administrator permission required/);
  assert.equal(calls.filter(row => row.route.startsWith('/api/compute/')).length, 2);
}));

test('native discovery does not authorize an unaccepted project', async () => scenario({accept: false}, async ({compute, calls}) => {
  await compute.useComputeStore.getState().load();
  assert.equal(compute.useComputeStore.getState().isLoaded, false);
  assert.match(compute.useComputeStore.getState().loadError, /공유 프로젝트 선택/);
  assert.equal(calls.filter(row => row.route.startsWith('/api/compute/')).length, 0);
}));

test('remote authority keeps the original SSH discovery refusal', async () => scenario({}, async ({api, compute, calls}) => {
  api.setRemoteTransport({connection: () => ({transport_url: 'https://remote.example.invalid',
    server_url: 'https://remote.example.invalid', remote_id: 'qa-remote', authority_epoch: 1}),
  assetUrl: value => value, socketUrl: value => value});
  await assert.rejects(compute.useComputeStore.getState().load(), /Server administrator permission required/);
  assert.equal(compute.useComputeStore.getState().isLoaded, false);
  assert.equal(calls.filter(row => row.route.startsWith('/api/compute/')).length, 2);
}));

test('expired browser identity never publishes local readiness', async () => scenario({}, async ({compute, expire}) => {
  expire();
  await assert.rejects(compute.useComputeStore.getState().load(), /다시 로그인/);
  assert.equal(compute.useComputeStore.getState().isLoaded, false);
}));

test('a previously prepared local context loses readiness when its browser identity expires', async () => scenario({}, async ({compute, expire}) => {
  await compute.useComputeStore.getState().load();
  assert.equal(compute.useComputeStore.getState().isLoaded, true);
  expire();
  await assert.rejects(compute.useComputeStore.getState().load(), /다시 로그인/);
  assert.equal(compute.useComputeStore.getState().isLoaded, false);
}));

test('a different current origin cannot reuse local native readiness', async () => scenario({}, async ({compute}) => {
  window.location.origin = 'https://different.example.invalid';
  await assert.rejects(compute.useComputeStore.getState().load(), /Server administrator permission required/);
  assert.equal(compute.useComputeStore.getState().isLoaded, false);
}));

test('local CPU readiness does not authorize changing the administrator SSH selection', async () => scenario({}, async ({compute, calls}) => {
  await compute.useComputeStore.getState().load();
  await assert.rejects(compute.useComputeStore.getState().selectTarget(null), /Server administrator permission required/);
  assert.ok(calls.some(row => row.route === '/api/compute/selection' && row.method === 'PUT'));
  assert.equal(compute.useComputeStore.getState().selectedProfileId, null);
}));

for (const admin of [true, 1]) test(`administrator ${admin} retains saved SSH discovery`,
  async () => scenario({admin}, async ({compute, calls}) => {
    await compute.useComputeStore.getState().load();
    assert.equal(compute.useComputeStore.getState().isLoaded, true);
    assert.equal(compute.useComputeStore.getState().selectedProfileId, 'admin-profile');
    assert.deepEqual(compute.useComputeStore.getState().profiles.map(row => row.id), ['admin-profile']);
    assert.equal(calls.filter(row => row.route.startsWith('/api/compute/')).length, 2);
  }));

test('accepted project changes during native discovery cannot publish old local readiness', async () => scenario({},
  async ({api, compute, context, hold, release, calls}) => {
    hold(); const pending = compute.useComputeStore.getState().load();
    await new Promise(setImmediate);
    api.setProjectContext({...context, actor_id: 'changed-actor'});
    release();
    await assert.rejects(pending, /계정|프로젝트/);
    assert.equal(compute.useComputeStore.getState().isLoaded, false);
    assert.equal(calls.filter(row => row.route.startsWith('/api/compute/')).length, 0);
  }));
