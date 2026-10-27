const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');

// The Studio's real rendered handlers call the real store and API serializer.
// Only HTTP suppliers, unrelated child components and network effects are isolated.
// This does not prove backend execution, browser focus or native process closure.
function load(file, mocks = {}, fallback) {
  const m = new Module(file, module);
  m.filename = file; m.paths = Module._nodeModulePaths(path.dirname(file));
  const original = m.require.bind(m);
  m.require = key => key === './flowPreparation' ? load(path.join(__dirname,'flowPreparation.ts')) : Object.hasOwn(mocks, key) ? mocks[key] : fallback ? fallback(key) : original(key);
  m._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {
    jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  }}).outputText, file);
  return m.exports;
}
const renderer = path.resolve(__dirname, '../..');
const ID = '8d72b3b5521745bda473d2a82fcc7078';
const NEXT_ID = '9d72b3b5521745bda473d2a82fcc7078';
const CONTEXT = {workspace_id: 'workspace', project_id: 'a', actor_id: 'operator', mode: 'local'};
const IMAGE = {source: 'file', imagePath: '/workspace/a/source/input.png', fileName: 'input.png'};
const GRAPH = {id: 'partial', name: 'Partial', nodes: [
  {id: 'input', position: {x: 0, y: 0}, data: {node_type: 'input', label: 'Input'}},
  {id: 'stop', position: {x: 100, y: 0}, data: {node_type: 'output', label: 'Stop'}},
], edges: [{id: 'edge', source: 'input', target: 'stop'}]};
const CPU = {savedVersionId: null, executionTarget: 'local', device: 'cpu', stopNodeId: 'stop'};
const snapshot = (run_id = ID, state = 'prepared', execution_finished = false) => ({
  run_id, project_id: 'a', stop_node_id: 'stop', execution_target: 'local', device: 'cpu',
  state, execution_finished, error_type: null,
});
const response = (value, status = 200) => ({ok: status >= 200 && status < 300, status,
  statusText: String(status), headers: new Headers(), json: async () => value});
function deferred() {
  let resolve; const promise = new Promise(done => {resolve = done;}); return {promise, resolve};
}
async function until(predicate) {
  for (let i = 0; i < 60; i++) {
    if (predicate()) return;
    await new Promise(resolve => setImmediate(resolve));
  }
  assert.fail('Expected original partial-run boundary was not reached');
}
const walk = node => !node || typeof node !== 'object' ? []
  : [node, ...[node.props?.children].flat(Infinity).flatMap(walk)];
const text = node => typeof node === 'string' ? node : Array.isArray(node)
  ? node.map(text).join('') : node?.props?.children ? text(node.props.children) : '';

function fixture(t, supplier, options = {}) {
  const api = load(path.join(renderer, 'services/api.ts'));
  api.setProjectContext(CONTEXT);
  const project = {language: 'ko', task: 'anomaly', viewMode: options.simple ? 'simple' : undefined, projectDir: '/workspace/a', project: {
    id: 'a', project_dir: '/workspace/a', active_labelset_id: 'default', source_dataset_dir: '/workspace/a/source',
  }};
  const hook = get => Object.assign(selector => selector ? selector(get()) : get(), {getState: get});
  const projectHook = hook(() => project);
  const actual = load(path.join(renderer, 'stores/useFlowchartStore.ts'), {
    '../services/api': api, '../services/flowDraft': {flowDraft: {}},
    './useProjectStore': {useProjectStore: projectHook},
    '../components/flowchart/flowchartStartup': {getFlowchartModelTask: () => null},
  });
  const store = actual.useFlowchartStore;
  const resetInput = () => store.setState({pipeline: structuredClone(GRAPH), selectedImage: {...IMAGE}});
  resetInput();
  const calls = [], originalFetch = global.fetch;
  global.fetch = async (url, options = {}) => {
    const row = {pathname: new URL(url).pathname, method: options.method || 'GET',
      body: options.body ? JSON.parse(options.body) : null, headers: new Headers(options.headers), signal: options.signal};
    calls.push(row); return supplier(row, calls);
  };
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', {configurable: true, value: {getItem: () => null}});
  t.after(() => {
    global.fetch = originalFetch;
    if (originalStorage) Object.defineProperty(globalThis, 'localStorage', originalStorage);
    else delete globalThis.localStorage;
  });
  const values = [], refs = []; let stateIndex = 0, refIndex = 0;
  const component = () => null, jsx = (type, props) => ({type, props: props || {}});
  const studio = load(path.join(__dirname, 'FlowchartStudio.tsx'), {
    react: {
      useState: initial => {const i = stateIndex++; if (!(i in values)) values[i] = typeof initial === 'function' ? initial() : initial;
        return [values[i], next => {values[i] = typeof next === 'function' ? next(values[i]) : next;}];},
      useRef: initial => {const i = refIndex++; return refs[i] || (refs[i] = {current: initial});},
      useEffect() {}, useLayoutEffect() {},
    },
    'react/jsx-runtime': {jsx, jsxs: jsx}, 'lucide-react': new Proxy({}, {get: () => component}),
    '../../stores/useFlowchartStore': {...actual, useFlowchartStore: hook(store.getState)},
    '../../stores/useProjectStore': {useProjectStore: projectHook},
    '../../stores/useDatasetStore': {useDatasetStore: hook(() => ({folderPath: '/workspace/a/source',
      datasetKey: '/workspace/a/source\0anomaly', hasSelectedFolder: true}))},
    '../../stores/useEvaluationStore': {useEvaluationStore: hook(() => ({}))},
    '../../stores/useTrainingStore': {useTrainingStore: hook(() => ({}))},
    '../../stores/useComputeStore': {useComputeStore: hook(() => ({profiles: [], selectedProfileId: null, isLoaded: true}))},
    '../../services/api': api,
    './flowchartViewport': {computeFlowchartViewport: () => ({scale: 1, contentWidth: 800, contentHeight: 240}), readableFlowScale: value => value},
    './flowchartStartup': {getFlowchartModelTask: () => null},
    './flowchartGraph': {nodeClassChoices: () => [], validateFlowchartGraph: () => null, locateFlowIssue: () => null,
      flowIssuesByTarget: () => ({nodes: new Map(), edges: new Map()})},
    './flowHandoff': {flowRecipeLabel: () => '검사'}, './flowRecipes': {FLOW_RECIPES: []},
    './FlowEditorWorkspace': {FlowEditorWorkspace: 'FlowEditorWorkspace', flowWorkspaceIdentity: () => ({
      exactSaved: false, evaluationCurrent: false, approval: '미조회', models: [], revision: 0, draft: '',
    })},
  }, () => new Proxy({}, {get: () => component})).FlowchartStudio;
  const render = () => {stateIndex = 0; refIndex = 0; return studio();};
  if(options.startAtTest!==false)walk(render()).find(node => node.type === 'FlowEditorWorkspace').props.onAreaChange('test');
  const buttons = () => {
    const panel = walk(render()).find(node => node.props.id === 'flow-panel-test');
    assert.equal(panel.props.hidden, false);
    return walk(panel).filter(node => node.type === 'button');
  };
  const button = label => {
    const found = buttons().find(node => text(node) === label);
    assert.ok(found, `Actual Studio must expose ${label}`); return found;
  };
  const unknown = async () => {
    assert.equal(await store.getState().runPipeline(undefined, undefined, CPU), false);
    assert.equal(store.getState().partialRun.phase, 'unknown');
    assert.equal(store.getState().isRunning, true);
    assert.equal(store.getState().executionResult, null);
  };
  return {api, project, store, calls, resetInput, unknown, button, buttons, render};
}
const cancels = calls => calls.filter(row => row.pathname.endsWith('/cancel'));
const reads = calls => calls.filter(row => row.method === 'GET');

test('guided re-entry into an uncertain running flow keeps explicit status and stop controls reachable',async t=>{
 const h=fixture(t,row=>row.pathname.endsWith('/prepare')?response(snapshot())
  :row.pathname.endsWith('/run')?response({detail:'Controlled original Run503'},503):response(snapshot()),{simple:true,startAtTest:false});
 await h.unknown();const before=h.calls.length;
 assert.equal(walk(h.render()).find(node=>node.props.id==='flow-panel-test').props.hidden,true);
 const view=walk(h.render()).find(node=>node.type==='button'&&text(node)==='진행 중 검사 상태 확인');
 assert.ok(view,'read-only run navigation remains available even while preparation is locked');
 assert.equal(view.props.disabled,false);view.props.onClick();
 assert.equal(h.calls.length,before,'opening status never cancels or restarts a job');
 assert.equal(h.button('중단 상태 확인').props.disabled,false);
 assert.equal(h.button('부분 실행 중단').props.disabled,false);
 assert.equal(cancels(h.calls).length,0);
});

test('unknown prepared run keeps read-only refresh and explicit cancel waits for matching terminal receipt', async t => {
  const terminal = deferred(); let cancelled = false;
  const h = fixture(t, row => {
    if (row.pathname.endsWith('/prepare')) return response(snapshot());
    if (row.pathname.endsWith('/run')) return response({detail: 'Controlled original Run503'}, 503);
    if (row.pathname.endsWith('/cancel')) {cancelled = true; return response(snapshot(ID, 'cancelled', true));}
    return cancelled ? terminal.promise : response(snapshot());
  });
  await h.unknown();
  assert.equal(cancels(h.calls).length, 0, 'Run transport failure cannot imply stop intent');
  const count = h.calls.length;
  h.button('중단 상태 확인').props.onClick();
  await until(() => reads(h.calls).length === 2 && h.store.getState().partialRun.phase === 'unknown');
  assert.ok(h.calls.slice(count).every(row => row.method === 'GET'));
  const stop = h.button('부분 실행 중단'); assert.equal(stop.props.disabled, false); stop.props.onClick();
  await until(() => reads(h.calls).length === 3);
  assert.equal(cancels(h.calls).length, 1);
  assert.equal(cancels(h.calls)[0].pathname, `/api/flowchart/partial-runs/${ID}/cancel`);
  assert.equal(cancels(h.calls)[0].method, 'POST');
  assert.equal(cancels(h.calls)[0].headers.get('X-Vision-Project'), 'a');
  assert.deepEqual(JSON.parse(cancels(h.calls)[0].headers.get('X-Vision-Context')), CONTEXT);
  assert.equal(h.store.getState().isRunning, true, 'Even a cancelled ACK is not the terminal read');
  assert.equal(h.store.getState().executionResult, null);
  terminal.resolve(response(snapshot(ID, 'cancelled', true)));
  await until(() => h.store.getState().isRunning === false);
  assert.equal(h.store.getState().partialRun.phase, 'cancelled');
  assert.equal(h.store.getState().executionResult, null);
  assert.equal(h.calls.filter(row => row.pathname.endsWith('/run')).length, 1);
  assert.ok(h.calls.every(row => !row.signal), 'Cooperative cancellation never aborts the original transport');
});

test('unknown cancel cannot clear busy from a foreign terminal receipt and refresh can reconcile the exact owner', async t => {
  let cancelled = false, correct = false;
  const h = fixture(t, row => {
    if (row.pathname.endsWith('/prepare')) return response(snapshot());
    if (row.pathname.endsWith('/run')) return response({detail: 'Controlled original Run503'}, 503);
    if (row.pathname.endsWith('/cancel')) {cancelled = true; return response(snapshot(ID, 'cancelling'));}
    return response(cancelled ? snapshot(correct ? ID : NEXT_ID, 'cancelled', true) : snapshot());
  });
  await h.unknown(); h.button('부분 실행 중단').props.onClick();
  await until(() => cancels(h.calls).length === 1 && h.store.getState().partialRun.phase === 'unknown');
  assert.equal(h.store.getState().isRunning, true); assert.equal(h.store.getState().executionResult, null);
  assert.equal(h.button('중단 요청됨').props.disabled, true);
  correct = true; h.button('중단 상태 확인').props.onClick();
  await until(() => h.store.getState().isRunning === false);
  assert.equal(h.store.getState().partialRun.phase, 'cancelled'); assert.equal(cancels(h.calls).length, 1);
});

test('old rendered unknown cancel cannot stop a later partial owner', async t => {
  let issued = ID, cancelled = false;
  const h = fixture(t, row => {
    if (row.pathname.endsWith('/prepare')) return response(snapshot(issued));
    if (row.pathname.endsWith('/run')) return response({detail: 'Controlled original Run503'}, 503);
    if (row.pathname.endsWith('/cancel')) {cancelled = true; return response(snapshot(issued, 'cancelled', true));}
    return response(cancelled ? snapshot(issued, 'cancelled', true) : snapshot(issued));
  });
  await h.unknown(); const previous = h.button('부분 실행 중단');
  h.store.getState().invalidateForDataChange(); h.resetInput(); issued = NEXT_ID;
  await h.unknown(); const current = h.store.getState().partialRun;
  previous.props.onClick(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(cancels(h.calls).length, 0); assert.equal(h.store.getState().partialRun, current);
  h.button('부분 실행 중단').props.onClick(); await until(() => !h.store.getState().isRunning);
  assert.equal(cancels(h.calls)[0].pathname, `/api/flowchart/partial-runs/${NEXT_ID}/cancel`);
});

for (const change of ['project', 'context ABA', 'transport']) test(`unknown cancel refuses a changed ${change}`, async t => {
  const h = fixture(t, row => response(row.pathname.endsWith('/run') ? {detail: 'Controlled original Run503'} : snapshot(),
    row.pathname.endsWith('/run') ? 503 : 200));
  await h.unknown(); const stop = h.button('부분 실행 중단'), state = h.store.getState().partialRun;
  if (change === 'project') h.project.project.id = 'foreign';
  if (change === 'context ABA') {h.api.setProjectContext({...CONTEXT, actor_id: 'other'}); h.api.setProjectContext(CONTEXT);}
  if (change === 'transport') h.api.setSharedApiBase('http://127.0.0.1:9876');
  stop.props.onClick(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(cancels(h.calls).length, 0); assert.equal(h.store.getState().partialRun, state);
  assert.equal(h.store.getState().isRunning, true);
});
