// Fixed tests-first source. Author has not imported/transpiled/executed this file.
// Private review may select an isolated checkout; normal discovery uses this checkout.
// These tests exercise the real store + API serializer; HTTP is a controlled supplier.
// They do not prove backend CPU unwind, cache absence, native closure or GUI behavior.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');
const ROOT = process.env.F064_FRONTEND_SOURCE_ROOT || path.resolve(__dirname, '../../..');
assert.ok(ROOT && path.isAbsolute(ROOT), 'Set the isolated frontend source root explicitly');
function load(relative, mocks = {}) {
  const file = path.join(ROOT, 'src/renderer', relative);
  const m = new Module(file, module); m.filename = file;
  m.paths = Module._nodeModulePaths(path.dirname(file));
  const original = m.require.bind(m);
  m.require = key => Object.hasOwn(mocks, key) ? mocks[key] : original(key);
  m._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, file);
  return m.exports;
}
const ID = '8d72b3b5521745bda473d2a82fcc7078';
const IMAGE = { source: 'file', imagePath: '/workspace/a/dataset/input.png', fileName: 'input.png' };
const GRAPH = { id: 'partial', name: 'Partial', nodes: [
  { id: 'input', position: { x: 0, y: 0 }, data: { node_type: 'input', label: 'Input' } },
  { id: 'stop', position: { x: 100, y: 0 }, data: { node_type: 'output', label: 'Stop' } },
], edges: [{ id: 'edge', source: 'input', target: 'stop' }] };
const CPU = { savedVersionId: null, executionTarget: 'local', device: 'cpu', stopNodeId: 'stop' };
const snapshot = (state = 'prepared', execution_finished = false, patch = {}) => ({
  run_id: ID, project_id: 'a', stop_node_id: 'stop', execution_target: 'local', device: 'cpu',
  state, execution_finished, error_type: null, ...patch,
});
const partial = (patch = {}) => ({ status: 'partial', partial_run_id: ID, stop_node_id: 'stop',
  final_verdict: 'REVIEW', is_ok: false, crops: [], execution_steps: [],
  execution_target: 'local', execution_device: 'cpu', compute_profile_id: null, ...patch });
const cancelled = () => partial({ status: 'cancelled', debug_cache: { status: 'hit' } });
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
async function until(predicate) {
  for (let i = 0; i < 60; i += 1) { if (predicate()) return; await new Promise(resolve => setImmediate(resolve)); }
  assert.fail('Expected original frontend boundary was not reached');
}
function response(value, status = 200) { return { ok: status >= 200 && status < 300, status, statusText: String(status), headers: new Headers(), json: async () => value }; }
function fixture(t, handler) {
  const project = { id: 'a', project_dir: '/workspace/a', active_labelset_id: 'default' };
  const api = load('services/api.ts');
  const store = load('stores/useFlowchartStore.ts', {
    '../services/api': api, '../services/flowDraft': { flowDraft: {} },
    './useProjectStore': { useProjectStore: { getState: () => ({ project }) } },
    '../components/flowchart/flowchartStartup': { getFlowchartModelTask: () => null },
  }).useFlowchartStore;
  store.setState({ pipeline: structuredClone(GRAPH), selectedImage: { ...IMAGE } });
  const calls = [], original = global.fetch;
  global.fetch = async (url, options = {}) => {
    const row = { pathname: new URL(url).pathname, method: options.method || 'GET',
      body: options.body ? JSON.parse(options.body) : null, signal: options.signal };
    calls.push(row); return handler(row, calls);
  };
  t.after(() => { global.fetch = original; });
  return { store, calls, project };
}
const at = (calls, suffix) => calls.filter(call => call.pathname.endsWith(suffix));
const ready = store => {
  assert.equal(typeof store.getState().cancelPartialRun, 'function', 'Expose the owning partial stop action');
  assert.equal(typeof store.getState().refreshPartialRun, 'function', 'Expose read-only uncertain-status recovery');
};

test('explicit CPU partial prepares once, runs the exact same body plus server id, and adopts only its finished partial', async t => {
  const { store, calls } = fixture(t, row => {
    if (row.pathname.endsWith('/prepare')) return response(snapshot());
    if (row.pathname.endsWith('/run')) return response(partial());
    return response(snapshot('completed', true));
  });
  assert.equal(await store.getState().runPipeline(undefined, undefined, CPU), true);
  assert.deepEqual(calls.map(r => [r.method, r.pathname]), [
    ['POST', '/api/flowchart/partial-runs/prepare'], ['POST', '/api/flowchart/run'], ['GET', `/api/flowchart/partial-runs/${ID}`],
  ]);
  const expected = { image_path: IMAGE.imagePath, image_id: null, pipeline: GRAPH, project_id: 'a',
    stop_node_id: 'stop', execution_target: 'local', device: 'cpu' };
  assert.deepEqual(calls[0].body, expected);
  assert.deepEqual(calls[1].body, { ...expected, partial_run_id: ID });
  assert.equal(store.getState().executionResult.status, 'partial');
  assert.equal(store.getState().partialRun.phase, 'completed');
  assert.equal(store.getState().isRunning, false);
});

for (const choice of [
  { savedVersionId: null, executionTarget: 'local', device: 'cpu' },
  { savedVersionId: null, executionTarget: 'selected_compute', device: 'cuda', computeProfileId: 'gpu2' },
  { ...CPU, executionTarget: 'selected_compute', device: 'cuda', computeProfileId: 'gpu2' },
  { ...CPU, device: 'mps' },
]) test(`full or non-CPU path stays one original Run: ${JSON.stringify(choice)}`, async t => {
  const result = partial({ ...(choice.stopNodeId ? {} : { status: 'completed', stop_node_id: null }),
    execution_target: choice.executionTarget, execution_device: choice.device === 'cuda' ? 'cuda:0' : choice.device,
    compute_profile_id: choice.computeProfileId || null });
  delete result.partial_run_id;
  const { store, calls } = fixture(t, () => response(result));
  assert.equal(await store.getState().runPipeline(undefined, undefined, choice), true);
  assert.equal(calls.length, 1); assert.equal(calls[0].pathname, '/api/flowchart/run');
  assert.equal(Object.hasOwn(calls[0].body, 'partial_run_id'), false);
  assert.equal(calls[0].body.execution_target, choice.executionTarget);
  assert.equal(calls[0].body.device, choice.device);
  assert.equal(calls[0].body.compute_profile_id, choice.computeProfileId);
});

for (const patch of [ { run_id: '' }, { project_id: 'foreign' }, { stop_node_id: 'foreign' },
  { execution_target: 'selected_compute' }, { device: 'cuda' }, { state: 'completed', execution_finished: true },
]) test(`refused/mismatched preparation never dispatches Run: ${JSON.stringify(patch)}`, async t => {
  const { store, calls } = fixture(t, () => response(snapshot('prepared', false, patch)));
  assert.equal(await store.getState().runPipeline(undefined, undefined, CPU), false);
  assert.equal(at(calls, '/run').length, 0);
  assert.equal(store.getState().executionResult, null);
  assert.ok(store.getState().errorMessage);
});

test('duplicate activation during prepare emits one prepare and one run', async t => {
  const prepare = deferred();
  const { store, calls } = fixture(t, row => row.pathname.endsWith('/prepare') ? prepare.promise
    : response(row.pathname.endsWith('/run') ? partial() : snapshot('completed', true)));
  const first = store.getState().runPipeline(undefined, undefined, CPU);
  await until(() => at(calls, '/prepare').length === 1);
  assert.equal(await store.getState().runPipeline(undefined, undefined, CPU), false);
  prepare.resolve(response(snapshot())); assert.equal(await first, true);
  assert.equal(at(calls, '/prepare').length, 1); assert.equal(at(calls, '/run').length, 1);
});

test('stop intent during prepare waits for issued id then cancels it without starting Run', async t => {
  const prepare = deferred();
  const { store, calls } = fixture(t, row => row.pathname.endsWith('/prepare') ? prepare.promise : response(snapshot('cancelled', true)));
  ready(store); const run = store.getState().runPipeline(undefined, undefined, CPU);
  await until(() => at(calls, '/prepare').length === 1);
  const stop = store.getState().cancelPartialRun();
  assert.equal(store.getState().isRunning, true);
  prepare.resolve(response(snapshot())); await Promise.all([run, stop]);
  assert.equal(at(calls, '/run').length, 0); assert.equal(at(calls, '/cancel').length, 1);
  assert.equal(at(calls, '/cancel')[0].pathname, `/api/flowchart/partial-runs/${ID}/cancel`);
  assert.equal(store.getState().partialRun.phase, 'cancelled');
  assert.equal(store.getState().executionResult, null); assert.equal(store.getState().isRunning, false);
});

test('cancel ack never ends running state; original Run and matching finished terminal must both settle', async t => {
  const running = deferred(), terminal = deferred();
  const { store, calls } = fixture(t, row => {
    if (row.pathname.endsWith('/prepare')) return response(snapshot());
    if (row.pathname.endsWith('/run')) return running.promise;
    if (row.pathname.endsWith('/cancel')) return response(snapshot('cancelling', false));
    return terminal.promise;
  });
  ready(store); const run = store.getState().runPipeline(undefined, undefined, CPU);
  await until(() => at(calls, '/run').length === 1); const stop = store.getState().cancelPartialRun();
  await until(() => at(calls, '/cancel').length === 1);
  assert.equal(store.getState().isRunning, true); assert.equal(store.getState().executionResult, null);
  const duplicate = store.getState().cancelPartialRun(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(at(calls, '/cancel').length, 1);
  running.resolve(response(cancelled())); await until(() => calls.some(r => r.method === 'GET'));
  assert.equal(store.getState().isRunning, true, 'Unfinished terminal read still holds UI busy');
  terminal.resolve(response(snapshot('cancelled', true))); await Promise.all([run, stop, duplicate]);
  assert.equal(store.getState().partialRun.phase, 'cancelled'); assert.equal(store.getState().isRunning, false);
  assert.equal(store.getState().executionResult, null); assert.equal(store.getState().executionIdentity, null);
  assert.equal(store.getState().lastRunSource, null); assert.equal(store.getState().inspectedCrop, null);
  assert.ok(calls.every(r => !r.signal), 'Never substitute transport abort for cooperative stop');
});

test('completion that won the server race stays completed, never becomes cancelled after 409', async t => {
  const running = deferred();
  const { store, calls } = fixture(t, row => {
    if (row.pathname.endsWith('/prepare')) return response(snapshot());
    if (row.pathname.endsWith('/run')) return running.promise;
    if (row.pathname.endsWith('/cancel')) return response({ detail: 'Partial run already has a terminal outcome' }, 409);
    return response(snapshot('completed', true));
  });
  ready(store); const run = store.getState().runPipeline(undefined, undefined, CPU);
  await until(() => at(calls, '/run').length === 1); const stop = store.getState().cancelPartialRun();
  running.resolve(response(partial())); await Promise.all([run, stop]);
  assert.equal(store.getState().partialRun.phase, 'completed');
  assert.equal(store.getState().executionResult.status, 'partial');
});

test('original Run fault concurrent with stop remains failed and never adopts cancelled/completed output', async t => {
  const running = deferred();
  const { store, calls } = fixture(t, row => {
    if (row.pathname.endsWith('/prepare')) return response(snapshot());
    if (row.pathname.endsWith('/run')) return running.promise;
    if (row.pathname.endsWith('/cancel')) return response(snapshot('cancelling', false));
    return response(snapshot('failed', true, { error_type: 'ValueError' }));
  });
  ready(store); const run = store.getState().runPipeline(undefined, undefined, CPU);
  await until(() => at(calls, '/run').length === 1); const stop = store.getState().cancelPartialRun();
  running.resolve(response({ detail: 'Original engine failure' }, 500)); await Promise.all([run, stop]);
  assert.equal(store.getState().partialRun.phase, 'failed'); assert.equal(store.getState().executionResult, null);
  assert.ok(store.getState().errorMessage); assert.equal(store.getState().isRunning, false);
});

test('transport/terminal uncertainty is not stopped; explicit status retry is GET only', async t => {
  const running = deferred(); let connected = false;
  const { store, calls } = fixture(t, row => {
    if (row.pathname.endsWith('/prepare')) return response(snapshot());
    if (row.pathname.endsWith('/run')) return running.promise;
    if (row.pathname.endsWith('/cancel')) return response(snapshot('cancelling', false));
    if (!connected) throw new Error('Disconnected');
    return response(snapshot('cancelled', true));
  });
  ready(store); const run = store.getState().runPipeline(undefined, undefined, CPU);
  await until(() => at(calls, '/run').length === 1); const stop = store.getState().cancelPartialRun();
  running.reject(new Error('Transport lost')); await Promise.all([run, stop]);
  assert.equal(store.getState().partialRun.phase, 'unknown'); assert.equal(store.getState().isRunning, true);
  assert.equal(store.getState().executionResult, null); const count = calls.length;
  connected = true; await store.getState().refreshPartialRun();
  assert.ok(calls.slice(count).every(r => r.method === 'GET'));
  assert.equal(at(calls, '/cancel').length, 1); assert.equal(at(calls, '/run').length, 1);
  assert.equal(store.getState().partialRun.phase, 'cancelled'); assert.equal(store.getState().isRunning, false);
});

test('image changed during original Run keeps new input and never adopts the older partial', async t => {
  const running = deferred();
  const { store, calls } = fixture(t, row => row.pathname.endsWith('/prepare') ? response(snapshot())
    : row.pathname.endsWith('/run') ? running.promise : response(snapshot('completed', true)));
  const run = store.getState().runPipeline(undefined, undefined, CPU); await until(() => at(calls, '/run').length === 1);
  const next = { ...IMAGE, imagePath: '/workspace/a/dataset/new.png' }; store.getState().setSelectedImage(next);
  running.resolve(response(partial())); assert.equal(await run, false);
  assert.equal(store.getState().selectedImage.imagePath, next.imagePath); assert.equal(store.getState().executionResult, null);
  assert.equal(at(calls, '/cancel').length, 0, 'Input revision is not an automatic server stop');
});

test('project changed during prepare never dispatches old capability under the new project', async t => {
  const prepare = deferred(); const { store, calls, project } = fixture(t, () => prepare.promise);
  const run = store.getState().runPipeline(undefined, undefined, CPU); await until(() => at(calls, '/prepare').length === 1);
  project.id = 'b'; prepare.resolve(response(snapshot())); assert.equal(await run, false);
  assert.equal(calls.length, 1); assert.equal(store.getState().executionResult, null);
});

test('foreign terminal cannot supply completion authority or result adoption', async t => {
  const { store } = fixture(t, row => row.pathname.endsWith('/prepare') ? response(snapshot())
    : row.pathname.endsWith('/run') ? response(partial()) : response(snapshot('completed', true, { run_id: 'foreign' })));
  assert.equal(await store.getState().runPipeline(undefined, undefined, CPU), false);
  assert.equal(store.getState().partialRun.phase, 'unknown'); assert.equal(store.getState().executionResult, null);
  assert.equal(store.getState().isRunning, true);
});
