const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

let savedLoads = 0;
let templateLoads = 0;
let runs = 0;
const saved = {
  id: 'updated-test-pipeline', name: 'Updated Test Pipeline', edges: [],
  nodes: [
    { id: 'detect', position: { x: 0, y: 0 }, data: { node_type: 'detection_crop', label: 'Detector', model_job_id: 'job_A' } },
    { id: 'inspect', position: { x: 0, y: 0 }, data: { node_type: 'inspection', label: 'Anomaly', model_job_id: 'job_A' } },
  ],
};
let savedPipeline = saved;
let verifiedJobId = null;
const blankSegmentation = {
  id: 'single_segmentation', name: '원본 이미지 타일 분할 검사', edges: [],
  nodes: [{ id: 'node_inspect', position: { x: 0, y: 0 },
    data: { node_type: 'inspection', task: 'segmentation', label: 'Segmentation', model_job_id: null } }],
};
const api = {
  flowchart: {
    getPipeline: async () => { savedLoads += 1; return structuredClone(savedPipeline); },
    getSingleSegmentationTemplate: async (jobId) => {
      templateLoads += 1;
      const pipeline = structuredClone(blankSegmentation);
      pipeline.nodes[0].data.model_job_id = jobId || null;
      return pipeline;
    },
    run: async () => { runs += 1; return { final_verdict: 'OK' }; },
  },
};
const filename = path.resolve(__dirname, '../src/renderer/stores/useFlowchartStore.ts');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const item = new Module(filename, module);
item.filename = filename;
item.paths = Module._nodeModulePaths(path.dirname(filename));
const originalRequire = item.require.bind(item);
item.require = (specifier) => {
  if (specifier === '../services/api') return { api };
  if (specifier === './useEvaluationStore') {
    return { useEvaluationStore: { getState: () => ({ jobId: verifiedJobId }) } };
  }
  return originalRequire(specifier);
};
item._compile(compiled, filename);
const flow = item.exports.useFlowchartStore;

test('new dataset clears old DAG and opens a blank segmentation template', async () => {
  await flow.getState().loadPipeline();
  assert.equal(flow.getState().pipeline.id, 'updated-test-pipeline');
  flow.getState().invalidateForDataChange();
  assert.equal(flow.getState().pipeline, null);
  assert.equal(flow.getState().pipelineDirty, false);
  const revision = flow.getState().contextRevision;
  await flow.getState().loadSingleSegmentationTemplate();
  assert.equal(flow.getState().pipeline.id, 'single_segmentation');
  assert.equal(flow.getState().pipeline.nodes[0].data.model_job_id, null);
  assert.equal(flow.getState().modelContextInvalidated, true);
  assert.equal(flow.getState().pipelineDirty, false);
  assert.equal(savedLoads, 1);
  assert.equal(templateLoads, 1);
  flow.getState().invalidateForDataChange();
  assert.equal(flow.getState().contextRevision, revision + 1);
});

test('blank template can be selected but cannot execute until a model is specified', async () => {
  await flow.getState().loadSingleSegmentationTemplate();
  flow.getState().setSelectedImage({ imageId: 'b', imagePath: '/dataset/B/b.jpg', fileName: 'b.jpg', source: 'dataset' });
  assert.equal(await flow.getState().runPipeline(), false);
  assert.equal(runs, 0);
  assert.match(flow.getState().errorMessage, /학습 모델 작업 ID/);
  flow.getState().updateNodeData('node_inspect', { model_job_id: 'job_B' });
  assert.equal(await flow.getState().runPipeline(), true);
  assert.equal(runs, 1);
});

test('explicit saved DAG restore strips old model IDs after data change', async () => {
  verifiedJobId = null;
  await flow.getState().loadPipeline(true);
  assert.equal(flow.getState().pipeline.id, 'updated-test-pipeline');
  assert.equal(flow.getState().pipeline.nodes[0].data.model_job_id, undefined);
  assert.equal(flow.getState().pipeline.nodes[1].data.model_job_id, undefined);
  await flow.getState().loadSingleSegmentationTemplate();
  assert.equal(flow.getState().pipeline.id, 'single_segmentation');
});

test('saved single-model restore preserves only the Step 4 verified job ID', async () => {
  try {
    savedPipeline = structuredClone(blankSegmentation);
    savedPipeline.nodes[0].data.model_job_id = 'job_A';
    verifiedJobId = 'job_A';
    await flow.getState().loadPipeline(true);
    assert.equal(flow.getState().pipeline.nodes[0].data.model_job_id, 'job_A');
    assert.equal(flow.getState().pipelineDirty, false);

    savedPipeline.nodes[0].data.model_job_id = 'job_B';
    await flow.getState().loadPipeline(true);
    assert.equal(flow.getState().pipeline.nodes[0].data.model_job_id, undefined);
    assert.equal(flow.getState().pipelineDirty, true);
  } finally {
    savedPipeline = saved;
    verifiedJobId = null;
  }
});

test('Step 5 defaults to template and disables RUN without a model', () => {
  const source = fs.readFileSync(path.resolve(__dirname, '../src/renderer/components/flowchart/FlowchartStudio.tsx'), 'utf8');
  assert.match(source, /if \(modelContextInvalidated\)/);
  assert.match(source, /await loadSingleSegmentationTemplate\(segmentationJobId \|\| undefined\)/);
  assert.match(source, /disabled=\{isRunning \|\| isLoading \|\| needsModel\}/);
});
