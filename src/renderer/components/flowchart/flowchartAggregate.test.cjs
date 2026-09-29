const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const filename = path.join(__dirname, 'flowchartGraph.ts');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const loaded = new Module(filename, module);
loaded.filename = filename;
loaded.paths = Module._nodeModulePaths(__dirname);
loaded._compile(compiled, filename);
const { connectFlowNodes, locateFlowIssue, updateFlowEdgePayload, validateFlowchartGraph } = loaded.exports;

function renderNode(flowNode) {
  const file = path.join(__dirname, 'CustomNode.tsx');
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const component = new Module(file, module);
  component.filename = file;
  component.paths = Module._nodeModulePaths(__dirname);
  const originalRequire = component.require.bind(component);
  component.require = (name) => name === './flowchartViewport' ? { FLOW_NODE_WIDTH: 272 } : originalRequire(name);
  component._compile(code, file);
  const React = require('react');
  return require('react-dom/server').renderToStaticMarkup(React.createElement(component.exports.CustomNode, {
    node: flowNode, isSelected: false, isActive: false, isPassed: false, isFlaggedNg: false,
    onSelect: () => {}, onConnectStart: () => {}, onConnectFinish: () => {},
  }));
}

function renderCropDetail(pipeline, crop) {
  const file = path.join(__dirname, 'CropDetailModal.tsx');
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const component = new Module(file, module);
  component.filename = file;
  component.paths = Module._nodeModulePaths(__dirname);
  const originalRequire = component.require.bind(component);
  component.require = (name) => name === '../../stores/useFlowchartStore'
    ? { useFlowchartStore: () => ({ pipeline, executionResult: { crops: [crop] }, setInspectedCrop: () => {} }) }
    : originalRequire(name);
  component._compile(code, file);
  const React = require('react');
  return require('react-dom/server').renderToStaticMarkup(React.createElement(component.exports.CropDetailModal, {
    crop, onClose: () => {},
  }));
}

const node = (id, node_type, extra = {}) => ({
  id, position: { x: 0, y: 0 },
  data: { label: id, node_type, threshold: 0.5, params: {}, ...extra },
});
const edge = (id, source, target, payload_type) => ({ id, source, target, payload_type });

function parallelGraph() {
  return {
    id: 'parallel', name: 'Parallel segmentation and patch inspection',
    nodes: [
      node('input', 'input'),
      node('roi', 'fixed_roi', { params: { roi_bbox: [0, 0, 32, 32] } }),
      node('seg', 'inspection', { task: 'segmentation' }),
      node('patch', 'inspection', { task: 'patch_classification' }),
      node('blob', 'blob_measure', { params: { min_blob_area_px: 2, min_blob_count_for_ng: 2 } }),
      node('combine', 'aggregate', { rule: 'all_ng' }),
      node('decision', 'decision', { rule: 'aggregate_verdict' }),
      node('output', 'output'),
    ],
    edges: [
      edge('e1', 'input', 'roi', 'image'),
      edge('e2', 'roi', 'seg', 'roi'),
      edge('e3', 'roi', 'patch', 'roi'),
      edge('e4', 'seg', 'blob', 'result'),
      edge('e5', 'blob', 'combine', 'result'),
      edge('e6', 'patch', 'combine', 'result'),
      edge('e7', 'combine', 'decision', 'result'),
      edge('e8', 'decision', 'output', 'result'),
    ],
  };
}

test('segmentation Blob and parallel aggregate graph validates for saving', () => {
  assert.equal(validateFlowchartGraph(parallelGraph()), null);
});

test('validation issue points to the broken Blob node or incompatible edge', () => {
  const graph = parallelGraph();
  graph.edges = graph.edges.filter((item) => item.id !== 'e4');
  const missing = validateFlowchartGraph(graph);
  assert.match(missing, /seg/i);
  assert.deepEqual(locateFlowIssue(graph, missing), { kind: 'node', id: 'seg' });

  const badPayload = parallelGraph();
  badPayload.edges = badPayload.edges.map((item) =>
    item.id === 'e4' ? { ...item, payload_type: 'roi' } : item);
  const error = validateFlowchartGraph(badPayload);
  assert.match(error, /payload/);
  assert.deepEqual(locateFlowIssue(badPayload, error), { kind: 'edge', id: 'e4' });
});

test('Blob accepts only one segmentation result and positive integer limits', () => {
  const graph = parallelGraph();
  const wrongSource = { ...graph, edges: graph.edges.map((item) =>
    item.id === 'e4' ? { ...item, source: 'patch' } : item) };
  assert.match(validateFlowchartGraph(wrongSource), /Blob|분할/);
  const badLimit = { ...graph, nodes: graph.nodes.map((item) => item.id === 'blob'
    ? { ...item, data: { ...item.data, params: { min_blob_area_px: 0, min_blob_count_for_ng: 2 } } } : item) };
  assert.match(validateFlowchartGraph(badLimit), /Blob|면적/);
});

test('aggregate accepts multiple result inputs but rejects unsupported rule', () => {
  const graph = parallelGraph();
  const badRule = { ...graph, nodes: graph.nodes.map((item) => item.id === 'combine'
    ? { ...item, data: { ...item.data, rule: 'average' } } : item) };
  assert.match(validateFlowchartGraph(badRule), /집계|aggregate/);
});

test('aggregate result requires matching decision rule', () => {
  const graph = parallelGraph();
  const wrongDecision = { ...graph, nodes: graph.nodes.map((item) => item.id === 'decision'
    ? { ...item, data: { ...item.data, rule: 'any_defect_is_ng' } } : item) };
  assert.match(validateFlowchartGraph(wrongDecision), /집계|aggregate/);
});

test('connecting segmentation to Blob and Blob to aggregate creates result payloads', () => {
  const graph = parallelGraph();
  const draft = { ...graph, edges: graph.edges.filter((item) => item.id !== 'e4' && item.id !== 'e5') };
  const withBlob = connectFlowNodes(draft, 'seg', 'blob');
  assert.equal(withBlob.edges.at(-1).payload_type, 'result');
  const complete = connectFlowNodes(withBlob, 'blob', 'combine');
  assert.equal(complete.edges.at(-1).payload_type, 'result');
  assert.equal(validateFlowchartGraph(complete), null);
  assert.throws(() => updateFlowEdgePayload(complete, complete.edges.at(-1).id, 'roi'), /형식|지원/);
});

test('Blob and aggregate nodes show their configured rules and result ports', () => {
  const graph = parallelGraph();
  const blob = renderNode(graph.nodes.find((item) => item.id === 'blob'));
  const aggregate = renderNode(graph.nodes.find((item) => item.id === 'combine'));
  assert.match(blob, /BLOB MEASURE/);
  assert.match(blob, /MIN AREA/);
  assert.match(blob, /2 px/);
  assert.match(blob, /NG COUNT/);
  assert.match(blob, /RESULT IN/);
  assert.match(blob, /RESULT OUT/);
  assert.match(aggregate, /aggregate/i);
  assert.match(aggregate, /ALL NG/);
  assert.match(aggregate, /RESULT IN/);
});

test('aggregate Blob evidence identifies the segmentation threshold and measured area', () => {
  const graph = parallelGraph();
  graph.nodes.find((item) => item.id === 'seg').data.threshold = 0.3;
  graph.nodes.find((item) => item.id === 'seg').data.params = { min_defect_area_px: 4 };
  const crop = {
    roi_id: 'blob:same_roi', source_node_id: 'blob', label: 'Segmentation ROI',
    bbox: [0, 0, 32, 32], defect_score: 0.35, verdict: 'NG',
    crop_thumbnail: 'data:image/png;base64,', flaw_type: 'Blob count 2 (minimum 2)',
    defect_area_px: 9, blob_count: 2, largest_blob_area_px: 9,
  };
  const html = renderCropDetail(graph, crop);
  assert.match(html, /임계 기준: 30\.0%/);
  assert.match(html, /9 \/ 4 px/);
  assert.match(html, /측정 Blob 개수:/);
  assert.match(html, /최대 Blob 면적:/);
});
