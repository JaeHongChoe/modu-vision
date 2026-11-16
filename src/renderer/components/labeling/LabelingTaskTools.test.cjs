const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

// These execute the production toolbar/list. Native disclosure visibility is
// represented here; keyboard, popup geometry and canvas interaction need the
// separately owned browser checks. Stores are controlled at their UI boundary.
const jsx = (type, props) => ({type, props: props || {}});
const allNodes = value => Array.isArray(value) ? value.flatMap(allNodes)
  : value && typeof value === 'object' ? [value, ...allNodes(value.props?.children)] : [];
const text = value => Array.isArray(value) ? value.map(text).join('')
  : typeof value === 'string' || typeof value === 'number' ? String(value) : value && typeof value === 'object' ? text(value.props?.children) : '';
const icons = new Proxy({}, {get: (_, name) => name === '__esModule' ? true : () => null});
const hook = state => selector => typeof selector === 'function' ? selector(state) : state;
const toolLabels = ['선택', '박스', '회전 박스', '다각형', '브러시', '지우개', '오토 셀렉터'];

function visibleNodes(value, opened = new Set()) {
  if (Array.isArray(value)) return value.flatMap(node => visibleNodes(node, opened));
  if (!value || typeof value !== 'object' || value.props?.hidden) return [];
  const children = value.type === 'details' && !value.props.open && !opened.has(value)
    ? [value.props.children].flat(Infinity).filter(child => child?.type === 'summary') : value.props?.children;
  return [value, ...visibleNodes(children, opened)];
}
function compile(filename, mocks) {
  const file = path.resolve(__dirname, filename), mod = new Module(file, module);
  mod.filename = file; mod.paths = Module._nodeModulePaths(path.dirname(file));
  const original = mod.require.bind(mod);
  mod.require = name => {
    if (Object.hasOwn(mocks, name)) return mocks[name];
    if (name.startsWith('.')) {
      const local = path.resolve(path.dirname(file), name);
      const resolved = [local + '.ts', local + '.tsx'].find(candidate => fs.existsSync(candidate));
      if (resolved) return compile(resolved, mocks);
    }
    return original(name);
  };
  mod._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  }}).outputText, file);
  return mod.exports;
}
function fixture({task = 'classification', activeTool = 'select', state = {}} = {}) {
  const calls = [];
  const annotations = [{id: 'kept-polygon', type: 'polygon', category_id: 7, label: 'crack',
    polygon: [[10, 10], [40, 10], [20, 40]], bbox: [10, 10, 40, 40], origin: {kind: 'manual'}}];
  const annotation = {activeTool, setActiveTool(tool) {calls.push(['tool', tool]); annotation.activeTool = tool; annotation.autoSelectError = null;},
    brushRadius: 10, setBrushRadius(value) {calls.push(['radius', value]);}, autoSelectTolerance: 25, setAutoSelectTolerance() {},
    convertShape() {calls.push(['convert']);}, selectedAnnotationId: null,
    viewTransform: {scale: 1, offsetX: 10, offsetY: 20}, imageDimensions: {width: 200, height: 150}, setViewTransform() {calls.push(['viewport']);},
    undo() {calls.push(['undo']);}, redo() {calls.push(['redo']);}, history: [[{id: 'old'}]], future: [], isDirty: true, isSaving: false,
    saveMessage: null, annotationLoadStatus: 'ready', editLeaseAcquiring: null, metadata: {image_uuid: 'image-a', revision: 4, labelset_id: 'truth-a'},
    saveAnnotations() {calls.push(['save']);}, markNormal() {calls.push(['normal']);}, annotations,
    currentImage: {file_path: '/owned/a.png', label: 'OK', class_source: 'folder'}, maskOpacity: .5, setMaskOpacity() {}, maskVisible: true,
    toggleMaskVisible() {calls.push(['mask']);}, setSelectedAnnotationId() {}, deleteAnnotation() {calls.push(['delete']);}, updateAnnotation() {calls.push(['geometry']);}, ...state};
  const react = {useState: value => [value, () => {}]};
  const mocks = {react: {__esModule: true, default: react, ...react}, 'react/jsx-runtime': {jsx, jsxs: jsx}, 'lucide-react': icons,
    '../../stores/useAnnotationStore': {useAnnotationStore: hook(annotation)},
    '../../stores/useProjectStore': {useProjectStore: hook({task})}};
  const Toolbar = compile('LabelingToolbar.tsx', mocks).LabelingToolbar;
  const List = compile('AnnotationList.tsx', mocks).AnnotationList;
  const render = (props = {}) => Toolbar({classification: task === 'classification', task, simpleView: true, ...props});
  const snapshot = () => structuredClone({activeTool: annotation.activeTool, annotations: annotation.annotations, history: annotation.history,
    future: annotation.future, isDirty: annotation.isDirty, metadata: annotation.metadata, viewTransform: annotation.viewTransform, currentImage: annotation.currentImage});
  return {calls, annotation, render, List, snapshot};
}
const visibleToolLabels = (tree, opened) => visibleNodes(tree, opened).filter(node => node.type === 'button')
  .map(text).filter(label => toolLabels.includes(label));

for (const [task, expected] of [
  ['classification', ['선택']], ['detection', ['선택', '박스']],
  ['segmentation', ['선택', '다각형', '브러시', '지우개', '오토 셀렉터']],
  ['anomaly', ['선택', '다각형', '브러시', '지우개', '오토 셀렉터']],
  ['rotated_detection', ['선택', '회전 박스']],
]) test(`simple ${task} exposes its useful tools and preserves the other tools in a disclosure`, () => {
  const f = fixture({task}), before = f.snapshot(), tree = f.render();
  assert.deepEqual(visibleToolLabels(tree), expected);
  const disclosure = allNodes(tree).find(node => node.type === 'details' && text(node).includes('다른 영역 도구'));
  assert.ok(disclosure, 'remaining tools have a native disclosure');
  assert.equal(disclosure.props.open, undefined, 'disclosure starts closed');
  assert.deepEqual([...visibleToolLabels(tree, new Set([disclosure]))].sort(), [...toolLabels].sort());
  assert.deepEqual(f.snapshot(), before); assert.deepEqual(f.calls, []);
});

test('the selected nonpreferred drawing tool remains visible across collapse and task/view changes', () => {
  const f = fixture({activeTool: 'brush'}), before = f.snapshot();
  let tree = f.render();
  assert.deepEqual(visibleToolLabels(tree), ['선택', '브러시']);
  const selected = visibleNodes(tree).find(node => node.type === 'button' && text(node) === '브러시');
  assert.equal(selected.props['aria-pressed'], true);
  const disclosure = allNodes(tree).find(node => node.type === 'details');
  disclosure.props.onToggle?.({currentTarget: {open: false}});
  tree = f.render({task: 'detection'});
  assert.deepEqual(visibleToolLabels(tree), ['선택', '박스', '브러시']);
  assert.deepEqual(visibleToolLabels(f.render({simpleView: false})), toolLabels);
  assert.deepEqual(f.snapshot(), before); assert.deepEqual(f.calls, []);
});

test('choosing an advanced tool only selects it; save, undo and viewport controls remain outside the closed disclosure', () => {
  const f = fixture(), before = f.snapshot(), tree = f.render();
  const disclosure = allNodes(tree).find(node => node.type === 'details');
  assert.ok(disclosure, 'advanced drawing tools are grouped');
  const polygon = visibleNodes(tree, new Set([disclosure])).find(node => node.type === 'button' && text(node) === '다각형');
  polygon.props.onClick();
  assert.equal(f.annotation.activeTool, 'polygon');
  assert.deepEqual(f.calls, [['tool', 'polygon']]);
  const after = f.snapshot(); after.activeTool = before.activeTool; assert.deepEqual(after, before);
  const current = f.render(), buttons = visibleNodes(current).filter(node => node.type === 'button');
  assert.ok(buttons.find(node => node.props['data-testid'] === 'annotation-save-button'));
  for (const title of ['실행 취소 (Ctrl+Z)', '축소', '확대', '화면 맞춤 (F)', '실제 크기 (1:1)']) {
    assert.ok(buttons.find(node => node.props.title === title), title);
  }
});

test('full view keeps all seven drawing tools regardless of task or active tool', () => {
  for (const task of ['classification', 'detection', 'segmentation', 'anomaly', 'rotated_detection']) {
    const f = fixture({task, activeTool: 'rotated_bbox'}), before = f.snapshot(), tree = f.render({simpleView: false});
    assert.deepEqual(visibleToolLabels(tree), toolLabels);
    assert.deepEqual(f.snapshot(), before); assert.deepEqual(f.calls, []);
  }
});

test('choosing an advanced tool returns focus before its button moves to the primary group', () => {
  const f = fixture(); let focused = null, focusDuringToolChange = null;
  const select = f.annotation.setActiveTool;
  f.annotation.setActiveTool = tool => {focusDuringToolChange = focused; select(tool);};
  const tree = f.render(), disclosure = allNodes(tree).find(node => node.type === 'details');
  const polygon = visibleNodes(tree, new Set([disclosure])).find(node => node.type === 'button' && text(node) === '다각형');
  const nativeDetails = {open: true, querySelector: () => ({focus() {focused = 'other-tools-summary';}})};
  polygon.props.onClick({currentTarget: {closest: () => nativeDetails}});
  assert.equal(nativeDetails.open, false);
  assert.equal(focusDuringToolChange, 'other-tools-summary');
  assert.equal(f.annotation.activeTool, 'polygon');
  assert.deepEqual(f.calls, [['tool', 'polygon']]);
});

test('a model-assisted point tool remains named when its native controls are owned by the assistant panel', () => {
  const f = fixture({activeTool: 'foundation_point'}), before = f.snapshot(), tree = f.render();
  assert.match(text(visibleNodes(tree)), /현재 도구: 모델 보조 점 선택/);
  assert.deepEqual(f.snapshot(), before); assert.deepEqual(f.calls, []);
});

test('classification without geometry describes optional regions without claiming the image class is missing', () => {
  for (const currentImage of [{file_path: '/owned/a.png', label: 'OK', class_source: 'folder'}, {file_path: '/owned/a.png'}]) {
    const f = fixture({state: {annotations: [], currentImage}});
    const before = f.snapshot(), body = text(f.List());
    assert.match(body, /선택적으로 그린 영역이 없습니다/);
    assert.doesNotMatch(body, /이 이미지에는 라벨이 없습니다/);
    assert.match(body, /이 이미지의 클래스/);
    assert.doesNotMatch(body, /정답이 준비됐|정답 없음/);
    assert.deepEqual(f.snapshot(), before); assert.deepEqual(f.calls, []);
  }
});

test('an empty detection list still directs the person to add the required geometry', () => {
  const f = fixture({task: 'detection', state: {annotations: []}}), before = f.snapshot(), body = text(f.List());
  assert.match(body, /이 이미지에는 라벨이 없습니다/);
  assert.match(body, /결함 영역을 그리세요/);
  assert.deepEqual(f.snapshot(), before); assert.deepEqual(f.calls, []);
});
