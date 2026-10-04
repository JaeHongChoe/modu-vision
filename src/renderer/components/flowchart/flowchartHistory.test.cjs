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
  getActivePipelineRecord: async () => ({ version_id: null, pipeline: structuredClone(initial) }),
  activeVersionId: async () => ({ version_id: null }),
  savePipeline: async () => ({}),
} };
loaded.require = (name) => {
  if (name === '../services/api') return { api };
  if (name === '../services/flowDraft') return { flowDraft: {} };
  if (name === './useProjectStore') return { useProjectStore: { getState: () => ({ project: null }) } };
  if (name === '../components/flowchart/flowchartStartup') return { getFlowchartModelTask: node => node.data.task || null };
  return originalRequire(name);
};
loaded._compile(compiled, filename);
const store = loaded.exports.useFlowchartStore;
const { isExecutionResultCurrent } = loaded.exports;
const graphFilename = path.join(__dirname, 'flowchartGraph.ts');
const graphModule = new Module(graphFilename, module);
graphModule.filename = graphFilename;
graphModule.paths = Module._nodeModulePaths(__dirname);
graphModule._compile(ts.transpileModule(fs.readFileSync(graphFilename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, graphFilename);

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

test('ordinary graph replacement keeps its stale base and remains undoable', () => {
  store.getState().setBaseVersionId(null);
  const previous = store.getState().pipeline;
  const next = { ...previous, name: 'Ordinary edit' };
  store.getState().replacePipeline(next);
  assert.equal(store.getState().baseVersionId, null);
  store.getState().undo();
  assert.equal(store.getState().pipeline, previous);
  assert.equal(store.getState().baseVersionId, null);
});

test('explicit new basis atomically clears old undo, redo and open drag history', () => {
  store.getState().setBaseVersionId(null);
  store.getState().updateNodeData('inspect', { threshold: 0.7 });
  store.getState().undo();
  store.getState().beginHistoryGroup();
  assert.equal(store.getState().historyFuture.length, 1);
  assert.ok(store.getState().historyGroupStart);
  const adopted = { ...store.getState().pipeline, name: 'Adopted recipe' };
  const observations = [];
  const unsubscribe = store.subscribe(state => observations.push({ pipeline: state.pipeline, base: state.baseVersionId,
    past: state.historyPast.length, future: state.historyFuture.length, group: state.historyGroupStart }));
  store.getState().replacePipeline(adopted, { baseVersionId: 'active-v1' });
  unsubscribe();
  assert.deepEqual(observations, [{ pipeline: adopted, base: 'active-v1', past: 0, future: 0, group: null }]);
  assert.equal(store.getState().canUndo, false);
  assert.equal(store.getState().canRedo, false);
  store.getState().undo();
  store.getState().redo();
  store.getState().endHistoryGroup();
  assert.equal(store.getState().pipeline, adopted);
  assert.equal(store.getState().historyPast.length, 0);
  assert.equal(store.getState().baseVersionId, 'active-v1');
});

test('edits after recipe adoption undo and redo within the observed basis', () => {
  const adopted = { ...store.getState().pipeline, name: 'Recipe with no active version' };
  store.getState().setBaseVersionId('old-version');
  store.getState().replacePipeline(adopted, { baseVersionId: null });
  store.getState().updateNodeData('inspect', { threshold: 0.8 });
  const edited = store.getState().pipeline;
  store.getState().undo();
  assert.equal(store.getState().pipeline, adopted);
  assert.equal(store.getState().baseVersionId, null);
  assert.equal(store.getState().canUndo, false);
  store.getState().redo();
  assert.equal(store.getState().pipeline, edited);
  assert.equal(store.getState().baseVersionId, null);
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

test('undo keeps a displayed result but never marks it current without its run identity', () => {
  // S0-05: results are kept as an earlier version instead of being discarded.
  store.getState().updateNodeData('inspect', { threshold: 0.7 });
  store.setState({ executionResult: { final_verdict: 'OK' }, executionIdentity: null, inspectedCrop: { roi_id: 'old' } });
  store.getState().undo();
  assert.equal(store.getState().executionResult.final_verdict, 'OK');
  assert.equal(isExecutionResultCurrent(store.getState()), false);
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

test('flow layout is one undoable edit and preserves the original connected graph', () => {
  const pipeline = { ...store.getState().pipeline, nodes: [
    { ...initial.nodes[0], id: 'input', position: { x: 40, y: 160 }, data: { node_type: 'input', label: 'Input' } },
    { ...initial.nodes[0], id: 'inspect', position: { x: 360, y: 160 } },
    { ...initial.nodes[0], id: 'roi', position: { x: 325, y: 320 }, data: { node_type: 'fixed_roi', label: 'ROI', params: { roi_bbox: [0, 0, 64, 64] } } },
    { ...initial.nodes[0], id: 'enhance', position: { x: 670, y: 320 }, data: { node_type: 'preprocess', label: 'Enhance', model_job_id: 'enhancement_original', params: { operation: 'enhancement' } } },
  ], edges: [{ id: 'e1', source: 'input', target: 'roi' }, { id: 'e2', source: 'roi', target: 'enhance' }, { id: 'e3', source: 'enhance', target: 'inspect' }] };
  store.setState({ pipeline, cleanPipeline: pipeline, pipelineDirty: false });
  const arranged = graphModule.exports.layoutFlowchart(pipeline);
  store.getState().replacePipeline(arranged);
  assert.equal(store.getState().historyPast.length, 1);
  assert.equal(store.getState().pipelineDirty, true);
  assert.deepEqual(store.getState().pipeline.edges, pipeline.edges);
  assert.deepEqual(store.getState().pipeline.nodes.map(node => node.data), pipeline.nodes.map(node => node.data));
  store.getState().undo();
  assert.equal(store.getState().pipeline, pipeline);
  assert.equal(store.getState().pipelineDirty, false);
  assert.equal(store.getState().canUndo, false);
  store.getState().redo();
  assert.equal(store.getState().pipeline, arranged);
});

test('debug run validates models only up to the selected node and sends stop identity',async()=>{
  const pipeline={...store.getState().pipeline,nodes:[{id:'input',data:{node_type:'input'},position:{x:0,y:0}},{id:'roi',data:{node_type:'fixed_roi',params:{roi_bbox:[0,0,32,32]}},position:{x:100,y:0}},{id:'later',data:{node_type:'inspection',task:'segmentation',model_job_id:null},position:{x:200,y:0}}],edges:[{id:'a',source:'input',target:'roi'},{id:'b',source:'roi',target:'later'}]};
  store.getState().replacePipeline(pipeline);store.getState().setSelectedImage({source:'dataset',imagePath:'/fixture/a.png',fileName:'a.png'});
  let request;
  api.flowchart.run=async data=>{request=data;return {status:'partial',final_verdict:'REVIEW',stop_node_id:'roi',execution_target:'local',execution_device:'cpu',execution_steps:[]};};
  const ok=await store.getState().runPipeline(undefined,undefined,{savedVersionId:null,executionTarget:'local',device:'cpu',stopNodeId:'roi'});
  assert.equal(ok,true);assert.equal(request.stop_node_id,'roi');assert.equal(store.getState().executionResult.status,'partial');
  assert.equal(isExecutionResultCurrent(store.getState()),true);
  store.getState().updateNodeData('roi',{params:{roi_bbox:[1,1,33,33]}});
  assert.equal(store.getState().executionResult.status,'partial','S0-05: the earlier run stays visible');
  assert.equal(isExecutionResultCurrent(store.getState()),false,'and is marked as an earlier version');
});

test('opening a saved version reads it without changing the active inspection recipe', async () => {
  let activations = 0;
  api.flowchart.getPipelineVersion = async (id) => ({ ...structuredClone(initial), name: id });
  api.flowchart.activatePipelineVersion = async () => { activations++; return { pipeline: initial }; };
  const opened = await store.getState().loadPipelineVersion('older', '/source');
  assert.equal(opened.name, 'older');
  assert.equal(activations, 0);
  assert.equal(store.getState().executionResult, null);
});
