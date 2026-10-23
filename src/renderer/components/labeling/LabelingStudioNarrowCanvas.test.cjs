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
  './AnnotationList', './ImageFilmstrip', './ModelAssistPanel', './DraftReviewQueue', './LabelSetBar', './ImageReviewPanel', './DerivedImagePanel',
  './SavedReviewQueuePanel', './DicomPanel', './TeamDataPanel', './AnnotationDraftRecovery', './EvidenceEditReturn',
  '../common/WorkflowImpactPanel', '../common/SpecialistFamilyNote'];

function fixture({viewMode = 'full', stored = null, resizeObserver = true, height = 4, task = 'classification'} = {}) {
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
    '../../stores/useProjectStore': {useProjectStore: () => ({task, isProjectBusy: false, viewMode})},
    '../../stores/useDraftReviewStore': {useDraftReviewStore: select => select({assistRequest: null})},
    './labelingLayout': layout,
    '../../stores/assistContext': {reportPanelError() {}},
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
    tree: () => tree,
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
    // The settings strip's panels come first, then the rows whose popovers open over the canvas.
    assert.deepEqual(f.panels(), ['DerivedImagePanel', 'SavedReviewQueuePanel', 'WorkflowImpactPanel', 'ImageReviewPanel', 'ModelAssistPanel']);
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

// 모두 보기 at 1440x900 left the drawing area 67 px under thirteen stacked rows. The settings rows (label set, derived
// images, saved queue, workflow impact, the specialist note) now scroll in their own strip that gives way first, so the
// work area keeps WORK_AREA_BASIS; every panel stays open in 모두 보기. The team row (the shared editing lock), the rows
// whose popovers open over the canvas, the canvas itself and the sidebar sit outside any scroll box, so the lock stays
// in sight, the popovers are not clipped and wheel zoom, keys and the sidebar's own scrolling behave as before.
const structure = tree => {
  const rows = [];
  const visit = (node, parents) => {
    if (Array.isArray(node)) { node.forEach(child => visit(child, parents)); return; }
    if (!node || typeof node !== 'object') return;
    rows.push({node, parents});
    visit(node.props.children, [...parents, node]);
  };
  visit(tree, []);
  const named = name => rows.filter(row => row.node.type?.displayName === name);
  const scrolls = row => row.parents.filter(parent => /\boverflow-(y-)?(auto|scroll)\b/.test(parent.props.className ?? ''));
  // The nearest host element around a row (the folded panels sit in a keyed Fragment).
  const host = row => [...row.parents].reverse().find(parent => typeof parent.type === 'string');
  return {rows, named, scrolls, host};
};

test('in 모두 보기 the settings rows scroll in a strip that gives way first, and the canvas keeps its basis', () => {
  const f = fixture({height: 600});
  try {
    f.flush();
    const {rows, named, scrolls, host} = structure(f.tree());
    const [strip] = rows.filter(row => 'data-labeling-panels' in row.node.props);
    assert.equal(strip.node.props.className, 'min-h-10 flex-[0_100_auto] overflow-y-auto overscroll-y-contain');
    const inStrip = rows.filter(row => row.parents.includes(strip.node) && row.node.type?.displayName).map(row => row.node.type.displayName);
    assert.deepEqual(inStrip, ['LabelSetBar', 'DerivedImagePanel', 'SavedReviewQueuePanel', 'WorkflowImpactPanel']);
    for (const name of ['TeamDataPanel', 'ImageReviewPanel', 'DraftReviewQueue', 'ModelAssistPanel', 'CategorySelector', 'LabelingCanvas', 'AnnotationList', 'ImageFilmstrip']) {
      const [row] = named(name);
      assert.ok(row, name);
      assert.deepEqual(scrolls(row), [], `${name} is outside any scroll box`);
    }
    for (const name of ['TeamDataPanel', 'ImageReviewPanel', 'DraftReviewQueue', 'ModelAssistPanel', 'CategorySelector']) {
      assert.equal(host(named(name)[0]).props.className, 'shrink-0', `${name} sits in the group that never shrinks`);
    }
    assert.equal(host(named('ImageFilmstrip')[0]).props.className, 'shrink-0');
    assert.equal(f.workArea().props.className, `flex-[1_1_${layout.WORK_AREA_BASIS}px] flex overflow-hidden relative`);
    assert.ok(named('LabelingCanvas')[0].parents.includes(f.workArea()) && named('AnnotationList')[0].parents.includes(f.workArea()));
    assert.ok(layout.WORK_AREA_BASIS > layout.NARROW_CANVAS_HEIGHT, 'the basis clears the narrow notice');
    const spec = fs.readFileSync(path.resolve(directory, '../../../../scripts/e2e/service-labeling-focus.spec.ts'), 'utf8');
    assert.match(spec, new RegExp(`const WORK_AREA_BASIS = ${layout.WORK_AREA_BASIS};`), 'the browser spec measures against the same basis');
  } finally { f.unmount(); f.restore(); }
});

test('focus editing keeps the label set in the strip and the team row in sight, and folds the rest', () => {
  const f = fixture({height: 600, stored: '1'});
  try {
    f.flush();
    const {rows, named, scrolls} = structure(f.tree());
    const [strip] = rows.filter(row => 'data-labeling-panels' in row.node.props);
    const inStrip = rows.filter(row => row.parents.includes(strip.node) && row.node.type?.displayName).map(row => row.node.type.displayName);
    assert.deepEqual(inStrip, ['LabelSetBar']);
    assert.deepEqual(scrolls(named('TeamDataPanel')[0]), []);
  } finally { f.unmount(); f.restore(); }
});

test('in a detection project the specialist note sits at the end of the strip', () => {
  const f = fixture({height: 600, task: 'detection'});
  try {
    f.flush();
    const {rows} = structure(f.tree());
    const [strip] = rows.filter(row => 'data-labeling-panels' in row.node.props);
    const inStrip = rows.filter(row => row.parents.includes(strip.node) && row.node.type?.displayName).map(row => row.node.type.displayName);
    assert.deepEqual(inStrip, ['LabelSetBar', 'DerivedImagePanel', 'SavedReviewQueuePanel', 'WorkflowImpactPanel', 'SpecialistFamilyNote']);
  } finally { f.unmount(); f.restore(); }
});

// An editor opened in the strip (a <details>: the derived image editor, the saved queue, the simple view's label set)
// would otherwise be seen through the strip's few dozen pixels; while one is open the work area yields as before.
test('a panel opened in the strip takes the height back from the work area', () => {
  const css = fs.readFileSync(path.resolve(directory, '../../index.css'), 'utf8');
  assert.match(css, /\[data-labeling-panels\]:has\(details\[open\]\) ~ \[data-labeling-work-area\] \{ flex-basis: 0; \}/);
  const f = fixture({height: 600});
  try {
    f.flush();
    const {rows} = structure(f.tree());
    const [strip] = rows.filter(row => 'data-labeling-panels' in row.node.props);
    const siblings = strip.parents.at(-1).props.children.flat().filter(child => child && typeof child === 'object');
    assert.ok(siblings.indexOf(strip.node) < siblings.indexOf(f.workArea()), 'the strip comes before the work area under the same parent');
  } finally { f.unmount(); f.restore(); }
});

test('Tailwind emits the strip and work area classes', async () => {
  const postcss = require('postcss'), tailwind = require('tailwindcss');
  const config = require(path.resolve(__dirname, '../../../../tailwind.config.js'));
  const raw = `<div class="min-h-10 flex-[0_100_auto] overflow-y-auto overscroll-y-contain flex-[1_1_${layout.WORK_AREA_BASIS}px] shrink-0"></div>`;
  const {css} = await postcss([tailwind({...config, content: [{raw, extension: 'html'}]})]).process('@tailwind utilities;', {from: undefined});
  for (const selector of ['.min-h-10', '.flex-\\[0_100_auto\\]', '.overflow-y-auto', '.overscroll-y-contain', `.flex-\\[1_1_${layout.WORK_AREA_BASIS}px\\]`, '.shrink-0']) {
    assert.ok(css.includes(selector + ' '), `${selector} is emitted`);
  }
  assert.match(css, /\.flex-\\\[0_100_auto\\\] \{\s*flex: 0 100 auto/);
  assert.match(css, new RegExp(`\\.flex-\\\\\\[1_1_${layout.WORK_AREA_BASIS}px\\\\\\] \\{\\s*flex: 1 1 ${layout.WORK_AREA_BASIS}px`));
});
