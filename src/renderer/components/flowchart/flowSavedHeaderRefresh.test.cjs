const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

// Mount the real Studio hooks, save handler, Zustand store, API serializer and
// header projection. Fetch is the external boundary; unrelated opening/model
// validation and child panels are isolated. No server, model or browser runs.
const renderer = path.resolve(__dirname, '../..');
const A = 'a'.repeat(32), B = 'b'.repeat(32);
const CONTEXT = {workspace_id: 'workspace', project_id: 'project', actor_id: 'actor', mode: 'local'};
const OLD = {id: 'saved-flow', name: 'Inspection', nodes: [
  {id: 'input', position: {x: 0, y: 0}, data: {node_type: 'input', label: 'Input'}},
  {id: 'inspect', position: {x: 200, y: 0}, data: {node_type: 'inspection', task: 'segmentation',
    label: 'Old inspection label', model_job_id: 'job_current', threshold: 0.5}},
], edges: [{id: 'image', source: 'input', target: 'inspect', payload_type: 'image'}]};
const row = (version_id, is_active) => ({version_id, pipeline_id: OLD.id, name: OLD.name,
  recipe_task: 'segmentation', source_dataset_path: '/source', saved_at: '2026-10-18T00:00:00Z',
  created_at: '2026-10-18T00:00:00Z', node_count: 2, model_count: 1, pipeline_hash: version_id.repeat(2),
  is_active, is_latest: is_active});
const oldList = {pipelines: [row(A, true)], total: 1};
const newList = {pipelines: [row(B, true), row(A, false)], total: 2};
const tick = () => new Promise(setImmediate);
const deferred = () => {let resolve; const promise = new Promise(done => {resolve = done;}); return {promise, resolve};};
const walk = node => !node || typeof node !== 'object' ? []
  : [node, ...[node.props?.children].flat(Infinity).flatMap(walk)];
const text = node => typeof node === 'string' ? node : Array.isArray(node) ? node.map(text).join(' ')
  : node?.props?.children ? text(node.props.children) : '';

function load(filename, mocks = {}, fallback) {
  const m = new Module(filename, module); m.filename = filename; m.paths = Module._nodeModulePaths(path.dirname(filename));
  const original = m.require.bind(m);
  m.require = key => key === './flowPreparation' ? load(path.join(__dirname,'flowPreparation.ts')) : Object.hasOwn(mocks, key) ? mocks[key] : fallback ? fallback(key) : original(key);
  m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  }}).outputText, filename);
  return m.exports;
}

function fixture(t, {delayInitial = false, refuseSave = false, delayReadback = false} = {}) {
  const globals = new Map(['window', 'localStorage', 'fetch'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  const install = (key, value) => Object.defineProperty(globalThis, key, {configurable: true, writable: true, value});
  install('window', {location: {search: '?port=8000'}, addEventListener() {}, removeEventListener() {}});
  install('localStorage', {getItem: () => null});
  const actualApi = load(path.join(renderer, 'services/api.ts'));
  actualApi.setProjectContext(CONTEXT);
  const project = {language: 'ko', task: 'segmentation', projectDir: '/project', isProjectBusy: false,
    project: {id: 'project', project_dir: '/project', source_dataset_dir: '/source', active_labelset_id: 'default'}};
  const data = {folderPath: '/source', datasetKey: '/source\0segmentation', hasSelectedFolder: true,
    isLoading: false, importError: null};
  const hook = get => Object.assign(selector => selector ? selector(get()) : get(), {getState: get});
  const startup = load(path.join(__dirname, 'flowchartStartup.ts'));
  const actual = load(path.join(renderer, 'stores/useFlowchartStore.ts'), {
    '../services/api': actualApi, '../services/flowDraft': {flowDraft: {}},
    './useProjectStore': {useProjectStore: hook(() => project)},
    '../components/flowchart/flowchartStartup': startup,
  });
  const store = actual.useFlowchartStore;
  const graph = structuredClone(OLD);
  store.setState({pipeline: graph, cleanPipeline: graph, pipelineDirty: false, pipelineIsDraft: false,
    baseVersionId: A, selectedNodeId: 'inspect', executionChoiceOverride: 'local_cpu'});
  let dirty = true, tree, cursor = 0, listCount = 0, stored;
  const slots = [], effects = [], calls = [], initial = deferred(), readback = deferred();
  const models = [{job_id: 'job_current', task: 'segmentation', label: 'Current source model', class_names: ['background', 'defect']}];
  install('fetch', async (value, options = {}) => {
    const url = new URL(value), method = options.method || 'GET';
    const call = {path: url.pathname, method, query: Object.fromEntries(url.searchParams),
      headers: new Headers(options.headers), body: options.body ? JSON.parse(options.body) : null};
    calls.push(call);
    const response = (body, status = 200) => ({ok: status === 200, status, statusText: String(status),
      headers: new Headers(), json: async () => structuredClone(body)});
    if (call.path === '/api/flowchart/models/catalog' && method === 'GET') return response({models, total: 1});
    if (call.path === '/api/flowchart/pipelines' && method === 'GET') {
      const index = ++listCount;
      if (index === 1) return response(delayInitial ? await initial.promise : oldList);
      if (delayReadback) return response(await readback.promise);
      return response(newList);
    }
    if (call.path === '/api/flowchart/models/verify' && method === 'POST') return response({verified_job_ids: ['job_current']});
    if (call.path === '/api/flowchart/pipeline' && method === 'POST') {
      if (refuseSave) return response({detail: '이 플로우를 연 뒤 적용 중인 플로우가 바뀌었습니다. 플로우를 다시 연 뒤 저장하세요.'}, 409);
      stored = {...structuredClone(call.body), execution_config: {max_workers: 1, device_slots: 1},
        nodes: call.body.nodes.map(node => ({...structuredClone(node), type: 'custom',
          data: {...structuredClone(node.data), crop_padding: 10, params: node.data.params || {}}}))};
      return response({status: 'saved', pipeline_id: OLD.id, node_count: 2, version_id: B});
    }
    if (call.path === `/api/flowchart/pipelines/${B}` && method === 'GET') return response(stored);
    throw Error(`Unexpected source-test transport: ${method} ${call.path}`);
  });
  const react = {
    useState(initialValue) {const i = cursor++; if (!(i in slots)) slots[i] = typeof initialValue === 'function' ? initialValue() : initialValue;
      return [slots[i], value => {const next = typeof value === 'function' ? value(slots[i]) : value;
        if (!Object.is(next, slots[i])) {slots[i] = next; dirty = true;}}];},
    useRef(value) {const i = cursor++; return slots[i] ?? (slots[i] = {current: value});},
    useEffect(fn, deps) {const i = cursor++, prior = slots[i];
      if (!prior || deps.some((value, index) => !Object.is(value, prior.deps[index]))) {
        const next = {deps}; slots[i] = next; effects.push(() => {prior?.cleanup?.(); next.cleanup = fn();});
      }},
  };
  react.useLayoutEffect = react.useEffect;
  const component = () => null, jsx = (type, props) => ({type, props: props || {}});
  const workspace = load(path.join(__dirname, 'FlowEditorWorkspace.tsx'), {'react/jsx-runtime': {jsx, jsxs: jsx}});
  const Studio = load(path.join(__dirname, 'FlowchartStudio.tsx'), {
    react, 'react/jsx-runtime': {jsx, jsxs: jsx}, 'lucide-react': new Proxy({}, {get: () => component}),
    '../../services/api': actualApi, '../../stores/useFlowchartStore': {...actual, useFlowchartStore: hook(store.getState)},
    '../../stores/useProjectStore': {useProjectStore: hook(() => project)},
    '../../stores/useDatasetStore': {useDatasetStore: hook(() => data)},
    '../../stores/useEvaluationStore': {useEvaluationStore: hook(() => ({jobId: null, allowLatestRecovery: false}))},
    '../../stores/useTrainingStore': {useTrainingStore: hook(() => ({}))},
    '../../stores/useComputeStore': {useComputeStore: hook(() => ({profiles: [], isLoaded: true, load: async () => {}}))},
    './flowchartStartup': {...startup, recoverThenLoadFlowchart: async () => ({status: 'ready', pipeline: store.getState().pipeline,
      verifiedJobId: 'job_current'}), singleModelAutoBinding: () => null},
    './flowchartGraph': {...load(path.join(__dirname, 'flowchartGraph.ts')), nodeClassChoices: () => [], validateFlowchartGraph: () => null, locateFlowIssue: () => null,
      flowIssuesByTarget: () => ({nodes: new Map(), edges: new Map()})},
    './flowchartViewport': {computeFlowchartViewport: () => ({scale: 1, contentWidth: 800, contentHeight: 240}), readableFlowScale: value => value},
    './flowHandoff': {flowRecipeLabel: () => '분할 검사'}, './flowRecipes': {FLOW_RECIPES: []},
    './modelFlowHandoff': {readModelFlowHandoff: () => null}, './FlowEditorWorkspace': workspace,
    './FlowChangeDialog': {FlowChangeDialog: 'FlowChangeDialog'}, './FlowWorkspacePanel': {FlowWorkspacePanel: 'FlowWorkspacePanel'},
    './FlowDraftControls': load(path.join(__dirname, 'FlowDraftControls.tsx'), {
      react, '../../services/api': actualApi, '../../stores/useFlowchartStore': {...actual, useFlowchartStore: hook(store.getState)},
      '../../stores/useProjectStore': {useProjectStore: hook(() => project)},
      '../../stores/useDatasetStore': {useDatasetStore: hook(() => data)},
      '../../stores/useComputeStore': {useComputeStore: hook(() => ({transportRevision: 0}))},
    }),
  }, () => new Proxy({}, {get: () => component})).FlowchartStudio;
  const unsubscribe = store.subscribe(() => {dirty = true;});
  t.after(() => {unsubscribe(); slots.forEach(slot => slot?.cleanup?.());
    for (const [key, descriptor] of globals) {if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];}});
  const render = () => {cursor = 0; dirty = false; tree = Studio(); effects.splice(0).forEach(effect => effect()); return tree;};
  const settle = async () => {for (let i = 0; i < 30; i++) {if (dirty) render(); await tick();
    if (!dirty && !effects.length) return tree;} throw Error('Studio did not settle');};
  const identity = () => walk(tree).find(node => node.type === workspace.FlowEditorWorkspace).props.identity;
  return {store, calls, initial, readback, models, settle, identity, tree: () => tree,
    invalidateContext() {actualApi.setProjectContext(null); dirty = true;},
    header: () => text(workspace.FlowEditorWorkspace({area: 'edit', onAreaChange() {}, identity: identity()})),
    lists: () => listCount,
    async save() {
      store.getState().updateNodeData('inspect', {label: 'New inspection label'}); await settle();
      const button = walk(tree).find(node => node.type === 'button' && text(node).trim() === '플로우 저장');
      assert.ok(button, 'the actual save control is rendered');
      assert.equal(button.props.disabled, false, 'the real model verification/save control is enabled');
      await button.props.onClick(); await settle();
      const dialog = walk(tree).find(node => node.type === 'FlowChangeDialog');
      assert.ok(dialog, 'model verification opened the actual Studio confirmation callback');
      return dialog.props.onConfirm('Rename the inspected stage');
    },
  };
}

test('a delayed opening list cannot replace the new saved/active header after save readback', async t => {
  const f = fixture(t, {delayInitial: true}); await f.settle();
  assert.equal(f.lists(), 1); await f.save(); await f.settle();
  assert.equal(f.identity().selectedVersion, B); assert.equal(f.identity().activeVersion, B);
  assert.equal(f.store.getState().pipeline.nodes[1].data.label, 'New inspection label');
  f.initial.resolve(oldList); await f.settle();
  assert.equal(f.identity().selectedVersion, B, 'late pre-save rows cannot select the prior version');
  assert.equal(f.identity().activeVersion, B, 'the header retains the fresh active readback');
  assert.match(f.header(), new RegExp(`저장\\s+v${B.slice(0,8)}.*활성\\s+v${B.slice(0,8)}`));
  assert.equal(walk(f.tree()).find(node => node.type === 'FlowWorkspacePanel').props.models[0].job_id, 'job_current',
    'the still-current model catalog is retained independently of stale version rows');
});

test('ordinary save updates the mounted header without navigation and retains model verification and save CAS', async t => {
  const f = fixture(t); await f.settle(); assert.equal(f.identity().activeVersion, A);
  await f.save(); await f.settle();
  assert.equal(f.identity().selectedVersion, B); assert.equal(f.identity().activeVersion, B);
  const verification = f.calls.find(call => call.path.endsWith('/models/verify'));
  assert.deepEqual(verification.body, {source_dataset_path: '/source', models: [{job_id: 'job_current', task: 'segmentation'}]});
  const saved = f.calls.find(call => call.path === '/api/flowchart/pipeline');
  assert.equal(saved.query.expected_version_id, A); assert.equal(saved.headers.get('X-Vision-Project'), 'project');
  assert.equal(saved.body.nodes[1].data.label, 'New inspection label');
  assert.equal(f.identity().evaluation, undefined); assert.equal(f.identity().approval, '미조회');
});

test('an accepted context change while save readback is pending fences that obsolete header response', async t => {
  const f = fixture(t, {delayReadback: true}); await f.settle();
  const pending = f.save();
  for (let i = 0; i < 30 && f.lists() < 2; i++) await tick();
  assert.equal(f.lists(), 2, 'the original post-save readback is in flight');
  f.invalidateContext();
  f.readback.resolve(newList); await pending; await f.settle();
  assert.notEqual(f.identity().selectedVersion, B); assert.notEqual(f.identity().activeVersion, B);
  assert.equal(f.store.getState().pipeline.nodes[1].data.label, 'New inspection label');
});
