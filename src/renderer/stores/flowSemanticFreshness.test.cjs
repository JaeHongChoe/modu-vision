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


// Separate additive regression: the original fixed20 file is never rewritten.
function semanticFixture(t, handler) {
  const result = fixture(t, handler);
  const graph = structuredClone(GRAPH);
  graph.nodes.find(n => n.id === 'stop').data = {
    node_type: 'inspection', label: 'Inspection stop', task: 'classification',
    model_job_id: 'isolated-frontend-transport-control', threshold: 0.5,
  };
  result.store.setState({ pipeline: graph });
  return result;
}
function editRealNode(store) {
  const before = store.getState();
  const revision = before.flowIdentity.semantic_revision;
  store.getState().updateNodeData('stop', { threshold: 0.75 });
  const after = store.getState();
  assert.notEqual(after.pipeline, before.pipeline);
  assert.equal(after.pipeline.nodes.find(n => n.id === 'stop').data.threshold, 0.75);
  assert.equal(after.flowIdentity.semantic_revision, revision + 1, 'Use real semantic updateNodeData, not input invalidation');
  const file = path.join(ROOT, 'src/renderer/stores/useFlowchartStore.ts');
  const source = fs.readFileSync(file, 'utf8');
  const body = source.slice(source.indexOf('  updateNodeData: (id, patch) => {'), source.indexOf('  addNode: (newNode) => {'));
  assert.ok(body.includes('flowIdentity: editedIdentity(state, next)'));
  assert.doesNotMatch(body, /\bflowchartGeneration\b|\bflowchartRunInputRevision\b/,
    'The original semantic edit does not write the old run-generation/input counters');
}

test('real semantic edit during prepare never dispatches the old graph Run or adopts its later response', async t => {
  const preparing = deferred(), later = deferred();
  const { store, calls } = semanticFixture(t, row => {
    if (row.pathname.endsWith('/prepare')) return preparing.promise;
    if (row.pathname.endsWith('/run')) return later.promise;
    return response(snapshot('completed', true));
  });
  const run = store.getState().runPipeline(undefined, undefined, CPU);
  await until(() => at(calls, '/prepare').length === 1);
  const originalPreparedGraph = structuredClone(at(calls, '/prepare')[0].body.pipeline);
  editRealNode(store);
  assert.equal(originalPreparedGraph.nodes.find(n => n.id === 'stop').data.threshold, 0.5);
  preparing.resolve(response(snapshot()));
  // Release a baseline's erroneous Run too: a semantic RED must not hang the harness.
  later.resolve(response(partial()));
  const completed = await run;
  assert.equal(at(calls, '/run').length, 0, 'Old prepared graph must not be dispatched after real semantic editing');
  assert.equal(completed, false);
  assert.equal(store.getState().executionResult, null);
  assert.equal(store.getState().lastRunSource, null);
  assert.equal(at(calls, '/cancel').length, 0, 'No automatic extra mutation cleans up this scope');
  assert.equal(store.getState().pipeline.nodes.find(n => n.id === 'stop').data.threshold, 0.75);
});

test('real semantic edit after original Run starts refuses its completed late result adoption', async t => {
  const later = deferred();
  const { store, calls } = semanticFixture(t, row => row.pathname.endsWith('/prepare') ? response(snapshot())
    : row.pathname.endsWith('/run') ? later.promise : response(snapshot('completed', true)));
  const run = store.getState().runPipeline(undefined, undefined, CPU);
  await until(() => at(calls, '/run').length === 1);
  editRealNode(store);
  later.resolve(response(partial()));
  assert.equal(await run, false);
  assert.equal(at(calls, '/run').length, 1); assert.equal(at(calls, '/cancel').length, 0);
  assert.equal(store.getState().executionResult, null); assert.equal(store.getState().executionIdentity, null);
  assert.equal(store.getState().lastRunSource, null);
  assert.equal(store.getState().pipeline.nodes.find(n => n.id === 'stop').data.threshold, 0.75);
});
