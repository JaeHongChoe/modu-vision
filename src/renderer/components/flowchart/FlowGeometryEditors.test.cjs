const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

// Run the real editor and calibration panel JSX, then invoke their rendered controls.
// Only React's hook scheduler and the external calibration-list request are replaced.
function compile(file, overrides = {}) {
  const filename = path.join(__dirname, file);
  const loaded = new Module(filename, module);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(__dirname);
  const originalRequire = loaded.require.bind(loaded);
  loaded.require = name => name in overrides ? overrides[name] : originalRequire(name);
  loaded._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, filename);
  return loaded.exports;
}

const graph = compile('flowchartGraph.ts');
const calibration = compile('spatialCalibration.ts');
const REF = 'spatial-cal:sha256:' + 'b'.repeat(64);
const sourceSize = [1280, 960];
const stored = { ref: REF, method: 'known_length_planar', camera_id: 'line3-top',
  acquisition_config_hash: 'sha256:' + 'c'.repeat(64), source_size: sourceSize,
  scales_or_mapping: { mm_per_pixel_x: 0.05, mm_per_pixel_y: 0.07 }, residual: 0.0032,
  approved_at: '2026-10-04T01:00:00+00:00', approved_by: 'QA lead' };
const pxLimits = { min_length: 390, max_length: 410, min_area: 1000, max_area: 1200 };
const mmLimits = { min_length: 19, max_length: 21, min_area: 3.5, max_area: 4.2 };

function elements(tree) {
  if (Array.isArray(tree)) return tree.flatMap(elements);
  if (!tree || typeof tree !== 'object') return [];
  return [tree, ...elements(tree.props?.children)];
}
function textOf(tree) {
  if (Array.isArray(tree)) return tree.map(textOf).join('');
  if (tree === null || tree === undefined || typeof tree === 'boolean') return '';
  return typeof tree === 'object' ? textOf(tree.props?.children) : String(tree);
}

function editor(initial) {
  let params = initial, frame;
  const states = new Map(), effects = [];
  const react = {
    useState(initialValue) {
      const state = frame.state, index = frame.cursor++;
      if (!(index in state)) state[index] = typeof initialValue === 'function' ? initialValue() : initialValue;
      return [state[index], value => { state[index] = typeof value === 'function' ? value(state[index]) : value; }];
    },
    useEffect(callback, dependencies) {
      const state = frame.state, index = frame.cursor++;
      if (!state[index] || dependencies.some((value, at) => value !== state[index].dependencies[at])) {
        state[index]?.cleanup?.();
        state[index] = { dependencies };
        effects.push(() => { state[index].cleanup = callback(); });
      }
    },
  };
  const dependencies = { react, './spatialCalibration': calibration, './flowchartGraph': graph,
    '../../services/geometryFlowApi': { geometryFlowApi: { calibrations: async () => ({ calibrations: [stored] }) } } };
  dependencies['./SpatialCalibrationPanel'] = compile('SpatialCalibrationPanel.tsx', dependencies);
  const { MeasurementEditor } = compile('FlowGeometryEditors.tsx', dependencies);
  function renderComponent(component, props) {
    if (!states.has(component)) states.set(component, []);
    const previous = frame;
    frame = { state: states.get(component), cursor: 0 };
    const rendered = component(props);
    frame = previous;
    return expand(rendered);
  }
  function expand(tree) {
    if (Array.isArray(tree)) return tree.map(expand);
    if (!tree || typeof tree !== 'object') return tree;
    if (typeof tree.type === 'function') return renderComponent(tree.type, tree.props);
    return { ...tree, props: { ...tree.props, children: expand(tree.props?.children) } };
  }
  const render = () => renderComponent(MeasurementEditor, { params, sourceSize,
    onChange: next => { params = next; } });
  const find = predicate => elements(render()).find(predicate);
  const labelInput = name => {
    const label = find(element => element.type === 'label' && textOf(element).startsWith(name));
    assert.ok(label, `rendered label ${name}`);
    return elements(label).find(element => element.type === 'input');
  };
  return {
    params: () => params,
    render,
    async load() {
      render();
      effects.splice(0).forEach(callback => callback());
      await new Promise(resolve => setImmediate(resolve));
    },
    toggle(checked) { labelInput('실제 길이 교정').props.onChange({ target: { checked } }); },
    scale(axis, value) { labelInput(`${axis} 교정`).props.onChange({ target: { value } }); },
    select(label, value) {
      const control = find(element => element.type === 'select' && element.props['aria-label'] === label);
      assert.ok(control && !control.props.disabled, `enabled rendered ${label} selector`);
      control.props.onChange({ target: { value } });
    },
    limits() {
      return elements(render()).filter(element => element.type === 'label' && /^(최소|최대) (길이|면적)/.test(textOf(element)))
        .map(label => [textOf(label), elements(label).find(element => element.type === 'input').props.value]);
    },
  };
}

for (const declared of [undefined, 'px']) test(`enabling manual calibration preserves ${declared ?? 'legacy implicit px'} numeric limits`, () => {
  const initial = { paths: [], ...pxLimits, ...(declared ? { threshold_unit: declared } : {}) };
  const view = editor(initial);
  view.toggle(true);
  assert.equal(view.params().threshold_unit, 'px', '390–410 px must not become 390–410 mm');
  assert.deepEqual(view.limits(), [['최소 길이 (px)', 390], ['최대 길이 (px)', 410], ['최소 면적 (px²)', 1000], ['최대 면적 (px²)', 1200]]);
  assert.deepEqual(view.params().calibration, { unit: 'mm', source_size: [1280, 960] });
  assert.deepEqual(initial, { paths: [], ...pxLimits, ...(declared ? { threshold_unit: declared } : {}) }, 'the original parameters are not mutated');
});

for (const declared of [undefined, 'mm']) test(`disabling manual calibration preserves ${declared ?? 'legacy implicit mm'} numeric limits`, () => {
  const view = editor({ paths: [], ...mmLimits, calibration: { unit: 'mm', source_size: sourceSize,
    mm_per_pixel_x: 0.05, mm_per_pixel_y: 0.07 }, ...(declared ? { threshold_unit: declared } : {}) });
  view.toggle(false);
  assert.equal(view.params().threshold_unit, 'mm', '19–21 mm must not become 19–21 px');
  assert.equal(view.params().calibration, undefined);
  assert.deepEqual(view.limits(), [['최소 길이 (mm)', 19], ['최대 길이 (mm)', 21], ['최소 면적 (mm²)', 3.5], ['최대 면적 (mm²)', 4.2]]);
  assert.match(graph.measurementIssue(view.params()), /mm 기준에는 교정이 필요합니다/, 'missing calibration remains a validation failure');
});

test('typed inline scales and a declared pixel unit survive a manual calibration round trip', () => {
  const view = editor({ paths: [], threshold_unit: 'px', ...pxLimits });
  view.toggle(true);
  view.scale('X', '0.05');
  view.scale('Y', '0.07');
  assert.equal(graph.measurementIssue(view.params()), null);
  assert.deepEqual(view.params().calibration, { unit: 'mm', source_size: [1280, 960], mm_per_pixel_x: 0.05, mm_per_pixel_y: 0.07 });
  view.scale('X', '');
  assert.match(graph.measurementIssue(view.params()), /양수 mm\/px 교정값/, 'empty manual scales are never silently supplied');
  view.toggle(false);
  assert.deepEqual(view.params(), { paths: [], threshold_unit: 'px', ...pxLimits });
});

test('explicit unit changes still clear incompatible limits through the rendered selector', () => {
  const view = editor({ paths: [], threshold_unit: 'px', ...pxLimits });
  view.toggle(true);
  view.select('측정 기준 단위', 'mm');
  assert.deepEqual(view.limits(), [['최소 길이 (mm)', ''], ['최대 길이 (mm)', ''], ['최소 면적 (mm²)', ''], ['최대 면적 (mm²)', '']]);
  assert.match(textOf(view.render()), /단위를 바꾸면 이전 단위로 적은 기준값은 지워집니다/);
  assert.equal(view.params().threshold_unit, 'mm');
  view.toggle(false);
  assert.equal(view.params().threshold_unit, 'mm');
  view.select('측정 기준 단위', 'px');
  assert.equal(view.params().threshold_unit, 'px');
});

test('stored calibration selection keeps pixel limits and replaces the inline scale', async () => {
  const view = editor({ paths: [], threshold_unit: 'px', ...pxLimits,
    calibration: { unit: 'mm', source_size: sourceSize, mm_per_pixel_x: 0.05, mm_per_pixel_y: 0.07 } });
  await view.load();
  view.select('교정 artifact', REF);
  assert.deepEqual(view.params(), { paths: [], threshold_unit: 'px', ...pxLimits, calibration_ref: REF });
  assert.equal(graph.measurementIssue(view.params()), null);
  assert.equal(elements(view.render()).some(element => element.type === 'label' && textOf(element).startsWith('실제 길이 교정')), false);
});

test('stored calibration without existing limits continues to select mm', async () => {
  const view = editor({ paths: [] });
  await view.load();
  view.select('교정 artifact', REF);
  assert.deepEqual(view.params(), { paths: [], threshold_unit: 'mm', calibration_ref: REF });
  view.select('교정 artifact', '');
  assert.deepEqual(view.params(), { paths: [], threshold_unit: 'mm' });
  assert.match(graph.measurementIssue(view.params()), /mm 기준에는 교정이 필요합니다/);
});
