const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

// Actual component + actual request-building service; React effects/state and
// the transport boundary are modeled. No HTTP, workers or models are executed.
function load(file, mocks = {}) {
  const name = path.resolve(__dirname, file), m = new Module(name, module);
  m.filename = name; m.paths = Module._nodeModulePaths(path.dirname(name));
  const original = m.require.bind(m);
  m.require = ref => {
    if (ref in mocks) return mocks[ref];
    const relative = ref.startsWith('.') ? path.resolve(path.dirname(name), ref + '.ts') : null;
    return relative && fs.existsSync(relative) ? load(relative, mocks) : original(ref);
  };
  m._compile(ts.transpileModule(fs.readFileSync(name, 'utf8'), {fileName: name,
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX}}).outputText, name);
  return m.exports;
}

const jsx = (type, props) => ({type, props: props || {}});
const nodes = tree => [tree, ...[tree?.props?.children].flat(Infinity).filter(x => x && typeof x === 'object').flatMap(nodes)];
const tick = () => new Promise(setImmediate);
const SOURCE = '/original region labels';
const A = '/project/dataset/patch/prepared-A';
const B = '/project/dataset/patch/prepared-B';

function fixture(reply = async () => ({parents: []})) {
  let cursor = 0; const slots = [], pending = [], calls = [], errors = [];
  const react = {
    useState(initial) {
      const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
      return [slots[i], value => {slots[i] = typeof value === 'function' ? value(slots[i]) : value;}];
    },
    useEffect(fn, deps) {
      const i = cursor++;
      if (!slots[i] || deps.some((value, j) => value !== slots[i].deps[j])) {
        slots[i]?.cleanup?.(); slots[i] = {deps}; pending.push(() => {slots[i].cleanup = fn();});
      }
    },
  };
  const state = {scope: 'owning-project', source: SOURCE, dataset: {dataset_path: A}, datasets: [], models: [],
    modelId: '', job: null, active: false, busy: '', isCurrent: () => true, setError: error => errors.push(error)};
  const api = {request: async route => {const url = new URL(route, 'http://owned.invalid'); calls.push(url); return reply(url);}};
  const actualService = load('../../services/modelTrainingProgram.ts', {'./api': api, './modelExecution': {}});
  const settings = load('TrainingSchedulingSettings.tsx', {'react/jsx-runtime': {jsx, jsxs: jsx}});
  const component = load('PatchClassificationWorkbench.tsx', {
    react, 'react/jsx-runtime': {jsx, jsxs: jsx}, 'lucide-react': {},
    './ProgramWorkbenchControls': {}, './useProgramWorkbench': {useProgramWorkbench: () => state},
    './TrainingSchedulingSettings': settings, '../../stores/useComputeStore': {useComputeStore: () => ({selectedProfileId: null})},
    '../../services/modelTrainingProgram': actualService, './JobProgressView': {}, './AutoDLWorkbench': {},
    './TrainingPreparationPanel': {}, './PatchRecipePreview': {}, './ModelExecutionEvidence': {},
  });
  const render = () => {cursor = 0; const tree = nodes(component.PatchClassificationWorkbench()); pending.splice(0).forEach(run => run()); return tree;};
  const assertQueries = expected => assert.deepEqual(calls.map(url => ({path: url.pathname, query: Object.fromEntries(url.searchParams)})),
    expected.map(dataset_path => ({path: '/api/training/warm-start-parents', query: {dataset_path, task: 'patch_classification', preset: 'fast', backbone: 'dinov3_vits16'}})));
  return {state, render, calls, errors, assertQueries};
}

test('patch parent lookup binds the selected prepared manifest rather than original region labels', async () => {
  const f = fixture(); f.render(); await tick(); f.assertQueries([A]); assert.deepEqual(f.errors, []);
});

test('patch parent lookup does not request classes before prepared data exists', async () => {
  const f = fixture(); f.state.dataset = null; f.render(); await tick(); f.assertQueries([]); assert.deepEqual(f.errors, []);
});

test('patch parent lookup follows a changed prepared dataset and rejects the old late result', async () => {
  let finishA; const f = fixture(url => url.searchParams.get('dataset_path') === A
    ? new Promise(resolve => {finishA = resolve;}) : Promise.resolve({parents: [{job_id: 'parent-B', checkpoint_sha256: 'b'.repeat(64)}]}));
  f.render(); f.state.dataset = {dataset_path: B}; f.render(); await tick();
  f.assertQueries([A, B]); finishA({parents: [{job_id: 'late-parent-A', checkpoint_sha256: 'a'.repeat(64)}]}); await tick();
  const options = f.render().filter(node => node.type === 'option').map(node => node.props.value);
  assert.ok(options.includes('parent-B')); assert.ok(!options.includes('late-parent-A')); assert.deepEqual(f.errors, []);
});

test('a genuine parent service refusal remains visible for the current prepared dataset', async () => {
  const error = new Error('Current prepared data cannot be read');
  const f = fixture(async () => {throw error;}); f.render(); await tick();
  f.assertQueries([A]); assert.deepEqual(f.errors, [error.message]);
});
