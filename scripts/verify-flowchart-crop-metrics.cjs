/** Missing JSON-null ROI metrics must not look like measured zeroes. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const root = path.resolve(__dirname, '..');
const pipeline = {
  id: 'mixed', name: 'Segmentation and patch chain',
  nodes: [
    { id: 'seg', data: { node_type: 'inspection', task: 'segmentation', threshold: 0.4, params: { min_defect_area_px: 8 } } },
    { id: 'patch', data: { node_type: 'inspection', task: 'patch_classification', threshold: 0.5 } },
    { id: 'blob', data: { node_type: 'blob_measure', params: {} } },
    { id: 'decision', data: { node_type: 'decision' } },
  ],
  edges: [{ source: 'seg', target: 'patch' }, { source: 'seg', target: 'blob' }, { source: 'patch', target: 'decision' }],
};
const baseCrop = {
  roi_id: 'patch:roi1', source_node_id: 'patch', label: 'Defect patch', bbox: [0, 0, 32, 32],
  defect_score: 0.75, verdict: 'NG', crop_thumbnail: 'data:image/png;base64,', flaw_type: 'Patch classification',
  defect_area_px: null, blob_count: null, largest_blob_area_px: null, confidence: null,
};

function renderResults(crop) {
  const state = {
    pipeline, executionResult: {
      crops: [crop], execution_steps: [], final_verdict: crop.verdict, is_ok: crop.verdict === 'OK',
      total_latency_ms: 0, roi_count: 1, defective_roi_count: crop.verdict === 'NG' ? 1 : 0,
    }, setInspectedCrop() {},
  };
  const render = (name, props = {}) => {
    const filename = path.join(root, `src/renderer/components/flowchart/${name}.tsx`);
    const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
    const component = new Module(filename, module);
    component.filename = filename;
    component.paths = Module._nodeModulePaths(path.dirname(filename));
    const originalRequire = component.require.bind(component);
    component.require = (specifier) => specifier === '../../stores/useFlowchartStore'
      ? { useFlowchartStore: () => state } : originalRequire(specifier);
    component._compile(compiled, filename);
    return renderToStaticMarkup(React.createElement(component.exports[name], props)).replace(/<[^>]+>/g, '');
  };
  return { drawer: render('IntermediateCropDrawer'), detail: render('CropDetailModal', { crop, onClose() {} }) };
}

test('patch ROI hides absent mask and Blob metrics in both card and detail', () => {
  const html = renderResults(baseCrop);
  for (const rendered of Object.values(html)) {
    assert.doesNotMatch(rendered, /임계값 초과 픽셀|측정 Blob 개수|최대 Blob 면적|Blob \s*개/);
  }
  assert.doesNotMatch(html.detail, /Blob 측정 판정|최고 결함 픽셀 확률|신뢰도 \(Confidence\)/);
  assert.match(html.detail, /로컬 모델 판정: NG/);
});

test('segmentation ROI retains measured zero defect pixels', () => {
  const html = renderResults({ ...baseCrop, roi_id: 'seg:roi1', source_node_id: 'seg', defect_area_px: 0, verdict: 'OK' });
  assert.match(html.drawer, /임계값 초과 픽셀: 0 px/);
  assert.match(html.detail, /임계값 초과 픽셀 \/ NG 최소 면적.*0 \/ 8 px/);
  assert.doesNotMatch(html.drawer, /Blob \s*개|최대 면적/);
  assert.doesNotMatch(html.detail, /측정 Blob 개수|최대 Blob 면적/);
});

test('Blob ROI retains measured zero count and largest area', () => {
  const html = renderResults({ ...baseCrop, roi_id: 'blob:roi1', source_node_id: 'blob', defect_area_px: 0, blob_count: 0, largest_blob_area_px: 0, verdict: 'OK' });
  assert.match(html.drawer, /Blob 0개 · 최대 면적 0 px²/);
  assert.match(html.detail, /Blob 측정 판정: OK/);
  assert.match(html.detail, /측정 Blob 개수:0/);
  assert.match(html.detail, /최대 Blob 면적:0 px²/);
});

test('missing largest Blob area is not replaced with a measured zero', () => {
  const html = renderResults({ ...baseCrop, roi_id: 'blob:roi1', source_node_id: 'blob', blob_count: 0, verdict: 'OK' });
  assert.match(html.drawer, /Blob 0개/);
  assert.doesNotMatch(html.drawer, /최대 면적/);
  assert.match(html.detail, /측정 Blob 개수:0/);
  assert.doesNotMatch(html.detail, /최대 Blob 면적/);
});
