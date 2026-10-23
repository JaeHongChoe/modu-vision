const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), Module = require('node:module'), ts = require('typescript');

// The auto selector is a drawing aid, not a draft (docs/product-direction-easy-labeling-agent.md §3.3): its shape joins
// the canvas at the click and is saved by the person's own save. These checks cover what that leaves to do: the shape
// says how it was made (origin kind auto_selector), the list badges it, and the small box the selector puts around a
// click where it found nothing is named on the canvas, in the list and in the toolbar until the person removes it.

function load(filename, mocks = {}) {
  const m = new Module(filename, module);
  m.filename = filename;
  m.paths = Module._nodeModulePaths(path.dirname(filename));
  const req = m.require.bind(m);
  m.require = name => {
    if (Object.hasOwn(mocks, name)) return mocks[name];
    if (name.startsWith('.')) {
      const local = path.resolve(path.dirname(filename), name);
      for (const suffix of ['.ts', '.tsx']) if (fs.existsSync(local + suffix)) return load(local + suffix, mocks);
    }
    return req(name);
  };
  m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX}}).outputText, filename);
  return m.exports;
}
const here = (...parts) => path.resolve(__dirname, ...parts);
const jsx = (type, props) => ({type, props: props || {}});
const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes) : tree && typeof tree === 'object' ? [tree, ...nodes(tree.props?.children)] : [];
const text = node => [node?.props?.children].flat(Infinity).filter(part => typeof part === 'string').join('');
const hook = state => Object.assign(selector => typeof selector === 'function' ? selector(state) : state, {getState: () => state});
const icons = new Proxy({}, {get: (_, name) => name === '__esModule' ? true : () => null});
const reactStub = {__esModule: true, default: {}, useState: value => [value, () => {}]};

const FOUND = {kind: 'auto_selector', backend: 'opencv_flood_fill', seed_point: [12, 34], tolerance: 25, fallback: false, created_at: '2026-10-22T09:00:00.000Z'};
const FALLBACK = {...FOUND, seed_point: [3, 4], fallback: true};
const ADOPTED = {kind: 'model_draft', proposal_id: 'suggestion_x', candidate_id: 'suggestion_x_c0', reviewer: 'kai', adopted_at: '2026-10-22T00:00:00Z'};
const polygon = (id, origin) => ({id, type: 'polygon', label: 'scratch', category_id: 2, color: '#f59e0b',
  polygon: [[1, 1], [9, 1], [9, 9]], points: [[1, 1], [9, 1], [9, 9]], bbox: [1, 1, 9, 9], ...(origin ? {origin} : {})});

// --- store: the click's result joins the labels with its origin -------------------------------------------------
function store(autoSelect) {
  const bodies = [];
  const m = load(here('..', '..', 'stores', 'useAnnotationStore.ts'), {
    '../services/api': {api: {annotations: {autoSelect}}, getApiPersistenceIdentity: () => 'local', getProjectContextGeneration: () => 1},
    '../services/datasetWorkflow': {datasetWorkflow: {saveAnnotations: async body => { bodies.push(body); return {status: 'saved'}; }}},
    './useDatasetStore': {useDatasetStore: {getState: () => ({annotationsChanged: async () => {}})}},
    '../components/labeling/foundationRequest': {labelCategoryPalette: categories => categories},
    '../components/labeling/convertedAnnotation': {}, '../components/labeling/teamDataWorkflow': {imageLeaseToken: () => null},
  });
  m.useAnnotationStore.setState({currentImage: {image_id: 'a', file_path: '/a.png', width: 10, height: 10}, imageDimensions: {width: 10, height: 10},
    annotationLoadStatus: 'ready', isDirty: false, metadata: {image_uuid: 'a', revision: 3}, teamEditingEnabled: false, annotations: [],
    autoSelectTolerance: 30, activeCategory: {id: 2, name: 'scratch', color: '#f59e0b'}});
  return {state: () => m.useAnnotationStore.getState(), bodies};
}
const result = extra => ({status: 'success', result: {polygon: [[1, 1], [4, 1], [4, 4]], bbox: [1, 1, 4, 4], area: 4.5, seed_point: [2, 2], ...extra}});

test('a shape the auto selector outlined carries its origin, and the person\'s save forwards it unchanged', async () => {
  const {state, bodies} = store(async () => result({source: 'opencv_flood_fill', fallback: false}));
  assert.equal(await state().triggerAutoSelect(2, 2), true);
  const [shape] = state().annotations;
  assert.equal(shape.type, 'polygon'); assert.equal(shape.label, 'scratch');
  assert.equal(shape.origin.kind, 'auto_selector'); assert.equal(shape.origin.backend, 'opencv_flood_fill');
  assert.deepEqual(shape.origin.seed_point, [2, 2]); assert.equal(shape.origin.tolerance, 30); assert.equal(shape.origin.fallback, false);
  assert.ok(!Number.isNaN(Date.parse(shape.origin.created_at)));
  assert.equal(state().isDirty, true, 'the shape is the person\'s, saved by their own save like a drawn one');
  assert.equal(await state().saveAnnotations(), true);
  assert.deepEqual(bodies[0].annotations[0].origin, shape.origin);
});

test('the fallback box is marked in its origin, and a backend without the flag counts as a found outline', async () => {
  const boxed = store(async () => result({source: 'opencv_flood_fill', fallback: true}));
  assert.equal(await boxed.state().triggerAutoSelect(2, 2), true);
  assert.equal(boxed.state().annotations[0].origin.fallback, true);
  assert.equal(boxed.state().autoSelectError, null, 'the box is on the canvas; the notice follows the shape, not an error');
  const older = store(async () => result({}));
  assert.equal(await older.state().triggerAutoSelect(2, 2), true);
  assert.deepEqual([older.state().annotations[0].origin.fallback, older.state().annotations[0].origin.backend], [false, 'opencv_flood_fill']);
});

// --- list: a badge per origin kind, and the fallback box named --------------------------------------------------
function listNodes(annotations) {
  const m = load(here('AnnotationList.tsx'), {
    react: reactStub, 'react/jsx-runtime': {jsx, jsxs: jsx}, 'lucide-react': icons,
    '../../stores/useAnnotationStore': {useAnnotationStore: hook({annotations, selectedAnnotationId: null, setSelectedAnnotationId() {}, deleteAnnotation() {}, updateAnnotation() {}})},
    '../../stores/useProjectStore': {useProjectStore: hook({task: 'segmentation'})},
  });
  return nodes(m.AnnotationList());
}
const badges = (tree, label) => tree.filter(node => node.type === 'span' && text(node) === label);

test('the list badges an auto-selector shape, names the fallback box, and keeps the draft badge for adopted shapes', () => {
  const tree = listNodes([polygon('drawn'), polygon('outlined', FOUND), polygon('boxed', FALLBACK), polygon('adopted', ADOPTED)]);
  const selector = badges(tree, '오토 셀렉터');
  assert.equal(selector.length, 2, 'both auto-selector shapes, never the drawn or the adopted one');
  assert.match(selector[0].props.title, /opencv_flood_fill/); assert.match(selector[0].props.title, /클릭 \(12, 34\)/); assert.match(selector[0].props.title, /민감도 25/);
  const fallback = badges(tree, '자동 선택 실패 · 대체 상자');
  assert.equal(fallback.length, 1); assert.match(fallback[0].props.title, /지우거나 직접 그리세요/);
  assert.equal(badges(tree, '초안에서 채택').length, 1);
  assert.equal(badges(listNodes([polygon('drawn')]), '오토 셀렉터').length, 0);
});

// --- toolbar: the notice follows the fallback box, not the click ------------------------------------------------
function toolbarNodes(state) {
  const value = {activeTool: 'select', setActiveTool() {}, brushRadius: 10, setBrushRadius() {}, autoSelectTolerance: 25, setAutoSelectTolerance() {}, convertShape() {},
    selectedAnnotationId: null, viewTransform: {scale: 1, offsetX: 0, offsetY: 0}, imageDimensions: {width: 10, height: 10}, setViewTransform() {}, undo() {}, redo() {},
    history: [], future: [], isDirty: false, isSaving: false, saveMessage: null, annotationLoadStatus: 'ready', editLeaseAcquiring: null, metadata: {image_uuid: 'a'},
    saveAnnotations() {}, markNormal() {}, annotations: [], currentImage: {file_path: '/source/a.png'}, maskOpacity: 0.5, setMaskOpacity() {}, maskVisible: true, toggleMaskVisible() {}, ...state};
  const m = load(here('LabelingToolbar.tsx'), {
    react: reactStub, 'react/jsx-runtime': {jsx, jsxs: jsx}, 'lucide-react': icons,
    '../../stores/useAnnotationStore': {useAnnotationStore: hook(value)},
    '../../utils/coordinateMath': {calculateActualSize() {}, calculateCenterZoom() {}, calculateFitToScreen() {}},
  });
  return nodes(m.LabelingToolbar({}));
}
const notice = tree => tree.find(node => node.props?.['data-testid'] === 'auto-select-fallback-notice');

test('the toolbar says the selector found nothing while its fallback box is among the labels, whatever tool is active', () => {
  for (const activeTool of ['select', 'auto_select']) {
    const shown = notice(toolbarNodes({activeTool, annotations: [polygon('drawn'), polygon('boxed', FALLBACK)]}));
    assert.ok(shown, activeTool);
    assert.equal(text(shown), '영역을 찾지 못해 작은 상자를 넣었습니다. 지우거나 직접 그리세요.');
    assert.equal(shown.props.role, 'status');
  }
  assert.equal(notice(toolbarNodes({activeTool: 'auto_select', annotations: [polygon('outlined', FOUND)]})), undefined, 'a found outline needs no notice');
  assert.equal(notice(toolbarNodes({activeTool: 'auto_select', annotations: []})), undefined, 'erasing the box ends the notice');
});

// --- canvas: the fallback box is dashed and named on the shape --------------------------------------------------
function canvasCalls(annotations) {
  const previous = Object.fromEntries(['Image', 'window', 'document', 'ResizeObserver', 'requestAnimationFrame', 'cancelAnimationFrame']
    .map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  const calls = [];
  // Every drawing call is recorded; style assignments are accepted and otherwise ignored.
  const context = new Proxy({}, {
    get: (_, name) => name === 'measureText' ? () => ({width: 40}) : (...args) => { calls.push([name, ...args]); },
    set: () => true,
  });
  const rect = {width: 1120, height: 446, left: 0, top: 0};
  const surface = () => ({width: 0, height: 0, style: {}, getContext: () => context, getBoundingClientRect: () => rect, addEventListener() {}, removeEventListener() {}});
  const container = {getBoundingClientRect: () => rect, addEventListener() {}, removeEventListener() {}};
  const slots = [], effects = [];
  let cursor = 0, dirty = false;
  const same = (a, b) => a?.length === b?.length && a.every((value, index) => Object.is(value, b[index]));
  const react = {
    useRef(value) { const i = cursor++; return slots[i] || (slots[i] = {current: value}); },
    useState(value) {
      const i = cursor++;
      if (!(i in slots)) slots[i] = typeof value === 'function' ? value() : value;
      return [slots[i], next => { const v = typeof next === 'function' ? next(slots[i]) : next; if (!Object.is(v, slots[i])) { slots[i] = v; dirty = true; } }];
    },
    useCallback(fn, deps) { const i = cursor++; if (!same(slots[i]?.deps, deps)) slots[i] = {deps, fn}; return slots[i].fn; },
    useEffect(fn, deps) {
      const i = cursor++;
      if (!same(slots[i]?.deps, deps)) { const old = slots[i]; slots[i] = {deps}; effects.push({old, install: () => { slots[i].cleanup = fn(); }}); }
    },
  };
  class ControlledImage { constructor() { this.onload = null; this.onerror = null; } set src(value) { this.url = value; } get src() { return this.url; } }
  Object.assign(globalThis, {
    Image: ControlledImage, document: {createElement: () => surface()},
    window: {devicePixelRatio: 1, addEventListener() {}, removeEventListener() {}},
    ResizeObserver: undefined, requestAnimationFrame: () => 1, cancelAnimationFrame() {},
  });
  try {
    const annotation = {currentImage: {image_id: 'a', file_path: '/controlled/a.png', file_name: 'a.png'}, annotations, selectedAnnotationId: null, activeTool: 'select',
      activeCategory: {id: 2, name: 'scratch', color: '#f59e0b'}, brushRadius: 4, viewTransform: {scale: 1, offsetX: 0, offsetY: 0}, maskOpacity: 0.5, maskVisible: false,
      maskUrl: null, heatmapUrl: null, setImageDimensions() {}, setViewTransform() {}};
    const component = load(here('LabelingCanvas.tsx'), {
      react: {__esModule: true, default: react, ...react}, 'react/jsx-runtime': {jsx, jsxs: jsx},
      '../../stores/useAnnotationStore': {useAnnotationStore: hook(annotation)},
      '../../stores/useProjectStore': {useProjectStore: hook({backendPort: 8000, project: {source_dataset_dir: '/controlled'}})},
      '../../stores/useFoundationPromptStore': {useFoundationPromptStore: hook({displaySource: null, displaySourcePath: null, points: [], boxes: [], bind() {}})},
      '../../services/api': {resolveApiUrl: route => 'http://127.0.0.1:8000' + route},
      './labelingShortcuts': {resolveLabelingShortcut: () => null},
    }).LabelingCanvas;
    const canvases = [surface(), surface(), surface()];
    dirty = true;
    for (let i = 0; i < 20 && dirty; i++) {
      cursor = 0; dirty = false;
      let index = 0;
      for (const node of nodes(component())) if (node.props?.ref) node.props.ref.current = node.type === 'canvas' ? canvases[index++] : container;
      const pending = effects.splice(0);
      pending.forEach(effect => effect.old?.cleanup?.());
      pending.forEach(effect => effect.install());
    }
    assert.equal(dirty, false, 'the component settles');
    slots.forEach(slot => slot?.cleanup?.());
    return calls;
  } finally {
    for (const [name, descriptor] of Object.entries(previous)) { if (descriptor) Object.defineProperty(globalThis, name, descriptor); else delete globalThis[name]; }
  }
}
const drew = (calls, label) => calls.some(([name, value]) => name === 'fillText' && value === label);

test('the canvas dashes the fallback box and names it on the shape; a found outline is drawn like any polygon', () => {
  const boxed = canvasCalls([polygon('boxed', FALLBACK)]);
  assert.ok(drew(boxed, '자동 선택 실패 · 대체 상자'));
  assert.ok(boxed.some(([name, dash]) => name === 'setLineDash' && Array.isArray(dash) && dash.join() === '4,3'), 'the dashed overstroke');
  assert.ok(drew(boxed, 'scratch'), 'the class badge stays');
  const outlined = canvasCalls([polygon('outlined', FOUND), polygon('drawn')]);
  assert.ok(drew(outlined, 'scratch'));
  assert.ok(!drew(outlined, '자동 선택 실패 · 대체 상자'));
  assert.ok(!outlined.some(([name, dash]) => name === 'setLineDash' && Array.isArray(dash) && dash.join() === '4,3'));
});
