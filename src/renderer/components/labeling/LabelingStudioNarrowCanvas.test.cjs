const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

// UI finish: in 모두 보기 the helper panels could leave the labeling canvas a few pixels tall (1440x900, classification).
// The actual LabelingStudio is rendered one level deep (child panels are stubs, so the panels' own layout is not under
// test) with controlled hooks, stores, storage and ResizeObserver: the 집중 편집 row says the drawing area is short and
// highlights the switch, and nothing folds the panels for the viewer.
const directory = __dirname;
const transpile = (file, jsx) => ts.transpileModule(fs.readFileSync(file, 'utf8'), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, ...(jsx ? {jsx: ts.JsxEmit.ReactJSX} : {})},
}).outputText;
function compile(file, mocks, jsx) {
  const m = new Module(file, module);
  m.filename = file;
  m.paths = Module._nodeModulePaths(path.dirname(file));
  const original = m.require.bind(m);
  m.require = name => Object.hasOwn(mocks, name) ? mocks[name] : original(name);
  m._compile(transpile(file, jsx), file);
  return m.exports;
}
const layout = compile(path.join(directory, 'labelingLayout.ts'), {}, false);

// Every child panel is a named stub: the tree keeps it as an element and never renders it.
const stubs = new Proxy({}, {get: (_, name) => name === '__esModule' ? true : Object.assign(() => null, {displayName: String(name)})});
const childModules = ['./LabelingToolbar', './CategorySelector', './ClassificationClassPicker', './LabelingCanvas', './MaskLayerControls',
  './AnnotationList', './ImageFilmstrip', './ModelAssistPanel', './LabelSetBar', './ImageReviewPanel', './DerivedImagePanel',
  './SavedReviewQueuePanel', './DicomPanel', './TeamDataPanel', './AnnotationDraftRecovery', './EvidenceEditReturn',
  '../common/WorkflowImpactPanel', '../common/SpecialistFamilyNote'];

function fixture({viewMode = 'full', stored = null, resizeObserver = true, height = 4} = {}) {
  const previous = Object.fromEntries(['localStorage', 'ResizeObserver'].map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  const slots = [], pendingEffects = [], writes = [], observers = [];
  let cursor = 0, dirty = false, renders = 0, tree = null;
  const rect = {width: 1120, height};
  const same = (a, b) => a?.length === b?.length && a.every((value, index) => Object.is(value, b[index]));
  const Fragment = Symbol('Fragment');
  const react = {
    Fragment,
    useRef(value) { const i = cursor++; return slots[i] || (slots[i] = {current: value}); },
    useId() { const i = cursor++; return slots[i] || (slots[i] = `:r${i}:`); },
    useState(value) {
      const i = cursor++;
      if (!(i in slots)) slots[i] = typeof value === 'function' ? value() : value;
      return [slots[i], next => { const value = typeof next === 'function' ? next(slots[i]) : next; if (!Object.is(value, slots[i])) { slots[i] = value; dirty = true; } }];
    },
    useEffect(fn, deps) {
      const i = cursor++;
      if (!same(slots[i]?.deps, deps)) {
        const old = slots[i];
        slots[i] = {deps};
        pendingEffects.push({old, install: () => { slots[i].cleanup = fn(); }});
      }
    },
  };
  react.default = react;
  const element = (type, props) => ({type, props: props || {}});
  const images = [];
  const annotation = Object.assign(() => annotationState, {getState: () => annotationState});
  const annotationState = {
    images, syncDatasetImages: async () => {}, task: 'classification', setTask() {}, currentImage: null,
    annotationLoadStatus: 'idle', annotationLoadError: null, loadAnnotationsForCurrent: async () => {},
    autoSelectError: null, clearAutoSelectError() {}, externalSelectionPath: null,
  };
  const mocks = {
    react, 'react/jsx-runtime': {jsx: element, jsxs: element, Fragment},
    '../../stores/useDatasetStore': {useDatasetStore: () => ({images})},
    '../../stores/useAnnotationStore': {useAnnotationStore: annotation},
    '../../stores/useProjectStore': {useProjectStore: () => ({task: 'classification', isProjectBusy: false, viewMode})},
    './labelingLayout': layout,
    ...Object.fromEntries(childModules.map(name => [name, stubs])),
  };
  const values = new Map(stored === null ? [] : [['mv.labeling.focus', stored]]);
  Object.defineProperty(globalThis, 'localStorage', {configurable: true, writable: true, value: {
    getItem: key => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => { writes.push([key, value]); values.set(key, value); },
  }});
  class ControlledResizeObserver {
    constructor(callback) { this.callback = callback; this.targets = []; this.disconnected = false; observers.push(this); }
    observe(target) { this.targets.push(target); }
    disconnect() { this.disconnected = true; }
  }
  Object.defineProperty(globalThis, 'ResizeObserver', {configurable: true, writable: true, value: resizeObserver ? ControlledResizeObserver : undefined});
  const {LabelingStudio} = compile(path.join(directory, 'LabelingStudio.tsx'), mocks, true);

  const walk = (node, visit) => {
    if (Array.isArray(node)) { node.forEach(child => walk(child, visit)); return; }
    if (!node || typeof node !== 'object') return;
    visit(node);
    walk(node.props.children, visit);
  };
  // What React does after a commit: host refs point at their element, then effects run.
  const attachRefs = () => walk(tree, node => {
    if (typeof node.type === 'string' && node.props.ref && !node.props.ref.current) {
      node.props.ref.current = {getBoundingClientRect: () => ({...rect})};
    }
  });
  const flush = () => {
    for (let pass = 0; pass < 10; pass += 1) {
      dirty = false; cursor = 0; renders += 1;
      tree = LabelingStudio();
      attachRefs();
      for (const effect of pendingEffects.splice(0)) { effect.old?.cleanup?.(); effect.install(); }
      if (!dirty) return;
    }
    throw new Error('LabelingStudio did not settle');
  };
  const find = predicate => { const found = []; walk(tree, node => { if (predicate(node)) found.push(node); }); return found; };
  const text = node => Array.isArray(node) ? node.map(text).join('') : node && typeof node === 'object' ? text(node.props.children) : node == null || node === false ? '' : String(node);
  return {
    flush, rect, writes, observers,
    renders: () => renders,
    resize(next) { rect.height = next; for (const observer of observers) if (!observer.disconnected) observer.callback([]); if (dirty) flush(); },
    unmount() { for (const slot of slots) slot?.cleanup?.(); },
    switchButton: () => { const [button] = find(node => node.type === 'button' && 'aria-pressed' in node.props); return button; },
    notices: () => find(node => node.props.role === 'status'),
    workArea: () => find(node => 'data-labeling-work-area' in node.props)[0],
    // The helper panels in the tree, in screen order (focus editing removes them).
    panels: () => find(node => /Panel$/.test(node.type?.displayName ?? '') && !['TeamDataPanel', 'DicomPanel'].includes(node.type.displayName))
      .map(node => node.type.displayName),
    text,
    restore() {
      for (const [name, descriptor] of Object.entries(previous)) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor); else delete globalThis[name];
      }
    },
  };
}

test('in 모두 보기 a short work area gets the notice beside 집중 편집 and a highlighted switch, and the panels stay open', () => {
  const f = fixture({height: 4});
  try {
    f.flush();
    const [notice] = f.notices();
    assert.equal(f.notices().length, 1);
    assert.equal(f.text(notice), layout.NARROW_CANVAS_NOTICE);
    const button = f.switchButton();
    // The switch keeps its one name (e2e specs find it by that exact name) and only gains a description.
    assert.equal(f.text(button), '집중 편집');
    assert.equal(button.props['aria-pressed'], false);
    assert.equal(button.props['aria-describedby'], notice.props.id);
    assert.match(button.props.className, /border-amber-400/);
    assert.match(notice.props.className, /\btruncate\b/, 'one line: the row never takes height from the canvas');
    // The observer watches the work area; nothing folded the panels or stored a choice for the viewer.
    assert.equal(f.observers.length, 1);
    assert.deepEqual(f.observers[0].targets, [f.workArea().props.ref.current]);
    assert.deepEqual(f.writes, []);
    assert.deepEqual(f.panels(), ['ImageReviewPanel', 'DerivedImagePanel', 'SavedReviewQueuePanel', 'WorkflowImpactPanel', 'ModelAssistPanel']);
  } finally { f.unmount(); f.restore(); }
});

test('the notice follows the work area: gone once it has room, back when it is short again; a roomy resize is free', () => {
  const f = fixture({height: 120});
  try {
    f.flush();
    assert.equal(f.notices().length, 1);
    f.resize(600);
    assert.equal(f.notices().length, 0);
    assert.doesNotMatch(f.switchButton().props.className, /amber/);
    assert.equal(f.switchButton().props['aria-describedby'], undefined);
    const before = f.renders();
    f.resize(820);
    f.resize(layout.NARROW_CANVAS_HEIGHT);
    assert.equal(f.renders(), before, 'heights with room are one state: resizing a roomy window renders nothing');
    f.resize(layout.NARROW_CANVAS_HEIGHT - 1);
    assert.equal(f.notices().length, 1);
    f.unmount();
    assert.equal(f.observers[0].disconnected, true);
  } finally { f.restore(); }
});

test('no notice while focus editing is on (pressed, or the simple view before any choice), nor without ResizeObserver', () => {
  for (const [label, options, panels] of [
    ['pressed 집중 편집', {stored: '1'}, 0],
    ['simple view, never pressed', {viewMode: 'simple'}, 0],
    ['no ResizeObserver', {resizeObserver: false}, 5],
  ]) {
    const f = fixture({height: 4, ...options});
    try {
      f.flush();
      assert.equal(f.notices().length, 0, label);
      assert.equal(f.panels().length, panels, label);
      assert.equal(f.switchButton().props['aria-describedby'], undefined, label);
      assert.doesNotMatch(f.switchButton().props.className, /amber/, label);
      assert.deepEqual(f.writes, [], label);
    } finally { f.unmount(); f.restore(); }
  }
});
