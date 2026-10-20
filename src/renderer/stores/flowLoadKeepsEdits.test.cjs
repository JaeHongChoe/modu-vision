// A flow read that began before the user edited the graph must not replace the edited, unsaved graph when it lands.
// Step 5 can start a second read right after the first one shows the graph; a click in between used to be lost.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');

function load(relative, dependencies = {}) {
  const filename = path.resolve(__dirname, relative);
  const loaded = new Module(filename, module);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  const original = loaded.require.bind(loaded);
  loaded.require = name => Object.hasOwn(dependencies, name) ? dependencies[name] : original(name);
  loaded._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, filename);
  return loaded.exports;
}

const HASH = 'c'.repeat(64);
const PROJECT = { id: 'p', project_dir: '/workspace/p', source_dataset_dir: '/original/source', active_labelset_id: 'default' };
const CONTEXT = { project_id: PROJECT.id, source_dataset_path: '/original/source', labelset_id: 'default' };
const AUTH = { workspace_id: 'w', project_id: PROJECT.id, actor_id: 'a', mode: 'team' };
const graph = label => ({ id: 'flow', name: 'Flow', edges: [], nodes: [
  { id: 'judge', type: 'custom', position: { x: 0, y: 0 }, data: { node_type: 'decision', label, rule: 'any_defect_is_ng' } }] });

function fixture(t) {
  const api = load('../services/api.ts');
  const service = load('../services/flowDraft.ts', { './api': api });
  const store = load('./useFlowchartStore.ts', {
    '../services/api': api, '../services/flowDraft': service,
    './useProjectStore': { useProjectStore: { getState: () => ({ project: PROJECT }) } },
    '../components/flowchart/flowchartStartup': { getFlowchartModelTask: () => null },
  }).useFlowchartStore;
  api.setSharedApiBase('https://owned.invalid', () => ({ credentials: 'include' }));
  api.setProjectContext(AUTH);
  const replies = [], previous = global.fetch;
  global.fetch = async (url, options = {}) => {
    assert.equal(new URL(url).pathname, '/api/flowchart/draft');
    assert.equal(options.method || 'GET', 'GET');
    const body = await replies.shift();
    return { ok: true, status: 200, headers: new Headers({ 'X-Vision-Context': JSON.stringify(AUTH) }),
      json: async () => ({ version: 1, saved_at_ns: 1, context: CONTEXT, draft_sha256: HASH, active_version_id: null, pipeline: body }) };
  };
  t.after(() => { global.fetch = previous; });
  const later = () => { let resolve; const promise = new Promise(done => { resolve = done; }); replies.push(promise); return resolve; };
  const now = body => replies.push(Promise.resolve(body));
  return { store, later, now };
}

for (const [name, read] of [['loadPipeline', s => s.loadPipeline(true)], ['loadDraft', s => s.loadDraft()]]) {
  test(`${name}: an edit made while the read is in flight stays, unsaved and selectable`, async t => {
    const { store, later, now } = fixture(t);
    now(graph('Saved'));
    await store.getState().loadPipeline(true);
    assert.equal(store.getState().pipeline.nodes[0].data.label, 'Saved');
    const answer = later();
    const pending = read(store.getState());
    store.getState().updateNodeData('judge', { label: 'Edited by the user' });
    answer(graph('Saved'));
    const returned = await pending;
    const state = store.getState();
    assert.equal(state.pipeline.nodes[0].data.label, 'Edited by the user', 'the late read does not replace the edit');
    assert.equal(returned, state.pipeline, 'the caller gets the graph on screen');
    assert.equal(state.pipelineDirty, true, 'the edit still needs saving');
    assert.equal(state.isLoading, false);
    assert.equal(state.canUndo, true, 'the edit keeps its undo history');
  });

  test(`${name}: without an edit the read is applied as before`, async t => {
    const { store, now } = fixture(t);
    now(graph('Saved'));
    await store.getState().loadPipeline(true);
    now(graph('Changed on the server'));
    await read(store.getState());
    assert.equal(store.getState().pipeline.nodes[0].data.label, 'Changed on the server');
    assert.equal(store.getState().pipelineDirty, false);
  });
}

test('an edit that was saved before the read lands does not hold the read back', async t => {
  const { store, later, now } = fixture(t);
  now(graph('Saved'));
  await store.getState().loadPipeline(true);
  const answer = later();
  const pending = store.getState().loadPipeline(true);
  store.getState().updateNodeData('judge', { label: 'Edited' });
  store.setState({ pipelineDirty: false });
  answer(graph('Read after the save'));
  await pending;
  assert.equal(store.getState().pipeline.nodes[0].data.label, 'Read after the save');
});
