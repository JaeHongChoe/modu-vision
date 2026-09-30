/** Render the real label-set bar with deferred requests across project changes. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');

const source = path.resolve(__dirname, '../src/renderer/components/labeling/LabelSetBar.tsx');
const compiled = ts.transpileModule(fs.readFileSync(source, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;

function harness() {
  const hooks = [];
  let cursor = 0;
  const effects = [];
  const requests = [];
  const projectA = { id: 'a', project_dir: '/tmp/labelset-a', active_labelset_id: 'default' };
  const projectB = { id: 'b', project_dir: '/tmp/labelset-b', active_labelset_id: 'default' };
  const state = { project: projectA, isProjectBusy: false, projectError: null };
  const useProjectStore = (selector) => selector(state);
  useProjectStore.getState = () => state;
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!(index in hooks)) hooks[index] = typeof initial === 'function' ? initial() : initial;
      return [hooks[index], (value) => { hooks[index] = typeof value === 'function' ? value(hooks[index]) : value; }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!(index in hooks)) hooks[index] = { current: initial };
      return hooks[index];
    },
    useEffect(effect, deps) {
      const index = cursor++;
      const previous = hooks[index];
      if (!previous || deps.some((value, i) => value !== previous.deps[i])) {
        effects.push(() => {
          previous?.cleanup?.();
          hooks[index] = { deps, cleanup: effect() };
        });
      }
    },
  };
  const moduleUnderTest = new Module(source, module);
  moduleUnderTest.filename = source;
  moduleUnderTest.paths = Module._nodeModulePaths(path.dirname(source));
  const originalRequire = moduleUnderTest.require.bind(moduleUnderTest);
  moduleUnderTest.require = (name) => {
    if (name === 'react') return react;
    if (name === 'lucide-react') return { CopyPlus() {}, Layers3() {}, RefreshCw() {} };
    if (name === '../../stores/useProjectStore') return { useProjectStore };
    if (name === '../../services/api') return { api: { project: { listLabelsets() {
      return new Promise((resolve, reject) => { requests.push({ resolve, reject }); });
    } } } };
    return originalRequire(name);
  };
  moduleUnderTest._compile(compiled, source);
  const render = () => {
    cursor = 0;
    const tree = moduleUnderTest.exports.LabelSetBar();
    while (effects.length) effects.shift()();
    return tree;
  };
  return { render, requests, switchProject() { state.project = projectB; render(); } };
}

function find(tree, predicate) {
  if (Array.isArray(tree)) return tree.flatMap((child) => find(child, predicate));
  if (!tree || typeof tree !== 'object') return [];
  return [...(predicate(tree) ? [tree] : []), ...find(tree.props?.children, predicate)];
}
const response = (name) => ({ labelsets: [{ id: 'default', name }] });
const optionNames = (tree) => find(tree, (node) => node.type === 'option').map((node) => node.props.children);
const flush = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); };

test('old project label-set response cannot replace the new project list', async () => {
  const ui = harness();
  ui.render();
  ui.switchProject();
  ui.requests[1].resolve(response('Project B labels'));
  await flush();
  assert.deepEqual(optionNames(ui.render()), ['Project B labels']);
  ui.requests[0].resolve(response('Project A labels'));
  await flush();
  assert.deepEqual(optionNames(ui.render()), ['Project B labels']);
});

test('old project failure cannot show an error in the current project', async () => {
  const ui = harness();
  ui.render();
  ui.switchProject();
  ui.requests[1].resolve(response('Project B labels'));
  await flush();
  ui.requests[0].reject(new Error('Old project unavailable'));
  await flush();
  assert.equal(find(ui.render(), (node) => node.props?.role === 'alert').length, 0);
});

test('old project completion does not unlock the current loading selector', async () => {
  const ui = harness();
  ui.render();
  ui.switchProject();
  ui.requests[0].resolve(response('Project A labels'));
  await flush();
  assert.equal(find(ui.render(), (node) => node.type === 'select')[0].props.disabled, true);
  ui.requests[1].resolve(response('Project B labels'));
  await flush();
  assert.equal(find(ui.render(), (node) => node.type === 'select')[0].props.disabled, false);
});
