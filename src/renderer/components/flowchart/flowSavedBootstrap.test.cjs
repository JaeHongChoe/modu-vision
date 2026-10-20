const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

// Real Studio hook/effect bodies, startup helper, Zustand flow/evaluation stores,
// and API serializer. The transport, React scheduler and unrelated child panels
// are controlled boundaries; this is not a browser/DOM/native acceptance test.
const renderer = path.resolve(__dirname, '../..');
const VERSION = 'a'.repeat(32);
const CONTEXT = {workspace_id: 'workspace', project_id: 'project', actor_id: 'actor', mode: 'local'};
const GRAPH = {id: 'saved-flow', name: 'Inspection', nodes: [
  {id: 'input', position: {x: 0, y: 0}, data: {node_type: 'input', label: 'Input'}},
  {id: 'inspect', position: {x: 200, y: 0}, data: {node_type: 'inspection', task: 'segmentation',
    label: 'Saved inspection', model_job_id: 'job_current', threshold: 0.5}},
], edges: [{id: 'image', source: 'input', target: 'inspect', payload_type: 'image'}]};
const tick = () => new Promise(setImmediate);
const deferred = () => {let resolve; const promise = new Promise(done => {resolve = done;}); return {promise, resolve};};
function load(filename, mocks = {}, fallback) {
  const m = new Module(filename, module); m.filename = filename; m.paths = Module._nodeModulePaths(path.dirname(filename));
  const original = m.require.bind(m);
  m.require = key => Object.hasOwn(mocks, key) ? mocks[key] : fallback ? fallback(key) : original(key);
  m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  }}).outputText, filename);
  return m.exports;
}
function fixture(t, {initial = null, dirty = false, draft = false, draftResponse = null, recordDelay = null} = {}) {
  const globals = new Map(['window', 'localStorage', 'fetch'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  const install = (key, value) => Object.defineProperty(globalThis, key, {configurable: true, writable: true, value});
  install('window', {location: {search: '?port=8000'}, addEventListener() {}, removeEventListener() {}});
  install('localStorage', {getItem: () => null});
  const actualApi = load(path.join(renderer, 'services/api.ts'));
  actualApi.setProjectContext(CONTEXT);
  const project = {language: 'ko', task: 'segmentation', projectDir: '/project', isProjectBusy: false,
    project: {id: 'project', task: 'segmentation', project_dir: '/project', source_dataset_dir: '/source', active_labelset_id: 'default'}};
  const data = {folderPath: '/source', datasetKey: '/source\0segmentation', hasSelectedFolder: true, isLoading: false, importError: null};
  const hook = get => Object.assign(selector => selector ? selector(get()) : get(), {getState: get});
  const training = {isCurrentData: false, status: 'idle', jobId: null};
  const startup = load(path.join(__dirname, 'flowchartStartup.ts'));
  const flowDraft = load(path.join(renderer, 'services/flowDraft.ts'), {'./api': actualApi}).flowDraft;
  const actual = load(path.join(renderer, 'stores/useFlowchartStore.ts'), {
    '../services/api': actualApi, '../services/flowDraft': {flowDraft}, './useProjectStore': {useProjectStore: hook(() => project)},
    '../components/flowchart/flowchartStartup': startup,
  });
  const evaluation = load(path.join(renderer, 'stores/useEvaluationStore.ts'), {
    '../services/api': actualApi, './useProjectStore': {useProjectStore: hook(() => project)},
    './useDatasetStore': {useDatasetStore: hook(() => data)}, './useTrainingStore': {useTrainingStore: hook(() => training)},
    '../utils/classSemantics': load(path.join(renderer, 'utils/classSemantics.ts')),
  }).useEvaluationStore;
  evaluation.setState({allowLatestRecovery: true});
  const store = actual.useFlowchartStore;
  store.setState({pipeline: initial, cleanPipeline: initial, pipelineDirty: dirty, pipelineIsDraft: draft,
    baseVersionId: initial ? VERSION : undefined, selectedNodeId: null, executionChoiceOverride: 'local_cpu'});
  let needsRender = true, tree, cursor = 0;
  const slots = [], effects = [], calls = [];
  const models = [{job_id: 'job_current', task: 'segmentation', label: 'Current source model', class_names: ['background', 'defect']}];
  install('fetch', async (value, options = {}) => {
    const url = new URL(value), method = options.method || 'GET';
    const call = {path: url.pathname, method, query: Object.fromEntries(url.searchParams),
      headers: new Headers(options.headers), body: options.body ? JSON.parse(options.body) : null};
    calls.push(call);
    const response = (body, status = 200) => ({ok: status === 200, status, statusText: String(status), headers: new Headers(), json: async () => structuredClone(body)});
    if (call.path === '/api/flowchart/models/catalog') return response({models, total: 1});
    if (call.path === '/api/flowchart/pipelines') return response({pipelines: [{version_id: VERSION, pipeline_id: GRAPH.id,
      name: GRAPH.name, recipe_task: 'segmentation', source_dataset_path: '/source', saved_at: '2026-10-18T00:00:00Z',
      node_count: 2, model_count: 1, pipeline_hash: 'b'.repeat(64), is_active: true, is_latest: true}], total: 1});
    if (call.path === '/api/flowchart/draft') return draftResponse ? response(draftResponse) : response({detail: 'No draft'}, 404);
    if (call.path === '/api/flowchart/pipeline/active/record') {
      if (recordDelay) await recordDelay.promise;
      return response({version_id: VERSION, pipeline: GRAPH});
    }
    if (call.path === '/api/flowchart/pipeline/active-version') return response({version_id: VERSION});
    if (call.path === '/api/flowchart/pipeline') return response(GRAPH);
    if (call.path === '/api/flowchart/models/verify') return response({verified_job_ids: ['job_current']});
    if (call.path === '/api/evaluation/results') return response({job_id: 'job_current', task: 'segmentation', metrics: {}, test_predictions: [
      {image_id: 'image', file_name: 'image.png', file_path: '/source/image.png', ground_truth: 'ok', predicted_class: 'ok', confidence: 0.9}]});
    if (call.path === '/api/evaluation/overkill-underkill') return response({optimal_threshold: 0.5});
    if (call.path.startsWith('/api/evaluation/heatmap/')) return response({overlay_base64: null});
    throw Error(`Unexpected source-test transport: ${method} ${call.path}`);
  });
  const react = {
    useState(initialValue) {const i = cursor++; if (!(i in slots)) slots[i] = typeof initialValue === 'function' ? initialValue() : initialValue;
      return [slots[i], value => {const next = typeof value === 'function' ? value(slots[i]) : value;
        if (!Object.is(next, slots[i])) {slots[i] = next; needsRender = true;}}];},
    useRef(value) {const i = cursor++; return slots[i] ?? (slots[i] = {current: value});},
    useEffect(fn, deps) {const i = cursor++, prior = slots[i];
      if (!prior || deps.length !== prior.deps.length || deps.some((value, index) => !Object.is(value, prior.deps[index]))) {
        const next = {deps}; slots[i] = next; effects.push(() => {prior?.cleanup?.(); next.cleanup = fn();});
      }},
  };
  react.useLayoutEffect = react.useEffect;
  const component = () => null, jsx = (type, props) => ({type, props: props || {}});
  const Studio = load(path.join(__dirname, 'FlowchartStudio.tsx'), {
    react, 'react/jsx-runtime': {jsx, jsxs: jsx}, 'lucide-react': new Proxy({}, {get: () => component}),
    '../../services/api': actualApi, '../../stores/useFlowchartStore': {...actual, useFlowchartStore: hook(store.getState)},
    '../../stores/useProjectStore': {useProjectStore: hook(() => project)}, '../../stores/useDatasetStore': {useDatasetStore: hook(() => data)},
    '../../stores/useEvaluationStore': {useEvaluationStore: hook(evaluation.getState)}, '../../stores/useTrainingStore': {useTrainingStore: hook(() => training)},
    '../../stores/useComputeStore': {useComputeStore: hook(() => ({profiles: [], isLoaded: true, load: async () => {}}))},
    './flowchartStartup': startup,
    './flowchartGraph': {...load(path.join(__dirname, 'flowchartGraph.ts')), nodeClassChoices: () => [], validateFlowchartGraph: () => null,
      locateFlowIssue: () => null, flowIssuesByTarget: () => ({nodes: new Map(), edges: new Map()})},
    './flowchartViewport': {computeFlowchartViewport: () => ({scale: 1, contentWidth: 800, contentHeight: 240}), readableFlowScale: value => value},
    './flowHandoff': {flowRecipeLabel: () => '분할 검사'}, './flowRecipes': {FLOW_RECIPES: []}, './modelFlowHandoff': {readModelFlowHandoff: () => null},
    './FlowEditorWorkspace': load(path.join(__dirname, 'FlowEditorWorkspace.tsx'), {'react/jsx-runtime': {jsx, jsxs: jsx}}),
  }, () => new Proxy({}, {get: () => component})).FlowchartStudio;
  const subscriptions = [store.subscribe(() => {needsRender = true;}), evaluation.subscribe(() => {needsRender = true;})];
  t.after(() => {subscriptions.forEach(unsubscribe => unsubscribe()); slots.forEach(slot => slot?.cleanup?.());
    for (const [key, descriptor] of globals) {if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];}});
  const render = () => {cursor = 0; needsRender = false; tree = Studio(); effects.splice(0).forEach(effect => effect()); return tree;};
  const settle = async () => {for (let i = 0; i < 40; i++) {if (needsRender) render(); await tick(); if (!needsRender && !effects.length) return tree;} throw Error('Studio did not settle');};
  return {store, actual, startup, actualApi, calls, project, data, evaluation, render, settle,
    evaluationCalls: () => calls.filter(call => call.path.startsWith('/api/evaluation/'))};
}

test('the real Studio opens a current fully-bound saved graph without evaluation, analysis or heatmap dispatch', async t => {
  const f = fixture(t); await f.settle();
  assert.deepEqual(f.evaluationCalls(), [], 'opening a source-verified saved graph must not start evaluation/inference follow-ups');
  const reads = f.calls.filter(call => call.path === '/api/flowchart/pipeline/active/record');
  assert.ok(reads.length > 0, 'the actual saved record, rather than an in-memory base ID, was read');
  const verified = f.calls.find(call => call.path === '/api/flowchart/models/verify');
  assert.deepEqual(verified.body, {source_dataset_path: '/source', models: [{job_id: 'job_current', task: 'segmentation'}]});
  assert.equal(verified.headers.get('X-Vision-Project'), 'project');
  assert.equal(f.store.getState().pipelineDirty, false);
  assert.equal(f.store.getState().baseVersionId, VERSION);
  assert.equal(f.store.getState().pipeline.nodes[1].data.model_job_id, 'job_current');
});

test('the original default helper caller retains recovery-before-load behavior', async t => {
  const f = fixture(t), events = []; let job = null;
  const result = await f.startup.recoverThenLoadFlowchart({folderPath: '/source', task: 'segmentation', datasetKey: '/source\0segmentation',
    hasSelectedFolder: true, datasetIsLoading: false, importError: null, allowLatestRecovery: true, completedCurrentJobId: null,
    getVerifiedJobId: () => job, loadEvaluation: async () => {events.push('evaluation'); job = 'job_current';},
    loadSavedPipeline: async () => {events.push('saved'); return GRAPH;}, verifyModels: async () => {events.push('verify');}, isCurrent: () => true});
  assert.equal(result.status, 'ready'); assert.deepEqual(events, ['evaluation', 'saved', 'verify']);
});

for (const branch of ['active-record', 'draft']) test(`an optional startup publication guard preserves an edit made during ${branch} await`, async t => {
  const pending = deferred();
  const draftResponse = branch === 'draft' ? {context: {project_id: 'project', source_dataset_path: '/source', labelset_id: 'default'},
    pipeline: GRAPH, draft_sha256: 'b'.repeat(64), active_version_id: VERSION, base_version_id: VERSION} : null;
  const f = fixture(t, {recordDelay: branch === 'active-record' ? pending : null, draftResponse});
  // Control only the original request's completion, never the store's edit or publication methods.
  const apiMethod = branch === 'draft' ? null : 'getActivePipelineRecord';
  let releaseDraft;
  if (branch === 'draft') {
    const oldFetch = globalThis.fetch;
    globalThis.fetch = async (...args) => {
      if (new URL(args[0]).pathname === '/api/flowchart/draft') {await pending.promise; releaseDraft = true;}
      return oldFetch(...args);
    };
  }
  const before = structuredClone(GRAPH);
  f.store.setState({pipeline: before, cleanPipeline: before, pipelineDirty: false, pipelineIsDraft: false, baseVersionId: VERSION});
  const guard = () => f.store.getState().pipeline === before && !f.store.getState().pipelineDirty;
  const loading = f.store.getState().loadPipeline(true, 'segmentation', '/source', guard);
  for (let i = 0; i < 8 && !f.calls.some(call => call.path === '/api/flowchart/pipeline/active/record') && branch !== 'draft'; i++) await tick();
  f.store.getState().updateNodeData('inspect', {label: 'Edit made while loading'});
  const edited = f.store.getState().pipeline;
  pending.resolve(); await loading;
  assert.equal(f.store.getState().pipeline, edited, 'an obsolete startup preload must not overwrite the genuine edit');
  assert.equal(f.store.getState().pipeline.nodes[1].data.label, 'Edit made while loading');
  assert.equal(f.store.getState().pipelineDirty, true);
  assert.equal(f.store.getState().isLoading, false);
  if (branch === 'draft') assert.equal(releaseDraft, true);
  void apiMethod;
});

function overrideJSON(route, body, status = 200) {
  const original = globalThis.fetch;
  globalThis.fetch = (...args) => new URL(args[0]).pathname === route
    ? Promise.resolve(new Response(JSON.stringify(body), {status, headers: {'content-type': 'application/json'}}))
    : original(...args);
}

test('a legacy/default graph plus independently read active base is not a saved-record receipt', async t => {
  const f = fixture(t);
  overrideJSON('/api/flowchart/pipeline/active/record', {detail: 'No matching saved graph'}, 404);
  await f.store.getState().loadPipeline(true, 'segmentation', '/source');
  const current = f.store.getState();
  assert.equal(current.baseVersionId, VERSION);
  assert.equal(current.pipelineDirty, false);
  assert.equal(current.pipelineIsDraft, false);
  assert.equal(f.actual.isCurrentSavedPipeline(current.pipeline, 'segmentation', '/source'), false);
});

for (const active of [false, true]) test(`a persisted draft ${active ? 'equal to the actual active graph' : 'with only a historical base'} has exact shortcut provenance`, async t => {
  const f = fixture(t, {draftResponse: {context: {project_id: 'project', source_dataset_path: '/source', labelset_id: 'default'},
    pipeline: GRAPH, draft_sha256: 'b'.repeat(64), active_version_id: active ? VERSION : null, base_version_id: VERSION}});
  await f.store.getState().loadPipeline(true, 'segmentation', '/source');
  assert.equal(f.actual.isCurrentSavedPipeline(f.store.getState().pipeline, 'segmentation', '/source'), active);
});

for (const flag of ['pipelineDirty', 'pipelineIsDraft', 'isLoading', 'isSaving', 'isRunning', 'historyGroupStart'])
  test(`Studio ${flag} at entry preserves the original recovery order without a speculative preload`, async t => {
    const f = fixture(t, {initial: structuredClone(GRAPH)});
    f.store.setState({[flag]: flag === 'historyGroupStart' ? f.store.getState().pipeline : true});
    await f.settle();
    const evaluation = f.calls.findIndex(call => call.path === '/api/evaluation/results');
    const saved = f.calls.findIndex(call => call.path === '/api/flowchart/pipeline/active/record');
    assert.ok(evaluation >= 0, 'the existing recovery path was retained');
    assert.ok(saved === -1 || evaluation < saved, 'no extra saved-record preload ran before the original recovery');
  });

for (const mutation of ['edit', 'edit-undo', 'in-place-graph', 'authority-ABA'])
  test(`a qualified saved proof retired by ${mutation} during verification cancels without evaluation fallback`, async t => {
    const f = fixture(t); await f.store.getState().loadPipeline(true, 'segmentation', '/source');
    const saved = f.store.getState().pipeline;
    assert.equal(f.actual.isCurrentSavedPipeline(saved, 'segmentation', '/source'), true);
    const pending = deferred(); let entered = false, evaluations = 0;
    const operation = f.startup.recoverThenLoadFlowchart({folderPath: '/source', task: 'segmentation', datasetKey: '/source\0segmentation',
      hasSelectedFolder: true, datasetIsLoading: false, importError: null, allowLatestRecovery: true, completedCurrentJobId: null,
      getVerifiedJobId: () => null, loadEvaluation: async () => {evaluations++;}, loadSavedPipeline: async () => saved,
      loadCurrentSavedPipeline: async () => saved, isCurrentSavedPipeline: graph => f.actual.isCurrentSavedPipeline(graph, 'segmentation', '/source'),
      verifyModels: async () => {entered = true; await pending.promise;}, isCurrent: () => true});
    for (let i = 0; i < 8 && !entered; i++) await tick();
    assert.equal(entered, true);
    if (mutation === 'edit' || mutation === 'edit-undo') f.store.getState().updateNodeData('inspect', {label: 'Changed during verification'});
    if (mutation === 'edit-undo') {
      f.store.getState().undo();
      assert.equal(f.store.getState().pipeline, saved, 'the original graph object really returned');
      assert.equal(f.store.getState().pipelineDirty, false);
    }
    if (mutation === 'in-place-graph') saved.nodes[1].data.label = 'Mutation not represented by a cached semantic key';
    if (mutation === 'authority-ABA') {
      f.actualApi.setProjectContext({...CONTEXT, project_id: 'other'});
      f.actualApi.setProjectContext(CONTEXT);
    }
    assert.equal(f.actual.isCurrentSavedPipeline(saved, 'segmentation', '/source'), false);
    pending.resolve();
    assert.deepEqual(await operation, {status: 'cancelled'});
    assert.equal(evaluations, 0);
  });

test('a partially bound saved graph retains legacy recovery instead of vacuously passing the shortcut', async t => {
  const f = fixture(t), partial = structuredClone(GRAPH);
  partial.nodes.push({id: 'second', position: {x: 400, y: 0}, data: {node_type: 'inspection', task: 'segmentation', model_job_id: null}});
  overrideJSON('/api/flowchart/pipeline/active/record', {version_id: VERSION, pipeline: partial});
  await f.store.getState().loadPipeline(true, 'segmentation', '/source');
  const saved = f.store.getState().pipeline; let recoveries = 0;
  const result = await f.startup.recoverThenLoadFlowchart({folderPath: '/source', task: 'segmentation', datasetKey: '/source\0segmentation',
    hasSelectedFolder: true, datasetIsLoading: false, importError: null, allowLatestRecovery: true, completedCurrentJobId: null,
    getVerifiedJobId: () => null, loadEvaluation: async () => {recoveries++;}, loadSavedPipeline: async () => saved,
    loadCurrentSavedPipeline: async () => saved, isCurrentSavedPipeline: graph => f.actual.isCurrentSavedPipeline(graph, 'segmentation', '/source'),
    verifyModels: async () => {}, isCurrent: () => true});
  assert.equal(recoveries, 1);
  assert.equal(result.status, 'ready', 'the old fallback policy, rather than a new model authority, remains in force');
});

test('failed model verification cannot mark the saved fast path ready', async t => {
  const f = fixture(t); await f.store.getState().loadPipeline(true, 'segmentation', '/source');
  const saved = f.store.getState().pipeline; let recoveries = 0;
  const result = await f.startup.recoverThenLoadFlowchart({folderPath: '/source', task: 'segmentation', datasetKey: '/source\0segmentation',
    hasSelectedFolder: true, datasetIsLoading: false, importError: null, allowLatestRecovery: true, completedCurrentJobId: null,
    getVerifiedJobId: () => null, loadEvaluation: async () => {recoveries++;}, loadSavedPipeline: async () => saved,
    loadCurrentSavedPipeline: async () => saved, isCurrentSavedPipeline: graph => f.actual.isCurrentSavedPipeline(graph, 'segmentation', '/source'),
    verifyModels: async () => {throw Error('Controlled source/model mismatch');}, isCurrent: () => true});
  assert.equal(recoveries, 1);
  assert.deepEqual(result, {status: 'blocked', reason: 'saved_model_mismatch'});
});

test('the actual Studio action and identity projection settle ready for the current saved graph', async t => {
  const f = fixture(t), tree = await f.settle();
  const walk = node => !node || typeof node !== 'object' ? []
    : [node, ...[node.props?.children].flat(Infinity).flatMap(walk)];
  const text = node => typeof node === 'string' ? node : Array.isArray(node) ? node.map(text).join(' ')
    : node?.props?.children ? text(node.props.children) : '';
  const add = walk(tree).find(node => node.type === 'button' && text(node).trim() === '패치 분할 추가');
  assert.ok(add, 'the actual graph-editing action is rendered');
  assert.equal(add.props.disabled, false, 'the original flowOpening gate has settled, rather than leaving a loaded graph checking');
  const header = walk(tree).find(node => node.type?.name === 'FlowEditorWorkspace');
  assert.ok(header, 'the actual Studio emits the shared identity projection');
  assert.equal(header.props.identity.selectedVersion, VERSION);
  assert.equal(header.props.identity.activeVersion, VERSION);
  assert.ok(header.props.identity.models.some(value => value.includes('job_current')));
  assert.equal(header.props.identity.evaluation, undefined, 'opening a graph does not manufacture evaluation evidence');
  assert.equal(header.props.identity.approval, '미조회');
  assert.deepEqual(f.evaluationCalls(), []);
});

test('a start reads the flow once: the saved-flow check hands what it read to the recovery step', async t => {
  const f = fixture(t), reads = []; let handed;
  const draftGraph = structuredClone(GRAPH);
  const result = await f.startup.recoverThenLoadFlowchart({folderPath: '/source', task: 'segmentation', datasetKey: '/source\0segmentation',
    hasSelectedFolder: true, datasetIsLoading: false, importError: null, allowLatestRecovery: true, completedCurrentJobId: null,
    getVerifiedJobId: () => 'job_current', loadEvaluation: async () => {},
    loadCurrentSavedPipeline: async () => {reads.push('check'); return draftGraph;},
    isCurrentSavedPipeline: () => false,
    loadSavedPipeline: async alreadyRead => {handed = alreadyRead; return alreadyRead;},
    verifyModels: async () => {}, isCurrent: () => true});
  assert.equal(result.status, 'ready');
  assert.deepEqual(reads, ['check']);
  assert.equal(handed, draftGraph, 'the recovery step receives the graph the check read');
});

test('the recovery step reuses that read only while the same graph is on screen and idle', t => {
  const f = fixture(t), graph = structuredClone(GRAPH), idle = {pipeline: graph, pipelineDirty: false, isLoading: false, isSaving: false, isRunning: false};
  const reuse = f.startup.canReuseStartupRead;
  assert.equal(reuse(idle, graph), true);
  assert.equal(reuse(idle, null), false, 'nothing was read');
  assert.equal(reuse({...idle, pipeline: structuredClone(GRAPH)}, graph), false, 'another read replaced it');
  assert.equal(reuse({...idle, pipelineDirty: true}, graph), false, 'the user edited it');
  for (const busy of ['isLoading', 'isSaving', 'isRunning']) assert.equal(reuse({...idle, [busy]: true}, graph), false, busy);
});
