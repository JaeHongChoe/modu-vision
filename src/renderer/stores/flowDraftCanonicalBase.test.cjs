// Exercise the original store, draft service and API serializer. Only HTTP and
// the selected project are controlled; no backend/native acceptance is claimed.
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

const OLD = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const ACTIVE = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
const HASH = 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc';
const PROJECT = { id: 'source-project', project_dir: '/workspace/source-project',
  source_dataset_dir: '/original/source', active_labelset_id: 'default' };
const CONTEXT = { project_id: PROJECT.id, source_dataset_path: '/original/source', labelset_id: 'default' };
const AUTH_CONTEXT = { workspace_id: 'workspace', project_id: PROJECT.id, actor_id: 'actor', mode: 'team' };
const GRAPH = { id: 'flow', name: 'Saved graph', nodes: [
  { id: 'inspect', type: 'custom', position: { x: 0, y: 0 },
    data: { node_type: 'inspection', task: 'segmentation', label: 'Saved label', model_job_id: null } },
], edges: [] };

function fixture(t, draft, versionSave = null) {
  const api = load('../services/api.ts');
  const service = load('../services/flowDraft.ts', { './api': api });
  const store = load('./useFlowchartStore.ts', {
    '../services/api': api,
    '../services/flowDraft': service,
    './useProjectStore': { useProjectStore: { getState: () => ({ project: PROJECT }) } },
    '../components/flowchart/flowchartStartup': { getFlowchartModelTask: node => node.data.task || null },
  }).useFlowchartStore;
  api.setSharedApiBase('https://owned.invalid', () => ({ credentials: 'include' }));
  api.setProjectContext(AUTH_CONTEXT);
  let persisted = { version: 1, saved_at_ns: 100, context: CONTEXT,
    pipeline: structuredClone(GRAPH), draft_sha256: HASH, ...draft };
  const calls = [], previous = global.fetch;
  global.fetch = async (url, options = {}) => {
    const parsedURL = new URL(url);
    const row = { path: parsedURL.pathname, query: Object.fromEntries(parsedURL.searchParams), method: options.method || 'GET',
      raw_body: options.body ?? null, credentials: options.credentials,
      headers: Object.fromEntries(new Headers(options.headers)) };
    calls.push(row);
    assert.equal(row.headers['x-vision-project'], PROJECT.id);
    assert.deepEqual(JSON.parse(row.headers['x-vision-context']), AUTH_CONTEXT);
    if (row.path === '/api/flowchart/pipeline' && row.method === 'POST' && versionSave) {
      return { ok: !versionSave.refuse, status: versionSave.refuse ? 409 : 200,
        statusText: versionSave.refuse ? 'Conflict' : 'OK', headers: new Headers(),
        json: async () => versionSave.refuse
          ? { detail: 'The active flow changed since this one was opened; reopen it before saving.' }
          : { status: 'saved', version_id: 'dddddddddddddddddddddddddddddddd' } };
    }
    if (row.path === '/api/flowchart/pipelines/dddddddddddddddddddddddddddddddd' && versionSave) {
      assert.equal(row.method, 'GET');
      return { ok: true, status: 200, headers: new Headers(),
        json: async () => JSON.parse(calls.find(call => call.method === 'POST').raw_body) };
    }
    assert.equal(row.path, '/api/flowchart/draft', 'No unrelated request may be dispatched');
    if (row.method === 'PUT') {
      persisted = { version: 1, saved_at_ns: 200, ...JSON.parse(row.raw_body),
        draft_sha256: HASH, active_version_id: null };
    } else assert.equal(row.method, 'GET');
    return { ok: true, status: 200, headers: new Headers({ 'X-Vision-Context': JSON.stringify(AUTH_CONTEXT) }),
      json: async () => structuredClone(persisted) };
  };
  t.after(() => { global.fetch = previous; });
  return { store, calls };
}

for (const loader of ['loadPipeline', 'loadDraft']) {
  for (const [name, draft, expectedBase, expectedLoadedBase] of [
    ['identical active graph normalizes its older base', { active_version_id: ACTIVE, base_version_id: OLD }, ACTIVE, ACTIVE],
    ['divergent draft preserves its older base', { active_version_id: null, base_version_id: OLD }, OLD, OLD],
    ['divergent no-active draft preserves none', { active_version_id: null, base_version_id: 'none' }, 'none', null],
    ['older divergent draft preserves an omitted base', { active_version_id: null }, undefined, undefined],
    ['empty active identity does not replace its base', { active_version_id: '', base_version_id: OLD }, OLD, OLD],
    ['malformed active identity does not replace its base', { active_version_id: 'not-a-saved-version', base_version_id: OLD }, OLD, OLD],
  ]) {
    test(`${loader}: ${name} through edit and raw PUT`, { timeout: 5000 }, async t => {
      const { store, calls } = fixture(t, draft);
      const loaded = loader === 'loadPipeline'
        ? await store.getState().loadPipeline(true, 'segmentation', '/original/source')
        : await store.getState().loadDraft();
      assert.deepEqual(loaded, GRAPH);
      assert.equal(store.getState().pipelineIsDraft, !draft.active_version_id);
      const loadedBase = store.getState().baseVersionId;
      store.getState().updateNodeData('inspect', { label: 'Edited label' });
      assert.equal(store.getState().pipelineDirty, true);
      assert.equal(await store.getState().saveDraft(), true);
      assert.deepEqual(calls.map(row => [row.method, row.path]), [
        ['GET', '/api/flowchart/draft'], ['PUT', '/api/flowchart/draft'], ['GET', '/api/flowchart/draft'],
      ]);
      const raw = JSON.parse(calls[1].raw_body);
      assert.deepEqual(raw.context, CONTEXT);
      assert.deepEqual(raw.pipeline, { ...GRAPH, nodes: [{ ...GRAPH.nodes[0],
        data: { ...GRAPH.nodes[0].data, label: 'Edited label' } }] });
      assert.deepEqual(Object.keys(raw).sort(), expectedBase === undefined
        ? ['context', 'pipeline'] : ['base_version_id', 'context', 'pipeline']);
      assert.equal(store.getState().pipelineDirty, false);
      assert.equal(store.getState().pipelineIsDraft, true);
      const proof = { source_only: true, loader, case: name, initial_reply: draft, loaded_base: loadedBase,
        expected_loaded_base: expectedLoadedBase, requests: calls, expected_PUT_base: expectedBase,
        save_returned_true: true, native_attribution_proven: false };
      if (process.env.DRAFT_BASE_PROOF_DIR) {
        fs.mkdirSync(process.env.DRAFT_BASE_PROOF_DIR, { recursive: true });
        fs.writeFileSync(path.join(process.env.DRAFT_BASE_PROOF_DIR,
          `${loader}-${name.replaceAll(' ', '-')}.json`), JSON.stringify(proof, null, 2)+'\n');
      }
      // Causal RED is the production serializer's emitted older base after all
      // original load, real edit, full response and readback assertions above.
      assert.equal(raw.base_version_id, expectedBase);
      assert.equal(loadedBase, expectedLoadedBase);
    });
  }

  for (const [name, draft, expected, server, refused] of [
    ['identical active graph uses its proven base for an explicit save', { active_version_id: ACTIVE, base_version_id: OLD }, ACTIVE, ACTIVE, false],
    ['divergent draft keeps its older CAS base and remains refused', { active_version_id: null, base_version_id: OLD }, OLD, ACTIVE, true],
    ['later active change keeps the loaded CAS base and remains refused', { active_version_id: ACTIVE, base_version_id: OLD }, ACTIVE, 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', true],
  ]) {
    test(`${loader}: ${name} at original version POST`, { timeout: 5000 }, async t => {
      const { store, calls } = fixture(t, draft, { refuse: refused, active: server });
      t.mock.method(console, 'error', () => {});
      if (loader === 'loadPipeline') await store.getState().loadPipeline(true, 'segmentation', '/original/source');
      else await store.getState().loadDraft();
      store.getState().updateNodeData('inspect', { label: 'Explicit save edit' });
      const saved = await store.getState().savePipeline(undefined, 'segmentation', '/original/source');
      const posts = calls.filter(row => row.method === 'POST');
      assert.equal(posts.length, 1);
      const sent = posts[0];
      assert.equal(sent.path, '/api/flowchart/pipeline');
      assert.equal(sent.query.recipe_task, 'segmentation');
      assert.equal(sent.query.source_dataset_path, '/original/source');
      assert.equal(JSON.parse(sent.raw_body).nodes[0].data.label, 'Explicit save edit');
      if (process.env.DRAFT_BASE_PROOF_DIR) {
        fs.mkdirSync(process.env.DRAFT_BASE_PROOF_DIR, { recursive: true });
        fs.writeFileSync(path.join(process.env.DRAFT_BASE_PROOF_DIR,
          `${loader}-${name.replaceAll(' ', '-')}.json`), JSON.stringify({ source_only: true,
          controlled_HTTP_CAS_response_not_backend_execution: true, initial_reply: draft,
          requests: calls, expected_POST_base: expected, server_active: server, saved }, null, 2)+'\n');
      }
      assert.equal(sent.query.expected_version_id, expected);
      assert.equal(saved, !refused);
      if (refused) {
        assert.equal(store.getState().pipelineDirty, true);
        assert.equal(store.getState().baseVersionId, expected);
        assert.match(store.getState().errorMessage, /활성 플로우가 바뀌었습니다/);
      } else {
        assert.equal(store.getState().pipelineDirty, false);
        assert.equal(store.getState().baseVersionId, 'dddddddddddddddddddddddddddddddd');
      }
      assert.deepEqual(calls.map(row => row.path), refused
        ? ['/api/flowchart/draft', '/api/flowchart/pipeline']
        : ['/api/flowchart/draft', '/api/flowchart/pipeline', '/api/flowchart/pipelines/dddddddddddddddddddddddddddddddd']);
    });
  }
}
