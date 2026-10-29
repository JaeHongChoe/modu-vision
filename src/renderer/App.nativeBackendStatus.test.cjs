const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

const STARTING = {port: null, healthy: false, pid: 35040};
const HEALTHY = {port: 63168, healthy: true, pid: 35040, device: 'cpu', deviceName: 'CPU'};
const STOPPED = {port: null, healthy: false, pid: null};
const RESTARTED = {port: 63169, healthy: true, pid: 35041, device: 'cpu', deviceName: 'CPU'};
const settle = () => new Promise(resolve => setImmediate(resolve));

// Load the whole production App module and render its real private StudioApp through
// the element returned by App. Only host transport, stores and unrelated presentation
// dependencies are controlled; no production effect/function is copied or extracted.
function fixture(t) {
  const oldWindow = global.window;
  const oldDocument = global.document;
  const effects = [], statuses = [], errors = [], pending = [];
  const statusListeners = new Set(), crashListeners = new Set();
  let telemetrySubscriptions = 0, connects = 0, disconnects = 0;
  const store = value => Object.assign(selector => selector ? selector(value) : value, {getState: () => value});
  const project = {language: 'ko', activeStep: 1, viewMode: 'simple', theme: 'dark', project: null,
    backendStatus: {...STOPPED}, setBackendStatus: value => {project.backendStatus = {...value}; statuses.push({...value});},
    showError: value => errors.push(value), syncCurrentProject: async () => {}};
  const react = {useEffect: effect => effects.push(effect), useLayoutEffect: effect => effects.push(effect),
    useState: value => [typeof value === 'function' ? value() : value, () => {}],
    useRef: current => ({current})};
  global.window = {api: {
    getBackendStatus: () => new Promise((resolve, reject) => pending.push({resolve, reject})),
    onBackendStatusChange: callback => {statusListeners.add(callback); return () => statusListeners.delete(callback);},
    onBackendCrashed: callback => {crashListeners.add(callback); return () => crashListeners.delete(callback);},
  }};
  global.document = {documentElement: {dataset: {}, style: {}, classList: {toggle() {}}}};
  t.after(() => {
    global.window = oldWindow;
    global.document = oldDocument;
  });
  const mocks = {
    react,
    './stores/useProjectStore': {useProjectStore: store(project), resolveTheme: () => 'dark'},
    './stores/useTrainingStore': {useTrainingStore: store({isCurrentData: false, status: 'idle', updateFromTelemetry() {}})},
    './stores/useComputeStore': {useComputeStore: store({transportRevision: 0})},
    './stores/useDatasetStore': {useDatasetStore: store({sourceImages: 0, totalImages: 0, unlabeledImages: 0})},
    './stores/useEvaluationStore': {useEvaluationStore: store({jobId: null, testPredictions: []})},
    './stores/useFlowchartStore': {useFlowchartStore: store({baseVersionId: null})},
    './stores/useAssistantStore': {useAssistantStore: store({isOpen: false})},
    './components/onboarding/firstRunStore': {useFirstRunStore: store({onboarding: null, open: false, setOpen() {}, dismiss() {}})},
    './components/onboarding/firstRun': {shouldOpenFirstRun: () => false, EXAMPLE_NOTICE: 'synthetic fixture'},
    './components/wizard/stepProgress': {completedSteps: () => [], mergeStepProgress: () => []},
    './stores/workflowProgress': {subscribeWorkflowProgress: () => () => {}, workflowProgressReport: () => null},
    './services/api': {request: () => {throw new Error('No backend requests in this App lifecycle fixture');}},
    './services/browserHealth': {needsBrowserHealthProbe: () => {throw new Error('Native lifecycle must not use the browser health probe');}},
    './services/jobEventFeed': {createJobEventStream: () => ({}), createCatchUp: () => () => {}},
    './services/websocket': {telemetryService: {connect: () => {connects++;}, disconnect: () => {disconnects++;},
      subscribe: () => {telemetrySubscriptions++; return () => {telemetrySubscriptions--;};}}},
  };
  const file = path.join(__dirname, 'App.tsx'), loaded = new Module(file, module);
  loaded.filename = file; loaded.paths = Module._nodeModulePaths(__dirname);
  const originalRequire = loaded.require.bind(loaded);
  loaded.require = name => {
    if (Object.hasOwn(mocks, name)) return mocks[name];
    if (name.startsWith('./components/')) return new Proxy({}, {get: (_target, key) => key === '__esModule' ? true : () => null});
    return originalRequire(name);
  };
  loaded._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  }}).outputText, file);
  const root = loaded.exports.default();
  root.props.children.type(root.props.children.props);
  const cleanups = effects.map(effect => effect()).filter(value => typeof value === 'function');
  const dispose = () => cleanups.splice(0).reverse().forEach(cleanup => cleanup());
  t.after(dispose);
  return {pending, statuses, errors, project, dispose,
    emitStatus: value => {for (const callback of statusListeners) callback(value);},
    emitCrash: value => {for (const callback of crashListeners) callback(value);},
    telemetry: () => ({connects, disconnects, subscriptions: telemetrySubscriptions}),
  };
}

test('actual App ignores an initial STARTING snapshot delivered after a HEALTHY event', async t => {
  const app = fixture(t);
  assert.equal(app.pending.length, 1);
  app.emitStatus(HEALTHY);
  app.pending[0].resolve(STARTING);
  await settle();
  assert.deepEqual(app.project.backendStatus, HEALTHY);
  assert.deepEqual(app.statuses, [HEALTHY], 'the obsolete initial response must not clear the current port');
});

test('actual App ignores its initial response after effect disposal', async t => {
  const app = fixture(t);
  app.dispose();
  app.pending[0].resolve(HEALTHY);
  await settle();
  assert.deepEqual(app.statuses, []);
  assert.deepEqual(app.project.backendStatus, STOPPED);
  assert.deepEqual(app.telemetry(), {connects: 1, disconnects: 1, subscriptions: 0});
});

test('actual App does not revive an obsolete healthy snapshot after a stopped event', async t => {
  const app = fixture(t);
  app.emitStatus(STOPPED);
  app.pending[0].resolve(HEALTHY);
  await settle();
  assert.deepEqual(app.statuses, [STOPPED]);
  assert.deepEqual(app.project.backendStatus, STOPPED);
});

test('actual App keeps genuine crash and successor PID/port events after its initial snapshot', async t => {
  const app = fixture(t);
  app.pending[0].resolve(HEALTHY);
  await settle();
  app.emitStatus(STOPPED);
  app.emitCrash({message: 'controlled native backend exited'});
  app.emitStatus(RESTARTED);
  assert.deepEqual(app.statuses, [HEALTHY, STOPPED, RESTARTED]);
  assert.deepEqual(app.project.backendStatus, RESTARTED);
  assert.equal(app.errors.length, 1);
  assert.equal(app.errors[0].code, 'ERR_001');
  assert.equal(app.errors[0].description_en, 'controlled native backend exited');
});

test('actual App keeps an initial status rejection visible and waits for genuine recovery', async t => {
  const logged = [];
  t.mock.method(console, 'error', error => logged.push(error));
  const app = fixture(t), refusal = new Error('controlled status IPC refusal');
  app.pending[0].reject(refusal);
  await settle();
  assert.deepEqual(logged, [refusal]);
  assert.deepEqual(app.statuses, []);
  assert.deepEqual(app.project.backendStatus, STOPPED);
  app.emitStatus(HEALTHY);
  assert.deepEqual(app.project.backendStatus, HEALTHY);
});
