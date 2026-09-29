const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const filename = path.resolve(__dirname, '../src/renderer/components/flowchart/flowchartGraph.ts');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const item = new Module(filename, module);
item.filename = filename;
item.paths = Module._nodeModulePaths(path.dirname(filename));
item._compile(compiled, filename);
const { validateFlowchartGraph, connectFlowNodes, removeFlowNode, updateFlowEdgeBranch,
  decisionRulePatch, shouldShowThreshold } = item.exports;

const loadComponent = (relativePath, mockedImports = {}) => {
  const componentFile = path.resolve(__dirname, relativePath);
  const componentCode = ts.transpileModule(fs.readFileSync(componentFile, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const component = new Module(componentFile, module);
  component.filename = componentFile;
  component.paths = Module._nodeModulePaths(path.dirname(componentFile));
  const originalRequire = component.require.bind(component);
  component.require = (name) => {
    if (Object.hasOwn(mockedImports, name)) return mockedImports[name];
    return name === './flowchartViewport' ? { FLOW_NODE_WIDTH: 272 } : originalRequire(name);
  };
  component._compile(componentCode, componentFile);
  return component.exports;
};

const node = (id, node_type, task = 'segmentation') => ({
  id, position: { x: 0, y: 0 },
  data: { label: id, node_type, task, model_job_id: node_type === 'inspection' ? `job_${id}` : undefined },
});
const linear = () => ({
  id: 'linear', name: 'linear',
  nodes: [node('input', 'input'), node('inspect', 'inspection'), node('decision', 'decision'), node('output', 'output')],
  edges: [
    { id: 'a', source: 'input', target: 'inspect' },
    { id: 'b', source: 'inspect', target: 'decision' },
    { id: 'c', source: 'decision', target: 'output' },
  ],
});

test('legacy linear graph validates and new model branch can join decision', () => {
  const original = linear();
  assert.equal(validateFlowchartGraph(original), null);
  const withNode = { ...original, nodes: [...original.nodes, node('inspect2', 'inspection')] };
  assert.match(validateFlowchartGraph(withNode), /연결|connection/i);
  const first = connectFlowNodes(withNode, 'input', 'inspect2');
  const second = connectFlowNodes(first, 'inspect2', 'decision');
  assert.equal(validateFlowchartGraph(second), null);
  assert.equal(original.nodes.length, 4);
});

test('adding verdict outputs produces pass fail review branches and supports branch swap', () => {
  let graph = linear();
  graph = { ...graph, nodes: [...graph.nodes, node('ng', 'output')] };
  graph = connectFlowNodes(graph, 'decision', 'ng');
  assert.equal(validateFlowchartGraph(graph), null);
  assert.deepEqual(graph.edges.filter(edge => edge.source === 'decision').map(edge => edge.isBranch), ['pass', 'fail']);
  graph = { ...graph, nodes: [...graph.nodes, node('review', 'output')] };
  graph = connectFlowNodes(graph, 'decision', 'review');
  assert.equal(validateFlowchartGraph(graph), null);
  assert.equal(graph.edges.at(-1).isBranch, 'review');
  const swapped = updateFlowEdgeBranch(graph, graph.edges.at(-1).id, 'pass');
  assert.equal(validateFlowchartGraph(swapped), null);
  assert.equal(swapped.edges.find(edge => edge.target === 'review').isBranch, 'pass');
  assert.equal(swapped.edges.find(edge => edge.target === 'output').isBranch, 'review');
});

test('legacy default output becomes OK when a second verdict branch is connected', () => {
  const old = linear();
  old.edges[2].isBranch = 'default';
  const draft = { ...old, nodes: [...old.nodes, node('ng', 'output')] };
  const branched = connectFlowNodes(draft, 'decision', 'ng');
  assert.equal(validateFlowchartGraph(branched), null);
  assert.deepEqual(branched.edges.filter(edge => edge.source === 'decision').map(edge => edge.isBranch), ['pass', 'fail']);
});

test('invalid connection is refused without corrupting graph and removing model prunes edges', () => {
  const graph = linear();
  assert.throws(() => connectFlowNodes(graph, 'decision', 'inspect'), /연결|connection/i);
  assert.throws(() => connectFlowNodes(graph, 'input', 'inspect'), /이미|already|existing/i);
  const pruned = removeFlowNode(graph, 'inspect');
  assert.deepEqual(pruned.edges.map(edge => edge.id), ['c']);
  assert.match(validateFlowchartGraph(pruned), /모델|model/i);
  assert.throws(() => removeFlowNode(graph, 'input'), /입력|input/i);
});

test('node ports render as accessible connection controls', () => {
  const React = require('react');
  const { renderToStaticMarkup } = require('react-dom/server');
  const { CustomNode } = loadComponent('../src/renderer/components/flowchart/CustomNode.tsx');
  const html = renderToStaticMarkup(React.createElement(CustomNode, {
    node: node('inspect', 'inspection'), isSelected: false, isActive: false,
    isPassed: false, isFlaggedNg: false, onSelect: () => {},
    onConnectStart: () => {}, onConnectFinish: () => {},
  }));
  assert.match(html, /aria-label="Start connection from inspect"/);
  assert.match(html, /aria-label="Connect to inspect"/);
  const outputHtml = renderToStaticMarkup(React.createElement(CustomNode, {
    node: node('result', 'output'), isSelected: false, isActive: false,
    isPassed: false, isFlaggedNg: false, onSelect: () => {},
    onConnectStart: () => {}, onConnectFinish: () => {},
  }));
  assert.doesNotMatch(outputHtml, /Start connection from result/);
});

test('saved edges render as selectable controls with their verdict branches', () => {
  const React = require('react');
  const { renderToStaticMarkup } = require('react-dom/server');
  const { DAGCircuitOverlay } = loadComponent('../src/renderer/components/flowchart/DAGCircuitOverlay.tsx');
  const graph = linear();
  graph.edges[2].isBranch = 'pass';
  const html = renderToStaticMarkup(React.createElement(DAGCircuitOverlay, {
    nodes: graph.nodes, edges: graph.edges, activeRunningNodeId: null,
    selectedEdgeId: 'c', onSelectEdge: () => {}, routedOutputNodeId: 'output', finalVerdict: 'OK',
  }));
  assert.match(html, /aria-label="Select connection decision to output"/);
  assert.match(html, /OK/);
});

test('score decision rule creates an editable threshold when a legacy node lacks one', () => {
  const decision = node('decision', 'decision');
  delete decision.data.threshold;
  const patch = decisionRulePatch(decision.data, 'score_gt_threshold');
  assert.equal(patch.rule, 'score_gt_threshold');
  assert.equal(patch.threshold, 0.5);
  assert.equal(shouldShowThreshold({ ...decision, data: { ...decision.data, rule: 'score_gt_threshold' } }), true);
});

const renderCropDetail = (pipeline, crop) => {
  const React = require('react');
  const { renderToStaticMarkup } = require('react-dom/server');
  const { CropDetailModal } = loadComponent('../src/renderer/components/flowchart/CropDetailModal.tsx', {
    '../../stores/useFlowchartStore': {
      useFlowchartStore: () => ({
        pipeline, executionResult: { crops: [crop] }, setInspectedCrop: () => {},
      }),
    },
  });
  return renderToStaticMarkup(React.createElement(CropDetailModal, { crop, onClose: () => {} }));
};

test('ROI detail shows the saved detector threshold used for an NG crop', () => {
  const pipeline = {
    id: 'saved_detector', name: 'Saved detector',
    nodes: [
      node('input', 'input'),
      { ...node('detect', 'detection_crop', 'detection'), data: {
        ...node('detect', 'detection_crop', 'detection').data, threshold: 0.10,
      } },
      node('decision', 'decision'), node('output', 'output'),
    ],
    edges: [
      { id: 'a', source: 'input', target: 'detect' },
      { id: 'b', source: 'detect', target: 'decision' },
      { id: 'c', source: 'decision', target: 'output' },
    ],
  };
  const crop = {
    roi_id: 'crop_1', label: 'CROP_1', bbox: [0, 0, 20, 20],
    defect_score: 0.148, verdict: 'NG', crop_thumbnail: 'data:image/png;base64,',
    flaw_type: 'defect', confidence: 0.148,
  };
  const html = renderCropDetail(pipeline, crop);
  assert.match(html, /임계 기준: 10\.0%/);
  assert.match(html, /\+4\.8%/);
  assert.doesNotMatch(html, /임계 기준: 45\.0%/);
});

test('ROI detail uses the tagged producer node when multiple model branches join', () => {
  const pipeline = {
    id: 'branched', name: 'Branched',
    nodes: [node('input', 'input'),
      { ...node('detect_a', 'detection_crop', 'detection'), data: { ...node('detect_a', 'detection_crop', 'detection').data, threshold: 0.10 } },
      { ...node('inspect_b', 'inspection', 'classification'), data: { ...node('inspect_b', 'inspection', 'classification').data, threshold: 0.30 } },
      node('decision', 'decision'), node('output', 'output')],
    edges: [
      { id: 'a', source: 'input', target: 'detect_a' },
      { id: 'b', source: 'input', target: 'inspect_b' },
      { id: 'c', source: 'detect_a', target: 'decision' },
      { id: 'd', source: 'inspect_b', target: 'decision' },
      { id: 'e', source: 'decision', target: 'output' },
    ],
  };
  const crop = {
    roi_id: 'inspect_b:full_image', label: 'Full image', bbox: [0, 0, 20, 20],
    defect_score: 0.35, verdict: 'NG', crop_thumbnail: 'data:image/png;base64,',
    flaw_type: 'defect',
  };
  const html = renderCropDetail(pipeline, crop);
  assert.match(html, /임계 기준: 30\.0%/);
  assert.match(html, /\+5\.0%/);
  assert.doesNotMatch(html, /임계 기준: 10\.0%/);
});
