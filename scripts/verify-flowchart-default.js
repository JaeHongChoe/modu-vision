const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

let savedLoads = 0;
let templateLoads = 0;
let detectionTemplateLoads = 0;
let detectorTemplateLoads = 0;
const requestedPipelineTasks = [];
const requestedPipelineSources = [];
const requestedTemplateTasks = [];
let runs = 0;
let saveCalls = 0;
let releaseSave = null;
let delaySave = false;
let releaseRun = null;
let delayRun = false;
const saved = {
  id: 'updated-test-pipeline', name: 'Updated Test Pipeline', edges: [],
  nodes: [
    { id: 'detect', position: { x: 0, y: 0 }, data: { node_type: 'detection_crop', label: 'Detector', model_job_id: 'job_A' } },
    { id: 'inspect', position: { x: 0, y: 0 }, data: { node_type: 'inspection', label: 'Anomaly', model_job_id: 'job_A' } },
  ],
};
let savedPipeline = saved;
const blankSegmentation = {
  id: 'single_segmentation', name: '원본 이미지 타일 분할 검사', edges: [],
  nodes: [{ id: 'node_inspect', position: { x: 0, y: 0 },
    data: { node_type: 'inspection', task: 'segmentation', label: 'Segmentation', model_job_id: null } }],
};
const api = {
  flowchart: {
    getActivePipeline: async () => {
      const error = new Error('No active saved flow in this fixture');
      error.status = 404;
      throw error;
    },
    getPipeline: async (task, source) => {
      savedLoads += 1;
      requestedPipelineTasks.push(task);
      requestedPipelineSources.push(source);
      const pipeline = structuredClone(savedPipeline);
      if (pipeline.id === 'single_segmentation' && task && task !== 'segmentation') {
        pipeline.id = `single_${task}`;
        pipeline.nodes[0].data.task = task;
      }
      return pipeline;
    },
    getSingleSegmentationTemplate: async (jobId, task) => {
      templateLoads += 1;
      requestedTemplateTasks.push(task);
      const pipeline = structuredClone(blankSegmentation);
      if (task && task !== 'segmentation') {
        pipeline.id = `single_${task}`;
        pipeline.nodes[0].data.task = task;
      }
      pipeline.nodes[0].data.model_job_id = jobId || null;
      return pipeline;
    },
    getSingleDetectionTemplate: async (jobId) => {
      detectionTemplateLoads += 1;
      return {
        id: 'single_detection', name: '원본 이미지 결함 검출', edges: [],
        nodes: [{ id: 'node_crop', position: { x: 0, y: 0 },
          data: { node_type: 'detection_crop', task: 'detection', label: 'Detector', model_job_id: jobId || null } }],
      };
    },
    getDetectorRoiTemplate: async (task) => {
      detectorTemplateLoads += 1;
      return {
        id: 'detector_roi', name: '검출 ROI 후 결함 검사', edges: [],
        nodes: [
          { id: 'detect', position: { x: 0, y: 0 }, data: { node_type: 'detection_crop', task: 'detection', label: 'Detector' } },
          { id: 'inspect', position: { x: 0, y: 0 }, data: { node_type: 'inspection', task, label: 'Inspector' } },
        ],
      };
    },
    savePipeline: async () => {
      saveCalls += 1;
      if (delaySave) await new Promise((resolve) => { releaseSave = resolve; });
    },
    run: async () => {
      runs += 1;
      if (delayRun) return new Promise((resolve) => { releaseRun = resolve; });
      return { final_verdict: 'OK' };
    },
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

test('classification flow requests a matching default and binds its completed model in the template', async () => {
  try {
    savedPipeline = structuredClone(blankSegmentation);
    const loaded = await flow.getState().loadPipeline(true, 'classification', '/data/source');
    assert.equal(requestedPipelineTasks.at(-1), 'classification');
    assert.equal(requestedPipelineSources.at(-1), '/data/source');
    assert.equal(loaded.nodes[0].data.task, 'classification');
    await flow.getState().loadSingleSegmentationTemplate('job_verified', 'classification');
    assert.equal(requestedTemplateTasks.at(-1), 'classification');
    assert.equal(flow.getState().pipeline.nodes[0].data.task, 'classification');
    assert.equal(flow.getState().pipeline.nodes[0].data.model_job_id, 'job_verified');
  } finally {
    savedPipeline = saved;
  }
});

test('detection recipe opens a single detector flow with its completed model', async () => {
  await flow.getState().loadSingleSegmentationTemplate('job_detector', 'detection');
  assert.equal(detectionTemplateLoads, 1);
  assert.equal(flow.getState().pipeline.id, 'single_detection');
  assert.equal(flow.getState().pipeline.nodes[0].data.model_job_id, 'job_detector');
  assert.equal(flow.getState().selectedNodeId, 'node_crop');
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

test('explicit saved DAG restore preserves model IDs for source verification', async () => {
  await flow.getState().loadPipeline(true);
  assert.equal(flow.getState().pipeline.id, 'updated-test-pipeline');
  assert.equal(flow.getState().pipeline.nodes[0].data.model_job_id, 'job_A');
  assert.equal(flow.getState().pipeline.nodes[1].data.model_job_id, 'job_A');
  await flow.getState().loadSingleSegmentationTemplate();
  assert.equal(flow.getState().pipeline.id, 'single_segmentation');
});

test('saved single-model restore never rewrites a model ID before verification', async () => {
  try {
    savedPipeline = structuredClone(blankSegmentation);
    savedPipeline.nodes[0].data.model_job_id = 'job_A';
    await flow.getState().loadPipeline(true);
    assert.equal(flow.getState().pipeline.nodes[0].data.model_job_id, 'job_A');
    assert.equal(flow.getState().pipelineDirty, false);

    savedPipeline.nodes[0].data.model_job_id = 'job_B';
    await flow.getState().loadPipeline(true);
    assert.equal(flow.getState().pipeline.nodes[0].data.model_job_id, 'job_B');
    assert.equal(flow.getState().pipelineDirty, false);
  } finally {
    savedPipeline = saved;
  }
});

test('saved detector and inspection model IDs both survive a reopen', async () => {
  try {
    savedPipeline = structuredClone(saved);
    savedPipeline.nodes[0].data.model_job_id = 'job_detector';
    savedPipeline.nodes[1].data.model_job_id = 'job_A';
    await flow.getState().loadPipeline(true);
    assert.equal(flow.getState().pipeline.nodes[0].data.model_job_id, 'job_detector');
    assert.equal(flow.getState().pipeline.nodes[1].data.model_job_id, 'job_A');
    assert.equal(flow.getState().pipelineDirty, false);
  } finally {
    savedPipeline = saved;
  }
});

test('detector ROI template opens the supported five-node chain for the chosen inspection task', async () => {
  await flow.getState().loadDetectorRoiTemplate('segmentation');
  assert.equal(flow.getState().pipeline.id, 'detector_roi');
  assert.equal(flow.getState().pipeline.nodes[1].data.task, 'segmentation');
  assert.equal(flow.getState().pipeline.nodes[0].data.model_job_id, undefined);
  assert.equal(detectorTemplateLoads, 1);
});

test('editing during save keeps newer changes unsaved and prevents concurrent writes', async () => {
  await flow.getState().loadSingleSegmentationTemplate();
  flow.getState().updateNodeData('node_inspect', { label: 'before-save' });
  delaySave = true;
  const firstSave = flow.getState().savePipeline();
  assert.equal(flow.getState().isSaving, true);
  const startedCalls = saveCalls;
  await flow.getState().savePipeline();
  assert.equal(saveCalls, startedCalls);
  flow.getState().updateNodeData('node_inspect', { label: 'edited-while-saving' });
  releaseSave();
  await firstSave;
  assert.equal(flow.getState().pipeline.nodes[0].data.label, 'edited-while-saving');
  assert.equal(flow.getState().pipelineDirty, true);
  assert.equal(flow.getState().isSaving, false);
  delaySave = false;
  await flow.getState().savePipeline();
  assert.equal(flow.getState().pipelineDirty, false);
});

test('changing the target image during RUN discards the prior result and releases running state', async () => {
  await flow.getState().loadSingleSegmentationTemplate('job_A');
  flow.getState().setSelectedImage({ imagePath: '/dataset/A/a.jpg', fileName: 'a.jpg', source: 'dataset' });
  delayRun = true;
  const pending = flow.getState().runPipeline();
  assert.equal(flow.getState().isRunning, true);
  flow.getState().setSelectedImage({ imagePath: '/dataset/A/b.jpg', fileName: 'b.jpg', source: 'dataset' });
  releaseRun({ final_verdict: 'NG', image_path: '/dataset/A/a.jpg' });
  assert.equal(await pending, false);
  assert.equal(flow.getState().isRunning, false);
  assert.equal(flow.getState().executionResult, null);
  assert.equal(flow.getState().selectedImage.fileName, 'b.jpg');
  delayRun = false;
  assert.equal(await flow.getState().runPipeline(), true);
  assert.equal(flow.getState().executionResult.final_verdict, 'OK');
});

test('editing a node during RUN cannot attach the old result to the new flow', async () => {
  await flow.getState().loadSingleSegmentationTemplate('job_A');
  flow.getState().setSelectedImage({ imagePath: '/dataset/A/a.jpg', fileName: 'a.jpg', source: 'dataset' });
  delayRun = true;
  const pending = flow.getState().runPipeline();
  flow.getState().updateNodeData('node_inspect', { threshold: 0.9 });
  releaseRun({ final_verdict: 'NG', image_path: '/dataset/A/a.jpg' });
  assert.equal(await pending, false);
  assert.equal(flow.getState().isRunning, false);
  assert.equal(flow.getState().executionResult, null);
  assert.equal(flow.getState().pipeline.nodes[0].data.threshold, 0.9);
  delayRun = false;
});

test('editing a threshold after RUN clears the displayed ROI and old result', async () => {
  await flow.getState().loadSingleSegmentationTemplate('job_A');
  flow.getState().setSelectedImage({ imagePath: '/dataset/A/a.jpg', fileName: 'a.jpg', source: 'dataset' });
  assert.equal(await flow.getState().runPipeline(), true);
  const crop = { roi_id: 'full_image', defect_score: 0.4, verdict: 'OK' };
  flow.getState().setInspectedCrop(crop);
  assert.equal(flow.getState().executionResult.final_verdict, 'OK');
  assert.equal(flow.getState().inspectedCrop, crop);
  flow.getState().updateNodeData('node_inspect', { threshold: 0.1 });
  assert.equal(flow.getState().executionResult, null);
  assert.equal(flow.getState().inspectedCrop, null);
});

test('graph edits and node dragging invalidate an old result and mark the flow unsaved', async () => {
  await flow.getState().loadSingleSegmentationTemplate('job_A');
  const previous = flow.getState().pipeline;
  const extended = { ...previous, nodes: [...previous.nodes, {
    id: 'new_output', position: { x: 123, y: 42 }, data: { node_type: 'output', label: 'NG result' },
  }] };
  flow.getState().replacePipeline(extended);
  assert.equal(flow.getState().pipeline.nodes.length, previous.nodes.length + 1);
  assert.equal(flow.getState().pipelineDirty, true);
  flow.getState().moveNode('new_output', { x: 200, y: 80 });
  assert.deepEqual(flow.getState().pipeline.nodes.at(-1).position, { x: 200, y: 80 });
  assert.equal(flow.getState().pipelineDirty, true);
  assert.notEqual(flow.getState().pipeline, extended);
});

test('an invalidated old RUN cannot clear a newer run', async () => {
  await flow.getState().loadSingleSegmentationTemplate('job_A');
  flow.getState().setSelectedImage({ imagePath: '/dataset/A/a.jpg', fileName: 'a.jpg', source: 'dataset' });
  delayRun = true;
  const oldRun = flow.getState().runPipeline();
  const releaseOldRun = releaseRun;
  flow.getState().invalidateForDataChange();
  await flow.getState().loadSingleSegmentationTemplate('job_B');
  flow.getState().setSelectedImage({ imagePath: '/dataset/B/b.jpg', fileName: 'b.jpg', source: 'dataset' });
  const newRun = flow.getState().runPipeline();
  const releaseNewRun = releaseRun;
  releaseOldRun({ final_verdict: 'NG', image_path: '/dataset/A/a.jpg' });
  assert.equal(await oldRun, false);
  assert.equal(flow.getState().isRunning, true);
  assert.equal(flow.getState().executionResult, null);
  releaseNewRun({ final_verdict: 'OK', image_path: '/dataset/B/b.jpg' });
  assert.equal(await newRun, true);
  assert.equal(flow.getState().isRunning, false);
  assert.equal(flow.getState().executionResult.image_path, '/dataset/B/b.jpg');
  delayRun = false;
});

test('Step 5 result keeps its draft or saved-version origin after later saving', async () => {
  await flow.getState().loadSingleSegmentationTemplate('job_A');
  flow.getState().setSelectedImage({ imagePath: '/dataset/A/a.jpg', fileName: 'a.jpg', source: 'dataset' });
  assert.equal(await flow.getState().runPipeline(undefined, undefined, { savedVersionId: null }), true);
  assert.deepEqual(flow.getState().lastRunSource, { kind: 'draft', versionId: null });
  await flow.getState().savePipeline();
  assert.deepEqual(flow.getState().lastRunSource, { kind: 'draft', versionId: null });

  await flow.getState().loadPipeline(true);
  flow.getState().setSelectedImage({ imagePath: '/dataset/A/a.jpg', fileName: 'a.jpg', source: 'dataset' });
  assert.equal(await flow.getState().runPipeline(undefined, undefined, { savedVersionId: 'active_revision' }), true);
  assert.deepEqual(flow.getState().lastRunSource, { kind: 'saved', versionId: 'active_revision' });
});

test('Step 5 verifies the source before reopening a saved flow and gates RUN on graph readiness', () => {
  const source = fs.readFileSync(path.resolve(__dirname, '../src/renderer/components/flowchart/FlowchartStudio.tsx'), 'utf8');
  assert.match(source, /recoverThenLoadFlowchart\(/);
  assert.match(source, /const canVerifyGraph = modelCheck\.status !== 'checking'/);
  assert.match(source, /disabled=\{!canVerifyGraph \|\| isVerifyingAction \|\| isRunning \|\| isLoading \|\| needsModel \|\| !!graphError\}/);
  assert.match(source, /disabled=\{!canVerifyGraph \|\| isVerifyingAction \|\| isSaving \|\| isLoading \|\| isRunning \|\| !pipeline \|\| needsModel \|\| !!graphError\}/);
  assert.match(source, /if \(modelNodes\.length === 0 \|\| models\.length !== modelNodes\.length\)/);
  assert.match(source, /loadDetectorRoiTemplate\(/);
  assert.match(source, /검출 ROI 검사/);
  assert.match(source, /verifyModels:/);
  assert.match(source, /모델 다시 확인/);
});
