const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

function load(relative, replacements = {}) {
  const file = path.resolve(__dirname, relative), loaded = new Module(file, module);
  loaded.filename = file; loaded.paths = Module._nodeModulePaths(path.dirname(file));
  const original = loaded.require.bind(loaded);
  loaded.require = name => name in replacements ? replacements[name] : original(name);
  loaded._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true,
  }}).outputText, file);
  return loaded.exports;
}
const context = actor => ({workspace_id: 'owned-workspace', project_id: 'owned-project', actor_id: actor, mode: 'team'});
const origin = 'https://owned.example.invalid';

async function runtime(run) {
  const previousFetch = global.fetch, calls = [];
  const api = load('../services/api.ts');
  const compute = load('useComputeStore.ts', {'../services/api': api});
  let active = context('actor-a'), profile = 'profile-a', failure = false;
  const response = (value, status = 200, headers) => new Response(JSON.stringify(value), {status, headers});
  global.fetch = async (url, options) => {
    const row = {path: new URL(url).pathname, headers: new Headers(options.headers)};
    calls.push(row);
    if (row.path === '/api/project/current') return response({id: active.project_id}, 200, {'X-Vision-Context': JSON.stringify(active)});
    if (row.path === '/api/compute/profiles') return response(failure ? {detail: 'Discovery refused'} : {profiles: [{id: profile, name: profile}]}, failure ? 503 : 200);
    if (row.path === '/api/compute/selection') return response({compute_profile_id: profile});
    throw new Error('Unexpected request path');
  };
  const accept = async actor => {
    active = context(actor);
    const project = await api.api.project.getCurrent();
    api.api.project.acceptContext(project);
  };
  try {await run({api, compute, calls, accept, setProfile: value => {profile = value;}, setFailure: () => {failure = true;}});}
  finally {global.fetch = previousFetch;}
}

test('actual compute load on immediate reset waits for accepted shared context, then the accepted reset refreshes', async () => runtime(async ({api, compute, calls, accept}) => {
  api.setSharedApiBase(origin);
  const loads = [];
  const unsubscribe = compute.useComputeStore.subscribe((current, previous) => {
    if (current.transportRevision !== previous.transportRevision) loads.push(current.load());
  });
  try {
    compute.resetComputeTransportCache(); await Promise.all(loads);
    assert.equal(calls.length, 0, 'automatic discovery must not reach the previous server-side selection');
    assert.equal(compute.useComputeStore.getState().isLoaded, false);
    assert.equal(compute.useComputeStore.getState().isLoading, false);
    assert.match(compute.useComputeStore.getState().loadError, /공유 프로젝트 선택/);
    await accept('actor-a');
    compute.resetComputeTransportCache(); await Promise.all(loads);
    const reads = calls.filter(row => row.path.startsWith('/api/compute/'));
    assert.equal(reads.length, 2);
    for (const row of reads) assert.deepEqual(JSON.parse(row.headers.get('X-Vision-Context')), context('actor-a'));
    assert.equal(compute.useComputeStore.getState().selectedProfileId, 'profile-a');
    assert.equal(compute.useComputeStore.getState().isLoaded, true);
    assert.equal(compute.useComputeStore.getState().loadError, null);
  } finally {unsubscribe();}
}));

test('shared no-project automatic load remains unloaded with explicit readiness and no dispatch', async () => runtime(async ({api, compute, calls}) => {
  api.setSharedApiBase(origin);
  await compute.useComputeStore.getState().load();
  await compute.useComputeStore.getState().load();
  assert.equal(calls.length, 0);
  const state = compute.useComputeStore.getState();
  assert.equal(state.isLoaded, false); assert.equal(state.isLoading, false);
  assert.deepEqual(state.profiles, []); assert.equal(state.selectedProfileId, null);
  assert.match(state.loadError, /공유 프로젝트 선택/);
}));

test('same-origin new-actor login invalidation cannot load old discovery and the new accepted context refreshes', async () => runtime(async ({api, compute, calls, accept, setProfile}) => {
  api.setSharedApiBase(origin); await accept('actor-a');
  await compute.useComputeStore.getState().load();
  const before = calls.length;
  api.setProjectContext(null); compute.resetComputeTransportCache();
  await compute.useComputeStore.getState().load();
  assert.equal(calls.length, before);
  assert.deepEqual(compute.useComputeStore.getState().profiles, []);
  setProfile('profile-b'); await accept('actor-b'); compute.resetComputeTransportCache();
  await compute.useComputeStore.getState().load();
  const reads = calls.slice(before).filter(row => row.path.startsWith('/api/compute/'));
  assert.equal(reads.length, 2);
  for (const row of reads) assert.deepEqual(JSON.parse(row.headers.get('X-Vision-Context')), context('actor-b'));
  assert.equal(compute.useComputeStore.getState().selectedProfileId, 'profile-b');
}));

test('local automatic discovery still loads without a project context', async () => runtime(async ({api, compute, calls}) => {
  assert.equal(api.getApiPersistenceIdentity(), 'local');
  await compute.useComputeStore.getState().load();
  assert.equal(calls.length, 2);
  assert.equal(compute.useComputeStore.getState().isLoaded, true);
  assert.ok(calls.every(row => !row.headers.has('X-Vision-Context')));
}));

test('direct shared administrator discovery API remains available before project publication', async () => runtime(async ({api, calls}) => {
  api.setSharedApiBase(origin);
  const [profiles, selection] = await Promise.all([api.api.compute.listProfiles(), api.api.compute.getSelection()]);
  assert.equal(calls.length, 2);
  assert.equal(profiles.profiles[0].id, 'profile-a'); assert.equal(selection.compute_profile_id, 'profile-a');
  assert.ok(calls.every(row => !row.headers.has('X-Vision-Context')));
}));

test('an accepted shared discovery failure retains the genuine error', async () => runtime(async ({api, compute, accept, setFailure}) => {
  api.setSharedApiBase(origin); await accept('actor-a'); setFailure();
  await assert.rejects(compute.useComputeStore.getState().load(), /Discovery refused/);
  assert.equal(compute.useComputeStore.getState().isLoaded, false);
  assert.match(compute.useComputeStore.getState().loadError, /Discovery refused/);
}));
