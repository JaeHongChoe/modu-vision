const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const filename = path.resolve(__dirname, '../../stores/useFlowchartStore.ts');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const loaded = new Module(filename, module);
loaded.filename = filename;
loaded.paths = Module._nodeModulePaths(path.dirname(filename));
const originalRequire = loaded.require.bind(loaded);
const initial = {
  id: 'history-test', name: 'History test',
  nodes: [{ id: 'inspect', position: { x: 100, y: 100 }, data: {
    label: 'Inspect', node_type: 'inspection', task: 'segmentation', model_job_id: 'job_1', threshold: 0.5,
  } }],
  edges: [],
};
const api = { flowchart: {
  getSingleSegmentationTemplate: async () => structuredClone(initial),
  getPipeline: async () => structuredClone(initial),
  getActivePipeline: async () => structuredClone(initial),
  savePipeline: async () => ({}),
} };
loaded.require = (name) => name === '../services/api' ? { api } : originalRequire(name);
loaded._compile(compiled, filename);
const store = loaded.exports.useFlowchartStore;

test.beforeEach(async () => {
  store.getState().invalidateForDataChange();
  await store.getState().loadPipeline(true);
});

test('node parameter and edge edits undo and redo in order', () => {
  store.getState().updateNodeData('inspect', { threshold: 0.7 });
  const withEdge = { ...store.getState().pipeline, edges: [{ id: 'e1', source: 'inspect', target: 'missing' }] };
  store.getState().replacePipeline(withEdge);
  assert.equal(store.getState().canUndo, true);
  assert.equal(store.getState().canRedo, false);
  store.getState().undo();
  assert.equal(store.getState().pipeline.edges.length, 0);
  assert.equal(store.getState().pipeline.nodes[0].data.threshold, 0.7);
  store.getState().undo();
  assert.equal(store.getState().pipeline.nodes[0].data.threshold, 0.5);
  assert.equal(store.getState().pipelineDirty, false);
  assert.equal(store.getState().canUndo, false);
  store.getState().redo();
  store.getState().redo();
  assert.equal(store.getState().pipeline.edges.length, 1);
  assert.equal(store.getState().pipelineDirty, true);
  assert.equal(store.getState().canRedo, false);
});

test('new graph edit after undo clears redo history', () => {
  store.getState().updateNodeData('inspect', { threshold: 0.7 });
  store.getState().undo();
  assert.equal(store.getState().canRedo, true);
  store.getState().updateNodeData('inspect', { threshold: 0.8 });
  assert.equal(store.getState().canRedo, false);
  store.getState().redo();
  assert.equal(store.getState().pipeline.nodes[0].data.threshold, 0.8);
});

test('pointer drag records one undo step for many position updates', () => {
  store.getState().beginHistoryGroup();
  store.getState().moveNode('inspect', { x: 120, y: 100 });
  store.getState().moveNode('inspect', { x: 160, y: 100 });
  store.getState().moveNode('inspect', { x: 200, y: 100 });
  store.getState().endHistoryGroup();
  assert.equal(store.getState().pipeline.nodes[0].position.x, 200);
  store.getState().undo();
  assert.equal(store.getState().pipeline.nodes[0].position.x, 100);
  assert.equal(store.getState().canUndo, false);
  store.getState().redo();
  assert.equal(store.getState().pipeline.nodes[0].position.x, 200);
});

test('loading a different graph clears old undo and redo', async () => {
  store.getState().updateNodeData('inspect', { label: 'Edited' });
  store.getState().undo();
  assert.equal(store.getState().canRedo, true);
  await store.getState().loadPipeline(true);
  assert.equal(store.getState().canUndo, false);
  assert.equal(store.getState().canRedo, false);
  assert.equal(store.getState().pipeline.nodes[0].data.label, 'Inspect');
});

test('undo invalidates a displayed execution result', () => {
  store.getState().updateNodeData('inspect', { threshold: 0.7 });
  store.setState({ executionResult: { final_verdict: 'OK' }, inspectedCrop: { roi_id: 'old' } });
  store.getState().undo();
  assert.equal(store.getState().executionResult, null);
  assert.equal(store.getState().inspectedCrop, null);
});

test('saving a graph makes redo back to that graph clean', async () => {
  store.getState().updateNodeData('inspect', { threshold: 0.7 });
  await store.getState().savePipeline();
  assert.equal(store.getState().pipelineDirty, false);
  store.getState().undo();
  assert.equal(store.getState().pipelineDirty, true);
  store.getState().redo();
  assert.equal(store.getState().pipelineDirty, false);
});

test('incomplete save recovery keeps edits dirty and explains how to reopen the saved flow', async () => {
  const previousSave = api.flowchart.savePipeline;
  const previousError = console.error;
  api.flowchart.savePipeline = async () => { throw { recovery_incomplete: true }; };
  console.error = () => {};
  try {
    store.getState().updateNodeData('inspect', { threshold: 0.7 });
    await store.getState().savePipeline();
    assert.equal(store.getState().pipelineDirty, true);
    assert.match(store.getState().errorMessage, /활성 플로우와 저장 버전/);
  } finally {
    api.flowchart.savePipeline = previousSave;
    console.error = previousError;
  }
});
