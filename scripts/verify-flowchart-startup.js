const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const filename = path.resolve(__dirname, '../src/renderer/components/flowchart/flowchartStartup.ts');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const loaded = new Module(filename, module);
loaded.filename = filename;
loaded.paths = Module._nodeModulePaths(path.dirname(filename));
loaded._compile(compiled, filename);
const { pipelineMatchesTask, recoverThenLoadFlowchart, singleModelAutoBinding } = loaded.exports;

test('model-node schema counts enhancement preprocessing even before model binding', () => {
  const { getFlowchartModelTask, getFlowchartModelReferences } = loaded.exports;
  const nodes = [
    { data: { node_type: 'preprocess', params: { operation: 'enhancement' }, model_job_id: 'job_enhance' } },
    { data: { node_type: 'inspection', task: 'segmentation', model_job_id: 'job_segment' } },
    { data: { node_type: 'preprocess', params: { operation: 'rotate' } } },
  ];
  assert.deepEqual(nodes.map(getFlowchartModelTask), ['enhancement', 'segmentation', null]);
  assert.equal(getFlowchartModelTask({ data: { node_type: 'preprocess', params: { operation: 'enhancement' } } }), 'enhancement');
  assert.deepEqual(getFlowchartModelReferences({ nodes }), [
    { job_id: 'job_enhance', task: 'enhancement' }, { job_id: 'job_segment', task: 'segmentation' },
  ]);
});

test('a recipe change rejects the previous inspection draft', () => {
  const classification = {
    nodes: [{ data: { node_type: 'inspection', task: 'classification' } }],
  };
  assert.equal(pipelineMatchesTask(classification, 'classification'), true);
  assert.equal(pipelineMatchesTask(classification, 'anomaly'), false);
  assert.equal(pipelineMatchesTask(null, 'classification'), false);
  const detector = {
    nodes: [{ id: 'node_crop', data: { node_type: 'detection_crop', task: 'detection', model_job_id: null } }],
  };
  assert.equal(pipelineMatchesTask(detector, 'detection'), true);
  assert.equal(pipelineMatchesTask(detector, 'classification'), false);
  const mixed = {
    nodes: [
      { data: { node_type: 'inspection', task: 'classification' } },
      { data: { node_type: 'inspection', task: 'segmentation' } },
    ],
  };
  assert.equal(pipelineMatchesTask(mixed, 'segmentation'), true);
  assert.equal(pipelineMatchesTask(mixed, 'anomaly'), false);
  assert.deepEqual(singleModelAutoBinding(detector, 'detection', 'job_verified'), {
    nodeId: 'node_crop', modelJobId: 'job_verified',
  });
});

test('source-verified classification and anomaly models bind only to blank single-model flows', () => {
  for (const task of ['classification', 'anomaly']) {
    const blank = {
      id: `single_${task}`,
      nodes: [{ id: 'node_inspect', data: { node_type: 'inspection', task, model_job_id: null } }],
    };
    assert.deepEqual(singleModelAutoBinding(blank, task, 'job_verified'), {
      nodeId: 'node_inspect', modelJobId: 'job_verified',
    });
    assert.equal(singleModelAutoBinding(blank, task === 'classification' ? 'anomaly' : 'classification', 'job_verified'), null);
    blank.nodes[0].data.model_job_id = 'job_saved';
    assert.equal(singleModelAutoBinding(blank, task, 'job_verified'), null);
  }
  const chained = {
    id: 'detector_roi',
    nodes: [
      { id: 'node_detect', data: { node_type: 'detection_crop', model_job_id: 'job_detector' } },
      { id: 'node_inspect', data: { node_type: 'inspection', task: 'classification', model_job_id: null } },
    ],
  };
  assert.equal(singleModelAutoBinding(chained, 'classification', 'job_verified'), null);
});

function setup() {
  let evaluationJobId = null;
  let loadCalls = 0;
  let evaluationCalls = [];
  let modelVerificationCalls = [];
  let releaseEvaluation;
  let stillCurrent = true;
  const options = {
    folderPath: '/data/current',
    task: 'segmentation',
    datasetKey: '/data/current\0segmentation',
    hasSelectedFolder: true,
    datasetIsLoading: false,
    importError: null,
    allowLatestRecovery: true,
    completedCurrentJobId: null,
    getVerifiedJobId: () => evaluationJobId,
    loadEvaluation: async (_jobId, source) => {
      evaluationCalls.push(source);
      evaluationJobId = await new Promise((resolve) => { releaseEvaluation = resolve; });
    },
    loadSavedPipeline: async () => {
      loadCalls += 1;
      return { id: 'saved-flow', nodes: [{ data: { model_job_id: evaluationJobId } }], edges: [] };
    },
    verifyModels: async (source, models) => { modelVerificationCalls.push({ source, models }); },
    isCurrent: () => stillCurrent,
  };
  return {
    options,
    get loadCalls() { return loadCalls; },
    get evaluationCalls() { return evaluationCalls; },
    get modelVerificationCalls() { return modelVerificationCalls; },
    release: (jobId) => releaseEvaluation(jobId),
    setVerified: (jobId) => { evaluationJobId = jobId; },
    setCurrent: (value) => { stillCurrent = value; },
  };
}

test('saved flow loads only after source-scoped completed model recovery', async () => {
  const state = setup();
  const pending = recoverThenLoadFlowchart(state.options);
  assert.equal(state.loadCalls, 0);
  assert.deepEqual(state.evaluationCalls, [{ folderPath: '/data/current', task: 'segmentation' }]);
  state.release('job_A');
  const result = await pending;
  assert.equal(result.status, 'ready');
  assert.equal(result.verifiedJobId, 'job_A');
  assert.equal(state.loadCalls, 1);
  assert.equal(result.pipeline.nodes[0].data.model_job_id, 'job_A');
});

test('failed model recovery inspects saved flow but blocks a graph without a verified model', async () => {
  const state = setup();
  state.options.loadEvaluation = async () => { throw new Error('No completed training job'); };
  const result = await recoverThenLoadFlowchart(state.options);
  assert.equal(result.status, 'blocked');
  assert.equal(state.loadCalls, 1);
  assert.equal(result.reason, 'model_unavailable');
});

test('dataset and task must match the source before any saved flow load', async () => {
  const state = setup();
  state.setVerified('job_A');
  state.options.datasetKey = '/data/other\0segmentation';
  const result = await recoverThenLoadFlowchart(state.options);
  assert.equal(result.status, 'blocked');
  assert.equal(state.loadCalls, 0);
  assert.equal(state.evaluationCalls.length, 0);
});

test('source changes during recovery prevent stale saved flow load', async () => {
  const state = setup();
  const pending = recoverThenLoadFlowchart(state.options);
  state.setCurrent(false);
  state.release('job_A');
  const result = await pending;
  assert.equal(result.status, 'cancelled');
  assert.equal(state.loadCalls, 0);
});

test('recovery-disabled data inspects saved flow but blocks an unbound graph', async () => {
  const state = setup();
  state.options.allowLatestRecovery = false;
  const result = await recoverThenLoadFlowchart(state.options);
  assert.equal(result.status, 'blocked');
  assert.equal(state.loadCalls, 1);
  assert.equal(state.evaluationCalls.length, 0);
});

test('a source-verified saved graph opens even without a current evaluation selection', async () => {
  const state = setup();
  state.options.allowLatestRecovery = false;
  state.options.loadSavedPipeline = async () => ({
    id: 'saved-chain',
    nodes: [{ data: { node_type: 'inspection', task: 'segmentation', model_job_id: 'job_verified_saved' } }],
    edges: [],
  });
  const result = await recoverThenLoadFlowchart(state.options);
  assert.equal(result.status, 'ready');
  assert.equal(result.verifiedJobId, '');
  assert.deepEqual(state.modelVerificationCalls, [{ source: '/data/current', models: [
    { job_id: 'job_verified_saved', task: 'segmentation' },
  ] }]);
});

test('already verified current model loads the saved flow without another evaluation call', async () => {
  const state = setup();
  state.setVerified('job_A');
  const result = await recoverThenLoadFlowchart(state.options);
  assert.equal(result.status, 'ready');
  assert.equal(state.loadCalls, 1);
  assert.equal(state.evaluationCalls.length, 0);
});

test('two source-verified models remain connected when reopening a detector ROI flow', async () => {
  const state = setup();
  state.setVerified('job_inspect');
  state.options.loadSavedPipeline = async () => ({
    id: 'detector_roi',
    nodes: [
      { data: { node_type: 'detection_crop', task: 'detection', model_job_id: 'job_detector' } },
      { data: { node_type: 'inspection', task: 'segmentation', model_job_id: 'job_inspect' } },
    ],
    edges: [],
  });
  const result = await recoverThenLoadFlowchart(state.options);
  assert.equal(result.status, 'ready');
  assert.deepEqual(state.modelVerificationCalls, [{
    source: '/data/current',
    models: [
      { job_id: 'job_detector', task: 'detection' },
      { job_id: 'job_inspect', task: 'segmentation' },
    ],
  }]);
  assert.equal(result.pipeline.nodes[0].data.model_job_id, 'job_detector');
});

test('saved flow stays blocked when either model fails source verification', async () => {
  const state = setup();
  state.setVerified('job_inspect');
  state.options.loadSavedPipeline = async () => ({
    id: 'detector_roi',
    nodes: [
      { data: { node_type: 'detection_crop', task: 'detection', model_job_id: 'job_inspect' } },
      { data: { node_type: 'inspection', task: 'segmentation', model_job_id: 'job_inspect' } },
    ],
    edges: [],
  });
  state.options.verifyModels = async () => { throw new Error('source mismatch'); };
  const result = await recoverThenLoadFlowchart(state.options);
  assert.deepEqual(result, { status: 'blocked', reason: 'saved_model_mismatch' });
});
