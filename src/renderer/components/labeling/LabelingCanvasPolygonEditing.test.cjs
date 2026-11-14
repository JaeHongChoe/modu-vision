const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

// Execute the actual component, store callbacks and coordinate math. The DOM,
// image decoder and React scheduling are controlled; real-browser proof is a
// separate owned Playwright test.
function fixture({controlHeight = 0, initiallySelected = true} = {}) {
  const names = ['Image', 'HTMLElement', 'window', 'document', 'ResizeObserver', 'requestAnimationFrame', 'cancelAnimationFrame'];
  const previous = new Map(names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  const slots = [], effects = [], listeners = new Map();
  let cursor = 0, dirty = true, tree, externalOffset = 0;
  const same = (a, b) => a?.length === b?.length && a.every((v, i) => Object.is(v, b[i]));
  const react = {
    useRef(value) { const i = cursor++; return slots[i] || (slots[i] = {current: value}); },
    useState(value) { const i = cursor++; if (!(i in slots)) slots[i] = typeof value === 'function' ? value() : value;
      return [slots[i], next => { const v = typeof next === 'function' ? next(slots[i]) : next; if (!Object.is(v, slots[i])) { slots[i] = v; dirty = true; } }]; },
    useCallback(fn, deps) { const i = cursor++; if (!same(slots[i]?.deps, deps)) slots[i] = {fn, deps}; return slots[i].fn; },
    useEffect(fn, deps) { const i = cursor++; if (!same(slots[i]?.deps, deps)) { const old = slots[i]; slots[i] = {deps}; effects.push(() => { old?.cleanup?.(); slots[i].cleanup = fn(); }); } },
  };
  const rectangle = {left: 100, top: 30, width: 800, height: 500};
  const container = {getBoundingClientRect: () => rectangle, addEventListener() {}, removeEventListener() {}};
  const canvas = () => {
    const node = {...container, width: 800, height: 500, style: {}};
    const context = new Proxy({canvas: node, measureText: () => ({width: 30})}, {get: (o, k) => k in o ? o[k] : () => {}});
    node.getContext = () => context;
    return node;
  };
  const canvases = [canvas(), canvas(), canvas()];
  class Element { closest() { return null; } }
  const original = {id: 'outline', type: 'polygon', label: '결함', category_id: 3,
    polygon: [[20, 20], [160, 20], [160, 130], [20, 130]],
    points: [[20, 20], [160, 20], [160, 130], [20, 130]], bbox: [20, 20, 160, 130],
    direction_deg: 35, origin: {kind: 'auto_selector', fallback: false}, text: '보존'};
  const annotation = {currentImage: {image_id: 'a', file_path: '/owned/a.png', file_name: 'a.png'},
    annotations: [structuredClone(original)], selectedAnnotationId: initiallySelected ? 'outline' : null, activeTool: 'select',
    activeCategory: {id: 3, name: '결함', color: '#ff0000'}, annotationLoadStatus: 'ready',
    metadata: {image_uuid: 'a', labelset_id: 'labels-a'}, brushRadius: 4,
    viewTransform: {scale: 2, offsetX: 5, offsetY: 7}, maskOpacity: .5, maskVisible: false,
    maskUrl: null, heatmapUrl: null, history: [], future: [], isSaving: false,
    setImageDimensions() {}, setViewTransform(value) { this.viewTransform = typeof value === 'function' ? value(this.viewTransform) : value; dirty = true; },
    setSelectedAnnotationId(id) { annotation.selectedAnnotationId = id; dirty = true; },
    updateAnnotation(id, updates) { if (annotation.annotationLoadStatus !== 'ready') return;
      annotation.history = [...annotation.history, structuredClone(annotation.annotations)]; annotation.future = [];
      annotation.annotations = annotation.annotations.map(a => a.id === id ? {...a, ...updates} : a); dirty = true; },
    deleteSelected() { annotation.annotations = annotation.annotations.filter(a => a.id !== annotation.selectedAnnotationId); dirty = true; },
    undo() { if (annotation.history.length) { annotation.future.push(annotation.annotations); annotation.annotations = annotation.history.pop(); dirty = true; } },
    redo() { if (annotation.future.length) { annotation.history.push(annotation.annotations); annotation.annotations = annotation.future.pop(); dirty = true; } },
    setActiveTool(tool) { annotation.activeTool = tool; dirty = true; }, addAnnotation() {}, commitBrushMask() {}, triggerAutoSelect() {},
  };
  const project = {backendPort: 8000, projectDir: '/owned/project-a', project: {id: 'p-a', source_dataset_dir: '/owned'}, isProjectBusy: false};
  const hook = state => Object.assign(() => state, {getState: () => state});
  const jsx = (type, props) => ({type, props});
  const mocks = {react: {__esModule: true, default: react, ...react}, 'react/jsx-runtime': {jsx, jsxs: jsx},
    '../../stores/useAnnotationStore': {useAnnotationStore: hook(annotation)},
    '../../stores/useProjectStore': {useProjectStore: hook(project)},
    '../../stores/useFoundationPromptStore': {useFoundationPromptStore: hook({displaySource: null, displaySourcePath: null, points: [], boxes: [], bind() {}})},
    './foundationRequest': {brushEditTarget: () => undefined}, '../../services/api': {resolveApiUrl: p => p, redactLocalAssetTicket: p => p},
  };
  const modules = new Map();
  function load(file) {
    if (modules.has(file)) return modules.get(file).exports;
    const m = new Module(file, module); m.filename = file; m.paths = Module._nodeModulePaths(path.dirname(file)); modules.set(file, m);
    const req = m.require.bind(m);
    m.require = name => {
      if (Object.hasOwn(mocks, name)) return mocks[name];
      if (name.startsWith('.')) { const base = path.resolve(path.dirname(file), name);
        const resolved = [base, base + '.ts', base + '.tsx'].find(p => fs.existsSync(p) && fs.statSync(p).isFile());
        if (resolved && /\.tsx?$/.test(resolved)) return load(resolved); }
      return req(name);
    };
    m._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX}}).outputText, file);
    return m.exports;
  }
  Object.assign(globalThis, {HTMLElement: Element, Image: class {set src(value) { this.url = value; }},
    document: {createElement: () => canvas(), querySelector: () => null},
    window: {devicePixelRatio: 1, addEventListener: (k, f) => listeners.set(k, f), removeEventListener: (k, f) => {if (listeners.get(k) === f) listeners.delete(k);}},
    ResizeObserver: undefined, requestAnimationFrame: () => 1, cancelAnimationFrame() {}});
  const Component = load(path.join(__dirname, 'LabelingCanvas.tsx')).LabelingCanvas;
  const nodes = value => Array.isArray(value) ? value.flatMap(nodes) : value && typeof value === 'object' ? [value, ...nodes(value.props?.children)] : [];
  function render() {
    dirty = true;
    for (let pass = 0; pass < 30 && dirty; pass++) {
      cursor = 0; dirty = false; tree = Component(); let i = 0;
      for (const n of nodes(tree)) if (n.props?.ref) n.props.ref.current = n.type === 'canvas' ? canvases[i++] : container;
      const hasControls = nodes(tree).some(n => n.props && Object.hasOwn(n.props, 'onInsert') && Object.hasOwn(n.props, 'selectedIndex'));
      rectangle.top = 30 + externalOffset + (hasControls ? controlHeight : 0);
      rectangle.height = 500 - (hasControls ? controlHeight : 0);
      effects.splice(0).forEach(run => run());
    }
    assert.equal(dirty, false, 'component settles');
  }
  function down(p, extras = {}) {
    const t = annotation.viewTransform;
    const e = {clientX: rectangle.left + t.offsetX + p[0] * t.scale, clientY: rectangle.top + t.offsetY + p[1] * t.scale,
      currentTarget: canvases[2], button: 0, shiftKey: false, preventDefault() {}, stopPropagation() {}, ...extras};
    nodes(tree).find(n => n.type === 'canvas' && n.props.onPointerDown).props.onPointerDown(e);
    render(); return e;
  }
  function up(e = {}) { nodes(tree).find(n => n.type === 'canvas' && n.props.onPointerUp).props.onPointerUp(e); render(); }
  function pointer(p, extras = {}) { up(down(p, extras)); }
  function moveClient(x, y) { nodes(tree).find(n => n.type === 'canvas' && n.props.onPointerMove).props.onPointerMove({clientX: x, clientY: y, currentTarget: canvases[2]}); render(); }
  function key(key, extras = {}) { let prevented = false;
    listeners.get('keydown')({key, code: key, target: new Element(), preventDefault() {prevented = true;}, ...extras}); render(); return prevented; }
  function close() { slots.forEach(s => s?.cleanup?.()); for (const [k, v] of previous) if (v) Object.defineProperty(globalThis, k, v); else delete globalThis[k]; }
  render();
  return {annotation, project, original, pointer, down, up, moveClient, key, render, close,
    shiftCanvas: offset => { externalOffset = offset; render(); },
    renderedControls: () => { const control = nodes(tree).find(n => n.props && Object.hasOwn(n.props, 'onInsert') && Object.hasOwn(n.props, 'selectedIndex')); return nodes(control.type(control.props)); },
    pointerHandler: () => nodes(tree).find(n => n.type === 'canvas' && n.props.onPointerDown).props.onPointerDown,
    controls: () => nodes(tree).find(n => n.props && Object.hasOwn(n.props, 'onInsert') && Object.hasOwn(n.props, 'selectedIndex'))};
}

test('Shift-click inserts one projected point into the selected polygon with one undo step', () => {
  const f = fixture(); try {
    f.pointer([80, 22], {shiftKey: true});
    const a = f.annotation.annotations[0]; assert.equal(a.polygon.length, 5);
    assert.deepEqual(a.polygon[1], [80, 20]); assert.deepEqual(a.points, a.polygon);
    assert.equal(f.annotation.history.length, 1); assert.equal(a.id, f.original.id);
    assert.deepEqual(a.origin, f.original.origin); assert.equal(a.text, '보존');
  } finally {f.close();}
});

test('Delete on a selected point preserves its annotation and rejects removing the third point', () => {
  const f = fixture(); try {
    f.pointer([20, 20]); f.key('Delete');
    assert.equal(f.annotation.annotations.length, 1); assert.equal(f.annotation.annotations[0].polygon.length, 3);
    f.key('Delete'); assert.equal(f.annotation.annotations[0].polygon.length, 3);
    assert.equal(f.annotation.history.length, 1);
  } finally {f.close();}
});

test('arrow key nudges the selected point rather than the whole polygon', () => {
  const f = fixture(); try {
    f.pointer([20, 20]); assert.equal(f.key('ArrowRight', {shiftKey: true}), true);
    const a = f.annotation.annotations[0]; assert.deepEqual(a.polygon[0], [30, 20]);
    assert.deepEqual(a.polygon.slice(1), f.original.polygon.slice(1)); assert.deepEqual(a.points, a.polygon);
  } finally {f.close();}
});

test('image and labelset changes discard point selection before the next input', () => {
  const f = fixture(); try {
    f.pointer([20, 20]); assert.equal(f.controls()?.props.selectedIndex, 0);
    f.annotation.currentImage = {image_id: 'b', file_path: '/owned/b.png', file_name: 'b.png'};
    f.annotation.metadata = {image_uuid: 'b', labelset_id: 'labels-b'}; f.render();
    assert.equal(f.controls()?.props.selectedIndex, null);
    f.key('ArrowRight');
    assert.deepEqual(f.annotation.annotations[0].polygon, f.original.polygon.map(([x, y]) => [x + 1, y]));
  } finally {f.close();}
});

test('loading state cannot insert, delete, or nudge polygon points', () => {
  const f = fixture(); try {
    f.pointer([20, 20]); f.annotation.annotationLoadStatus = 'loading'; f.render();
    f.pointer([80, 20], {shiftKey: true}); f.key('Delete'); f.key('ArrowRight');
    assert.deepEqual(f.annotation.annotations, [f.original]);
  } finally {f.close();}
});


test('retained controls and pointer callbacks cannot edit a replacement project with the same region ID', () => {
  const f = fixture(); try {
    f.pointer([20, 20]);
    const previousControls = f.controls().props, previousPointer = f.pointerHandler();
    f.project.projectDir = '/owned/project-b'; f.project.project = {id: 'p-b', active_labelset_id: 'new-labels'}; f.render();
    f.pointer([20, 20]);
    const before = structuredClone(f.annotation.annotations);
    previousControls.onInsert(); previousControls.onDelete();
    previousPointer({button: 0, shiftKey: true, clientX: 265, clientY: 77, currentTarget: {getBoundingClientRect: () => ({left: 100, top: 30})}, preventDefault() {}});
    f.render(); assert.deepEqual(f.annotation.annotations, before);
  } finally {f.close();}
});

test('undo restores the full geometry and clears point selection before another whole-shape nudge', () => {
  const f = fixture(); try {
    f.pointer([80, 22], {shiftKey: true});
    assert.equal(f.annotation.annotations[0].polygon.length, 5);
    f.key('z', {ctrlKey: true});
    assert.deepEqual(f.annotation.annotations, [f.original]);
    assert.equal(f.controls().props.selectedIndex, null);
    f.key('ArrowRight');
    assert.deepEqual(f.annotation.annotations[0].polygon, f.original.polygon.map(([x, y]) => [x + 1, y]));
  } finally {f.close();}
});


test('first selecting and dragging an unselected vertex preserves original-image coordinates while controls appear', () => {
  const f = fixture({controlHeight: 80, initiallySelected: false}); try {
    assert.equal(f.controls(), undefined);
    const start = f.down([20, 130]);
    f.moveClient(start.clientX + 1, start.clientY);
    assert.deepEqual(f.annotation.annotations[0].polygon[3], [20.5, 130]);
    assert.deepEqual(f.annotation.annotations[0].polygon.slice(0, 3), f.original.polygon.slice(0, 3));
    f.up(); assert.ok(f.controls());
  } finally {f.close();}
});


test('an already selected vertex drag tolerates an external draft banner moving the viewport', () => {
  const f = fixture({controlHeight: 80}); try {
    const start = f.down([20, 130]); f.shiftCanvas(67);
    f.moveClient(start.clientX + 10, start.clientY);
    assert.deepEqual(f.annotation.annotations[0].polygon[3], [25, 130]);
    f.up();
  } finally {f.close();}
});

test('whole-shape mode remains a focusable pressed control after clearing point selection', () => {
  const f = fixture(); try {
    f.pointer([20, 20]); f.controls().props.onSelect(null); f.render();
    const control = f.renderedControls().find(n => n.props?.['aria-label'] === '도형 선택');
    assert.equal(control.props.disabled, false);
    assert.equal(control.props['aria-pressed'], true);
  } finally {f.close();}
});
