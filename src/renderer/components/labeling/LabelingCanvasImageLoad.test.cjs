const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

// Use the existing renderer test loader to execute the actual component and
// actual coordinate helpers. Only the React/DOM/Image platform interfaces are
// controlled. This is a callback lifecycle regression, not browser pixel proof.
function load(file, mocks = {}) {
  const m = new Module(file, module);
  m.filename = file;
  m.paths = Module._nodeModulePaths(path.dirname(file));
  const original = m.require.bind(m);
  m.require = name => Object.hasOwn(mocks, name) ? mocks[name] : original(name);
  m._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX},
  }).outputText, file);
  return m.exports;
}

function fixture() {
  const previous = Object.fromEntries(['Image', 'window', 'document', 'ResizeObserver', 'requestAnimationFrame', 'cancelAnimationFrame']
    .map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  const originalWarn = console.warn;
  const images = [], sourceAssignments = [], warnings = [], dimensions = [], transforms = [], draws = [];
  const slots = [], pendingEffects = [];
  let cursor = 0, dirty = false, mounted = true;
  const same = (a, b) => a?.length === b?.length && a.every((value, index) => Object.is(value, b[index]));
  const react = {
    useRef(value) { const i = cursor++; return slots[i] || (slots[i] = {current: value}); },
    useState(value) {
      const i = cursor++;
      if (!(i in slots)) slots[i] = typeof value === 'function' ? value() : value;
      return [slots[i], next => { const value = typeof next === 'function' ? next(slots[i]) : next; if (!Object.is(value, slots[i])) { slots[i] = value; dirty = true; } }];
    },
    useCallback(fn, deps) {
      const i = cursor++;
      if (!same(slots[i]?.deps, deps)) slots[i] = {deps, fn};
      return slots[i].fn;
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
  const rect = {width: 1120, height: 446, left: 0, top: 370};
  const listeners = new Map();
  const container = {
    getBoundingClientRect: () => rect,
    addEventListener() {}, removeEventListener() {},
  };
  const canvas = name => {
    const ctx = {
      clearRect() {}, setTransform() {}, save() {}, restore() {},
      drawImage(...args) { draws.push({canvas: name, image: args[0], arguments: args.slice(1)}); },
      measureText: () => ({width: 0}),
    };
    return {width: 0, height: 0, style: {}, getContext: () => ctx,
      getBoundingClientRect: () => rect, addEventListener() {}, removeEventListener() {}};
  };
  const canvases = ['base', 'mask', 'vector'].map(canvas);
  let offscreen = 0;
  class ControlledImage {
    constructor() { this.onload = null; this.onerror = null; this.naturalWidth = 64; this.naturalHeight = 64; images.push(this); }
    set src(value) { this.url = value; sourceAssignments.push({image: this, url: value}); }
    get src() { return this.url; }
  }
  Object.assign(globalThis, {
    Image: ControlledImage,
    document: {createElement: name => { assert.equal(name, 'canvas'); return canvas('offscreen-' + ++offscreen); }},
    window: {devicePixelRatio: 1, addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: (name, fn) => { if (listeners.get(name) === fn) listeners.delete(name); }},
    ResizeObserver: undefined,
    requestAnimationFrame: () => 1, cancelAnimationFrame() {},
  });
  console.warn = (...args) => warnings.push(args);
  const annotation = {
    currentImage: {image_id: 'a', file_path: '/controlled/a.png', file_name: 'a.png'},
    annotations: [], selectedAnnotationId: null, activeTool: 'select', activeCategory: {id: 'controlled', color: '#ffffff'},
    brushRadius: 4, viewTransform: {scale: 1, offsetX: 0, offsetY: 0},
    maskOpacity: 0.5, maskVisible: false, maskUrl: null, heatmapUrl: null,
    setImageDimensions: value => dimensions.push({...value}),
    setViewTransform: value => { annotation.viewTransform = typeof value === 'function' ? value(annotation.viewTransform) : value; transforms.push({...annotation.viewTransform}); dirty = true; },
  };
  const project = {backendPort: 8000};
  const foundation = {displaySource: null, displaySourcePath: null, points: [], boxes: [], bind() {}};
  const hook = state => Object.assign(() => state, {getState: () => state});
  const math = load(path.resolve(__dirname, '../../utils/coordinateMath.ts'));
  const layout = load(path.resolve(__dirname, 'labelingLayout.ts'));
  const jsx = (type, props) => ({type, props});
  const component = load(path.resolve(__dirname, 'LabelingCanvas.tsx'), {
    react: {__esModule: true, default: react, ...react},
    'react/jsx-runtime': {jsx, jsxs: jsx},
    '../../stores/useAnnotationStore': {useAnnotationStore: hook(annotation)},
    '../../stores/useProjectStore': {useProjectStore: hook(project)},
    '../../stores/useFoundationPromptStore': {useFoundationPromptStore: hook(foundation)},
    './foundationRequest': {brushEditTarget: () => null},
    '../../services/api': {resolveApiUrl: route => 'http://127.0.0.1:' + project.backendPort + route},
    './labelingShortcuts': {resolveLabelingShortcut: () => null},
    './labelingLayout': layout,
    '../../utils/coordinateMath': math,
  }).LabelingCanvas;
  const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes) : tree && typeof tree === 'object' ? [tree, ...nodes(tree.props?.children)] : [];
  function render() {
    assert.ok(mounted);
    dirty = true;
    for (let i = 0; i < 20 && dirty; i++) {
      cursor = 0; dirty = false;
      const tree = component();
      let canvasIndex = 0;
      for (const node of nodes(tree)) {
        if (node.props?.ref) node.props.ref.current = node.type === 'canvas' ? canvases[canvasIndex++] : container;
      }
      // React runs the old effect cleanup before installing the replacement.
      const effects = pendingEffects.splice(0);
      effects.forEach(effect => effect.old?.cleanup?.());
      effects.forEach(effect => effect.install());
    }
    assert.equal(dirty, false, 'The real component must settle without reloading on pan/zoom callbacks');
  }
  function select(name) {
    annotation.currentImage = name === null ? null : {image_id: name, file_path: '/controlled/' + name + '.png', file_name: name + '.png'};
    render();
    return images.at(-1);
  }
  const complete = (image, width = 64, height = 64, retained = image.onload) => {
    assert.equal(typeof retained, 'function'); image.naturalWidth = width; image.naturalHeight = height; retained(); render();
  };
  const observation = () => ({dimensions: structuredClone(dimensions), transforms: structuredClone(transforms),
    baseImages: draws.filter(row => row.canvas === 'base').map(row => row.image),
    assignments: sourceAssignments.map(row => ({image: row.image, url: row.url})), warnings: warnings.map(row => row.slice())});
  function unmount() { slots.forEach(slot => slot?.cleanup?.()); mounted = false; }
  function close() {
    if (mounted) unmount();
    console.warn = originalWarn;
    for (const [name, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor); else delete globalThis[name];
    }
  }
  render();
  return {images, annotation, project, foundation, math, dimensions, transforms, draws, sourceAssignments, warnings,
    render, select, complete, observation, unmount, close};
}

test('current raw image commits its natural dimensions, fitted view and actual base draw without another load', () => {
  const f = fixture(); try {
    const image = f.images[0]; assert.match(image.src, /\/api\/dataset\/raw\/a\.png\?/); assert.equal(image.crossOrigin, 'anonymous');
    f.complete(image, 64, 48);
    assert.deepEqual(f.dimensions.at(-1), {width: 64, height: 48});
    assert.deepEqual(f.transforms.at(-1), f.math.calculateFitToScreen(1120, 446, 64, 48));
    assert.equal(f.draws.filter(row => row.canvas === 'base').at(-1).image, image);
    assert.equal(f.images.length, 1);
  } finally { f.close(); }
});

test('late previous raw completion cannot replace the selected image after reverse completion', () => {
  const f = fixture(); try {
    const old = f.images[0], queuedLoad = old.onload;
    const current = f.select('b'); f.complete(current, 64, 48);
    const before = f.observation(); old.naturalWidth = 17; old.naturalHeight = 23; queuedLoad(); f.render();
    assert.deepEqual(f.observation(), before, 'The selected raw image, dimensions and fitted view must survive a retained old onload');
    assert.equal(f.draws.filter(row => row.canvas === 'base').at(-1).image, current);
  } finally { f.close(); }
});

test('late previous raw error cannot issue a thumbnail request or warning for the selected image', () => {
  const f = fixture(); try {
    const old = f.images[0], queuedError = old.onerror; f.complete(f.select('b'));
    const before = f.observation(); queuedError(new Error('Controlled old raw failure')); f.render();
    assert.deepEqual(f.observation(), before);
  } finally { f.close(); }
});

test('an old thumbnail completion is fenced after a new raw image has completed', () => {
  const f = fixture(); try {
    const old = f.images[0]; old.onerror(new Error('Controlled current raw failure'));
    assert.match(old.src, /\/api\/dataset\/thumbnail\/a\.png\?/); const queuedThumbnail = old.onload;
    const current = f.select('b'); f.complete(current); const before = f.observation();
    queuedThumbnail(); f.render(); assert.deepEqual(f.observation(), before);
    assert.equal(f.draws.filter(row => row.canvas === 'base').at(-1).image, current);
  } finally { f.close(); }
});

test('leaving the canvas fences retained load and error callbacks without fallback requests', () => {
  const f = fixture(); try {
    const old = f.images[0], queuedLoad = old.onload, queuedError = old.onerror;
    f.unmount(); const before = f.observation(); queuedLoad(); queuedError(new Error('Controlled detached failure'));
    assert.deepEqual(f.observation(), before);
  } finally { f.close(); }
});

test('clearing the selected image fences the pending raw callback', () => {
  const f = fixture(); try {
    const old = f.images[0], queuedLoad = old.onload, queuedError = old.onerror; f.select(null);
    const before = f.observation(); queuedLoad(); queuedError(new Error('Controlled cleared selection')); f.render();
    assert.deepEqual(f.observation(), before); assert.equal(f.images.length, 1);
  } finally { f.close(); }
});

for (const boundary of ['display source', 'backend transport']) test(`a changed ${boundary} fences the previous load of the same image`, () => {
  const f = fixture(); try {
    const old = f.images[0], queuedLoad = old.onload, queuedError = old.onerror;
    if (boundary === 'display source') { f.foundation.displaySourcePath = f.annotation.currentImage.file_path; f.foundation.displaySource = '/controlled/display.png'; }
    else f.project.backendPort = 8001;
    f.render(); const current = f.images.at(-1); assert.notEqual(current, old); f.complete(current, 31, 47);
    const before = f.observation(); queuedLoad(); queuedError(new Error('Controlled old source')); f.render();
    assert.deepEqual(f.observation(), before); assert.equal(f.draws.filter(row => row.canvas === 'base').at(-1).image, current);
  } finally { f.close(); }
});

test('current raw failure still falls back to the current thumbnail and commits its decoded dimensions', () => {
  const f = fixture(); try {
    const current = f.images[0], raw = current.src; current.onerror(new Error('Controlled current raw failure'));
    assert.equal(f.warnings.length, 1); assert.notEqual(current.src, raw); assert.match(current.src, /\/api\/dataset\/thumbnail\/a\.png\?.*size=1024/);
    const assignments = f.sourceAssignments.length; current.onerror(new Error('Controlled thumbnail failure'));
    assert.equal(f.sourceAssignments.length, assignments, 'An active failed thumbnail must not recursively reset the same fallback source');
    f.complete(current, 37, 29); assert.deepEqual(f.dimensions.at(-1), {width: 37, height: 29});
    assert.equal(f.draws.filter(row => row.canvas === 'base').at(-1).image, current); assert.equal(f.images.length, 1);
  } finally { f.close(); }
});

test('the existing zero-natural-dimension fallback remains 512 for the current image', () => {
  const f = fixture(); try { f.complete(f.images[0], 0, 0); assert.deepEqual(f.dimensions.at(-1), {width: 512, height: 512}); }
  finally { f.close(); }
});
